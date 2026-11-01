"""Native controls preserve ownership, queue, scope and persistence guards."""
from copy import deepcopy
from dataclasses import replace

import pytest

from thechatplace.providers.codex_features import CodexFeatureError, validate_action, verify_anchor
from thechatplace.providers.codex_input import build_input, input_items, CodexInput
from thechatplace.providers.codex_owned import CodexOwnedThreads
from thechatplace.providers.codex_rpc import CodexRpcError, CodexTurnRejected, codex_tool_overrides, verify_codex_tools
from thechatplace.providers.codex_tools import server_names, inventory
from thechatplace.providers.codex_events import CodexEvent
from test_codex_owned import Connection as LifecycleConnection, response
from test_codex_runtime import runtime, wait


def picture(tmp_path):
    path = tmp_path / 'reference.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 32)
    return path


def test_images_skills_and_documents_have_native_parts_and_recoverable_text(tmp_path):
    image = picture(tmp_path)
    skill = tmp_path / 'SKILL.md'
    skill.write_text('Only review the supplied file.')
    document = tmp_path / 'notes.txt'
    document.write_text('A note.')
    value = build_input('Check these', [image, document], skills=[('review', skill)])
    assert isinstance(value, str)
    assert input_items(value)[1:] == [{'type': 'localImage', 'path': str(image)},
        {'type': 'skill', 'path': str(skill), 'name': 'review'}]
    assert str(image) in str(value) and str(document) in str(value) and '$review' in value
    assert input_items(build_input('', [image]))[1]['type'] == 'localImage'
    # Send recovery to a plain draft must keep every reference visible.
    assert str(document) in input_items(str(value))[0]['text']
    # A caller cannot mutate a queued input by altering the returned list.
    parts = value.items()
    parts[1]['path'] = 'replaced'
    assert value.items()[1]['path'] == str(image)


def test_missing_images_and_unrecognised_parts_are_rejected(tmp_path):
    image = picture(tmp_path)
    value = build_input('Check', [image])
    image.unlink()
    with pytest.raises(ValueError):
        input_items(value)
    with pytest.raises(ValueError):
        CodexInput('Check', [{'type': 'image', 'url': 'https://example.com/private'}])
    with pytest.raises(ValueError):
        build_input('Check', [tmp_path] * 21)
    with pytest.raises(ValueError):
        build_input('x' * 32001)


def test_queued_image_is_sent_with_its_native_parts(tmp_path):
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Images')
        service.send(record, 'First')
        value = build_input('Then this', [picture(tmp_path)])
        assert service.send(record, value)
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: connection.turns == 2)
        sent = [p for m, p in connection.calls if m == 'turn/start']
        assert sent[-1]['input'] == value.items()
    finally:
        service.close()


def test_branch_resolves_a_native_turn_and_passes_exact_boundary():
    service, connection, _ = runtime()
    try:
        source = service.create('/workspace', 'Original').to_info()
        original = connection.request
        def request(method, params):
            if method == 'thread/turns/list':
                return {'data': [{'id': 'native-turn', 'status': 'completed'}]}
            return original(method, params)
        connection.request = request
        copy = service.fork_at(source, 'Branch', 'native-turn')
        assert copy.cli_session_id != source.cli_session_id
        assert [p for m, p in connection.calls if m == 'thread/fork'][-1]['lastTurnId'] == 'native-turn'
        service.fork_at(source, 'Rewind', 'native-turn', before=True)
        assert [p for m, p in connection.calls if m == 'thread/fork'][-1]['beforeTurnId'] == 'native-turn'
        before = len(connection.calls)
        with pytest.raises(CodexFeatureError):
            service.fork_at(source, 'Missing', 'missing')
        assert not any(m == 'thread/fork' for m, _ in connection.calls[before:])
    finally:
        service.close()


