"""Credential-free native sandbox investigation for #307; never uses a real account.

Usage: python -I tools/probe_codex_scoped_permissions.py CODEX OUT.json [WINDOWS_MODE]
Only synthetic commands in disposable directories are accepted. No saved native grants.
"""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


class Model(http.server.BaseHTTPRequestHandler):
    scenario = None
    calls = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        scenario = type(self).scenario
        type(self).calls += 1
        scenario.setdefault('tool_outputs', []).extend(
            item for item in body.get('input', []) if item.get('type') == 'function_call_output'
        )
        tools = body.get('tools', [])
        scenario['permission_tool_schemas'] = [
            tool for tool in tools if tool.get('name') in ('exec_command', 'request_permissions')
        ]
        if type(self).calls == 1:
            item = {'type': 'function_call', 'call_id': 'probe1', 'name': 'exec_command',
                    'arguments': json.dumps(scenario['args'])}
        else:
            item = {'type': 'message', 'role': 'assistant', 'id': 'done',
                    'content': [{'type': 'output_text', 'text': 'Probe complete.'}]}
        events = [
            {'type': 'response.created', 'response': {'id': 'probe'}},
            {'type': 'response.output_item.done', 'item': item},
            {'type': 'response.completed', 'response': {'id': 'probe', 'usage': {
                'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}},
        ]
        data = ''.join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                       for event in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Rpc:
    def __init__(self, process, scenario):
        self.process, self.scenario, self.next_id = process, scenario, 0

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()

    def read(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('app-server exited before the turn completed')
        return json.loads(line)

    def handle(self, message):
        method = message.get('method')
        if method and 'id' in message:
            self.scenario.setdefault('requests', []).append(message)
            if method == 'item/commandExecution/requestApproval':
                # Full escalation is NEVER accepted by this probe.
                params = message['params']
                decision = 'accept' if params.get('additionalPermissions') else 'cancel'
                self.send({'id': message['id'], 'result': {'decision': decision}})
            else:
                self.send({'id': message['id'], 'error': {
                    'code': -32601, 'message': 'Unsupported probe request'}})
        elif method == 'turn/completed':
            self.scenario['turn_completed'] = message['params']
        elif method == 'windowsSandbox/setupCompleted':
            self.scenario['sandbox_setup'] = message['params']

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send({'id': request_id, 'method': method, 'params': params})
        while True:
            message = self.read()
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise RuntimeError(f'{method}: {message["error"]}')
                return message['result']
            self.handle(message)


def run(binary, root, work, scenario, port, windows_mode):
    home = root / ('home-' + scenario['name'])
    home.mkdir()
    (home / 'config.toml').write_text(f'''model = "gpt-5.1-codex"
model_provider = "probe"
approvals_reviewer = "user"
default_permissions = "probe-base"
[features]
exec_permission_approvals = true
request_permissions_tool = true
network_proxy = {str(bool(scenario.get('network_probe'))).lower()}
[model_providers.probe]
name = "probe"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
[permissions.probe-base]
extends = ":workspace"
[permissions.probe-base.filesystem]
":tmpdir" = "read"
":slash_tmp" = "read"
[permissions.probe-git]
extends = "probe-base"
[permissions.probe-git.filesystem.":workspace_roots"]
".git" = "write"
[permissions.probe-network]
extends = "probe-base"
[permissions.probe-network.network]
enabled = true
[permissions.probe-network.network.domains]
"example.com" = "allow"
"example.org" = "deny"
''', encoding='utf-8')
    env = dict(os.environ, CODEX_HOME=str(home))
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
        env.pop(key, None)
    command = [binary, 'app-server']
    if os.name == 'nt':
        command += ['-c', f'windows.sandbox="{windows_mode}"']
    Model.scenario, Model.calls = scenario, 0
    with (home / 'stderr.txt').open('wb') as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, env=env, cwd=work)
        timer = threading.Timer(90, process.kill)
        timer.start()
        rpc = Rpc(process, scenario)
        try:
            scenario['initialize'] = rpc.request('initialize', {
                'clientInfo': {'name': 'tcp-scope-probe', 'version': '1'},
                'capabilities': {'experimentalApi': True}})
            rpc.send({'method': 'initialized', 'params': {}})
            if os.name == 'nt':
                rpc.request('windowsSandbox/setupStart', {'mode': windows_mode, 'cwd': str(work)})
                while 'sandbox_setup' not in scenario:
                    rpc.handle(rpc.read())
                if not scenario['sandbox_setup'].get('success'):
                    raise RuntimeError('Windows sandbox setup failed')
            thread_params = {'cwd': str(work), 'approvalPolicy': 'on-request'}
            if scenario.get('profile'):
                thread_params['permissions'] = scenario['profile']
            else:
                thread_params['sandbox'] = 'workspace-write'
            thread = rpc.request('thread/start', thread_params)
            scenario['thread_settings'] = {key: value for key, value in thread.items() if key != 'thread'}
            turn_params = {
                'threadId': thread['thread']['id'],
                'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': [str(work)],
                                  'networkAccess': False, 'excludeTmpdirEnvVar': True,
                                  'excludeSlashTmp': True},
                'input': [{'type': 'text', 'text': 'Execute the synthetic permission probe.'}]}
            if scenario.get('profile'):
                del turn_params['sandboxPolicy']
                turn_params['permissions'] = scenario['profile']
            rpc.request('turn/start', turn_params)
            while 'turn_completed' not in scenario:
                rpc.handle(rpc.read())
        except Exception as error:
            scenario['error'] = repr(error)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
    scenario['stderr_tail'] = (home / 'stderr.txt').read_text(errors='replace')[-3000:]
    scenario['head_after'] = (work / '.git' / 'HEAD').read_text()
    scenario['outside_file_exists'] = (root / 'outside.txt').exists()
    scenario['hook_outside_file_exists'] = (root / 'hook-outside.txt').exists()


def main():
    binary, output = sys.argv[1:3]
    windows_mode = sys.argv[3] if len(sys.argv) > 3 else 'unelevated'
    root = Path(tempfile.mkdtemp(prefix='tcp-scoped-permissions-')).resolve()
    work = root / 'work'
    work.mkdir()
    subprocess.run(['git', 'init', '-q', str(work)], check=True)
    subprocess.run(['git', '-C', str(work), 'config', 'user.name', 'Synthetic Probe'], check=True)
    subprocess.run(['git', '-C', str(work), 'config', 'user.email', 'probe@example.test'], check=True)
    (work / 'synthetic.txt').write_text('Synthetic permission test.\n')
    subprocess.run(['git', '-C', str(work), 'add', 'synthetic.txt'], check=True)
    hook = work / '.git' / 'hooks' / 'pre-commit'
    hook.write_text(f"#!/bin/sh\nprintf escaped > '{(root / 'hook-outside.txt').as_posix()}'\nexit 0\n")
    hook.chmod(0o755)
    if os.name == 'nt':
        # Python's owner-only temporary ACL otherwise prevents restricted-token traversal.
        # Synthetic folder only; this gives read access, never outside write access.
        subprocess.run(['icacls', str(root), '/grant', '*S-1-1-0:(OI)(CI)(RX)', '/T', '/Q'],
                       capture_output=True, check=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    grant = {'file_system': {'write': [str(work / '.git')]}}
    outside = (f"Set-Content -LiteralPath '{root / 'outside.txt'}' -Value escaped"
               if os.name == 'nt' else f"printf escaped > '{root / 'outside.txt'}'")
    scenarios = [
        {'name': 'baseline', 'args': {'cmd': 'git switch -c baseline-probe'}},
        {'name': 'scoped', 'args': {'cmd': 'git switch -c scoped-probe',
            'sandbox_permissions': 'with_additional_permissions', 'additional_permissions': grant}},
        {'name': 'outside-write', 'args': {'cmd': outside,
            'sandbox_permissions': 'with_additional_permissions', 'additional_permissions': grant}},
        {'name': 'after-grant', 'args': {'cmd': 'git switch -c after-probe'}},
        {'name': 'scoped-explicit-path', 'args': {
            'cmd': f"git -C '{work}' switch -c explicit-probe",
            'sandbox_permissions': 'with_additional_permissions', 'additional_permissions': grant}},
        {'name': 'scoped-hook', 'args': {'cmd': 'git commit -m probe',
            'sandbox_permissions': 'with_additional_permissions', 'additional_permissions': grant}},
        {'name': 'profile-git', 'profile': 'probe-git',
         'args': {'cmd': 'git switch -c profile-probe'}},
        {'name': 'profile-outside', 'profile': 'probe-git', 'args': {'cmd': outside}},
        {'name': 'profile-base', 'profile': 'probe-base',
         'args': {'cmd': 'git switch -c profile-base-probe'}},
        {'name': 'profile-explicit-path', 'profile': 'probe-git',
         'args': {'cmd': f"git -C '{work}' switch -c explicit-profile-probe"}},
    ]
    curl = 'curl.exe' if os.name == 'nt' else 'curl'
    scenarios.extend([
        {'name': 'network-allowed', 'profile': 'probe-network', 'network_probe': True,
         'args': {'cmd': f'{curl} -I --max-time 5 https://example.com'}},
        {'name': 'network-denied', 'profile': 'probe-network', 'network_probe': True,
         'args': {'cmd': f'{curl} -I --max-time 5 https://example.org'}},
        {'name': 'network-direct-bypass', 'profile': 'probe-network', 'network_probe': True,
         'args': {'cmd': f"{curl} --noproxy '*' -I --max-time 5 https://example.org"}},
    ])
    for scenario in scenarios:
        scenario['args'].update(login=False, workdir=str(work), max_output_tokens=1000)
        run(binary, root, work, scenario, server.server_address[1], windows_mode)
    result = {'platform': sys.platform, 'windows_mode': windows_mode, 'root': str(root),
              'codex': subprocess.run([binary, '--version'], capture_output=True, text=True).stdout.strip(),
              'scenarios': scenarios}
    Path(output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    server.shutdown()
    for scenario in scenarios:
        print(scenario['name'], json.dumps({key: scenario.get(key) for key in (
            'error', 'head_after', 'outside_file_exists', 'tool_outputs')}))
    # A completed workflow means evidence was collected, not that the feature is viable.
    if any('error' in scenario or not scenario.get('tool_outputs') for scenario in scenarios):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
