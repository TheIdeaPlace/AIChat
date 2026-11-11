"""View, Connected Tools and connected tools' questions in the window (#468),
on a hidden window with every outside thing faked (``fake_env``): no real
claude, no browser, no speech."""
import sys

import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402
from thechatplace.claude_mcp import McpServer, elicitation_from  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402
from thechatplace.ui_text import key_name  # noqa: E402

import fake_env  # noqa: E402
from fake_env import FakeRunner, add_desktop, now_ms, pump  # noqa: E402
from markers import msaa  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def frame(env, monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace.ui.main_frame import MainFrame
    FakeRunner.instances = []
    monkeypatch.setattr(main_frame, "TurnRunner", FakeRunner)
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    folder = env["tmp"] / "Scratch"
    folder.mkdir()
    env["folder"] = str(folder)
    add_desktop(env, "local_a", "cli-a", "Desktop one", cwd=str(folder))
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("own-1", "Tools probe", str(folder), last_activity_ms=now_ms()))
    window = MainFrame(store=store, check_updates_at_start=False)
    assert pump(lambda: window.session_list.GetCount() == 2)
    yield window
    window._runners.clear()
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def select(frame, title):
    for i in range(frame.session_list.GetCount()):
        if frame.session_list.GetString(i).startswith(title):
            frame.session_list.SetSelection(i)
            return
    raise AssertionError(f"{title} not in list")


def _connected_tools(frame, monkeypatch, drive):
    """Open View, Connected Tools on the selected chat; ``drive(dialog)``
    runs once its list has been read."""
    from thechatplace.ui import dialogs
    seen = {}

    class Drives(dialogs.ConnectedToolsDialog):
        def ShowModal(self):
            assert pump(lambda: self.loaded, timeout=10)
            seen["result"] = drive(self)
            return wx.ID_CANCEL
    monkeypatch.setattr("thechatplace.ui.main_frame.ConnectedToolsDialog", Drives)
    frame.on_connected_tools()
    return seen.get("result")


def _key(dialog, code):
    event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(code)
    dialog._on_list_key(event)


