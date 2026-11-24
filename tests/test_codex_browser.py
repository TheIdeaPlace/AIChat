"""Windowless host capability instructions, including old chats (#524)."""
from copy import deepcopy
from dataclasses import replace

import pytest

from thechatplace.providers.codex_events import CodexEvent
from thechatplace.providers.codex_execution import (
    CodexTurnExecutor, UNAVAILABLE_NOTE, instruction_context,
)
from thechatplace.providers.codex_rpc import CodexRpcError
from test_codex_execution import Connection, runtime_connection
from test_codex_owned import Connection as LifecycleConnection, CodexOwnedThreads


@pytest.mark.parametrize('billing', ['subscription', 'api'])
@pytest.mark.parametrize('instructions', [None, '', 'Use short paragraphs.'])
def test_every_turn_has_host_capabilities_separate_from_chat_instructions(billing, instructions):
    connection = Connection()
    connection.billing_route = billing
    proof = replace(connection.proof('one'), instructions=instructions)
    connection.proofs['one'] = proof
    executor = CodexTurnExecutor(connection)
    for prompt in ('First question', 'Next question'):
        turn = executor.start(proof, prompt)
        params = connection.calls[-1][1]
        assert params['input'] == [{'type': 'text', 'text': prompt}]
        context = params['additionalContext']
        assert context['tcp_capabilities'] == {'kind': 'application', 'value': UNAVAILABLE_NOTE}
        if instructions is None:
            assert 'tcp_chat_instructions' not in context
        else:
            assert context['tcp_chat_instructions'] == {'kind': 'application', 'value': instructions}
        connection.events.append(CodexEvent('turn_completed', 'one', turn.turn_id, status='completed'))
        executor.drain()


@pytest.mark.parametrize('change', ['missing-context', 'missing-note', 'changed-note', 'changed-instructions'])
def test_wire_guard_requires_the_actual_capability_note(change):
    connection, proof, params = runtime_connection()
    connection._verify_turn_route(params)
    params = deepcopy(params)
    if change == 'missing-context':
        params.pop('additionalContext')
    elif change == 'missing-note':
        params['additionalContext'].pop('tcp_capabilities')
    elif change == 'changed-note':
        params['additionalContext']['tcp_capabilities']['value'] = 'Enable browser in Settings.'
    else:
        params['additionalContext']['tcp_chat_instructions'] = {'kind': 'application', 'value': 'Extra'}
    with pytest.raises(CodexRpcError, match='do not match'):
        connection._verify_turn_route(params)


def test_resumed_chat_and_copy_get_note_without_saved_chat_instructions():
    from thechatplace.own_store import OwnSession
    from thechatplace.sessions import SessionInfo
    for operation in ('resume', 'fork'):
        lifecycle = LifecycleConnection()
        threads = CodexOwnedThreads(lifecycle)
        if operation == 'resume':
            record = OwnSession('new', 'Old chat', '/workspace', provider='codex', started=True)
            proof = threads.resume(record)
        else:
            source = SessionInfo('external', 'codex:source', 'Source', '/workspace', 'source', provider='codex')
            proof = threads.fork(source, 'Copy')
        connection = Connection()
        proof = replace(proof, connection=connection)
        connection.proofs[proof.native_id] = proof
        CodexTurnExecutor(connection).start(proof, 'Can you use my browser?')
        assert connection.calls[-1][1]['additionalContext']['tcp_capabilities']['value'] == UNAVAILABLE_NOTE


def test_note_explains_limits_without_denying_enabled_mcp_tools():
    for phrase in ('app-server', 'built-in browser', 'browser extension', 'native computer use',
                   'Codex Cloud', 'No setting', "don't tell the user", 'only to Claude',
                   'API-credit', 'explicitly enabled MCP', 'While The Chat Place',
                   'another app, these limits may not apply'):
        assert phrase in UNAVAILABLE_NOTE


def test_ephemeral_aside_keeps_host_capabilities():
    connection = Connection()
    proof = replace(connection.proof('aside'), ephemeral=True)
    assert instruction_context(proof)['tcp_capabilities']['value'] == UNAVAILABLE_NOTE
