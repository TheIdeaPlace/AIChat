"""Copilot CLI chats over ACP, against tests/fake_copilot_acp.py (#352)."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from thechatplace import platform_paths
from thechatplace.providers import copilot_acp
from thechatplace.providers.copilot_runtime import (ALLOW_ONCE, COMMAND, MAX_DIFF_CHARS,
                                                    MAX_TEXT_CHARS, REJECT, CopilotChatError,
                                                    CopilotChatRuntime, CopilotRequest, Stopped,
                                                    usage_kinds)
from thechatplace.sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, WORKING

FAKE = Path(__file__).with_name("fake_copilot_acp.py")
PYTHON = getattr(sys, "_base_executable", None) or sys.executable


@pytest.fixture(autouse=True)
def own_copilot_home(tmp_path, monkeypatch):
    """Hooks are looked for in a Copilot folder of the test's own, never the
    machine's, and no organisation policy applies."""
    monkeypatch.setattr(platform_paths, "copilot_home", lambda: tmp_path / "copilot-home")
    monkeypatch.setattr(platform_paths, "copilot_policy_texts", lambda: [])


class Process:
    """What platform_paths.start_copilot_acp returns: pipes and close()."""

    def __init__(self, mode, env):
        # The interpreter itself, not a venv's python.exe: on Windows that's
        # a launcher whose child would have another pid than the one this
        # app knows, and the fake names its lock for itself, as Copilot does.
        self.process = subprocess.Popen([PYTHON, "-u", str(FAKE), mode],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env)
        self.stdin, self.stdout = self.process.stdin, self.process.stdout

    def close(self):
        self.process.kill()
        self.process.wait(5)
        for stream in (self.stdin, self.stdout):
            try:
                stream.close()
            except OSError:
                pass


@pytest.fixture
def make(tmp_path):
    made = []

    def build(mode="answer"):
        env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"),
                   FAKE_COPILOT_LOG=str(tmp_path / "received.jsonl"))
        updates = []
        runtime = CopilotChatRuntime(updates.append, start=lambda: Process(mode, env),
                                     sessions_dir=tmp_path / "sessions", billing_wait=1.0)
        made.append(runtime)
        return runtime, updates

    yield build
    for runtime in made:
        runtime.close()


def wait(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def received(tmp_path):
    path = tmp_path / "received.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] \
        if path.exists() else []


def test_a_turn_streams_its_reply_and_ends_idle(make, tmp_path):
    runtime, updates = make()
    chat = runtime.create(str(tmp_path))
    assert runtime.ready(chat)
    assert runtime.send(chat, "Say hello") is False
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    state = runtime.snapshot(chat)
    assert state.state == IDLE
    assert [text for _id, text in state.replies] == ["Hello there"]
    assert (state.context_used, state.context_size) == (100, 1000)
    assert WORKING in [u.state for u in updates]
    sent = [m for m in received(tmp_path) if m.get("method") == "session/prompt"]
    assert sent[0]["params"]["prompt"] == [{"type": "text", "text": "Say hello"}]


def test_the_client_offers_copilot_no_files_or_terminal(make, tmp_path):
    runtime, _ = make()
    runtime.create(str(tmp_path))
    init = next(m for m in received(tmp_path) if m.get("method") == "initialize")
    assert init["params"]["clientCapabilities"] == {
        "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
    new = next(m for m in received(tmp_path) if m.get("method") == "session/new")
    assert new["params"]["mcpServers"] == []


def test_a_message_sent_while_working_waits_its_turn(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    assert runtime.send(chat, "Second") is True
    assert wait(lambda: runtime.snapshot(chat).turn_id == "2"
                and runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    # Each turn's state has its own replies, as Codex's does.
    assert [text for _id, text in runtime.snapshot(chat).replies] == ["Hello there"]
    prompts = [m["params"]["prompt"][0]["text"] for m in received(tmp_path)
               if m.get("method") == "session/prompt"]
    assert prompts == ["First", "Second"]


def test_permission_is_asked_and_only_one_time_answers_exist(make, tmp_path):
    runtime, _ = make("ask")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Run it")
    assert wait(lambda: runtime.snapshot(chat).requests)
    state = runtime.snapshot(chat)
    assert state.state == NEEDS_YOU
    request = state.requests[0]
    assert request.method == COMMAND and request.command == "echo synthetic"
    # Copilot offered "always" too; it is never offered here (#284's rule).
    assert request.offered() == (ALLOW_ONCE, REJECT)
    with pytest.raises(ValueError):
        request.approval_answer("allow_always")
    runtime.answer(chat, request.request_id, REJECT)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert runtime.snapshot(chat).replies[-1][1] == "Denied"
    with pytest.raises(CopilotChatError):
        runtime.answer(chat, request.request_id, ALLOW_ONCE)  # answered once only


def test_allowing_once(make, tmp_path):
    runtime, _ = make("ask")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Run it")
    assert wait(lambda: runtime.snapshot(chat).requests)
    runtime.answer(chat, runtime.snapshot(chat).requests[0].request_id, ALLOW_ONCE)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert runtime.snapshot(chat).replies[-1][1] == "Allowed"
    answer = next(m for m in received(tmp_path) if "result" in m and "method" not in m)
    assert answer["result"] == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}


def test_stop_when_copilot_has_gone_cancels_nothing(make, tmp_path):
    """Kelly's review of #386: Copilot's process has gone (its connection is
    marked closed) but the turn hasn't ended yet. Stop gives the waiting
    message back, sends no cancel, and doesn't claim an answer."""
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    runtime.send(chat, "Then this")
    assert wait(lambda: runtime.snapshot(chat).replies)
    runtime._connection._closed = True  # as _fail_all marks it, before the turn ends
    assert runtime.stop(chat) == Stopped(("Then this",), cancelled=False, answered=False)
    assert runtime._chats[chat].cancel_timer is None
    assert not [m for m in received(tmp_path) if m.get("method") == "session/cancel"]


def test_stop_after_copilot_closes_mid_turn_cancels_nothing(make, tmp_path):
    """Kelly's review of #386: when Copilot's process really ends mid-turn,
    the prompt returns at once, so Stop may find the turn being checked or
    already over. Either way it cancels nothing, and the window's wording
    for both is neutral."""
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    runtime._connection.close()
    stopped = runtime.stop(chat)
    assert not stopped.cancelled and stopped.waiting == ()
    assert not [m for m in received(tmp_path) if m.get("method") == "session/cancel"]


def test_stop_doesnt_warn_of_a_close_when_copilot_answers_before_the_cancel(make, tmp_path):
    """Kelly's review of #386: Copilot answers after Stop read the turn, but
    before its cancel was queued. No cancel is sent and no timer kept, so
    Stop mustn't report one: the window would warn that Copilot will be
    closed in 15 seconds when nothing will close it. (Staged while Stop
    answers a waiting permission request, which it does in between.)"""
    runtime, _ = make("ask")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Run it")
    assert wait(lambda: runtime.snapshot(chat).requests)
    connection = runtime._connection
    real_respond = connection.respond

    def answered_meanwhile(request_id, result):
        with runtime._lock:
            runtime._chats[chat].answered = True
        return real_respond(request_id, result)
    connection.respond = answered_meanwhile
    assert runtime.stop(chat) == Stopped((), cancelled=False, answered=True)
    assert runtime._chats[chat].cancel_timer is None
    assert not [m for m in received(tmp_path) if m.get("method") == "session/cancel"]


def test_stop_cancels_and_gives_back_waiting_messages_unsent(make, tmp_path):
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    runtime.send(chat, "Then this")
    assert wait(lambda: runtime.snapshot(chat).replies)
    assert runtime.stop(chat) == Stopped(("Then this",), cancelled=True)
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
    assert runtime.snapshot(chat).state == IDLE
    prompts = [m for m in received(tmp_path) if m.get("method") == "session/prompt"]
    assert len(prompts) == 1  # never resent, and the queued one never sent


@pytest.mark.parametrize("mode", ["unsafe", "allowall"])
def test_nothing_is_sent_unless_copilot_will_ask_first(make, tmp_path, mode):
    runtime, _ = make(mode)
    chat = runtime.create(str(tmp_path))
    assert not runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="ask before it acts"):
        runtime.send(chat, "Hello")
    assert not [m for m in received(tmp_path) if m.get("method") == "session/prompt"]


@pytest.mark.parametrize("text", ["/allow-all on", "  /yolo", "/model gpt"])
def test_slash_commands_are_refused(make, tmp_path, text):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    with pytest.raises(CopilotChatError, match="starting with /"):
        runtime.send(chat, text)
    assert not [m for m in received(tmp_path) if m.get("method") == "session/prompt"]


def test_an_answer_billed_elsewhere_stops_every_chat(make, tmp_path):
    runtime, _ = make("byok")
    chat = runtime.create(str(tmp_path))
    other = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).problem)
    assert runtime.snapshot(chat).state == STATUS_UNAVAILABLE
    assert runtime.snapshot(other).problem
    with pytest.raises(CopilotChatError, match="couldn't confirm"):
        runtime.send(other, "Anything")


def test_an_answer_with_no_billing_record_stops_only_its_chat(make, tmp_path):
    runtime, _ = make("nolog")
    chat = runtime.create(str(tmp_path))
    other = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).problem)
    assert "won't send until it's opened again" in runtime.snapshot(chat).problem
    assert not runtime.snapshot(other).problem and runtime.ready(other)


def write_workspace(tmp_path, native_id, client):
    folder = tmp_path / "sessions" / native_id
    folder.mkdir(parents=True)
    (folder / "workspace.yaml").write_text(f"id: {native_id}\nclient_name: {client}\n",
                                           encoding="utf-8")


def test_reopening_a_chat_ignores_the_replayed_history(make, tmp_path):
    runtime, _ = make()
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    # Copilot recorded this app as its client, but it must also be one the
    # window says The Chat Place owns.
    with pytest.raises(CopilotChatError, match="started outside The Chat Place"):
        runtime.prepare(native, str(tmp_path))
    runtime.adopt(native)
    runtime.prepare(native, str(tmp_path))
    state = runtime.snapshot(native)
    assert state.replies == () and runtime.ready(state.native_id)


@pytest.mark.parametrize("client", ["github/cli", "github/acp", "zed", ""])
def test_a_chat_started_elsewhere_stays_read_only(make, tmp_path, client):
    runtime, _ = make()
    native = "66666666-7777-8888-9999-000000000000"
    if client:
        write_workspace(tmp_path, native, client)
    runtime.adopt(native)  # even on the app's own list, Copilot must agree
    with pytest.raises(CopilotChatError, match="started outside The Chat Place"):
        runtime.prepare(native, str(tmp_path))
    assert not [m for m in received(tmp_path) if m.get("method") == "session/load"]


def test_another_agent_is_refused(make, tmp_path):
    runtime, _ = make("oldagent")
    # Callers only ever see CopilotChatError, with a message safe to show.
    with pytest.raises(CopilotChatError, match="isn't supported"):
        runtime.create(str(tmp_path))


def test_request_decoding_refuses_requests_without_a_deny():
    with pytest.raises(CopilotChatError):
        CopilotRequest.decode(1, {"toolCall": {"kind": "execute"},
                                  "options": [{"optionId": "a", "kind": "allow_once"}]})


def test_usage_kinds_reads_only_what_was_written_after_the_offset(tmp_path):
    path = tmp_path / "events.jsonl"
    first = json.dumps({"type": "session.usage_record", "data": {
        "usage": {"provider": {"kind": "copilot"}}}}) + "\n"
    odd = ['"session.usage_record"', json.dumps({"type": "session.usage_record", "data": "x"}),
           json.dumps({"type": "session.usage_record", "data": {"usage": [1]}})]
    later = json.dumps({"type": "session.usage_record", "data": {
        "usage": {"provider": {"kind": "openai"}}}}) + "\n"
    path.write_text(first + "\n".join(odd) + "\n" + later, encoding="utf-8")
    assert usage_kinds(path) == ("copilot", "openai")  # odd lines skipped, not raised
    assert usage_kinds(path, len(first.encode())) == ("openai",)
    assert usage_kinds(tmp_path / "missing.jsonl") == ()


def test_copilot_environment_drops_what_changes_account_billing_or_asking():
    env = platform_paths.copilot_child_environment({
        "PATH": "p", "GH_HOST": "h", "COPILOT_HOME": "c", "copilot_allow_all": "1",
        "GH_TOKEN": "t", "GITHUB_TOKEN": "t", "COPILOT_GITHUB_TOKEN": "t",
        "COPILOT_PROVIDER_BASE_URL": "u", "COPILOT_PROVIDER_API_KEY": "k",
        "COPILOT_OFFLINE": "true", "COPILOT_MODEL": "m"})
    assert sorted(env) == ["COPILOT_HOME", "GH_HOST", "PATH"]


def test_find_copilot_never_takes_a_script(tmp_path):
    script = tmp_path / "copilot.cmd"
    script.write_text("@echo off\n", encoding="utf-8")
    shim = tmp_path / "copilot"
    shim.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    assert platform_paths.find_copilot(lambda name: str(script), [shim]) is None


@pytest.mark.parametrize("mode", ["flip", "curmode"])
def test_a_queued_message_isnt_sent_after_copilot_stops_asking(make, tmp_path, mode):
    runtime, _ = make(mode)
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    runtime.send(chat, "Second")
    assert wait(lambda: runtime.snapshot(chat).outcome not in ("", "inProgress"))
    state = runtime.snapshot(chat)
    # The running turn is stopped at once, and the waiting message comes back.
    assert state.unsent == ("Second",) and "stopped asking" in state.notice
    assert any(m.get("method") == "session/cancel" for m in received(tmp_path))
    assert not runtime.ready(chat)
    prompts = [m for m in received(tmp_path) if m.get("method") == "session/prompt"]
    assert len(prompts) == 1


def test_stop_answers_a_waiting_permission_request_first(make, tmp_path):
    runtime, _ = make("ask")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Run it")
    assert wait(lambda: runtime.snapshot(chat).requests)
    request = runtime.snapshot(chat).requests[0]
    runtime.stop(chat)
    assert wait(lambda: not runtime.snapshot(chat).requests
                and runtime.snapshot(chat).outcome not in ("", "inProgress"))
    answers = [m for m in received(tmp_path) if m.get("id") == request.request_id and "result" in m]
    assert answers[0]["result"] == {"outcome": {"outcome": "cancelled"}}
    assert any(m.get("method") == "session/cancel" for m in received(tmp_path))


