from copy import deepcopy

import pytest

from thechatplace.own_store import OwnSession
from thechatplace.providers.codex_owned import CodexLifecycleConnection, CodexOwnedThreads, CHATGPT_BASE_URL
from thechatplace.providers.codex_rpc import CodexRpcError
from thechatplace.sessions import SessionInfo


def response(native_id='new'):
    return {'thread': {'id': native_id, 'createdAt': 100, 'status': {'type': 'idle'}, 'ephemeral': False},
            'cwd': '/workspace', 'model': 'gpt-default', 'modelProvider': 'openai',
            'sandbox': {'type': 'readOnly'}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}


class Connection:
    def __init__(self, result=None, account=None, config=None):
        self.calls = []
        self.result = response() if result is None else result
        self.account = {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True} if account is None else account
        self.config = {} if config is None else config

    def request(self, method, params):
        self.calls.append((method, deepcopy(params)))
        if method == 'account/read':
            return self.account
        if method == 'config/read':
            return {'config': self.config}
        if method == 'thread/turns/list':
            return {'data': []}
        return deepcopy(self.result)


def test_new_owned_thread_pins_runtime_options_and_returns_fresh_store_records():
    connection = Connection()
    lease = CodexOwnedThreads(connection).start('/workspace', 'Local title')
    assert [m for m, p in connection.calls] == ['account/read', 'config/read', 'thread/start']
    params = connection.calls[-1][1]
    assert params['modelProvider'] == 'openai' and not params['ephemeral']
    assert params['config'] == {'model_provider': 'openai', 'model_providers': {},
        'forced_login_method': 'chatgpt', 'openai_base_url': None,
        'chatgpt_base_url': CHATGPT_BASE_URL, 'approvals_reviewer': 'user'}
    record = lease.record()
    assert record.provider == 'codex' and record.started and record.created_ms == 100_000
    record.provider_options['model'] = 'changed'
    assert lease.record().provider_options['model'] == 'gpt-default'
    assert 'turn/start' not in CodexLifecycleConnection.METHODS


def test_external_chat_is_forked_into_another_identity_and_not_resumed():
    source = SessionInfo('external', 'codex:source', 'Source', '/workspace', 'source', provider='codex')
    connection = Connection()
    lease = CodexOwnedThreads(connection).fork(source, 'Copy')
    assert lease.native_id != source.cli_session_id and source.title == 'Source'
    assert connection.calls[-1][0] == 'thread/fork'
    assert connection.calls[-1][1]['threadId'] == 'source'
    assert not any(m == 'thread/resume' for m, p in connection.calls)


def test_owned_resume_retains_native_identity_and_chosen_model():
    connection = Connection(response('owned'))
    record = OwnSession('owned', 'Owned', '/workspace', provider='codex', provider_options={'model': 'gpt-default'})
    lease = CodexOwnedThreads(connection).resume(record)
    assert lease.native_id == 'owned'
    assert connection.calls[-1][0] == 'thread/resume'
    assert connection.calls[-1][1]['model'] == 'gpt-default'
    assert 'ephemeral' not in connection.calls[-1][1]


@pytest.mark.parametrize('status,latest', [('active', []), ('notLoaded', [{'status': 'inProgress'}]),
                                         ('idle', [{'status': 'unknown'}]), ('idle', None)])
def test_resume_never_overwrites_unfinished_or_uncertain_native_turn(status, latest):
    class BusyConnection(Connection):
        def request(self, method, params):
            if method == 'thread/turns/list':
                self.calls.append((method, params))
                return {'data': latest}
            return super().request(method, params)
    result = response('owned')
    result['thread']['status']['type'] = status
    connection = BusyConnection(result)
    record = OwnSession('owned', 'Owned', '/workspace', provider='codex')
    with pytest.raises(CodexRpcError, match='not confirmed idle'):
        CodexOwnedThreads(connection).resume(record)
    assert not any(method == 'thread/resume' for method, _ in connection.calls)


@pytest.mark.parametrize('record', [
    SessionInfo('external', 'codex:source', 'Source', '/workspace', 'source', provider='codex'),
    OwnSession('owned', 'Claude', '/workspace'),
    OwnSession('owned', 'Not started', '/workspace', provider='codex', started=False),
    OwnSession('owned', 'Options', '/workspace', provider='codex', provider_options={'sandbox': 'danger-full-access'})])
def test_resume_refuses_unowned_or_unsupported_records_before_rpc(record):
    connection = Connection()
    with pytest.raises(ValueError):
        CodexOwnedThreads(connection).resume(record)
    assert not connection.calls