def test_connected_tools_lists_each_tool_in_words(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        assert dialog.GetTitle() == "Connected Tools: Tools probe"
        assert dialog.list.GetName() == "Connected tools, 4"
        details = dialog.details.GetValue()
        return list(dialog.list.GetStrings()), details
    rows, details = _connected_tools(frame, monkeypatch, drive)
    assert rows == [
        "claude.ai Microsoft Learn: connected, claude.ai connector, 3 tools",
        "claude.ai Morningstar: needs sign-in, claude.ai connector",
        "github: failed: Connection closed, your settings",
        "notes (from the probe-tools plugin): connected, a plugin, 1 tool",
    ]
    assert "microsoft_docs_search" in details
    assert env["mcp"]["calls"][0] == ("open", env["folder"])
    # Closing the dialog ends the idle Claude Code.
    assert pump(lambda: env["mcp"]["closed"] == 1)


def test_turn_off_for_this_chat_keeps_it_out_of_the_next_turn(frame, env, monkeypatch):
    select(frame, "Tools probe")
    frame.on_open_session()

    def drive(dialog):
        dialog.list.SetSelection(1)
        dialog._show_selected()
        assert dialog.toggle_btn.GetLabel() == "Turn &Off for This Chat"
        _key(dialog, wx.WXK_DELETE)
        row = dialog.list.GetString(1)
        label = dialog.toggle_btn.GetLabel()
        return row, label, dialog.list.GetSelection()
    row, label, selected = _connected_tools(frame, monkeypatch, drive)
    assert row == "claude.ai Morningstar: off for this chat, claude.ai connector"
    assert label == "Turn &On for This Chat" and selected == 1
    assert frame.store.get("own-1").disabled_mcp == ["claude.ai Morningstar"]
    assert env["feedback"][-1] == ("claude.ai Morningstar is off for this chat from this "
                                   "chat's next turn.")
    frame.reply_text.SetValue("look it up")
    frame.on_send()
    assert FakeRunner.instances[-1].disabled_mcp == ["claude.ai Morningstar"]


def test_turn_on_again(frame, env, monkeypatch):
    frame.store.update("own-1", disabled_mcp=["github", "gone-server"])
    select(frame, "Tools probe")

    def drive(dialog):
        rows = list(dialog.list.GetStrings())
        dialog.list.SetSelection(2)
        dialog.on_toggle()
        # A new idle process: the old one was started without it.
        assert dialog.busy
        assert pump(lambda: not dialog.busy, timeout=10)
        return rows, list(dialog.list.GetStrings())
    before, after = _connected_tools(frame, monkeypatch, drive)
    assert before[2] == "github: off for this chat, your settings"
    assert before[-1] == "gone-server: off for this chat"
    assert after[2] == "github: failed: Connection closed, your settings"
    assert frame.store.get("own-1").disabled_mcp == ["gone-server"]
    assert env["feedback"][-1] == "github is on again for this chat from this chat's next turn."
    assert env["mcp"]["opened_with"]["disabled"] == ["gone-server"]
    assert pump(lambda: env["mcp"]["closed"] == 2)


def test_opening_the_list_doesnt_start_tools_the_chat_turned_off(frame, env, monkeypatch):
    frame.store.update("own-1", disabled_mcp=["github"])
    select(frame, "Tools probe")
    _connected_tools(frame, monkeypatch, lambda dialog: None)
    assert env["mcp"]["opened_with"]["disabled"] == ["github"]


def test_a_tool_turned_off_while_open_needs_no_new_process(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        dialog.list.SetSelection(2)
        dialog.on_toggle()
        assert not dialog.busy
    _connected_tools(frame, monkeypatch, drive)
    assert [call for call in env["mcp"]["calls"] if call[0] == "open"] == [("open", env["folder"])]


def test_a_chat_started_here_lists_its_session_tools_and_a_desktop_chat_doesnt(frame, env, monkeypatch):
    select(frame, "Tools probe")
    _connected_tools(frame, monkeypatch, lambda dialog: None)
    assert "ccd_session_mgmt" in env["mcp"]["opened_with"]["mcp_config"]
    assert '"--self", "own-1"' in env["mcp"]["opened_with"]["mcp_config"]
    select(frame, "Desktop one")
    _connected_tools(frame, monkeypatch, lambda dialog: None)
    assert env["mcp"]["opened_with"] == {"disabled": [], "mcp_config": None}


def test_a_server_only_named_like_the_session_tools_cant_be_turned_off(frame, env, monkeypatch):
    """Denying goes by name, which would take the real ones too (#468)."""
    env["mcp"]["servers"] = [{"name": "ccd_session_mgmt", "status": "connected", "scope": "project",
                              "source": "project"}]
    select(frame, "Tools probe")

    def drive(dialog):
        assert "The Chat Place" not in dialog.list.GetString(0)
        dialog.on_toggle()
    _connected_tools(frame, monkeypatch, drive)
    assert env["boxes"][-1] == "ccd_session_mgmt can't be turned off here."
    assert frame.store.get("own-1").disabled_mcp == []


def test_refresh_stays_on_the_row_and_says_the_result(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        count = len(env["feedback"])
        dialog.list.SetSelection(2)
        dialog.on_refresh()
        assert pump(lambda: not dialog.busy, timeout=10)
        assert dialog.list.GetSelection() == 2
        assert len(env["feedback"]) == count + 1
        assert env["feedback"][-1] == "4 connected tools, 1 needs sign-in, 1 failed"
        env["mcp"]["error"] = "Claude Code said no."
        dialog.on_refresh()
        assert pump(lambda: not dialog.busy, timeout=10)
        assert env["feedback"][-1] == "Couldn't read the connected tools. Claude Code said no."
    _connected_tools(frame, monkeypatch, drive)


def test_opening_the_dialog_says_nothing_of_its_own(frame, env, monkeypatch):
    select(frame, "Tools probe")
    count = len(env["feedback"])
    _connected_tools(frame, monkeypatch, lambda dialog: None)
    assert len(env["feedback"]) == count


def test_a_list_that_comes_back_as_nothing_doesnt_stick_on_reading(frame):
    from types import SimpleNamespace
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    host = SimpleNamespace(load=lambda done: done(None, ""), note=lambda: "A note.",
                           can_turn_off=False)
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        assert dialog.loaded and not dialog.busy
        assert "has no connected tools" in dialog.list.GetString(0)
    finally:
        dialog.Destroy()


def _codex_frame(record=None, work=None):
    from types import SimpleNamespace
    return SimpleNamespace(store=SimpleNamespace(get=lambda *a, **k: record),
                           sessions=SimpleNamespace(connected_tools=lambda info: []),
                           _codex_work=work or (lambda action, done: done(action(), False)))


def _codex_info(is_own=False):
    from types import SimpleNamespace
    return SimpleNamespace(cli_session_id="c-1", is_own=is_own, title="Chat")


@pytest.mark.parametrize("reason,said", [(None, "Codex could not say."), ("Codex is busy.", "Codex is busy.")])
def test_codex_failure_with_no_words_still_ends_the_wait(frame, reason, said):
    """_codex_work gives (None, True) when it has no words for a failure, which
    left the list on "Reading..." for good (#468)."""
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    host = CodexConnectedTools(_codex_frame(work=lambda action, done: done(reason, True)), _codex_info())
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        assert dialog.load_error == f"Couldn't read the connected tools. {said}"
        assert dialog.list.GetString(0) == dialog.load_error and not dialog.busy
    finally:
        dialog.Destroy()


def test_a_codex_chat_from_elsewhere_reads_as_read_only(frame, env):
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    host = CodexConnectedTools(_codex_frame(), _codex_info())
    host.load = lambda done: done([CodexTool("notes", "configured", off_for_chat=False,
                                             read_only=True)], "")
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        assert dialog.list.GetString(0) == "notes: configured, configured Codex MCP server"
        details = dialog.details.GetValue()
        assert "can't be changed here" in details and "off for this chat" not in details
        assert dialog.toggle_btn.GetLabel() == "Turn &Off for This Chat"
        dialog.on_toggle()
        assert env["boxes"][-1] == "Tools can be turned off only for chats The Chat Place started."
    finally:
        dialog.Destroy()


def test_codex_read_status_on_an_off_tool_says_a_copy_is_how(frame, env):
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    host = CodexConnectedTools(_codex_frame(), _codex_info())
    host.load = lambda done: done([CodexTool("notes", "off")], "")
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        dialog.on_reconnect()
        assert "Copy with Tool On makes a new copy" in env["boxes"][-1]
        assert "Turn it on first" not in env["boxes"][-1]
    finally:
        dialog.Destroy()


def test_codex_consent_is_parented_on_the_dialog_not_the_disabled_window(frame, monkeypatch):
    from types import SimpleNamespace
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    record = SimpleNamespace(cli_session_id="c-1", provider_options={})
    host = CodexConnectedTools(_codex_frame(record), _codex_info(is_own=True))
    host.load = lambda done: done([CodexTool("notes", "off")], "")
    parents = []
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: parents.append(a[3]) or wx.NO)
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        dialog.on_toggle()
        assert parents == [dialog]
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("off", [True, False])
def test_copy_with_tool_says_the_copy_keeps_the_originals_settings(frame, env, off):
    """A tools copy keeps the original's settings, as Rewind's and Branch's
    copies do: the question and the message on open say so (#596, decision 3)."""
    from types import SimpleNamespace
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_extras import copy_note
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    record = SimpleNamespace(cli_session_id="c-1", provider_options={"mcp_servers": [] if off else ["notes"]})
    copy = SimpleNamespace(title="Chat (tools copy)", forked_from=None)
    copy.to_info = lambda: SimpleNamespace(cli_session_id="c-2", is_own=True, title=copy.title)
    said, opened = [], []
    codex_frame = _codex_frame(record)
    codex_frame.sessions.copy_with_tools = lambda *a, **k: copy
    codex_frame.store.add = lambda record: None
    codex_frame._store_write = lambda write, record: True
    codex_frame._feedback = said.append
    codex_frame.open_session = opened.append
    codex_frame.refresh_sessions = lambda: None
    host = CodexConnectedTools(codex_frame, _codex_info(is_own=True))
    host.load = lambda done: done([CodexTool("notes", "connected", off_for_chat=off)], "")
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        count = len(env["boxes"])
        dialog.on_toggle()
        asked = env["boxes"][count:]
        assert len(asked) == 1
        assert asked[0].startswith("A new copy of this chat will be made, and this chat is kept. "
                                   + copy_note(tools=False) + " ")
        assert asked[0].endswith(" Make a new copy of this chat with notes " + ("on?" if off else "off?"))
        assert "keeps the original's permission mode" in asked[0] and "connected tools" not in asked[0]
        assert said[-1] == ("Opened Chat (tools copy). It keeps the original's settings. "
                            "The original chat is kept.")
        assert len(opened) == 1
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("off,button", [(True, "Copy with Tool On"), (False, "Copy with Tool Off")])
def test_delete_in_a_codex_chat_makes_no_copy_and_says_what_to_press(frame, env, monkeypatch, off, button):
    """Delete turned a tool off in a Claude chat, and in a Codex chat made a
    whole new chat from one key (#596): now it says which button makes one."""
    from types import SimpleNamespace
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    record = SimpleNamespace(cli_session_id="c-1", provider_options={"mcp_servers": [] if off else ["notes"]})
    copied = []
    codex_frame = _codex_frame(record)
    codex_frame.sessions.copy_with_tools = lambda *a: copied.append(a)
    host = CodexConnectedTools(codex_frame, _codex_info(is_own=True))
    host.load = lambda done: done([CodexTool("notes", "connected", off_for_chat=off)], "")
    asked = []
    monkeypatch.setattr(host, "set_off", lambda *a: asked.append(a))
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        count = len(env["boxes"])
        for code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE, wx.WXK_BACK):
            _key(dialog, code)
        assert asked == [] and copied == []
        said = env["boxes"][count:]
        assert len(said) == 3
        key = "" if sys.platform == "darwin" else " (Alt+O)"
        assert said[0] == f"In a Codex chat, a tool changes only in a new copy: press {button}{key} to make one."
        assert dialog.list.GetString(0).startswith("notes")
        # The button itself still makes the copy.
        dialog.on_toggle()
        assert len(asked) == 1 and asked[0][1] is (not off)
    finally:
        dialog.Destroy()


def test_delete_in_a_codex_chat_checks_first_as_the_button_does(frame, env, monkeypatch):
    """Delete said "press Copy with Tool Off" while the list was loading, after
    a failed read, on an empty list and on the session tools, which can't be
    turned off (#601's review): it answers as the button would instead."""
    from types import SimpleNamespace
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    record = SimpleNamespace(cli_session_id="c-1", provider_options={})
    host = CodexConnectedTools(_codex_frame(record), _codex_info(is_own=True))
    session_tools = CodexTool("ccd_session_mgmt", "connected", off_for_chat=False, is_session_tools=True)
    host.load = lambda done: done([session_tools], "")
    monkeypatch.setattr(host, "set_off", lambda *a: pytest.fail("Delete made a copy"))
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        _key(dialog, wx.WXK_DELETE)
        assert env["boxes"][-1] == f"{session_tools.label} can't be turned off here."
        dialog.busy = "Still reading the connected tools. Try again in a moment."
        _key(dialog, wx.WXK_DELETE)
        assert env["boxes"][-1] == dialog.busy
        dialog.busy = ""
        dialog._fill([])
        _key(dialog, wx.WXK_DELETE)
        assert env["boxes"][-1] == "Select a connected tool to change first."
        dialog._failed("Codex could not say.")
        _key(dialog, wx.WXK_DELETE)
        assert env["boxes"][-1] == ("Couldn't read the connected tools. Codex could not say. "
                                    "Press Refresh to try again.")
        assert not any("Copy with Tool" in box for box in env["boxes"])
    finally:
        dialog.Destroy()


def test_delete_in_a_codex_chat_from_elsewhere_still_says_why_not(frame, env):
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    host = CodexConnectedTools(_codex_frame(), _codex_info())
    host.load = lambda done: done([CodexTool("notes", "configured", off_for_chat=False, read_only=True)], "")
    dialog = ConnectedToolsDialog(frame, "Chat", host)
    try:
        _key(dialog, wx.WXK_DELETE)
        assert env["boxes"][-1] == "Tools can be turned off only for chats The Chat Place started."
    finally:
        dialog.Destroy()


def test_codex_sign_in_says_how_for_an_off_tool_and_a_chat_from_elsewhere(frame, env):
    """Sign In said "Open a chat started here with this tool enabled first."
    (#596): now it says the steps, which differ by case."""
    from types import SimpleNamespace
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    key = "" if sys.platform == "darwin" else " (Alt+O)"
    record = SimpleNamespace(cli_session_id="c-1", provider_options={})
    own = CodexConnectedTools(_codex_frame(record), _codex_info(is_own=True))
    own.load = lambda done: done([CodexTool("notes", "off")], "")
    elsewhere = CodexConnectedTools(_codex_frame(), _codex_info())
    elsewhere.load = lambda done: done([CodexTool("my notes", "needs-auth", off_for_chat=False, read_only=True)], "")
    expected = [
        "Couldn't start signing in to notes. Sign In works only in a chat with this tool on. Press "
        f"Copy with Tool On{key} to make a copy with it on, then sign in from Connected Tools in that copy.",
        "Couldn't start signing in to my notes. This chat wasn't started in The Chat Place. To sign in "
        "to this tool, use Codex in a terminal: codex mcp login 'my notes'."]
    for host, said in zip((own, elsewhere), expected):
        dialog = ConnectedToolsDialog(frame, "Chat", host)
        try:
            dialog.on_sign_in()
            assert env["boxes"][-1] == said
        finally:
            dialog.Destroy()


def test_a_copilot_chat_hears_which_chats_have_connected_tools(frame, env, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(frame, "_selected_session",
                        lambda: SimpleNamespace(provider="copilot", title="Chat"))
    frame.on_connected_tools()
    assert env["feedback"][-1] == ("Connected Tools is for Claude and Codex chats. Chat is a "
                                   "Copilot chat.")


def test_a_desktop_chat_can_be_read_but_not_changed(frame, env, monkeypatch):
    select(frame, "Desktop one")

    def drive(dialog):
        dialog.list.SetSelection(0)
        dialog.on_toggle()
        return dialog.details.GetValue()
    details = _connected_tools(frame, monkeypatch, drive)
    assert env["boxes"][-1] == "Tools can be turned off only for chats The Chat Place started."
    assert "can't be turned off for it here" in details


def test_sign_in_opens_the_page_and_says_what_to_do(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        dialog.list.SetSelection(1)
        dialog._show_selected()
        dialog.on_sign_in()
        assert pump(lambda: not dialog.busy, timeout=10)
    _connected_tools(frame, monkeypatch, drive)
    assert ("sign_in", "claude.ai Morningstar") in env["mcp"]["calls"]
    assert env["opened"] == ["https://claude.ai/api/organizations/org/mcp/start-auth/srv"]
    assert env["boxes"][-1].startswith(
        "Your browser opened claude.ai to sign in to claude.ai Morningstar. Sign in there, "
        "then come back and press Refresh.")


def _host(frame):
    from thechatplace.ui.mcp_host import ClaudeConnectedTools
    return ClaudeConnectedTools(frame, frame._selected_session())


def test_closing_during_sign_in_never_opens_the_browser(frame, env):
    select(frame, "Tools probe")
    host = _host(frame)
    answers = []
    server = McpServer("claude.ai Morningstar", "needs-auth")
    host.sign_in(server, lambda *a: answers.append(a))
    host.close()
    pump(lambda: False, timeout=0.5)
    assert env["opened"] == [] and answers == []


def test_an_address_the_system_refuses_is_said_and_the_dialog_is_not_left_busy(frame, env, monkeypatch):
    def refuses(url):
        raise ValueError("embedded null character")
    monkeypatch.setattr(platform_paths, "open_url", refuses)
    select(frame, "Tools probe")

    def drive(dialog):
        dialog.list.SetSelection(1)
        dialog.on_sign_in()
        assert pump(lambda: not dialog.busy, timeout=10)
    _connected_tools(frame, monkeypatch, drive)
    assert "Couldn't start signing in" in env["boxes"][-1]
    assert "embedded null character" in env["boxes"][-1]


def test_an_expired_sign_in_is_said_while_the_dialog_is_open(frame, env):
    select(frame, "Tools probe")
    host = _host(frame)
    host._say_expired(["own-oauth"])
    assert env["feedback"][-1].startswith("The sign-in for own-oauth expired after 15 minutes.")
    count = len(env["feedback"])
    host.close()
    host._say_expired(["own-oauth"])
    assert len(env["feedback"]) == count


def test_a_session_that_died_is_replaced_and_closed(frame, env):
    select(frame, "Tools probe")
    host = _host(frame)
    first = host._open()
    first.closed = True
    assert host._open() is not first
    assert env["mcp"]["closed"] == 1
    host.close()


def test_sign_in_on_a_connected_tool_says_so(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        dialog.list.SetSelection(0)
        dialog.on_sign_in()
    _connected_tools(frame, monkeypatch, drive)
    assert env["boxes"][-1] == "claude.ai Microsoft Learn is connected already."
    assert env["opened"] == []


def test_sign_in_on_a_removed_codex_tool_says_its_removed(frame, env, monkeypatch):
    # Off for this chat isn't what happened to it, and turning it on wouldn't help.
    from thechatplace.providers.codex_tools import CodexTool
    select(frame, "Tools probe")
    removed = CodexTool("gone", "", off_for_chat=False, missing=True)

    def drive(dialog):
        monkeypatch.setattr(dialog, "_selected", lambda: removed)
        dialog.on_sign_in()
    _connected_tools(frame, monkeypatch, drive)
    assert env["boxes"][-1] == removed.text()
    assert "off for this chat" not in env["boxes"][-1]
    assert ("sign_in", "gone") not in env["mcp"]["calls"]


def test_reconnect_reads_it_again(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        dialog.list.SetSelection(2)
        dialog.on_reconnect()
        assert pump(lambda: not dialog.busy, timeout=10)
        return dialog.list.GetString(2)
    row = _connected_tools(frame, monkeypatch, drive)
    assert row == "github: connected, your settings"
    assert env["boxes"][-1] == "github: connected, your settings"


def test_a_refusal_is_said_in_the_list(frame, env, monkeypatch):
    env["mcp"]["error"] = "This version of Claude Code can't do that."
    select(frame, "Tools probe")

    def drive(dialog):
        return list(dialog.list.GetStrings()), dialog.list.GetName()
    rows, name = _connected_tools(frame, monkeypatch, drive)
    assert rows == ["Couldn't read the connected tools. This version of Claude Code can't do "
                    "that."]
    assert name == "Connected tools"


def test_connected_tools_needs_a_folder_that_is_there(frame, env, monkeypatch):
    frame.store.update("own-1", cwd=str(env["tmp"] / "gone"))
    frame.refresh_sessions(force=True)
    assert pump(lambda: not frame._snapshot_busy)
    select(frame, "Tools probe")
    frame.session_list.SetFocus()
    frame.on_connected_tools()
    assert env["feedback"][-1] == ("Tools probe's folder isn't there, so Claude Code can't say "
                                   "which tools it has.")


# -- a turn's start ---------------------------------------------------------------------------

def _start(frame, servers):
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Tools probe", TurnEvent(
        "started", session_id="own-1", data={"model": "", "mcp_servers": servers}))


def test_a_turn_says_once_which_tools_claude_cant_use(frame, env):
    from thechatplace.speech import ANNOUNCE_FULL
    frame.speech.announce = ANNOUNCE_FULL
    servers = [{"name": "claude.ai Morningstar", "status": "needs-auth"},
               {"name": "claude.ai Microsoft Learn", "status": "connected"},
               {"name": "github", "status": "failed"}]
    _start(frame, servers)
    said = ("Tools probe: Claude can't use claude.ai Morningstar: it needs sign-in. Claude "
            "can't use github: it failed to connect. To fix this, or stop hearing it, use "
            "View, Connected Tools.")
    assert env["feedback"][-1] == said
    # Too long for the status bar, which would cut off what you can do.
    assert frame._status_latest == ("Claude can't use 2 connected tools. To fix this, or stop "
                                    "hearing it, use View, Connected Tools.")
    count = len(env["feedback"])
    _start(frame, servers)  # not again this run
    assert len(env["feedback"]) == count


def test_a_tool_turned_off_for_the_chat_isnt_mentioned(frame, env):
    from thechatplace.speech import ANNOUNCE_FULL
    frame.speech.announce = ANNOUNCE_FULL
    frame.store.update("own-1", disabled_mcp=["claude.ai Morningstar"])
    count = len(env["feedback"])
    _start(frame, [{"name": "claude.ai Morningstar", "status": "needs-auth"}])
    assert len(env["feedback"]) == count


def test_below_the_full_level_its_only_on_the_status_bar(frame, env):
    from thechatplace.speech import ANNOUNCE_SUMMARY
    frame.speech.announce = ANNOUNCE_SUMMARY
    count = len(env["feedback"])
    _start(frame, [{"name": "github", "status": "failed"}])
    assert len(env["feedback"]) == count
    assert frame._status_latest == ("Tools probe: Claude can't use github: it failed to "
                                    "connect. To reconnect, or stop hearing this, use View, "
                                    "Connected Tools.")


def test_turn_off_for_new_chats_stops_the_message_and_can_be_undone(frame, env, monkeypatch):
    from thechatplace.speech import ANNOUNCE_FULL, SpeechSettings
    frame.speech.announce = ANNOUNCE_FULL
    select(frame, "Tools probe")

    def off(dialog):
        dialog.list.SetSelection(1)
        dialog._show_selected()
        assert dialog.new_chats_btn.GetLabel() == "Turn Off for &New Chats"
        dialog.on_toggle_new_chats()
        assert dialog.list.GetSelection() == 1
        assert dialog.new_chats_btn.GetLabel() == "Turn On for &New Chats"
        assert "New chats start with this tool off" in dialog.details.GetValue()
        return dialog.list.GetString(1)
    assert _connected_tools(frame, monkeypatch, off) == (
        "claude.ai Morningstar: needs sign-in, claude.ai connector, off for new chats")
    assert env["feedback"][-1] == ("claude.ai Morningstar is off for new chats. "
                                   "You won't hear about it again.")
    assert SpeechSettings.load().mcp_off_for_new_chats == ["claude.ai Morningstar"]
    # This chat kept its own setting, and the message has stopped for it too.
    assert frame.store.get("own-1").disabled_mcp == []
    count = len(env["feedback"])
    _start(frame, [{"name": "claude.ai Morningstar", "status": "needs-auth"}])
    assert len(env["feedback"]) == count

    def on(dialog):
        dialog.list.SetSelection(1)
        dialog._show_selected()
        assert dialog.new_chats_btn.GetLabel() == "Turn On for &New Chats"
        dialog.on_toggle_new_chats()
        assert dialog.new_chats_btn.GetLabel() == "Turn Off for &New Chats"
    _connected_tools(frame, monkeypatch, on)
    assert env["feedback"][-1] == "claude.ai Morningstar is on for new chats."
    assert SpeechSettings.load().mcp_off_for_new_chats == []


def test_new_chats_start_with_those_tools_off(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    frame.speech.mcp_off_for_new_chats = ["claude.ai Morningstar"]

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            self.set_folder(env["folder"])
            self.message.SetValue("Hello")
            return wx.ID_OK
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    frame.on_new_session()
    assert pump(lambda: FakeRunner.instances, timeout=10)
    made = [s for s in frame.store.all() if s.cli_session_id != "own-1"]
    assert [s.disabled_mcp for s in made] == [["claude.ai Morningstar"]]
    # The chats you already have are left as they were.
    assert frame.store.get("own-1").disabled_mcp == []


def test_a_tool_not_in_this_folder_is_only_for_the_new_chats_button(frame, env, monkeypatch):
    # It's listed so it can be turned back on. Nothing else applies, each
    # says why, and turning it on keeps its row, so you stay on that tool.
    select(frame, "Tools probe")
    frame.speech.mcp_off_for_new_chats = ["elsewhere"]
    boxes = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text) or wx.OK)
    why = ("elsewhere isn't in this chat's folder. It's listed so you can change whether new "
           "chats have it; nothing else applies to it here.")

    def drive(dialog):
        rows = list(dialog.list.GetStrings())
        index = rows.index("elsewhere: off for new chats")
        dialog.list.SetSelection(index)
        dialog._show_selected()
        assert why in dialog.details.GetValue()
        dialog.on_sign_in()
        dialog.on_reconnect()
        dialog.on_toggle()
        _key(dialog, wx.WXK_DELETE)
        dialog.on_toggle_new_chats()
        assert dialog.list.GetString(index) == "elsewhere: on for new chats"
        assert dialog.list.GetSelection() == index
        assert dialog.new_chats_btn.GetLabel() == "Turn Off for &New Chats"
        assert frame.speech.mcp_off_for_new_chats == []
        dialog.on_toggle_new_chats()     # pressed again: the same tool, undone
        assert dialog.list.GetString(index) == "elsewhere: off for new chats"
        assert frame.speech.mcp_off_for_new_chats == ["elsewhere"]
    _connected_tools(frame, monkeypatch, drive)
    assert boxes == [why] * 4
    assert frame.store.get("own-1").disabled_mcp == []
    assert env["mcp"]["calls"].count(("sign_in", "elsewhere")) == 0


def test_a_save_that_fails_changes_nothing_for_new_chats(frame, env, monkeypatch):
    select(frame, "Tools probe")
    boxes = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text) or wx.OK)

    def refuse():
        raise OSError("disk full")
    monkeypatch.setattr(frame.speech, "save", refuse)

    def drive(dialog):
        dialog.list.SetSelection(1)
        dialog._show_selected()
        dialog.on_toggle_new_chats()
        return dialog.new_chats_btn.GetLabel()
    assert _connected_tools(frame, monkeypatch, drive) == "Turn Off for &New Chats"
    assert boxes == ["It couldn't be saved, so nothing changed."]
    assert frame.speech.mcp_off_for_new_chats == []


