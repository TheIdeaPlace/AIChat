"""Synthetic Copilot CLI session folders, shaped like what 1.0.103 writes."""
import json

import pytest

from thechatplace import platform_paths
from thechatplace.hub import finished_turns
from thechatplace.providers.base import Snapshot
from thechatplace.providers.copilot import (STALE_TURN_SECONDS, CopilotProvider,
                                            parse_events, parse_workspace,
                                            state_from_events)
from thechatplace.session_controller import SessionController
from thechatplace.sessions import (IDLE, NEEDS_YOU, VIEW_COPILOT, WORKING, SessionInfo,
                                   in_view)
from thechatplace.transcript import (ASSISTANT, DENIED, ERROR, INTERRUPTED, TOOL,
                                     TOOL_RESULT, USER)

ONE = "11111111-2222-3333-4444-555555555555"
TWO = "66666666-7777-8888-9999-000000000000"


def event(kind, n, **data):
    return {"type": kind, "data": data, "id": f"e{n}", "parentId": None,
            "timestamp": "2026-01-02T03:04:05.000Z"}


def answered_turn(start=0, question="Hi", answer="Ready"):
    return [
        event("session.start", start, sessionId=ONE, version=1, copilotVersion="1.0.103"),
        event("session.model_change", start + 1, newModel="auto"),
        event("user.message", start + 2, content=question,
              transformedContent="<current_datetime>x</current_datetime>\n\n" + question),
        event("system.message", start + 3, role="system", content="You are the GitHub Copilot CLI"),
        event("assistant.turn_start", start + 4, turnId="0"),
        event("assistant.message", start + 5, messageId="m1", model="gpt-test", content=answer,
              toolRequests=[]),
        event("assistant.turn_end", start + 6, turnId="0"),
        event("session.shutdown", start + 7, accountingSnapshot={}),
    ]


