"""Rewind to Here and Branch from Here (#468), without a window: where each
message sits in Claude Code's record chain, which messages have file
checkpoints, what the dialog says each choice will do, and putting files
back through a (fake) ``claude --rewind-files``."""
import json
import sys
from pathlib import Path

import pytest

from thechatplace import claude_cli as cli
from thechatplace import rewind
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.transcript import ASSISTANT, USER, ChatMessage, parse_lines

from records import (assistant_block, lines, other, text_block, tool_result, tool_use_block,
                     user_text)

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


def chained(*records):
    """Records linked as Claude Code links them: each one's parentUuid is
    the record before it."""
    previous = None
    for record in records:
        if "uuid" in record:
            record["parentUuid"] = previous
            previous = record["uuid"]
    return records


def snapshot(message_uuid):
    # The shape Claude Code 2.1.296 writes, with checkpoints on.
    return other("file-history-snapshot", messageId=message_uuid, isSnapshotUpdate=False,
                 snapshot={"messageId": message_uuid, "trackedFileBackups": {}})


def two_turns(checkpointed=True):
    first = user_text("Create a.txt")
    write = assistant_block(tool_use_block("Write", {"file_path": "a.txt", "content": "one"},
                                           "t1"), "m1")
    result = tool_result("t1", "File created.", toolUseResult={
        "type": "create", "filePath": "a.txt", "content": "one", "structuredPatch": []})
    done = assistant_block(text_block("DONE"), "m2")
    more = assistant_block(text_block("And more."), "m2")  # the same reply, merged
    turn_end = other("system", uuid="sys-1", subtype="turn_duration")
    second = user_text("Change a.txt to two\nPlease.")
    edit = assistant_block(tool_use_block("Write", {"file_path": "a.txt", "content": "two"},
                                          "t2"), "m3")
    result2 = tool_result("t2", "Updated.", toolUseResult={
        "type": "update", "filePath": "a.txt", "content": "two", "structuredPatch": []})
    records = list(chained(first, write, result, done, more, turn_end, second, edit, result2))
    if checkpointed:
        records.insert(1, snapshot(first["uuid"]))
        records.insert(8, snapshot(second["uuid"]))
    return records, first, done, more, turn_end, second


def test_messages_carry_their_place_in_the_record_chain():
    records, first, done, more, turn_end, second = two_turns()
    tr = parse_lines(lines(*records))
    users = [m for m in tr.messages if m.kind == USER]
    assert [(m.uuid, m.parent_uuid) for m in users] == [
        (first["uuid"], ""), (second["uuid"], turn_end["uuid"])]
    reply = next(m for m in tr.messages if m.kind == ASSISTANT)
    # Merged from two records: the cut keeps all of it.
    assert reply.text == "DONE\n\nAnd more."
    assert reply.uuid == more["uuid"] and reply.parent_uuid == done["parentUuid"]
    assert tr.checkpoints == {first["uuid"], second["uuid"]}


def test_a_record_id_that_could_read_as_an_option_is_not_kept():
    record = user_text("hi", uuid="--bare", parentUuid="-p")
    message = parse_lines(lines(record)).messages[0]
    assert (message.uuid, message.parent_uuid) == ("", "")
    assert not rewind.rewindable(message)


def test_bad_snapshot_records_are_skipped_not_counted_as_unreadable():
    tr = parse_lines(lines(other("file-history-snapshot", messageId=7),
                           other("file-history-snapshot")))
    assert tr.checkpoints == set() and tr.unreadable_lines == 0


def test_plan_for_the_second_message_names_the_files_and_keeps_the_original():
    records, _first, _d, _m, turn_end, second = two_turns()
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    plan = rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True)
    assert plan.cut == turn_end["uuid"]
    assert plan.default_choice() == rewind.BOTH
    text = plan.describe(rewind.BOTH)
    assert "Fixes (rewound)" in text and "Change a.txt to two" in text
    assert "Fixes is kept as it is" in text
    assert "Claude changed 1 file since this message: a.txt." in text
    assert "isn't changed" in plan.describe(rewind.FILES)
    assert "files" not in plan.describe(rewind.CONVERSATION).split("reply box")[0].lower()


def test_the_first_message_starts_with_no_history():
    records, *_ = two_turns()
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][0]
    plan = rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True)
    assert plan.cut == ""
    assert "with no history" in plan.describe(rewind.CONVERSATION)
    # Both turns' edits: everything since the first message.
    assert len(plan.edits) == 2


def test_without_checkpoints_only_the_conversation_can_go_back():
    records, *_ = two_turns(checkpointed=False)
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    plan = rewind.plan_rewind("Fixes", tr, message, can_rewind_files=True)
    assert plan.default_choice() == rewind.CONVERSATION
    assert not plan.allowed(rewind.BOTH) and not plan.allowed(rewind.FILES)
    assert "no file checkpoints" in plan.describe(rewind.FILES)


