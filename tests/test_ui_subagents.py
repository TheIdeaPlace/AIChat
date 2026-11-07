"""Subagents in the window (#468): Lists' Subagents kind, reading one's
conversation, their edits in Changed Files, the messages menu and the
announcement when one ends. These tests construct windows: run them in the
VM or CI only."""
import json
import os

import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import _lists_dialog, env, frame, select  # noqa: E402,F401
from fake_env import add_transcript, pump  # noqa: E402
from markers import windows_paths  # noqa: E402
from records import (agent_call, agent_completed, agent_launched, assistant_block,  # noqa: E402
                     lines, sidechain, text_block, tool_result, tool_use_block, user_text)
from thechatplace import lists, platform_paths, subagents  # noqa: E402
from thechatplace.speech import ANNOUNCE_FULL, ANNOUNCE_SUMMARY  # noqa: E402

CWD = "C:\\G\\Repo"


def _at(minute, second=0):
    return f"2026-10-06T12:{minute:02d}:{second:02d}.000Z"


def _reviewer_records(edit_path=None, finished=True):
    records = [user_text("Review the branch against main.", timestamp=_at(0)),
               assistant_block(tool_use_block("Bash", {"command": "git diff main"}, "tb1"), "s1",
                               timestamp=_at(1)),
               tool_result("tb1", "diff output", timestamp=_at(1, 5))]
    if edit_path:
        records += [assistant_block(tool_use_block("Edit", {"file_path": edit_path, "old_string": "a",
                                                            "new_string": "a\nb"}, "te1"), "s2",
                                    timestamp=_at(2)),
                    tool_result("te1", "Updated.", toolUseResult={"filePath": edit_path},
                                timestamp=_at(2, 5))]
    if finished:
        records.append(assistant_block(text_block("Two problems found.\n\nDetails follow."), "s3",
                                       timestamp=_at(4)))
    return records


def _session_with_subagent(frame, env, edit_path=None):
    """Quiet one, whose second turn handed a review to a code-reviewer."""
    parent = add_transcript(env, CWD, "cli-a", [
        user_text("First question", timestamp=_at(0)),
        assistant_block(text_block("First answer"), "m1", timestamp=_at(0)),
        user_text("Please review the branch", timestamp=_at(0)),
        agent_call("toolu_ag1", "code-reviewer", "Review the branch",
                   "Review the branch against main.", timestamp=_at(0)),
        agent_completed("toolu_ag1", "a1", "Two problems found.", "code-reviewer",
                        timestamp=_at(4)),
        assistant_block(text_block("The reviewer found two problems."), "m3", timestamp=_at(4))])
    folder = platform_paths.subagents_dir(parent)
    folder.mkdir(parents=True)
    (folder / "agent-a1.jsonl").write_text(
        "\n".join(lines(*[sidechain(r, "a1") for r in _reviewer_records(edit_path)])) + "\n",
        encoding="utf-8")
    # Written when the made-up records say, not after the parent heard it
    # finish: that would be it being sent on again.
    written = subagents._ms(_at(4)) / 1000
    os.utime(folder / "agent-a1.jsonl", (written, written))
    (folder / "agent-a1.meta.json").write_text(json.dumps({
        "agentType": "code-reviewer", "description": "Review the branch",
        "toolUseId": "toolu_ag1", "spawnDepth": 1}), encoding="utf-8")
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame._chat_subagents)
    return parent


