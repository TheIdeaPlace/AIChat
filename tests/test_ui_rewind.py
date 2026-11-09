"""Rewind to Here and Branch from Here (#468) in the window. These tests
construct windows: run them in the VM or CI only."""
import dataclasses
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import env, fake_runner, frame, select, settle  # noqa: F401,E402
from fake_env import add_desktop, add_transcript, now_ms, pump  # noqa: E402
from records import (assistant_block, lines, other, text_block, tool_result,  # noqa: E402
                     tool_use_block, user_text)
from thechatplace import rewind  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402
from thechatplace.own_store import OwnSession  # noqa: E402
from thechatplace.sessions import NEEDS_YOU, WORKING  # noqa: E402
from thechatplace.transcript import ASSISTANT, USER  # noqa: E402
from thechatplace.ui import rewind_chats  # noqa: E402


def chained(*records):
    previous = None
    for record in records:
        if "uuid" in record:
            record["parentUuid"] = previous
            previous = record["uuid"]
    return list(records)


def two_turns(checkpointed=True):
    first = user_text("Create a.txt")
    write = assistant_block(tool_use_block("Write", {"file_path": "a.txt", "content": "one"},
                                           "t1"), "m1")
    result = tool_result("t1", "File created.", toolUseResult={
        "type": "create", "filePath": "a.txt", "content": "one", "structuredPatch": []})
    done = assistant_block(text_block("DONE one"), "m2")
    turn_end = other("system", uuid="sys-1", subtype="turn_duration")
    second = user_text("Change a.txt to two")
    edit = assistant_block(tool_use_block("Write", {"file_path": "a.txt", "content": "two"},
                                          "t2"), "m3")
    result2 = tool_result("t2", "Updated.", toolUseResult={
        "type": "update", "filePath": "a.txt", "content": "two", "structuredPatch": []})
    done2 = assistant_block(text_block("DONE two"), "m4")
    records = chained(first, write, result, done, turn_end, second, edit, result2, done2)
    if checkpointed:
        for message in (first, second):
            records.append(other("file-history-snapshot", messageId=message["uuid"],
                                 snapshot={"messageId": message["uuid"]}))
    return records, {"first": first, "done": done, "turn_end": turn_end, "second": second}


def own_chat(frame, env, checkpointed=True, title="Fixes"):
    folder = env["tmp"] / "work"
    folder.mkdir(exist_ok=True)
    records, parts = two_turns(checkpointed)
    frame.store.add(OwnSession("own-rw", title, str(folder), model="sonnet",
                               permission_mode="acceptEdits", last_activity_ms=now_ms()))
    add_transcript(env, str(folder), "own-rw", records)
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, title)
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame._reader is not None)
    return folder, parts


def choose(frame, kind, nth=0):
    """Select the nth visible message of ``kind`` in the messages list."""
    visible = frame._visible_messages()
    index = [i for i, m in enumerate(visible) if m.kind == kind][nth]
    frame.chat_list.SetSelection(index)
    return visible[index]


def answer_dialog(monkeypatch, choice=None):
    """The Rewind dialog, answered with ``choice`` (its default if None)."""
    from thechatplace.ui import dialogs
    seen = {}

    class Answers(dialogs.RewindDialog):
        def ShowModal(self):
            seen["title"] = self.GetTitle()
            seen["default"] = self.selected()
            seen["labels"] = [self.what.GetString(i) for i in range(self.what.GetCount())]
            if choice is not None:
                self.what.SetSelection([c for c, _l in rewind.CHOICES].index(choice))
                self._describe()
            seen["happens"] = self.happens.GetValue()
            self._go()
            return wx.ID_OK if self.choice else wx.ID_CANCEL
    monkeypatch.setattr("thechatplace.ui.rewind_chats.RewindDialog", Answers)
    return seen


def stub_files(monkeypatch, ok=True):
    calls = []

    def run(executable, session_id, message, cwd, title, count, own_ids, desktop_ids):
        calls.append((session_id, message.uuid, cwd, count))
        return rewind.RewindResult(ok, f"Files in {title} restored: {count} file."
                                   if ok else "Claude Code couldn't restore the files: no.")
    monkeypatch.setattr(rewind, "run_rewind_files", run)
    return calls