def test_anchor_handles_pagination_and_refuses_unfinished_turn():
    from thechatplace.providers.codex_features import turn_anchor
    from thechatplace.providers.codex import transcript_from_thread
    from test_codex_provider import history
    transcript = transcript_from_thread(history())
    assert turn_anchor(transcript.messages[0]) == 'turn-1'
    assert turn_anchor(transcript.messages[-1]) == 'turn-1'
    class Pages:
        def request(self, method, params):
            return ({'data': [], 'nextCursor': 'next'} if 'cursor' not in params else
                    {'data': [{'id': 'found', 'status': 'inProgress'}]})
    with pytest.raises(CodexFeatureError, match='finish'):
        verify_anchor(Pages(), 'thread', 'found')
    verify_anchor(Pages(), 'thread', 'found', before=True)


@pytest.mark.parametrize('method,args', [
    ('thread/goal/set', {'status': 'active', 'origin': 'user', 'objective': 'Work'}),
    ('thread/goal/set', {'status': 'paused', 'origin': 'automatic'}),
    ('thread/goal/set', {'status': 'paused', 'origin': 'user', 'tokenBudget': True}),
    ('thread/memoryMode/set', {'mode': 'unknown'}),
    ('thread/backgroundTerminals/terminate', {'processId': '../other'}),
    ('thread/compact/start', {'config': {}}),
    ('thread/backgroundTerminals/list', {'cursor': '', 'limit': True}),
    ('thread/delete', {}),
])
def test_feature_validation_does_not_admit_autonomy_or_arbitrary_options(method, args):
    with pytest.raises(CodexFeatureError):
        validate_action(method, dict(args, threadId='owned'))


def test_feature_refuses_active_turn_and_never_replays_unknown_mutation():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Controls')
        service.send(record, 'Work')
        with pytest.raises(CodexFeatureError, match='finish'):
            service.feature(record, 'thread/compact/start')
        assert not any(m == 'thread/compact/start' for m, _ in connection.calls)
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'completed')
        original = connection.request
        mutations = []
        def lost(method, params):
            if method == 'thread/memoryMode/set':
                mutations.append(params)
                raise CodexRpcError('private native failure')
            return original(method, params)
        connection.request = lost
        with pytest.raises(CodexRpcError):
            service.feature(record, 'thread/memoryMode/set', {'mode': 'disabled'})
        with pytest.raises(CodexRpcError):
            service.feature(record, 'thread/memoryMode/set', {'mode': 'disabled'})
        assert len(mutations) == 1 and not service.ready(record)
    finally:
        service.close()


def test_background_terminals_collect_all_pages_and_reject_repeated_cursor():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Terminals')
        original = connection.request
        def request(method, params):
            if method == 'thread/backgroundTerminals/list':
                return ({'data': [{'processId': 'first'}], 'nextCursor': 'next'} if 'cursor' not in params
                        else {'data': [{'processId': 'second'}]})
            return original(method, params)
        connection.request = request
        assert service.feature(record, 'thread/backgroundTerminals/list')['data'] == [
            {'processId': 'first'}, {'processId': 'second'}]
        connection.request = lambda m, p: {'data': [], 'nextCursor': 'repeat'}
        with pytest.raises(CodexFeatureError):
            service.feature(record, 'thread/backgroundTerminals/list')
    finally:
        service.close()


def test_tool_selection_enables_only_explicit_configured_servers():
    config = {'mcp_servers': {'allowed': {'command': 'server'}, 'other': {'url': 'https://example.com'}}}
    before = deepcopy(config)
    overrides = codex_tool_overrides(config, ['allowed'])
    assert config == before
    assert overrides == {'mcp_servers': {'allowed': {'enabled': True}, 'other': {'enabled': False}},
                         'features': {'plugins': False, 'apps': False}}
    with pytest.raises(CodexTurnRejected):
        codex_tool_overrides(config, ['invented'])
    for invalid in ['allowed', ['a', 'a'], ['bad\nname']]:
        with pytest.raises(ValueError):
            server_names(invalid)


def test_tool_verification_rejects_unselected_or_plugin_servers():
    class Status:
        rows = []
        def request(self, method, params):
            return {'data': self.rows}
    c = Status()
    c.rows = [{'name': 'allowed', 'runtimeStatus': 'connected', 'tools': {'read': {}}}]
    verify_codex_tools(c, 'owned', ['allowed'])
    with pytest.raises(CodexTurnRejected):
        verify_codex_tools(c, 'owned')
    c.rows[0]['pluginId'] = 'plugin'
    with pytest.raises(CodexTurnRejected):
        verify_codex_tools(c, 'owned', ['allowed'])


