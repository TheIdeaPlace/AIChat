from pathlib import Path
from collections import deque
from dataclasses import replace
import threading
import time

import pytest

from test_codex_owned import response
from thechatplace.providers.codex_runtime import CodexChatRuntime
from thechatplace.providers.codex_events import CodexEvent
from thechatplace.providers.codex_rpc import CodexRpcError


class Connection:
    def __init__(self):
        self.closed = False
        self.proofs = {}
        self.calls = []
        self.events = deque()
        self.created = self.turns = 0
        self.entered = self.release = None

    def initialize(self):
        pass

    def close(self):
        self.closed = True

    def _accept_owned_thread(self, proof):
        self.proofs[proof.native_id] = proof

    def owns_runtime(self, proof):
        return not self.closed and self.proofs.get(proof.native_id) is proof

    def pop_event(self):
        if self.closed:
            raise CodexRpcError('closed')
        return self.events.popleft() if self.events else None

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
        if method == 'mcpServerStatus/list':
            return {'data': [], 'nextCursor': None}
        if method == 'model/list':
            from test_codex_models import model
            return {'data': [model('gpt-default')]}
        if method == 'config/read':
            return {'config': {}, 'layers': [{'name': {'type': 'project',
                'dotCodexFolder': str(Path(__file__).resolve().parent / '__empty_codex_layer__')}}]}
        if method == 'thread/read':
            return response(params['threadId'])
        if method == 'thread/turns/list':
            return {'data': []}
        if method in ('thread/start', 'thread/fork'):
            self.created += 1
            result = response(f'owned-{self.created}')
            result['thread']['ephemeral'] = params.get('ephemeral', False)
            return result
        if method == 'thread/resume':
            return response(params['threadId'])
        if method == 'turn/start':
            if self.entered:
                self.entered.set()
                assert self.release.wait(5)
            self.turns += 1
            return {'turn': {'id': f'turn-{self.turns}', 'status': 'inProgress'}}
        if method == 'turn/interrupt':
            return {}
        raise AssertionError(method)


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def runtime():
    connection = Connection()
    updates = []
    service = CodexChatRuntime(updates.append, factory=lambda exe: connection)
    return service, connection, updates


def _owned_reader(service, record):
    from thechatplace.providers.codex import CodexProvider
    from thechatplace.sessions import SessionInfo, OWN
    from test_codex_provider import Connection as HistoryConnection, history, history_pages
    info = SessionInfo(OWN, 'codex:' + record.cli_session_id, 'New chat',
                       record.cwd, record.cli_session_id, provider='codex')
    discovery = HistoryConnection(history_pages(history(record.cli_session_id)))
    return CodexProvider(discovery, runtime=service).open_reader(info), discovery


def test_new_chat_is_empty_until_its_first_turn_then_read_where_it_lives():
    # A new thread exists only in the runtime's process until Codex saves it,
    # so reading it elsewhere said "Couldn't load this Codex chat" (#285).
    from thechatplace.providers.codex import EMPTY_HISTORY
    from test_codex_provider import Connection as HistoryConnection, history, history_pages
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'New chat')
        reader, discovery = _owned_reader(service, record)
        before = len(connection.calls)
        assert service.history_route(record.cli_session_id) is EMPTY_HISTORY
        assert not reader.refresh() and not reader.transcript.messages
        assert len(connection.calls) == before and not discovery.calls

        # During the first turn: the owner process, never the discovery one.
        service.send(record, 'Hello')
        pages = HistoryConnection(history_pages(history(record.cli_session_id)))
        connection.request = pages.request
        assert service.history_route(record.cli_session_id) is connection
        assert reader.refresh()
        assert reader.transcript.last_reply().text == 'Ready'
        assert [method for method, _ in pages.calls] == [
            'thread/read', 'thread/turns/list', 'thread/items/list']
        assert not discovery.calls

        # Once the first turn ends the thread is saved: back to the
        # reconnecting discovery connection, and never empty again.
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id,
                                            'turn-1', status='completed'))
        wait(lambda: service.history_route(record.cli_session_id) is None)
        reader.refresh()
        assert discovery.calls and discovery.calls[0][0] == 'thread/read'
    finally:
        service.close()