def test_billing_is_checked_on_each_turns_own_record(make, tmp_path):
    runtime, _ = make("byok2")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert not runtime.snapshot(chat).problem  # the first turn really was Copilot
    runtime.send(chat, "Two")
    assert wait(lambda: runtime.snapshot(chat).problem)


def test_an_unoffered_choice_leaves_the_request_waiting(make, tmp_path):
    runtime, _ = make("ask")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Run it")
    assert wait(lambda: runtime.snapshot(chat).requests)
    request = runtime.snapshot(chat).requests[0]
    with pytest.raises(ValueError):
        runtime.answer(chat, request.request_id, "allow_always")
    assert runtime.snapshot(chat).requests == (request,)
    runtime.answer(chat, request.request_id, REJECT)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")


def test_after_copilot_exits_a_chat_must_be_opened_again(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime._connection._process.close()  # Copilot ends unexpectedly
    assert wait(lambda: runtime._connection.closed)
    assert not runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="open this chat again"):
        runtime.send(chat, "Hello")
    runtime.prepare(chat, str(tmp_path))  # a new Copilot, which loads and checks it
    assert runtime.ready(chat)
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")


def test_probe_tool_runs_a_chat_and_asks_about_permissions(tmp_path, monkeypatch, capsys):
    """tools/probe_copilot.py drives the same runtime, in plain text."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    import probe_copilot
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"))
    real = probe_copilot.CopilotChatRuntime
    monkeypatch.setattr(probe_copilot, "CopilotChatRuntime", lambda on_update: real(
        on_update, start=lambda: Process("ask", env), sessions_dir=tmp_path / "sessions",
        billing_wait=1.0))
    answers = iter(["Run it", "n", "/allow-all on", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert probe_copilot.main(["--folder", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Safe to send (agent mode, allow-all off): yes" in out
    assert "Copilot asks permission." in out and "echo synthetic" in out
    assert "Denied." in out and "Copilot: Denied" in out and "[turn completed]" in out
    assert "Not sent: Copilot commands starting with /" in out


def test_a_request_racing_a_stop_is_answered_not_left_waiting(make, tmp_path):
    """A permission request that arrives after Stop is cancelled at once."""
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    runtime.stop(chat)
    connection = runtime._connection
    sent = []
    real = connection.respond
    connection.respond = lambda rid, result: (sent.append((rid, result)), real(rid, result))
    runtime._on_request(connection, "late", "session/request_permission", {
        "sessionId": chat, "toolCall": {"kind": "execute", "rawInput": {"command": "x"}},
        "options": [{"optionId": "r", "kind": "reject_once"}]})
    assert sent == [("late", {"outcome": {"outcome": "cancelled"}})]
    assert not runtime.snapshot(chat).requests


def test_messages_held_by_an_automatic_stop_always_come_back(make, tmp_path):
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    runtime.send(chat, "Waiting")
    assert wait(lambda: runtime.snapshot(chat).replies)
    assert runtime.stop(chat, hold=True) == Stopped((), cancelled=True)  # kept, not returned
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
    assert runtime.snapshot(chat).unsent == ("Waiting",)


def test_a_late_record_from_elsewhere_stops_everything_on_reopening(make, tmp_path):
    runtime, _ = make("nolog")
    chat = runtime.create(str(tmp_path))
    other = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).problem)
    assert runtime.ready(other)
    log = tmp_path / "sessions" / chat / "events.jsonl"
    with open(log, "a", encoding="utf-8") as handle:  # the late record arrives
        handle.write(json.dumps({"type": "session.usage_record", "data": {
            "usage": {"provider": {"kind": "openai"}}}}) + "\n")
    runtime.prepare(chat, str(tmp_path))  # opened again: closed in Copilot, then loaded
    assert not runtime.ready(other) and runtime.snapshot(other).problem


def test_probe_tool_says_each_reply_once_across_turns(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    import probe_copilot
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"))
    real = probe_copilot.CopilotChatRuntime
    monkeypatch.setattr(probe_copilot, "CopilotChatRuntime", lambda on_update: real(
        on_update, start=lambda: Process("answer", env), sessions_dir=tmp_path / "sessions",
        billing_wait=1.0))
    answers = iter(["One", "Two", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert probe_copilot.main(["--folder", str(tmp_path)]) == 0
    assert capsys.readouterr().out.count("Copilot: Hello there") == 2  # once per turn


# -- from Kelly's review of #364 ---------------------------------------------


def test_sessions_this_app_starts_are_recorded_as_its_own(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    init = next(m for m in received(tmp_path) if m.get("method") == "initialize")
    assert init["params"]["clientInfo"]["name"] == "thechatplace"
    workspace = (tmp_path / "sessions" / chat / "workspace.yaml").read_text(encoding="utf-8")
    assert "client_name: thechatplace" in workspace


@pytest.mark.parametrize("folder", ["C:\\", "/", r"\\server\share", "relative/path"])
def test_copilot_never_works_in_a_whole_drive(make, tmp_path, folder):
    runtime, _ = make()
    with pytest.raises(CopilotChatError, match="project folder"):
        runtime.create(folder)
    assert not [m for m in received(tmp_path) if m.get("method") == "session/new"]


@pytest.mark.parametrize("text", ["\ufeff/allow-all on", "\u200b/allow-all on",
                                  "\u2060 /yolo", "\u00a0/model x"])
def test_invisible_characters_cant_hide_a_slash_command(make, tmp_path, text):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    with pytest.raises(CopilotChatError, match="starting with /"):
        runtime.send(chat, text)


@pytest.mark.parametrize("mode", ["edit", "editcontent"])
def test_a_file_edit_request_shows_its_diff(make, tmp_path, mode):
    runtime, _ = make(mode)
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Make a.txt")
    assert wait(lambda: runtime.snapshot(chat).requests)
    request = runtime.snapshot(chat).requests[0]
    assert request.method == "files" and request.reviewable()
    assert "hello" in request.detail() and "a.txt" in request.detail()
    runtime.answer(chat, request.request_id, ALLOW_ONCE)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert runtime.snapshot(chat).replies[-1][1] == "Written"


def test_the_files_a_turn_changed_come_with_its_end(make, tmp_path):
    """An agent conversation passes them on with the reply (#369), read from
    the turn's own stretch of the log, so a later turn starts with none."""
    runtime, _ = make("edit")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Make a.txt")
    assert wait(lambda: runtime.snapshot(chat).requests)
    runtime.answer(chat, runtime.snapshot(chat).requests[0].request_id, ALLOW_ONCE)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert runtime.snapshot(chat).changed_paths == ("C:/synthetic/a.txt",)
    runtime.send(chat, "Make it again")
    assert wait(lambda: runtime.snapshot(chat).requests)
    assert runtime.snapshot(chat).changed_paths == ()
    runtime.answer(chat, runtime.snapshot(chat).requests[0].request_id, REJECT)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert runtime.snapshot(chat).changed_paths == ()


def test_only_a_successful_tools_file_edits_count():
    from thechatplace.providers.copilot_runtime import _edited_paths
    events = [("tool.execution_complete", {"success": True, "fileEdits": [
                  {"path": "a.py", "kind": "edit"}, {"path": "b.py"}, "odd", {"path": 3}]}),
              ("tool.execution_complete", {"success": False, "fileEdits": [{"path": "c.py"}]}),
              ("tool.execution_complete", {"success": True, "fileEdits": [{"path": "a.py"}]}),
              ("tool.execution_complete", {"success": True, "fileEdits": "x"}),
              ("hook.start", {"success": True, "fileEdits": [{"path": "d.py"}]})]
    assert _edited_paths(events) == ("a.py", "b.py")


def test_copilot_asking_for_files_or_a_terminal_is_refused(make, tmp_path):
    runtime, _ = make("clientreq")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    replies = {m["id"]: m for m in received(tmp_path) if m.get("id") in ("fs-1", "term-1")}
    assert replies["fs-1"]["error"]["code"] == -32601
    assert replies["term-1"]["error"]["code"] == -32601


def test_a_permission_request_for_another_session_is_cancelled(make, tmp_path):
    runtime, _ = make("strayperm")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    stray = next(m for m in received(tmp_path) if m.get("id") == "stray-1")
    assert stray["result"] == {"outcome": {"outcome": "cancelled"}}
    assert not runtime.snapshot(chat).requests


def test_copilot_dying_mid_turn_ends_the_turn_and_tells_idle_chats(make, tmp_path):
    runtime, updates = make("die")
    chat = runtime.create(str(tmp_path))
    idle = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "failed")
    assert "wasn't sent again" in runtime.snapshot(chat).notice
    assert wait(lambda: "Copilot stopped" in runtime.snapshot(idle).notice)
    assert not runtime.ready(idle)


def test_a_copilot_that_isnt_signed_in_says_so_for_a_new_chat(make, tmp_path):
    """Kelly's clean VM (#391): session/new fails with ACP's -32000. The
    window is told to sign in, in our words, never Copilot's own text."""
    runtime, _ = make("signedout")
    with pytest.raises(CopilotChatError) as raised:
        runtime.create(str(tmp_path))
    assert str(raised.value) == copilot_acp.NOT_SIGNED_IN
    assert "Help, Copilot Sign-in" in str(raised.value)
    assert "Authentication required" not in str(raised.value)


def test_a_copilot_that_isnt_signed_in_says_so_when_a_chat_is_opened(make, tmp_path):
    runtime, _ = make("signedout")
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    runtime.adopt(native)
    with pytest.raises(CopilotChatError, match="isn't signed in"):
        runtime.prepare(native, str(tmp_path))


def test_a_sign_in_that_stops_working_says_what_to_do(make, tmp_path):
    """A prompt refused for want of a sign-in says to sign in. Copilot had
    already logged it (it may even have acted), so it isn't given back, nor
    taken for another Copilot's message; the one waiting comes back unsent."""
    runtime, _ = make("expired")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    assert runtime.send(chat, "Second") is True  # queued behind it
    assert wait(lambda: runtime.snapshot(chat).outcome == "failed")
    state = runtime.snapshot(chat)
    assert state.notice == copilot_acp.NOT_SIGNED_IN_SENT
    assert state.unsent == ("Second",)
    assert not runtime._chats[chat].elsewhere


def test_other_errors_from_copilot_keep_their_general_wording(make, tmp_path):
    runtime, _ = make()
    runtime.create(str(tmp_path))
    with pytest.raises(copilot_acp.AcpError) as raised:
        runtime._connection.request("synthetic/unknown", {})  # the fake answers -32601
    assert not isinstance(raised.value, copilot_acp.AcpAuthRequired)
    assert str(raised.value) == "Copilot couldn't do that."


def test_a_stop_copilot_ignores_ends_the_turn_by_closing_it(tmp_path):
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"))
    runtime = CopilotChatRuntime(lambda state: None, start=lambda: Process("deaf", env),
                                 sessions_dir=tmp_path / "sessions", billing_wait=0.5,
                                 cancel_grace=0.5)
    try:
        chat = runtime.create(str(tmp_path))
        runtime.send(chat, "Long job")
        assert wait(lambda: runtime.snapshot(chat).replies)
        runtime.stop(chat)
        assert wait(lambda: runtime.snapshot(chat).outcome not in ("", "inProgress"))
        assert "didn't stop when asked" in runtime.snapshot(chat).notice
        assert not runtime.ready(chat)
    finally:
        runtime.close()


def test_a_stop_as_the_prompt_goes_out_reaches_copilot_after_it(tmp_path):
    """Kelly's review of #372: a Stop after the turn checked for one, but
    before its prompt was written, sent the cancel first. Copilot ignores a
    cancel with no prompt running, answered in full, and the timer then
    closed it. The cancel now always follows the prompt."""
    log = tmp_path / "received.jsonl"
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"),
               FAKE_COPILOT_LOG=str(log))
    runtime = CopilotChatRuntime(lambda state: None, start=lambda: Process("strict", env),
                                 sessions_dir=tmp_path / "sessions", billing_wait=0.5,
                                 cancel_grace=0.5)
    try:
        chat = runtime.create(str(tmp_path))
        connection = runtime._connection
        real_request = connection.request
        stops = []

        def stop_first(method, params, *args, **kwargs):
            if method == "session/prompt":
                stops.append(runtime.stop(chat))  # after the turn's check, before the write
            return real_request(method, params, *args, **kwargs)
        connection.request = stop_first
        runtime.send(chat, "Long job")
        assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
        assert stops == [Stopped((), cancelled=True)]  # its cancel follows the prompt
        order = [m["method"] for m in received(tmp_path)
                 if m.get("method") in ("session/prompt", "session/cancel")]
        assert order == ["session/prompt", "session/cancel"]
        # Copilot stopped, so the timer that would close it is called off
        # (checked directly, rather than by sleeping past the grace).
        assert wait(lambda: runtime._chats[chat].cancel_timer is None)
        assert runtime.ready(chat) and "didn't stop" not in runtime.snapshot(chat).notice
    finally:
        runtime.close()


def test_bad_lines_from_copilot_are_skipped(make, tmp_path):
    runtime, _ = make("garbage")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert runtime.snapshot(chat).replies[-1][1] == "Hello there"


def test_an_over_long_line_ends_the_connection(tmp_path):
    class Pipe:
        def __init__(self, data):
            import io
            self.stdout, self.stdin = io.BytesIO(data), io.BytesIO()

        def close(self):
            pass

    closed = []
    data = b'{"jsonrpc":"2.0","method":"session/update","params":{}}' + b" " * (
        copilot_acp.MAX_LINE_BYTES + 10) + b"\n"
    connection = copilot_acp.AcpConnection(Pipe(data), lambda m, p: None,
                                           lambda i, m, p: None, lambda: closed.append(1))
    assert wait(lambda: connection.closed and closed)


