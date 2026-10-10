"""The window's logic, exercised on a frame that is never shown.

No window appears on screen: the frame is created hidden and destroyed at the
end, and speech is replaced by a recorder. The real look and sound with JAWS
and NVDA is checked by hand (see the README).
"""
import dataclasses
import json
import threading
import time

import pytest

wx = pytest.importorskip("wx")

from thechatplace import hub, platform_paths, speech  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402
from thechatplace.sessions import NEEDS_YOU  # noqa: E402

from records import (assistant_block, lines, text_block, tool_result, tool_use_block,  # noqa: E402
                     user_text)
from markers import msaa, voiceover, windows_paths  # noqa: E402
import fake_env  # noqa: E402
from fake_env import (FAKE_COMMANDS, FakeRunner, add_desktop, add_transcript,  # noqa: E402,F401
                      now_ms, pump)


@pytest.fixture(scope="module")
def app():
    instance = wx.App(False)
    yield instance


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


def settle(frame):
    """Let any background list refresh land."""
    pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None)


@pytest.fixture
def frame(env):
    from thechatplace.ui.main_frame import MainFrame
    add_desktop(env, "local_a", "cli-a", "Quiet one")
    add_desktop(env, "local_b", "cli-b", "Blocked one",
                postTurnSummary={"status_category": "blocked", "needs_action": "Pick a name"})
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("own-1", "Hub probe", "C:\\G\\Scratch", last_activity_ms=now_ms()))
    window = MainFrame(store=store, check_updates_at_start=False)
    assert not window.IsShown()
    assert pump(lambda: window.session_list.GetCount() == 3)
    yield window
    window._runners.clear()
    # All of them, as closing does: a delayed call left running (the
    # sign-in check, 4 seconds in) would fire into this destroyed window
    # during a later test, which crashes wx on a Mac.
    window.stop_timers()
    window._pool.shutdown(wait=True)
    # What the background work handed back runs now, while the window is
    # whole, not after Destroy, when a Mac still has it half there.
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def select(frame, title):
    for i in range(frame.session_list.GetCount()):
        if frame.session_list.GetString(i).startswith(title):
            frame.session_list.SetSelection(i)
            return i
    raise AssertionError(f"{title} not in list")


@pytest.fixture
def fake_runner(monkeypatch):
    from thechatplace.ui import main_frame
    FakeRunner.instances = []
    monkeypatch.setattr(main_frame, "TurnRunner", FakeRunner)
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    return FakeRunner


# -- list ------------------------------------------------------------------------------


def test_list_order_and_lines(frame):
    items = list(frame.session_list.GetStrings())
    assert items[0].startswith("Blocked one, Repo, needs you: Pick a name")
    assert items[1].startswith("Hub probe, Scratch, idle")
    assert items[1].endswith("Chat Place session")
    assert items[2].startswith("Quiet one, Repo, idle, active 1 minute ago")
    assert frame.session_list.GetSelection() == 0


def test_refresh_keeps_selection_on_same_session(frame, env):
    select(frame, "Quiet one")
    add_desktop(env, "local_c", "cli-c", "Newcomer",
                postTurnSummary={"status_category": "blocked"})
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    assert frame.session_list.GetStringSelection().startswith("Quiet one")


def test_focused_list_keeps_its_order_until_f5(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    select(frame, "Quiet one")
    before = [s.split(",")[0] for s in frame.session_list.GetStrings()]
    # Quiet one now needs Kelly, which would sort it first.
    add_desktop(env, "local_a", "cli-a", "Quiet one",
                postTurnSummary={"status_category": "blocked", "needs_action": "Look"})
    add_desktop(env, "local_d", "cli-d", "Brand new")
    frame.refresh_sessions()
    assert pump(lambda: frame.session_list.GetCount() == 4)
    settle(frame)
    rows = [s.split(",")[0] for s in frame.session_list.GetStrings()]
    assert rows == before + ["Brand new"]           # nothing moved, new one at the end
    assert frame.session_list.GetStringSelection().startswith("Quiet one, Repo, needs you")
    frame.refresh_sessions(force=True, resort=True)  # F5
    assert pump(lambda: frame.session_list.GetString(0).startswith("Quiet one"))
    assert frame.session_list.GetStringSelection().startswith("Quiet one")


def test_focused_list_removes_vanished_rows_in_place(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    index = select(frame, "Hub probe")
    frame.store.remove("own-1")
    frame.refresh_sessions()
    assert pump(lambda: frame.session_list.GetCount() == 2)
    assert frame.session_list.GetSelection() == min(index, 1)


# -- session view ----------------------------------------------------------------------


def tab_order(frame):
    """The focusable controls in Tab order, as the window would visit them."""
    order = []

    def walk(window):
        for child in window.GetChildren():
            if isinstance(child, wx.TopLevelWindow) or not child.IsShown():
                continue
            if isinstance(child, wx.Panel):
                walk(child)  # a container: its controls are the Tab stops
            elif child.AcceptsFocusFromKeyboard() and child.IsEnabled():
                order.append(child)
            else:
                walk(child)
    walk(frame)
    return order


def test_desktop_session_loads_in_the_same_window(frame, env):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Please check the build"),
        assistant_block(text_block("It passes.\nAll green."), "m1"),
    ])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame.chat_list.GetCount() == 2 and frame._chat_loaded)
    assert list(frame.chat_list.GetStrings()) == ["You: Please check the build",
                                                  "Claude: It passes."]
    assert frame.chat_list.GetSelection() == 1
    # The session list is still there, on the same session.
    assert frame.session_list.IsShown()
    assert frame.session_list.GetStringSelection().startswith("Quiet one")
    assert not frame.own_reply.IsShown() and frame.desktop_reply.IsShown()
    # State and read-only are in the list's label, which is its accessible name.
    assert frame.messages_label.GetLabel() == "&Messages in Quiet one (idle, read-only):"
    assert frame.chat_list.GetName() == "Messages in Quiet one (idle, read-only)"
    assert env["feedback"][-1] == "Loaded Quiet one, 2 messages."
    frame.on_send()  # must do nothing for a desktop session
    assert frame._runners == {}
    frame.on_open_in_claude()
    assert env["opened"] == ["claude://claude.ai/epitaxy/local_a"]
    assert env["feedback"][-1] == "Opened Quiet one in Claude."


def test_tab_order_own_session(frame):
    select(frame, "Hub probe")
    frame.on_open_session()
    order = tab_order(frame)
    assert order == [frame.session_list, frame.chat_list, frame.reply_text, frame.send_btn,
                     frame.stop_btn, frame.commands_btn, frame.attach_btn, frame.activity_check,
                     frame.new_btn, frame.refresh_btn]
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._update_send_state()
    # Issue #175: a running turn doesn't move anything. Tab, Enter from the
    # reply box is Send, never Stop.
    assert tab_order(frame) == order


def test_tab_order_desktop_session_puts_the_note_where_the_reply_box_is(frame):
    select(frame, "Quiet one")
    frame.on_open_session()
    order = tab_order(frame)
    assert order == [frame.session_list, frame.chat_list, frame.desktop_note,
                     frame.reply_claude_btn, frame.continue_btn, frame.activity_check,
                     frame.new_btn, frame.refresh_btn]


def test_nothing_loaded_at_start(frame):
    assert frame._open is None
    assert frame.chat_list.GetString(0).startswith("No session loaded")
    assert not frame.own_reply.IsShown() and not frame.desktop_reply.IsShown()


def test_arrowing_the_session_list_does_not_load(frame):
    select(frame, "Quiet one")
    frame.session_list.SetSelection(0)
    wx.GetApp().ProcessPendingEvents()
    assert frame._open is None


def test_escape_and_ctrl_shortcuts_move_between_the_three_parts(frame, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    focused = []
    for name in ("session_list", "chat_list", "reply_text", "desktop_note"):
        control = getattr(frame, name)
        monkeypatch.setattr(control, "SetFocus",
                            lambda n=name: focused.append(n), raising=False)
    frame.focus_sessions()
    frame.focus_messages()
    frame.focus_reply()
    assert focused == ["session_list", "chat_list", "reply_text"]
    # Escape from the reply box goes to the session list, still on Hub probe.
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.reply_text))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_ESCAPE)
    frame._on_char_hook(event)
    assert focused[-1] == "session_list"
    assert frame.session_list.GetStringSelection().startswith("Hub probe")
    assert frame._open is not None  # still loaded


def test_enter_on_a_message_opens_its_full_text_and_returns_to_it(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("First"), assistant_block(text_block("Line one\nLine two"), "m1"),
        user_text("Third")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame.chat_list.SetSelection(1)
    shown = []

    class FakeMessageDialog:
        def __init__(self, parent, label, text):
            shown.append((label, text))

        def ShowModal(self):
            return wx.ID_CANCEL  # Escape

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "MessageDialog", FakeMessageDialog)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    frame._on_char_hook(event)
    assert shown == [("Claude", "Line one\nLine two")]
    assert frame.chat_list.GetSelection() == 1  # same message


def test_return_on_a_focused_button_presses_it_on_a_mac(frame, monkeypatch):
    # On a Mac, Return on Send did nothing; only Space pressed it.
    monkeypatch.setattr(wx, "Platform", "__WXMAC__")
    pressed = []
    for button in (frame.send_btn, frame.attach_btn):
        button.Bind(wx.EVT_BUTTON, lambda e, b=button: pressed.append(b))

    def press(focus, key=wx.WXK_RETURN, shift=False):
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(key)
        event.SetShiftDown(shift)
        frame._on_char_hook(event)
        return event.GetSkipped()

    assert not press(frame.send_btn) and pressed == [frame.send_btn]
    assert not press(frame.attach_btn, wx.WXK_NUMPAD_ENTER) and pressed[-1] is frame.attach_btn
    assert press(frame.send_btn, shift=True) and len(pressed) == 2  # modified: left alone
    monkeypatch.setattr(wx, "Platform", "__WXMSW__")
    press(frame.send_btn)
    assert len(pressed) == 2  # Windows presses the button itself


def test_message_dialog_is_a_labelled_read_only_rich_edit(frame):
    from thechatplace.ui.dialogs import MessageDialog
    dialog = MessageDialog(frame, "Claude", "Line one\nLine two")
    try:
        assert dialog.text.GetValue() == "Line one\nLine two"
        assert not dialog.text.IsEditable()
        assert dialog.text.GetWindowStyle() & wx.TE_RICH2
        assert dialog.text.GetName() == "Claude said"
        assert dialog.GetEscapeId() == wx.ID_CANCEL
    finally:
        dialog.Destroy()


def test_live_refresh_keeps_the_selected_message(frame, env):
    path = add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Go"), assistant_block(text_block("Line one\nLine two"), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame.chat_list.SetSelection(0)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_block(text_block("Line three"), "m1")) + "\n")
        handle.write(json.dumps(user_text("Another")) + "\n")
    frame._refresh_chat()
    assert pump(lambda: frame.chat_list.GetCount() == 3)
    assert frame.chat_list.GetSelection() == 0


def test_needs_you_state_is_in_the_label(frame):
    select(frame, "Blocked one")
    frame.on_open_session()
    assert "needs you: Pick a name, read-only" in frame.messages_label.GetLabel()


def test_new_message_in_open_session_is_announced(frame, env):
    path = add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Go")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame.chat_list.SetSelection(0)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_block(text_block("Finished the job."), "m2")) + "\n")
    frame._refresh_chat()
    assert pump(lambda: frame.chat_list.GetCount() == 2)
    assert env["spoken"][-1] == "Quiet one replied. Finished the job."
    assert frame.chat_list.GetSelection() == 0  # the reader stays put


def test_missing_transcript_says_so(frame):
    select(frame, "Blocked one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    assert frame.chat_list.GetString(0).startswith("No transcript")


def test_tool_activity_toggle_keeps_the_place(frame, env):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("q"),
        assistant_block(tool_use_block("Bash", {"command": "ls"}, "t1"), "m1"),
        assistant_block(text_block("done"), "m2"),
        user_text("thanks"),
    ])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    assert frame.chat_list.GetCount() == 3
    frame.chat_list.SetSelection(0)  # "You: q"
    frame._set_activity(True)
    assert list(frame.chat_list.GetStrings()) == ["You: q", "Tool: Bash: ls",
                                                  "Claude: done", "You: thanks"]
    assert frame.chat_list.GetStringSelection() == "You: q"
    assert env["feedback"][-1] == "Tool activity shown in Quiet one."
    frame.chat_list.SetSelection(1)  # on the tool call, then hide tools
    frame._set_activity(False)
    assert frame.chat_list.GetCount() == 3
    assert frame.chat_list.GetStringSelection() == "You: q"  # nearest before it


def test_tool_activity_is_each_sessions_own_and_remembered(frame, env):
    """#162: turning it on for one session leaves the others as they were,
    and it's back on when the session is loaded again, even after a restart."""
    from thechatplace.hidden import ToolActivityStore
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("q"), assistant_block(tool_use_block("Bash", {"command": "ls"}, "t1"), "m1"),
        assistant_block(text_block("done"), "m2")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame._set_activity(True)
    assert frame.chat_list.GetCount() == 3
    quiet = frame._open.key
    select(frame, "Hub probe")
    frame.on_open_session()
    assert not frame._show_activity
    assert not frame.activity_item.IsChecked() and not frame.activity_check.GetValue()
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    assert frame._show_activity and frame.activity_item.IsChecked()
    assert frame.activity_check.GetValue()
    assert frame.chat_list.GetCount() == 3  # the tool call is shown again
    assert ToolActivityStore().keys() == [quiet]  # kept on disk for next time
    frame._set_activity(False)
    assert ToolActivityStore().keys() == []
    assert env["feedback"][-1] == "Tool activity hidden in Quiet one."


def test_tool_activity_with_no_session_loaded_says_so(frame, env):
    frame._set_activity(True)
    assert not frame._show_activity and not frame.activity_item.IsChecked()
    assert not frame.activity_check.GetValue()
    assert env["feedback"][-1] == ("No session loaded. Show Tool Activity is set for "
                                   "each session.")


def test_tool_activity_from_the_menu_with_no_session_stays_unchecked(frame, env):
    """wx checks the item before the handler runs; the handler must win."""
    frame.activity_item.Check(True)
    frame.on_toggle_activity_menu(None)
    assert not frame.activity_item.IsChecked() and not frame.activity_check.GetValue()
    frame.activity_check.SetValue(True)
    frame.on_toggle_activity_check(None)
    assert not frame.activity_check.GetValue() and not frame.activity_item.IsChecked()


def test_unloading_a_session_unchecks_tool_activity(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._set_activity(True)
    frame.unload_session()
    assert not frame._show_activity
    assert not frame.activity_item.IsChecked() and not frame.activity_check.GetValue()
    assert "own:own-1" in frame.tool_activity  # still kept for next time


def test_tool_activity_follows_a_session_given_a_new_id(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._set_activity(True)
    frame.reply_text.SetValue("go")
    frame.on_send()
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("started", session_id="own-9"))
    assert frame.tool_activity.keys() == ["own:own-9"]
    assert frame._show_activity and frame.activity_check.GetValue()


def test_deleting_a_session_forgets_its_tool_activity(frame, env):
    add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._set_activity(True)
    frame.on_delete_permanently()  # MessageBox stub answers Yes
    assert "own:own-1" not in frame.tool_activity


def test_export_of_a_session_not_loaded_uses_its_own_tool_activity(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("q"), assistant_block(tool_use_block("Bash", {"command": "ls"}, "t1"), "m1"),
        assistant_block(text_block("done"), "m2")])
    select(frame, "Quiet one")  # selected, not loaded
    target = env["tmp"] / "plain.md"
    _fake_save_dialog(monkeypatch, target)
    frame.on_export()
    assert pump(lambda: env["feedback"][-1].startswith("Exported"))
    assert "ls" not in target.read_text(encoding="utf-8")
    frame.tool_activity.add(frame._list_keys[frame.session_list.GetSelection()])
    target = env["tmp"] / "tools.md"
    _fake_save_dialog(monkeypatch, target)
    frame.on_export()
    assert pump(lambda: "tools.md" in env["feedback"][-1])
    assert "ls" in target.read_text(encoding="utf-8")


def test_tool_activity_that_cant_be_saved_still_shows_and_says_so(frame, env, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()

    def refuse(_key):
        raise OSError("disk full")
    monkeypatch.setattr(frame.tool_activity, "add", refuse)
    frame._set_activity(True)
    assert frame._show_activity
    assert env["feedback"][-1] == ("Tool activity shown in Hub probe. The Chat Place couldn't "
                                   "save this, so it won't be remembered.")


# -- own sessions ------------------------------------------------------------------------


def test_own_session_turn_finish_with_denials(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.own_reply.IsShown() and not frame.desktop_reply.IsShown()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._denials["own-1"] = []
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "finished", text="I couldn't write the file.",
        denials=["Write was refused: C:/x/probe.txt"]))
    assert frame._runners == {}
    session = frame.store.get("own-1")
    assert session.state == NEEDS_YOU and session.detail == "1 tool was refused"
    assert not session.unread  # it was open
    assert env["spoken"][-1] == ("Hub probe replied. I couldn't write the file. 1 tool was "
                                 "refused: Write was refused: C:/x/probe.txt")
    assert frame.send_btn.IsEnabled()


def test_open_in_claude_refused_for_own_session(frame, env):
    select(frame, "Hub probe")
    frame.on_open_in_claude()
    assert env["opened"] == []
    assert "started by The Chat Place" in env["boxes"][0]