def test_lists_subagents_and_reading_one(frame, env, monkeypatch):
    _session_with_subagent(frame, env)
    opened = {}

    def show_chat(dialog):
        opened.update(title=dialog.GetTitle(), rows=list(dialog.list.GetStrings()),
                      name=dialog.list.GetName(), selected=dialog.list.GetSelection(),
                      whole=dialog._item_text(dialog.list.GetSelection()))
        dialog.set_activity(True)
        opened["with_activity"] = list(dialog.list.GetStrings())
        copied = []
        dialog._copy = lambda text: copied.append(text) or True
        dialog.copy_selected()
        opened["copied"] = copied
        return wx.ID_CANCEL
    monkeypatch.setattr(frame, "_show_subagent_chat", show_chat)

    def act(page):
        page.activate()
        # Read in the background, and opened over Lists once it's read.
        assert pump(lambda: opened)
    seen = _lists_dialog(frame, monkeypatch, act)
    frame.on_subagents()
    assert (seen["category"], seen["scope"]) == ("subagents", "chat")
    assert seen["label"] == "&List: Subagents (1):"
    assert seen["rows"] == ["code-reviewer: Review the branch. Done, 2 messages, 1 tool call, "
                            "4 minutes"]
    assert "Prompt:\nReview the branch against main." in seen["detail"]
    assert seen["detail"].endswith("Final report:\nTwo problems found.")
    assert opened["title"] == "Subagent code-reviewer, in Quiet one"
    assert opened["rows"] == ["Claude: Review the branch against main.",
                              "code-reviewer: Two problems found."]
    assert opened["name"] == "code-reviewer's conversation, done"
    assert opened["selected"] == 1  # on the newest, as the messages list opens
    assert opened["whole"].startswith("code-reviewer: Two problems found.")
    assert "Details follow." in opened["whole"]
    assert opened["with_activity"][1] == "Tool: Bash: git diff main"
    assert opened["copied"] == ["code-reviewer:\nTwo problems found.\n\nDetails follow."]
    assert env["feedback"][-2:] == ["Showing tool activity.", "Message copied."]
    assert frame._subagent_open_id is None  # closed: the window says endings again


def test_the_messages_menu_offers_the_turns_subagents(frame, env, monkeypatch):
    _session_with_subagent(frame, env)
    frame.chat_list.SetSelection(frame.chat_list.GetCount() - 1)  # the reply of turn 2
    menu = frame._message_menu()
    labels = [item.GetItemLabelText() for item in menu.GetMenuItems()]
    menu.Destroy()
    assert "Subagent Conversations..." in labels
    frame.chat_list.SetSelection(0)  # turn 1 used none
    menu = frame._message_menu()
    labels = [item.GetItemLabelText() for item in menu.GetMenuItems()]
    menu.Destroy()
    assert "Subagent Conversations..." not in labels
    frame.chat_list.SetSelection(frame.chat_list.GetCount() - 1)
    seen = _lists_dialog(frame, monkeypatch, lambda page: None)
    frame.on_message_subagents()
    assert (seen["category"], seen["scope"]) == ("subagents", "message")
    assert len(seen["rows"]) == 1 and seen["rows"][0].startswith("code-reviewer:")


def test_the_view_menu_has_subagents(frame):
    view = frame.GetMenuBar().GetMenu(frame.GetMenuBar().FindMenu("View"))
    labels = [item.GetItemLabel() for item in view.GetMenuItems()]
    assert "Suba&gents..." in labels


@windows_paths
def test_changed_files_count_a_subagents_edits(frame, env, monkeypatch):
    target = "C:\\G\\Repo\\main.py"
    _session_with_subagent(frame, env, edit_path=target)
    seen = _lists_dialog(frame, monkeypatch, lambda page: None)
    frame.on_lists(lists.FILES, lists.SCOPE_CHAT)
    assert seen["rows"] == ["main.py, 1 line added, by code-reviewer"]
    assert seen["detail"].startswith(f"{target}: 1 line added, by code-reviewer.")