def test_a_request_with_no_answer_times_out_alone(make, tmp_path, monkeypatch):
    runtime, _ = make("mute")
    connection = runtime._connect()
    with pytest.raises(copilot_acp.AcpError, match="in time"):
        connection.request("session/new", {"cwd": str(tmp_path), "mcpServers": []}, timeout=0.5)
    assert not connection.closed  # other chats' turns would carry on


def test_a_message_too_long_is_refused_before_it_is_queued(make, tmp_path):
    runtime, _ = make()
    connection = runtime._connect()
    with pytest.raises(copilot_acp.AcpError, match="too long"):
        connection.notify("session/cancel", {"x": "y" * (copilot_acp.MAX_MESSAGE_BYTES + 1)})
    assert not connection.closed


def test_start_copilot_acp_uses_no_shell_the_job_and_a_clean_environment(monkeypatch):
    calls = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls["args"], calls["kwargs"] = args, kwargs
            self.stdin = self.stdout = None
            self.pid = 1

    class FakeTree:
        def popen_kwargs(self):
            return {"start_new_session": True}

        def attach(self, process):
            calls["attached"] = process

    monkeypatch.setattr(platform_paths, "_native_codex", lambda path: True)
    monkeypatch.setattr(platform_paths, "ProcessTree", FakeTree)
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    monkeypatch.setenv("COPILOT_ALLOW_ALL", "true")
    monkeypatch.setenv("GH_TOKEN", "t")
    platform_paths.start_copilot_acp("C:/x/copilot.exe", cwd=None)
    assert calls["args"] == ["C:/x/copilot.exe", "--acp", "--disable-client-telemetry"]
    assert "shell" not in calls["kwargs"] or calls["kwargs"]["shell"] is False
    env = calls["kwargs"]["env"]
    assert "COPILOT_ALLOW_ALL" not in env and "GH_TOKEN" not in env
    assert calls["attached"] is not None


def test_start_copilot_sdk_uses_read_only_server_and_clean_environment(monkeypatch):
    calls = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls["args"], calls["kwargs"] = args, kwargs
            self.stdin = self.stdout = None
            self.pid = 1

    class FakeTree:
        def popen_kwargs(self):
            return {"start_new_session": True}

        def attach(self, process):
            calls["attached"] = process

    monkeypatch.setattr(platform_paths, "_native_codex", lambda path: True)
    monkeypatch.setattr(platform_paths, "ProcessTree", FakeTree)
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    monkeypatch.setenv("COPILOT_ALLOW_ALL", "true")
    monkeypatch.setenv("GH_TOKEN", "t")
    platform_paths.start_copilot_sdk("C:/x/copilot.exe")
    assert calls["args"] == [
        "C:/x/copilot.exe", "--headless", "--no-auto-update", "--stdio",
        "--log-level", "error", "--disable-client-telemetry"]
    assert "shell" not in calls["kwargs"] or calls["kwargs"]["shell"] is False
    env = calls["kwargs"]["env"]
    assert "COPILOT_ALLOW_ALL" not in env and "GH_TOKEN" not in env
    assert calls["attached"] is not None


def test_a_message_too_long_is_refused_up_front(make, tmp_path):
    from thechatplace.providers.copilot_runtime import MAX_MESSAGE_CHARS
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    with pytest.raises(CopilotChatError, match="too long"):
        runtime.send(chat, "x" * (MAX_MESSAGE_CHARS + 1))
    assert not [m for m in received(tmp_path) if m.get("method") == "session/prompt"]


def test_a_content_diff_shows_only_what_changed():
    old = "".join(f"line {n}\n" for n in range(1, 500))
    new = old.replace("line 250\n", "line two hundred and fifty\n")
    request = CopilotRequest.decode(1, {"toolCall": {"kind": "edit", "rawInput": {},
        "content": [{"type": "diff", "path": "a.txt", "oldText": old, "newText": new}]},
        "options": [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]})
    assert "-line 250" in request.diff and "+line two hundred and fifty" in request.diff
    assert "line 100" not in request.diff  # not the whole file twice
    assert request.offered() == (ALLOW_ONCE, REJECT)


def test_a_change_too_long_to_show_can_only_be_denied():
    from thechatplace.providers.copilot_runtime import MAX_DIFF_CHARS
    request = CopilotRequest.decode(1, {"toolCall": {"kind": "edit", "rawInput": {
        "fileName": "big.txt", "diff": "+" + "x" * MAX_DIFF_CHARS}},
        "options": [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]})
    assert request.offered() == (REJECT,) and not request.reviewable()
    assert "too long to show in full" in request.detail()
    with pytest.raises(ValueError):
        request.approval_answer(ALLOW_ONCE)


@pytest.mark.parametrize("text", ["\u034f/allow-all", "\u3164/yolo", "\x00/yolo", "\u2800/x"])
def test_more_invisible_characters_cant_hide_a_slash(make, tmp_path, text):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    with pytest.raises(CopilotChatError, match="starting with /"):
        runtime.send(chat, text)


def test_a_stop_in_time_cancels_its_timer_and_closing_says_nothing(make, tmp_path):
    runtime, updates = make("hang")
    chat = runtime.create(str(tmp_path))
    idle = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    runtime.stop(chat)
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
    assert runtime._chats[chat].cancel_timer is None
    runtime.close()
    assert "Copilot stopped" not in (runtime.snapshot(idle).notice or "")


def test_a_permission_request_for_an_idle_chat_is_cancelled(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))  # open, but no turn running
    connection = runtime._connection
    sent = []
    real = connection.respond
    connection.respond = lambda rid, result: (sent.append((rid, result)), real(rid, result))
    runtime._on_request(connection, "idle-1", "session/request_permission", {
        "sessionId": chat, "toolCall": {"kind": "execute", "rawInput": {"command": "x"}},
        "options": [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]})
    assert sent == [("idle-1", {"outcome": {"outcome": "cancelled"}})]
    assert not runtime.snapshot(chat).requests


def test_a_billing_stop_gives_back_its_own_chats_waiting_messages(make, tmp_path):
    """The chat billed elsewhere doesn't carry on with its waiting message.
    (Another running chat's: see the test after Kelly's second review.)"""
    runtime, _ = make("byok")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    runtime.send(chat, "Waiting one")
    assert wait(lambda: runtime.snapshot(chat).problem
                and runtime.snapshot(chat).outcome not in ("", "inProgress"))
    assert runtime.snapshot(chat).unsent == ("Waiting one",)
    prompts = [m for m in received(tmp_path) if m.get("method") == "session/prompt"]
    assert len(prompts) == 1


# -- from Kelly's second review of #364 ----------------------------------------


def prompts_sent(tmp_path):
    return [m["params"]["prompt"][0]["text"] for m in received(tmp_path)
            if m.get("method") == "session/prompt"]


def test_messages_held_by_an_automatic_stop_survive_a_turn_that_ends_normally(make, tmp_path):
    """Copilot turns allow-all on (the waiting message is held), then off
    again, then finishes the turn as if never cancelled: the held message,
    and one sent after, come back unsent rather than being dropped or sent."""
    runtime, _ = make("flipback")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    assert runtime.send(chat, "Second") is True
    assert wait(lambda: "stopped asking" in runtime.snapshot(chat).notice)
    assert wait(lambda: runtime._chats[chat].safe)  # allow-all is off again
    assert runtime.send(chat, "Third") is True
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    state = runtime.snapshot(chat)
    assert state.unsent == ("Second", "Third") and not state.queued
    assert "stopped asking" in state.notice
    assert prompts_sent(tmp_path) == ["First"]


def command_request(raw):
    return CopilotRequest.decode(1, {"toolCall": {"kind": "execute", "title": "Run",
                                                  "rawInput": raw},
        "options": [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]})


@pytest.mark.parametrize("raw", [
    {"command": "cat <<END\n" + "x" * MAX_TEXT_CHARS + "\nEND; rm -rf /synthetic"},
    {"commands": ["echo one", "y" * (MAX_TEXT_CHARS + 1)]},
    {"commands": ["a" * 40_000, "b" * 40_000]},  # each fits, together they don't
    {"commands": ["echo shown", {"hidden": "not text"}]},
    {"command": ["not", "text"]},
    {"command": "echo shown", "commands": "z" * (MAX_TEXT_CHARS + 1)},
])
def test_a_command_too_long_to_show_can_only_be_denied(raw):
    request = command_request(raw)
    assert request.method == COMMAND and request.too_long
    assert request.offered() == (REJECT,) and not request.reviewable()
    with pytest.raises(ValueError):
        request.approval_answer(ALLOW_ONCE)
    assert "too long to show in full" in request.detail()


def test_a_command_and_its_commands_are_all_shown():
    request = command_request({"command": "echo one", "commands": ["echo two"]})
    assert request.command == "echo one\necho two" and not request.too_long
    assert request.offered() == (ALLOW_ONCE, REJECT) and request.reviewable()


def test_copilots_own_list_of_commands_is_read_not_repeated():
    """Copilot 1.0.103 lists each command it found as an object; one already
    in the command isn't shown twice, and the request can still be allowed."""
    request = command_request({"command": "echo a && echo b", "commands": [
        {"identifier": "echo a", "readOnly": False}, {"identifier": "echo b", "readOnly": False}]})
    assert request.command == "echo a && echo b" and not request.too_long
    assert request.offered() == (ALLOW_ONCE, REJECT)
    only_listed = command_request({"commands": [{"identifier": "echo c", "readOnly": True}]})
    assert only_listed.command == "echo c" and not only_listed.too_long


@pytest.mark.parametrize("error, shown", [
    (OSError(platform_paths.COPILOT_MISSING), platform_paths.COPILOT_MISSING),
    (OSError(r"[WinError 5] Access is denied: 'C:\Users\someone\copilot.exe'"),
     "couldn't be started"),
])
def test_copilot_failing_to_start_is_a_chat_error_in_our_words(tmp_path, error, shown):
    def start():
        raise error
    runtime = CopilotChatRuntime(lambda state: None, start=start, sessions_dir=tmp_path)
    try:
        with pytest.raises(CopilotChatError, match=shown) as caught:
            runtime.create(str(tmp_path))
        assert "someone" not in str(caught.value)
    finally:
        runtime.close()


def test_stop_while_billing_is_checked_cancels_nothing(tmp_path):
    """Copilot has answered; the turn is only waiting for its log. Stop then
    sends no cancel and never closes Copilot, which other chats share."""
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"),
               FAKE_COPILOT_LOG=str(tmp_path / "received.jsonl"))
    runtime = CopilotChatRuntime(lambda state: None, start=lambda: Process("nolog", env),
                                 sessions_dir=tmp_path / "sessions", billing_wait=1.5,
                                 cancel_grace=0.2)
    try:
        chat = runtime.create(str(tmp_path))
        other = runtime.create(str(tmp_path))
        runtime.send(chat, "Hello")
        assert wait(lambda: runtime._chats[chat].answered)
        runtime.stop(chat)
        assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
        time.sleep(0.4)  # past the grace a cancel would have had
        assert not runtime._connection.closed and runtime.ready(other)
        assert not [m for m in received(tmp_path) if m.get("method") == "session/cancel"]
    finally:
        runtime.close()