def test_the_chat_places_own_tools_cant_be_turned_off_for_new_chats(frame, env, monkeypatch):
    from thechatplace.session_tools import SERVER_NAME
    select(frame, "Tools probe")
    boxes = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text) or wx.OK)

    def drive(dialog):
        dialog._fill([McpServer(SERVER_NAME, "connected", source="dynamic")])
        dialog.on_toggle_new_chats()
    _connected_tools(frame, monkeypatch, drive)
    assert boxes and boxes[0].endswith("can't be turned off here.")
    assert frame.speech.mcp_off_for_new_chats == []


# -- a connected tool's question ----------------------------------------------------------------

FORM = {"subtype": "elicitation", "mcp_server_name": "elicit-test",
        "message": "Where would you like to go, and for how long?", "mode": "form",
        "requested_schema": {"type": "object", "properties": {
            "city": {"type": "string", "title": "City", "description": "Which city to plan for"},
            "nights": {"type": "integer", "title": "Nights", "minimum": 1, "maximum": 14},
            "pets": {"type": "boolean", "title": "Bringing pets", "default": False},
            "budget": {"type": "string", "title": "Budget", "enum": ["low", "medium", "high"],
                       "enumNames": ["Low", "Medium", "High"]}},
            "required": ["city", "nights"]}}
