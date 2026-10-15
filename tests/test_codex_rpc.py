from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
from io import BytesIO
from types import SimpleNamespace

import pytest

from thechatplace import platform_paths
from thechatplace.providers.codex_rpc import CodexRpcError, ReadOnlyCodexConnection
from thechatplace.providers.codex_connection import ManagedCodexConnection


@pytest.fixture
def server():
    owned = []

    def start(executable):
        tree = platform_paths.ProcessTree()
        argv = [sys.executable, "-u", str(Path(__file__).with_name("fake_codex_server.py"))]
        if executable:
            argv.append(executable)
        process = subprocess.Popen(argv,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, **tree.popen_kwargs())
        tree.attach(process)
        child = platform_paths.CodexServerProcess(process, tree)
        owned.append(child)
        return child

    yield start, owned
    for child in owned:
        child.close()


def test_handshake_partial_utf8_lines_and_context_cleanup(server):
    start, owned = server
    with ReadOnlyCodexConnection(start=start) as connection:
        assert connection.request("thread/read", {"threadId": "café\u2028chat"}) == {
            "echo": {"threadId": "café\u2028chat"}}
    assert owned[0].process.poll() is not None
    assert owned[0].stdin.closed and owned[0].stdout.closed
    connection.close()
    with pytest.raises(CodexRpcError, match="closed"):
        connection.request("thread/list", {})


