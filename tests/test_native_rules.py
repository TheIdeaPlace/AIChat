"""The safety rules of the native routes and the plain chat adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness import FakeModelServer, Run, claude, codex
from thechatplace.contracts import (AskPermission, ChatRef, Decision, IntegrationError, OwnedChat, Refused, TurnStatus)
from thechatplace.integrations import claude as claude_module
from thechatplace.integrations.claude import (api_key_problem, build_command, check_can_send, child_environment)
from thechatplace.integrations.openai_chat import OpenAIChatIntegration, check_local_url
from thechatplace import platform_paths


@pytest.fixture
def setup(tmp_path):
    runs = []
    folder = tmp_path / "work"
    folder.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    def make(integration):
        run = Run(integration, data, folder)
        runs.append(run)
        return run

    yield make, data
    for run in runs:
        run.close()


def _chat(native="11111111-2222-3333-4444-555555555555", mode="manual", turns=0):
    return OwnedChat(ChatRef("claude", native), "A", "C:/", {"mode": mode}, turns)


@pytest.mark.parametrize("native", ["local_abc", "", "bad id; rm -rf"])
def test_claude_refuses_desktop_and_bad_ids(native):
    with pytest.raises(Refused):
        check_can_send(native, True)


def test_claude_refuses_a_session_it_didnt_start():
    with pytest.raises(Refused):
        check_can_send("abc", False)


def test_claude_commands_never_use_bare_and_resume_only_after_the_first_turn():
    first = build_command("claude", _chat(), True)
    later = build_command("claude", _chat(turns=1), False)
    assert "--bare" not in first + later
    assert "--session-id" in first and "--resume" not in first
    assert "--resume" in later and "--session-id" not in later
    with pytest.raises(Refused):
        build_command("claude", _chat(mode="bypassPermissions"), True)


def test_claude_environment_loses_billing_and_parent_session_variables():
    env = child_environment({"ANTHROPIC_API_KEY": "x", "anthropic_base_url": "y", "CLAUDECODE": "1",
                             "CLAUDE_CODE_SDK_FOO": "1", "CLAUDE_CODE_USE_BEDROCK": "1", "PATH": "keep"})
    assert env == {"PATH": "keep"}


def test_claude_stops_a_turn_reporting_an_api_key(setup, tmp_path):
    make, data = setup
    run = make(claude("apikey", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    ended = run.end_turn()
    assert ended.status is TurnStatus.FAILED and "API key" in ended.message
    run.idle(info.ref)
    assert not any(t.startswith("assistant") for t in run.text(info.ref))
    assert api_key_problem("none") is None and api_key_problem(None) is None


def test_claude_starts_a_session_until_one_really_exists(setup):
    """Counting turns would leave a chat resuming a session that never existed."""
    make, data = setup
    run = make(claude("apikey", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.end_turn()
    run.idle(info.ref)
    run.service.send(info.ref, "Again")
    run.end_turn()
    run.idle(info.ref)
    args = [line for line in (data / "claude.log").read_text(encoding="utf-8").splitlines() if line.startswith("ARGS")]
    assert len(args) == 2 and all("--session-id" in line and "--resume" not in line for line in args)
    folder = data / "projects" / "here"
    folder.mkdir(parents=True)
    (folder / f"{info.ref.native_id}.jsonl").write_text("{}", encoding="utf-8")
    run.service.send(info.ref, "Once more")
    run.end_turn()
    args = [line for line in (data / "claude.log").read_text(encoding="utf-8").splitlines() if line.startswith("ARGS")]
    assert "--resume" in args[-1]


def test_claude_answers_a_question_with_nothing_to_ask(setup):
    make, data = setup
    run = make(claude("emptyquestion", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.COMPLETED
    assert run.service.state(info.ref).pending_permission is None
    assert '"behavior": "deny"' in (data / "claude.log").read_text(encoding="utf-8")


def test_claude_offers_allow_only_with_something_to_read():
    from thechatplace.integrations.claude import _describe_input
    assert _describe_input({}) == ""
    assert "old" in _describe_input({"file_path": "a", "old_string": "old", "new_string": "new"})
    assert "unusual" in _describe_input({"something": "unusual"})


def test_claude_refuses_an_unknown_control_request_and_carries_on(setup):
    make, data = setup
    run = make(claude("badcontrol", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.COMPLETED
    log = (data / "claude.log").read_text(encoding="utf-8")
    assert '"subtype": "error"' in log and '"request_id": "x-1"' in log
    assert run.integration.drift.snapshot()["unknown"] == {"control:new_kind_of_request": 1}


def test_claude_reads_transcripts_skipping_bad_lines_and_never_writes(tmp_path):
    folder = tmp_path / "projects" / "one"
    folder.mkdir(parents=True)
    path = folder / "abc-123.jsonl"
    lines = ['{"type":"user","cwd":"C:/w","message":{"role":"user","content":"Hello"}}', "{not json",
             '{"type":"brand_new","secret":"never stored"}',
             '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"},{"type":"tool_use"}]}}',
             '{"type":"user","isSidechain":true,"message":{"content":"sub agent"}}']
    path.write_text("\n".join(lines), encoding="utf-8")
    before = path.read_bytes()
    integration = claude_module.ClaudeIntegration(projects_dir=lambda: tmp_path / "projects", program="x")
    chats = integration.list_foreign()
    assert [c.title for c in chats] == ["Hello"] and chats[0].folder == "C:/w"
    from thechatplace.contracts import Capability
    assert Capability.SEND not in chats[0].capabilities and not chats[0].owned
    history = integration.read_history(ChatRef("claude", "abc-123"))
    assert [(m.role, m.text) for m in history] == [("user", "Hello"), ("assistant", "Hi")]
    report = integration.drift.snapshot()
    assert report["malformed"] and report["unknown"] == {"transcript:brand_new": 1}
    assert "never stored" not in json.dumps(report)
    assert path.read_bytes() == before


def test_codex_refuses_anything_but_chatgpt_sign_in(setup):
    make, data = setup
    run = make(codex("apikey", data))
    with pytest.raises(Refused, match="ChatGPT"):
        run.start()


def _codex_sent(data):
    return [json.loads(l) for l in (data / "codex.log").read_text(encoding="utf-8").splitlines()]


def test_codex_checks_sign_in_and_pins_its_endpoint_when_resuming_too(setup):
    make, data = setup
    run = make(codex("answer", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.end_turn()
    run.idle(info.ref)
    run.integration.close()
    (data / "codex.log").write_text("", encoding="utf-8")
    run.service.send(info.ref, "Again")
    assert run.end_turn().status is TurnStatus.COMPLETED
    sent = _codex_sent(data)
    methods = [m.get("method") for m in sent]
    assert methods.index("account/read") < methods.index("thread/resume") < methods.index("turn/start")
    config = next(m for m in sent if m.get("method") == "thread/resume")["params"]["config"]
    assert config["forced_login_method"] == "chatgpt" and config["openai_base_url"] is None


def test_codex_ignores_an_earlier_turns_messages(setup):
    make, data = setup
    integration = codex("answer", data)
    run = make(integration)
    from thechatplace.integrations.codex import _Turn
    connection = object()
    told = []
    turn = _Turn(told.append, connection=connection, turn_id="now")
    integration._turns["thread"] = turn
    integration._on_notification(connection, "turn/completed", {"threadId": "thread", "turn": {"id": "before", "status": "completed"}})
    assert not turn.done.is_set()
    integration._on_notification(connection, "item/agentMessage/delta", {"threadId": "thread", "turnId": "before", "itemId": "a", "delta": "old"})
    assert told == []
    integration._on_notification(connection, "turn/completed", {"threadId": "thread", "turn": {"id": "now", "status": "completed"}})
    assert turn.done.is_set()
    integration._turns.clear()


def test_a_failed_codex_read_isnt_an_empty_chat(setup):
    make, data = setup
    integration = codex("answer", data)
    run = make(integration)
    ref = ChatRef("codex", "someone-elses")

    class Broken:
        closed = False

        def request(self, *args, **kwargs):
            raise integration_rpc_error
    from thechatplace.integrations.jsonrpc import RpcError
    integration_rpc_error = RpcError("no")
    integration._connect = lambda: Broken()  # type: ignore[method-assign]
    with pytest.raises(IntegrationError):
        run.service.load_history(ref)
    assert run.service.state(ref).loaded is False


def test_codex_pins_its_endpoint_and_starts_read_only(setup):
    make, data = setup
    run = make(codex("answer", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.end_turn()
    sent = [json.loads(l) for l in (data / "codex.log").read_text(encoding="utf-8").splitlines()]
    start = next(m for m in sent if m.get("method") == "thread/start")["params"]
    assert start["sandbox"] == "read-only" and start["config"]["forced_login_method"] == "chatgpt"
    assert start["config"]["openai_base_url"] is None and start["approvalPolicy"] == "on-request"
    turn = next(m for m in sent if m.get("method") == "turn/start")["params"]
    assert turn["sandboxPolicy"] == {"type": "readOnly"}
    init = next(m for m in sent if m.get("method") == "initialize")["params"]
    assert init["capabilities"]["experimentalApi"] is True


def test_codex_slash_commands_are_refused_with_the_real_reason(setup):
    make, data = setup
    run = make(codex("answer", data))
    info = run.start()
    with pytest.raises(Refused, match="just read it as text"):
        run.service.send(info.ref, "/permissions")


def test_codex_approval_it_cant_fully_show_can_only_be_denied(setup):
    make, data = setup
    run = make(codex("netask", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    request = run.wait(AskPermission).request
    assert request.decisions == (Decision.DENY,)
    # Even a forged Allow comes out as a denial.
    run.service.answer_permission(info.ref, request, Decision.ALLOW_ONCE)
    run.end_turn()
    run.idle(info.ref)
    assert any("Denied" in t for t in run.text(info.ref))


def test_codex_refuses_a_request_kind_it_doesnt_know(setup):
    make, data = setup
    run = make(codex("newrequest", data))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.end_turn()
    run.idle(info.ref)
    sent = [json.loads(l) for l in (data / "codex.log").read_text(encoding="utf-8").splitlines()]
    assert next(m for m in sent if m.get("id") == "x-1")["error"]["code"] == -32601
    assert any("Nothing was allowed" in t for t in run.text(info.ref))


def test_codex_lists_its_threads_read_only_and_the_service_keeps_owned_apart(setup):
    make, data = setup
    run = make(codex("answer", data))
    info = run.start()
    (data / "threads.json").write_text(json.dumps({info.ref.native_id: {"turns": []}, "elsewhere": {"turns": []}}))
    chats, problems = run.service.list_chats()
    assert problems == []
    rows = {c.ref.native_id: c for c in chats}
    assert rows[info.ref.native_id].owned is True
    assert rows["elsewhere"].owned is False
    with pytest.raises(Refused):
        run.service.send(rows["elsewhere"].ref, "Hi")


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://10.0.0.5:1234/v1", "http://127.0.0.1.evil.example/v1",
                                 "file:///etc/passwd"])
def test_plain_chat_refuses_anywhere_but_this_computer(url):
    with pytest.raises(Refused):
        check_local_url(url)


def test_plain_chat_sends_no_key_and_the_whole_conversation(setup):
    make, data = setup
    server = FakeModelServer()
    run = make(OpenAIChatIntegration(id="fakelocal", name="L", base_url=server.url))
    info = run.start(model="m1")
    run.service.send(info.ref, "One")
    run.end_turn()
    run.idle(info.ref)
    run.service.send(info.ref, "Two")
    run.end_turn()
    run.idle(info.ref)
    last = server.requests[-1]
    assert last["auth"] is None
    assert [m["role"] for m in last["body"]["messages"]] == ["user", "assistant", "user"]
    assert last["body"]["model"] == "m1"
    server.close()


def test_plain_chat_follows_no_redirect():
    """A local server mustn't be able to send a chat's text elsewhere."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from thechatplace.integrations.openai_chat import _OPENER
    import urllib.error
    hits = []

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

    class Redirector(Target):
        def do_POST(self):
            # Read first: hanging up on an unread request can abort the client's
            # read before it sees the redirect.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/stolen")
            self.end_headers()
    target = HTTPServer(("127.0.0.1", 0), Target)
    redirector = HTTPServer(("127.0.0.1", 0), Redirector)
    for server in (target, redirector):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    import urllib.request
    request = urllib.request.Request(f"http://127.0.0.1:{redirector.server_address[1]}/v1", data=b"secret")
    with pytest.raises(urllib.error.HTTPError):
        _OPENER.open(request, timeout=5)
    assert hits == []
    for server in (target, redirector):
        server.shutdown()
        server.server_close()