PAGE = {"subtype": "elicitation", "mcp_server_name": "elicit-test",
        "message": "Open this page to finish signing in.", "mode": "url",
        "url": "https://example.com/sign-in", "elicitation_id": "elic-123"}


def _asked(frame, request):
    select(frame, "Tools probe")
    frame.on_open_session()
    runner = FakeRunner([], "", "", None)
    frame._runners["own-1"] = runner
    frame._on_turn_event({"id": "own-1"}, "Tools probe",
                         TurnEvent("permission", text=request.summary(), request=request))
    return runner


def _answer_with(monkeypatch, drive):
    from thechatplace.ui import dialogs

    class Drives(dialogs.ElicitationDialog):
        def ShowModal(self):
            ended = []
            self.EndModal = ended.append
            drive(self)
            return ended[-1] if ended else wx.ID_CANCEL
    monkeypatch.setattr("thechatplace.ui.main_frame.ElicitationDialog", Drives)


def test_a_question_is_announced_and_the_chat_needs_you(frame, env):
    _asked(frame, elicitation_from("e1", FORM))
    assert env["spoken"][-1] == key_name(
        "Tools probe needs you. elicit-test asks: Where would you like to go, and for how "
        "long?. Ctrl+Shift+A answers.")


def test_a_form_is_sent_with_what_was_entered(frame, env, monkeypatch):
    runner = _asked(frame, elicitation_from("e1", FORM))

    def drive(dialog):
        assert dialog.GetTitle() == "elicit-test asks: Tools probe"
        assert "Where would you like to go" in dialog.message_text.GetValue()
        dialog.control_for("city").SetValue("Seattle")
        dialog.control_for("nights").SetValue("3")
        dialog.control_for("pets").SetValue(True)
        dialog.control_for("budget").SetSelection(2)  # (none), Low, Medium
        dialog._on_send()
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert runner.responses == [("e1", {"action": "accept", "content": {
        "city": "Seattle", "nights": 3, "pets": True, "budget": "medium"}})]
    assert env["feedback"][-1] == "Sent to elicit-test."
    assert frame._pending["own-1"] == []