def test_rewind_both_restores_files_and_branches_before_the_message(frame, env, fake_runner,
                                                                    monkeypatch):
    folder, parts = own_chat(frame, env)
    choose(frame, USER, 1)
    seen = answer_dialog(monkeypatch, rewind.BOTH)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert seen["title"] == "Retry Fixes from before your message: Change a.txt to two"
    # Conversation only until an arrow press chooses more (#468).
    assert seen["default"] == rewind.CONVERSATION
    assert "a.txt" in seen["happens"] and "Fixes is kept as it is" in seen["happens"]
    # The new chat: unstarted, cut just before the message, settings carried.
    own = frame.store.get(frame._open.cli_session_id)
    assert own.cli_session_id != "own-rw" and own.title == "Fixes (retry)"
    assert (own.fork_source, own.fork_at, own.started) == ("own-rw", "sys-1", False)
    assert (own.model, own.permission_mode) == ("sonnet", "acceptEdits")
    assert frame.reply_text.GetValue() == "Change a.txt to two"
    assert any("Opened Fixes (retry), from before your message." in f
               for f in env["feedback"])
    assert pump(lambda: calls and any("restored" in f for f in env["feedback"]))
    assert calls == [("own-rw", parts["second"]["uuid"], str(folder), 1)]
    assert frame.store.get("own-rw") is not None  # the original is kept
    assert pump(lambda: "new copy of Fixes" in frame.chat_list.GetString(0))
    # Nothing ran until Send; then the copy is made, cut where chosen.
    assert fake_runner.instances == []
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert command[command.index("--resume") + 1] == "own-rw"
    assert command[command.index("--resume-session-at") + 1] == "sys-1"
    assert "--fork-session" in command
    assert command[command.index("--session-id") + 1] == own.cli_session_id
    assert fake_runner.instances[-1].prompt == "Change a.txt to two"
    # A first turn that never got going makes the same copy again.
    frame._on_turn_event({"id": own.cli_session_id}, own.title,
                         TurnEvent("failed", text="not signed in", is_error=True))
    frame.reply_text.SetValue("again")
    frame.on_send()
    again = fake_runner.instances[-1].command
    assert again[again.index("--resume-session-at") + 1] == "sys-1"
    assert again[again.index("--session-id") + 1] == own.cli_session_id


def test_enter_on_rewind_as_it_opens_leaves_the_files_alone(frame, env, fake_runner, monkeypatch):
    # Rewind is the default button, and files can't be put back again (#468).
    own_chat(frame, env)
    choose(frame, USER, 1)
    seen = answer_dialog(monkeypatch)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert seen["default"] == rewind.CONVERSATION
    assert seen["labels"] == ["Conversation and files", "Conversation only", "Files only"]
    assert frame.store.get(frame._open.cli_session_id).title == "Fixes (retry)"
    pump(lambda: False, timeout=0.3)
    assert calls == [] and not any("Putting the files" in f for f in env["feedback"])


def test_rewind_files_only_keeps_the_conversation(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.FILES)
    calls = stub_files(monkeypatch)
    before = {s.cli_session_id for s in frame.store.all()}
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == "Putting the files in Fixes back."
    # Sending waits for the files.
    frame.reply_text.SetValue("next")
    frame.on_send()
    assert env["feedback"][-1] == "Still putting the files back. Send again in a moment."
    assert pump(lambda: calls and any("restored" in f for f in env["feedback"]))
    assert {s.cli_session_id for s in frame.store.all()} == before
    assert frame._open.cli_session_id == "own-rw"


def test_files_stay_when_the_new_chat_cant_be_made(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.BOTH)
    calls = stub_files(monkeypatch)
    monkeypatch.setattr(frame, "_make_branch", lambda *a, **k: None)
    frame.on_rewind_to_here()
    pump(lambda: False, timeout=0.5)
    assert calls == [] and "own-rw" not in frame._rewinding_files


