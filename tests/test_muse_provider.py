"""Synthetic Muse history; no installed agent or user conversation access."""
from copy import deepcopy
import threading

import pytest

from thechatplace.providers.base import SessionCapabilities, Snapshot
from thechatplace.providers.muse import MuseProvider, MuseReader, session_from_muse, transcript_from_muse
from thechatplace.providers.muse_connection import MuseHistoryError
from thechatplace.session_controller import SessionController
from thechatplace.sessions import SessionInfo, STATUS_UNAVAILABLE, SORT_STATUS
from thechatplace.transcript import USER, ASSISTANT, TOOL, TOOL_RESULT, ERROR, EVENT, THINKING


NATIVE_ID = "019a7b33-1234-7000-8000-000000000001"


def session(**changes):
    return {"sessionId": NATIVE_ID, "name": "Synthetic history", "workspaceRoot": "C:/synthetic",
            "createdAt": "2026-11-01T10:00:00Z", "updatedAt": "2026-11-02T11:00:00Z",
            "status": "notLoaded", "activeTurnId": None, **changes}


def item(kind, ident="item", **changes):
    return {"kind": kind, "itemId": ident, "revision": 1, "status": "completed", **changes}


def history(items=None, **changes):
    return {"session": session(), "history": {"mode": "inline", "items": items or [], "snapshot": None},
            "viewCursor": "point-in-time", "pendingRequests": [], **changes}


class Connection:
    def __init__(self, pages=None, result=None):
        self.pages = pages or [{"sessions": [session()], "nextCursor": None}]
        self.result = result or history([item("userMessage", "user", text="Hello"),
                                       item("agentMessage", "answer", text="Synthetic reply")])
        self.requests = []
        self.closed = False

    def request(self, method, params):
        assert method in {"session/list", "session/read"}
        self.requests.append((method, deepcopy(params)))
        if method == "session/list":
            return deepcopy(self.pages[1 if params.get("cursor") else 0])
        if isinstance(self.result, Exception):
            raise self.result
        return deepcopy(self.result)

    def close(self):
        self.closed = True


def test_metadata_uses_provider_identity_and_never_infers_ownership_or_live_state():
    info = session_from_muse(session(status="idle", source="own", archived=True))
    assert info.identity == ("muse", NATIVE_ID)
    assert info.key == "muse:" + NATIVE_ID and info.provider_label == "Muse Code"
    assert info.source == "app_server" and not info.is_own
    assert info.state == STATUS_UNAVAILABLE and info.transcript_path() is None
    assert info.created_ms == 1793527200000 and info.last_activity_ms == 1793617200000


@pytest.mark.parametrize("changes", [{"sessionId": ""}, {"sessionId": 1}, {"sessionId": "bad\nID"}])
def test_invalid_native_identity_is_rejected(changes):
    with pytest.raises(MuseHistoryError):
        session_from_muse(session(**changes))


def test_optional_metadata_remains_unknown_and_unknown_fields_are_ignored():
    info = session_from_muse({"sessionId": NATIVE_ID, "future": {"private": "value"}})
    assert info.cwd == "" and info.last_activity_ms == 0 and info.created_ms == 0
    assert info.title == "Untitled Muse Code chat"
    assert session_from_muse(session(name=None, title="A title")).title == "A title"
    assert session_from_muse(session(updatedAt=True)).last_activity_ms == 0


def test_session_title_is_one_line_bounded_and_free_of_control_characters():
    info = session_from_muse(session(name="  First\nSecond\tThird\x00Fourth  " + "x" * 400))
    assert info.title.startswith("First Second Third Fourth ")
    assert len(info.title) == 200 and all(char.isprintable() for char in info.title)


