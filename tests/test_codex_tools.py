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
    assert "codex mcp login" in elsewhere


@pytest.mark.parametrize("platform,key", [("win32", " (Alt+O)"), ("linux", " (Alt+O)"), ("darwin", "")])
def test_delete_and_sign_in_name_the_button_and_its_key_where_it_has_one(platform, key):
    host = _host(True)
    on, off = CodexTool("notes", "off"), CodexTool("notes", "connected", (), False)
    assert host.delete_text(on, platform) == (
        f"In a Codex chat, a tool changes only in a new copy: press Copy with Tool On{key} to make one.")
    assert host.delete_text(off, platform).endswith(f"press Copy with Tool Off{key} to make one.")
    assert host.sign_in_text(on, platform) == (
        f"Sign In works only in a chat with this tool on. Press Copy with Tool On{key} to make a "
        "copy with it on, then sign in from Connected Tools in that copy.")


def test_sign_in_for_a_chat_from_elsewhere_gives_the_codex_command():
    host = _host(False)
    assert host.sign_in_text(CodexTool("notes", "needs-auth", read_only=True)) == (
        "This chat wasn't started in The Chat Place. To sign in to this tool, use Codex in a "
        "terminal: codex mcp login notes.")


@pytest.mark.parametrize("name,platform,said", [
    # Every character codex mcp add allows (codex-cli 0.163) goes as it is.
    ("my-notes_2", "win32", "codex mcp login my-notes_2"),
    ("team/notes.v2", "linux", "codex mcp login team/notes.v2"),
    ("me@work:notes", "darwin", "codex mcp login me@work:notes"),
    # PowerShell would take a leading @ as splatting.
    ("@work", "win32", "codex mcp login '@work'"),
    # Hand-written in config.toml: single quotes, a quote inside as each shell needs.
    ("my notes", "win32", "codex mcp login 'my notes'"),
    ("Kelly's notes", "win32", "codex mcp login 'Kelly''s notes'"),
    ("Kelly's notes", "darwin", "codex mcp login 'Kelly'\\''s notes'"),
    ("$(rm -rf ~)", "linux", "codex mcp login '$(rm -rf ~)'"),
])
def test_the_login_command_is_safe_to_paste_in_each_systems_terminal(name, platform, said):
    pytest.importorskip("wx")
    from thechatplace.ui.codex_tools_host import _login_command
    assert _login_command(name, platform) == said


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
    note = " " + copy_note(tools=False)
    assert asked[0].startswith("Make a new copy of this chat with notes on? Its tools can reach")
    assert asked[0].endswith("so turn on only a tool you trust." + note)
    assert asked[1] == "Make a new copy of this chat with notes off?" + note
    assert asked[2] == "Make a new copy of this chat without gone? It's no longer in Codex's configuration." + note
    assert not any("Create" in text for text in asked)


def test_a_tools_copy_says_it_cant_change_files_without_asking(monkeypatch):
    """A tools copy is made read-only with approval on request, as Rewind's
    and Branch's are, so the question and the message on open say so as
    theirs do (#602). It keeps the tools you chose, so the question doesn't
    say it drops them."""
    wx = pytest.importorskip("wx")
    from thechatplace.ui.codex_extras import copy_note
    from thechatplace.ui_text import key_name
    host = _host(True)
    said, opened = [], []
    copy = SimpleNamespace(title="Chat (tools copy)", forked_from=None)
    copy.to_info = lambda: SimpleNamespace(cli_session_id="c-2", title=copy.title)
    host.frame.store.get = lambda *a, **k: host.record
    host.frame.store.add = lambda record: None
    host.frame._store_write = lambda write, record: True
    host.frame._feedback = said.append
    host.frame.open_session = opened.append
    host.frame.refresh_sessions = lambda: None
    host.frame.sessions = SimpleNamespace(copy_with_tools=lambda *a: copy)
    host.frame._codex_work = lambda action, done: done(action(), None)
    host.load = lambda done: None
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.YES)
    host.set_off(CodexTool("notes", "off"), False, lambda *a: None)
    key = key_name("Ctrl+Shift+O")
    assert asked[0].endswith(f"The new chat can't change files without asking, and doesn't keep the original's "
                             f"model, effort or Chat Options. Change Permission Mode ({key}) lets it edit files.")
    assert "connected tools" not in asked[0]
    assert said[-1] == ("Opened Chat (tools copy). " + copy_note(short=True) + " The original chat is kept.")
    assert said[-1] == (f"Opened Chat (tools copy). It can't change files without asking; Change Permission "
                        f"Mode ({key}) lets it edit. The original chat is kept.")
    assert opened and copy.forked_from == "Chat"