def test_a_form_says_whats_missing_and_stays_open(frame, env, monkeypatch):
    runner = _asked(frame, elicitation_from("e1", FORM))
    seen = {}

    def drive(dialog):
        dialog.control_for("city").SetValue("Oslo")
        dialog.control_for("nights").SetValue("40")
        dialog._on_send()
        seen["focus"] = wx.Window.FindFocus() is dialog.control_for("nights")
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert env["boxes"][-1] == "Nights must be between 1 and 14."
    assert not hasattr(runner, "responses")  # answered later
    assert len(frame._pending["own-1"]) == 1
    assert "still waiting" in env["feedback"][-1]


def test_decline(frame, env, monkeypatch):
    runner = _asked(frame, elicitation_from("e1", FORM))
    _answer_with(monkeypatch, lambda dialog: dialog._on_decline())
    frame.on_answer()
    assert runner.responses == [("e1", {"action": "decline"})]
    assert env["feedback"][-1] == "Declined. elicit-test has your answer."


def test_a_page_is_opened_and_that_is_the_answer(frame, env, monkeypatch):
    runner = _asked(frame, elicitation_from("e2", PAGE))

    def drive(dialog):
        assert dialog.send_btn.GetLabel() == "&Open in Browser"
        assert dialog.address_text.GetValue() == ("Opens example.com\n"
                                                  "https://example.com/sign-in")
        assert "example.com" not in dialog.message_text.GetValue()
        dialog._on_send()
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert env["opened"] == ["https://example.com/sign-in"]
    assert runner.responses == [("e2", {"action": "accept"})]
    assert env["feedback"][-1].startswith("Opened the page in your browser.")


