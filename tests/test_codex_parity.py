"""Regression boundaries for the Codex additions to the 0.5 preview."""
from dataclasses import replace
import threading
import pytest
from thechatplace.providers.codex_events import CodexEvent, CodexTurnBuffer, decode_event
from thechatplace.providers.codex_requests import decode_request, ELICITATION
from thechatplace.providers.codex_execution import CodexExecutionConnection, CodexTurnExecutor
from test_codex_execution import Connection


def form(**changes):
    return decode_request(1, ELICITATION, dict(threadId='one', turnId='turn-1', serverName='test',
        mode='form', message='How many?', requestedSchema={'type': 'object',
        'properties': {'count': {'type': 'integer', 'minimum': 0}}, 'required': ['count']}, **changes))


def test_form_zero_is_valid_and_content_cannot_change_the_reviewed_schema():
    request = form()
    assert request.validate_answer({'action': 'accept', 'content': {'count': 0}})['content'] == {'count': 0}
    for content in ({}, {'count': -1}, {'count': 1, 'extra': 'unreviewed'}):
        with pytest.raises(ValueError):
            request.elicitation_answer('accept', content)
    schema = request.elicitation().schema
    schema['properties'].clear()
    assert request.elicitation().fields()
    assert request.elicitation_answer('decline') == {'action': 'decline', 'content': None}


def test_form_url_rejects_local_and_script_addresses():
    for url in ('file:///private', 'javascript:alert(1)'):
        request = decode_request(2, ELICITATION, dict(threadId='one', turnId='turn-1',
            serverName='test', mode='url', message='Open', url=url, elicitationId='flow'))
        with pytest.raises(ValueError):
            request.elicitation_answer('accept')
        assert request.elicitation_answer('decline')['action'] == 'decline'


def test_missing_turn_binds_once_and_cannot_follow_a_new_turn():
    c = CodexExecutionConnection.__new__(CodexExecutionConnection)
    c._runtime_lock = threading.Lock()
    c._active_turns = {'one': 'turn-1'}
    request = replace(form(), turn_id='')
    bound = c._bind_server_request(request)
    assert bound.turn_id == 'turn-1'
    c._active_turns['one'] = 'turn-2'
    assert c._bind_server_request(bound).turn_id == 'turn-1'
    c._active_turns.clear()
    with pytest.raises(ValueError):
        c._bind_server_request(request)


def test_form_is_no_longer_answerable_after_its_turn_ends():
    c = Connection(); proof = c.proof('one'); executor = CodexTurnExecutor(c)
    turn = executor.start(proof, 'hello')
    request = form()
    c.events.append(request); executor.drain()
    assert executor.snapshot(proof).requests == (request,)
    c.events.append(CodexEvent('turn_completed', 'one', turn.turn_id, status='interrupted'))
    executor.drain()
    assert not executor.snapshot(proof).requests
    with pytest.raises(Exception, match='no longer waiting'):
        executor.answer(proof, request, request.elicitation_answer('accept', {'count': 1}))


def test_retry_deduplicates_and_clears_when_progress_resumes():
    event = decode_event('error', dict(threadId='one', turnId='turn', willRetry=True,
        error={'message': 'private credential', 'codexErrorInfo': 'serverOverloaded'}))
    assert 'private' not in repr(event)
    buffer = CodexTurnBuffer('one', 'turn')
    assert buffer.apply(event) and not buffer.apply(event)
    assert buffer.retry and buffer.status == 'inProgress'
    assert not buffer.apply(replace(event, thread_id='other'))
    buffer.apply(CodexEvent('text_delta', 'one', 'turn', 'reply', 'Recovered'))
    assert not buffer.retry and buffer.activity == 'Writing a reply.'
    buffer.apply(event)
    buffer.apply(CodexEvent('turn_completed', 'one', 'turn', status='failed'))
    assert buffer.status == 'failed' and not buffer.retry


