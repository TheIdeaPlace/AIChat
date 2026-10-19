from dataclasses import replace
import json
import os
import shlex
import sys

import pytest

from thechatplace.providers.codex_permissions import (
    CodexPermissionStore, candidate, matches, safe_prefix,
    command_words,
)
from thechatplace.providers.codex_requests import COMMAND, decode_request, CodexUnsupportedRequest
from thechatplace.providers.codex_events import CodexEvent
from thechatplace.providers.codex_rpc import CodexRpcError, CodexApprovalNotSent
from thechatplace import platform_paths
from test_codex_runtime import runtime, wait


def wrapped(command, style=None):
    style = style or ('powershell' if sys.platform == 'win32' else 'posix')
    if style == 'powershell':
        shell = os.environ.get('SystemRoot', r'C:\Windows') + r'\System32\WindowsPowerShell\v1.0\powershell.exe'
        # Captured from codex-cli 0.162.1: Rust shlex double-quotes and
        # doubles the Windows path's backslashes, unlike Python shlex.join.
        return '"' + shell.replace('\\', '\\\\') + '" -Command ' + shlex.quote(command)
    return shlex.join(['/bin/zsh', '-lc', command])


def approval(folder, identity='request', command='git commit -m "A change"', **extra):
    params = dict(
        threadId='owned-1', turnId='turn-1', itemId=identity,
        command=wrapped(command), cwd=str(folder), proposedExecpolicyAmendment=['git', 'commit'])
    params.update(extra)
    return decode_request(identity, COMMAND, params)


def test_prefix_is_retained_but_only_one_time_wire_answers_are_possible(tmp_path):
    request = approval(tmp_path)
    rule = candidate(request, str(tmp_path))
    assert request.proposed_prefix == ('git', 'commit')
    assert rule and rule.description == 'Run git commit (simple commands with reviewed options).'
    assert matches(rule, replace(request, command=wrapped('git commit --amend'), proposed_prefix=()), str(tmp_path))
    for decision in ('acceptForSession', 'acceptWithExecpolicyAmendment'):
        with pytest.raises(ValueError):
            request.approval_answer(decision)


@pytest.mark.parametrize('suffix', [
    ' && rm file', ' || echo yes', '; echo yes', ' | cat', ' > file', ' < file',
    ' 2>file', ' $(echo yes)', ' `echo yes`', '\nwhoami', '\rwhoami',
    ' (echo yes)', ' & echo yes', ' %COMMAND%', ' $COMMAND', ' ^&echo yes',
    ' \\; echo yes', ' #comment', ' *.txt', ' !COMMAND!', ' \targ', ' café',
])
def test_shell_syntax_never_creates_or_matches_a_rule(tmp_path, suffix):
    request = approval(tmp_path)
    rule = candidate(request, str(tmp_path))
    unsafe = replace(request, command=wrapped('git commit' + suffix))
    assert candidate(unsafe, str(tmp_path)) is None
    assert not matches(rule, unsafe, str(tmp_path))


@pytest.mark.parametrize('exe', [
    'bash', 'sh', 'pwsh', 'powershell.exe', 'cmd.exe', 'python', 'python3',
    'python3.12.exe', 'node', 'env', 'sudo', 'zsh', 'cscript.exe', 'ruby',
    'perl', 'lua', 'deno', 'bun', 'busybox', 'xargs', '/usr/bin/git', './git',
])
def test_interpreters_and_wrappers_have_no_chat_offer(exe):
    assert not safe_prefix((exe,))


def test_folder_and_access_cannot_expand(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / '.git').mkdir()
    child = root / 'child'
    child.mkdir()
    request = approval(root, additionalPermissions={'fileSystem': {'write': [str(root / '.git')]}})
    rule = candidate(request, str(root))
    assert matches(rule, replace(request, cwd=str(child)), str(root))
    assert not matches(rule, replace(request, cwd=str(tmp_path)), str(root))
    assert not matches(rule, replace(request, cwd=str(root / 'missing')), str(root))
    assert candidate(replace(request, cwd='relative'), str(root)) is None
    for changes in (
        {'fileSystem': {'write': [str(root / '.git'), str(tmp_path)]}},
        {'fileSystem': {'write': [str(root / '.git')]}, 'network': {'enabled': True}},
        None,
    ):
        assert not matches(rule, approval(root, additionalPermissions=changes), str(root))
    assert not matches(rule, replace(request, command='git commitment'), str(root))
    assert not matches(rule, replace(request, decisions=('cancel',)), str(root))