@pytest.mark.parametrize('scenario', ['owned-turn-approval', 'owned-turn-hang', 'owned-turn-unsupported', 'owned-turn-command-output', 'owned-turn-unknown'])
def test_runtime_midturn_request_hang_and_protocol_failure_use_real_stdio(server, scenario, tmp_path, monkeypatch):
    import time
    from thechatplace.providers.codex_execution import CodexExecutionConnection
    from thechatplace.providers.codex_runtime import CodexChatRuntime
    start, _ = server
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: tmp_path / 'errors.log')
    connection = CodexExecutionConnection(scenario, start=start, initialize=False)
    service = CodexChatRuntime(factory=lambda exe: connection)
    def wait(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        assert predicate()
    try:
        record = service.create('/workspace', 'Synthetic owned chat')
        service.send(record, 'Synthetic prompt')
        if scenario in ('owned-turn-unsupported', 'owned-turn-unknown'):
            wait(lambda: service.snapshot('owned').outcome == 'completed')
            state = service.snapshot('owned')
            kind = 'command approval' if scenario == 'owned-turn-unsupported' else 'request'
            assert f'unsupported Codex {kind} was declined' in state.notice
            assert not connection.closed and not state.problem
            return
        if scenario in ('owned-turn-approval', 'owned-turn-command-output'):
            wait(lambda: service.snapshot('owned').requests)
            request = service.snapshot('owned').requests[0]
            assert service.snapshot('owned').state == 'needs you'
            service.prepare(record)
            service.answer(record, request, request.approval_answer('decline'))
            wait(lambda: service.snapshot('owned').outcome == 'completed')
        else:
            for _ in range(5):
                service.prepare(record)
                assert service.snapshot('owned').outcome == 'inProgress'
            assert service.send(record, 'Queued synthetic prompt')
            assert service.stop(record) == ('Queued synthetic prompt',)
            wait(lambda: service.snapshot('owned').outcome == 'interrupted')
            assert not service.snapshot('owned').queued
    finally:
        service.close()


def test_interleaved_notifications_and_reverse_responses_route_by_id(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start) as connection, ThreadPoolExecutor(2) as pool:
        first = pool.submit(connection.request, "thread/read", {"mode": "pair", "threadId": "one"})
        second = pool.submit(connection.request, "thread/read", {"mode": "pair", "threadId": "two"})
        assert first.result(timeout=5)["echo"]["threadId"] == "one"
        assert second.result(timeout=5)["echo"]["threadId"] == "two"


@pytest.mark.parametrize("mode", ["exit", "malformed", "nested", "server-request", "unknown-id", "bad-result"])
def test_protocol_failures_close_process_without_exposing_private_payloads(server, mode):
    start, owned = server
    connection = ReadOnlyCodexConnection(start=start)
    with pytest.raises(CodexRpcError) as error:
        connection.request("thread/read", {"mode": mode})
    assert "private" not in str(error.value) and "approve" not in str(error.value)
    connection.close()
    assert owned[0].process.poll() is not None
    connection._reader.join(timeout=5)
    assert not connection._reader.is_alive()
    assert owned[0].process.poll() is not None


def test_rpc_error_is_redacted_and_other_requests_remain_usable(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start) as connection:
        with pytest.raises(CodexRpcError, match="^Codex request failed$"):
            connection.request("thread/read", {"mode": "error"})
        assert connection.request("thread/list", {}) == {"echo": {}}


def test_timeout_fails_all_waiters_and_does_not_replay_requests(server):
    start, owned = server
    with ReadOnlyCodexConnection(start=start, timeout=1) as connection, ThreadPoolExecutor(2) as pool:
        first = pool.submit(connection.request, "thread/read", {"mode": "hang", "threadId": "one"})
        second = pool.submit(connection.request, "thread/read", {"mode": "hang", "threadId": "two"})
        for future in (first, second):
            with pytest.raises(CodexRpcError, match="timed out"):
                future.result(timeout=5)
        assert len(owned) == 1 and owned[0].process.poll() is not None
        with pytest.raises(CodexRpcError, match="closed"):
            connection.request("thread/list", {})


def test_shutdown_unblocks_pending_read_without_waiting_for_timeout(server):
    start, owned = server
    connection = ReadOnlyCodexConnection(start=start)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(connection.request, "thread/read", {"mode": "hang"})
        connection.close()
        with pytest.raises(CodexRpcError, match="closed"):
            future.result(timeout=5)
    connection._reader.join(timeout=5)
    connection._writer.join(timeout=5)
    assert not connection._reader.is_alive() and not connection._writer.is_alive()
    assert owned[0].process.poll() is not None


def test_read_only_methods_and_request_limits_fail_without_poisoning_connection(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start) as connection:
        for method in ("turn/start", "thread/resume", "thread/fork", "initialize", "account/login/start"):
            with pytest.raises(ValueError, match="read-only"):
                connection.request(method, {})
        with pytest.raises(ValueError, match="too large"):
            connection.request("thread/read", {"text": "x" * connection.MAX_REQUEST})
        assert connection.request("thread/list", {}) == {"echo": {}}


def test_oversize_response_fails_connection(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start) as connection:
        connection.MAX_FRAME = 80
        with pytest.raises(CodexRpcError, match="invalid protocol"):
            connection.request("thread/read", {"text": "x" * 100})


@pytest.mark.parametrize("header,suffix", [(b"#!/usr/bin/node\n", ""),
    (b"#!/bin/sh\n", ""), (b"@echo off", ".cmd"), (b"MZ", ".bat"), (b"text", "")])
def test_cli_discovery_rejects_scripts(tmp_path, header, suffix):
    path = tmp_path / ("codex" + suffix)
    path.write_bytes(header)
    path.chmod(0o755)
    assert platform_paths.find_codex(which=lambda name: str(path), native_candidates=[]) is None
    with pytest.raises(OSError, match="native Codex"):
        platform_paths.start_codex_server(str(path))


def test_cli_discovery_finds_native_candidate_after_rejected_shim(tmp_path):
    shim, native = tmp_path / "codex.cmd", tmp_path / "codex.exe"
    shim.write_bytes(b"@echo off")
    native.write_bytes(b"MZ native fixture")
    native.chmod(0o755)
    assert platform_paths.find_codex(which=lambda name: str(shim), native_candidates=[native]) == str(native)


def test_failed_handshake_closes_child_before_constructor_raises(server):
    start, owned = server
    with pytest.raises(CodexRpcError, match="request failed"):
        ReadOnlyCodexConnection("bad-init", start=start)
    assert owned[0].process.poll() is not None
    assert owned[0].stdin.closed and owned[0].stdout.closed


def test_pending_request_limit_preserves_existing_request_until_shutdown(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start) as connection, ThreadPoolExecutor(1) as pool:
        connection.MAX_PENDING = 1
        queued = threading.Event()
        encode = connection._encode

        def encoded(message):
            result = encode(message)
            if message.get("params", {}).get("mode") == "hang":
                queued.set()
            return result

        connection._encode = encoded
        waiting = pool.submit(connection.request, "thread/read", {"mode": "hang"})
        assert queued.wait(5)
        with pytest.raises(CodexRpcError, match="Too many pending"):
            connection.request("thread/list", {})
        connection.close()
        with pytest.raises(CodexRpcError, match="closed"):
            waiting.result(timeout=5)


def test_concurrent_close_waits_for_existing_cleanup(server):
    start, owned = server
    connection = ReadOnlyCodexConnection(start=start)
    child = owned[0]
    close = child.close
    entered, release = threading.Event(), threading.Event()

    def delayed_close():
        entered.set()
        assert release.wait(5)
        close()

    child.close = delayed_close
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(connection.close)
        assert entered.wait(5)
        second = pool.submit(connection.close)
        try:
            assert not first.done() and not second.done()
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert child.process.poll() is not None


@pytest.mark.parametrize("attach_fails", [False, True])
def test_native_launch_uses_stdio_process_tree_and_cleans_up_attach_failure(tmp_path, monkeypatch,
                                                                          attach_fails):
    executable = tmp_path / "native codex.exe"
    executable.write_bytes(b"MZ native fixture")
    executable.chmod(0o755)
    calls = []
    process = SimpleNamespace(stdin=BytesIO(), stdout=BytesIO(),
                              wait=lambda timeout: calls.append(("wait", timeout)),
                              kill=lambda: calls.append("process kill"))

    class Tree:
        def popen_kwargs(self):
            return {"creationflags": 123}

        def attach(self, child):
            assert child is process
            calls.append("attach")
            if attach_fails:
                raise OSError("attach failed")

        def kill(self):
            calls.append("tree kill")

        def close(self):
            calls.append("tree close")

    def popen(argv, **kwargs):
        assert argv == [str(executable), "app-server", "--listen", "stdio://"]
        assert kwargs == dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, creationflags=123,
                              env=platform_paths.codex_child_environment())
        return process

    monkeypatch.setattr(platform_paths, "ProcessTree", Tree)
    monkeypatch.setattr(subprocess, "Popen", popen)
    if attach_fails:
        with pytest.raises(OSError, match="attach failed"):
            platform_paths.start_codex_server(str(executable))
    else:
        child = platform_paths.start_codex_server(str(executable))
        child.close()
        child.close()
    assert calls == ["attach", "tree kill", ("wait", 5), "tree close"]
    assert process.stdin.closed and process.stdout.closed


