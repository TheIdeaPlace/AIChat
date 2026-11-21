"""Read-only Muse history presentation, registration, and UI recovery."""

import pytest

wx = pytest.importorskip("wx")
import fake_env

from thechatplace.sessions import (IDLE, STATUS_UNAVAILABLE, VIEW_MUSE, SessionInfo,
                                    in_view, provider_label)


@pytest.fixture
def frame(tmp_path, monkeypatch, app):
    from thechatplace.own_store import OwnSessionStore
    from thechatplace.ui.main_frame import MainFrame

    env = fake_env.install(tmp_path, monkeypatch.setattr)
    fake_env.add_desktop(env, "local_muse_ui", "cli-muse-ui", "Muse UI fixture")
    window = MainFrame(store=OwnSessionStore(tmp_path / "own.json"),
                       check_updates_at_start=False)
    assert fake_env.pump(lambda: window.session_list.GetCount() == 1)
    yield window
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def test_muse_is_a_listed_read_only_provider_with_a_named_filter():
    info = SessionInfo("external", "muse:one", "Muse task", "C:/repo", "one",
                       provider="muse", provider_label="Muse Code", state=IDLE)
    assert provider_label("muse") == "Muse Code"
    assert in_view(info, VIEW_MUSE)
    assert "Muse Code" in info.list_line(fields=["title", "provider", "kind"])


def test_shared_provider_labels_name_muse_without_granting_agent_membership():
    from thechatplace import agents

    assert agents.provider_name("muse") == "Muse Code"
    assert "muse" not in agents.MEMBER_PROVIDERS


def test_properties_use_the_muse_code_display_name():
    from thechatplace.properties import session_properties

    info = SessionInfo("external", "muse:one", "Muse task", "C:/repo", "one",
                       provider="muse", state=STATUS_UNAVAILABLE)
    assert "Provider: Muse Code" in session_properties(info)


def test_muse_unavailable_rows_keep_the_status_detail():
    info = SessionInfo("external", "muse:one", "Muse task", "C:/repo", "one",
                       provider="muse", state=STATUS_UNAVAILABLE,
                       detail="Muse Code history is unavailable")
    assert "Muse Code history is unavailable" in info.list_line()


def test_muse_provider_is_not_editable_in_the_session_controller():
    from thechatplace.session_controller import SessionController

    class MuseReader:
        provider_id = "muse"
        def has_history(self, session): return True
        def open_reader(self, session): return None
        def last_reply(self, session): return ""
        def capabilities(self, session):
            from thechatplace.providers.base import SessionCapabilities
            return SessionCapabilities(read=True)

    capabilities = SessionController([MuseReader()]).capabilities(
        SessionInfo("external", "muse:one", "Muse task", "", "one", provider="muse"))
    assert capabilities.read and not capabilities.send


def _muse_wire_session(native_id="muse-ui-1"):
    return {"sessionId": native_id, "name": "Muse UI history", "workspaceRoot": "C:/muse",
            "createdAt": "2026-11-01T10:00:00Z", "updatedAt": "2026-11-02T11:00:00Z"}


class _MuseHistoryConnection:
    def __init__(self):
        self.failed = False
        self.closed = False

    def request(self, method, params):
        if self.failed:
            from thechatplace.providers.muse_connection import MuseHistoryError
            raise MuseHistoryError("synthetic connection outage")
        native_id = params["sessionId"]
        return {"session": _muse_wire_session(native_id),
                "history": {"mode": "inline", "items": [
                    {"kind": "userMessage", "itemId": "u", "revision": 1,
                     "status": "completed", "text": "Read this history."},
                    {"kind": "agentMessage", "itemId": "a", "revision": 1,
                     "status": "completed", "text": "Muse history is readable."}],
                            "snapshot": None},
                "viewCursor": "cursor-1", "pendingRequests": []}

    def close(self):
        self.closed = True