def test_a_subagent_ending_is_said_at_the_full_level_only(frame, env, monkeypatch):
    _session_with_subagent(frame, env)
    running = subagents.Subagent("a1", agent_type="code-reviewer", status=subagents.RUNNING)
    done = subagents.Subagent("a1", agent_type="code-reviewer", status=subagents.DONE)
    tracker = frame._subagent_tracker
    generation = frame._open_generation
    monkeypatch.setattr(frame.speech, "announce", ANNOUNCE_FULL)
    frame._apply_subagents(generation, tracker, [running])
    env["feedback"].clear()
    frame._apply_subagents(generation, tracker, [done])
    assert env["feedback"] == ["Subagent code-reviewer finished."]
    # Not twice, and not at the summary level.
    frame._apply_subagents(generation, tracker, [done])
    monkeypatch.setattr(frame.speech, "announce", ANNOUNCE_SUMMARY)
    frame._apply_subagents(generation, tracker, [running])
    frame._apply_subagents(generation, tracker, [done])
    assert env["feedback"] == ["Subagent code-reviewer finished."]
    # With its conversation open, the dialog says it instead.
    monkeypatch.setattr(frame.speech, "announce", ANNOUNCE_FULL)
    frame._apply_subagents(generation, tracker, [running])
    frame._subagent_open_id = "a1"
    frame._apply_subagents(generation, tracker, [done])
    frame._subagent_open_id = None
    assert env["feedback"] == ["Subagent code-reviewer finished."]


def test_a_running_subagents_conversation_grows_without_moving_you(frame, env, tmp_path):
    from thechatplace.ui import subagent_chat
    from thechatplace.ui.subagent_chat import SubagentChatDialog
    path = tmp_path / "agent-a1.jsonl"
    records = [sidechain(r, "a1") for r in _reviewer_records(finished=False)]
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")
    reader = subagents.open_conversation(path, "Explore")
    reader.refresh()
    state = {"sub": subagents.Subagent("a1", path=path, agent_type="Explore",
                                       status=subagents.RUNNING)}
    said = []
    dialog = SubagentChatDialog(frame, state["sub"], "Quiet one", reader,
                                read_message=lambda m, parent: None, copy=lambda t: True,
                                feedback=said.append, latest=lambda: state["sub"])
    try:
        assert list(dialog.list.GetStrings()) == ["Claude: Review the branch against main."]
        assert dialog.timer.IsRunning()
        assert dialog.list.GetName() == "Explore's conversation, running"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(sidechain(assistant_block(text_block("Halfway there."), "s9"),
                                              "a1")) + "\n")
        dialog.list.SetSelection(0)
        assert dialog.refresh()
        assert list(dialog.list.GetStrings())[-1] == "Explore: Halfway there."
        assert dialog.list.GetSelection() == 0  # you stay where you were
        state["sub"] = subagents.Subagent("a1", path=path, agent_type="Explore",
                                          status=subagents.DONE)
        dialog.refresh()
        assert said == ["Subagent Explore finished."]
        # Still read, slowly: Claude can send it on with SendMessage.
        assert dialog.timer.GetInterval() == subagent_chat.FINISHED_REFRESH_MS
        assert dialog.list.GetName() == "Explore's conversation, done"
        # Ctrl+T in the dialog: tool activity here only.
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("T"))
        event.SetControlDown(True)
        dialog._on_char_hook(event)
        assert dialog.show_activity and dialog.activity.GetValue()
        assert "Tool: Bash: git diff main" in dialog.list.GetStrings()
        assert not frame._show_activity
    finally:
        dialog.Destroy()


def _conversation_dialog(frame, tmp_path, records, status=subagents.DONE, latest=None):
    from thechatplace.ui.subagent_chat import SubagentChatDialog
    path = tmp_path / "agent-a1.jsonl"
    path.write_text("\n".join(lines(*[sidechain(r, "a1") for r in records])) + "\n",
                    encoding="utf-8")
    reader = subagents.open_conversation(path, "Explore")
    reader.refresh()
    sub = subagents.Subagent("a1", path=path, agent_type="Explore", status=status)
    said = []
    dialog = SubagentChatDialog(frame, sub, "Quiet one", reader,
                                read_message=lambda m, parent: None, copy=lambda t: True,
                                feedback=said.append, latest=latest)
    return dialog, path, said