def test_deferred_initialization_rejects_reads_until_single_handshake(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start, initialize=False) as connection:
        with pytest.raises(CodexRpcError, match="not initialized"):
            connection.request("thread/list", {})
        connection.initialize()
        connection.initialize()
        assert connection.request("thread/list", {}) == {"echo": {}}


def test_managed_lazy_start_and_concurrent_requests_share_one_process(server):
    start, owned = server
    manager = ManagedCodexConnection(factory=lambda executable:
                                    ReadOnlyCodexConnection(start=start, initialize=False))
    assert owned == []
    try:
        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(manager.request, "thread/read", {"mode": "pair", "threadId": "one"})
            two = pool.submit(manager.request, "thread/read", {"mode": "pair", "threadId": "two"})
            assert one.result(timeout=5)["echo"]["threadId"] == "one"
            assert two.result(timeout=5)["echo"]["threadId"] == "two"
        assert len(owned) == 1
    finally:
        manager.close()
    assert owned[0].process.poll() is not None


def test_managed_reconnect_is_a_later_operation_not_a_replay(server):
    start, owned = server
    manager = ManagedCodexConnection(factory=lambda executable:
                                    ReadOnlyCodexConnection(start=start, initialize=False))
    try:
        with pytest.raises(CodexRpcError, match="closed"):
            manager.request("thread/read", {"mode": "exit"})
        assert len(owned) == 1
        assert manager.request("thread/list", {}) == {"echo": {}}
        assert len(owned) == 2 and owned[0].process.poll() is not None
        with pytest.raises(CodexRpcError, match="request failed"):
            manager.request("thread/read", {"mode": "error"})
        assert manager.request("thread/list", {}) == {"echo": {}}
        assert len(owned) == 2  # an ordinary RPC error is not a disconnect
    finally:
        manager.close()


