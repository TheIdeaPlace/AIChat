"""Connected Tools for a Codex chat (#468): which servers it can list, above
all for a chat started elsewhere, which Codex can't say the status of."""
from types import SimpleNamespace

import pytest

from thechatplace.claude_mcp import tools_summary
from thechatplace.providers import codex
from thechatplace.providers.codex_rpc import CodexRpcError
from thechatplace.providers.codex_tools import CodexTool, inventory


class FakeConnection:
    """Answers config/read. mcpServerStatus/list fails unless the thread is
    loaded, as measured with real Codex 0.163 on a discovery connection."""

    def __init__(self, servers=None, status=None):
        self.servers = {"notes": {}, "search": {}} if servers is None else servers
        self.status = status
        self.calls = []

    def request(self, method, params):
        self.calls.append(method)
        if method == "config/read":
            return {"config": {"mcp_servers": self.servers}}
        if self.status is None:
            raise CodexRpcError("Codex request failed")
        return {"data": self.status}


INFO = SimpleNamespace(cwd="folder", cli_session_id="native-1", is_own=False)


def test_a_chat_started_elsewhere_lists_the_config_and_never_asks_for_status():
    connection = FakeConnection()
    rows = inventory(connection, INFO, read_only=True)
    assert connection.calls == ["config/read"]
    assert [r.name for r in rows] == ["notes", "search"]
    assert [r.text() for r in rows] == ["notes: configured, configured Codex MCP server",
                                        "search: configured, configured Codex MCP server"]
    # Not "off for this chat", which reads as something to turn on.
    assert not any(r.off_for_chat for r in rows)
    assert tools_summary(rows) == "2 connected tools"


def test_no_servers_configured_is_an_empty_list():
    assert inventory(FakeConnection(servers={}), INFO, read_only=True) == []


def _provider(connection, runtime=None):
    return SimpleNamespace(connection=connection, runtime=runtime, _check=lambda session: None)


def test_the_provider_never_sends_another_chats_thread_id_to_discovery():
    connection = FakeConnection()
    rows = codex.CodexProvider.connected_tools(_provider(connection, runtime=object()), INFO)
    assert connection.calls == ["config/read"] and rows[0].read_only


def test_a_row_counts_tools_in_words_and_says_needs_sign_in():
    def row(tools=(), status="connected"):
        return CodexTool("notes", status, tools, False).text()
    assert row(("find",)).endswith(", 1 tool")
    assert row(("find", "add")).endswith(", 2 tools")
    assert row(()).endswith(", 0 tools")
    assert row(status="needs-auth").startswith("notes: needs sign-in,")
    assert "needs-auth" not in row(status="needs-auth")
    # What the dialog checks for its sign-in line is still Codex's own status.
    connection = FakeConnection(servers={"notes": {}},
                                status=[{"name": "notes", "runtimeStatus": "authenticationRequired"}])
    info = SimpleNamespace(cwd="folder", cli_session_id="native-1")
    only = inventory(connection, info, ("notes",))[0]
    assert only.status == "needs-auth" and "needs sign-in" in only.text()


def test_a_chat_of_its_own_still_reads_status_on_its_own_connection():
    connection = FakeConnection(status=[{"name": "notes", "runtimeStatus": "connected",
                                         "tools": {"find": {}}}])
    runtime = SimpleNamespace(tools_context=lambda native: (connection, ("notes",)))
    info = SimpleNamespace(cwd="folder", cli_session_id="native-1", is_own=True)
    rows = codex.CodexProvider.connected_tools(_provider(FakeConnection(), runtime), info)
    assert connection.calls == ["config/read", "mcpServerStatus/list"]
    assert rows[0] == CodexTool("notes", "connected", ("find",), False)
    assert rows[1].off_for_chat and rows[1].text().startswith("search: off for this chat")


def test_a_failure_reading_the_config_still_reaches_the_host():
    class Refuses(FakeConnection):
        def request(self, method, params):
            raise CodexRpcError("Codex request failed")
    with pytest.raises(CodexRpcError):
        inventory(Refuses(), INFO, read_only=True)