def test_history_falls_back_to_discovery_when_the_runtime_process_is_gone():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'New chat')
        service.send(record, 'Hello')
        reader, discovery = _owned_reader(service, record)
        reader._failed_stamp = reader._history_stamp = ('stale', 'from the owner')
        connection.closed = True
        assert service.history_route(record.cli_session_id) is None
        assert reader.refresh()
        assert reader.transcript.last_reply().text == 'Ready'
        assert discovery.calls
    finally:
        service.close()


def test_refused_first_send_leaves_the_new_chat_empty():
    from thechatplace.providers.codex import EMPTY_HISTORY
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'New chat')
        original = connection.request
        def refused(method, params):
            if method == 'turn/start':
                raise CodexTurnRejected('preflight_failed')
            return original(method, params)
        connection.request = refused
        with pytest.raises(CodexTurnRejected):
            service.send(record, 'Not sent')
        assert service.history_route(record.cli_session_id) is EMPTY_HISTORY
    finally:
        service.close()


def test_forked_and_reopened_chats_are_not_assumed_empty():
    from thechatplace.sessions import SessionInfo, OWN

    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'New chat')
        assert service.history_route(record.cli_session_id) is not None
        source = SessionInfo(OWN, 'codex:' + record.cli_session_id, 'New chat',
                             record.cwd, record.cli_session_id, provider='codex')
        forked = service.fork(source, 'Copied chat')
        assert service.history_route(forked.cli_session_id) is None
        service.reopen(record)
        assert service.history_route(record.cli_session_id) is None
    finally:
        service.close()


def test_lazy_creation_owned_send_and_successful_queue_advance():
    service, c, updates = runtime()
    try:
        assert not c.calls
        record = service.create('/workspace', 'Owned')
        assert record.provider == 'codex' and record.started
        assert not service.send(record, 'First')
        assert service.send(record, 'Second')
        assert service.snapshot(record.cli_session_id).queued == ('Second',)
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: c.turns == 2)
        assert [p['input'][0]['text'] for m, p in c.calls if m == 'turn/start'] == ['First', 'Second']
        assert not service.snapshot(record.cli_session_id).queued
    finally:
        service.close()
    assert c.closed


def test_failed_turn_returns_queue_unsent_and_never_advances_it():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Keep me')
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='failed'))
        wait(lambda: any(u.unsent == ('Keep me',) for u in updates))
        assert c.turns == 1 and not service.snapshot(record.cli_session_id).queued
    finally:
        service.close()


def test_stop_during_startup_interrupts_confirmed_turn_and_discards_queue():
    from concurrent.futures import ThreadPoolExecutor
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        c.entered, c.release = threading.Event(), threading.Event()
        with ThreadPoolExecutor(1) as pool:
            starting = pool.submit(service.send, record, 'First')
            assert c.entered.wait(5)
            assert service.send(record, 'Queued')
            assert service.stop(record) == ('Queued',)
            with pytest.raises(CodexRpcError, match='stopping'):
                service.send(record, 'Another')
            c.release.set()
            assert not starting.result(timeout=5)
        assert ('turn/interrupt', {'threadId': record.cli_session_id, 'turnId': 'turn-1'}) in c.calls
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='interrupted'))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'interrupted')
        assert c.turns == 1
    finally:
        service.close()


def test_two_chats_do_not_share_queues_and_unknown_connection_never_replays():
    service, c, updates = runtime()
    try:
        one = service.create('/workspace', 'One')
        two = service.create('/workspace', 'Two')
        service.send(one, 'A')
        service.send(two, 'B')
        service.send(one, 'Unsent')
        c.closed = True
        wait(lambda: any(u.problem for u in updates))
        assert c.turns == 2
        with pytest.raises(CodexRpcError, match='Use Stop'):
            service.send(one, 'Retry')
        assert any(u.unsent == ('Unsent',) for u in updates)
    finally:
        service.close()