def test_managed_close_cancels_handshake_without_waiting_for_rpc_timeout(server):
    start, owned = server
    handshake = threading.Event()

    def factory(executable):
        connection = ReadOnlyCodexConnection("hang-init", start=start, initialize=False)
        encode = connection._encode

        def encoded(message):
            result = encode(message)
            if message.get("method") == "initialize":
                handshake.set()
            return result

        connection._encode = encoded
        return connection

    manager = ManagedCodexConnection(factory=factory)
    with ThreadPoolExecutor(1) as pool:
        read = pool.submit(manager.request, "thread/list", {})
        assert handshake.wait(5)
        manager.close()
        with pytest.raises(CodexRpcError, match="closed"):
            read.result(timeout=5)
    assert owned[0].process.poll() is not None


def test_close_waits_for_late_factory_and_disposes_result_before_return(server):
    start, owned = server
    entered, release = threading.Event(), threading.Event()

    def factory(executable):
        entered.set()
        assert release.wait(5)
        return ReadOnlyCodexConnection(start=start, initialize=False)

    manager = ManagedCodexConnection(factory=factory)
    with ThreadPoolExecutor(2) as pool:
        read = pool.submit(manager.request, "thread/list", {})
        assert entered.wait(5)
        close = pool.submit(manager.close)
        try:
            with manager._condition:
                assert manager._condition.wait_for(lambda: manager._closed, timeout=5)
            assert not close.done()
            with pytest.raises(CodexRpcError, match="backend closed"):
                manager.request("thread/list", {})
        finally:
            release.set()
        close.result(timeout=5)
        with pytest.raises(CodexRpcError, match="backend closed"):
            read.result(timeout=5)
    assert len(owned) == 1 and owned[0].process.poll() is not None


def test_close_before_first_read_and_invalid_methods_never_start_a_process(server):
    start, owned = server
    manager = ManagedCodexConnection(factory=lambda executable:
                                    ReadOnlyCodexConnection(start=start, initialize=False))
    with pytest.raises(ValueError, match="read-only"):
        manager.request("turn/start", {})
    with pytest.raises(ValueError, match="parameters"):
        manager.request("thread/list", [])
    assert owned == []
    manager.close()
    manager.close()
    with pytest.raises(CodexRpcError, match="backend closed"):
        manager.request("thread/list", {})
    assert owned == []


def test_late_factory_cleanup_failure_is_retained_and_reported_to_shutdown(server):
    start, _ = server
    entered, release = threading.Event(), threading.Event()

    def factory(executable):
        entered.set()
        assert release.wait(5)
        connection = ReadOnlyCodexConnection(start=start, initialize=False)
        close = connection.close

        def failed_close():
            close()
            raise RuntimeError("private cleanup details")

        connection.close = failed_close
        return connection

    manager = ManagedCodexConnection(factory=factory)
    with ThreadPoolExecutor(2) as pool:
        read = pool.submit(manager.request, "thread/list", {})
        assert entered.wait(5)
        close = pool.submit(manager.close)
        try:
            with manager._condition:
                assert manager._condition.wait_for(lambda: manager._closed, timeout=5)
        finally:
            release.set()
        for future in (read, close):
            with pytest.raises(CodexRpcError, match="^Codex backend cleanup failed$"):
                future.result(timeout=5)
    assert manager._connection is not None  # retain ownership for diagnosis
    with pytest.raises(CodexRpcError, match="^Codex backend cleanup failed$"):
        manager.close()


