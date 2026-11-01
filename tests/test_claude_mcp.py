"""Claude's connected tools (#468): reading mcp_status, saying it in words,
turning a tool off for a chat, and MCP servers' questions (elicitation).

The shapes come from real captures with Claude Code 2.1.296 (an idle
``claude -p`` asked ``mcp_status``, and a Haiku turn calling a small stdio
server's tool that asked for a form and a page); the names here are made up.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from thechatplace import claude_cli, claude_mcp
from thechatplace.claude_cli import StreamParser, TurnRunner, child_environment
from thechatplace.claude_mcp import (ElicitationRequest, McpError, McpServer, McpSession,
                                     elicitation_from, elicitation_response, form_content,
                                     form_fields, parse_servers, problems_text, settings_args,
                                     with_chat_settings)
from thechatplace.own_store import OwnSession, OwnSessionStore, _session_from_dict

#: An mcp_status answer as Claude Code 2.1.296 gives it, names made up.
STATUS = {"mcpServers": [
    {"name": "plugin:probe-tools:notes", "status": "needs-auth", "scope": "dynamic",
     "source": "plugin", "config": {"type": "http", "url": "https://example.com/mcp"}},
    {"name": "claude.ai Docs", "status": "connected", "scope": "claudeai", "source": "claudeai",
     "config": {"type": "claudeai-proxy", "url": "https://example.com", "id": "x"},
     "serverInfo": {"name": "docs", "version": "1"},
     "tools": [{"name": "search"}, {"name": "fetch"}, {"name": "sample"}]},
    {"name": "claude.ai Travel", "status": "pending", "scope": "claudeai", "source": "claudeai"},
    {"name": "github", "status": "failed", "scope": "user", "source": "user",
     "error": "Connection   closed\nby peer"},
    {"name": "ccd_session_mgmt", "status": "connected", "scope": "dynamic", "source": "dynamic",
     "tools": [{"name": "list_sessions"}]},
    {"name": "", "status": "connected"},
    "not a server",
]}


# -- reading the status --------------------------------------------------------------------

def test_parse_servers_keeps_names_statuses_sources_tools_and_errors():
    servers = parse_servers(STATUS)
    assert [s.name for s in servers] == ["plugin:probe-tools:notes", "claude.ai Docs",
                                        "claude.ai Travel", "github", "ccd_session_mgmt"]
    docs = servers[1]
    assert docs.status == "connected" and docs.source == "claudeai"
    assert docs.tools == ["search", "fetch", "sample"]
    assert servers[3].error == "Connection closed by peer"  # one line


@pytest.mark.parametrize("body", [None, [], {}, {"mcpServers": "x"}, {"mcpServers": None}])
def test_parse_servers_survives_odd_answers(body):
    assert parse_servers(body) == []


def test_each_row_says_the_status_and_where_it_came_from_in_words():
    texts = [s.text() for s in parse_servers(STATUS)]
    assert texts == [
        "notes (from the probe-tools plugin): needs sign-in, a plugin",
        "claude.ai Docs: connected, claude.ai connector, 3 tools",
        "claude.ai Travel: still connecting, claude.ai connector",
        "github: failed: Connection closed by peer, your settings",
        "The Chat Place's session tools: connected, The Chat Place, 1 tool",
    ]


@pytest.mark.parametrize("source,scope,words", [
    ("claudeai", "claudeai", "claude.ai connector"), ("plugin", "dynamic", "a plugin"),
    ("user", "user", "your settings"), ("project", "project", "this project"),
    ("local", "local", "your settings for this folder"), ("", "managed", "your organization"),
    ("dynamic", "dynamic", "added for this run"), ("", "", "Claude Code"),
    ("something-new", "", "something-new")])
def test_source_words(source, scope, words):
    assert claude_mcp.source_words(source, scope) == words


def test_disabled_status_in_words():
    assert McpServer("x", "disabled").text() == "x: turned off in Claude Code, Claude Code"


def test_with_chat_settings_marks_turned_off_tools_and_keeps_missing_ones():
    rows = with_chat_settings(parse_servers(STATUS), ["github", "gone-server", "\x00bad"])
    texts = [r.text() for r in rows]
    assert "github: off for this chat, your settings" in texts
    assert "gone-server: off for this chat, no longer set up in Claude Code" in texts
    assert not any("bad" in t for t in texts)
    # The Chat Place's own tools come last.
    assert rows[-1].is_session_tools


# -- turning a tool off for a chat ------------------------------------------------------------

def test_settings_args_deny_the_chats_turned_off_tools():
    args = settings_args(["claude.ai Travel", "plugin:probe-tools:notes", "claude.ai Travel"])
    assert args[0] == "--settings"
    assert json.loads(args[1]) == {"deniedMcpServers": [
        {"serverName": "claude.ai Travel"}, {"serverName": "plugin:probe-tools:notes"}]}


def test_settings_args_never_turn_off_the_session_tools_or_bad_names():
    assert settings_args([]) == []
    assert settings_args(["ccd_session_mgmt", "", "a\nb", 7, "x" * 201]) == []


def test_turn_runner_puts_settings_before_mcp_config(tmp_path):
    """--mcp-config takes several values, so it stays last; --settings goes
    just before it, after the turn's own command (which ends --resume <id>)."""
    seen = {}

    class Stop(Exception):
        pass

    def popen(command, **kwargs):
        seen["command"] = command
        raise Stop()
    runner = TurnRunner(["claude", "-p", "--resume", "abc"], str(tmp_path), "hi", lambda e: None,
                        popen=popen, mcp_config='{"mcpServers":{}}',
                        disabled_mcp=["claude.ai Travel"])
    runner._run()
    command = seen["command"]
    assert command[:4] == ["claude", "-p", "--resume", "abc"]
    assert command[4:6] == settings_args(["claude.ai Travel"])
    assert command[6:] == ["--mcp-config", '{"mcpServers":{}}']
    assert runner.command == ["claude", "-p", "--resume", "abc"]  # its own, untouched


