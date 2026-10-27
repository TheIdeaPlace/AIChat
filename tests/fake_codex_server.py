"""Synthetic stdio server; never reads account data or runs an agent."""
import json
import shlex
from pathlib import Path
import sys
import threading
import time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

LAYERS = [{'name': {'type': 'project', 'dotCodexFolder': str(Path(__file__).resolve().parent / '__empty_codex_layer__')}}]

lock = threading.Lock()
pair = []
initialized = False
account_reads = 0
config_reads = 0
server_answer = None
owned_active = False
owned_cwd = '/workspace'
owned_turn = 'turn'
owned_turn_count = 0


def emit(message, partial=False):
    raw = json.dumps(message, ensure_ascii=False) + "\n"
    with lock:
        if partial:
            middle = len(raw) // 2
            sys.stdout.write(raw[:middle])
            sys.stdout.flush()
            time.sleep(.01)
            sys.stdout.write(raw[middle:])
        else:
            sys.stdout.write(raw)
        sys.stdout.flush()


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method is None and ('result' in request or 'error' in request):
        server_answer = request
        if len(sys.argv) > 1 and sys.argv[1] in ('owned-turn-approval', 'owned-turn-command-output', 'owned-turn-unsupported', 'owned-turn-unknown', 'owned-turn-kept'):
            owned_active = False
            emit({'method': 'turn/completed', 'params': {'threadId': 'owned',
                  'turn': {'id': owned_turn, 'status': 'completed'}}})
        continue
    if method == "initialize":
        if len(sys.argv) > 1 and sys.argv[1] == "hang-init":
            continue
        if len(sys.argv) > 1 and sys.argv[1] == "bad-init":
            emit({"id": request["id"], "error": {"message": "private init error"}})
            continue
        emit({"id": request["id"], "result": {"userAgent": "fake"}})
        continue
    if method == "initialized":
        initialized = True
        continue
    if not initialized:
        emit({"id": request["id"], "error": {"message": "not initialized"}})
        continue
    params = request.get("params", {})
    if method == "account/read" and len(sys.argv) > 1 and sys.argv[1].startswith("account-"):
        account_reads += 1
        kind = sys.argv[1][8:]
        if kind.startswith("route-"):
            kind = "chatgpt"
        if kind == "changed":
            kind = "chatgpt" if account_reads == 1 else "apiKey"
        result = {"account": {"type": kind}, "requiresOpenaiAuth": True}
        if kind == "missing":
            result["account"] = None
        if kind == "other-provider":
            result = {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": False}
        emit({"id": request["id"], "result": result})
        continue
    scenario = sys.argv[1] if len(sys.argv) > 1 else ""
    if method == 'mcpServerStatus/list':
        rows = [{'runtimeStatus': 'connected', 'tools': {}}] if scenario == 'account-route-mcp' else []
        emit({'id': request['id'], 'result': {'data': rows, 'nextCursor': None}})
        continue
    if scenario in ('owned-turn-approval', 'owned-turn-hang', 'owned-turn-unsupported', 'owned-turn-command-output', 'owned-turn-unknown', 'owned-turn-plan', 'owned-turn-kept',
                    'owned-turn-steer', 'owned-turn-steer-race', 'owned-turn-steer-close'):
        if method == 'account/read':
            result = {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
        elif method == 'config/read':
            result = {'config': {}, 'layers': LAYERS}
        elif method == 'thread/start':
            if scenario == 'owned-turn-kept':
                owned_cwd = params['cwd']
            result = {'thread': {'id': 'owned', 'createdAt': 1, 'status': {'type': 'idle'}, 'ephemeral': False},
                      'cwd': owned_cwd, 'model': 'synthetic', 'modelProvider': 'openai',
                      'sandbox': {'type': 'readOnly'}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}
        elif method == 'thread/read':
            result = {'thread': {'id': 'owned', 'modelProvider': 'openai', 'cwd': owned_cwd,
                                'status': {'type': 'active' if owned_active else 'idle'}}}
        elif method == 'turn/start':
            owned_active = True
            if scenario == 'owned-turn-kept':
                owned_turn_count += 1
                owned_turn = f'turn-{owned_turn_count}'
            emit({'id': request['id'], 'result': {'turn': {'id': owned_turn, 'status': 'inProgress'}}})
            if scenario == 'owned-turn-command-output':
                for _ in range(256):
                    emit({'method': 'item/commandExecution/outputDelta', 'params': {
                        'threadId': 'owned', 'turnId': 'turn', 'itemId': 'command', 'delta': 'synthetic output\n'}})
            if scenario == 'owned-turn-plan':
                for thread, turn, steps in [
                        ('foreign', 'turn', [{'step': 'Foreign task', 'status': 'completed'}]),
                        ('owned', 'wrong-turn', [{'step': 'Wrong turn', 'status': 'completed'}]),
                        ('owned', 'turn', [{'step': 'Find the cause', 'status': 'inProgress'},
                                           {'step': 'Run the tests', 'status': 'pending'}])]:
                    emit({'method': 'turn/plan/updated', 'params': {
                        'threadId': thread, 'turnId': turn, 'plan': steps}})
                for _ in range(100):
                    emit({'method': 'turn/plan/updated', 'params': {'threadId': 'owned', 'turnId': 'turn',
                          'plan': [{'step': 'Run the tests', 'status': 'inProgress'},
                                   {'step': 'Find the cause', 'status': 'completed'}]}})
                emit({'method': 'turn/plan/updated', 'params': {'threadId': 'owned', 'turnId': 'turn',
                      'plan': [{'step': 'Bad state', 'status': []}]}})
            if scenario not in ('owned-turn-hang', 'owned-turn-plan', 'owned-turn-steer',
                                'owned-turn-steer-race', 'owned-turn-steer-close'):
                # Shaped like codex-cli 0.162.1's command approvals (#284):
                # environmentId and availableDecisions are always present.
                details = {'kind': 'command', 'threadId': 'owned', 'turnId': 'turn', 'itemId': 'command',
                           'startedAtMs': 1, 'environmentId': 'local',
                           'command': 'synthetic command', 'cwd': '/workspace',
                           'reason': 'Synthetic reason', 'commandActions': [],
                           'additionalPermissions': {'fileSystem': {'write': ['/workspace/.git']}},
                           'proposedExecpolicyAmendment': ['synthetic'],
                           'availableDecisions': ['accept', {'acceptWithExecpolicyAmendment': {
                               'execpolicy_amendment': ['synthetic']}}, 'decline', 'cancel']}
                if scenario == 'owned-turn-unsupported':
                    details['kind'] = 'writeStdin'
                if scenario == 'owned-turn-kept':
                    shell = ['C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe', '-Command'] if sys.platform == 'win32' else ['/bin/bash', '-lc']
                    details.update(turnId=owned_turn, cwd=owned_cwd,
                                   command=shlex.join([*shell, f'git commit -m commit{owned_turn_count}']),
                                   proposedExecpolicyAmendment=['git', 'commit'])
                    del details['additionalPermissions']
                emit({'id': 'approval', 'method': 'future/unsupportedRequest' if scenario == 'owned-turn-unknown'
                      else 'item/commandExecution/requestApproval', 'params': details})
            continue
        elif method == 'turn/steer':
            # Shaped like the documented app-server turn/steer (#239).
            if scenario == 'owned-turn-steer-close':
                sys.exit(0)  # the connection is lost as the steer goes
            if scenario == 'owned-turn-steer-race':
                # The turn finished just as the steer arrived.
                owned_active = False
                emit({'method': 'turn/completed', 'params': {'threadId': 'owned',
                      'turn': {'id': owned_turn, 'status': 'completed'}}})
            if (not owned_active or params.get('expectedTurnId') != owned_turn
                    or set(params) != {'threadId', 'expectedTurnId', 'input'}):
                emit({'id': request['id'], 'error': {'code': -32600, 'message': 'no active turn to steer'}})
            else:
                emit({'id': request['id'], 'result': {'turnId': owned_turn}})
            continue
        elif method == 'turn/interrupt':
            owned_active = False
            emit({'id': request['id'], 'result': {}})
            emit({'method': 'turn/completed', 'params': {'threadId': 'owned',
                  'turn': {'id': 'turn', 'status': 'interrupted'}}})
            continue
        else:
            emit({'id': request['id'], 'error': {'message': 'unsupported synthetic method'}})
            continue
        emit({'id': request['id'], 'result': result})
        continue
    if scenario.startswith("account-") and method == "thread/read":
        thread = {"id": params["threadId"], "modelProvider": "openai", "cwd": "/synthetic"}
        if scenario == "account-route-provider":
            thread["modelProvider"] = "custom"
        if scenario == "account-route-wrong-id":
            thread["id"] = "other"
        if scenario == "account-route-missing-cwd":
            thread.pop("cwd")
        emit({"id": request["id"], "result": {"thread": thread}})
        continue
    if scenario.startswith("account-") and method == "config/read":
        config_reads += 1
        config = {}
        kind = scenario.removeprefix("account-route-")
        if kind == "canonical-chatgpt":
            config["chatgpt_base_url"] = "https://chatgpt.com/backend-api/"
        if kind in ("openai_base_url", "chatgpt_base_url", "model_provider", "forced_login_method"):
            config[kind] = "alternate"
        if kind == "builtin-override":
            config["model_providers"] = {"openai": {"env_key": "ALTERNATE_KEY"}}
        if kind == "malformed-providers":
            config["model_providers"] = []
        if kind == "mcp":
            config['mcp_servers'] = {'synthetic': {}}
        if kind == "malformed-config":
            config = None
        if kind == "changed" and config_reads > 1:
            config["openai_base_url"] = "alternate"
        if kind == "cwd-sensitive" and params.get("cwd") != "/synthetic":
            config["model_provider"] = "alternate"
        layers = ([{'name': {'type': 'project', 'dotCodexFolder': params.get('cwd')}}]
                  if kind == 'rule-files' else LAYERS)
        emit({"id": request["id"], "result": {"config": config, "layers": layers, "echo": params}})
        continue
    mode = params.get("mode")
    if mode == 'approval-receive':
        emit({'id': 'server-approval', 'method': 'item/commandExecution/requestApproval',
              'params': {'threadId': 'owned', 'turnId': 'turn', 'itemId': 'command',
                         'command': 'synthetic command', 'cwd': '/workspace'}}, partial=True)
    if mode == 'approval-response':
        emit({'id': request['id'], 'result': {'serverAnswer': server_answer}})
        continue
    if mode in ("events", "event-overflow", "bad-event"):
        if mode == "bad-event":
            emit({"method": "item/agentMessage/delta", "params": {"threadId": "one"}})
        elif mode == "event-overflow":
            for number in range(64):
                emit({"method": "item/agentMessage/delta", "params": {
                    "threadId": "one", "turnId": "turn", "itemId": "reply", "delta": str(number)}})
        else:
            for notification, data in [
                ("turn/started", {"threadId": "one", "turn": {"id": "a", "status": "inProgress"}}),
                ("item/agentMessage/delta", {"threadId": "one", "turnId": "a", "itemId": "reply", "delta": "Draft"}),
                ("item/agentMessage/delta", {"threadId": "two", "turnId": "b", "itemId": "reply", "delta": "Other"}),
                ("item/completed", {"threadId": "one", "turnId": "a", "item": {"type": "agentMessage", "id": "reply", "text": "Final result"}}),
                ("turn/completed", {"threadId": "one", "turn": {"id": "a", "status": "completed"}}),
                ("turn/completed", {"threadId": "two", "turn": {"id": "b", "status": "interrupted", "error": {"message": "private secret"}}})]:
                emit({"method": notification, "params": data}, partial=True)
    if mode == "late":
        threading.Timer(.2, lambda identity=request['id']: emit({'id': identity, 'result': {'late': True}})).start()
        continue
    if mode == "hang":
        continue
    if mode == "exit":
        break
    if mode == "malformed":
        print("private invalid JSON", flush=True)
        continue
    if mode == "nested":
        print("[" * 10000 + "0" + "]" * 10000, flush=True)
        continue
    if mode == "server-request":
        emit({"id": "approval", "method": "item/commandExecution/requestApproval",
              "params": {"private": "do not approve"}})
        continue
    if mode == "unknown-id":
        emit({"id": 999, "result": {}})
        continue
    if mode == "bad-result":
        emit({"id": request["id"], "result": []})
        continue
    if mode == "error":
        emit({"id": request["id"], "error": {"message": "private secret"}})
        continue
    response = {"id": request["id"], "result": {"echo": params}}
    if mode == "pair":
        pair.append(response)
        if len(pair) == 2:
            emit({"method": "future/notification", "params": {"text": "ignored"}})
            for message in reversed(pair):
                emit(message, partial=True)
            pair.clear()
    else:
        emit(response, partial=True)