def test_failed_previous_cleanup_retains_ownership_and_prevents_reconnect():
    created, closed = [], []

    def factory(executable):
        created.append(True)
        pytest.fail("Replacement started before previous cleanup succeeded")

    def failed_close():
        closed.append(True)
        raise RuntimeError("private old-process cleanup details")

    previous = SimpleNamespace(closed=True, close=failed_close)
    manager = ManagedCodexConnection(factory=factory)
    manager._connection = previous
    for _ in range(2):
        with pytest.raises(CodexRpcError, match="^Codex backend cleanup failed$"):
            manager.request("thread/list", {})
        assert manager._connection is previous
    with pytest.raises(CodexRpcError, match="^Codex backend cleanup failed$"):
        manager.close()
    assert not created and len(closed) == 3


def test_reconnect_can_proceed_after_previous_cleanup_eventually_succeeds():
    attempts, created = [], []

    def previous_close():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("private cleanup failure")

    replacement = SimpleNamespace(closed=False, initialize=lambda: None,
                                  request=lambda method, params: {"ok": True}, close=lambda: None)

    def factory(executable):
        created.append(True)
        return replacement

    manager = ManagedCodexConnection(factory=factory)
    manager._connection = SimpleNamespace(closed=True, close=previous_close)
    with pytest.raises(CodexRpcError, match="cleanup failed"):
        manager.request("thread/list", {})
    assert not created
    assert manager.request("thread/list", {}) == {"ok": True}
    assert len(created) == 1 and len(attempts) == 2
    manager.close()


def test_codex_environment_removes_billing_overrides_without_mutating_parent():
    overrides = {name: "synthetic" for name in platform_paths.CODEX_BILLING_VARS}
    overrides.update({"OPENAI_API_KEY": "synthetic", "openai_base_url": "synthetic",
                      "OPENAI_AUTH_TOKEN": "synthetic", "AZURE_OPENAI_ENDPOINT": "synthetic",
                      "OPENAI_FUTURE_OVERRIDE": "synthetic"})
    keep = {"PATH": "native", "HOME": "login", "CODEX_HOME": "profile", "LANG": "en_US.UTF-8"}
    parent = dict(overrides, **keep)
    assert platform_paths.codex_child_environment(parent) == keep
    assert parent == dict(overrides, **keep)


class FutureTurnConnection(ReadOnlyCodexConnection):
    # Synthetic execution exercises the guard without enabling production turns.
    METHODS = ReadOnlyCodexConnection.METHODS | {"turn/start", "turn/steer"}


@pytest.mark.parametrize("kind", ["apiKey", "missing", "unknown", "chatgptAuthTokens",
                                   "amazonBedrock", "other-provider"])
@pytest.mark.parametrize("method", ["turn/start", "turn/steer"])
def test_future_turn_rejects_non_chatgpt_account_before_sending(server, kind, method):
    start, _ = server
    with FutureTurnConnection("account-" + kind, start=start) as connection:
        with pytest.raises(CodexRpcError, match="ChatGPT sign-in"):
            connection.request(method, {"threadId": "synthetic"})
        # Only initialize and account/read consumed IDs; no turn was written.
        assert connection._next_id == 2


def test_future_turn_checks_account_each_time_on_same_connection(server):
    start, _ = server
    with FutureTurnConnection("account-changed", start=start) as connection:
        assert connection.request("turn/start", {"threadId": "synthetic"})["echo"] == {
            "threadId": "synthetic"}
        with pytest.raises(CodexRpcError, match="ChatGPT sign-in"):
            connection.request("turn/start", {"threadId": "synthetic"})
        assert connection._next_id == 6  # second turn never reaches the wire


