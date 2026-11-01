"""Codex's native conversation controls behind TCP's shared workflows (#467).

Every operation is scoped to a verified runtime. No raw rollout, shell
fallback, or undocumented hosted-service endpoint is used.
"""
from .codex_rpc import CodexRpcError


class CodexFeatureError(CodexRpcError):
    REASONS = {
        'unsupported': 'This operation is not available in this Codex version. Update Codex and try again.',
        'busy': 'Wait for this Codex chat to finish, or stop its turn, before doing that.',
        'invalid': 'The selected Codex operation or its settings are not valid.',
        'history': 'The selected message no longer identifies a complete Codex turn. Reload the chat and choose it again.',
        'unavailable': 'This Codex chat is not ready. Use Stop to recheck its connection first.',
        'failed': 'Codex could not confirm this operation. Check the chat before trying again.',
        'settings_pending': 'Send a normal message with this chat\'s changed settings before compacting its context.',
    }

    def __init__(self, code):
        self.code = code if code in self.REASONS else 'failed'
        super().__init__(self.REASONS[self.code])


READ_METHODS = frozenset({'thread/goal/get', 'thread/backgroundTerminals/list'})
ACTION_METHODS = frozenset({'thread/compact/start', 'thread/revert', 'thread/goal/set',
    'thread/goal/clear', 'thread/memoryMode/set', 'thread/backgroundTerminals/terminate'})


def validate_action(method, params):
    from ..platform_paths import is_safe_id
    if method not in READ_METHODS | ACTION_METHODS or not isinstance(params, dict):
        raise CodexFeatureError('invalid')
    if not is_safe_id(params.get('threadId')):
        raise CodexFeatureError('invalid')
    keys = {'threadId'}
    if method == 'thread/backgroundTerminals/list':
        keys |= {'cursor', 'limit'}
        if ('cursor' in params and (not isinstance(params['cursor'], str) or not params['cursor']
                or len(params['cursor']) > 4096)) or ('limit' in params and
                (type(params['limit']) is not int or not 1 <= params['limit'] <= 100)):
            raise CodexFeatureError('invalid')
    elif method == 'thread/revert':
        keys.add('beforeTurnId')
        if not is_safe_id(params.get('beforeTurnId')):
            raise CodexFeatureError('history')
    elif method == 'thread/backgroundTerminals/terminate':
        keys.add('processId')
        if not is_safe_id(params.get('processId')):
            raise CodexFeatureError('invalid')
    elif method == 'thread/memoryMode/set':
        keys.add('mode')
        if params.get('mode') not in ('enabled', 'disabled'):
            raise CodexFeatureError('invalid')
    elif method == 'thread/goal/set':
        keys |= {'objective', 'status', 'origin', 'tokenBudget'}
        if params.get('origin') != 'user' or params.get('status') != 'paused':
            # Native continuation bypasses TCP's per-turn subscription and
            # permission checks. Goals can be inspected, saved paused, and
            # cleared until continuation is driven through the TCP executor.
            raise CodexFeatureError('invalid')
        if 'objective' in params and (not isinstance(params['objective'], str)
                or not params['objective'].strip() or len(params['objective']) > 4000):
            raise CodexFeatureError('invalid')
        if 'tokenBudget' in params and (type(params['tokenBudget']) is not int
                or not 1 <= params['tokenBudget'] <= 100_000_000):
            raise CodexFeatureError('invalid')
    if set(params) - keys:
        raise CodexFeatureError('invalid')


def turn_anchor(message):
    """Native turn IDs are retained before ':' in Codex message keys."""
    from ..platform_paths import is_safe_id
    key = getattr(message, 'key', '')
    turn = key.split(':', 1)[0] if isinstance(key, str) and ':' in key else ''
    if not is_safe_id(turn):
        raise CodexFeatureError('history')
    return turn


def verify_anchor(connection, native_id, turn_id, *, before=False):
    """Resolve against current native history; never infer from row numbers."""
    cursor, seen = None, set()
    for _ in range(1000):
        params = {'threadId': native_id, 'sortDirection': 'desc', 'limit': 100, 'itemsView': 'notLoaded'}
        if cursor is not None:
            params['cursor'] = cursor
        page = connection.request('thread/turns/list', params)
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            break
        for turn in page['data']:
            if isinstance(turn, dict) and turn.get('id') == turn_id:
                if turn.get('status') not in ('completed', 'failed', 'interrupted') and not before:
                    raise CodexFeatureError('busy')
                return
        cursor = page.get('nextCursor')
        if cursor is None or not isinstance(cursor, str) or not cursor or cursor in seen:
            break
        seen.add(cursor)
    raise CodexFeatureError('history')


def goal_lines(result):
    goal = result.get('goal') if isinstance(result, dict) else None
    if goal is None:
        return ['No goal is set for this chat.']
    if not isinstance(goal, dict) or not isinstance(goal.get('objective'), str):
        raise CodexFeatureError('failed')
    return ['Goal: ' + goal['objective'], 'Status: ' + str(goal.get('status', 'unknown')),
            'Tokens used: ' + str(goal.get('tokensUsed', 'not reported')),
            'Token budget: ' + str(goal.get('tokenBudget') or 'none'),
            'Time used: ' + str(goal.get('timeUsedSeconds', 'not reported')) + ' seconds.']
