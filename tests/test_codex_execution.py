import threading
from collections import deque
from dataclasses import replace

import pytest

from thechatplace.providers.codex_events import CodexEvent
from thechatplace.providers.codex_execution import CodexExecutionConnection, CodexTurnExecutor
from thechatplace.providers.codex_owned import CodexOwnedThread
from thechatplace.providers.codex_rpc import CodexRpcError, CodexTurnRejected


class Connection:
    def __init__(self):
        self.proofs = {}
        self.calls = []
        self.events = deque()
        self.closed = False
        self.failure = False
        self.counter = 0

    def owns_runtime(self, proof):
        return not self.closed and self.proofs.get(proof.native_id) is proof and proof.connection is self

    def proof(self, native_id):
        proof = CodexOwnedThread(native_id, '/workspace', 'model', 'Owned', 0, self)
        self.proofs[native_id] = proof
        return proof

    def request(self, method, params):
        self.calls.append((method, params))
        if self.failure:
            raise ValueError('private backend payload')
        if method == 'turn/interrupt':
            return {}
        self.counter += 1
        return {'turn': {'id': f'turn-{self.counter}', 'status': 'inProgress'}}

    def pop_event(self):
        if self.closed:
            raise CodexRpcError('closed')
        return self.events.popleft() if self.events else None


def test_verified_text_turn_pins_model_policy_and_refuses_second_active_turn():
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    turn = executor.start(proof, 'Hello')
    assert turn.thread_id == 'one' and turn.turn_id == 'turn-1'
    assert c.calls[0] == ('turn/start', {'threadId': 'one', 'cwd': '/workspace',
        'model': 'model', 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
        'sandboxPolicy': {'type': 'readOnly'}, 'input': [{'type': 'text', 'text': 'Hello'}]})
    with pytest.raises(CodexRpcError, match='in progress'):
        executor.start(proof, 'Duplicate')
    assert len(c.calls) == 1


def test_forged_or_other_connection_proofs_cannot_send_or_interrupt():
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    for invalid in [replace(proof), replace(proof, native_id='external'), Connection().proof('one')]:
        with pytest.raises(CodexRpcError, match='verified owned'):
            executor.start(invalid, 'Hello')
        with pytest.raises(CodexRpcError):
            executor.interrupt(invalid)
    assert not c.calls


def test_interleaved_streams_and_interrupt_keep_other_chat_running():
    c = Connection()
    one, two = c.proof('one'), c.proof('two')
    executor = CodexTurnExecutor(c)
    a, b = executor.start(one, 'A'), executor.start(two, 'B')
    c.events.extend([CodexEvent('text_delta', 'one', a.turn_id, 'reply', 'Draft'),
        CodexEvent('text_completed', 'two', b.turn_id, 'reply', 'B reply'),
        CodexEvent('text_completed', 'one', a.turn_id, 'reply', 'A reply')])
    assert executor.drain() == frozenset({'one', 'two'})
    assert a.replies == (('reply', 'A reply'),) and b.replies == (('reply', 'B reply'),)
    assert executor.interrupt(one)
    assert c.calls[-1] == ('turn/interrupt', {'threadId': 'one', 'turnId': a.turn_id})
    assert a.status == b.status == 'inProgress'
    c.events.append(CodexEvent('turn_completed', 'one', a.turn_id, status='interrupted'))
    executor.drain()
    assert a.status == 'interrupted' and b.status == 'inProgress'
    assert executor.start(one, 'Next').turn_id == 'turn-3'


def test_failed_send_is_redacted_and_never_replayed_on_same_connection():
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    c.failure = True
    with pytest.raises(CodexRpcError, match='Reopen') as exc:
        executor.start(proof, 'Private prompt')
    assert 'private' not in str(exc.value).lower()
    c.failure = False
    with pytest.raises(CodexRpcError, match='reopened'):
        executor.start(proof, 'Retry')
    assert len(c.calls) == 1


def test_preflight_refusal_keeps_other_chat_running_and_stoppable():
    c = Connection()
    one, two = c.proof('one'), c.proof('two')
    executor = CodexTurnExecutor(c)
    active = executor.start(one, 'Working')
    original = c.request
    def request(method, params):
        if method == 'turn/start' and params['threadId'] == 'two':
            raise CodexTurnRejected('not_idle')
        return original(method, params)
    c.request = request
    with pytest.raises(CodexTurnRejected, match='not confirmed idle'):
        executor.start(two, 'Refused')
    assert not c.closed and active.status == 'inProgress'
    assert executor.interrupt(one)
    assert executor.snapshot(two).status == 'idle'
    c.request = original
    assert executor.start(two, 'Now ready').status == 'inProgress'