def test_host_and_protocol_must_stay_the_same(tmp_path):
    request = approval(tmp_path, networkApprovalContext={'host': 'example.test', 'protocol': 'https'})
    rule = candidate(request, str(tmp_path))
    assert rule and matches(rule, request, str(tmp_path))
    for host, protocol in [('other.test', 'https'), ('example.test', 'http')]:
        assert not matches(rule, approval(tmp_path, networkApprovalContext={
            'host': host, 'protocol': protocol}), str(tmp_path))


@pytest.mark.parametrize('files', [
    {'write': ['relative']}, {'write': ['/missing-synthetic-permission-path']},
    {'entries': [{'access': 'write', 'path': {'type': 'glob_pattern', 'pattern': '*.py'}}]},
    {'entries': [{'access': 'write', 'path': {'type': 'special', 'value': {'kind': 'project_roots'}}}]},
])
def test_uncertain_extra_paths_have_no_chat_rule(tmp_path, files):
    request = approval(tmp_path, additionalPermissions={'fileSystem': files})
    assert candidate(request, str(tmp_path)) is None


def test_existing_decoder_refusals_still_apply(tmp_path):
    for extra in ({'kind': 'writeStdin'}, {'environmentId': 'remote'},
                  {'networkApprovalContext': {'host': 'hidden\nhost', 'protocol': 'https'}},
                  {'additionalPermissions': {'fileSystem': {'write': ['hidden\npath']}}}):
        with pytest.raises(CodexUnsupportedRequest):
            approval(tmp_path, **extra)


def test_store_roundtrip_is_bound_to_chat_and_corruption_fails_closed(tmp_path):
    store = CodexPermissionStore(tmp_path / 'store')
    rule = candidate(approval(tmp_path), str(tmp_path))
    store.save('owned-1', (rule,))
    assert CodexPermissionStore(store.root).load('owned-1') == (rule,)
    assert not store.load('copy')
    path = store._path('owned-1')
    raw = json.loads(path.read_text())
    raw['rules'][0]['prefix'] = ['python3']
    path.write_text(json.dumps(raw))
    assert not store.load('owned-1')
    path.write_text('{broken')
    assert not store.load('owned-1')


def running(tmp_path, *, start=True):
    service, connection, updates = runtime()
    answers = []
    connection.respond_request = lambda request, result: answers.append((request.request_id, result))
    original = connection.request
    def local_response(method, params):
        result = original(method, params)
        if 'cwd' in result:
            result['cwd'] = str(tmp_path)
        return result
    connection.request = local_response
    record = service.create(str(tmp_path), 'Owned')
    if start:
        service.send(record, 'Synthetic request')
    return service, connection, updates, record, answers


def enqueue(service, connection, record, request):
    connection.events.append(request)
    wait(lambda: any(r is request for r in service.snapshot(record.cli_session_id).requests))


def test_allow_auto_answer_and_remove(tmp_path):
    service, connection, updates, record, answers = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        assert service.chat_rule_candidate(record, request)
        service.allow_for_chat(record, request)
        assert answers == [('request', {'decision': 'accept'})]
        kept = service.chat_rules(record)
        assert len(kept) == 1
        connection.events.append(approval(tmp_path, 'second'))
        wait(lambda: len(answers) == 2)
        wait(lambda: bool(updates[-1].permission_notices))
        assert updates[-1].permission_notices[-1] == (
            'turn-1', 'second', 'Allowed git commit, as you said for this chat.')
        compound = approval(tmp_path, 'compound', command='git commit && rm file')
        enqueue(service, connection, record, compound)
        assert len(answers) == 2
        service.remove_chat_rule(record, kept[0].rule_id)
        enqueue(service, connection, record, approval(tmp_path, 'removed'))
        assert len(answers) == 2
        assert not service.chat_rules(record)
        with pytest.raises(ValueError):
            service.allow_for_chat(record, compound)
    finally:
        service.close()


