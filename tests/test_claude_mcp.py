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
    assert "gone-server: off for this chat" in texts
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
        "Claude can't use github: it failed to connect. "
        "To fix this, or stop hearing it, use View, Connected Tools.")
    assert problems_text(servers, skip={"claude.ai Shop", "github"}) == (
        "Claude can't use claude.ai Travel: it needs sign-in. "
        "To sign in, or stop hearing this, use View, Connected Tools.")
    assert problems_text([("github", "failed")]) == (
        "Claude can't use github: it failed to connect. "
        "To reconnect, or stop hearing this, use View, Connected Tools.")
    assert problems_text([("claude.ai Docs", "connected"), ("x", "pending")]) == ""
    assert problems_text(servers, skip={"claude.ai Travel", "claude.ai Shop", "github"}) == ""


def test_a_tool_off_for_new_chats_says_so_and_is_always_listed():
    listed = [McpServer("claude.ai Shop", "needs-auth", source="claudeai"),
              McpServer("github", "connected", source="user", tools=["a", "b"])]
    rows = with_chat_settings(listed, ["gone-here-too"],
                              ["claude.ai Shop", "elsewhere", "gone-here-too"])
    assert [r.text() for r in rows] == [
        "claude.ai Shop: needs sign-in, claude.ai connector, off for new chats",
        "github: connected, your settings, 2 tools",
        "gone-here-too: off for this chat, off for new chats",
        # Not in this folder: still listed, as this is the way to turn it back on.
        "elsewhere: off for new chats",
    ]
    # Nothing in new chats' list: rows read as they always did.
    assert with_chat_settings([McpServer("github", "connected", source="user")], []
                              )[0].text() == "github: connected, your settings"


def test_the_tools_off_for_new_chats_are_saved_and_checked(tmp_path):
    from thechatplace.speech import SpeechSettings
    path = tmp_path / "speech.json"
    assert SpeechSettings.load(path).mcp_off_for_new_chats == []
    SpeechSettings(mcp_off_for_new_chats=["claude.ai Shop", "github"]).save(path)
    assert SpeechSettings.load(path).mcp_off_for_new_chats == ["claude.ai Shop", "github"]
    for bad in ("github", 3, None, {"a": 1}):
        path.write_text(json.dumps({"mcp_off_for_new_chats": bad}), encoding="utf-8")
        assert SpeechSettings.load(path).mcp_off_for_new_chats == []
    path.write_text(json.dumps({"mcp_off_for_new_chats": ["ok", 3, "", "ok", "bad\nname"]}),
                    encoding="utf-8")
    assert SpeechSettings.load(path).mcp_off_for_new_chats == ["ok"]


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
    assert not idle.closed
    idle.close()
    assert idle.closed
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


def test_a_process_that_dies_by_itself_counts_as_closed_and_close_still_tears_it_down(tmp_path):
    """Every action used to fail until the 15-minute timer, as closed stayed
    False once the process had gone (#468)."""
    def popen(command, **kwargs):
        return subprocess.Popen([sys.executable, "-c", "pass"], **kwargs)
    session = McpSession("claude.exe", str(tmp_path), popen=popen)
    session._process.wait(timeout=30)
    assert session.closed
    with pytest.raises(McpError, match="stopped"):
        session.request("mcp_status")
    ended = []
    real = session._tree.close
    session._tree.close = lambda: (ended.append(True), real())
    session.close()
    assert ended == [True]  # the job, and any server left in it
    assert not session._timer.is_alive() or session._timer.finished.is_set()


def test_the_idle_process_is_started_as_the_chats_turns_are(tmp_path):
    """The chat's turned-off tools aren't started, and its session tools are
    there to be listed (#468)."""
    seen = {}

    def popen(command, **kwargs):
        seen["command"] = command
        raise OSError("stop")
    with pytest.raises(McpError):
        McpSession("claude.exe", str(tmp_path), popen=popen, disabled=["github", "ccd_session_mgmt"],
                   mcp_config='{"mcpServers":{}}')
    command = seen["command"]
    assert command[-4:-2] == settings_args(["github"])
    # Last, as in a turn: it takes several values.
    assert command[-2:] == ["--mcp-config", '{"mcpServers":{}}']


