"""The session tools Claude turns get in The Chat Place (#165): the MCP
server, the tools' answers, and the connection to the window. No window is
made here; the window's side is in test_ui_hidden.py."""
import io
import json
import os
import sys
import time

import pytest

from records import assistant_block, lines, text_block, user_text

from thechatplace import platform_paths, session_tools, session_tools_mcp
from thechatplace.session_tools import View, call_tool
from thechatplace.sessions import DESKTOP, OWN, TERMINAL, WORKING, SessionInfo
from thechatplace.transcript import ASSISTANT, ChatMessage, Transcript

NOW = int(time.time() * 1000)

#: The desktop app's tools, as /wrap, session-status and switch-session call
#: them.
DESKTOP_TOOLS = {"list_sessions", "get_session", "archive_session",
                 "search_session_transcripts", "open_session_in"}


def desktop(key="local_d1", title="Desktop work", ago=60_000, **extra):
    return SessionInfo(DESKTOP, key, title, "C:\\G\\Repo", "cli-" + key, key, NOW - ago, **extra)


def own(cli="own-1", title="Chat Place work", ago=120_000, **extra):
    return SessionInfo(OWN, f"own:{cli}", title, "C:\\G\\Other", cli, "", NOW - ago, **extra)


def world(**extra):
    me = own("me", "This session", 0)
    sessions = [desktop(), own(), me,
                desktop("local_old", "Archived desktop", 9_000_000, archived=True),
                own("gone", "Hidden one", 8_000_000),
                SessionInfo(TERMINAL, "terminal:t1", "Terminal work", "C:\\G\\T", "t1",
                            last_activity_ms=NOW - 300_000),
                SessionInfo("app_server", "codex:c1", "Codex chat", "C:\\G\\C", "c1",
                            last_activity_ms=NOW - 400_000, provider="codex")]
    view = View(sessions=sessions, hidden={"own:gone"}, current=me)
    for name, value in extra.items():
        setattr(view, name, value)
    return view


def call(name, arguments=None, view=None, hidden=None):
    hidden = [] if hidden is None else hidden
    return call_tool(name, arguments or {}, view or world(),
                     lambda info: hidden.append(info.key))


# -- the tools offered --------------------------------------------------------


def test_the_tools_are_the_desktop_apps_under_its_server_name():
    assert set(session_tools.TOOL_NAMES) == DESKTOP_TOOLS
    # Claude Code names them mcp__<server>__<tool>: these are the names
    # commands and skills use.
    assert session_tools.SERVER_NAME == "ccd_session_mgmt"
    for tool in session_tools.TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and tool["description"]
        for required in schema.get("required", []):
            assert required in schema["properties"]
    by_name = {tool["name"]: tool["inputSchema"] for tool in session_tools.TOOLS}
    # The arguments the desktop app's tools take, as its callers pass them.
    assert set(by_name["list_sessions"]["properties"]) >= {"limit", "include_archived"}
    assert by_name["archive_session"]["required"] == ["session_id"]
    assert "reason" in by_name["archive_session"]["properties"]
    assert by_name["get_session"]["required"] == ["session_id"]
    assert by_name["search_session_transcripts"]["required"] == ["query"]


# -- list_sessions and get_session --------------------------------------------


def test_list_leaves_out_this_session_and_archived_and_hidden_ones_newest_first():
    listed = json.loads(call("list_sessions").text)
    assert [item["sessionId"] for item in listed] == [
        "local_d1", "own:own-1", "terminal:t1", "codex:c1"]
    first = listed[0]
    # The desktop app's fields, in its forms.
    assert first["title"] == "Desktop work" and first["cwd"] == "C:\\G\\Repo"
    assert first["isArchived"] is False and first["isRunning"] is False
    assert first["link"] == "claude://claude.ai/epitaxy/local_d1"
    stamp = first["lastActivityAt"]
    assert stamp.endswith("Z") and len(stamp) == len("2026-07-28T14:10:46.075Z")
    # Only the desktop app's sessions have its link; The Chat Place's own
    # have theirs.
    assert "link" not in listed[1]
    assert listed[1]["chatPlaceLink"] == "thechatplace://session/own-1"
    assert listed[1]["kind"] == "Chat Place session" and listed[3]["provider"] == "codex"