def test_active_chat_cannot_be_resumed_and_callback_failure_never_replays():
    service, c, updates = runtime()
    service._on_update = lambda state: (_ for _ in ()).throw(RuntimeError('UI closing'))
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        before = len(c.calls)
        with pytest.raises(CodexRpcError, match='in progress'):
            service.reopen(record)
        assert len(c.calls) == before and c.turns == 1
    finally:
        service.close()


def test_reopen_reserves_lifecycle_before_rpc_and_prevents_concurrent_send():
    from concurrent.futures import ThreadPoolExecutor
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        entered, release = threading.Event(), threading.Event()
        request = c.request
        def blocked(method, params):
            if method == 'thread/resume':
                entered.set()
                assert release.wait(5)
            return request(method, params)
        c.request = blocked
        with ThreadPoolExecutor(1) as pool:
            reopening = pool.submit(service.reopen, record)
            assert entered.wait(5)
            with pytest.raises(CodexRpcError):
                service.send(record, 'Concurrent')
            with pytest.raises(CodexRpcError):
                service.stop(record)
            with pytest.raises(CodexRpcError):
                service.reopen(record)
            release.set()
            reopening.result(timeout=5)
        assert c.turns == 0
        service.send(record, 'After reopen')
        assert c.turns == 1
    finally:
        service.close()


def test_stop_cancels_reserved_queued_worker_before_dispatch_and_returns_text():
    service, c, updates = runtime()
    deferred = []
    service._launch_queued = deferred.append
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Queued')
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: len(deferred) == 1)
        assert service.stop(record) == ('Queued',)
        service.send(record, 'New explicit message')
        deferred[0]()
        assert c.turns == 2
        assert [p['input'][0]['text'] for m, p in c.calls if m == 'turn/start'] == ['First', 'New explicit message']
    finally:
        service.close()


def test_stale_advance_after_reopen_returns_old_queue_unsent_and_never_sends():
    service, c, updates = runtime()
    service._listen = lambda connection, executor: None
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Old queued text')
        proof, executor = service._context(record)
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        executor.drain()
        entered, release = threading.Event(), threading.Event()
        snapshot = executor.snapshot
        def blocked(proof):
            result = snapshot(proof)
            if threading.current_thread().name == 'stale-advance':
                entered.set()
                assert release.wait(5)
            return result
        executor.snapshot = blocked
        old = threading.Thread(target=service._advance, args=(proof, executor), name='stale-advance')
        old.start()
        assert entered.wait(5)
        service.reopen(record)
        release.set()
        old.join(5)
        assert not old.is_alive()
        assert c.turns == 1 and not service.snapshot(record.cli_session_id).problem
        assert any(u.unsent == ('Old queued text',) for u in updates)
        assert not service.snapshot(record.cli_session_id).queued
    finally:
        service.close()


def test_failed_interrupt_still_returns_removed_queue_without_claiming_completion():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Keep queued text')
        original_request = c.request
        def request(method, params):
            if method == 'turn/interrupt':
                raise CodexRpcError('Interrupt unavailable')
            return original_request(method, params)
        c.request = request
        with pytest.raises(CodexRpcError):
            service.stop(record)
        assert any(state.unsent == ('Keep queued text',) for state in updates)
        assert service.snapshot(record.cli_session_id).outcome == 'inProgress'
        assert c.turns == 1
    finally:
        service.close()


def test_failed_automatic_dispatch_returns_dispatched_and_remaining_text_once():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Dispatched queued text')
        service.send(record, 'Remaining queued text')
        original_request = c.request
        def request(method, params):
            if method == 'turn/start':
                raise CodexRpcError('Unknown send outcome')
            return original_request(method, params)
        c.request = request
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: any(state.unsent == ('Dispatched queued text',) for state in updates))
        recovered = [text for state in updates for text in state.unsent]
        assert recovered.count('Dispatched queued text') == 1
        assert recovered.count('Remaining queued text') == 1
        assert c.turns == 1
        assert not service.snapshot(record.cli_session_id).queued
        with pytest.raises(CodexRpcError):
            service.send(record, 'Never automatically replay')
    finally:
        service.close()


