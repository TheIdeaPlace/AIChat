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
    # A Copilot closed mid-turn never writes the end: don't say working forever.
    old = now - STALE_TURN_SECONDS * 1000 - 1
    assert state_from_events(start, old, now)[0] == "status unavailable"


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
