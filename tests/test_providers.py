"""Discovery parity and identity guards before adding another backend."""
from thechatplace import hub
from thechatplace.own_store import OwnSession
from thechatplace.providers.base import Snapshot
from thechatplace.providers.claude import ClaudeProvider
from thechatplace.sessions import NEEDS_YOU, OWN, SessionInfo


def test_hub_routes_discovery_through_claude_adapter(monkeypatch, tmp_path):
    expected = Snapshot(desktop_cli_ids={"external"})
    calls = []

    def collect(provider):
        calls.append(provider)
        return expected

    monkeypatch.setattr(ClaudeProvider, "collect", collect)
    own = OwnSession("mine", "My chat", str(tmp_path))
    result = hub.collect([own], {"mine"}, desktop_dir=tmp_path,
                         waiting={"mine": "Answer a question"})
    assert result is expected
    assert calls[0].own == [own]
    assert calls[0].waiting == {"mine": "Answer a question"}
    assert result.desktop_cli_ids == {"external"}


def test_claude_waiting_state_and_legacy_keys_survive_adapter(tmp_path):
    own = OwnSession("mine", "My chat", str(tmp_path))
    snapshot = hub.collect([own], {"mine"}, desktop_dir=tmp_path / "desktop",
                           live_dir=tmp_path / "live",
                           waiting={"mine": "Approve a command"})
    info, = snapshot.sessions
    assert info.key == "own:mine"
    assert info.identity == ("claude", "mine")
    assert (info.state, info.detail) == (NEEDS_YOU, "Approve a command")


def test_equal_native_ids_have_distinct_backend_identities():
    claude = SessionInfo(OWN, "own:same", "Claude", "", "same")
    codex = SessionInfo(OWN, "codex:same", "Codex", "", "same", provider="codex")
    assert claude.identity != codex.identity


def test_other_backend_never_uses_claude_transcript_paths(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Looked for a foreign chat in Claude's files")

    monkeypatch.setattr(hub.platform_paths, "transcript_path", unexpected)
    monkeypatch.setattr(hub.platform_paths, "direct_transcript_path", unexpected)
    info = SessionInfo(OWN, "codex:same", "Chat", "", "same",
                       desktop_session_id="local_same", provider="codex")
    assert info.transcript_path() is None
    assert info.transcript_written_ms() == 0
    assert not info.can_open_in_claude


def test_claude_discovery_ignores_foreign_owned_records_with_colliding_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "terminal_projects_dir", lambda: tmp_path / "projects")
    claude = OwnSession("same", "Claude owned", str(tmp_path))
    codex = OwnSession("same", "Codex owned", str(tmp_path), provider="codex")
    snapshot = hub.collect([claude, codex], {"same"}, desktop_dir=tmp_path / "desktop",
                           live_dir=tmp_path / "live")
    assert [(s.provider, s.key, s.title) for s in snapshot.sessions] == [
        ("claude", "own:same", "Claude owned")]


def test_native_identity_never_uses_presentation_key():
    info = SessionInfo('desktop', 'desktop:local_native', 'Fixture', '', '',
                       desktop_session_id='local_native')
    assert info.identity == ('claude', 'local_native')
    info.desktop_session_id = ''
    assert info.identity == ('claude', '')