def test_a_page_that_isnt_a_web_address_is_never_opened(frame, env, monkeypatch):
    runner = _asked(frame, elicitation_from("e3", {**PAGE, "url": "file:///C:/x.exe"}))

    def drive(dialog):
        assert not dialog.send_btn.IsEnabled()
        assert "won't open this" in dialog.address_text.GetValue()
        dialog._on_send()  # does nothing
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert env["opened"] == [] and not hasattr(runner, "responses")


@pytest.mark.parametrize("url", [
    "https://good.example@evil.example/", "http://example.com/", "https://evil.example/\x00",
    "https://evil.example/\u202e"])
def test_an_address_that_hides_where_it_goes_is_never_opened(frame, env, monkeypatch, url):
    runner = _asked(frame, elicitation_from("e3", {**PAGE, "url": url}))

    def drive(dialog):
        assert not dialog.send_btn.IsEnabled()
        assert "\x00" not in dialog.address_text.GetValue()
        dialog._on_send()
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert env["opened"] == [] and not hasattr(runner, "responses")


def test_a_message_cant_get_ahead_of_the_address_or_pose_as_it(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    request = elicitation_from("e", {**PAGE, "url": "https://evil.example/",
                                     "message": "Opens bank.example\nPage: https://bank.example/"})
    dialog = ElicitationDialog(frame, "Tools probe", request)
    try:
        assert dialog.address_text.GetValue().splitlines()[0] == "Opens evil.example"
        assert "evil.example" not in dialog.message_text.GetValue()
        # Made first, so read first and reached first (a hidden dialog has no
        # layout to compare).
        children = list(dialog.GetChildren())
        assert children.index(dialog.address_text) < children.index(dialog.message_text)
        assert dialog.address_text.GetName() == "Address"
    finally:
        dialog.Destroy()


def test_refused_system_open_after_the_answer_went_is_said_and_carries_on(frame, env, monkeypatch):
    runner = _asked(frame, elicitation_from("e2", PAGE))

    def refuses(url):
        raise ValueError("embedded null character")
    monkeypatch.setattr(platform_paths, "open_url", refuses)
    _answer_with(monkeypatch, lambda dialog: dialog._on_send())
    frame.on_answer()
    assert runner.responses == [("e2", {"action": "accept"})]
    assert env["feedback"][-1].startswith("Couldn't open your browser")
    assert frame._pending["own-1"] == []


def _enter(dialog, monkeypatch, focus):
    """Whether Enter, pressed with ``focus``, goes on to the default button."""
    monkeypatch.setattr(dialog, "_focus", lambda: focus)
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    dialog._on_key(event)
    return event.GetSkipped()


def test_enter_in_the_message_doesnt_send_what_the_server_filled_in(frame, env, monkeypatch):
    """Answer then Enter used to send a form untouched, even a ticked "yes,
    delete it" (#468)."""
    from thechatplace.ui.dialogs import ElicitationDialog
    schema = {"type": "object", "properties": {
        "confirm_delete": {"type": "boolean", "default": True},
        "why": {"type": "string"}, "what": {"type": "string", "enum": ["a", "b"]}}}
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    try:
        assert not _enter(dialog, monkeypatch, dialog.message_text)
        assert not _enter(dialog, monkeypatch, None)
        # From inside the form, and on a button, Enter does what it usually does.
        for control in (dialog.control_for("why"), dialog.control_for("confirm_delete"),
                        dialog.control_for("what"), dialog.send_btn, dialog.decline_btn):
            assert _enter(dialog, monkeypatch, control)
        other = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        other.SetKeyCode(wx.WXK_TAB)
        monkeypatch.setattr(dialog, "_focus", lambda: dialog.message_text)
        dialog._on_key(other)
        assert other.GetSkipped()  # Tab still moves on
    finally:
        dialog.Destroy()


def test_shift_enter_in_a_form_s_text_box_sends_nothing(frame, env, monkeypatch):
    """Shift+Enter starts a new line in the Answer dialog's own answers (#591);
    in a one-line box, where there's no line to start, it used to send the
    form, as Windows presses the default button for it. Now it only beeps;
    Enter still sends."""
    from thechatplace.ui import dialogs
    schema = {"type": "object", "properties": {
        "why": {"type": "string"}, "what": {"type": "string", "enum": ["a", "b"]}}}
    dialog = dialogs.ElicitationDialog(frame, "Tools probe",
                                       elicitation_from("e", {**FORM, "requested_schema": schema}))
    beeps = []
    monkeypatch.setattr(dialogs.wx, "Bell", lambda: beeps.append(1))
    try:
        why = dialog.control_for("why")

        def press(focus, shift):
            monkeypatch.setattr(dialog, "_focus", lambda: focus)
            monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus))
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.SetKeyCode(wx.WXK_RETURN)
            event.SetShiftDown(shift)
            dialog._on_key(event)
            return event.GetSkipped()
        assert not press(why, True) and beeps == [1]  # taken: no default button
        assert press(why, False) and beeps == [1]  # Enter goes on to Send
        assert press(dialog.control_for("what"), True)  # not a text box: as before
    finally:
        dialog.Destroy()


def test_enter_in_the_message_says_where_to_go(frame, env, monkeypatch):
    # A key that did nothing and said nothing (#468).
    runner = _asked(frame, elicitation_from("e1", FORM))

    def drive(dialog):
        count = len(env["feedback"])
        assert not _enter(dialog, monkeypatch, dialog.message_text)
        assert env["feedback"][count:] == ["Tab to the form, or to Send."]
        # Nothing to say from where Enter works.
        assert _enter(dialog, monkeypatch, dialog.send_btn)
        assert len(env["feedback"]) == count + 1
        dialog._on_decline()
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert runner.responses == [("e1", {"action": "decline"})]


