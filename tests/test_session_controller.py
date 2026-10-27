import pytest
import threading
from concurrent.futures import ThreadPoolExecutor

from thechatplace.providers.claude import ClaudeConversations
from thechatplace.session_controller import SessionController
from thechatplace.sessions import OWN, SessionInfo
from thechatplace.transcript import ChatMessage, Transcript, ASSISTANT
from records import assistant_block, lines, text_block


class FakeReader:
    def __init__(self, text):
        self.transcript = Transcript()
        self.transcript.messages = [ChatMessage(ASSISTANT, text)]
        self.refreshed = False

    def refresh(self):
        self.refreshed = True
        return True


class FakeProvider:
    def __init__(self, provider_id, text):
        self.provider_id = provider_id
        self.reader = FakeReader(text)
        self.seen = []

    def open_reader(self, session):
        self.seen.append(session.identity)
        return self.reader

    def has_history(self, session):
        return True

    def last_reply(self, session):
        self.seen.append(session.identity)
        return self.reader.transcript.messages[-1].text


def info(provider="claude"):
    return SessionInfo(OWN, f"{provider}:same", "Chat", "", "same", provider=provider)


def test_routes_colliding_native_ids_to_their_own_readers():
    claude, codex = FakeProvider("claude", "Claude reply"), FakeProvider("codex", "Codex reply")
    controller = SessionController([claude, codex])
    assert controller.read(info()).messages[-1].text == "Claude reply"
    assert controller.read(info("codex")).messages[-1].text == "Codex reply"
    assert controller.last_reply(info("codex")) == "Codex reply"
    assert claude.seen == [("claude", "same")]
    assert codex.seen == [("codex", "same"), ("codex", "same")]
    assert claude.reader.refreshed and codex.reader.refreshed


def test_unavailable_provider_never_falls_back_to_claude():
    provider = FakeProvider("claude", "Wrong history")
    controller = SessionController([provider])
    with pytest.raises(ValueError, match="Provider unavailable: codex"):
        controller.read(info("codex"))
    assert not provider.seen
    assert controller.read(info()).messages[-1].text == "Wrong history"


def test_duplicate_backend_registration_is_rejected():
    with pytest.raises(ValueError, match="Duplicate provider"):
        SessionController([FakeProvider("claude", "a"), FakeProvider("claude", "b")])


def test_missing_claude_history_returns_none(monkeypatch):
    monkeypatch.setattr(SessionInfo, "transcript_path", lambda self: None)
    controller = SessionController([ClaudeConversations()])
    assert controller.read(info()) is None
    assert not controller.has_history(info())
    assert controller.last_reply(info()) == ""