def _open_real_muse(frame, connection):
    from thechatplace.providers.muse import MuseProvider, session_from_muse
    from thechatplace.session_controller import SessionController

    info = session_from_muse(_muse_wire_session())
    frame.sessions = SessionController([MuseProvider(connection=connection)])
    frame.open_session(info)
    from fake_env import pump
    assert pump(lambda: frame._chat_loaded and not frame._reader_busy)
    return info


def test_muse_reader_loads_as_read_only_with_native_provider(frame):
    connection = _MuseHistoryConnection()
    info = _open_real_muse(frame, connection)
    assert info.provider == "muse"
    assert [message.text for message in frame._chat_messages] == [
        "Read this history.", "Muse history is readable."]
    assert "Muse Code" in frame.session_heading.GetLabel()
    assert not frame.own_reply.IsShown()
    assert frame.desktop_note.GetValue().startswith("This is a Muse Code chat")


def test_muse_history_failure_keeps_messages_and_announces_once_then_recovers(
        frame, monkeypatch):
    connection = _MuseHistoryConnection()
    _open_real_muse(frame, connection)
    frame.chat_list.SetFocus()
    # GTK may leave a hidden test window unfocused. Compare the observed
    # focus; a physical keyboard/screen-reader pass is recorded separately.
    prior_focus = wx.Window.FindFocus()
    feedback = []
    monkeypatch.setattr(frame, "_feedback", lambda text: feedback.append(text))
    connection.failed = True
    frame._refresh_chat()
    from fake_env import pump
    assert pump(lambda: not frame._reader_busy)
    assert [message.text for message in frame._chat_messages] == [
        "Read this history.", "Muse history is readable."]
    assert wx.Window.FindFocus() is prior_focus
    assert len(feedback) == 1
    assert "Muse Code history is unavailable" in frame._status_latest
    frame._refresh_chat()
    assert pump(lambda: not frame._reader_busy)
    assert len(feedback) == 1
    connection.failed = False
    frame._refresh_chat()
    assert pump(lambda: not frame._reader_busy)
    assert frame._muse_history_error_keys == set()
    assert not frame._status_latest


def test_initial_muse_history_failure_is_announced_and_recovers(frame, monkeypatch):
    connection = _MuseHistoryConnection()
    connection.failed = True
    feedback = []
    original = frame._feedback
    def record(text):
        feedback.append(text)
        original(text)
    monkeypatch.setattr(frame, "_feedback", record)
    _open_real_muse(frame, connection)
    assert "Couldn't load this Muse Code chat" in frame.chat_list.GetString(0)
    assert frame._status_latest == "Muse Code history is unavailable. Try refreshing."
    assert sum("Muse Code history is unavailable" in text for text in feedback) == 1
    frame._refresh_chat()
    assert fake_env.pump(lambda: not frame._reader_busy)
    assert sum("Muse Code history is unavailable" in text for text in feedback) == 1
    connection.failed = False
    frame._refresh_chat()
    assert fake_env.pump(lambda: not frame._reader_busy)
    assert frame._chat_messages[-1].text == "Muse history is readable."
    assert not frame._muse_history_error_keys
    assert frame._status_latest.startswith("Loaded Muse UI history, 2 messages.")


def test_muse_stale_or_closed_error_callbacks_do_not_announce(frame, monkeypatch):
    from thechatplace.providers.muse import session_from_muse
    info = session_from_muse(_muse_wire_session())
    frame._open = info
    frame._open_generation = 8
    frame._chat_loaded = True
    frame._chat_messages = []
    frame._reader_busy = True
    notices, refreshes = [], []
    monkeypatch.setattr(frame, "_feedback", lambda text: notices.append(text))
    monkeypatch.setattr(frame, "_status", lambda text: notices.append(text))
    monkeypatch.setattr(frame, "_refresh_chat", lambda: refreshes.append(True))
    frame._apply_chat(7, False, [], 0, RuntimeError("late"))
    assert notices == [] and refreshes == [True]
    frame._closing = True
    frame._apply_chat(8, False, [], 0, RuntimeError("closed"))
    assert notices == []


