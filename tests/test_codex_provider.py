"""Synthetic protocol records based on app-server 0.162.0-alpha.17.2 schema."""
import pytest

from thechatplace.providers.codex import CodexProvider, transcript_from_thread
from thechatplace.session_controller import SessionController
from thechatplace.sessions import SessionInfo, NEEDS_YOU, WORKING
from thechatplace.transcript import ASSISTANT, USER, TOOL, TOOL_RESULT


def thread(native_id="one", **extra):
    return dict(id=native_id, name="Build", preview="Preview", cwd="/example",
                createdAt=100, updatedAt=200, status={"type": "notLoaded"}, **extra)


def history(native_id="one"):
    return thread(native_id, turns=[{"id": "turn-1", "startedAt": 100, "items": [
        {"type": "userMessage", "id": "u", "content": [{"type": "text", "text": "Hi"}]},
        {"type": "commandExecution", "id": "cmd", "command": "pytest", "aggregatedOutput": "Passed"},
        {"type": "agentMessage", "id": "a", "text": "Ready"},
    ]}])


def history_pages(data):
    turns = data["turns"]
    return [{"thread": {k: v for k, v in data.items() if k != "turns"}},
            {"data": [{**t, "items": [], "itemsView": "notLoaded"} for t in turns]},
            {"data": [{"turnId": t["id"], "item": item}
                      for t in turns for item in t["items"]]}]


class Connection:
    def __init__(self, responses):
        self.responses, self.calls = [], []
        for response in responses:
            data = response.get("thread", {})
            if data.get("turns") and data.get("historyMode") != "paginated":
                self.responses.extend(history_pages(data))
            else:
                self.responses.append(response)

    def request(self, method, params):
        self.calls.append((method, params))
        return self.responses.pop(0)


def test_lists_all_pages_and_archives_without_loading_threads():
    connection = Connection([
        {"data": [thread()], "nextCursor": "page-2"},
        {"data": [thread(), thread("two"), thread("child", parentThreadId="one")], "nextCursor": None},
        {"data": [thread("old")], "nextCursor": None},
    ])
    snapshot = CodexProvider(connection).collect()
    assert [s.key for s in snapshot.sessions] == ["codex:one", "codex:two", "codex:old"]
    assert snapshot.sessions[-1].archived
    assert snapshot.sessions[0].state == "status unavailable"
    assert snapshot.sessions[0].created_ms == 100_000
    assert snapshot.desktop_cli_ids == set() and snapshot.live == {}
    assert [method for method, _ in connection.calls] == ["thread/list"] * 3
    assert connection.calls[1][1]["cursor"] == "page-2"
    assert "appServer" in connection.calls[0][1]["sourceKinds"]
    assert connection.calls[-1][1]["archived"] is True


def test_repeated_cursor_fails_instead_of_looping():
    connection = Connection([{"data": [], "nextCursor": "same"}] * 2)
    with pytest.raises(ValueError, match="pagination cursor"):
        CodexProvider(connection).collect()


def test_reads_backend_history_without_resume_or_claude_paths():
    connection = Connection(history_pages(history()) + [{"thread": thread()}] + history_pages(history()))
    provider = CodexProvider(connection)
    info = SessionInfo("external", "codex:one", "Build", "/example", "one", provider="codex")
    reader = provider.open_reader(info)
    assert reader.refresh()
    assert not reader.refresh()
    assert [(m.kind, m.text) for m in reader.transcript.messages] == [
        (USER, "Hi"), (TOOL, "pytest"), (TOOL_RESULT, "Passed"), (ASSISTANT, "Ready")]
    assert reader.transcript.last_reply().label == "Codex"
    assert provider.last_reply(info) == "Ready"
    assert all(not params["includeTurns"] for method, params in connection.calls
               if method == "thread/read")
    assert {m for m, _ in connection.calls} == {"thread/read", "thread/turns/list", "thread/items/list"}


def test_read_rejects_wrong_thread_without_replacing_history():
    connection = Connection([{"thread": history()}, {"thread": history("other")}])
    info = SessionInfo("external", "codex:one", "Build", "", "one", provider="codex")
    reader = CodexProvider(connection).open_reader(info)
    reader.refresh()
    with pytest.raises(ValueError, match="Couldn.t load this Codex chat"):
        reader.refresh()
    assert reader.transcript.last_reply().text == "Ready"


def test_future_items_are_skipped_and_malformed_known_items_counted():
    data = history()
    data["turns"][0]["items"] += [
        {"type": "futureType", "id": "future", "text": "Private reasoning"},
        {"type": "userMessage", "id": "bad", "content": "wrong type"},
        {"type": "agentMessage", "id": "a", "text": "Ready"},
    ]
    transcript = transcript_from_thread(data)
    assert transcript.unreadable_lines == 1
    assert [m.text for m in transcript.messages].count("Ready") == 1
    assert not any("Private reasoning" in m.text for m in transcript.messages)