def test_live_connection_problem_requires_reopen_then_queue_dispatch_works():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        proof, executor = service._context(record)
        original_start = executor.start
        executor.start = lambda *args: (_ for _ in ()).throw(CodexRpcError('Before dispatch'))
        with pytest.raises(CodexRpcError):
            service.send(record, 'Not sent')
        assert not c.closed and not service.ready(record)
        with pytest.raises(CodexRpcError, match='Use Stop'):
            service.send(record, 'No replay')
        executor.start = original_start
        service.reopen(record)
        assert service.ready(record) and not service.snapshot(record.cli_session_id).problem
        service.send(record, 'New explicit message')
        service.send(record, 'Queued message')
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: c.turns == 2)
        assert [params['input'][0]['text'] for method, params in c.calls if method == 'turn/start'] == [
            'New explicit message', 'Queued message']
    finally:
        service.close()


def test_recorded_problem_does_not_prevent_stopping_a_verified_active_turn():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service._problems[record.cli_session_id] = 'Could not confirm this Codex turn. Use Stop to recheck this chat.'
        assert not service.ready(record)
        service.stop(record)
        assert ('turn/interrupt', {'threadId': record.cli_session_id, 'turnId': 'turn-1'}) in c.calls
        assert service.snapshot(record.cli_session_id).outcome == 'inProgress'
    finally:
        service.close()


def test_new_problem_after_context_validation_blocks_direct_send_reservation():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        context = service._context
        def changed(record, **kwargs):
            result = context(record, **kwargs)
            service._problems[record.cli_session_id] = 'Use Stop to recheck this chat.'
            return result
        service._context = changed
        with pytest.raises(CodexRpcError, match='Use Stop'):
            service.send(record, 'Must not send')
        assert c.turns == 0
    finally:
        service.close()


def policy_runtime(tmp_path):
    class PolicyConnection(Connection):
        def request(self, method, params):
            result = super().request(method, params)
            if method in ('thread/start', 'thread/resume'):
                from thechatplace.providers.codex_policy import CodexPolicy
                policy = CodexPolicy.choose(params['cwd'], params['sandbox'], params['approvalPolicy'])
                result.update(cwd=params['cwd'], sandbox=policy.turn_policy(), approvalPolicy=policy.approval)
            return result
    connection = PolicyConnection()
    service = CodexChatRuntime(factory=lambda exe: connection)
    record = service.create(str(tmp_path), 'Policy test')
    return service, connection, record


def test_permission_change_blocks_sends_until_saved_and_verified(tmp_path):
    service, connection, record = policy_runtime(tmp_path)
    try:
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        assert not service.ready(record)
        with pytest.raises(CodexRpcError):
            service.send(record, 'Must not run before save')
        proposed = change.record()
        assert proposed.cli_session_id == record.cli_session_id
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
        assert any(method == 'thread/start' and params.get('ephemeral')
                   for method, params in connection.calls)
        assert proposed.provider_options['workspace_root'] == str(tmp_path.resolve())
        assert record.provider_options['sandbox'] == 'read-only'
        service.finish_policy(change, True)
        assert service.ready(proposed)
        service.send(proposed, 'Synthetic prompt')
        assert connection.calls[-1][1]['sandboxPolicy']['type'] == 'workspaceWrite'
        with pytest.raises(CodexRpcError, match='no longer pending'):
            service.finish_policy(change, True)
    finally:
        service.close()


