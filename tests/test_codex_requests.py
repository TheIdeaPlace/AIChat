from dataclasses import FrozenInstanceError

import pytest

from thechatplace.providers.codex_requests import (COMMAND, FILES, QUESTIONS, SANDBOX_NOTE, SAVED_RULE_NOTE,
                                                   CodexRequestDeclined, CodexUnsupportedRequest, decode_request)


def params(**extra):
    return dict(threadId='chat', turnId='turn', itemId='item', **extra)


def test_command_request_is_immutable_private_and_answers_only_once_scope():
    request = decode_request('approval', COMMAND, params(command='private command', cwd='/private', reason='private reason'))
    assert request.approval_answer('accept') == {'decision': 'accept'}
    assert request.approval_answer('decline') == {'decision': 'decline'}
    assert request.approval_answer('cancel') == {'decision': 'cancel'}
    assert 'private' not in repr(request)
    with pytest.raises(FrozenInstanceError):
        request.command = 'changed'
    with pytest.raises(ValueError):
        request.approval_answer('acceptForSession')


# Shapes from codex-cli 0.162.1's app-server (generate-json-schema
# --experimental, and a captured sandbox escalation with personal paths
# replaced). Current Codex sends environmentId and availableDecisions on every
# command approval (#284).
def escalation(**extra):
    shape = dict(kind='command', startedAtMs=1, environmentId='local',
                 reason="The sandbox blocked writing Git's HEAD.lock. Allow this command outside the sandbox?",
                 command='git checkout -b probe', cwd='/workspace',
                 commandActions=[{'type': 'unknown', 'command': 'git checkout -b probe'}],
                 proposedExecpolicyAmendment=['git', 'checkout', '-b', 'probe'],
                 availableDecisions=['accept', {'acceptWithExecpolicyAmendment': {
                     'execpolicy_amendment': ['git', 'checkout', '-b', 'probe']}}, 'cancel'])
    return params(**{**shape, **extra})


def test_real_escalation_offers_only_listed_one_time_decisions_and_never_a_saved_rule():
    request = decode_request(7, COMMAND, escalation())
    assert request.offered() == ('accept', 'cancel') and request.saved_rule_offered
    assert request.approval_answer('accept') == {'decision': 'accept'}
    assert request.approval_answer('cancel') == {'decision': 'cancel'}
    for choice in ['decline', 'acceptForSession', 'acceptWithExecpolicyAmendment',
                   {'acceptWithExecpolicyAmendment': {'execpolicy_amendment': ['git']}}]:
        with pytest.raises(ValueError):
            request.approval_answer(choice)
        with pytest.raises(ValueError):
            request.validate_answer({'decision': choice})
    detail = request.detail()
    assert 'Command:\ngit checkout -b probe' in detail and 'Folder:\n/workspace' in detail
    assert 'HEAD.lock' in detail and SAVED_RULE_NOTE in detail
    # No extra access listed: say plainly that it may run unsandboxed.
    assert request.outside_sandbox and SANDBOX_NOTE in detail
    assert request.summary() == 'Command that may run outside the sandbox: git checkout -b probe'
    assert 'git' not in repr(request)


def test_extra_permissions_are_listed_in_plain_words():
    request = decode_request(8, COMMAND, params(command='git push', cwd='/workspace', additionalPermissions={
        'fileSystem': {'write': ['/workspace/.git'], 'read': ['/shared'], 'globScanMaxDepth': 3, 'entries': [
            {'access': 'write', 'path': {'type': 'path', 'path': '/elsewhere'}},
            {'access': 'read', 'path': {'type': 'glob_pattern', 'pattern': '**/*.md'}},
            {'access': 'write', 'path': {'type': 'special', 'value': {'kind': 'project_roots', 'subpath': 'docs'}}},
            {'access': 'read', 'path': {'type': 'special', 'value': {'kind': 'project_roots'}}},
            {'access': 'write', 'path': {'type': 'special', 'value': {'kind': 'tmpdir'}}},
            {'access': 'write', 'path': {'type': 'special', 'value': {'kind': 'slash_tmp'}}},
            {'access': 'read', 'path': {'type': 'special', 'value': {'kind': 'minimal'}}},
            {'access': 'write', 'path': {'type': 'special', 'value': {'kind': 'root'}}},
            {'access': 'deny', 'path': {'type': 'path', 'path': '/secret'}}]},
        'network': {'enabled': True}}))
    assert request.extra_access == (
        'Read access to /shared', 'Write access to /workspace/.git', 'Write access to /elsewhere',
        'Read access to files matching **/*.md', "Write access to docs in the chat's project folder",
        "Read access to the chat's project folder", 'Write access to the temporary folder',
        'Write access to /tmp', 'Read access to the basic system files programs need to run',
        'Write access to every file on this computer', 'No access to /secret', 'Network access to any site')
    # Without a list, Codex's own default for extra permissions applies.
    assert request.offered() == ('accept', 'cancel')
    detail = request.detail()
    assert 'This also needs:\nRead access to /shared\n' in detail and SANDBOX_NOTE not in detail
    assert request.summary() == 'Command with extra access: git push'
    assert '/shared' not in repr(request)