def test_a_finished_subagent_sent_on_again_updates_and_says_when_it_ends(frame, env, tmp_path):
    from thechatplace.ui import subagent_chat
    state = {}
    dialog, path, said = _conversation_dialog(frame, tmp_path, _reviewer_records(),
                                              latest=lambda: state.get("sub"))
    try:
        assert dialog.timer.IsRunning()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(sidechain(user_text("One more thing."), "a1")) + "\n")
        assert dialog.refresh()  # a tick of the slow timer
        assert list(dialog.list.GetStrings())[-1] == "Claude: One more thing."
        state["sub"] = subagents.Subagent("a1", path=path, agent_type="Explore",
                                          status=subagents.RUNNING)
        dialog.refresh()
        assert dialog.timer.GetInterval() == subagent_chat.REFRESH_MS
        assert dialog.list.GetName() == "Explore's conversation, running"
        state["sub"] = subagents.Subagent("a1", path=path, agent_type="Explore",
                                          status=subagents.DONE)
        dialog.refresh()
        assert said == ["Subagent Explore finished."]
        assert dialog.timer.GetInterval() == subagent_chat.FINISHED_REFRESH_MS
    finally:
        dialog.Destroy()


def test_hiding_tool_activity_on_a_tool_row_stays_near_it(frame, env, tmp_path):
    records = _reviewer_records() + [assistant_block(text_block("And another thing."), "s4",
                                                     timestamp=_at(5))]
    dialog, _path, _said = _conversation_dialog(frame, tmp_path, records)
    try:
        dialog.set_activity(True)
        rows = list(dialog.list.GetStrings())
        tool_row = rows.index("Tool: Bash: git diff main")
        dialog.list.SetSelection(tool_row)
        dialog.set_activity(False)
        # The message before it, your place kept, not the last row.
        assert dialog.selected().text == "Review the branch against main."
        # A row still shown stays selected, either way.
        dialog.list.SetSelection(1)
        chosen = dialog.selected()
        dialog.set_activity(True)
        assert dialog.selected() is chosen
    finally:
        dialog.Destroy()


def test_hiding_tool_activity_with_nothing_before_the_row_goes_to_the_first(frame, env, tmp_path):
    records = [assistant_block(tool_use_block("Bash", {"command": "ls"}, "tb0"), "s0",
                               timestamp=_at(0)),
               user_text("Review the branch against main.", timestamp=_at(1)),
               assistant_block(text_block("Done."), "s9", timestamp=_at(2))]
    dialog, _path, _said = _conversation_dialog(frame, tmp_path, records)
    try:
        dialog.set_activity(True)
        dialog.list.SetSelection(0)
        dialog.set_activity(False)
        assert dialog.list.GetSelection() == 0
    finally:
        dialog.Destroy()