def write_session(root, native_id=ONE, events=(), name="Build the thing", raw_yaml=None,
                  cwd="C:\\work\\example"):
    folder = root / native_id
    folder.mkdir(parents=True)
    (folder / "workspace.yaml").write_text(raw_yaml if raw_yaml is not None else (
        f"id: {native_id}\ncwd: {cwd}\nclient_name: github/cli\n"
        f"name: '{name}'\nuser_named: false\n"
        "created_at: 2026-01-02T03:04:05.000Z\nupdated_at: 2026-01-02T03:05:05.000Z\n"),
        encoding="utf-8")
    if events is not None:
        (folder / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return folder


def info_for(native_id=ONE):
    return SessionInfo("external", f"copilot:{native_id}", "t", "", native_id, provider="copilot")


def test_workspace_yaml_scalars():
    values = parse_workspace(
        "id: abc\ncwd: C:\\Users\\me\\code\nname: 'It''s: quoted'\n"
        'other: "tab\\there"\nplain: value # comment\nblock: |\n  text\n  - item\n')
    assert values["id"] == "abc"
    assert values["cwd"] == "C:\\Users\\me\\code"
    assert values["name"] == "It's: quoted"
    assert values["other"] == "tab\there"
    assert values["plain"] == "value"
    assert "block" not in values  # never guessed at


def test_lists_sessions_without_starting_copilot(tmp_path):
    write_session(tmp_path, ONE, answered_turn())
    write_session(tmp_path, TWO, events=None)  # failed before its first prompt
    (tmp_path / ".session-operation-locks").mkdir()
    snapshot = CopilotProvider(tmp_path).collect()
    assert [s.key for s in snapshot.sessions] == [f"copilot:{ONE}"]
    session = snapshot.sessions[0]
    assert session.provider == "copilot" and session.title == "Build the thing"
    assert session.cwd == "C:\\work\\example" and session.repo == "example"
    assert session.state == IDLE
    assert session.created_ms == 1767323045000
    assert session.last_activity_ms >= 1767323105000
    assert snapshot.desktop_cli_ids == set() and snapshot.live == {}


def test_missing_copilot_folder_is_an_empty_list(tmp_path):
    assert CopilotProvider(tmp_path / "nothing").collect().sessions == []


def test_title_falls_back_to_first_prompt(tmp_path):
    raw = f"id: {ONE}\ncwd: C:\\x\nname: |\n  block\n"
    write_session(tmp_path, ONE, answered_turn(question="First line\nsecond"), raw_yaml=raw)
    assert CopilotProvider(tmp_path).collect().sessions[0].title == "First line"


def test_session_with_no_prompt_is_left_out(tmp_path):
    raw = f"id: {ONE}\ncwd: C:\\x\n"
    write_session(tmp_path, ONE, answered_turn()[:2], raw_yaml=raw)
    assert CopilotProvider(tmp_path).collect().sessions == []


def test_unreadable_folders_are_counted_not_raised(tmp_path):
    write_session(tmp_path, ONE, answered_turn())
    write_session(tmp_path, TWO, answered_turn(), raw_yaml=f"id: {ONE}\n")  # id mismatch
    (tmp_path / "not a safe id").mkdir()
    snapshot = CopilotProvider(tmp_path).collect()
    assert [s.cli_session_id for s in snapshot.sessions] == [ONE]
    assert snapshot.unreadable_files == 1


def test_reads_messages_tools_and_denials():
    events = answered_turn()[:5] + [
        event("assistant.message", 10, content="", toolRequests=[{"name": "apply_patch"}]),
        event("tool.execution_start", 11, toolCallId="c1", toolName="apply_patch",
              toolTitle="Applying patch",
              arguments="*** Begin Patch\n*** Add File: a.txt\n+hello\n*** End Patch\n"),
        event("permission.requested", 12, requestId="r1",
              permissionRequest={"kind": "write", "intention": "Create file",
                                 "fileName": "C:/work/a.txt"}),
        event("permission.completed", 13, requestId="r1",
              result={"kind": "denied-interactively-by-user"}),
        event("tool.execution_complete", 14, toolCallId="c1", success=False,
              error={"message": "The user rejected this tool call.", "code": "rejected"}),
        event("tool.execution_start", 15, toolCallId="c2", toolName="powershell",
              arguments={"command": "dir", "description": "List files"}),
        event("tool.execution_complete", 16, toolCallId="c2", success=True,
              result={"content": "a.txt"}),
        event("session.error", 17, message="Rate limited"),
        event("abort", 18, reason="user_initiated"),
        event("some.future.event", 19, anything=True),
        event("assistant.message", 20, model="gpt-test", content="Done"),
    ]
    lines = [json.dumps(e) for e in events] + ["{not json", "[1, 2]"]
    transcript = parse_events(lines)
    assert [(m.kind, m.text) for m in transcript.messages] == [
        (USER, "Hi"),
        (TOOL, "Applying patch (apply_patch): Add File: a.txt"),
        (DENIED, "Copilot wasn't allowed to: Create file C:/work/a.txt"),
        (TOOL_RESULT, "Failed: The user rejected this tool call."),
        (TOOL, "powershell: dir\nList files"),
        (TOOL_RESULT, "a.txt"),
        (ERROR, "Rate limited"),
        (INTERRUPTED, "Stopped before the turn finished."),
        (ASSISTANT, "Done"),
    ]
    assert transcript.unreadable_lines == 2
    assert transcript.turns == 1 and transcript.model == "gpt-test"
    assert transcript.last_reply().label == "Copilot"
    assert transcript.visible()[0].text == "Hi"  # tools are activity, hidden by default
    assert all(not m.is_activity for m in transcript.visible())


def test_reader_is_incremental_and_waits_for_whole_lines(tmp_path):
    write_session(tmp_path, ONE, answered_turn()[:3])
    provider = CopilotProvider(tmp_path)
    reader = provider.open_reader(info_for())
    assert reader.refresh()
    assert [m.text for m in reader.transcript.messages] == ["Hi"]
    assert not reader.refresh()
    path = tmp_path / ONE / "events.jsonl"
    reply = json.dumps(event("assistant.message", 9, content="Ready"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(reply[:20])  # Copilot is still writing this line
    assert not reader.refresh()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(reply[20:] + "\n")
    assert reader.refresh()
    assert [m.text for m in reader.transcript.messages] == ["Hi", "Ready"]
    assert provider.last_reply(info_for()) == "Ready"


def test_states_from_the_end_of_the_log():
    now = 10_000_000
    start = [json.dumps(event("assistant.turn_start", 1, turnId="0"))]
    asked = start + [json.dumps(event("permission.requested", 2, requestId="r"))]
    answered = asked + [json.dumps(event("permission.completed", 3, requestId="r",
                                         result={"kind": "approved"}))]
    ended = answered + [json.dumps(event("assistant.turn_end", 4, turnId="0"))]
    assert state_from_events(start, now, now) == (WORKING, "")
    assert state_from_events(asked, now, now) == (NEEDS_YOU, "waiting in Copilot")
    assert state_from_events(answered, now, now) == (WORKING, "")
    assert state_from_events(ended, now, now) == (IDLE, "")
    assert state_from_events([], now, now)[0] == "status unavailable"
    # Quiet for ten minutes (a long command, or a Copilot closed mid-reply):
    # still working, so a real finish is announced, but it says so.
    old = now - STALE_TURN_SECONDS * 1000 - 1
    assert state_from_events(start, old, now) == (
        WORKING, "no activity from Copilot for 10 minutes")
    assert state_from_events(asked, old, now)[0] == NEEDS_YOU


def test_a_reply_of_several_steps_ends_only_when_copilot_answers():
    """As Copilot 1.0.103 wrote a real reply: a step per tool, each with its
    own turn_start/turn_end, then a step that answers (#352)."""
    now = 10_000_000
    steps = [event("user.message", 1, content="List and write"),
             event("assistant.turn_start", 2, turnId="0"),
             event("assistant.message", 3, content="", toolRequests=[{"name": "powershell"}]),
             event("tool.execution_start", 4, toolCallId="a", toolName="powershell"),
             event("tool.execution_complete", 5, toolCallId="a", result={"content": "x"}),
             event("assistant.turn_end", 6, turnId="0"),
             event("assistant.turn_start", 7, turnId="1"),
             event("assistant.message", 8, content="Done.", toolRequests=[]),
             event("assistant.turn_end", 9, turnId="1")]
    lines = [json.dumps(e) for e in steps]
    for cut in range(1, len(lines)):
        assert state_from_events(lines[:cut], now, now)[0] == WORKING, steps[cut - 1]["type"]
    assert state_from_events(lines, now, now)[0] == IDLE
    # A tool step that ends with the reply stopped is idle too.
    stopped = lines[:6] + [json.dumps(event("abort", 10, reason="user_initiated"))]
    assert state_from_events(stopped, now, now)[0] == IDLE


def test_terminal_kind_and_a_title_past_the_head(tmp_path, monkeypatch):
    from thechatplace.providers import copilot
    from thechatplace.sessions import TERMINAL
    write_session(tmp_path, ONE, answered_turn())
    raw = f"id: {TWO}\ncwd: C:\\x\nclient_name: github/acp\n"
    big = [event("session.start", 0, filler="x" * 2000)] + answered_turn(10)[2:]
    write_session(tmp_path, TWO, big, raw_yaml=raw)
    monkeypatch.setattr(copilot, "HEAD_BYTES", 1000)
    sessions = {s.cli_session_id: s for s in CopilotProvider(tmp_path).collect().sessions}
    assert sessions[ONE].source == TERMINAL  # Copilot CLI in a terminal
    assert sessions[TWO].source == "external"
    # The first prompt is past the part read for a title: kept, untitled.
    assert sessions[TWO].title == "Untitled session"


def test_unchanged_sessions_are_not_read_again(tmp_path, monkeypatch):
    from thechatplace.providers import copilot
    write_session(tmp_path, ONE, answered_turn())
    provider = CopilotProvider(tmp_path)
    first = provider.collect().sessions
    reads = []
    monkeypatch.setattr(copilot, "_read_entry", lambda *a: reads.append(a) or None)
    second = provider.collect().sessions
    assert reads == [] and [s.key for s in second] == [s.key for s in first]
    provider.fill_last_messages(second)
    assert second[0].last_message == "Copilot: Ready"


def test_finished_turn_needs_an_explicit_idle():
    working = info_for()
    working.state = WORKING
    previous = {working.key: WORKING}
    unknown = info_for()
    unknown.state = "status unavailable"
    assert finished_turns(previous, [unknown]) == []
    idle = info_for()
    idle.state = IDLE
    assert [s.key for s in finished_turns(previous, [idle])] == [working.key]
    assert Snapshot(sessions=[unknown]).fresh_states() == {}


def test_reading_only_and_routed_through_the_controller(tmp_path, monkeypatch):
    write_session(tmp_path, ONE, answered_turn())
    monkeypatch.setattr(platform_paths, "copilot_sessions_dir", lambda: tmp_path)
    controller = SessionController([CopilotProvider()])
    snapshot = controller.collect(lambda: Snapshot(), "status")
    assert [s.key for s in snapshot.sessions] == [f"copilot:{ONE}"]
    session = snapshot.sessions[0]
    capabilities = controller.capabilities(session)
    assert not any(value for name, value in vars(capabilities).items() if name != "read")
    assert [m.text for m in controller.read(session).messages] == ["Hi", "Ready"]
    assert in_view(session, VIEW_COPILOT) and not in_view(session, "provider:claude")
    controller.close()


def test_last_message_column(tmp_path):
    write_session(tmp_path, ONE, answered_turn())
    provider = CopilotProvider(tmp_path)
    sessions = provider.collect().sessions
    provider.fill_last_messages(sessions)
    assert sessions[0].last_message == "Copilot: Ready"
    again = provider.collect().sessions
    provider.cached_last_messages(again)
    assert again[0].last_message == "Copilot: Ready"
    provider.forget_last_messages()
    provider.cached_last_messages(again)
    assert again[0].last_message == ""


@pytest.mark.parametrize("bad", ["../escape", "", "a/b"])
def test_refuses_ids_that_are_not_copilot_sessions(tmp_path, bad):
    provider = CopilotProvider(tmp_path)
    with pytest.raises(ValueError):
        provider.open_reader(info_for(bad))
    with pytest.raises(ValueError):
        provider.open_reader(SessionInfo("external", "codex:x", "t", "", ONE, provider="codex"))


def test_copilot_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    assert platform_paths.copilot_sessions_dir() == tmp_path / "session-state"


def test_a_tool_step_cut_off_by_the_tail_is_not_the_answer():
    """The tail can begin after the message that asked for a tool (a large
    tool output): the step's end still isn't the reply's end."""
    now = 10_000_000
    tail = [event("tool.execution_complete", 1, toolCallId="a", result={"content": "big"}),
            event("assistant.turn_end", 2, turnId="0")]
    assert state_from_events([json.dumps(e) for e in tail], now, now)[0] == WORKING


def test_quiet_waits_and_left_behind_replies():
    from thechatplace.providers.copilot import ABANDONED_TURN_SECONDS
    now = 100_000_000
    start = [json.dumps(event("assistant.turn_start", 1, turnId="0"))]
    asked = start + [json.dumps(event("permission.requested", 2, requestId="r"))]
    quiet = now - STALE_TURN_SECONDS * 1000 - 1
    # Waiting on you is why it's quiet: still says to answer in Copilot.
    assert state_from_events(asked, quiet, now) == (NEEDS_YOU, "waiting in Copilot")
    gone = now - ABANDONED_TURN_SECONDS * 1000 - 1
    assert state_from_events(start, gone, now) == (
        "status unavailable", "no activity from Copilot for over 2 hours")
    # Waiting on you stays Needs You far longer than a reply stays working...
    assert state_from_events(asked, gone, now) == (NEEDS_YOU, "waiting in Copilot")
    # ...but not for ever: a Copilot closed while asking never says so (#355 review).
    from thechatplace.providers.copilot import ABANDONED_QUESTION_SECONDS
    left = now - ABANDONED_QUESTION_SECONDS * 1000 - 1
    assert state_from_events(asked, left, now) == (
        "status unavailable", "waiting in Copilot for over a day")


def test_title_from_the_first_prompt_is_read_once(tmp_path, monkeypatch):
    from thechatplace.providers import copilot
    raw = f"id: {ONE}\ncwd: C:/x\nclient_name: github/cli\n"
    write_session(tmp_path, ONE, answered_turn(question="Plan the work"), raw_yaml=raw)
    provider = CopilotProvider(tmp_path)
    assert provider.collect().sessions[0].title == "Plan the work"
    heads = []
    real = copilot._read_head
    monkeypatch.setattr(copilot, "_read_head", lambda *a: heads.append(a) or real(*a))
    with open(tmp_path / ONE / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event("assistant.turn_start", 50, turnId="1")) + "\n")
    session = provider.collect().sessions[0]
    assert session.title == "Plan the work" and session.state == WORKING
    assert heads == []


def test_last_message_only_for_the_rows_own_activity(tmp_path):
    write_session(tmp_path, ONE, answered_turn())
    provider = CopilotProvider(tmp_path)
    old = provider.collect().sessions
    with open(tmp_path / ONE / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event("user.message", 60, content="Newer")) + "\n")
    import os, time
    stamp = time.time() + 5
    os.utime(tmp_path / ONE / "events.jsonl", (stamp, stamp))
    provider.collect()
    provider.fill_last_messages(old)  # a row from before the newer read
    assert old[0].last_message == ""


def test_an_error_ends_a_reply_only_once_nothing_follows():
    """No real Copilot log has shown a session.error yet (#355 review): it
    counts as the end only after ERROR_SETTLE_SECONDS of quiet, so a Copilot
    that recovers isn't announced as finished twice."""
    from thechatplace.providers.copilot import ERROR_SETTLE_SECONDS
    now = 10_000_000
    errored = [json.dumps(event("assistant.turn_start", 1, turnId="0")),
               json.dumps(event("session.error", 2, message="Rate limited"))]
    assert state_from_events(errored, now, now)[0] == WORKING
    settled = now - ERROR_SETTLE_SECONDS * 1000 - 1
    assert state_from_events(errored, settled, now)[0] == IDLE
    recovered = errored + [json.dumps(event("assistant.turn_start", 3, turnId="1"))]
    assert state_from_events(recovered, settled, now)[0] == WORKING
    # An error with no reply running doesn't start one.
    assert state_from_events([json.dumps(event("session.error", 4))], now, now)[0] \
        == "status unavailable"


def test_a_session_without_workspace_yaml_yet_is_skipped_not_unreadable(tmp_path):
    folder = tmp_path / ONE
    folder.mkdir()
    (folder / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in answered_turn()), encoding="utf-8")
    snapshot = CopilotProvider(tmp_path).collect()
    assert snapshot.sessions == [] and snapshot.unreadable_files == 0


