from copy import deepcopy
from dataclasses import replace

import pytest

from thechatplace.providers.codex_policy import CodexPolicy
from thechatplace.providers.codex_rpc import CodexRpcError


def result(policy):
    return {'cwd': policy.workspace_root, 'approvalPolicy': policy.approval,
            'sandbox': policy.turn_policy()}


def test_workspace_scope_is_canonical_and_configuration_cannot_leak_mutation(tmp_path):
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write', 'never')
    assert policy.workspace_root == str(tmp_path.resolve())
    policy.verify(result(policy))
    settings = policy.config()
    settings['sandbox_workspace_write']['writable_roots'].append('/elsewhere')
    assert policy.config()['sandbox_workspace_write']['writable_roots'] == []
    assert policy.turn_policy()['networkAccess'] is False
    assert policy.turn_policy()['excludeSlashTmp'] is True
    assert policy.turn_policy()['excludeTmpdirEnvVar'] is True


@pytest.mark.parametrize('field,value', [('networkAccess', True), ('networkAccess', 0),
    ('excludeSlashTmp', False), ('excludeTmpdirEnvVar', 1), ('writableRoots', [str('/elsewhere'), str('/another')]),
    ('writableRoots', ['/elsewhere']), ('type', 'dangerFullAccess')])
def test_broader_or_unverified_returned_policies_are_rejected(tmp_path, field, value):
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write')
    response = result(policy)
    response['sandbox'][field] = value
    with pytest.raises(CodexRpcError, match='policy could not be verified'):
        policy.verify(response)


def test_wrong_cwd_or_approval_cannot_grant_a_proof(tmp_path):
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write')
    other = tmp_path / 'other'; other.mkdir()
    for field, value in [('cwd', str(other)), ('approvalPolicy', 'never')]:
        response = result(policy); response[field] = value
        with pytest.raises(CodexRpcError):
            policy.verify(response)


@pytest.mark.parametrize('sandbox,approval', [('danger-full-access', 'on-request'),
    ('externalSandbox', 'never'), ('workspace-write', 'on-failure')])
def test_unsupported_choices_fail_before_policy_creation(tmp_path, sandbox, approval):
    with pytest.raises(ValueError):
        CodexPolicy.choose(str(tmp_path), sandbox, approval)


def test_readonly_policy_does_not_accept_network_or_workspace_write():
    policy = CodexPolicy()
    policy.verify({'sandbox': {'type': 'readOnly'}, 'approvalPolicy': 'on-request'})
    for sandbox in [{'type': 'workspaceWrite'}, {'type': 'readOnly', 'networkAccess': True}]:
        with pytest.raises(CodexRpcError):
            policy.verify({'sandbox': sandbox, 'approvalPolicy': 'on-request'})


def test_missing_relative_file_and_root_workspaces_are_not_write_scopes(tmp_path):
    file = tmp_path / 'file'; file.write_text('synthetic')
    for cwd in ['relative-folder', str(file), str(tmp_path / 'missing'), tmp_path.anchor]:
        with pytest.raises((ValueError, OSError)):
            CodexPolicy.choose(cwd, 'workspace-write')


def test_implicit_cwd_native_policy_is_verified(tmp_path):
    policy = CodexPolicy.choose(str(tmp_path), 'workspace-write')
    response = result(policy)
    response['sandbox']['writableRoots'] = []
    policy.verify(response)


def test_removed_workspace_cannot_retain_write_permission(tmp_path):
    folder = tmp_path / 'workspace'
    folder.mkdir()
    policy = CodexPolicy.choose(str(folder), 'workspace-write')
    folder.rmdir()
    with pytest.raises(CodexRpcError, match='workspace could not be verified'):
        policy.verify_workspace(str(folder))


def test_home_and_its_parents_cannot_be_write_scopes(tmp_path, monkeypatch):
    from pathlib import Path
    home = tmp_path / 'users' / 'person'
    home.mkdir(parents=True)
    project = home / 'project'; project.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: home)
    for root in (home, home.parent, tmp_path):
        with pytest.raises(ValueError):
            CodexPolicy.choose(str(root), 'workspace-write')
    assert CodexPolicy.choose(str(project), 'workspace-write').workspace_root == str(project)
    assert CodexPolicy.choose(str(home), 'read-only').sandbox == 'read-only'