def test_return_presses_the_focused_button_on_a_mac(frame, env, tmp_path, monkeypatch):
    from thechatplace.ui import subagent_chat
    dialog, _path, _said = _conversation_dialog(frame, tmp_path, _reviewer_records())
    try:
        pressed = []
        monkeypatch.setattr(subagent_chat, "press_focused_button_on_a_mac",
                            lambda event: pressed.append(event) or True)

        class Key(wx.KeyEvent):
            skipped = False

            def Skip(self, skip=True):
                self.skipped = True
        event = Key(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_char_hook(event)
        assert pressed == [event] and not event.skipped
    finally:
        dialog.Destroy()


def test_a_conversation_is_read_in_the_background_and_opened_after(frame, env, monkeypatch):
    import threading
    _session_with_subagent(frame, env)
    sub = frame._chat_subagents[0]
    release, threads, opened, closed = threading.Event(), [], [], []
    real = subagents.open_conversation

    def held(path, agent_type=""):
        threads.append(threading.current_thread())
        release.wait(20)
        return real(path, agent_type)
    monkeypatch.setattr(subagents, "open_conversation", held)
    monkeypatch.setattr(frame, "_show_subagent_chat",
                        lambda d: opened.append(d.GetTitle()) or wx.ID_CANCEL)
    frame._open_subagent(sub, None, lambda: closed.append(True))
    # Back here at once, with nothing open, and the window isn't held up.
    assert opened == [] and frame._subagent_opening
    assert pump(lambda: threads)
    assert threads[0] is not threading.current_thread()
    frame._open_subagent(sub)  # Enter again doesn't read it twice, and says so
    assert env["feedback"][-1] == "Still reading that subagent's conversation."
    release.set()
    assert pump(lambda: opened)
    assert opened == ["Subagent code-reviewer, in Quiet one"] and closed == [True]
    assert not frame._subagent_opening and len(threads) == 1


def test_a_conversation_read_after_youve_moved_on_doesnt_open(frame, env, monkeypatch):
    # Opening a moment later would take the keys you were pressing (#468).
    import threading
    _session_with_subagent(frame, env)
    sub = frame._chat_subagents[0]
    release, opened = threading.Event(), []
    real = subagents.open_conversation

    def held(path, agent_type=""):
        release.wait(20)
        return real(path, agent_type)
    monkeypatch.setattr(subagents, "open_conversation", held)
    monkeypatch.setattr(frame, "_show_subagent_chat",
                        lambda d: opened.append(d.GetTitle()) or wx.ID_CANCEL)
    frame._open_subagent(sub)
    frame.reply_text.SetValue("typing something else")
    release.set()
    assert pump(lambda: not frame._subagent_opening)
    assert opened == []
    assert any("code-reviewer's conversation is ready, but you've moved on" in f
               for f in env["feedback"])


def test_a_dialog_it_opens_from_doesnt_count_as_another(frame, env, monkeypatch):
    # Lists is modal while a subagent's conversation is read for it. Any other
    # dialog that's up means you've moved on.
    class Up(wx.Dialog):
        def IsModal(self):
            return True
    up = Up(frame)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(wx, "GetTopLevelWindows", lambda: [frame, up])
            mark = frame._codex_mark()
            assert frame._codex_may_open(mark, "It's", over=up)
            assert not frame._codex_may_open(mark, "It's")
        assert "It's ready, but you've moved on" in env["feedback"][-1]
    finally:
        up.Destroy()


def _two_subagents(frame, tmp_path, open_subagent):
    """Lists on two subagents, over the frame, with what opens them replaced."""
    from thechatplace.ui.dialogs import ListsDialog
    subs = [subagents.Subagent(agent_id, path=tmp_path / f"agent-{agent_id}.jsonl",
                               status=subagents.DONE) for agent_id in ("a1", "a2")]
    items = [lists.ListItem(row=sub.agent_id, detail=sub.agent_id, subagent=sub) for sub in subs]

    def build(category, scope):
        return items if category == lists.SUBAGENTS else []
    return ListsDialog(frame, "Quiet one", build, lambda text: True, lambda text: None,
                       category=lists.SUBAGENTS, open_subagent=open_subagent)


def test_lists_says_when_its_moved_off_the_subagent_it_asked_for(frame, tmp_path):
    asked = []
    dialog = _two_subagents(frame, tmp_path, lambda *a: asked.append(a))
    try:
        page = dialog.current()
        page.list.SetSelection(0)
        page.show_selected()
        page.read_conversation()
        sub, parent, _closed, still_on = asked[0]
        assert sub.agent_id == "a1" and parent is dialog and still_on()
        page.detail.SetFocus()  # about Lists on the same row: still asking for it
        assert still_on()
        page.list.SetSelection(1)  # Down to the next subagent
        page.show_selected()
        assert not still_on()
        page.list.SetSelection(0)  # and back
        page.show_selected()
        assert still_on()
        dialog._show_kind(lists.CATEGORY_IDS.index(lists.CODE))  # another kind
        assert not still_on()
    finally:
        dialog.Destroy()


class _ModalDialog(wx.Dialog):
    """Stands in for Lists, up while a subagent's conversation is read."""

    def IsModal(self):
        return True


def test_a_subagent_doesnt_open_over_the_row_you_moved_to(frame, env, monkeypatch):
    # Enter on large A, Down to B: A opened over you later, as Lists turned the
    # focus check off. Enter on B says it's still reading, and B is asked for
    # again once it has.
    import threading
    _session_with_subagent(frame, env)
    sub = frame._chat_subagents[0]
    release, opened, reading = threading.Event(), [], []
    real = subagents.open_conversation

    def held(path, agent_type=""):
        reading.append(True)
        release.wait(20)
        return real(path, agent_type)
    monkeypatch.setattr(subagents, "open_conversation", held)
    monkeypatch.setattr(frame, "_show_subagent_chat",
                        lambda d: opened.append(d.GetTitle()) or wx.ID_CANCEL)
    on_it = [True]
    up = _ModalDialog(frame)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(wx, "GetTopLevelWindows", lambda: [frame, up])
            frame._open_subagent(sub, up, None, lambda: on_it[0])
            assert pump(lambda: reading)
            on_it[0] = False
            frame._open_subagent(sub, up, None, lambda: True)  # Enter on B
            assert env["feedback"][-1] == "Still reading that subagent's conversation."
            release.set()
            assert pump(lambda: not frame._subagent_opening)
        assert opened == []
        assert len(reading) == 1  # B wasn't started either
        assert "code-reviewer's conversation is ready, but you've moved on" in env["feedback"][-1]
    finally:
        up.Destroy()


def test_a_subagent_opens_when_focus_moved_within_lists_on_the_same_row(frame, env, monkeypatch):
    # Whatever has focus by the time it's read. Compared, this failed on
    # Windows and Linux, which is why it's the row that's compared.
    _session_with_subagent(frame, env)
    sub = frame._chat_subagents[0]
    opened = []
    monkeypatch.setattr(frame, "_show_subagent_chat",
                        lambda d: opened.append(d.GetTitle()) or wx.ID_CANCEL)
    up = _ModalDialog(frame)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(wx, "GetTopLevelWindows", lambda: [frame, up])
            patch.setattr(frame, "_codex_mark",
                          lambda: (object(), frame.reply_text.GetValue()))
            frame._open_subagent(sub, up, None, lambda: True)
            assert pump(lambda: opened)
    finally:
        up.Destroy()


def test_a_conversation_that_cant_be_read_is_said(frame, env, monkeypatch):
    _session_with_subagent(frame, env)

    def broken(path, agent_type=""):
        raise OSError("no such file")
    monkeypatch.setattr(subagents, "open_conversation", broken)
    frame._open_subagent(frame._chat_subagents[0])
    assert pump(lambda: any("Couldn't read code-reviewer's conversation" in f
                            for f in env["feedback"]))
    assert not frame._subagent_opening


def test_a_conversation_read_after_loading_another_chat_isnt_opened(frame, env, monkeypatch):
    _session_with_subagent(frame, env)
    opened = []
    monkeypatch.setattr(frame, "_show_subagent_chat", lambda d: opened.append(d) or wx.ID_CANCEL)
    frame._open_subagent(frame._chat_subagents[0])
    frame._open_generation += 1
    assert pump(lambda: not frame._subagent_opening)
    assert opened == []


def test_enter_reads_a_message_over_the_dialog(frame, env, tmp_path):
    from thechatplace.ui import subagent_chat
    from thechatplace.ui.subagent_chat import SubagentChatDialog
    path = tmp_path / "agent-a1.jsonl"
    path.write_text("\n".join(lines(*[sidechain(r, "a1") for r in _reviewer_records()])) + "\n",
                    encoding="utf-8")
    reader = subagents.open_conversation(path, "code-reviewer")
    reader.refresh()
    read = []
    dialog = SubagentChatDialog(frame, subagents.Subagent("a1", path=path, status=subagents.DONE),
                                "Quiet one", reader, read_message=lambda m, p: read.append((m.text, p)),
                                copy=lambda t: True, feedback=lambda t: None)
    try:
        assert dialog.timer.GetInterval() == subagent_chat.FINISHED_REFRESH_MS
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_list_key(event)
        assert read == [("Two problems found.\n\nDetails follow.", dialog)]
        # And from the dialog's char hook: on Windows a list box's Enter
        # goes to the dialog before the list's own handler (#468).
        read.clear()
        focus = dialog.list
        original = wx.Window.FindFocus
        wx.Window.FindFocus = staticmethod(lambda: focus)
        try:
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.SetKeyCode(wx.WXK_RETURN)
            dialog._on_char_hook(event)
        finally:
            wx.Window.FindFocus = original
        assert read == [("Two problems found.\n\nDetails follow.", dialog)]
    finally:
        dialog.Destroy()


def test_lists_say_when_provider_does_not_support_subagents(frame, env, monkeypatch):
    add_transcript(env, CWD, "cli-a", [user_text("hi"), assistant_block(text_block("hello"), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    monkeypatch.setattr(frame._open, "provider", "copilot")
    monkeypatch.setattr(frame.sessions, "capabilities",
                        lambda info: type("Caps", (), {"changes": False})())
    seen = _lists_dialog(frame, monkeypatch, lambda page: None)
    frame.on_lists(lists.SUBAGENTS, lists.SCOPE_CHAT)
    assert seen["rows"] == ["Subagents aren't listed for Copilot chats yet."]


def test_lists_shows_a_codex_chats_subagents(frame, env, monkeypatch):
    # Codex's child conversations are read into the same list as Claude's
    # (#558); Lists once passed it an empty one, saying "none" (#279).
    _session_with_subagent(frame, env)
    monkeypatch.setattr(frame._open, "provider", "codex")
    seen = _lists_dialog(frame, monkeypatch, lambda page: None)
    frame.on_lists(lists.SUBAGENTS, lists.SCOPE_CHAT)
    assert seen["label"] == "&List: Subagents (1):"
    assert seen["rows"][0].startswith("code-reviewer: Review the branch.")


def test_a_background_subagent_still_running_reads_as_running(frame, env, monkeypatch):
    parent = add_transcript(env, CWD, "cli-a", [
        user_text("Review in the background", timestamp=_at(0)),
        agent_call("toolu_ag1", "code-reviewer", "Review", "Review it.", background=True,
                   timestamp=_at(0)),
        agent_launched("toolu_ag1", "a1")])
    folder = platform_paths.subagents_dir(parent)
    folder.mkdir(parents=True)
    (folder / "agent-a1.jsonl").write_text(
        "\n".join(lines(*[sidechain(r, "a1") for r in _reviewer_records(finished=False)])) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(type(frame), "_subagents_live", lambda self, info: True)
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame._chat_subagents)
    assert frame._chat_subagents[0].status == subagents.RUNNING


def test_enter_in_lists_opens_a_subagent_and_goes_from_kinds_to_the_list(frame, env,
                                                                         monkeypatch):
    # On Windows a list box's Enter goes to the dialog before the list's own
    # handler, so the dialog's char hook takes it (#468).
    _session_with_subagent(frame, env)
    opened, steps = [], {}
    monkeypatch.setattr(frame, "_show_subagent_chat", lambda d: opened.append(d.GetTitle())
                        or wx.ID_CANCEL)

    def act(page):
        dialog = page.owner
        focus = [dialog.kinds]
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus[0]))
        monkeypatch.setattr(page.list, "SetFocus", lambda: steps.setdefault("list", True))

        def enter():
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.SetKeyCode(wx.WXK_RETURN)
            dialog._on_char_hook(event)
        enter()
        focus[0] = page.list
        enter()
        assert pump(lambda: opened)  # read in the background first
    _lists_dialog(frame, monkeypatch, act)
    frame.on_subagents()
    assert steps == {"list": True}
    assert opened == ["Subagent code-reviewer, in Quiet one"]


def test_closing_a_conversation_stops_its_timer_first(frame, env):
    # Its next tick reached a window that had gone, which crashed the app on
    # Linux (the Orca check on #512).
    from thechatplace.ui.subagent_chat import SubagentChatDialog
    from types import SimpleNamespace
    from thechatplace import subagents
    reader = SimpleNamespace(transcript=SimpleNamespace(messages=[]), refresh=lambda: False)
    sub = subagents.Subagent(agent_id="a1", agent_type="code-reviewer", status=subagents.DONE)
    dialog = SubagentChatDialog(frame, sub, "Chat", reader, read_message=lambda *a: None,
                                copy=lambda *a: None, feedback=lambda *a: None,
                                latest=lambda: None, show_activity=False, whole_in_list=False)
    assert dialog.timer.IsRunning()
    timer = dialog.timer
    dialog.Destroy()
    assert not timer.IsRunning()