def test_list_takes_a_limit_and_can_include_archived_and_hidden_sessions():
    assert len(json.loads(call("list_sessions", {"limit": 2}).text)) == 2
    assert len(json.loads(call("list_sessions", {"limit": "lots"}).text)) == 4
    every = {item["sessionId"]: item for item in
             json.loads(call("list_sessions", {"include_archived": True}).text)}
    assert every["local_old"]["isArchived"] is True and every["local_old"]["isHidden"] is False
    # Archiving one of The Chat Place's own hides it: hidden is archived here.
    assert every["own:gone"]["isArchived"] is True and every["own:gone"]["isHidden"] is True
    assert "own:me" not in every


def test_running_sessions_say_so():
    view = world(running={"own:own-1"})
    view.sessions[0].state = WORKING
    listed = {item["sessionId"]: item for item in json.loads(call("list_sessions", view=view).text)}
    assert listed["own:own-1"]["isRunning"] and listed["local_d1"]["isRunning"]
    assert not listed["terminal:t1"]["isRunning"]


def test_get_session_finds_self_a_session_id_or_a_link_id():
    me = json.loads(call("get_session", {"session_id": "self"}).text)
    assert me["sessionId"] == "own:me" and me["isCurrentSession"] is True
    assert json.loads(call("get_session", {"session_id": "own:own-1"}).text)["title"] == \
        "Chat Place work"
    # The id in a thechatplace:// link, or a desktop app session's local_ id.
    assert json.loads(call("get_session", {"session_id": "own-1"}).text)["sessionId"] == "own:own-1"
    assert json.loads(call("get_session", {"session_id": "local_d1"}).text)["link"].startswith(
        "claude://claude.ai/epitaxy/")
    missing = call("get_session", {"session_id": "nope"})
    assert missing.is_error and "nope" in missing.text


def test_self_not_listed_yet_is_an_error_not_a_crash():
    result = call("get_session", {"session_id": "self"}, world(current=None))
    assert result.is_error and "isn't in The Chat Place's list yet" in result.text


# -- archive_session ----------------------------------------------------------


def test_archiving_an_own_session_hides_it():
    hidden = []
    result = call("archive_session", {"session_id": "own:own-1", "reason": "PR merged"},
                  hidden=hidden)
    assert not result.is_error and hidden == ["own:own-1"]
    assert result.text.startswith('Archived session own:own-1 ("Chat Place work")')
    assert "File, Bring Back Session" in result.text


def test_a_terminal_session_can_be_hidden_too():
    hidden = []
    assert not call("archive_session", {"session_id": "terminal:t1"}, hidden=hidden).is_error
    assert hidden == ["terminal:t1"]


def test_desktop_app_sessions_are_never_changed():
    hidden = []
    result = call("archive_session", {"session_id": "local_d1", "reason": "done"}, hidden=hidden)
    assert result.is_error and hidden == []
    assert "was not archived" in result.text and "Claude desktop app" in result.text
    assert "sidebar" in result.text and "File, Hide Session" in result.text
    cowork = world()
    cowork.sessions[0].cowork = True
    assert "Cowork session" in call("archive_session", {"session_id": "local_d1"}, cowork).text


def test_another_apps_chat_is_sent_to_that_app():
    hidden = []
    result = call("archive_session", {"session_id": "codex:c1"}, hidden=hidden)
    assert result.is_error and hidden == [] and "Archive it in Codex" in result.text


def test_this_session_and_running_ones_are_left_alone():
    hidden = []
    result = call("archive_session", {"session_id": "self"}, hidden=hidden)
    assert result.is_error and "it's this session" in result.text
    view = world(running={"own:own-1"})
    result = call("archive_session", {"session_id": "own:own-1"}, view, hidden)
    assert result.is_error and "a turn is running" in result.text
    assert hidden == []