def test_closing_a_program_that_stopped_reading_doesnt_hang(tmp_path):
    """Writing to a full pipe holds stdin's lock; close must kill it first."""
    import sys
    import threading
    import time
    process = platform_paths.OwnedProcess([sys.executable, "-c", "import time; time.sleep(60)"])

    def writer():
        try:
            process.stdin.write(b"x" * 4_000_000)
            process.stdin.flush()
        except (OSError, ValueError):
            pass
    threading.Thread(target=writer, daemon=True).start()
    time.sleep(0.5)
    closing = threading.Thread(target=process.close, daemon=True)
    closing.start()
    closing.join(8)
    assert not closing.is_alive(), "close hung"
    assert process.process.poll() is not None


def test_a_late_stop_doesnt_end_the_next_plain_chat_turn():
    integration = OpenAIChatIntegration(id="l", name="L", base_url="http://127.0.0.1:9/v1")
    integration.stop(ChatRef("l", "chat"))
    assert integration._stopping == set()


def test_plain_chat_needs_a_model(setup):
    make, data = setup
    server = FakeModelServer()
    run = make(OpenAIChatIntegration(id="fakelocal", name="L", base_url=server.url))
    with pytest.raises(Refused, match="model"):
        run.start()
    server.close()


def test_plain_chat_status_when_no_server_answers():
    integration = OpenAIChatIntegration(base_url="http://127.0.0.1:9/v1")
    assert integration.status().installed is False