def test_missing_turns_is_an_error_not_empty_history():
    with pytest.raises(ValueError, match="readable turn history"):
        transcript_from_thread(thread())


@pytest.mark.parametrize("view", ["summary", "notLoaded"])
def test_incomplete_turn_history_is_rejected(view):
    data = history()
    data["turns"][0]["itemsView"] = view
    with pytest.raises(ValueError, match="incomplete turn history"):
        transcript_from_thread(data)


def test_paginated_history_is_not_silently_truncated():
    with pytest.raises(ValueError, match="pagination reader"):
        transcript_from_thread(thread(historyMode="paginated", turns=[]))


def test_mcp_results_errors_and_file_diffs_are_readable():
    data = history()
    data["turns"][0]["items"] += [
        {"type": "mcpToolCall", "id": "mcp", "server": "docs", "tool": "read",
         "result": {"content": [{"type": "text", "text": "Documentation"}]},
         "error": {"message": "Tool failed"}},
        {"type": "fileChange", "id": "file", "changes": [{"path": "a.py", "diff": "+new line"}]},
    ]
    messages = transcript_from_thread(data).messages
    assert any(m.kind == TOOL_RESULT and m.text == "Documentation" for m in messages)
    assert any(m.text == "Tool failed" for m in messages)
    assert any("+new line" in m.text for m in messages)


def test_codex_rejects_foreign_session_before_rpc():
    connection = Connection([])
    provider = CodexProvider(connection)
    foreign = SessionInfo("own", "own:one", "Claude", "", "one")
    with pytest.raises(ValueError, match="Not a Codex session"):
        provider.open_reader(foreign)
    assert not connection.calls


def test_controller_routes_codex_history_with_its_own_speaker():
    connection = Connection([{"thread": history()}])
    controller = SessionController([CodexProvider(connection)])
    info = SessionInfo("external", "codex:one", "Build", "", "one", provider="codex")
    assert controller.has_history(info)
    assert controller.read(info).messages[-1].label == "Codex"


def test_default_codex_provider_and_reader_do_not_start_a_process(monkeypatch):
    from thechatplace import platform_paths

    def unexpected(*args):
        pytest.fail("Provider construction started a process")

    monkeypatch.setattr(platform_paths, "start_codex_server", unexpected)
    provider = CodexProvider()
    info = SessionInfo("external", "codex:one", "Build", "", "one", provider="codex")
    assert provider.has_history(info)
    assert provider.open_reader(info).native_id == "one"
    provider.close()


def test_controller_close_reaches_codex_connection():
    connection = Connection([])
    closed = []
    connection.close = lambda: closed.append(True)
    SessionController([CodexProvider(connection)]).close()
    assert closed == [True]


def test_last_message_cache_is_bounded_fair_and_invalidated_by_activity():
    from thechatplace.providers.codex import session_from_thread

    class Histories:
        calls = []

        def request(self, method, params):
            if method == "thread/read":
                self.calls.append(params["threadId"])
            pages = history_pages(history(params["threadId"]))
            return pages[{"thread/read": 0, "thread/turns/list": 1, "thread/items/list": 2}[method]]

    connection = Histories()
    provider = CodexProvider(connection)
    provider.LAST_READ_BUDGET = 1
    rows = [session_from_thread(thread(str(i)), False) for i in range(3)]
    for _ in range(3):
        provider.fill_last_messages(rows)
    assert connection.calls == ["0", "1", "2"]
    assert all(s.last_message == "Codex: Ready" for s in rows)
    provider.fill_last_messages(rows)
    assert len(connection.calls) == 3
    rows[0].last_activity_ms += 1
    provider.fill_last_messages(rows)
    assert connection.calls[-1] == "0" and len(connection.calls) == 4
    provider.MAX_LAST_MESSAGES = 1
    rows[1].last_activity_ms += 1
    provider.fill_last_messages(rows)
    assert len(provider._last_messages) == 1
    provider.forget_last_messages()
    assert not provider._last_messages


def test_last_message_failure_does_not_reconnect_for_every_history():
    from thechatplace.providers.codex import session_from_thread

    class Failure:
        calls = 0

        def request(self, method, params):
            self.calls += 1
            raise RuntimeError("private account and path")

    connection = Failure()
    provider = CodexProvider(connection)
    rows = [session_from_thread(thread(str(i)), False) for i in range(3)]
    provider.fill_last_messages(rows)
    assert connection.calls == 1 and all(not s.last_message for s in rows)
    assert "private" not in repr(provider._last_messages)