def test_a_message_that_never_reached_copilot_comes_back_unsent(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    connection = runtime._connection
    with connection._lock:  # as many requests as may wait are already waiting
        for n in range(copilot_acp.MAX_PENDING):
            connection._pending[f"busy-{n}"] = {"done": threading.Event(), "message": None}
    try:
        runtime.send(chat, "Hello")
        assert wait(lambda: runtime.snapshot(chat).outcome == "failed")
    finally:
        with connection._lock:
            for n in range(copilot_acp.MAX_PENDING):
                connection._pending.pop(f"busy-{n}")
    state = runtime.snapshot(chat)
    assert state.unsent == ("Hello",) and "wasn't sent" in state.notice
    assert prompts_sent(tmp_path) == []


@pytest.mark.parametrize("content", [{"type": "diff"}, "text", 3])
def test_a_request_with_odd_content_is_cancelled_not_refused(make, tmp_path, content):
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    connection = runtime._connection
    sent = []
    real = connection.respond
    connection.respond = lambda rid, result: (sent.append((rid, result)), real(rid, result))
    runtime._on_request(connection, "odd-1", "session/request_permission", {
        "sessionId": chat, "toolCall": {"kind": "edit", "rawInput": {}, "content": content},
        "options": [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]})
    assert sent == [("odd-1", {"outcome": {"outcome": "cancelled"}})]
    assert not runtime.snapshot(chat).requests
    runtime.stop(chat)


def test_odd_options_are_unanswerable():
    with pytest.raises(CopilotChatError):
        CopilotRequest.decode(1, {"toolCall": {"kind": "execute"}, "options": {"kind": "x"}})


def test_texts_too_long_to_show_are_never_diffed(monkeypatch):
    from thechatplace.providers import copilot_runtime
    monkeypatch.setattr(copilot_runtime.difflib, "unified_diff",
                        lambda *a, **k: pytest.fail("diffed before the size check"))
    half = "x\n" * (MAX_DIFF_CHARS // 4 + 1)
    request = CopilotRequest.decode(1, {"toolCall": {"kind": "edit", "rawInput": {}, "content": [
        {"type": "diff", "path": "a.txt", "oldText": half, "newText": half + "y"}]},
        "options": [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]})
    assert request.too_long and request.offered() == (REJECT,)


def test_a_billing_stop_gives_back_another_running_chats_waiting_messages(make, tmp_path):
    """One chat's answer is billed elsewhere while another is mid-turn with a
    message waiting: that one is stopped too, and its message comes back."""
    runtime, _ = make("byokhang")
    running = runtime.create(str(tmp_path))
    chat = runtime.create(str(tmp_path))
    runtime.send(running, "Long job")
    assert runtime.send(running, "Waiting one") is True
    assert wait(lambda: runtime.snapshot(running).replies)
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(running).outcome == "interrupted")
    state = runtime.snapshot(running)
    assert state.unsent == ("Waiting one",) and state.problem
    assert prompts_sent(tmp_path) == ["Long job", "Hello"]


def test_a_stop_copilot_ignores_tells_the_other_running_chats(tmp_path):
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"))
    runtime = CopilotChatRuntime(lambda state: None, start=lambda: Process("deaf", env),
                                 sessions_dir=tmp_path / "sessions", billing_wait=0.5,
                                 cancel_grace=0.5)
    try:
        chat = runtime.create(str(tmp_path))
        other = runtime.create(str(tmp_path))
        runtime.send(chat, "Long job")
        runtime.send(other, "Another job")
        assert wait(lambda: runtime.snapshot(chat).replies and runtime.snapshot(other).replies)
        runtime.stop(chat)
        assert wait(lambda: runtime.snapshot(other).outcome not in ("", "inProgress"))
        assert "another chat didn't stop" in runtime.snapshot(other).notice
        assert not runtime.ready(other)
    finally:
        runtime.close()


class _Stuck:
    """A process that reads nothing and answers nothing; close() is slow."""

    def __init__(self):
        self.release = threading.Event()
        self.stdin = self.stdout = self

    def write(self, data):
        self.release.wait(10)  # Copilot has stopped reading

    def flush(self):
        pass

    def readline(self, limit=-1):
        self.release.wait(10)
        return b""

    def close(self):
        time.sleep(1.0)  # as waiting for a process to end can take a while
        self.release.set()


def test_a_full_queue_closes_the_connection_without_making_the_caller_wait():
    closed = []
    connection = copilot_acp.AcpConnection(_Stuck(), lambda m, p: None, lambda i, m, p: None,
                                           lambda: closed.append(1))
    try:
        with pytest.raises(copilot_acp.AcpNotSent, match="stopped responding"):
            for _ in range(copilot_acp.MAX_OUTGOING + 2):
                start = time.monotonic()
                connection.notify("session/cancel", {"sessionId": "x"})
        assert time.monotonic() - start < 0.5  # closing went to another thread
        assert wait(lambda: connection.closed and closed)
    finally:
        connection.close()


def test_at_most_so_many_requests_wait_for_copilot():
    connection = copilot_acp.AcpConnection(_Stuck(), lambda m, p: None, lambda i, m, p: None)
    errors = []

    def ask():
        try:
            connection.request("session/new", {}, timeout=10)
        except copilot_acp.AcpError as exc:
            errors.append(exc)
    threads = [threading.Thread(target=ask, daemon=True) for _ in range(copilot_acp.MAX_PENDING)]
    for thread in threads:
        thread.start()
    try:
        assert wait(lambda: len(connection._pending) == copilot_acp.MAX_PENDING)
        with pytest.raises(copilot_acp.AcpNotSent, match="busy"):
            connection.request("session/new", {}, timeout=1)
    finally:
        connection.close()
    for thread in threads:
        thread.join(5)
    assert len(errors) == copilot_acp.MAX_PENDING  # each woken when it closed


def test_at_most_so_many_permission_requests_wait_in_one_chat(make, tmp_path):
    from thechatplace.providers.copilot_runtime import MAX_REQUESTS
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    connection = runtime._connection
    sent = []
    real = connection.respond
    connection.respond = lambda rid, result: (sent.append((rid, result)), real(rid, result))
    for n in range(MAX_REQUESTS + 1):
        runtime._on_request(connection, f"many-{n}", "session/request_permission", {
            "sessionId": chat, "toolCall": {"kind": "execute", "rawInput": {"command": "x"}},
            "options": [{"optionId": "r", "kind": "reject_once"}]})
    assert len(runtime.snapshot(chat).requests) == MAX_REQUESTS
    assert sent == [(f"many-{MAX_REQUESTS}", {"outcome": {"outcome": "cancelled"}})]
    assert "too many" in runtime.snapshot(chat).notice
    runtime.stop(chat)


def add_hook(folder):
    path = Path(folder) / ".github" / "hooks" / "allow.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [{"bash": "echo"}]}}),
                    encoding="utf-8")


def append_event(tmp_path, chat, kind, data=None):
    with open(tmp_path / "sessions" / chat / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": kind, "data": data or {}}) + "\n")


def test_no_chat_is_started_while_a_hook_could_approve_for_you(make, tmp_path):
    """Real Copilot 1.0.103 ran a command unasked over ACP when a project
    hook answered "allow", even with disableAllHooks set."""
    runtime, _ = make()
    add_hook(tmp_path)
    with pytest.raises(CopilotChatError, match="hook that could act") as caught:
        runtime.create(str(tmp_path))
    assert "allow.json" in str(caught.value)
    assert not [m for m in received(tmp_path) if m.get("method") == "session/new"]


def test_nothing_is_sent_once_a_hook_appears(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    add_hook(tmp_path)
    with pytest.raises(CopilotChatError, match="hook that could act"):
        runtime.send(chat, "Hello")
    assert prompts_sent(tmp_path) == []


def test_a_waiting_message_isnt_sent_after_a_hook_appears(make, tmp_path):
    runtime, _ = make("ask")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    assert runtime.send(chat, "Second") is True
    assert wait(lambda: runtime.snapshot(chat).requests)
    add_hook(tmp_path)
    runtime.answer(chat, runtime.snapshot(chat).requests[0].request_id, REJECT)
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    state = runtime.snapshot(chat)
    assert state.unsent == ("Second",) and "hook" in state.notice
    assert prompts_sent(tmp_path) == ["First"]


def test_a_chat_isnt_reopened_while_a_hook_could_approve(make, tmp_path):
    runtime, _ = make()
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    runtime.adopt(native)
    add_hook(tmp_path)
    with pytest.raises(CopilotChatError, match="hook that could act"):
        runtime.prepare(native, str(tmp_path))
    assert not [m for m in received(tmp_path) if m.get("method") == "session/load"]


def test_a_hook_that_answered_during_a_turn_stops_the_chat_for_good(make, tmp_path):
    """Belt and braces: the log shows a hook ran though none was found, so
    wherever it is, it's still there; reopening doesn't clear it."""
    runtime, _ = make("hookran")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert "hook answered" in runtime.snapshot(chat).problem and not runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="hook answered"):
        runtime.send(chat, "Again")
    runtime.prepare(chat, str(tmp_path))
    assert not runtime.ready(chat) and "hook answered" in runtime.snapshot(chat).problem


def test_a_chat_used_in_another_copilot_must_be_opened_again(make, tmp_path):
    """Copilot keeps client_name: thechatplace when a terminal resumes the
    chat (checked with 1.0.103), so the log is what shows it."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert runtime.ready(chat)
    append_event(tmp_path, chat, "session.resume", {"alreadyInUse": False})
    append_event(tmp_path, chat, "user.message", {"content": "from a terminal"})
    with pytest.raises(CopilotChatError, match="used outside The Chat Place"):
        runtime.send(chat, "Two")
    assert not runtime.ready(chat)
    runtime.prepare(chat, str(tmp_path))  # reloads what the terminal added
    assert runtime.ready(chat)
    methods = [m.get("method") for m in received(tmp_path)]
    assert methods.index("session/close") < len(methods) - 1 - methods[::-1].index("session/load")
    runtime.send(chat, "Two")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert prompts_sent(tmp_path) == ["One", "Two"]


def test_our_own_turns_dont_look_like_use_elsewhere(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    for text in ("One", "Two", "Three"):
        runtime.send(chat, text)
        assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                    and not runtime._chats[chat].busy)
    assert runtime.ready(chat) and prompts_sent(tmp_path) == ["One", "Two", "Three"]


def test_another_copilot_sending_during_a_turn_is_noticed(make, tmp_path):
    runtime, _ = make("twousers")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert runtime.send(chat, "Waiting") is True
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    state = runtime.snapshot(chat)
    assert state.unsent == ("Waiting",) and "used outside" in state.notice
    assert not runtime.ready(chat) and prompts_sent(tmp_path) == ["One"]


def test_a_chat_open_in_another_copilot_isnt_opened_here(make, tmp_path):
    runtime, _ = make("inuse")
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    runtime.adopt(native)
    with pytest.raises(CopilotChatError, match="open in another Copilot"):
        runtime.prepare(native, str(tmp_path))
    assert not runtime.ready(native)
    with pytest.raises(CopilotChatError):
        runtime.send(native, "Hello")
    # Trying again closes it in this app's Copilot first (which has it
    # open), rather than being refused the load.
    with pytest.raises(CopilotChatError, match="open in another Copilot"):
        runtime.prepare(native, str(tmp_path))
    methods = [m.get("method") for m in received(tmp_path)]
    assert methods.count("session/load") == 2 and "session/close" in methods


def test_a_sub_agents_messages_arent_use_elsewhere(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    append_event(tmp_path, chat, "user.message", {"content": "task", "parentAgentTaskId": "t1"})
    runtime.send(chat, "Two")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert runtime.ready(chat)


def test_a_reopened_chat_stays_ready_after_its_next_turn(make, tmp_path):
    """Copilot logs this app's own reopening (session.resume, not in use
    elsewhere) when the next prompt arrives, inside that turn's stretch."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    append_event(tmp_path, chat, "user.message", {"content": "from a terminal"})
    with pytest.raises(CopilotChatError, match="used outside"):
        runtime.send(chat, "Two")
    runtime.prepare(chat, str(tmp_path))
    runtime.send(chat, "Two")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    log = (tmp_path / "sessions" / chat / "events.jsonl").read_text(encoding="utf-8")
    assert '"session.resume"' in log  # the fake logged it in the turn, as Copilot does
    assert runtime.ready(chat)


def test_a_chat_found_in_use_elsewhere_after_reopening_is_noticed(make, tmp_path):
    runtime, _ = make("inuselater")
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    runtime.adopt(native)
    runtime.prepare(native, str(tmp_path))
    runtime.send(native, "Hello")
    assert runtime.send(native, "Waiting") is True
    assert wait(lambda: runtime.snapshot(native).outcome == "completed")
    state = runtime.snapshot(native)
    assert state.unsent == ("Waiting",) and "used outside" in state.notice
    assert not runtime.ready(native)


