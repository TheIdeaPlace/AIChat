from dataclasses import replace
import json

import pytest

from thechatplace.providers.codex_permissions import (
    CodexPermissionStore, candidate, matches, safe_prefix,
)
from thechatplace.providers.codex_requests import COMMAND, decode_request, CodexUnsupportedRequest
from thechatplace.providers.codex_events import CodexEvent
from thechatplace.providers.codex_rpc import CodexRpcError
from test_codex_runtime import runtime, wait


def approval(folder, identity='request', command='git commit -m "A change"', **extra):
    return decode_request(identity, COMMAND, dict(
        threadId='owned-1', turnId='turn-1', itemId=identity,
        command=command, cwd=str(folder), proposedExecpolicyAmendment=['git', 'commit'],
        **extra))


def test_prefix_is_retained_but_only_one_time_wire_answers_are_possible(tmp_path):
    request = approval(tmp_path)
    rule = candidate(request, str(tmp_path))
    assert request.proposed_prefix == ('git', 'commit')
    assert rule and rule.description == 'Run git commit (any arguments).'
    assert matches(rule, replace(request, command='git commit --amend', proposed_prefix=()), str(tmp_path))
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
    unsafe = replace(request, command='git commit' + suffix)
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


def running(tmp_path):
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