def test_send_speaks_confirmation_and_one_turn_at_a_time(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Next step please")
    frame.on_send()
    runner = fake_runner.instances[0]
    assert runner.command[-2:] == ["--resume", "own-1"]
    assert (runner.cwd, runner.prompt) == ("C:\\G\\Scratch", "Next step please")
    assert env["feedback"][-1] == "Sent to Hub probe: Next step please."
    assert frame.send_btn.IsEnabled() and frame.stop_btn.IsEnabled()
    assert frame.turn_status.GetLabel() == "Claude is working (1 minute 15 seconds)."
    frame.reply_text.SetValue("again")
    frame.on_send()
    assert len(fake_runner.instances) == 1  # queued, not a second turn
    assert env["feedback"][-1] == "Queued for Hub probe: again."
    frame.on_turn_status()
    assert env["feedback"][-1] == ("Hub probe: 1 message queued. Claude has been working for "
                                   "1 minute 15 seconds, last starting.")


def test_send_refused_when_busy_elsewhere(frame, env, fake_runner, monkeypatch):
    (env["live"] / "4242.json").write_text(json.dumps(
        {"pid": 4242, "sessionId": "own-1", "status": "busy"}))
    original = hub.load_live_status
    monkeypatch.setattr(hub, "load_live_status",
                        lambda directory=None, alive=None, started=None: original(
                            directory, alive=lambda pid: True, started=lambda pid: None))
    frame.refresh_sessions(force=True)
    assert pump(lambda: "own-1" in frame._snapshot.live)
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("hello")
    frame.on_send()
    assert fake_runner.instances == []
    assert "running somewhere else" in env["boxes"][-1]


def test_send_refused_for_a_desktop_id_even_in_own_store(frame, env, fake_runner):
    # A corrupted store claims a desktop session's id as its own.
    frame.store.add(OwnSession("cli-a", "Imposter", "C:\\G\\Repo", last_activity_ms=1))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    select(frame, "Imposter")
    frame.on_open_session()
    frame.reply_text.SetValue("hello")
    frame.on_send()
    assert fake_runner.instances == []
    assert "desktop app" in env["boxes"][-1]
    assert frame.reply_text.GetValue() == "hello"  # nothing lost


def test_first_turn_failure_restores_message_and_starts_again(frame, env, fake_runner):
    frame.store.add(OwnSession("new-1", "Fresh", "C:\\G\\Scratch", started=False,
                               last_activity_ms=now_ms(), model="sonnet"))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    select(frame, "Fresh")
    frame.on_open_session()
    assert frame.chat_list.GetName() == "Messages in Fresh (idle, on Sonnet)"
    frame.reply_text.SetValue("Build the thing")
    frame.on_send()
    first = fake_runner.instances[0]
    assert "--session-id" in first.command and "--resume" not in first.command
    # Starting it again keeps the model too.
    assert first.command[first.command.index("--model") + 1] == "sonnet"
    assert frame.reply_text.GetValue() == ""
    # It fails before Claude ever created the session.
    frame._on_turn_event({"id": "new-1"}, "Fresh", TurnEvent(
        "failed", text="Claude exited (exit code 1). Not logged in.", is_error=True))
    assert frame.reply_text.GetValue() == "Build the thing"
    assert frame.send_btn.IsEnabled()
    frame.on_send()
    second = fake_runner.instances[1]
    assert "--session-id" in second.command and "--resume" not in second.command
    # This time it starts: the next send resumes.
    frame._on_turn_event({"id": "new-1"}, "Fresh", TurnEvent("started", session_id="new-1"))
    assert frame.store.get("new-1").started
    frame._on_turn_event({"id": "new-1"}, "Fresh", TurnEvent("finished", text="ok"))
    frame.reply_text.SetValue("more")
    frame.on_send()
    assert fake_runner.instances[2].command[-2:] == ["--resume", "new-1"]


def test_renamed_session_id_resets_the_reader(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("go")
    frame.on_send()
    frame._reader = object()  # stand-in for a reader on the old id
    generation = frame._open_generation
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("started", session_id="own-9"))
    assert frame._reader is None and frame._open_generation == generation + 1
    assert frame._open.cli_session_id == "own-9" and "own-9" in frame._runners
    assert frame.store.get("own-9") is not None


def test_stop_cancels_and_stopped_turn_is_reported(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("long job")
    frame.on_send()
    frame.on_stop()
    runner = fake_runner.instances[0]
    assert runner.cancelled
    assert env["feedback"][-1] == "Stopping."
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Stopped.", is_error=True))
    assert env["spoken"][-1] == "Hub probe: the turn failed. Stopped."
    assert frame.reply_text.GetValue() == ""  # Kelly stopped it: don't refill the box
    assert frame.send_btn.IsEnabled()


def test_close_during_turn_asks_and_stops(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("long job")
    frame.on_send()
    event = wx.CloseEvent(wx.wxEVT_CLOSE_WINDOW)
    event.SetCanVeto(True)
    frame._on_close(event)
    assert "Quit anyway" in env["boxes"][-1]
    assert fake_runner.instances[0].cancelled
    assert frame._closing  # no context menu opens from here on


def test_a_cancelled_quit_leaves_the_window_working(frame, env, fake_runner, monkeypatch):
    # Answering No to "Quit anyway?" keeps the window, so its menus must
    # still open: the closing flag is set only past the question.
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("long job")
    frame.on_send()
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.NO)
    event = wx.CloseEvent(wx.wxEVT_CLOSE_WINDOW)
    event.SetCanVeto(True)
    frame._on_close(event)
    assert event.GetVeto() and not frame._closing
    assert not fake_runner.instances[0].cancelled


def test_store_write_failure_is_reported_and_send_state_still_updates(frame, env,
                                                                       monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(frame.store, "update", broken)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Done."))
    assert frame.send_btn.IsEnabled()
    assert any("disk full" in s for s in env["spoken"])


def test_reply_draft_stays_with_its_session(frame, env):
    frame.store.add(OwnSession("own-2", "Second", "C:\\G\\Two", last_activity_ms=now_ms() - 5))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("draft for one")
    frame.focus_sessions()
    settle(frame)
    select(frame, "Second")
    frame.on_open_session()
    assert frame.reply_text.GetValue() == ""
    frame.focus_sessions()
    settle(frame)
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.reply_text.GetValue() == "draft for one"
    frame.focus_sessions()
    settle(frame)
    select(frame, "Quiet one")
    frame.on_open_session()
    assert frame.reply_text.GetValue() == ""


def test_silent_level_still_puts_the_reply_in_the_status_bar(frame, env):
    frame.speech.announce = speech.ANNOUNCE_SILENT
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("finished", text="All good. More text."))
    assert env["spoken"] == []
    assert frame.GetStatusBar().GetStatusText() == "Hub probe finished. All good."


# -- hide --------------------------------------------------------------------------------


def test_hide_keeps_the_place_and_bring_back_returns_it(frame, env):
    index = select(frame, "Hub probe")
    frame.on_hide()
    assert env["boxes"] == []  # no question: nothing is lost
    assert frame.store.get("own-1") is not None  # still a session, just hidden
    assert "own:own-1" in frame.hidden
    assert frame.session_list.GetSelection() == index
    assert frame.session_list.GetStringSelection().startswith("Quiet one")
    assert env["feedback"][-1].startswith("Hid Hub probe.")
    # A desktop app session can be hidden too.
    select(frame, "Blocked one")
    frame.on_hide()
    assert any(k.startswith("local_") for k in frame.hidden.keys())
    # The Hidden view lists them; Bring Back returns one.
    frame.on_view("hidden")
    settle(frame)
    rows = list(frame.session_list.GetStrings())
    assert len(rows) == 2 and all("hidden" in r for r in rows)
    select(frame, "Hub probe")
    frame.on_unhide()
    assert "own:own-1" not in frame.hidden
    assert env["feedback"][-1] == "Hub probe is back in the list."


def test_delete_permanently_works_from_the_list_without_hiding_first(frame, env):
    # Hiding first and then finding it in the Hidden view was a hunt nobody
    # could guess: delete it right where it is.
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    select(frame, "Hub probe")
    frame.on_delete_permanently()  # MessageBox stub answers Yes
    assert frame.store.get("own-1") is None and not path.exists()
    assert env["feedback"][-1] == "Deleted Hub probe permanently."
    assert not any(s.startswith("Hub probe") for s in frame.session_list.GetStrings())


def test_delete_permanently_a_hidden_session(frame, env):
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    select(frame, "Hub probe")
    frame.on_hide()
    frame.on_view("hidden")
    settle(frame)
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert frame.store.get("own-1") is None and not path.exists()
    assert "own:own-1" not in frame.hidden
    assert env["feedback"][-1] == "Deleted Hub probe permanently."


def test_delete_permanently_refuses_a_desktop_session(frame, env):
    select(frame, "Blocked one")
    frame.on_delete_permanently()
    assert env["feedback"][-1].startswith("Blocked one is a desktop app session")
    frame.on_hide()
    frame.on_view("hidden")
    settle(frame)
    select(frame, "Blocked one")
    frame.on_delete_permanently()
    assert env["feedback"][-1].startswith("Blocked one is a desktop app session")


def test_delete_permanently_asks_and_no_keeps_it(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    asked = []
    monkeypatch.setattr(main_frame.wx, "MessageBox",
                        lambda message, *a, **k: asked.append(message) or main_frame.wx.NO)
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert asked and "permanently" in asked[0]
    assert frame.store.get("own-1") is not None and path.exists()


def test_shift_delete_in_the_list_deletes_permanently(frame, env, monkeypatch):
    calls = []
    monkeypatch.setattr(frame, "on_delete_permanently", lambda: calls.append("delete"))
    monkeypatch.setattr(frame, "on_hide", lambda: calls.append("hide"))
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    for shift, ctrl in ((True, False), (False, False), (False, True)):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_DELETE)
        event.SetShiftDown(shift)
        event.SetControlDown(ctrl)
        frame._on_char_hook(event)
    assert calls == ["delete", "hide"]  # Ctrl+Delete does neither


def test_no_menu_accelerator_takes_delete(frame):
    # As accelerators, Delete and Shift+Delete fired from every control:
    # Delete in the attachments list hid the loaded session, and Shift+Delete
    # in the messages asked to delete it. The session list's char hook owns them.
    def items(menu):
        for item in menu.GetMenuItems():
            yield item
            if item.GetSubMenu():
                yield from items(item.GetSubMenu())

    bar = frame.GetMenuBar()
    for i in range(bar.GetMenuCount()):
        for item in items(bar.GetMenu(i)):
            accel = item.GetAccel()
            assert accel is None or accel.GetKeyCode() != wx.WXK_DELETE, item.GetItemLabel()


def test_delete_permanently_defaults_to_no(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    styles = []
    monkeypatch.setattr(main_frame.wx, "MessageBox",
                        lambda message, caption, style, *a: styles.append(style) or wx.NO)
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert styles and styles[0] & wx.NO_DEFAULT


def test_delete_permanently_refuses_while_a_turn_runs(frame, env):
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert env["feedback"][-1] == "A turn is running in that session. Stop it first."
    assert frame.store.get("own-1") is not None and path.exists()


def test_delete_permanently_refuses_an_own_session_with_a_desktop_id(frame, env):
    # Desktop sessions are read-only: an own session claiming a desktop id
    # (found in any project folder) must never take the desktop transcript with it.
    desktop = add_transcript(env, "C:\\G\\Repo", "cli-b", [user_text("theirs")])
    frame.store.add(OwnSession("cli-b", "Collider", "C:\\G\\Elsewhere", last_activity_ms=1))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    select(frame, "Collider")
    frame.on_delete_permanently()
    assert desktop.exists() and frame.store.get("cli-b") is not None
    assert "shares its id with a desktop app session" in env["feedback"][-1]


def test_delete_permanently_refuses_a_session_working_elsewhere(frame, env):
    from thechatplace.sessions import WORKING
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    select(frame, "Hub probe")
    next(s for s in frame._snapshot.sessions if s.key == "own:own-1").state = WORKING
    frame.on_delete_permanently()
    assert env["feedback"][-1] == ("Hub probe is working outside The Chat Place. "
                                   "Let it finish first.")
    assert path.exists() and frame.store.get("own-1") is not None


def test_delete_permanently_says_so_when_the_files_stay(frame, env, monkeypatch):
    from thechatplace.ui import main_frame

    def refuse(path, tries=5):
        raise PermissionError("in use")

    monkeypatch.setattr(main_frame, "_delete_transcript", refuse)
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert "couldn't all be deleted" in env["boxes"][-1]
    assert env["feedback"][-1] == ("Removed Hub probe from The Chat Place; its files are "
                                   "still on this computer.")
    assert frame.store.get("own-1") is None


def test_delete_permanently_forgets_its_draft(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._drafts["own-1"] = "half a reply"
    frame._queued["own-1"] = ["later"]
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert not frame._unsent_text()
    assert "own-1" not in frame._drafts and "own-1" not in frame._queued


def test_deleting_the_loaded_session_focuses_the_list_after_its_row_goes(frame, env,
                                                                        monkeypatch):
    # A screen reader reads the row that has focus: it should be the
    # neighbour, never the session just deleted.
    select(frame, "Hub probe")
    frame.on_open_session()
    rows_at_focus = []
    monkeypatch.setattr(frame.session_list, "SetFocus",
                        lambda: rows_at_focus.append(list(frame.session_list.GetStrings())))
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    frame.on_delete_permanently()  # from the messages: it means the loaded one
    assert rows_at_focus
    assert not any(row.startswith("Hub probe") for row in rows_at_focus[0])


def test_delete_transcript_retries_while_windows_holds_it(tmp_path, monkeypatch):
    from pathlib import Path
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame.time, "sleep", lambda s: None)
    path = tmp_path / "own-1.jsonl"
    path.write_text("{}\n")
    real_unlink, refusals = Path.unlink, [1]

    def unlink(self, *a, **k):
        if refusals:
            refusals.pop()
            raise PermissionError("in use")
        real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    main_frame._delete_transcript(path)  # refused once, then deleted
    assert not path.exists()
    path.write_text("{}\n")
    refusals[:] = [1] * 5
    with pytest.raises(PermissionError):
        main_frame._delete_transcript(path)  # refused every time: reported
    assert path.exists()


def test_delete_permanently_the_loaded_session_unloads_it(frame, env):
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("hi")])
    (path.with_suffix("") / "subagents").mkdir(parents=True)
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame._open is not None and frame._open.key == "own:own-1"
    select(frame, "Hub probe")
    frame.on_delete_permanently()
    assert frame._open is None
    assert not path.exists() and not path.with_suffix("").exists()
    assert frame.session_list.GetSelection() != wx.NOT_FOUND


def test_new_session_view_says_claude_is_starting(frame, env, fake_runner, monkeypatch):
    from thechatplace.ui import main_frame

    class FakeDialog:
        def __init__(self, parent, folder, **kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def values(self):
            return ("C:/G/Brand", "Brand new work", "auto", "Start the thing", "opus")

        def effort(self):
            return ""

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "NewSessionDialog", FakeDialog)
    frame.on_new_session()
    runner = fake_runner.instances[0]
    assert "--session-id" in runner.command and runner.prompt == "Start the thing"
    assert runner.command[runner.command.index("--model") + 1] == "opus"
    session_id = runner.command[runner.command.index("--session-id") + 1]
    assert frame.store.get(session_id).model == "opus"
    assert frame.session_heading.GetLabel().endswith("Chat Place session on Opus.")
    # Read back first, then the new session's view is announced after it.
    assert env["feedback"][-2:] == [
        "Sent to Brand new work: Start the thing.",
        "Loaded Brand new work. No messages yet. Claude is starting this session."]
    assert frame._open is not None
    assert frame._open.title == "Brand new work"
    frame._refresh_chat()
    assert frame.chat_list.GetString(0) == "No messages yet. Claude is starting this session."
    assert not frame._chat_loaded          # keeps looking for the transcript
    assert frame.send_btn.IsEnabled()  # its first turn is running; Send would queue
    # Before the transcript exists, later ticks don't rewrite (and re-read) the list.
    frame.chat_list.SetSelection(0)
    frame._refresh_chat()
    assert frame.chat_list.GetSelection() == 0
    # The first turn fails before Claude creates the session.
    frame._on_turn_event({"id": runner.command[runner.command.index("--session-id") + 1]},
                         "Brand new work",
                         TurnEvent("failed", text="Not logged in.", is_error=True))
    frame._refresh_chat()
    assert frame.chat_list.GetString(0).startswith("No messages yet. The first message "
                                                   "didn't reach Claude")
    assert frame.reply_text.GetValue() == "Start the thing"


def test_focus_stays_in_the_reply_box_after_sending(frame, env, fake_runner, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    calls = []
    monkeypatch.setattr(frame.reply_text, "SetFocus", lambda: calls.append("reply"))
    frame.reply_text.SetValue("hello")
    frame.on_send()
    assert fake_runner.instances and calls[-1] == "reply"
    assert frame.reply_text.GetValue() == ""


def test_loading_another_session_announces_it(frame, env):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Hi")])
    select(frame, "Blocked one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    assert env["feedback"][-1].startswith("Loaded Blocked one. No transcript")
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame._open.title == "Quiet one")
    assert env["feedback"][-1] == "Loaded Quiet one, 1 message."


# -- review of PR #172 ---------------------------------------------------------------------


def test_enter_on_the_loaded_session_goes_back_without_reloading(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("One"), assistant_block(text_block("Two"), "m1"), user_text("Three")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame.chat_list.SetSelection(0)  # Kelly was reading the first message
    generation = frame._open_generation
    focused = []
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focused.append("messages"))
    frame.on_open_session()          # Enter on the same session again
    assert frame._open_generation == generation   # not reloaded
    assert frame.chat_list.GetSelection() == 0
    assert frame.chat_list.GetCount() == 3
    assert focused == ["messages"]
    assert env["feedback"][-1] == "Back in Quiet one."


def test_hiding_the_loaded_session_moves_focus_to_the_session_list(frame, env,
                                                                       monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    focused = []
    monkeypatch.setattr(frame.session_list, "SetFocus", lambda: focused.append("sessions"))
    frame.on_hide()
    assert frame._open is None
    assert not frame.own_reply.IsShown()
    assert focused == ["sessions"]


def test_message_menu_binds_on_the_menu_and_opens_at_the_message(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Hello")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    read = []
    monkeypatch.setattr(frame, "on_read_message", lambda: read.append(True))
    menu = frame._message_menu()
    try:
        item = menu.FindItemByPosition(0)
        assert item.GetItemLabelText() == "Read Full Message"
        event = wx.CommandEvent(wx.wxEVT_MENU, item.GetId())
        menu.ProcessEvent(event)
        assert read == [True]
    finally:
        menu.Destroy()
    # Nothing was bound on the frame, so repeated menus don't pile up handlers.
    frame.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, item.GetId()))
    assert read == [True]
    # Opened from the keyboard: at the selected message, not wherever the mouse is.
    keyboard = wx.ContextMenuEvent(wx.wxEVT_CONTEXT_MENU, frame.chat_list.GetId(),
                                   wx.DefaultPosition)
    point = frame._message_menu_position(keyboard)
    size = frame.chat_list.GetClientSize()
    assert 0 <= point.x <= max(size.width, 8) and 0 <= point.y <= max(size.height, 40)


def test_a_stale_load_starts_the_current_one_straight_away(frame, monkeypatch):
    select(frame, "Quiet one")
    frame.on_open_session()
    calls = []
    monkeypatch.setattr(frame, "_refresh_chat", lambda: calls.append(True))
    frame._apply_chat(frame._open_generation - 1, True, [], 0, None)
    assert calls == [True]


def test_a_turn_ending_in_an_unloaded_session_marks_it_and_leaves_the_loaded_one(
        frame, env, fake_runner, monkeypatch):
    frame.store.add(OwnSession("own-2", "Second", "C:\\G\\Two", started=False,
                               last_activity_ms=now_ms()))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    # Start a turn in Second, then load Hub probe and type there.
    select(frame, "Second")
    frame.on_open_session()
    frame.reply_text.SetValue("first message for Second")
    frame.on_send()
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("typing in Hub probe")
    refreshed = []
    monkeypatch.setattr(frame, "_refresh_chat", lambda: refreshed.append(True))
    # Second's first turn fails before reaching Claude.
    frame._on_turn_event({"id": "own-2"}, "Second",
                         TurnEvent("failed", text="Not logged in.", is_error=True))
    second = frame.store.get("own-2")
    assert second.unread and second.state == NEEDS_YOU
    assert frame._drafts["own-2"] == "first message for Second"   # kept for Second
    assert frame.reply_text.GetValue() == "typing in Hub probe"   # Hub probe untouched
    assert refreshed == []                                        # loaded chat not reloaded
    assert frame._open.title == "Hub probe"


# -- updates ---------------------------------------------------------------------------------


class FakeUpdates:
    def __init__(self, result, download_ok=True, apply_ok=True, during_download=None):
        self.result = result
        self.download_ok = download_ok
        self.apply_ok = apply_ok
        self.during_download = during_download
        self.calls = []

    def check(self, manual=True):
        self.calls.append("check" if manual else "quiet check")
        return self.result

    def download(self):
        self.calls.append("download")
        if self.during_download:
            wx.CallAfter(self.during_download)
            # Let the UI thread run it before the download "finishes".
            import time
            time.sleep(0.3)
        return self.download_ok

    def apply_and_restart(self):
        self.calls.append("apply")
        return self.apply_ok


def run_check(frame, manual=True):
    frame.check_for_updates(manual)
    assert pump(lambda: not frame._update_busy)


def test_manual_check_with_no_releases_says_so(frame, env):
    from thechatplace.updater import NO_RELEASES, CheckResult
    frame.updates = FakeUpdates(CheckResult(NO_RELEASES, "0.1.0"))
    run_check(frame)
    assert env["spoken"][-1].startswith("No release of The Chat Place has been published yet.")
    assert frame._last_announcement == env["spoken"][-1]    # Ctrl+Shift+R repeats it
    assert env["boxes"] == []


def test_manual_check_result_is_spoken_even_when_announcements_are_silent(frame, env):
    from thechatplace.speech import ANNOUNCE_SILENT
    from thechatplace.updater import FAILED, CheckResult
    frame.speech.announce = ANNOUNCE_SILENT
    frame.updates = FakeUpdates(CheckResult(FAILED, "0.1.0", detail="GitHub couldn't be reached"))
    run_check(frame)
    assert env["spoken"][-1].startswith("Couldn't check for updates: GitHub couldn't be reached")


def test_startup_check_is_quiet_unless_there_is_an_update(frame, env):
    from thechatplace.updater import CURRENT, FAILED, NO_RELEASES, NOT_INSTALLED, CheckResult
    for status in (CURRENT, NO_RELEASES, NOT_INSTALLED, FAILED):
        frame.updates = FakeUpdates(CheckResult(status, "0.1.0", "0.1.0"))
        before = (list(env["feedback"]), list(env["spoken"]))
        run_check(frame, manual=False)
        assert (env["feedback"], env["spoken"]) == before
        assert frame.updates.calls == ["quiet check"]


def test_startup_check_announces_an_update_but_never_opens_a_dialog(frame, env):
    from thechatplace.updater import AVAILABLE, CheckResult
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    run_check(frame, manual=False)
    assert env["spoken"][-1] == ("The Chat Place 0.2.0 is available. You have 0.1.0. "
                                 "Help, Update to 0.2.0 Available installs it, or the Update button "
                                 "on the status bar.")
    assert env["boxes"] == []
    button = frame.status_parts.get("update")
    assert button.IsShown() and button.GetLabel() == "Update available: 0.2.0"
    assert frame.updates.calls == ["quiet check"]


def test_available_update_is_asked_about_with_no_as_the_default(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    asked = []
    monkeypatch.setattr(wx, "MessageBox",
                        lambda text, caption, style, *a, **k: asked.append((text, style)) or wx.NO)
    spoken_before = list(env["spoken"])
    run_check(frame)
    text, style = asked[-1]
    assert text.startswith("The Chat Place 0.2.0 is available. You have 0.1.0.")
    assert "Install it now?" in text and "sessions and settings are kept" in text
    assert style & wx.NO_DEFAULT
    assert env["spoken"] == spoken_before     # the dialog is read; nothing said over it
    assert frame.updates.calls == ["check"]   # No: nothing downloaded
    assert env["feedback"][-1].startswith("Not now.")


def test_yes_downloads_then_applies(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    run_check(frame)
    assert pump(lambda: frame.updates.calls == ["check", "download", "apply"])
    assert env["spoken"][-1] == "Installing The Chat Place 0.2.0 and restarting."


def test_failed_apply_restarts_the_timers_and_says_so(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"), apply_ok=False)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    run_check(frame)
    assert pump(lambda: "apply" in frame.updates.calls and not frame._update_busy)
    assert env["spoken"][-1].startswith("Couldn't install the update.")
    assert frame._list_timer.IsRunning()


def test_unsent_text_is_warned_about(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("half a thought")
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.NO)
    run_check(frame)
    assert "haven't sent" in asked[-1]


def test_text_typed_during_the_download_is_asked_about_again(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"),
                                during_download=lambda: frame.reply_text.SetValue("typing"))
    asked = []

    def box(text, *a, **k):
        asked.append(text)
        return wx.YES if len(asked) == 1 else wx.NO
    monkeypatch.setattr(wx, "MessageBox", box)
    run_check(frame)
    assert pump(lambda: "download" in frame.updates.calls and not frame._update_busy)
    assert len(asked) == 2 and "Restart now to install it?" in asked[1]
    assert "apply" not in frame.updates.calls
    assert "installed the next time The Chat Place starts" in env["spoken"][-1]


def test_a_turn_started_during_the_download_postpones_the_install(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult

    def start_turn():
        frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"),
                                during_download=start_turn)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    run_check(frame)
    assert pump(lambda: "download" in frame.updates.calls and not frame._update_busy)
    assert "apply" not in frame.updates.calls
    assert "installed the next time The Chat Place starts" in env["spoken"][-1]
    frame._runners.clear()


def test_a_turn_started_while_the_install_is_announced_postpones_it(frame, env, monkeypatch):
    from thechatplace import speech
    from thechatplace.updater import AVAILABLE, CheckResult
    busy = iter([True])

    def still_speaking():
        # The announcement is playing; Kelly sends a reply meanwhile.
        frame._runners["own-1"] = FakeRunner([], "", "", None)
        return next(busy, False)
    monkeypatch.setattr(speech.speaker, "busy", still_speaking)
    # pump() doesn't run wx timers; the 200 ms re-check runs straight away here.
    monkeypatch.setattr(wx, "CallLater", lambda ms, fn, *args: wx.CallAfter(fn, *args))
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    run_check(frame)
    assert pump(lambda: "installed the next time" in (env["spoken"] or [""])[-1])
    assert "apply" not in frame.updates.calls
    assert frame._list_timer.IsRunning()
    frame._runners.clear()


def test_no_update_is_applied_while_claude_is_working(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: pytest.fail("must not ask"))
    spoken_before = len(env["spoken"])
    run_check(frame)
    assert frame.updates.calls == ["check"]
    assert "once Claude finishes" in env["spoken"][-1]
    assert len(env["spoken"]) == spoken_before + 1     # said once, not twice
    frame._runners.clear()


def test_failed_download_is_reported_and_nothing_applied(frame, env, monkeypatch):
    from thechatplace.updater import AVAILABLE, CheckResult
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"), download_ok=False)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    run_check(frame)
    assert pump(lambda: "download" in frame.updates.calls and not frame._update_busy)
    assert "apply" not in frame.updates.calls
    assert env["spoken"][-1].startswith("Couldn't download The Chat Place 0.2.0.")

# -- queued messages (issue #175) -----------------------------------------------------------


def _start(frame, fake_runner, text="first"):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue(text)
    frame.on_send()
    return fake_runner.instances[-1]


def test_send_during_a_turn_queues_and_goes_when_it_ends(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("second")
    frame.on_send()
    assert frame.reply_text.GetValue() == ""
    assert frame._queued == {"own-1": ["second"]}
    assert frame.turn_status.GetLabel().startswith("1 message queued. Claude is working")
    frame.reply_text.SetValue("third")
    frame.on_send()
    assert env["feedback"][-1] == "Also queued for Hub probe: third."
    assert len(fake_runner.instances) == 1
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Done."))
    # The reply is announced first, then the queued message goes as one turn.
    assert env["spoken"][-1] == "Hub probe replied. Done."
    assert len(fake_runner.instances) == 2
    assert fake_runner.instances[1].prompt == "second\n\nthird"
    assert env["feedback"][-1] == "Sent your queued message. Hub probe is working."
    assert frame._queued == {}
    assert "own-1" in frame._runners
    assert env["boxes"] == []


def test_queued_message_goes_despite_the_lists_stale_busy_status(frame, env, fake_runner,
                                                                 monkeypatch):
    # During the turn, The Chat Place's own `claude -p` writes a busy pid file
    # for the session, and the list's snapshot keeps it up to 5 seconds after
    # the process has exited.
    _start(frame, fake_runner)
    (env["live"] / "4242.json").write_text(json.dumps(
        {"pid": 4242, "sessionId": "own-1", "status": "busy"}))
    claude_running = {"yes": True}
    original = hub.load_live_status
    monkeypatch.setattr(hub, "load_live_status",
                        lambda directory=None, alive=None, started=None: original(
                            directory, alive=lambda pid: claude_running["yes"],
                            started=lambda pid: None))
    frame.refresh_sessions(force=True)
    assert pump(lambda: "own-1" in frame._snapshot.live)
    frame.reply_text.SetValue("second")
    frame.on_send()
    claude_running["yes"] = False  # the turn's process has exited
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Done."))
    assert "own-1" in frame._snapshot.live  # the snapshot is still stale
    assert env["boxes"] == []
    assert fake_runner.instances[-1].prompt == "second"


def test_stop_gives_the_queued_message_back(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    frame.reply_text.SetValue("queued words")
    frame.on_send()
    frame.reply_text.SetValue("half typed")
    frame.reply_text.SetInsertionPointEnd()
    frame.on_stop()
    assert runner.cancelled
    assert frame._queued == {}
    # In the order written, with the caret still where Kelly was typing.
    assert frame.reply_text.GetValue() == "queued words\n\nhalf typed"
    assert frame.reply_text.GetInsertionPoint() == frame.reply_text.GetLastPosition()
    assert env["feedback"][-1] == "Stopping. Your queued message is back in the message box."
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Stopped.", is_error=True))
    assert len(fake_runner.instances) == 1  # nothing sent after a stop


def test_send_while_stopping_keeps_the_text(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.on_stop()
    frame.reply_text.SetValue("do this instead")
    frame.on_send()
    assert frame._queued == {}
    assert frame.reply_text.GetValue() == "do this instead"
    assert env["feedback"][-1] == "Still stopping. Send again in a moment."
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Stopped.", is_error=True))
    frame.on_send()
    assert fake_runner.instances[-1].prompt == "do this instead"


def test_queued_message_is_not_sent_after_a_failed_turn(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("follow up")
    frame.on_send()
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("finished", text="API error", is_error=True))
    assert len(fake_runner.instances) == 1
    assert frame.reply_text.GetValue() == "follow up"
    assert env["feedback"][-1] == ("Your queued message for Hub probe wasn't sent: the turn "
                                   "before it failed. It's back in the message box.")


def test_queued_message_refused_at_send_time_is_spoken_and_goes_to_its_draft(
        frame, env, fake_runner, monkeypatch):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("later")
    frame.on_send()
    frame._drafts["own-1"] = "typed elsewhere"
    frame._open = None  # Kelly has moved on to another session
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup(None, "Claude Code isn't installed."))
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="ok"))
    assert len(fake_runner.instances) == 1
    assert env["boxes"] == []  # no dialog he didn't ask for
    assert env["feedback"][-1] == ("Your queued message for Hub probe wasn't sent: Claude "
                                   "Code isn't installed. To install it, choose Claude Code "
                                   "Sign-in on the Help menu. It's back in the message box.")
    assert frame._drafts["own-1"] == "later\n\ntyped elsewhere"
    assert frame._last_announcement == env["feedback"][-1]  # Ctrl+Shift+R repeats it


def test_queued_send_leaves_the_open_sessions_reply_box_alone(frame, env, fake_runner):
    frame.store.add(OwnSession("own-2", "Second", "C:\\G\\Two", last_activity_ms=now_ms() - 5))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    _start(frame, fake_runner)
    frame.reply_text.SetValue("queued")
    frame.on_send()
    frame.focus_sessions()
    settle(frame)
    select(frame, "Second")
    frame.on_open_session()
    frame.reply_text.SetValue("for second")
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="ok"))
    assert fake_runner.instances[-1].prompt == "queued"
    assert fake_runner.instances[-1].cwd == "C:\\G\\Scratch"
    assert frame.reply_text.GetValue() == "for second"


def test_failed_first_turn_gives_back_both_messages_in_order(frame, env, fake_runner):
    frame.store.add(OwnSession("new-1", "Fresh", "C:\\G\\Scratch", started=False,
                               last_activity_ms=now_ms()))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    select(frame, "Fresh")
    frame.on_open_session()
    frame.reply_text.SetValue("first")
    frame.on_send()
    frame.reply_text.SetValue("queued")
    frame.on_send()
    frame.reply_text.SetValue("typing more")
    frame._on_turn_event({"id": "new-1"}, "Fresh", TurnEvent(
        "failed", text="Not logged in.", is_error=True))
    assert frame.reply_text.GetValue() == "first\n\nqueued\n\ntyping more"
    assert len(fake_runner.instances) == 1


def test_failed_first_turn_with_the_session_closed_keeps_its_draft(frame, env, fake_runner):
    frame.store.add(OwnSession("new-1", "Fresh", "C:\\G\\Scratch", started=False,
                               last_activity_ms=now_ms()))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 4)
    select(frame, "Fresh")
    frame.on_open_session()
    frame.reply_text.SetValue("first")
    frame.on_send()
    frame._open = None
    frame._drafts["new-1"] = "draft text"
    frame._on_turn_event({"id": "new-1"}, "Fresh", TurnEvent(
        "failed", text="Not logged in.", is_error=True))
    assert frame._drafts["new-1"] == "first\n\ndraft text"


def test_turn_status_mentions_a_queued_message(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("more")
    frame.on_send()
    frame.on_turn_status()
    assert env["feedback"][-1] == ("Hub probe: 1 message queued. Claude has been working for "
                                   "1 minute 15 seconds, last starting.")


def test_queued_message_follows_a_renamed_session_id(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("next")
    frame.on_send()
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("started", session_id="own-9"))
    assert frame._queued == {"own-9": ["next"]}
    frame._on_turn_event({"id": "own-9"}, "Hub probe", TurnEvent("finished", text="ok"))
    assert fake_runner.instances[-1].prompt == "next"
    assert fake_runner.instances[-1].command[-2:] == ["--resume", "own-9"]


def test_stop_with_nothing_running_says_so(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.stop_btn.IsEnabled()
    frame.on_stop()
    assert env["feedback"][-1] == "Nothing is running."


def test_quit_prompt_mentions_queued_messages(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("pending")
    frame.on_send()
    event = wx.CloseEvent(wx.wxEVT_CLOSE_WINDOW)
    event.SetCanVeto(True)
    frame._on_close(event)
    assert "queued messages will not be sent" in env["boxes"][-1]


# -- reading your own messages back (issue #178) ------------------------------------------


def test_own_messages_not_read_back_when_turned_off(frame, env, fake_runner):
    frame.speech.announce_own = False
    _start(frame, fake_runner, "private words")
    assert env["feedback"][-1] == "Sent. Hub probe is working."
    frame.reply_text.SetValue("more private words")
    frame.on_send()
    assert env["feedback"][-1] == "Queued. It will be sent when Hub probe finishes."


def test_summary_level_reads_back_the_first_sentence(frame, env, fake_runner):
    frame.speech.announce = speech.ANNOUNCE_SUMMARY
    _start(frame, fake_runner, "Fix the build. Then run every test and report back.")
    assert env["feedback"][-1] == "Sent to Hub probe: Fix the build."


def test_silent_level_reads_nothing_back(frame, env, fake_runner):
    frame.speech.announce = speech.ANNOUNCE_SILENT
    _start(frame, fake_runner, "quiet please")
    assert env["feedback"] == []  # silent speaks no confirmations at all
    assert frame.GetStatusBar().GetStatusText() == "Sent. Hub probe is working."


def test_queued_message_is_read_once(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("the follow up")
    frame.on_send()
    assert env["feedback"][-1] == "Queued for Hub probe: the follow up."
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="ok"))
    assert env["feedback"][-1] == "Sent your queued message. Hub probe is working."
    assert sum("the follow up" in f for f in env["feedback"]) == 1


def test_settings_dialog_has_the_read_back_checkbox(frame):
    from thechatplace.ui.dialogs import SettingsDialog
    settings = speech.SpeechSettings(announce_own=False)
    dialog = SettingsDialog(frame, settings, speech.default_options())
    try:
        box = dialog.own_messages
        assert box.GetLabel() == "Read your own &messages back when they're sent"
        assert not box.GetValue() and not dialog.get_settings().announce_own
        box.SetValue(True)
        assert dialog.get_settings().announce_own
    finally:
        dialog.Destroy()


# -- choosing the model (issue #180) --------------------------------------------------------


def test_a_later_turn_keeps_the_sessions_model(frame, env, fake_runner):
    frame.store.update("own-1", model="sonnet")
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.session_heading.GetLabel().endswith("Chat Place session on Sonnet.")
    frame.reply_text.SetValue("next")
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert command[-2:] == ["--resume", "own-1"]
    assert command[command.index("--model") + 1] == "sonnet"
    # The session's name goes too, so other sessions can keep reaching it (#123).
    assert command[command.index("--name") + 1] == "Hub probe"


def test_an_old_session_without_a_model_uses_the_default(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.session_heading.GetLabel().endswith("Chat Place session on the default model.")
    frame.reply_text.SetValue("next")
    frame.on_send()
    assert "--model" not in fake_runner.instances[-1].command


def test_new_session_dialog_offers_the_models(frame):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, "C:\\G")
    try:
        assert dialog.model.GetStringSelection() == "Default (your Claude Code setting)"
        assert dialog.model.GetCount() == 4
        assert "Fable" not in dialog.model.GetStrings()
        dialog.message.SetValue("hello")
        assert dialog.values()[4] == ""
        dialog.model.SetStringSelection("Opus")
        assert dialog.values()[4] == "opus"
        # Its label (and Alt+D) comes right before it, after Title.
        labels = [c for c in dialog.GetChildren() if isinstance(c, wx.StaticText)]
        assert "Mo&del:" in [c.GetLabel() for c in labels]
        order = list(dialog.GetChildren())
        assert order.index(dialog.title_text) < order.index(dialog.model) < \
            order.index(dialog.mode)
    finally:
        dialog.Destroy()


# -- answering Claude (#187, #188) ------------------------------------------------------


def _request(request_id="r1", tool="Bash", tool_input=None, suggestions=None):
    from thechatplace.claude_cli import PermissionRequest
    return PermissionRequest(request_id, tool, tool_input or {"command": "git push"},
                             suggestions=suggestions or [])


def _waiting_turn(frame, *requests):
    select(frame, "Hub probe")
    frame.on_open_session()
    runner = FakeRunner([], "", "", None)
    frame._runners["own-1"] = runner
    for request in requests:
        frame._on_turn_event({"id": "own-1"}, "Hub probe",
                             TurnEvent("permission", text=request.summary(), request=request))
    return runner


def test_permission_request_is_announced_and_the_session_needs_you(frame, env):
    _waiting_turn(frame, _request())
    assert env["spoken"][-1] == ("Hub probe needs you. Claude wants to run git push. "
                                 "Ctrl+Shift+A answers.")
    assert frame.turn_status.GetLabel() == ("Waiting for you: Claude wants to run git push. "
                                            "Ctrl+Shift+A answers.")
    settle(frame)
    row = [s for s in frame.session_list.GetStrings() if s.startswith("Hub probe")][0]
    assert "needs you: Claude wants to run git push" in row
    frame.on_turn_status()
    assert env["feedback"][-1].startswith("Hub probe is waiting for you: Claude wants to run")


def test_answering_sends_the_response_and_announces_the_next(frame, env, monkeypatch):
    from thechatplace.claude_cli import allow_response
    first, second = _request("r1"), _request("r2", tool_input={"command": "git tag v1"})
    runner = _waiting_turn(frame, first, second)
    monkeypatch.setattr(frame, "_ask", lambda title, request, own: (
        allow_response(request), "Allowed.", {}))
    frame.on_answer()
    assert runner.responses == [("r1", {"behavior": "allow", "updatedInput": first.input})]
    assert env["feedback"][-1] == ("Allowed. Next: Claude wants to run git tag v1. "
                                   "Ctrl+Shift+A answers.")
    frame.on_answer()
    assert [r[0] for r in runner.responses] == ["r1", "r2"]
    assert frame._pending["own-1"] == []
    frame.on_answer()
    assert env["feedback"][-1] == "Claude isn't waiting for an answer."


def test_answer_later_leaves_it_waiting(frame, env, monkeypatch):
    runner = _waiting_turn(frame, _request())
    monkeypatch.setattr(frame, "_ask", lambda *a: None)
    frame.on_answer()
    assert not hasattr(runner, "responses")
    assert len(frame._pending["own-1"]) == 1
    assert "still waiting" in env["feedback"][-1]


def test_allow_for_session_keeps_the_rule_for_later_turns(frame, env, fake_runner, monkeypatch):
    from thechatplace.ui import dialogs
    request = _request(suggestions=[{"type": "addRules", "behavior": "allow",
                                     "destination": "localSettings",
                                     "rules": [{"toolName": "Bash",
                                                "ruleContent": "git push:*"}]}])
    runner = _waiting_turn(frame, request)

    class Picks(dialogs.PermissionDialog):
        def ShowModal(self):
            self.choice = dialogs.ALLOW_SESSION
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.PermissionDialog", Picks)
    frame.on_answer()
    response = runner.responses[0][1]
    assert response["updatedPermissions"][0]["destination"] == "session"
    assert frame.store.get("own-1").allowed_tools == ["Bash(git push:*)"]
    # The turn ends; the next one is given the rule.
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Pushed."))
    frame.reply_text.SetValue("and tag it")
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert command[command.index("--allowedTools") + 1] == "Bash(git push:*)"


def test_plan_approval_keeps_the_new_mode(frame, env, monkeypatch):
    from thechatplace.ui import dialogs
    frame.store.update("own-1", permission_mode="plan")
    runner = _waiting_turn(frame, _request("p1", "ExitPlanMode", {"plan": "# Plan\n1. Go"}))
    assert "plan is ready" in env["spoken"][-1]

    class Approves(dialogs.PlanDialog):
        def ShowModal(self):
            assert self.plan_text.GetValue() == "# Plan\n1. Go"
            assert self.mode.GetStringSelection().startswith("Accept edits")
            self.approved = True
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.PlanDialog", Approves)
    frame.on_answer()
    assert runner.responses[0][1]["updatedPermissions"] == [
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    assert frame.store.get("own-1").permission_mode == "acceptEdits"
    assert env["feedback"][-1] == "Plan approved. Claude is carrying on in accept edits mode."


def test_question_answers_go_back_to_claude(frame, env, monkeypatch):
    from thechatplace.ui import dialogs
    questions = [{"question": "Which color?", "header": "Color",
                  "options": [{"label": "Red", "description": "Warm"}, {"label": "Blue"}]},
                 {"question": "Which toppings?", "multiSelect": True,
                  "options": [{"label": "Cheese"}, {"label": "Olives"}]}]
    request = _request("q1", "AskUserQuestion", {"questions": questions})
    runner = _waiting_turn(frame, request)

    class Answers(dialogs.QuestionDialog):
        def ShowModal(self):
            color, toppings = self._controls
            assert [c.GetLabel() for c in color[1]] == ["Red: Warm", "Blue",
                                                        "Other (type below)"]
            color[1][1].SetValue(True)
            toppings[1][0].SetValue(True)
            toppings[2].SetValue("anchovies")  # typing ticks Other
            assert toppings[1][2].GetValue()
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.QuestionDialog", Answers)
    frame.on_answer()
    sent = runner.responses[0][1]
    assert sent["updatedInput"]["answers"] == {"Which color?": "Blue",
                                               "Which toppings?": "Cheese, anchovies"}
    assert env["feedback"][-1] == "Answer sent."


def test_turn_end_clears_waiting_and_a_late_answer_says_so(frame, env, monkeypatch):
    from thechatplace.claude_cli import allow_response
    runner = _waiting_turn(frame, _request())
    request = frame._pending["own-1"][0]
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("failed", text="Stopped.",
                                                                 is_error=True))
    assert "own-1" not in frame._pending
    runner.ended = True
    frame._runners["own-1"] = runner
    frame._apply_answer("own-1", request, allow_response(request), "Allowed.", {})
    assert env["feedback"][-1] == "That isn't waiting any more: the turn has ended."


def test_permission_dialog_buttons(frame):
    from thechatplace.ui.dialogs import PermissionDialog
    plain = PermissionDialog(frame, "Hub probe", _request(tool="WebFetch",
                                                          tool_input={"url": "https://x"}))
    try:
        assert plain.session_button is None
        assert plain.request_text.GetValue().startswith("Claude wants to fetch https://x.")
        default = plain.GetDefaultItem()
        assert default.GetLabel() == "&Deny"
    finally:
        plain.Destroy()
    with_rule = PermissionDialog(frame, "Hub probe", _request(suggestions=[
        {"type": "addRules", "behavior": "allow",
         "rules": [{"toolName": "Bash", "ruleContent": "git push:*"}]}]))
    try:
        assert with_rule.session_button.GetName() == (
            "Allow, and don't ask again this session for Bash(git push:*)")
    finally:
        with_rule.Destroy()


# -- Continue Here (#189) ------------------------------------------------------------------


def _continue(frame, env, monkeypatch, message="Carry on from here"):
    from thechatplace.ui import dialogs
    seen = {}

    class Fills(dialogs.NewSessionDialog):
        def ShowModal(self):
            seen["title"] = self.GetTitle()
            seen["folder_editable"] = self.folder.IsEditable()
            seen["name"] = self.title_text.GetValue()
            self.message.SetValue(message)
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.NewSessionDialog", Fills)
    frame.on_continue_here()
    return seen


def test_continue_here_forks_a_desktop_session(frame, env, fake_runner, monkeypatch):
    folder = env["tmp"] / "repo"
    folder.mkdir()
    add_desktop(env, "local_c", "cli-c", "Desktop work", cwd=str(folder))
    add_transcript(env, str(folder), "cli-c", [user_text("Earlier question")])
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Desktop work")
    frame.on_open_session()
    assert frame.continue_btn.IsShown()
    seen = _continue(frame, env, monkeypatch)
    assert seen == {"title": "Continue Here: Desktop work", "folder_editable": False,
                    "name": "Desktop work (continued)"}
    runner = fake_runner.instances[-1]
    command = runner.command
    assert command[command.index("--resume") + 1] == "cli-c"
    assert "--fork-session" in command
    new_id = command[command.index("--session-id") + 1]
    assert new_id != "cli-c"
    assert runner.cwd == str(folder) and runner.prompt == "Carry on from here"
    own = frame.store.get(new_id)
    assert own.fork_source == "cli-c" and own.forked_from == "Desktop work"
    assert frame._open.cli_session_id == new_id
    assert frame.session_heading.GetLabel().endswith(", continued from Desktop work.")
    # If that first turn never got going, Send copies the session again.
    frame._on_turn_event({"id": new_id}, "Desktop work (continued)",
                         TurnEvent("failed", text="not signed in", is_error=True))
    frame.reply_text.SetValue("again")
    frame.on_send()
    again = fake_runner.instances[-1].command
    assert again[again.index("--resume") + 1] == "cli-c" and "--fork-session" in again
    assert again[again.index("--session-id") + 1] == new_id


def test_continue_here_keeps_showing_tool_activity(frame, env, fake_runner, monkeypatch):
    """#162: carried on here, the tool calls you were following stay shown."""
    folder = env["tmp"] / "repo"
    folder.mkdir()
    add_desktop(env, "local_c", "cli-c", "Desktop work", cwd=str(folder))
    add_transcript(env, str(folder), "cli-c", [user_text("Earlier question")])
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Desktop work")
    frame.on_open_session()
    frame._set_activity(True)
    _continue(frame, env, monkeypatch)
    assert frame._open.cli_session_id != "cli-c"
    assert frame._show_activity and frame.activity_check.GetValue()
    assert frame._open.key in frame.tool_activity


def test_continue_here_needs_a_transcript_and_a_desktop_session(frame, env, fake_runner,
                                                                monkeypatch):
    select(frame, "Quiet one")  # no transcript on disk
    frame.on_continue_here()
    assert "no longer on disk" in env["boxes"][-1]
    select(frame, "Hub probe")
    frame.on_continue_here()
    assert env["feedback"][-1] == ("Hub probe is already a Chat Place session; "
                                   "reply to it here.")
    assert fake_runner.instances == []


# -- the desktop app's Cowork sessions (#91) ----------------------------------------------


def add_cowork(env, local, cli, title, *records):
    """A Cowork session, laid out as the desktop app does: metadata, and a
    folder beside it that is the session's own Claude Code home."""
    folder = env["cowork"] / "acct" / "org"
    folder.mkdir(parents=True, exist_ok=True)
    cwd = str(folder / local / "outputs")
    (folder / f"{local}.json").write_text(json.dumps(
        {"sessionId": local, "cliSessionId": cli, "cwd": cwd, "title": title,
         "isArchived": False, "lastActivityAt": now_ms() - 30_000}), encoding="utf-8")
    projects = (platform_paths.long_path(folder / local / ".claude") / "projects"
                / platform_paths.encode_cwd(cwd))
    projects.mkdir(parents=True)
    (projects / f"{cli}.jsonl").write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")


def test_cowork_session_reads_opens_in_claude_and_wont_continue(frame, env, fake_runner,
                                                                monkeypatch):
    add_cowork(env, "local_cw", "cli-cw", "Sort the receipts",
               user_text("Total these"), assistant_block(text_block("It's 42 dollars."), "m1"))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    row = select(frame, "Sort the receipts")
    assert frame.session_list.GetString(row).endswith("Cowork session")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() == 2)
    assert "It's 42 dollars." in frame.chat_list.GetString(1)
    assert frame.session_heading.GetLabel().endswith(
        "Claude desktop app Cowork session, read-only.")
    assert frame.desktop_reply.IsShown() and not frame.own_reply.IsShown()
    assert not frame.continue_btn.IsShown()
    assert "Cowork" in frame.desktop_note.GetValue()
    frame.on_send()  # read-only, like any desktop app session
    assert frame._runners == {}
    frame.on_open_in_claude()
    assert env["opened"] == ["claude://claude.ai/cowork/local_cw"]
    frame.on_continue_here()
    assert "can't be continued here" in env["boxes"][-1]
    assert fake_runner.instances == []
    # A Code session after it gets its note and Continue Here back.
    select(frame, "Quiet one")
    frame.on_open_session()
    assert frame.continue_btn.IsShown()
    assert "Continue Here starts" in frame.desktop_note.GetValue()


def test_cowork_missing_transcript_and_bug_report_count(frame, env, monkeypatch):
    folder = env["cowork"] / "acct" / "org"
    folder.mkdir(parents=True)
    (folder / "local_gone.json").write_text(json.dumps(
        {"sessionId": "local_gone", "cliSessionId": "cli-gone", "cwd": "C:\\x\\outputs",
         "title": "Gone one", "isArchived": False, "lastActivityAt": now_ms()}),
        encoding="utf-8")
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Gone one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    assert frame.chat_list.GetString(0).startswith("No transcript: this Cowork session's")
    fills = _fake_bug_dialog(monkeypatch, "open", ("T", "W", "E", ""))
    frame.on_report_bug()
    assert any(line.startswith("Sessions listed: 2 desktop app Code, 1 desktop app Cowork, "
                               "1 Chat Place") for line in fills.seen)


def test_cowork_view_shows_only_cowork_sessions(frame, env):
    from thechatplace.sessions import VIEW_COWORK
    add_cowork(env, "local_cw", "cli-cw", "Sort the receipts", user_text("Hi"))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    frame.on_view(VIEW_COWORK)
    assert env["feedback"][-1] == "Showing Cowork sessions: 1 session."
    assert [frame.session_list.GetString(i).split(",")[0]
            for i in range(frame.session_list.GetCount())] == ["Sort the receipts"]


# -- sessions started in a terminal (#158) ---------------------------------------------------


def add_terminal(env, monkeypatch, cli, cwd, *records):
    """A session started by typing claude in a terminal: a transcript whose
    records say so, and no metadata file. The list looks for them in the
    env's projects folder."""
    monkeypatch.setattr(hub, "terminal_projects_dir", lambda: env["projects"])
    stamped = [dict(r, entrypoint="cli", cwd=cwd) if r.get("type") in ("user", "assistant")
               else r for r in records]
    return add_transcript(env, cwd, cli, stamped)


def test_terminal_session_reads_is_read_only_and_continues(frame, env, fake_runner,
                                                           monkeypatch):
    folder = env["tmp"] / "notes"
    folder.mkdir()
    add_terminal(env, monkeypatch, "term-1", str(folder), user_text("Tidy my notes"),
                 assistant_block(text_block("Sorted them by date."), "m1"))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    row = select(frame, "Tidy my notes")
    assert "terminal session" in frame.session_list.GetString(row)
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() == 2)
    assert "Sorted them by date." in frame.chat_list.GetString(1)
    assert frame.session_heading.GetLabel().endswith("Terminal session, read-only.")
    assert frame.desktop_reply.IsShown() and not frame.own_reply.IsShown()
    assert frame.continue_btn.IsShown() and not frame.reply_claude_btn.IsShown()
    assert "typing claude in a terminal" in frame.desktop_note.GetValue()
    frame.on_send()  # read-only
    assert frame._runners == {}
    frame.on_open_in_claude()
    assert "started in a terminal" in env["boxes"][-1] and env["opened"] == []
    frame.on_delete_permanently()
    assert env["feedback"][-1] == ("Tidy my notes is a terminal session: The Chat Place "
                                   "doesn't delete it. Here it can only be hidden.")
    seen = _continue(frame, env, monkeypatch)
    assert seen["name"] == "Tidy my notes (continued)"
    command = fake_runner.instances[-1].command
    assert command[command.index("--resume") + 1] == "term-1" and "--fork-session" in command
    new_id = command[command.index("--session-id") + 1]
    assert frame.store.get(new_id).forked_from == "Tidy my notes"
    # A desktop app session after it gets its Open in Claude button back.
    select(frame, "Quiet one")
    frame.on_open_session()
    assert frame.reply_claude_btn.IsShown()
    assert "belongs to the Claude desktop app" in frame.desktop_note.GetValue()


def test_terminal_session_waiting_on_you_is_announced(frame, env, monkeypatch):
    """A permission or question up in the terminal: Claude Code's live file
    says "waiting", and the session needs you, not "finished"."""
    add_terminal(env, monkeypatch, "term-1", "C:\\T\\Docs", user_text("Clean the drive"))
    original = hub.load_live_status
    monkeypatch.setattr(hub, "load_live_status",
                        lambda directory=None, alive=None, started=None: original(
                            directory, alive=lambda pid: True, started=lambda pid: None))
    monkeypatch.setattr(frame, "_app_is_active", lambda: False)
    live = env["live"] / "4242.json"
    live.write_text(json.dumps({"pid": 4242, "sessionId": "term-1", "status": "busy"}))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert next(s for s in frame._snapshot.sessions if s.is_terminal).state == "working"
    live.write_text(json.dumps({"pid": 4242, "sessionId": "term-1", "status": "waiting",
                                "waitingFor": "dialog open"}))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert env["notified"][-1] == ("Clean the drive needs you", "dialog open", "terminal:term-1")
    row = select(frame, "Clean the drive")
    assert "needs you: dialog open" in frame.session_list.GetString(row)


def test_terminal_view_menu_filter_and_bug_report(frame, env, monkeypatch):
    from thechatplace.sessions import VIEW_DESKTOP, VIEW_TERMINAL
    add_terminal(env, monkeypatch, "term-1", "C:\\T\\Docs", user_text("Plan the trip"))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    frame.on_view(VIEW_TERMINAL)
    assert env["feedback"][-1] == "Showing terminal sessions: 1 session."
    assert [frame.session_list.GetString(i).split(",")[0]
            for i in range(frame.session_list.GetCount())] == ["Plan the trip"]
    frame.on_view(VIEW_DESKTOP)
    assert "Plan the trip" not in [frame.session_list.GetString(i).split(",")[0]
                                   for i in range(frame.session_list.GetCount())]
    frame.on_view("all")
    assert frame._filter_text(next(s for s in frame._snapshot.sessions
                                   if s.is_terminal)).endswith(" terminal")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    select(frame, "Plan the trip")
    menu, _actions = frame._session_menu()
    assert _enabled(menu, "Con&tinue Here") and not _enabled(menu, "Open in &Claude")
    fills = _fake_bug_dialog(monkeypatch, "open", ("T", "W", "E", ""))
    frame.on_report_bug()
    assert any(line.startswith("Sessions listed: 2 desktop app, 1 Chat Place, 1 terminal")
               for line in fills.seen)


def test_cowork_session_menu_remote_control_and_filter(frame, env, monkeypatch):
    add_cowork(env, "local_cw", "cli-cw", "Sort the receipts", user_text("Hi"))
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert frame._snapshot.cowork_cli_ids == {"cli-cw"}
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    select(frame, "Sort the receipts")
    menu, _actions = frame._session_menu()
    # Listed, but heard as unavailable, as its button is hidden.
    assert "Con&tinue Here..." in _labels(menu) and not _enabled(menu, "Con&tinue Here")
    menu.Destroy()
    offered = []
    monkeypatch.setattr(frame, "_choose",
                        lambda title, prompt, choices: offered.extend(choices) or None)
    frame.on_remote_control()
    assert offered and not any("Continue it here" in choice for choice in offered)
    # Ctrl+F finds it by "Cowork", though its folder has another name.
    frame._set_session_filter("cowork receipts")
    assert [frame.session_list.GetString(i).split(",")[0]
            for i in range(frame.session_list.GetCount())] == ["Sort the receipts"]


# -- full messages as a formatted page (#190) ----------------------------------------------


def _load_reply(frame, env, text):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Show me"), assistant_block(text_block(text), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() == 2)


def _fake_viewer(monkeypatch, result):
    from thechatplace.ui import main_frame
    shown = []

    class FakeViewer:
        def __init__(self, parent, title, page):
            shown.append((title, page))

        def ShowModal(self):
            return result

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "formatted_view_available", lambda: True)
    monkeypatch.setattr(main_frame, "FormattedMessageDialog", FakeViewer)
    return shown


def test_full_message_opens_as_a_formatted_page(frame, env, monkeypatch):
    _load_reply(frame, env, "## Result\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    shown = _fake_viewer(monkeypatch, wx.ID_CANCEL)
    plain = []
    monkeypatch.setattr("thechatplace.ui.main_frame.MessageDialog",
                        lambda *a: plain.append(a) or pytest.fail("plain text opened"))
    frame.on_read_message()
    title, page = shown[0]
    assert title == "Message from Claude"
    assert "<h2>Result</h2>" in page and "<th>a</th>" in page and "<td>2</td>" in page


def test_read_as_plain_text_and_the_setting_open_the_text_box(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ID_PLAIN_TEXT, MessageDialog
    _load_reply(frame, env, "## Result")
    shown = _fake_viewer(monkeypatch, ID_PLAIN_TEXT)
    opened = []

    class Plain(MessageDialog):
        def ShowModal(self):
            opened.append(self.text.GetValue())
            return wx.ID_CANCEL
    monkeypatch.setattr("thechatplace.ui.main_frame.MessageDialog", Plain)
    frame.on_read_message()
    assert len(shown) == 1 and opened == ["## Result"]
    frame.speech.formatted_messages = False
    frame.on_read_message()
    assert len(shown) == 1 and len(opened) == 2  # straight to the text box


def test_settings_dialog_has_the_formatted_page_choice(frame):
    from thechatplace.ui.dialogs import SettingsDialog
    dialog = SettingsDialog(frame, speech.SpeechSettings(formatted_messages=False),
                            speech.default_options())
    try:
        assert not dialog.formatted.GetValue()
        dialog.formatted.SetValue(True)
        assert dialog.get_settings().formatted_messages
    finally:
        dialog.Destroy()


def test_f1_shows_the_shortcuts_page_or_the_text_box(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ID_PLAIN_TEXT
    shown = _fake_viewer(monkeypatch, wx.ID_CANCEL)
    texts = []
    monkeypatch.setattr(frame, "_modal", lambda dialog: texts.append(dialog) or dialog.Destroy())
    frame.on_shortcuts()
    assert shown[0][0] == "Keyboard Shortcuts"
    assert ">Session list</h2>" in shown[0][1] and texts == []
    shown2 = _fake_viewer(monkeypatch, ID_PLAIN_TEXT)
    frame.on_shortcuts()
    assert len(shown2) == 1 and len(texts) == 1  # Read as Plain Text: the text box


def test_help_user_guide_shows_the_guide_page_or_the_text_box(frame, env, monkeypatch):
    # #104: the guide shipped in assets, as a page read by heading, or the
    # text box on Read as Plain Text or without the formatted view.
    from thechatplace.ui.dialogs import ID_PLAIN_TEXT, MessageDialog
    texts = []
    monkeypatch.setattr(frame, "_modal", lambda dialog: texts.append(dialog) or dialog.Destroy())
    shown = _fake_viewer(monkeypatch, wx.ID_CANCEL)
    frame.on_user_guide()
    assert shown[0][0] == "User Guide"
    assert "<h1>The Chat Place User Guide</h1>" in shown[0][1]
    assert ">When Claude needs you</h2>" in shown[0][1] and texts == []
    _fake_viewer(monkeypatch, ID_PLAIN_TEXT)
    frame.on_user_guide()
    assert len(texts) == 1 and isinstance(texts[0], MessageDialog)


def test_help_user_guide_text_box_has_no_markdown(frame, env, monkeypatch):
    # Without the formatted view (a Mac, or no WebView2): straight to the text
    # box, as clean text.
    seen = []
    monkeypatch.setattr(frame, "_modal",
                        lambda dialog: seen.append(dialog.text.GetValue()) or dialog.Destroy())
    frame.on_user_guide()
    assert seen and seen[0].startswith("The Chat Place User Guide")
    assert "**" not in seen[0] and "](" not in seen[0]


def test_help_user_guide_says_so_when_the_guide_is_missing(frame, env, monkeypatch, tmp_path):
    from thechatplace.ui import main_frame
    boxes = []
    monkeypatch.setattr(main_frame.wx, "MessageBox",
                        lambda message, caption, *a: boxes.append((message, caption)))
    monkeypatch.setattr(platform_paths, "user_guide_path", lambda: tmp_path / "missing.md")
    frame.on_user_guide()
    assert boxes and boxes[0][0].startswith("Couldn't open the user guide")
    assert boxes[0][1] == "The Chat Place"


def test_no_menu_repeats_an_access_letter(frame):
    # A letter two items share only moves between them (#104: User Guide
    # first took Check for Updates' U). Each menu, and each submenu, on its own.
    def letter(label):
        label = label.replace("&&", "")
        at = label.find("&")
        return label[at + 1].lower() if 0 <= at < len(label) - 1 else None

    def check(menu, where):
        letters = {}
        for item in menu.GetMenuItems():
            if item.IsSeparator():
                continue
            label = item.GetItemLabel().split("\t")[0]
            key = letter(label)
            if key:
                assert key not in letters, f"{where}: {label!r} and {letters[key]!r} share {key}"
                letters[key] = label
            if item.GetSubMenu():
                check(item.GetSubMenu(), f"{where}, {item.GetItemLabelText()}")

    bar = frame.GetMenuBar()
    for i in range(bar.GetMenuCount()):
        check(bar.GetMenu(i), bar.GetMenuLabelText(i))


def test_shortcuts_and_messages_fall_back_to_the_text_box(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    texts = []
    monkeypatch.setattr(frame, "_modal", lambda dialog: texts.append(dialog) or dialog.Destroy())
    # No WebView2 (the fixture's default): straight to the text box.
    frame.on_shortcuts()
    assert len(texts) == 1

    class Broken:
        def __init__(self, *args):
            raise RuntimeError("Couldn't show the formatted page: no runtime")
    monkeypatch.setattr(main_frame, "formatted_view_available", lambda: True)
    monkeypatch.setattr(main_frame, "FormattedMessageDialog", Broken)
    frame.on_shortcuts()
    assert len(texts) == 2
    assert frame.GetStatusBar().GetStatusText() == "Couldn't show the formatted page: no runtime"
    # Read Full Message takes the same way back to its text box.
    from thechatplace.ui.dialogs import MessageDialog
    _load_reply(frame, env, "## Result")
    opened = []

    class Plain(MessageDialog):
        def ShowModal(self):
            opened.append(self.text.GetValue())
            return wx.ID_CANCEL
    monkeypatch.setattr(main_frame, "MessageDialog", Plain)
    frame.on_read_message()
    assert opened == ["## Result"]


# -- sorting the session list ---------------------------------------------------------------


def test_sort_sessions_menu_reorders_keeps_place_and_remembers(frame, env):
    from thechatplace.speech import SpeechSettings
    assert frame.sort_items["status"].IsChecked()
    # Idle and a day old: last by status, first by title and by age.
    add_desktop(env, "local_z", "cli-z", "Aardvark", ago=86_400_000)
    frame.refresh_sessions(resort=True)
    settle(frame)
    titles = lambda: [s.split(",")[0] for s in frame.session_list.GetStrings()]  # noqa: E731
    assert titles() == ["Blocked one", "Hub probe", "Quiet one", "Aardvark"]
    select(frame, "Quiet one")
    frame.on_sort("title")
    settle(frame)
    assert titles() == ["Aardvark", "Blocked one", "Hub probe", "Quiet one"]
    assert frame.session_list.GetStringSelection().startswith("Quiet one")  # same session
    frame.on_sort("oldest")
    settle(frame)
    assert titles()[0] == "Aardvark" and titles()[-1] == "Hub probe"  # oldest first, newest last
    assert frame.session_list.GetStringSelection().startswith("Quiet one")  # same session
    assert frame.sort_items["oldest"].IsChecked() and not frame.sort_items["title"].IsChecked()
    assert env["feedback"][-1] == "Sessions sorted oldest first."
    assert SpeechSettings.load().session_order == "oldest"
    frame.on_sort("oldest")
    assert env["feedback"][-1] == "Sessions are already sorted oldest first."


def test_settings_dialog_keeps_the_sort_order(frame):
    from thechatplace.ui.dialogs import SettingsDialog
    dialog = SettingsDialog(frame, speech.SpeechSettings(session_order="folder"),
                            speech.default_options())
    try:
        assert dialog.get_settings().session_order == "folder"
    finally:
        dialog.Destroy()


def test_saved_sort_order_is_used_and_checked_at_start(env):
    from thechatplace.ui.main_frame import MainFrame
    speech.SpeechSettings(session_order="folder").save()
    add_desktop(env, "local_a", "cli-a", "Quiet one", cwd="C:\\G\\Zeta")
    add_desktop(env, "local_b", "cli-b", "Other", cwd="C:\\G\\Alpha")
    window = MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"),
                       check_updates_at_start=False)
    try:
        assert pump(lambda: window.session_list.GetCount() == 2)
        assert [o for o, item in window.sort_items.items() if item.IsChecked()] == ["folder"]
        assert window.session_list.GetString(0).startswith("Other, Alpha")
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        window.Destroy()
        wx.GetApp().ProcessPendingEvents()


# -- F6, Shift+F6 and the status bar (#10) ---------------------------------------------------


def _walk(frame, monkeypatch, start, steps, forward=True):
    """Press F6 (or Shift+F6) ``steps`` times from ``start``; the panes visited."""
    focused = {"window": start}
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focused["window"]))
    targets = {frame.PANE_SESSIONS: frame.session_list, frame.PANE_MESSAGES: frame.chat_list,
               frame.PANE_STATUS: frame.status_text}
    visited = []

    def focus_pane(pane):
        visited.append(pane)
        focused["window"] = targets.get(pane, frame.reply_text)
    monkeypatch.setattr(frame, "focus_pane", focus_pane)
    for _ in range(steps):
        frame.cycle_focus(forward)
    return visited


def test_f6_with_nothing_loaded_skips_the_reply(frame, monkeypatch):
    assert _walk(frame, monkeypatch, frame.session_list, 3) == [
        frame.PANE_MESSAGES, frame.PANE_STATUS, frame.PANE_SESSIONS]
    assert _walk(frame, monkeypatch, frame.session_list, 3, forward=False) == [
        frame.PANE_STATUS, frame.PANE_MESSAGES, frame.PANE_SESSIONS]


def test_f6_with_a_session_loaded_visits_every_part(frame, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    assert _walk(frame, monkeypatch, frame.session_list, 4) == [
        frame.PANE_MESSAGES, frame.PANE_REPLY, frame.PANE_STATUS, frame.PANE_SESSIONS]
    # From Send (or any control after the messages), forward is the status bar.
    assert _walk(frame, monkeypatch, frame.send_btn, 1) == [frame.PANE_STATUS]
    assert _walk(frame, monkeypatch, frame.send_btn, 1, forward=False) == [frame.PANE_MESSAGES]


def test_f6_key_and_shift_f6_reach_cycle_focus(frame, monkeypatch):
    calls = []
    monkeypatch.setattr(frame, "cycle_focus", lambda forward=True: calls.append(forward))
    for shift in (False, True):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_F6)
        event.SetShiftDown(shift)
        frame._on_char_hook(event)
    assert calls == [True, False]


@msaa
def test_status_bar_parts_are_read_only_text_and_buttons(frame):
    frame._status("Hub probe: Claude is using Bash.")
    bar = frame.GetStatusBar()
    assert frame.status_text.GetLabel() == "Hub probe: Claude is using Bash."
    assert bar.GetStatusText(0) == "Hub probe: Claude is using Bash."  # read-status-bar key
    # Information: focusable static text, no caret, never a Tab stop.
    assert frame.status_text.GetParent() is bar
    assert not isinstance(frame.status_text, wx.TextCtrl)
    role = frame.status_text.GetAccessible().GetRole(0)
    assert role == (wx.ACC_OK, wx.ROLE_SYSTEM_STATICTEXT)
    assert frame.status_text.GetAccessible().GetName(0) == (
        wx.ACC_OK, "Hub probe: Claude is using Bash.")
    assert frame.status_text not in tab_order(frame)
    assert frame.status_session.GetAccessible().GetName(0) == (wx.ACC_OK, "No session loaded")
    # Buttons only when they have something to say.
    assert not frame.status_parts.get("update").IsShown()
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.status_session.GetLabel().startswith("Hub probe: ")
    assert bar.GetStatusText(1) == frame.status_session.GetLabel()


def test_needs_you_button_goes_to_the_session(frame, env, monkeypatch):
    settle(frame)
    button = frame.status_parts.get("needs_you")
    assert button.IsShown() and button.GetLabel() == "1 session needs you"
    went = []
    monkeypatch.setattr(frame, "focus_sessions", lambda: went.append(True))
    frame._go_to_needs_you()
    assert went == [True]
    assert frame.session_list.GetStringSelection().startswith("Blocked one")
    assert frame.status_parts.shown()[2] is button  # message, session, then it


def test_arrows_move_between_status_bar_parts(frame, monkeypatch):
    settle(frame)
    parts = frame.status_parts.shown()
    assert len(parts) >= 3
    focused = {"window": parts[0]}
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focused["window"]))
    for part in parts:
        monkeypatch.setattr(part, "SetFocus", lambda p=part: focused.update(window=p))

    def press(key):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(key)
        frame._on_char_hook(event)
    press(wx.WXK_RIGHT)
    assert focused["window"] is parts[1]
    press(wx.WXK_LEFT)
    press(wx.WXK_LEFT)  # stops at the first
    assert focused["window"] is parts[0]
    press(wx.WXK_END)
    assert focused["window"] is parts[-1]
    press(wx.WXK_RIGHT)  # and at the last
    assert focused["window"] is parts[-1]
    press(wx.WXK_HOME)
    assert focused["window"] is parts[0]
    assert frame._current_pane() == frame.PANE_STATUS