def test_discovery_pages_until_null_and_deduplicates_without_reading_messages():
    conn = Connection(pages=[{"sessions": [session()], "nextCursor": "opaque"},
        {"sessions": [session(), session(sessionId="second")], "nextCursor": None}])
    provider = MuseProvider(connection=conn)
    snap = provider.collect()
    assert len(snap.sessions) == 2
    assert conn.requests == [("session/list", {"limit": 200}),
                             ("session/list", {"limit": 200, "cursor": "opaque"})]
    assert not snap.desktop_cli_ids and not snap.live
    info = snap.sessions[0]
    assert provider.capabilities(info) == SessionCapabilities(usage=True)
    assert not provider.can_create()
    provider.close()
    assert conn.closed


@pytest.mark.parametrize("page", [None, {}, {"sessions": {}, "nextCursor": None},
    {"sessions": [None], "nextCursor": None}, {"sessions": [], "nextCursor": ""},
    {"sessions": [], "nextCursor": False}])
def test_invalid_or_incomplete_catalog_is_not_an_empty_success(page):
    conn = Connection()
    conn.pages = [page]
    with pytest.raises(MuseHistoryError):
        MuseProvider(connection=conn).collect()


def test_repeated_cursor_and_page_limit_fail_instead_of_silent_truncation(monkeypatch):
    conn = Connection(pages=[{"sessions": [session()], "nextCursor": "same"}] * 2)
    provider = MuseProvider(connection=conn)
    with pytest.raises(MuseHistoryError):
        provider.collect()
    assert len(conn.requests) == 2
    monkeypatch.setattr(provider, "MAX_PAGES", 1)
    with pytest.raises(MuseHistoryError):
        provider.collect()


def test_history_mapping_preserves_order_roles_tool_output_and_provider_name():
    result = history([item("userMessage", "u", text="Question"),
        item("agentMessage", "a", text="Answer"),
        item("toolCall", "t", tool="shell", args='{"command":"echo synthetic"}',
             visibleOutput="synthetic output"),
        item("systemNotice", "e", fallbackText="Synthetic failure", noticeSeverity="error")])
    transcript = transcript_from_muse(result, NATIVE_ID)
    assert [m.kind for m in transcript.messages] == [USER, ASSISTANT, TOOL, TOOL_RESULT, ERROR]
    assert transcript.messages[1].label == "Muse Code"
    assert "synthetic output" in transcript.messages[3].text
    assert all(m.key.startswith("muse:") for m in transcript.messages)
    assert transcript.turns == 1


def test_unknown_and_malformed_items_produce_readable_partial_history_warning():
    result = history([item("agentMessage", "good", text="Readable"),
                      item("futureKind", "future", secret="do not dump raw JSON"), None,
                      item("agentMessage", "bad", text={"invalid": "value"})])
    transcript = transcript_from_muse(result, NATIVE_ID)
    assert transcript.messages[0].text == "Readable"
    assert transcript.unreadable_lines == 3
    assert any("couldn't be displayed" in m.text for m in transcript.messages)
    assert "secret" not in repr(transcript) and "do not dump" not in repr(transcript)


def test_reminder_child_summary_is_activity_not_unreadable_conversation():
    transcript = transcript_from_muse(history([
        item("userMessage", "u", text="Synthetic question"),
        item("reminderChild", "r", fallbackText="Synthetic reminder activity"),
        item("agentMessage", "a", text="Synthetic answer")]), NATIVE_ID)
    assert [message.kind for message in transcript.messages] == [USER, EVENT, ASSISTANT]
    assert transcript.messages[1].text == "Synthetic reminder activity"
    assert transcript.unreadable_lines == 0
    assert [message.kind for message in transcript.visible()] == [USER, ASSISTANT]


def test_duplicate_revisions_cannot_duplicate_messages_or_reveal_retracted_content():
    result = history([item("agentMessage", "a", text="old"),
        item("agentMessage", "a", revision=2, text="new"),
        item("agentMessage", "hidden", text="retracted content", retracted=True)])
    transcript = transcript_from_muse(result, NATIVE_ID)
    assert [m.text for m in transcript.messages] == ["new"]


@pytest.mark.parametrize("result", [history(session=session(sessionId="another")),
    history(history={"mode": "none", "items": None, "snapshot": None}),
    history(history={"mode": "anchoredSnapshot", "items": None, "snapshot": {}}),
    history(history={"mode": "future", "items": []}), history(viewCursor=None)])