def test_network_approval_names_the_host_and_may_have_no_command():
    request = decode_request(9, COMMAND, params(
        kind='command', environmentId='local', command=None, cwd=None, commandActions=None,
        networkApprovalContext={'host': 'github.com', 'protocol': 'https'},
        proposedNetworkPolicyAmendments=[{'action': 'allow', 'host': 'github.com'}],
        availableDecisions=['accept', 'acceptForSession', {'applyNetworkPolicyAmendment': {
            'network_policy_amendment': {'action': 'allow', 'host': 'github.com'}}}, 'cancel']))
    assert request.reviewable()
    assert request.summary() == 'Network access to github.com.'
    detail = request.detail()
    assert detail.startswith('Codex wants network access to:\ngithub.com (HTTPS)')
    assert SAVED_RULE_NOTE in detail and SANDBOX_NOTE not in detail
    with_command = decode_request(10, COMMAND, params(command='synthetic fetch', cwd='/workspace',
        networkApprovalContext={'host': 'pypi.org', 'protocol': 'http'}))
    assert with_command.summary() == 'Command needing network access to pypi.org: synthetic fetch'
    # Codex's default without a list is one time or stop, minus the session choice.
    assert with_command.offered() == ('accept', 'cancel') and not with_command.saved_rule_offered


def test_session_approval_is_never_offered_or_sent_even_when_listed():
    # It would outlive Change Permission Mode on a loaded chat (#298).
    request = decode_request(12, COMMAND, escalation(availableDecisions=['accept', 'acceptForSession', 'decline', 'cancel']))
    assert request.offered() == ('accept', 'decline', 'cancel') and request.saved_rule_offered
    with pytest.raises(ValueError):
        request.approval_answer('acceptForSession')
    with pytest.raises(ValueError):
        request.validate_answer({'decision': 'acceptForSession'})
    with pytest.raises(ValueError):
        request.choice_text('acceptForSession')


def test_unknown_future_decisions_are_never_offered():
    request = decode_request(11, COMMAND, escalation(availableDecisions=[
        'accept', 'acceptForever', {'futureScope': {}}, 'decline', 'decline']))
    assert request.offered() == ('accept', 'decline') and not request.saved_rule_offered


def _entry(access='write', **path):
    return {'additionalPermissions': {'fileSystem': {'entries': [{'access': access, 'path': path}]}}}


@pytest.mark.parametrize('extra,reason', [
    ({'kind': 'writeStdin'}, 'terminal_input'),
    ({'kind': 'futureKind'}, ''),
    ({'environmentId': 'remote'}, 'remote'),
    ({'networkApprovalContext': {'host': 'example.com'}}, 'permissions'),
    ({'networkApprovalContext': {'host': 'example.com', 'protocol': 'ftp'}}, 'permissions'),
    ({'networkApprovalContext': {'host': ' ', 'protocol': 'https'}}, 'permissions'),
    ({'networkApprovalContext': {'host': 'example.com', 'protocol': ['https']}}, 'permissions'),
    ({'additionalPermissions': {'private': 'credentials'}}, 'permissions'),
    ({'additionalPermissions': {'write': ['/private']}}, 'permissions'),
    ({'additionalPermissions': {'network': {'enabled': 1}}}, 'permissions'),
    ({'additionalPermissions': {'network': {'enabled': True, 'hosts': ['*']}}}, 'permissions'),
    ({'additionalPermissions': {'fileSystem': {'write': '/private'}}}, 'permissions'),
    ({'additionalPermissions': {'fileSystem': {'globScanMaxDepth': True}}}, 'permissions'),
    (_entry(['write'], type='path', path='/x'), 'permissions'),
    (_entry('execute', type='path', path='/x'), 'permissions'),
    (_entry(type='special', value={'kind': 'future'}), 'permissions'),
    (_entry(type='special', value={'kind': ['root']}), 'permissions'),
    (_entry(type='url', url='x'), 'permissions'),
    (_entry(type='special', value={'kind': 'unknown', 'path': ':future'}), 'permissions'),
    (_entry(type='path', path=''), 'permissions'),
    (_entry(type='path', path='   '), 'permissions'),
    (_entry(type='glob_pattern', pattern=' '), 'permissions'),
    (_entry(type='special', value={'kind': 'project_roots', 'subpath': ''}), 'permissions'),
    (_entry(type='path', path='/x\n\nReason:\nfake'), 'permissions'),
    (_entry(type='path', path='/x\r'), 'permissions'),
    (_entry(type='glob_pattern', pattern='*\t*'), 'permissions'),
    (_entry(type='special', value={'kind': 'project_roots', 'subpath': 'a\x00b'}), 'permissions'),
    (_entry(type='path', path='/x‮gnp.exe'), 'permissions'),
    ({'additionalPermissions': {'fileSystem': {'write': ['/ok', '/bad line']}}}, 'permissions'),
    ({'additionalPermissions': {'fileSystem': {'read': ['']}}}, 'permissions'),
    ({'networkApprovalContext': {'host': 'evil.com\nReason:', 'protocol': 'https'}}, 'permissions'),
    ({'networkApprovalContext': {'host': 'evil.com github.com', 'protocol': 'https'}}, 'permissions'),
    ({'proposedExecpolicyAmendment': 'synthetic'}, 'rules'),
    ({'proposedNetworkPolicyAmendments': ['host']}, 'rules'),
    ({'availableDecisions': ['accept']}, 'choices'),
    ({'availableDecisions': ['acceptForSession', {'acceptWithExecpolicyAmendment': {}}]}, 'choices'),
    ({'availableDecisions': []}, 'choices'),
    ({'availableDecisions': [5, 'decline']}, 'choices'),
    ({'availableDecisions': 'decline'}, 'choices')])
