"""Automatic review stays a verified per-chat choice, never an allow-all (#358)."""
from dataclasses import replace

import pytest

from test_codex_owned import Connection, response
from test_codex_execution import runtime_connection
from test_codex_runtime import Connection as RuntimeConnection, wait
from thechatplace.providers.codex_owned import CodexOwnedThreads
from thechatplace.providers.codex_events import CodexTurnBuffer, decode_event
from thechatplace.providers.codex_permissions import CodexReviewStore
from thechatplace.providers.codex_rpc import CodexRpcError, CodexTurnRejected
from thechatplace.providers.codex_runtime import CodexChatRuntime


def auto_response(native='owned'):
    result = response(native)
    result['approvalsReviewer'] = 'auto_review'
    return result


def test_creation_fork_and_restart_keep_the_explicit_reviewer():
    connection = Connection(auto_response())
    lifecycle = CodexOwnedThreads(connection)
    proof = lifecycle.start('/workspace', 'Owned', approvals_reviewer='auto_review')
    record = proof.record()
    assert record.provider_options['approvals_reviewer'] == 'auto_review'
    assert lifecycle.resume(record).policy.reviewer == 'auto_review'
    connection.result = auto_response('copy')
    forked = lifecycle.fork(record.to_info(), 'Copy', approvals_reviewer='auto_review')
    assert forked.policy.reviewer == 'auto_review'
    for method, params in connection.calls:
        if method in ('thread/start', 'thread/resume', 'thread/fork'):
            assert params['config']['approvals_reviewer'] == 'auto_review'
            assert params['approvalPolicy'] == 'on-request'
            assert params['sandbox'] == 'read-only'


def test_saved_automatic_chat_survives_a_real_store_reload(tmp_path):
    from thechatplace.own_store import OwnSessionStore
    record = CodexOwnedThreads(Connection(auto_response())).start(
        '/workspace', 'Owned', approvals_reviewer='auto_review').record()
    store = OwnSessionStore(tmp_path / 'own.json')
    store.add(record)
    restored = OwnSessionStore(tmp_path / 'own.json').get('owned', 'codex')
    assert restored is not None
    assert restored.provider_options['approvals_reviewer'] == 'auto_review'


@pytest.mark.parametrize('reviewer', [None, 'user', 'future-reviewer'])
def test_unconfirmed_automatic_review_never_silently_falls_back(reviewer):
    result = response()
    result['approvalsReviewer'] = reviewer
    connection = Connection(result)
    with pytest.raises(CodexTurnRejected, match='could not confirm automatic'):
        CodexOwnedThreads(connection).start('/workspace', 'Title', approvals_reviewer='auto_review')
    assert not any(method == 'turn/start' for method, params in connection.calls)


def test_unsupported_or_managed_rejection_has_a_readable_reason():
    class Refused(Connection):
        def request(self, method, params):
            if method == 'thread/start':
                raise CodexRpcError('private server error')
            return super().request(method, params)
    with pytest.raises(CodexTurnRejected, match='managed policy') as error:
        CodexOwnedThreads(Refused()).start('/workspace', 'Title', approvals_reviewer='auto_review')
    assert 'private' not in str(error.value)


@pytest.mark.parametrize('reviewer,approval', [('auto_review', 'never'), ('anything', 'on-request')])
def test_never_is_not_automatic_review(reviewer, approval):
    connection = Connection()
    with pytest.raises(ValueError):
        CodexOwnedThreads(connection).start('/workspace', 'Title', approvals_reviewer=reviewer, approval_policy=approval)
    assert connection.calls == []


def test_turns_pin_the_verified_reviewer_and_keep_billing_checks():
    connection, proof, params = runtime_connection()
    auto = replace(proof, policy=replace(proof.policy, reviewer='auto_review'))
    connection._accept_owned_thread(auto)
    with pytest.raises(CodexRpcError, match='options do not match'):
        connection._verify_turn_route(params)
    params['approvalsReviewer'] = 'auto_review'
    connection._verify_turn_route(params)
    params['model'] = 'unverified-model'
    with pytest.raises(CodexRpcError):
        connection._verify_turn_route(params)


