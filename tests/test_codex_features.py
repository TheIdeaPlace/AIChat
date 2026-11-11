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
    ('thread/delete', {'recursive': False}),
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


def test_only_an_owned_codex_chat_has_delete_capability():
    from thechatplace.providers.codex import CodexProvider
    service, connection, _ = runtime()
    try:
        provider = CodexProvider(connection, runtime=service)
        assert provider.capabilities(_session()).delete
        assert not provider.capabilities(_session(is_own=False)).delete
        assert not CodexProvider(connection).capabilities(_session()).delete
    finally:
        service.close()


def test_delete_retires_only_the_selected_owned_chat():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Delete this')
        other = service.create('/workspace', 'Keep this')
        original = connection.request
        deleted = []
        def request(method, params):
            if method == 'thread/delete':
                deleted.append(params)
                return {}
            return original(method, params)
        connection.request = request
        assert service.delete(record) == {}
        assert deleted == [{'threadId': record.cli_session_id}]
        assert not service.known(record.cli_session_id)
        assert record.cli_session_id not in connection.proofs
        assert service.ready(other)
        for action in (service.prepare, service.reopen, service.delete):
            with pytest.raises(CodexRpcError):
                action(record)
        assert len(deleted) == 1
    finally:
        service.close()


def test_delete_refuses_a_running_chat_and_unowned_record_before_rpc():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Working')
        with pytest.raises(CodexRpcError):
            service.delete(replace(record, provider='claude'))
        service.send(record, 'Work')
        with pytest.raises(CodexFeatureError, match='finish'):
            service.delete(record)
        assert not any(m == 'thread/delete' for m, _ in connection.calls)
    finally:
        service.close()


def test_delete_does_not_replay_an_uncertain_native_result():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Uncertain')
        original = connection.request
        attempts = []
        def request(method, params):
            if method == 'thread/delete':
                attempts.append(params)
                raise CodexRpcError('Synthetic lost response')
            return original(method, params)
        connection.request = request
        for _ in range(2):
            with pytest.raises(CodexRpcError):
                service.delete(record)
        assert len(attempts) == 1
        assert service.known(record.cli_session_id)
        assert record.cli_session_id in connection.proofs
        assert not service.ready(record)
    finally:
        service.close()


def test_delete_serializes_with_sends_and_other_deletions():
    import threading
    service, connection, _ = runtime()
    entered, release = threading.Event(), threading.Event()
    errors = []
    worker = None
    try:
        record = service.create('/workspace', 'Deleting')
        original = connection.request
        attempts = []
        def request(method, params):
            if method == 'thread/delete':
                attempts.append(params)
                entered.set()
                assert release.wait(5)
                return {}
            return original(method, params)
        connection.request = request
        def delete():
            try:
                service.delete(record)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=delete)
        worker.start()
        assert entered.wait(5)
        with pytest.raises(CodexRpcError):
            service.send(record, 'Too late')
        with pytest.raises(CodexRpcError):
            service.delete(record)
        release.set()
        worker.join(5)
        assert not worker.is_alive() and not errors
        assert len(attempts) == 1
    finally:
        release.set()
        if worker is not None:
            worker.join(5)
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
    # What's reported is pinned on each turn; only a chosen effort is saved.
    assert 'effort' not in proof.record().provider_options


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


@pytest.mark.parametrize('method,arguments', [
    ('thread/memoryMode/set', {'mode': 'disabled'}), ('thread/delete', {}),
])
def test_feature_connection_rechecks_account_route_and_tools_before_mutation(monkeypatch, method, arguments):
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
    c.request(method, dict(arguments, threadId='owned'))
    assert calls[0][0] == 'account/read'
    assert [m for m, p in calls][-1] == method
    calls.clear()
    account['account']['type'] = 'apiKey'
    with pytest.raises(CodexTurnRejected, match='ChatGPT'):
        c.request(method, dict(arguments, threadId='owned'))
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


# -- through the real store (#467): these passed against fakes while no real chat could save ---


def _store(tmp_path):
    from thechatplace.own_store import OwnSessionStore
    return OwnSessionStore(tmp_path / 'sessions.json')


def _on_disk(tmp_path):
    import json
    return json.loads((tmp_path / 'provider-sessions.json').read_text(encoding='utf-8'))['sessions'][0]


def test_plan_mode_and_tools_survive_a_real_save_and_reload(tmp_path):
    from thechatplace.own_store import OwnSession
    options = {'model': 'gpt-default', 'sandbox': 'read-only', 'approval_policy': 'on-request',
               'collaboration': 'plan', 'mcp_servers': ['docs', 'build reports']}
    store = _store(tmp_path)
    store.add(OwnSession('owned', 'Planning', '/workspace', provider='codex', provider_options=options))
    again = _store(tmp_path).get('owned', provider='codex')
    assert again.provider_options == options
    store.update('owned', provider='codex', provider_options={**options, 'collaboration': 'default'})
    assert _store(tmp_path).get('owned', provider='codex').provider_options['collaboration'] == 'default'