def test_muse_registration_is_lazy_and_does_not_start_a_host(frame, monkeypatch):
    from thechatplace import platform_paths
    from thechatplace.providers import muse
    from thechatplace.providers.base import Snapshot

    created = []

    class ProbeProvider:
        provider_id = "muse"

        def __init__(self, **kwargs):
            created.append(True)

        def set_owned_persistence(self, callback):
            pass

        def collect(self):
            return Snapshot()

        def close(self):
            pass

    monkeypatch.setattr(platform_paths, "find_muse",
                        lambda: platform_paths.MuseLookup("muse.exe"))
    monkeypatch.setattr(muse, "MuseProvider", ProbeProvider)
    from thechatplace.ui.main_frame import MainFrame
    window = MainFrame(check_updates_at_start=False)
    try:
        assert created and "muse" in window.sessions._providers
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        window.Destroy()


def test_muse_is_not_registered_when_executable_is_absent(frame):
    assert "muse" not in frame.sessions._providers


@pytest.mark.parametrize("key", [wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER])
def test_discovered_muse_row_opens_with_enter_from_provider_filter(frame, monkeypatch, key):
    from thechatplace.providers.base import Snapshot
    from thechatplace.providers.muse import MuseProvider
    from thechatplace.session_controller import SessionController
    from thechatplace.sessions import SORT_STATUS

    class CatalogConnection(_MuseHistoryConnection):
        def request(self, method, params):
            if method == "session/list":
                return {"sessions": [_muse_wire_session()], "nextCursor": None}
            return super().request(method, params)

    frame._list_timer.Stop()
    frame.sessions = SessionController([MuseProvider(connection=CatalogConnection())])
    frame.sessions._discovery.PROVIDER_WAIT_SECONDS = 1
    snapshot = frame.sessions.collect(lambda: Snapshot(), SORT_STATUS)
    frame._apply_snapshot(snapshot, [], {}, False)
    frame.on_view(VIEW_MUSE)
    assert frame.session_list.GetCount() == 1
    frame.session_list.SetSelection(0)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(key)
    frame._on_char_hook(event)
    assert fake_env.pump(lambda: frame._chat_loaded and not frame._reader_busy)
    assert frame._open.identity == ("muse", "muse-ui-1")
    assert frame._chat_messages[-1].text == "Muse history is readable."


@pytest.mark.parametrize("state,expected", [
    ("empty", "No Muse Code conversations were found"),
    ("pending", "Checking Muse Code conversations"),
    ("failed", "Muse Code conversations couldn't be checked"),
    ("absent", "Muse Code wasn't found when this app started"),
    ("filtered", "No Muse Code conversations match this view"),
])
def test_empty_muse_view_and_enter_explain_discovery_state(frame, monkeypatch, state, expected):
    from thechatplace.providers.base import Snapshot
    from thechatplace.providers.muse import MuseProvider, session_from_muse
    from thechatplace.session_controller import SessionController

    frame._list_timer.Stop()
    providers = [] if state == "absent" else [MuseProvider(connection=_MuseHistoryConnection())]
    frame.sessions = SessionController(providers)
    snapshot = Snapshot(sessions=list(frame._snapshot.sessions))
    if state == "pending":
        snapshot.pending_providers.add("muse")
    elif state == "failed":
        snapshot.stale_providers.add("muse")
    elif state == "filtered":
        snapshot.sessions.append(session_from_muse(_muse_wire_session()))
        frame._session_filter = "no matching conversation"
    frame._apply_snapshot(snapshot, [], {}, False)
    feedback = []
    monkeypatch.setattr(frame, "_feedback", lambda text: feedback.append(text))
    frame.on_view(VIEW_MUSE)
    assert frame.session_list.GetCount() == 0
    assert expected in frame.chat_list.GetString(0)
    assert expected in feedback[-1]
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    frame._on_char_hook(event)
    assert expected in feedback[-1]
    assert frame._open is None
    assert "No session selected" not in feedback[-1]