def test_a_turned_off_tool_reads_as_off_when_claude_code_doesnt_list_it():
    rows = with_chat_settings([McpServer("docs", "connected")], ["github"])
    assert [r.text() for r in rows] == ["docs: connected, Claude Code", "github: off for this chat"]
    assert rows[1].missing and rows[1].off_for_chat


def test_a_sign_in_still_waiting_is_said_to_have_expired(tmp_path):
    script = tmp_path / "fake_idle.py"
    script.write_text(FAKE_IDLE, encoding="utf-8")

    def popen(command, **kwargs):
        return subprocess.Popen([sys.executable, str(script)], **kwargs)
    session = McpSession("claude.exe", str(tmp_path), popen=popen)
    session._lifetime = 0.3  # from when the sign-in starts waiting
    heard = []
    expired = threading.Event()
    session.on_expired = lambda names: (heard.extend(names), expired.set())
    try:
        assert session.sign_in("own-oauth").callback_expected
        assert expired.wait(15)
        assert heard == ["own-oauth"] and session.closed
    finally:
        session.close()
    assert claude_mcp.expired_text(heard) == (
        "The sign-in for own-oauth expired after 15 minutes. If you didn't finish, press Sign In "
        "to start again.")


def test_nothing_is_said_when_no_sign_in_is_waiting_or_it_was_closed_first(tmp_path):
    script = tmp_path / "fake_idle.py"
    script.write_text(FAKE_IDLE, encoding="utf-8")

    def popen(command, **kwargs):
        return subprocess.Popen([sys.executable, str(script)], **kwargs)
    heard = []
    plain = McpSession("claude.exe", str(tmp_path), popen=popen)
    plain.on_expired = heard.append
    plain.sign_in("claude.ai Docs")  # nothing waits on a connector's sign-in
    plain._expire()
    closed_first = McpSession("claude.exe", str(tmp_path), popen=popen)
    closed_first.on_expired = heard.append
    closed_first.sign_in("own-oauth")
    closed_first.close()
    closed_first._expire()
    assert heard == []


def test_a_sign_in_that_finished_isnt_said_to_have_expired(tmp_path):
    script = tmp_path / "fake_idle.py"
    script.write_text(FAKE_IDLE, encoding="utf-8")

    def popen(command, **kwargs):
        return subprocess.Popen([sys.executable, str(script)], **kwargs)
    session = McpSession("claude.exe", str(tmp_path), popen=popen)
    heard = []
    session.on_expired = heard.append
    try:
        session.sign_in("claude.ai Docs")
        session._callbacks.add("claude.ai Docs")
        session.status(wait=5, poll=0.01)  # the fake says connected on its third asking
        session._expire()
    finally:
        session.close()
    assert heard == []


