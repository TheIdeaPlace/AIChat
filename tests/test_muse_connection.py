"""Read-only MSP transport, with no real Muse account or history."""
import json
import threading
import time

import pytest

from test_muse_account import Host, HANDSHAKE
from thechatplace.providers.muse_connection import (
    ExecutionConnection, HistoryConnection, ManagedMuseConnection,
    ManagedMuseExecution, MuseHistoryError,
    SCHEMA_FINGERPRINT, EXPERIMENTAL_FINGERPRINT,
)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setitem(HANDSHAKE, "schema", {
        "version": 1, "fingerprint": SCHEMA_FINGERPRINT,
        "experimentalFingerprint": EXPERIMENTAL_FINGERPRINT})
    return Host(lambda message: {"sessions": [], "nextCursor": None})


def test_managed_execution_first_request_reaches_execution_host_and_keeps_allowlist(host):
    built = []
    def factory(executable):
        built.append(True)
        return ExecutionConnection(executable, start=lambda ignored: host, initialize=False)
    managed = ManagedMuseExecution("fake", factory=factory)
    try:
        assert managed.drain_events() == [] and not built
        assert managed.request("session/start", {"cwd": "synthetic"}) == {
            "sessions": [], "nextCursor": None}
        assert built == [True]
        for method in ("session/read", "session/resume", "approval/listPending",
                       "userInput/answer", "userInput/cancel", "permissionProfile/list",
                       "turn/steer", "session/setModel", "session/setReasoningEffort",
                       "skill/list", "session/sideChat", "session/setPermissionProfile",
                       "session/fork", "session/setApprovalMode", "task/list", "task/stop"):
            managed.request(method, {})
        # admission/* belongs to peerMessaging, which this app never asks for.
        for method in ("session/rewind", "task/stopAll", "settings/writeValue", "admission/listPending",
                       "admission/decide", "approval/listpending", "userinput/answer",
                       "permissionprofile/list"):
            with pytest.raises(MuseHistoryError):
                managed.request(method, {})
        assert not managed.closed
    finally:
        managed.close()


@pytest.mark.parametrize("connection_class,method", [
    (HistoryConnection, "session/read"), (ExecutionConnection, "turn/start")])
def test_request_rejection_is_redacted_and_keeps_host_usable(host, connection_class, method):
    def reject(message):
        return json.dumps({"jsonrpc": "2.0", "id": message["id"],
            "error": {"code": -32602, "message": "PRIVATE TOKEN",
                      "data": {"private": "PRIVATE PATH"}}}).encode() + b"\n"
    host.answer = reject
    with connection_class("fake", start=lambda exe: host) as connection:
        with pytest.raises(MuseHistoryError) as error:
            connection.request(method, {})
        assert "PRIVATE" not in str(error.value)
        assert error.value.code == -32602
        assert not connection.closed and not host.closed
        host.answer = lambda message: {"ok": True}
        assert connection.request(method, {}) == {"ok": True}


@pytest.mark.parametrize("managed", [False, True])
def test_execution_keeps_requests_once_in_wire_order_and_no_notifications(host, managed):
    """Polls read Muse's whole state, so a notification is never acted on and
    none is kept; Muse's requests to the app are, in the order they came."""
    first = {"jsonrpc": "2.0", "id": "server-1", "method": "approval/request", "params": {"n": 1}}
    second = {"jsonrpc": "2.0", "id": "server-2", "method": "userInput/request", "params": {"n": 2}}
    events = [
        {"jsonrpc": "2.0", "method": "turn/started", "params": {"sessionId": "synthetic"}},
        first,
        {"jsonrpc": "2.0", "method": "view/changed", "params": {"revision": 2}},
        second,
    ]
    def answer(message):
        if message["method"] == "turn/start":
            for event in events:
                host.stdout.lines.put(json.dumps(event).encode() + b"\n")
        return {}
    host.answer = answer
    factory = lambda exe: ExecutionConnection(exe, start=lambda ignored: host, initialize=False)
    connection = ManagedMuseExecution("fake", factory=factory) if managed else factory("fake")
    if not managed:
        connection.initialize()
    try:
        connection.request("turn/start", {})
        connection.request("usage/read", {})  # answered after all four arrive
        assert connection.drain_events() == [first, second]
        assert connection.drain_events() == []
        assert not connection.closed
    finally:
        connection.close()


