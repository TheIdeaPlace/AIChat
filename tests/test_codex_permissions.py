"""Consent matching and lifecycle, including native shell shapes (#307)."""
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest

from thechatplace.providers.codex_permissions import candidate, matches, CodexPermissionStore, NOTICE
from thechatplace.providers.codex_requests import COMMAND, decode_request
from thechatplace.providers.codex_rpc import CodexApprovalNotSent


def request(folder, command='git commit -m first', prefix=('git', 'commit'), **extra):
    return decode_request(1, COMMAND, dict(threadId='owned-1', turnId='turn', itemId='item',
        command=command, cwd=str(folder), environmentId='local',
        proposedExecpolicyAmendment=list(prefix), availableDecisions=['accept', 'decline', 'cancel'], **extra))


def test_native_wrappers_match_and_unsupported_shell_shapes_ask(tmp_path, monkeypatch):
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, 'codex_approval_shell', lambda s: {
        '/bin/zsh': 'posix', '/bin/bash': 'posix',
        'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe': 'powershell',
        'C:\\Program Files\\PowerShell\\7\\pwsh.exe': 'powershell'}.get(s, ''))
    wrappers = [("/bin/zsh -lc '{command}'"), ("/bin/bash -c '{command}'"),
                ('"C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe" -Command \'{command}\''),
                ('"C:\\\\Program Files\\\\PowerShell\\\\7\\\\pwsh.exe" -NoProfile -Command \'{command}\'')]
    for wrapper in wrappers:
        first = request(tmp_path, wrapper.format(command='git commit -m first'))
        rule = candidate(first, str(tmp_path))
        assert rule is not None
        assert matches(rule, replace(first, command=wrapper.format(command='git commit -m second'), proposed_prefix=()), str(tmp_path))
        assert not matches(rule, replace(first, command="git commit -m second"), str(tmp_path))
    assert candidate(request(tmp_path, "/bin/zsh -i -c 'git commit -m first'"), str(tmp_path)) is None


@pytest.mark.parametrize('command', [
    'git commit -m x && git status', 'git commit -m $(whoami)', 'git commit -m x; git status',
    'git commit -m x | git status', 'git commit -m x > out', 'git commit -m `whoami`',
    'git commit -m x\ngit status', 'git commit -m x @PSVersionTable', 'git commit -m "" --amend',
    'git commit -m x =ls', 'git -c core.fsmonitor=evil status', 'git push --force origin main',
    'git push origin +main', 'git push --receive-pack=evil origin main', 'git fetch --upload-pack=evil origin',
    'git add ../outside', 'git switch --force main', 'git commit --amend -m x',
    'python file.py', 'powershell -Command git', 'git log --output=evil', 'git diff --ext-diff'])
def test_changed_or_compound_operations_still_ask(tmp_path, command):
    prefix = tuple(command.split()[:2])
    assert candidate(request(tmp_path, command, prefix=prefix), str(tmp_path)) is None


def test_rules_bind_folder_and_request_kind_not_just_prefix(tmp_path):
    first = request(tmp_path)
    rule = candidate(first, str(tmp_path))
    assert rule and 'outside the sandbox' in rule.description and 'hooks' in NOTICE
    other = tmp_path / 'other'
    other.mkdir()
    assert not matches(rule, replace(first, cwd=str(other)), str(tmp_path))
    assert not matches(rule, first, str(other))
    assert not matches(rule, replace(first, extra_access=('Write access elsewhere',)), str(tmp_path))
    assert not matches(rule, replace(first, network_host='example.test'), str(tmp_path))
    assert not matches(rule, replace(first, decisions=('cancel',)), str(tmp_path))
    assert candidate(replace(first, proposed_prefix=('git',)), str(tmp_path)) is None