def test_failed_policy_save_requires_explicit_reload_of_old_permissions(tmp_path):
    service, connection, record = policy_runtime(tmp_path)
    try:
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        service.finish_policy(change, False)
        assert not service.ready(record)
        with pytest.raises(CodexRpcError):
            service.send(record, 'Unpersisted permission must not run')
        restored = service.reopen(record)
        assert restored.provider_options['sandbox'] == 'read-only'
        assert service.ready(restored)
        assert not any(method == 'turn/start' for method, _ in connection.calls)
    finally:
        service.close()


def test_permission_check_cannot_retarget_the_requested_workspace(tmp_path, monkeypatch):
    from thechatplace.providers.codex_owned import CodexOwnedThreads
    service, connection, record = policy_runtime(tmp_path)
    other = tmp_path / 'different-project'
    other.mkdir()
    start = CodexOwnedThreads.start
    def retargeted(lifecycle, cwd, title, **options):
        return start(lifecycle, str(other), title, **options)
    monkeypatch.setattr(CodexOwnedThreads, 'start', retargeted)
    try:
        with pytest.raises(CodexRpcError, match='workspace scope'):
            service.prepare_policy(record, 'workspace-write', 'on-request')
        assert not service.ready(record)
        assert not any(method == 'turn/start' for method, _ in connection.calls)
        assert connection.proofs[record.cli_session_id].policy.sandbox == 'read-only'
    finally:
        service.close()


def test_unloaded_chat_can_change_to_readonly_when_its_saved_edit_mode_is_unavailable(tmp_path, monkeypatch):
    service, connection, record = policy_runtime(tmp_path)
    record.provider_options.update(sandbox='workspace-write', workspace_root=str(tmp_path.resolve()))
    service._proofs.clear()
    connection.proofs.clear()
    request = connection.request
    def unavailable(method, params):
        if method == 'thread/resume' and params['sandbox'] == 'workspace-write':
            raise CodexRpcError('Previous editing mode is unavailable')
        return request(method, params)
    monkeypatch.setattr(connection, 'request', unavailable)
    try:
        change = service.prepare_policy(record, 'read-only', 'on-request')
        service.finish_policy(change, True)
        restored = change.record()
        assert service.ready(restored)
        assert restored.provider_options['sandbox'] == 'read-only'
        assert restored.cli_session_id == record.cli_session_id
        assert not any(method == 'turn/start' for method, _ in connection.calls)
    finally:
        service.close()


def test_active_or_queued_chat_refuses_permission_change_without_rpc(tmp_path):
    service, connection, record = policy_runtime(tmp_path)
    try:
        service.send(record, 'First')
        service.send(record, 'Queued')
        before = len(connection.calls)
        with pytest.raises(CodexRpcError, match='no work in progress'):
            service.prepare_policy(record, 'workspace-write', 'on-request')
        assert len(connection.calls) == before
        assert service.snapshot(record.cli_session_id).queued == ('Queued',)
    finally:
        service.close()


def test_permission_change_rechecks_chatgpt_account_and_blocks_on_failure(tmp_path, monkeypatch):
    service, connection, record = policy_runtime(tmp_path)
    try:
        request = connection.request
        monkeypatch.setattr(connection, 'request', lambda method, params:
            {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True}
            if method == 'account/read' else request(method, params))
        with pytest.raises(CodexRpcError, match='ChatGPT sign-in'):
            service.prepare_policy(record, 'workspace-write', 'on-request')
        assert not service.ready(record)
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
    finally:
        service.close()


def test_permission_change_to_downgraded_sandbox_blocks_with_the_sandbox_reason(tmp_path, monkeypatch):
    # Windows without Codex's sandbox answers workspace-write with read-only (#257).
    from thechatplace import platform_paths
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    monkeypatch.setattr(platform_paths, 'codex_downgrades_workspace_write', lambda: True)
    service, connection, record = policy_runtime(tmp_path)
    try:
        request = connection.request

        def downgraded(method, params):
            result = request(method, params)
            if method == 'thread/start' and params.get('ephemeral'):
                result = dict(result, sandbox={'type': 'readOnly', 'networkAccess': False})
            return result
        monkeypatch.setattr(connection, 'request', downgraded)
        with pytest.raises(CodexTurnRejected) as refused:
            service.prepare_policy(record, 'workspace-write', 'on-request')
        assert refused.value.code == 'sandbox_unavailable'
        assert not service.ready(record)
        assert 'Windows sandbox' in service._problems[record.cli_session_id]
        assert 'usually means' in service._problems[record.cli_session_id]
        assert service._problems[record.cli_session_id].endswith('Use Stop to recheck this chat before sending.')
        assert 'reload' not in str(refused.value).lower()
    finally:
        service.close()