def test_files_wait_for_a_turn_that_started_while_the_dialog_was_open(frame, env, fake_runner,
                                                                      monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    from thechatplace.ui import dialogs

    class HandOffMeanwhile(dialogs.RewindDialog):
        def ShowModal(self):
            frame._runners["own-rw"] = object()  # a hand-off started a turn
            self.what.SetSelection(2)
            self._go()
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.rewind_chats.RewindDialog", HandOffMeanwhile)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    frame._runners.pop("own-rw", None)
    assert calls == []
    assert env["feedback"][-1] == ("Fixes is busy, so its files weren't put back. Try again "
                                   "once it has finished.")


def test_a_failed_files_rewind_is_said_and_shown(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.FILES)
    stub_files(monkeypatch, ok=False)
    frame.on_rewind_to_here()
    assert pump(lambda: env["boxes"] and "couldn't restore" in env["boxes"][-1])
    assert "couldn't restore" in env["feedback"][-1]


def test_without_checkpoints_files_are_unavailable(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env, checkpointed=False)
    choose(frame, USER, 1)
    seen = answer_dialog(monkeypatch, rewind.FILES)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert seen["default"] == rewind.CONVERSATION
    assert seen["labels"] == ["Conversation and files (unavailable)", "Conversation only",
                              "Files only (unavailable)"]
    assert "no saved copies of your files" in env["boxes"][-1]
    assert calls == [] and frame._open.cli_session_id == "own-rw"


def test_while_claude_works_only_the_conversation_can_go_back(frame, env, fake_runner,
                                                              monkeypatch):
    own_chat(frame, env)
    frame.reply_text.SetValue("busy")
    frame.on_send()  # a turn starts, and keeps going
    choose(frame, USER, 1)
    seen = answer_dialog(monkeypatch)
    stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert seen["default"] == rewind.CONVERSATION
    assert "Claude is working or waiting on you in this chat" in seen["happens"]


def test_rewind_a_desktop_session_branches_but_leaves_files(frame, env, fake_runner,
                                                            monkeypatch):
    folder = env["tmp"] / "repo"
    folder.mkdir()
    records, _parts = two_turns()
    add_desktop(env, "local_c", "cli-c", "Desktop work", cwd=str(folder))
    add_transcript(env, str(folder), "cli-c", records)
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Desktop work")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded and frame._reader is not None)
    choose(frame, USER, 1)
    seen = answer_dialog(monkeypatch)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert seen["default"] == rewind.CONVERSATION
    assert "Claude desktop app session" in seen["happens"]
    # Where it works, not how (#596).
    assert "Files can be put back only for chats started in The Chat Place" in seen["happens"]
    assert "writes to the chat" not in seen["happens"]
    own = frame.store.get(frame._open.cli_session_id)
    assert (own.fork_source, own.fork_at) == ("cli-c", "sys-1")
    assert (own.model, own.permission_mode) == ("", "auto")  # as Continue Here starts
    assert calls == []


def test_rewind_the_first_message_starts_a_chat_with_no_history(frame, env, fake_runner,
                                                                monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 0)
    answer_dialog(monkeypatch, rewind.CONVERSATION)
    frame.on_rewind_to_here()
    own = frame.store.get(frame._open.cli_session_id)
    assert (own.fork_source, own.fork_at, own.forked_from) == ("", "", "Fixes")
    assert pump(lambda: "no history" in frame.chat_list.GetString(0))
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == "Fixes (retry) has no messages yet."
    frame.reply_text.SetValue("Create a.txt")
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert "--resume" not in command and "--fork-session" not in command
    assert command[command.index("--session-id") + 1] == own.cli_session_id


def test_rewind_needs_one_of_your_messages(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, ASSISTANT, 0)
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == ("Choose one of your own messages in the messages list, "
                                   "then Retry from Here.")


def test_branch_from_a_reply_keeps_history_through_it(frame, env, fake_runner, monkeypatch):
    _folder, parts = own_chat(frame, env)
    reply = choose(frame, ASSISTANT, 0)
    frame.on_branch_from_here()
    own = frame.store.get(frame._open.cli_session_id)
    assert own.title == "Fixes (branch)"
    assert (own.fork_source, own.fork_at) == ("own-rw", parts["done"]["uuid"]) == \
        ("own-rw", reply.uuid)
    assert frame.reply_text.GetValue() == ""
    assert frame.FindFocus() is frame.reply_text or not frame.IsShown()
    assert ("Branched Fixes at \"DONE one\" into Fixes (branch). Type your message to carry "
            "on from there.") in env["feedback"]


def test_menus_offer_both_only_for_claude_chats(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    menu = frame._message_menu()
    try:
        labels = {item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()}
        assert labels["Retry from Here..."] and labels["Branch from Here"]
    finally:
        menu.Destroy()
    choose(frame, ASSISTANT, 0)
    menu = frame._message_menu()
    try:
        labels = {item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()}
        assert not labels["Retry from Here..."] and labels["Branch from Here"]
    finally:
        menu.Destroy()


def test_codex_chats_are_refused_with_a_reason(frame, env, fake_runner):
    frame._open = dataclasses.replace(frame._snapshot.sessions[0], provider="codex")
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == "This action isn't available for Codex chats yet."
    frame.on_branch_from_here()
    assert env["feedback"][-1] == "This action isn't available for Codex chats yet."


def test_nothing_loaded_says_so(frame, env):
    frame.unload_session()
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == "Load a chat first, then choose a message to retry from."


def test_rewind_dialog_names_its_controls_and_says_what_happens(frame, env):
    from thechatplace.transcript import parse_lines
    from thechatplace.ui.dialogs import RewindDialog
    records, _parts = two_turns(checkpointed=False)
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    plan = rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True)
    dialog = RewindDialog(frame, plan)
    try:
        assert dialog.what.GetLabel() == "&What goes back"
        assert dialog.happens.GetName() == "What happens"
        assert dialog.selected() == rewind.CONVERSATION
        assert "Fixes (retry)" in dialog.happens.GetValue()
        assert dialog.go.GetLabel() == "&Retry"
        dialog.what.SetSelection(2)
        dialog._describe()
        assert "no saved copies of your files" in dialog.happens.GetValue()
        # Files only makes no chat: the button says what it does (#596).
        assert dialog.go.GetLabel() == "&Put Files Back"
        assert dialog.button_label() == "Put Files Back"
        dialog.what.SetSelection(0)
        dialog._describe()
        assert dialog.go.GetLabel() == "&Retry"
        dialog.what.SetSelection(2)
        dialog._describe()
        dialog._go()
        assert dialog.choice == ""  # stays open, and says why
        assert "no saved copies of your files" in env["boxes"][-1]
        dialog.what.SetSelection(1)
        dialog._go()
        assert dialog.choice == rewind.CONVERSATION
    finally:
        dialog.Destroy()


def test_alt_w_goes_to_what_to_rewind(frame, env, monkeypatch):
    # Windows doesn't take a radio box's label's access key itself (#468).
    from thechatplace.transcript import parse_lines
    from thechatplace.ui.dialogs import RewindDialog
    records, _parts = two_turns(checkpointed=False)
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    dialog = RewindDialog(frame, rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True))
    try:
        focused = []
        monkeypatch.setattr(dialog.what, "SetFocus", lambda: focused.append("what"))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("W"))
        event.SetAltDown(True)
        dialog._on_key(event)
        assert focused == ["what"]
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("W"))
        dialog._on_key(event)  # W alone is left alone
        assert focused == ["what"]
    finally:
        dialog.Destroy()