@pytest.mark.parametrize("bounds", [{"event_limit": 1}, {"event_bytes_limit": 128}])
def test_a_flood_of_streamed_output_never_fails_the_connection(host, bounds):
    """A test suite's output once streamed as hundreds of item/delta between
    polls; the queue overflowed and the host, the turn running in it and its
    permission request were lost (#701). Notifications aren't kept now."""
    def answer(message):
        for _ in range(500):
            event = {"jsonrpc": "2.0", "method": "item/delta",
                     "params": {"field": "output", "delta": "x" * 128}}
            host.stdout.lines.put(json.dumps(event).encode() + b"\n")
        return {}
    host.answer = answer
    with ExecutionConnection("fake", start=lambda exe: host, **bounds) as connection:
        connection.request("turn/start", {})
        connection.request("usage/read", {})
        assert connection.drain_events() == [] and not connection.closed


@pytest.mark.parametrize("bounds", [{"event_limit": 1}, {"event_bytes_limit": 300}])
def test_request_overflow_fails_instead_of_losing_a_question(host, bounds):
    def answer(message):
        for n in range(2):
            event = {"jsonrpc": "2.0", "id": f"server-{n}", "method": "approval/request",
                     "params": {"text": "x" * 128}}
            host.stdout.lines.put(json.dumps(event).encode() + b"\n")
        return {}
    host.answer = answer
    with ExecutionConnection("fake", start=lambda exe: host, **bounds) as connection:
        with pytest.raises(MuseHistoryError):
            connection.request("turn/start", {})
        assert connection.closed
        with pytest.raises(MuseHistoryError):
            connection.drain_events()


def test_execution_declines_streamed_deltas_and_history_asks_nothing_new(host):
    with ExecutionConnection("fake", start=lambda exe: host) as connection:
        assert connection.request("usage/read", {}) is not None
    sent = host.messages[0]["params"]["capabilities"]
    assert sent["optOutNotificationMethods"] == ["item/delta"]


def test_history_ignores_notifications_without_enabling_execution(host):
    def answer(message):
        for _ in range(4):
            host.stdout.lines.put(b'{"jsonrpc":"2.0","method":"turn/started","params":{}}\n')
        return {"sessions": [], "nextCursor": None}
    host.answer = answer
    with HistoryConnection("fake", start=lambda exe: host, event_limit=1) as connection:
        assert connection.request("session/list", {})["sessions"] == []
        assert not connection.closed
        with pytest.raises(MuseHistoryError, match="read-only"):
            connection.request("turn/start", {})


@pytest.mark.parametrize("managed", [False, True])
def test_execution_receipts_only_record_presented_server_requests_without_approving(host, managed):
    request = {"jsonrpc": "2.0", "id": "server-1", "method": "approval/request",
               "params": {"requestId": "synthetic", "summary": "Synthetic request"}}
    original_write = host.write
    receipts = []
    def write(data):
        message = json.loads(data)
        if "method" not in message:
            receipts.append(message)
        else:
            original_write(data)
    host.write = write
    def answer(message):
        if message["method"] == "turn/start":
            host.stdout.lines.put(json.dumps(request).encode() + b"\n")
        return {}
    host.answer = answer
    factory = lambda exe: ExecutionConnection(exe, start=lambda ignored: host, initialize=False)
    connection = ManagedMuseExecution("fake", factory=factory) if managed else factory("fake")
    if not managed:
        connection.initialize()
    try:
        connection.request("turn/start", {})
        assert host.messages[0]["params"]["capabilities"]["userInputDialogs"] is True
        assert connection.drain_events() == [request]
        assert not receipts
        connection.respond_receipt("server-1")
        connection.request("usage/read", {})
        assert receipts == [{"jsonrpc": "2.0", "id": "server-1", "result": {}}]
        assert "approval/decide" not in [message["method"] for message in host.messages]
        with pytest.raises(MuseHistoryError):
            connection.respond_receipt("server-1")
        with pytest.raises(MuseHistoryError):
            connection.respond_receipt("unknown")
        assert not connection.closed
    finally:
        connection.close()


