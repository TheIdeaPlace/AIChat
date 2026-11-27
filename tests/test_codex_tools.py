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
    # The fake's servers have no "url", so they're stdio servers to Sign In.
    assert rows[0] == CodexTool("notes", "connected", ("find",), False, auth="unsupported")
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


@pytest.mark.parametrize("own", [True, False])
@pytest.mark.parametrize("off", [True, False])
def test_the_apps_unavailable_row_has_a_neutral_toggle_label(own, off):
    """It isn't a tool, so the button names no copy with it on or off (#729's
    review), however the row was made."""
    host = _host(own)
    notice = CodexTool("Apps unavailable", "Press Refresh to try again.",
                       off_for_chat=off, informational=True)
    assert host.toggle_label(notice) == "Change T&ool"
    assert "Copy with Tool" not in host.toggle_label(notice)
    # A real tool's label is unchanged.
    assert host.toggle_label(CodexTool("notes", "off")) == (
        "Copy with Tool &On" if own else "Turn &Off for This Chat")


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
    # The notice is its own sentence after the count (#729's review).
    assert summary == "2 connected tools. " + notice.text()
    assert not notice.off_for_chat
    assert tools_summary([notice]).startswith("No configured tools listed.")


def test_app_pagination_refetches_catalog_only_on_the_first_page():
    from thechatplace.providers.codex_tools import app_inventory
    calls = []
    class Connection:
        def request(self, method, params):
            assert method == "app/list"
            calls.append(params)
            return {"data": [], "nextCursor": "page-two" if len(calls) == 1 else None}
    assert app_inventory(Connection(), INFO, refresh=True) == []
    assert calls == [{"limit": 100, "forceRefetch": True},
                     {"limit": 100, "forceRefetch": False, "cursor": "page-two"}]


def test_only_refresh_reads_codexs_catalog_again():
    """Codex's first app/list page waits for the whole catalog, 40 seconds or
    more, unless Codex already has it (#729's end-to-end test). Every read
    takes what Codex has unless Refresh asks for it again."""
    from thechatplace.providers.codex_tools import app_inventory
    calls = []
    class Connection:
        def request(self, method, params):
            calls.append(params)
            return {"data": []}
    app_inventory(Connection(), INFO)
    assert calls == [{"limit": 100, "forceRefetch": False}]
    sent = []
    class Records(FakeConnection):
        def request(self, method, params):
            sent.append((method, params))
            return super().request(method, params)
    connection = Records()
    codex.CodexProvider.connected_tools(_provider(connection), INFO)
    assert ("app/list", {"limit": 100, "forceRefetch": False}) in sent
    sent.clear()
    codex.CodexProvider.connected_tools(_provider(connection), INFO, refresh=True)
    assert ("app/list", {"limit": 100, "forceRefetch": True}) in sent


def test_reconnect_reads_the_list_without_fetching_the_catalog_again():
    """Reconnect re-reads the list, and was refetching the catalog for most of
    a minute on the shared pool (#729's review)."""
    sent = []
    class Records(FakeConnection):
        def request(self, method, params):
            sent.append((method, params))
            return super().request(method, params)
    connection = Records(status=[])
    runtime = SimpleNamespace(tools_context=lambda native: (connection, ("notes",)),
                              selected_apps=lambda native: ())
    provider = SimpleNamespace(connection=connection, runtime=runtime, provider_id="codex",
                               _check=lambda session: None)
    provider.connected_tools = lambda session, **k: codex.CodexProvider.connected_tools(provider, session, **k)
    info = SimpleNamespace(cwd="folder", cli_session_id="native-1", is_own=True)
    codex.CodexProvider.connected_tool_action(provider, info, "notes", "reconnect")
    assert ("config/mcpServer/reload", {}) in sent
    assert ("app/list", {"limit": 100, "forceRefetch": False}) in sent
    assert ("app/list", {"limit": 100, "forceRefetch": True}) not in sent


