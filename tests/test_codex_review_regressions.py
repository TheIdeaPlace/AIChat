"""Reproduced #255 lifecycle failures, using synthetic chats only."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from test_codex_rpc import request_transport, approval_message
from test_codex_runtime import runtime, wait
from test_codex_owned import response
from thechatplace.providers.codex_events import CodexEvent, CodexTurnBuffer, decode_event
from thechatplace.providers.codex_rpc import CodexRpcError


def test_reply_burst_is_coalesced_and_final_text_precedes_completion():
    c = request_transport()
    c._receive({'method': 'turn/started', 'params': {'threadId': 'owned',
        'turn': {'id': 'turn', 'status': 'inProgress'}}})
    for _ in range(256):
        c._receive({'method': 'item/agentMessage/delta', 'params': {
            'threadId': 'owned', 'turnId': 'turn', 'itemId': 'reply', 'delta': 'x'}})
    assert c._events.qsize() == 1 and len(c._display_events) == 1
    assert next(iter(c._display_events.values())).text == 'x' * 256
    c._receive(approval_message())
    assert c.pop_event().kind == 'turn_started'
    assert c.pop_event().request_id == 'approval'
    c._receive({'method': 'item/completed', 'params': {'threadId': 'owned', 'turnId': 'turn',
        'item': {'id': 'reply', 'type': 'agentMessage', 'text': 'Final'}}})
    c._receive({'method': 'turn/completed', 'params': {'threadId': 'owned',
        'turn': {'id': 'turn', 'status': 'completed'}}})
    buffer = CodexTurnBuffer('owned', 'turn')
    while (event := c.pop_event()) is not None:
        buffer.apply(event)
    assert buffer.replies == (('reply', 'Final'),) and buffer.status == 'completed'
    assert not c.closed


def test_many_large_display_items_truncate_without_losing_completion():
    c = request_transport()
    for i in range(200):
        c._receive({'method': 'item/completed', 'params': {'threadId': 'owned', 'turnId': 'turn',
            'item': {'type': 'agentMessage', 'id': str(i), 'text': 'x' * 10000}}})
    c._receive({'method': 'turn/completed', 'params': {'threadId': 'owned',
        'turn': {'id': 'turn', 'status': 'completed'}}})
    buffer = CodexTurnBuffer('owned', 'turn')
    while (event := c.pop_event()) is not None:
        buffer.apply(event)
    assert buffer.status == 'completed' and buffer.truncated and not c.closed
    assert buffer._characters <= buffer.MAX_TEXT
    assert 'truncated' in buffer.replies[-1][1]


def test_large_diff_and_broad_file_change_remain_displayable():
    event = decode_event('item/completed', {'threadId': 'chat', 'turnId': 'turn', 'item': {
        'id': 'files', 'type': 'fileChange', 'changes': [
            {'path': f'file-{i}', 'kind': {'type': 'update'}, 'diff': 'x' * 300000}
            for i in range(129)]}})
    assert len(event.text) <= 256 * 1024 and 'truncated' in event.text
    buffer = CodexTurnBuffer('chat', 'turn')
    buffer.apply(event)
    buffer.apply(CodexEvent('turn_completed', 'chat', 'turn', status='completed'))
    assert buffer.status == 'completed'


def test_second_stop_reconciles_missing_completion_without_resume_or_replay():
    service, c, updates = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.send(record, 'Queued')
        service.stop(record)  # fake server acknowledges but emits no completion
        assert record.cli_session_id in service._stopping
        original = c.request
        def read(method, params):
            if method == 'thread/read':
                return response(record.cli_session_id)
            if method == 'thread/turns/list':
                return {'data': [{'id': 'turn-1', 'status': 'interrupted'}]}
            return original(method, params)
        c.request = read
        service.stop(record)
        assert service.snapshot(record.cli_session_id).outcome == 'interrupted'
        assert record.cli_session_id not in service._stopping
        assert [t for state in updates for t in state.unsent] == ['Queued']
        service.send(record, 'New deliberate message')
        assert c.turns == 2
        assert not any(m == 'thread/resume' for m, _ in c.calls)
    finally:
        service.close()


@pytest.mark.parametrize('latest,status', [('turn-1', 'inProgress'), ('other', 'interrupted')])
def test_stop_reconciliation_never_clears_unfinished_or_different_turn(latest, status):
    service, c, _ = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.stop(record)
        original = c.request
        c.request = lambda method, params: ({'data': [{'id': latest, 'status': status}]}
            if method == 'thread/turns/list' else original(method, params))
        with pytest.raises(CodexRpcError, match='not confirmed'):
            service.stop(record)
        assert service.snapshot(record.cli_session_id).outcome == 'inProgress'
        assert record.cli_session_id in service._stopping
    finally:
        service.close()


@pytest.mark.parametrize('outcome', ['interrupted', 'failed'])
def test_queue_after_terminal_during_next_preflight_is_not_returned_unsent(outcome):
    service, c, updates = runtime()
    release = threading.Event()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status=outcome))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == outcome)
        c.entered, c.release = threading.Event(), release
        with ThreadPoolExecutor(1) as pool:
            send = pool.submit(service.send, record, 'A')
            assert c.entered.wait(3)
            assert service.send(record, 'B') is True
            assert service.snapshot(record.cli_session_id).queued == ('B',)
            assert not any('B' in state.unsent for state in updates)
            release.set()
            assert send.result(timeout=3) is False
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-2', status='completed'))
        wait(lambda: c.turns == 3)
        assert not any('B' in state.unsent for state in updates)
    finally:
        release.set()
        service.close()


def test_stale_terminal_notifier_cannot_flush_a_later_turn_queue():
    service, c, updates = runtime()
    paused, release = threading.Event(), threading.Event()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        proof, executor = service._context(record)
        c.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='interrupted'))
        executor.drain()  # do not let a listener's earlier notification establish the flush marker
        service._flushed_turns.clear()
        original = service.snapshot
        def snapshot(native_id, **kwargs):
            state = original(native_id, **kwargs)
            if threading.current_thread().name == 'stale-notifier' and not paused.is_set():
                paused.set()
                assert release.wait(5)
            return state
        service.snapshot = snapshot
        worker = threading.Thread(target=service._notify, args=(record.cli_session_id,), name='stale-notifier')
        worker.start()
        assert paused.wait(3)
        service.send(record, 'A')
        assert service.send(record, 'B') is True
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert service.snapshot(record.cli_session_id).queued == ('B',)
        assert not any('B' in state.unsent for state in updates)
    finally:
        release.set()
        service.close()


def test_reconciled_stop_ignores_late_old_approval_without_breaking_next_turn():
    from thechatplace.providers.codex_requests import CodexRequest, COMMAND
    service, c, _ = runtime()
    try:
        record = service.create('/workspace', 'Owned')
        service.send(record, 'First')
        service.stop(record)
        original = c.request
        c.request = lambda method, params: ({'data': [{'id': 'turn-1', 'status': 'interrupted'}]}
            if method == 'thread/turns/list' else original(method, params))
        service.stop(record)
        service.send(record, 'Second')
        c.events.append(CodexRequest('late', COMMAND, record.cli_session_id, 'turn-1', 'cmd'))
        _, executor = service._context(record)
        executor.drain()
        assert not c.closed and service.snapshot(record.cli_session_id).outcome == 'inProgress'
        assert not service.snapshot(record.cli_session_id).requests
    finally:
        service.close()


def test_late_approval_for_confirmed_ended_turn_is_declined_without_new_pending_request():
    import json
    c = request_transport()
    c.forget_turn_requests('owned', 'turn')
    c._receive(approval_message())
    reply = json.loads(c._outgoing.get_nowait())
    assert reply['id'] == 'approval' and 'error' in reply and 'result' not in reply
    assert not c._server_pending and not c.closed