def test_unreceipted_requests_remain_bounded_after_events_are_drained(host):
    def answer(message):
        event = {"jsonrpc": "2.0", "id": f"server-{message['id']}",
                 "method": "approval/request", "params": {}}
        host.stdout.lines.put(json.dumps(event).encode() + b"\n")
        return {}
    host.answer = answer
    with ExecutionConnection("fake", start=lambda exe: host, event_limit=1) as connection:
        connection.request("turn/start", {})
        assert len(connection.drain_events()) == 1
        with pytest.raises(MuseHistoryError):
            connection.request("usage/read", {})
        assert connection.closed


@pytest.mark.parametrize("event", [
    {"method": "approval/request", "id": True, "params": {}},
    {"method": "approval/request", "id": "x" * 8193, "params": {}},
    {"method": "tool/run", "id": "unknown", "params": {}},
    {"method": "admission/request", "id": "peer-1", "params": {}},
    {"method": "view/changed", "params": []},
])
def test_unusable_execution_events_fail_without_exposing_private_data(host, event):
    def answer(message):
        host.stdout.lines.put(json.dumps({"jsonrpc": "2.0", **event}).encode() + b"\n")
        return {}
    host.answer = answer
    with ExecutionConnection("fake", start=lambda exe: host) as connection:
        with pytest.raises(MuseHistoryError):
            connection.request("turn/start", {})
        assert connection.closed


def test_history_handshake_and_allowlist_leave_account_and_execution_unavailable(host):
    with HistoryConnection("fake", start=lambda exe: host) as connection:
        assert connection.request("session/list", {"limit": 200})["sessions"] == []
        connection.request("session/read", {"sessionId": "synthetic", "excludeItems": False})
        for method in ("account/read", "account/loginStart", "session/start", "session/resume",
                       "session/fork", "turn/start", "approval/decide", "view/subscribe"):
            with pytest.raises(MuseHistoryError, match="read-only"):
                connection.request(method, {})
    assert [m["method"] for m in host.messages] == [
        "initialize", "initialized", "session/list", "session/read"]
    assert host.closed


NEW_SCHEMA = ("sha256:283b1b125dcc9172928fb5d6711a92a353796d030a7c186988ed527a13cf07f0",
              "sha256:c2f9255c3e027d8c167a64f638429655f2121d5b28d7b5f6b5da437a99dbe57a")
SCHEMA_1_4_24 = ("sha256:b7fb9137267aa35c92424df37a5ac56287006a0e546b197bf0930d593fecbdb1",
                 "sha256:a9f08f8960b531bf661b54c4a48f8fc3968f7af508a70e16c821ee553e06a8ed")


@pytest.mark.parametrize("pair", [(SCHEMA_FINGERPRINT, EXPERIMENTAL_FINGERPRINT), NEW_SCHEMA, SCHEMA_1_4_24])
def test_verified_history_schema_pairs_keep_read_only_boundary(host, monkeypatch, pair):
    monkeypatch.setitem(HANDSHAKE, "schema", {
        "version": 1, "fingerprint": pair[0], "experimentalFingerprint": pair[1]})
    with HistoryConnection("fake", start=lambda exe: host) as connection:
        assert connection.request("session/list", {"limit": 200}) == {"sessions": [], "nextCursor": None}
        for method in ("session/start", "session/resume", "turn/start", "account/loginStart"):
            with pytest.raises(MuseHistoryError, match="read-only"):
                connection.request(method, {})
    assert host.closed


@pytest.mark.parametrize("pair", [
    (SCHEMA_FINGERPRINT, NEW_SCHEMA[1]), (NEW_SCHEMA[0], EXPERIMENTAL_FINGERPRINT),
    (SCHEMA_1_4_24[0], NEW_SCHEMA[1]), (NEW_SCHEMA[0], SCHEMA_1_4_24[1]),
    (["not-a-fingerprint"], EXPERIMENTAL_FINGERPRINT),
    (SCHEMA_FINGERPRINT, {"not": "a fingerprint"})])
def test_mixed_or_malformed_schema_pairs_are_rejected_safely(host, monkeypatch, pair):
    monkeypatch.setitem(HANDSHAKE, "schema", {
        "version": 1, "fingerprint": pair[0], "experimentalFingerprint": pair[1]})
    with pytest.raises(MuseHistoryError, match="compatible"):
        HistoryConnection("fake", start=lambda exe: host)
    assert host.closed and len(host.messages) == 1