def test_explicit_tool_selection_survives_saved_reopen_and_passive_forks_disable_execution():
    c = LifecycleConnection(config={'mcp_servers': {'notes': {}}})
    proof = CodexOwnedThreads(c).start('/workspace', 'Tools', mcp_servers=['notes'])
    assert proof.mcp_servers == ('notes',)
    record = proof.record()
    assert record.provider_options['mcp_servers'] == ['notes']
    c.result = response(proof.native_id)
    assert CodexOwnedThreads(c).resume(record).mcp_servers == ('notes',)
    c.result = response('aside')
    c.result['thread']['ephemeral'] = True
    c.result['approvalPolicy'] = 'never'
    aside = CodexOwnedThreads(c).fork(record.to_info(), 'Aside', ephemeral=True,
        sandbox='read-only', approval_policy='never', passive=True)
    params = [p for m, p in c.calls if m == 'thread/fork'][-1]
    assert all(params['config']['features'][feature] is False for feature in
               ('hooks', 'goals', 'memories', 'shell_tool', 'unified_exec', 'js_repl', 'multi_agent'))
    assert not aside.mcp_servers
    assert params['config']['mcp_servers']['notes']['enabled'] is False


def test_effective_configured_effort_is_pinned_instead_of_inheriting_old_effort():
    result = response()
    result['reasoningEffort'] = 'medium'
    proof = CodexOwnedThreads(LifecycleConnection(result)).start('/workspace', 'Configured')
    assert proof.effort == 'medium'
    assert proof.record().provider_options['effort'] == 'medium'


def test_aside_declines_all_questions_and_approvals_without_waiting():
    from thechatplace.providers.codex_aside import AsideConnection
    from thechatplace.providers.codex_requests import CodexRequest, QUESTIONS, COMMAND
    connection = object.__new__(AsideConnection)
    for method in (QUESTIONS, COMMAND):
        assert not connection._accept_server_request(CodexRequest(1, method, 'aside', 'turn', 'item'))


def test_aside_cancel_emits_one_terminal_event_even_before_start():
    from thechatplace.providers.codex_aside import CodexAsideRunner
    from thechatplace.aside import AsideEvent
    events = []
    runner = CodexAsideRunner(None, 'Question', events.append)
    runner.cancel()
    runner.cancel()
    runner._emit(AsideEvent('done', 'Too late'))
    runner._emit(AsideEvent('text', 'Too late'))
    assert [(event.kind, event.text) for event in events] == [('failed', 'Stopped.')]


def test_settings_change_pins_default_and_defers_compaction_until_a_normal_turn():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Settings')
        change = service.prepare_settings(record, 'gpt-default', None)
        assert change.proof.effort == 'future-effort'  # advertised default, not null
        assert not service.ready(record)  # save/finish is a real send barrier
        record = change.record()
        service.finish_policy(change, True)
        with pytest.raises(CodexFeatureError, match='normal message'):
            service.feature(record, 'thread/compact/start')
        service.send(record, 'Apply these settings')
        sent = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert sent['effort'] == 'future-effort'
        assert record.cli_session_id not in service._settings_pending
    finally:
        service.close()