def test_last_message_cache_refreshes_unchanged_rows_after_deadline(monkeypatch):
    from types import SimpleNamespace
    from thechatplace.providers import codex

    connection = Connection([{"thread": history()}, {"thread": history()}])
    provider = CodexProvider(connection)
    rows = [codex.session_from_thread(thread(), False)]
    monkeypatch.setattr(codex, "time", SimpleNamespace(monotonic=lambda: 0))
    provider.fill_last_messages(rows)
    monkeypatch.setattr(codex, "time", SimpleNamespace(monotonic=lambda: 31))
    provider.fill_last_messages(rows)
    assert len(connection.calls) == 6 and rows[0].last_message == "Codex: Ready"


def test_controller_optional_column_preserves_cache_on_outage_without_history_reads():
    from thechatplace.providers.base import Snapshot
    from thechatplace.sessions import SORT_STATUS

    class Catalog:
        failed = False
        calls = []

        def request(self, method, params):
            self.calls.append(method)
            if method == "thread/list":
                if self.failed:
                    raise RuntimeError("private error")
                return {"data": [] if params["archived"] else [thread()], "nextCursor": None}
            return history_pages(history())[
                {"thread/read": 0, "thread/turns/list": 1, "thread/items/list": 2}[method]]

    connection = Catalog()
    provider = CodexProvider(connection)
    controller = SessionController([provider])
    first = controller.collect(lambda: Snapshot(), SORT_STATUS, last_messages=True)
    assert first.sessions[0].last_message == "Codex: Ready"
    connection.failed = True
    connection.calls.clear()
    stale = controller.collect(lambda: Snapshot(), SORT_STATUS, last_messages=True)
    assert stale.stale_providers == {"codex"}
    assert stale.sessions[0].last_message == "Codex: Ready"
    assert connection.calls == ["thread/list"]
    controller.collect(lambda: Snapshot(), SORT_STATUS, last_messages=False)
    assert not provider._last_messages


@pytest.mark.parametrize("action", ["close", "forget_last_messages"])
def test_inflight_last_message_cannot_repopulate_cleared_cache(action):
    import threading
    from thechatplace.providers.codex import session_from_thread

    entered, release = threading.Event(), threading.Event()

    class Blocked:
        calls = 0

        def request(self, method, params):
            self.calls += 1
            entered.set()
            assert release.wait(5)
            return history_pages(history())[
                {"thread/read": 0, "thread/turns/list": 1, "thread/items/list": 2}[method]]

        def close(self):
            release.set()

    connection = Blocked()
    provider = CodexProvider(connection)
    rows = [session_from_thread(thread(), False)]
    worker = threading.Thread(target=provider.fill_last_messages, args=(rows,))
    worker.start()
    try:
        assert entered.wait(2)
        getattr(provider, action)()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not provider._last_messages
    assert rows[0].last_message == ""
    if action == "close":
        provider.fill_last_messages(rows)
        assert connection.calls == 3


def paginated_pages():
    turns = [{'id': 't1', 'itemsView': 'notLoaded', 'items': []},
             {'id': 't2', 'itemsView': 'notLoaded', 'items': []}]
    def entry(turn, item, text, kind='agentMessage'):
        data = {'id': item, 'type': kind, 'text': text}
        if kind == 'userMessage':
            data['content'] = [{'type': 'text', 'text': text}]
        return {'turnId': turn, 'item': data}
    return [{'thread': thread(historyMode='paginated', turns=[])},
            {'data': turns[:1], 'nextCursor': 'turn-next'},
            {'data': turns, 'nextCursor': None},
            {'data': [entry('t1', 'u', 'First', 'userMessage')], 'nextCursor': 'item-next'},
            {'data': [entry('t1', 'u', 'First', 'userMessage'), entry('t1', 'a', 'Reply'),
                      entry('t2', 'a', 'Newest')], 'nextCursor': None}]


def test_paginated_reader_merges_mid_turn_pages_in_order_with_qualified_dedup():
    connection = Connection(paginated_pages())
    info = SessionInfo('external', 'codex:one', 'Build', '', 'one', provider='codex')
    reader = CodexProvider(connection).open_reader(info)
    assert reader.refresh()
    assert [m.text for m in reader.transcript.messages] == ['First', 'Reply', 'Newest']
    assert [m.key for m in reader.transcript.messages] == ['t1:u', 't1:a', 't2:a']
    assert reader.transcript.turns == 2
    assert reader.transcript.last_reply().text == 'Newest'
    assert all(p.get('sortDirection') == 'asc' for m, p in connection.calls[1:])
    assert connection.calls[2][1]['cursor'] == 'turn-next'
    assert connection.calls[4][1]['cursor'] == 'item-next'
    assert not any(m == 'thread/resume' for m, p in connection.calls)