class AutoConnection(RuntimeConnection):
    def request(self, method, params):
        result = super().request(method, params)
        if method in ('thread/start', 'thread/fork', 'thread/resume'):
            from thechatplace.providers.codex_policy import CodexPolicy
            policies = self.__dict__.setdefault('policies', {})
            native = result['thread']['id']
            # A loaded thread ignores resume overrides, including the
            # reviewer. A fresh thread uses the requested configuration.
            if method != 'thread/resume':
                policies[native] = CodexPolicy.choose(params['cwd'], params['sandbox'],
                    params['approvalPolicy'], params['config']['approvals_reviewer'])
            policy = policies[native]
            result.update(cwd=params['cwd'], sandbox=policy.turn_policy(),
                          approvalPolicy=policy.approval, approvalsReviewer=policy.reviewer)
        elif method == 'turn/start':
            self.policies[params['threadId']] = self.proofs[params['threadId']].policy
        return result


def test_policy_change_waits_for_storage_and_does_not_affect_other_chats():
    connection = AutoConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        one = runtime.create('/workspace', 'One')
        two = runtime.create('/workspace', 'Two')
        change = runtime.prepare_policy(one, 'read-only', 'on-request', 'auto_review')
        assert not runtime.ready(one)
        automatic = change.record()
        runtime.finish_policy(change, True)
        runtime.send(automatic, 'First')
        runtime.send(two, 'Second')
        turns = [p for m, p in connection.calls if m == 'turn/start']
        assert [p['approvalsReviewer'] for p in turns] == ['auto_review', 'user']
        with pytest.raises(CodexRpcError, match='work in progress'):
            runtime.prepare_policy(automatic, 'read-only', 'on-request', 'user')
    finally:
        runtime.close()


def test_failed_save_does_not_activate_automatic_review():
    connection = AutoConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create('/workspace', 'Chat')
        change = runtime.prepare_policy(record, 'read-only', 'on-request', 'auto_review')
        runtime.finish_policy(change, False)
        assert not runtime.ready(record)
        with pytest.raises(CodexRpcError):
            runtime.send(record, 'Must not send')
        assert not any(m == 'turn/start' for m, p in connection.calls)
    finally:
        runtime.close()