def test_enter_in_what_happens_says_where_to_go(frame, env, monkeypatch):
    from thechatplace.transcript import parse_lines
    from thechatplace.ui.dialogs import RewindDialog
    records, _parts = two_turns(checkpointed=False)
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    said = []
    dialog = RewindDialog(frame, rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True),
                          feedback=said.append)
    ended = []
    dialog.EndModal = ended.append

    def enter(focus):
        monkeypatch.setattr(dialog, "_focus", lambda: focus)
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_key(event)
        return event.GetSkipped()
    try:
        assert not enter(dialog.happens)
        assert said == ["Tab to Retry, or to Cancel."]
        assert ended == [] and dialog.choice == ""
        # Elsewhere Enter goes on to Retry, the default button, and isn't said.
        assert enter(dialog.what)
        assert said == ["Tab to Retry, or to Cancel."]
        # With Files only chosen, the button it names is Put Files Back (#596).
        dialog.what.SetSelection(2)
        dialog._describe()
        assert not enter(dialog.happens)
        assert said[-1] == "Tab to Put Files Back, or to Cancel."
    finally:
        dialog.Destroy()


def test_cancelling_puts_focus_back_only_if_it_isnt_there(frame, env, fake_runner, monkeypatch):
    # Setting focus where Windows had already put it back had a screen
    # reader say the message twice (#468).
    from thechatplace.ui import dialogs
    own_chat(frame, env)
    choose(frame, USER, 1)

    after = [None]  # where focus is once it closes (None: left where it was)

    class Cancelled(dialogs.RewindDialog):
        def ShowModal(self):
            if after[0] is not None:
                where[0] = after[0]
            return wx.ID_CANCEL
    monkeypatch.setattr("thechatplace.ui.rewind_chats.RewindDialog", Cancelled)
    focused = []
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focused.append("messages"))
    where = [frame.chat_list]
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: where[0]))
    frame.on_rewind_to_here()
    assert focused == []
    # Focus somewhere else once it closed: back to the message.
    after[0] = frame.reply_text
    frame.on_rewind_to_here()
    assert focused == ["messages"]