def test_feature_connection_rechecks_account_route_and_tools_before_mutation(monkeypatch):
    import threading
    from thechatplace.providers.codex_execution import CodexExecutionConnection
    from thechatplace.providers.codex_owned import CodexOwnedThread
    from thechatplace.providers.codex_rpc import ReadOnlyCodexConnection
    c = object.__new__(CodexExecutionConnection)
    c._runtime_lock, c._lock = threading.Lock(), threading.Lock()
    c._closed = False
    proof = CodexOwnedThread('owned', '/workspace', 'gpt-default', 'Test', 0, c)
    c._runtime_threads = {'owned': proof}
    calls = []
    account = {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
    fake = LifecycleConnection()
    def rpc(self, method, params):
        calls.append((method, params))
        if method == 'account/read':
            return account
        if method == 'thread/read':
            return {'thread': {'id': 'owned', 'modelProvider': 'openai', 'cwd': '/workspace',
                               'status': {'type': 'idle'}}}
        if method == 'thread/turns/list':
            pytest.fail('Unsent chats have no rollout history')
        return fake.request(method, params)
    monkeypatch.setattr(ReadOnlyCodexConnection, 'request', rpc)
    c.request('thread/memoryMode/set', {'threadId': 'owned', 'mode': 'disabled'})
    assert calls[0][0] == 'account/read'
    assert [m for m, p in calls][-1] == 'thread/memoryMode/set'
    calls.clear()
    account['account']['type'] = 'apiKey'
    with pytest.raises(CodexTurnRejected, match='ChatGPT'):
        c.request('thread/memoryMode/set', {'threadId': 'owned', 'mode': 'disabled'})
    assert [m for m, p in calls] == ['account/read']
    calls.clear()
    c._runtime_threads.clear()
    with pytest.raises(CodexFeatureError):
        c.request('thread/goal/clear', {'threadId': 'owned'})
    assert not calls


def test_missing_queued_attachment_does_not_close_other_chats(tmp_path):
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Images')
        other = service.create('/workspace', 'Other')
        service.send(record, 'First')
        service.send(other, 'Continue')
        path = picture(tmp_path)
        service.send(record, build_input('Then image', [path]))
        path.unlink()
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: any(state.unsent for state in _))
        assert not connection.closed
        assert service.snapshot(other.cli_session_id).outcome == 'inProgress'
    finally:
        service.close()


def test_plan_mode_is_persisted_and_pinned_to_each_turn_and_can_be_left():
    service, connection, _ = runtime()
    original = connection.request
    def request(method, params):
        if method == 'collaborationMode/list':
            return {'data': [{'name': 'Plan', 'mode': 'plan'}, {'name': 'Default', 'mode': 'default'}]}
        return original(method, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Planning')
        change = service.prepare_mode(record, 'plan')
        record = change.record()
        service.finish_policy(change, True)
        assert record.provider_options['collaboration'] == 'plan'
        service.send(record, 'Plan first')
        params = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert params['collaborationMode'] == {'mode': 'plan', 'settings': {
            'model': 'gpt-default', 'reasoning_effort': None, 'developer_instructions': None}}
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'completed')
        change = service.prepare_mode(record, 'default')
        record = change.record()
        service.finish_policy(change, True)
        service.send(record, 'Now carry it out')
        params = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert params['collaborationMode']['mode'] == 'default'
    finally:
        service.close()


def test_oversized_unicode_message_does_not_disconnect_other_chats():
    service, connection, _ = runtime()
    try:
        one = service.create('/workspace', 'Active')
        two = service.create('/workspace', 'Oversized')
        service.send(one, 'Keep working')
        with pytest.raises(CodexTurnRejected, match='size limit'):
            service.send(two, '\U0001f600' * 20000)
        assert not connection.closed and connection.turns == 1
        assert service.snapshot(one.cli_session_id).outcome == 'inProgress'
        assert service.ready(two)
    finally:
        service.close()


def test_native_compaction_turn_events_arrive_before_the_rpc_acknowledgment():
    service, connection, _ = runtime()
    original = connection.request
    def request(method, params):
        if method == 'thread/compact/start':
            connection.events.append(CodexEvent('turn_started', params['threadId'], 'compact-turn'))
            wait(lambda: service.snapshot(params['threadId']).outcome == 'inProgress')
            connection.events.append(CodexEvent('turn_completed', params['threadId'], 'compact-turn', status='completed'))
            return {}
        return original(method, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Compact')
        assert service.feature(record, 'thread/compact/start') == {}
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'completed')
        assert service.snapshot(record.cli_session_id).turn_id == 'compact-turn'
        service.send(record, 'Continue normally')
        assert service.snapshot(record.cli_session_id).outcome == 'inProgress'
    finally:
        service.close()
