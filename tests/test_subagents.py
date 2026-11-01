"""Subagents (#468): the parent's Agent calls, each subagent's own
transcript, pairing them, their status and their file changes. Windowless;
every record is synthetic (tests/records.py)."""
import json
import os

from records import (agent_call, agent_completed, agent_launched, assistant_block, handback,
                     handback_attachment, lines, sidechain, task_notification, text_block,
                     tool_result, tool_use_block, user_text)
from thechatplace import lists, platform_paths, properties, subagents
from thechatplace.changes import FileEdit, by_file, file_text
from thechatplace.sessions import SessionInfo
from thechatplace.subagents import DONE, FAILED, RUNNING, STOPPED, SubagentTracker
from thechatplace.transcript import (AGENT_COMPLETED, AGENT_FAILED, AGENT_LAUNCHED, AGENT_STOPPED,
                                     ASSISTANT, TOOL, USER, TranscriptParser, parse_lines)


def _at(minute, second=0):
    return f"2026-10-06T12:{minute:02d}:{second:02d}.000Z"


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")


def _append(path, records):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines(*records)) + "\n")


def _subagent(parent, agent_id, records, **meta):
    """A subagent's transcript, and its meta file, beside ``parent``."""
    folder = platform_paths.subagents_dir(parent)
    path = folder / f"agent-{agent_id}.jsonl"
    _write(path, [sidechain(r, agent_id) for r in records])
    if meta:
        (folder / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def _review(agent_id="a1", tool_id="toolu_ag1", start=0, end=4, edit=None):
    """A code-reviewer's conversation: the prompt, a tool call, a reply."""
    records = [user_text("Review the branch against main.", timestamp=_at(start)),
               assistant_block(tool_use_block("Bash", {"command": "git diff main"}, "tb1"),
                               "s1", timestamp=_at(start, 30)),
               tool_result("tb1", "diff output", timestamp=_at(start, 40))]
    if edit:
        records += [assistant_block(tool_use_block("Edit", {"file_path": edit, "old_string": "a",
                                                            "new_string": "a\nb\nc"}, "te1"),
                                    "s2", timestamp=_at(start + 1)),
                    tool_result("te1", "The file has been updated.",
                                toolUseResult={"filePath": edit}, timestamp=_at(start + 1, 5))]
    reply = assistant_block(text_block("Two problems found."), "s3", timestamp=_at(end))
    reply["message"]["stop_reason"] = "end_turn"
    return records + [reply]


# -- the parent's transcript -----------------------------------------------------------------


def test_an_agent_call_waited_for_is_recorded_with_its_report():
    transcript = parse_lines(lines(
        user_text("Please review this."),
        agent_call("toolu_ag1", "code-reviewer", "Review the branch", "Review the branch against main."),
        agent_completed("toolu_ag1", "a1", "Two problems found.", "code-reviewer"),
        assistant_block(text_block("The reviewer found two problems."), "m2")))
    [call] = transcript.agents
    assert (call.tool_id, call.agent_type, call.description, call.turn) == (
        "toolu_ag1", "code-reviewer", "Review the branch", 1)
    assert call.prompt == "Review the branch against main."
    assert (call.agent_id, call.status, call.report) == ("a1", AGENT_COMPLETED, "Two problems found.")
    # The tool call is activity, named by what it was for.
    assert [m.text for m in transcript.messages if m.kind == TOOL] == ["Agent: Review the branch"]


def test_a_plain_text_result_loses_its_id_and_usage_lines():
    transcript = parse_lines(lines(
        user_text("go"),
        agent_call("toolu_ag1", "Explore", "Find it", "Find the uploader."),
        tool_result("toolu_ag1", [text_block("Found it in up.py.\nagentId: a9 (for resuming)\n"
                                             "<usage>total_tokens: 5</usage>")])))
    call = transcript.agents[0]
    assert (call.status, call.report, call.agent_id) == (AGENT_COMPLETED, "Found it in up.py.", "a9")


def test_a_background_agent_is_launched_then_notified():
    transcript = parse_lines(lines(
        user_text("go"),
        agent_call("toolu_ag1", "code-reviewer", "Review", "Review it.", background=True),
        agent_launched("toolu_ag1", "a1")))
    call = transcript.agents[0]
    assert (call.status, call.agent_id, call.background) == (AGENT_LAUNCHED, "a1", True)
    parser = TranscriptParser()
    parser.feed(lines(user_text("go"),
                      agent_call("toolu_ag1", "code-reviewer", "Review", "Review it.", background=True),
                      agent_launched("toolu_ag1", "a1"),
                      task_notification("a1", "completed", "All good.", tool_id="toolu_ag1",
                                        timestamp=_at(9))))
    call = parser.transcript.agents[0]
    assert (call.status, call.report, call.finished_at) == (AGENT_COMPLETED, "All good.", _at(9))
    assert parser.transcript.agent_notes["a1"].status == AGENT_COMPLETED
    # The notification is harness activity, never a message of yours.
    assert not [m for m in parser.transcript.messages if m.kind == USER and "task" in m.text]


def test_failed_killed_and_unfinished_agents():
    transcript = parse_lines(lines(
        user_text("go"),
        agent_call("toolu_f", "Explore", "Look", "Look."),
        tool_result("toolu_f", "Agent type 'Nope' not found", is_error=True),
        agent_call("toolu_k", "Plan", "Plan", "Plan it.", background=True),
        agent_launched("toolu_k", "ak"),
        task_notification("ak", "killed"),
        agent_call("toolu_s", "Plan", "Plan again", "Plan it again.", background=True),
        agent_launched("toolu_s", "as"),
        task_notification("as", "stopped", summary="Didn't finish before the session ended")))
    statuses = {c.tool_id: (c.status, c.report) for c in transcript.agents}
    assert statuses["toolu_f"] == (AGENT_FAILED, "Agent type 'Nope' not found")
    assert statuses["toolu_k"][0] == AGENT_STOPPED
    assert statuses["toolu_s"] == (AGENT_STOPPED, "Didn't finish before the session ended")


def test_a_hand_back_marks_a_background_agent_done():
    transcript = parse_lines(lines(
        user_text("go"),
        agent_call("toolu_ag1", "code-reviewer", "Review", "Review it.", background=True),
        agent_launched("toolu_ag1", "a1"),
        handback_attachment("a1")))
    assert transcript.agents[0].status == AGENT_COMPLETED
    # Still not shown as a message from another session.
    assert all(m.kind != "peer" for m in transcript.messages)


def test_the_main_transcript_still_skips_sidechain_records():
    transcript = parse_lines(lines(user_text("hi"),
                                   sidechain(assistant_block(text_block("subagent words"), "x"), "a1"),
                                   assistant_block(text_block("hello"), "m1")))
    assert [m.text for m in transcript.messages] == ["hi", "hello"]


# -- a subagent's own transcript ------------------------------------------------------------


def test_a_subagent_transcript_reads_as_claude_asking_and_the_subagent_answering():
    parser = TranscriptParser(sidechain=True, assistant_name="code-reviewer", user_name="Claude")
    parser.feed(lines(*[sidechain(r, "a1") for r in _review()]))
    transcript = parser.transcript
    conversation = [m for m in transcript.messages if not m.is_activity]
    assert [m.list_line() for m in conversation] == [
        "Claude: Review the branch against main.", "code-reviewer: Two problems found."]
    assert (transcript.first_time, transcript.last_time) == (_at(0), _at(4))
    assert transcript.ended


def test_a_hand_back_is_the_subagents_last_reply():
    parser = TranscriptParser(sidechain=True, assistant_name="Explore", user_name="Claude")
    parser.feed(lines(*[sidechain(r, "a1") for r in [
        user_text("Find it."), handback("It's in up.py."),
        tool_result("toolu_hb", "Handed back.")]]))
    assert parser.transcript.handback == "It's in up.py."
    assert [m.list_line() for m in parser.transcript.messages if not m.is_activity][-1] == \
        "Explore: It's in up.py."
    assert not parser.transcript.ended


# -- finding and pairing --------------------------------------------------------------------


def test_subagents_are_found_beside_the_transcript_and_paired_with_their_calls(tmp_path):
    parent = tmp_path / "proj" / "sess.jsonl"
    _write(parent, [user_text("Review please", timestamp=_at(0)),
                    agent_call("toolu_ag1", "code-reviewer", "Review the branch",
                               "Review the branch against main.", timestamp=_at(0)),
                    agent_completed("toolu_ag1", "a1", "Two problems found.", "code-reviewer",
                                    timestamp=_at(4))])
    _subagent(parent, "a1", _review(), agentType="code-reviewer", description="Review the branch",
              toolUseId="toolu_ag1", spawnDepth=1)
    tracker = SubagentTracker(parent)
    assert tracker.refresh()
    [sub] = tracker.subagents(parse_lines(parent.read_text("utf-8").splitlines()), live=False)
    assert (sub.agent_type, sub.description, sub.status, sub.turn) == (
        "code-reviewer", "Review the branch", DONE, 1)
    assert (sub.messages, sub.tool_calls, sub.report) == (2, 1, "Two problems found.")
    assert sub.row() == ("code-reviewer: Review the branch. Done, 2 messages, 1 tool call, "
                         "4 minutes")
    detail = sub.detail()
    assert "Prompt:\nReview the branch against main." in detail
    assert detail.endswith("Final report:\nTwo problems found.")
    assert subagents.count(parent) == 1
    # Still being written as it was read: its reader is kept for now.
    assert tracker.files["a1"]._reader is not None
    # Nothing new: nothing changed, and the finished one's reader is let go.
    assert not tracker.refresh()
    tracker.subagents(parse_lines(parent.read_text("utf-8").splitlines()), live=False)
    assert tracker.files["a1"]._reader is None


def test_status_without_a_result_depends_on_the_session_working(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("go"),
             agent_call("toolu_ag1", "Explore", "Look around", "Look around.", background=True),
             agent_launched("toolu_ag1", "a1")]
    _write(parent, calls)
    records = _review()[:3]  # still working: no reply yet
    _subagent(parent, "a1", records, agentType="Explore", toolUseId="toolu_ag1")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    transcript = parse_lines(lines(*calls))
    [running] = tracker.subagents(transcript, live=True, now_ms=subagents._ms(_at(2)))
    assert running.status == RUNNING and running.report == ""
    assert running.row(now_ms=subagents._ms(_at(2))).endswith("Running, 1 message, 1 tool call, "
                                                              "2 minutes so far")
    assert "No report yet." in running.detail()
    # A background one goes on after the parent's turn: written lately, it's
    # still at work though the session looks idle.
    assert tracker.subagents(transcript, live=False)[0].status == RUNNING
    # Long quiet, with nothing saying it ended: it didn't finish.
    later = tracker.files["a1"].modified_ms + subagents.RECENT_MS + 1000
    [stopped] = tracker.subagents(transcript, live=False, now_ms=later)
    assert stopped.status == STOPPED and "didn't finish" in stopped.note


def test_stopped_by_you_and_failed(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("go"),
             agent_call("toolu_1", "Explore", "One", "One.", background=True),
             agent_launched("toolu_1", "a1"),
             agent_call("toolu_2", "Explore", "Two", "Two."),
             tool_result("toolu_2", "It broke.", is_error=True)]
    _write(parent, calls)
    _subagent(parent, "a1", _review()[:3], agentType="Explore", toolUseId="toolu_1",
              stoppedByUser=True)
    _subagent(parent, "a2", _review()[:1], agentType="Explore", toolUseId="toolu_2")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    found = {s.agent_id: s for s in tracker.subagents(parse_lines(lines(*calls)), live=True)}
    assert found["a1"].status == STOPPED
    assert (found["a2"].status, found["a2"].report) == (FAILED, "It broke.")
    assert found["a2"].ended_text() == "Subagent Explore failed."


def test_a_background_subagents_hand_back_is_its_report(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("go"),
             agent_call("toolu_1", "code-reviewer", "Review", "Review.", background=True),
             agent_launched("toolu_1", "a1")]
    _write(parent, calls)
    _subagent(parent, "a1", [user_text("Review.", timestamp=_at(0)),
                             handback("Looks fine.", timestamp=_at(3)),
                             tool_result("toolu_hb", "ok", timestamp=_at(3, 1))],
              agentType="code-reviewer", toolUseId="toolu_1", requestShape="background")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    [sub] = tracker.subagents(parse_lines(lines(*calls)), live=True)
    assert (sub.status, sub.report, sub.background) == (DONE, "Looks fine.", True)
    assert sub.ended_text() == "Subagent code-reviewer finished."


def test_a_running_subagent_is_read_as_it_grows(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("go"), agent_call("toolu_1", "Explore", "Look", "Look.")]
    _write(parent, calls)
    records = _review()
    path = _subagent(parent, "a1", records[:1], agentType="Explore", toolUseId="toolu_1")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    transcript = parse_lines(lines(*calls))
    assert tracker.subagents(transcript, live=True)[0].messages == 1
    reader = tracker.files["a1"]._reader
    # A line still being written waits; it isn't counted as unreadable.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sidechain(records[1], "a1"))[:20])
    tracker.refresh()
    assert tracker.subagents(transcript, live=True)[0].unreadable_lines == 0
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sidechain(records[1], "a1"))[20:] + "\n")
    _append(path, [sidechain(r, "a1") for r in records[2:]])
    assert tracker.refresh()
    assert tracker.files["a1"]._reader is reader  # read on, not again from the start
    [sub] = tracker.subagents(transcript, live=True)
    # Its last reply ended its turn: done, though the parent hasn't heard yet.
    assert (sub.messages, sub.tool_calls, sub.report) == (2, 1, "Two problems found.")
    assert sub.status == DONE
    tracker.refresh()
    tracker.subagents(transcript, live=True)
    assert tracker.files["a1"]._reader is None  # let go once it stopped growing