def _focus_moved_by_the_dialog(frame, monkeypatch, choice):
    """The dialog answered with ``choice``, leaving focus in the reply box as
    it closes. What's asked to take focus on the messages list."""
    from thechatplace.ui import dialogs
    where = [frame.chat_list]

    class Answers(dialogs.RewindDialog):
        def ShowModal(self):
            self.what.SetSelection([c for c, _l in rewind.CHOICES].index(choice))
            self._go()
            where[0] = frame.reply_text
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.rewind_chats.RewindDialog", Answers)
    focused = []
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focused.append("messages"))
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: where[0]))
    return focused, where


def test_files_only_puts_focus_back(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    focused, _where = _focus_moved_by_the_dialog(frame, monkeypatch, rewind.FILES)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert focused == ["messages"]
    assert pump(lambda: calls)


def test_files_only_refused_puts_focus_back(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    focused, _where = _focus_moved_by_the_dialog(frame, monkeypatch, rewind.FILES)
    monkeypatch.setattr(frame, "_start_rewind_files", lambda *a, **k: False)
    frame.on_rewind_to_here()
    assert focused == ["messages"]


def test_a_new_chat_that_cant_be_made_puts_focus_back(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    focused, _where = _focus_moved_by_the_dialog(frame, monkeypatch, rewind.BOTH)
    monkeypatch.setattr(frame, "_make_branch", lambda *a, **k: None)
    frame.on_rewind_to_here()
    assert focused == ["messages"]


def test_focus_already_back_isnt_set_again_after_files_only(frame, env, fake_runner, monkeypatch):
    # Setting it again would have a screen reader say the message twice.
    own_chat(frame, env)
    choose(frame, USER, 1)
    from thechatplace.ui import dialogs

    class Answers(dialogs.RewindDialog):
        def ShowModal(self):
            self.what.SetSelection(2)
            self._go()
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.rewind_chats.RewindDialog", Answers)
    focused = []
    monkeypatch.setattr(frame.chat_list, "SetFocus", lambda: focused.append("messages"))
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert focused == []
    assert pump(lambda: calls)


def test_a_loaded_chat_with_no_transcript_has_no_history_to_go_back_to(frame, env, fake_runner):
    own_chat(frame, env)
    frame._reader = None
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == "Fixes has no history to go back to."
    frame._chat_loaded = False
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == "Fixes is still loading."


def test_what_happens_says_subagents_arent_known_until_theyre_read(frame, env, fake_runner,
                                                                   monkeypatch):
    own_chat(frame, env)
    choose(frame, USER, 1)
    assert pump(lambda: frame._subagent_statuses is not None)
    seen = answer_dialog(monkeypatch, rewind.FILES)  # stays in this chat
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert "isn't known yet" not in seen["happens"]
    assert pump(lambda: calls and "own-rw" not in frame._rewinding_files)
    frame._subagent_statuses = None  # the first read hasn't finished
    frame.on_rewind_to_here()
    assert "isn't known yet" in seen["happens"]
    assert pump(lambda: len(calls) == 2 and "own-rw" not in frame._rewinding_files)


def test_a_chat_cant_be_deleted_while_its_files_go_back(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    frame._rewinding_files.add("own-rw")
    try:
        before = list(env["boxes"])
        frame.on_delete_permanently()
        assert env["feedback"][-1] == ("The files in Fixes are being put back. Wait for that to "
                                       "finish first.")
        assert env["boxes"] == before  # not even asked
        assert frame.store.get("own-rw") is not None
    finally:
        frame._rewinding_files.discard("own-rw")


def test_quitting_while_files_go_back_warns(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    frame._rewinding_files.add("own-rw")
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.NO)
    event = wx.CloseEvent(wx.wxEVT_CLOSE_WINDOW)
    event.SetCanVeto(True)
    frame._on_close(event)
    assert event.GetVeto() and not frame._closing
    assert "still putting files back. Quit anyway?" in asked[-1]
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.YES)
    event = wx.CloseEvent(wx.wxEVT_CLOSE_WINDOW)
    event.SetCanVeto(True)
    frame._on_close(event)
    assert not event.GetVeto() and frame._closing


# -- the lock is the folder's: a turn can't start in any chat there (#468) --------------------


def _held_files(monkeypatch):
    """The restore, held until the test lets it finish."""
    import threading
    release = threading.Event()

    def run(executable, session_id, message, cwd, title, count, own_ids, desktop_ids):
        release.wait(20)
        return rewind.RewindResult(True, f"Files in {title} restored: {count} file.")
    monkeypatch.setattr(rewind, "run_rewind_files", run)
    return release


def test_the_new_chat_cant_send_while_its_files_go_back(frame, env, fake_runner, monkeypatch):
    folder, _parts = own_chat(frame, env)
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.BOTH)
    release = _held_files(monkeypatch)
    try:
        frame.on_rewind_to_here()
        own = frame.store.get(frame._open.cli_session_id)
        assert own.cli_session_id != "own-rw"
        assert frame._files_going_back_in(str(folder))
        frame.on_send()
        assert fake_runner.instances == []
        assert frame.reply_text.GetValue() == "Change a.txt to two"  # kept to send later
        assert any("Still putting the files back" in f for f in env["feedback"])
        # Every other way a turn starts is refused too, and says why.
        assert "putting files back" in frame._send_now(own.cli_session_id, "Queued")
    finally:
        release.set()
    assert pump(lambda: not frame._files_going_back_in(str(folder)))
    frame.on_send()
    assert fake_runner.instances


def test_another_chat_in_the_folder_cant_send_while_files_go_back(frame, env, fake_runner,
                                                                  monkeypatch):
    folder, _parts = own_chat(frame, env)
    frame.store.add(OwnSession("own-other", "Other", str(folder), last_activity_ms=now_ms()))
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.BOTH)
    release = _held_files(monkeypatch)
    try:
        frame.on_rewind_to_here()
        assert "putting files back" in frame._send_now("own-other", "Hello")
        assert fake_runner.instances == []
    finally:
        release.set()
    assert pump(lambda: not frame._files_going_back_in(str(folder)))


def test_a_chat_in_another_folder_isnt_held_up(frame, env, fake_runner, monkeypatch):
    folder, _parts = own_chat(frame, env)
    elsewhere = env["tmp"] / "elsewhere"
    elsewhere.mkdir()
    frame.store.add(OwnSession("own-away", "Away", str(elsewhere), last_activity_ms=now_ms()))
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.BOTH)
    release = _held_files(monkeypatch)
    try:
        frame.on_rewind_to_here()
        assert frame._files_going_back_in(str(folder))
        assert not frame._files_going_back_in(str(elsewhere))
    finally:
        release.set()
    assert pump(lambda: not frame._files_going_back_in(str(folder)))