def test_ctrl_9_goes_to_the_status_bar(frame, monkeypatch):
    focused = []
    monkeypatch.setattr(frame.status_text, "SetFocus", lambda: focused.append(True))
    labels = [item.GetItemLabel() for item in frame.GetMenuBar().GetMenu(1).GetMenuItems()]
    assert "Go to Status &Bar\tCtrl+9" in labels
    frame.focus_status()
    assert focused == [True]


def test_tab_and_shift_tab_leave_the_status_bar(frame, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.status_text))
    went = []
    monkeypatch.setattr(frame, "focus_sessions", lambda: went.append("sessions"))
    monkeypatch.setattr(frame.refresh_btn, "SetFocus", lambda: went.append("refresh"))
    for shift in (False, True):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_TAB)
        event.SetShiftDown(shift)
        frame._on_char_hook(event)
    assert went == ["sessions", "refresh"]


# -- announcing tool activity (#12) ----------------------------------------------------------


def test_own_session_tool_calls_and_notes_are_spoken_together(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._set_activity(True)
    for event in (TurnEvent("text", text="Let me check the **build**."),
                  TurnEvent("tool", text="Bash", detail="Bash: git status"),
                  TurnEvent("tool", text="Read", detail="Read: C:\\r\\main.py")):
        frame._on_turn_event({"id": "own-1"}, "Hub probe", event)
    assert frame._activity_timer is not None
    frame._activity_timer.Stop()
    frame._flush_activity()
    assert env["feedback"][-1] == ("Let me check the build. Using Bash: git status; "
                                   "Read: C:\\r\\main.py.")


def test_activity_is_quiet_with_tool_activity_off_or_another_session(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("tool", text="Bash"))
    assert frame._activity == []  # Show Tool Activity is off
    frame._set_activity(True)
    frame._on_turn_event({"id": "own-2"}, "Other", TurnEvent("tool", text="Bash"))
    assert frame._activity == []  # not the open session


def test_finishing_the_turn_drops_unspoken_activity(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._set_activity(True)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("text", text="All done."))
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="All done."))
    assert frame._activity == [] and frame._activity_timer is None
    assert env["spoken"][-1].startswith("Hub probe replied. All done")  # said once, as the reply


def test_desktop_session_tool_calls_are_spoken(frame, env):
    from records import tool_use_block
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Build it")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame._set_activity(True)
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Build it"),
        assistant_block(tool_use_block("Bash", {"command": "make"}), "m1")])
    frame._refresh_chat()
    assert pump(lambda: frame._activity)
    frame._activity_timer.Stop()
    frame._flush_activity()
    assert env["feedback"][-1] == "Using Bash: make."


def test_last_note_waits_for_a_tool_call_so_a_slow_finish_says_the_reply_once(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._set_activity(True)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("tool", text="Bash", detail="Bash: make"))
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("text", text="All built."))
    frame._activity_timer.Stop()
    frame._flush_activity()
    assert env["feedback"][-1] == "Using Bash: make."  # the note is held back
    assert frame._activity == [("text", "All built.")]
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="All built."))
    assert frame._activity == []
    assert sum("All built" in said for said in env["feedback"] + env["spoken"]) == 1


def test_a_desktop_reply_drops_tool_calls_still_waiting(frame, env):
    select(frame, "Quiet one")
    frame.on_open_session()
    frame._set_activity(True)
    frame._queue_activity("tool", "Bash: npm test")
    from thechatplace.transcript import ASSISTANT, ChatMessage
    frame._chat_loaded = True
    frame._apply_chat(frame._open_generation, True,
                      [ChatMessage(ASSISTANT, "Tests pass.", "", "r1")], 0, None)
    assert frame._activity == [] and frame._activity_timer is None
    assert env["spoken"][-1].startswith("Quiet one replied. Tests pass")
# -- whole messages in the messages list (#11) -----------------------------------------------


@msaa
def test_screen_reader_reads_whole_messages_in_the_list(frame, env):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Check the build"),
        assistant_block(text_block("## Result\nIt **passes**.\n\n- tests: 352\n\n```py\nx = 1\n```\nShip it."),
                        "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() == 2)
    assert frame.chat_list.GetString(1) == "Claude: ## Result"  # the row shows the first line
    accessible = frame.chat_list._hub_accessible
    assert accessible.GetName(2) == (wx.ACC_OK, "Claude: Result. It passes. tests: 352. "
                                                "Code block, Python, 1 line. Ship it.")
    assert accessible.GetName(0) == (wx.ACC_OK, "Messages in Quiet one (idle, read-only)")
    # The row's own text: the name must still be a string, or MSAA errors.
    assert accessible.GetName(9) == (wx.ACC_NOT_IMPLEMENTED, "")  # no such row
    frame.speech.full_messages_in_list = False
    assert accessible.GetName(2) == (wx.ACC_NOT_IMPLEMENTED, "")


@msaa
def test_rows_that_are_not_messages_read_as_shown(frame):
    accessible = frame.chat_list._hub_accessible
    assert frame.chat_list.GetString(0).startswith("No session loaded")
    assert accessible.GetName(1) == (wx.ACC_NOT_IMPLEMENTED, "")


def test_whole_message_setting_round_trips_and_is_in_settings(frame, tmp_path):
    from thechatplace.ui.dialogs import SettingsDialog
    path = tmp_path / "s.json"
    assert speech.SpeechSettings.load(path).full_messages_in_list  # on by default
    speech.SpeechSettings(full_messages_in_list=False).save(path)
    assert not speech.SpeechSettings.load(path).full_messages_in_list
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        assert dialog.whole_in_list.GetValue()
        dialog.whole_in_list.SetValue(False)
        assert not dialog.get_settings().full_messages_in_list
    finally:
        dialog.Destroy()


# -- export (#33) ---------------------------------------------------------------------------


def _fake_save_dialog(monkeypatch, path, filter_index=0):
    class Save:
        def __init__(self, parent, message, defaultDir="", defaultFile="", wildcard="", style=0):
            Save.default_file = defaultFile
            Save.wildcard = wildcard

        def ShowModal(self):
            return wx.ID_OK

        def GetPath(self):
            return str(path)

        def GetFilterIndex(self):
            return filter_index

        def Destroy(self):
            pass
    monkeypatch.setattr(wx, "FileDialog", Save)
    return Save


def test_export_the_loaded_session_as_markdown(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Check the build"),
        assistant_block(text_block("## Result\nIt passes."), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    target = env["tmp"] / "out.md"
    dialog = _fake_save_dialog(monkeypatch, target)
    frame.on_export()
    assert env["feedback"][-1] == "Exporting Quiet one."  # done in the background
    assert pump(lambda: env["feedback"][-1].startswith("Exported"))
    assert dialog.default_file.startswith("Quiet one ") and dialog.default_file.endswith(".md")
    assert "Web page (*.html)" in dialog.wildcard
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Quiet one\n")
    assert "## Claude, " in text and "\n#### Result\nIt passes." in text  # its ## moved down two
    assert env["feedback"][-1] == f"Exported Quiet one, 2 messages, to out.md in {env['tmp']}."
    assert env["boxes"] == []  # no box to dismiss afterwards


def test_export_asks_before_replacing_a_file_whose_extension_it_added(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Hello")])
    select(frame, "Quiet one")
    (env["tmp"] / "notes.v2.md").write_text("keep me", encoding="utf-8")
    _fake_save_dialog(monkeypatch, env["tmp"] / "notes.v2", filter_index=0)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: env["boxes"].append(a[0]) or wx.NO)
    frame.on_export()
    assert "notes.v2.md already exists" in env["boxes"][-1]
    assert (env["tmp"] / "notes.v2.md").read_text(encoding="utf-8") == "keep me"


def test_export_an_unloaded_session_reads_its_transcript_and_the_extension_wins(frame, env,
                                                                                monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Hello")])
    select(frame, "Quiet one")  # selected, not loaded
    target = env["tmp"] / "page.html"
    _fake_save_dialog(monkeypatch, target, filter_index=0)  # Markdown chosen, .html typed
    frame.on_export()
    assert pump(lambda: env["feedback"][-1].startswith("Exported"))
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_export_with_no_transcript_says_so(frame, env, monkeypatch):
    select(frame, "Quiet one")
    monkeypatch.setattr(wx, "FileDialog", lambda *a, **k: pytest.fail("no dialog"))
    frame.on_export()
    assert "no messages to export" in env["boxes"][-1]
# -- views and groups in the window (#31, #32) ------------------------------------------------


def _titles(frame):
    return [s.split(",")[0] for s in frame.session_list.GetStrings()]


def test_show_sessions_filters_names_the_list_and_remembers(frame, env):
    add_desktop(env, "local_z", "cli-z", "Old work", isArchived=True)
    frame.refresh_sessions(resort=True)
    settle(frame)
    assert "Old work" not in _titles(frame)  # archived: not in All
    assert frame.view_items["all"].IsChecked()
    frame.on_view("needs")
    assert _titles(frame) == ["Blocked one"]
    assert frame.session_list.GetName() == "Session list, needs you, 1 of 3"
    assert frame.sessions_label.GetLabel() == "Session &list, needs you, 1 of 3:"  # Alt+L kept
    assert env["feedback"][-1] == "Showing needs you: 1 session."
    assert speech.SpeechSettings.load().session_view == "needs"
    frame.on_view("archived")
    assert _titles(frame) == ["Old work"]
    assert frame.session_list.GetString(0).endswith(", archived")
    frame.on_view("own")
    assert _titles(frame) == ["Hub probe"]
    frame.on_view("all")
    assert frame.session_list.GetName() == "Session list"
    assert sorted(_titles(frame)) == ["Blocked one", "Hub probe", "Quiet one"]


def test_a_refresh_keeps_the_view(frame, env):
    frame.on_view("own")
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert _titles(frame) == ["Hub probe"]
    assert env["feedback"][-1].endswith("Showing Chat Place sessions: 1.")


def test_add_to_a_new_group_then_show_it_then_remove(frame, env, monkeypatch):
    select(frame, "Quiet one")
    picks = iter([0, 0])  # "New group..." (the only choice so far), then the group
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices: next(picks))
    monkeypatch.setattr(frame, "_ask_group_name", lambda title, value="": "Work")
    frame.on_add_to_group()
    assert env["feedback"][-1] == "Added Quiet one to Work."
    assert frame.groups.names() == ["Work"] and frame.groups.members("Work") == ["local_a"]
    row = [s for s in frame.session_list.GetStrings() if s.startswith("Quiet one")][0]
    assert row.endswith(", group Work")
    assert "group:Work" in frame.view_items
    frame.on_view("group:Work")
    assert _titles(frame) == ["Quiet one"]
    assert env["feedback"][-1] == "Showing group Work: 1 session."
    select(frame, "Quiet one")
    frame.on_remove_from_group()
    assert env["feedback"][-1] == "Removed Quiet one from Work."
    assert _titles(frame) == []
    assert frame.session_list.GetName() == "Session list, group Work, 0 of 3"


def test_deleting_the_group_being_shown_goes_back_to_all(frame, env, monkeypatch):
    frame.groups.create("Temp")
    frame._build_show_menu()
    frame.on_view("group:Temp")
    frame.groups.delete("Temp")

    class Closes:
        renamed = {}

        def __init__(self, *a):
            pass

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr("thechatplace.ui.main_frame.ManageGroupsDialog", Closes)
    frame.on_manage_groups()
    assert frame.speech.session_view == "all" and frame.view_items["all"].IsChecked()
    assert "group:Temp" not in frame.view_items


def test_manage_groups_dialog_lists_renames_and_deletes(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ManageGroupsDialog
    frame.groups.create("Work")
    frame.groups.add("Work", "local_a")
    frame.groups.create("Home")
    dialog = ManageGroupsDialog(frame, frame.groups, {"Work": 1, "Home": 0})
    try:
        assert list(dialog.list.GetStrings()) == ["Work, 1 session", "Home, 0 sessions"]
        monkeypatch.setattr(dialog, "_ask", lambda title, value="": "Jobs")
        dialog.list.SetSelection(0)
        dialog.on_rename()
        assert frame.groups.names() == ["Jobs", "Home"]
        assert dialog.list.GetString(0) == "Jobs, 1 session"
        dialog.list.SetSelection(1)
        dialog.on_delete()  # wx.MessageBox is patched to say yes
        assert frame.groups.names() == ["Jobs"]
        monkeypatch.setattr(dialog, "_ask", lambda title, value="": "jobs")
        dialog.on_new()  # same name, other case: refused, says why
        assert frame.groups.names() == ["Jobs"]
        assert "already a group" in env["boxes"][-1]
    finally:
        dialog.Destroy()


def test_session_list_keeps_alt_l_in_every_view(frame):
    assert frame.sessions_label.GetLabel() == "Session &list:"
    frame.groups.create("R&D")
    frame._build_show_menu()
    frame.on_view("group:R&D")
    assert frame.sessions_label.GetLabel() == "Session &list, group R&&D, 0 of 3:"
    assert frame.session_list.GetName() == "Session list, group R&D, 0 of 3"


def test_a_session_leaving_the_view_stays_while_you_are_on_it(frame, env, monkeypatch):
    frame.on_view("needs")
    select(frame, "Blocked one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    add_desktop(env, "local_b", "cli-b", "Blocked one")  # it no longer needs you
    frame.refresh_sessions()
    settle(frame)
    assert frame.session_list.GetStringSelection().startswith("Blocked one, Repo, idle")
    frame.refresh_sessions(resort=True)  # F5: the view is put right
    settle(frame)
    assert frame.session_list.GetCount() == 0


def test_deleting_a_session_takes_it_out_of_its_groups(frame, env):
    frame.groups.create("Work")
    frame.groups.add("Work", "own:own-1")
    select(frame, "Hub probe")
    frame.on_delete_permanently()  # wx.MessageBox is patched to say yes
    assert frame.groups.members("Work") == []


def test_renaming_the_group_in_view_follows_it(frame, monkeypatch):
    frame.groups.create("Work")
    frame._build_show_menu()
    frame.on_view("group:Work")

    class Renames:
        renamed = {}

        def __init__(self, parent, groups, counts):
            groups.rename("Work", "Jobs")
            Renames.renamed = {"Work": "Jobs"}

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr("thechatplace.ui.main_frame.ManageGroupsDialog", Renames)
    frame.on_manage_groups()
    assert frame.speech.session_view == "group:Jobs"
    assert frame.view_items["group:Jobs"].IsChecked()
    assert speech.SpeechSettings.load().session_view == "group:Jobs"


# -- reporting a bug (#28) -------------------------------------------------------------------


def _fake_bug_dialog(monkeypatch, action, values):
    class Fills:
        seen = None

        def __init__(self, parent, lines):
            Fills.seen = lines
            self.action = action

        def ShowModal(self):
            return wx.ID_OK

        def values(self):
            return values

        def Destroy(self):
            pass
    monkeypatch.setattr("thechatplace.ui.main_frame.BugReportDialog", Fills)
    return Fills


def test_report_a_bug_opens_github_with_the_report(frame, env, monkeypatch):
    fills = _fake_bug_dialog(monkeypatch, "open",
                             ("Sort resets", "It went back to status.", "Stay sorted", ""))
    frame.on_report_bug()
    assert any(line.startswith("Sessions listed: 2 desktop app, 1 Chat Place")
               for line in fills.seen)
    assert env["opened"][-1].startswith("https://github.com/TheIdeaPlace/AIChat/issues/new?")
    assert "title=Sort+resets" in env["opened"][-1]
    assert env["feedback"][-1].startswith("Opened GitHub's new issue page")


def test_copy_report_only_copies(frame, env, monkeypatch):
    _fake_bug_dialog(monkeypatch, "copy", ("Sort resets", "It went back.", "", ""))
    frame.on_report_bug()
    assert env["opened"] == []
    assert env["feedback"][-1] == "Report copied. Email it to support@theideaplace.net."
    assert env["copied"][-1].startswith("Sort resets\n\n### What happened\nIt went back.")


def test_bug_dialog_needs_a_summary_and_what_happened(frame, env):
    from thechatplace.ui.dialogs import BugReportDialog
    dialog = BugReportDialog(frame, ["The Chat Place: 0.1.0"])
    try:
        assert dialog.included.GetValue() == "The Chat Place: 0.1.0"
        assert dialog.GetDefaultItem() is dialog.open_btn
        dialog._finish("open")
        assert "write a summary" in env["boxes"][-1] and dialog.action == ""
        dialog.summary.SetValue("  Two   words ")
        dialog._finish("open")
        assert "write what happened" in env["boxes"][-1]
        dialog.happened.SetValue("It broke.")
        assert dialog.values()[0] == "Two words"
    finally:
        dialog.Destroy()


def test_bug_report_never_names_a_group(frame, env, monkeypatch):
    frame.groups.create("Acme client work")
    frame._build_show_menu()
    frame.on_view("group:Acme client work")
    fills = _fake_bug_dialog(monkeypatch, "copy", ("x", "y", "", ""))
    frame.on_report_bug()
    assert "Acme" not in "\n".join(fills.seen) and "Acme" not in env["copied"][-1]
    assert "Session list: showing a group, sorted status" in fills.seen


# -- inserting a command or skill (#23) -------------------------------------------------------


def test_insert_command_is_for_own_sessions(frame, env):
    select(frame, "Quiet one")
    frame.on_open_session()
    frame.on_insert_command()
    assert env["feedback"][-1].startswith("Commands and skills are for The Chat Place")


def test_commands_are_fetched_when_an_own_session_loads_and_inserted(frame, env, monkeypatch):
    # A claude to find, as on Kelly's PC; the fetch itself is faked by the fixture.
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    from thechatplace.ui import dialogs
    select(frame, "Hub probe")
    frame.on_open_session()
    assert pump(lambda: _folder_key("C:\\G\\Scratch") in frame._commands)
    seen = {}

    class Picks(dialogs.CommandPickerDialog):
        def ShowModal(self):
            seen["rows"] = list(self.list.GetStrings())
            self.search.SetValue("summary")  # filters by description too
            seen["filtered"] = list(self.list.GetStrings())
            self.chosen = self._shown[0]
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.CommandPickerDialog", Picks)
    frame.reply_text.SetValue("/context please keep the tests")
    frame.on_insert_command()
    assert seen["rows"][0] == "/blog-publish: Publish a post to the blog"
    assert seen["rows"][1].startswith("/compact <optional instructions>, Claude Code: ")
    assert seen["filtered"] == [Picks.row(FAKE_COMMANDS[1])]
    assert frame.reply_text.GetValue() == "/compact please keep the tests"  # replaced, kept rest
    assert env["feedback"][-1] == "Inserted /compact."


@windows_paths
def test_a_turn_keeps_the_folder_commands_current(frame, env):
    from thechatplace.claude_cli import StreamParser
    select(frame, "Hub probe")
    frame.on_open_session()
    runner = FakeRunner([], "C:\\G\\Scratch", "", None)
    runner.parser = StreamParser()
    runner.parser.commands = [{"name": "fresh-skill"}]
    frame._runners["own-1"] = runner
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="ok"))
    assert [c["name"] for c in frame._commands[_folder_key("c:\\g\\scratch\\")]] == \
        ["fresh-skill"]  # one key per folder, whatever the case or a trailing slash


def _folder_key(cwd):
    from thechatplace.ui.main_frame import _folder_key as key
    return key(cwd)


def test_commands_never_open_by_themselves(frame, env, monkeypatch):
    # A claude to find, as on Kelly's PC; the fetch itself is faked by the fixture.
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    from thechatplace.ui import main_frame
    select(frame, "Hub probe")
    frame.on_open_session()
    assert pump(lambda: frame._commands)
    frame._commands.clear()
    monkeypatch.setattr("thechatplace.ui.main_frame.CommandPickerDialog",
                        lambda *a: pytest.fail("opened by itself"))
    frame.on_insert_command()
    assert env["feedback"][-1] == "Getting the commands for this folder."
    assert pump(lambda: env["feedback"][-1] == "Commands are ready: press Ctrl+/ or Commands.")
    frame._commands.clear()
    monkeypatch.setattr(main_frame, "fetch_commands", lambda exe, cwd: [])
    frame.on_insert_command()
    assert pump(lambda: env["feedback"][-1] == "Couldn't get the commands from Claude Code.")


def test_inserting_keeps_line_breaks_and_leaves_paths_alone(frame):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("/context\n\nSecond paragraph.")
    frame._insert_command("compact", FAKE_COMMANDS)
    assert frame.reply_text.GetValue() == "/compact \n\nSecond paragraph."
    frame.reply_text.SetValue("/path/x is broken")
    frame._insert_command("compact", FAKE_COMMANDS)
    assert frame.reply_text.GetValue() == "/compact /path/x is broken"


def test_picker_with_nothing_matching_chooses_nothing(frame):
    from thechatplace.ui.dialogs import CommandPickerDialog
    dialog = CommandPickerDialog(frame, FAKE_COMMANDS)
    try:
        dialog.search.SetValue("zzz")
        assert list(dialog.list.GetStrings()) == ["Nothing matches."]
        assert dialog.list.GetName() == "Commands and skills, 0 of 3"
        dialog._choose()
        assert dialog.chosen is None
    finally:
        dialog.Destroy()


def test_ctrl_slash_is_on_the_session_menu(frame):
    items = frame.GetMenuBar().GetMenu(0).GetMenuItems()
    item = [i for i in items if i.GetItemLabelText().startswith("Insert Command")][0]
    assert item.GetAccel() is not None and item.GetAccel().GetKeyCode() == ord("/")


# -- attachments (#22) -----------------------------------------------------------------------

TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8"
            "AAAAASUVORK5CYII=")


def _files(env):
    import base64
    image = env["tmp"] / "screen shot.png"
    image.write_bytes(base64.b64decode(TINY_PNG))
    note = env["tmp"] / "log.txt"
    note.write_text("error 42", encoding="utf-8")
    return image, note


def test_attach_then_send_puts_images_inline_and_files_as_mentions(frame, env, fake_runner):
    image, note = _files(env)
    select(frame, "Hub probe")
    frame.on_open_session()
    assert not frame.attach_list.IsShown()
    frame._add_attachments("own-1", [str(image), str(note)])
    assert frame.attach_list.IsShown()
    assert list(frame.attach_list.GetStrings()) == ["screen shot.png", "log.txt"]
    assert frame.attach_list.GetName() == ("2 attachments: screen shot.png, log.txt. "
                                           "Delete removes one")
    assert env["feedback"][-1].startswith("Attached screen shot.png, log.txt.")
    frame.reply_text.SetValue("What went wrong?")
    frame.on_send()
    runner = fake_runner.instances[-1]
    assert runner.prompt == f'What went wrong?\n\nAttached: @"{note}"'
    assert [b["type"] for b in runner.images] == ["image"]
    assert env["feedback"][-1].endswith("With 1 image.")
    assert "Attached:" not in env["feedback"][-1]  # read back as typed
    assert not frame.attach_list.IsShown() and frame._attachments == {}


def test_an_attachment_alone_can_be_sent(frame, env, fake_runner):
    image, _note = _files(env)
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._add_attachments("own-1", [str(image)])
    frame.on_send()
    assert fake_runner.instances[-1].images and fake_runner.instances[-1].prompt == ""


def test_queued_message_takes_attachments_as_mentions(frame, env):
    image, _note = _files(env)
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._add_attachments("own-1", [str(image)])
    frame.reply_text.SetValue("And this")
    frame.on_send()
    assert frame._queued["own-1"] == [f'And this\n\nAttached: @"{image}"']
    assert frame._attachments == {}


def test_delete_removes_an_attachment(frame, env):
    image, note = _files(env)
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._add_attachments("own-1", [str(image), str(note)])
    frame.attach_list.SetSelection(0)
    event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(wx.WXK_DELETE)
    frame._on_attach_key(event)
    assert frame._attachments["own-1"] == [str(note)]
    assert env["feedback"][-1] == "Removed screen shot.png. 1 attachment: log.txt."


def test_pasting_a_picture_attaches_it(frame, env, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    monkeypatch.setattr(frame, "_clipboard_image", lambda: wx.Bitmap(4, 4))

    class Paste:
        skipped = False

        def Skip(self):
            Paste.skipped = True
    frame._on_reply_paste(Paste())
    paths = frame._attachments["own-1"]
    assert len(paths) == 1 and paths[0].endswith(".png") and not Paste.skipped
    assert "pasted images" in paths[0]
    monkeypatch.setattr(frame, "_clipboard_image", lambda: None)
    frame._on_reply_paste(Paste())
    assert Paste.skipped  # text pastes as usual


def test_attachments_are_for_own_sessions_and_follow_the_session(frame, env):
    image, _note = _files(env)
    select(frame, "Quiet one")
    frame.on_open_session()
    frame.on_attach_files()
    assert env["feedback"][-1].startswith("Attachments are for The Chat Place")
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._add_attachments("own-1", [str(image)])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert not frame.attach_list.IsShown()
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.attach_list.IsShown()  # still waiting for this session's next message


def test_a_turn_that_never_starts_gives_back_your_words_and_attachments(frame, env, fake_runner):
    image, note = _files(env)
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._add_attachments("own-1", [str(image), str(note)])
    frame.reply_text.SetValue("Why does this fail?")
    frame.on_send()
    assert frame._attachments == {}
    runner = fake_runner.instances[-1]
    runner.session_started = False
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Not signed in.", is_error=True))
    assert frame.reply_text.GetValue() == "Why does this fail?"  # as typed, no Attached: line
    assert frame._attachments["own-1"] == [str(image), str(note)]  # the image too
    assert frame.attach_list.IsShown()


def test_shift_insert_pastes_through_the_same_path(frame, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    pasted = []
    monkeypatch.setattr(frame.reply_text, "Paste", lambda: pasted.append(True))
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.reply_text))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_INSERT)
    event.SetShiftDown(True)
    frame._on_char_hook(event)
    assert pasted == [True]


# -- search (#21) -----------------------------------------------------------------------------


def test_find_sessions_filters_by_text_and_escape_clears(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    monkeypatch.setattr(frame, "_ask_text", lambda title, prompt, value: "pick NAME")
    frame.on_find()
    assert [s.split(",")[0] for s in frame.session_list.GetStrings()] == ["Blocked one"]
    assert frame.session_list.GetName() == 'Session list, matching "pick NAME", 1 of 3'
    assert env["feedback"][-1] == '1 session matching "pick NAME". Escape shows them all.'
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_ESCAPE)
    frame._on_char_hook(event)
    assert frame.session_list.GetCount() == 3 and frame.session_list.GetName() == "Session list"


def test_find_sessions_works_with_a_view_and_says_nothing_found(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    frame.on_view("desktop")
    monkeypatch.setattr(frame, "_ask_text", lambda title, prompt, value: "probe")
    frame.on_find()  # "Hub probe" is a Chat Place session, not in this view
    assert frame.session_list.GetCount() == 0
    assert frame.session_list.GetName() == ('Session list, desktop app sessions, '
                                            'matching "probe", 0 of 3')
    assert env["feedback"][-1] == 'No sessions matching "probe". Escape shows them all.'


def _load_three(frame, env):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("First question"),
        assistant_block(text_block("Short line\nThe word GIRAFFE is hidden here."), "m1"),
        user_text("Second about giraffe"),
        assistant_block(text_block("No animals."), "m2")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() == 4)


def test_find_in_messages_searches_whole_text_and_moves_with_f3(frame, env, monkeypatch):
    _load_three(frame, env)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    monkeypatch.setattr(frame, "_ask_text", lambda title, prompt, value: "giraffe")
    frame.chat_list.SetSelection(0)
    frame.on_find()
    assert frame.chat_list.GetSelection() == 1  # found past the first line
    assert env["feedback"][-1] == "Found in message 2 of 4: Claude: Short line."
    frame.find_again(True)
    assert frame.chat_list.GetSelection() == 2
    frame.find_again(True)
    assert frame.chat_list.GetSelection() == 1
    assert env["feedback"][-1].endswith("Searched round from the other end.")
    frame.find_again(False)
    assert frame.chat_list.GetSelection() == 2
    assert env["feedback"][-1].endswith("Searched round from the other end.")
    frame.find_again(False)
    assert frame.chat_list.GetSelection() == 1
    assert not env["feedback"][-1].endswith("other end.")


def test_find_in_messages_says_when_nothing_matches(frame, env, monkeypatch):
    _load_three(frame, env)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    monkeypatch.setattr(frame, "_ask_text", lambda title, prompt, value: "zebra")
    frame.on_find()
    assert env["feedback"][-1] == 'No message contains "zebra".'
# -- usage and context (#19) -----------------------------------------------------------------


def _limits(seven):
    import time
    return {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.1, "resetsAt": time.time() + 3600},
        "seven_day": {"utilization": seven, "resetsAt": time.time() + 86400}}}