def test_bad_lines_are_counted_never_raised(tmp_path):
    parent = tmp_path / "sess.jsonl"
    _write(parent, [user_text("go")])
    path = _subagent(parent, "a1", _review(), agentType="Explore")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n[1, 2]\n")
    (path.parent / "agent-a1.meta.json").write_text("{broken", encoding="utf-8")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    [sub] = tracker.subagents(parse_lines(lines(user_text("go"))), live=False)
    assert sub.unreadable_lines == 2
    assert "Couldn't read 2 lines of its transcript." in sub.detail()
    assert sub.agent_type == ""  # the meta file couldn't be read
    assert sub.name == "subagent" and sub.prompt == "Review the branch against main."


def test_a_call_without_a_file_and_a_file_without_a_call(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("go", timestamp=_at(0)),
             agent_call("toolu_1", "Plan", "Plan it", "Make a plan.", timestamp=_at(0)),
             agent_completed("toolu_1", "gone", "The plan.", "Plan", timestamp=_at(1))]
    _write(parent, calls)
    _subagent(parent, "a2", _review(start=5, end=6), agentType="code-reviewer",
              description="Orphan review")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    found = tracker.subagents(parse_lines(lines(*calls)), live=False)
    assert [s.agent_type for s in found] == ["Plan", "code-reviewer"]  # oldest first
    plan, orphan = found
    assert plan.path is None and plan.status == DONE and plan.report == "The plan."
    assert plan.row() == "Plan: Plan it. Done, its conversation isn't on disk"
    assert orphan.turn == 0 and orphan.prompt == "Review the branch against main."