def test_a_script_launcher_is_never_started(tmp_path):
    with pytest.raises(OSError):
        platform_paths.OwnedProcess(["C:/x/claude.cmd"])
    assert platform_paths.is_script_launcher("thing.BAT")


def test_finding_a_program_skips_a_launcher_with_no_real_program(tmp_path, monkeypatch):
    launcher = tmp_path / "tool.cmd"
    launcher.write_text("")
    monkeypatch.setattr(platform_paths.shutil, "which", lambda name: str(launcher))
    assert platform_paths.find_program("tool") is None
    assert platform_paths.find_program("tool", "@scope/tool") is None


def test_models_that_run_here_come_first(tmp_path):
    from thechatplace.integrations import openai_chat
    assert openai_chat._forwarded("kimi:cloud") and openai_chat._forwarded("gemma-cloud")
    assert not openai_chat._forwarded("moondream:latest")
    integration = OpenAIChatIntegration(id="l", name="L", base_url="http://127.0.0.1:9/v1")

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return json.dumps({"data": [{"id": "far:cloud"}, {"id": "near"}]}).encode()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(openai_chat._OPENER, "open", lambda *args, **kwargs: Answer())
    group = integration.new_chat_options()[0]
    monkey.undo()
    assert group.current == "near" and [v.id for v in group.values] == ["near", "far:cloud"]
    assert group.values[1].description == openai_chat.FORWARDED_NOTE