@pytest.mark.parametrize("field", ["version", "fingerprint", "experimentalFingerprint"])
def test_schema_mismatch_fails_before_reading_history(host, monkeypatch, field):
    monkeypatch.setitem(HANDSHAKE["schema"], field, "incompatible")
    with pytest.raises(MuseHistoryError, match="compatible"):
        HistoryConnection("fake", start=lambda exe: host)
    assert host.closed and len(host.messages) == 1


@pytest.mark.parametrize("answer", [b'no json\n', b'',
    b'{"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"PRIVATE"}}\n',
    b'{"jsonrpc":"2.0","id":"wrong","result":{}}\n',
    b'{"jsonrpc":"2.0","method":"tool/run","id":99,"params":{}}\n',
    b'x' * 1025])
def test_unreadable_response_is_bounded_redacted_and_closes(host, answer):
    host.answer = lambda message: answer
    with pytest.raises(MuseHistoryError) as error:
        with HistoryConnection("fake", start=lambda exe: host, frame_limit=1024) as connection:
            connection.request("session/list", {})
    assert "PRIVATE" not in str(error.value) and host.closed


def test_timeout_closes_transport(host):
    host.answer = lambda message: None
    with pytest.raises(MuseHistoryError, match="timed out"):
        with HistoryConnection("fake", start=lambda exe: host, timeout=.02) as connection:
            connection.request("session/list", {})
    assert host.closed


def test_close_interrupts_request(host):
    host.answer = lambda message: None
    connection = HistoryConnection("fake", start=lambda exe: host)
    errors = []
    def request():
        try:
            connection.request("session/list", {})
        except MuseHistoryError as error:
            errors.append(error)
    worker = threading.Thread(target=request)
    worker.start()
    deadline = time.monotonic() + 2
    while len(host.messages) < 3 and time.monotonic() < deadline:
        time.sleep(.001)
    connection.close()
    worker.join(2)
    assert not worker.is_alive() and errors and host.closed


def test_managed_connection_is_lazy_reconnects_and_never_replays(host):
    built = []
    def factory(executable):
        connection = HistoryConnection("fake", start=lambda exe: host, initialize=False)
        built.append(connection)
        return connection
    managed = ManagedMuseConnection("fake", factory=factory)
    assert not built
    managed.request("session/list", {})
    assert len(built) == 1
    managed.close()
    with pytest.raises(MuseHistoryError):
        managed.request("session/list", {})
    assert len(built) == 1 and host.closed


def test_managed_close_cleans_up_late_factory_result(host):
    started, finish = threading.Event(), threading.Event()
    def factory(executable):
        started.set()
        assert finish.wait(2)
        return HistoryConnection("fake", start=lambda exe: host, initialize=False)
    managed = ManagedMuseConnection("fake", factory=factory)
    errors = []
    def request():
        try:
            managed.request("session/list", {})
        except MuseHistoryError as error:
            errors.append(error)
    worker = threading.Thread(target=request)
    worker.start()
    assert started.wait(2)
    managed.close()
    finish.set()
    worker.join(3)
    assert not worker.is_alive() and errors and host.closed


def test_cleanup_failure_is_redacted_and_can_be_retried(host):
    connection = HistoryConnection("fake", start=lambda exe: host)
    original = host.close
    attempts = []
    def fail_once():
        original()
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("PRIVATE PATH AND TOKEN")
    host.close = fail_once
    with pytest.raises(MuseHistoryError) as error:
        connection.close()
    assert "PRIVATE" not in str(error.value)
    connection.close()
    assert len(attempts) == 2


def test_managed_reconnects_on_next_operation_without_replaying_failed_read(host):
    replacement = Host(lambda message: {"sessions": [], "nextCursor": None})
    hosts = iter([host, replacement])
    managed = ManagedMuseConnection("fake", factory=lambda exe:
        HistoryConnection(exe, start=lambda ignored: next(hosts), initialize=False))
    host.answer = lambda message: b''
    with pytest.raises(MuseHistoryError):
        managed.request("session/list", {})
    assert len(host.messages) == 3 and not replacement.messages
    assert managed.request("session/list", {}) == {"sessions": [], "nextCursor": None}
    managed.close()
    assert host.closed and replacement.closed