def _new_session_filled(monkeypatch, folder, message="Start the thing"):
    from thechatplace.ui import main_frame

    class Filled:
        def __init__(self, parent, root, **kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def values(self):
            return (str(folder), "New work", "auto", message, "")

        def effort(self):
            return ""

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "NewSessionDialog", Filled)


def _copied(env, monkeypatch, frame):
    copied = []
    monkeypatch.setattr(frame, "_copy_text", lambda text: copied.append(text) or True)
    return copied


def test_new_session_waits_for_files_going_back_and_keeps_your_message(frame, env, fake_runner,
                                                                       monkeypatch):
    # It started its turn without the check _send_now makes (#468).
    folder, _parts = own_chat(frame, env)
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.BOTH)
    release = _held_files(monkeypatch)
    copied = _copied(env, monkeypatch, frame)
    _new_session_filled(monkeypatch, folder)
    try:
        frame.on_rewind_to_here()
        before = [s.cli_session_id for s in frame.store.all()]
        frame.on_new_session()
        assert fake_runner.instances == []
        assert [s.cli_session_id for s in frame.store.all()] == before  # no chat saved
        assert copied == ["Start the thing"]
        said = env["feedback"][-1]
        assert "still putting files back" in said and "no chat was started" in said
        assert "wasn't sent" in said and "copied to the clipboard" in said
    finally:
        release.set()
    assert pump(lambda: not frame._files_going_back_in(str(folder)))
    frame.on_new_session()
    assert fake_runner.instances[-1].prompt == "Start the thing"