def test_wrong_session_or_unavailable_history_is_not_a_complete_empty_transcript(result):
    with pytest.raises(MuseHistoryError):
        transcript_from_muse(result, NATIVE_ID)


def test_complete_snapshot_is_read_without_attaching_or_paging():
    result = history(history={"mode": "snapshot", "items": None, "snapshot": {
        "schemaVersion": 1, "viewCursor": "point-in-time", "state": {
            "items": [item("agentMessage", "a", text="Snapshot reply")]}}})
    assert transcript_from_muse(result, NATIVE_ID).last_reply().text == "Snapshot reply"
    result["history"]["snapshot"]["schemaVersion"] = 999
    with pytest.raises(MuseHistoryError):
        transcript_from_muse(result, NATIVE_ID)


def test_reader_refresh_retains_last_good_history_on_failure_and_retries():
    conn = Connection()
    reader = MuseReader(conn, NATIVE_ID)
    assert reader.refresh() and not reader.refresh()
    good = reader.transcript
    conn.result = MuseHistoryError("synthetic outage")
    with pytest.raises(MuseHistoryError):
        reader.refresh()
    assert reader.transcript is good
    conn.result = history([item("agentMessage", "a", text="Recovered")], viewCursor="new-cursor")
    assert reader.refresh() and reader.transcript.last_reply().text == "Recovered"
    assert all(method == "session/read" and params["excludeItems"] is False
               for method, params in conn.requests)


def test_provider_routes_reading_and_rejects_other_provider_before_any_request():
    conn = Connection()
    provider = MuseProvider(connection=conn)
    info = session_from_muse(session())
    controller = SessionController([provider])
    assert controller.read(info).last_reply().text == "Synthetic reply"
    assert controller.last_reply(info) == "Synthetic reply"
    assert controller.capabilities(info) == SessionCapabilities(usage=True)
    assert not controller.can_create("muse")
    foreign = deepcopy(info)
    foreign.provider = "claude"
    before = len(conn.requests)
    for action in (provider.open_reader, provider.capabilities, provider.has_history, provider.last_reply):
        with pytest.raises(MuseHistoryError):
            action(foreign)
    assert len(conn.requests) == before
    controller.close()


def test_discovery_keeps_stale_rows_and_recovers_without_false_finished_turns():
    from thechatplace.providers.discovery import DiscoveryRegistry
    conn = Connection()
    provider = MuseProvider(connection=conn)
    registry = DiscoveryRegistry([provider])
    registry.PROVIDER_WAIT_SECONDS = 1
    first = registry.collect(SORT_STATUS)
    assert len(first.sessions) == 1
    conn.pages = [{}]
    failed = registry.collect(SORT_STATUS)
    assert len(failed.sessions) == 1 and failed.stale_providers == {"muse"}
    assert failed.newly_failed_providers == {"muse"} and failed.fresh_states() == {}
    assert not registry.collect(SORT_STATUS).newly_failed_providers
    conn.pages = [{"sessions": [session()], "nextCursor": None}]
    recovered = registry.collect(SORT_STATUS)
    assert not recovered.stale_providers and not recovered.fresh_states()


def test_close_refuses_new_operations_and_does_not_publish_late_discovery():
    conn = Connection()
    provider = MuseProvider(connection=conn)
    provider.close()
    with pytest.raises(MuseHistoryError):
        provider.collect()
    assert conn.closed and not conn.requests


def test_closing_during_read_keeps_prior_transcript_and_discards_late_result():
    started, finish = threading.Event(), threading.Event()
    class SlowConnection(Connection):
        slow = False
        def request(self, method, params):
            if self.slow:
                started.set()
                assert finish.wait(2)
            return super().request(method, params)
    conn = SlowConnection()
    provider = MuseProvider(connection=conn)
    reader = provider.open_reader(session_from_muse(session()))
    assert reader.refresh()
    good = reader.transcript
    conn.slow = True
    conn.result = history([item("agentMessage", "a", text="Late answer")])
    errors = []
    def read():
        try:
            reader.refresh()
        except MuseHistoryError as error:
            errors.append(error)
    worker = threading.Thread(target=read)
    worker.start()
    assert started.wait(2)
    provider.close()
    finish.set()
    worker.join(3)
    assert errors and not worker.is_alive() and reader.transcript is good