def test_an_older_release_can_still_read_the_file(tmp_path):
    # 0.4 refuses all of provider-sessions.json, and then saves nothing, over a
    # provider option it doesn't know. These are the only ones it knows.
    from thechatplace.own_store import OwnSession
    store = _store(tmp_path)
    store.add(OwnSession('owned', 'Planning', '/workspace', provider='codex', provider_options={
        'model': 'gpt-default', 'collaboration': 'plan', 'mcp_servers': ['docs']}))
    item = _on_disk(tmp_path)
    assert set(item['provider_options']) <= {'model', 'effort', 'sandbox', 'approval_policy',
                                             'workspace_root', 'approvals_reviewer'}
    assert all(isinstance(v, str) for v in item['provider_options'].values())
    assert item['provider_extras'] == {'collaboration': 'plan', 'mcp_servers': ['docs']}


def test_a_chat_without_the_new_settings_is_stored_as_before(tmp_path):
    from thechatplace.own_store import OwnSession
    _store(tmp_path).add(OwnSession('owned', 'Plain', '/workspace', provider='codex',
                                    provider_options={'model': 'gpt-default'}))
    assert 'provider_extras' not in _on_disk(tmp_path)


@pytest.mark.parametrize('extras', [{'collaboration': 'everything'}, {'mcp_servers': 'docs'},
                                    {'mcp_servers': ['a\nb']}, {'mcp_servers': ['a', 'a']},
                                    {'unknown': 'x'}, 'not a dict'])
def test_a_hand_edit_of_the_new_settings_is_dropped_not_refused(tmp_path, extras):
    import json
    (tmp_path / 'provider-sessions.json').write_text(json.dumps({'version': 2, 'sessions': [{
        'cli_session_id': 'owned', 'title': 'T', 'cwd': '/workspace', 'provider': 'codex',
        'provider_options': {'model': 'gpt-default'}, 'provider_extras': extras}]}), encoding='utf-8')
    store = _store(tmp_path)
    assert not store.load_error
    assert store.get('owned', provider='codex').provider_options == {'model': 'gpt-default'}


def _with_modes(connection):
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            return {'data': [{'name': 'Plan', 'mode': 'plan'}, {'name': 'Default', 'mode': 'default'}]}
        return original(method, params)
    connection.request = request


def test_plan_mode_goes_all_the_way_through_the_store_and_a_reopen(tmp_path):
    service, connection, _ = runtime()
    _with_modes(connection)
    store = _store(tmp_path)
    try:
        record = service.create('/workspace', 'Planning')
        store.add(record)
        change = service.prepare_mode(store.get(record.cli_session_id, provider='codex'), 'plan')
        store.update(record.cli_session_id, provider='codex',
                     provider_options=change.record().provider_options)
        service.finish_policy(change, True)
        saved = _store(tmp_path).get(record.cli_session_id, provider='codex')
        assert saved.provider_options['collaboration'] == 'plan'
        service.send(saved, 'Plan first')
        params = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert params['collaborationMode']['mode'] == 'plan'
    finally:
        service.close()


def test_a_chat_following_its_configured_effort_can_still_change_permissions():
    # A chat saved by 0.4 has no effort, and Codex reports the configured one.
    # Saving what Codex reports made the record and the proof disagree, which
    # locked the chat, with Stop refused the same way.
    from thechatplace.own_store import OwnSession
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        answer = original(method, params)
        if method in ('thread/resume', 'thread/start') and isinstance(answer, dict):
            answer = {**answer, 'reasoningEffort': 'high'}
        return answer
    connection.request = request
    try:
        record = service.create('/workspace', 'Older')
        assert 'effort' not in record.provider_options
        proof = service._proofs[record.cli_session_id]
        assert proof.effort == 'high' and proof.record().provider_options == record.provider_options
        service._require_saved_policy(OwnSession(record.cli_session_id, 'Older', '/workspace',
            provider='codex', provider_options=dict(record.provider_options)), proof.record())
        change = service.prepare_policy(record, 'read-only', 'on-request')
        assert 'effort' not in change.record().provider_options
        service.finish_policy(change, True)
        assert service.send(change.record(), 'Hello') is not None
        assert [p for m, p in connection.calls if m == 'turn/start'][-1]['effort'] == 'high'
    finally:
        service.close()


def test_a_chat_on_a_retired_model_still_opens():
    from thechatplace.own_store import OwnSession
    connection = LifecycleConnection(response('owned'))
    original = connection.request

    def request(method, params):
        if method == 'model/list':
            return {'data': [{'id': 'gpt-now', 'model': 'gpt-now', 'displayName': 'Now',
                              'hidden': False, 'isDefault': True,
                              'supportedReasoningEfforts': [{'reasoningEffort': 'low'}],
                              'defaultReasoningEffort': 'low'}], 'nextCursor': None}
        answer = original(method, params)
        return {**answer, 'reasoningEffort': 'high'} if method == 'thread/resume' else answer
    connection.request = request
    record = OwnSession('owned', 'Older', '/workspace', provider='codex',
                        provider_options={'model': 'gpt-default', 'effort': 'high'})
    proof = CodexOwnedThreads(connection).resume(record)
    assert proof.record().provider_options['effort'] == 'high'
    # A model still listed keeps its check on the effort.
    gone = OwnSession('owned', 'Older', '/workspace', provider='codex',
                      provider_options={'model': 'gpt-now', 'effort': 'high'})
    with pytest.raises(ValueError):
        CodexOwnedThreads(connection).resume(gone)