@pytest.mark.parametrize('account', [
    {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True},
    {'account': None, 'requiresOpenaiAuth': True},
    {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': False}])
def test_account_gate_precedes_any_thread_creation(account):
    connection = Connection(account=account)
    with pytest.raises(CodexRpcError, match='ChatGPT sign-in'):
        CodexOwnedThreads(connection).start('/workspace', 'Title')
    assert [m for m, p in connection.calls] == ['account/read']


@pytest.mark.parametrize('config', [
    {'model_provider': 'custom'}, {'openai_base_url': 'alternate'},
    {'chatgpt_base_url': 'alternate'}, {'forced_login_method': 'api'},
    {'model_providers': {'openai': {'env_key': 'ALTERNATE'}}}, {'model_providers': []}])
def test_route_gate_refuses_alternate_endpoints_before_mutation(config):
    connection = Connection(config=config)
    with pytest.raises(CodexRpcError, match='default ChatGPT route'):
        CodexOwnedThreads(connection).start('/workspace', 'Title')
    assert [m for m, p in connection.calls] == ['account/read', 'config/read']


@pytest.mark.parametrize('field,value', [('modelProvider', 'custom'), ('model', ''),
    ('sandbox', {'type': 'dangerFullAccess'}), ('approvalPolicy', 'never'),
    ('approvalsReviewer', 'auto_review'), ('cwd', ''), ('thread', {'id': 'bad/path'})])
def test_inconsistent_creation_never_produces_an_owned_record(field, value):
    result = response()
    result[field] = value
    connection = Connection(result)
    with pytest.raises(CodexRpcError, match='could not be verified'):
        CodexOwnedThreads(connection).start('/workspace', 'Title')
    assert len(connection.calls) == 3  # no automatic replay


def test_fork_cannot_adopt_source_and_resume_cannot_change_identity():
    source = SessionInfo('external', 'codex:source', 'Source', '/workspace', 'source', provider='codex')
    with pytest.raises(CodexRpcError):
        CodexOwnedThreads(Connection(response('source'))).fork(source, 'Copy')
    record = OwnSession('owned', 'Owned', '/workspace', provider='codex')
    with pytest.raises(CodexRpcError):
        CodexOwnedThreads(Connection(response('other'))).resume(record)


def test_ephemeral_probe_thread_cannot_be_saved_as_resumable():
    result = response()
    result['thread']['ephemeral'] = True
    lease = CodexOwnedThreads(Connection(result)).start('/workspace', 'Probe', ephemeral=True)
    with pytest.raises(ValueError, match='cannot be saved'):
        lease.record()


@pytest.mark.parametrize('returned', [None, 0, 1, 'false', True, 'missing'])
def test_persistent_creation_requires_confirmed_boolean_persistence(returned):
    result = response()
    if returned == 'missing':
        result['thread'].pop('ephemeral')
    else:
        result['thread']['ephemeral'] = returned
    with pytest.raises(CodexRpcError, match='could not be verified'):
        CodexOwnedThreads(Connection(result)).start('/workspace', 'Title')


def test_ephemeral_request_and_owned_resume_reject_opposite_persistence():
    with pytest.raises(CodexRpcError):
        CodexOwnedThreads(Connection()).start('/workspace', 'Probe', ephemeral=True)
    result = response('owned')
    result['thread']['ephemeral'] = True
    record = OwnSession('owned', 'Owned', '/workspace', provider='codex')
    with pytest.raises(CodexRpcError):
        CodexOwnedThreads(Connection(result)).resume(record)


class ModelConnection(Connection):
    def request(self, method, params):
        if method == 'model/list':
            self.calls.append((method, deepcopy(params)))
            return {'data': [{'id': 'model-id', 'model': 'gpt-default', 'displayName': 'Synthetic',
                'hidden': False, 'isDefault': True, 'defaultReasoningEffort': 'future-effort',
                'supportedReasoningEfforts': [{'reasoningEffort': 'future-effort'}]}]}
        return super().request(method, params)


def test_selected_model_and_effort_are_verified_persisted_and_restored():
    result = response('owned')
    result['reasoningEffort'] = 'future-effort'
    connection = ModelConnection(result)
    lifecycle = CodexOwnedThreads(connection)
    proof = lifecycle.start('/workspace', 'Owned', model='gpt-default', effort='future-effort')
    assert connection.calls[-1][1]['config']['model_reasoning_effort'] == 'future-effort'
    assert proof.effort == 'future-effort'
    record = proof.record()
    assert record.provider_options['effort'] == 'future-effort'
    assert lifecycle.resume(record).effort == 'future-effort'
    assert connection.calls[-1][0] == 'thread/resume'


@pytest.mark.parametrize('model,effort', [('removed', None), ('gpt-default', 'removed-effort')])
def test_unadvertised_selection_cannot_create_a_thread(model, effort):
    connection = ModelConnection()
    with pytest.raises(ValueError, match='no longer available'):
        CodexOwnedThreads(connection).start('/workspace', 'Owned', model=model, effort=effort)
    assert not any(method == 'thread/start' for method, _ in connection.calls)


def test_selected_effort_mismatch_never_grants_runtime_proof():
    connection = ModelConnection()
    with pytest.raises(CodexRpcError, match='could not be verified'):
        CodexOwnedThreads(connection).start('/workspace', 'Owned', model='gpt-default', effort='future-effort')


def test_effort_without_model_is_rejected_before_rpc():
    connection = ModelConnection()
    with pytest.raises(ValueError):
        CodexOwnedThreads(connection).start('/workspace', 'Owned', effort='future-effort')
    assert not connection.calls


def test_fork_pins_selection_without_resuming_the_source():
    result = response('new-owned')
    result['reasoningEffort'] = 'future-effort'
    connection = ModelConnection(result)
    source = SessionInfo('external', 'codex:source', 'Source', '/workspace', 'source', provider='codex')
    proof = CodexOwnedThreads(connection).fork(source, 'Copy', model='gpt-default', effort='future-effort')
    assert proof.native_id == 'new-owned'
    assert connection.calls[-1][0] == 'thread/fork'
    assert connection.calls[-1][1]['threadId'] == 'source'
    assert connection.calls[-1][1]['config']['model_reasoning_effort'] == 'future-effort'
    assert not any(method == 'thread/resume' for method, _ in connection.calls)


def test_workspace_policy_is_pinned_persisted_and_restored_without_extra_roots(tmp_path):
    from thechatplace.providers.codex_policy import CodexPolicy
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write', 'never')
    result = response('owned'); result.update(cwd=policy.workspace_root,
        sandbox={**policy.turn_policy(), 'writableRoots': []}, approvalPolicy='never')
    connection = Connection(result)
    lifecycle = CodexOwnedThreads(connection)
    proof = lifecycle.start(str(tmp_path), 'Owned', sandbox='workspace-write', approval_policy='never')
    assert connection.calls[-1][1]['sandbox'] == 'workspace-write'
    assert connection.calls[-1][1]['config']['sandbox_workspace_write']['writable_roots'] == []
    record = proof.record()
    assert record.provider_options['sandbox'] == 'workspace-write'
    assert record.provider_options['approval_policy'] == 'never'
    assert lifecycle.resume(record).policy == policy
    assert connection.calls[-1][0] == 'thread/resume'


@pytest.mark.parametrize('selection', [{'sandbox': 'danger-full-access'}, {'approval_policy': 'on-failure'}])
def test_unsupported_policy_choices_fail_before_any_lifecycle_rpc(selection):
    connection = Connection()
    with pytest.raises(ValueError):
        CodexOwnedThreads(connection).start('/workspace', 'Owned', **selection)
    assert not connection.calls


def test_returned_alias_is_not_saved_as_the_writable_scope(tmp_path, monkeypatch):
    from thechatplace import platform_paths
    canonical = str(tmp_path.resolve())
    alias = str(tmp_path / 'alias')
    resolve = platform_paths.codex_workspace_root
    monkeypatch.setattr(platform_paths, 'codex_workspace_root',
                        lambda cwd: canonical if cwd == alias else resolve(cwd))
    result = response('owned')
    result.update(cwd=alias, sandbox={'type': 'workspaceWrite', 'writableRoots': [],
        'networkAccess': False, 'excludeSlashTmp': True, 'excludeTmpdirEnvVar': True})
    proof = CodexOwnedThreads(Connection(result)).start(canonical, 'Owned', sandbox='workspace-write')
    assert proof.cwd == canonical
    assert proof.record().cwd == canonical
    assert proof.record().provider_options['workspace_root'] == canonical


@pytest.mark.parametrize('saved_root', [None, '/different-grant'])
def test_missing_or_changed_saved_write_scope_refuses_resume_before_rpc(tmp_path, saved_root):
    options = {'sandbox': 'workspace-write', 'approval_policy': 'on-request'}
    if saved_root is not None:
        options['workspace_root'] = saved_root
    record = OwnSession('owned', 'Owned', str(tmp_path), provider='codex',
                        started=True, provider_options=options)
    connection = Connection()
    with pytest.raises((ValueError, CodexRpcError)):
        CodexOwnedThreads(connection).resume(record)
    assert connection.calls == []


def test_retargeted_saved_workspace_refuses_resume_before_rpc(tmp_path, monkeypatch):
    from thechatplace import platform_paths
    root = str(tmp_path.resolve())
    record = OwnSession('owned', 'Owned', root, provider='codex', started=True,
        provider_options={'sandbox': 'workspace-write', 'workspace_root': root})
    monkeypatch.setattr(platform_paths, 'codex_workspace_root', lambda cwd: root + '-retargeted')
    connection = Connection()
    with pytest.raises(CodexRpcError, match='workspace could not be verified'):
        CodexOwnedThreads(connection).resume(record)
    assert connection.calls == []