def test_discovery_refreshes_host_catalog_after_external_session_creation(host):
    from copy import deepcopy
    from test_muse_provider import session
    from thechatplace.providers.muse import MuseProvider

    saved_sessions, built = [], []
    def factory(executable):
        catalog = deepcopy(saved_sessions)
        child = Host(lambda message: {"sessions": catalog, "nextCursor": None})
        built.append(child)
        return HistoryConnection(executable, start=lambda ignored: child, initialize=False)
    provider = MuseProvider(connection=ManagedMuseConnection("fake", factory=factory))
    try:
        assert provider.collect().sessions == []
        saved_sessions.append(session())
        assert len(provider.collect().sessions) == 1
        assert len(built) == 2 and built[0].closed and not built[1].closed
    finally:
        provider.close()
    assert built[-1].closed


def test_catalog_refresh_waits_for_active_read_instead_of_interrupting_it(host):
    host.answer = lambda message: None
    managed = ManagedMuseConnection("fake", factory=lambda exe:
        HistoryConnection(exe, start=lambda ignored: host, initialize=False))
    results, errors = [], []
    refreshed = threading.Event()
    def read():
        try:
            results.append(managed.request("session/read", {}))
        except Exception as error:
            errors.append(error)
    def refresh():
        try:
            managed.refresh_catalog()
        except Exception as error:
            errors.append(error)
        finally:
            refreshed.set()
    reader = threading.Thread(target=read)
    reader.start()
    deadline = time.monotonic() + 2
    while len(host.messages) < 3 and time.monotonic() < deadline:
        time.sleep(.001)
    worker = threading.Thread(target=refresh)
    worker.start()
    try:
        assert not refreshed.wait(.05) and not host.closed
    finally:
        host.stdout.lines.put(b'{"jsonrpc":"2.0","id":2,"result":{}}\n')
        reader.join(2)
        worker.join(2)
        managed.close()
    assert results == [{}] and not errors and refreshed.is_set()
    assert not reader.is_alive() and not worker.is_alive() and host.closed


def test_close_interrupts_active_read_and_queued_catalog_refresh(host):
    host.answer = lambda message: None
    managed = ManagedMuseConnection("fake", factory=lambda exe:
        HistoryConnection(exe, start=lambda ignored: host, initialize=False))
    errors = []
    def run(action):
        try:
            action()
        except MuseHistoryError as error:
            errors.append(error)
    reader = threading.Thread(target=lambda: run(lambda: managed.request("session/read", {})))
    reader.start()
    deadline = time.monotonic() + 2
    while len(host.messages) < 3 and time.monotonic() < deadline:
        time.sleep(.001)
    refresh = threading.Thread(target=lambda: run(managed.refresh_catalog))
    refresh.start()
    managed.close()
    reader.join(2)
    refresh.join(2)
    assert not reader.is_alive() and not refresh.is_alive()
    assert len(errors) == 2 and host.closed


def test_history_provider_through_real_synthetic_subprocess_pipes(host, tmp_path):
    import subprocess
    import sys
    from test_muse_provider import history, item, session, NATIVE_ID
    from thechatplace.providers.muse import MuseProvider

    result = history([item("userMessage", "u", text="Synthetic question"),
                      item("agentMessage", "a", text="Synthetic answer")])
    script = tmp_path / "synthetic_muse.py"
    script.write_text(
        "import json,sys\n"
        f"handshake={HANDSHAKE!r}\n"
        f"catalog={{'sessions': [{session()!r}], 'nextCursor': None}}\n"
        f"history={result!r}\n"
        "initialized=False\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " method=request['method']\n"
        " if method=='initialized':\n"
        "  initialized=True\n"
        "  continue\n"
        " if method=='initialize': result=handshake\n"
        " elif initialized and method=='session/list': result=catalog\n"
        " elif initialized and method=='session/read': result=history\n"
        " else: raise RuntimeError('unapproved method')\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n",
        encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    class Owner:
        stdin, stdout = process.stdin, process.stdout
        def close(self):
            process.kill()
            process.wait(timeout=3)
            self.stdin.close()
            self.stdout.close()
    try:
        with HistoryConnection("synthetic", start=lambda exe: Owner()) as connection:
            provider = MuseProvider(connection=connection)
            info = provider.collect().sessions[0]
            assert info.identity == ("muse", NATIVE_ID)
            assert provider.last_reply(info) == "Synthetic answer"
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
