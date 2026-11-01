"""Rewind to Here and Branch from Here (#468) in the window. These tests
construct windows: run them in the VM or CI only."""
import dataclasses

import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import env, fake_runner, frame, select, settle  # noqa: F401,E402
from fake_env import add_desktop, add_transcript, now_ms, pump  # noqa: E402
from records import (assistant_block, lines, other, text_block, tool_result,  # noqa: E402
                     tool_use_block, user_text)
from thechatplace import rewind  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402
from thechatplace.own_store import OwnSession  # noqa: E402
from thechatplace.transcript import ASSISTANT, USER  # noqa: E402


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
            seen["happens"] = self.happens.GetValue()
            if choice is not None:
                self.what.SetSelection([c for c, _l in rewind.CHOICES].index(choice))
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
    seen = answer_dialog(monkeypatch)
    calls = stub_files(monkeypatch)
    frame.on_rewind_to_here()
    assert seen["title"] == "Rewind Fixes to before your message: Change a.txt to two"
    assert seen["default"] == rewind.BOTH
    assert "a.txt" in seen["happens"] and "Fixes is kept as it is" in seen["happens"]
    # The new chat: unstarted, cut just before the message, settings carried.
    own = frame.store.get(frame._open.cli_session_id)
    assert own.cli_session_id != "own-rw" and own.title == "Fixes (rewound)"
    assert (own.fork_source, own.fork_at, own.started) == ("own-rw", "sys-1", False)
    assert (own.model, own.permission_mode) == ("sonnet", "acceptEdits")
    assert frame.reply_text.GetValue() == "Change a.txt to two"
    assert any("Rewound to before your message in Fixes (rewound)" in f
               for f in env["feedback"])
    assert pump(lambda: calls and any("restored" in f for f in env["feedback"]))
    assert calls == [("own-rw", parts["second"]["uuid"], str(folder), 1)]
    assert frame.store.get("own-rw") is not None  # the original is kept
    assert pump(lambda: "branch of Fixes" in frame.chat_list.GetString(0))
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
    assert "no file checkpoints" in env["boxes"][-1]
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
    assert "Claude is working in this chat" in seen["happens"]


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
    frame.on_send()
    command = fake_runner.instances[-1].command
    assert "--resume" not in command and "--fork-session" not in command
    assert command[command.index("--session-id") + 1] == own.cli_session_id


def test_rewind_needs_one_of_your_messages(frame, env, fake_runner, monkeypatch):
    own_chat(frame, env)
    choose(frame, ASSISTANT, 0)
    frame.on_rewind_to_here()
    assert env["feedback"][-1] == ("Choose one of your own messages in the messages list, "
                                   "then Rewind to Here.")


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
        assert labels["Rewind to Here..."] and labels["Branch from Here"]
    finally:
        menu.Destroy()
    choose(frame, ASSISTANT, 0)
    menu = frame._message_menu()
    try:
        labels = {item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()}
        assert not labels["Rewind to Here..."] and labels["Branch from Here"]
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
    assert env["feedback"][-1] == "Load a chat first, then choose a message to rewind from."


def test_rewind_dialog_names_its_controls_and_says_what_happens(frame, env):
    from thechatplace.transcript import parse_lines
    from thechatplace.ui.dialogs import RewindDialog
    records, _parts = two_turns(checkpointed=False)
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    plan = rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True)
    dialog = RewindDialog(frame, plan)
    try:
        assert dialog.what.GetLabel() == "&What to rewind"
        assert dialog.happens.GetName() == "What happens"
        assert dialog.selected() == rewind.CONVERSATION
        assert "Fixes (rewound)" in dialog.happens.GetValue()
        dialog.what.SetSelection(2)
        dialog._describe()
        assert "no file checkpoints" in dialog.happens.GetValue()
        dialog._go()
        assert dialog.choice == ""  # stays open, and says why
        assert "no file checkpoints" in env["boxes"][-1]
        dialog.what.SetSelection(1)
        dialog._go()
        assert dialog.choice == rewind.CONVERSATION
    finally:
        dialog.Destroy()