@pytest.mark.parametrize('page', [
    {'data': [], 'nextCursor': 1}, {'data': [], 'nextCursor': ''},
    {'data': None}, {'data': [{'turnId': 'missing', 'item': {'id': 'i'}}]}])
def test_paginated_failure_does_not_replace_previously_complete_history(page):
    connection = Connection([{'thread': history()}] + paginated_pages()[:3] + [page])
    info = SessionInfo('external', 'codex:one', 'Build', '', 'one', provider='codex')
    reader = CodexProvider(connection).open_reader(info)
    reader.refresh()
    reader._history_checked = 0
    with pytest.raises(ValueError):
        reader.refresh()
    assert reader.transcript.last_reply().text == 'Ready'


def test_repeated_history_cursor_fails_without_publishing_partial_messages():
    pages = paginated_pages()[:3] + [{'data': [], 'nextCursor': 'repeat'}] * 2
    reader = CodexProvider(Connection(pages)).open_reader(
        SessionInfo('external', 'codex:one', 'Build', '', 'one', provider='codex'))
    with pytest.raises(ValueError, match='Couldn.t load this Codex chat'):
        reader.refresh()
    assert not reader.transcript.messages


def test_unchanged_long_chat_refresh_avoids_reloading_pages_until_deadline(monkeypatch):
    from thechatplace.providers import codex
    clock = [100.0]
    monkeypatch.setattr(codex.time, 'monotonic', lambda: clock[0])
    metadata = paginated_pages()[0]
    connection = Connection(paginated_pages() + [metadata] + paginated_pages())
    reader = CodexProvider(connection).open_reader(
        SessionInfo('external', 'codex:one', 'Build', '', 'one', provider='codex'))
    assert reader.refresh()
    assert not reader.refresh()
    assert len(connection.calls) == 6
    clock[0] = 131.0
    assert not reader.refresh()
    assert len(connection.calls) == 11


def test_long_chat_controller_paths_support_find_export_and_last_message():
    from thechatplace import export, search
    connection = Connection(paginated_pages() * 3)
    provider = CodexProvider(connection)
    controller = SessionController([provider])
    info = SessionInfo('external', 'codex:one', 'Build', '', 'one', provider='codex')
    results = search.search_sessions([
        search.Target(info.key, info.title, lambda: None,
                      read_history=lambda: controller.read(info))], 'First')
    assert results.searched == 1 and len(results.matches) == 1
    rendered = export.render(export.TEXT, info.title, controller.read(info).messages)
    assert 'First' in rendered and 'Reply' in rendered and 'Newest' in rendered
    provider.fill_last_messages([info])
    assert 'Newest' in info.last_message


def test_failed_last_message_waits_for_activity_change_even_after_cache_deadline(monkeypatch):
    from thechatplace.providers import codex
    clock = [100.0]
    monkeypatch.setattr(codex.time, "monotonic", lambda: clock[0])
    class Broken:
        calls = 0
        def request(self, method, params):
            self.calls += 1
            raise ValueError("synthetic oversized history")
    connection = Broken()
    provider = CodexProvider(connection)
    rows = [codex.session_from_thread(thread(), False)]
    provider.fill_last_messages(rows)
    clock[0] += 3600
    provider.fill_last_messages(rows)
    assert connection.calls == 1
    rows[0].last_activity_ms += 1
    provider.fill_last_messages(rows)
    assert connection.calls == 2


def test_legacy_metadata_refresh_does_not_reload_unchanged_history():
    connection = Connection(history_pages(history()) + [{"thread": thread(historyMode="legacy")}])
    reader = CodexProvider(connection).open_reader(
        SessionInfo("external", "codex:one", "Build", "", "one", provider="codex"))
    assert reader.refresh()
    assert not reader.refresh()
    assert [m for m, p in connection.calls] == [
        "thread/read", "thread/turns/list", "thread/items/list", "thread/read"]
    assert all(not p["includeTurns"] for m, p in connection.calls if m == "thread/read")


def test_reader_failed_pages_back_off_until_metadata_changes():
    connection = Connection([{"thread": thread()}, {"data": None},
                             {"thread": thread()}] + history_pages({**history(), "updatedAt": 201}))
    reader = CodexProvider(connection).open_reader(
        SessionInfo("external", "codex:one", "Build", "", "one", provider="codex"))
    for _ in range(2):
        with pytest.raises(ValueError, match="Couldn't load"):
            reader.refresh()
    assert [m for m, p in connection.calls] == ["thread/read", "thread/turns/list", "thread/read"]
    assert reader.refresh()
    assert reader.transcript.last_reply().text == "Ready"