def test_limits_are_kept_and_a_near_limit_is_said_once(frame, env):
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("limits", data=_limits(0.5)))
    assert frame._limits["unifiedWindows"]["seven_day"]["utilization"] == 0.5
    said = len(env["spoken"])
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("limits", data=_limits(0.92)))
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("limits", data=_limits(0.93)))
    warnings = [s for s in env["spoken"][said:] if "weekly limit" in s]
    assert len(warnings) == 1 and warnings[0].startswith("You've used 92% of your weekly limit")


def _usage_dialog(frame, monkeypatch, act=None):
    """Ctrl+Shift+U with the dialog never shown: what it lists, its title,
    the list's name, and what's selected."""
    seen = {}

    def modal(dialog):
        seen.update(title=dialog.GetTitle(), name=dialog.list.GetName(),
                    selected=dialog.list.GetSelection(), escape=dialog.GetEscapeId(),
                    rows=[dialog.list.GetString(i) for i in range(dialog.list.GetCount())])
        if act:
            act(dialog)
        dialog.Destroy()
    monkeypatch.setattr(frame, "_modal", modal)
    frame.on_usage()
    return seen


def test_usage_and_context_command(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Hi"), {
        "type": "assistant", "uuid": "a1", "timestamp": "2026-10-07T03:00:00Z", "message": {
            "role": "assistant", "id": "m1", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 170_000,
                      "output_tokens": 900}}}])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame._reader is not None)
    spoken, feedback = len(env["spoken"]), len(env["feedback"])
    seen = _usage_dialog(frame, monkeypatch)
    # A list to read (#130), the first line selected; nothing spoken over it.
    assert seen["title"] == "Usage and Context" and seen["name"] == "Usage and context"
    assert seen["selected"] == 0 and seen["escape"] == wx.ID_CANCEL
    # The window isn't known yet: no percentage, and no warning.
    assert seen["rows"][0].startswith("Quiet one: Context: 171,000 tokens used; the "
                                      "window's size isn't known")
    assert seen["rows"][1:] == ["Usage limits: not known until a Chat Place session runs a turn."]
    assert len(env["spoken"]) == spoken and len(env["feedback"]) == feedback
    # A turn of ours reported this model's window and the limits: now there's
    # a percentage, a line for each limit, and over 80% it's said once, unasked.
    frame._model_windows["claude-opus-5-5"] = 200_000
    frame._limits = _limits(0.34)
    seen = _usage_dialog(frame, monkeypatch)
    assert seen["rows"][0].startswith("Quiet one: Context 86% full: 171,000 of 200,000")
    assert seen["rows"][1].startswith("5-hour limit 10% used, resets ")
    assert seen["rows"][2].startswith("Weekly limit 34% used, resets ")
    assert len(seen["rows"]) == 3
    frame._check_context()
    frame._check_context()
    assert sum("Context 86% full" in s for s in env["spoken"]) == 1


def _hook(dialog, code, ctrl=False, shift=False):
    """A key as the dialog's char hook sees it (Escape, Ctrl+C)."""
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(code)
    event.SetControlDown(ctrl)
    event.SetShiftDown(shift)
    event.SetEventObject(dialog.list)
    dialog.GetEventHandler().ProcessEvent(event)


def test_usage_dialog_with_no_session_copies_and_closes_on_escape(frame, env, monkeypatch):
    frame._limits = _limits(0.5)
    closed = []

    def act(dialog):
        dialog.Bind(wx.EVT_BUTTON, lambda e: closed.append(e.GetId()), id=wx.ID_CANCEL)
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.list))
        dialog.list.SetSelection(1)
        _hook(dialog, ord("C"), ctrl=True)  # Ctrl+C in the list: the selected line
        copies = len(env["copied"])
        _hook(dialog, ord("C"), ctrl=True, shift=True)  # not Ctrl+Shift+C
        assert len(env["copied"]) == copies
        dialog.copy_all()  # Copy All: every line
        _hook(dialog, wx.WXK_ESCAPE)
    seen = _usage_dialog(frame, monkeypatch, act)
    assert seen["rows"][0] == ("Context: no session is loaded. Load one to see how full its "
                               "context is.")
    assert [r.split(" used")[0] for r in seen["rows"][1:]] == ["5-hour limit 10%",
                                                               "Weekly limit 50%"]
    assert env["copied"][-2] == seen["rows"][1]
    assert env["copied"][-1] == "\n".join(seen["rows"])
    assert env["feedback"][-2:] == ["Copied the line.", "Copied usage and context."]
    assert closed == [wx.ID_CANCEL]  # Escape presses Close


def test_usage_dialog_says_when_the_clipboard_fails(frame, env, monkeypatch):
    monkeypatch.setattr(type(frame), "_copy_text", lambda self, text: False)
    _usage_dialog(frame, monkeypatch, lambda dialog: dialog.copy_selected())
    assert env["feedback"][-1] == "Couldn't open the clipboard."


def test_usage_dialog_is_named_for_screen_readers(frame):
    from thechatplace.ui.dialogs import UsageDialog
    dialog = UsageDialog(frame, ["Context: not known."], lambda text, one: None)
    try:
        assert dialog.GetTitle() == "Usage and Context"
        assert dialog.list.GetName() == "Usage and context"
        close = dialog.FindWindow(wx.ID_CANCEL)
        assert close is not None and close.GetLabel() == "C&lose"
        assert dialog.GetDefaultItem() is close
        labels = [c.GetLabel() for c in dialog.GetChildren() if isinstance(c, wx.Button)]
        assert labels == ["&Copy", "Copy &All", "C&lose"]
    finally:
        dialog.Destroy()


def test_a_usage_limit_failure_is_said_plainly(frame, env):
    import time
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    reset = int(time.time() + 3600)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "finished", text=f"Claude AI usage limit reached|{reset}", is_error=True))
    assert "You've reached your Claude usage limit. It resets" in env["spoken"][-1]


def test_compaction_is_said(frame, env):
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("compacted"))
    assert env["spoken"][-1] == ("Hub probe: Claude Code compacted the conversation to "
                                 "free the context.")


def test_find_says_when_it_goes_round_at_either_end(frame, env, monkeypatch):
    _load_three(frame, env)  # matches at rows 1 and 2 (of 0-3)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    frame._find_text = "giraffe"
    frame.chat_list.SetSelection(3)  # the last message
    frame.find_again(True)
    assert frame.chat_list.GetSelection() == 1
    assert env["feedback"][-1].endswith("Searched round from the other end.")
    frame.chat_list.SetSelection(0)  # the first message
    frame.find_again(False)
    assert frame.chat_list.GetSelection() == 2
    assert env["feedback"][-1].endswith("Searched round from the other end.")
    frame.chat_list.SetSelection(3)
    monkeypatch.setattr(frame, "_ask_text", lambda title, prompt, value: "giraffe")
    frame.on_find()  # a new search from the last message goes round too
    assert frame.chat_list.GetSelection() == 1
    assert env["feedback"][-1].endswith("Searched round from the other end.")


def test_find_with_a_single_match_says_so_both_ways(frame, env, monkeypatch):
    _load_three(frame, env)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    frame._find_text = "No animals"
    frame.chat_list.SetSelection(3)
    frame.find_again(True)
    assert frame.chat_list.GetSelection() == 3
    assert env["feedback"][-1].endswith("It's the only message that matches.")
    frame.find_again(False)
    assert env["feedback"][-1].endswith("It's the only message that matches.")


def test_shift_f3_with_nothing_selected_starts_from_the_last(frame, env, monkeypatch):
    _load_three(frame, env)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    frame._find_text = "No animals"  # the last message
    frame.chat_list.SetSelection(wx.NOT_FOUND)
    frame.find_again(False)
    assert frame.chat_list.GetSelection() == 3
    assert not env["feedback"][-1].endswith("other end.")


def test_f5_mentions_the_filter(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    frame._set_session_filter("quiet")
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert env["feedback"][-1].endswith('Showing sessions matching "quiet": 1.')


def _load_code(frame, env):
    fence = "`" * 3
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Show me"),
        assistant_block(text_block(f"Two ways:\n\n{fence}python\nprint(1)\n{fence}\n\n"
                                   f"or\n\n~~~bash\necho hi\necho there\n~~~\nDone."), "m1"),
        user_text("Thanks")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() == 3)


def test_code_blocks_are_described_listed_and_copied(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import CodeBlocksDialog
    _load_code(frame, env)
    assert frame._message_item_text(1) == ("Claude: Two ways. Code block, Python, 1 line. or. "
                                           "Code block, Bash, 2 lines. Done.")
    frame.chat_list.SetSelection(1)
    frame.copy_last_code_block()  # Ctrl+Shift+C: the last block
    assert env["copied"][-1] == "echo hi\necho there"
    assert env["feedback"][-1] == "Copied: Code block, Bash, 2 lines."
    shown = []

    def modal(dialog):
        shown.append([dialog.list.GetString(i) for i in range(dialog.list.GetCount())])
        assert dialog.code.GetValue() == "print(1)"
        dialog.copy_selected()
        dialog.Destroy()
    monkeypatch.setattr(frame, "_modal", modal)
    frame.on_code_blocks()
    assert shown == [["Python, 1 line: print(1)", "Bash, 2 lines: echo hi"]]
    assert env["copied"][-1] == "print(1)"
    assert isinstance(CodeBlocksDialog, type)
    menu = frame._message_menu()
    labels = {item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()}
    assert labels["Code Blocks..."] and labels["Copy Last Code Block"]
    menu.Destroy()


def test_messages_without_code_say_so(frame, env, monkeypatch):
    _load_code(frame, env)
    frame.chat_list.SetSelection(2)  # "Thanks"
    frame.copy_last_code_block()
    assert env["feedback"][-1] == "This message has no code blocks."
    monkeypatch.setattr(frame, "_modal", lambda dialog: pytest.fail("no dialog"))
    frame.on_code_blocks()
    assert env["feedback"][-1] == "This message has no code blocks."
    menu = frame._message_menu()
    labels = {item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()}
    assert not labels["Code Blocks..."] and not labels["Copy Last Code Block"]
    menu.Destroy()


def test_ctrl_c_copies_the_message_and_ctrl_shift_c_its_code(frame, env, monkeypatch):
    _load_code(frame, env)
    frame.chat_list.SetSelection(1)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    for shift, expected in ((False, "Claude:\nTwo ways:"), (True, "echo hi\necho there")):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("C"))
        event.SetControlDown(True)
        event.SetShiftDown(shift)
        frame._on_char_hook(event)
        assert env["copied"][-1].startswith(expected)


@windows_paths
def test_changed_files_view_and_turn_end_summary(frame, env, monkeypatch):
    patch = [{"oldStart": 3, "oldLines": 1, "newStart": 3, "newLines": 2,
              "lines": ["-old", "+new", "+more"]}]
    path = add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Fix it"),
        assistant_block(tool_use_block("Edit", {"file_path": "C:\\G\\Repo\\src\\a.py"}, "t1"),
                        "m1"),
        tool_result("t1", "Updated.", toolUseResult={"filePath": "C:\\G\\Repo\\src\\a.py",
                                                     "structuredPatch": patch}),
        assistant_block(text_block("Fixed."), "m2")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    shown = []

    def modal(dialog):
        shown.append((dialog.scope.GetSelection(),
                      [dialog.list.GetString(i) for i in range(dialog.list.GetCount())],
                      dialog.text.GetValue()))
        dialog.Destroy()
    monkeypatch.setattr(frame, "_modal", modal)
    frame.on_changes()
    assert shown[0][0] == 0  # your latest message
    assert shown[0][1] == ["a.py, 2 lines added, 1 removed, in src"]
    assert "Removed: old\nAdded: new\nAdded: more" in shown[0][2]
    # Changes there when the session loaded are old news; a turn's new ones
    # are said after its reply, without cutting it off.
    frame._changes_due = True
    frame._refresh_chat()
    pump(lambda: not frame._changes_due)
    assert not any("Changed" in t for t in env["spoken"] + env["feedback"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(user_text("And the docs")) + "\n")
        handle.write(json.dumps(assistant_block(tool_use_block(
            "Write", {"file_path": "C:\\G\\Repo\\README.md", "content": "Hi\n"}, "t2"),
            "m3")) + "\n")
        handle.write(json.dumps(tool_result("t2", "Created.", toolUseResult={
            "type": "create", "filePath": "C:\\G\\Repo\\README.md", "content": "Hi\n",
            "structuredPatch": []})) + "\n")
        handle.write(json.dumps(assistant_block(text_block("All done."), "m4")) + "\n")
    frame._changes_due = True
    frame._refresh_chat()
    assert pump(lambda: any("Changed 1 file" in t for t in env["feedback"]))
    assert env["spoken"][-1] == "Quiet one replied. All done."
    assert env["feedback"][-1] == ("Quiet one: Changed 1 file: README.md, created, 1 line. "
                                   "Ctrl+Shift+D shows the changes.")
    assert not frame._changes_due
    # Another turn with no changes (a background task's, say): nothing again.
    count = len(env["feedback"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_block(text_block("Task finished."), "m5")) + "\n")
    frame._changes_due = True
    frame._refresh_chat()
    pump(lambda: not frame._changes_due)
    assert not any("Changed" in t for t in env["feedback"][count:])
    # A new message with no changes yet: the view starts on the whole session.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(user_text("Thanks")) + "\n")
    frame._refresh_chat()
    assert pump(lambda: frame._chat_turns == 3)
    frame.on_changes()
    assert shown[-1][0] == 1 and len(shown[-1][1]) == 2
    # A summary due for a session that's no longer loaded isn't said.
    frame._say_changes(frame._open_generation - 1)
    assert frame._changes_due is False


def test_changed_files_with_nothing_changed(frame, env):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Hi"),
                                                 assistant_block(text_block("Hello."), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    frame.on_changes()
    assert env["feedback"][-1] == "Quiet one: Claude hasn't changed any files in this session."


def test_notifications_when_another_window_is_active(frame, env, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    monkeypatch.setattr(frame, "_app_is_active", lambda: False)
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._denials["own-1"] = []
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="All done."))
    assert env["notified"][-1] == ("Hub probe finished", "All done.", "own:own-1")
    # Only when it needs you: a finished turn says nothing, a failure does.
    frame.speech.notifications = speech.NOTIFY_NEEDS_YOU
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Again."))
    assert len(env["notified"]) == 1
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Stopped.", is_error=True))
    assert env["notified"][-1][0] == "Hub probe: the turn failed"
    # Off, or while The Chat Place is the active window: nothing.
    count = len(env["notified"])
    frame.speech.notifications = speech.NOTIFY_OFF
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Stopped.", is_error=True))
    frame.speech.notifications = speech.NOTIFY_ALL
    monkeypatch.setattr(frame, "_app_is_active", lambda: True)
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Here."))
    assert len(env["notified"]) == count


def test_choosing_a_notification_loads_its_session(frame, env):
    select(frame, "Quiet one")
    frame.on_open_session()
    hub_key = next(s.key for s in frame._snapshot.sessions if s.title == "Hub probe")
    frame._go_to_session(hub_key)
    assert frame._open is not None and frame._open.key == hub_key
    # The app becomes active, not just its window, so VO+M finds its menus.
    assert env["activated"] == [True]
    frame._go_to_session("gone")  # forgotten since: just comes forward
    assert frame._open.key == hub_key


def test_notification_setting_is_saved_and_checked(tmp_path):
    path = tmp_path / "speech.json"
    path.write_text(json.dumps({"notifications": "needs_you"}), encoding="utf-8")
    assert speech.SpeechSettings.load(path).notifications == speech.NOTIFY_NEEDS_YOU
    path.write_text(json.dumps({"notifications": "loud"}), encoding="utf-8")
    assert speech.SpeechSettings.load(path).notifications == speech.NOTIFY_ALL


def test_notifications_for_permissions_desktop_sessions_and_stops(frame, env, monkeypatch):
    import dataclasses
    monkeypatch.setattr(frame, "_app_is_active", lambda: False)
    _waiting_turn(frame, _request("r1"), _request("r2"))
    # One notification for the first request; the second waits behind it.
    assert env["notified"] == [("Hub probe needs you",
                                "Claude wants to run git push. Ctrl+Shift+A answers.",
                                "own:own-1")]
    # Stopping it yourself isn't news.
    runner = frame._runners["own-1"]
    runner.cancelled = True
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Stopped.", is_error=True))
    assert len(env["notified"]) == 1
    # A desktop session that starts needing you, and the loaded one finishing.
    quiet = next(s for s in frame._snapshot.sessions if s.title == "Quiet one")
    needs = dataclasses.replace(quiet, state=NEEDS_YOU, detail="Pick a name")
    frame._apply_snapshot(frame._snapshot, [needs], {}, False)
    assert env["notified"][-1] == ("Quiet one needs you", "Pick a name", quiet.key)
    idle = dataclasses.replace(quiet, state="idle")
    frame._apply_snapshot(frame._snapshot, [idle], {}, False)
    assert len(env["notified"]) == 2  # not loaded: its finishing isn't notified
    select(frame, "Quiet one")
    frame.on_open_session()
    frame._apply_snapshot(frame._snapshot, [idle], {quiet.key: "Done it."}, False)
    assert env["notified"][-1] == ("Quiet one finished", "Done it.", quiet.key)


def test_choosing_a_notification_with_a_dialog_open_leaves_the_session(frame, env, monkeypatch):
    select(frame, "Quiet one")
    frame.on_open_session()
    loaded = frame._open.key
    hub_key = next(s.key for s in frame._snapshot.sessions if s.title == "Hub probe")

    class Modal:
        raised = False

        def IsModal(self):
            return True

        def IsEnabled(self):
            return True

        def Raise(self):
            Modal.raised = True
    monkeypatch.setattr(wx, "GetTopLevelWindows", lambda: [frame, Modal()])
    monkeypatch.setattr(wx, "Dialog", Modal)
    frame._go_to_session(hub_key)
    assert Modal.raised and frame._open.key == loaded
    frame._go_to_session(None)  # the icon itself: just come forward
    assert frame._open.key == loaded


def test_settings_keep_the_notification_choice(frame, env):
    from thechatplace.ui.dialogs import SettingsDialog
    frame.speech.notifications = speech.NOTIFY_NEEDS_YOU
    dialog = SettingsDialog(frame, frame.speech, speech.default_options())
    try:
        assert dialog.notify_choice.GetSelection() == 1
        dialog.notify_choice.SetSelection(2)
        assert dialog.get_settings().notifications == speech.NOTIFY_OFF
    finally:
        dialog.Destroy()


def test_a_different_model_is_said_once(frame, env):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.store.update("own-1", model="opus")
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    started = TurnEvent("started", session_id="own-1", data={"model": "claude-sonnet-5-5"})
    frame._on_turn_event({"id": "own-1"}, "Hub probe", started)
    assert env["spoken"][-1] == ("Hub probe is using Sonnet 5.5 instead of Opus, the model "
                                 "this session chose.")
    count = len(env["spoken"])
    frame._on_turn_event({"id": "own-1"}, "Hub probe", started)
    assert len(env["spoken"]) == count  # once
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "started", session_id="own-1", data={"model": "claude-opus-5-5"}))
    assert len(env["spoken"]) == count  # the chosen one: nothing to say
    # No model chosen: nothing to compare (a Fable default never gets here,
    # its turn is stopped first, #58).
    frame.store.update("own-1", model="")
    count = len(env["spoken"])
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "started", session_id="own-1", data={"model": "claude-opus-5-5"}))
    assert len(env["spoken"]) == count
    # A turn for a session no longer in the store: nothing.
    count = len(env["spoken"])
    frame._check_model("gone", "Gone", "claude-fable-5-1")
    assert len(env["spoken"]) == count


def test_enter_presses_a_status_bar_button(frame, monkeypatch):
    pressed = []
    monkeypatch.setattr(frame, "check_for_updates", lambda manual=True: pressed.append(manual))
    frame.status_parts.set("update", "Update available: 0.2.0")
    button = frame.status_parts.get("update")
    event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(wx.WXK_RETURN)
    button._on_key(event)
    assert pressed == [True]


def test_a_part_you_are_reading_waits_for_you(frame, monkeypatch):
    frame._status("First.")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.status_text))
    frame._status("Second.")
    assert frame.status_text.GetLabel() == "First."  # not read out under you
    assert frame.GetStatusBar().GetStatusText(0) == "Second."  # the read key has it
    frame.status_text._on_focus_change(wx.FocusEvent(wx.wxEVT_KILL_FOCUS))
    assert frame.status_text.GetLabel() == "Second."  # caught up on leaving


def test_empty_parts_and_the_update_button_going_away(frame, env):
    from thechatplace.updater import AVAILABLE, CheckResult, CURRENT
    bar = frame.GetStatusBar()
    assert bar.GetStatusText(1) == "No session loaded"
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, "0.1.0", "0.2.0"))
    run_check(frame, manual=False)
    assert frame.status_parts.get("update").IsShown()
    frame.updates = FakeUpdates(CheckResult(CURRENT, "0.2.0", "0.2.0"))
    run_check(frame, manual=False)
    assert not frame.status_parts.get("update").IsShown()
    assert bar.GetFieldsCount() == len(frame.status_parts.shown())


def test_queued_messages_are_in_the_list_to_edit_or_remove(frame, env, fake_runner, monkeypatch):
    _start(frame, fake_runner)
    for text in ("second", "third"):
        frame.reply_text.SetValue(text)
        frame.on_send()
    rows = list(frame.chat_list.GetStrings())
    assert rows[-2:] == ["Queued: second", "Queued: third"]
    # Delete on a queued row removes it.
    frame.chat_list.SetSelection(len(rows) - 2)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_DELETE)
    frame._on_char_hook(event)
    assert frame._queued == {"own-1": ["third"]}
    assert list(frame.chat_list.GetStrings())[-1] == "Queued: third"
    assert env["feedback"][-1] == "Queued message removed. 1 still queued."
    # The context menu offers Edit and Remove for a queued row.
    frame.chat_list.SetSelection(frame.chat_list.GetCount() - 1)
    menu = frame._message_menu()
    labels = [item.GetItemLabelText() for item in menu.GetMenuItems()]
    assert "Edit Queued Message" in labels and "Remove Queued Message" in labels
    menu.Destroy()
    # Edit takes it back into the reply box, ahead of anything typed.
    frame.reply_text.SetValue("draft")
    frame.edit_queued()
    assert frame._queued == {}
    assert frame.reply_text.GetValue() == "third\n\ndraft"
    assert not any(r.startswith("Queued:") for r in frame.chat_list.GetStrings())
    # Delete on an ordinary message does nothing to it.
    frame.chat_list.SetSelection(0)
    count = frame.chat_list.GetCount()
    frame._on_char_hook(event)
    assert frame.chat_list.GetCount() == count


def test_queued_rows_go_when_the_turn_fails_and_find_still_works(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("second")
    frame.on_send()
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("failed", text="Broke.", is_error=True))
    assert not any(r.startswith("Queued:") for r in frame.chat_list.GetStrings())
    assert frame._chat_keys == [m.key for m in frame._visible_messages()]


def test_queued_before_the_transcript_exists_is_not_acted_on(frame, env, fake_runner):
    _start(frame, fake_runner)  # no transcript yet: "Claude is starting"
    frame.reply_text.SetValue("second")
    frame.on_send()
    frame._chat_loaded = False  # the next tick: still no transcript
    frame._show_missing_transcript(frame._open)
    frame.chat_list.SetSelection(0)
    assert frame._selected_message() is None
    frame.remove_queued()
    assert frame._queued == {"own-1": ["second"]}


def test_new_messages_go_in_above_the_queue_without_rewriting_the_list(
        frame, env, fake_runner, monkeypatch):
    path = add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("first")])
    _start(frame, fake_runner)
    frame._refresh_chat()
    assert pump(lambda: frame.chat_list.GetCount() >= 1 and frame._chat_loaded)
    frame.reply_text.SetValue("second")
    frame.on_send()
    frame.chat_list.SetSelection(0)
    sets = []
    monkeypatch.setattr(frame.chat_list, "Set", lambda lines: sets.append(lines))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_block(text_block("Working on it."), "m1")) + "\n")
    frame._refresh_chat()
    assert pump(lambda: "Claude: Working on it." in frame.chat_list.GetStrings())
    assert sets == []
    rows = list(frame.chat_list.GetStrings())
    assert rows[-2:] == ["Claude: Working on it.", "Queued: second"]
    assert frame.chat_list.GetSelection() == 0


def test_export_leaves_out_queued_messages(frame, env, fake_runner):
    _start(frame, fake_runner)
    frame.reply_text.SetValue("second")
    frame.on_send()
    frame._chat_loaded = True
    messages, _path = frame._export_source(frame._open)
    assert all(m.kind != "queued" for m in messages)


def test_the_desktop_apps_groups_show_in_the_list_and_views(frame, env, monkeypatch):
    quiet = next(s for s in frame._snapshot.sessions if s.title == "Quiet one")
    prefs = {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {"a/o": {
        "groups": [{"id": "cg-1", "name": "IDT"}],
        "assignments": {f"code:{quiet.key}": "cg-1"}}}}}}
    (env["desktop"] / "a" / "o").mkdir(parents=True, exist_ok=True)
    config = env["desktop"].parent / "claude_desktop_config.json"
    config.write_text(json.dumps(prefs), encoding="utf-8")
    frame.refresh_sessions(force=True)
    assert pump(lambda: "Group: IDT" in [i.GetItemLabelText()
                                          for i in frame.show_menu.GetMenuItems()])
    row = next(r for r in frame.session_list.GetStrings() if r.startswith("Quiet one"))
    assert "group IDT" in row
    frame.on_view("group:IDT")
    settle(frame)
    assert [r.split(",")[0] for r in frame.session_list.GetStrings()] == ["Quiet one"]
    # Removing is for the desktop app; adding a Chat Place session joins it.
    select(frame, "Quiet one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    frame.on_remove_from_group()
    assert env["feedback"][-1] == ("Quiet one is in the desktop app's group IDT; change that "
                                   "in the desktop app.")
    frame.on_view("all")
    settle(frame)
    select(frame, "Hub probe")
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices: choices.index("IDT"))
    frame.on_add_to_group()
    assert env["feedback"][-1] == "Added Hub probe to IDT."
    frame.on_view("group:IDT")
    settle(frame)
    assert sorted(r.split(",")[0] for r in frame.session_list.GetStrings()) == [
        "Hub probe", "Quiet one"]

    # Already in it (the desktop app's group): said, nothing made.
    select(frame, "Quiet one")
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices: next(
        i for i, c in enumerate(choices) if c.startswith("IDT")))
    frame.on_add_to_group()
    assert env["feedback"][-1] == "Quiet one is already in IDT."


def test_a_bad_read_of_the_desktop_apps_groups_keeps_your_view(frame, env):
    quiet = next(s for s in frame._snapshot.sessions if s.title == "Quiet one")
    (env["desktop"] / "a" / "o").mkdir(parents=True, exist_ok=True)
    config = env["desktop"].parent / "claude_desktop_config.json"
    config.write_text(json.dumps({"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {
        "a/o": {"groups": [{"id": "cg-1", "name": "IDT"}],
                "assignments": {f"code:{quiet.key}": "cg-1"}}}}}}), encoding="utf-8")
    frame.refresh_sessions(force=True)
    assert pump(lambda: "IDT" in frame._snapshot.desktop_groups.names)
    frame.on_view("group:IDT")
    config.write_text('{"preferences": {"epit', encoding="utf-8")  # mid-rewrite
    frame.refresh_sessions(force=True)
    settle(frame)
    assert frame.speech.session_view == "group:IDT"
    assert "IDT" in frame._snapshot.desktop_groups.names  # the last good read kept
    assert [r.split(",")[0] for r in frame.session_list.GetStrings()] == ["Quiet one"]
    # Really gone (deleted in the desktop app): the view stays, marked not found.
    config.write_text(json.dumps({"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {
        "a/o": {"groups": [], "assignments": {}}}}}}), encoding="utf-8")
    frame.refresh_sessions(force=True)
    settle(frame)
    labels = [i.GetItemLabelText() for i in frame.show_menu.GetMenuItems()]
    assert "Group: IDT (not found)" in labels
    checked = [i.GetItemLabelText() for i in frame.show_menu.GetMenuItems()
               if i.IsCheckable() and i.IsChecked()]
    assert checked == ["Group: IDT (not found)"]


def test_sign_in_is_checked_and_offered(frame, env, monkeypatch):
    from thechatplace import signin
    from thechatplace.ui import main_frame
    shown = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, title, style, parent=None: (
        shown.append((text, style)), wx.YES)[1])
    frame._on_sign_in_result(signin.SignIn(True, True, "claude.ai", plan="max", email="k@example.com"),
                             manual=True)
    assert shown[-1][0] == "Claude Code is signed in to your Claude Max plan as k@example.com."
    started = []
    monkeypatch.setattr(signin, "login_command", lambda: ["claude", "auth", "login"])

    class Process:
        def wait(self):
            return 0
    monkeypatch.setattr(main_frame.platform_paths, "run_in_terminal",
                        lambda command, *a, **k: started.append((command, k)) or Process())
    frame._on_sign_in_result(signin.SignIn(True, signed_in=False), manual=True)
    assert shown[-1][0].startswith("Claude Code isn't signed in.")
    assert started[0][0] == ["claude", "auth", "login"]
    assert "ANTHROPIC_API_KEY" not in started[0][1]["env"]
    # At start-up only a problem is said, and without cutting off the list.
    frame._sign_in_asked = False
    count = len(env["feedback"])
    frame._on_sign_in_result(signin.SignIn(True, True, "claude.ai", plan="max"), manual=False)
    frame._on_sign_in_result(signin.SignIn(False, problem="no answer"), manual=False)
    assert len(env["feedback"]) == count
    frame._on_sign_in_result(signin.SignIn(True, signed_in=False), manual=False)
    assert env["feedback"][-1].endswith(
        "To sign in, choose Claude Code Sign-in on the Help menu.")
    # Asked from the menu meanwhile: the start-up answer isn't said as well.
    frame._sign_in_asked = True
    count = len(env["feedback"])
    frame._on_sign_in_result(signin.SignIn(True, signed_in=False), manual=False)
    assert len(env["feedback"]) == count
    # The sign-in can't start: said.
    monkeypatch.setattr(signin, "login_command", lambda: None)
    frame._on_sign_in_result(signin.SignIn(True, signed_in=False), manual=True)
    assert shown[-1][0].startswith("Couldn't start the sign-in")


def test_install_is_offered_when_claude_is_missing(frame, env, monkeypatch):
    """No claude The Chat Place can use: said at start-up, and Claude Code
    Sign-in offers to run its installer, then checks again."""
    from thechatplace import signin
    from thechatplace.ui import main_frame
    missing = signin.SignIn(False, missing=True,
                            problem="Claude Code isn't installed. The desktop app's isn't for "
                                    "other programs.",
                            install_help="To install it, run its native installer. In …")
    frame._sign_in_asked = False
    frame._on_sign_in_result(missing, manual=False)
    # Spoken short: the first sentence, without the commands.
    assert env["feedback"][-1] == ("Claude Code isn't installed. To install it, choose Claude "
                                   "Code Sign-in on the Help menu.")
    shown, answers = [], [wx.NO, wx.YES]
    monkeypatch.setattr(wx, "MessageBox", lambda text, title, style, parent=None: (
        shown.append(text), answers.pop(0))[1])
    installs, checks = [], []

    class Process:
        def wait(self):
            return 0
    monkeypatch.setattr(main_frame.platform_paths, "start_claude_install",
                        lambda env: installs.append(env) or Process())
    monkeypatch.setattr(main_frame.wx, "CallAfter", lambda fn, *a, **k: checks.append(k))
    frame._on_sign_in_result(missing, manual=True)  # No: nothing runs
    assert shown[-1].startswith("Claude Code isn't installed.")
    assert "To install it, run its native installer." in shown[-1]
    assert "Install Claude Code now?" in shown[-1] and installs == []
    frame._on_sign_in_result(missing, manual=True)  # Yes
    assert len(installs) == 1 and "ANTHROPIC_API_KEY" not in installs[0]
    assert env["feedback"][-1].startswith("Installing Claude Code, in a new window.")
    for _ in range(100):
        if checks:
            break
        time.sleep(0.02)
    assert checks == [{"manual": True}]  # checked again once the window closed


def test_change_model_for_the_next_turns(frame, env, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    offered = []

    def choose(title, prompt, choices, selection=None):
        offered.append((choices, selection, prompt))
        return [c.split(" (")[0] for c in choices].index("Sonnet")
    monkeypatch.setattr(frame, "_choose", choose)
    frame.on_change_model()
    assert frame.store.get("own-1").model == "sonnet"
    assert env["feedback"][-1] == "Hub probe now uses Sonnet, from its next turn."
    choices, selection, prompt = offered[0]
    assert "Sonnet" in choices and choices[selection].endswith("(now)")
    assert prompt.startswith("Model for Hub probe, now ")
    # A desktop app session's model isn't The Chat Place's to change.
    select(frame, "Quiet one")
    frame.on_open_session()
    frame.on_change_model()
    assert env["feedback"][-1] == ("Quiet one is a desktop app session: its model is set "
                                   "in the desktop app.")


def test_a_turn_stopped_before_claude_answered_gives_the_message_back(frame, env, fake_runner):
    _start(frame, fake_runner, text="Fix the build")
    runner = fake_runner.instances[-1]
    runner.session_started = True  # init came, then the stop
    runner.stopped_before_answer = True
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "failed", text="Claude Code would have used Fable…", is_error=True))
    assert frame.reply_text.GetValue() == "Fix the build"


class _Questions:
    def questions(self):
        return [{"header": "One", "question": "First?", "options": [{"label": "Red"},
                                                                    {"label": "Blue"}]},
                {"header": "Two", "question": "Second?", "multiSelect": True,
                 "options": [{"label": "M"}]},
                {"header": "Three", "question": "Third?", "options": []},
                {"header": "Four", "question": "Fourth?", "options": [{"label": "S"}]}]


def _boxes_and_their_controls(parent, sizer):
    """Each group box (found through its sizer) with the controls that come
    after it among the window's children, up to the next box."""
    boxes = []

    def walk(s):
        for item in s.GetChildren():
            inner = item.GetSizer()
            if isinstance(inner, wx.StaticBoxSizer):
                boxes.append(inner.GetStaticBox())
            if inner is not None:
                walk(inner)
    walk(sizer)
    children = list(parent.GetChildren())
    handles = [c.GetHandle() for c in children]
    groups = []
    for box in boxes:
        start = handles.index(box.GetHandle())
        later = [handles.index(b.GetHandle()) for b in boxes if handles.index(b.GetHandle()) > start]
        end = min(later) if later else len(handles)
        controls = [c for c in list(parent.GetChildren())[start + 1:end]
                    if isinstance(c, (wx.RadioButton, wx.CheckBox, wx.TextCtrl))]
        groups.append((box, controls))
    return groups


def test_each_question_is_a_group_box_its_options_follow(frame):
    from thechatplace.ui.dialogs import QuestionDialog
    dialog = QuestionDialog(frame, "Probe", _Questions())
    try:
        dialog.Layout()
        panel = dialog.GetChildren()[0]
        groups = _boxes_and_their_controls(panel, panel.GetSizer())
        assert [box.GetLabel() for box, _ in groups] == [
            "One: First?", "Two: Second?", "Three: Third?", "Four: Fourth?"]
        for box, controls in groups:
            # Siblings after the box, inside it: how screen readers find the label.
            assert controls and all(c.GetParent() is panel for c in controls)
            assert all(box.GetRect().Contains(c.GetRect()) for c in controls)
            radios = [c for c in controls if isinstance(c, wx.RadioButton)]
            if radios:  # one radio group per question
                assert radios[0].HasFlag(wx.RB_GROUP)
                assert not any(r.HasFlag(wx.RB_GROUP) for r in radios[1:])
        # Answers in two questions both stay chosen.
        first, fourth = groups[0][1][0], groups[3][1][0]
        first.SetValue(True)
        fourth.SetValue(True)
        assert first.GetValue() and fourth.GetValue()
    finally:
        dialog.Destroy()


def test_arrow_keys_move_through_a_question_s_options(frame):
    # #121: on a Mac the arrows did nothing in the answer dialog's radio group.
    from thechatplace.ui.dialogs import RADIO_ARROW_STEPS, QuestionDialog
    assert RADIO_ARROW_STEPS == {wx.WXK_UP: -1, wx.WXK_LEFT: -1, wx.WXK_DOWN: 1, wx.WXK_RIGHT: 1}
    dialog = QuestionDialog(frame, "Probe", _Questions())
    try:
        fired = []
        dialog.Bind(wx.EVT_RADIOBUTTON, lambda e: fired.append(e.GetEventObject()._hub_label))
        (_k1, first, _t1), (_k2, multi, _t2), _third, (_k4, fourth, _t4) = dialog._controls
        red, blue, other = first
        fourth[0].SetValue(True)
        assert dialog.move_in_radio_group(red, 1)
        assert blue.GetValue() and not red.GetValue()
        assert dialog.move_in_radio_group(blue, 1) and other.GetValue()
        assert dialog.move_in_radio_group(other, 1) and red.GetValue()  # wraps, as Windows does
        assert dialog.move_in_radio_group(red, -1) and other.GetValue()
        assert fired == ["Blue", "Other", "Red", "Other"]
        assert fourth[0].GetValue()  # another question's answer is untouched
        assert not dialog.move_in_radio_group(multi[0], 1)  # check boxes aren't a radio group
    finally:
        dialog.Destroy()