def test_already_hidden_and_unknown_sessions():
    hidden = []
    result = call("archive_session", {"session_id": "own:gone"}, hidden=hidden)
    assert not result.is_error and "already archived" in result.text and hidden == []
    result = call("archive_session", {"session_id": "nothing"}, hidden=hidden)
    assert result.is_error and "nothing was archived" in result.text


def test_a_hide_that_fails_is_not_reported_as_done():
    result = call_tool("archive_session", {"session_id": "own:own-1"}, world(),
                       lambda info: "couldn't save your hidden sessions: disk full")
    assert result.is_error
    assert result.text.endswith("was not archived: couldn't save your hidden sessions: disk full")


# -- search_session_transcripts and open_session_in ----------------------------


def _with_transcript(info, home, *records):
    info.claude_home = home
    path = platform_paths.direct_transcript_path(info.cwd, info.cli_session_id, home / "projects")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")


def test_search_finds_sessions_with_a_snippet_of_their_newest_match(tmp_path):
    view = world()
    first, second = view.sessions[0], view.sessions[1]
    _with_transcript(first, tmp_path, user_text("Plan the database migration"),
                     assistant_block(text_block("The database migration is done."), "m1"))
    _with_transcript(second, tmp_path, user_text("Nothing to see"))
    history = Transcript(messages=[ChatMessage(ASSISTANT, "Migration ready", key="i1",
                                               assistant_name="Codex")])
    view.read_history = lambda info: history if info.key == "codex:c1" else None
    result = call("search_session_transcripts", {"query": "MIGRATION"}, view)
    assert not result.is_error
    warning, found = result.text.split("\n\n", 1)
    assert warning == session_tools.SNIPPET_WARNING
    found = json.loads(found)
    assert [item["sessionId"] for item in found] == ["local_d1", "codex:c1"]
    assert "done" in found[0]["snippet"]  # the newest message that has it
    assert set(found[0]) == {"sessionId", "title", "cwd", "isArchived", "lastActivityAt",
                             "snippet"}
    assert len(json.loads(call("search_session_transcripts", {"query": "migration", "limit": 1},
                               view).text.split("\n\n", 1)[1])) == 1


def test_search_with_nothing_found_or_nothing_asked():
    result = call("search_session_transcripts", {"query": "zebra"})
    assert not result.is_error and result.text.startswith("No matching sessions found.")
    assert call("search_session_transcripts", {"query": "  "}).is_error


def test_open_session_in_says_what_to_do_instead():
    result = call("open_session_in", {"session_id": "local_d1"})
    assert result.is_error and "isn't available in The Chat Place" in result.text


def test_an_unknown_tool_is_an_error():
    assert call("delete_everything").is_error
    answer = session_tools.answer_request({"tool": "delete_everything"},
                                          lambda self_id: pytest.fail("not gathered"),
                                          lambda info: None)
    assert answer["is_error"]


def test_answer_request_gathers_for_the_session_that_asked():
    asked = []

    def gather(self_id):
        asked.append(self_id)
        return world()
    answer = session_tools.answer_request(
        {"tool": "list_sessions", "arguments": {"limit": 1}, "self": "me"}, gather,
        lambda info: None)
    assert asked == ["me"] and not answer["is_error"]
    assert len(json.loads(answer["text"])) == 1


# -- the MCP server -----------------------------------------------------------


def rpc(server, method, request_id=1, **params):
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        message["id"] = request_id
    return server.handle(message)


