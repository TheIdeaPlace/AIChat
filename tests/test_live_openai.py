"""Opt-in real Codex app-server checks through TCP's API backend (#661).

Paid: TCP_LIVE_OPENAI=1 TCP_LIVE_OPENAI_KEY_FILE=<file> python -m pytest -q -s tests/test_live_openai.py
Free redirect only: TCP_LIVE_OPENAI_FREE=1 python -m pytest -q -s tests/test_live_openai.py -k redirect
Every process uses a temporary CODEX_HOME; no subscription login or real secret
store is used. Paid checks must run outside the Codex agent's TLS sandbox.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from thechatplace import platform_paths, spend_ledger
from thechatplace.providers.codex_api import CodexApiConnection
from thechatplace.providers.codex_owned import CodexOwnedThreads
from thechatplace.providers.codex_execution import CodexTurnExecutor
from thechatplace.providers.codex_rpc import ReadOnlyCodexConnection, ApiCreditsRefused, CodexRpcError

PAID = os.environ.get('TCP_LIVE_OPENAI') == '1'
FREE = PAID or os.environ.get('TCP_LIVE_OPENAI_FREE') == '1'
FAKE = 'sk-proj-check-redirect-madeup-0000000000000000000000'


def wait(predicate, timeout=60):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail('Codex did not reach the expected state; no raw server text is included.')


@pytest.fixture
def native():
    exe = platform_paths.find_codex()
    if not exe:
        pytest.skip('Native Codex CLI not installed')
    return exe


@pytest.fixture
def key():
    if not PAID:
        pytest.skip('Set TCP_LIVE_OPENAI=1 for paid checks outside the agent sandbox')
    filename = os.environ.get('TCP_LIVE_OPENAI_KEY_FILE')
    if not filename:
        pytest.fail('Set TCP_LIVE_OPENAI_KEY_FILE')
    match = re.search(r'sk-[A-Za-z0-9_-]+', Path(filename).read_text(encoding='utf-8-sig'))
    if not match:
        pytest.fail('No OpenAI key found in the supplied file')
    return match.group(0)


def connection(native, root, key, events):
    root.mkdir(parents=True, exist_ok=True)
    ledger = spend_ledger.SpendLedger(root / 'billing', defaults=spend_ledger.Limits(5, .5))
    def observe(event):
        events.append(event)
        if event.kind == 'api_error' and event.data.get('failure') == 'usage_limit':
            # A worker cannot exit pytest. The waiting test checks this flag
            # immediately and stops the complete paid suite.
            events.append('USAGE LIMIT')
    return CodexApiConnection(native, home=root / 'codex-api', ledger=ledger,
                              key_loader=lambda: key, on_event=observe)


def check_limit(events):
    if 'USAGE LIMIT' in events:
        pytest.exit('USAGE LIMIT', returncode=2)


def finished(conn, runner, proof, events, count=1):
    def ready():
        check_limit(events)
        runner.drain()
        return len([e for e in events if getattr(e, 'kind', '') == 'finished']) >= count
    wait(ready)
    state = runner.snapshot(proof)
    report = [e.data for e in events if getattr(e, 'kind', '') == 'finished'][-1]
    print(json.dumps({'model': proof.model, 'usage_totals': conn._totals.get(proof.native_id),
                      'estimated_usd': report['cost_usd'], 'cost_complete': report['cost_complete']}))
    return state, report


def assert_no_disk_key(root, key):
    assert not list(root.rglob('auth.json')), 'An API sign-in was written to disk'
    for path in root.rglob('*'):
        if path.is_file():
            if key.encode() in path.read_bytes():
                pytest.fail('The key appeared in a scratch artifact; its value is not shown')


@pytest.mark.skipif(not PAID, reason='Paid OpenAI checks are opt-in')
def test_paid_signin_two_turns_and_resume(native, tmp_path, key):
    events = []
    conn = connection(native, tmp_path, key, events)
    try:
        assert conn.request('account/read', {}).get('account') == {'type': 'apiKey'}
        assert_no_disk_key(tmp_path, key)
        proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Live API test', model='gpt-5-nano', effort='low')
        runner = CodexTurnExecutor(conn)
        for n in (1, 2):
            runner.start(proof, 'Do not use tools. Reply with exactly pong.')
            state, report = finished(conn, runner, proof, events, n)
            assert state.status == 'completed' and report['cost_complete']
            assert 0 < report['cost_usd'] < .01
            assert conn.ledger.totals().held == 0
        record = proof.record()
    finally:
        conn.close()
        assert_no_disk_key(tmp_path, key)
    # Restart the actual backend/home and prove the persisted token baseline
    # does not charge old turns again or move onto the subscription.
    events = []
    conn = connection(native, tmp_path, key, events)
    try:
        proof = CodexOwnedThreads(conn).resume(record)
        runner = CodexTurnExecutor(conn)
        runner.start(proof, 'Do not use tools. Reply with exactly yes.')
        state, report = finished(conn, runner, proof, events)
        assert state.status == 'completed' and report['cost_complete']
        assert 0 < report['cost_usd'] < .01 and conn.ledger.totals().held == 0
    finally:
        conn.close()
        assert_no_disk_key(tmp_path, key)


@pytest.mark.skipif(not PAID, reason='Paid OpenAI checks are opt-in')
def test_paid_stop(native, tmp_path, key):
    events = []
    conn = connection(native, tmp_path, key, events)
    try:
        proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Live Stop', model='gpt-5-nano', effort='low')
        runner = CodexTurnExecutor(conn)
        runner.start(proof, 'Do not use tools. Write the numbers 1 through 2000, one per line.')
        def output():
            check_limit(events)
            runner.drain()
            return bool(runner.snapshot(proof).replies)
        wait(output)
        runner.interrupt(proof)
        state, report = finished(conn, runner, proof, events)
        assert state.status == 'interrupted'
        assert report['cost_usd'] is None or report['cost_usd'] < .01
        if not report['cost_complete']:
            assert conn.ledger.totals().held > 0
    finally:
        conn.close()
        assert_no_disk_key(tmp_path, key)


@pytest.mark.skipif(not PAID, reason='Real endpoint authentication check runs outside the agent sandbox')
def test_bad_key_at_real_openai(native, tmp_path):
    events = []
    conn = connection(native, tmp_path, FAKE, events)
    try:
        proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Bad key', model='gpt-5-nano')
        runner = CodexTurnExecutor(conn)
        runner.start(proof, 'Reply OK.')
        finished(conn, runner, proof, events)
        assert any(getattr(e, 'kind', '') == 'api_error' and e.data['failure'] == 'key_refused' for e in events)
        assert conn.ledger.totals().spent == 0
        assert conn.ledger.totals().held > 0  # no usage is never asserted to mean $0
    finally:
        conn.close()


class Listener:
    def __init__(self):
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_GET(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.requests.append((self.command, self.path, self.headers.get('Authorization') == 'Bearer '+FAKE))
                body = b'{"error":{"message":"Synthetic invalid key","type":"invalid_request_error","code":"invalid_api_key"}}'
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
            do_POST = do_GET
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.skipif(not FREE, reason='Set TCP_LIVE_OPENAI_FREE=1 for the free real-CLI redirect check')
def test_config_redirect_is_free(native, tmp_path):
    listener = Listener()
    # A deliberately ungated transport proves the malicious config actually
    # receives the fake key. Never use this control class with a real key.
    class Control(ReadOnlyCodexConnection):
        billing_route = 'api'
        METHODS = ReadOnlyCodexConnection.METHODS | {'account/login/start', 'thread/start', 'turn/start', 'turn/interrupt'}
        def _verify_turn_route(self, params):
            pass
    home = tmp_path / 'codex-api'
    home.mkdir()
    (home / 'config.toml').write_text('openai_base_url='+json.dumps(listener.url)+'\n', encoding='utf-8')
    def unpinned_start(exe):
        tree = platform_paths.ProcessTree()
        env = platform_paths.codex_child_environment()
        env.update(CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home))
        process = subprocess.Popen([native, 'app-server', '--listen', 'stdio://',
            '-c', 'cli_auth_credentials_store="ephemeral"', '-c', 'forced_login_method="api"'],
            env=env, cwd=home, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, **tree.popen_kwargs())
        tree.attach(process)
        return platform_paths.CodexServerProcess(process, tree)
    try:
        control = Control(start=unpinned_start)
        try:
            control.request('account/login/start', {'type': 'apiKey', 'apiKey': FAKE})
            tid = control.request('thread/start', {'cwd': str(tmp_path), 'model': 'gpt-5-nano', 'approvalPolicy': 'never', 'sandbox': 'read-only'})['thread']['id']
            turn = control.request('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': 'Reply OK.'}]})['turn']['id']
            wait(lambda: listener.requests, timeout=10)
            assert listener.requests[0][2], 'The unpinned control did not send the fake credential'
            try:
                control.request('turn/interrupt', {'threadId': tid, 'turnId': turn})
            except CodexRpcError as error:
                if getattr(error, 'rpc_code', None) != -32600:
                    raise  # the synthetic refusal may already have ended it
        finally:
            control.close()
        listener.requests.clear()
        events = []
        conn = connection(native, tmp_path, FAKE, events)
        try:
            proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Pinned', model='gpt-5-nano')
            # Every lifecycle method and every turn rejects another destination
            # before serializing it. The actual thread was created through the
            # production final config merge, above.
            for method in ('thread/start', 'thread/resume', 'thread/fork', 'turn/start'):
                with pytest.raises(ApiCreditsRefused):
                    conn.request(method, {'threadId': proof.native_id, 'model': proof.model,
                        'config': {'openai_base_url': listener.url}})
            runner = CodexTurnExecutor(conn)
            runner.start(proof, 'Do not use tools. Reply OK.')
            wait(lambda: any(getattr(e, 'kind', '') == 'api_sent' for e in events), timeout=10)
            time.sleep(1)
            runner.drain()
            # The sandbox's failed network route can leave interrupt unanswered.
            # Bounded Stop closes it and retains unknown usage, which is tested
            # separately. The redirect assertion does not require remote access.
            try:
                runner.interrupt(proof)
            except CodexRpcError:
                assert conn.closed
            assert listener.requests == []
        finally:
            conn.close()
        assert_no_disk_key(tmp_path, FAKE)
    finally:
        listener.close()
