from dataclasses import FrozenInstanceError

import pytest

from thechatplace.providers.codex_requests import COMMAND, FILES, QUESTIONS, decode_request


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


@pytest.mark.parametrize('extra', [{'kind': 'stdin'}, {'environmentId': 'remote'},
    {'networkApprovalContext': {'host': 'example.com'}}, {'additionalPermissions': {'network': {'enabled': True}}},
    {'availableDecisions': ['decline', 'cancel']}])
def test_unrepresented_command_context_never_looks_like_ordinary_approval(extra):
    with pytest.raises(ValueError):
        decode_request(1, COMMAND, params(command='command', cwd='/workspace', **extra))


def test_file_request_preserves_requested_root_without_granting_it():
    request = decode_request(2, FILES, params(grantRoot='/requested', reason='Changes'))
    assert request.grant_root == '/requested'
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


def test_future_permission_fields_and_malformed_falsy_options_fail_closed():
    with pytest.raises(ValueError):
        decode_request(1, COMMAND, params(command='synthetic', cwd='/workspace',
                                         additionalPermissions={'write': ['/private']}))
    for options in [{}, 0, '']:
        with pytest.raises(ValueError):
            question_request(options=options)


def test_many_question_choices_have_a_total_display_text_limit():
    with pytest.raises(ValueError, match='too long'):
        question_request(options=[{'label': str(i), 'description': 'x' * 8192} for i in range(20)])