def test_a_chat_that_is_not_ours_says_why_its_files_stay():
    records, *_ = two_turns()
    tr = parse_lines(lines(*records))
    message = [m for m in tr.messages if m.kind == USER][1]
    plan = rewind.plan_rewind("Desk", tr, message, can_rewind_files=False,
                              files_reason="This is a Claude desktop app session.")
    assert plan.default_choice() == rewind.CONVERSATION
    assert "desktop app session" in plan.describe(rewind.BOTH)


def test_many_files_are_counted_not_all_named():
    records = [user_text("Do lots")]
    for n in range(12):
        records += [assistant_block(tool_use_block("Write", {"file_path": f"f{n}.txt",
                                                             "content": "x"}, f"t{n}"), f"m{n}"),
                    tool_result(f"t{n}", "ok", toolUseResult={"type": "create",
                                                              "filePath": f"f{n}.txt",
                                                              "content": "x",
                                                              "structuredPatch": []})]
    tr = parse_lines(lines(*chained(*records)))
    plan = rewind.plan_rewind("Lots", tr, tr.messages[0], can_rewind_files=True)
    assert "Claude changed 12 files" in plan.changed_files_text()
    assert "and 4 more" in plan.changed_files_text()


def test_titles():
    assert rewind.rewound_title("Fix bug") == "Fix bug (rewound)"
    assert rewind.branch_title("Fix bug") == "Fix bug (branch)"


# -- what the CLI said, in words ------------------------------------------------------------


def message(text="Change a.txt to two"):
    return parse_lines(lines(user_text(text))).messages[0]


WARNING = "Permission allow rule (..\\settings.json): Bash(x *) has a wildcard.\n"


def test_success_says_how_many_files():
    result = rewind.result_from_output(
        0, "Files rewound to state at message abc\n", WARNING, "Fixes", message(), 3)
    assert result.ok
    assert result.text == ("Files in Fixes restored to how they were before your message "
                           "\"Change a.txt to two\": 3 files.")


@pytest.mark.parametrize("stderr,said", [
    ("Error: File rewinding is not enabled.", "file checkpoints weren't kept for this chat"),
    ("Error: --rewind-files requires a user message UUID, but x is not a user message in "
     "this session", "it doesn't know that message in this chat"),
    ("No file checkpoint found for this message.", "there's no file checkpoint for that "
                                                   "message"),
    ("No conversation found with session ID: x", "it couldn't find this chat's "
                                                 "conversation"),
    ("error: unknown option '--rewind-files'", "this version of Claude Code can't rewind "
                                               "files; update it and try again"),
    ("Error: disk full", "disk full"),
    ("", "it stopped with exit code 1"),
])
def test_failures_say_why_in_plain_words(stderr, said):
    result = rewind.result_from_output(1, "", WARNING + stderr, "Fixes", message(), 1)
    assert not result.ok
    assert result.text == f"Claude Code couldn't restore the files: {said}."


def test_run_refuses_a_chat_that_is_not_ours_before_starting_anything(tmp_path):
    def popen(*a, **k):
        pytest.fail("must not start")
    result = rewind.run_rewind_files("claude", "dddd-2222", message(), str(tmp_path), "Desk",
                                     1, {"aaaa-1111"}, {"dddd-2222"}, popen=popen)
    assert not result.ok and "desktop app" in result.text


def test_run_with_a_missing_folder_says_so(tmp_path):
    msg = parse_lines(lines(user_text("x"))).messages[0]
    result = rewind.run_rewind_files("claude", "aaaa-1111", msg, str(tmp_path / "gone"), "Mine",
                                     1, {"aaaa-1111"}, set(),
                                     popen=lambda *a, **k: pytest.fail("must not start"))
    assert not result.ok and "doesn't exist" in result.text


def _fake_command(monkeypatch):
    """The real process path with fake_claude.py standing in for claude."""
    real = cli.build_rewind_files_command

    def build(executable, *args):
        return [sys.executable, str(FAKE_CLAUDE), *real(executable, *args)[1:]]
    monkeypatch.setattr(rewind, "build_rewind_files_command", build)


def test_run_puts_files_back_through_a_real_child_with_checkpoints_on(tmp_path, monkeypatch):
    _fake_command(monkeypatch)
    log = tmp_path / "fake.log"
    msg = parse_lines(lines(user_text("Change a.txt"))).messages[0]
    env = cli.child_environment({**__import__("os").environ, "FAKE_CLAUDE_LOG": str(log),
                                 cli.CHECKPOINT_VAR: "0"})
    result = rewind.run_rewind_files("claude", "aaaa-1111", msg, str(tmp_path), "Mine", 2,
                                     {"aaaa-1111"}, set(), env=env)
    assert result.ok, result.text
    assert result.text.endswith(": 2 files.")
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen["args"] == ["-p", "--resume", "aaaa-1111", "--rewind-files", msg.uuid]
    assert seen["checkpoints"] == "1"
    assert (tmp_path / "rewound.txt").read_text(encoding="utf-8") == msg.uuid