def _reconnect_provider(connection, allowed=("notes",), is_own=True):
    runtime = SimpleNamespace(tools_context=lambda native: (connection, allowed),
                              selected_apps=lambda native: ())
    provider = SimpleNamespace(connection=connection, runtime=runtime, provider_id="codex",
                               _check=lambda session: None)
    provider.connected_tools = lambda session, **k: codex.CodexProvider.connected_tools(provider, session, **k)
    info = SimpleNamespace(cwd="folder", cli_session_id="native-1", is_own=is_own)
    return provider, info


@pytest.mark.parametrize("error,code", [
    ("rpc", "failed"), ("timeout", "timeout"), ("elsewhere", "not_own"), ("off", "not_enabled")])
def test_reconnect_refusals_are_the_apps_own_words_with_a_next_step(error, code):
    """Codex's own reload error can carry the server's text; the window said
    only "Codex could not reconnect this tool" (#729's acceptance test). Each
    refusal is now a fixed reason safe_reason passes through."""
    from thechatplace.codex_diagnostics import safe_reason
    from thechatplace.providers.codex_rpc import CodexReconnectRefused, CodexRequestTimeout

    class Fails(FakeConnection):
        def request(self, method, params):
            if method == "config/mcpServer/reload":
                if error == "timeout":
                    raise CodexRequestTimeout()
                raise CodexRpcError("server said: secret token abc123")
            return super().request(method, params)
    provider, info = _reconnect_provider(Fails(status=[]), allowed=() if error == "off" else ("notes",),
                                         is_own=error != "elsewhere")
    with pytest.raises(CodexReconnectRefused) as raised:
        codex.CodexProvider.connected_tool_action(provider, info, "notes", "reconnect")
    assert raised.value.code == code
    said = safe_reason(raised.value)
    assert said == CodexReconnectRefused.REASONS[code]
    assert "secret" not in said and said.rstrip().endswith(".")


def test_a_lost_connection_during_reconnect_keeps_its_own_words():
    from thechatplace.codex_diagnostics import safe_reason
    from thechatplace.providers.codex_rpc import CodexConnectionFailure

    class Lost(FakeConnection):
        def request(self, method, params):
            if method == "config/mcpServer/reload":
                raise CodexConnectionFailure("stream_closed")
            return super().request(method, params)
    provider, info = _reconnect_provider(Lost(status=[]))
    with pytest.raises(CodexConnectionFailure) as raised:
        codex.CodexProvider.connected_tool_action(provider, info, "notes", "reconnect")
    assert safe_reason(raised.value) == CodexConnectionFailure.REASONS["stream_closed"]


def test_a_finished_sign_in_refreshes_without_fetching_the_catalog_again(monkeypatch):
    wx = pytest.importorskip("wx")
    host = _host(True)
    refreshed = []
    # Waits for anything under way, not a "Still ..." box nobody asked for (#729's review).
    host.dialog = SimpleNamespace(refresh_when_free=lambda: refreshed.append("refresh"))
    host.frame._feedback = lambda text: None
    host.frame._codex_work = lambda action, done: done(True, False)
    host.frame.sessions = SimpleNamespace(connected_tool_action=lambda *a: True)
    server = CodexTool("notes", "needs-auth", (), False)
    host._logins["notes"] = "login"
    host._poll_login(host.info, server, "login", 1)
    assert refreshed == ["refresh"]


def test_sign_in_says_what_happened_so_focus_needs_no_guessing_from_the_words():
    """The host says whether anything changed, rather than the dialog
    comparing the words (#729's review): nothing to sign in to is UNCHANGED."""
    from thechatplace.ui.dialogs import SIGN_IN_UNCHANGED
    host = _host(True)
    server = CodexTool("everything", "configured", auth="unsupported")
    calls = []
    host.sign_in(server, lambda *a: calls.append(a))
    assert calls == [(host.nothing_to_sign_in(server), "", SIGN_IN_UNCHANGED)]