def test_reasoning_sections_order_and_final_reconciliation_never_become_replies():
    buffer = CodexTurnBuffer('one', 'turn')
    for index, text in [(1, 'Second'), (0, 'First'), (0, ' part')]:
        buffer.apply(decode_event('item/reasoning/summaryTextDelta', dict(threadId='one',
            turnId='turn', itemId='thought', summaryIndex=index, delta=text)))
    assert buffer.reasoning == (('thought', 'First part\n\nSecond'),)
    buffer.apply(decode_event('item/completed', dict(threadId='one', turnId='turn',
        item={'type': 'reasoning', 'id': 'thought', 'summary': ['Final summary']})))
    buffer.apply(CodexEvent('reasoning_delta', 'one', 'turn', 'thought', 'Late'))
    assert buffer.reasoning == (('thought', 'Final summary'),)
    assert not buffer.replies
    assert sum(len(t) for _, t in buffer.reasoning) <= buffer.MAX_TEXT


def test_retry_queue_keeps_progress_on_the_correct_side_of_error():
    from test_codex_rpc import request_transport
    c = request_transport()
    c._receive({'method': 'item/agentMessage/delta', 'params': dict(
        threadId='owned', turnId='turn', itemId='reply', delta='before')})
    c._receive({'method': 'error', 'params': dict(threadId='owned', turnId='turn',
        willRetry=True, error={'message': 'redacted'})})
    buffer = CodexTurnBuffer('owned', 'turn')
    while (event := c.pop_event()) is not None:
        buffer.apply(event)
    assert buffer.retry
    c._receive({'method': 'item/agentMessage/delta', 'params': dict(
        threadId='owned', turnId='turn', itemId='reply', delta='after')})
    buffer.apply(c.pop_event())
    assert not buffer.retry


def test_form_rejects_boolean_disguised_as_number():
    with pytest.raises(ValueError):
        form().elicitation_answer('accept', {'count': True})


def test_app_allowlist_overrides_existing_enables_and_rejects_broader_runtime():
    from thechatplace.providers.codex_tools import app_overrides, verify_apps
    from thechatplace.providers.codex_rpc import CodexRpcError
    from types import SimpleNamespace
    config = {'apps': {'_default': {'enabled': True}, 'other': {'enabled': True}}}
    overrides = app_overrides(config, ['chosen'])
    assert overrides == {'other': {'enabled': False}, '_default': {'enabled': False}, 'chosen': {'enabled': True}}
    assert config['apps']['other']['enabled']
    rows = [{'id': 'chosen', 'enabled': True, 'callable': True}]
    c = SimpleNamespace(request=lambda method, params: {'apps': rows})
    verify_apps(c, 'owned', ['chosen'])
    rows.append({'id': 'other', 'enabled': True, 'callable': False})
    with pytest.raises(CodexRpcError):
        verify_apps(c, 'owned', ['chosen'])
    with pytest.raises(ValueError):
        app_overrides({}, ['_default'])


def test_app_inventory_refreshes_and_never_passes_foreign_thread_identity():
    from thechatplace.providers.codex_tools import app_inventory
    from types import SimpleNamespace
    calls = []
    def request(method, params):
        calls.append((method, params))
        return {'data': [{'id': 'docs', 'name': 'Documents', 'isAccessible': True}]}
    rows = app_inventory(SimpleNamespace(request=request), SimpleNamespace(cli_session_id='foreign'), ['docs'])
    assert rows[0].label == 'Documents' and not rows[0].off_for_chat
    assert calls == [('app/list', {'limit': 100, 'forceRefetch': True})]


def test_options_save_barrier_preserves_old_instructions_on_failed_save():
    from test_codex_runtime import runtime
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Options', instructions='Old')
        change = service.prepare_chat_options(record, 'New', [])
        with pytest.raises(Exception):
            service.send(record, 'Blocked until saved')
        service.finish_policy(change, False)
        assert service._proofs[record.cli_session_id].instructions == 'Old'
    finally:
        service.close()