def test_only_plain_arrows_on_a_radio_are_taken(frame, monkeypatch):
    # #121's key hook keeps every other key, and arrows anywhere else, working.
    from thechatplace.ui.dialogs import QuestionDialog
    dialog = QuestionDialog(frame, "Probe", _Questions())
    try:
        (_k1, first, other_text), (_k2, multi, _t2), _third, _fourth = dialog._controls
        red, blue, _other = first
        red.SetValue(True)

        def press(focus, key, control=False):
            monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus))
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.SetKeyCode(key)
            event.SetControlDown(control)
            dialog._on_char_hook(event)
            return event.GetSkipped()

        assert not press(red, wx.WXK_DOWN) and blue.GetValue()  # taken: moves the choice
        for focus, key, control in [(blue, wx.WXK_RETURN, False), (blue, wx.WXK_ESCAPE, False),
                                    (blue, wx.WXK_TAB, False), (blue, wx.WXK_DOWN, True),
                                    (other_text, wx.WXK_DOWN, False), (multi[0], wx.WXK_DOWN, False),
                                    (None, wx.WXK_DOWN, False)]:
            assert press(focus, key, control), (focus, key, control)
        assert blue.GetValue()  # none of those moved it
    finally:
        dialog.Destroy()


def test_settings_reading_messages_options_follow_their_group_box(frame):
    from thechatplace.ui.dialogs import SettingsDialog
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        dialog.Layout()
        groups = dict((box.GetLabel(), controls)
                      for box, controls in _boxes_and_their_controls(dialog, dialog.GetSizer()))
        reading = groups["Reading messages"]
        assert dialog.formatted in reading and dialog.whole_in_list in reading
        assert all(c.GetParent() is dialog for c in (dialog.formatted, dialog.whole_in_list))
    finally:
        dialog.Destroy()


def test_remote_control_follows_the_setting_and_each_session(frame, env, fake_runner, monkeypatch):
    _start(frame, fake_runner)
    assert fake_runner.instances[-1].remote_control is None  # off by default
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Done."))
    frame.speech.remote_control = True
    frame.reply_text.SetValue("again")
    frame.on_send()
    assert fake_runner.instances[-1].remote_control == {"name": "Hub probe", "reattach": ""}
    # Its answer is remembered, and joined again next turn.
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("remote_control", data={
        "bridge_session_id": "cse_1", "session_url": "https://claude.ai/code/session_1"}))
    assert frame.store.get("own-1").bridge_session_id == "cse_1"
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Done."))
    # This session says off, whatever the setting.
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        choices.index("Off for this session"))
    frame.on_remote_control()
    assert frame.store.get("own-1").remote_control == "off"
    assert env["feedback"][-1] == "Remote Control off for Hub probe, from its next turn."
    frame.reply_text.SetValue("third")
    frame.on_send()
    assert fake_runner.instances[-1].remote_control is None
    # Turned on for the session: the same Remote Control session is joined.
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Done."))
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        choices.index("On for this session"))
    frame.speech.remote_control = False
    frame.on_remote_control()
    frame.reply_text.SetValue("fourth")
    frame.on_send()
    assert fake_runner.instances[-1].remote_control == {"name": "Hub probe",
                                                        "reattach": "cse_1"}


def test_remote_control_refused_is_said_on_the_status_bar(frame, env):
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "remote_control", data={"error": "Remote Control is turned off by your organization"}))
    assert env["spoken"][-1].startswith("Hub probe: couldn't turn on Remote Control")


def test_a_question_answered_elsewhere_is_no_longer_waiting(frame, env):
    _waiting_turn(frame, _request("r1"))
    assert frame._pending.get("own-1")
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("permission_cancelled", text="r1"))
    assert "own-1" not in frame._pending
    assert frame.status_text.GetLabel() == "Hub probe: answered elsewhere."


def test_remote_control_address_copied_and_a_refusal_said_once(frame, env, monkeypatch):
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("remote_control", data={
        "bridge_session_id": "cse_9", "session_url": "https://claude.ai/code/session_9"}))
    assert env["spoken"][-1].startswith("Hub probe is on Remote Control.")
    select(frame, "Hub probe")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None: next(
        i for i, c in enumerate(choices) if c.startswith("Copy its claude.ai address")))
    frame.on_remote_control()
    assert env["copied"][-1] == "https://claude.ai/code/session_9"
    refused = TurnEvent("remote_control", data={"error": "Remote Control is turned off"})
    count = len(env["spoken"])
    frame._on_turn_event({"id": "own-1"}, "Hub probe", refused)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", refused)
    assert len(env["spoken"]) == count + 1


def test_send_now_takes_a_queued_message_into_the_running_turn(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    for text in ("second", "third"):
        frame.reply_text.SetValue(text)
        frame.on_send()
    frame.chat_list.SetSelection(frame.chat_list.GetCount() - 1)  # "Queued: third"
    menu = frame._message_menu()
    labels = [item.GetItemLabelText() for item in menu.GetMenuItems()]
    assert "Send Now" in labels
    menu.Destroy()
    frame.send_queued_now()
    assert runner.sent_now == ["third"]
    assert frame._queued == {"own-1": ["second"]}
    assert env["feedback"][-1] == ("Sent now. Hub probe is stopping what it was doing to "
                                   "answer it.")
    # The turn just ended: it stays queued, to go as usual.
    runner.ended = True
    frame.chat_list.SetSelection(frame.chat_list.GetCount() - 1)
    frame.send_queued_now()
    assert frame._queued == {"own-1": ["second"]}


# VoiceOver names (macOS). wx's SetName never reaches VoiceOver, so without
# ui/mac_a11y.py every edit box, list and choice was read with no label.

def _unlabelled(window):
    """Edit boxes, lists and choices under `window` that VoiceOver can't name."""
    from thechatplace.ui import mac_a11y
    missing = []
    for child in window.GetChildren():
        if isinstance(child, (wx.TextCtrl, wx.ListBox, wx.Choice, wx.ComboBox)):
            if not mac_a11y.get_label(child):
                missing.append(f"{type(child).__name__} {child.GetName()!r}")
        missing.extend(_unlabelled(child))
    return missing


@voiceover
def test_voiceover_reads_the_main_window_labels(frame):
    from thechatplace.ui import mac_a11y
    assert mac_a11y.get_label(frame.reply_text) == "Your message"
    assert mac_a11y.get_label(frame.desktop_note) == "About replying"
    assert mac_a11y.get_label(frame.session_list)
    assert _unlabelled(frame) == []


@voiceover
def test_voiceover_follows_a_changing_name(frame):
    from thechatplace.ui import mac_a11y
    from thechatplace.ui.a11y import set_accessible_name
    set_accessible_name(frame.chat_list, "Messages, Hub probe, idle")
    assert mac_a11y.get_label(frame.chat_list) == "Messages, Hub probe, idle"


def _voiceover_actions(listbox):
    import ctypes
    from thechatplace.ui import mac_a11y
    names = mac_a11y._send(mac_a11y._target_view(listbox), "accessibilityActionNames")
    count = mac_a11y._send(ctypes.c_void_p(names), "count", restype=ctypes.c_ulong)
    return [mac_a11y._to_str(mac_a11y._send(ctypes.c_void_p(names), "objectAtIndex:", i,
                                            argtypes=(ctypes.c_ulong,)))
            for i in range(count)]


def _voiceover_show_menu(listbox):
    """What VO+Shift+M does: perform AXShowMenu on the list's table."""
    import ctypes
    from thechatplace.ui import mac_a11y
    mac_a11y._send(mac_a11y._target_view(listbox), "accessibilityPerformAction:",
                   ctypes.c_void_p(mac_a11y._nsstring("AXShowMenu")),
                   restype=None, argtypes=(ctypes.c_void_p,))


@voiceover
def test_voiceover_opens_the_lists_context_menus(frame, env, monkeypatch):
    # A wx list box's table offered VoiceOver no AXShowMenu, so VO+Shift+M
    # did nothing in either list.
    assert "AXShowMenu" in _voiceover_actions(frame.session_list)
    assert "AXShowMenu" in _voiceover_actions(frame.chat_list)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    select(frame, "Quiet one")
    shown = []
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser",
                        lambda menu, position: shown.append(_labels(menu)) or wx.ID_NONE)
    _voiceover_show_menu(frame.session_list)
    assert shown == []  # not inside VoiceOver's request, which it would hold up
    assert pump(lambda: shown)
    assert "H&ide Session" in shown[0]
    popped = []
    monkeypatch.setattr(frame.chat_list, "PopupMenu",
                        lambda menu, position: popped.append(menu) or True)
    _voiceover_show_menu(frame.chat_list)
    assert pump(lambda: popped)


@voiceover
def test_voiceover_menu_by_the_newer_call_and_only_for_live_lists(frame, monkeypatch):
    import ctypes
    from thechatplace.ui import mac_a11y
    popped = []
    monkeypatch.setattr(frame.chat_list, "PopupMenu",
                        lambda menu, position: popped.append(menu) or True)
    view = mac_a11y._target_view(frame.chat_list)
    assert mac_a11y._send(view, "accessibilityPerformShowMenu", restype=ctypes.c_bool)
    assert pump(lambda: popped)
    # Another list box is left as it was, and a destroyed one is forgotten,
    # so a new view at its address doesn't open the old list's menu.
    other = wx.ListBox(frame, choices=["one"])
    assert "AXShowMenu" not in _voiceover_actions(other)
    from thechatplace.ui.a11y import set_voiceover_menu
    opened = []
    set_voiceover_menu(other, lambda: opened.append(True))
    address = mac_a11y._target_view(other).value
    assert address in mac_a11y._menu_handlers
    # VO+Shift+M, then the list goes before the menu's turn comes: nothing
    # opens and nothing raises.
    mac_a11y._menu_handlers[address]()
    other.Destroy()
    assert address not in mac_a11y._menu_handlers
    wx.GetApp().ProcessPendingEvents()
    assert opened == []


@voiceover
def test_voiceover_reads_dialog_labels(frame):
    from thechatplace.ui.dialogs import (BugReportDialog, ChangesDialog, CommandPickerDialog,
                                         NewSessionDialog, SettingsDialog, UsageDialog)
    dialogs = [
        NewSessionDialog(frame, "/tmp"),
        SettingsDialog(frame, speech.SpeechSettings(), speech.default_options()),
        BugReportDialog(frame, ["The Chat Place: 0.1.0"]),
        CommandPickerDialog(frame, FAKE_COMMANDS),
        ChangesDialog(frame, "Hub probe", [], [], str),
        UsageDialog(frame, ["Context: not known."], lambda text, one: None),
    ]
    try:
        for dialog in dialogs:
            assert _unlabelled(dialog) == [], dialog.GetTitle()
    finally:
        for dialog in dialogs:
            dialog.Destroy()


def test_mac_naming_does_nothing_off_macos(monkeypatch):
    from thechatplace.ui import mac_a11y
    monkeypatch.setattr(mac_a11y, "IS_MACOS", False)
    assert mac_a11y.set_label(object(), "Your message") is False
    assert mac_a11y.get_label(object()) is None


def test_a_control_without_a_native_view_is_not_an_error():
    from thechatplace.ui import mac_a11y
    assert mac_a11y.set_label(object(), "Your message") is False
    assert mac_a11y.get_label(object()) is None


def test_activating_does_nothing_off_macos(monkeypatch):
    from thechatplace.ui import mac_a11y
    monkeypatch.setattr(mac_a11y, "IS_MACOS", False)
    assert mac_a11y.activate_app() is False


def test_the_reply_box_grows_with_the_window(frame):
    """It was held at its minimum (about four lines) however big the window
    was, with all the extra height going to the messages list."""
    def heights(size):
        frame.SetSize(size)
        frame.Layout()
        frame.own_reply.Layout()
        return frame.GetSize().height, frame.reply_text.GetSize().height

    select(frame, "Hub probe")
    frame.on_open_session()
    # The window may not get as tall as asked (CI's Windows screen is small),
    # so measure what it actually grew by.
    (small_frame, small), (large_frame, large) = heights((1000, 720)), heights((1440, 900))
    assert small >= 120
    assert large_frame > small_frame
    assert large - small >= (large_frame - small_frame) / 5  # about a third goes to the reply
    assert frame.chat_list.GetSize().height > frame.own_reply.GetSize().height


def test_long_session_titles_leave_the_reply_box_its_width(env):
    """A list box's minimum width was its longest row. With real session
    titles the session list took nearly the whole window, the reply box was
    one point wide, and VoiceOver read it one letter per line."""
    from thechatplace.ui.main_frame import MainFrame
    long_title = "A session whose title goes on and on " * 12
    add_desktop(env, "local_a", "cli-a", long_title)
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("own-1", "Hub probe " + long_title, "/tmp", last_activity_ms=now_ms()))
    window = MainFrame(store=store, check_updates_at_start=False)
    try:
        assert pump(lambda: window.session_list.GetCount() == 2)
        select(window, "Hub probe")
        window.on_open_session()
        window.SetSize((1000, 720))
        window.Layout()
        window.own_reply.Layout()
        assert window.reply_text.GetSize().width > 300
        assert window.session_list.GetSize().width < window.GetSize().width / 2
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        window.Destroy()


def test_a_hidden_session_is_not_said_counted_or_notified(frame, env, monkeypatch):
    import dataclasses
    settle(frame)
    blocked = next(s for s in frame._snapshot.sessions if s.title == "Blocked one")
    select(frame, "Blocked one")
    frame.on_hide()
    settle(frame)
    assert not frame.status_parts.get("needs_you").IsShown()
    monkeypatch.setattr(frame, "_app_is_active", lambda: False)
    spoken = len(env["spoken"])
    again = dataclasses.replace(blocked, state="needs you", detail="Pick a name")
    frame._apply_snapshot(frame._snapshot, [again], {}, False)
    assert env["notified"] == [] and len(env["spoken"]) == spoken


# -- rename (#93) ------------------------------------------------------------------------


def test_rename_an_own_session_renames_it_in_its_store(frame, env):
    select(frame, "Hub probe")
    frame.session_list.SetFocus()
    frame.rename_session(frame._selected_session(), "  Probe  two ")
    assert frame.store.get("own-1").title == "Probe two"
    assert not (env["tmp"] / "appdata" / "titles.json").exists()  # never needed for our own
    assert any(r.startswith("Probe two") for r in frame.session_list.GetStrings())
    assert env["feedback"][-1] == "Renamed Hub probe to Probe two."
    frame.refresh_sessions(force=True)
    settle(frame)
    assert any(r.startswith("Probe two") for r in frame.session_list.GetStrings())


def test_rename_an_own_session_to_nothing_is_refused(frame, env):
    select(frame, "Hub probe")
    frame.rename_session(frame._selected_session(), "   ")
    assert frame.store.get("own-1").title == "Hub probe"
    assert "needs a name" in env["feedback"][-1]


def test_rename_a_desktop_session_only_here(frame, env):
    metadata = env["desktop"] / "local_a" / "org" / "local_a.json"
    before = metadata.read_bytes()
    select(frame, "Quiet one")
    frame.rename_session(frame._selected_session(), "Calm one")
    assert metadata.read_bytes() == before  # the desktop app's file is never written
    saved = json.loads((env["tmp"] / "appdata" / "titles.json").read_text(encoding="utf-8"))
    assert saved["titles"] == {"local_a": "Calm one"}
    assert env["feedback"][-1] == "Renamed Quiet one to Calm one."
    # It survives the next read of the desktop app's files.
    frame.refresh_sessions(force=True)
    settle(frame)
    rows = list(frame.session_list.GetStrings())
    assert any(r.startswith("Calm one") for r in rows)
    assert not any(r.startswith("Quiet one") for r in rows)
    # Empty goes back to the desktop app's name.
    select(frame, "Calm one")
    frame.rename_session(frame._selected_session(), "")
    assert env["feedback"][-1] == "Calm one goes back to the desktop app's name."
    settle(frame)
    frame.refresh_sessions(force=True)
    settle(frame)
    assert any(r.startswith("Quiet one") for r in frame.session_list.GetStrings())


def test_renaming_a_terminal_session_names_claude_codes_name(frame, env, monkeypatch):
    add_terminal(env, monkeypatch, "term-1", "C:\\T\\Docs", user_text("Clean the drive"))
    frame.refresh_sessions(force=True)
    settle(frame)
    select(frame, "Clean the drive")
    frame.rename_session(frame._selected_session(), "")
    assert env["feedback"][-1] == "Clean the drive already has Claude Code's name."
    frame.rename_session(frame._selected_session(), "Tidy")
    settle(frame)
    select(frame, "Tidy")
    frame.rename_session(frame._selected_session(), "")
    assert env["feedback"][-1] == "Tidy goes back to Claude Code's name."


def test_clearing_a_name_never_given_says_so(frame, env):
    select(frame, "Quiet one")
    frame.rename_session(frame._selected_session(), "")
    assert env["feedback"][-1] == "Quiet one already has the desktop app's name."


def test_a_read_begun_before_a_rename_doesnt_bring_the_old_name_back(frame, env):
    # The list's own refresh copies the store when it starts; one that lands
    # after the rename must still show the new name.
    stale = hub.collect(frame.store.all(), set())
    select(frame, "Hub probe")
    frame.rename_session(frame._selected_session(), "Probe two")
    frame._apply_snapshot(stale, [], {}, False)
    rows = list(frame.session_list.GetStrings())
    assert any(r.startswith("Probe two") for r in rows)
    assert not any(r.startswith("Hub probe") for r in rows)


def test_rename_the_loaded_session_updates_its_heading(frame, env):
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._open is not None)
    frame.rename_session(frame._open, "Calm one")
    assert frame._open.title == "Calm one"
    assert frame.session_heading.GetLabel().startswith("Calm one,")


def test_rename_says_so_when_titles_cant_be_saved(frame, env, monkeypatch):
    def fail(key, title):
        raise OSError("disk full")
    monkeypatch.setattr(frame.titles, "set", fail)
    select(frame, "Quiet one")
    frame.rename_session(frame._selected_session(), "Calm one")
    assert env["boxes"] and "disk full" in env["boxes"][-1]
    assert any(r.startswith("Quiet one") for r in frame.session_list.GetStrings())


def test_rename_asks_for_the_name_with_the_old_one_filled_in(frame, env, monkeypatch):
    asked = []

    class FakeEntry:
        def __init__(self, parent, prompt, title, value):
            asked.append((prompt, title, value))

        def SetMaxLength(self, n):
            self.max = n

        def ShowModal(self):
            return wx.ID_OK

        def GetValue(self):
            return "Calm one"

        def Destroy(self):
            pass
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame.wx, "TextEntryDialog", FakeEntry)
    select(frame, "Quiet one")
    frame.session_list.SetFocus()
    frame.on_rename()
    prompt, title, value = asked[0]
    assert title == "Rename Session" and value == "Quiet one"
    assert "only in The Chat Place" in prompt
    assert frame.titles.get("local_a") == "Calm one"


def test_f2_is_rename_on_the_file_menu(frame):
    labels = [item.GetItemLabel() for item in frame.GetMenuBar().GetMenu(0).GetMenuItems()]
    assert "Rename Session...\tF2" in labels


# -- the session menu and Shift+F10 (#89) ---------------------------------------------------


def _menu_key(code, shift=False, kind=None):
    event = wx.KeyEvent(kind or wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(code)
    event.SetShiftDown(shift)
    return event


@pytest.mark.parametrize("code,shift", [(wx.WXK_F10, True), (wx.WXK_WINDOWS_MENU, False)])
def test_menu_keys_open_the_lists_menus_themselves(frame, monkeypatch, code, shift):
    calls = []
    monkeypatch.setattr(frame, "_on_session_menu", lambda event=None: calls.append("sessions"))
    monkeypatch.setattr(frame, "_on_message_menu", lambda event=None: calls.append("messages"))
    for focus in (frame.session_list, frame.chat_list):
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda f=focus: f))
        event = _menu_key(code, shift)
        event.Skip(False)
        frame._on_char_hook(event)
        # Not handed on to Windows, which took Shift+F10 as F10 and started the menu bar.
        assert not event.GetSkipped()
    assert calls == ["sessions", "messages"]


def test_plain_f10_and_ctrl_shift_f10_are_left_alone(frame, monkeypatch):
    calls = []
    monkeypatch.setattr(frame, "_on_session_menu", lambda event=None: calls.append("menu"))
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    plain = _menu_key(wx.WXK_F10)  # F10 alone is the menu bar, as ever
    frame._on_char_hook(plain)
    assert plain.GetSkipped()
    event = _menu_key(wx.WXK_F10, shift=True)
    event.SetControlDown(True)
    frame._on_char_hook(event)
    assert calls == []


def _labels(menu):
    return [i.GetItemLabel().split("\t")[0] for i in menu.GetMenuItems() if not i.IsSeparator()]


def _enabled(menu, label):
    return next(i for i in menu.GetMenuItems() if i.GetItemLabel().startswith(label)).IsEnabled()


def test_session_menu_for_an_own_and_a_desktop_session(frame, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    select(frame, "Hub probe")
    menu, actions = frame._session_menu()
    own = _labels(menu)
    assert "Re&name Session..." in own and "Delete Session &Permanently..." in own
    assert "Con&tinue Here..." not in own
    assert not _enabled(menu, "Open in &Claude")  # never opened in the desktop app
    assert not _enabled(menu, "Remove from Gro&up")  # in no group
    menu.Destroy()
    select(frame, "Quiet one")
    menu, actions = frame._session_menu()
    desktop = _labels(menu)
    assert "Con&tinue Here..." in desktop and "Delete Session &Permanently..." not in desktop
    assert "H&ide Session" in desktop and _enabled(menu, "Open in &Claude")
    menu.Destroy()


def test_session_menu_with_nothing_selected_says_so(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    frame.session_list.SetSelection(wx.NOT_FOUND)
    shown = []
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser",
                        lambda menu, position: shown.append(menu) or wx.ID_NONE)
    frame._on_session_menu()
    assert shown == [] and env["feedback"][-1] == "No session selected."


def test_session_menu_runs_the_choice_on_the_highlighted_session(frame, env, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    select(frame, "Quiet one")

    def pick(menu, position):
        return next(i.GetId() for i in menu.GetMenuItems()
                    if i.GetItemLabel().startswith("H&ide Session"))
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser", pick)
    frame._on_session_menu()
    assert "local_a" in frame.hidden
    # Dismissing the menu does nothing.
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser",
                        lambda menu, position: wx.ID_NONE)
    frame._on_session_menu()
    assert frame.hidden.keys() == ["local_a"]


class _RightClick:
    def GetPosition(self):
        return wx.Point(40, 40)


def test_a_right_click_means_the_row_under_the_mouse(frame, env, monkeypatch):
    # A list box neither selects the row right-clicked nor takes focus: with
    # Quiet one highlighted and focus in the messages, right-clicking Blocked
    # one must hide Blocked one.
    select(frame, "Quiet one")
    target = next(i for i in range(frame.session_list.GetCount())
                  if frame.session_list.GetString(i).startswith("Blocked one"))
    monkeypatch.setattr(frame.session_list, "HitTest", lambda point: target)
    focus = {"on": frame.chat_list}
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus["on"]))
    monkeypatch.setattr(frame.session_list, "SetFocus",
                        lambda: focus.update(on=frame.session_list))

    def pick(menu, position):
        return next(i.GetId() for i in menu.GetMenuItems()
                    if i.GetItemLabel().startswith("H&ide Session"))
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser", pick)
    frame._on_session_menu(_RightClick())
    assert "local_b" in frame.hidden and "local_a" not in frame.hidden


def test_a_right_click_in_the_messages_means_the_message_under_the_mouse(frame, monkeypatch):
    # The messages list didn't select the row right-clicked either, so the
    # menu acted on the highlighted message, not the one clicked.
    frame.chat_list.Set(["first", "second", "third"])
    frame.chat_list.SetSelection(0)
    monkeypatch.setattr(frame.chat_list, "HitTest", lambda point: 2)
    focus = {"on": frame.reply_text}
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focus.update(on=frame.chat_list))
    seen = []
    monkeypatch.setattr(frame.chat_list, "PopupMenu",
                        lambda menu, position: seen.append(frame.chat_list.GetSelection()))
    frame._on_message_menu(_RightClick())
    assert seen == [2] and focus["on"] is frame.chat_list
    # Below the last message, nothing opens and the highlight stays.
    monkeypatch.setattr(frame.chat_list, "HitTest", lambda point: wx.NOT_FOUND)
    frame._on_message_menu(_RightClick())
    assert seen == [2] and frame.chat_list.GetSelection() == 2


def test_the_messages_menu_from_the_keyboard_keeps_the_highlight(frame, monkeypatch):
    # The Applications key and VO+Shift+M (no mouse position) use the
    # highlighted message and move focus to the list, even from the reply box.
    frame.chat_list.Set(["first", "second"])
    frame.chat_list.SetSelection(1)
    focus = {"on": frame.reply_text}
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focus.update(on=frame.chat_list))
    seen = []
    monkeypatch.setattr(frame.chat_list, "PopupMenu",
                        lambda menu, position: seen.append(frame.chat_list.GetSelection()))
    frame._on_message_menu()
    assert seen == [1] and focus["on"] is frame.chat_list


def test_no_menu_opens_once_the_window_is_closing(frame, monkeypatch):
    # A menu scheduled by VO+Shift+M can come due after Cmd+Q, before the
    # window is destroyed.
    shown = []
    monkeypatch.setattr(frame.chat_list, "PopupMenu",
                        lambda menu, position: shown.append(menu))
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser",
                        lambda menu, position: shown.append(menu) or wx.ID_NONE)
    select(frame, "Quiet one")
    frame._closing = True
    frame._on_message_menu()
    frame._on_session_menu()
    assert shown == []


def test_a_right_click_below_the_rows_does_nothing(frame, env, monkeypatch):
    monkeypatch.setattr(frame.session_list, "HitTest", lambda point: wx.NOT_FOUND)
    shown = []
    monkeypatch.setattr(frame.session_list, "GetPopupMenuSelectionFromUser",
                        lambda menu, position: shown.append(menu) or wx.ID_NONE)
    frame._on_session_menu(_RightClick())
    assert shown == []


# -- what Claude knows about you (#92) ------------------------------------------------------


def _about_you_home(env):
    home = env["tmp"] / "claude"
    (home / "agents").mkdir(parents=True)
    (home / "CLAUDE.md").write_text("# Be brief\n", encoding="utf-8")
    (home / "agents" / "reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews code\n---\nBody text\n",
        encoding="utf-8")
    return home


def _dialog(frame, env, home, edit=None, show=None):
    from thechatplace import about_you
    from thechatplace.ui.dialogs import AboutYouDialog

    def collect():
        return about_you.collect([], home=home, user_home="")
    return AboutYouDialog(frame, collect(), lambda done: done(collect()),
                          edit or platform_paths.edit_file,
                          show or platform_paths.show_in_folder, env["copied"].append)


def test_about_you_dialog_lists_kinds_items_and_reads_a_file(frame, env):
    home = _about_you_home(env)
    dialog = _dialog(frame, env, home)
    try:
        # Each kind says what it is: focus never reaches a label.
        assert dialog.kinds.GetString(0).startswith("Instructions, 1 item: What you've told")
        assert dialog.items.GetString(0).startswith("Your instructions for every session")
        assert dialog.text.GetValue() == "# Be brief\n"
        assert dialog.location.GetValue() == str(home / "CLAUDE.md")
        dialog.kinds.SetSelection(3)  # Subagents
        dialog._fill_items()
        assert dialog.items.GetString(0) == "code-reviewer — Reviews code"
        assert "Body text" in dialog.text.GetValue()
        dialog.edit_selected()
        dialog.show_selected()
        dialog.copy_selected()
        path = home / "agents" / "reviewer.md"
        assert env["opened"][-2:] == [("edit", path), ("show", path)]
        assert env["copied"][-1] == str(path)
        # Reload keeps your place and shows the change.
        path.write_text("---\nname: code-reviewer\ndescription: Changed\n---\n",
                        encoding="utf-8")
        dialog.reload()
        assert dialog.kinds.GetSelection() == 3 and dialog.reload_btn.IsEnabled()
        assert dialog.items.GetString(0) == "code-reviewer — Changed"
        # An empty kind says so and has nothing to edit.
        dialog.kinds.SetSelection(1)
        dialog._fill_items()
        assert dialog.items.GetString(0) == "Nothing here yet."
        assert not dialog.edit_btn.IsEnabled() and dialog.selected_item() is None
    finally:
        dialog.Destroy()


def test_about_you_dialog_says_why_a_file_cant_be_opened(frame, env):
    home = _about_you_home(env)

    def fail(path):
        raise OSError("no editor")
    dialog = _dialog(frame, env, home, edit=fail, show=fail)
    try:
        dialog.edit_selected()
        dialog.show_selected()
        assert len(env["boxes"]) == 2 and "no editor" in env["boxes"][0]
        (home / "CLAUDE.md").unlink()
        dialog._show()
        assert dialog.text.GetValue().startswith("Couldn't read this file")
    finally:
        dialog.Destroy()


def test_about_you_reads_in_the_background_with_the_sessions_folders(frame, env, monkeypatch):
    import threading
    from thechatplace import about_you
    from thechatplace.ui import main_frame
    _about_you_home(env)
    seen = {}
    real = about_you.collect

    def collect(cwds):
        seen["cwds"] = list(cwds)
        seen["thread"] = threading.current_thread()
        return real(cwds)
    monkeypatch.setattr(main_frame.about_you, "collect", collect)
    shown = []

    def modal(self, dialog):
        shown.append(dialog.kinds.GetString(0))
        dialog.reload()  # Reload reads again, also in the background
        assert pump(lambda: dialog.reload_btn.IsEnabled())
        dialog.Destroy()
    monkeypatch.setattr(main_frame.MainFrame, "_modal", modal)
    frame.on_about_you()
    assert env["feedback"][-1] == "Reading what Claude knows about you…"
    assert pump(lambda: shown)
    assert seen["thread"] is not threading.main_thread()
    assert seen["cwds"] == ["C:\\G\\Repo", "C:\\G\\Scratch"]
    assert shown[0].startswith("Instructions, 1 item")
    assert env["feedback"][-1].startswith("Reloaded. Found: Instructions 1")
    labels = [i.GetItemLabel() for i in frame.GetMenuBar().GetMenu(1).GetMenuItems()]
    assert "What Claude &Knows About You...\tCtrl+Shift+K" in labels


def test_about_you_says_so_when_reading_fails(frame, env, monkeypatch):
    from thechatplace.ui import main_frame

    def broken(cwds):
        raise RuntimeError("bad disk")
    monkeypatch.setattr(main_frame.about_you, "collect", broken)
    frame.on_about_you()
    assert pump(lambda: "bad disk" in env["feedback"][-1])


# -- Remote Control, easier to find (#96) ----------------------------------------------------


def test_a_remote_control_session_says_so_in_its_row():
    from thechatplace.sessions import SessionInfo
    info = SessionInfo(source="desktop", key="local_a", title="Work", cwd="/r", cli_session_id="c",
                       last_activity_ms=1, remote=True)
    assert ", Remote Control" in info.list_line(now_ms=1)
    info.remote = False
    assert "Remote Control" not in info.list_line(now_ms=1)


def test_turning_remote_control_on_shows_in_the_row_heading_and_view(frame, env, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    assert pump(lambda: frame._open is not None)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        choices.index("On for this session"))
    frame.on_remote_control()
    assert frame.session_heading.GetLabel().endswith(", Remote Control on.")
    assert any(r.startswith("Hub probe") and "Remote Control" in r
               for r in frame.session_list.GetStrings())
    # Still so after the next read, and in View, Show Sessions, Remote Control Sessions.
    frame.refresh_sessions(force=True)
    settle(frame)
    frame.on_view("remote")
    settle(frame)
    assert [r.split(",")[0] for r in frame.session_list.GetStrings()] == ["Hub probe"]
    # Turning it off takes it out of the row.
    frame.on_view("all")
    settle(frame)
    select(frame, "Hub probe")
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        choices.index("Off for this session"))
    frame.on_remote_control()
    assert not any(r.startswith("Hub probe") and "Remote Control" in r
                   for r in frame.session_list.GetStrings())
    assert not frame.session_heading.GetLabel().endswith(", Remote Control on.")


def _hub_row(frame):
    return next(r for r in frame.session_list.GetStrings() if r.startswith("Hub probe"))


def test_with_only_the_default_on_a_row_says_remote_control_once_connected(frame, env):
    # Every row saying so would be noise when it's true of them all.
    frame.speech.remote_control = True
    frame.refresh_sessions(force=True)
    settle(frame)
    assert "Remote Control" not in _hub_row(frame)
    frame.store.update("own-1", bridge_session_id="cse_1")
    frame.refresh_sessions(force=True)
    settle(frame)
    assert "Remote Control" in _hub_row(frame)
    frame.speech.remote_control = False  # the default off: not used, so not said
    frame.refresh_sessions(force=True)
    settle(frame)
    assert "Remote Control" not in _hub_row(frame)


def test_a_linked_desktop_session_is_said_to_be_on_remote_control(frame, env, monkeypatch):
    add_desktop(env, "local_r", "cli-r", "Linked one", bridgeSessionIds=["cse_7"])
    add_transcript(env, "C:\\G\\Repo", "cli-r", [user_text("hi")])
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert "Remote Control" in next(r for r in frame.session_list.GetStrings()
                                    if r.startswith("Linked one"))
    select(frame, "Linked one")
    frame.on_open_session()
    assert pump(lambda: frame._open is not None)
    assert frame.session_heading.GetLabel().endswith(
        ", on Remote Control in the desktop app.")
    # Its dialog says nothing more is needed, rather than offering to turn it on.
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    asked = []
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        asked.append((prompt, choices)))
    frame.on_remote_control()
    prompt, choices = asked[-1]
    assert "already on Remote Control" in prompt and "nothing more is needed" in prompt
    assert not any("to turn on Remote Control" in c for c in choices)


def test_desktop_remote_control_offers_only_what_can_work(frame, env, monkeypatch):
    # No transcript: Continue Here can't work, so only Open is offered; with
    # neither, it says why instead of showing an empty choice.
    add_desktop(env, "local_n", "cli-n", "No transcript")
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "No transcript")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    asked = []
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        asked.append(choices))
    frame.on_remote_control()
    assert asked[-1] == ["Open it in the Claude desktop app, to turn on Remote Control there"]
    monkeypatch.setattr(type(frame._selected_session()), "can_open_in_claude",
                        property(lambda self: False))
    count = len(asked)
    frame.on_remote_control()
    assert len(asked) == count and "can't use Remote Control" in env["feedback"][-1]


def test_remote_control_is_on_the_session_menu(frame, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    for title in ("Hub probe", "Quiet one"):
        select(frame, title)
        menu, actions = frame._session_menu()
        assert "&Remote Control..." in _labels(menu)
        menu.Destroy()


def test_remote_control_for_a_desktop_session_offers_both_ways(frame, env, fake_runner,
                                                                monkeypatch):
    folder = env["tmp"] / "repo"
    folder.mkdir()
    add_desktop(env, "local_c", "cli-c", "Desktop work", cwd=str(folder))
    add_transcript(env, str(folder), "cli-c", [user_text("Earlier question")])
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Desktop work")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    asked = []

    def choose(title, prompt, choices, selection=None):
        asked.append((prompt, list(choices)))
        return next(i for i, c in enumerate(choices) if c.startswith("Open it in"))
    monkeypatch.setattr(frame, "_choose", choose)
    frame.on_remote_control()
    prompt, choices = asked[-1]
    assert "doesn't change" in prompt
    assert choices == ["Open it in the Claude desktop app, to turn on Remote Control there",
                       "Continue it here as a copy, with Remote Control on"]
    assert env["opened"][-1].startswith("claude://")
    # Continuing it here starts the copy on Remote Control from its first turn.
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        choices.index("Continue it here as a copy, with Remote Control on"))
    from thechatplace.ui import dialogs

    class Fills(dialogs.NewSessionDialog):
        def ShowModal(self):
            self.message.SetValue("Carry on")
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.NewSessionDialog", Fills)
    frame.on_remote_control()
    runner = fake_runner.instances[-1]
    new_id = runner.command[runner.command.index("--session-id") + 1]
    assert frame.store.get(new_id).remote_control == "on"
    assert runner.remote_control == {"name": "Desktop work (continued)", "reattach": ""}
    assert "starts on Remote Control" in env["feedback"][-1]
    # The desktop app's session is untouched.
    assert (env["desktop"] / "local_c" / "org" / "local_c.json").is_file()


def test_remote_control_for_a_desktop_session_cancelled_does_nothing(frame, env, monkeypatch):
    select(frame, "Quiet one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None: None)
    frame.on_remote_control()
    assert env["opened"] == [] and len(frame.store.all()) == 1


def test_ctrl_shift_b_opens_code_blocks_from_the_messages(frame, monkeypatch):
    calls = []
    monkeypatch.setattr(frame, "on_code_blocks", lambda: calls.append("blocks"))
    # AltGr arrives as Ctrl+Alt on Windows, so Ctrl+Alt+Shift+B must do nothing.
    for focus, shift, alt in ((frame.chat_list, True, False), (frame.chat_list, False, False),
                              (frame.session_list, True, False), (frame.chat_list, True, True)):
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda f=focus: f))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("B"))
        event.SetControlDown(True)
        event.SetShiftDown(shift)
        event.SetAltDown(alt)
        frame._on_char_hook(event)
    assert calls == ["blocks"]  # only Ctrl+Shift+B, only in the messages
    menu = frame._message_menu()
    labels = [i.GetItemLabel() for i in menu.GetMenuItems()]
    menu.Destroy()
    assert "Code &Blocks...\tCtrl+Shift+B" in labels


# -- coming back with a dialog open (#103) ---------------------------------------------------


class _FakeModal(wx.Dialog):
    raised = 0
    enabled = True

    def IsModal(self):
        return True

    def IsEnabled(self):
        return self.enabled

    def Raise(self):
        self.raised += 1


def _activate(frame, monkeypatch, active=True):
    """Through the frame's own event handling, so a lost Bind fails the test.
    The fix runs from a timer, which a hidden test window never fires, so the
    timer runs at once here; the real one is checked by hand (#103)."""
    from thechatplace.ui import main_frame
    timers = []
    monkeypatch.setattr(main_frame.wx, "CallLater",
                        lambda ms, fn, *a: timers.append(ms) or fn(*a))
    event = wx.ActivateEvent(wx.wxEVT_ACTIVATE, active)
    event.SetEventObject(frame)
    frame.GetEventHandler().ProcessEvent(event)
    return timers


@pytest.fixture
def behind_a_dialog(frame, monkeypatch):
    """The main window active but disabled, as it is behind a modal dialog."""
    monkeypatch.setattr(wx, "GetActiveWindow", lambda: frame)
    monkeypatch.setattr(frame, "IsEnabled", lambda: False)
    popups = []
    monkeypatch.setattr(platform_paths, "bring_last_popup_forward",
                        lambda hwnd: popups.append(hwnd) and False)
    return popups


