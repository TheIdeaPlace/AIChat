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
        if method == "app/list":
            return {"data": []}
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
    assert connection.calls == ["config/read", "app/list"] and rows[0].read_only


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
    runtime = SimpleNamespace(tools_context=lambda native: (connection, ("notes",)),
                              selected_apps=lambda native: ())
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


def test_a_row_gives_no_count_until_its_tools_are_known():
    from thechatplace.providers.codex_tools import CodexTool
    assert " tool" not in CodexTool("off", "connected", (), True).text().replace("MCP server", "")
    assert "0 tools" not in CodexTool("signin", "needs-auth", (), False).text()
    assert "1 tool" in CodexTool("one", "connected", ("find",), False).text()
    assert "0 tools" in CodexTool("none", "connected", (), False).text()


# -- what the Connected Tools dialog says in a Codex chat (#596) ----------------------------


def _host(own):
    pytest.importorskip("wx")
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    record = SimpleNamespace(cli_session_id="c-1", provider_options={}) if own else None
    frame = SimpleNamespace(store=SimpleNamespace(get=lambda *a, **k: record))
    return CodexConnectedTools(frame, SimpleNamespace(cli_session_id="c-1", is_own=own, title="Chat"))


def test_the_codex_note_says_first_that_a_change_is_a_new_copy():
    from thechatplace.ui import codex_tools_host
    own, elsewhere = _host(True).note(), _host(False).note()
    assert own.startswith("In a Codex chat, a tool can't be turned on or off in the chat itself: "
                          "Copy with Tool On or Off makes a new copy of the chat with that choice, "
                          "and this chat stays as it is.")
    assert own == codex_tools_host.NOTE and elsewhere == codex_tools_host.NOTE_READ_ONLY
    for note in (own, elsewhere):
        # The developer words of #596's finding 26, and a plugin nobody's offered.
        for word in ("MCP", "sandbox", "Plugin", "read-only", "Configured", "CLI"):
            assert word not in note
        assert "codex mcp add" in note
    assert "Sign In connects your account without changing this chat's tools" in elsewhere


@pytest.mark.parametrize("platform,key", [("win32", " (Alt+O)"), ("linux", " (Alt+O)"), ("darwin", "")])
def test_delete_and_sign_in_name_the_button_and_its_key_where_it_has_one(platform, key):
    host = _host(True)
    on, off = CodexTool("notes", "off"), CodexTool("notes", "connected", (), False)
    assert host.delete_text(on, platform) == (
        f"In a Codex chat, a tool changes only in a new copy: press Copy with Tool On{key} to make one.")
    assert host.delete_text(off, platform).endswith(f"press Copy with Tool Off{key} to make one.")


@pytest.mark.parametrize('own', [False, True])
def test_signin_can_start_for_an_off_server_without_a_chat(own):
    host=_host(own)
    work=[];calls=[]
    host.frame._codex_work=lambda action,done: work.append(action)
    host.frame.sessions=SimpleNamespace(connected_tool_action=lambda *a: calls.append(a))
    host.sign_in(CodexTool('notes','off',off_for_chat=True),lambda *a: None)
    assert len(work)==1
    work[0]()
    assert calls==[(host.info,'notes','login')]


def test_every_copy_question_says_make_a_new_copy(monkeypatch):
    wx = pytest.importorskip("wx")
    host = _host(True)
    host.frame.store.get = lambda *a, **k: host.record
    host.load = lambda done: None
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.NO)
    host.set_off(CodexTool("notes", "off"), False, lambda *a: None)
    host.set_off(CodexTool("notes", "connected", (), False), True, lambda *a: None)
    host.set_off(CodexTool("gone", "removed", (), False, True), True, lambda *a: None)
    from thechatplace.ui.codex_extras import copy_note
    note = "A new copy of this chat will be made, and this chat is kept. " + copy_note(tools=False)
    # It starts by saying a copy is made, and the question comes last, as
    # Branch's does (#602), so it's the last thing a screen reader says
    # before the buttons.
    assert asked[0] == (note + " A tool can reach websites, services and files outside this chat's folder, "
                        "so turn on only a tool you trust. Make a new copy of this chat with notes on?")
    assert asked[1] == note + " Make a new copy of this chat with notes off?"
    assert asked[2] == ("gone is no longer in Codex's configuration. " + note
                        + " Make a new copy of this chat without gone?")
    assert not any("Create" in text for text in asked)