@pytest.mark.parametrize("url,ok", [
    ("https://claude.ai/x", True), ("http://localhost:3000/cb", True),
    ("HTTPS://Claude.AI/x?a=b%20c#d", True), ("http://127.0.0.1:8080/cb", True),
    ("http://[::1]:8080/cb", True), ("https://[2001:db8::1]/x", True),
    ("https://xn--80ak6aa92e.com/", True),  # the look-alike in the form that shows it
    ("https://auth.example.com/authorize?redirect=https://x@y/", True),
    ("file:///C:/x.exe", False), ("javascript:alert(1)", False), ("claude://x", False),
    ("https://a b", False), ("https://x\n", False), ("", False), (None, False), (5, False),
    # A name or password before the host: it goes to the host after the @.
    ("https://good.example@evil.example/", False), ("https://user:pw@evil.example/", False),
    ("https://good.example%40evil.example/", False),
    # Browsers end the host at a backslash.
    ("https://good.example\\@evil.example/", False), ("https://evil.example\\.good.example/", False),
    # Characters that hide what it says.
    ("https://evil.example/\u202egro.doog", False), ("https://evil\u202e.example/", False),
    ("https://evil.example/\x00.good.example", False), ("https://evil.example/\u200b", False),
    ("https://evil.example/\t", False), ("\thttps://evil.example/", False),
    # Plain http only to this computer.
    ("http://x", False), ("http://example.com/", False), ("http://localhost.evil.example/", False),
    ("http://localhost@evil.example/", False),
    # Other alphabets' letters in the host, which can look like a familiar one.
    ("https://\u0430\u0440\u0440\u04cf\u0435.com/", False), ("https://exa\uff4dple.com/", False),
    # No host, or nonsense where it goes.
    ("https://", False), ("https:///x", False), ("https:x", False), ("https://:443/", False),
    ("https://example.com:notaport/", False), ("https://example.com:99999/", False),
    ("https://[nonsense]/", False), ("https://[::1/", False), ("https://exa%6Dple.com/", False),
    ("https://-bad-.example/", False), ("https://a..b/", False),
    # urlsplit takes the brackets as the whole host of these; a browser may not.
    ("http://evil.example[::1]/", False), ("https://bank.example[::1]/login", False),
    ("http://evil.example[::1]:80/path", False), ("http://[::1]evil.example/", False)])
def test_is_web_url(url, ok):
    assert claude_mcp.is_web_url(url) is ok


def test_web_url_host_names_where_it_really_goes():
    assert claude_mcp.web_url_host("https://Accounts.Example.COM:8443/x") == "accounts.example.com"
    assert claude_mcp.web_url_host("https://good.example@evil.example/") == ""


def test_tools_summary_says_the_list_in_a_line():
    rows = parse_servers({"mcpServers": [
        {"name": "a", "status": "connected"}, {"name": "b", "status": "needs-auth"},
        {"name": "c", "status": "connected"}]})
    assert claude_mcp.tools_summary(rows) == "3 connected tools, 1 needs sign-in"
    rows += parse_servers({"mcpServers": [{"name": "d", "status": "needs-auth"},
                                          {"name": "e", "status": "failed"}]})
    rows[0].off_for_chat = True
    assert claude_mcp.tools_summary(rows) == ("5 connected tools, 2 need sign-in, 1 failed, "
                                              "1 off for this chat")
    assert claude_mcp.tools_summary(rows[:1]) == "1 connected tool, 1 off for this chat"
    assert claude_mcp.tools_summary([]) == "No connected tools."


def test_only_the_source_the_app_gives_makes_it_the_chat_places_own():
    """Any server can call itself that (#468)."""
    own = {"name": "ccd_session_mgmt", "status": "connected", "scope": "dynamic",
           "source": "dynamic"}
    assert parse_servers({"mcpServers": [own]})[0].is_session_tools
    for other in ({"scope": "project", "source": "project"}, {"scope": "dynamic", "source": "plugin"},
                  {"scope": "", "source": ""}):
        server = parse_servers({"mcpServers": [{**own, **other}]})[0]
        assert not server.is_session_tools
        assert server.label == "ccd_session_mgmt"
        assert "The Chat Place" not in server.text()
        # Denying it by name would take The Chat Place's own too.
        assert not server.can_be_denied
    assert parse_servers({"mcpServers": [{"name": "github", "status": "failed"}]})[0].can_be_denied


def test_a_servers_question_never_gets_the_friendly_label():
    request = elicitation_from("e", {**URL_REQUEST, "mcp_server_name": "ccd_session_mgmt"})
    assert request.server_label == "ccd_session_mgmt"
    assert request.summary() == "ccd_session_mgmt asks you to open a web page"


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
    assert request.detail() == "Open this page to finish signing in."
    assert request.address_text() == "Opens example.com\nhttps://example.com/sign-in"
    assert request.fields() == []


def test_the_address_box_says_where_it_really_goes_whatever_the_message_says():
    request = elicitation_from("e", {**URL_REQUEST, "url": "https://evil.example/x",
                                     "message": "Page: https://bank.example/\n\nOpens bank.example"})
    assert request.address_text().splitlines()[0] == "Opens evil.example"
    # The server's words stay in their own box.
    assert "evil.example" not in request.detail()