def test_claude_reader_preserves_history_and_tail_without_writing(tmp_path, monkeypatch):
    path = tmp_path / "chat.jsonl"
    path.write_text("\n".join(lines(assistant_block(text_block("Existing reply"), "answer"))) + "\n",
                    encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(SessionInfo, "transcript_path", lambda self: path)
    controller = SessionController([ClaudeConversations()])
    assert controller.read(info()).last_reply().text == "Existing reply"
    assert controller.last_reply(info()) == "Existing reply"
    assert path.read_bytes() == before


def test_claude_adapter_rejects_foreign_session_before_path_lookup(monkeypatch):
    def unexpected(self):
        pytest.fail("Foreign session attempted Claude path lookup")

    monkeypatch.setattr(SessionInfo, "transcript_path", unexpected)
    provider = ClaudeConversations()
    with pytest.raises(ValueError, match="Not a Claude session"):
        provider.open_reader(info("codex"))
    with pytest.raises(ValueError, match="Not a Claude session"):
        provider.last_reply(info("codex"))


def test_controller_shutdown_closes_every_backend_and_rejects_further_reads():
    providers = [FakeProvider("claude", "a"), FakeProvider("codex", "b")]
    closed = []
    for provider in providers:
        provider.close = lambda name=provider.provider_id: closed.append(name)
    controller = SessionController(providers)
    controller.close()
    controller.close()
    assert closed == ["claude", "codex"]
    with pytest.raises(ValueError, match="controller closed"):
        controller.read(info())


def test_controller_cleanup_failure_does_not_skip_other_backend_or_expose_error():
    claude, codex = FakeProvider("claude", "a"), FakeProvider("codex", "b")
    closed = []

    def failed_close():
        closed.append("claude")
        raise RuntimeError("private payload")

    claude.close = failed_close
    codex.close = lambda: closed.append("codex")
    controller = SessionController([claude, codex])
    with pytest.raises(RuntimeError, match="^Session provider cleanup failed$"):
        controller.close()
    assert closed == ["claude", "codex"]


def test_concurrent_controller_close_waits_for_same_backend_cleanup():
    provider = FakeProvider("claude", "reply")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def close_provider():
        calls.append("close")
        entered.set()
        assert release.wait(5)

    provider.close = close_provider
    controller = SessionController([provider])
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(controller.close)
        assert entered.wait(5)
        second = pool.submit(controller.close)
        try:
            assert not first.done() and not second.done()
            with pytest.raises(ValueError, match="controller closed"):
                controller.read(info())
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert calls == ["close"]


def test_controller_merges_discovery_and_reads_with_same_backend():
    from thechatplace.providers.base import Snapshot
    from thechatplace.sessions import SORT_STATUS, SessionInfo

    codex = SessionInfo("external", "codex:same", "Codex", "", "same", provider="codex")
    claude = SessionInfo("own", "own:same", "Claude", "", "same")
    calls = []

    class Backend:
        provider_id = "codex"

        def collect(self):
            calls.append("collect")
            return Snapshot(sessions=[codex])

        def last_reply(self, session):
            calls.append(session.identity)
            return "Codex answer"

    controller = SessionController([Backend()])
    snapshot = controller.collect(lambda: Snapshot(sessions=[claude]), SORT_STATUS)
    assert {s.identity for s in snapshot.sessions} == {("claude", "same"), ("codex", "same")}
    assert controller.last_reply(codex) == "Codex answer"
    assert calls == ["collect", ("codex", "same")]


def test_controller_discovery_keeps_outage_cache_and_takes_fresh_claude_inputs():
    from thechatplace.providers.base import Snapshot
    from thechatplace.sessions import SORT_STATUS, SessionInfo

    class Backend:
        provider_id = "codex"
        failed = False

        def collect(self):
            if self.failed:
                raise RuntimeError("private payload")
            return Snapshot(sessions=[SessionInfo("external", "codex:one", "Codex", "", "one",
                                                   provider="codex")])

    backend = Backend()
    controller = SessionController([backend])
    controller.collect(lambda: Snapshot(), SORT_STATUS)
    backend.failed = True
    claude = SessionInfo("own", "own:new", "New Claude", "", "new")
    snapshot = controller.collect(lambda: Snapshot(sessions=[claude]), SORT_STATUS)
    assert {s.key for s in snapshot.sessions} == {"codex:one", "own:new"}
    assert snapshot.stale_providers == {"codex"}
    assert "private" not in str(snapshot.provider_errors)
    assert controller._claude_discovery.collect_current is None
    controller.close()
    with pytest.raises(ValueError, match="closed"):
        controller.collect(lambda: Snapshot(), SORT_STATUS)


def test_overlapping_discovery_preserves_each_refresh_callback():
    from thechatplace.providers.base import Snapshot
    from thechatplace.sessions import SORT_STATUS, SessionInfo

    controller = SessionController([])
    entered, release = threading.Event(), threading.Event()

    def first():
        entered.set()
        assert release.wait(5)
        return Snapshot(sessions=[SessionInfo("own", "own:first", "First", "", "first")])

    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(controller.collect, first, SORT_STATUS)
        assert entered.wait(2)
        two = pool.submit(controller.collect, lambda: Snapshot(sessions=[
            SessionInfo("own", "own:second", "Second", "", "second")]), SORT_STATUS)
        release.set()
        assert [s.key for s in one.result(timeout=5).sessions] == ["own:first"]
        assert [s.key for s in two.result(timeout=5).sessions] == ["own:second"]
    assert controller._claude_discovery.collect_current is None


def test_capabilities_do_not_grant_codex_write_access_from_ownership():
    from thechatplace.providers.codex import CodexProvider
    from thechatplace.providers.base import SessionCapabilities

    class NeverConnect:
        def request(self, *args):
            raise AssertionError("Capabilities must not perform RPC")

    controller = SessionController([ClaudeConversations(), CodexProvider(NeverConnect())])
    owned_claude, owned_codex = info(), info("codex")
    assert controller.capabilities(owned_claude).send
    assert controller.capabilities(owned_claude).delete
    assert controller.capabilities(owned_codex) == SessionCapabilities(usage=True)
    assert not controller.capabilities(owned_codex).fork
    external = SessionInfo("external", "external:one", "External", "", "one")
    assert controller.capabilities(external).fork
    assert not controller.capabilities(external).send
    external.cowork = True
    assert not controller.capabilities(external).fork
    assert not controller.capabilities(owned_codex).share_link
    controller.close()
    with pytest.raises(ValueError, match="closed"):
        controller.capabilities(owned_codex)


def test_malformed_capability_flags_fail_closed():
    from thechatplace.providers.base import SessionCapabilities

    class Backend:
        provider_id = "codex"

        def capabilities(self, session):
            return SessionCapabilities(send="false")

    with pytest.raises(ValueError, match="Invalid provider capabilities"):
        SessionController([Backend()]).capabilities(info("codex"))


def test_owned_foreign_overlay_keeps_provider_identity_and_live_metadata():
    from thechatplace.own_store import OwnSession
    from thechatplace.providers.base import Snapshot
    from thechatplace.sessions import SORT_STATUS, WORKING

    foreign = info("codex")
    foreign.source = "external"
    foreign.state = WORKING
    foreign.archived = True

    class Backend:
        provider_id = "codex"

        def collect(self):
            return Snapshot(sessions=[foreign])

    owned = OwnSession(foreign.cli_session_id, "Local Codex title", "/local",
                       provider="codex", unread=True)
    claude = info("claude")
    claude.source = "external"
    controller = SessionController([Backend()])
    snap = controller.collect(lambda: Snapshot(sessions=[claude]), SORT_STATUS, owned=[owned])
    result = next(s for s in snap.sessions if s.provider == "codex")
    assert result.is_own and result.title == "Local Codex title" and result.unread
    assert result.state == WORKING and result.archived and result.cwd == foreign.cwd
    assert not next(s for s in snap.sessions if s.provider == "claude").is_own
    assert not foreign.is_own and foreign.title != owned.title
    assert not controller.capabilities(result).send


def test_owned_foreign_missing_catalog_is_unknown_and_disabled_provider_stays_hidden():
    from thechatplace.own_store import OwnSession
    from thechatplace.providers.base import Snapshot
    from thechatplace.sessions import SORT_STATUS

    owned = OwnSession("same", "Saved Codex", "/synthetic", provider="codex", unread=True)
    disabled = SessionController([])
    assert not disabled.collect(lambda: Snapshot(), SORT_STATUS, owned=[owned]).sessions

    class Backend:
        provider_id = "codex"

        def collect(self):
            return Snapshot()

    controller = SessionController([Backend()])
    snap = controller.collect(lambda: Snapshot(), SORT_STATUS, owned=[owned])
    assert len(snap.sessions) == 1
    result = snap.sessions[0]
    assert result.key == "codex:same" and result.is_own and result.unread
    assert result.state == "status unavailable" and not snap.fresh_states()
    assert not controller.capabilities(result).send


def test_properties_resolves_claude_path_once_and_reads_that_file(tmp_path, monkeypatch):
    from thechatplace.providers.claude import ClaudeConversations
    from records import assistant_block, text_block, lines
    path = tmp_path / 'same.jsonl'
    path.write_text('\n'.join(lines(assistant_block(text_block('Same file'), 'm'))) + '\n')
    info = SessionInfo('desktop', 'local_native', 'Fixture', '', 'native')
    calls = []
    def resolve(self):
        calls.append(self.identity)
        return path
    monkeypatch.setattr(SessionInfo, 'transcript_path', resolve)
    controller = SessionController([ClaudeConversations()])
    reader, name, size = controller.open_reader_with_facts(info)
    reader.refresh()
    assert calls == [('claude', 'native')]
    assert reader.transcript.messages[-1].text == 'Same file'
    assert name == str(path) and size == path.stat().st_size


def test_last_reply_routes_colliding_ids_and_unknown_find_is_counted():
    from thechatplace.search import Target, search_sessions
    claude = FakeProvider('claude', 'Claude reply')
    codex = FakeProvider('codex', 'Codex reply')
    controller = SessionController([claude, codex])
    assert controller.last_reply(info('claude')) == 'Claude reply'
    assert controller.last_reply(info('codex')) == 'Codex reply'
    targets = [Target('missing:one', 'Missing', lambda: None,
                      read_history=lambda: controller.read(info('missing')))]
    results = search_sessions(targets, 'reply')
    assert results.unreadable == 1 and not results.matches


def test_steer_is_offered_for_owned_chats_only_and_routed_to_the_runtime():
    # #238, #239: a capability, never inferred from ownership by the window.
    from thechatplace.providers.codex import CodexProvider
    from thechatplace.own_store import OwnSession

    class NeverConnect:
        def request(self, *args):
            raise AssertionError("Capabilities must not perform RPC")

    class Runtime:
        calls = []

        def steer(self, record, text, index=None):
            self.calls.append((record.cli_session_id, text, index))
            return "turn-1"

    runtime = Runtime()
    controller = SessionController([ClaudeConversations(),
                                    CodexProvider(NeverConnect(), runtime=runtime)])
    assert controller.capabilities(info()).steer
    assert controller.capabilities(info("codex")).steer
    external = SessionInfo("external", "external:one", "External", "", "one")
    assert not controller.capabilities(external).steer
    record = OwnSession("one", "Owned", "/w", provider="codex")
    assert controller.owned_action("steer", record, "Also the docs") == "turn-1"
    assert controller.owned_action("steer_queued", record, 2, "Queued") == "turn-1"
    assert runtime.calls == [("one", "Also the docs", None), ("one", "Queued", 2)]
    controller.close()