def test_a_form_with_nothing_to_fill_in_doesnt_send_from_the_message(frame, env, monkeypatch):
    # "Delete the production table?" with no fields: accept is the yes, so
    # Answer then a stray Enter must not confirm it (#468).
    from thechatplace.claude_mcp import ACCEPT
    from thechatplace.ui.dialogs import ElicitationDialog
    said = []
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": {
                                   "type": "object", "properties": {}}}), feedback=said.append)
    ended = []
    dialog.EndModal = ended.append
    try:
        assert dialog.fields == [] and dialog.send_btn is not None
        assert dialog.form_panel is None
        assert not _enter(dialog, monkeypatch, dialog.message_text)
        assert not _enter(dialog, monkeypatch, None)
        assert ended == [] and dialog.action == ""
        assert said == ["Tab to Send, or to Decline."] * 2
        # Send is the next stop after the message, with no form in between.
        children = [c for c in dialog.GetChildren() if c.IsEnabled() and c.AcceptsFocus()]
        assert children[children.index(dialog.message_text) + 1] is dialog.send_btn
        # On Send, Enter is Send's own: Windows then presses it.
        assert _enter(dialog, monkeypatch, dialog.send_btn)
        dialog._on_send()
        assert ended == [wx.ID_OK] and dialog.action == ACCEPT and dialog.content == {}
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("schema", [
    None, {}, {"type": "object"}, {"type": "object", "properties": {"a": "x"}}])
def test_a_schema_that_isnt_one_has_only_decline_and_later(frame, env, schema):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    try:
        assert dialog.send_btn is None and dialog.form_panel is None
        assert "not a list of fields" in dialog.note_text.GetValue()
    finally:
        dialog.Destroy()


