"""Synthetic stdio server; never connects to a service or persists credentials."""
import json
import os
import sys
from pathlib import Path

home = Path(os.environ['CODEX_HOME'])
scenario = sys.argv[1] if len(sys.argv) > 1 else 'normal'
threads = {}
logged_in = False
pending = None


def emit(value):
    print(json.dumps(value), flush=True)


def event(method, params):
    emit({'method': method, 'params': params})


def total(n):
    return {'inputTokens': 1000*n, 'cachedInputTokens': 200*n, 'outputTokens': 50*n,
            'reasoningOutputTokens': 10*n, 'totalTokens': 1050*n}


for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    method, params = msg['method'], msg.get('params', {})
    result = {}
    if method == 'account/login/start':
        logged_in = True
        result = {'type': 'apiKey'}
    elif method == 'account/read':
        result = {'account': {'type': 'apiKey'} if logged_in else None, 'requiresOpenaiAuth': True}
    elif method == 'config/read':
        result = {'config': {'model_provider': 'openai', 'forced_login_method': 'api',
            'openai_base_url': 'https://api.openai.com/v1', 'cli_auth_credentials_store': 'ephemeral',
            'web_search': 'disabled', 'features': {k: False for k in ('hooks', 'multi_agent', 'goals', 'apps', 'plugins', 'memories')}},
            'layers': [{'name': {'type': 'user', 'file': str(home / 'config.toml')}}]}
    elif method in ('mcpServerStatus/list', 'thread/turns/list'):
        result = {'data': [], 'nextCursor': None}
    elif method in ('thread/start', 'thread/fork', 'thread/resume'):
        tid = params.get('threadId') if method == 'thread/resume' else 'chat-' + str(len(threads)+1)
        count = threads.get(params.get('threadId'), {}).get('count', 0) if method != 'thread/start' else 0
        threads[tid] = {'count': count, 'cwd': params['cwd'], 'model': params.get('model', 'gpt-5-nano'), 'status': 'idle'}
        thread = {'id': tid, 'createdAt': 100, 'cwd': params['cwd'], 'modelProvider': 'openai',
                  'ephemeral': params.get('ephemeral', False), 'status': {'type': 'idle'}}
        result = {'thread': thread, 'cwd': params['cwd'], 'model': threads[tid]['model'],
                  'reasoningEffort': params.get('config', {}).get('model_reasoning_effort'),
                  'modelProvider': 'openai', 'serviceTier': None, 'approvalPolicy': params['approvalPolicy'],
                  'approvalsReviewer': 'user', 'sandbox': {'type': 'readOnly'}}
    elif method == 'thread/read':
        t = threads[params['threadId']]
        result = {'thread': {'id': params['threadId'], 'cwd': t['cwd'], 'modelProvider': 'openai',
                             'status': {'type': t['status']}}}
    elif method == 'turn/start':
        if scenario == 'rpc-error':
            emit({'id': msg['id'], 'error': {'code': -32603, 'message': 'Synthetic ambiguous failure'}})
            continue
        tid = params['threadId']
        t = threads[tid]
        t['count'] += 1
        t['status'] = 'active'
        turn = 'turn-' + str(t['count'])
        emit({'id': msg['id'], 'result': {'turn': {'id': turn, 'status': 'inProgress'}}})
        event('turn/started', {'threadId': tid, 'turn': {'id': turn, 'status': 'inProgress'}})
        if scenario in ('401', 'unauthorized') and t['count'] == 1:
            info = {'responseStreamDisconnected': {'httpStatusCode': 401}} if scenario == '401' else 'unauthorized'
            event('error', {'threadId': tid, 'turnId': turn, 'willRetry': False,
                            'error': {'codexErrorInfo': info}})
            t['status'] = 'idle'
            event('turn/completed', {'threadId': tid, 'turn': {'id': turn, 'status': 'failed'}})
            continue
        if scenario == 'retry':
            event('error', {'threadId': tid, 'turnId': turn, 'willRetry': True,
                            'error': {'codexErrorInfo': 'streamDisconnected'}})
        if scenario == 'rerouted' and t['count'] > 1:
            event('model/rerouted', {'threadId': tid, 'turnId': turn, 'toModel': 'unpriced-model'})
        if scenario != 'missing':
            # Refused requests do not advance reported token totals.
            count = t['count'] - (1 if scenario in ('401', 'unauthorized') else 0)
            usage = {'last': total(1), 'total': total(count), 'modelContextWindow': 400000}
            if scenario == 'cap':
                usage = {'last': total(1000), 'total': total(1000), 'modelContextWindow': 400000}
            if scenario == 'malformed':
                usage['total']['cachedInputTokens'] = -1
            event('thread/tokenUsage/updated', {'threadId': tid, 'turnId': turn, 'tokenUsage': usage})
            if scenario == 'duplicate':
                event('thread/tokenUsage/updated', {'threadId': tid, 'turnId': turn, 'tokenUsage': usage})
        if scenario in ('hang', 'missing', 'malformed', 'unanswered-stop', 'cap') or (scenario == 'stop-once' and t['count'] == 1):
            pending = (tid, turn)
            continue
        event('item/completed', {'threadId': tid, 'turnId': turn,
              'item': {'id': 'reply', 'type': 'agentMessage', 'text': 'OK'}})
        t['status'] = 'idle'
        event('turn/completed', {'threadId': tid, 'turn': {'id': turn, 'status': 'completed', 'error': None}})
        continue
    elif method == 'turn/interrupt':
        if scenario == 'unanswered-stop':
            continue
        tid, turn = params['threadId'], params['turnId']
        threads[tid]['status'] = 'idle'
        event('turn/completed', {'threadId': tid, 'turn': {'id': turn, 'status': 'interrupted', 'error': None}})
    emit({'id': msg['id'], 'result': result})