def test_send_context_acquired_before_policy_change_cannot_cross_reservation(tmp_path, monkeypatch):
    service, connection, record = policy_runtime(tmp_path)
    entered, release = threading.Event(), threading.Event()
    context = service._context
    errors = []
    def delayed(record, **kw):
        value = context(record, **kw)
        entered.set()
        assert release.wait(5)
        return value
    monkeypatch.setattr(service, '_context', delayed)
    def send():
        try:
            service.send(record, 'Must not cross policy change')
        except CodexRpcError as exc:
            errors.append(exc)
    thread = threading.Thread(target=send)
    try:
        thread.start()
        assert entered.wait(5)
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        release.set()
        thread.join(5)
        assert not thread.is_alive() and len(errors) == 1
        assert not any(method == 'turn/start' for method, _ in connection.calls)
        service.finish_policy(change, False)
    finally:
        release.set()
        thread.join(5)
        service.close()


def test_reserved_queue_dispatch_blocks_permission_change_before_rpc(tmp_path):
    service, connection, record = policy_runtime(tmp_path)
    try:
        with service._lock:
            service._pending_dispatch[record.cli_session_id] = (object(), 'Reserved prompt')
        before = len(connection.calls)
        with pytest.raises(CodexRpcError, match='no work in progress'):
            service.prepare_policy(record, 'workspace-write', 'on-request')
        assert len(connection.calls) == before
    finally:
        service.close()


def test_selecting_known_active_chat_does_not_resume_or_interrupt():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'Still running')
        before = tuple(connection.calls)
        assert service.prepare(record) is record
        assert tuple(connection.calls) == before
        assert service.snapshot(record.cli_session_id).outcome == 'inProgress'
        assert service.send(record, 'Queued safely')
    finally:
        service.close()


def test_selecting_uncertain_known_chat_never_reconnects_or_resumes(monkeypatch):
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service._problems[record.cli_session_id] = 'Synthetic uncertainty'
        before = tuple(connection.calls)
        monkeypatch.setattr(service, '_binding', lambda: pytest.fail('Readiness must not reconnect'))
        with pytest.raises(CodexRpcError, match='Automatic resume is disabled'):
            service.prepare(record)
        assert tuple(connection.calls) == before
    finally:
        service.close()


def test_proven_unsent_refusal_allows_fresh_retry_without_resume():
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        original = connection.request
        def refused(method, params):
            if method == 'turn/start':
                raise CodexTurnRejected('preflight_failed')
            return original(method, params)
        connection.request = refused
        with pytest.raises(CodexTurnRejected):
            service.send(record, 'Not sent')
        assert service.ready(record) and not service.snapshot(record.cli_session_id).problem
        connection.request = original
        assert service.send(record, 'Fresh deliberate retry') is False
        assert connection.turns == 1
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
    finally:
        service.close()


def test_failed_interrupt_then_stream_loss_does_not_permanently_block_recovery():
    from thechatplace.providers.codex_rpc import CodexConnectionFailure
    service, connection, updates = runtime()
    replacement = Connection()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'Running')
        service.send(record, 'Recover this queued text')
        original = connection.request
        def fail_interrupt(method, params):
            if method == 'turn/interrupt':
                connection.closed = True
                raise CodexConnectionFailure('stream_closed')
            return original(method, params)
        connection.request = fail_interrupt
        with pytest.raises(CodexConnectionFailure):
            service.stop(record)
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'unknown')
        assert record.cli_session_id not in service._stopping
        assert any(u.unsent == ('Recover this queued text',) for u in updates)
        service._managed._factory = lambda exe: replacement
        service.reopen(record)
        assert service.ready(record)
        assert not any(method == 'turn/start' for method, _ in replacement.calls)
    finally:
        service.close()


