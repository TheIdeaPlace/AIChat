"""View, Connected Tools and connected tools' questions in the window (#468),
on a hidden window with every outside thing faked (``fake_env``): no real
claude, no browser, no speech."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402
from thechatplace.claude_mcp import elicitation_from  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402
from thechatplace.ui_text import key_name  # noqa: E402

import fake_env  # noqa: E402
from fake_env import FakeRunner, add_desktop, now_ms, pump  # noqa: E402


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
        return rows, list(dialog.list.GetStrings())
    before, after = _connected_tools(frame, monkeypatch, drive)
    assert before[2] == "github: off for this chat, your settings"
    assert before[-1] == "gone-server: off for this chat, no longer set up in Claude Code"
    assert after[2] == "github: failed: Connection closed, your settings"
    assert frame.store.get("own-1").disabled_mcp == ["gone-server"]
    assert env["feedback"][-1] == "github is on again for this chat from this chat's next turn."


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


def test_sign_in_on_a_connected_tool_says_so(frame, env, monkeypatch):
    select(frame, "Tools probe")

    def drive(dialog):
        dialog.list.SetSelection(0)
        dialog.on_sign_in()
    _connected_tools(frame, monkeypatch, drive)
    assert env["boxes"][-1] == "claude.ai Microsoft Learn is connected already."
    assert env["opened"] == []


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
            "can't use github: it failed to connect. View, Connected Tools.")
    assert env["feedback"][-1] == said
    assert frame._status_latest == said
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
                                    "connect. View, Connected Tools.")


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
        assert "Page: https://example.com/sign-in" in dialog.message_text.GetValue()
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
        assert "won't open it" in dialog.message_text.GetValue()
        dialog._on_send()  # does nothing
    _answer_with(monkeypatch, drive)
    frame.on_answer()
    assert env["opened"] == [] and not hasattr(runner, "responses")


def test_the_form_is_labelled_for_a_screen_reader(frame, env):
    from thechatplace.ui.dialogs import ElicitationDialog
    dialog = ElicitationDialog(frame, "Tools probe", elicitation_from("e1", FORM))
    try:
        assert dialog.control_for("city").GetName() == "City, required: Which city to plan for"
        assert dialog.control_for("nights").GetName() == "Nights, required"
        assert dialog.control_for("pets").GetLabel() == "Bringing pets"
        assert dialog.control_for("budget").GetStrings() == ["(none)", "Low", "Medium", "High"]
        assert dialog.control_for("budget").GetSelection() == 0
    finally:
        dialog.Destroy()