def test_turn_runner_without_turned_off_tools_adds_no_settings(tmp_path):
    seen = {}

    def popen(command, **kwargs):
        seen["command"] = command
        raise OSError("stop")
    TurnRunner(["claude", "-p"], str(tmp_path), "hi", lambda e: None, popen=popen)._run()
    assert "--settings" not in seen["command"]


def test_a_chat_keeps_its_turned_off_tools(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("own-1", "Chat", str(tmp_path)))
    store.update("own-1", disabled_mcp=["claude.ai Travel", "github"])
    again = OwnSessionStore(tmp_path / "sessions.json")
    assert again.get("own-1").disabled_mcp == ["claude.ai Travel", "github"]
    saved = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1  # still the file older releases read
    assert saved["sessions"][0]["disabled_mcp"] == ["claude.ai Travel", "github"]


def test_an_older_store_without_the_field_loads_with_none_turned_off(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"version": 1, "sessions": [
        {"cli_session_id": "own-1", "title": "Old", "cwd": str(tmp_path)}]}), encoding="utf-8")
    assert OwnSessionStore(path).get("own-1").disabled_mcp == []


def test_bad_turned_off_names_are_dropped_on_load():
    session = _session_from_dict({"cli_session_id": "own-1", "title": "T", "cwd": "C:\\x",
                                  "disabled_mcp": ["ok", "", "a\nb", 3, "ok"]})
    assert session.disabled_mcp == ["ok"]
    session = _session_from_dict({"cli_session_id": "own-1", "title": "T", "cwd": "C:\\x",
                                  "disabled_mcp": "github"})
    assert session.disabled_mcp == []


def test_another_providers_record_never_carries_turned_off_tools():
    session = _session_from_dict({"cli_session_id": "c-1", "title": "T", "cwd": "C:\\x",
                                  "provider": "codex", "disabled_mcp": ["github"]})
    assert session.disabled_mcp == []


