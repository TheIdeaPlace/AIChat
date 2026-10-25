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
                                                    CopilotChatRuntime, CopilotRequest,
                                                    usage_kinds)
from thechatplace.sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, WORKING

FAKE = Path(__file__).with_name("fake_copilot_acp.py")


@pytest.fixture(autouse=True)
def own_copilot_home(tmp_path, monkeypatch):
    """Hooks are looked for in a Copilot folder of the test's own, never the
    machine's, and no organisation policy applies."""
    monkeypatch.setattr(platform_paths, "copilot_home", lambda: tmp_path / "copilot-home")
    monkeypatch.setattr(platform_paths, "copilot_policy_texts", lambda: [])


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