def test_probe_tool_needs_a_folder(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    import probe_copilot
    with pytest.raises(SystemExit):
        probe_copilot.main([])
    assert "--folder" in capsys.readouterr().err


# -- Kelly's third review of #364 ------------------------------------------------


def foreign_message_line():
    return json.dumps({"type": "user.message", "data": {"content": "from a terminal"},
                       "id": "terminal-1"}) + "\n"


def test_a_half_written_line_is_read_whole_next_time(tmp_path):
    from thechatplace.providers.copilot_runtime import _whole_lines_end, log_events
    log = tmp_path / "events.jsonl"
    first = json.dumps({"type": "assistant.message", "data": {}}) + "\n"
    line = foreign_message_line()
    log.write_bytes((first + line[:20]).encode())  # cut at byte 20, as in the review
    end = _whole_lines_end(log)
    assert end == len(first.encode())
    assert log_events(log, 0, ("user.message",), end) == []
    with open(log, "ab") as handle:
        handle.write(line[20:].encode())
    assert [kind for kind, _ in log_events(log, end, ("user.message",), _whole_lines_end(log))] \
        == ["user.message"]


def test_a_terminal_line_cut_at_a_turns_end_is_still_seen(make, tmp_path, monkeypatch):
    """Another Copilot is partway through a line when a turn's end samples
    the log: the next check reads the whole line, and the chat must be
    opened again."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    log = tmp_path / "sessions" / chat / "events.jsonl"
    line = foreign_message_line()
    real = runtime._check_billing

    def billing_then_half_a_line(state, outcome):
        real(state, outcome)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(line[:20])
    monkeypatch.setattr(runtime, "_check_billing", billing_then_half_a_line)
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    monkeypatch.setattr(runtime, "_check_billing", real)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(line[20:])
    with pytest.raises(CopilotChatError, match="used outside"):
        runtime.send(chat, "Two")
    assert prompts_sent(tmp_path) == ["One"]


def test_what_a_terminal_writes_before_a_queued_turn_starts_is_seen(make, tmp_path, monkeypatch):
    """The hook search at a turn's end takes a while; a terminal message
    written meanwhile falls in the next (queued) turn's stretch, not between."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    log = tmp_path / "sessions" / chat / "events.jsonl"
    real = runtime._hooks_refusal
    written = []

    def search_while_a_terminal_writes(cwd):
        # Only the search at the first turn's end, after Copilot answered.
        if runtime._chats[chat].turns == 1 and runtime._chats[chat].answered and not written:
            written.append(1)
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(foreign_message_line())
        return real(cwd)
    monkeypatch.setattr(runtime, "_hooks_refusal", search_while_a_terminal_writes)
    runtime.send(chat, "One")
    assert runtime.send(chat, "Two") is True
    assert wait(lambda: runtime.snapshot(chat).turn_id == "2"
                and runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert written and not runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="used outside"):
        runtime.send(chat, "Three")


def test_a_terminal_message_counts_when_ours_never_reached_copilot(make, tmp_path, monkeypatch):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    log = tmp_path / "sessions" / chat / "events.jsonl"
    connection = runtime._connection
    with connection._lock:  # ours will never be written
        for n in range(copilot_acp.MAX_PENDING):
            connection._pending[f"busy-{n}"] = {"done": threading.Event(), "message": None}
    real = runtime._check_billing

    def a_terminal_writes_during_the_turn(state, outcome):
        with open(log, "a", encoding="utf-8") as handle:  # inside this turn's stretch
            handle.write(foreign_message_line())
        real(state, outcome)
    monkeypatch.setattr(runtime, "_check_billing", a_terminal_writes_during_the_turn)
    try:
        runtime.send(chat, "Hello")
        assert wait(lambda: runtime.snapshot(chat).outcome == "failed"
                    and not runtime._chats[chat].busy)
    finally:
        with connection._lock:
            for n in range(copilot_acp.MAX_PENDING):
                connection._pending.pop(f"busy-{n}")
    assert runtime._chats[chat].elsewhere and runtime.snapshot(chat).unsent == ("Hello",)


@pytest.fixture
def live_other_pid():
    """A process that's running and isn't this app's Copilot."""
    process = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(60)"])
    yield process.pid
    process.kill()
    process.wait(5)


def test_a_chat_another_copilot_has_open_isnt_sent_to(make, tmp_path, live_other_pid):
    """Copilot holds inuse.<pid>.lock while it has a chat open (1.0.103)."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    folder = tmp_path / "sessions" / chat
    (folder / f"inuse.{live_other_pid}.lock").write_text("", encoding="utf-8")
    with pytest.raises(CopilotChatError, match="open in another Copilot"):
        runtime.send(chat, "Hello")
    assert not runtime.ready(chat) and prompts_sent(tmp_path) == []


def test_locks_left_by_copilots_that_exited_or_by_this_apps_own_dont_count(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    folder = tmp_path / "sessions" / chat
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(10)
    (folder / f"inuse.{dead.pid}.lock").write_text("", encoding="utf-8")
    own = runtime._connection._process.process.pid
    (folder / f"inuse.{own}.lock").write_text("", encoding="utf-8")
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")


def test_a_chat_another_copilot_has_open_isnt_reopened(make, tmp_path, live_other_pid):
    runtime, _ = make()
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    (tmp_path / "sessions" / native / f"inuse.{live_other_pid}.lock").write_text("", encoding="utf-8")
    runtime.adopt(native)
    with pytest.raises(CopilotChatError, match="open in another Copilot"):
        runtime.prepare(native, str(tmp_path))
    assert not [m for m in received(tmp_path) if m.get("method") == "session/load"]


def test_a_stale_lock_whose_number_a_newer_process_has_doesnt_count(make, tmp_path, live_other_pid):
    """Copilot leaves its lock behind when it exits, and process ids are
    handed out again: a lock older than the live process with its number was
    left by another, finished one."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    lock = tmp_path / "sessions" / chat / f"inuse.{live_other_pid}.lock"
    lock.write_text("", encoding="utf-8")
    old = time.time() - 3600  # written an hour before that process started
    os.utime(lock, (old, old))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")


def test_a_queued_message_isnt_sent_once_another_copilot_has_the_chat(make, tmp_path,
                                                                      live_other_pid, monkeypatch):
    """A terminal opens the chat while a turn runs: Copilot logs nothing of
    it until that terminal's first prompt, but its lock is there, so the
    waiting message comes back rather than going."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    folder = tmp_path / "sessions" / chat
    real = runtime._check_billing

    def a_terminal_opens_it(state, outcome):
        (folder / f"inuse.{live_other_pid}.lock").write_text("", encoding="utf-8")
        real(state, outcome)
    monkeypatch.setattr(runtime, "_check_billing", a_terminal_opens_it)
    runtime.send(chat, "One")
    assert runtime.send(chat, "Two") is True
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert runtime.snapshot(chat).unsent == ("Two",) and prompts_sent(tmp_path) == ["One"]


def test_a_log_shorter_than_last_time_means_the_chat_is_opened_again(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    (tmp_path / "sessions" / chat / "events.jsonl").write_bytes(b"")  # replaced
    with pytest.raises(CopilotChatError, match="used outside"):
        runtime.send(chat, "Two")


def test_process_start_times_are_known_for_running_processes_only():
    started = platform_paths.processes_started_at([os.getpid()])
    assert started[os.getpid()] <= time.time()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(10)  # exited, though this test still holds its handle
    assert dead.pid not in platform_paths.processes_started_at([dead.pid, os.getpid()])


# -- Kelly's follow-ups on approving #364 ----------------------------------------


@pytest.mark.parametrize("text, seconds", [("05:23", 323), ("02:03:04", 7384),
                                           ("1-02:03:04", 93784), ("", None), ("soon", None)])
def test_ps_elapsed_times_are_read(text, seconds):
    assert platform_paths.etime_seconds(text) == seconds


def test_the_fakes_own_lock_is_really_checked(make, tmp_path, monkeypatch):
    """The fake holds inuse.<its pid>.lock, as Copilot does: if this app
    couldn't tell its own Copilot, every send would be refused."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    own = runtime._connection._process.process.pid
    assert (tmp_path / "sessions" / chat / f"inuse.{own}.lock").exists()
    monkeypatch.setattr(runtime, "_is_own", lambda connection: lambda pid: False)
    with pytest.raises(CopilotChatError, match="open in another Copilot"):
        runtime.send(chat, "Hello")


def test_a_lock_that_goes_while_looking_is_skipped_and_one_unreadable_counts(tmp_path, monkeypatch,
                                                                            live_other_pid):
    from thechatplace.providers import copilot_runtime
    folder = tmp_path / "chat"
    folder.mkdir()
    (folder / f"inuse.{live_other_pid}.lock").write_text("", encoding="utf-8")
    (folder / "inuse.1.hold").write_text("", encoding="utf-8")
    real = os.stat

    def gone(path, *args, **kwargs):
        if str(path).endswith(".lock"):
            raise FileNotFoundError(path)
        return real(path, *args, **kwargs)
    monkeypatch.setattr(copilot_runtime.os, "stat", gone)
    assert not copilot_runtime.locked_elsewhere(folder, None)

    def unreadable(path, *args, **kwargs):
        if str(path).endswith(".lock"):
            raise PermissionError(path)
        return real(path, *args, **kwargs)
    monkeypatch.setattr(copilot_runtime.os, "stat", unreadable)
    assert copilot_runtime.locked_elsewhere(folder, None)


def test_no_cancel_timer_is_left_when_copilot_answers_during_stop(make, tmp_path):
    """No timer is left for a turn that has ended (it made a test flaky).
    The timer is kept in the same locked step that queues the cancel, so
    Copilot's answer to the cancel always finds it, and clears it."""
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    assert runtime.stop(chat).cancelled
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
    assert runtime._chats[chat].cancel_timer is None


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows process list")
def test_every_running_process_has_its_real_start_on_windows():
    """Read without opening any process, so another user's (a service whose
    id a stale Copilot lock names) has its start too, never an unknown that
    would count (#372)."""
    every = platform_paths._windows_process_starts()
    filetime = platform_paths.process_start(os.getpid())
    assert abs(every[os.getpid()] - (filetime / 10_000_000 - 11_644_473_600)) < 0.01
    others = [pid for pid in list(every)[:400] if platform_paths.process_start(pid) == platform_paths.NOT_YOURS]
    assert all(every[pid] > 0 for pid in others)
    dead = subprocess.Popen([PYTHON, "-c", "pass"])
    dead.wait(10)
    assert dead.pid not in platform_paths._windows_process_starts()


# -- Kelly's review of #372 -------------------------------------------------------


def test_a_turn_with_a_message_waiting_is_heard_to_end_first(make, tmp_path):
    """The window hears turn 1 complete, with its reply, before turn 2 starts:
    one update for both skipped turn 1's announcement."""
    runtime, updates = make("slow")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    assert runtime.send(chat, "Second") is True
    assert wait(lambda: runtime.snapshot(chat).turn_id == "2"
                and runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    seen = [(u.turn_id, u.outcome) for u in updates]
    first_done = seen.index(("1", "completed"))
    assert first_done < seen.index(("2", "inProgress"))
    assert [text for _id, text in updates[first_done].replies] == ["Hello there: First"]
    assert prompts_sent(tmp_path) == ["First", "Second"]


def test_a_stop_between_two_turns_sends_nothing_more(make, tmp_path, monkeypatch):
    """Stop pressed just as one turn ends and the waiting message's begins:
    that message comes back unsent and never reaches Copilot."""
    runtime, _ = make("slow")
    chat = runtime.create(str(tmp_path))
    real = runtime._deliver
    stopped = []

    def stop_in_the_gap(state):
        real(state)
        if state.turn_id == "1" and state.outcome == "completed" and not stopped:
            stopped.append(runtime.stop(chat))
    monkeypatch.setattr(runtime, "_deliver", stop_in_the_gap)
    runtime.send(chat, "First")
    assert runtime.send(chat, "Second") is True
    assert wait(lambda: stopped and runtime.snapshot(chat).turn_id == "2"
                and not runtime._chats[chat].busy)
    assert runtime.snapshot(chat).unsent == ("Second",)
    assert prompts_sent(tmp_path) == ["First"]


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows process list")
def test_the_process_list_is_trusted_only_if_it_agrees_about_this_process(monkeypatch):
    real = platform_paths.process_start
    monkeypatch.setattr(platform_paths, "process_start",
                        lambda pid: real(pid) + 10 ** 9 if pid == os.getpid() else real(pid))
    assert platform_paths._windows_process_starts() is None  # the careful way is used instead


@pytest.mark.parametrize("before, after, counts", [
    (95_000, 20_000, True),
    (85_000, 45_000, True),  # from past the 80% warning to below it
    (70_000, 30_000, False),  # the warning was never reached, so not again
])
def test_a_sharp_drop_in_context_counts_as_a_compaction(make, tmp_path, before, after, counts):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    connection = runtime._connection
    for used in (before - 5_000, before, after):
        runtime._on_notification(connection, "session/update", {
            "sessionId": chat, "update": {"sessionUpdate": "usage_update", "used": used, "size": 100_000}})
    state = runtime.snapshot(chat)
    assert state.context_epoch == (1 if counts else 0)
    assert state.token_usage.share == after / 100_000


def test_the_window_hears_when_copilot_has_already_answered(make, tmp_path):
    runtime, updates = make("nolog")  # the billing check then waits a moment
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: any(u.checking for u in updates))
    assert wait(lambda: not runtime._chats[chat].busy)
    assert not runtime.snapshot(chat).checking


def test_a_new_chat_listed_before_its_log_is_never_untitled(make, tmp_path):
    from thechatplace.providers.copilot import CopilotProvider
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    provider = CopilotProvider(tmp_path / "sessions", runtime=runtime)
    row = next(info for info in provider.collect().sessions if info.cli_session_id == chat)
    assert row.title == "New Copilot chat" and not row.is_own



def test_stop_while_an_answer_is_checked_takes_back_the_waiting_message(make, tmp_path):
    """Kelly's review of #372: once Copilot has answered, Stop cancels
    nothing, but it still takes back what's waiting, so it isn't sent."""
    runtime, updates = make("nolog")  # the billing check then waits a moment
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "One")
    assert runtime.send(chat, "Two") is True
    assert wait(lambda: any(u.checking for u in updates))
    # Not cancelled: the window words Stop from this, not its own state,
    # which can lag behind (Kelly, #372).
    assert runtime.stop(chat) == Stopped(("Two",), cancelled=False, answered=True)
    assert wait(lambda: not runtime._chats[chat].busy)
    assert runtime.stop(chat) == Stopped()  # nothing running
    assert prompts_sent(tmp_path) == ["One"]
    assert not [m for m in received(tmp_path) if m.get("method") == "session/cancel"]
    assert runtime.snapshot(chat).unsent == ()  # given back once, by Stop, not again


def test_a_chat_whose_log_was_removed_isnt_listed_again(make, tmp_path):
    import shutil
    from thechatplace.providers.copilot import CopilotProvider
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed" and not runtime._chats[chat].busy)
    provider = CopilotProvider(tmp_path / "sessions", runtime=runtime)
    assert [info.cli_session_id for info in provider.collect().sessions] == [chat]  # from its log
    shutil.rmtree(tmp_path / "sessions" / chat)  # removed outside the app
    assert not [info for info in provider.collect().sessions if info.cli_session_id == chat]


def _locked(*args, **kwargs):
    raise PermissionError(32, "The process cannot access the file")


def _half_written(*args, **kwargs):
    raise UnicodeDecodeError("utf-8", bytes([0xE2, 0x80]), 0, 2, "unexpected end of data")


def _touch_log(tmp_path, chat):
    with open(tmp_path / "sessions" / chat / "events.jsonl", "a", encoding="utf-8") as log:
        log.write("\n")  # a new size, so it's read again


@pytest.mark.parametrize("runs_it", [True, False])
@pytest.mark.parametrize("failure", [_locked, _half_written])
def test_a_chat_whose_log_cant_be_read_for_a_moment_stays_listed(make, tmp_path, monkeypatch, runs_it,
                                                                 failure):
    """Kelly's reviews of #372 and #386: a read that fails while Copilot
    writes the log (a sharing violation on Windows) keeps the chat listed as
    last read, so it neither drops out nor moves, and focus stays. Read-only
    chats too, Last message included, but not what the last read said."""
    from thechatplace.providers import copilot
    from thechatplace.providers.copilot import CopilotProvider
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed" and not runtime._chats[chat].busy)
    provider = CopilotProvider(tmp_path / "sessions", runtime=runtime if runs_it else None)
    [before] = provider.collect().sessions
    assert before.cli_session_id == chat and before.title == "synthetic"  # from its log
    provider.fill_last_messages([before])
    assert before.last_message

    monkeypatch.setattr(copilot, "_read_entry", failure)
    _touch_log(tmp_path, chat)
    snapshot = provider.collect()
    assert snapshot.unreadable_files == 1
    [row] = snapshot.sessions
    assert (row.cli_session_id, row.title, row.source, row.created_ms, row.last_activity_ms) == (
        chat, before.title, before.source, before.created_ms, before.last_activity_ms)
    # A chat this app runs gets its detail from the runtime; a read-only
    # one keeps what its last read and the clock say.
    assert row.detail == ("" if runs_it else before.detail)
    provider.fill_last_messages([row])
    assert row.last_message == before.last_message

    monkeypatch.undo()  # readable again: read afresh, not kept
    assert [info.title for info in provider.collect().sessions] == ["synthetic"]


@pytest.mark.parametrize("gone", ["events.jsonl", "workspace.yaml"])
def test_a_chat_whose_log_is_gone_isnt_kept_listed(make, tmp_path, monkeypatch, gone):
    """Kelly's review of #386: only a read that failed keeps a chat listed.
    A log, or a workspace file past its grace period, that's gone hides it,
    as before, rather than listing it under its old title until restart."""
    from thechatplace.providers import copilot
    from thechatplace.providers.copilot import CopilotProvider
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed" and not runtime._chats[chat].busy)
    provider = CopilotProvider(tmp_path / "sessions", runtime=runtime)
    assert [info.cli_session_id for info in provider.collect().sessions] == [chat]
    (tmp_path / "sessions" / chat / gone).unlink()
    monkeypatch.setattr(copilot, "WORKSPACE_GRACE_SECONDS", 0)
    assert provider.collect().sessions == []