@pytest.mark.parametrize('has_history', [False, True])
def test_permission_changes_keep_the_chat_and_apply_to_its_next_turn(tmp_path, has_history):
    connection = AutoConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Original chat')
        native = record.cli_session_id
        if has_history:
            runtime.send(record, 'Initial synthetic turn')
            connection.events.append(decode_event('turn/completed', {
                'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
            wait(lambda: runtime.snapshot(native).outcome == 'completed')
        for sandbox, reviewer in [('workspace-write', 'auto_review'), ('read-only', 'user')]:
            change = runtime.prepare_policy(record, sandbox, 'on-request', reviewer)
            assert not runtime.ready(record)
            record = change.record()
            runtime.finish_policy(change, True)
            assert record.cli_session_id == native
            runtime.send(record, 'Synthetic message after changing permissions')
            params = [p for m, p in connection.calls if m == 'turn/start'][-1]
            assert params['threadId'] == native
            assert params['approvalsReviewer'] == reviewer
            assert params['sandboxPolicy']['type'] == ('workspaceWrite' if sandbox == 'workspace-write' else 'readOnly')
            connection.events.append(decode_event('turn/completed', {
                'threadId': native, 'turn': {'id': f'turn-{connection.turns}', 'status': 'completed'}}))
            wait(lambda: runtime.snapshot(native).outcome == 'completed')
        assert not any(m == 'thread/resume' for m, _ in connection.calls)
        assert set(connection.proofs) == {native}
    finally:
        runtime.close()


def review_params(status='denied', **changes):
    return dict(threadId='owned', turnId='turn-1', reviewId='review-1',
                action={'type': 'command', 'command': 'synthetic-command'},
                review={'status': status, 'rationale': 'Synthetic review reason.'}, **changes)


@pytest.mark.parametrize('status', ['approved', 'denied', 'timedOut', 'aborted'])
def test_decisions_are_readable_bounded_and_isolated_from_stale_turns(status):
    event = decode_event('item/autoApprovalReview/completed', review_params(status))
    assert 'Synthetic review reason.' in event.text
    buffer = CodexTurnBuffer('owned', 'turn-1')
    assert not buffer.apply(replace(event, turn_id='old'))
    assert not buffer.apply(replace(event, thread_id='other-chat'))
    assert buffer.apply(event)
    assert not buffer.apply(event)
    assert list(buffer.review_notices.values()) == [event.text]
    assert decode_event('item/autoApprovalReview/completed', review_params('future-status')) is None


def test_decisions_are_announced_data_and_persist_without_granting_anything(tmp_path):
    connection = AutoConnection()
    updates = []
    runtime = CodexChatRuntime(updates.append, factory=lambda exe: connection)
    try:
        record = runtime.create('/workspace', 'Automatic', approvals_reviewer='auto_review')
        runtime.send(record, 'Synthetic message')
        params = review_params()
        params['threadId'] = record.cli_session_id
        connection.events.append(decode_event('item/autoApprovalReview/completed', params))
        wait(lambda: bool(runtime.review_store.load(record.cli_session_id)))
        assert 'denied' in runtime.snapshot(record.cli_session_id).permission_notices[-1][2]
        assert runtime.permission_store.load(record.cli_session_id) == ()
        assert len([m for m, p in connection.calls if m == 'turn/start']) == 1
        saved = CodexReviewStore(runtime.review_store.root.parent).load(record.cli_session_id)
        assert saved[-1][2].endswith('Synthetic review reason.')
        assert runtime.review_store.load('other-chat') == ()
    finally:
        runtime.close()


def test_corrupt_decision_history_is_preserved(tmp_path):
    store = CodexReviewStore(tmp_path)
    path = store._path('owned')
    path.parent.mkdir(parents=True)
    path.write_text('{"version":99}', encoding='utf-8')
    with pytest.raises(ValueError):
        store.remember('owned', 'turn', [('review', 'Allowed')])
    assert path.read_text(encoding='utf-8') == '{"version":99}'


def test_long_turn_keeps_recent_decisions_including_later_denials(tmp_path):
    buffer = CodexTurnBuffer('owned', 'turn-1')
    store = CodexReviewStore(tmp_path)
    for number in range(140):
        params = review_params('approved' if number < 139 else 'denied')
        params['reviewId'] = str(number)
        assert buffer.apply(decode_event('item/autoApprovalReview/completed', params))
    assert len(buffer.review_notices) == 128
    assert next(iter(buffer.review_notices)) == 'auto-review:12'
    store.remember('owned', 'turn-1', buffer.review_notices.items())
    assert len(store.load('owned')) == 128
    assert 'denied' in store.load('owned')[-1][2]


@pytest.mark.parametrize('server_failed', [False, True])
def test_permission_history_host_shows_decisions_and_effective_boundaries(tmp_path, server_failed):
    from test_chat_permissions import Frame, _codex
    frame = Frame(tmp_path)
    host, record = _codex(tmp_path, frame)
    record.provider_options['approvals_reviewer'] = 'auto_review'
    frame.sessions.owned_action = lambda action, own: [('turn', 'review', 'Codex automatic review denied the request.')]
    frame._load_codex_saved_rules = lambda folder, done: done(
        'Codex unavailable.' if server_failed else ([], [], 0, ''), server_failed)
    seen = []
    host.native(seen.append)
    assert 'denied the request' in seen[0]
    if server_failed:
        assert 'Codex unavailable.' in seen[0]
    assert 'automatic approval review' in host.mode_text()
    assert 'temporary folders' in host.note() and 'Network' in host.note()