def test_forged_local_ownership_never_grants_muse_write_actions():
    from dataclasses import replace
    provider = MuseProvider(connection=Connection())
    forged = replace(session_from_muse(session()), source="own")
    assert provider.capabilities(forged) == SessionCapabilities(usage=True)
    controller = SessionController([provider])
    assert not controller.can_create("muse")
    with pytest.raises(ValueError):
        controller.fork_owned(forged, "forbidden")


@pytest.mark.parametrize("selector,said", [
    ("bundled:plan", "Muse Code read the plan skill."),
    ("plugin:acme:deploy", "Muse Code read the acme:deploy skill."),
    (None, "Muse Code read a skill."),
])
def test_a_skill_read_is_activity_not_unreadable_history(selector, said):
    """Installed 1.4.20 records a skillRead item when a skill is loaded."""
    fields = {"selector": selector} if selector else {}
    transcript = transcript_from_muse(history([
        item("userMessage", "u", text="/plan Synthetic"),
        item("skillRead", "s", **fields),
        item("agentMessage", "a", text="Synthetic plan")]), NATIVE_ID)
    assert [(m.kind, m.text) for m in transcript.messages][1] == (EVENT, said)
    assert transcript.unreadable_lines == 0
    assert not any("couldn't be" in m.text for m in transcript.messages)


def test_a_shortened_item_says_so_where_it_is_and_raises_no_warning():
    """Muse keeps very long items cut short in its history (truncated)."""
    transcript = transcript_from_muse(history([
        item("agentMessage", "a", text="Synthetic long answer", truncated=True),
        item("toolCall", "t", tool="shell", args="synthetic", visibleOutput="Synthetic output", truncated=True),
        item("reasoning", "r", text="Synthetic thinking", truncated=True)]), NATIVE_ID)
    shortened = "(Shortened by Muse Code.)"
    by_kind = {m.kind: m.text for m in transcript.messages}
    assert by_kind[ASSISTANT].endswith(shortened) and by_kind[TOOL_RESULT].endswith(shortened)
    assert by_kind[THINKING].endswith(shortened)
    assert not by_kind[TOOL].endswith(shortened)  # the command itself is whole
    assert transcript.unreadable_lines == 0
    assert not any("couldn't be" in m.text for m in transcript.messages)


@pytest.mark.parametrize("status,said", [
    ("inProgress", "Workflow: synthetic-review (running in the background)"),
    ("completed", "Workflow: synthetic-review (finished)"),
    ("failed", "Workflow: synthetic-review (failed)"),
    ("cancelled", "Workflow: synthetic-review (stopped)"),
    ("someNewStatus", "Workflow: synthetic-review"),
])
def test_a_workflow_and_its_sub_agents_are_activity_not_unreadable_history(status, said):
    """Installed 1.4.24 records a workflow item and one subagent item per part.
    A sub-agent's own status isn't said: 1.4.24 leaves it in progress after
    its workflow completes."""
    transcript = transcript_from_muse(history([
        item("userMessage", "u", text="Synthetic review request"),
        item("workflow", "w", status=status, fallbackText="Workflow: synthetic-review"),
        item("subagent", "s", status="inProgress", fallbackText="Subagent: Synthetic part")]), NATIVE_ID)
    assert [(m.kind, m.text) for m in transcript.messages][1:] == [
        (EVENT, said), (EVENT, "Subagent: Synthetic part")]
    assert transcript.unreadable_lines == 0
    assert not any("couldn't be" in m.text for m in transcript.messages)


def test_a_workflow_without_a_description_is_still_unreadable():
    transcript = transcript_from_muse(history([item("workflow", "w", status="inProgress")]), NATIVE_ID)
    assert transcript.unreadable_lines == 1