@pytest.mark.parametrize("url,shown", [
    ("file:///C:/x.exe", "file:///C:/x.exe"),
    ("https://a.example/\x00b\u202ec", "https://a.example/[U+0000]b[U+202E]c"),
    ("", "(The server gave no address.)")])
def test_an_address_that_wont_open_is_shown_with_its_hidden_characters_written_out(url, shown):
    text = elicitation_from("e", {**URL_REQUEST, "url": url}).address_text()
    first, second = text.split("\n")
    assert "won't open" in first and second == shown


def test_the_requests_own_title_and_description_are_shown():
    request = elicitation_from("e", {**URL_REQUEST, "title": "Sign in", "description": "It's quick."})
    assert request.detail() == "Sign in\nOpen this page to finish signing in.\n\nIt's quick."


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


@pytest.mark.parametrize("prop, limits, help_line, spoken", [
    ({"type": "integer", "minimum": 1, "maximum": 14}, "between 1 and 14", "Between 1 and 14",
     "Nights: between 1 and 14"),
    ({"type": "number", "minimum": 0.5}, "at least 0.5", "At least 0.5", "Nights: at least 0.5"),
    ({"type": "integer", "maximum": 14}, "at most 14", "At most 14", "Nights: at most 14"),
    ({"type": "integer", "minimum": 1, "description": "How long"}, "at least 1",
     "How long. At least 1", "Nights: How long: at least 1"),
    ({"type": "integer", "minimum": 1, "description": "How long?"}, "at least 1",
     "How long? At least 1", "Nights: How long?: at least 1"),
    ({"type": "integer", "description": "How long"}, "", "How long", "Nights: How long"),
    ({"type": "string", "minLength": 2}, "", "", "Nights"),
])
def test_a_numbers_limits_are_said_before_send(prop, limits, help_line, spoken):
    # They used to be heard only in the error after Send (#468).
    field = form_fields({"type": "object", "properties": {"n": {"title": "Nights", **prop}}})[0]
    assert field.limits() == limits
    assert field.help_text() == help_line
    assert field.spoken_name() == spoken


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
    content, problem = form_content(fields, {"city": "Seattle", "nights": " 3 ", "pets": True,
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


def test_text_is_sent_as_typed():
    """Spaces can be part of a passphrase (#468)."""
    fields = form_fields({"type": "object", "required": ["secret"], "properties": {
        "secret": {"type": "string", "minLength": 4}, "note": {"type": "string"}}})
    assert form_content(fields, {"secret": " a b ", "note": " "}) == ({"secret": " a b "}, "")
    assert form_content(fields, {"secret": "    "})[1] == "secret is required."


@pytest.mark.parametrize("prop,value,sent,problem", [
    ({"format": "date"}, "2026-11-02", "2026-11-02", ""),
    ({"format": "date"}, " 2026-11-02 ", "2026-11-02", ""),
    ({"format": "date"}, "2026-13-02", None, "must be a date"),
    ({"format": "date"}, "20261102", None, "must be a date"),
    ({"format": "date"}, "tomorrow", None, "must be a date"),
    ({"format": "date-time"}, "2026-11-02T14:30:00Z", "2026-11-02T14:30:00Z", ""),
    ({"format": "date-time"}, "2026-11-02T14:30:00.5+01:00", "2026-11-02T14:30:00.5+01:00", ""),
    ({"format": "date-time"}, "2026-11-02", None, "must be a date and time"),
    ({"format": "date-time"}, "2026-11-02T25:00:00Z", None, "must be a date and time"),
    ({"format": "uri"}, "https://example.com/x", "https://example.com/x", ""),
    ({"format": "uri"}, "mailto:a@b.c", "mailto:a@b.c", ""),
    ({"format": "uri"}, "example.com", None, "must be a full address"),
    ({"format": "uri"}, "https://a b", None, "must be a full address")])
def test_formats_are_checked(prop, value, sent, problem):
    fields = form_fields({"properties": {"x": {"type": "string", **prop}}})
    content, said = form_content(fields, {"x": value})
    if problem:
        assert problem in said and content == {}
    else:
        assert said == "" and content == {"x": sent}


def test_a_minimum_says_what_it_is_rather_than_rounding_it():
    fields = form_fields({"properties": {"n": {"type": "integer", "minimum": 1.5}}})
    assert form_content(fields, {"n": "1"})[1] == "n must be at least 1.5."
    assert form_content(fields, {"n": "2"}) == ({"n": 2}, "")


def test_a_number_that_may_be_null_is_a_number():
    fields = form_fields({"properties": {"n": {"type": ["number", "null"], "maximum": 5},
                                         "s": {"type": ["null", "string"]}}})
    assert [(f.key, f.kind) for f in fields] == [("n", "number"), ("s", "text")]
    assert form_content(fields, {"n": "2.5", "s": "x"}) == ({"n": 2.5, "s": "x"}, "")
    assert form_content(fields, {"n": "", "s": ""}) == ({}, "")
    assert claude_mcp.form_problem({"properties": {"n": {"type": ["number", "null"]}}}) == ""


@pytest.mark.parametrize("word", ["anyOf", "oneOf"])
def test_a_type_or_null_is_that_type(word):
    # What the MCP Python SDK makes for Optional[str] and Optional[int].
    schema = {"required": ["must"], "properties": {
        "s": {"title": "Note", word: [{"type": "string", "maxLength": 3}, {"type": "null"}],
              "default": None},
        "n": {word: [{"type": "null"}, {"type": "integer", "minimum": 2}]},
        "must": {word: [{"type": "string"}, {"type": "null"}]}}}
    assert claude_mcp.form_problem(schema) == ""
    fields = form_fields(schema)
    assert [(f.key, f.kind, f.label) for f in fields] == [
        ("s", "text", "Note"), ("n", "integer", "n"), ("must", "text", "must")]
    assert fields[0].max_length == 3 and fields[1].minimum == 2 and fields[2].required
    assert form_content(fields, {"s": "", "n": "", "must": "x"}) == ({"must": "x"}, "")
    assert form_content(fields, {"s": "ab", "n": "4", "must": "x"}) == (
        {"s": "ab", "n": 4, "must": "x"}, "")
    assert "n" in form_content(fields, {"n": "1", "must": "x"})[1]


@pytest.mark.parametrize("word", ["anyOf", "oneOf"])
def test_a_null_option_in_a_choice_list_isnt_a_choice(word):
    schema = {"properties": {
        "pick": {word: [{"const": "a", "title": "A"}, {"const": "b"}, {"type": "null"}]},
        "one": {word: [{"const": "a"}, {"type": "null"}]},
        "literal": {word: [{"enum": ["x", "y"], "type": "string"}, {"type": "null"}]},
        "many": {"type": "array", "items": {word: [{"const": "p"}, {"const": "q"},
                                                   {"type": "null"}]}},
        "maybe_many": {word: [{"type": "array", "items": {"enum": ["r"]}}, {"type": "null"}]}}}
    assert claude_mcp.form_problem(schema) == ""
    fields = {f.key: f for f in form_fields(schema)}
    assert fields["pick"].kind == "choice"
    assert fields["pick"].choices == [("a", "A"), ("b", "b")]
    assert fields["one"].kind == "choice" and fields["one"].choices == [("a", "a")]
    assert fields["literal"].kind == "choice"
    assert [c[0] for c in fields["literal"].choices] == ["x", "y"]
    assert fields["many"].kind == "multi" and [c[0] for c in fields["many"].choices] == ["p", "q"]
    assert fields["maybe_many"].kind == "multi" and fields["maybe_many"].choices == [("r", "r")]


@pytest.mark.parametrize("prop", [
    {"anyOf": [{"type": "null"}]},  # only "nothing": there's no way to answer
    {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
    {"anyOf": [{"type": "object", "properties": {}}, {"type": "null"}]},
    {"oneOf": [{"const": "a"}, {"type": "string"}, {"type": "null"}]}])
def test_a_null_option_doesnt_make_an_unshowable_field_showable(prop):
    assert "can't show" in claude_mcp.form_problem({"properties": {"x": prop}})


def test_how_many_to_choose_is_checked():
    fields = form_fields({"required": ["tags"], "properties": {
        "tags": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"enum": list("abcd")}},
        "more": {"type": "array", "minItems": 2, "items": {"enum": list("xyz")}}}})
    assert fields[0].min_items == 2 and fields[0].max_items == 3
    good = {"tags": ["a", "b"]}
    assert form_content(fields, good) == ({"tags": ["a", "b"]}, "")
    assert form_content(fields, {"tags": []})[1] == "Choose at least 2 for tags."
    assert form_content(fields, {"tags": ["a"]})[1] == "Choose at least 2 for tags."
    assert form_content(fields, {"tags": list("abcd")})[1] == "Choose at most 3 for tags."
    # An optional one left empty isn't answered, so its minimum doesn't apply.
    assert form_content(fields, {**good, "more": ["x"]})[1] == "Choose at least 2 for more."
    # A required one that may be empty is answered with nothing chosen.
    none_ok = form_fields({"required": ["t"], "properties": {
        "t": {"type": "array", "minItems": 0, "items": {"enum": ["a"]}}}})
    assert form_content(none_ok, {"t": []}) == ({"t": []}, "")


@pytest.mark.parametrize("schema,named", [
    ({"properties": {"trip": {"type": "object", "title": "Trip", "properties": {}}}}, "Trip"),
    ({"properties": {"x": {"properties": {"y": {"type": "string"}}}}}, "x"),
    ({"properties": {"x": {"type": ["string", "number"]}}}, "x"),
    ({"properties": {"x": {"type": "something"}}}, "x"),
    ({"properties": {"x": {"$ref": "#/defs/y"}}}, "x"),
    ({"properties": {"x": {"anyOf": [{"type": "string"}, {"type": "number"}]}}}, "x"),
    ({"properties": {"x": {"oneOf": [{"const": 1}, {"type": "number"}]}}}, "x"),
    ({"properties": {"x": {"type": "string", "enum": []}}}, "x"),
    # An array with nothing to choose from: no boxes, so it can't be finished.
    ({"required": ["x"], "properties": {"x": {"type": "array", "items": {"type": "string"}}}}, "x"),
    ({"properties": {"x": {"type": "array"}}}, "x"),
    ({"properties": {"x": {"type": "array", "items": {"type": "object"}}}}, "x"),
    # More needed than there are to choose, or a range nothing fits.
    ({"required": ["x"], "properties": {"x": {"type": "array", "minItems": 3,
                                              "items": {"enum": ["a"]}}}}, "x"),
    ({"properties": {"x": {"type": "array", "minItems": 3, "maxItems": 1,
                           "items": {"enum": ["a"]}}}}, "x"),
    # Required, but no way to answer it.
    ({"required": ["x"], "properties": {"x": "not a property"}}, "x"),
    ({"required": ["gone"], "properties": {}}, "gone"),
    ({"required": [7], "properties": {}}, "7"),
    # Not a list of fields at all, whose only answer would be a yes to nothing.
    ({"type": "array"}, "not a list of fields"),
    ({"properties": []}, "not a list of fields"),
    ({}, "not a list of fields"),
    ({"type": "object"}, "not a list of fields"),
    ({"properties": {"weird": "not a property"}}, "not a list of fields"),
    ({"type": "object", "properties": {"a": "x"}}, "not a list of fields")])
def test_a_form_that_cant_be_shown_says_so_and_has_no_fields(schema, named):
    problem = claude_mcp.form_problem(schema)
    assert f"({named})" in problem and "can only decline it or answer later" in problem
    request = elicitation_from("e", {**FORM_REQUEST, "requested_schema": schema})
    assert request.form_problem() == problem and request.fields() == []


def test_no_schema_at_all_is_a_form_that_cant_be_shown():
    assert "not a list of fields" in claude_mcp.form_problem(None)
    for odd in (None, "x", 5, []):
        request = elicitation_from("e", {**FORM_REQUEST, "requested_schema": odd})
        assert "not a list of fields" in request.form_problem() and request.fields() == []
    missing = {k: v for k, v in FORM_REQUEST.items() if k != "requested_schema"}
    assert "not a list of fields" in elicitation_from("e", missing).form_problem()


@pytest.mark.parametrize("schema", [
    # Deliberately empty: "properties" is there, with nothing in it.
    {"type": "object", "properties": {}}, {"properties": {}},
    FORM_REQUEST["requested_schema"],
    # One that can't be read among others that can: left out, as before.
    {"properties": {"weird": "not a property", "name": {"type": "string"}}},
    {"properties": {"x": {"oneOf": [{"const": "a"}, {"const": "b"}]}}},
    {"required": ["x"], "properties": {"x": {"type": "array", "minItems": 1,
                                             "items": {"enum": ["a"]}}}}])
def test_a_form_that_can_be_shown_has_no_problem(schema):
    assert claude_mcp.form_problem(schema) == ""


def test_a_url_request_has_no_form_problem():
    request = elicitation_from("e", {**URL_REQUEST, "requested_schema": {"type": "array"}})
    assert request.form_problem() == ""


def test_too_long_a_form_can_only_be_declined():
    many = {f"f{i}": {"type": "string"} for i in range(claude_mcp.MAX_FORM_FIELDS)}
    assert claude_mcp.form_problem({"properties": many}) == ""
    assert "too long" in claude_mcp.form_problem({"properties": {**many, "one": {"type": "string"}}})
    fits = {"enum": list(range(claude_mcp.MAX_FORM_CHOICES))}
    over = {"enum": list(range(claude_mcp.MAX_FORM_CHOICES + 1))}
    assert claude_mcp.form_problem({"properties": {"x": fits}}) == ""
    assert "too long" in claude_mcp.form_problem({"properties": {"x": over}})
    assert "too long" in claude_mcp.form_problem(
        {"properties": {"x": {"type": "array", "items": over}}})


def test_a_forms_problem_cant_bring_hidden_characters_with_it():
    problem = claude_mcp.form_problem({"properties": {
        "x": {"type": "object", "title": "a\u202eb\x00c"}}})
    assert "a[U+202E]b[U+0000]c" in problem


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


def test_a_numeric_choice_is_sent_as_a_number():
    fields = form_fields({"type": "object", "properties": {
        "size": {"type": "integer", "enum": [1, 2, 3]},
        "sizes": {"type": "array", "items": {"enum": [1, 2]}}}})
    assert fields[0].choices == [(1, "1"), (2, "2"), (3, "3")]
    assert form_content(fields, {"size": 2, "sizes": [2]}) == ({"size": 2, "sizes": [2]}, "")


def test_turned_off_tools_join_chat_options_settings_rather_than_replace_them():
    """Claude Code keeps only the last --settings (checked with 2.1.296: a
    second one dropped the first's output style), so a chat with an output
    style and a tool turned off gets one --settings holding both (#468)."""
    from thechatplace.chat_options import ChatOptions
    from thechatplace.claude_mcp import with_settings
    command = ["claude", "-p", *ChatOptions("", "", "Concise").flags(), "--resume", "x"]
    merged = with_settings(command, ["claude.ai Travel"])
    assert merged.count("--settings") == 1
    settings = json.loads(merged[merged.index("--settings") + 1])
    assert settings == {"outputStyle": "Concise",
                        "deniedMcpServers": [{"serverName": "claude.ai Travel"}]}
    # No style: a --settings of its own, as before.
    plain = with_settings(["claude", "-p"], ["claude.ai Travel"])
    assert plain[-2:] == settings_args(["claude.ai Travel"])
    # Nothing turned off: the command as it was.
    assert with_settings(command, []) == command
    # A chat titled "--settings" isn't taken for the flag.
    titled = ["claude", "--name", "--settings", "--resume", "x"]
    assert with_settings(titled, ["claude.ai Travel"])[:5] == titled