def test_restart_fork_and_mode_change(tmp_path):
    service, connection, _, record, answers = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        service.allow_for_chat(record, request)
        # Close the process, then open the original chat in a fresh runtime.
        root = service.permission_store.root
    finally:
        service.close()
    reopened, c, updates, _, replies = running(tmp_path)
    reopened.permission_store = CodexPermissionStore(root)
    try:
        c.events.append(approval(tmp_path, 'after-restart'))
        wait(lambda: bool(replies))
        assert replies == [('after-restart', {'decision': 'accept'})]
        copy = reopened.fork(record.to_info(), 'Copy')
        assert not reopened.chat_rules(copy)
        reopened.send(copy, 'Synthetic copied chat turn')
        copy_request = approval(tmp_path, 'copy-command', threadId=copy.cli_session_id, turnId='turn-2')
        enqueue(reopened, c, copy, copy_request)
        assert replies == [('after-restart', {'decision': 'accept'})]
        assert len(reopened.chat_rules(record)) == 1 and not reopened.chat_rules(copy)
        reopened.answer(copy, copy_request, copy_request.approval_answer('decline'))
        c.events.append(CodexEvent(
            'turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: reopened.snapshot(record.cli_session_id).outcome == 'completed')
        change = reopened.prepare_policy(record, 'read-only', 'on-request')
        reopened.finish_policy(change, True)
        assert not reopened.chat_rules(record)
    finally:
        reopened.close()


def test_failed_save_never_answers_and_foreign_chats_cannot_use_rules(tmp_path, monkeypatch):
    service, connection, _, record, answers = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        def fail(*args):
            raise OSError('synthetic disk failure')
        monkeypatch.setattr(service.permission_store, 'save', fail)
        with pytest.raises(OSError):
            service.allow_for_chat(record, request)
        assert not answers and service.snapshot(record.cli_session_id).requests
        for foreign in (replace(record, provider='claude'), replace(record, started=False)):
            with pytest.raises(CodexRpcError):
                service.chat_rules(foreign)
    finally:
        service.close()


def test_uncertain_kept_answer_is_not_retried(tmp_path):
    service, connection, _, record, answers = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        def uncertain(request, result):
            answers.append(result)
            raise CodexRpcError('synthetic uncertain response')
        connection.respond_request = uncertain
        with pytest.raises(CodexRpcError):
            service.allow_for_chat(record, request)
        assert len(answers) == 1 and service.chat_rules(record)
        assert not service.ready(record)
        service._auto_answer(service._proofs[record.cli_session_id], service._executor)
        assert len(answers) == 1
        assert service.snapshot(record.cli_session_id).problem
    finally:
        service.close()


@pytest.mark.parametrize('style', ['powershell', 'posix'])
def test_exact_shell_wrappers_unwrap_once_and_keep_the_shell_scope(tmp_path, monkeypatch, style):
    real = platform_paths.codex_approval_shell
    def fixture_shell(executable):
        if executable == '/bin/zsh':
            return 'posix'
        if executable.lower().endswith(r'\system32\windowspowershell\v1.0\powershell.exe'):
            return 'powershell'
        return real(executable)
    monkeypatch.setattr(platform_paths, 'codex_approval_shell', fixture_shell)
    request = replace(approval(tmp_path), command=wrapped('git commit -m synthetic', style))
    rule = candidate(request, str(tmp_path))
    assert rule and matches(rule, request, str(tmp_path))
    assert command_words(request.command)[0] == ('git', 'commit', '-m', 'synthetic')
    assert not matches(rule, replace(request, command='git commit -m synthetic'), str(tmp_path))
    other = 'posix' if style == 'powershell' else 'powershell'
    assert not matches(rule, replace(request, command=wrapped('git commit -m synthetic', other)), str(tmp_path))
    outer = shlex.split(request.command)
    assert candidate(replace(request, command=shlex.join([*outer[:-1], '-extra', outer[-1]])), str(tmp_path)) is None
    nested = wrapped('bash -c "git commit"', style)
    assert candidate(replace(request, command=nested), str(tmp_path)) is None


@pytest.mark.parametrize('script', [
    'git commit -m "a""b"', "git commit -m 'a''b'", 'git com"mit"',
    'git commit -m "$env:PAYLOAD"', 'git commit -m `whoami`',
])
def test_shell_dependent_quoting_is_never_kept(tmp_path, script):
    request = approval(tmp_path, command=script)
    assert candidate(request, str(tmp_path)) is None


@pytest.mark.parametrize('prefix,script', [
    (('git',), 'git -c "core.fsmonitor=powershell -enc QQA=" status'),
    (('git', 'push'), 'git push --receive-pack="sh -c id" .'),
    (('git', 'fetch'), 'git fetch --upload-pack="calc.exe" .'),
    (('git', 'fetch'), 'git fetch -ucalc.exe .'),
    (('git', 'grep'), 'git grep -Ocalc.exe x'),
    (('git', 'grep'), 'git grep --open-files-in-pager=calc.exe x'),
    (('git', 'log'), 'git log --output=C:/Users/synthetic/x.ps1'),
    (('git', 'diff'), 'git diff --ext-diff'),
    (('git', 'diff'), 'git diff --textconv'),
    (('git', 'diff'), 'git diff -x calc.exe'),
    (('git', 'diff'), 'git diff --extcmd=calc.exe'),
    (('git', 'commit'), 'git commit --exec calc.exe'),
    (('git', 'switch'), 'git switch --template=evil new'),
    (('git',), 'git --config-env=core.fsmonitor=PAYLOAD status'),
    (('git',), 'git --exec-path=evil commit'),
    (('git', 'push'), 'git push ext::calc.exe'),
    (('git', 'fetch'), 'git fetch ssh://evil/repo'),
    (('rg',), 'rg --pre powershell.exe x evil.ps1'),
    (('tar',), 'tar -cf a.tar --checkpoint=1 --checkpoint-action=exec=calc.exe .'),
    (('zip',), 'zip a.zip f -T -TT calc.exe'),
    (('gh',), 'gh alias set --shell evil calc.exe'),
    (('curl',), 'curl --output C:/Users/synthetic/x.ps1 example.test'),
    (('wget',), 'wget -O C:/Users/synthetic/x.ps1 example.test'),
])
def test_reviewed_attacks_never_create_or_match_rules(tmp_path, prefix, script):
    baseline = candidate(approval(tmp_path), str(tmp_path))
    # Include formerly allowed broad prefixes to ensure old/forged rules
    # cannot bypass whole-command checks during matching.
    rule = replace(baseline, prefix=prefix)
    request = approval(tmp_path, command=script, proposedExecpolicyAmendment=list(prefix))
    assert candidate(request, str(tmp_path)) is None
    assert not matches(rule, request, str(tmp_path))


@pytest.mark.parametrize('path', ['/outside', 'C:/Users/synthetic', 'C:relative', '../outside', 'child/../../outside'])
def test_git_path_arguments_cannot_escape_even_with_full_escalation(tmp_path, path):
    request = approval(tmp_path, command='git add inside.txt', proposedExecpolicyAmendment=['git', 'add'])
    rule = candidate(request, str(tmp_path))
    assert rule and request.outside_sandbox
    outside = replace(request, command=wrapped('git add -- ' + path))
    assert candidate(outside, str(tmp_path)) is None
    assert not matches(rule, outside, str(tmp_path))


@pytest.mark.parametrize('exe', ['rm', 'cp', 'mv', 'tar', 'unzip', 'curl', 'wget', 'cat', 'ls', 'mkdir', 'diff'])
def test_file_tools_and_powershell_aliases_are_not_kept(tmp_path, exe):
    assert candidate(approval(tmp_path, command=exe + ' inside', proposedExecpolicyAmendment=[exe]), str(tmp_path)) is None


@pytest.mark.parametrize('script,prefix', [
    ('git switch -c feature', ['git', 'switch']), ('git add README.md', ['git', 'add']),
    ('git commit -m "A change"', ['git', 'commit']), ('git push origin main', ['git', 'push']),
    ('git fetch origin main', ['git', 'fetch']), ('git status --short', ['git', 'status']),
])
def test_normal_git_workflow_is_kept(tmp_path, script, prefix):
    request = approval(tmp_path, command=script, proposedExecpolicyAmendment=prefix)
    rule = candidate(request, str(tmp_path))
    assert rule and matches(rule, request, str(tmp_path))


@pytest.mark.parametrize('change', ['version', 'field'])
def test_unrecognized_storage_is_never_overwritten(tmp_path, change):
    store = CodexPermissionStore(tmp_path / 'store')
    rule = candidate(approval(tmp_path), str(tmp_path))
    store.save('owned-1', (rule,))
    raw = json.loads(store._path('owned-1').read_text())
    if change == 'version':
        raw['version'] += 1
    else:
        raw['rules'][0]['future_field'] = 'must survive'
    store._path('owned-1').write_text(json.dumps(raw))
    before = store._path('owned-1').read_bytes()
    assert not store.load('owned-1')
    with pytest.raises(ValueError):
        store.save('owned-1', ())
    assert store._path('owned-1').read_bytes() == before


@pytest.mark.parametrize('raw', [None, [], {}, {'version': True}, {'version': 99}])
def test_unknown_storage_root_fails_closed_and_is_preserved(tmp_path, raw):
    store = CodexPermissionStore(tmp_path / 'store')
    rule = candidate(approval(tmp_path), str(tmp_path))
    store.save('owned-1', (rule,))
    path = store._path('owned-1')
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    assert not store.load('owned-1')
    with pytest.raises(ValueError):
        store.save('owned-1', ())
    assert path.read_bytes() == before


def test_unknown_rule_id_does_not_rewrite_storage(tmp_path):
    service, connection, _, record, _ = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        service.allow_for_chat(record, request)
        path = service.permission_store._path(record.cli_session_id)
        before = path.read_bytes()
        with pytest.raises(ValueError):
            service.remove_chat_rule(record, 'missing')
        assert path.read_bytes() == before
    finally:
        service.close()


def test_failed_mode_verification_still_revokes_rules(tmp_path, monkeypatch):
    service, connection, _, record, _ = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        service.allow_for_chat(record, request)
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'completed')
        def fail(*args, **kwargs):
            raise CodexRpcError('synthetic policy failure')
        monkeypatch.setattr('thechatplace.providers.codex_runtime.CodexOwnedThreads.resume', fail)
        with pytest.raises(CodexRpcError):
            service.prepare_policy(record, 'read-only', 'on-request')
        assert not service.chat_rules(record)
    finally:
        service.close()


@pytest.mark.parametrize('failure', ['stale', 'preflight', 'uncertain'])
def test_listener_failure_distinguishes_unsent_from_uncertain(tmp_path, failure):
    service, connection, _, record, answers = running(tmp_path)
    try:
        request = approval(tmp_path)
        enqueue(service, connection, record, request)
        service.allow_for_chat(record, request)
        attempts = []
        def fail(request, result):
            attempts.append(request.request_id)
            if failure != 'uncertain':
                raise CodexApprovalNotSent(stale=failure == 'stale')
            raise CodexRpcError('synthetic uncertain write')
        connection.respond_request = fail
        connection.events.append(approval(tmp_path, 'automatic'))
        wait(lambda: bool(attempts))
        if failure == 'uncertain':
            wait(lambda: bool(service.snapshot(record.cli_session_id).problem))
            assert not service.ready(record)
        else:
            wait(lambda: bool(service._manual_approvals))
            assert service.ready(record) and not service.snapshot(record.cli_session_id).problem
            # Another request can still be answered by hand, including Deny.
            other = approval(tmp_path, 'manual', command='git status --short')
            enqueue(service, connection, record, other)
            connection.respond_request = lambda req, result: answers.append((req.request_id, result))
            service.answer(record, other, other.approval_answer('decline'))
            assert answers[-1] == ('manual', {'decision': 'decline'})
        # An unrelated event drives another listener iteration. Never retry
        # either a pre-send refusal or a response whose delivery is uncertain.
        connection.events.append(CodexEvent('activity', record.cli_session_id, 'turn-1', text='Synthetic work'))
        wait(lambda: not connection.events)
        assert attempts == ['automatic']
    finally:
        service.close()


@pytest.mark.parametrize('preflight_failure', [False, True])
def test_startup_approval_is_handled_only_by_listener_without_failing_send(tmp_path, preflight_failure):
    service, connection, _, record, answers = running(tmp_path, start=False)
    try:
        request = approval(tmp_path)
        rule = candidate(request, str(tmp_path))
        service.permission_store.save(record.cli_session_id, (rule,))
        original = connection.request
        def startup(method, params):
            if method == 'turn/start':
                connection.events.append(request)
                # Force the listener to drain the approval before start returns.
                wait(lambda: not connection.events)
            return original(method, params)
        connection.request = startup
        if preflight_failure:
            def unsent(*args):
                raise CodexApprovalNotSent()
            connection.respond_request = unsent
        assert service.send(record, 'Synthetic startup') is False
        if preflight_failure:
            wait(lambda: bool(service._manual_approvals))
        else:
            wait(lambda: bool(answers))
        assert service.ready(record) and not service.snapshot(record.cli_session_id).problem
    finally:
        service.close()


@pytest.mark.parametrize('executable', ['/tmp/bash', '/tmp/powershell.exe', r'C:\temp\powershell.exe', 'pwsh.exe'])
def test_shell_wrapper_needs_a_trusted_absolute_location(executable):
    assert not platform_paths.codex_approval_shell(executable)


@pytest.mark.parametrize('flags', [['-Command'], ['-NoProfile', '-Command']])
def test_powershell_only_accepts_exact_command_switches(flags, monkeypatch):
    shell = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    monkeypatch.setattr(platform_paths, 'codex_approval_shell', lambda exe: 'powershell' if exe == shell else '')
    command = shlex.join([shell, *flags, 'git commit -m synthetic'])
    assert command_words(command)[0] == ('git', 'commit', '-m', 'synthetic')
    for extra in ('-EncodedCommand', '-File', '-ExecutionPolicy'):
        assert not command_words(shlex.join([shell, extra, *flags, 'git commit -m synthetic']))[0]


@pytest.mark.parametrize('shell', ['/bin/bash', '/bin/zsh', '/bin/sh'])
@pytest.mark.parametrize('flag', ['-c', '-lc'])
def test_posix_only_accepts_exact_command_switches(shell, flag, monkeypatch):
    monkeypatch.setattr(platform_paths, 'codex_approval_shell', lambda exe: 'posix' if exe == shell else '')
    assert command_words(shlex.join([shell, flag, 'git commit -m synthetic']))[0] == (
        'git', 'commit', '-m', 'synthetic')
    assert not command_words(shlex.join([shell, flag, 'git commit', 'extra-argv']))[0]


@pytest.mark.parametrize('shell', ['/bin/bash', r'C:\Program Files\PowerShell\7\pwsh.exe'])
@pytest.mark.parametrize('script,prefix', [
    ('git commit -m probe', ['git', 'commit']), ('git push origin main', ['git', 'push']),
])
def test_runner_captures_decode_and_match_with_native_saved_offer_ignored(tmp_path, monkeypatch, shell, script, prefix):
    # Synthetic IDs/paths with the full request shape captured by real Codex
    # 0.162.1 in Actions run 39253925878. The model and prompt were synthetic.
    # Trust lookup is stubbed here to replay either platform on either host;
    # the native probes and stdio matrix exercise the host's real lookup.
    kind = 'posix' if shell.startswith('/') else 'powershell'
    monkeypatch.setattr(platform_paths, 'codex_approval_shell', lambda exe: kind if exe == shell else '')
    if kind == 'posix':
        command = shell + " -lc '" + script + "'"
    else:
        command = '"' + shell.replace('\\', '\\\\') + '" -Command ' + "'" + script + "'"
    request = decode_request(1, COMMAND, {
        'kind': 'command', 'threadId': 'synthetic-chat', 'turnId': 'synthetic-turn', 'itemId': 'call1',
        'startedAtMs': 1, 'environmentId': 'local', 'reason': 'probe', 'command': command,
        'cwd': str(tmp_path), 'commandActions': [{'type': 'unknown', 'command': script}],
        'proposedExecpolicyAmendment': prefix,
        'availableDecisions': ['accept', {'acceptWithExecpolicyAmendment': {'execpolicy_amendment': prefix}}, 'cancel'],
    })
    rule = candidate(request, str(tmp_path))
    assert rule and matches(rule, request, str(tmp_path))
    assert request.outside_sandbox and request.saved_rule_offered
    assert request.offered() == ('accept', 'cancel')
    assert request.approval_answer('accept') == {'decision': 'accept'}
    for decision in ('acceptForSession', 'acceptWithExecpolicyAmendment'):
        with pytest.raises(ValueError):
            request.approval_answer(decision)