def test_the_server_says_hello_and_lists_its_tools():
    server = session_tools_mcp.Server(None, "me", asker=lambda *a: pytest.fail("not asked"))
    hello = rpc(server, "initialize", protocolVersion="2025-03-26")["result"]
    assert hello["protocolVersion"] == "2025-03-26"
    assert hello["serverInfo"]["name"] == "ccd_session_mgmt"
    assert "tools" in hello["capabilities"]
    # A version it doesn't know gets its newest.
    assert rpc(server, "initialize", protocolVersion="1999-01-01")["result"][
        "protocolVersion"] == session_tools_mcp.PROTOCOL_VERSIONS[0]
    assert rpc(server, "notifications/initialized", request_id=None) is None
    assert rpc(server, "ping")["result"] == {}
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == DESKTOP_TOOLS
    assert rpc(server, "resources/list")["error"]["code"] == session_tools_mcp.METHOD_NOT_FOUND
    assert rpc(server, "tools/call", name="nope")["error"]["code"] == \
        session_tools_mcp.INVALID_PARAMS
    assert server.handle({"id": 1})["error"]["code"] == session_tools_mcp.INVALID_REQUEST


def test_a_tool_call_goes_to_the_window_with_this_sessions_id(tmp_path):
    asked = []

    def asker(request, data_dir, timeout):
        asked.append((request, data_dir, timeout))
        return {"text": "[]", "is_error": False}
    server = session_tools_mcp.Server(tmp_path, "me", asker=asker)
    reply = rpc(server, "tools/call", name="list_sessions", arguments={"limit": 3})["result"]
    assert reply == {"content": [{"type": "text", "text": "[]"}], "isError": False}
    assert asked == [({"tool": "list_sessions", "arguments": {"limit": 3}, "self": "me"},
                      tmp_path, session_tools.CALL_TIMEOUT)]
    rpc(server, "tools/call", name="search_session_transcripts", arguments={"query": "x"})
    assert asked[-1][2] == session_tools.SEARCH_TIMEOUT


def test_no_window_to_ask_is_an_error_result_not_a_failure(tmp_path):
    server = session_tools_mcp.Server(tmp_path, "me")  # nothing in tmp_path
    reply = rpc(server, "tools/call", name="list_sessions")["result"]
    assert reply["isError"] is True
    assert "only while The Chat Place is running" in reply["content"][0]["text"]


def test_the_server_reads_and_writes_one_message_per_line():
    server = session_tools_mcp.Server(None, "me", asker=lambda *a: {"text": "ok"})
    incoming = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n'
                          b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                          b'[{"jsonrpc":"2.0","id":2,"method":"ping"}]\n')
    outgoing = io.BytesIO()
    session_tools_mcp.serve(server, incoming, outgoing)
    replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert replies[1]["error"]["code"] == session_tools_mcp.PARSE_ERROR
    assert replies[2]["error"]["code"] == session_tools_mcp.INVALID_REQUEST
    assert len(replies) == 3  # nothing for the notification


def test_arguments():
    assert session_tools_mcp.parse_arguments(["--data-dir", "D", "--self", "abc"]) == (
        session_tools_mcp.Path("D"), "abc")
    assert session_tools_mcp.parse_arguments([]) == (None, "")


def test_the_server_runs_over_stdio_as_claude_code_starts_it():
    """The real thing, as a child process: started the way --mcp-config
    says, it answers on stdout. No window."""
    report = session_tools_mcp.self_test(timeout=60)
    assert set(report["tools"]) == DESKTOP_TOOLS


# -- how Claude Code starts it ---------------------------------------------------


def test_a_built_app_starts_itself_and_a_source_run_starts_the_package(tmp_path):
    command, args, env = session_tools.server_command("abc", tmp_path, executable="TCP.exe",
                                                      frozen=True)
    assert command == "TCP.exe" and env == {}
    assert args == ["--session-tools-mcp", "--data-dir", str(tmp_path), "--self", "abc"]
    command, args, env = session_tools.server_command("abc", tmp_path, executable="py",
                                                      frozen=False)
    assert args[:3] == ["-m", "thechatplace", "--session-tools-mcp"]
    assert os.path.isdir(os.path.join(env["PYTHONPATH"], "thechatplace"))