def test_a_new_chat_stays_listed_during_its_first_turn(make, tmp_path, monkeypatch):
    """Copilot writes the log a moment after the first message is sent: the
    chat stays listed from the runtime meanwhile (the review of the fix above)."""
    from thechatplace.providers.copilot import CopilotProvider
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")  # running; the fake logs only the user.message
    (tmp_path / "sessions" / chat / "events.jsonl").unlink(missing_ok=True)  # not written yet
    provider = CopilotProvider(tmp_path / "sessions", runtime=runtime)
    assert [info.cli_session_id for info in provider.collect().sessions] == [chat]
    runtime.stop(chat)


# -- Continue Here (#352) ---------------------------------------------------------------


def test_the_conversation_so_far_goes_ahead_of_the_first_message_only(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.set_preamble(chat, "HISTORY\n\n")
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    runtime.send(chat, "Two")
    assert wait(lambda: runtime.snapshot(chat).turn_id == "2"
                and runtime.snapshot(chat).outcome == "completed")
    assert prompts_sent(tmp_path) == ["HISTORY\n\nOne", "Two"]
    with pytest.raises(CopilotChatError):
        runtime.set_preamble("not-a-chat", "x")


def test_the_conversation_so_far_waits_for_a_message_that_goes(make, tmp_path):
    """A first message that never left keeps the history for the next."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.set_preamble(chat, "HISTORY\n\n")
    connection = runtime._connection
    real = connection.request

    def not_sent_once(method, params, *args, **kwargs):
        if method == "session/prompt" and not getattr(not_sent_once, "done", False):
            not_sent_once.done = True
            raise copilot_acp.AcpNotSent("Copilot couldn't take your message.")
        return real(method, params, *args, **kwargs)
    connection.request = not_sent_once
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "failed"
                and not runtime._chats[chat].busy)
    assert runtime._chats[chat].preamble == "HISTORY\n\n"
    runtime.send(chat, "One again")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert prompts_sent(tmp_path) == ["HISTORY\n\nOne again"]


def test_a_first_message_that_didnt_go_says_the_history_waits(make, tmp_path):
    from thechatplace.providers.copilot_runtime import PREAMBLE_WAITING
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.set_preamble(chat, "HISTORY\n\n")
    connection = runtime._connection
    real = connection.request

    def not_sent(method, params, *args, **kwargs):
        if method == "session/prompt":
            raise copilot_acp.AcpNotSent("Copilot couldn't take your message.")
        return real(method, params, *args, **kwargs)
    connection.request = not_sent
    runtime.send(chat, "One")
    assert wait(lambda: not runtime._chats[chat].busy and runtime.snapshot(chat).outcome)
    assert PREAMBLE_WAITING in runtime.snapshot(chat).notice


def test_the_cap_counts_the_conversation_so_far_too(make, tmp_path):
    """The history goes in the same prompt as the first message, so the two
    together are held to one message's limit (#423 review)."""
    from thechatplace.providers.copilot_runtime import MAX_MESSAGE_CHARS, PREAMBLE_WAITING
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    history = "H" * 100_000
    runtime.set_preamble(chat, history)
    with pytest.raises(CopilotChatError, match="too long to send to Copilot with the "
                                              "conversation so far") as refused:
        runtime.send(chat, "x" * (MAX_MESSAGE_CHARS - len(history) + 1))
    assert f"{MAX_MESSAGE_CHARS - len(history):,} characters" in str(refused.value)
    assert PREAMBLE_WAITING in str(refused.value)
    assert runtime._chats[chat].preamble == history  # still waiting
    assert not [m for m in received(tmp_path) if m.get("method") == "session/prompt"]
    runtime.send(chat, "x" * (MAX_MESSAGE_CHARS - len(history)))  # exactly fits
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert runtime._chats[chat].preamble == ""


def test_a_refused_first_message_says_the_history_waits(make, tmp_path):
    from thechatplace.providers.copilot_runtime import PREAMBLE_WAITING
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.set_preamble(chat, "HISTORY\n\n")
    for text, why in (("   ", "nothing to send"), ("/allow-all", "starting with /")):
        with pytest.raises(CopilotChatError, match=why) as refused:
            runtime.send(chat, text)
        assert str(refused.value).endswith(PREAMBLE_WAITING)
    assert runtime._chats[chat].preamble == "HISTORY\n\n"
    runtime.send(chat, "One")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed"
                and not runtime._chats[chat].busy)
    assert prompts_sent(tmp_path) == ["HISTORY\n\nOne"]
    # Gone with that message: a later refusal doesn't mention it.
    with pytest.raises(CopilotChatError) as refused:
        runtime.send(chat, "/help")
    assert PREAMBLE_WAITING not in str(refused.value)


def test_a_chat_without_history_is_refused_as_before(make, tmp_path):
    from thechatplace.providers.copilot_runtime import PREAMBLE_WAITING
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    with pytest.raises(CopilotChatError) as refused:
        runtime.send(chat, "/help")
    assert PREAMBLE_WAITING not in str(refused.value)


def test_a_refusal_while_the_first_turn_starts_doesnt_say_the_history_waits(make, tmp_path):
    """Once the first message's turn has begun the history is going with it,
    even before Copilot has taken the prompt (#423 review)."""
    from thechatplace.providers.copilot_runtime import PREAMBLE_WAITING
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.set_preamble(chat, "HISTORY\n\n")
    with runtime._lock:
        runtime._chats[chat].busy = True  # begun; the prompt not yet queued
    try:
        with pytest.raises(CopilotChatError) as refused:
            runtime.send(chat, "/help")
        assert PREAMBLE_WAITING not in str(refused.value)
    finally:
        with runtime._lock:
            runtime._chats[chat].busy = False


def test_history_set_while_a_message_is_checked_is_held_to_the_cap_too(make, tmp_path):
    """The check under the lock, for history that arrived after the first
    check, says the same as the first check does."""
    from thechatplace.providers.copilot_runtime import MAX_MESSAGE_CHARS
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    history = "H" * 100_000
    real = runtime._hooks_refusal

    def late_history(cwd):
        runtime.set_preamble(chat, history)  # between the two checks
        return real(cwd)
    runtime._hooks_refusal = late_history
    with pytest.raises(CopilotChatError) as refused:
        runtime.send(chat, "x" * (MAX_MESSAGE_CHARS - len(history) + 1))
    assert f"({len(history):,} characters)" in str(refused.value)
    assert f"{MAX_MESSAGE_CHARS - len(history):,} characters or put" in str(refused.value)
    assert not [m for m in received(tmp_path) if m.get("method") == "session/prompt"]


# -- choosing the model -----------------------------------------------------------------


OFFERED = (("auto", "Auto (1x)"), ("gpt-test", "GPT Test (1x)"), ("gpt-mini", "GPT Mini (0.33x)"))


def test_a_new_chat_lists_the_models_copilot_offers(make, tmp_path):
    """Auto, listed twice by Copilot, once; each with what a message costs."""
    runtime, _ = make()
    assert runtime.models() == ()
    chat = runtime.create(str(tmp_path))
    state = runtime.snapshot(chat)
    assert state.model == "auto" and state.models == OFFERED
    assert runtime.models() == OFFERED


def test_changing_a_chats_model(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime.set_model(chat, "gpt-mini")
    assert runtime.snapshot(chat).model == "gpt-mini"
    sent = [m for m in received(tmp_path) if m.get("method") == "session/set_config_option"]
    assert sent[-1]["params"] == {"sessionId": chat, "configId": "model", "value": "gpt-mini"}
    with pytest.raises(CopilotChatError, match="doesn't offer"):
        runtime.set_model(chat, "not-offered")
    assert runtime.ready(chat)  # still safe to send: mode and allow_all unchanged


def test_a_model_copilot_refuses_is_said(make, tmp_path):
    runtime, _ = make("nomodel")
    chat = runtime.create(str(tmp_path))
    with pytest.raises(CopilotChatError, match="couldn't switch"):
        runtime.set_model(chat, "gpt-test")
    assert runtime.snapshot(chat).model == "auto"


def test_a_new_chat_starts_on_the_model_chosen(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path), model="gpt-test")
    assert runtime.snapshot(chat).model == "gpt-test"
    # Refused, the chat is still made, on Copilot's own, and says so.
    runtime2, _ = make("nomodel")
    other = runtime2.create(str(tmp_path), model="gpt-test")
    state = runtime2.snapshot(other)
    assert state.model == "auto" and "Change Model" in state.notice


def test_odd_model_lists_are_made_safe():
    from thechatplace.providers.copilot_runtime import _models
    assert _models(None) is None and _models([{"id": "mode"}]) is None
    found = _models([{"id": "model", "currentValue": "b", "options": [
        "odd", {"value": ""}, {"value": "a\u202e", "name": "Flipped"},
        {"value": "b", "name": "  Bee \n model "}, {"value": "c", "name": "\u200b"},
        {"value": "d", "_meta": {"copilotUsage": 7}}]}])
    assert found == ("b", (("b", "Bee model"), ("c", "c"), ("d", "d")))


def test_a_model_change_that_stops_copilot_asking_stops_the_turn(make, tmp_path):
    """Copilot's reply to a model change names its other settings too: one
    saying it no longer asks before it acts counts, as a config_option_update
    would, and stops the turn running."""
    runtime, _ = make("modelflip")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    runtime.set_model(chat, "gpt-mini")
    assert not runtime.ready(chat)
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
    assert "stopped asking" in runtime.snapshot(chat).notice


def test_a_model_reply_that_comes_after_the_chat_was_opened_again_is_ignored(make, tmp_path):
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    connection = runtime._connection
    real = connection.request

    def reopened_meanwhile(method, params, *args, **kwargs):
        result = real(method, params, *args, **kwargs)
        if method == "session/set_config_option":
            runtime._chats[chat].connection = object()  # as if opened on another Copilot
        return result
    connection.request = reopened_meanwhile
    runtime.set_model(chat, "gpt-mini")
    assert runtime._chats[chat].model == "auto"


def test_a_models_cost_is_kept_whatever_its_name():
    from thechatplace.providers.copilot_runtime import _models
    found = _models([{"id": "model", "currentValue": "x", "options": [
        {"value": "x", "name": "Model X (preview)"},
        {"value": "x", "name": "Model X (preview)", "_meta": {"copilotUsage": "3x"}}]}])
    assert found == ("x", (("x", "Model X (preview) (3x)"),))


def test_grouped_model_options_are_read_as_one_list():
    """ACP's SessionConfigSelectOptions may come in groups, each with its own
    options: one level of those is read in place, in order."""
    from thechatplace.providers.copilot_runtime import _models
    found = _models([{"id": "model", "currentValue": "b", "options": [
        {"value": "a", "name": "A"},
        {"group": "fast", "name": "Fast", "options": [
            {"value": "b", "name": "B", "_meta": {"copilotUsage": "0.33x"}},
            {"value": "a", "name": "A", "_meta": {"copilotUsage": "1x"}}]},
        {"group": "deep", "name": "Deeper", "options": [
            {"group": "inner", "options": [{"value": "z", "name": "Too deep"}]},
            {"value": "c", "name": "C"}]},
        {"group": "odd", "options": "not a list"}]}])
    assert found == ("b", (("a", "A (1x)"), ("b", "B (0.33x)"), ("c", "C")))


def test_a_model_reply_older_than_another_switch_is_ignored(make, tmp_path):
    """Two switches in flight: the first one's reply, arriving after the
    second was asked for, mustn't overwrite what the second leaves."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    connection = runtime._connection
    real = connection.request

    def switched_again_meanwhile(method, params, *args, **kwargs):
        result = real(method, params, *args, **kwargs)
        if method == "session/set_config_option" and params.get("value") == "gpt-test":
            runtime.set_model(chat, "gpt-mini")  # asked for, and answered, first
        return result
    connection.request = switched_again_meanwhile
    runtime.set_model(chat, "gpt-test")
    assert runtime._chats[chat].model == "gpt-mini"


def test_a_failed_switch_doesnt_make_an_earlier_ones_reply_look_old(make, tmp_path):
    """Switch A succeeds; switch B, asked for before A's reply is taken in,
    fails. Copilot is on A, so A's reply still counts and A is recorded."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    connection = runtime._connection
    real = connection.request

    def second_switch_fails_meanwhile(method, params, *args, **kwargs):
        if method == "session/set_config_option" and params.get("value") == "gpt-mini":
            raise copilot_acp.AcpError("refused")
        result = real(method, params, *args, **kwargs)
        if method == "session/set_config_option" and params.get("value") == "gpt-test":
            with pytest.raises(CopilotChatError, match="couldn't switch"):
                runtime.set_model(chat, "gpt-mini")
        return result
    connection.request = second_switch_fails_meanwhile
    runtime.set_model(chat, "gpt-test")
    assert runtime.snapshot(chat).model == "gpt-test"
    assert runtime.ready(chat)


def test_a_model_is_refused_for_a_chat_that_isnt_open(make, tmp_path):
    runtime, _ = make()
    with pytest.raises(CopilotChatError, match="isn't open here yet"):
        runtime.set_model("no-such-chat", "gpt-mini")


# -- Kelly's follow-ups (#405) ------------------------------------------------------


def test_stopping_again_doesnt_put_the_forced_close_off(make, tmp_path):
    """A second Stop while the first cancel is pending sends nothing more and
    keeps the first timer, so pressing Stop again can't keep Copilot open.
    A Copilot that ignores the cancel ("deaf"), so the turn is still going
    when the second Stop comes, however slow the machine."""
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"),
               FAKE_COPILOT_LOG=str(tmp_path / "received.jsonl"))
    runtime = CopilotChatRuntime(lambda state: None, start=lambda: Process("deaf", env),
                                 sessions_dir=tmp_path / "sessions", billing_wait=0.5,
                                 cancel_grace=1.5)
    try:
        chat = runtime.create(str(tmp_path))
        runtime.send(chat, "Long job")
        assert wait(lambda: runtime.snapshot(chat).replies)
        first = runtime.stop(chat)
        assert first.cancelled and not first.already_asked
        timer = runtime._chats[chat].cancel_timer
        assert timer is not None
        again = runtime.stop(chat)
        assert again.cancelled and again.already_asked
        assert runtime._chats[chat].cancel_timer is timer
        # The first Stop's clock still runs out, and closes it.
        assert wait(lambda: "didn't stop when asked" in runtime.snapshot(chat).notice)
        # By then the fake has logged what it was sent (it logs as it reads,
        # which on a busy machine came after the Stops returned): one cancel.
        cancels = [m for m in received(tmp_path) if m.get("method") == "session/cancel"]
        assert len(cancels) == 1
    finally:
        runtime.close()