def test_new_session_in_another_folder_isnt_held_up(frame, env, fake_runner, monkeypatch):
    folder, _parts = own_chat(frame, env)
    elsewhere = env["tmp"] / "elsewhere"
    elsewhere.mkdir()
    choose(frame, USER, 1)
    answer_dialog(monkeypatch, rewind.BOTH)
    release = _held_files(monkeypatch)
    _new_session_filled(monkeypatch, elsewhere)
    try:
        frame.on_rewind_to_here()
        frame.on_new_session()
        assert fake_runner.instances[-1].cwd == str(elsewhere)
    finally:
        release.set()
    assert pump(lambda: not frame._files_going_back_in(str(folder)))


def test_continue_here_waits_for_files_going_back_and_keeps_your_message(frame, env, fake_runner,
                                                                         monkeypatch):
    from thechatplace.ui import dialogs
    folder = env["tmp"] / "repo"
    folder.mkdir()
    add_desktop(env, "local_c", "cli-c", "Desktop work", cwd=str(folder))
    add_transcript(env, str(folder), "cli-c", [user_text("Earlier question")])
    frame.refresh_sessions(force=True, resort=True)
    settle(frame)
    select(frame, "Desktop work")
    frame.on_open_session()

    class Fills(dialogs.NewSessionDialog):
        def ShowModal(self):
            self.message.SetValue("Carry on from here")
            return wx.ID_OK
    monkeypatch.setattr("thechatplace.ui.main_frame.NewSessionDialog", Fills)
    copied = _copied(env, monkeypatch, frame)
    held = frame._rewinding_folders
    held["another-chat"] = rewind_chats._folder_key(str(folder))
    before = [s.cli_session_id for s in frame.store.all()]
    frame.on_continue_here()
    assert fake_runner.instances == []
    assert [s.cli_session_id for s in frame.store.all()] == before
    assert copied == ["Carry on from here"]
    assert "no copy was made" in env["feedback"][-1]
    del held["another-chat"]
    frame.on_continue_here()
    assert fake_runner.instances[-1].prompt == "Carry on from here"


def test_a_codex_new_chat_keeps_its_message_while_files_go_back(frame, env, monkeypatch):
    # The New Session path for Codex and Copilot: the chat exists, nothing runs,
    # and the message comes back to its reply box, by way of _send_codex_owned.
    folder = env["tmp"] / "work"
    folder.mkdir(exist_ok=True)
    frame._rewinding_folders["another-chat"] = rewind_chats._folder_key(str(folder))
    record = OwnSession("codex-new", "Codex new", str(folder), provider="codex", started=True)
    actions = []
    monkeypatch.setattr(frame.sessions, "owned_action", lambda *a, **k: actions.append(a))
    try:
        frame._send_codex_owned(record, "Start the thing")
        assert actions == []
        assert frame._codex_drafts[record.to_info().key] == "Start the thing"
        assert "Still putting files back" in env["feedback"][-1]
    finally:
        del frame._rewinding_folders["another-chat"]