def test_the_mcp_config_names_the_server_and_carries_no_secret(tmp_path):
    config = json.loads(session_tools.mcp_config("abc", tmp_path, executable="TCP.exe",
                                                 frozen=True))
    server = config["mcpServers"]["ccd_session_mgmt"]
    assert server["type"] == "stdio" and server["command"] == "TCP.exe"
    assert "key" not in json.dumps(config).lower()


# -- the connection between the server and the window -----------------------------


def test_a_request_reaches_the_window_and_comes_back(tmp_path):
    seen = []

    def handle(request):
        seen.append(request)
        return {"text": f"hello {request['self']}", "is_error": False}
    host = session_tools.Host(handle, tmp_path)
    host.start()
    try:
        details = json.loads((tmp_path / session_tools.CONNECTION_FILE).read_text())
        assert details["pid"] == os.getpid() and len(bytes.fromhex(details["key"])) == 32
        if sys.platform != "win32":
            assert (tmp_path / session_tools.CONNECTION_FILE).stat().st_mode & 0o077 == 0
        answer = session_tools.ask({"tool": "list_sessions", "self": "me"}, tmp_path, 10)
        assert answer == {"text": "hello me", "is_error": False}
        assert seen == [{"tool": "list_sessions", "self": "me"}]
    finally:
        host.close()
    # Gone with the window, so a later server can't reach a stale address.
    assert not (tmp_path / session_tools.CONNECTION_FILE).exists()
    with pytest.raises(session_tools.Unreachable):
        session_tools.ask({"tool": "list_sessions"}, tmp_path, 2)


def test_the_wrong_key_gets_nothing(tmp_path):
    host = session_tools.Host(lambda request: pytest.fail("must not be asked"), tmp_path)
    host.start()
    try:
        path = tmp_path / session_tools.CONNECTION_FILE
        details = json.loads(path.read_text())
        details["key"] = "00" * 32
        path.write_text(json.dumps(details))
        with pytest.raises(session_tools.Unreachable):
            session_tools.ask({"tool": "list_sessions"}, tmp_path, 5)
    finally:
        host.close()


def test_a_handler_that_fails_says_why(tmp_path):
    def handle(request):
        raise RuntimeError("window closing")
    host = session_tools.Host(handle, tmp_path)
    host.start()
    try:
        answer = session_tools.ask({"tool": "list_sessions"}, tmp_path, 10)
    finally:
        host.close()
    assert answer["is_error"] and "window closing" in answer["text"]


def test_iso_time_is_the_desktop_apps_form():
    assert session_tools.iso_time(1785247846075) == "2026-07-28T14:10:46.075Z"



def test_a_caller_that_never_answers_holds_nobody_up(tmp_path):
    from multiprocessing.connection import Client
    host = session_tools.Host(lambda request: {"text": "ok", "is_error": False}, tmp_path)
    host.start()
    try:
        details = json.loads((tmp_path / session_tools.CONNECTION_FILE).read_text())
        silent = Client(details["address"], family=details["family"])  # no key, says nothing
        try:
            assert session_tools.ask({"tool": "list_sessions"}, tmp_path, 10)["text"] == "ok"
        finally:
            silent.close()
    finally:
        host.close()


def test_archive_waits_longer_than_the_window_may_take_twice(tmp_path):
    asked = []
    server = session_tools_mcp.Server(tmp_path, "me",
                                      asker=lambda r, d, timeout: asked.append(timeout) or {})
    rpc(server, "tools/call", name="archive_session", arguments={"session_id": "x"})
    assert asked == [session_tools.ARCHIVE_TIMEOUT]
    assert session_tools.ARCHIVE_TIMEOUT > 2 * session_tools.CALL_TIMEOUT


def test_a_search_stops_when_the_window_closes(tmp_path):
    view = world(cancelled=lambda: True)
    _with_transcript(view.sessions[0], tmp_path, user_text("migration"))
    result = call("search_session_transcripts", {"query": "migration"}, view)
    assert result.text.startswith("No matching sessions found. Searched 0 sessions.")