def test_a_bad_turned_off_name_is_refused_on_update(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("own-1", "Chat", str(tmp_path)))
    with pytest.raises(ValueError):
        store.update("own-1", disabled_mcp=["bad\nname"])


# -- a turn's start ---------------------------------------------------------------------------

def test_init_lists_the_turns_servers():
    parser = StreamParser()
    events = parser.feed(json.dumps({
        "type": "system", "subtype": "init", "session_id": "s", "apiKeySource": "none",
        "mcp_servers": [{"name": "claude.ai Docs", "status": "connected", "source": "claudeai"},
                        {"name": "claude.ai Travel", "status": "needs-auth"}]}))
    assert events[0].kind == "started"
    assert claude_mcp.init_servers(events[0].data["mcp_servers"]) == [
        ("claude.ai Docs", "connected"), ("claude.ai Travel", "needs-auth")]


def test_problems_text_says_what_claude_cant_use():
    servers = [("claude.ai Docs", "connected"), ("claude.ai Travel", "needs-auth"),
               ("claude.ai Shop", "needs-auth"), ("github", "failed"), ("x", "pending")]
    assert problems_text(servers) == (
        "Claude can't use claude.ai Travel and claude.ai Shop: they need sign-in. "
        "Claude can't use github: it failed to connect. View, Connected Tools.")
    assert problems_text(servers, skip={"claude.ai Shop", "github"}) == (
        "Claude can't use claude.ai Travel: it needs sign-in. View, Connected Tools.")
    assert problems_text([("claude.ai Docs", "connected"), ("x", "pending")]) == ""
    assert problems_text(servers, skip={"claude.ai Travel", "claude.ai Shop", "github"}) == ""


def test_init_servers_survives_odd_input():
    assert claude_mcp.init_servers(None) == []
    assert claude_mcp.init_servers([{"status": "failed"}, "x", {"name": "a"}]) == [("a", "")]


# -- the idle Claude Code ----------------------------------------------------------------------

FAKE_IDLE = r'''
import json, sys
calls = 0
for raw in iter(sys.stdin.buffer.readline, b""):
    message = json.loads(raw)
    request = message.get("request", {})
    rid = message.get("request_id")
    kind = request.get("subtype")
    def answer(body=None, error=None):
        response = ({"subtype": "error", "request_id": rid, "error": error} if error
                    else {"subtype": "success", "request_id": rid, "response": body or {}})
        sys.stdout.write(json.dumps({"type": "control_response", "response": response}) + "\n")
        sys.stdout.flush()
    if kind == "initialize":
        answer({"commands": []})
    elif kind == "mcp_status":
        calls += 1
        status = "pending" if calls < 3 else "connected"
        answer({"mcpServers": [{"name": "claude.ai Docs", "status": status,
                                "source": "claudeai"}]})
    elif kind == "mcp_authenticate":
        if request["serverName"] == "own-oauth":
            answer({"authUrl": "https://auth.example.com/authorize?x=1",
                    "requiresUserAction": True, "callbackExpected": True,
                    "callbackPort": 4000})
        elif request["serverName"] == "done":
            answer({"requiresUserAction": False, "callbackExpected": False})
        elif request["serverName"] == "file":
            answer({"authUrl": "file:///C:/Windows/notepad.exe", "requiresUserAction": True})
        else:
            answer({"authUrl": "https://claude.ai/api/organizations/o/mcp/start-auth/s",
                    "requiresUserAction": True, "callbackExpected": False})
    elif kind == "mcp_reconnect":
        if request["serverName"] == "nope":
            answer(error="Server not found: nope")
        else:
            answer({})
    else:
        answer(error="Unsupported control request subtype: " + str(kind))
'''


