import pytest

from thechatplace.providers.codex_events import CodexEvent, CodexTokenUsageEvent, decode_event
from thechatplace.providers.codex_execution import CodexTurnExecutor
from thechatplace.providers.codex_tokens import CodexTokenUsage, context_lines, decode_tokens
from test_codex_execution import Connection


def report(latest=800, cumulative=10000, window=1000):
    def breakdown(total):
        return dict(inputTokens=total, outputTokens=0, cachedInputTokens=0,
                    reasoningOutputTokens=0, totalTokens=total)
    return dict(last=breakdown(latest), total=breakdown(cumulative), modelContextWindow=window)


def test_latest_request_is_distinct_from_cumulative_usage_and_window_is_reported_only():
    tokens = decode_tokens(report())
    assert tokens.share == .8
    lines = context_lines(tokens)
    assert any('80%' in line for line in lines)
    assert any('10,000' in line and 'not the current context size' in line for line in lines)
    assert 'not reported' in context_lines(None)[0]
    assert any('0%' in line for line in context_lines(decode_tokens(report(latest=0))))
    assert decode_tokens(report(window=None)).share is None


@pytest.mark.parametrize('bad', [None, -1, True, 1.5, '800', 2**64])
def test_bad_optional_usage_does_not_become_zero_or_break_stream(bad):
    payload = report()
    payload['last']['totalTokens'] = bad
    assert decode_tokens(payload) is None
    assert decode_event('thread/tokenUsage/updated', {'threadId': 'one', 'turnId': 'turn', 'tokenUsage': payload}) is None


def test_context_updates_stay_with_their_owned_turn_and_compaction_clears_stale_counts():
    connection = Connection()
    one, two = connection.proof('one'), connection.proof('two')
    executor = CodexTurnExecutor(connection)
    a, b = executor.start(one, 'First'), executor.start(two, 'Second')
    tokens = decode_tokens(report())
    connection.events.extend([CodexTokenUsageEvent('one', a.turn_id, tokens),
                              CodexTokenUsageEvent('two', 'wrong-turn', tokens)])
    executor.drain()
    assert executor.snapshot(one).token_usage == tokens
    assert executor.snapshot(two).token_usage is None
    compacted = CodexEvent('context_compacted', 'one', a.turn_id, 'compact')
    connection.events.extend([compacted, compacted])
    executor.drain()
    assert executor.snapshot(one).token_usage is None
    assert executor.snapshot(one).context_epoch == 1
    connection.events.extend([CodexTokenUsageEvent('one', a.turn_id, decode_tokens(report(latest=100))),
                              CodexEvent('turn_completed', 'one', a.turn_id, status='completed')])
    executor.drain()
    assert executor.snapshot(one).token_usage.latest_total == 100
    executor.start(one, 'Next')
    connection.events.append(CodexTokenUsageEvent('one', a.turn_id, tokens))
    executor.drain()
    assert executor.snapshot(one).token_usage.latest_total == 100


def test_token_bursts_coalesce_without_crowding_out_approvals_and_compaction_discards_old_snapshot():
    from test_codex_rpc import request_transport, approval_message
    connection = request_transport()
    for total in range(256):
        connection._receive({'method': 'thread/tokenUsage/updated', 'params': {
            'threadId': 'owned', 'turnId': 'turn', 'tokenUsage': report(latest=total)}})
    assert connection._events.empty() and len(connection._token_events) == 1
    connection._receive(approval_message())
    assert connection.pop_event().request_id == 'approval'
    assert connection.pop_event().usage.latest_total == 255
    connection._receive({'method': 'thread/tokenUsage/updated', 'params': {
        'threadId': 'owned', 'turnId': 'turn', 'tokenUsage': report()}})
    connection._receive({'method': 'item/completed', 'params': {'threadId': 'owned', 'turnId': 'turn',
                         'item': {'id': 'compacted', 'type': 'contextCompaction'}}})
    assert connection.pop_event().kind == 'context_compacted'
    assert connection.pop_event() is None and not connection.closed