def test_store_restart_copy_unknown_formats_and_atomic_failure(tmp_path, monkeypatch):
    store = CodexPermissionStore(tmp_path / 'tcp')
    rule = candidate(request(tmp_path), str(tmp_path))
    store.save('chat', (rule,))
    assert CodexPermissionStore(store.root).load('chat') == (rule,)
    assert store.load('copy') == ()
    path = store._path('chat')
    original = path.read_bytes()
    from thechatplace.providers import codex_permissions
    monkeypatch.setattr(codex_permissions, 'atomic_write_json', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        store.save('chat', ())
    assert path.read_bytes() == original
    monkeypatch.undo()
    newer = dict(json.loads(original), version=2)
    path.write_text(json.dumps(newer))
    with pytest.raises(ValueError):
        store.save('chat', ())
    store.clear('chat')
    assert store.load('chat') == ()
    assert json.loads(next(store.root.glob('*.preserved-*.json')).read_text()) == newer


def prepared_runtime(tmp_path):
    from test_codex_runtime import runtime
    service, connection, updates = runtime()
    original_request = connection.request
    def in_folder(method, params):
        result = original_request(method, params)
        return dict(result, cwd=str(tmp_path)) if method == 'thread/start' else result
    connection.request = in_folder
    record = service.create(str(tmp_path), 'Synthetic permissions chat')
    proof = service._proofs[record.cli_session_id]
    req = request(tmp_path)
    class Executor:
        current = [req]
        answers = []
        failure = None
        entered = release = None
        def waiting(self, proof):
            return tuple(self.current)
        def answer(self, proof, request, answer):
            if self.entered:
                self.entered.set()
                assert self.release.wait(5)
            if self.failure:
                raise self.failure
            self.answers.append(answer)
            self.current = []
        def snapshot(self, proof):
            from thechatplace.providers.codex_execution import CodexTurnState
            return CodexTurnState('turn', 'inProgress', requests=tuple(self.current))
        def interrupt(self, proof):
            pass
    executor = Executor()
    service._executor = executor
    # Prevent the background event consumer from competing with this explicit
    # dispatch test; native/stdin tests cover event-driven dispatch separately.
    service._notify = lambda *a, **kw: None
    return service, connection, record, proof, executor, req


def test_remember_second_command_and_remove_during_turn(tmp_path):
    service, connection, record, proof, executor, req = prepared_runtime(tmp_path)
    try:
        rule = service.allow_for_chat(record, req)
        assert executor.answers == [{'decision': 'accept'}]
        executor.current = [replace(req, request_id=2, command='git commit -m second', proposed_prefix=())]
        assert service._auto_answer(proof, executor)
        assert executor.answers == [{'decision': 'accept'}] * 2
        assert service.snapshot(record.cli_session_id).permission_notices[0][:2] == ('turn', 2)
        assert service.remove_chat_rule(record, rule.rule_id) == ()
        executor.current = [replace(req, request_id=3)]
        assert not service._auto_answer(proof, executor)
        assert len(executor.answers) == 2
    finally:
        service.close()


@pytest.mark.parametrize('failure', [CodexApprovalNotSent('not queued'), RuntimeError('uncertain')])
def test_automatic_failure_is_never_retried(tmp_path, failure):
    service, connection, record, proof, executor, req = prepared_runtime(tmp_path)
    try:
        service.permission_store.save(record.cli_session_id, (candidate(req, str(tmp_path)),))
        executor.failure = failure
        service._auto_answer(proof, executor)
        executor.failure = None
        assert not service._auto_answer(proof, executor)
        assert not executor.answers
        assert bool(service._problems.get(record.cli_session_id)) == (not isinstance(failure, CodexApprovalNotSent))
    finally:
        service.close()


def test_removal_serializes_with_dispatch(tmp_path):
    service, connection, record, proof, executor, req = prepared_runtime(tmp_path)
    try:
        rule = candidate(req, str(tmp_path))
        service.permission_store.save(record.cli_session_id, (rule,))
        executor.entered, executor.release = threading.Event(), threading.Event()
        dispatched = threading.Thread(target=service._auto_answer, args=(proof, executor))
        dispatched.start()
        assert executor.entered.wait(5)
        removed = threading.Event()
        remover = threading.Thread(target=lambda: (service.remove_chat_rule(record, rule.rule_id), removed.set()))
        remover.start()
        assert not removed.wait(.05)
        executor.release.set()
        dispatched.join(5)
        remover.join(5)
        assert removed.is_set()
        executor.current = [replace(req, request_id=2)]
        assert not service._auto_answer(proof, executor)
    finally:
        service.close()


def test_mode_change_revokes_rules_even_if_native_resume_fails(tmp_path, monkeypatch):
    from test_codex_runtime import policy_runtime
    from thechatplace.providers.codex_owned import CodexOwnedThreads
    service, connection, record = policy_runtime(tmp_path)
    req = request(tmp_path)
    service.permission_store.save(record.cli_session_id, (candidate(req, str(tmp_path)),))
    monkeypatch.setattr(CodexOwnedThreads, 'resume', lambda *a: (_ for _ in ()).throw(RuntimeError('failed')))
    try:
        with pytest.raises(RuntimeError):
            service.prepare_policy(record, 'read-only', 'on-request')
        assert service.permission_store.load(record.cli_session_id) == ()
    finally:
        service.close()