@pytest.fixture
def idle(tmp_path, monkeypatch):
    script = tmp_path / "fake_idle.py"
    script.write_text(FAKE_IDLE, encoding="utf-8")
    seen = {}

    def popen(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.Popen([sys.executable, str(script)], **kwargs)
    session = McpSession("claude.exe", str(tmp_path), popen=popen)
    session.seen = seen
    yield session
    session.close()


def test_session_runs_claude_idle_with_no_prompts(idle):
    command = idle.seen["command"]
    assert command == ["claude.exe", "-p", "--input-format", "stream-json", "--output-format",
                       "stream-json", "--verbose", "--permission-prompts", "none"]
    assert "--bare" not in command
    env = idle.seen["kwargs"]["env"]
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env


def test_status_asks_again_while_servers_are_connecting(idle):
    servers = idle.status(poll=0.01)
    assert [(s.name, s.status) for s in servers] == [("claude.ai Docs", "connected")]


def test_status_gives_up_waiting_and_says_still_connecting(idle):
    servers = idle.status(wait=0)
    assert servers[0].status == "pending"
    assert servers[0].text() == "claude.ai Docs: still connecting, claude.ai connector"


def test_sign_in_for_a_connector_and_for_a_servers_own_oauth(idle):
    connector = idle.sign_in("claude.ai Docs")
    assert connector.url.startswith("https://claude.ai/") and not connector.callback_expected
    own = idle.sign_in("own-oauth")
    assert own.url.startswith("https://auth.example.com/") and own.callback_expected
    assert idle.sign_in("done").url == ""


def test_sign_in_never_hands_over_anything_but_a_web_page(idle):
    with pytest.raises(McpError):
        idle.sign_in("file")


def test_reconnect_and_its_refusal(idle):
    idle.reconnect("claude.ai Docs")
    with pytest.raises(McpError, match="Server not found: nope"):
        idle.reconnect("nope")


def test_an_older_claude_code_is_told_to_update(idle):
    with pytest.raises(McpError, match="claude update"):
        idle.request("mcp_something_new")


def test_after_close_nothing_more_is_asked(idle):
    idle.close()
    with pytest.raises(McpError):
        idle.request("mcp_status")


def test_a_session_needs_a_folder():
    with pytest.raises(McpError):
        McpSession("claude.exe", "")


def test_a_claude_that_cant_start_says_so(tmp_path):
    def popen(command, **kwargs):
        raise OSError("not found")
    with pytest.raises(McpError, match="Couldn't start Claude Code"):
        McpSession("claude.exe", str(tmp_path), popen=popen)


def test_a_claude_that_exits_at_once_ends_the_wait(tmp_path):
    def popen(command, **kwargs):
        return subprocess.Popen([sys.executable, "-c", "pass"], **kwargs)
    session = McpSession("claude.exe", str(tmp_path), popen=popen)
    try:
        with pytest.raises(McpError):
            session.request("mcp_status", timeout=10)
    finally:
        session.close()


@pytest.mark.parametrize("url,ok", [
    ("https://claude.ai/x", True), ("http://localhost:3000/cb", True),
    ("file:///C:/x.exe", False), ("javascript:alert(1)", False), ("claude://x", False),
    ("https://a b", False), ("", False), (None, False)])
def test_is_web_url(url, ok):
    assert claude_mcp.is_web_url(url) is ok


# -- elicitation --------------------------------------------------------------------------------

#: The form request exactly as Claude Code 2.1.296 sent it (captured).
FORM_REQUEST = {
    "subtype": "elicitation", "mcp_server_name": "elicit-test",
    "message": "Where would you like to go, and for how long?", "mode": "form",
    "requested_schema": {"type": "object", "properties": {
        "city": {"type": "string", "title": "City", "description": "Which city to plan for"},
        "nights": {"type": "integer", "title": "Nights", "minimum": 1, "maximum": 14},
        "pets": {"type": "boolean", "title": "Bringing pets", "default": False},
        "budget": {"type": "string", "title": "Budget", "enum": ["low", "medium", "high"],
                   "enumNames": ["Low", "Medium", "High"]}},
        "required": ["city", "nights"]}}

URL_REQUEST = {"subtype": "elicitation", "mcp_server_name": "elicit-test",
               "message": "Open this page to finish signing in.", "mode": "url",
               "url": "https://example.com/sign-in", "elicitation_id": "elic-123"}


def test_parser_turns_an_elicitation_into_a_request_to_answer():
    parser = StreamParser()
    events = parser.feed(json.dumps({"type": "control_request", "request_id": "e1",
                                     "request": FORM_REQUEST}))
    assert [e.kind for e in events] == ["permission"]
    request = events[0].request
    assert isinstance(request, ElicitationRequest)
    assert request.request_id == "e1" and request.server == "elicit-test"
    assert not request.is_question and not request.is_plan
    assert events[0].text == "elicit-test asks: Where would you like to go, and for how long?"
    assert parser.unsupported_requests == []  # answered, not refused


def test_url_elicitation_summary_and_detail():
    request = elicitation_from("e2", URL_REQUEST)
    assert request.is_url and request.elicitation_id == "elic-123"
    assert request.summary() == "elicit-test asks you to open a web page"
    assert "Page: https://example.com/sign-in" in request.detail()
    assert request.fields() == []


def test_elicitation_from_odd_input():
    request = elicitation_from("e3", {"subtype": "elicitation", "mode": "something",
                                      "requested_schema": "x", "url": 5})
    assert request.mode == "form" and request.schema == {} and request.url == ""
    assert request.summary() == "A connected tool asks you for information"


def test_plugin_server_is_named_readably():
    request = elicitation_from("e4", {**URL_REQUEST, "mcp_server_name": "plugin:tools:notes"})
    assert request.summary() == "notes (from the tools plugin) asks you to open a web page"


def test_form_fields_from_the_schema():
    fields = form_fields(FORM_REQUEST["requested_schema"])
    assert [(f.key, f.kind, f.label, f.required) for f in fields] == [
        ("city", "text", "City", True), ("nights", "integer", "Nights", True),
        ("pets", "boolean", "Bringing pets", False), ("budget", "choice", "Budget", False)]
    assert fields[0].name() == "City, required"
    assert fields[0].description == "Which city to plan for"
    assert fields[1].minimum == 1 and fields[1].maximum == 14
    assert fields[3].choices == [("low", "Low"), ("medium", "Medium"), ("high", "High")]


def test_form_fields_other_shapes():
    fields = form_fields({"type": "object", "properties": {
        "size": {"type": "string", "oneOf": [{"const": "s", "title": "Small"},
                                             {"const": "l", "title": "Large"}]},
        "tags": {"type": "array", "items": {"enum": ["a", "b"]}, "default": ["a"]},
        "ratio": {"type": "number", "minimum": 0.5},
        "email": {"type": "string", "format": "email", "maxLength": 50},
        "weird": "not a property"}})
    assert [(f.key, f.kind) for f in fields] == [("size", "choice"), ("tags", "multi"),
                                                 ("ratio", "number"), ("email", "text")]
    assert fields[0].choices == [("s", "Small"), ("l", "Large")]
    assert fields[1].choices == [("a", "a"), ("b", "b")] and fields[1].default == ["a"]
    assert form_fields(None) == [] and form_fields({"properties": []}) == []


def test_form_content_accepts_good_answers():
    fields = form_fields(FORM_REQUEST["requested_schema"])
    content, problem = form_content(fields, {"city": " Seattle ", "nights": "3", "pets": True,
                                             "budget": "medium"})
    assert problem == ""
    # The answer that Claude Code accepted in the capture.
    assert content == {"city": "Seattle", "nights": 3, "pets": True, "budget": "medium"}


def test_form_content_leaves_out_unanswered_optional_ones():
    fields = form_fields(FORM_REQUEST["requested_schema"])
    content, problem = form_content(fields, {"city": "Oslo", "nights": "2", "pets": False,
                                             "budget": None})
    assert problem == "" and content == {"city": "Oslo", "nights": 2, "pets": False}


@pytest.mark.parametrize("values,problem,key", [
    ({"city": "", "nights": "3"}, "City is required.", "city"),
    ({"city": "Oslo", "nights": "three"}, "Nights must be a whole number.", "nights"),
    ({"city": "Oslo", "nights": "2.5"}, "Nights must be a whole number.", "nights"),
    ({"city": "Oslo", "nights": "40"}, "Nights must be between 1 and 14.", "nights"),
    ({"city": "Oslo", "nights": "0"}, "Nights must be between 1 and 14.", "nights")])
def test_form_content_names_what_is_wrong(values, problem, key):
    fields = form_fields(FORM_REQUEST["requested_schema"])
    assert form_content(fields, values, with_field=True) == ({}, problem, key)


def test_form_content_other_checks():
    fields = form_fields({"type": "object", "required": ["size", "tags"], "properties": {
        "size": {"type": "string", "enum": ["s", "l"]},
        "tags": {"type": "array", "items": {"enum": ["a", "b"]}},
        "ratio": {"type": "number", "minimum": 0.5},
        "email": {"type": "string", "format": "email", "minLength": 3, "maxLength": 10}}})
    good = {"size": "s", "tags": ["a", "zzz"], "ratio": "0.75", "email": "a@b.c"}
    assert form_content(fields, good) == (
        {"size": "s", "tags": ["a"], "ratio": 0.75, "email": "a@b.c"}, "")
    assert form_content(fields, {**good, "size": None})[1] == "Choose one for size."
    assert form_content(fields, {**good, "tags": []})[1] == "Choose at least one for tags."
    assert form_content(fields, {**good, "ratio": "0.1"})[1] == "ratio must be at least 0.5."
    assert form_content(fields, {**good, "ratio": "nan"})[1] == "ratio must be a number."
    assert form_content(fields, {**good, "email": "nope"})[1] == "email must be an email address."
    assert form_content(fields, {**good, "email": "a@"})[1] == \
        "email must be at least 3 characters."
    assert form_content(fields, {**good, "email": "abcdef@ghijk"})[1] == \
        "email must be at most 10 characters."


def test_elicitation_responses():
    assert elicitation_response("accept", {"city": "Oslo"}) == {
        "action": "accept", "content": {"city": "Oslo"}}
    assert elicitation_response("accept") == {"action": "accept"}  # a page opened
    assert elicitation_response("decline", {"x": 1}) == {"action": "decline"}
    assert elicitation_response("cancel") == {"action": "cancel"}
    with pytest.raises(ValueError):
        elicitation_response("allow")


FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


@pytest.mark.parametrize("mode,answer", [
    ("elicit", elicitation_response("accept", {"city": "Seattle", "nights": 3})),
    ("elicit", elicitation_response("decline")),
    ("elicit-url", elicitation_response("accept"))])
def test_real_child_process_elicitation_answered(tmp_path, mode, answer):
    log = tmp_path / "fake.log"
    env = child_environment({**child_environment(), "FAKE_CLAUDE_LOG": str(log),
                             "FAKE_CLAUDE_MODE": mode})
    events = []
    done = threading.Event()
    holder = {}

    def on_event(event):
        events.append(event)
        if event.kind == "permission":
            threading.Thread(target=lambda: holder["runner"].respond(
                event.request.request_id, answer)).start()
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner([sys.executable, str(FAKE_CLAUDE)], str(tmp_path), "plan it",
                        on_event, env=env, disabled_mcp=["claude.ai Travel"])
    holder["runner"] = runner
    runner.start()
    assert done.wait(30)
    runner.join(10)
    assert [e.kind for e in events] == ["started", "permission", "text", "finished"]
    assert isinstance(events[1].request, ElicitationRequest)
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen["answer"] == {"type": "control_response", "response": {
        "subtype": "success", "request_id": "elicit-1", "response": answer}}
    assert json.loads(events[-1].text) == answer
    # The chat's turned-off tool reached the command line.
    assert seen["args"][-2:] == settings_args(["claude.ai Travel"])