def test_a_stop_before_the_turn_sends_warns_of_no_close(make, tmp_path):
    """Stopped before the turn decided to send, the message comes back
    unsent: nothing is cancelled, and no close is threatened."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    real_run, stops = runtime._run_turn, []

    def stop_then_run(chat_, connection, text):
        stops.append(runtime.stop(chat))
        real_run(chat_, connection, text)
    runtime._run_turn = stop_then_run
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted"
                and not runtime._chats[chat].busy)
    assert stops == [Stopped((), cancelled=False, before_sending=True)]
    assert runtime.snapshot(chat).unsent == ("Hello",)
    assert not [m for m in received(tmp_path)
                if m.get("method") in ("session/prompt", "session/cancel")]


def test_a_cancel_that_cant_go_out_isnt_reported_as_sent(make, tmp_path):
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)

    def closed(method, params):
        raise copilot_acp.AcpError("Copilot is closing.")
    runtime._connection.notify = closed
    stopped = runtime.stop(chat)
    assert not stopped.cancelled and not stopped.answered
    assert runtime._chats[chat].cancel_timer is None


def test_a_stop_between_the_prompts_write_and_its_queuing_cancels_after_it(tmp_path):
    """The prompt is written, but the turn hasn't yet heard it's queued: the
    Stop leaves the cancel to the turn, which sends it after the prompt."""
    env = dict(os.environ, FAKE_COPILOT_SESSIONS=str(tmp_path / "sessions"),
               FAKE_COPILOT_LOG=str(tmp_path / "received.jsonl"))
    runtime = CopilotChatRuntime(lambda state: None, start=lambda: Process("strict", env),
                                 sessions_dir=tmp_path / "sessions", billing_wait=0.5,
                                 cancel_grace=0.5)
    try:
        chat = runtime.create(str(tmp_path))
        connection = runtime._connection
        real_request, stops = connection.request, []

        def stop_once_written(method, params, *args, on_queued=None, **kwargs):
            def queued():
                if method == "session/prompt":
                    stops.append(runtime.stop(chat))  # written, not yet queued here
                if on_queued is not None:
                    on_queued()
            return real_request(method, params, *args, on_queued=queued, **kwargs)
        connection.request = stop_once_written
        runtime.send(chat, "Long job")
        assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
        assert stops == [Stopped((), cancelled=True)]
        order = [m["method"] for m in received(tmp_path)
                 if m.get("method") in ("session/prompt", "session/cancel")]
        assert order == ["session/prompt", "session/cancel"]
        # Copilot stopped, so the timer that would close it is called off
        # (checked directly, rather than by sleeping past the grace).
        assert wait(lambda: runtime._chats[chat].cancel_timer is None)
        assert runtime.ready(chat) and "didn't stop" not in runtime.snapshot(chat).notice
    finally:
        runtime.close()


def test_a_prompt_refused_before_copilot_logs_it_isnt_use_elsewhere(make, tmp_path):
    """Refused with -32000 before Copilot wrote its user.message (#400): no
    message of ours is in the log, which mustn't look like another Copilot."""
    runtime, _ = make("refused")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "First")
    assert wait(lambda: runtime.snapshot(chat).outcome == "failed"
                and not runtime._chats[chat].busy)
    assert runtime.snapshot(chat).notice == copilot_acp.NOT_SIGNED_IN_SENT
    assert not runtime._chats[chat].elsewhere


# -- plan mode --------------------------------------------------------------------------


def test_a_chat_can_start_in_plan_mode_and_still_be_sent_to(make, tmp_path):
    """Plan mode asks before acting too (allow-all stays off), so it's as
    safe to send to as agent mode; autopilot is never asked for."""
    from thechatplace.providers.copilot_runtime import AGENT_MODE, PLAN_MODE
    runtime, _ = make()
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(chat).mode == PLAN_MODE and runtime.ready(chat)
    runtime.send(chat, "Plan it")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    runtime.set_mode(chat, AGENT_MODE)
    assert runtime.snapshot(chat).mode == AGENT_MODE and runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="agent and plan"):
        runtime.set_mode(chat, "https://agentclientprotocol.com/protocol/session-modes#autopilot")


def test_copilot_switching_itself_to_plan_mode_doesnt_stop_the_turn(make, tmp_path):
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    runtime, _ = make("toplan")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Hello")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    state = runtime.snapshot(chat)
    assert state.mode == PLAN_MODE and "stopped" not in state.notice and runtime.ready(chat)


def test_a_mode_switch_that_stops_copilot_asking_stops_the_turn(make, tmp_path):
    """A switch made while a turn runs, whose reply says allow-all is on:
    Copilot no longer asks before it acts, so the turn is stopped."""
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    runtime, _ = make("modeflip")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    assert wait(lambda: runtime.snapshot(chat).replies)
    runtime.set_mode(chat, PLAN_MODE)  # its reply says allow-all is on
    assert not runtime.ready(chat)
    assert wait(lambda: runtime.snapshot(chat).outcome == "interrupted")
    assert "stopped asking" in runtime.snapshot(chat).notice


def test_a_new_chat_copilot_wont_put_in_plan_mode_sends_nothing_until_switched(make, tmp_path):
    """Plan mode was chosen so nothing would change: refused, the chat
    doesn't carry on in agent mode by itself."""
    from thechatplace.providers.copilot_runtime import PLAN_MODE, PLAN_NOT_SET
    runtime, _ = make("nomode")
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(chat).problem == PLAN_NOT_SET and not runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="plan mode"):
        runtime.send(chat, "Change things")
    assert prompts_sent(tmp_path) == []


@pytest.mark.parametrize("fake", ["keepagent", "modeless"])
def test_plan_mode_copilot_says_yes_to_but_doesnt_confirm_sends_nothing(make, tmp_path, fake):
    """Copilot accepts the switch but its reply still names agent mode, or
    names no mode: plan mode isn't confirmed, so nothing is sent (#424)."""
    from thechatplace.providers.copilot_runtime import AGENT_MODE, PLAN_MODE, PLAN_NOT_SET
    runtime, _ = make(fake)
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    state = runtime.snapshot(chat)
    assert state.mode == AGENT_MODE and state.problem == PLAN_NOT_SET
    assert not runtime.ready(chat)
    with pytest.raises(CopilotChatError, match="plan mode"):
        runtime.send(chat, "Change things")
    assert prompts_sent(tmp_path) == []


def test_a_later_switch_clears_plan_not_set(make, tmp_path):
    """Plan mode refused at the start: choosing agent mode, the mode the chat
    is already in, goes through and lets it send; set_mode says what
    Copilot recorded."""
    from thechatplace.providers.copilot_runtime import AGENT_MODE, PLAN_MODE, PLAN_NOT_SET
    runtime, _ = make("keepagent")
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(chat).problem == PLAN_NOT_SET
    assert runtime.set_mode(chat, PLAN_MODE) == AGENT_MODE  # still not confirmed
    assert runtime.snapshot(chat).problem == PLAN_NOT_SET
    assert runtime.set_mode(chat, AGENT_MODE) == AGENT_MODE
    assert runtime.snapshot(chat).problem == "" and runtime.ready(chat)


def test_a_stale_mode_reply_isnt_taken_as_plan_mode(make, tmp_path):
    """A reply dropped as older than a reopen doesn't count as plan mode
    confirmed: the chat sends nothing."""
    from thechatplace.providers.copilot_runtime import PLAN_MODE, PLAN_NOT_SET
    runtime, _ = make()
    original = runtime._set_option

    def reopened_meanwhile(native_id, config_id, value, refused):
        if config_id == "mode":
            runtime._chats[native_id].settings_applied += 5  # as a reopen would
        return original(native_id, config_id, value, refused)
    runtime._set_option = reopened_meanwhile
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(chat).problem == PLAN_NOT_SET and not runtime.ready(chat)


def test_a_reopened_chat_is_put_back_in_plan_mode(make, tmp_path):
    """Copilot may reopen a chat in agent mode ("forgetmode"); the mode last
    chosen here is switched back to before anything is sent (#424)."""
    from thechatplace.providers.copilot_runtime import AGENT_MODE, PLAN_MODE
    runtime, _ = make("forgetmode")
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(chat).mode == PLAN_MODE
    runtime._chats[chat].elsewhere = True  # so prepare opens it again
    runtime.prepare(chat, str(tmp_path))
    assert runtime.snapshot(chat).mode == AGENT_MODE  # forgotten, with no mode asked for
    runtime._chats[chat].elsewhere = True
    runtime.prepare(chat, str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(chat).mode == PLAN_MODE and runtime.ready(chat)


def test_after_a_restart_a_plan_chat_copilot_wont_switch_back_sends_nothing(make, tmp_path):
    """A new runtime (the app restarted) opens a chat chosen for plan mode,
    which Copilot reopens in agent mode and won't switch: nothing is sent,
    rather than carrying on in agent mode; a switch by hand clears it."""
    from thechatplace.providers.copilot_runtime import AGENT_MODE, MODE_NOT_KEPT, PLAN_MODE
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    runtime, _ = make("nomode")
    runtime.adopt(native)
    runtime.prepare(native, str(tmp_path), mode=PLAN_MODE)
    state = runtime.snapshot(native)
    assert state.mode == AGENT_MODE and state.problem == MODE_NOT_KEPT
    assert not runtime.ready(native)
    with pytest.raises(CopilotChatError, match="another mode"):
        runtime.send(native, "Change things")
    # Opened again, it's still not sendable: the late-record check that
    # clears other problems leaves this one.
    runtime._chats[native].elsewhere = True
    runtime.prepare(native, str(tmp_path), mode=PLAN_MODE)
    assert runtime.snapshot(native).problem == MODE_NOT_KEPT
    assert prompts_sent(tmp_path) == []
    # A Copilot that does switch puts it back in plan mode.
    runtime.close()  # it holds the chat open
    other, _ = make()
    other.adopt(native)
    other.prepare(native, str(tmp_path), mode=PLAN_MODE)
    assert other.snapshot(native).mode == PLAN_MODE and other.ready(native)


def test_copilots_own_files_are_never_changed_files(tmp_path):
    """Only this chat's own session folder: a file under another chat's, or
    elsewhere in Copilot's, is still a changed file."""
    from thechatplace.providers.copilot_runtime import _edited_paths, copilot_own_file
    own = tmp_path / "state" / "abc"
    plan = str(own / "plan.md")
    other_chat = str(tmp_path / "state" / "xyz" / "notes.md")
    work = str(tmp_path / "work" / "a.py")
    assert copilot_own_file(plan, own) and not copilot_own_file(work, own)
    assert not copilot_own_file(other_chat, own)
    assert not copilot_own_file(str(tmp_path / "state" / "abc-other" / "x"), own)
    assert not copilot_own_file(plan, None)
    events = [("tool.execution_complete", {"success": True, "fileEdits": [
        {"path": plan}, {"path": work}, {"path": other_chat}]})]
    assert _edited_paths(events, own) == (work, other_chat)


# -- round 2 of #424's review ---------------------------------------------------------


def test_nothing_can_be_sent_while_a_new_chats_mode_is_being_switched(make, tmp_path):
    """A chat asked for in plan mode is blocked before the switch goes to
    Copilot, so nothing sees it ready in agent mode meanwhile."""
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    runtime, _ = make()
    original, seen = runtime._set_option, []

    def checked(native_id, config_id, value, refused):
        if config_id == "mode":
            seen.append(runtime.ready(native_id))
            with pytest.raises(CopilotChatError, match="plan mode"):
                runtime.send(native_id, "Too soon")
        return original(native_id, config_id, value, refused)
    runtime._set_option = checked
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    assert seen == [False]
    state = runtime.snapshot(chat)
    assert state.mode == PLAN_MODE and state.problem == "" and runtime.ready(chat)
    assert prompts_sent(tmp_path) == []


def test_nothing_can_be_sent_while_a_reopened_chat_is_switched_back(make, tmp_path):
    """Reopened in agent mode, a chat chosen for plan mode is blocked as
    loading ends, before the switch back goes to Copilot."""
    from thechatplace.providers.copilot_runtime import MODE_NOT_KEPT, PLAN_MODE
    runtime, _ = make("forgetmode")
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    original, seen = runtime._set_option, []

    def checked(native_id, config_id, value, refused):
        if config_id == "mode":
            seen.append((runtime.ready(native_id), runtime.snapshot(native_id).problem))
            with pytest.raises(CopilotChatError, match="another mode"):
                runtime.send(native_id, "Too soon")
        return original(native_id, config_id, value, refused)
    runtime._set_option = checked
    runtime._chats[chat].elsewhere = True  # so prepare opens it again
    runtime.prepare(chat, str(tmp_path), mode=PLAN_MODE)
    assert seen == [(False, MODE_NOT_KEPT)]
    assert runtime.snapshot(chat).mode == PLAN_MODE and runtime.ready(chat)
    assert prompts_sent(tmp_path) == []


def test_agent_mode_asked_for_where_copilot_starts_in_plan_is_switched(make, tmp_path):
    """Copilot's own default needn't be agent ("startplan"): agent asked for
    is switched to, and a refusal says agent mode, not plan mode."""
    from thechatplace.providers.copilot_runtime import AGENT_MODE, AGENT_NOT_SET
    runtime, _ = make("startplan")
    chat = runtime.create(str(tmp_path), mode=AGENT_MODE)
    state = runtime.snapshot(chat)
    assert state.mode == AGENT_MODE and state.problem == "" and runtime.ready(chat)

    def refused(native_id, config_id, value, refused_text):
        raise CopilotChatError(refused_text)
    runtime._set_option = refused
    other = runtime.create(str(tmp_path), mode=AGENT_MODE)
    assert runtime.snapshot(other).problem == AGENT_NOT_SET
    with pytest.raises(CopilotChatError, match="in agent mode"):
        runtime.send(other, "Change things")


def test_a_continue_here_copy_whose_plan_mode_isnt_confirmed_keeps_its_history(make, tmp_path):
    """#424 with #423: a copy asked for in plan mode that Copilot won't
    confirm sends nothing, its history included, and says the history is
    still waiting; once a mode is chosen the first message takes it."""
    from thechatplace.providers.copilot_runtime import (AGENT_MODE, PLAN_MODE, PLAN_NOT_SET,
                                                        PREAMBLE_WAITING)
    runtime, _ = make("keepagent")
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    runtime.set_preamble(chat, "Earlier: history. ")
    assert not runtime.ready(chat)
    with pytest.raises(CopilotChatError) as refused:
        runtime.send(chat, "Carry on")
    assert PLAN_NOT_SET in str(refused.value) and PREAMBLE_WAITING in str(refused.value)
    assert prompts_sent(tmp_path) == []
    assert runtime.set_mode(chat, AGENT_MODE) == AGENT_MODE and runtime.ready(chat)
    runtime.send(chat, "Carry on")
    assert wait(lambda: runtime.snapshot(chat).outcome == "completed")
    assert prompts_sent(tmp_path) == ["Earlier: history. Carry on"]


def test_no_update_carries_a_mode_problem_while_a_reopened_chat_is_switched_back(make, tmp_path):
    """Reopened in agent mode and put back in plan mode: the reply that
    confirms it clears MODE_NOT_KEPT before its update goes out, so the
    window never hears plan mode together with the problem it cleared."""
    from thechatplace.providers.copilot_runtime import MODE_PROBLEMS, PLAN_MODE
    runtime, updates = make("forgetmode")
    chat = runtime.create(str(tmp_path), mode=PLAN_MODE)
    runtime._chats[chat].elsewhere = True  # so prepare opens it again
    updates.clear()
    runtime.prepare(chat, str(tmp_path), mode=PLAN_MODE)
    assert updates and updates[-1].mode == PLAN_MODE and updates[-1].problem == ""
    assert not [u for u in updates if u.problem in MODE_PROBLEMS]
    assert runtime.ready(chat)


def test_a_notice_added_keeps_the_one_the_chat_has(make, tmp_path):
    """A refused model's notice and a mode that couldn't be saved are both
    said: add_notice adds to the chat's notice, never replaces it."""
    runtime, _ = make()
    chat = runtime.create(str(tmp_path))
    runtime._chats[chat].notice = "Model refused."
    runtime.add_notice(chat, "Mode not saved.")
    assert runtime.snapshot(chat).notice == "Model refused. Mode not saved."
    runtime.add_notice(chat, "Mode not saved.")  # not twice
    assert runtime.snapshot(chat).notice == "Model refused. Mode not saved."


# -- saved modes (provider level) -------------------------------------------------------


@pytest.fixture
def provider(make, tmp_path, monkeypatch):
    from thechatplace.providers.copilot import CopilotProvider
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "data")
    (tmp_path / "data").mkdir()

    def build(mode="answer"):
        runtime, _ = make(mode)
        return CopilotProvider(tmp_path / "sessions", runtime=runtime), runtime
    return build