def _retired_model_chat(tmp_path):
    """A chat on gpt-default, which the catalog has since stopped listing."""
    from test_codex_runtime import policy_runtime
    service, connection, record = policy_runtime(tmp_path, model='gpt-default')
    assert record.provider_options['model'] == 'gpt-default'
    original = connection.request

    def request(method, params):
        if method == 'model/list':
            return {'data': [{'id': 'gpt-now', 'model': 'gpt-now', 'displayName': 'Now',
                              'hidden': False, 'isDefault': True,
                              'supportedReasoningEfforts': [{'reasoningEffort': 'low'}],
                              'defaultReasoningEffort': 'low'}], 'nextCursor': None}
        return original(method, params)
    connection.request = request
    return service, connection, record


def test_a_retired_model_can_change_permissions_and_still_send(tmp_path):
    # The check in a temporary thread asked the catalog about the chat's own
    # model, which latched the chat with no way out (#467).
    service, connection, record = _retired_model_chat(tmp_path)
    try:
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        service.finish_policy(change, True)
        assert not service.snapshot(record.cli_session_id).problem
        assert service.send(change.record(), 'Hello') is not None
        assert [p for m, p in connection.calls if m == 'turn/start'][-1]['model'] == 'gpt-default'
    finally:
        service.close()


def test_a_retired_model_recovers_with_stop_after_a_permission_change_fails_to_save(tmp_path):
    # Change permission, Stop, send: Stop's recheck starts a temporary thread too.
    service, connection, record = _retired_model_chat(tmp_path)
    try:
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        service.finish_policy(change, False)
        assert 'Press Stop' in service.snapshot(record.cli_session_id).problem
        assert service.recover(record) == 'permissions'
        assert not service.snapshot(record.cli_session_id).problem
        assert service.send(record, 'Hello') is not None
    finally:
        service.close()


def test_a_retired_model_can_change_mode(tmp_path):
    service, connection, record = _retired_model_chat(tmp_path)
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            return {'data': [{'mode': 'plan'}, {'mode': 'default'}]}
        return original(method, params)
    connection.request = request
    try:
        change = service.prepare_mode(record, 'plan')
        service.finish_policy(change, True)
        assert change.record().provider_options['collaboration'] == 'plan'
        assert service.ready(change.record())
    finally:
        service.close()


def test_choosing_a_retired_model_is_still_refused_and_the_chat_keeps_sending(tmp_path):
    # The exemption is for a chat staying on its model, not moving to one.
    service, connection, record = _retired_model_chat(tmp_path)
    try:
        with pytest.raises(ValueError, match='no longer available'):
            service.prepare_settings(record, 'gpt-gone', None)
        with pytest.raises(ValueError, match='no longer available'):
            service.prepare_settings(record, 'gpt-default', 'high')
        assert not service.snapshot(record.cli_session_id).problem
        assert service.ready(record)
    finally:
        service.close()


# -- native controls beside a running turn (#467) --------------------------------------


def _blocked(connection, method):
    """A connection whose ``method`` waits until released, as a slow RPC does."""
    import threading
    entered, release = threading.Event(), threading.Event()
    original = connection.request

    def request(called, params):
        if called == method:
            entered.set()
            assert release.wait(5)
            return {'data': [], 'goal': None}
        return original(called, params)
    connection.request = request
    return entered, release


def _in_thread(action):
    import threading
    results = []

    def run():
        try:
            results.append(action())
        except Exception as error:  # noqa: BLE001 - the test reads it
            results.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results


@pytest.mark.parametrize('method', ['thread/goal/get', 'thread/backgroundTerminals/list'])
def test_a_read_in_flight_leaves_send_and_stop_free(method):
    service, connection, _ = runtime()
    entered, release = _blocked(connection, method)
    try:
        record = service.create('/workspace', 'Controls')
        native = record.cli_session_id
        thread, results = _in_thread(lambda: service.feature(record, method))
        assert entered.wait(5)
        assert native not in service._reopening and service.ready(record)
        assert service.send(record, 'Work') is False  # starts at once
        assert connection.turns == 1
        assert service.send(record, 'Then this') is True  # queued, not refused
        assert service.stop(record) == ('Then this',)
        assert any(m == 'turn/interrupt' for m, _p in connection.calls)
        release.set()
        thread.join(5)
        assert isinstance(results[0], dict)
    finally:
        release.set()
        service.close()


def test_an_answer_is_not_blocked_by_a_read_in_flight():
    # _approval_dispatch and _auto_answer both stand down while a chat is
    # reserved, which turned a remembered approval into a manual prompt.
    service, connection, _ = runtime()
    entered, release = _blocked(connection, 'thread/backgroundTerminals/list')
    try:
        record = service.create('/workspace', 'Controls')
        service.send(record, 'Work')
        thread, _results = _in_thread(lambda: service.feature(record, 'thread/backgroundTerminals/list'))
        assert entered.wait(5)
        proof, executor = service._context(record)  # what answer() itself asks
        assert proof.native_id not in service._reopening and executor is service._executor
        release.set()
        thread.join(5)
    finally:
        release.set()
        service.close()