def test_coming_back_to_the_main_window_brings_its_dialog_forward(frame, behind_a_dialog,
                                                                 monkeypatch):
    dialog = _FakeModal(frame)
    try:
        # A timer, which a native message box's loop still runs; CallAfter wouldn't.
        assert _activate(frame, monkeypatch) == [1]
        assert behind_a_dialog == [frame.GetHandle()]  # Windows asked first
        assert dialog.raised == 1  # and wx's own dialog when Windows had none
        _activate(frame, monkeypatch, active=False)  # leaving: nothing to do
        assert dialog.raised == 1
    finally:
        dialog.Destroy()


def test_with_a_dialog_over_a_dialog_the_inner_one_comes_forward(frame, behind_a_dialog,
                                                                 monkeypatch):
    outer, inner = _FakeModal(frame), _FakeModal(frame)
    outer.enabled = False  # disabled under the inner one
    try:
        _activate(frame, monkeypatch)
        assert (outer.raised, inner.raised) == (0, 1)
    finally:
        inner.Destroy()
        outer.Destroy()


def test_when_windows_finds_the_popup_wx_isnt_asked(frame, behind_a_dialog, monkeypatch):
    monkeypatch.setattr(platform_paths, "bring_last_popup_forward", lambda hwnd: True)
    dialog = _FakeModal(frame)
    try:
        _activate(frame, monkeypatch)
        assert dialog.raised == 0  # a native message box, say, came forward
    finally:
        dialog.Destroy()


def test_nothing_happens_unless_the_disabled_main_window_is_active(frame, monkeypatch):
    calls = []
    monkeypatch.setattr(platform_paths, "bring_last_popup_forward",
                        lambda hwnd: calls.append(hwnd) or True)
    dialog = _FakeModal(frame)
    try:
        # The dialog came forward properly (or you switched away again).
        monkeypatch.setattr(wx, "GetActiveWindow", lambda: dialog)
        monkeypatch.setattr(frame, "IsEnabled", lambda: False)
        _activate(frame, monkeypatch)
        # No dialog at all: the main window is enabled; wx restores its focus.
        monkeypatch.setattr(wx, "GetActiveWindow", lambda: frame)
        monkeypatch.setattr(frame, "IsEnabled", lambda: True)
        _activate(frame, monkeypatch)
        assert calls == [] and dialog.raised == 0
    finally:
        dialog.Destroy()


def test_activation_is_still_skipped_for_wx(frame):
    event = wx.ActivateEvent(wx.wxEVT_ACTIVATE, True)
    frame._on_activate(event)
    assert event.GetSkipped()  # wx still restores the last focus itself


def test_ungrouped_shows_sessions_in_no_group_yours_or_the_desktop_apps(frame, env):
    quiet = next(s for s in frame._snapshot.sessions if s.title == "Quiet one")
    prefs = {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {"a/o": {
        "groups": [{"id": "cg-1", "name": "IDT"}],
        "assignments": {f"code:{quiet.key}": "cg-1"}}}}}}
    (env["desktop"] / "a" / "o").mkdir(parents=True, exist_ok=True)
    (env["desktop"].parent / "claude_desktop_config.json").write_text(json.dumps(prefs),
                                                                      encoding="utf-8")
    frame.groups.create("Work")
    frame.groups.add("Work", "own:own-1")
    frame.refresh_sessions(force=True)
    settle(frame)
    frame.on_view("ungrouped")
    settle(frame)
    assert [r.split(",")[0] for r in frame.session_list.GetStrings()] == ["Blocked one"]
    assert frame.sessions_label.GetLabelText() == "Session list, ungrouped sessions, 1 of 3:"
    labels = [i.GetItemLabelText() for i in frame.show_menu.GetMenuItems()]
    assert "Ungrouped" in labels
    # Taken out of its group, it's ungrouped again.
    frame.groups.remove("Work", "own:own-1")
    frame.refresh_sessions(force=True)
    settle(frame)
    assert sorted(r.split(",")[0] for r in frame.session_list.GetStrings()) == [
        "Blocked one", "Hub probe"]
    assert frame.view_items["ungrouped"].IsChecked()


def test_filing_a_session_takes_it_out_of_ungrouped(frame, env, monkeypatch):
    frame.on_view("ungrouped")
    settle(frame)
    assert len(_titles(frame)) == 3  # nothing is in a group yet
    index = select(frame, "Quiet one")
    next_title = frame.session_list.GetString(index + 1).split(",")[0] \
        if index + 1 < frame.session_list.GetCount() else None
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices: 0)  # New group...
    monkeypatch.setattr(frame, "_ask_group_name", lambda title, value="": "Work")
    frame.on_add_to_group()
    assert env["feedback"][-1] == "Added Quiet one to Work."
    assert "Quiet one" not in _titles(frame)
    if next_title:
        assert frame.session_list.GetStringSelection().startswith(next_title)


# -- other machines (#123) -----------------------------------------------------------


def _load_hub_probe_with_listing(frame, listing=None):
    from thechatplace.transcript import TOOL_RESULT, ChatMessage
    frame.store.update("own-1", remote_control="on")
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._chat_messages = [ChatMessage("user", "hello", "", "u1")]
    if listing is not None:
        frame._chat_messages.append(ChatMessage(TOOL_RESULT, "ListAgents returned: " + listing,
                                                "", "r1"))


class _FakeTextDialog:
    value = ""

    def __init__(self, parent, prompt, title, value="", style=0):
        _FakeTextDialog.prompt = prompt

    def ShowModal(self):
        return wx.ID_OK

    def GetValue(self):
        return _FakeTextDialog.value

    def Destroy(self):
        pass


def test_other_machines_needs_an_own_session_with_remote_control(frame, env, fake_runner):
    frame.on_other_machines()  # nothing loaded
    assert "Load one first" in env["boxes"][-1]
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.store.update("own-1", remote_control="off")
    frame.on_other_machines()
    assert "Remote Control is off for Hub probe" in env["boxes"][-1]
    assert fake_runner.instances == []


def test_other_machines_refresh_asks_claude_for_the_list(frame, env, fake_runner, monkeypatch):
    _load_hub_probe_with_listing(frame)
    seen = {}
    monkeypatch.setattr(frame, "_choose",
                        lambda title, prompt, choices: seen.update(p=prompt, c=choices) or 0)
    frame.on_other_machines()
    assert "hasn't listed" in seen["p"]
    assert seen["c"] == ["Refresh the list (asks Claude)"]
    runner = fake_runner.instances[-1]
    assert "Call ListAgents" in runner.prompt and runner.remote_control
    assert "Ctrl+Shift+M again" in env["feedback"][-1]


def test_other_machines_sends_your_words_to_the_chosen_session(frame, env, fake_runner,
                                                             monkeypatch):
    from thechatplace.ui import main_frame
    _load_hub_probe_with_listing(
        frame, "Peer sessions (2):\n  Surface Hub [66e038]  ·  Remote Control  ·  offline\n"
               "  Mac work [a03676]  ·  Remote Control  ·  idle")
    seen = {}
    monkeypatch.setattr(frame, "_choose",
                        lambda title, prompt, choices: seen.update(c=choices) or 1)
    _FakeTextDialog.value = "What's your hostname?"
    monkeypatch.setattr(main_frame.wx, "TextEntryDialog", _FakeTextDialog)
    frame.on_other_machines()
    assert seen["c"] == ["Surface Hub, offline", "Mac work, idle",
                         "Refresh the list (asks Claude)"]
    assert _FakeTextDialog.prompt == "Message to Mac work (Enter sends):"
    runner = fake_runner.instances[-1]
    assert '"Mac work"' in runner.prompt
    assert runner.prompt.endswith("<<<MESSAGE\nWhat's your hostname?\nMESSAGE>>>")
    # The instructions aren't read back, or given back if the turn fails.
    assert runner.typed == ""
    assert not any("SendMessage" in said for said in env["feedback"])
    assert env["feedback"][-1].endswith("A reply shows here as from Mac work.")


def test_other_machines_not_during_a_turn(frame, env, fake_runner, monkeypatch):
    _load_hub_probe_with_listing(frame)
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices: 0)
    frame.on_other_machines()
    # Not queued, where Claude's instructions would show as your message.
    assert not frame._queued.get("own-1")
    assert env["feedback"][-1] == ("Hub probe is working. Use Other Machines again when "
                                   "the turn ends.")


def test_other_machines_empty_message_sends_nothing(frame, env, fake_runner, monkeypatch):
    from thechatplace.ui import main_frame
    _load_hub_probe_with_listing(
        frame, "Peer sessions (1):\n  Mac work [a03676]  ·  Remote Control  ·  idle")
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices: 0)
    _FakeTextDialog.value = "   "
    monkeypatch.setattr(main_frame.wx, "TextEntryDialog", _FakeTextDialog)
    frame.on_other_machines()
    assert fake_runner.instances == []
    assert env["feedback"][-1] == "Nothing sent: the message was empty."


# -- status views and the session list's columns (#134) --------------------------------------


def _session(frame, title):
    return next(s for s in frame._snapshot.sessions if s.title == title)


def test_show_sessions_by_status_keeps_you_on_the_same_session(frame, env):
    from thechatplace.sessions import WORKING
    _session(frame, "Quiet one").state = WORKING
    _session(frame, "Hub probe").unread = True
    for view in ("working", "new_reply", "idle"):
        assert view in frame.view_items
    select(frame, "Hub probe")
    frame.on_view("idle")
    assert _titles(frame) == ["Hub probe"]
    assert frame.session_list.GetStringSelection().startswith("Hub probe")
    assert env["feedback"][-1] == "Showing idle sessions: 1 session."
    assert frame.session_list.GetName() == "Session list, idle sessions, 1 of 3"
    frame.on_view("new_reply")
    assert _titles(frame) == ["Hub probe"]
    assert env["feedback"][-1] == "Showing sessions with a new reply: 1 session."
    frame.on_view("working")
    assert _titles(frame) == ["Quiet one"]
    assert env["feedback"][-1] == "Showing working sessions: 1 session."
    assert frame.view_items["working"].IsChecked()
    assert speech.SpeechSettings.load().session_view == "working"
    frame.on_view("all")
    assert frame.session_list.GetCount() == 3


def test_session_list_columns_reorder_the_rows_at_once_and_are_remembered(frame, env,
                                                                         monkeypatch):
    from thechatplace.ui import main_frame
    select(frame, "Blocked one")
    seen = {}

    class Chooses:
        def __init__(self, parent, fields, sample, say):
            seen["fields"], seen["sample"] = list(fields), sample

        def ShowModal(self):
            return wx.ID_OK

        def fields(self):
            return ["status", "title"]

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "SessionColumnsDialog", Chooses)
    frame.on_session_columns()
    assert seen["sample"].title == "Blocked one"  # the preview shows the selected session
    assert seen["fields"][:3] == ["title", "folder", "status"]
    assert frame.session_list.GetStringSelection() == "needs you: Pick a name, Blocked one"
    assert sorted(frame.session_list.GetStrings()) == [
        "idle, Hub probe", "idle, Quiet one", "needs you: Pick a name, Blocked one"]
    assert env["feedback"][-1] == ("Session list columns saved. Each session reads: "
                                   "Status, Title.")
    assert speech.SpeechSettings.load().session_fields == ["status", "title"]
    # A refresh keeps them.
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert frame.session_list.GetStringSelection() == "needs you: Pick a name, Blocked one"
    frame.on_session_columns()  # the same again
    assert env["feedback"][-1] == "Session list columns unchanged."


def _choose_columns(monkeypatch, fields):
    from thechatplace.ui import main_frame

    class Chooses:
        def __init__(self, *a):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def fields(self):
            return list(fields)

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "SessionColumnsDialog", Chooses)


def test_moving_only_last_activity_still_rewrites_the_row_you_are_on(frame, env,
                                                                     monkeypatch):
    # A row differing only in its age is normally left alone (a clock tick);
    # a change of columns is not one.
    from thechatplace.sessions import DEFAULT_FIELDS
    select(frame, "Blocked one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    _choose_columns(monkeypatch, ["activity"] + [f for f in DEFAULT_FIELDS if f != "activity"])
    frame.on_session_columns()
    assert frame.session_list.GetStringSelection().startswith("active ")
    _choose_columns(monkeypatch, [f for f in DEFAULT_FIELDS if f != "activity"])
    frame.on_session_columns()
    assert "active " not in frame.session_list.GetStringSelection()


def test_columns_that_cannot_be_saved_say_so_and_still_apply(frame, env, monkeypatch):
    select(frame, "Blocked one")
    _choose_columns(monkeypatch, ["status", "title"])

    def fails(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(frame.speech, "save", fails)
    frame.on_session_columns()
    assert frame.session_list.GetStringSelection() == "needs you: Pick a name, Blocked one"
    assert "couldn't be saved" in env["feedback"][-1] and "disk full" in env["feedback"][-1]
    assert not any(said.startswith("Session list columns saved") for said in env["feedback"])


def test_session_list_columns_cancel_changes_nothing(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    before = list(frame.session_list.GetStrings())

    class Cancels:
        def __init__(self, *a):
            pass

        def ShowModal(self):
            return wx.ID_CANCEL

        def fields(self):
            raise AssertionError("not asked after Cancel")

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "SessionColumnsDialog", Cancels)
    frame.on_session_columns()
    assert list(frame.session_list.GetStrings()) == before
    assert frame.speech.session_fields[0] == "title"


def _columns_dialog(frame, fields=None, sample=None):
    from thechatplace.sessions import DEFAULT_FIELDS
    from thechatplace.ui.dialogs import SessionColumnsDialog
    said = []
    dialog = SessionColumnsDialog(frame, fields or DEFAULT_FIELDS,
                                  sample or _session(frame, "Blocked one"), said.append)
    focused = []
    for name in ("shown", "available"):
        control = getattr(dialog, name)
        control.SetFocus = lambda n=name: focused.append(n)
    return dialog, said, focused


def _key(dialog, monkeypatch, focus, code, alt=False):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(code)
    event.SetAltDown(alt)
    dialog._on_char_hook(event)


def test_columns_dialog_moves_says_where_and_stays_on_the_column(frame, monkeypatch):
    dialog, said, focused = _columns_dialog(frame)
    try:
        assert dialog.shown.GetString(0) == "Title"
        # Started (#209) and Last message (#146) aren't shown by default.
        assert list(dialog.available.GetStrings()) == [
            "Started (when the session began)", "Last message"]
        assert dialog.preview.GetValue().startswith("Blocked one, Repo, needs you")
        dialog.shown.SetSelection(2)  # Status
        dialog.move_to_end(top=True)
        assert said[-1] == "Status moved to top, 1 of 10."
        assert dialog.fields()[:3] == ["status", "title", "folder"]
        assert dialog.shown.GetSelection() == 0 and focused[-1] == "shown"
        assert dialog.preview.GetValue().startswith("needs you: Pick a name, Blocked one")
        dialog.move(-1)
        assert said[-1] == "Status is already at the top."
        dialog.move(1)
        assert said[-1] == "Status moved down, 2 of 10." and dialog.shown.GetSelection() == 1
        dialog.move_to_end(top=False)
        assert said[-1] == "Status moved to bottom, 10 of 10."
        assert dialog.fields()[-1] == "status" and dialog.shown.GetSelection() == 9
        dialog.move(1)
        assert said[-1] == "Status is already at the bottom."
        # The keys do the same, in the Shown list only.
        _key(dialog, monkeypatch, dialog.shown, wx.WXK_HOME, alt=True)
        assert said[-1] == "Status moved to top, 1 of 10."
        _key(dialog, monkeypatch, dialog.shown, wx.WXK_DOWN, alt=True)
        assert said[-1] == "Status moved down, 2 of 10."
        _key(dialog, monkeypatch, dialog.shown, wx.WXK_UP, alt=True)
        _key(dialog, monkeypatch, dialog.shown, wx.WXK_END, alt=True)
        assert dialog.fields()[-1] == "status"
        count = len(said)
        _key(dialog, monkeypatch, dialog.available, wx.WXK_UP, alt=True)
        assert len(said) == count  # not in Available
    finally:
        dialog.Destroy()


def test_columns_dialog_add_remove_and_reset(frame, monkeypatch):
    dialog, said, focused = _columns_dialog(frame, ["title", "status"])
    try:
        assert dialog.available.GetString(0) == "Folder"
        dialog.shown.SetSelection(0)
        _key(dialog, monkeypatch, dialog.shown, wx.WXK_DELETE)
        assert said[-1] == "Title removed. 1 column shown."
        assert dialog.fields() == ["status"] and focused[-1] == "shown"
        _key(dialog, monkeypatch, dialog.shown, wx.WXK_BACK)  # a Mac's Delete key
        assert said[-1] == "At least one column has to be shown."  # the last one stays
        assert dialog.fields() == ["status"]
        assert dialog.available.GetString(0) == "Title"  # back in catalog order
        dialog.available.SetSelection(0)
        _key(dialog, monkeypatch, dialog.available, wx.WXK_RETURN)  # Enter adds
        assert said[-1] == "Title added, 2 of 2."
        assert dialog.fields() == ["status", "title"]
        assert focused[-1] == "available"  # stays, to add more
        assert dialog.shown.GetSelection() == 1  # on what it added
        assert dialog.available.GetString(0) == "Folder"
        dialog.reset()
        assert said[-1] == "Columns reset to the default: 10 shown, title first."
        assert dialog.fields()[0] == "title" and len(dialog.fields()) == 10
        assert list(dialog.available.GetStrings()) == [
            "Started (when the session began)", "Last message"]
        dialog.available.SetSelection(0)
        dialog.add()
        assert said[-1] == "Started added, 11 of 11."
        # Adding the last one left moves you to Shown, on it.
        dialog.available.SetSelection(0)
        focused.clear()
        dialog.add()
        assert said[-1] == "Last message added, 12 of 12."
        assert focused[-1] == "shown" and dialog.shown.GetSelection() == 11
        assert list(dialog.available.GetStrings()) == ["Every column is shown."]
        dialog.add()
        assert said[-1] == "Every column is shown already."
    finally:
        dialog.Destroy()


def test_columns_dialog_drops_unknown_columns_and_names_its_controls(frame):
    dialog, said, focused = _columns_dialog(frame, ["from_the_future", "status"])
    try:
        assert dialog.fields() == ["status"]
        assert dialog.shown.GetName() == "Shown columns, in the order they're read"
        assert dialog.available.GetName() == "Available columns"
        assert dialog.preview.GetName() == "Preview of the selected session's line"
        assert dialog.GetEscapeId() == wx.ID_CANCEL
    finally:
        dialog.Destroy()


def test_columns_preview_has_a_sample_when_nothing_is_selected(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    monkeypatch.setattr(frame, "_selected_session", lambda: None)
    seen = {}

    class Looks:
        def __init__(self, parent, fields, sample, say):
            seen["line"] = sample.list_line(fields=fields)

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "SessionColumnsDialog", Looks)
    frame.on_session_columns()
    assert seen["line"].startswith("Example session, Example, needs you: Choose a version "
                                   "number, new reply, active 2 minutes ago")


# -- saved prompts (#131) -------------------------------------------------------------


class _FakePromptEdit:
    """Stands in for the New/Edit Prompt dialog: answers come from ``answers``
    in turn, None meaning Cancel."""
    answers = []
    asked = []

    def __init__(self, parent, title, name="", text="", focus_name=None):
        _FakePromptEdit.asked.append((title, name, text))
        _FakePromptEdit.focus_name = focus_name

    def ShowModal(self):
        self._answer = _FakePromptEdit.answers.pop(0)
        return wx.ID_CANCEL if self._answer is None else wx.ID_OK

    def values(self):
        return self._answer

    def Destroy(self):
        pass


class _FakeNameEntry:
    """wx.TextEntryDialog for Send and Save as Prompt: ``answers`` in turn,
    None meaning Cancel; what it was offered is kept in ``offered``."""
    answers = []
    offered = []

    def __init__(self, parent, prompt, title, value=""):
        _FakeNameEntry.offered.append(value)

    def SetMaxLength(self, n):
        _FakeNameEntry.max_length = n

    def ShowModal(self):
        self._answer = _FakeNameEntry.answers.pop(0)
        return wx.ID_CANCEL if self._answer is None else wx.ID_OK

    def GetValue(self):
        return self._answer

    def Destroy(self):
        pass


@pytest.fixture
def name_entry(monkeypatch):
    from thechatplace.ui import main_frame
    _FakeNameEntry.answers, _FakeNameEntry.offered = [], []
    monkeypatch.setattr(main_frame.wx, "TextEntryDialog", _FakeNameEntry)
    return _FakeNameEntry


def _press_enter(frame, monkeypatch, focus, ctrl=True, shift=False):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus))
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    event.SetControlDown(ctrl)
    event.SetShiftDown(shift)
    frame._on_char_hook(event)


def test_prompts_are_kept_in_the_apps_own_folder(frame, env):
    assert frame.prompts.path == env["tmp"] / "appdata" / "prompts.json"


def test_prompts_dialog_new_edit_move_delete_and_use(frame, env, monkeypatch):
    from thechatplace.ui import dialogs
    monkeypatch.setattr(dialogs, "PromptEditDialog", _FakePromptEdit)
    _FakePromptEdit.asked = []
    said = []
    dialog = dialogs.PromptsDialog(frame, frame.prompts, said.append)
    try:
        assert list(dialog.list.GetStrings()) == ["No prompts yet. New makes one."]
        assert dialog.list.GetName() == "Prompts, 0"
        dialog.on_delete()  # nothing to delete: says so, changes nothing
        assert "no prompt to delete" in env["boxes"][-1]

        # New, refused for an empty name, asked again keeping the text.
        _FakePromptEdit.answers = [("", "Review the change"), ("Review", "Review the change")]
        dialog.on_new()
        assert "needs a name" in env["boxes"][-1]
        assert _FakePromptEdit.asked[-1] == ("New Prompt", "", "Review the change")
        assert _FakePromptEdit.focus_name is True  # asked again at the field at fault
        assert frame.prompts.names() == ["Review"]
        assert said[-1] == "Prompt saved: Review."
        assert dialog.list.GetName() == "Prompts, 1"
        assert dialog.text.GetValue() == "Review the change"

        _FakePromptEdit.answers = [("Tests", "Run the tests\nand fix them")]
        dialog.on_new()
        _FakePromptEdit.answers = [("Plan", "Make a plan")]
        dialog.on_new()
        assert frame.prompts.names() == ["Review", "Tests", "Plan"]
        assert dialog.list.GetSelection() == 2  # the new one is selected

        # Cancel in New changes nothing.
        _FakePromptEdit.answers = [None]
        dialog.on_new()
        assert frame.prompts.names() == ["Review", "Tests", "Plan"]

        # Edit starts from the selected prompt, and keeps its place.
        dialog.list.SetSelection(1)
        _FakePromptEdit.answers = [("Tests", "Run all the tests")]
        dialog.on_edit()
        assert _FakePromptEdit.asked[-1] == ("Edit Prompt", "Tests",
                                             "Run the tests\nand fix them")
        assert frame.prompts.get(1).text == "Run all the tests"
        assert dialog.list.GetSelection() == 1

        # Move Up, and already first.
        dialog.on_move(-1)
        assert frame.prompts.names() == ["Tests", "Review", "Plan"]
        assert said[-1] == "Moved up: Tests, 1 of 3."
        assert dialog.list.GetSelection() == 0
        dialog.on_move(-1)
        assert said[-1] == "Tests is already first."

        # Delete asks first (No keeps it), and the next prompt takes its place.
        monkeypatch.setattr(wx, "MessageBox",
                            lambda *a, **k: env["boxes"].append(a[0]) or wx.NO)
        dialog.on_delete()
        assert env["boxes"][-1] == "Delete the prompt Tests?"
        assert len(frame.prompts) == 3
        monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
        dialog.on_delete()
        assert frame.prompts.names() == ["Review", "Plan"]
        assert said[-1] == "Prompt deleted: Tests."
        assert dialog.list.GetStringSelection() == "Review"
        dialog.list.SetSelection(1)
        dialog.on_delete()  # the last one: the one before it is selected
        assert dialog.list.GetStringSelection() == "Review"

        # Use chooses the selected prompt and closes.
        ended = []
        monkeypatch.setattr(dialog, "EndModal", ended.append)
        dialog.on_use()
        assert ended == [wx.ID_OK] and dialog.chosen.name == "Review"
        # Saved as they're made.
        from thechatplace.prompts import PromptStore
        assert PromptStore(frame.prompts.path).names() == ["Review"]
    finally:
        dialog.Destroy()


def test_prompts_dialog_keys(frame, monkeypatch):
    from thechatplace.ui import dialogs
    frame.prompts.add("Review", "Review it")
    dialog = dialogs.PromptsDialog(frame, frame.prompts, lambda text: None)
    try:
        assert dialog.GetEscapeId() == wx.ID_CANCEL
        assert dialog.use_btn.GetId() == wx.ID_OK  # the default: Enter is Use
        assert dialog.text.GetName() == "Prompt text" and not dialog.text.IsEditable()
        calls = []
        monkeypatch.setattr(dialog, "on_use", lambda: calls.append("use"))
        monkeypatch.setattr(dialog, "on_delete", lambda: calls.append("delete"))
        for key in (wx.WXK_RETURN, wx.WXK_DELETE):
            event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
            event.SetKeyCode(key)
            dialog._on_list_key(event)
        assert calls == ["use", "delete"]
    finally:
        dialog.Destroy()


def test_prompt_edit_dialog_is_named_and_ctrl_enter_saves(frame, monkeypatch):
    from thechatplace.ui.dialogs import PromptEditDialog
    dialog = PromptEditDialog(frame, "Edit Prompt", "Review", "Line one\nLine two")
    try:
        assert dialog.name.GetName() == "Prompt name"
        assert dialog.text.GetName() == "Prompt text"
        assert dialog.values() == ("Review", "Line one\nLine two")
        ended = []
        monkeypatch.setattr(dialog, "EndModal", ended.append)
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        event.SetControlDown(True)
        dialog._on_key(event)
        assert ended == [wx.ID_OK]
    finally:
        dialog.Destroy()


class _UsesPrompt:
    """PromptsDialog that picks the first prompt, as Use would."""
    use_refused = None

    def __init__(self, parent, store, say, use_refused=""):
        _UsesPrompt.use_refused = use_refused
        self.chosen = store.get(0)

    def ShowModal(self):
        return wx.ID_OK

    def Destroy(self):
        pass


def test_use_puts_the_prompt_in_the_reply_box(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame, "PromptsDialog", _UsesPrompt)
    frame.prompts.add("Review", "Review the change.")
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.on_prompts()
    assert _UsesPrompt.use_refused == ""
    assert frame.reply_text.GetValue() == "Review the change."
    assert frame.reply_text.GetInsertionPoint() == len("Review the change.")
    assert env["feedback"][-1] == "Inserted the prompt Review."
    # With something typed, it goes in at the cursor.
    frame.reply_text.SetValue("Hi. Thanks.")
    frame.reply_text.SetInsertionPoint(4)
    frame.on_prompts()
    assert frame.reply_text.GetValue() == "Hi. Review the change.Thanks."


def test_use_says_why_not_without_one_of_our_sessions(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame, "PromptsDialog", _UsesPrompt)
    frame.prompts.add("Review", "Review the change.")
    frame.on_prompts()  # nothing loaded
    assert "Load one of The Chat Place's own sessions" in _UsesPrompt.use_refused
    select(frame, "Quiet one")
    frame.on_open_session()
    frame.on_prompts()
    assert "only reads" in _UsesPrompt.use_refused
    assert "only reads" in env["feedback"][-1]  # and nothing was put anywhere


def test_ctrl_shift_enter_saves_the_message_as_a_prompt_and_sends_it(
        frame, env, fake_runner, monkeypatch, name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Please review the tests and fix what fails\nThanks")
    name_entry.answers = ["Fix tests"]
    _press_enter(frame, monkeypatch, frame.reply_text, shift=True)
    assert name_entry.offered == ["Please review the tests and fix"]  # its first words
    assert frame.prompts.names() == ["Fix tests"]
    assert frame.prompts.get(0).text == "Please review the tests and fix what fails\nThanks"
    runner = fake_runner.instances[0]
    assert runner.prompt == "Please review the tests and fix what fails\nThanks"
    assert frame.reply_text.GetValue() == ""
    assert env["feedback"][-2] == "Prompt saved: Fix tests."
    assert env["feedback"][-1].startswith("Sent to Hub probe:")


def test_ctrl_shift_enter_cancelled_neither_saves_nor_sends(
        frame, env, fake_runner, monkeypatch, name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Keep me")
    name_entry.answers = [None]
    _press_enter(frame, monkeypatch, frame.reply_text, shift=True)
    assert fake_runner.instances == [] and len(frame.prompts) == 0
    assert frame.reply_text.GetValue() == "Keep me"
    assert env["feedback"][-1] == "Not saved or sent."


def test_ctrl_shift_enter_asks_again_for_a_taken_name(
        frame, env, fake_runner, monkeypatch, name_entry):
    frame.prompts.add("Review", "Older")
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Review")
    name_entry.answers = ["review", "Review again"]
    frame.on_send_and_save()
    assert name_entry.offered == ["Review (2)", "review"]  # unique first, then what you typed
    assert "already a prompt called Review" in env["boxes"][-1]
    assert frame.prompts.names() == ["Review", "Review again"]
    assert fake_runner.instances[0].prompt == "Review"


def test_ctrl_shift_enter_that_cannot_save_does_not_send(
        frame, env, fake_runner, monkeypatch, name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Important")

    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(frame.prompts, "save", fail)
    name_entry.answers = ["Important"]
    frame.on_send_and_save()
    assert fake_runner.instances == [] and frame.reply_text.GetValue() == "Important"
    assert "disk full" in env["boxes"][-1] and "wasn't sent" in env["boxes"][-1]


def test_ctrl_shift_enter_with_nothing_typed_is_send(frame, env, fake_runner, monkeypatch,
                                                     name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.on_send_and_save()
    assert name_entry.offered == []  # not asked
    assert env["feedback"][-1] == "Type a message first."


def test_ctrl_enter_still_just_sends(frame, env, fake_runner, monkeypatch, name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Plain send")
    _press_enter(frame, monkeypatch, frame.reply_text)
    assert name_entry.offered == [] and len(frame.prompts) == 0
    assert fake_runner.instances[0].prompt == "Plain send"


@msaa
def test_prompts_list_says_how_many_to_jaws_and_nvda(frame):
    from thechatplace.ui import dialogs
    frame.prompts.add("Review", "Review it")
    dialog = dialogs.PromptsDialog(frame, frame.prompts, lambda text: None)
    try:
        assert dialog.list.GetAccessible().GetName(0) == (wx.ACC_OK, "Prompts, 1")
        frame.prompts.add("Plan", "Plan it")
        dialog._fill(1)
        assert dialog.list.GetAccessible().GetName(0) == (wx.ACC_OK, "Prompts, 2")
    finally:
        dialog.Destroy()


def test_prompt_editor_keeps_what_was_typed_when_the_save_fails(frame, env, monkeypatch):
    from thechatplace.ui import dialogs
    monkeypatch.setattr(dialogs, "PromptEditDialog", _FakePromptEdit)
    _FakePromptEdit.asked = []
    dialog = dialogs.PromptsDialog(frame, frame.prompts, lambda text: None)
    try:
        def fail():
            raise OSError("disk full")
        monkeypatch.setattr(frame.prompts, "save", fail)
        _FakePromptEdit.answers = [("Long", "A long prompt"), None]
        dialog.on_new()
        assert "disk full" in env["boxes"][-1]
        assert _FakePromptEdit.asked[-1] == ("New Prompt", "Long", "A long prompt")
        assert len(frame.prompts) == 0
    finally:
        dialog.Destroy()


def test_ctrl_shift_enter_whose_send_fails_says_the_prompt_is_saved(
        frame, env, fake_runner, monkeypatch, name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup(None, reason="No Claude Code."))
    frame.reply_text.SetValue("Important")
    name_entry.answers = ["Important"]
    frame.on_send_and_save()
    assert fake_runner.instances == [] and frame.prompts.names() == ["Important"]
    assert "No Claude Code." in env["boxes"][-1]
    assert env["feedback"][-1] == "The prompt Important is saved; press Ctrl+Enter to send."


def test_ctrl_shift_enter_when_the_reply_changes_meanwhile(
        frame, env, fake_runner, monkeypatch, name_entry):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("Mine")

    class GivesBack(_FakeNameEntry):
        def ShowModal(self):
            frame.reply_text.SetValue("Queued one\n\nMine")  # a queued send given back
            return super().ShowModal()
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame.wx, "TextEntryDialog", GivesBack)
    name_entry.answers = ["Mine"]
    frame.on_send_and_save()
    assert fake_runner.instances == [] and len(frame.prompts) == 0
    assert "changed" in env["boxes"][-1]


def test_prompts_are_on_the_file_menu(frame):
    labels = [item.GetItemLabel() for item in frame.GetMenuBar().GetMenu(0).GetMenuItems()]
    assert "Prompts...\tCtrl+Shift+P" in labels
    keys = "Cmd+Shift+Return" if wx.Platform == "__WXMAC__" else "Ctrl+Shift+Enter"
    assert f"Send and Save as Prompt... ({keys})" in labels


# -- screen reader can't be reached (issue #98) -------------------------------------------


def test_an_unreachable_screen_reader_is_reported_beside_the_announcement(frame, env):
    frame._say("Hub probe replied.")
    reason = "not spoken: NVDA is running but didn't answer (error 1722)"
    speech.speaker.on_problem(reason)
    wx.Yield()
    assert frame._status_latest == ("Hub probe replied. (Speech: Not spoken: NVDA is running "
                                    "but didn't answer (error 1722).)")
    assert env["notified"][-1] == ("Announcements aren't being spoken",
                                   "Not spoken: NVDA is running but didn't answer (error 1722).",
                                   None)


def test_no_notification_about_speech_when_notifications_are_off(frame, env):
    frame.speech.notifications = speech.NOTIFY_OFF
    before = len(env["notified"])
    frame._on_speech_problem("not spoken: JAWS is running but refused the text")
    assert len(env["notified"]) == before
    assert frame._status_latest.endswith(
        "(Speech: Not spoken: JAWS is running but refused the text.)")


def test_a_speech_problem_after_the_window_closed_is_ignored(env):
    from thechatplace.ui.main_frame import MainFrame
    closed = MainFrame()
    report = closed._on_speech_problem
    closed.stop_timers()  # its delayed calls must not reach it once it's gone
    closed._pool.shutdown(wait=True)
    closed.Destroy()
    wx.Yield()
    report("not spoken: NVDA is running but didn't answer (error 1722)")  # no error


def test_settings_says_why_the_last_announcement_was_not_spoken(frame, monkeypatch):
    """In a read-only box after the speech engine, so Tab reaches it: the
    note under the engine choice is static text no Tab ever lands on."""
    from thechatplace.ui.dialogs import SettingsDialog
    monkeypatch.setattr(speech.speaker, "last_problem",
                        "not spoken: JAWS is running but refused the text")
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        box = dialog.speech_problem
        assert box.GetValue() == ("Last announcement not spoken: JAWS is running but refused "
                                  "the text.")
        assert box.GetName() == "Speech problem" and box.IsEditable() is False
        assert box.AcceptsFocusFromKeyboard()
        children = list(dialog.GetChildren())
        assert children.index(box) == children.index(dialog.rate_choice) + 2  # after the note
    finally:
        dialog.Destroy()
    monkeypatch.setattr(speech.speaker, "last_problem", "")
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        assert dialog.speech_problem is None
    finally:
        dialog.Destroy()


def test_a_speech_problem_goes_beside_what_the_status_bar_says_now(frame, env):
    """Not beside an older announcement: a confirmation can be what failed."""
    frame._say("Hub probe replied.")
    frame._feedback("Sent. Hub probe is working.")
    frame._on_speech_problem("not spoken: JAWS is running but refused the text")
    assert frame._status_latest == ("Sent. Hub probe is working. (Speech: Not spoken: JAWS is "
                                    "running but refused the text.)")


# -- Help's update item and "Update Installed" (#141) ----------------------------------------


def help_update_label(frame):
    return frame.update_item.GetItemLabel()


def test_help_update_item_names_the_running_version_then_the_update(frame, env, monkeypatch):
    from thechatplace import __version__
    from thechatplace.updater import (AVAILABLE, CURRENT, FAILED, NO_RELEASES, NOT_INSTALLED,
                                      CheckResult)
    running = f"Check for &Updates (running {__version__})..."
    assert help_update_label(frame) == running
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, __version__, "9.0.0"))
    run_check(frame, manual=False)
    assert help_update_label(frame) == "&Update to 9.0.0 Available..."
    assert env["spoken"][-1].endswith("Help, Update to 9.0.0 Available installs it, or the "
                                      "Update button on the status bar.")
    # A failed check doesn't make the update vanish.
    frame.updates = FakeUpdates(CheckResult(FAILED, __version__, detail="offline"))
    run_check(frame, manual=False)
    assert help_update_label(frame) == "&Update to 9.0.0 Available..."
    # Choosing it offers the update; No leaves it on the menu.
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.NO)
    frame.updates = FakeUpdates(CheckResult(AVAILABLE, __version__, "9.0.0"))
    run_check(frame)
    assert asked and "Install it now?" in asked[-1]
    assert env["feedback"][-1] == "Not now. Help, Update to 9.0.0 Available installs it later."
    assert help_update_label(frame) == "&Update to 9.0.0 Available..."
    # Nothing newer after all: back to the running version.
    for status in (CURRENT, NO_RELEASES, NOT_INSTALLED):
        frame.updates = FakeUpdates(CheckResult(AVAILABLE, __version__, "9.0.0"))
        run_check(frame, manual=False)
        frame.updates = FakeUpdates(CheckResult(status, __version__, __version__))
        run_check(frame, manual=False)
        assert help_update_label(frame) == running


class NoticeUpdates:
    def __init__(self, installed):
        self.can_update = installed


def run_notice(frame, installed=True):
    frame.updates = NoticeUpdates(installed)
    frame.check_update_installed()
    frame._pool.submit(lambda: None).result()
    pump(lambda: False, timeout=0.2)


@pytest.fixture
def notice(frame, monkeypatch):
    """The frame running 0.1.4, with the dialog recorded instead of shown."""
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame, "__version__", "0.1.4")
    shown = []
    monkeypatch.setattr(frame, "_show_update_installed_when_free",
                        lambda version, tries: shown.append(version))
    return shown