@pytest.mark.parametrize("kind", ["provider", "wrong-id", "missing-cwd", "openai_base_url",
    "chatgpt_base_url", "model_provider", "forced_login_method", "builtin-override",
    "malformed-providers", "malformed-config"])
@pytest.mark.parametrize("method", ["turn/start", "turn/steer"])
def test_future_turn_rejects_alternate_or_unverified_route(server, kind, method):
    start, _ = server
    with FutureTurnConnection("account-route-" + kind, start=start) as connection:
        with pytest.raises(CodexRpcError):
            connection.request(method, {"threadId": "synthetic"})
        assert connection._next_id <= 4  # initialize, account, metadata, config; no turn


def test_future_turn_rechecks_configuration_before_each_send(server):
    start, _ = server
    with FutureTurnConnection("account-route-changed", start=start) as connection:
        connection.request("turn/start", {"threadId": "synthetic"})
        with pytest.raises(CodexRpcError, match="billing route"):
            connection.request("turn/start", {"threadId": "synthetic"})
        assert connection._next_id == 8  # changed second route never sends a turn


@pytest.mark.parametrize("thread_id", [None, "", "  ", 123])
def test_future_turn_requires_thread_identity_before_reading_route(server, thread_id):
    start, _ = server
    with FutureTurnConnection("account-chatgpt", start=start) as connection:
        with pytest.raises(CodexRpcError, match="verified thread"):
            connection.request("turn/start", {"threadId": thread_id})
        assert connection._next_id == 2


def test_future_turn_checks_the_requested_working_directory(server):
    start, _ = server
    with FutureTurnConnection("account-route-cwd-sensitive", start=start) as connection:
        connection.request("turn/start", {"threadId": "synthetic"})
        with pytest.raises(CodexRpcError, match="billing route"):
            connection.request("turn/start", {"threadId": "synthetic", "cwd": "/alternate"})
        assert connection._next_id == 8


@pytest.mark.parametrize("cwd", [None, "", "  ", 42])
def test_future_turn_rejects_unverified_working_directory(server, cwd):
    start, _ = server
    with FutureTurnConnection("account-chatgpt", start=start) as connection:
        with pytest.raises(CodexRpcError, match="configuration is unavailable"):
            connection.request("turn/start", {"threadId": "synthetic", "cwd": cwd})
        assert connection._next_id == 3


def test_opt_in_events_preserve_interleaved_chat_ids_and_authoritative_final_text(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start, receive_events=True) as connection:
        connection.request("thread/read", {"mode": "events"})
        events = []
        while (event := connection.pop_event()) is not None:
            events.append(event)
        assert [e.thread_id for e in events] == ["one", "one", "two", "one", "one", "two"]
        assert events[1].text == "Draft" and events[3].text == "Final result"
        assert events[-1].status == "interrupted" and "private" not in repr(events)
        assert connection.pop_event() is None


@pytest.mark.parametrize("mode", ["bad-event", "event-overflow"])
def test_opt_in_stream_disconnects_rather_than_losing_or_misrouting_events(server, mode):
    start, _ = server
    with ReadOnlyCodexConnection(start=start, receive_events=True) as connection:
        with pytest.raises(CodexRpcError):
            connection.request("thread/read", {"mode": mode})
        assert connection.closed
        assert connection._events.qsize() <= connection.MAX_EVENTS
        with pytest.raises(CodexRpcError, match="reconcile"):
            connection.pop_event()


def test_default_reader_keeps_ignoring_notifications_without_enabling_execution(server):
    start, _ = server
    with ReadOnlyCodexConnection(start=start) as connection:
        connection.request("thread/read", {"mode": "event-overflow"})
        assert connection.pop_event() is None and not connection.closed
        with pytest.raises(ValueError):
            connection.request("turn/start", {})