@pytest.mark.parametrize("provider", ["codex", "copilot"])
def test_a_send_refused_while_files_go_back_leaves_box_and_attachments(frame, env, monkeypatch,
                                                                       provider):
    # Refused after clearing, the attachment was gone and a recovered text path
    # was left in the box, so sending again gave Codex a path, not the image.
    folder = env["tmp"] / "work"
    folder.mkdir(exist_ok=True)
    record = OwnSession(f"{provider}-send", "Send", str(folder), provider=provider)
    frame.store.add(record)
    info = record.to_info()
    frame._open = info
    monkeypatch.setattr(frame, "_can_edit", lambda _info: True)
    monkeypatch.setattr(frame, "_still_preparing", lambda _info: False)
    monkeypatch.setattr(frame, "_handle_codex_command", lambda _info, _text: False)
    built, actions = [], []
    monkeypatch.setattr(frame, "_codex_message", lambda i, text: built.append(text) or text)
    monkeypatch.setattr(frame.sessions, "owned_action", lambda *a, **k: actions.append(a))
    picture = str(folder / "picture.png")
    key = frame._attachment_key(info)
    frame._attachments[key] = [picture]
    frame._codex_skills = {info.key: {"lint": "skills/lint"}}
    frame.reply_text.SetValue("Look at this")
    frame._rewinding_folders["another-chat"] = rewind_chats._folder_key(str(folder))
    try:
        frame.on_send()
        assert env["feedback"][-1] == ("Still putting files back in this folder. "
                                       "Send again in a moment.")
        assert frame.reply_text.GetValue() == "Look at this"
        assert frame._attachments[key] == [picture]
        assert frame._codex_skills[info.key] == {"lint": "skills/lint"}
        assert built == [] and actions == []
        assert not frame._codex_send_pending
    finally:
        del frame._rewinding_folders["another-chat"]


def test_a_codex_send_on_its_way_stops_files_being_offered(frame, env, fake_runner):
    # Accepted but not yet a turn: it starts during the restore otherwise.
    folder, _parts = own_chat(frame, env)
    record = OwnSession("codex-other", "Other", str(folder), provider="codex")
    frame.store.add(record)
    key = record.to_info().key
    assert not frame._turn_running_in(str(folder))
    frame._codex_send_pending[key] = 1
    try:
        assert frame._turn_running_in(str(folder))
        assert not frame._can_rewind_files(frame._open)
        assert not frame._turn_running_in(str(folder / "elsewhere"))
    finally:
        del frame._codex_send_pending[key]
    assert not frame._turn_running_in(str(folder))


@pytest.mark.parametrize("state,outcome", [(NEEDS_YOU, "inProgress"), (WORKING, "inProgress")])
def test_files_arent_offered_while_a_codex_turn_is_in_progress(frame, env, fake_runner, state,
                                                               outcome):
    # A Codex turn on an approval says Needs You, and edits files once answered.
    folder, _parts = own_chat(frame, env)
    record = OwnSession("codex-other", "Other", str(folder), provider="codex")
    frame.store.add(record)
    frame._codex_states[record.to_info().key] = SimpleNamespace(state=state, outcome=outcome)
    try:
        info = frame._open
        assert frame._turn_running_in(str(folder))
        assert not frame._can_rewind_files(info)
        assert "Another chat in this folder is working or waiting on you" in \
            frame._files_reason(info)
    finally:
        del frame._codex_states[record.to_info().key]
    assert not frame._turn_running_in(str(folder))


def test_files_arent_offered_while_another_chat_works_in_the_folder(frame, env, fake_runner,
                                                                    monkeypatch):
    folder, _parts = own_chat(frame, env)
    frame.store.add(OwnSession("own-other", "Other", str(folder), last_activity_ms=now_ms()))
    frame._runners["own-other"] = object()
    try:
        info = frame._open
        assert not frame._can_rewind_files(info)
        assert "Another chat in this folder is working or waiting on you" in \
            frame._files_reason(info)
    finally:
        del frame._runners["own-other"]