@pytest.mark.parametrize("previous,installed,enabled,shown,recorded", [
    ("0.1.3", True, True, ["0.1.4"], "0.1.4"),     # updated
    ("", True, True, [], "0.1.4"),                 # first run
    ("0.1.4", True, True, [], "0.1.4"),            # same version
    ("0.1.5", True, True, [], "0.1.4"),            # downgrade
    ("junk", True, True, [], "0.1.4"),             # unreadable record
    ("0.1.3", False, True, [], "0.1.3"),           # portable copy or source run
    ("0.1.3", True, False, [], "0.1.4"),           # turned off in Settings
])
def test_update_installed_notice_decision(frame, env, notice, previous, installed, enabled,
                                          shown, recorded):
    from thechatplace.speech import SpeechSettings
    frame.speech.last_run_version = previous
    frame.speech.update_installed_notice = enabled
    run_notice(frame, installed)
    assert notice == shown
    assert frame.speech.last_run_version == recorded
    if recorded != previous:
        assert SpeechSettings.load(env["tmp"] / "speech.json").last_run_version == recorded


def test_update_installed_notice_survives_a_failed_settings_save(frame, env, notice,
                                                                 monkeypatch):
    def refuse(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(frame.speech, "save", refuse)
    frame.speech.last_run_version = "0.1.3"
    run_notice(frame, True)
    assert notice == ["0.1.4"]
    # Kept in memory, so the next save (Settings, OK) records it.
    assert frame.speech.last_run_version == "0.1.4"


def test_update_installed_waits_for_a_free_main_window(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    shown = []
    monkeypatch.setattr(frame, "show_update_installed", lambda version: shown.append(version))
    later = []
    monkeypatch.setattr(main_frame.wx, "CallLater", lambda ms, fn, *args: later.append(
        (ms, fn, args)))
    # The hidden test window is never free: it tries again a second later...
    frame._show_update_installed_when_free("0.1.4", 3)
    assert later == [(main_frame.UPDATE_NOTICE_RETRY_MS, frame._show_update_installed_when_free,
                      ("0.1.4", 2, False))]
    assert shown == []
    # ...and on its last try says so on the status bar instead.
    frame._show_update_installed_when_free("0.1.4", 1)
    assert frame.GetStatusBar().GetStatusText() == "The Chat Place was updated to 0.1.4."
    assert shown == [] and len(later) == 1
    # Shown, in front, nothing over it: free, but only shown once it was
    # free at the try before too (not the moment you switch back).
    monkeypatch.setattr(frame, "IsShown", lambda: True)
    monkeypatch.setattr(frame, "IsActive", lambda: True)
    monkeypatch.setattr(frame, "_modal_open", lambda: False)
    later.clear()
    frame._show_update_installed_when_free("0.1.4", 3)
    assert shown == [] and later[-1][2] == ("0.1.4", 2, True)
    frame._show_update_installed_when_free("0.1.4", 2, True)
    assert shown == ["0.1.4"]
    # Not stacked on a dialog, over an open menu, or while you're in another app.
    for name, value in (("_modal_open", lambda: True), ("IsActive", lambda: False),
                        ("IsEnabled", lambda: False)):
        shown.clear()
        with monkeypatch.context() as m:
            m.setattr(frame, name, value)
            frame._show_update_installed_when_free("0.1.4", 3, True)
        assert shown == [], name
    frame._menu_open = True
    frame._show_update_installed_when_free("0.1.4", 3, True)
    assert shown == []
    frame._menu_open = False


def test_update_installed_notice_is_never_written_over_unreadable_settings(frame, env, notice):
    from thechatplace.speech import SpeechSettings
    path = env["tmp"] / "speech.json"
    path.write_text("{not json", encoding="utf-8")
    frame.speech = SpeechSettings.load(path)
    assert frame.speech.unreadable
    run_notice(frame, True)
    assert path.read_text(encoding="utf-8") == "{not json"
    assert notice == []


def test_update_installed_notice_when_installed_cannot_be_told(frame, env, notice):
    class Broken:
        @property
        def can_update(self):
            raise RuntimeError("no velopack")
    frame.speech.last_run_version = "0.1.3"
    frame.updates = Broken()
    frame.check_update_installed()
    frame._pool.submit(lambda: None).result()
    pump(lambda: False, timeout=0.2)
    assert notice == [] and frame.speech.last_run_version == "0.1.3"


def test_update_installed_notice_does_nothing_once_closing(frame, env, notice):
    frame.speech.last_run_version = "0.1.3"
    frame._closing = True
    try:
        frame._on_update_installed_known(True)
    finally:
        frame._closing = False
    assert notice == [] and frame.speech.last_run_version == "0.1.3"


def test_startup_schedules_the_notice_and_close_stops_it(env, monkeypatch):
    from thechatplace.ui import main_frame
    store = OwnSessionStore(env["tmp"] / "own.json")
    window = main_frame.MainFrame(store=store, check_updates_at_start=True)
    try:
        assert window._startup_update_notice.IsRunning()
        window._update_notice_wait = wx.CallLater(60_000, lambda: None)
        window._startup_update_check.Stop()
        window.Close(force=True)
        assert not window._startup_update_notice.IsRunning()
        assert not window._update_notice_wait.IsRunning()
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        if window:
            window.Destroy()
        wx.GetApp().ProcessPendingEvents()


def test_update_installed_dialog(frame, env):
    from thechatplace.ui.dialogs import UpdateInstalledDialog
    opened = []
    dialog = UpdateInstalledDialog(frame, "0.1.4", lambda: opened.append(True))
    try:
        assert dialog.GetTitle() == "The Chat Place Update Installed"
        assert dialog.message.GetLabel() == "The Chat Place was updated to 0.1.4."
        assert dialog.whats_new.GetLabel() == "See &what's new in 0.1.4"
        assert dialog.whats_new.GetName() == "See what's new in 0.1.4"
        assert dialog.close_button.GetId() == wx.ID_CANCEL and dialog.GetEscapeId() == wx.ID_CANCEL
        # Tab order: See What's New first, then Close.
        buttons = [c for c in dialog.GetChildren() if isinstance(c, wx.Button)]
        assert buttons == [dialog.whats_new, dialog.close_button]
        dialog.see_whats_new()
        assert opened == [True]
    finally:
        dialog.Destroy()


def test_update_installed_dialog_opens_the_release_notes(frame, env, monkeypatch):
    seen = []

    def fake_modal(dialog):
        seen.append(dialog.GetTitle())
        dialog.see_whats_new()
        dialog.Destroy()
    monkeypatch.setattr(frame, "_modal", fake_modal)
    frame.show_update_installed("0.1.4")
    assert seen == ["The Chat Place Update Installed"]
    assert env["opened"] == ["https://github.com/TheIdeaPlace/AIChat/releases/tag/v0.1.4"]
    assert env["feedback"][-1] == "Opening what's new in The Chat Place 0.1.4."

    def broken(url):
        raise OSError("no browser")
    monkeypatch.setattr(platform_paths, "open_url", broken)
    frame.show_update_installed("0.1.4")
    assert env["boxes"][-1].startswith("Couldn't open the release notes: no browser")
    assert "releases/tag/v0.1.4" in env["boxes"][-1]


def test_settings_has_the_update_installed_choice(frame, env):
    from thechatplace.ui.dialogs import SettingsDialog
    frame.speech.last_run_version = "0.1.3"
    dialog = SettingsDialog(frame, frame.speech, speech.default_options())
    try:
        assert dialog.update_notice.GetValue() is True
        assert dialog.update_notice.GetLabel().startswith(
            "&Tell me when an update has been installed")
        dialog.update_notice.SetValue(False)
        got = dialog.get_settings()
        assert got.update_installed_notice is False
        assert got.last_run_version == "0.1.3"   # passes through untouched
    finally:
        dialog.Destroy()


# -- links to sessions (#144) --------------------------------------------------------------


def _links_focus(frame, monkeypatch):
    """Where focus was sent (the hidden window can't really take it)."""
    focused = []
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focused.append("messages"))
    monkeypatch.setattr(frame, "_come_forward",
                        lambda: focused.append("forward") or frame._open_modal())
    return focused


def test_copy_session_link_on_both_menus_with_its_own_key(frame):
    bar = frame.GetMenuBar()
    labels = []
    keys = []

    def walk(menu):
        for item in menu.GetMenuItems():
            if item.GetSubMenu() is not None:
                walk(item.GetSubMenu())
                continue
            labels.append(item.GetItemLabelText())
            accel = item.GetAccel()
            if accel is not None:
                keys.append(accel.ToString())
    for index in range(bar.GetMenuCount()):
        walk(bar.GetMenu(index))
    assert "Copy Session Link..." in labels
    assert "Ctrl+Shift+L" in keys
    assert len(keys) == len(set(keys)), "two menu items share a key"
    select(frame, "Quiet one")
    menu, _actions = frame._session_menu()
    assert "Copy Session Link..." in [i.GetItemLabelText() for i in menu.GetMenuItems()]
    menu.Destroy()


def test_copy_session_link_for_a_desktop_session(frame, env, monkeypatch):
    select(frame, "Quiet one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    offered = []

    def choose(title, prompt, choices, selection=None):
        offered.append((title, prompt, list(choices), selection))
        return selection
    monkeypatch.setattr(frame, "_choose", choose)
    frame.on_copy_session_link()
    title, prompt, choices, selection = offered[0]
    assert (title, prompt, selection) == ("Copy Session Link", "Copy a link to Quiet one:", 0)
    assert choices[0] == "Open in The Chat Place, as a Markdown link"
    assert choices[1] == "Open in Claude, as a Markdown link"
    assert env["copied"][-1] == "[Quiet one](thechatplace://session/local_a)"
    assert env["feedback"][-1] == ("Copied a Markdown link to Quiet one, to open it in The "
                                   "Chat Place.")
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None: next(
        i for i, c in enumerate(choices) if c.startswith("Open in Claude, the link only")))
    frame.on_copy_session_link()
    assert env["copied"][-1] == "claude://claude.ai/epitaxy/local_a"
    monkeypatch.setattr(frame, "_choose", lambda *a, **k: None)
    count = len(env["copied"])
    frame.on_copy_session_link()  # Escape: nothing copied
    assert len(env["copied"]) == count


def test_copy_session_link_for_an_own_session_on_remote_control(frame, env, monkeypatch):
    frame.store.update("own-1", remote_url="https://claude.ai/code/session_9")
    select(frame, "Hub probe")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    offered = []
    monkeypatch.setattr(frame, "_choose", lambda title, prompt, choices, selection=None:
                        offered.append(list(choices)) or len(choices) - 1)
    frame.on_copy_session_link()
    assert [c.split(",")[0] for c in offered[0]] == [
        "Open in The Chat Place", "Its claude.ai address",
        "Open in The Chat Place", "Its claude.ai address"]
    assert env["copied"][-1] == "https://claude.ai/code/session_9"
    assert env["feedback"][-1] == "Copied Hub probe's claude.ai address."


def test_a_link_selects_and_loads_its_session(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("hi"),
                                                  assistant_block(text_block("Hello."), "m1")])
    focused = _links_focus(frame, monkeypatch)
    frame.open_link("thechatplace://session/local_a")
    assert frame._open is not None and frame._open.key == "local_a"
    assert frame._list_keys[frame.session_list.GetSelection()] == "local_a"
    assert "messages" in focused
    assert pump(lambda: any(t.startswith("Loaded Quiet one") for t in env["feedback"]))
    # Again, while it's loaded: back to it, not reloaded.
    frame.open_link("thechatplace://session/LOCAL_A/")
    assert env["feedback"][-1] == "Back in Quiet one."
    # The Chat Place's own session, by its Claude Code id.
    frame.open_link("thechatplace://session/own-1")
    assert frame._open.key == "own:own-1"


def test_bad_and_unknown_links_change_nothing(frame, env, monkeypatch):
    _links_focus(frame, monkeypatch)
    frame.open_link("thechatplace://session/../own.json")
    assert env["feedback"][-1] == "That link isn't one The Chat Place knows."
    frame.open_link("thechatplace://session/nobody")
    assert env["feedback"][-1] == "No session with that link."
    assert frame._open is None
    assert frame._pending_link is None


def test_a_link_to_a_session_out_of_view_shows_all(frame, env, monkeypatch):
    _links_focus(frame, monkeypatch)
    from thechatplace.sessions import VIEW_NEEDS_YOU
    frame.on_view(VIEW_NEEDS_YOU)
    assert "local_a" not in frame._list_keys
    frame.open_link("thechatplace://session/local_a")
    assert frame.speech.session_view == "all"
    assert "local_a" in frame._list_keys
    assert "Showing all sessions, to show Quiet one." in env["feedback"]
    assert frame._open.key == "local_a"


def test_a_link_to_a_hidden_session_shows_hidden(frame, env, monkeypatch):
    _links_focus(frame, monkeypatch)
    frame.hidden.hide("local_a")
    frame._refresh_list_in_place()
    frame.open_link("thechatplace://session/local_a")
    assert frame.speech.session_view == "hidden"
    assert frame._open.key == "local_a"


def test_a_link_waits_for_the_list_and_for_a_dialog(frame, env, monkeypatch):
    focused = _links_focus(frame, monkeypatch)
    frame._first_snapshot = True  # as if just started
    frame.open_link("thechatplace://session/local_a")
    assert frame._open is None and frame._pending_link
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame._open is not None)
    assert frame._open.key == "local_a"
    raised = []
    dialog = type("Dialog", (), {"Raise": lambda self: raised.append(True)})()
    monkeypatch.setattr(frame, "_open_modal", lambda: dialog)
    focused.clear()
    frame.open_link("thechatplace://session/local_b")
    for _tick in range(3):
        frame._check_link_inbox()
    assert env["feedback"].count("The link opens when you close this dialog.") == 1
    # It came forward once, not on every tick of the timer.
    assert focused.count("forward") == 1
    assert frame._open.key == "local_a"
    monkeypatch.setattr(frame, "_open_modal", lambda: None)
    frame._check_link_inbox()
    assert frame._open.key == "local_b"
    assert focused.count("forward") == 1


def test_a_link_waits_behind_a_native_message_box(frame, env, monkeypatch):
    _links_focus(frame, monkeypatch)
    # A native message box disables the window without being a wx dialog.
    monkeypatch.setattr(frame, "IsEnabled", lambda: False)
    frame.open_link("thechatplace://session/local_a")
    frame._check_link_inbox()
    assert frame._open is None
    assert env["feedback"].count("The link opens when you close this dialog.") == 1
    monkeypatch.setattr(frame, "IsEnabled", lambda: True)
    frame._check_link_inbox()
    assert frame._open.key == "local_a"


def test_a_link_to_an_archived_session_shows_archived(frame, env, monkeypatch):
    _links_focus(frame, monkeypatch)
    add_desktop(env, "local_old", "cli-old", "Old one", isArchived=True)
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert "local_old" not in frame._list_keys
    frame.open_link("thechatplace://session/local_old")
    assert frame.speech.session_view == "archived"
    assert frame._open.key == "local_old"


def test_a_link_from_another_copy_is_opened(frame, env, monkeypatch):
    _links_focus(frame, monkeypatch)
    platform_paths.drop_link("thechatplace://session/local_b")
    assert (env["tmp"] / "appdata" / "links").is_dir()
    frame._check_link_inbox()
    assert frame._open.key == "local_b"
    assert list((env["tmp"] / "appdata" / "links").iterdir()) == []


def test_a_session_link_in_the_formatted_view_opens_here(frame, env, monkeypatch):
    from thechatplace.ui import dialogs, main_frame

    class FakePage:
        def __init__(self, parent, title, page):
            self.app_link = None

        def ShowModal(self):
            self.app_link = "thechatplace://session/local_a"
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "formatted_view_available", lambda: True)
    monkeypatch.setattr(main_frame, "FormattedMessageDialog", FakePage)
    _links_focus(frame, monkeypatch)
    assert frame._show_page("t", "<p>x</p>")
    assert pump(lambda: frame._open is not None)
    assert frame._open.key == "local_a"
    assert env["opened"] == []  # never handed to the shell
    # The page itself: a session link closes it with the link; a web link
    # goes to the browser; anything else goes nowhere.
    ended = []
    page = type("Page", (), {})()
    page.app_link = None
    page.EndModal = lambda code: ended.append(code)
    page._open_url = lambda url: env["opened"].append(url)
    dialogs.FormattedMessageDialog._follow(page, "thechatplace://session/x")
    assert page.app_link == "thechatplace://session/x"
    assert pump(lambda: ended == [wx.ID_CANCEL])
    dialogs.FormattedMessageDialog._follow(page, "https://example.com")
    dialogs.FormattedMessageDialog._follow(page, "file:///C:/x")
    assert env["opened"] == ["https://example.com"]


# -- the Last message column (#146) ---------------------------------------------------


def _last_message_transcripts(env):
    from records import assistant_block, text_block, user_text
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Is it fixed?"),
                                                 assistant_block(text_block("**Yes**, it's fixed."),
                                                                 "m1")])
    add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("Try the probe again")])


def test_last_message_column_is_off_and_nothing_is_read(frame, env, monkeypatch):
    from thechatplace import hub
    from thechatplace.transcript import LastMessages
    cache = LastMessages()
    monkeypatch.setattr(hub, "LAST_MESSAGES", cache)
    _last_message_transcripts(env)
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    assert "last_message" not in frame.speech.session_fields
    assert not any("Claude:" in row or "You:" in row for row in frame.session_list.GetStrings())
    assert cache.reads == 0  # no transcript is opened while the column is off


def test_adding_last_message_fills_the_rows(frame, env, monkeypatch):
    from thechatplace import hub
    from thechatplace.transcript import LastMessages
    from thechatplace.ui import main_frame
    monkeypatch.setattr(hub, "LAST_MESSAGES", LastMessages())
    _last_message_transcripts(env)
    select(frame, "Quiet one")
    seen = {}

    class Chooses:
        def __init__(self, parent, fields, sample, say):
            seen["sample"] = sample

        def ShowModal(self):
            return wx.ID_OK

        def fields(self):
            return ["title", "last_message", "status"]

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "SessionColumnsDialog", Chooses)
    frame.on_session_columns()
    # The preview has the selected session's last message although the
    # column was off; the session in the list isn't changed by it.
    assert seen["sample"].last_message == "Claude: Yes, it's fixed."
    assert seen["sample"] is not _session(frame, "Quiet one")
    settle(frame)
    rows = sorted(frame.session_list.GetStrings())
    assert rows == ["Blocked one, needs you: Pick a name",
                    "Hub probe, You: Try the probe again, idle",
                    "Quiet one, Claude: Yes, it's fixed., idle"]
    assert frame.session_list.GetStringSelection().startswith("Quiet one, Claude:")
    assert env["feedback"][-1] == ("Session list columns saved. Each session reads: "
                                   "Title, Last message, Status.")
    assert speech.SpeechSettings.load().session_fields == ["title", "last_message", "status"]


def test_a_new_last_message_on_your_row_waits_while_it_works(frame, env, monkeypatch):
    """The row under the reader is rewritten (and so read again) when its
    status changes, as before, but not for each message Claude writes while
    working; other rows change at once."""
    from thechatplace.sessions import IDLE, WORKING
    frame.speech.session_fields = ["title", "status", "last_message", "activity"]
    quiet, blocked = _session(frame, "Quiet one"), _session(frame, "Blocked one")
    quiet.last_message, blocked.last_message = "Claude: one", "You: a"
    select(frame, "Quiet one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    frame._refresh_list_in_place(rewrite=True)
    assert frame.session_list.GetStringSelection().startswith("Quiet one, idle, Claude: one,")
    # No patching of the list itself: an attribute put on a wx control can
    # outlive the window, and the row's text shows whether it was rewritten
    # (each change below also changes its age, so a rewrite would show).

    def row():
        return frame.session_list.GetStringSelection()
    quiet.state = WORKING
    frame._refresh_list_in_place()
    assert row().startswith("Quiet one, working, Claude: one,")  # a status change
    before = row()
    quiet.last_message = "Claude: two"
    blocked.last_message = "You: b"
    quiet.last_activity_ms -= 3 * 60_000
    frame._refresh_list_in_place()
    assert row() == before  # left alone, age and all
    assert any(r.startswith("Blocked one") and "You: b" in r
               for r in frame.session_list.GetStrings())  # another row: at once
    quiet.state = IDLE
    frame._refresh_list_in_place()
    assert row().startswith("Quiet one, idle, Claude: two,")
    # Idle, a new message (a turn that ended between refreshes): rewritten.
    quiet.last_message = "You: three"
    frame._refresh_list_in_place()
    assert row().startswith("Quiet one, idle, You: three,")
    before = row()
    quiet.last_activity_ms -= 3 * 60_000  # a clock tick alone: left alone
    frame._refresh_list_in_place()
    assert row() == before


def test_the_started_column_is_a_clock_too_and_never_rereads_your_row(frame, env,
                                                                     monkeypatch):
    """#209 review: Started moves with the clock ("started 5 minutes ago",
    then 6), and the row you're on mustn't be read again for that."""
    select(frame, "Quiet one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    quiet = _session(frame, "Quiet one")
    quiet.created_ms = int(time.time() * 1000) - 5 * 60_000
    _choose_columns(monkeypatch, ["title", "status", "started"])
    frame.on_session_columns()  # rewrites the rows, with the new column
    before = frame.session_list.GetStringSelection()
    assert before.endswith("started 5 minutes ago")
    quiet.created_ms -= 3 * 60_000  # three minutes on
    frame._refresh_list_in_place()
    assert frame.session_list.GetStringSelection() == before


def test_adding_last_message_on_a_working_session_fills_your_row_at_once(frame, env,
                                                                        monkeypatch):
    """The row you're on gets its message with the columns, in the one
    rewrite OK makes, even while its session works (when later messages
    wait for the status to change)."""
    from thechatplace.sessions import WORKING
    _last_message_transcripts(env)
    select(frame, "Quiet one")
    _session(frame, "Quiet one").state = WORKING
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    _choose_columns(monkeypatch, ["title", "status", "last_message"])
    frame.on_session_columns()
    assert frame.session_list.GetStringSelection() == \
        "Quiet one, working, Claude: Yes, it's fixed."
    settle(frame)
    assert "Claude: Yes, it's fixed." in frame.session_list.GetStringSelection()


def test_removing_last_message_forgets_what_was_read(frame, env, monkeypatch):
    from thechatplace import hub
    _last_message_transcripts(env)
    _choose_columns(monkeypatch, ["title", "last_message"])
    frame.on_session_columns()
    settle(frame)
    assert hub.LAST_MESSAGES.reads >= 1
    hub._NO_TRANSCRIPT["own:somewhere"] = (1, 0.0)
    _choose_columns(monkeypatch, ["title", "status"])
    frame.on_session_columns()
    assert hub.LAST_MESSAGES._entries == {} and hub._NO_TRANSCRIPT == {}
    assert not any("Claude:" in r for r in frame.session_list.GetStrings())


def test_your_working_row_waits_in_a_kept_order_and_changes_for_a_rename(frame, env,
                                                                          monkeypatch):
    """The same rule when a row leaves the list in the same refresh (rows
    kept in place), and a change other than the message, such as a new
    name, rewrites the row with the newest message."""
    from thechatplace.sessions import WORKING
    frame.speech.session_fields = ["title", "status", "last_message"]
    quiet = _session(frame, "Quiet one")
    quiet.state, quiet.last_message = WORKING, "Claude: one"
    select(frame, "Quiet one")
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    frame._refresh_list_in_place(rewrite=True)
    assert frame.session_list.GetStringSelection() == "Quiet one, working, Claude: one"
    quiet.last_message = "Claude: two"
    blocked = _session(frame, "Blocked one")
    frame._snapshot.sessions.remove(blocked)  # gone in the same refresh
    frame._refresh_list_in_place()
    assert frame.session_list.GetCount() == 2
    assert frame.session_list.GetStringSelection() == "Quiet one, working, Claude: one"
    quiet.title = "Quieter one"
    frame._refresh_list_in_place()
    assert frame.session_list.GetStringSelection() == "Quieter one, working, Claude: two"


# -- where a new session works (#154) ---------------------------------------------------

def _git_repo(folder):
    import shutil
    import subprocess
    git = shutil.which("git")
    if not git:
        pytest.skip("git isn't installed")
    folder.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                  "--allow-empty", "-m", "first"],
                 ["branch", "older"]):
        subprocess.run([git, *args], cwd=folder, check=True, capture_output=True)
    return folder


def _read(dialog):
    """Wait for the background read of the dialog's folder."""
    assert pump(lambda: dialog._repo_known, timeout=15)


def test_new_session_offers_recent_folders(frame, env, fake_runner, monkeypatch):
    from thechatplace.ui import main_frame
    old, new = env["tmp"] / "old", env["tmp"] / "new"
    old.mkdir()
    new.mkdir()
    frame.store.add(OwnSession("own-old", "Old", str(old), last_activity_ms=1000))
    frame.store.add(OwnSession("own-new", "New", str(new), last_activity_ms=now_ms() + 5000))
    monkeypatch.setattr(platform_paths, "default_projects_root", lambda: env["tmp"])
    monkeypatch.setattr(platform_paths, "clone_root", lambda: env["tmp"] / "GitHub")
    seen = {}

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            seen["choices"] = list(self.folder.GetStrings())
            seen["value"] = self.folder.GetValue()
            seen["roots"] = (self._projects_root, self._clone_root)
            return wx.ID_CANCEL
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    frame.on_new_session()
    # Newest first; C:\G\Scratch (the frame's own) doesn't exist, so isn't offered.
    assert seen["choices"] == [str(new), str(old)]
    assert seen["value"] == str(new)
    assert seen["roots"] == (str(env["tmp"]), str(env["tmp"] / "GitHub"))


def test_recent_folders_are_offered_unchecked_if_checking_is_slow(frame, env, fake_runner,
                                                                   monkeypatch):
    from thechatplace import workplaces
    from thechatplace.ui import main_frame
    real = workplaces.recent_folders

    def slow(sessions, exists=None, **kwargs):
        if exists is None:
            time.sleep(3)  # a network drive that's gone
            return []
        return real(sessions, exists=exists, **kwargs)
    monkeypatch.setattr(workplaces, "recent_folders", slow)
    seen = {}

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            seen["choices"] = list(self.folder.GetStrings())
            seen["value"] = self.folder.GetValue()
            return wx.ID_CANCEL
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    started = time.monotonic()
    frame.on_new_session()
    assert time.monotonic() - started < 2.5
    assert "C:\\G\\Scratch" in seen["choices"]  # not checked, so offered
    # ...but not started at, as it may be gone: the projects folder is there.
    assert seen["value"] == str(platform_paths.default_projects_root())


def test_arrowing_through_recent_folders_reads_only_the_one_stopped_on(frame, tmp_path,
                                                                       monkeypatch):
    from thechatplace.ui import dialogs
    from thechatplace.ui.dialogs import NewSessionDialog
    folders = [str(tmp_path / name) for name in ("a", "b", "c")]
    dialog = NewSessionDialog(frame, "C:\\G", recent=folders)
    try:
        _read(dialog)
        read, timers = [], []

        class Timer:
            def __init__(self, ms, fn):
                self.ms, self.fn, self.running = ms, fn, True
                timers.append(self)

            def Stop(self):
                self.running = False
        monkeypatch.setattr(dialogs.wx, "CallLater", Timer)
        monkeypatch.setattr(dialog, "refresh_repo",
                            lambda: read.append(dialog.folder.GetValue()))
        for index in (1, 2):
            dialog.folder.SetSelection(index)
            dialog.folder.GetEventHandler().ProcessEvent(
                wx.CommandEvent(wx.wxEVT_COMBOBOX, dialog.folder.GetId()))
        assert read == []  # not yet: still arrowing
        assert [x.running for x in timers] == [False, True] and timers[-1].ms >= 200
        timers[-1].fn()
        assert read == [folders[2]]
    finally:
        dialog.Destroy()


def test_new_session_without_recent_folders_starts_at_the_root(frame):
    from thechatplace.ui.dialogs import NewSessionDialog, WORK_IN_FOLDER
    dialog = NewSessionDialog(frame, "C:\\G")
    try:
        assert dialog.folder.GetValue() == "C:\\G" and dialog.folder.GetCount() == 0
        _read(dialog)
        assert dialog.work_in.GetSelection() == WORK_IN_FOLDER
        assert dialog.work_in.GetString(WORK_IN_FOLDER) == \
            "The folder as it is (not a git repository, so no worktree)"
        assert not dialog.branch.IsEnabled()
        # Tab order: folder, Browse, From GitHub, Work in, Branch, Title.
        order = list(dialog.GetChildren())
        assert order.index(dialog.folder) < order.index(dialog.github_btn) < \
            order.index(dialog.work_in) < order.index(dialog.branch) < \
            order.index(dialog.title_text)
    finally:
        dialog.Destroy()


def test_a_worktree_is_refused_at_once_outside_a_repository(frame, env):
    from thechatplace.ui.dialogs import NewSessionDialog, WORK_IN_FOLDER, WORK_IN_WORKTREE
    plain = env["tmp"] / "plain"
    plain.mkdir()
    dialog = NewSessionDialog(frame, str(plain), feedback=env["feedback"].append)
    try:
        _read(dialog)
        dialog.work_in.SetSelection(WORK_IN_WORKTREE)
        dialog._on_work_in()
        assert dialog.work_in.GetSelection() == WORK_IN_FOLDER
        assert not dialog.branch.IsEnabled()
        assert env["feedback"][-1] == ("That folder isn't in a git repository, so it can't "
                                       "have a worktree.")
    finally:
        dialog.Destroy()


def test_a_stale_read_of_another_folder_is_dropped(frame, env):
    from thechatplace.ui.dialogs import NewSessionDialog
    from thechatplace.workplaces import RepoInfo
    plain = env["tmp"] / "plain"
    plain.mkdir()
    dialog = NewSessionDialog(frame, str(plain))
    try:
        _read(dialog)
        stale = RepoInfo(top="x", main="x", common="x/.git", rel="", branch="old",
                         branches=["old"], busy=[], remotes=[])
        dialog._repo_read("C:\\some\\other\\folder", stale)
        assert dialog._repo is None and dialog.branch.GetCount() == 0
    finally:
        dialog.Destroy()
    dialog._repo_read(str(plain), None)  # after it closed: nothing happens


def test_new_session_in_a_new_worktree(frame, env, fake_runner, monkeypatch):
    from thechatplace.ui import main_frame
    repo = _git_repo(env["tmp"] / "Repo")
    monkeypatch.setattr(platform_paths, "default_projects_root", lambda: env["tmp"])
    seen = {}

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            self.set_folder(str(repo))
            _read(self)
            seen["here"] = self.work_in.GetString(0)
            seen["branches"] = list(self.branch.GetStrings())
            self.work_in.SetSelection(1)
            self._on_work_in()
            seen["branch_enabled"] = self.branch.IsEnabled()
            self.branch.SetValue("fix/the-thing")
            self.message.SetValue("Fix it")
            self._on_ok(None)
            seen["start_while_making"] = self.start_btn.IsEnabled()
            assert pump(lambda: self.GetReturnCode() == wx.ID_OK, timeout=30)
            return self.GetReturnCode()
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    frame.on_new_session()
    # main is checked out in the folder itself, so only "older" is offered.
    assert seen == {"here": "The folder as it is, on main", "branches": ["older"],
                    "branch_enabled": True, "start_while_making": False}
    assert env["feedback"][0] == "Making the worktree."
    worktree = repo / ".claude" / "worktrees" / "fix-the-thing"
    assert worktree.is_dir()
    runner = fake_runner.instances[-1]
    assert runner.cwd == str(worktree) and runner.prompt == "Fix it"
    session_id = runner.command[runner.command.index("--session-id") + 1]
    assert frame.store.get(session_id).cwd == str(worktree)


def test_worktree_problems_are_said_and_cancel_waits_for_one_being_made(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import NewSessionDialog
    boxes = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text) or wx.OK)
    repo = _git_repo(env["tmp"] / "Repo")
    dialog = NewSessionDialog(frame, str(repo), feedback=env["feedback"].append)
    try:
        _read(dialog)
        dialog.message.SetValue("hi")
        dialog.work_in.SetSelection(1)
        dialog._on_work_in()
        dialog.branch.SetValue("-bad")
        dialog._on_ok(None)
        assert pump(lambda: boxes, timeout=15)
        assert "can't be a branch name" in boxes[-1] and dialog.start_btn.IsEnabled()
        assert dialog.GetReturnCode() != wx.ID_OK
        dialog.branch.SetValue("main")
        dialog._on_ok(None)
        assert pump(lambda: len(boxes) == 2, timeout=15)
        assert "already checked out in another folder" in boxes[-1]
        assert not (repo / ".claude").exists()
        # While one is being made, Cancel says so and stays.
        dialog._making = True
        event = wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_CANCEL)
        skipped = []
        event.Skip = lambda skip=True: skipped.append(skip)
        dialog._on_cancel(event)
        assert env["feedback"][-1] == "Still making the worktree." and not skipped
        dialog._making = False
        # The folder as it is: no worktree, the folder itself.
        dialog.work_in.SetSelection(0)
        dialog._on_ok(None)
        assert dialog.GetReturnCode() == wx.ID_OK
        assert dialog.values()[0] == str(repo)
    finally:
        dialog.Destroy()


def test_continue_here_has_no_folder_choices(frame):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, "C:\\G\\Repo", continue_from="Work")
    try:
        assert not dialog.folder.IsEditable()
        for control in (dialog.github_btn, dialog.work_in, dialog.branch):
            assert not control.IsShown()
    finally:
        dialog.Destroy()


def _repo_dialog(frame, root, said):
    from thechatplace.ui.dialogs import GitHubRepoDialog
    return GitHubRepoDialog(frame, "gh", str(root), said.append)


def test_from_github_lists_filters_and_uses_a_clone(frame, env, monkeypatch):
    import sys
    from pathlib import Path
    from thechatplace import workplaces
    monkeypatch.setattr(workplaces, "_gh", lambda gh: [
        sys.executable, str(Path(__file__).resolve().parent / "fake_gh.py")])
    monkeypatch.setattr(workplaces, "list_github_repos", lambda gh: [])
    said = []
    dialog = _repo_dialog(frame, env["tmp"], said)
    try:
        assert pump(lambda: said == ["0 repositories."])  # the background list
        assert dialog.search.GetName().endswith(f"Clones go into {env['tmp']}")
        dialog._loaded = False
        dialog._filter()
        assert dialog.list.GetString(0) == "Loading your repositories…"
        said.clear()
        dialog.loaded([workplaces.GitHubRepo("me/Alpha", "First tool"),
                       workplaces.GitHubRepo("me/Beta", "Second, like me/Alpha", True)])
        assert said == ["2 repositories."]
        assert dialog.list.GetStrings() == ["me/Alpha: First tool",
                                            "me/Beta, private: Second, like me/Alpha"]
        dialog.search.SetValue("second")
        assert dialog.list.GetStrings() == ["me/Beta, private: Second, like me/Alpha"]
        assert dialog.list_label.GetLabel() == "&Repositories (1 of 2):"
        # owner/name is that repository, not one that mentions it.
        dialog.search.SetValue("me/alpha")
        assert dialog.list.GetStrings() == ["me/Alpha: First tool"]
        dialog.search.SetValue("someone/Else")
        assert dialog.list.GetString(0) == "Not one of yours: Enter uses someone/Else."
        assert dialog.chosen_repo() == "someone/Else"
        dialog.search.SetValue("nothing here")
        dialog.choose()
        assert said[-1] == "Type owner/name, or choose one of your repositories."
        said.clear()
        dialog.search.SetValue("beta")
        dialog.choose()
        assert not dialog.search.IsEnabled()  # cloning
        assert pump(lambda: dialog.folder is not None, timeout=30)
        assert dialog.folder == env["tmp"] / "Beta" and dialog.GetReturnCode() == wx.ID_OK
        assert said == [f"Cloning me/Beta into {env['tmp'] / 'Beta'}. Cancel stops it.",
                        "Cloned me/Beta."]
    finally:
        dialog.Destroy()
    # Again: already there, so it's used as it is.
    said.clear()
    dialog = _repo_dialog(frame, env["tmp"], said)
    try:
        dialog.loaded([workplaces.GitHubRepo("me/Beta")])
        dialog.choose()
        assert dialog.folder == env["tmp"] / "Beta"
        assert said[-1] == f"me/Beta is already in {env['tmp'] / 'Beta'}."
    finally:
        dialog.Destroy()


def test_from_github_cancel_stops_the_clone(frame, env, monkeypatch):
    import sys
    from pathlib import Path
    from thechatplace import workplaces
    monkeypatch.setattr(workplaces, "_gh", lambda gh: [
        sys.executable, str(Path(__file__).resolve().parent / "fake_gh.py")])
    monkeypatch.setattr(workplaces, "list_github_repos", lambda gh: [])
    monkeypatch.setenv("FAKE_GH_MODE", "hang")
    dialog = _repo_dialog(frame, env["tmp"], [])
    try:
        dialog.loaded([workplaces.GitHubRepo("me/Slow")])
        dialog.choose()
        clone = dialog._clone
        event = wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_CANCEL)
        dialog._on_cancel(event)
        assert dialog._clone is None and dialog.GetReturnCode() == wx.ID_CANCEL
        clone.wait(15)
        assert not clone.staging.exists() and not (env["tmp"] / "Slow").exists()
    finally:
        dialog.Destroy()
    wx.GetApp().ProcessPendingEvents()  # the cancelled clone's answer, to a closed dialog


def test_from_github_never_touches_a_folder_that_is_something_else(frame, env, monkeypatch):
    from thechatplace import workplaces
    monkeypatch.setattr(workplaces, "list_github_repos", lambda gh: [])
    boxes = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text) or wx.OK)
    (env["tmp"] / "Taken").mkdir()
    dialog = _repo_dialog(frame, env["tmp"], [])
    try:
        dialog.loaded([], "To get started with GitHub CLI, please run: gh auth login")
        assert "gh auth login" in dialog.status.GetLabel()
        assert dialog.list.GetStrings() == [
            "Couldn't list your repositories. To get started with GitHub CLI, please run: "
            "gh auth login"]
        dialog.search.SetValue("me/Taken")
        dialog.choose()
        assert dialog.folder is None and "isn't a clone of me/Taken" in boxes[-1]
        assert list((env["tmp"] / "Taken").iterdir()) == []
    finally:
        dialog.Destroy()


def test_from_github_without_gh_says_how_to_get_it(frame, monkeypatch):
    from thechatplace import workplaces
    from thechatplace.ui.dialogs import NewSessionDialog
    monkeypatch.setattr(workplaces, "find_gh", lambda: None)
    boxes = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text) or wx.OK)
    dialog = NewSessionDialog(frame, "C:\\G")
    try:
        dialog.on_github()
        assert "cli.github.com" in boxes[-1]
    finally:
        dialog.Destroy()


# -- background work keeps the turn open (#161) ----------------------------------------------


def _answered_with_background(frame, runner, text="Started the build.", tasks=("Windows build",),
                              denials=()):
    runner.waiting_on_background = True
    runner.background_tasks = list(tasks)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "waiting", text=text, denials=list(denials), data={"tasks": list(tasks)}))