def test_turn_accepts_explicit_canonical_chatgpt_route(server):
    start, _ = server
    with FutureTurnConnection('account-route-canonical-chatgpt', start=start) as connection:
        assert connection.request('turn/start', {'threadId': 'synthetic'})['echo']['threadId'] == 'synthetic'


def request_transport():
    import queue
    connection = ReadOnlyCodexConnection.__new__(ReadOnlyCodexConnection)
    connection._lock = threading.Lock()
    connection._closed = False
    connection._receive_events = True
    connection._receive_requests = True
    connection._server_pending = {}
    connection._events = queue.Queue(maxsize=32)
    connection._token_events = {}
    connection._outgoing = queue.Queue()
    connection._accept_server_request = lambda request: request.thread_id == 'owned'
    connection.request = lambda method, params: {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
    return connection


def approval_message(request_id='approval', thread='owned'):
    return {'id': request_id, 'method': 'item/commandExecution/requestApproval',
            'params': {'threadId': thread, 'turnId': 'turn', 'itemId': 'command',
                       'command': 'synthetic command', 'cwd': '/workspace'}}


def test_unconsumed_command_output_cannot_fill_the_control_event_queue():
    connection = request_transport()
    for _ in range(256):
        connection._receive({'method': 'item/commandExecution/outputDelta', 'params': {
            'threadId': 'owned', 'turnId': 'turn', 'itemId': 'command', 'delta': 'synthetic output'}})
    assert connection._events.empty() and not connection.closed
    connection._receive(approval_message())
    assert connection.pop_event().request_id == 'approval'


def test_server_approval_response_is_validated_and_routed_once_by_rpc_identity():
    import json
    connection = request_transport()
    connection._receive(approval_message())
    request = connection.pop_event()
    with pytest.raises(ValueError):
        connection.respond_request(request, {'decision': 'acceptForSession'})
    assert connection._outgoing.empty()
    connection.respond_request(request, request.approval_answer('accept'))
    assert json.loads(connection._outgoing.get()) == {'id': 'approval', 'result': {'decision': 'accept'}}
    with pytest.raises(CodexRpcError, match='no longer waiting'):
        connection.respond_request(request, {'decision': 'decline'})


def test_same_item_multiple_callbacks_remain_distinct_and_terminal_invalidates_answers():
    connection = request_transport()
    connection._receive(approval_message('first'))
    connection._receive(approval_message('second'))
    one, two = connection.pop_event(), connection.pop_event()
    assert one.item_id == two.item_id and one.request_id != two.request_id
    connection._receive({'method': 'turn/completed', 'params': {
        'threadId': 'owned', 'turn': {'id': 'turn', 'status': 'interrupted'}}})
    with pytest.raises(CodexRpcError, match='no longer waiting'):
        connection.respond_request(one, {'decision': 'accept'})
    assert connection._outgoing.empty()


def test_api_sign_in_cannot_grant_approval_even_for_a_waiting_owned_request():
    connection = request_transport()
    connection._receive(approval_message())
    request = connection.pop_event()
    connection.request = lambda method, params: {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True}
    with pytest.raises(CodexRpcError, match='ChatGPT sign-in'):
        connection.respond_request(request, {'decision': 'accept'})
    assert connection._outgoing.empty()
    connection.respond_request(request, {'decision': 'decline'})


def test_unowned_duplicate_and_overflowing_requests_are_rejected():
    connection = request_transport()
    with pytest.raises(ValueError):
        connection._receive(approval_message(thread='external'))
    connection._receive(approval_message())
    with pytest.raises(ValueError):
        connection._receive(approval_message())
    connection.pop_event()
    for index in range(31):
        connection._receive(approval_message(index))
        connection.pop_event()
    with pytest.raises(ValueError):
        connection._receive(approval_message('overflow'))


@pytest.mark.parametrize('known', [False, True])
def test_unsupported_request_gets_error_without_permission_or_private_payload(known):
    import json
    from thechatplace.providers.codex_requests import CodexRequestDeclined
    connection = request_transport()
    message = approval_message()
    if known:
        message['params']['additionalPermissions'] = {'private': 'credentials'}
    else:
        message['method'] = 'private/new-method'
        message['params'] = {'private': 'credentials'}
    connection._receive(message)
    answer = json.loads(connection._outgoing.get())
    assert answer['id'] == message['id'] and answer['error']['code'] == (-32602 if known else -32601)
    assert 'result' not in answer and 'private' not in json.dumps(answer)
    assert isinstance(connection.pop_event(), CodexRequestDeclined)
    assert not connection.closed and not connection._server_pending
    connection._receive(approval_message('next'))
    assert connection.pop_event().request_id == 'next'


def test_stdio_server_request_and_answer_keep_client_response_ids_independent(server):
    class ApprovalConnection(ReadOnlyCodexConnection):
        def _accept_server_request(self, request):
            return request.thread_id == 'owned'
    start, _ = server
    with ApprovalConnection(start=start, receive_events=True, receive_requests=True) as c:
        c.request('thread/read', {'mode': 'approval-receive'})
        request = c.pop_event()
        assert request.request_id == 'server-approval'
        c.respond_request(request, request.approval_answer('decline'))
        result = c.request('thread/read', {'mode': 'approval-response'})
        assert result['serverAnswer'] == {'id': 'server-approval', 'result': {'decision': 'decline'}}


def test_backend_resolved_request_cannot_be_answered_again():
    connection = request_transport()
    connection._receive(approval_message())
    request = connection.pop_event()
    connection._receive({'method': 'serverRequest/resolved', 'params': {
        'threadId': 'owned', 'requestId': request.request_id}})
    event = connection.pop_event()
    assert event.request_id == request.request_id
    with pytest.raises(CodexRpcError, match='no longer waiting'):
        connection.respond_request(request, {'decision': 'accept'})
    assert connection._outgoing.empty()


def test_shutdown_during_buffered_handshake_write_closes_both_pipes(server):
    start, owned = server
    entered, release = threading.Event(), threading.Event()
    connection = ReadOnlyCodexConnection('hang-init', start=start, initialize=False)
    original = connection._process.stdin
    class PausedWrite:
        def write(self, data):
            result = original.write(data)
            entered.set()
            assert release.wait(5)
            return result
        def flush(self):
            return original.flush()
        def close(self):
            return original.close()
        @property
        def closed(self):
            return original.closed
    connection._process.stdin = PausedWrite()
    with ThreadPoolExecutor(1) as pool:
        initializing = pool.submit(connection.initialize)
        assert entered.wait(5)
        try:
            connection.close()
        finally:
            release.set()
        with pytest.raises(CodexRpcError):
            initializing.result(timeout=5)
    assert owned[0].process.poll() is not None
    assert connection._process.stdin.closed and connection._process.stdout.closed


@pytest.mark.parametrize('platform,number,closed,dead,tolerated', [
    ('win32', 22, True, True, True), ('darwin', 32, True, True, True),
    ('darwin', 22, True, True, False), ('win32', 5, True, True, False),
    ('win32', 22, False, True, False), ('win32', 22, True, False, False),
])
def test_final_pipe_flush_only_tolerates_completed_cleanup(monkeypatch, platform, number, closed, dead, tolerated):
    class FailedFlush:
        def close(self):
            raise OSError(number, 'synthetic flush failure')
    stdin = FailedFlush()
    stdin.closed = closed
    stdout = BytesIO()
    process = SimpleNamespace(stdin=stdin, stdout=stdout, wait=lambda timeout: None,
                              poll=lambda: 0 if dead else None)
    tree = SimpleNamespace(kill=lambda: None, close=lambda: None)
    child = platform_paths.CodexServerProcess(process, tree)
    monkeypatch.setattr(platform_paths.sys, 'platform', platform)
    if tolerated:
        child.close()
    else:
        with pytest.raises(OSError):
            child.close()
    assert stdout.closed