def test_a_saved_plan_mode_is_put_back_when_copilot_reopens_in_agent(provider, tmp_path):
    """Through prepare_owned, as the window opens a chat: Copilot forgets the
    mode across session/load, and the saved one is applied again."""
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    copilot, runtime = provider("forgetmode")
    record = copilot.create_owned(str(tmp_path), "Planner", copilot_mode=PLAN_MODE)
    runtime._chats[record.cli_session_id].elsewhere = True  # so it's loaded again
    copilot.prepare_owned(record)
    state = runtime.snapshot(record.cli_session_id)
    assert state.mode == PLAN_MODE and state.problem == ""
    assert runtime.ready(record.cli_session_id)
    # The load did forget it: the switch was sent again after it.
    sent = [m["params"]["value"] for m in received(tmp_path)
            if m.get("method") == "session/set_config_option"]
    assert sent == [PLAN_MODE, PLAN_MODE]


def test_a_saved_plan_mode_copilot_wont_put_back_blocks_the_chat(provider, tmp_path):
    from thechatplace.own_store import OwnSession
    from thechatplace.providers.copilot import _remember_mode
    from thechatplace.providers.copilot_runtime import MODE_NOT_KEPT, PLAN_MODE
    native = "11111111-2222-3333-4444-555555555555"
    write_workspace(tmp_path, native, "thechatplace")
    copilot, runtime = provider("nomode")
    assert _remember_mode(native, PLAN_MODE) == ""
    copilot.prepare_owned(OwnSession(native, "Planner", str(tmp_path), provider="copilot",
                                     started=True))
    assert runtime.snapshot(native).problem == MODE_NOT_KEPT and not runtime.ready(native)


def test_saved_modes_file_edge_cases(tmp_path, monkeypatch):
    from thechatplace.providers.copilot import (MODE_NOT_SAVED, MODES_SET_ASIDE,
                                                _remember_mode, chosen_mode)
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    native = "11111111-2222-3333-4444-555555555555"
    other = "22222222-2222-3333-4444-555555555555"
    path = tmp_path / "copilot-modes.json"
    # Missing: no mode, nothing to say.
    assert chosen_mode(native) == ("", "")
    # A bad id and an unknown mode are left out; the rest are read.
    autopilot = "https://agentclientprotocol.com/protocol/session-modes#autopilot"
    path.write_text(json.dumps({"version": 1, "modes": {
        "../x": PLAN_MODE, native: autopilot, other: PLAN_MODE}}), encoding="utf-8")
    assert chosen_mode(native) == ("", "")
    assert chosen_mode(other) == (PLAN_MODE, "")
    # Corrupt: moved aside whole, said, and not written over.
    path.write_text("{not json", encoding="utf-8")
    assert _remember_mode(native, PLAN_MODE) == MODES_SET_ASIDE
    aside = list(tmp_path.glob("copilot-modes.json.bad-*"))
    assert [p.read_text(encoding="utf-8") for p in aside] == ["{not json"]
    assert chosen_mode(native) == (PLAN_MODE, "")
    # A second damaged file doesn't write over the first set aside.
    path.write_text("[]", encoding="utf-8")
    assert chosen_mode(native) == ("", MODES_SET_ASIDE)
    aside = list(tmp_path.glob("copilot-modes.json.bad-*"))
    assert sorted(p.read_text(encoding="utf-8") for p in aside) == ["[]", "{not json"]
    # Nor does a file that isn't UTF-8: it's set aside, not taken for one
    # that can't be opened, so saving works again.
    path.write_bytes(bytes([0xFF, 0xFE]) + b"{bad")
    assert chosen_mode(native) == ("", MODES_SET_ASIDE)
    assert len(list(tmp_path.glob("copilot-modes.json.bad-*"))) == 3
    assert _remember_mode(native, PLAN_MODE) == ""
    assert chosen_mode(native) == (PLAN_MODE, "")
    # A save that fails is said.

    def full(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("thechatplace.provider_storage.atomic_write_json", full)
    assert _remember_mode(native, PLAN_MODE) == MODE_NOT_SAVED


def test_a_refused_model_and_an_unsaved_mode_are_both_said(provider, tmp_path, monkeypatch):
    from thechatplace.providers.copilot import MODE_NOT_SAVED
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    copilot, runtime = provider()

    def full(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("thechatplace.provider_storage.atomic_write_json", full)
    record = copilot.create_owned(str(tmp_path), "Both", model="not-offered",
                                  copilot_mode=PLAN_MODE)
    notice = runtime.snapshot(record.cli_session_id).notice
    assert "couldn't switch this chat to the model chosen" in notice
    assert notice.endswith(MODE_NOT_SAVED)


def test_set_mode_owned_saves_only_a_confirmed_mode(provider, tmp_path):
    from thechatplace.providers.copilot import chosen_mode
    from thechatplace.providers.copilot_runtime import AGENT_MODE, PLAN_MODE
    copilot, runtime = provider("keepagent")
    record = copilot.create_owned(str(tmp_path), "Unconfirmed")
    assert copilot.set_mode_owned(record, PLAN_MODE) == AGENT_MODE
    assert chosen_mode(record.cli_session_id) == ("", "")
    assert copilot.set_mode_owned(record, AGENT_MODE) == AGENT_MODE
    assert chosen_mode(record.cli_session_id) == (AGENT_MODE, "")


def test_a_failed_save_of_a_mode_is_the_chats_notice(provider, tmp_path, monkeypatch):
    from thechatplace.providers.copilot import MODE_NOT_SAVED
    from thechatplace.providers.copilot_runtime import PLAN_MODE
    copilot, runtime = provider()

    def full(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("thechatplace.provider_storage.atomic_write_json", full)
    record = copilot.create_owned(str(tmp_path), "Unsaved", copilot_mode=PLAN_MODE)
    assert runtime.snapshot(record.cli_session_id).notice == MODE_NOT_SAVED


LAUNCHER = """
import subprocess, sys, time
sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
print(subprocess.Popen(sleeper).pid, flush=True)
sys.stdin.readline()  # a tool of a turn, later on, starting another
print(subprocess.Popen(sleeper).pid, flush=True)
time.sleep(60)
"""


@pytest.fixture
def launcher():
    """The launcher, the real Copilot it started, and a connection to them.
    Real processes, because it's the process tree that has to know."""
    from types import SimpleNamespace
    tree = platform_paths.ProcessTree()
    process = subprocess.Popen([sys.executable, "-c", LAUNCHER], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, **tree.popen_kwargs())
    try:
        tree.attach(process)
        child = int(process.stdout.readline())
        yield SimpleNamespace(tree=tree, process=process, child=child, connection=SimpleNamespace(
            _process=SimpleNamespace(process=process, tree=tree), own_pids=tree.members()))
    finally:
        tree.kill()
        process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        tree.close()


@pytest.mark.skipif(sys.platform != "win32", reason="only a Windows job can list a tree")
def test_a_chat_held_by_the_copilot_a_launcher_started_is_not_held_elsewhere(launcher, tmp_path):
    """#463: VS Code's copilot command keeps running with the real Copilot as
    its child, and the chat's lock is named for the child."""
    from thechatplace.providers import copilot_runtime
    is_own = CopilotChatRuntime._is_own(launcher.connection)
    assert is_own(launcher.process.pid) and is_own(launcher.child)
    (tmp_path / f"inuse.{launcher.child}.lock").write_text("")
    assert not copilot_runtime.locked_elsewhere(tmp_path, is_own)


@pytest.mark.skipif(sys.platform != "win32", reason="only a Windows job can list a tree")
def test_a_copilot_a_tool_started_later_in_the_tree_can_hold_a_chat(launcher, tmp_path):
    """The check is there for a Copilot with the chat open and idle: one a tool
    started is in the job, but it's another Copilot all the same."""
    from thechatplace.providers import copilot_runtime
    launcher.process.stdin.write("\n")
    launcher.process.stdin.flush()
    later = int(launcher.process.stdout.readline())
    assert later in launcher.tree.members()
    is_own = CopilotChatRuntime._is_own(launcher.connection)
    assert not is_own(later)
    (tmp_path / f"inuse.{later}.lock").write_text("")
    assert copilot_runtime.locked_elsewhere(tmp_path, is_own)


def test_a_copilot_outside_the_tree_still_holds_a_chat(launcher, tmp_path, live_other_pid):
    from thechatplace.providers import copilot_runtime
    (tmp_path / f"inuse.{live_other_pid}.lock").write_text("")
    assert copilot_runtime.locked_elsewhere(
        tmp_path, CopilotChatRuntime._is_own(launcher.connection))


def test_a_pid_seen_at_the_start_that_has_left_the_tree_isnt_ours(launcher):
    """Its number can be handed out again."""
    from types import SimpleNamespace
    connection = SimpleNamespace(_process=launcher.connection._process, own_pids=frozenset({999999}))
    assert not CopilotChatRuntime._is_own(connection)(999999)


def test_a_tree_lists_the_one_it_started_and_not_this_process(launcher):
    assert launcher.process.pid in launcher.tree.members()
    assert os.getpid() not in launcher.tree.members()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows jobs only")
def test_a_list_too_small_for_a_job_is_tried_larger(launcher):
    job = launcher.tree._job
    assert platform_paths._job_process_ids(job, rooms=(1,))  == frozenset()
    assert launcher.process.pid in platform_paths._job_process_ids(job, rooms=(1, 1024))


LAUNCHER_PATH = os.path.join(os.sep, "Code", "bin", "copilot-shim", "copilot.exe")
REAL_PATH = os.path.join(os.sep, "tools", "copilot.exe")


def test_a_real_copilot_is_preferred_to_vs_codes_launcher(monkeypatch):
    monkeypatch.setattr(platform_paths, "_native_codex", lambda path: True)
    assert platform_paths.find_copilot(lambda name: LAUNCHER_PATH, [REAL_PATH]) == REAL_PATH


def test_a_real_copilot_later_on_the_path_is_found_past_the_launcher(monkeypatch):
    import shutil
    monkeypatch.setattr(platform_paths, "_native_codex", lambda path: True)
    monkeypatch.setattr(platform_paths, "copilot_native_candidates", lambda: [])
    folder = os.path.dirname(LAUNCHER_PATH)
    monkeypatch.setenv("PATH", os.pathsep.join([folder, "elsewhere"]))
    seen = []

    def which(name, path=None):
        seen.append(path)
        return LAUNCHER_PATH if path is None else REAL_PATH

    monkeypatch.setattr(shutil, "which", which)
    assert platform_paths.find_copilot() == REAL_PATH
    assert seen == [None, "elsewhere"]


def test_the_launcher_is_used_if_theres_nothing_else(monkeypatch):
    monkeypatch.setattr(platform_paths, "_native_codex", lambda path: path == LAUNCHER_PATH)
    assert platform_paths.find_copilot(lambda name: LAUNCHER_PATH, [REAL_PATH]) == LAUNCHER_PATH


def test_a_real_copilot_first_on_the_path_stays_first(monkeypatch):
    monkeypatch.setattr(platform_paths, "_native_codex", lambda path: True)
    assert platform_paths.find_copilot(lambda name: REAL_PATH, [LAUNCHER_PATH]) == REAL_PATH