def test_a_reply_with_background_work_is_said_and_the_turn_stays_open(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    _answered_with_background(frame, runner)
    assert env["spoken"][-1] == ("Hub probe replied. Started the build. "
                                 "Still running in the background: Windows build.")
    assert frame._runners["own-1"] is runner  # still running: Stop still ends it
    assert frame.turn_status.GetLabel() == (
        "Claude answered. Still running in the background: Windows build. "
        "What you send goes to Claude now.")
    frame.on_turn_status()
    assert env["feedback"][-1] == (
        "Hub probe: Claude answered. Still running in the background: Windows build. "
        "The turn has run for 1 minute 15 seconds; Claude carries on when the work finishes.")
    # When Claude carries on and finishes, the turn ends as any turn does.
    runner.waiting_on_background = False
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Built."))
    assert env["spoken"][-1] == "Hub probe replied. Built."
    assert "own-1" not in frame._runners


def test_background_work_ending_with_no_new_answer_isnt_notified_again(frame, env, fake_runner,
                                                                      monkeypatch):
    monkeypatch.setattr(frame, "_app_is_active", lambda: False)
    runner = _start(frame, fake_runner)
    _answered_with_background(frame, runner)
    before = len(env["notified"])
    runner.waiting_on_background = False
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent(
        "finished", data={"after_background": True}))
    assert "own-1" not in frame._runners
    assert len(env["notified"]) == before
    # A turn that ends with an answer is still notified.
    runner = _start(frame, fake_runner)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Built."))
    assert env["notified"][-1] == ("Hub probe finished", "Built.", "own:own-1")


def test_send_while_waiting_on_background_work_goes_straight_to_claude(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    _answered_with_background(frame, runner)
    frame.reply_text.SetValue("Meanwhile, tidy the README")
    frame.on_send()
    assert runner.follow_ups == [("Meanwhile, tidy the README", [])]
    assert frame._queued == {}
    assert frame.reply_text.GetValue() == ""
    assert env["feedback"][-1] == "Sent to Hub probe: Meanwhile, tidy the README."
    assert len(fake_runner.instances) == 1
    # Claude is on it now: the next message queues as usual.
    frame.reply_text.SetValue("And then this")
    frame.on_send()
    assert frame._queued == {"own-1": ["And then this"]}


def test_messages_queued_before_the_reply_go_in_when_claude_waits(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    frame.reply_text.SetValue("second")
    frame.on_send()
    assert frame._queued == {"own-1": ["second"]}
    _answered_with_background(frame, runner)
    assert runner.follow_ups == [("second", None)]
    assert frame._queued == {}
    assert env["feedback"][-1] == "Sent your queued message. Hub probe is working."


def test_a_queued_message_stays_queued_if_claude_already_moved_on(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    frame.reply_text.SetValue("second")
    frame.on_send()
    runner.send_follow_up = lambda prompt, images=None: False
    _answered_with_background(frame, runner)
    assert frame._queued == {"own-1": ["second"]}


def test_refusals_said_with_background_work_are_not_said_again(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    _answered_with_background(frame, runner, denials=["Write a"])
    assert "1 tool was refused: Write a" in env["spoken"][-1]
    assert env["spoken"][-1].endswith("Still running in the background: Windows build.")
    runner.waiting_on_background = False
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("finished", text="Built."))
    assert env["spoken"][-1] == "Hub probe replied. Built."
    # Not said again, but the session is still left needing you.
    own = frame.store.get("own-1")
    assert own.state == "needs you" and own.detail == "1 tool was refused"


def test_the_list_says_a_session_answered_and_is_waiting_on_background_work(frame, env,
                                                                         fake_runner):
    runner = _start(frame, fake_runner)
    _answered_with_background(frame, runner)

    def row():
        return next((frame.session_list.GetString(i)
                     for i in range(frame.session_list.GetCount())
                     if frame.session_list.GetString(i).startswith("Hub probe")), "")
    assert pump(lambda: "answered, background work running" in row())


def test_a_background_update_changes_the_turn_status(frame, env, fake_runner):
    runner = _start(frame, fake_runner)
    _answered_with_background(frame, runner, tasks=("Build", "Watch CI"))
    assert "Build; Watch CI." in frame.turn_status.GetLabel()
    runner.background_tasks = ["Watch CI"]
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("background", data={"tasks": ["Watch CI"]}))
    assert "background: Watch CI. What" in frame.turn_status.GetLabel()


def test_send_now_on_a_queued_message_while_waiting_sends_it_without_stopping(frame, env,
                                                                          fake_runner):
    runner = _start(frame, fake_runner)
    frame.reply_text.SetValue("second")
    frame.on_send()
    runner.waiting_on_background = True
    frame.chat_list.SetSelection(frame.chat_list.GetCount() - 1)
    frame.send_queued_now()
    assert runner.follow_ups == [("second", None)]
    assert not hasattr(runner, "sent_now")
    assert frame._queued == {}
    assert env["feedback"][-1] == "Sent now to Hub probe."



# -- permission mode and effort (#189) --------------------------------------------------------


def _chooser(monkeypatch, frame, pick):
    offered = []

    def choose(title, prompt, choices, selection=None):
        offered.append((title, prompt, choices, selection))
        if pick is None:
            return None
        return [c.split(" (now)")[0] for c in choices].index(pick)
    monkeypatch.setattr(frame, "_choose", choose)
    return offered


def test_change_permission_mode_for_the_next_turns(frame, env, fake_runner, monkeypatch):
    from thechatplace.claude_cli import PERMISSION_MODES
    select(frame, "Hub probe")
    frame.on_open_session()
    plan = dict(PERMISSION_MODES)["plan"]
    offered = _chooser(monkeypatch, frame, plan)
    frame.on_change_permission_mode()
    assert frame.store.get("own-1").permission_mode == "plan"
    assert env["feedback"][-1] == "Permission mode for Hub probe: plan, from its next turn."
    title, prompt, choices, selection = offered[0]
    assert title == "Change Permission Mode"
    assert prompt == "Permission mode for Hub probe, now auto:"
    assert choices[selection].startswith("Auto:") and choices[selection].endswith("(now)")
    assert frame.session_heading.GetLabel().endswith("on the default model, plan mode.")
    frame.reply_text.SetValue("next")
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert command[command.index("--permission-mode") + 1] == "plan"


def test_a_change_during_a_turn_says_it_waits_for_the_next(frame, env, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    _chooser(monkeypatch, frame, "High")
    frame.on_change_effort()
    assert frame.store.get("own-1").effort == "high"
    assert env["feedback"][-1] == ("Effort for Hub probe: high effort, from its next turn; the "
                                   "turn running now carries on as it started.")


def test_choosing_the_same_mode_or_cancelling_changes_nothing(frame, env, monkeypatch):
    from thechatplace.claude_cli import PERMISSION_MODES
    select(frame, "Hub probe")
    frame.on_open_session()
    before = list(env["feedback"])
    _chooser(monkeypatch, frame, dict(PERMISSION_MODES)["auto"])
    frame.on_change_permission_mode()
    _chooser(monkeypatch, frame, None)
    frame.on_change_permission_mode()
    frame.on_change_effort()
    assert frame.store.get("own-1").permission_mode == "auto"
    assert frame.store.get("own-1").effort == ""
    assert env["feedback"] == before


def test_change_effort_goes_on_every_turn(frame, env, fake_runner, monkeypatch):
    select(frame, "Hub probe")
    frame.on_open_session()
    offered = _chooser(monkeypatch, frame, "Extra high")
    frame.on_change_effort()
    assert frame.store.get("own-1").effort == "xhigh"
    assert env["feedback"][-1] == "Effort for Hub probe: extra high effort, from its next turn."
    _title, prompt, choices, selection = offered[0]
    assert prompt.startswith("Effort for Hub probe, now the default effort. ")
    assert choices[selection] == "Default (your Claude Code setting) (now)"
    assert frame.session_heading.GetLabel().endswith(
        "on the default model at extra high effort.")
    frame.reply_text.SetValue("next")
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert command[command.index("--effort") + 1] == "xhigh"
    # Back to the default: no --effort at all.
    _chooser(monkeypatch, frame, "Default (your Claude Code setting)")
    frame.on_change_effort()
    assert frame.store.get("own-1").effort == ""


def test_mode_and_effort_of_a_desktop_or_terminal_session_are_set_there(frame, env, monkeypatch):
    offered = _chooser(monkeypatch, frame, "High")
    select(frame, "Quiet one")
    frame.on_open_session()
    frame.on_change_permission_mode()
    assert env["feedback"][-1] == ("Quiet one is a desktop app session: its permission mode "
                                   "is set in the desktop app.")
    frame.on_change_effort()
    assert env["feedback"][-1] == ("Quiet one is a desktop app session: its effort is set in "
                                   "the desktop app.")
    assert offered == []


def test_new_session_dialog_offers_effort(frame):
    from thechatplace.claude_cli import EFFORTS
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, "C:\\G")
    try:
        assert dialog.effort_choice.GetStringSelection() == "Default (your Claude Code setting)"
        assert list(dialog.effort_choice.GetStrings()) == [label for _v, label in EFFORTS]
        assert dialog.effort() == ""
        dialog.effort_choice.SetStringSelection("Max")
        assert dialog.effort() == "max"
        labels = [c.GetLabel() for c in dialog.GetChildren() if isinstance(c, wx.StaticText)]
        assert "&Effort:" in labels
        order = list(dialog.GetChildren())
        assert order.index(dialog.model) < order.index(dialog.effort_choice) < \
            order.index(dialog.mode)
    finally:
        dialog.Destroy()


def test_new_session_starts_with_the_chosen_effort(frame, env, fake_runner, monkeypatch):
    from thechatplace.ui import main_frame
    seen = {}

    class Dialog:
        def __init__(self, *a, **k):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def values(self):
            return "C:\\G\\New", "Effortful", "auto", "hello", ""

        def effort(self):
            return "low"

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "NewSessionDialog", Dialog)
    frame.on_new_session()
    command = fake_runner.instances[-1].command
    assert command[command.index("--effort") + 1] == "low"
    own = [s for s in frame.store.all() if s.title == "Effortful"][0]
    assert own.effort == "low"



def test_a_new_id_while_the_list_is_open_still_saves_the_change(frame, env, fake_runner,
                                                                 monkeypatch):
    """#189 review: Claude can give a new session another id while Change
    Effort's list is open; the change must go to the session, renamed."""
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("go")
    frame.on_send()

    def choose(title, prompt, choices, selection=None):
        frame._on_turn_event({"id": "own-1"}, "Hub probe",
                             TurnEvent("started", session_id="own-9"))
        return [c.split(" (now)")[0] for c in choices].index("Low")
    monkeypatch.setattr(frame, "_choose", choose)
    frame.on_change_effort()
    assert frame.store.get("own-9").effort == "low"
    assert env["feedback"][-1].endswith("the turn running now carries on as it started.")


def test_an_old_default_mode_reads_as_manual(frame, env, monkeypatch):
    frame.store.update("own-1", permission_mode="default", effort="high")
    select(frame, "Hub probe")
    frame.on_open_session()
    assert frame.session_heading.GetLabel().endswith(
        "on the default model at high effort, manual mode.")
    offered = _chooser(monkeypatch, frame, None)
    frame.on_change_permission_mode()
    _title, prompt, choices, selection = offered[0]
    assert prompt == "Permission mode for Hub probe, now manual:"
    assert choices[selection].startswith("Manual:") and choices[selection].endswith("(now)")


def test_approving_a_plan_starts_on_the_mode_chosen_for_the_session(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    frame.store.update("own-1", permission_mode="manual")
    seen = []

    class Plan:
        def __init__(self, parent, title, request, modes, default):
            seen.append(default)

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "PlanDialog", Plan)
    request = type("R", (), {"is_question": False, "is_plan": True})()
    frame._ask("Hub probe", request, frame.store.get("own-1"))
    frame.store.update("own-1", permission_mode="plan")
    frame._ask("Hub probe", request, frame.store.get("own-1"))
    assert seen == ["manual", "acceptEdits"]


def test_continue_here_keeps_the_chosen_effort(frame, env, fake_runner, monkeypatch):
    folder = env["tmp"] / "repo"
    folder.mkdir()
    add_desktop(env, "local_c", "cli-c", "Desktop work", cwd=str(folder))
    add_transcript(env, str(folder), "cli-c", [user_text("Earlier question")])
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Desktop work")
    frame.on_open_session()
    from thechatplace.ui import dialogs

    class Fills(dialogs.NewSessionDialog):
        def ShowModal(self):
            self.message.SetValue("Carry on")
            self.effort_choice.SetStringSelection("Max")
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.NewSessionDialog", Fills)
    frame.on_continue_here()
    command = fake_runner.instances[-1].command
    assert command[command.index("--effort") + 1] == "max"
    new_id = command[command.index("--session-id") + 1]
    assert frame.store.get(new_id).effort == "max"
    # A first turn that never got going is started again with it.
    frame._on_turn_event({"id": new_id}, "Desktop work (continued)",
                         TurnEvent("failed", text="not signed in", is_error=True))
    frame.reply_text.SetValue("again")
    frame.on_send()
    again = fake_runner.instances[-1].command
    assert "--fork-session" in again and again[again.index("--effort") + 1] == "max"


# -- View, Links (#190) --------------------------------------------------------------------------


def _links_session(frame, env, records=None):
    add_transcript(env, "C:\\G\\Repo", "cli-a", records or [
        user_text("Where are the notes? https://example.com/old"),
        assistant_block(text_block("Here: [release notes](https://example.com/notes) and "
                                   "[a file](file:///C:/secret.txt)."), "m1"),
        user_text("thanks")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)


def _links_dialog(monkeypatch, act):
    """LinksDialog, shown modally by doing ``act(dialog)`` instead."""
    from thechatplace.ui import dialogs, main_frame
    seen = {}

    class Shown(dialogs.LinksDialog):
        def ShowModal(self):
            seen["dialog"] = self
            seen["title"] = self.GetTitle()
            seen["rows"] = list(self.list.GetStrings())
            seen["label"] = self.list_label.GetLabel()
            act(self)
            return self.GetReturnCode()
    monkeypatch.setattr(main_frame, "LinksDialog", Shown)
    return seen


def test_links_lists_newest_first_and_opens_one(frame, env, monkeypatch):
    _links_session(frame, env)
    opened = []
    monkeypatch.setattr(main_frame_paths(), "open_url", opened.append)
    seen = _links_dialog(monkeypatch, lambda d: d.open_selected())
    frame.on_links()
    assert seen["title"] == "Links in Quiet one: 3"
    assert seen["label"] == "&Links (3):"
    assert seen["rows"] == [
        "release notes, example.com/notes. Claude, 1 message ago",
        "a file, file:///C:/secret.txt. Claude, 1 message ago, copy only",
        "example.com/old. You, 2 messages ago"]
    assert opened == ["https://example.com/notes"]
    assert env["feedback"][-1] == "Opening example.com/notes."


def test_a_link_that_cant_open_says_why_and_can_be_copied(frame, env, monkeypatch):
    _links_session(frame, env)
    opened, copied = [], []
    monkeypatch.setattr(main_frame_paths(), "open_url", opened.append)
    monkeypatch.setattr(frame, "_copy_text", lambda text: copied.append(text) or True)

    def act(dialog):
        dialog.list.SetSelection(1)
        dialog.open_selected()
        assert dialog.chosen is None  # stays open
        dialog.copy_selected()
        dialog.copy_selected(markdown=True)
    _links_dialog(monkeypatch, act)
    frame.on_links()
    assert opened == []
    assert env["feedback"][-3].startswith("A file: link isn't opened from here")
    assert env["feedback"][-2] == "Copied file:///C:/secret.txt."
    assert env["feedback"][-1] == "Copied file:///C:/secret.txt as Markdown."
    assert copied == ["file:///C:/secret.txt", "[a file](file:///C:/secret.txt)"]


def test_typing_narrows_the_links(frame, env, monkeypatch):
    _links_session(frame, env)

    def act(dialog):
        dialog.filter.SetValue("old")
        assert list(dialog.list.GetStrings()) == ["example.com/old. You, 2 messages ago"]
        assert dialog.list_label.GetLabel() == "&Links (1 of 3):"
        dialog.filter.SetValue("nothing like it")
        assert dialog.list.GetCount() == 0
        dialog.open_selected()
    _links_dialog(monkeypatch, act)
    frame.on_links()
    assert env["feedback"][-1] == "No links match."


def test_links_with_none_or_nothing_loaded_say_so(frame, env, monkeypatch):
    monkeypatch.setattr("thechatplace.ui.main_frame.LinksDialog",
                        lambda *a, **k: pytest.fail("no dialog"))
    frame.on_links()
    assert env["feedback"][-1] == "No session loaded."
    _links_session(frame, env, [user_text("no links here"),
                                assistant_block(text_block("none at all"), "m1")])
    frame.on_links()
    assert env["feedback"][-1] == "No links in this session."


def test_links_in_tool_results_count_only_with_tool_activity(frame, env, monkeypatch):
    _links_session(frame, env, [
        user_text("look"),
        assistant_block(tool_use_block("WebFetch", {"url": "https://tool.example.com/x"}, "t1"),
                        "m1"),
        assistant_block(text_block("done"), "m2")])
    monkeypatch.setattr("thechatplace.ui.main_frame.LinksDialog",
                        lambda *a, **k: pytest.fail("no dialog"))
    frame.on_links()
    assert env["feedback"][-1] == "No links in this session."
    frame._set_activity(True)
    # Not monkeypatch.undo(): that would take away the env fixture's stand-ins too.
    seen = _links_dialog(monkeypatch, lambda d: None)
    frame.on_links()
    assert any("tool.example.com/x" in row for row in seen["rows"])


def test_a_session_link_opens_here_not_in_the_browser(frame, env, monkeypatch):
    _links_session(frame, env, [
        user_text("see [Build](thechatplace://session/own-1)")])
    monkeypatch.setattr(main_frame_paths(), "open_url",
                        lambda url: pytest.fail("not the browser"))
    followed = []
    monkeypatch.setattr(frame, "open_link", followed.append)
    _links_dialog(monkeypatch, lambda d: d.open_selected())
    frame.on_links()
    assert followed == ["thechatplace://session/own-1"]


def main_frame_paths():
    from thechatplace.ui import main_frame
    return main_frame.platform_paths



def test_links_keys_in_the_list(frame, env, monkeypatch):
    """Typing in the list goes to the filter; Enter opens; Ctrl+C and
    Ctrl+Shift+C copy; Down in the filter comes back to the list."""
    _links_session(frame, env)
    opened, copied = [], []
    monkeypatch.setattr(main_frame_paths(), "open_url", opened.append)
    monkeypatch.setattr(frame, "_copy_text", lambda text: copied.append(text) or True)

    def key(dialog, code, control=False, shift=False, char=False):
        event = wx.KeyEvent(wx.wxEVT_CHAR if char else wx.wxEVT_KEY_DOWN)
        event.SetControlDown(control)
        event.SetShiftDown(shift)
        if char:
            event.SetUnicodeKey(code)
            dialog._on_list_char(event)
        else:
            event.SetKeyCode(code)
            dialog._on_list_key(event)

    def act(dialog):
        key(dialog, ord("C"), control=True)
        key(dialog, ord("C"), control=True, shift=True)
        key(dialog, ord("o"), char=True)
        assert dialog.filter.GetValue() == "o"
        down = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        down.SetKeyCode(wx.WXK_DOWN)
        dialog._on_filter_key(down)
        key(dialog, wx.WXK_RETURN)
    _links_dialog(monkeypatch, act)
    frame.on_links()
    assert copied == ["https://example.com/notes", "[release notes](https://example.com/notes)"]
    assert opened == ["https://example.com/notes"]  # "o" still matches it


def test_a_link_the_system_refuses_is_said_not_raised(frame, env, monkeypatch):
    _links_session(frame, env)

    def refuse(url):
        raise ValueError("embedded null character")
    monkeypatch.setattr(main_frame_paths(), "open_url", refuse)
    _links_dialog(monkeypatch, lambda d: d.open_selected())
    frame.on_links()
    assert "Couldn't open the link: embedded null character" in env["boxes"][-1]


# -- Find in All Sessions (#109) ------------------------------------------------------------------


def _find_all(frame, monkeypatch, text, act=None):
    """Run Find in All Sessions for ``text``; ``act(dialog)`` stands in for
    the results dialog (closing it if it doesn't choose)."""
    from thechatplace.ui import dialogs, main_frame
    seen = {}

    class Shown(dialogs.FindResultsDialog):
        def ShowModal(self):
            seen["title"] = self.GetTitle()
            seen["rows"] = list(self.list.GetStrings())
            seen["summary"] = self.summary.GetValue()
            if act:
                act(self)
            return self.GetReturnCode()
    monkeypatch.setattr(main_frame, "FindResultsDialog", Shown)
    monkeypatch.setattr(frame, "_ask_text", lambda *a: text)
    frame.on_find_all()
    assert pump(lambda: not frame._find_all_busy)
    if frame._find_all_waiting is not None:
        # A hidden window isn't active, so the results wait for Ctrl+Shift+S.
        assert env_said(frame).endswith("Press Ctrl+Shift+S to see them.")
        frame.on_find_all()
    return seen


def env_said(frame):
    return frame._last_announcement


def test_find_in_all_sessions_lists_matches_and_goes_to_one(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("Start the database migration"),
        assistant_block(text_block("Done."), "m1"),
        user_text("thanks")])
    add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("no match here")])
    seen = _find_all(frame, monkeypatch, "database MIGRATION", lambda d: d.go())
    assert seen["title"] == ('The Chat Place: 1 result for "database MIGRATION", '
                             "Find in All Sessions")
    assert len(seen["rows"]) == 1 and seen["rows"][0].startswith("Quiet one, You, ")
    assert seen["rows"][0].endswith(": Start the database migration")
    assert 'contains "database MIGRATION". Searched ' in seen["summary"]
    assert "had no transcript to read" in seen["summary"]  # not on disk
    assert pump(lambda: frame._chat_loaded and frame._open.title == "Quiet one")
    assert frame.chat_list.GetSelection() == 0
    assert env["feedback"][-1] == ("Loaded Quiet one. Message 1 of 3: You: Start the "
                                   "database migration")
    # Already loaded: it moves to the message without loading again.
    frame.chat_list.SetSelection(2)
    _find_all(frame, monkeypatch, "migration", lambda d: d.go())
    assert frame.chat_list.GetSelection() == 0


def test_find_in_all_sessions_with_no_match_says_so_without_a_dialog(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("hello")])
    monkeypatch.setattr("thechatplace.ui.main_frame.FindResultsDialog",
                        lambda *a, **k: pytest.fail("no dialog"))
    monkeypatch.setattr(frame, "_ask_text", lambda *a: "zebra")
    frame.on_find_all()
    assert env["feedback"][-1].startswith('Searching ')
    assert pump(lambda: not frame._find_all_busy)
    assert frame._last_announcement.startswith('No messages contain "zebra". Searched 1 session.')


def test_find_in_all_sessions_searches_only_the_listed_view(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("needle in a desktop session")])
    add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("needle in my own")])
    frame._set_session_filter("Hub probe")  # only that one listed
    seen = _find_all(frame, monkeypatch, "needle")
    assert [row.split(",")[0] for row in seen["rows"]] == ["Hub probe"]


def test_find_in_all_sessions_cancelled_or_busy(frame, env, monkeypatch):
    monkeypatch.setattr(frame, "_ask_text", lambda *a: None)
    before = list(env["feedback"])
    frame.on_find_all()
    assert env["feedback"] == before and not frame._find_all_busy
    frame._find_all_busy = True
    frame.on_find_all()  # pressed again while searching: it stops
    assert frame._find_all_cancel and env["feedback"][-1] == "Stopped searching."


def test_a_match_after_a_tool_call_in_the_same_reply_is_the_one_selected(frame, env,
                                                                         monkeypatch):
    """Text either side of a tool call is one reply, with one key: the
    result is found by its place, not its key (#109 review)."""
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("go"),
        assistant_block(text_block("before the tool"), "m2"),
        assistant_block(tool_use_block("Bash", {"command": "ls"}, "t1"), "m2"),
        assistant_block(text_block("after: the needle"), "m2")])
    _find_all(frame, monkeypatch, "needle", lambda d: d.go())
    assert pump(lambda: frame._chat_loaded and frame._open.title == "Quiet one")
    assert frame.chat_list.GetStringSelection().startswith("Claude: after: the needle")


def test_results_that_arrive_while_you_are_elsewhere_wait(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("the needle")])
    monkeypatch.setattr("thechatplace.ui.main_frame.FindResultsDialog",
                        lambda *a, **k: pytest.fail("not until asked"))
    monkeypatch.setattr(frame, "IsActive", lambda: False)  # gone to another app
    monkeypatch.setattr(frame, "_ask_text", lambda *a: "needle")
    frame.on_find_all()
    assert pump(lambda: not frame._find_all_busy)
    assert frame._find_all_waiting is not None
    assert frame._last_announcement == ('1 message in 1 session contains "needle". Searched 1 '
                                        "session. 2 had no transcript to read. Press "
                                        "Ctrl+Shift+S to see them.")



def test_results_open_at_once_when_you_are_still_there(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("the needle")])
    monkeypatch.setattr(frame, "IsActive", lambda: True)
    monkeypatch.setattr(frame, "_modal_open", lambda: False)
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    opened = []
    monkeypatch.setattr(frame, "_open_find_results", opened.append)
    monkeypatch.setattr(frame, "_ask_text", lambda *a: "needle")
    frame.on_find_all()
    assert pump(lambda: not frame._find_all_busy)
    assert len(opened) == 1 and frame._find_all_waiting is None


def test_results_wait_while_a_dialog_is_up_and_go_stale(frame, env, monkeypatch):
    from thechatplace.ui import main_frame
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("the needle")])
    monkeypatch.setattr(frame, "IsActive", lambda: True)
    monkeypatch.setattr(frame, "_modal_open", lambda: True)  # Settings, say
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.session_list))
    opened = []
    monkeypatch.setattr(frame, "_open_find_results", opened.append)
    monkeypatch.setattr(frame, "_ask_text", lambda *a: "needle")
    frame.on_find_all()
    assert pump(lambda: not frame._find_all_busy)
    assert opened == [] and frame._find_all_waiting is not None
    # Long after, Ctrl+Shift+S asks for a new search instead of showing them.
    when, results = frame._find_all_waiting
    frame._find_all_waiting = (when - main_frame.FIND_ALL_KEEP_S - 1, results)
    asked = []
    monkeypatch.setattr(frame, "_ask_text", lambda *a: asked.append(a) or None)
    frame.on_find_all()
    assert opened == [] and len(asked) == 1


def test_changing_the_view_drops_waiting_results(frame, env):
    frame._find_all_waiting = (0.0, object())
    frame.on_view("all")
    assert frame._find_all_waiting is None


def test_stopping_drops_the_results_and_a_late_error(frame, env, monkeypatch):
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("the needle")])
    monkeypatch.setattr("thechatplace.ui.main_frame.FindResultsDialog",
                        lambda *a, **k: pytest.fail("stopped"))
    monkeypatch.setattr(frame, "_ask_text", lambda *a: "needle")
    started = threading.Event()
    release = threading.Event()
    real = main_frame_module().search_sessions

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return real(*args, **kwargs)
    monkeypatch.setattr(main_frame_module(), "search_sessions", slow)
    frame.on_find_all()
    assert started.wait(5)
    frame.on_find_all()  # Ctrl+Shift+S again
    assert env["feedback"][-1] == "Stopped searching."
    frame.on_find_all()
    assert env["feedback"][-1] == "Stopping. It ends after the session it's reading."
    release.set()
    assert pump(lambda: not frame._find_all_busy)
    assert frame._find_all_waiting is None
    said = frame._last_announcement
    frame._find_all_failed(OSError("late"))
    assert frame._last_announcement == said  # nothing said after a stop


def test_progress_is_said_only_for_the_search_running_now(frame, env):
    frame._find_all_busy, frame._find_all_cancel = True, False
    frame._find_all_run, frame._find_all_done = 2, 7
    frame._say_find_all_progress(1, 30)  # an earlier search's timer
    assert not env["feedback"] or "Searched" not in env["feedback"][-1]
    assert frame._find_all_timer is None
    frame._say_find_all_progress(2, 30)
    assert env["feedback"][-1] == "Searched 7 of 30 sessions."
    assert frame._find_all_timer is not None
    frame.stop_timers()


def test_a_result_thats_a_hidden_tool_call_says_so(frame, env, monkeypatch):
    from thechatplace.search import Match
    add_transcript(env, "C:\\G\\Repo", "cli-a", [
        user_text("go"),
        assistant_block(tool_use_block("Bash", {"command": "grep needle"}, "t1"), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    tool = frame._chat_messages[1]
    frame._go_to_match(Match(frame._open.key, "Quiet one", tool.key, "Tool", "", "grep needle", 1))
    assert env["feedback"][-1] == ("1 message. The message found isn't in the list now; if it "
                                   "was a tool call, Ctrl+T shows them.")


def test_a_result_in_a_session_without_its_transcript_forgets_the_target(frame, env):
    from thechatplace.search import Match
    frame._go_to_match(Match(frame._list_keys[0], "x", "k", "You", "", "", 0))
    assert pump(lambda: frame._chat_loaded or frame._select_on_load is None)
    assert frame._select_on_load is None


def main_frame_module():
    from thechatplace.ui import main_frame
    return main_frame


# -- long labels wrap (#176) ----------------------------------------------------------


def test_a_wrapping_label_wraps_to_its_width_and_keeps_its_text(frame):
    from thechatplace.ui.dialogs import WrappingText
    dialog = wx.Dialog(frame, size=(400, 300))
    try:
        sizer = wx.BoxSizer(wx.VERTICAL)
        text = "A sentence long enough that it needs more than one line in a narrow dialog. " * 3
        note = WrappingText(dialog, text)
        sizer.Add(note, 0, wx.EXPAND | wx.ALL, 8)
        dialog.SetSizer(sizer)
        dialog.Layout()
        one_line = note.GetTextExtent("Ag").height
        assert note.GetSize().width <= 400
        assert note.GetBestSize().width <= note.GetSize().width + 1
        assert note.GetSize().height >= 2 * one_line  # wrapped onto several lines
        assert note.GetLabel() == text  # without the breaks Wrap put in
        note.set_text("Short.")
        dialog.Layout()
        assert note.GetLabel() == "Short." and note.GetSize().height < 2 * one_line
    finally:
        dialog.Destroy()


def test_remote_control_wraps_but_says_all_it_says(frame):
    from thechatplace.ui.dialogs import SettingsDialog
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        dialog.Layout()
        label = dialog.remote_control.GetLabel()
        # Several lines, so it fits; the same words, so it's read the same.
        assert label.count("\n") >= 1
        assert " ".join(label.split()) == (
            "Turn on Remote &Control for The Chat Place's sessions, so you can reach them "
            "from claude.ai and other devices. Their conversations are copied to claude.ai "
            "and kept there. (File, Remote Control changes one session.)")
        box = dialog.remote_control
        assert box.GetBestSize().width <= box.GetSize().width + 1
        assert box.GetRect().GetRight() <= dialog.GetClientSize().width
    finally:
        dialog.Destroy()


def test_usage_is_wide_enough_for_its_longest_line(frame):
    from thechatplace.ui.dialogs import UsageDialog
    long_line = ("Visual probe: Context: 1 tokens used; the window's size isn't known until "
                 "this session or another on the same model runs a turn here.")
    dialog = UsageDialog(frame, [long_line, "Five-hour limit: 12% used."], lambda t, o: None)
    try:
        dialog.Layout()
        needed = dialog.GetTextExtent(long_line).width
        screen = wx.Display(0).GetClientArea().width
        assert dialog.list.GetSize().width >= min(needed, screen - 100)
    finally:
        dialog.Destroy()


def test_settings_is_no_taller_than_its_controls(frame):
    from thechatplace.ui.dialogs import SettingsDialog
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        dialog.Layout()
        spare = dialog.GetClientSize().height - dialog.GetSizer().GetMinSize().height
        assert 0 <= spare <= 2
    finally:
        dialog.Destroy()


def _questions(count):
    from thechatplace.claude_cli import PermissionRequest
    return PermissionRequest("q1", "AskUserQuestion", {"questions": [
        {"header": f"Q{n}", "question": f"Question number {n}?",
         "options": [{"label": "Yes"}, {"label": "No"}]} for n in range(count)]},
        suggestions=[])


def test_a_few_questions_leave_no_empty_band(frame):
    from thechatplace.ui.dialogs import QuestionDialog
    dialog = QuestionDialog(frame, "Probe", _questions(2))
    try:
        dialog.Layout()
        panel = dialog.GetChildren()[0]
        content = panel.GetSizer().GetMinSize().height
        # The panel is as tall as its questions (with the sizer's border).
        assert 0 <= panel.GetSize().height - content <= 10
        assert dialog.GetSize().height < 560
    finally:
        dialog.Destroy()


def test_many_questions_keep_the_size_and_scroll(frame):
    from thechatplace.ui.dialogs import QuestionDialog
    dialog = QuestionDialog(frame, "Probe", _questions(12))
    try:
        dialog.Layout()
        panel = dialog.GetChildren()[0]
        assert dialog.GetSize().height == 560
        assert panel.GetVirtualSize().height > panel.GetClientSize().height  # it scrolls
        buttons = [c for c in dialog.GetChildren() if isinstance(c, wx.Button)]
        assert all(b.GetRect().GetBottom() <= dialog.GetClientSize().height for b in buttons)
    finally:
        dialog.Destroy()


def test_the_messages_label_keeps_its_whole_text_and_key(env):
    from thechatplace.ui.main_frame import MainFrame
    long_title = "A session whose title goes on and on " * 6
    add_desktop(env, "local_a", "cli-a", long_title)
    window = MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"),
                       check_updates_at_start=False)
    try:
        assert pump(lambda: window.session_list.GetCount() == 1)
        select(window, "A session")
        window.on_open_session()
        assert pump(lambda: window._chat_loaded)
        label = window.messages_label.GetLabel()
        # Shown with "..." when it doesn't fit, but the label (what screen
        # readers read, and the Alt+M key) is whole.
        assert label.startswith("&Messages in " + long_title.strip())
        assert window.messages_label.HasFlag(wx.ST_ELLIPSIZE_END)
        heading = window.session_heading
        assert heading.GetLabel().startswith(long_title.strip())
        window.SetSize((1000, 720))
        window.Layout()
        assert heading.GetSize().width <= window.GetClientSize().width
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        wx.GetApp().ProcessPendingEvents()
        window.Destroy()
        wx.GetApp().ProcessPendingEvents()


# -- a first run (#182) -----------------------------------------------------------------


def test_with_no_sessions_the_messages_say_how_to_start_one(env):
    from thechatplace.ui.main_frame import NO_SESSIONS_YET, MainFrame
    store = OwnSessionStore(env["tmp"] / "own.json")
    window = MainFrame(store=store, check_updates_at_start=False)
    try:
        assert pump(lambda: not window._first_snapshot)
        settle(window)
        assert window.session_list.GetCount() == 0
        assert window.chat_list.GetStrings() == [NO_SESSIONS_YET]
        assert "File, New Session (" in NO_SESSIONS_YET
        # The first session arrives: the usual line again.
        add_desktop(env, "local_a", "cli-a", "First one")
        window.refresh_sessions(force=True)
        assert pump(lambda: window.session_list.GetCount() == 1)
        assert window.chat_list.GetStrings() == [
            "No session loaded. Choose one in the session list and press Enter."]
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        wx.GetApp().ProcessPendingEvents()
        window.Destroy()
        wx.GetApp().ProcessPendingEvents()




def test_hiding_every_session_is_not_a_first_run(frame, env, monkeypatch):
    from thechatplace.ui.main_frame import NO_SESSIONS_YET
    settle(frame)
    usual = frame.chat_list.GetStrings()
    for title in ("Blocked one", "Hub probe", "Quiet one"):
        select(frame, title)
        frame.on_hide()
        settle(frame)
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 0)
    settle(frame)
    # The list is empty, but there are sessions: hidden ones.
    assert frame.chat_list.GetStrings() == usual != [NO_SESSIONS_YET]


def test_a_loaded_session_is_left_alone_when_the_sessions_go(frame, env):
    from thechatplace.ui.main_frame import NO_SESSIONS_YET
    _load_reply(frame, env, "Only one message here.")
    before = frame.chat_list.GetStrings()
    frame._snapshot = dataclasses.replace(frame._snapshot, sessions=[])
    frame._sync_no_session_line()
    assert frame.chat_list.GetStrings() == before != [NO_SESSIONS_YET]


def test_when_the_last_session_goes_the_first_run_line_comes_back(env):
    import shutil
    from thechatplace.ui.main_frame import NO_SESSIONS_YET, MainFrame
    add_desktop(env, "local_a", "cli-a", "Only one")
    window = MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"),
                       check_updates_at_start=False)
    try:
        assert pump(lambda: window.session_list.GetCount() == 1)
        assert window.chat_list.GetStrings() != [NO_SESSIONS_YET]
        shutil.rmtree(env["desktop"] / "local_a")  # deleted in the desktop app
        window.refresh_sessions(force=True)
        assert pump(lambda: window.session_list.GetCount() == 0)
        settle(window)
        assert window.chat_list.GetStrings() == [NO_SESSIONS_YET]
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        wx.GetApp().ProcessPendingEvents()
        window.Destroy()
        wx.GetApp().ProcessPendingEvents()


def test_the_first_run_line_names_this_platform_s_key():
    from thechatplace.ui.main_frame import NO_SESSIONS_YET
    key = "Cmd+N" if wx.Platform == "__WXMAC__" else "Ctrl+N"
    assert f"File, New Session ({key})" in NO_SESSIONS_YET


# -- New Session and Continue Here fields (#178) ------------------------------------------


def test_new_session_folder_is_as_wide_as_the_other_fields(frame, tmp_path):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, str(tmp_path))
    try:
        dialog.Layout()
        # Browse and From GitHub moved under it, so it isn't squeezed.
        assert dialog.folder.GetSize().width == dialog.title_text.GetSize().width
    finally:
        dialog.Destroy()


def test_continue_here_shows_the_start_of_its_title_and_all_its_note(frame, tmp_path):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, str(tmp_path), continue_from="Fix the flaky upload test")
    try:
        assert dialog.title_text.GetValue() == "Fix the flaky upload test (continued)"
        # Laid out before it's shown, so the title box has its full width
        # when it's first drawn: at its first small size (110 pixels) it
        # showed only the title's end.
        assert dialog.title_text.GetSize().width == dialog.folder.GetSize().width
        assert dialog.title_text.GetSize().width > dialog.title_text.GetTextExtent(
            dialog.title_text.GetValue()).width
        note = dialog.note
        if wx.Platform == "__WXMSW__":  # elsewhere it counts paragraphs, not lines
            lines = note.GetNumberOfLines()
            assert lines >= 6  # two paragraphs, wrapped
            assert note.GetClientSize().height >= lines * note.GetCharHeight()
    finally:
        dialog.Destroy()