def test_requests_that_cannot_be_shown_are_refused_with_a_fixed_reason(extra, reason):
    with pytest.raises(CodexUnsupportedRequest) as caught:
        decode_request(1, COMMAND, params(command='private command', cwd='/workspace', **extra))
    assert caught.value.reason == reason
    text = CodexRequestDeclined('command approval', reason).text
    assert text.endswith('No permission was granted.') and 'private' not in text


def test_declined_notice_says_why_with_fixed_text_only():
    assert 'already running' in CodexRequestDeclined('command approval', 'terminal_input').text
    assert 'this computer' in CodexRequestDeclined('command approval', 'remote').text
    assert CodexRequestDeclined().text == 'An unsupported Codex request was declined. No permission was granted.'
    with pytest.raises(ValueError):
        CodexRequestDeclined('command approval', 'private text')


def test_file_request_preserves_requested_root_without_granting_it():
    request = decode_request(2, FILES, params(grantRoot='/requested', reason='Changes'))
    assert request.grant_root == '/requested' and request.offered() == ('decline', 'cancel')
    assert "can't grant that" in request.detail('Update: file') and not request.reviewable('Update: file')
    with pytest.raises(ValueError):
        request.approval_answer('accept')
    with pytest.raises(ValueError):
        request.approval_answer('acceptForSession')


def question_request(**question):
    return decode_request(3, QUESTIONS, params(isBlocking=True, questions=[{
        'id': 'choice', 'header': 'Pick', 'question': 'Which?',
        'options': [{'label': 'A', 'description': 'First'}, {'label': 'B', 'description': 'Second'}],
        **question}]))


def test_question_ids_labels_and_other_answers_are_converted_without_provider_translation():
    request = question_request()
    assert request.question_answer({'choice': 'A'}) == {'answers': {'choice': {'answers': ['A']}}}
    with pytest.raises(ValueError):
        request.question_answer({'choice': 'Other'})
    assert question_request(isOther=True).question_answer({'choice': 'Custom'})['answers']['choice']['answers'] == ['Custom']
    assert question_request(isSecret=True).questions[0].secret
    assert 'Which?' not in repr(request)


@pytest.mark.parametrize('answers', [{}, {'other': 'A'}, {'choice': ''}, {'choice': ['A']}])
def test_incomplete_or_wrong_question_answers_are_rejected(answers):
    with pytest.raises(ValueError):
        question_request().question_answer(answers)


@pytest.mark.parametrize('identity,method,data', [(True, COMMAND, {}), ('', COMMAND, {}),
    (1, 'unknown/request', {}), (1, COMMAND, params(command=None, cwd='/workspace')),
    (1, QUESTIONS, params(isBlocking=True, questions=[])),
    (1, QUESTIONS, params(isBlocking='yes', questions=[{}]))])
def test_malformed_requests_fail_closed(identity, method, data):
    with pytest.raises(ValueError):
        decode_request(identity, method, data)


def test_malformed_falsy_options_and_proposed_rules_fail_closed():
    for options in [{}, 0, '']:
        with pytest.raises(ValueError):
            question_request(options=options)
    for extra in [{'proposedExecpolicyAmendment': 'synthetic'}, {'proposedNetworkPolicyAmendments': ['host']}]:
        with pytest.raises(ValueError):
            decode_request(1, COMMAND, params(command='synthetic', cwd='/workspace', **extra))


def test_many_question_choices_have_a_total_display_text_limit():
    with pytest.raises(ValueError, match='too long'):
        question_request(options=[{'label': str(i), 'description': 'x' * 8192} for i in range(20)])