def test_background_terminals_can_be_listed_and_stopped_with_a_message_queued():
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        if method == 'thread/backgroundTerminals/list':
            return {'data': [{'processId': 'one'}]}
        if method == 'thread/backgroundTerminals/terminate':
            return {'terminated': True}
        return original(method, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Controls')
        service.send(record, 'Work')
        assert service.send(record, 'Queued') is True
        assert service.feature(record, 'thread/backgroundTerminals/list')['data'] == [{'processId': 'one'}]
        assert service.feature(record, 'thread/backgroundTerminals/terminate',
                               {'processId': 'one'}) == {'terminated': True}
        assert service.snapshot(record.cli_session_id).queued == ('Queued',)
    finally:
        service.close()


def test_what_needs_an_idle_chat_still_holds_it_and_waits_for_the_queue():
    service, connection, _ = runtime()
    entered, release = _blocked(connection, 'thread/memoryMode/set')
    try:
        record = service.create('/workspace', 'Controls')
        thread, _results = _in_thread(lambda: service.feature(record, 'thread/memoryMode/set', {'mode': 'enabled'}))
        assert entered.wait(5)
        assert not service.ready(record)
        with pytest.raises(CodexFeatureError, match='finish'):
            service.feature(record, 'thread/goal/get')  # busy, not "Reopen this chat"
        release.set()
        thread.join(5)
        assert service.ready(record)
        service.send(record, 'Work')
        service.send(record, 'Queued')
        with pytest.raises(CodexFeatureError, match='finish'):
            service.feature(record, 'thread/memoryMode/set', {'mode': 'enabled'})
    finally:
        release.set()
        service.close()


# -- what Codex's refusals say (#467) ----------------------------------------------------


def _refused(code, unknown=False):
    error = CodexRpcError('Codex request failed')
    error.rpc_code, error.unknown_method = code, unknown
    return error


@pytest.mark.parametrize('method,arguments', [
    ('thread/memoryMode/set', {'mode': 'enabled'}), ('thread/compact/start', {})])
@pytest.mark.parametrize('code,unknown,reason', [
    (-32601, True, 'unsupported'), (-32600, True, 'unsupported'),
    (-32600, False, 'refused'), (-32603, False, 'refused'), (-1, False, 'refused')])
def test_a_refusal_says_which_kind_and_never_blocks_the_chat(method, arguments, code, unknown, reason):
    service, connection, _ = runtime()
    original = connection.request

    def request(called, params):
        if called == method:
            raise _refused(code, unknown)
        return original(called, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Controls')
        with pytest.raises(CodexFeatureError) as refused:
            service.feature(record, method, arguments)
        assert refused.value.code == reason
        assert ('version' in str(refused.value)) is (reason == 'unsupported')
        assert service.ready(record) and not service.snapshot(record.cli_session_id).problem
        assert not service._executor._native_starts  # no compaction is expected now
    finally:
        service.close()


@pytest.mark.parametrize('method,arguments', [
    ('thread/goal/clear', {}), ('thread/backgroundTerminals/terminate', {'processId': 'one'})])
def test_a_lost_answer_about_a_goal_or_terminal_leaves_the_turn_and_queue_alone(method, arguments):
    service, connection, updates = runtime()
    original = connection.request

    def request(called, params):
        if called == method:
            raise CodexRpcError('private native failure')
        return original(called, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Controls')
        service.send(record, 'Work')
        service.send(record, 'Queued')
        with pytest.raises(CodexRpcError):
            service.feature(record, method, arguments)
        state = service.snapshot(record.cli_session_id)
        assert not state.problem and state.queued == ('Queued',) and service.ready(record)
    finally:
        service.close()


def test_the_connection_notes_an_unknown_method_without_keeping_codexs_words():
    import threading
    from thechatplace.providers.codex_rpc import ReadOnlyCodexConnection, _Pending
    connection = object.__new__(ReadOnlyCodexConnection)
    connection._lock, connection._closed = threading.Lock(), False
    connection._abandoned, connection._abandoned_before = set(), 0

    def answer(error):
        pending = _Pending()
        connection._pending = {1: pending}
        connection._receive({'id': 1, 'error': error})
        return pending.error
    known = answer({'code': -32600, 'message': 'Invalid request: unknown variant `thread/x`'})
    assert known.rpc_code == -32600 and known.unknown_method and 'variant' not in str(known)
    assert answer({'code': -32601, 'message': 'x'}).unknown_method
    assert answer({'code': -32601}).unknown_method
    assert not answer({'code': -32600, 'message': 'turn is not active'}).unknown_method
    assert not answer('not a dict').unknown_method


# -- changing the model, effort or mode (#467) ------------------------------------------


@pytest.mark.parametrize('prepare,arguments', [('prepare_settings', ('gpt-default', None)),
                                               ('prepare_mode', ('plan',))])
@pytest.mark.parametrize('failure', ['ephemeral', 'list', 'value'])
def test_a_failed_setting_change_leaves_the_chat_sending(prepare, arguments, failure):
    service, connection, updates = runtime()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            if failure == 'list':
                raise _refused(-32601, True)
            return {'data': [{'mode': 'plan'}, {'mode': 'default'}]}
        if method == 'model/list' and failure == 'list':
            raise _refused(-32601, True)
        if method == 'thread/start' and params.get('ephemeral'):
            if failure == 'value':
                raise ValueError('Codex model or reasoning effort is no longer available')
            if failure == 'ephemeral':
                raise CodexRpcError('Codex request failed')
        return original(method, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Settings')
        with pytest.raises((CodexRpcError, ValueError)):
            getattr(service, prepare)(record, *arguments)
        state = service.snapshot(record.cli_session_id)
        assert not state.problem and service.ready(record)
        assert service.send(record, 'Still works') is False
        assert not any('permissions' in update.problem for update in updates)
    finally:
        service.close()


@pytest.mark.parametrize('code,unknown', [(-32600, True), (-32601, True), (-32601, False)])
def test_choosing_a_mode_codex_has_no_list_for_says_to_update(code, unknown):
    from thechatplace.codex_diagnostics import safe_reason
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            raise _refused(code, unknown)
        return original(method, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Plan')
        with pytest.raises(CodexFeatureError) as refused:
            service.prepare_mode(record, 'plan')
        assert refused.value.code == 'unsupported'
        assert 'Update Codex' in safe_reason(refused.value)
        assert service.ready(record)
    finally:
        service.close()


@pytest.mark.parametrize('code', [-32603, -32600])
def test_collaboration_list_failing_some_other_way_is_not_called_unsupported(code):
    # -32600 is also what a known method says when it refuses a request.
    connection = LifecycleConnection()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            raise _refused(code)
        return original(method, params)
    connection.request = request
    with pytest.raises(CodexRpcError) as failed:
        CodexOwnedThreads(connection).start('/workspace', 'Plan', collaboration='plan')
    assert not isinstance(failed.value, CodexFeatureError)


def test_a_setting_that_could_not_be_saved_changes_nothing_and_keeps_sending():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Settings')
        change = service.prepare_settings(record, 'gpt-default', None)
        assert not service.ready(record)  # still a barrier while it saves
        service.finish_policy(change, False)
        assert not service.snapshot(record.cli_session_id).problem
        assert service.ready(record) and service._proofs[record.cli_session_id] is change.previous
    finally:
        service.close()


def test_a_setting_saved_when_the_connection_went_says_what_was_changing():
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Settings')
        change = service.prepare_settings(record, 'gpt-default', None)
        connection.closed = True
        service.finish_policy(change, True)
        problem = service.snapshot(record.cli_session_id).problem
        assert 'model or effort' in problem and 'permissions' not in problem
        from thechatplace.providers.codex_runtime import STOP_TO_RECHECK
        assert problem == ("The new model or effort couldn't be saved, or Codex didn't confirm the change. "
                           + STOP_TO_RECHECK)
        assert STOP_TO_RECHECK.startswith('Press Stop to have The Chat Place check this chat')
    finally:
        service.close()


def _saved(options):
    from thechatplace.own_store import OwnSession
    return OwnSession('owned', 'Saved plan', '/workspace', provider='codex', started=True,
                      provider_options={'model': 'gpt-default', 'sandbox': 'read-only',
                                        'approval_policy': 'on-request', **options})


@pytest.mark.parametrize('answer', [_refused(-32601), _refused(-32600, True), {'data': []}])
def test_a_saved_plan_mode_does_not_stop_a_chat_opening_on_a_codex_without_it(answer):
    service, connection, updates = runtime()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            if isinstance(answer, Exception):
                raise answer
            return answer
        return original(method, params)
    connection.request = request
    try:
        record = _saved({'collaboration': 'plan'})
        reopened = service.reopen(record)
        # The choice stays saved, so a later Codex offers it again, and the
        # saved-grant check on Stop still matches.
        assert reopened.provider_options['collaboration'] == 'plan'
        service._require_saved_policy(record, service._proofs['owned'].record())
        state = service.snapshot('owned')
        assert state.plan_unavailable and 'Default' in state.notice and not state.problem
        service.send(record, 'Carry on')
        assert 'collaborationMode' not in [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert not service.snapshot('owned').notice  # said once, for this opening
    finally:
        service.close()


def test_a_saved_mode_stays_saved_through_a_permission_change():
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            raise _refused(-32601)
        return original(method, params)
    connection.request = request
    try:
        record = service.reopen(_saved({'collaboration': 'plan'}))
        change = service.prepare_policy(record, 'read-only', 'on-request')
        assert change.record().provider_options['collaboration'] == 'plan'
        service.finish_policy(change, True)
        assert service.ready(change.record())
    finally:
        service.close()


def test_a_saved_default_mode_on_a_codex_without_modes_says_nothing():
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            raise _refused(-32601)
        return original(method, params)
    connection.request = request
    try:
        service.reopen(_saved({'collaboration': 'default'}))
        state = service.snapshot('owned')
        assert not state.notice and not state.plan_unavailable
    finally:
        service.close()


def test_a_saved_mode_refused_for_another_reason_still_fails_the_resume():
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            raise CodexRpcError('Codex request failed')
        return original(method, params)
    connection.request = request
    try:
        with pytest.raises(CodexRpcError):
            service.reopen(_saved({'collaboration': 'plan'}))
    finally:
        service.close()


def test_choosing_a_mode_after_updating_codex_clears_the_unavailable_one():
    service, connection, _ = runtime()
    original = connection.request
    has_modes = []

    def request(method, params):
        if method == 'collaborationMode/list':
            if not has_modes:
                raise _refused(-32601)
            return {'data': [{'mode': 'plan'}, {'mode': 'default'}]}
        return original(method, params)
    connection.request = request
    try:
        record = service.reopen(_saved({'collaboration': 'plan'}))
        has_modes.append(True)
        change = service.prepare_mode(record, 'plan')
        service.finish_policy(change, True)
        assert not service.snapshot('owned').plan_unavailable
        assert change.proof.collaboration == 'plan'
    finally:
        service.close()


# -- a selected tool removed from config.toml (#467) -------------------------------------


def _session(native='owned', is_own=True):
    from thechatplace.sessions import SessionInfo, OWN
    return SessionInfo(OWN if is_own else 'external', 'codex:' + native, 'Tools', '/workspace', native,
                       provider='codex')


def test_a_removed_server_is_refused_with_why_and_a_way_out():
    from thechatplace.codex_diagnostics import safe_reason
    with pytest.raises(CodexTurnRejected) as refused:
        codex_tool_overrides({'mcp_servers': {'kept': {}}}, ['gone'])
    assert refused.value.code == 'mcp_server_removed'
    assert 'no longer in your Codex configuration' in safe_reason(refused.value)
    assert 'without it' in safe_reason(refused.value)


def test_the_list_shows_a_removed_server_first_instead_of_failing():
    connection = LifecycleConnection(config={'mcp_servers': {'kept': {}, 'other': {}}})
    rows = inventory(connection, _session(), ['kept', 'gone'])
    assert [(r.name, r.missing, r.off_for_chat) for r in rows] == [
        ('gone', True, False), ('kept', False, False), ('other', False, True)]
    assert "can't send" in rows[0].text() and 'copy without it' in rows[0].text()
    # A broken configuration is still refused: only a missing name is listed.
    with pytest.raises(CodexTurnRejected):
        inventory(LifecycleConnection(config={'mcp_servers': ['bad']}), _session(), ())


def test_a_chat_that_cannot_reopen_can_still_list_its_tools():
    # A removed server stops the chat resuming, so it has no runtime proof, and
    # this list is the way out of that.
    from thechatplace.providers.codex import CodexProvider
    from types import SimpleNamespace

    def not_loaded(_native):
        raise CodexFeatureError('unavailable')
    connection = LifecycleConnection(config={'mcp_servers': {'kept': {}}})
    original = connection.request

    def request(method, params):
        if method == 'mcpServerStatus/list':
            raise CodexRpcError('thread not loaded here')
        return original(method, params)
    connection.request = request
    provider = CodexProvider(connection, runtime=SimpleNamespace(tools_context=not_loaded))
    rows = provider.connected_tools(_session(), selected=['gone'])
    assert [(r.name, r.missing) for r in rows] == [('gone', True), ('kept', False), ('Apps unavailable', False)]
    with pytest.raises(CodexFeatureError):  # with no saved selection, as before
        provider.connected_tools(_session())
    # A loaded chat's status failure isn't hidden.
    provider = CodexProvider(connection, runtime=SimpleNamespace(
        tools_context=lambda native: (connection, ('kept',)), selected_apps=lambda native: ()))
    with pytest.raises(CodexRpcError):
        provider.connected_tools(_session())


def test_a_copy_without_the_removed_server_is_made_and_saved():
    service, connection, _ = runtime()
    original = connection.request
    config = {'mcp_servers': {'kept': {}, 'gone': {}}}

    def request(method, params):
        answer = original(method, params)
        if method == 'config/read':
            return {**answer, 'config': config}
        if method == 'mcpServerStatus/list':
            return {'data': [dict(name=name, tools={}, runtimeStatus=('connected' if name in names else 'disabled'))
                             for name in config['mcp_servers']], 'nextCursor': None}
        return answer
    connection.request = request
    names = ['kept', 'gone']
    try:
        record = service.create('/workspace', 'Tools', mcp_servers=['kept', 'gone'])
        del config['mcp_servers']['gone']
        names = ['kept']
        copy = service.copy_with_tools(record.to_info(), 'Tools copy', ['kept'])
        assert copy.provider_options['mcp_servers'] == ['kept']
        assert [p for m, p in connection.calls if m in ('thread/fork', 'thread/start')][-1][
            'config']['mcp_servers'] == {'kept': {'enabled': True}}
        with pytest.raises(CodexTurnRejected) as refused:
            CodexOwnedThreads(connection).resume(record)
        assert refused.value.code == 'mcp_server_removed'
    finally:
        service.close()


# -- small things said (#467) ----------------------------------------------------------


def test_goal_lines_say_what_was_not_reported():
    from thechatplace.providers.codex_features import goal_lines
    assert goal_lines({'goal': {'objective': 'Ship it'}})[-1] == 'Time used: not reported.'
    assert goal_lines({'goal': {'objective': 'Ship it', 'timeUsedSeconds': None}})[-1] == 'Time used: not reported.'
    assert goal_lines({'goal': {'objective': 'Ship it', 'timeUsedSeconds': 0}})[-1] == 'Time used: 0 seconds.'
    assert goal_lines({'goal': None}) == ['No goal is set for this chat.']
    with pytest.raises(CodexFeatureError):
        goal_lines({'goal': {'objective': 3}})


def test_a_terminal_that_had_already_ended_is_not_called_stopped():
    from thechatplace.providers.codex_features import terminal_stopped_text
    assert terminal_stopped_text({'terminated': False}) == 'That background task had already finished.'
    assert terminal_stopped_text({'terminated': True}) == 'Background task stopped.'
    assert terminal_stopped_text({}) == 'Background task stopped.'


def test_revert_is_no_longer_a_native_control():
    from thechatplace.providers.codex_execution import CodexExecutionConnection
    with pytest.raises(CodexFeatureError):
        validate_action('thread/revert', {'threadId': 'owned', 'beforeTurnId': 'turn'})
    assert 'thread/revert' not in CodexExecutionConnection.METHODS


def test_a_compaction_is_known_as_one_until_the_next_real_turn():
    service, connection, _ = runtime()
    original = connection.request

    def request(method, params):
        if method == 'thread/compact/start':
            connection.events.append(CodexEvent('turn_started', params['threadId'], 'compact-turn'))
            return {}
        return original(method, params)
    connection.request = request
    try:
        record = service.create('/workspace', 'Compact')
        native = record.cli_session_id
        assert not service.snapshot(native).compaction
        service.feature(record, 'thread/compact/start')
        wait(lambda: service.snapshot(native).outcome == 'inProgress')
        assert service.snapshot(native).compaction
        connection.events.append(CodexEvent('turn_completed', native, 'compact-turn', status='completed'))
        wait(lambda: service.snapshot(native).outcome == 'completed')
        assert service.snapshot(native).compaction  # what the end is announced from
        service.send(record, 'Continue normally')
        assert not service.snapshot(native).compaction
    finally:
        service.close()


# -- Ask Aside's whole path (#467) ------------------------------------------------------


class AsideFake:
    """A connection that takes accept_aside's real checks and a read-only fork."""
    from thechatplace.providers.codex_aside import AsideConnection
    accept_aside = AsideConnection.accept_aside

    def __init__(self, answer=None, turns=(), later=()):
        import threading
        from collections import deque
        self._runtime_lock = threading.Lock()
        self._runtime_threads = {}
        self.calls, self.events = [], deque()
        self.closed = False
        self.answer = answer
        # The source's turns, newest first: one page, and a second if ``later``.
        self.turns, self.later = list(turns), list(later)

    def close(self):
        self.closed = True

    def owns_runtime(self, proof):
        return not self.closed and self._runtime_threads.get(proof.native_id) is proof

    def pop_event(self):
        return self.events.popleft() if self.events else None

    def request(self, method, params):
        self.calls.append((method, deepcopy(params)))
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
        if method == 'config/read':
            return LifecycleConnection().request(method, params)
        if method == 'mcpServerStatus/list':
            return {'data': [], 'nextCursor': None}
        if method == 'thread/turns/list':
            if params.get('cursor') == 'page-2':
                return {'data': self.later, 'nextCursor': None}
            return {'data': self.turns, 'nextCursor': 'page-2' if self.later else None}
        if method == 'thread/fork':
            result = response('aside-copy')
            result['thread']['ephemeral'] = True
            result['approvalPolicy'] = 'never'
            return result
        if method == 'turn/start':
            for event in self.answer or ():
                self.events.append(event)
            return {'turn': {'id': 'aside-turn', 'status': 'inProgress'}}
        raise AssertionError(method)


def _aside_run(connection, **options):
    import threading
    from thechatplace.providers.codex_aside import CodexAsideRunner
    events, ended = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ('done', 'failed'):
            ended.set()
    runner = CodexAsideRunner(_session('source', is_own=False), 'Which file matters?', on_event,
                              factory=lambda: connection, **options).start()
    assert ended.wait(5)
    return runner, events


def test_aside_forks_read_only_asks_and_reports_the_answer():
    connection = AsideFake(answer=[
        CodexEvent('text_completed', 'aside-copy', 'aside-turn', 'reply', 'The parser.'),
        CodexEvent('turn_completed', 'aside-copy', 'aside-turn', status='completed')])
    _runner, events = _aside_run(connection)
    assert events[-1].kind == 'done' and events[-1].text == 'The parser.'
    assert [e.kind for e in events].count('done') == 1
    fork = next(p for m, p in connection.calls if m == 'thread/fork')
    assert fork['ephemeral'] is True and fork['threadId'] == 'source'
    assert fork['approvalPolicy'] == 'never' and fork['sandbox'] == 'read-only'
    assert all(fork['config']['features'][name] is False for name in
               ('hooks', 'goals', 'memories', 'shell_tool', 'unified_exec', 'js_repl', 'multi_agent'))
    turn = next(p for m, p in connection.calls if m == 'turn/start')
    assert turn['threadId'] == 'aside-copy'
    assert turn['approvalPolicy'] == 'never' and turn['sandboxPolicy']['type'] == 'readOnly'
    assert turn['input'] == [{'type': 'text', 'text': 'Which file matters?'}]
    assert connection.closed  # its own process goes with it


_DONE = [CodexEvent('text_completed', 'aside-copy', 'aside-turn', 'reply', 'The parser.'),
         CodexEvent('turn_completed', 'aside-copy', 'aside-turn', status='completed')]


def _turn(turn_id, status):
    return {'id': turn_id, 'status': status, 'items': []}


@pytest.mark.parametrize('turns, cut', [
    # Newest first, as thread/turns/list gives them.
    ([_turn('turn-2', 'inProgress'), _turn('turn-1', 'completed')], 'turn-1'),
    # Always cut, even with nothing running: a turn that starts before the
    # fork is left out too (and the moment after sending, before it's listed).
    ([_turn('turn-1', 'completed')], 'turn-1'),
    # A chat started elsewhere, running, after one you stopped.
    ([_turn('turn-3', 'inProgress'), _turn('turn-2', 'interrupted'), _turn('turn-1', 'completed')],
     'turn-1'),
    # A turn that failed can leave your message unanswered.
    ([_turn('turn-2', 'failed'), _turn('turn-1', 'completed')], 'turn-1'),
    ([], None),
])
def test_an_aside_forks_up_to_the_newest_completed_turn(turns, cut):
    # #559: in Claude, a copy holding an unfinished turn had its message
    # answered in the aside too.
    connection = AsideFake(answer=list(_DONE), turns=turns)
    _runner, events = _aside_run(connection)
    assert events[-1].kind == 'done'
    fork = next(p for m, p in connection.calls if m == 'thread/fork')
    assert fork.get('lastTurnId') == cut and 'beforeTurnId' not in fork
    listed = next(p for m, p in connection.calls if m == 'thread/turns/list')
    assert listed['threadId'] == 'source' and listed['sortDirection'] == 'desc'


def test_an_aside_follows_the_turn_list_past_its_first_page():
    connection = AsideFake(answer=list(_DONE), turns=[_turn('turn-3', 'inProgress')],
                           later=[_turn('turn-2', 'completed')])
    _aside_run(connection)
    fork = next(p for m, p in connection.calls if m == 'thread/fork')
    assert fork['lastTurnId'] == 'turn-2'


@pytest.mark.parametrize('status', ['inProgress', 'failed', 'interrupted'])
def test_an_aside_says_when_codex_has_nothing_finished_yet(status):
    from thechatplace.aside import NOTHING_FINISHED
    connection = AsideFake(answer=list(_DONE), turns=[_turn('turn-1', status)])
    _runner, events = _aside_run(connection)
    assert events[-1].kind == 'failed' and events[-1].text == NOTHING_FINISHED
    assert not any(m == 'thread/fork' for m, _p in connection.calls)
    assert connection.closed


def test_aside_says_when_codex_did_not_finish_the_question():
    connection = AsideFake(answer=[CodexEvent('turn_completed', 'aside-copy', 'aside-turn', status='failed')])
    _runner, events = _aside_run(connection)
    assert events[-1].kind == 'failed' and 'did not complete' in events[-1].text
    assert connection.closed


def test_aside_says_when_it_could_not_ask():
    connection = AsideFake()
    connection.request = lambda method, params: (_ for _ in ()).throw(CodexRpcError('private detail'))
    _runner, events = _aside_run(connection)
    assert events[-1].kind == 'failed' and 'private' not in events[-1].text


@pytest.mark.parametrize('change', ['other-connection', 'persistent', 'writable', 'asks'])
def test_aside_refuses_anything_but_its_own_read_only_ephemeral_fork(change):
    from thechatplace.providers.codex_owned import CodexOwnedThread
    from thechatplace.providers.codex_policy import CodexPolicy
    connection = AsideFake()
    settings = {'connection': connection, 'ephemeral': True, 'policy': CodexPolicy('read-only', 'never')}
    if change == 'other-connection':
        settings['connection'] = AsideFake()
    elif change == 'persistent':
        settings['ephemeral'] = False
    elif change == 'writable':
        settings['policy'] = CodexPolicy('workspace-write', 'never', workspace_root='/workspace')
    else:
        settings['policy'] = CodexPolicy('read-only', 'on-request')
    proof = CodexOwnedThread('aside-copy', '/workspace', 'gpt-default', 'Ask Aside', 0, **settings)
    with pytest.raises(CodexRpcError, match='aside'):
        connection.accept_aside(proof)
    assert not connection._runtime_threads
    good = CodexOwnedThread('aside-copy', '/workspace', 'gpt-default', 'Ask Aside', 0, connection, True,
                            policy=CodexPolicy('read-only', 'never'))
    connection.accept_aside(good)
    assert connection.owns_runtime(good)


def test_an_ephemeral_fork_asks_codex_to_leave_the_turns_out():
    # Real Codex 0.163 refuses it otherwise, for any paginated thread, so Ask
    # Aside failed on every new chat ("requires `excludeTurns: true`").
    from thechatplace.sessions import OWN, SessionInfo
    result = response('aside')
    result['thread']['ephemeral'] = True
    result['approvalPolicy'] = 'never'
    connection = LifecycleConnection(result, config={})
    info = SessionInfo(OWN, 'codex:source', 'Source', '/workspace', 'source', provider='codex')
    CodexOwnedThreads(connection).fork(info, 'Ask Aside', ephemeral=True, sandbox='read-only',
                                       approval_policy='never', passive=True)
    params = [p for m, p in connection.calls if m == 'thread/fork'][-1]
    assert params['excludeTurns'] is True and params['ephemeral'] is True


def test_a_kept_fork_keeps_its_turns_in_the_reply():
    from thechatplace.sessions import OWN, SessionInfo
    connection = LifecycleConnection(response('copy'))
    info = SessionInfo(OWN, 'codex:source', 'Source', '/workspace', 'source', provider='codex')
    CodexOwnedThreads(connection).fork(info, 'Copy')
    assert 'excludeTurns' not in [p for m, p in connection.calls if m == 'thread/fork'][-1]