def test_enter_on_a_page_says_where_to_go_and_opens_nothing(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ElicitationDialog
    said = []
    page = ElicitationDialog(frame, "Tools probe", elicitation_from("e", PAGE),
                             feedback=said.append)
    ended = []
    page.EndModal = ended.append
    try:
        for box in (page.address_text, page.message_text):
            assert not _enter(page, monkeypatch, box)
        assert said == ["Tab to Open in Browser, or to Decline."] * 2
        assert ended == [] and page.action == "" and env["opened"] == []
        # The default stays Decline, and a focused button keeps its own Enter.
        assert page.decline_btn.GetId() == page.GetDefaultItem().GetId()
        assert _enter(page, monkeypatch, page.decline_btn)
    finally:
        page.Destroy()


def test_enter_on_a_page_that_wont_open_doesnt_offer_to_open_it(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ElicitationDialog
    said = []
    page = ElicitationDialog(frame, "Tools probe",
                             elicitation_from("e", {**PAGE, "url": "file:///C:/x.exe"}),
                             feedback=said.append)
    try:
        assert not _enter(page, monkeypatch, page.address_text)
        assert said == ["Tab to Decline."]
    finally:
        page.Destroy()


def test_a_form_it_cant_show_has_only_decline_and_later(frame, env, monkeypatch):
    schema = {"type": "object", "required": ["trip"], "properties": {
        "trip": {"type": "object", "title": "Trip", "properties": {}}}}
    runner = _asked(frame, elicitation_from("e1", {**FORM, "requested_schema": schema}))

    def drive(dialog):
        assert dialog.send_btn is None and dialog.fields == []
        assert "can't show (Trip)" in dialog.note_text.GetValue()
        # What Answer Later does, as the form will never be shown (#596).
        assert dialog.note_text.GetValue().endswith(
            "Decline tells the tool no. Answer Later leaves the turn waiting until you come "
            "back and decline, or stop the turn.")
        assert dialog.note_text.GetName() == "Note"
        assert dialog.form_panel is None
        assert "Where would you like to go" in dialog.message_text.GetValue()
        count = len(env["feedback"])
        for box in (dialog.note_text, dialog.message_text):
            assert not _enter(dialog, monkeypatch, box)
        assert env["feedback"][count:] == ["Tab to Decline."] * 2
        assert dialog.action == ""
        assert _enter(dialog, monkeypatch, dialog.decline_btn)
        dialog._on_decline()
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert runner.responses == [("e1", {"action": "decline"})]


def test_too_long_a_form_has_only_decline_and_later(frame, env):
    from thechatplace.claude_mcp import MAX_FORM_FIELDS
    from thechatplace.ui.dialogs import ElicitationDialog
    schema = {"properties": {f"f{i}": {"type": "string"} for i in range(MAX_FORM_FIELDS + 1)}}
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    try:
        assert dialog.send_btn is None and dialog.form_panel is None
        assert "too long" in dialog.note_text.GetValue()
    finally:
        dialog.Destroy()


LONG = ("A description long enough that it can't possibly fit on one line of the dialog, however "
        "wide its font is, so it has to wrap rather than run off the edge of the form. " * 2)


def test_long_labels_and_descriptions_wrap(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    schema = {"properties": {
        "one": {"type": "boolean", "title": LONG, "description": LONG},
        "two": {"type": "string", "title": LONG, "description": LONG},
        "three": {"type": "array", "title": "Three", "description": LONG,
                  "items": {"enum": ["a"], "enumNames": [LONG]}},
        "four": {"type": "string", "title": "Four", "enum": [LONG]}}}
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    try:
        assert dialog.form_panel.GetScrollPixelsPerUnit()[0] == 0  # up and down only
        assert not dialog.form_panel.HasFlag(wx.HSCROLL)
        limit = dialog.GetClientSize().width
        assert "\n" in dialog.control_for("one").GetLabel()
        assert "\n" in dialog.control_for("three").GetLabel()
        for child in dialog.form_panel.GetChildren():
            if not isinstance(child, wx.StaticBox):
                assert child.GetBestSize().width <= limit or isinstance(child, wx.Choice)
        assert dialog.control_for("four").GetMinSize().width < limit
        # What a screen reader hears isn't broken up.
        assert dialog.control_for("one").GetName().startswith(LONG)
    finally:
        dialog.Destroy()


MANY = {"subtype": "elicitation", "mcp_server_name": "elicit-test", "message": "Pick.",
        "mode": "form", "requested_schema": {"type": "object", "required": ["extras"], "properties": {
            "extras": {"type": "array", "title": "Extras", "description": "What to add",
                       "items": {"enum": ["breakfast", "parking"],
                                 "enumNames": ["Breakfast", "Parking"]}},
            "pets": {"type": "boolean", "title": "Pets", "description": "Any at all"},
            "size": {"type": "string", "title": "Size", "description": "How big",
                     "enum": ["s", "l"]}}}}


def test_choosing_several_is_a_group_and_its_first_box_says_the_rest(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe", elicitation_from("e", MANY))
    try:
        first, second = [box for _value, box in dialog._controls["extras"]]
        assert first.GetName() == "Extras, required: What to add: Breakfast"
        assert second.GetName() == "Extras: Parking"
        # By wx's class name: a wrapper from GetChildren isn't always a StaticBox.
        groups = [c for c in dialog.form_panel.GetChildren() if c.GetClassName() == "wxStaticBox"]
        assert [g.GetLabel() for g in groups] == ["&Extras (required)"]
        # Siblings of the box, not its children: see QuestionDialog.
        assert first.GetParent() is dialog.form_panel
    finally:
        dialog.Destroy()


@msaa
def test_a_forms_check_boxes_and_lists_are_named_through_msaa(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe", elicitation_from("e", MANY))
    try:
        for control, name in (
                (dialog.control_for("extras"), "Extras, required: What to add: Breakfast"),
                (dialog._controls["extras"][1][1], "Extras: Parking"),
                (dialog.control_for("pets"), "Pets: Any at all"),
                (dialog.control_for("size"), "Size: How big")):
            assert control.GetAccessible().GetName(0) == (wx.ACC_OK, name)
    finally:
        dialog.Destroy()


def test_the_form_is_labelled_for_a_screen_reader(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe", elicitation_from("e1", FORM))
    try:
        assert dialog.control_for("city").GetName() == "City, required: Which city to plan for"
        assert dialog.control_for("nights").GetName() == "Nights, required: between 1 and 14"
        assert dialog.control_for("pets").GetLabel() == "&Bringing pets"
        assert dialog.control_for("budget").GetStrings() == ["(none)", "Low", "Medium", "High"]
        assert dialog.control_for("budget").GetSelection() == 0
        shown = [c.GetLabel() for c in dialog.form_panel.GetChildren()
                 if c.GetClassName() == "wxStaticText"]
        assert "Between 1 and 14" in shown
    finally:
        dialog.Destroy()


def test_each_field_has_its_own_access_key(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    schema = {"type": "object", "required": ["story"], "properties": {
        # Its first letters are the dialog's own: Send and Decline.
        "story": {"type": "string", "title": "Send & deliver"},
        "city": {"type": "string", "title": "City"},
        "cats": {"type": "boolean", "title": "Cats"},
        "size": {"type": "string", "title": "Colour", "enum": ["a"]},
        "extras": {"type": "array", "title": "Extras", "items": {"enum": ["one", "two"]}}}}
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    try:
        labels = [c.GetLabel() for c in dialog.form_panel.GetChildren()
                  if c.GetClassName() in ("wxStaticText", "wxCheckBox", "wxStaticBox")]
        assert "S&end && deliver (required):" in labels  # "(required)" gives no key
        assert "&City:" in labels
        assert "C&ats" in labels
        assert "C&olour:" in labels
        assert "E&xtras" in labels
    finally:
        dialog.Destroy()


def test_alt_goes_to_a_choose_several_fields_first_box(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe", elicitation_from("e", MANY))
    moved = []
    first = dialog._controls["extras"][0][1]
    monkeypatch.setattr(first, "SetFocus", lambda: moved.append(True))
    assert dialog._group_keys[ord("E")] is first

    def press(key, alt=True):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord(key))
        event.SetAltDown(alt)
        dialog._on_key(event)
        return event.GetSkipped()
    try:
        assert press("Q")
        assert press("E", alt=False)
        assert not moved
        if sys.platform == "darwin":
            assert press("E") and not moved  # Option and a letter types
        else:
            assert not press("E") and moved
    finally:
        dialog.Destroy()


def test_alt_on_a_check_box_moves_to_it_and_leaves_it_as_it_was(frame, env, monkeypatch):
    # Windows ticks a check box on its Alt key as well as moving to it, so one
    # a server had ticked could be turned off just by going to it (#468).
    from thechatplace.ui.dialogs import ElicitationDialog
    schema = {"type": "object", "properties": {
        "pets": {"type": "boolean", "title": "Bringing pets", "default": True}}}
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    box = dialog.control_for("pets")
    moved = []
    monkeypatch.setattr(box, "SetFocus", lambda: moved.append(True))
    try:
        assert box.GetLabel() == "&Bringing pets"  # still shown as its key
        assert dialog._group_keys == {ord("B"): box}
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("B"))
        event.SetAltDown(True)
        dialog._on_key(event)
        if sys.platform == "darwin":
            assert event.GetSkipped() and not moved  # Option and a letter types
        else:
            assert not event.GetSkipped() and moved == [True]
        assert box.GetValue() is True
    finally:
        dialog.Destroy()


def test_a_forms_boxes_sit_under_their_labels_and_line_up(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe", elicitation_from("e", FORM))
    try:
        dialog.Layout()
        panel = dialog.form_panel
        city = dialog.control_for("city")
        field_x = panel.GetPosition().x + city.GetPosition().x
        assert field_x == dialog.message_text.GetPosition().x
        item = dialog.GetSizer().GetItem(dialog.message_text)
        assert item.GetFlag() & wx.TOP == 0
        # A field's box likewise: 8 pixels above it was four times the Message's.
        assert panel.GetSizer().GetItem(city).GetFlag() & wx.TOP == 0
    finally:
        dialog.Destroy()


def test_a_forms_wrapped_words_reach_the_boxes_right_edge(frame, env):
    # They stopped 45 to 70 pixels short of it.
    from thechatplace.ui.dialogs import ElicitationDialog
    schema = {"properties": {
        "one": {"type": "string", "title": LONG},
        "two": {"type": "string", "title": "Two"}}}
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "requested_schema": schema}))
    try:
        dialog.Layout()
        box_right = (dialog.control_for("two").GetPosition().x
                     + dialog.control_for("two").GetSize().width)
        label = next(c for c in dialog.form_panel.GetChildren()
                     if c.GetClassName() == "wxStaticText" and "\n" in c.GetLabel())
        # Within a word and a scroll bar's width of it, and never past it.
        right = label.GetPosition().x + label.GetBestSize().width
        room = max(wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, dialog), 0)
        assert right <= box_right
        assert dialog._wrap_width == dialog.GetClientSize().width - 16 - room
    finally:
        dialog.Destroy()


@msaa
def test_a_read_only_boxs_text_is_not_parsed_for_a_shortcut(frame, env):
    # "Pick & choose" read as the box having a shortcut of "Alt+ " (#468).
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe",
                               elicitation_from("e", {**FORM, "message": "Pick & choose"}))
    try:
        box = dialog.message_text
        assert "Pick & choose" in box.GetValue()
        assert box.GetAccessible().GetKeyboardShortcut(0) == (wx.ACC_OK, "")
        assert box.GetAccessible().GetName(0) == (wx.ACC_OK, "Message")
        # A field beneath its label keeps Windows' own reading of its key.
        city = dialog.control_for("city").GetAccessible()
        assert not isinstance(city, type(box.GetAccessible()))
    finally:
        dialog.Destroy()
