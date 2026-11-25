"""Queue identity, child attribution and OAuth lifecycle, without windows."""
from dataclasses import replace
from types import SimpleNamespace
import threading

import pytest

from test_codex_runtime import runtime
from test_codex_parity import child_transport
from thechatplace.providers.codex_events import CodexEvent, CodexTurnBuffer
from thechatplace.providers.codex_execution import CodexTurnExecutor
from thechatplace.providers.codex_input import build_input, input_items
from thechatplace.providers.codex_tools import CodexToolLogins, CodexToolConnection
from thechatplace.providers.codex_rpc import CodexRpcError


def test_take_queue_keeps_native_parts_and_refuses_a_stale_occurrence(tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
    skill = tmp_path / 'SKILL.md'; skill.write_text('Synthetic skill')
    text = build_input('Look at this', [str(image)], skills=[('example', str(skill))])
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Queue')
        service.send(record, 'Working')
        service.send(record, text)
        service.send(record, text)
        first, second = service.snapshot(record.cli_session_id).queued
        assert first == second and first is not second
        before = len(connection.calls)
        taken = service.take_queued(record, 0, first)
        assert input_items(taken) == input_items(text)
        assert taken.draft_text == 'Look at this'
        assert taken.attachment_paths == (str(image.resolve()),)
        with pytest.raises(ValueError, match='changed'):
            service.take_queued(record, 0, first)
        assert service.snapshot(record.cli_session_id).queued == (second,)
        assert len(connection.calls) == before
        assert service.take_queued(record, 0, second) is second
        assert not service.snapshot(record.cli_session_id).queued
    finally:
        service.close()


@pytest.mark.parametrize('state', ['steering', 'stopping', 'reopening'])
def test_take_queue_waits_for_inflight_queue_operations(state):
    service, connection, _ = runtime()
    try:
        record = service.create('/workspace', 'Queue')
        service.send(record, 'Working'); service.send(record, 'Queued')
        selected = service.snapshot(record.cli_session_id).queued[0]
        if state == 'steering': service._steering[record.cli_session_id] = ('Other', None, 0)
        else: getattr(service, '_' + state).add(record.cli_session_id)
        with pytest.raises((ValueError, CodexRpcError)):
            service.take_queued(record, 0, selected)
        assert service.snapshot(record.cli_session_id).queued == (selected,)
    finally:
        service.close()


def drain_children(executor):
    import time
    deadline = time.monotonic() + 3
    while True:
        executor.drain()
        with executor._child_event_lock:
            done = not executor._child_event_pending and not executor._child_event_ready
        if done:
            return
        assert time.monotonic() < deadline, 'child display lookup did not finish'
        time.sleep(.001)


def child_executor():
    connection, proof = child_transport()
    executor = CodexTurnExecutor(connection)
    executor._turns['owned'] = CodexTurnBuffer('owned', 'parent-turn')
    for child in ('child', 'other-child'):
        connection._receive({'method':'turn/started', 'params':{'threadId':child,'turn':{'id':'turn','status':'inProgress'}}})
    drain_children(executor)
    return connection, proof, executor


def test_child_tools_edits_and_reviews_route_once_without_replies_or_ownership():
    c, proof, executor = child_executor()
    # Native thread ancestry is the authority, not an item's or tool's name.
    for method, item in [('item/started', {'type':'commandExecution', 'id':'tool', 'command':'echo synthetic'}),
                         ('item/completed', {'type':'commandExecution', 'id':'tool'}),
                         ('item/completed', {'type':'fileChange', 'id':'edit', 'status':'completed',
                          'changes':[{'path':'example.py', 'kind':{'type':'update'}, 'diff':'synthetic diff'}]})]:
        wire = {'method': method, 'params': {'threadId':'child', 'turnId':'turn', 'item':item}}
        c._receive(wire); c._receive(wire)
    c._receive({'method':'item/autoApprovalReview/completed', 'params':{
        'threadId':'child', 'turnId':'turn', 'reviewId':'same', 'review':{'status':'denied','rationale':'Synthetic reason'},
        'action':{'command':'echo synthetic'}}})
    c._receive({'method':'item/completed','params':{'threadId':'child','turnId':'turn',
        'item':{'type':'agentMessage','id':'reply','text':'A child reply'}}})
    drain_children(executor)
    state = executor.snapshot(proof)
    assert state.tools_started == state.edits_made == 1
    assert not state.replies and not state.changed_paths and not state.file_changes
    assert len(state.review_notices) == 1
    identity, text = state.review_notices[0]
    assert identity.startswith('auto-review:subagent:') and text.startswith('Codex subagent: ')
    from thechatplace.approvals import parse
    assert parse(identity,text).outcome == 'denied'
    assert list(c._runtime_threads) == ['owned'] and c.owns_runtime(proof)


@pytest.mark.parametrize('bad', ['foreign', 'cycle', 'wrong_id', 'unowned', 'slow_lookup'])
def test_child_activity_cannot_attach_to_unproven_or_later_parent_turn(bad):
    c, proof, executor = child_executor()
    def request(method, params):
        thread = {'id':params['threadId'], 'modelProvider':'openai','parentThreadId':'owned'}
        if bad == 'foreign': thread['modelProvider']='other'
        if bad == 'cycle': thread['parentThreadId']='child'
        if bad == 'wrong_id': thread['id']='someone-else'
        if bad == 'unowned': thread.pop('parentThreadId')
        if bad == 'slow_lookup': executor._turns['owned'] = CodexTurnBuffer('owned','later')
        return {'thread':thread}
    c.request=request
    c._receive({'method':'item/started','params':{'threadId':'child','turnId':'turn',
        'item':{'id':'tool','type':'commandExecution','command':'echo synthetic'}}})
    drain_children(executor)
    assert executor.snapshot(proof).tools_started == 0
    assert not c.closed


def test_distinct_child_review_ids_do_not_collide_with_each_other_or_parent():
    c, proof, executor = child_executor()
    for thread in ('child', 'other-child', 'owned'):
        c._receive({'method':'item/autoApprovalReview/completed','params':{
            'threadId':thread,'turnId':'parent-turn' if thread=='owned' else 'turn','reviewId':'same',
            'review':{'status':'approved'}}})
    drain_children(executor)
    state = executor.snapshot(proof)
    assert len(state.review_notices)==3
    assert len({i for i,t in state.review_notices})==3


class LoginConnection:
    def __init__(self):
        self.calls=[]; self.results={}; self.closed=False
    def request(self,method,params):
        self.calls.append((method,params))
        if method=='config/read': return {'config':{'mcp_servers':{'notes':{'url':'https://example.invalid/mcp'}}}}
        return {'authorizationUrl':'https://example.invalid/authorize','loginId':'login'}
    def login_result(self,name,login): return self.results.pop((name,login),None)


class Manager:
    def __init__(self): self.connection=LoginConnection();self.closes=0
    def _get(self): return self.connection
    def close(self): self.closes+=1;self.connection.closed=True


def test_standalone_signin_uses_folder_config_no_thread_and_closes_when_complete(tmp_path):
    manager=Manager();folders=[]
    service=CodexToolLogins(factory=lambda folder: folders.append(folder) or manager)
    try:
        response=service.start(str(tmp_path),'notes')
        assert folders==[str(tmp_path.resolve())]
        assert manager.connection.calls==[
            ('config/read',{'includeLayers':False}),
            ('mcpServer/oauth/login',{'name':'notes','timeoutSecs':180})]
        assert service.result(str(tmp_path),'notes',response['loginId']) is None
        with pytest.raises(CodexRpcError): service.result(str(tmp_path),'different','login')
        manager.connection.results[('notes','login')]=True
        assert service.result(str(tmp_path),'notes','login') is True
        assert manager.closes==1 and not service._attempts and not service._timers
    finally: service.close()


def test_signin_failure_expiry_and_shutdown_release_every_connection(tmp_path):
    managers=[]
    def factory(folder):
        manager=Manager();managers.append(manager);return manager
    service=CodexToolLogins(factory=factory)
    with pytest.raises(CodexRpcError): service.start(str(tmp_path),'missing')
    assert managers[0].closes==1 and not service._attempts
    service.start(str(tmp_path),'notes')
    service._expire(next(iter(service._attempts)))
    assert managers[1].closes==1 and not service._attempts
    service.start(str(tmp_path),'notes');service.close()
    assert managers[2].closes==1 and not service._timers
    with pytest.raises(CodexRpcError): service.start(str(tmp_path),'notes')


def test_oauth_results_require_matching_name_login_and_null_thread():
    c=CodexToolConnection.__new__(CodexToolConnection)
    c._login_lock=threading.Lock();c._login_results={}
    for params in ({'name':'notes','loginId':'login','threadId':'other','success':True},
                   {'name':'notes','loginId':'login','threadId':None,'success':'yes'}):
        c._receive({'method':'mcpServer/oauthLogin/completed','params':params})
    assert c.login_result('notes','login') is None
    c._receive({'method':'mcpServer/oauthLogin/completed','params':{
        'name':'notes','loginId':'login','threadId':None,'success':False}})
    assert c.login_result('notes','wrong') is None
    assert c.login_result('notes','login') is False


def test_queued_steer_refuses_equal_text_from_a_stale_row():
    from thechatplace.providers.codex_rpc import CodexSteerRefused
    service, connection, _ = runtime()
    try:
        record=service.create('/workspace','Queue')
        service.send(record,'Working');service.send(record,'Same');service.send(record,'Same')
        first,second=service.snapshot(record.cli_session_id).queued
        service.take_queued(record,0,first)
        before=len(connection.calls)
        with pytest.raises(CodexSteerRefused): service.steer(record,first,index=0)
        assert service.snapshot(record.cli_session_id).queued==(second,)
        assert service.snapshot(record.cli_session_id).queued[0] is second
        assert len(connection.calls)==before
    finally: service.close()


@pytest.mark.parametrize('new_turn', [False, True])
def test_first_child_review_after_parent_completion_stays_with_original_turn(new_turn):
    c,proof,executor=child_executor()
    c._receive({'method':'turn/completed','params':{'threadId':'owned',
        'turn':{'id':'parent-turn','status':'completed'}}})
    drain_children(executor)
    if new_turn:
        c._active_turns['owned']='later'
        executor._turns['owned']=CodexTurnBuffer('owned','later')
        # A duplicate start cannot rebind the old child turn to the later parent.
        c._receive({'method':'turn/started','params':{'threadId':'child','turn':{'id':'turn','status':'inProgress'}}})
    c._receive({'method':'item/autoApprovalReview/completed','params':{
        'threadId':'child','turnId':'turn','reviewId':'late','review':{'status':'denied'}}})
    drain_children(executor)
    assert len(executor.snapshot(proof).review_notices)==(0 if new_turn else 1)


def test_shutdown_waits_for_expiry_cleanup_and_retries_a_failed_close(tmp_path):
    entered, release, shutdown_done = threading.Event(), threading.Event(), threading.Event()
    class SlowManager(Manager):
        def close(self):
            self.closes+=1
            if self.closes==1:
                entered.set(); assert release.wait(5)
                raise CodexRpcError('Synthetic cleanup failure')
            self.connection.closed=True
    manager=SlowManager(); service=CodexToolLogins(factory=lambda folder: manager)
    service.start(str(tmp_path),'notes')
    expiring=threading.Thread(target=service._expire,args=(next(iter(service._attempts)),))
    expiring.start(); assert entered.wait(5)
    failures=[]
    def close():
        try: service.close()
        except Exception as e: failures.append(e)
        finally: shutdown_done.set()
    shutdown=threading.Thread(target=close);shutdown.start()
    assert not shutdown_done.wait(.05)
    release.set();expiring.join(5);shutdown.join(5)
    assert shutdown_done.is_set() and not failures
    assert manager.closes==2 and not service._cleanup


def test_native_mcp_call_consent_shows_arguments_and_never_keeps_a_grant():
    from thechatplace.providers.codex_requests import decode_request,ELICITATION
    params={'threadId':'owned','turnId':'turn','serverName':'notes','mode':'form',
        'message':'Allow this tool call?', 'requestedSchema':{'type':'object','properties':{}},
        '_meta':{'codex_approval_kind':'mcp_tool_call','persist':['session','always'],
                 'tool_params':{'path':'example.txt','replace':True}}}
    request=decode_request('approval',ELICITATION,params)
    assert 'example.txt' in request.elicitation().message
    assert '"replace": true' in request.elicitation().message
    assert 'covers this call only' in request.elicitation().message
    assert request.elicitation_answer('accept',{})=={'action':'accept','content':{}}
    params['_meta']['tool_params']['path']='changed.txt'
    assert 'changed.txt' not in request.elicitation().message
    params['_meta']['tool_params']={'huge':'x'*64000}
    bounded = decode_request('large', ELICITATION, params).elicitation().message
    assert len(bounded) < 4400
    assert 'more characters not shown' in bounded
    assert bounded.endswith('Your answer covers this call only.')
    # The native request and answer remain unchanged, including its actual args.
    assert len(params['_meta']['tool_params']['huge']) == 64000


def test_provider_signin_does_not_require_chat_ownership_or_enable_tools(tmp_path):
    from thechatplace.providers.codex import CodexProvider
    calls=[]
    provider=SimpleNamespace(provider_id='codex',_check=lambda session:None,
        _tool_logins=SimpleNamespace(start=lambda cwd,name:calls.append(('login',cwd,name)),
                                    result=lambda cwd,name,login:calls.append(('status',cwd,name,login))))
    info=SimpleNamespace(is_own=False,cwd=str(tmp_path),cli_session_id='external')
    CodexProvider.connected_tool_action(provider,info,'notes','login')
    CodexProvider.connected_tool_action(provider,info,'notes','login_status','login-id')
    assert calls==[('login',str(tmp_path),'notes'),('status',str(tmp_path),'notes','login-id')]


@pytest.mark.parametrize('status', ['completed', 'failed', 'interrupted', 'unknown'])
def test_late_child_activity_only_updates_same_successful_parent(status):
    c, proof, executor = child_executor()
    buffer = executor._turns['owned']
    buffer.status = status
    activity = buffer.activity
    wire = {'method': 'item/started', 'params': {
        'threadId': 'child', 'turnId': 'turn',
        'item': {'id': 'late-tool', 'type': 'commandExecution', 'command': 'echo synthetic'}}}
    c._receive(wire); c._receive(wire)
    drain_children(executor)
    assert executor.snapshot(proof).tools_started == (1 if status == 'completed' else 0)
    assert buffer.status == status and buffer.activity == activity
    executor._turns['owned'] = CodexTurnBuffer('owned', 'later-parent')
    c._receive(wire); drain_children(executor)
    assert executor.snapshot(proof).tools_started == 0


def test_native_probe_restores_both_storage_overrides(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    import os
    spec = importlib.util.spec_from_file_location('workflow_probe', Path(__file__).parents[1] / 'tools/probe_codex_workflows.py')
    probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
    monkeypatch.setenv('CODEX_HOME', 'original-home')
    monkeypatch.delenv('CODEX_SQLITE_HOME', raising=False)
    with pytest.raises(RuntimeError):
        with probe.isolated_storage(tmp_path):
            assert os.environ['CODEX_HOME'] == os.environ['CODEX_SQLITE_HOME'] == str(tmp_path)
            raise RuntimeError('synthetic failure')
    assert os.environ['CODEX_HOME'] == 'original-home'
    assert 'CODEX_SQLITE_HOME' not in os.environ


def test_slow_child_lookup_does_not_block_parent_updates_or_repeat_requests():
    c, proof, executor = child_executor()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def ancestry(child):
        calls.append(child); entered.set()
        assert release.wait(3)
        return None
    c.child_ancestor = ancestry
    wire = {'method': 'item/started', 'params': {'threadId': 'child', 'turnId': 'turn',
        'item': {'id': 'tool', 'type': 'commandExecution', 'command': 'echo synthetic'}}}
    try:
        c._receive(wire)
        executor.drain()
        assert entered.wait(1)
        for _ in range(80): c._receive(wire)
        c._receive({'method': 'item/completed', 'params': {'threadId': 'owned', 'turnId': 'parent-turn',
            'item': {'id': 'reply', 'type': 'agentMessage', 'text': 'Parent keeps working'}}})
        executor.drain()
        assert executor.snapshot(proof).replies
        assert calls == ['child']
        with executor._child_event_lock:
            assert len(executor._child_event_pending[('child', 'turn')]) <= 32
        release.set(); drain_children(executor)
        for _ in range(5):
            c._receive(wire); executor.drain()
        assert calls == ['child']
        assert executor._child_event_parents[('child', 'turn')] is None
    finally:
        release.set()


def test_child_display_workers_and_negative_cache_are_bounded():
    c, proof, executor = child_executor()
    release = threading.Event()
    active = []
    def ancestry(child):
        active.append(child)
        assert release.wait(3)
        return None
    c.child_ancestor = ancestry
    try:
        for index in range(150):
            executor._route_child_event(CodexEvent('activity', 'child-' + str(index), 'turn',
                item_id='tool', tool=True, parent_turns=(('owned', 'parent-turn'),)))
        assert len(active) <= 4
        assert len(executor._child_event_pending) <= 4
        assert len(executor._child_event_parents) <= 128
    finally:
        release.set(); drain_children(executor)
    assert len(executor._child_event_parents) <= 128


@pytest.mark.parametrize('login_id', [None, '', 3])
def test_login_result_rejects_starting_or_invalid_identity(tmp_path, login_id):
    service = CodexToolLogins()
    # Same shape as a reserved attempt while connection startup is pending.
    service._attempts['starting'] = (None, str(tmp_path.resolve()), 'notes', None, 0, None)
    with pytest.raises(CodexRpcError, match='completed sign-in start'):
        service.result(str(tmp_path), 'notes', login_id)


def test_api_provider_does_not_create_tool_login_owner(tmp_path):
    from thechatplace.providers.codex_api import CodexApiProvider
    provider = CodexApiProvider()
    try:
        assert provider._tool_logins is None
    finally:
        provider.close()


def test_child_skipped_during_worker_saturation_can_route_a_later_event():
    c, proof, executor = child_executor()
    release = threading.Event()
    entered = threading.Barrier(5)
    def ancestry(child):
        entered.wait(timeout=3)
        assert release.wait(3)
        return proof
    c.child_ancestor = ancestry
    def event(child):
        return CodexEvent('activity', child, 'turn', item_id='tool', tool=True,
                          parent_turns=(('owned', 'parent-turn'),))
    try:
        for index in range(4): executor._route_child_event(event('child-' + str(index)))
        entered.wait(timeout=3)
        executor._route_child_event(event('fifth'))
        assert ('fifth', 'turn') not in executor._child_event_parents
        release.set(); drain_children(executor)
        c.child_ancestor = lambda child: proof
        executor._route_child_event(event('fifth')); drain_children(executor)
        assert executor.snapshot(proof).tools_started == 5
    finally:
        release.set()


def test_probe_rejects_external_and_header_injection_redirects():
    import importlib.util
    import json
    from pathlib import Path
    import urllib.request, urllib.parse, urllib.error
    from http.server import ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location('workflow_redirect_probe', Path(__file__).parents[1] / 'tools/probe_codex_workflows.py')
    probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
    server = ThreadingHTTPServer(('127.0.0.1', 0), probe.Model)
    worker = threading.Thread(target=server.serve_forever); worker.start()
    try:
        for redirect in ('https://example.com/callback', 'http://127.0.0.1/callback\r\nInjected: yes'):
            query = urllib.parse.urlencode({'redirect_uri': redirect, 'state': 'probe'})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/authorize?{query}', timeout=3)
            assert error.value.code == 400
            assert error.value.headers.get('Location') is None
    finally:
        server.shutdown(); server.server_close(); worker.join(3)