def test_options_apply_after_save_and_each_turn_pins_the_instruction_context():
    from test_codex_runtime import runtime
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Options', instructions='Old')
        change = service.prepare_chat_options(record, 'New', [])
        saved = change.record()
        service.finish_policy(change, True)
        service.send(saved, 'Hello')
        params = next(p for method, p in reversed(connection.calls) if method == 'turn/start')
        assert params['additionalContext'] == {'tcp_chat_instructions': {'kind': 'application', 'value': 'New'}}
    finally:
        service.close()


def test_removed_additional_folder_can_be_removed_from_chat_options(tmp_path):
    from test_codex_runtime import runtime
    service, _, _ = runtime()
    extra = tmp_path / 'extra'; extra.mkdir()
    try:
        record = service.create('/workspace', 'Options', extra_roots=[str(extra)])
        extra.rmdir()
        change = service.prepare_chat_options(record, '', [])
        assert not change.proof.policy.extra_roots
        service.finish_policy(change, True)
    finally:
        service.close()


def test_unchanged_extra_folder_that_retargets_is_not_a_new_grant(tmp_path, monkeypatch):
    from test_codex_runtime import runtime
    from thechatplace import platform_paths
    service, _, _ = runtime()
    extra = tmp_path / 'extra'; extra.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    try:
        record = service.create('/workspace', 'Options', extra_roots=[str(extra)])
        original = platform_paths.codex_workspace_root
        monkeypatch.setattr(platform_paths, 'codex_workspace_root',
            lambda p: str(other) if p == str(extra) else original(p))
        with pytest.raises(Exception, match='moved'):
            service.prepare_chat_options(record, '', [str(extra)])
    finally:
        service.close()


def test_more_folders_are_explicit_write_grants_only_in_folder_edits_mode(tmp_path):
    from thechatplace.providers.codex_policy import CodexPolicy
    extra = tmp_path / 'extra'; extra.mkdir()
    read = CodexPolicy.choose(str(tmp_path), extra_roots=[str(extra)])
    assert read.turn_policy() == {'type': 'readOnly'}
    write = CodexPolicy.choose(str(tmp_path), 'workspace-write', extra_roots=read.extra_roots)
    assert write.turn_policy()['writableRoots'] == [str(tmp_path), str(extra)]
    assert write.config()['sandbox_workspace_write']['network_access'] is False


def test_builtin_session_tools_caller_is_private_preserved_on_loaded_resume_and_absent_in_aside():
    from test_codex_owned import Connection, response
    from thechatplace.providers.codex_owned import CodexOwnedThreads
    c = Connection(); c.session_tools = True; c.loaded_caller = lambda native: 'codex:retained'
    lifecycle = CodexOwnedThreads(c)
    proof = lifecycle.start('/workspace', 'Session tools')
    params = next(p for method, p in c.calls if method == 'thread/start')
    server = params['config']['mcp_servers']['ccd_session_mgmt']
    assert proof.session_tool_caller in server['args']
    assert 'session_tool_caller' not in proof.record().provider_options
    c.result = response(proof.native_id)
    resumed = lifecycle.resume(proof.record())
    assert resumed.session_tool_caller == 'codex:retained'
    c.result['thread']['ephemeral'] = True
    aside = lifecycle.start('/workspace', 'Check', ephemeral=True)
    assert not aside.session_tool_caller and not aside.allowed_tools
    assert not c.config  # no global config mutation