def test_disconnect_marks_every_active_turn_unknown_and_blocks_replay():
    c = Connection()
    one, two = c.proof('one'), c.proof('two')
    executor = CodexTurnExecutor(c)
    a, b = executor.start(one, 'A'), executor.start(two, 'B')
    c.closed = True
    with pytest.raises(CodexRpcError, match='Reload'):
        executor.drain()
    assert a.status == b.status == 'unknown'
    assert a.needs_reconciliation and b.needs_reconciliation


@pytest.mark.parametrize('text', ['', ' ', None, 'x' * 32001], ids=['empty','blank','missing','oversized'])
def test_invalid_text_does_not_reserve_or_send(text):
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    with pytest.raises(ValueError):
        executor.start(proof, text)
    assert not c.calls
    executor.start(proof, 'Valid')


def runtime_connection():
    c = CodexExecutionConnection.__new__(CodexExecutionConnection)
    c._runtime_lock = threading.Lock()
    c._runtime_threads = {}
    c._lock = threading.Lock()
    c._closed = False
    proof = CodexOwnedThread('owned', '/workspace', 'model', 'Owned', 0, c)
    c._accept_owned_thread(proof)
    c.request = lambda method, params: ({'thread': {'id': 'owned', 'modelProvider': 'openai',
        'cwd': '/workspace', 'status': {'type': 'idle'}}} if method == 'thread/read'
        else {'config': {}})
    params = {'threadId': 'owned', 'cwd': '/workspace', 'model': 'model',
        'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
        'sandboxPolicy': {'type': 'readOnly'}, 'input': [{'type': 'text', 'text': 'Hello'}]}
    return c, proof, params


def test_wire_route_requires_registered_persistent_proof_and_exact_pinned_options():
    c, proof, params = runtime_connection()
    c._verify_turn_route(params)
    for field, value in [('model', 'other'), ('cwd', '/elsewhere'), ('approvalPolicy', 'never'),
                         ('sandboxPolicy', {'type': 'dangerFullAccess'}), ('effort', 'high')]:
        changed = {**params, field: value}
        with pytest.raises(CodexRpcError):
            c._verify_turn_route(changed)
    c._runtime_threads.clear()
    with pytest.raises(CodexRpcError, match='verified owned'):
        c._verify_turn_route(params)


def test_unknown_or_busy_thread_cannot_start_a_new_turn():
    c, proof, params = runtime_connection()
    for status in ['notLoaded', 'active']:
        c.request = lambda method, args: ({'thread': {'id': 'owned', 'modelProvider': 'openai',
            'cwd': '/workspace', 'status': {'type': status}}} if method == 'thread/read'
            else {'config': {}})
        with pytest.raises(CodexRpcError, match='confirmed idle'):
            c._verify_turn_route(params)


def test_events_before_start_response_are_preserved_even_after_previous_turn():
    from concurrent.futures import ThreadPoolExecutor
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    old = executor.start(proof, 'Old')
    c.events.append(CodexEvent('turn_completed', 'one', old.turn_id, status='completed'))
    executor.drain()
    entered, release = threading.Event(), threading.Event()
    request = c.request
    def blocked(method, params):
        if method == 'turn/start':
            entered.set()
            assert release.wait(5)
        return request(method, params)
    c.request = blocked
    with ThreadPoolExecutor(1) as pool:
        starting = pool.submit(executor.start, proof, 'New')
        assert entered.wait(5)
        c.events.extend([CodexEvent('text_delta', 'one', 'turn-2', 'reply', 'Draft'),
            CodexEvent('text_completed', 'one', 'turn-2', 'reply', 'Complete'),
            CodexEvent('turn_completed', 'one', 'turn-2', status='completed')])
        assert not executor.drain()
        release.set()
        turn = starting.result(timeout=5)
    assert turn.replies == (('reply', 'Complete'),) and turn.status == 'completed'
    assert executor.start(proof, 'Next').turn_id == 'turn-3'