CATALOG = [
    {"id": "unconnected-one", "name": "First catalog app", "isAccessible": False},
    {"id": "available-one", "name": "First usable app", "isAccessible": True},
    {"id": "unconnected-two", "name": "Second catalog app", "isAccessible": False},
    {"id": "available-two", "name": "Second usable app", "isAccessible": True},
    {"id": "unconnected-three", "name": "Third catalog app", "isAccessible": False},
]


class Catalog:
    def request(self, method, params):
        return {"data": CATALOG}


def test_only_chosen_and_usable_apps_are_rows_and_one_row_counts_the_rest():
    """The ChatGPT catalog listed 5,027 apps as rows, nearly all never set up
    (#729's end-to-end test). Rows are the apps on for this chat and those the
    account can already use; one information row counts the rest."""
    from thechatplace.providers.codex_tools import app_inventory
    rows = app_inventory(Catalog(), INFO)
    assert [row.app_id for row in rows] == ["available-one", "available-two", ""]
    assert all(row.off_for_chat for row in rows[:2])
    more = rows[-1]
    assert more.informational and not more.off_for_chat and not more.app_id
    assert more.text() == ("More ChatGPT apps: 3 more aren't connected to your account. To use "
                           "one, connect it in ChatGPT's settings, under Apps, then press Refresh here.")
    # It isn't a tool: the count and the summary leave it out, and say it.
    assert tools_summary(rows) == "2 connected tools, 2 off for this chat. " + more.text()
    # An app on for the chat that now needs sign-in stays with the chat's
    # apps, ahead of usable apps that are off: it's the row someone opened
    # the list to fix (#729's review). It's no longer counted with the rest.
    rows = app_inventory(Catalog(), INFO, selected=("unconnected-two", "available-two"))
    assert [row.app_id for row in rows] == ["available-two", "unconnected-two", "available-one", ""]
    assert [row.off_for_chat for row in rows] == [False, False, True, False]
    assert rows[1].install_url == "" and rows[1].status == "needs sign-in or installation"
    assert rows[-1].text().startswith("More ChatGPT apps: 2 more aren't connected to your account.")
    # One on for the chat that Codex no longer lists is with the chat's apps
    # too, ahead of the rest.
    rows = app_inventory(Catalog(), INFO, selected=("gone-app", "available-two"))
    assert [row.app_id for row in rows] == ["available-two", "gone-app", "available-one", ""]
    assert rows[1].missing and not rows[1].off_for_chat


def test_a_large_catalog_is_one_row_with_a_readable_count():
    from thechatplace.providers.codex_tools import app_inventory
    class Big:
        def request(self, method, params):
            page = int(params.get("cursor") or 0)
            data = [{"id": f"app-{page}-{n}", "name": f"App {page} {n}",
                     "isAccessible": page == 0 and n < 3} for n in range(100)]
            return {"data": data, "nextCursor": str(page + 1) if page < 50 else None}
    rows = app_inventory(Big(), INFO)
    assert len(rows) == 4 and rows[-1].informational
    assert rows[-1].text().startswith("More ChatGPT apps: 5,097 more aren't connected to your account.")


def test_every_app_usable_or_chosen_leaves_no_more_apps_row():
    from thechatplace.providers.codex_tools import app_inventory
    class Usable:
        def request(self, method, params):
            return {"data": [{"id": "one", "name": "One", "isAccessible": True},
                             {"id": "two", "name": "Two", "isAccessible": False}]}
    rows = app_inventory(Usable(), INFO, selected=("two",))
    assert [row.app_id for row in rows] == ["two", "one"]
    assert not any(row.informational for row in rows)


def test_a_chat_started_elsewhere_lists_only_usable_apps():
    """Nothing is on for such a chat here, so every app was a read-only row."""
    from thechatplace.providers.codex_tools import app_inventory
    rows = app_inventory(Catalog(), INFO, read_only=True)
    assert [row.app_id for row in rows] == ["available-one", "available-two", ""]
    assert rows[-1].text().startswith("More ChatGPT apps: 3 more aren't connected to your account.")
    assert all(row.read_only for row in rows)


