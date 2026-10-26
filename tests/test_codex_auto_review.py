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


class SavedHistoryConnection(AutoConnection):
    """An empty loaded thread has no rollout to list or resume."""
    def __init__(self):
        super().__init__()
        self.saved_threads = set()

    def request(self, method, params):
        if (method in ('thread/resume', 'thread/turns/list')
                and params['threadId'] not in self.saved_threads):
            self.calls.append((method, params))
            raise CodexRpcError('Synthetic thread has no saved rollout')
        result = super().request(method, params)
        if method == 'turn/start':
            self.saved_threads.add(params['threadId'])
        return result


@pytest.mark.parametrize('failure', ['save', 'verification'])
@pytest.mark.parametrize('recovery', ['reload', 'retry'])
def test_unloaded_policy_failure_retains_recovery_of_the_saved_mode(tmp_path, monkeypatch, failure, recovery):
    class UnloadedConnection(SavedHistoryConnection):
        unloaded = False

        def request(self, method, params):
            if method == 'thread/resume' and self.unloaded:
                from thechatplace.providers.codex_policy import CodexPolicy
                self.policies[params['threadId']] = CodexPolicy.choose(params['cwd'], params['sandbox'],
                    params['approvalPolicy'], params['config']['approvals_reviewer'])
                self.unloaded = False
            return super().request(method, params)

    connection = UnloadedConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        native = record.cli_session_id
        runtime.send(record, 'Initial synthetic turn')
        connection.events.append(decode_event('turn/completed', {
            'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
        wait(lambda: runtime.snapshot(native).outcome == 'completed')
        runtime._proofs.clear()
        connection.proofs.clear()
        connection.unloaded = True
        request = connection.request

        def refuse_check(method, params):
            if method == 'thread/start' and params.get('ephemeral'):
                raise CodexRpcError('Synthetic verification failure')
            return request(method, params)

        if failure == 'verification':
            monkeypatch.setattr(connection, 'request', refuse_check)
            with pytest.raises(CodexRpcError):
                runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
            monkeypatch.setattr(connection, 'request', request)
        else:
            change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
            runtime.finish_policy(change, False)
        assert not runtime.ready(record)
        assert connection.policies[native].reviewer == 'auto_review'
        if recovery == 'reload':
            restored = runtime.reopen(record)
            assert restored.provider_options == record.provider_options
        else:
            change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
            restored = change.record()
            runtime.finish_policy(change, True)
        assert runtime.ready(restored)
        assert len([method for method, _ in connection.calls if method == 'thread/resume']) == 1
        assert connection.turns == 1
        runtime.send(restored, 'Deliberate message after recovery')
        sent = [params for method, params in connection.calls if method == 'turn/start'][-1]
        assert sent['approvalsReviewer'] == restored.provider_options.get('approvals_reviewer', 'user')
    finally:
        runtime.close()


@pytest.mark.parametrize('saved', [False, True])
def test_preparation_keeps_the_saved_runtime_owner_until_persistence(tmp_path, saved):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        original = runtime._proofs[record.cli_session_id]
        change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        assert connection.owns_runtime(original)
        assert not connection.owns_runtime(change.proof)
        assert not runtime.ready(record)
        runtime.finish_policy(change, saved)
        assert connection.owns_runtime(change.proof if saved else original)
        assert not any(method == 'turn/start' for method, _ in connection.calls)
    finally:
        runtime.close()


def test_failed_policy_save_after_reconnect_recovers_the_actual_saved_mode(tmp_path):
    class FreshConnection(SavedHistoryConnection):
        def request(self, method, params):
            if method == 'thread/resume':
                from thechatplace.providers.codex_policy import CodexPolicy
                policies = self.__dict__.setdefault('policies', {})
                policies.setdefault(params['threadId'], CodexPolicy.choose(params['cwd'], params['sandbox'],
                    params['approvalPolicy'], params['config']['approvals_reviewer']))
            return super().request(method, params)

    first, second = SavedHistoryConnection(), FreshConnection()
    second.created = 100  # Native ids are unique across real server processes.
    connections = iter((first, second))
    runtime = CodexChatRuntime(factory=lambda exe: next(connections))
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        native = record.cli_session_id
        runtime.send(record, 'Initial synthetic turn')
        first.events.append(decode_event('turn/completed', {
            'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
        wait(lambda: runtime.snapshot(native).outcome == 'completed')
        change = runtime.prepare_policy(record, 'read-only', 'on-request', 'auto_review')
        saved = change.record()
        first.close()
        wait(lambda: bool(runtime.snapshot(native).problem))
        runtime.finish_policy(change, True)
        assert not runtime.ready(saved)
        runtime.create(str(tmp_path), 'Reconnect the shared runtime')
        second.saved_threads.add(native)
        change = runtime.prepare_policy(saved, 'workspace-write', 'on-request', 'user')
        runtime.finish_policy(change, False)
        restored = runtime.reopen(saved)
        assert restored.provider_options == saved.provider_options
        assert runtime.ready(restored)
        assert second.turns == 0
        runtime.send(restored, 'Deliberate message after recovery')
        sent = [params for method, params in second.calls if method == 'turn/start'][-1]
        assert sent['approvalsReviewer'] == 'auto_review'
    finally:
        runtime.close()


@pytest.mark.parametrize('has_history', [False, True])
@pytest.mark.parametrize('recovery', ['reload', 'retry'])
def test_failed_permission_save_recovers_the_loaded_chat(tmp_path, has_history, recovery):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        native = record.cli_session_id
        if has_history:
            runtime.send(record, 'Initial synthetic turn')
            connection.events.append(decode_event('turn/completed', {
                'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
            wait(lambda: runtime.snapshot(native).outcome == 'completed')
            change = runtime.prepare_policy(record, 'read-only', 'on-request', 'auto_review')
            record = change.record()
            runtime.finish_policy(change, True)
        reviewer = 'user' if has_history else 'auto_review'
        change = runtime.prepare_policy(record, 'workspace-write', 'on-request', reviewer)
        runtime.finish_policy(change, False)
        assert not runtime.ready(record)
        assert runtime.stop(record) == ()
        if recovery == 'reload':
            restored = runtime.reopen(record)
            assert restored.provider_options == record.provider_options
        else:
            change = runtime.prepare_policy(record, 'workspace-write', 'on-request', reviewer)
            restored = change.record()
            runtime.finish_policy(change, True)
        assert runtime.ready(restored)
        assert restored.cli_session_id == native
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
        assert connection.turns == int(has_history)
        if not has_history:
            from thechatplace.providers.codex import EMPTY_HISTORY
            assert runtime.history_route(native) is EMPTY_HISTORY
        runtime.send(restored, 'Deliberate message after recovery')
        sent = [params for method, params in connection.calls if method == 'turn/start'][-1]
        assert sent['approvalsReviewer'] == restored.provider_options.get('approvals_reviewer', 'user')
        assert connection.turns == int(has_history) + 1
    finally:
        runtime.close()


@pytest.mark.parametrize('recovery', ['reload', 'retry'])
@pytest.mark.parametrize('failure', ['active', 'wrong-thread', 'unfinished-history', 'unknown-history',
                                   'account', 'route', 'model', 'reviewer', 'owned-tools'])
def test_policy_recovery_rechecks_native_state_and_saved_grant(tmp_path, monkeypatch, recovery, failure):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat', approvals_reviewer='auto_review')
        native = record.cli_session_id
        runtime.send(record, 'Initial synthetic turn')
        connection.events.append(decode_event('turn/completed', {
            'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
        wait(lambda: runtime.snapshot(native).outcome == 'completed')
        change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        runtime.finish_policy(change, False)
        request = connection.request

        def changed(method, params):
            result = request(method, params)
            if method == 'thread/read' and failure == 'active':
                result['thread']['status'] = {'type': 'active', 'activeFlags': []}
            elif method == 'thread/read' and failure == 'wrong-thread':
                result['thread']['id'] = 'different-thread'
            elif method == 'thread/turns/list' and failure.endswith('-history'):
                result['data'] = [{'id': 'foreign-turn', 'status':
                                   'inProgress' if failure == 'unfinished-history' else 'future-status'}]
            elif method == 'account/read' and failure == 'account':
                result['account']['type'] = 'apiKey'
            elif method == 'config/read' and failure == 'route':
                result['config']['model_provider'] = 'different-provider'
            elif method == 'model/list' and failure == 'model':
                result['data'] = []
            elif method == 'thread/start' and failure == 'reviewer':
                result['approvalsReviewer'] = 'user'
            elif method == 'mcpServerStatus/list' and params['threadId'] == native and failure == 'owned-tools':
                result['data'] = [{'runtimeStatus': 'connected', 'tools': {'unexpected': {}}}]
            return result

        monkeypatch.setattr(connection, 'request', changed)
        with pytest.raises((CodexRpcError, ValueError)):
            if recovery == 'reload':
                runtime.reopen(record)
            else:
                runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        assert not runtime.ready(record)
        assert connection.turns == 1
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
    finally:
        runtime.close()


@pytest.mark.parametrize('mismatch', ['cwd', 'sandbox', 'reviewer', 'model'])
def test_failed_save_recovery_cannot_reuse_a_different_saved_grant(tmp_path, mismatch):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        runtime.finish_policy(change, False)
        changed = replace(record, provider_options=dict(record.provider_options))
        if mismatch == 'cwd':
            changed.cwd = str(tmp_path / 'different-project')
        else:
            key, value = {'sandbox': ('sandbox', 'workspace-write'),
                          'reviewer': ('approvals_reviewer', 'auto_review'),
                          'model': ('model', 'different-model')}[mismatch]
            changed.provider_options[key] = value
        with pytest.raises(CodexRpcError, match='no longer match'):
            runtime.reopen(changed)
        assert not runtime.ready(record)
        assert runtime.reopen(record).provider_options == record.provider_options
        assert runtime.ready(record)
        assert connection.turns == 0
    finally:
        runtime.close()


def test_stream_failure_during_permission_save_cannot_use_policy_recovery(tmp_path, monkeypatch):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        monkeypatch.setattr(connection, 'pop_event', lambda: (_ for _ in ()).throw(CodexRpcError('Synthetic stream failure')))
        wait(lambda: bool(runtime.snapshot(record.cli_session_id).problem))
        problem = runtime.snapshot(record.cli_session_id).problem
        runtime.finish_policy(change, True)
        assert not runtime.ready(record)
        assert runtime.snapshot(record.cli_session_id).problem == problem
        assert connection.proofs[record.cli_session_id] is change.previous
        assert not connection.owns_runtime(change.proof)
        with pytest.raises(CodexRpcError):
            runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        assert connection.turns == 0
    finally:
        runtime.close()


@pytest.mark.parametrize('has_history', [False, True])
def test_unrelated_problem_after_a_saved_change_rechecks_instead_of_resuming(tmp_path, has_history):
    # A loaded thread keeps the settings it was resumed with until a turn
    # pins new ones, so thread/resume would report the old grant and Stop
    # would wrongly say automatic review is unavailable (#385 review). An
    # uncertain remembered approval latches a problem without closing the
    # connection, so the chat is still loaded and owned here.
    from thechatplace.providers.codex_rpc import CodexRememberedApprovalError
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        native = record.cli_session_id
        if has_history:
            runtime.send(record, 'Initial synthetic turn')
            connection.events.append(decode_event('turn/completed', {
                'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
            wait(lambda: runtime.snapshot(native).outcome == 'completed')
            connection.saved_threads.add(native)
        change = runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        saved = change.record()
        runtime.finish_policy(change, True)
        assert runtime.ready(saved)
        assert connection.policies[native].reviewer == 'user'  # Not yet pinned by a turn
        runtime._problems[native] = CodexRememberedApprovalError.REASONS['kept_uncertain']
        assert connection.owns_runtime(runtime._proofs[native])
        assert not runtime.ready(saved)
        assert runtime.recover(saved) == 'rechecked'
        assert runtime.ready(saved)
        assert not runtime.snapshot(native).problem
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
        assert connection.turns == int(has_history)
        # A mismatched saved grant is refused, never resumed with overrides.
        runtime._problems[native] = 'Synthetic unrelated problem'
        with pytest.raises(CodexRpcError, match='no longer match'):
            runtime.reopen(record)
        assert not runtime.ready(saved)
    finally:
        runtime.close()


@pytest.mark.parametrize('failure', ['reviewer', 'private'])
def test_failed_permission_check_says_to_use_stop_once(tmp_path, monkeypatch, failure):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        request = connection.request

        def refuse(method, params):
            if method == 'thread/start' and params.get('ephemeral'):
                if failure == 'private':
                    raise ValueError('private backend detail')
                result = request(method, params)
                result['approvalsReviewer'] = 'user'
                return result
            return request(method, params)

        monkeypatch.setattr(connection, 'request', refuse)
        with pytest.raises((CodexRpcError, ValueError)):
            runtime.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
        problem = runtime.snapshot(record.cli_session_id).problem
        assert problem.endswith('Use Stop to recheck this chat before sending.')
        assert problem.count('Use Stop') == 1 and 'reload' not in problem.lower()
        monkeypatch.setattr(connection, 'request', request)
        assert runtime.recover(record) == 'permissions'
        assert runtime.ready(record)
    finally:
        runtime.close()


def loaded_chat_with_problem(tmp_path):
    from thechatplace.providers.codex_rpc import CodexRememberedApprovalError
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    record = runtime.create(str(tmp_path), 'Synthetic chat')
    native = record.cli_session_id
    runtime.send(record, 'Initial synthetic turn')
    connection.events.append(decode_event('turn/completed', {
        'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
    wait(lambda: runtime.snapshot(native).outcome == 'completed')
    connection.saved_threads.add(native)
    problem = CodexRememberedApprovalError.REASONS['kept_uncertain']
    with runtime._lock:
        runtime._problems[native] = problem
    return connection, runtime, record, problem


@pytest.mark.parametrize('failing', ['ephemeral-start', 'thread-read'])
def test_failed_loaded_chat_recheck_keeps_the_problem_and_can_be_retried(tmp_path, monkeypatch, failing):
    connection, runtime, record, problem = loaded_chat_with_problem(tmp_path)
    native = record.cli_session_id
    try:
        request = connection.request

        def fail(method, params):
            if ((failing == 'ephemeral-start' and method == 'thread/start' and params.get('ephemeral'))
                    or (failing == 'thread-read' and method == 'thread/read')):
                raise CodexRpcError('Synthetic recheck failure')
            return request(method, params)

        monkeypatch.setattr(connection, 'request', fail)
        with pytest.raises(CodexRpcError):
            runtime.recover(record)
        assert runtime.snapshot(native).problem == problem
        assert not runtime.ready(record)
        assert native not in runtime._reopening
        monkeypatch.setattr(connection, 'request', request)
        assert runtime.recover(record) == 'rechecked'
        assert runtime.ready(record) and not runtime.snapshot(native).problem
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
        assert connection.turns == 1
    finally:
        runtime.close()


def test_the_same_problem_set_again_during_a_recheck_is_not_cleared(tmp_path, monkeypatch):
    # Problem text is not identity: a second, identical problem raised while
    # the recheck runs must survive it (#385).
    connection, runtime, record, problem = loaded_chat_with_problem(tmp_path)
    native = record.cli_session_id
    try:
        request = connection.request

        def again(method, params):
            if method == 'thread/read':
                with runtime._lock:
                    runtime._problems[native] = problem
            return request(method, params)

        monkeypatch.setattr(connection, 'request', again)
        with pytest.raises(CodexRpcError, match='changed while checking'):
            runtime.recover(record)
        assert runtime.snapshot(native).problem == problem
        assert not runtime.ready(record)
        assert native not in runtime._reopening
        assert connection.turns == 1
    finally:
        runtime.close()


def test_unrelated_problem_on_an_owned_chat_with_work_is_refused(tmp_path):
    connection = SavedHistoryConnection()
    runtime = CodexChatRuntime(factory=lambda exe: connection)
    try:
        record = runtime.create(str(tmp_path), 'Synthetic chat')
        native = record.cli_session_id
        runtime.send(record, 'Initial synthetic turn')
        runtime._problems[native] = 'Synthetic unrelated problem'
        with pytest.raises(CodexRpcError, match='work in progress'):
            runtime.reopen(record)
        assert not any(method == 'thread/resume' for method, _ in connection.calls)
        assert connection.turns == 1
    finally:
        runtime.close()


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
        assert 'Use Stop' in runtime.snapshot(record.cli_session_id).problem
        with pytest.raises(CodexRpcError):
            runtime.send(record, 'Must not send')
        assert not any(m == 'turn/start' for m, p in connection.calls)
        assert runtime.recover(record) == 'permissions'
        assert runtime.ready(record)
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