def test_an_error_while_permission_is_asked_still_needs_you():
    from thechatplace.providers.copilot import ERROR_SETTLE_SECONDS
    now = 10_000_000
    lines = [json.dumps(event("assistant.turn_start", 1, turnId="0")),
             json.dumps(event("permission.requested", 2, requestId="r")),
             json.dumps(event("session.error", 3, message="transient"))]
    settled = now - ERROR_SETTLE_SECONDS * 1000 - 1
    assert state_from_events(lines, settled, now)[0] == NEEDS_YOU
    # Errors after tools, and twice over, settle the same way.
    tools = [json.dumps(event("assistant.turn_start", 1, turnId="0")),
             json.dumps(event("tool.execution_start", 2, toolCallId="a")),
             json.dumps(event("session.error", 3)), json.dumps(event("session.error", 4))]
    assert state_from_events(tools, now, now)[0] == WORKING
    assert state_from_events(tools, settled, now)[0] == IDLE


def test_a_permission_question_after_an_error_needs_you():
    """Asking after an error means Copilot carried on: never announced as
    finished while a question is open (#355 review)."""
    from thechatplace.providers.copilot import ERROR_SETTLE_SECONDS
    now = 10_000_000
    lines = [json.dumps(event("assistant.turn_start", 1, turnId="0")),
             json.dumps(event("session.error", 2, message="transient")),
             json.dumps(event("permission.requested", 3, requestId="r"))]
    settled = now - ERROR_SETTLE_SECONDS * 1000 - 1
    assert state_from_events(lines, settled, now)[0] == NEEDS_YOU
    answered = lines + [json.dumps(event("permission.completed", 4, requestId="r"))]
    assert state_from_events(answered, now, now)[0] == WORKING
    # An answer after an error is Copilot carrying on too, until its step ends.
    carried = lines[:2] + [json.dumps(event("assistant.message", 3, content="Retrying",
                                            toolRequests=[{"name": "shell"}]))]
    assert state_from_events(carried, settled, now)[0] == WORKING


def test_a_folder_without_workspace_yaml_for_long_is_counted(tmp_path):
    import os
    folder = tmp_path / ONE
    folder.mkdir()
    log = folder / "events.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in answered_turn()), encoding="utf-8")
    old = log.stat().st_mtime - 3600
    os.utime(log, (old, old))
    snapshot = CopilotProvider(tmp_path).collect()
    assert snapshot.sessions == [] and snapshot.unreadable_files == 1


def test_settings_needs_a_real_session_log_not_just_a_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "copilot_sessions_dir", lambda: tmp_path)
    (tmp_path / "empty-folder").mkdir()
    assert not platform_paths.copilot_has_sessions()
    write_session(tmp_path, ONE, answered_turn())
    assert platform_paths.copilot_has_sessions()