@pytest.mark.parametrize('mode', ['overflow', 'disconnect', 'failed', 'interrupted', 'wrong-turn'])
def test_pending_start_stream_outcomes_remain_safe(mode):
    from concurrent.futures import ThreadPoolExecutor
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    entered, release = threading.Event(), threading.Event()
    request = c.request
    def blocked(method, params):
        entered.set()
        assert release.wait(5)
        return request(method, params)
    c.request = blocked
    with ThreadPoolExecutor(1) as pool:
        starting = pool.submit(executor.start, proof, 'New')
        assert entered.wait(5)
        if mode == 'disconnect':
            c.closed = True
        elif mode == 'overflow':
            c.events.extend([CodexEvent('text_delta', 'one', 'turn-1', 'reply', 'x')] * 33)
        elif mode == 'wrong-turn':
            c.events.append(CodexEvent('text_completed', 'one', 'other-turn', 'reply', 'Foreign'))
        else:
            c.events.append(CodexEvent('turn_completed', 'one', 'turn-1', status=mode))
        if mode in ('overflow', 'disconnect'):
            with pytest.raises(CodexRpcError):
                executor.drain()
            release.set()
            with pytest.raises(CodexRpcError, match='Reopen'):
                starting.result(timeout=5)
            c.closed = False
            with pytest.raises(CodexRpcError):
                executor.start(proof, 'Retry')
            assert len(c.calls) == 1
        else:
            executor.drain()
            release.set()
            turn = starting.result(timeout=5)
            if mode == 'wrong-turn':
                assert not turn.replies and turn.status == 'inProgress'
            else:
                assert turn.status == mode
                assert executor.start(proof, 'Next').turn_id == 'turn-2'


def test_requests_stay_on_matching_chat_and_cannot_be_answered_after_completion():
    from thechatplace.providers.codex_requests import COMMAND, decode_request
    c = Connection()
    one, two = c.proof('one'), c.proof('two')
    executor = CodexTurnExecutor(c)
    a, b = executor.start(one, 'A'), executor.start(two, 'B')
    request = decode_request('approve', COMMAND, {'threadId': 'one', 'turnId': a.turn_id,
        'itemId': 'tool', 'command': 'synthetic', 'cwd': '/workspace'})
    c.events.append(request)
    executor.drain()
    assert executor.waiting(one) == (request,) and not executor.waiting(two)
    replies = []
    c.respond_request = lambda request, result: replies.append((request, result))
    with pytest.raises(CodexRpcError):
        executor.answer(two, request, {'decision': 'accept'})
    executor.answer(one, request, {'decision': 'decline'})
    assert replies == [(request, {'decision': 'decline'})]
    assert not executor.waiting(one)
    c.events.append(request)
    executor.drain()
    c.events.append(CodexEvent('turn_completed', 'one', a.turn_id, status='interrupted'))
    executor.drain()
    with pytest.raises(CodexRpcError):
        executor.answer(one, request, {'decision': 'accept'})


def test_mismatched_request_during_start_clears_waiting_and_closes_uncertain_stream():
    from concurrent.futures import ThreadPoolExecutor
    from thechatplace.providers.codex_requests import COMMAND, decode_request
    c = Connection()
    c.close = lambda: setattr(c, 'closed', True)
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    entered, release = threading.Event(), threading.Event()
    request = c.request
    def blocked(method, params):
        entered.set()
        assert release.wait(5)
        return request(method, params)
    c.request = blocked
    def approval(identity, turn):
        return decode_request(identity, COMMAND, {'threadId': 'one', 'turnId': turn,
            'itemId': 'tool', 'command': 'synthetic', 'cwd': '/workspace'})
    with ThreadPoolExecutor(1) as pool:
        starting = pool.submit(executor.start, proof, 'New')
        assert entered.wait(5)
        valid = approval('valid', 'turn-1')
        c.events.extend([valid, approval('wrong', 'another-turn')])
        executor.drain()
        release.set()
        with pytest.raises(CodexRpcError, match='Reopen'):
            starting.result(timeout=5)
    assert c.closed and executor._broken and not executor.waiting(proof)
    assert not executor._requests
    with pytest.raises(CodexRpcError):
        executor.start(proof, 'Retry')
    with pytest.raises(CodexRpcError):
        executor.answer(proof, valid, {'decision': 'accept'})