@pytest.mark.parametrize("own", [True, False])
def test_the_more_apps_row_has_the_neutral_toggle_label(own):
    from thechatplace.providers.codex_tools import more_apps
    assert _host(own).toggle_label(more_apps(12)) == "Change T&ool"


# -- Sign In for a server with nothing to sign in to, or that can't (#729) ------------------


@pytest.mark.parametrize("config,code", [
    ({"command": "npx", "args": ["-y", "server"]}, "not_needed"),
    ({"url": "https://example.invalid/mcp"}, None),
    ({"url": "https://example.invalid/mcp", "bearer_token_env_var": "TOKEN"}, "token"),
    (None, "not_configured"),
    ("not a table", "not_configured"),
])
def test_sign_in_refusal_comes_from_the_configuration(config, code):
    from thechatplace.providers.codex_tools import sign_in_refusal, config_auth
    assert sign_in_refusal(config) == code
    assert config_auth(config) == {"not_needed": "unsupported", "token": "bearerToken"}.get(code, "")


def test_rows_carry_how_each_server_signs_in_whether_or_not_the_chat_is_its_own():
    """From the configuration, not mcpServerStatus/list's authStatus: with a
    thread, Codex 0.163 said "unsupported" for GitHub's HTTP server while it
    was off for the chat, which needs signing in (#729)."""
    servers = {"stdio": {"command": "npx"}, "web": {"url": "https://example.invalid/mcp"},
               "token": {"url": "https://example.invalid/mcp", "bearer_token_env_var": "T"}}
    status = [{"name": name, "runtimeStatus": None, "authStatus": "unsupported"} for name in servers]
    for rows in (inventory(FakeConnection(servers=servers), INFO, read_only=True),
                 inventory(FakeConnection(servers=servers, status=status),
                           SimpleNamespace(cwd="folder", cli_session_id="native-1"), ())):
        assert {row.name: row.auth for row in rows} == {
            "stdio": "unsupported", "web": "", "token": "bearerToken"}


def test_sign_in_says_there_is_nothing_to_sign_in_to_without_asking_codex():
    host = _host(True)
    work = []
    host.frame._codex_work = lambda action, done: work.append(action)
    said = []
    for server in (CodexTool("everything", "connected", (), False, auth="unsupported"),
                   CodexTool("tokened", "off", auth="bearerToken")):
        host.sign_in(server, lambda text, error, outcome: said.append((text, error, outcome)))
    assert work == []
    assert said == [
        ("everything: This tool doesn't need signing in: Codex starts it on this computer "
         "without an account.", "", "unchanged"),
        ("tokened: This tool signs in with a token set up in Codex's configuration, so there's "
         "nothing to sign in to here. To change the token, change its environment variable.", "",
         "unchanged")]
    assert host.nothing_to_sign_in(CodexTool("web", "needs-auth", (), False)) == ""
    # An app's Sign In is its page, whatever the field says.
    assert host.nothing_to_sign_in(CodexTool("app:x", "off", app_id="x", auth="unsupported")) == ""


@pytest.mark.parametrize("result,error,expected", [
    # The fixed sentence for a service with no dynamic client registration.
    ("This service doesn't support signing in through Codex. Give Codex a token",
     True, (None, "This service doesn't support signing in through Codex. Give Codex a token")),
    # A refusal from a chat whose rows had no auth status isn't a failure.
    ("This tool doesn't need signing in: Codex starts it on this computer without an account.",
     True, ("notes: This tool doesn't need signing in: Codex starts it on this computer "
            "without an account.", "", "unchanged")),
    # No known reason: still a next step, as Details promises.
    (None, True, (None, "Codex could not start signing in to this tool. To see Codex's reason, "
                        "run codex mcp login followed by the tool's name in a terminal.")),
    ({}, True, (None, "Codex could not start signing in to this tool. To see Codex's reason, "
                      "run codex mcp login followed by the tool's name in a terminal.")),
])
def test_sign_in_failure_says_why_in_the_apps_own_words(result, error, expected):
    host = _host(True)
    jobs, said = [], []
    host.frame._codex_work = lambda action, done: jobs.append(done)
    host.sign_in(CodexTool("notes", "needs-auth", (), False), lambda *a: said.append(a))
    jobs[0](result, error)
    assert said == [expected]