def test_a_subagent_two_levels_down_belongs_to_the_starting_turn(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("go"), agent_call("toolu_1", "general-purpose", "Plan", "Plan.")]
    _write(parent, calls)
    _subagent(parent, "a1", _review(), agentType="general-purpose", toolUseId="toolu_1")
    _subagent(parent, "a2", _review(), agentType="Plan", parentAgentId="a1")
    _subagent(parent, "a3", _review(), agentType="Explore", parentAgentId="a2")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    found = {s.agent_id: s.turn for s in tracker.subagents(parse_lines(lines(*calls)), live=False)}
    assert found == {"a1": 1, "a2": 1, "a3": 1}


def test_subagent_edits_are_marked_and_belong_to_the_starting_turn(tmp_path):
    parent = tmp_path / "sess.jsonl"
    calls = [user_text("first"), assistant_block(text_block("ok"), "m1"),
             user_text("second"),
             agent_call("toolu_1", "general-purpose", "Fix it", "Fix the bug."),
             agent_completed("toolu_1", "a1", "Fixed.")]
    _write(parent, calls)
    target = str(tmp_path / "main.py")
    _subagent(parent, "a1", _review(edit=target), agentType="general-purpose", toolUseId="toolu_1")
    # One the general-purpose subagent started in turn: same turn.
    _subagent(parent, "a3", _review(edit=target, start=2, end=3), agentType="Explore",
              parentAgentId="a1", toolUseId="toolu_inner")
    tracker = SubagentTracker(parent)
    tracker.refresh()
    found = {s.agent_id: s for s in tracker.subagents(parse_lines(lines(*calls)), live=False)}
    assert found["a1"].turn == 2 and found["a3"].turn == 2
    assert found["a3"].started_by == "general-purpose"
    edits = subagents.edits_of(found.values())
    assert [(e.by, e.turn) for e in edits] == [("general-purpose", 2), ("Explore", 2)]
    files = by_file(edits + [FileEdit(target, "Edit", 2)])
    assert [f.by for f in files] == ["general-purpose", "Explore", ""]
    assert file_text(files[0]).startswith(f"{target}: 2 lines added, by general-purpose.")
    assert "Changed 1 file: main.py, 2 lines added." in found["a1"].detail()