def test_backend_resolution_clears_only_matching_waiting_request():
    from thechatplace.providers.codex_requests import COMMAND, decode_request, decode_resolved
    c = Connection()
    proof = c.proof('one')
    executor = CodexTurnExecutor(c)
    turn = executor.start(proof, 'A')
    request = decode_request('approve', COMMAND, {'threadId': 'one', 'turnId': turn.turn_id,
        'itemId': 'tool', 'command': 'synthetic', 'cwd': '/workspace'})
    c.events.append(request)
    executor.drain()
    c.events.append(decode_resolved({'threadId': 'one', 'requestId': 'approve'}))
    executor.drain()
    assert not executor.waiting(proof) and turn.status == 'inProgress'


def test_selected_effort_is_sent_and_wire_guard_refuses_changes():
    c = Connection()
    proof = replace(c.proof('one'), effort='future-effort')
    c.proofs['one'] = proof
    CodexTurnExecutor(c).start(proof, 'Hello')
    assert c.calls[-1][1]['effort'] == 'future-effort'
    real, original, params = runtime_connection()
    proof = replace(original, effort='future-effort')
    real._accept_owned_thread(proof)
    params['effort'] = 'future-effort'
    real._verify_turn_route(params)
    for changed in [{key: value for key, value in params.items() if key != 'effort'},
                    {**params, 'effort': 'changed'}]:
        with pytest.raises(CodexRpcError, match='do not match'):
            real._verify_turn_route(changed)


def test_workspace_turn_wire_pins_scope_network_temps_and_approval(tmp_path):
    from thechatplace.providers.codex_policy import CodexPolicy
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write', 'never')
    real, original, params = runtime_connection()
    proof = replace(original, cwd=policy.workspace_root, policy=policy)
    real._accept_owned_thread(proof)
    params.update(cwd=proof.cwd, approvalPolicy='never', sandboxPolicy=policy.turn_policy())
    real._verify_turn_route(params)
    for field, value in [('networkAccess', True), ('writableRoots', ['/elsewhere']),
                         ('excludeSlashTmp', False), ('excludeTmpdirEnvVar', False)]:
        changed = {**params, 'sandboxPolicy': {**params['sandboxPolicy'], field: value}}
        with pytest.raises(CodexRpcError, match='do not match'):
            real._verify_turn_route(changed)
    with pytest.raises(CodexRpcError, match='do not match'):
        real._verify_turn_route({**params, 'approvalPolicy': 'on-request'})
    c = Connection()
    local = replace(c.proof('one'), cwd=proof.cwd, policy=policy)
    c.proofs['one'] = local
    CodexTurnExecutor(c).start(local, 'Synthetic prompt')
    assert c.calls[-1][1]['sandboxPolicy'] == policy.turn_policy()
    assert c.calls[-1][1]['approvalPolicy'] == 'never'


def test_never_ask_policy_refuses_permission_callbacks_but_allows_questions():
    from types import SimpleNamespace
    from thechatplace.providers.codex_policy import CodexPolicy
    from thechatplace.providers.codex_requests import COMMAND, FILES, QUESTIONS
    real, original, _ = runtime_connection()
    real._accept_owned_thread(replace(original, policy=CodexPolicy(approval='never')))
    assert not real._accept_server_request(SimpleNamespace(thread_id='owned', method=COMMAND))
    assert not real._accept_server_request(SimpleNamespace(thread_id='owned', method=FILES))
    assert real._accept_server_request(SimpleNamespace(thread_id='owned', method=QUESTIONS))


def test_retargeted_workspace_blocks_turn_before_any_rpc(tmp_path, monkeypatch):
    from thechatplace import platform_paths
    from thechatplace.providers.codex_policy import CodexPolicy
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write')
    real, original, params = runtime_connection()
    proof = replace(original, cwd=policy.workspace_root, policy=policy)
    real._accept_owned_thread(proof)
    params.update(cwd=proof.cwd, sandboxPolicy=policy.turn_policy())
    monkeypatch.setattr(platform_paths, 'codex_workspace_root', lambda cwd: policy.workspace_root + '-changed')
    monkeypatch.setattr(real, 'request', lambda *args: pytest.fail('Changed scope must fail before RPC'))
    with pytest.raises(CodexRpcError, match='workspace could not be verified'):
        real._verify_turn_route(params)
