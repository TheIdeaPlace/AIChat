"""Subagents in the window (#468): Lists' Subagents kind, reading one's
conversation, their edits in Changed Files, the messages menu and the
announcement when one ends. These tests construct windows: run them in the
VM or CI only."""
import json

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
    seen = _lists_dialog(frame, monkeypatch, lambda page: page.activate())
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
        assert not dialog.timer.IsRunning()
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


def test_enter_reads_a_message_over_the_dialog(frame, env, tmp_path):
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
        assert not dialog.timer.IsRunning()  # finished: nothing to keep reading
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_list_key(event)
        assert read == [("Two problems found.\n\nDetails follow.", dialog)]
    finally:
        dialog.Destroy()


def test_lists_say_subagents_are_claude_only(frame, env, monkeypatch):
    add_transcript(env, CWD, "cli-a", [user_text("hi"), assistant_block(text_block("hello"), "m1")])
    select(frame, "Quiet one")
    frame.on_open_session()
    assert pump(lambda: frame._chat_loaded)
    monkeypatch.setattr(frame._open, "provider", "codex")
    monkeypatch.setattr(frame.sessions, "capabilities",
                        lambda info: type("Caps", (), {"changes": False})())
    seen = _lists_dialog(frame, monkeypatch, lambda page: None)
    frame.on_lists(lists.SUBAGENTS, lists.SCOPE_CHAT)
    assert seen["rows"] == ["Subagents aren't listed for Codex chats yet."]


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