# -- Lists ------------------------------------------------------------------------------------


def _subs():
    return [subagents.Subagent("a1", path=None, agent_type="Explore", description="Find it",
                               status=DONE, turn=1, report="It's in up.py.", prompt="Find it."),
            subagents.Subagent("a2", path=None, agent_type="code-reviewer", description="Review",
                               status=RUNNING, turn=2, prompt="Review the branch.")]


def test_lists_subagents_newest_first_and_by_turn():
    items = lists.build(lists.SUBAGENTS, lists.SCOPE_CHAT, [], None, [], [], str, subagents=_subs())
    assert [i.row.split(":")[0] for i in items] == ["code-reviewer", "Explore"]
    assert items[0].copy_what == "the prompt" and items[1].copy_what == "the final report"
    assert items[1].copy_text == "It's in up.py." and items[1].subagent.agent_id == "a1"
    assert items[1].detail_name == "Subagent Explore"
    from thechatplace.transcript import ChatMessage
    message = ChatMessage(ASSISTANT, "done", turn=1)
    items = lists.build(lists.SUBAGENTS, lists.SCOPE_MESSAGE, [message], message, [], [], str,
                        subagents=_subs())
    assert [i.subagent.agent_id for i in items] == ["a1"]
    assert lists.empty_text(lists.SUBAGENTS, lists.SCOPE_MESSAGE) == \
        "No subagents in this message's turn."
    assert lists.count_text(lists.SUBAGENTS, 1, 2) == "Subagents (1 of 2)"


def test_durations_in_words():
    assert subagents.duration_words(0) == "under a minute"
    assert subagents.duration_words(60_000) == "1 minute"
    assert subagents.duration_words(4 * 60_000 + 59_000) == "4 minutes"
    assert subagents.duration_words(65 * 60_000) == "1 hour 5 minutes"
    assert subagents.duration_words(120 * 60_000) == "2 hours"


def test_properties_say_how_many_subagents():
    info = SessionInfo("desktop", "local_x", "T", "C:\\x", "cli-x")
    transcript = parse_lines(lines(user_text("hi")))
    rows = properties.session_properties(info, transcript, subagent_count=3)
    assert "Subagents: 3" in rows
    assert not any(r.startswith("Subagents") for r in properties.session_properties(info, transcript))


def test_count_is_zero_without_a_folder(tmp_path):
    assert subagents.count(tmp_path / "none.jsonl") == 0
    assert subagents.count(None) == 0
    assert os.fspath(platform_paths.subagents_dir(tmp_path / "s.jsonl")) == \
        os.fspath(tmp_path / "s" / "subagents")