def test_run_reports_the_clis_refusal(tmp_path, monkeypatch):
    _fake_command(monkeypatch)
    msg = parse_lines(lines(user_text("Change a.txt"))).messages[0]
    env = cli.child_environment({**__import__("os").environ,
                                 "FAKE_CLAUDE_MODE": "rewind-unknown"})
    result = rewind.run_rewind_files("claude", "aaaa-1111", msg, str(tmp_path), "Mine", 2,
                                     {"aaaa-1111"}, set(), env=env)
    assert not result.ok
    assert result.text == ("Claude Code couldn't restore the files: it doesn't know "
                           "that message in this chat.")


def test_run_stops_a_claude_that_hangs(tmp_path, monkeypatch):
    def build(executable, *args):
        return [sys.executable, "-c", "import time; time.sleep(60)"]
    monkeypatch.setattr(rewind, "build_rewind_files_command", build)
    msg = parse_lines(lines(user_text("x"))).messages[0]
    result = rewind.run_rewind_files("claude", "aaaa-1111", msg, str(tmp_path), "Mine", 1,
                                     {"aaaa-1111"}, set(), timeout=1.0)
    assert not result.ok and "took too long" in result.text


# -- the branch's record ----------------------------------------------------------------------


def test_a_branch_keeps_its_cut_through_the_store(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession(cli_session_id="nnnn-3333", title="Fixes (rewound)", cwd=str(tmp_path),
                         started=False, fork_source="aaaa-1111", forked_from="Fixes",
                         fork_at="sys-1"))
    again = OwnSessionStore(tmp_path / "sessions.json").get("nnnn-3333")
    assert (again.fork_source, again.fork_at, again.started) == ("aaaa-1111", "sys-1", False)


@pytest.mark.parametrize("item", [
    {"fork_source": "aaaa-1111", "fork_at": "--bare"},
    {"fork_source": "", "fork_at": "sys-1"},      # a cut means nothing without a source
])
def test_a_hand_edited_cut_is_dropped(tmp_path, item):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"version": 1, "sessions": [
        {"cli_session_id": "nnnn-3333", "title": "t", "cwd": str(tmp_path), **item}]}),
        encoding="utf-8")
    assert OwnSessionStore(path).get("nnnn-3333").fork_at == ""


def test_unstarted_branch_line():
    from thechatplace.rewind import unstarted_line
    branch = OwnSession(cli_session_id="n-1", title="t", cwd=".", started=False,
                        fork_source="s-1", forked_from="Fixes", fork_at="u-1")
    assert "branch of Fixes" in unstarted_line(branch)
    fresh = OwnSession(cli_session_id="n-1", title="t", cwd=".", started=False,
                       forked_from="Fixes")
    assert "no history" in unstarted_line(fresh)
    copy = OwnSession(cli_session_id="n-1", title="t", cwd=".", started=False,
                      fork_source="s-1", forked_from="Fixes")
    assert unstarted_line(copy) == ""  # Continue Here's own wording stays
    assert unstarted_line(None) == ""


def test_a_steered_message_is_not_a_place_to_go_back_to():
    # Taken in mid-turn (a queued_command attachment): no checkpoint of its own.
    steer = other("attachment", uuid="att-1", parentUuid="a-1", attachment={
        "type": "queued_command", "commandMode": "prompt", "prompt": "Also do the docs"})
    tr = parse_lines(lines(user_text("Fix it"), steer))
    steered = tr.messages[-1]
    assert steered.kind == USER and steered.text == "Also do the docs"
    assert not rewind.rewindable(steered) and steered.uuid == ""


def test_pictures_are_left_out_of_the_draft_and_counted():
    msg = parse_lines(lines(user_text("Look at this\n(image attached)\n(image attached)"))
                      ).messages[0]
    assert rewind.draft_text(msg) == ("Look at this", 2)
    assert rewind.draft_text(message("Plain")) == ("Plain", 0)


def test_files_a_subagent_changed_are_said_to_stay():
    """Claude Code doesn't put back what a subagent wrote (checked with
    2.1.296), so What Happens names those files as staying (#468)."""
    from thechatplace.changes import FileEdit
    plan = rewind.RewindPlan(chat_title="Fixes", message=ChatMessage(kind=USER, text="Go", turn=2),
                             cut="c1",
                             subagent_edits=[FileEdit(path="src/b.txt", tool="Write", turn=2,
                                                      by="code-reviewer")])
    said = plan.describe(rewind.FILES)
    assert "1 file changed by subagents stay as they are" in said and "b.txt" in said
    assert rewind.RewindPlan(chat_title="Fixes", message=ChatMessage(kind=USER, text="Go"),
                             cut="c1").subagent_files_text() == ""
