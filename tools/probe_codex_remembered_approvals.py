"""Real Codex + synthetic model: TCP-only remembered approvals, no credentials (#307).

Only disposable repositories are changed. All answers on the wire are accept or
cancel. Output belongs outside the checkout: --out <temporary-folder>/result.json.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thechatplace import platform_paths
from thechatplace.providers.codex_permissions import CodexPermissionStore, candidate, matches
from thechatplace.providers.codex_requests import decode_request


class Model(http.server.BaseHTTPRequestHandler):
    scenario = None
    calls = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        cls = type(self)
        cls.calls += 1
        if cls.calls == 1:
            names = {tool.get('name') for tool in body.get('tools', [])}
            if 'exec_command' not in names:
                raise RuntimeError('This Codex version does not offer exec_command')
            item = {'type': 'function_call', 'call_id': 'synthetic-call', 'name': 'exec_command',
                    'arguments': json.dumps({'cmd': cls.scenario['command'], 'login': False,
                                            'sandbox_permissions': 'require_escalated',
                                            'justification': 'Disposable TCP approval probe',
                                            'prefix_rule': cls.scenario['prefix']})}
        else:
            cls.scenario['tool_outputs'] = [item for item in body.get('input', [])
                                            if item.get('type') == 'function_call_output']
            item = {'type': 'message', 'role': 'assistant', 'id': 'synthetic-message',
                    'content': [{'type': 'output_text', 'text': 'Probe complete.'}]}
        events = [{'type': 'response.created', 'response': {'id': 'probe'}},
                  {'type': 'response.output_item.done', 'item': item},
                  {'type': 'response.completed', 'response': {'id': 'probe', 'usage': {
                      'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}}]
        data = ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Rpc:
    def __init__(self, binary, home, work, store, env):
        self.work, self.store = work, store
        self.process = subprocess.Popen([binary, 'app-server'], cwd=work, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.messages = queue.Queue()
        self.next_id = 0
        self.scenario = None
        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            finally:
                self.messages.put(None)
        threading.Thread(target=read, daemon=True).start()
        self.request('initialize', {'clientInfo': {'name': 'tcp-approval-probe', 'version': '1'},
                                    'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized', 'params': {}})

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()

    def read(self):
        message = self.messages.get(timeout=40)
        if message is None:
            raise RuntimeError('Native app-server exited')
        return message

    def handle(self, message):
        if 'method' in message and 'id' in message:
            if message['method'] != 'item/commandExecution/requestApproval':
                raise RuntimeError('Unexpected native request: ' + message['method'])
            request = decode_request(message['id'], message['method'], message['params'])
            rules = self.store.load(request.thread_id)
            remembered = any(matches(rule, request, str(self.work)) for rule in rules)
            self.scenario.setdefault('approvals', []).append({'remembered': remembered, 'params': message['params']})
            if self.scenario.get('deny'):
                decision = 'cancel'
            else:
                if not remembered:
                    rule = candidate(request, str(self.work))
                    if rule is None:
                        raise RuntimeError('TCP cannot remember the real native request')
                    self.store.save(request.thread_id, (*rules, rule))
                decision = 'accept'
            self.send({'id': message['id'], 'result': request.approval_answer(decision)})
        elif message.get('method') == 'turn/completed':
            self.scenario['completed'] = message['params']['turn']['status']

    def request(self, method, params):
        self.next_id += 1
        identity = self.next_id
        self.send({'id': identity, 'method': method, 'params': params})
        while True:
            message = self.read()
            if message.get('id') == identity and 'method' not in message:
                if 'error' in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message['result']
            self.handle(message)

    def turn(self, thread, command, prefix, *, expect_remembered=False, deny=False):
        scenario = self.scenario = {'command': command, 'prefix': prefix, 'deny': deny}
        Model.scenario, Model.calls = scenario, 0
        self.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Synthetic probe'}]})
        while 'completed' not in scenario:
            self.handle(self.read())
        approvals = scenario.get('approvals', [])
        if len(approvals) != 1 or approvals[0]['remembered'] != expect_remembered:
            raise RuntimeError('Unexpected remembered approval result: ' + json.dumps(scenario))
        return scenario

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()


def run(binary):
    results = {'codex': subprocess.check_output([binary, '--version'], text=True).strip(),
               'platform': sys.platform, 'scenarios': []}
    with tempfile.TemporaryDirectory(prefix='tcp-native-approvals-') as temporary:
        root = Path(temporary).resolve()
        home, work = root / 'codex-home', root / 'work'
        home.mkdir()
        work.mkdir()
        env = dict(os.environ, CODEX_HOME=str(home), GIT_CONFIG_NOSYSTEM='1',
                   GIT_CONFIG_GLOBAL=str(root / 'empty-git-config'),
                   GIT_AUTHOR_NAME='TCP Probe', GIT_COMMITTER_NAME='TCP Probe',
                   GIT_AUTHOR_EMAIL='probe@example.invalid', GIT_COMMITTER_EMAIL='probe@example.invalid')
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
            env.pop(key, None)
        def git(*args):
            subprocess.run(['git', *args], cwd=work, env=env, check=True,
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        git('init', '-q')
        git('init', '--bare', '-q', str(root / 'remote.git'))
        git('remote', 'add', 'origin', str(root / 'remote.git'))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (home / 'config.toml').write_text(f'''model = "gpt-5.1-codex"
model_provider = "probe"
approvals_reviewer = "user"
[windows]
sandbox = "unelevated"
[model_providers.probe]
name = "probe"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
''', encoding='utf-8')
        store = CodexPermissionStore(root / 'tcp-data')
        rpc = None
        try:
            rpc = Rpc(binary, home, work, store, env)
            thread = rpc.request('thread/start', {'cwd': str(work), 'sandbox': 'workspace-write',
                                                'approvalPolicy': 'on-request'})['thread']['id']
            results['scenarios'].append(rpc.turn(thread, 'git switch -c tcp-probe', ['git', 'switch']))
            (work / 'note.txt').write_text('first\n')
            results['scenarios'].append(rpc.turn(thread, 'git add note.txt', ['git', 'add']))
            results['scenarios'].append(rpc.turn(thread, 'git commit -m first', ['git', 'commit']))
            (work / 'note.txt').write_text('second\n')
            results['scenarios'].append(rpc.turn(thread, 'git add note.txt', ['git', 'add'], expect_remembered=True))
            results['scenarios'].append(rpc.turn(thread, 'git commit -m second', ['git', 'commit'], expect_remembered=True))
            results['scenarios'].append(rpc.turn(thread, 'git push origin HEAD', ['git', 'push']))
            results['scenarios'].append(rpc.turn(thread, 'git push origin HEAD', ['git', 'push'], expect_remembered=True))
            assert subprocess.check_output(['git', 'rev-list', '--count', 'HEAD'], cwd=work, env=env, text=True).strip() == '2'
            assert subprocess.check_output(['git', '--git-dir', str(root / 'remote.git'), 'rev-parse', 'refs/heads/tcp-probe'], env=env) == subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=work, env=env)
            copy = rpc.request('thread/fork', {'threadId': thread, 'cwd': str(work), 'sandbox': 'workspace-write',
                                             'approvalPolicy': 'on-request'})['thread']['id']
            assert not store.load(copy)
            results['scenarios'].append(rpc.turn(copy, 'git commit --allow-empty -m copy', ['git', 'commit'], deny=True))
            rpc.close()
            rpc = Rpc(binary, home, work, store, env)
            rpc.request('thread/resume', {'threadId': thread, 'cwd': str(work), 'sandbox': 'workspace-write',
                                          'approvalPolicy': 'on-request'})
            results['scenarios'].append(rpc.turn(thread, 'git commit --allow-empty -m restart', ['git', 'commit'], expect_remembered=True))
            store.clear(thread)
            results['scenarios'].append(rpc.turn(thread, 'git commit --allow-empty -m removed', ['git', 'commit'], deny=True))
            assert not list(home.glob('rules/*.rules'))
            results['passed'] = True
        finally:
            if rpc is not None:
                rpc.close()
            server.shutdown()
            server.server_close()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=platform_paths.find_codex())
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.codex:
        parser.error('Codex binary not found')
    result = run(args.codex)
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'passed': result['passed'], 'codex': result['codex'], 'scenarios': len(result['scenarios'])}))