class SignInConnection:
    def __init__(self, server, error=None):
        self.server, self.error, self.calls, self.closed = server, error, [], False

    def request(self, method, params):
        self.calls.append(method)
        if method == "config/read":
            return {"config": {"mcp_servers": {"notes": self.server}}}
        raise self.error


class SignInManager:
    def __init__(self, connection):
        self.connection, self.closes = connection, 0

    def _get(self):
        return self.connection

    def close(self):
        self.closes += 1


def _error(message, code):
    """A refusal as the connection makes it from Codex's answer."""
    import threading
    from thechatplace.providers.codex_rpc import ReadOnlyCodexConnection, _Pending
    connection = object.__new__(ReadOnlyCodexConnection)
    connection._lock, connection._closed = threading.Lock(), False
    connection._abandoned, connection._abandoned_before = set(), 0
    pending = _Pending()
    connection._pending = {1: pending}
    connection._receive({"id": 1, "error": {"code": code, "message": message}})
    return pending.error


# Codex 0.163's words, measured with GitHub's MCP server and a stdio server.
NO_DCR = ("failed to login to MCP server 'github': Registration failed: Dynamic registration "
          "failed: Registration failed: Dynamic client registration not supported")
HTTP_ONLY = "OAuth login is only supported for streamable HTTP servers."


def test_the_connection_keeps_which_sign_in_refusal_it_was_and_never_the_words():
    assert _error(NO_DCR, -32603).sign_in_refusal == "no_registration"
    assert _error(HTTP_ONLY, -32600).sign_in_refusal == "not_needed"
    other = _error("failed to login: connection refused", -32603)
    assert other.sign_in_refusal is None
    assert "github" not in str(_error(NO_DCR, -32603))


@pytest.mark.parametrize("server,error,code,asked", [
    ({"command": "npx"}, None, "not_needed", False),
    ({"url": "https://example.invalid/mcp", "bearer_token_env_var": "T"}, None, "token", False),
    ({"url": "https://example.invalid/mcp"}, (NO_DCR, -32603), "no_registration", True),
    ({"url": "https://example.invalid/mcp"}, (HTTP_ONLY, -32600), "not_needed", True),
])
def test_standalone_sign_in_refuses_with_a_fixed_reason(tmp_path, server, error, code, asked):
    from thechatplace.codex_diagnostics import safe_reason
    from thechatplace.providers.codex_rpc import CodexSignInRefused
    from thechatplace.providers.codex_tools import CodexToolLogins
    connection = SignInConnection(server, _error(*error) if error else None)
    manager = SignInManager(connection)
    service = CodexToolLogins(factory=lambda folder: manager)
    try:
        with pytest.raises(CodexSignInRefused) as refused:
            service.start(str(tmp_path), "notes")
        assert refused.value.code == code
        assert ("mcpServer/oauth/login" in connection.calls) == asked
        shown = safe_reason(refused.value)
        assert shown == CodexSignInRefused.REASONS[code]
        assert "github" not in shown and "Registration failed" not in shown
        assert manager.closes == 1 and not service._attempts
    finally:
        service.close()


def test_an_unknown_sign_in_failure_shows_nothing_of_codexs(tmp_path):
    from thechatplace.codex_diagnostics import safe_reason
    from thechatplace.providers.codex_tools import CodexToolLogins
    connection = SignInConnection({"url": "https://example.invalid/mcp"},
                                  _error("private server words", -32603))
    service = CodexToolLogins(factory=lambda folder: SignInManager(connection))
    try:
        with pytest.raises(CodexRpcError) as failed:
            service.start(str(tmp_path), "notes")
        assert safe_reason(failed.value) == ""
    finally:
        service.close()