def test_rejected_queued_preflight_returns_remaining_text_fifo_without_advancing():
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    service, connection, updates = runtime()
    deferred = []
    service._launch_queued = deferred.append
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Second')
        service.send(record, 'Third')
        original = connection.request
        def reject(method, params):
            if method == 'turn/start':
                raise CodexTurnRejected('preflight_failed')
            return original(method, params)
        connection.request = reject
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id,
                                            'turn-1', status='completed'))
        wait(lambda: len(deferred) == 1)
        deferred.pop()()
        assert not deferred and connection.turns == 1
        assert [text for state in updates for text in state.unsent] == ['Second', 'Third']
        assert not service.snapshot(record.cli_session_id).queued
        assert service.ready(record) and not service.snapshot(record.cli_session_id).problem
        connection.request = original
        assert service.send(record, 'Deliberate retry') is False
        assert connection.turns == 2
    finally:
        service.close()


def test_send_during_rejection_recovery_does_not_join_a_stranded_queue():
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    service, connection, updates = runtime()
    deferred, fresh = [], []
    service._launch_queued = deferred.append
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Second')
        service.send(record, 'Third')
        original = connection.request
        def reject(method, params):
            if method == 'turn/start':
                raise CodexTurnRejected('preflight_failed')
            return original(method, params)
        connection.request = reject
        def on_update(state):
            updates.append(state)
            if state.unsent == ('Second', 'Third'):
                connection.request = original
                fresh.append(service.send(record, 'Deliberate new message'))
        service._on_update = on_update
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id,
                                            'turn-1', status='completed'))
        wait(lambda: len(deferred) == 1)
        deferred.pop()()
        assert fresh == [False]
        assert connection.turns == 2 and not deferred
        assert not service.snapshot(record.cli_session_id).queued
        assert service.snapshot(record.cli_session_id).outcome == 'inProgress'
    finally:
        service.close()


def test_permission_change_keeps_project_rule_refusal_reason(tmp_path, monkeypatch):
    from tests.test_codex_rule_guard import layer
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    service, connection, record = policy_runtime(tmp_path)
    rules = tmp_path / 'rules'; rules.mkdir()
    (rules / 'saved.rules').write_text('synthetic')
    request = connection.request
    def configured(method, params):
        result = request(method, params)
        if method == 'config/read':
            result['layers'] = [layer(tmp_path)]
        return result
    monkeypatch.setattr(connection, 'request', configured)
    try:
        with pytest.raises(CodexTurnRejected):
            service.prepare_policy(record, 'workspace-write', 'on-request')
        problem = service.snapshot(record.cli_session_id).problem
        assert 'project' in problem and str(rules) in problem
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
    finally:
        service.close()



def test_plan_save_failure_does_not_cancel_turn_or_other_chat(tmp_path, monkeypatch):
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path)
    service, connection, updates = runtime()
    attempts = []
    def fail(*args):
        attempts.append(args)
        raise OSError('disk unavailable')
    monkeypatch.setattr(service.plan_store, 'save', fail)
    try:
        one, two = service.create('/workspace', 'One'), service.create('/workspace', 'Two')
        service.send(one, 'First'); service.send(two, 'Second')
        connection.events.append(CodexEvent('plan_updated', one.cli_session_id, 'turn-1',
                                           plan=(('Run the tests', 'in_progress'),)))
        wait(lambda: any(update.tasks for update in updates))
        assert attempts and not connection.closed
        assert service.snapshot(one.cli_session_id).outcome == 'inProgress'
        assert service.snapshot(two.cli_session_id).outcome == 'inProgress'
        assert not service.snapshot(one.cli_session_id).problem
    finally:
        service.close()
