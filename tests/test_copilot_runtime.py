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
from thechatplace.providers.copilot_runtime import (ALLOW_ONCE, COMMAND, REJECT,
                                                    CopilotChatError, CopilotChatRuntime,
                                                    CopilotRequest, usage_kinds)
from thechatplace.sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, WORKING

FAKE = Path(__file__).with_name("fake_copilot_acp.py")


class Process:
    """What platform_paths.start_copilot_acp returns: pipes and close()."""

    def __init__(self, mode, env):
        self.process = subprocess.Popen([sys.executable, "-u", str(FAKE), mode],
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
    assert wait(lambda: len(runtime.snapshot(chat).replies) == 2
                and runtime.snapshot(chat).outcome == "completed"
                and not runtime.snapshot(chat).queued)
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


def test_stop_cancels_and_gives_back_waiting_messages_unsent(make, tmp_path):
    runtime, _ = make("hang")
    chat = runtime.create(str(tmp_path))
    runtime.send(chat, "Long job")
    runtime.send(chat, "Then this")
    assert wait(lambda: runtime.snapshot(chat).replies)
    assert runtime.stop(chat) == ("Then this",)
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
    write_workspace(tmp_path, native, "github/acp")
    # Any ACP client's chat says github/acp; it must also be one the window
    # says The Chat Place owns.
    with pytest.raises(CopilotChatError, match="started outside The Chat Place"):
        runtime.prepare(native, str(tmp_path))
    runtime.adopt(native)
    runtime.prepare(native, str(tmp_path))
    state = runtime.snapshot(native)
    assert state.replies == () and runtime.ready(state.native_id)


@pytest.mark.parametrize("client", ["github/cli", ""])
def test_a_chat_started_elsewhere_stays_read_only(make, tmp_path, client):
    runtime, _ = make()
    native = "66666666-7777-8888-9999-000000000000"
    if client:
        write_workspace(tmp_path, native, client)
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
    assert runtime.stop(chat, hold=True) == ()  # kept, not returned
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
    runtime._chats[chat].connection = None  # opened again
    runtime.prepare(chat, str(tmp_path))
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