def test_a_tools_copy_says_it_keeps_the_originals_settings(monkeypatch):
    """A tools copy keeps the original's settings, as Rewind's and Branch's
    copies do, so the question and the message on open say so as theirs do
    (#596, decision 3). Its tools are the ones you chose, so the question
    doesn't name them; the saved record goes along, for a chat not loaded."""
    wx = pytest.importorskip("wx")
    from thechatplace.ui.codex_extras import copy_note
    host = _host(True)
    said, opened, made = [], [], []
    copy = SimpleNamespace(title="Chat (tools copy)", forked_from=None)
    copy.to_info = lambda: SimpleNamespace(cli_session_id="c-2", title=copy.title)
    host.frame.store.get = lambda *a, **k: host.record
    host.frame.store.add = lambda record: None
    host.frame._store_write = lambda write, record: True
    host.frame._feedback = said.append
    host.frame.open_session = opened.append
    host.frame.refresh_sessions = lambda: None
    host.frame.sessions = SimpleNamespace(copy_with_tools=lambda *a, **k: made.append(k) or copy)
    host.frame._codex_work = lambda action, done: done(action(), None)
    host.load = lambda done: None
    asked = []
    original = host.record
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.YES)
    host.set_off(CodexTool("notes", "off"), False, lambda *a: None)
    assert asked[0].startswith("A new copy of this chat will be made, and this chat is kept. "
                               "The new chat keeps the original's permission mode, model, effort and "
                               "Chat Options. ")
    assert "connected tools" not in asked[0] and "without asking" not in asked[0].split(". A tool")[0]
    assert said[-1] == ("Opened Chat (tools copy). " + copy_note(short=True) + " The original chat is kept.")
    assert said[-1] == "Opened Chat (tools copy). It keeps the original's settings. The original chat is kept."
    assert made == [{'own': original}]
    assert opened and copy.forked_from == "Chat"


def test_a_tools_copy_finished_after_the_dialog_closed_still_says_it(monkeypatch):
    """Closing Connected Tools while the copy is made only saves it; that
    message says what the copy can do too (#602)."""
    wx = pytest.importorskip("wx")
    from thechatplace.ui.codex_extras import copy_note
    host = _host(True)
    said, opened = [], []
    copy = SimpleNamespace(title="Chat (tools copy)", forked_from=None)
    host.frame.store.get = lambda *a, **k: host.record
    host.frame.store.add = lambda record: None
    host.frame._store_write = lambda write, record: True
    host.frame._feedback = said.append
    host.frame.open_session = opened.append
    host.frame.refresh_sessions = lambda: None
    host.frame.sessions = SimpleNamespace(copy_with_tools=lambda *a, **k: copy)
    jobs = []
    host.frame._codex_work = lambda action, done: jobs.append((action, done))
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    host.set_off(CodexTool("notes", "off"), False, lambda *a: None)
    host.close()
    action, done = jobs.pop()
    done(action(), None)
    assert said[-1] == "Saved Chat (tools copy). No prompt was sent. " + copy_note(short=True)
    assert opened == []


@pytest.mark.parametrize("failure,expected", [
    ("timeout", "timed out"), ("unsupported", "does not support"), ("other", "could not list"),
])
def test_app_inventory_failure_is_information_not_a_configured_server(failure, expected):
    from thechatplace.providers.codex_rpc import CodexRequestTimeout
    class Connection(FakeConnection):
        def request(self, method, params):
            if method == "app/list":
                error = CodexRequestTimeout() if failure == "timeout" else CodexRpcError("private payload")
                error.unknown_method = failure == "unsupported"
                raise error
            return super().request(method, params)
    rows = codex.CodexProvider.connected_tools(_provider(Connection()), INFO)
    notice = rows[-1]
    assert notice.informational and expected in notice.text()
    assert "private payload" not in notice.text()
    assert "configured Codex MCP server" not in notice.text()
    assert "does not mean your account has no apps" in notice.text()
    summary = tools_summary(rows)
    assert "2 connected tools" in summary and notice.text() in summary
    assert tools_summary([notice]).startswith("No configured tools listed.")


def test_app_pagination_refetches_catalog_only_on_the_first_page():
    from thechatplace.providers.codex_tools import app_inventory
    calls = []
    class Connection:
        def request(self, method, params):
            assert method == "app/list"
            calls.append(params)
            return {"data": [], "nextCursor": "page-two" if len(calls) == 1 else None}
    assert app_inventory(Connection(), INFO) == []
    assert calls == [{"limit": 100, "forceRefetch": True},
                     {"limit": 100, "forceRefetch": False, "cursor": "page-two"}]


def test_accessible_apps_precede_the_installable_catalog_without_losing_entries():
    from thechatplace.providers.codex_tools import app_inventory
    class Connection:
        def request(self, method, params):
            return {"data": [
                {"id": "unconnected-one", "name": "First catalog app", "isAccessible": False},
                {"id": "available-one", "name": "First usable app", "isAccessible": True},
                {"id": "unconnected-two", "name": "Second catalog app", "isAccessible": False},
                {"id": "available-two", "name": "Second usable app", "isAccessible": True},
            ]}
    rows = app_inventory(Connection(), INFO)
    assert [row.app_id for row in rows] == [
        "available-one", "available-two", "unconnected-one", "unconnected-two"]
    assert all(row.off_for_chat for row in rows)