def child_transport():
    from test_codex_rpc import request_transport
    from thechatplace.providers.codex_owned import CodexOwnedThread
    c = request_transport()
    c.__class__ = CodexExecutionConnection
    del c._accept_server_request
    c._runtime_lock = threading.Lock()
    c._child_routing_lock = threading.RLock()
    c._child_pending = set(); c._child_files = {}; c._oauth_results = {}
    c._child_workers = threading.BoundedSemaphore(8)
    proof = CodexOwnedThread('owned', '/workspace', 'model', 'Parent', 0, c)
    c._runtime_threads = {'owned': proof}
    c._active_turns = {'owned': 'parent-turn', 'child': 'turn'}
    def request(method, params):
        if method == 'thread/read':
            return {'thread': {'id': params['threadId'], 'parentThreadId': 'owned', 'modelProvider': 'openai'}}
        return {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
    c.request = request
    return c, proof


def test_child_approval_routes_without_granting_child_execution_ownership():
    from test_codex_rpc import approval_message
    from test_codex_runtime import wait
    from thechatplace.providers.codex_permissions import candidate
    c, proof = child_transport()
    c._receive(approval_message(thread='child'))
    wait(lambda: not c._child_pending)
    request = c.pop_event()
    assert request.thread_id == 'owned' and request.turn_id == 'parent-turn'
    assert request.source_thread_id == 'child' and request.source_turn_id == 'turn'
    assert 'subagent' in request.summary().lower()
    assert candidate(request, '/workspace') is None
    assert 'child' not in c._runtime_threads
    c.respond_request(request, request.approval_answer('decline'))
    assert not c.closed and c.owns_runtime(proof)


@pytest.mark.parametrize('race', ['new_parent_turn', 'resolved', 'child_ended'])
def test_child_lookup_cannot_resurrect_a_request_after_its_lifetime(race):
    from test_codex_rpc import approval_message
    from test_codex_runtime import wait
    c, _ = child_transport()
    entered, release = threading.Event(), threading.Event()
    original = c.request
    def request(method, params):
        if method == 'thread/read':
            entered.set(); assert release.wait(5)
        return original(method, params)
    c.request = request
    c._receive(approval_message(thread='child'))
    assert entered.wait(5)
    if race == 'new_parent_turn':
        c._active_turns['owned'] = 'next-turn'
    elif race == 'resolved':
        c._receive({'method': 'serverRequest/resolved', 'params': {'threadId': 'child', 'requestId': 'approval'}})
    else:
        c._receive({'method': 'turn/completed', 'params': {'threadId': 'child', 'turn': {'id': 'turn', 'status': 'completed'}}})
    release.set(); wait(lambda: c._child_workers.acquire(blocking=False))
    # Wait for all worker slots, avoiding a transient empty pending set after resolution.
    wait(lambda: not c._child_pending)
    assert not c._server_pending and not c.closed


def test_child_file_diff_is_qualified_by_native_thread_turn_and_item():
    from test_codex_provider import file_change
    from test_codex_runtime import wait
    c, _ = child_transport()
    c._receive({'method': 'item/started', 'params': dict(threadId='child', turnId='turn', item=file_change())})
    c._receive({'method': 'item/started', 'params': dict(threadId='owned', turnId='parent-turn',
        item=file_change(changes=[{'path': 'wrong.py', 'kind': {'type': 'update'}, 'diff': 'wrong diff'}]))})
    c._receive({'id': 'files', 'method': 'item/fileChange/requestApproval', 'params': dict(
        threadId='child', turnId='turn', itemId='files', reason='Review')})
    wait(lambda: not c._child_pending)
    request = c._server_pending['files']
    assert 'src/app.py' in request.file_detail and 'wrong.py' not in request.file_detail


def test_child_unknown_parent_is_declined_without_closing_other_chats():
    from test_codex_rpc import approval_message
    from test_codex_runtime import wait
    c, proof = child_transport()
    c.request = lambda method, params: {'thread': {'id': params['threadId'], 'modelProvider': 'openai'}}
    c._receive(approval_message(thread='unowned'))
    wait(lambda: not c._child_pending)
    assert not c._server_pending and not c.closed and c.owns_runtime(proof)
    assert not c._outgoing.empty()
