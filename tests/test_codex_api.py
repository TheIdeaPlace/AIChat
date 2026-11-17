"""Windowless billing boundaries, using the real transport and synthetic tokens."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from thechatplace import platform_paths, billing, openai_billing, openai_prices, spend_ledger
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.providers.codex_api import CodexApiConnection
from thechatplace.providers.codex_billing import account_matches, config_matches, route_config
from thechatplace.providers.codex_owned import CodexOwnedThreads
from thechatplace.providers.codex_execution import CodexTurnExecutor
from thechatplace.providers.codex_rpc import ApiCreditsRefused, CodexRpcError

FAKE = 'sk-proj-synthetic-key-for-tests-000000000000'


def wait(predicate, timeout=8):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'Synthetic backend did not reach the expected state'


@pytest.fixture
def connect(tmp_path):
    children = []
    connections = []
    def create(scenario='normal', ledger=None):
        home = tmp_path / ('home-' + str(len(connections)))
        home.mkdir()
        def start(exe):
            tree = platform_paths.ProcessTree()
            env = platform_paths.codex_child_environment()
            env['CODEX_HOME'] = str(home)
            proc = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('fake_codex_api.py')), scenario],
                env=env, cwd=home, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **tree.popen_kwargs())
            tree.attach(proc)
            child = platform_paths.CodexServerProcess(proc, tree)
            children.append(child)
            return child
        events = []
        ledger = ledger or spend_ledger.SpendLedger(tmp_path / ('spend-' + str(len(connections))),
                                                   defaults=spend_ledger.Limits(5, .50))
        conn = CodexApiConnection(start=start, key_loader=lambda: FAKE, ledger=ledger, on_event=events.append)
        connections.append(conn)
        return conn, events
    yield create
    for conn in connections:
        conn.close()
    for child in children:
        child.close()


def test_price_cache_reasoning_unknown_tier_and_model():
    counters = {'inputTokens': 1000, 'cachedInputTokens': 200, 'outputTokens': 50, 'reasoningOutputTokens': 10}
    assert openai_prices.estimate('gpt-5-nano', counters) == pytest.approx(.000061)
    assert openai_prices.cost_words(.000061).startswith('estimated ')
    assert openai_prices.cost_words(None) == 'cost not known'
    for model, tier in [('future-model', None), ('gpt-5-nano', 'priority')]:
        with pytest.raises(openai_prices.Unpriced):
            openai_prices.require_price(model, tier)
    with pytest.raises(ValueError):
        openai_prices.estimate('gpt-5-nano', {**counters, 'cachedInputTokens': 1001})


def test_routes_keep_subscription_checks_strict():
    from types import SimpleNamespace
    api, sub = SimpleNamespace(billing_route='api'), SimpleNamespace()
    key = {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True}
    chatgpt = {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
    assert account_matches(api, key) and not account_matches(sub, key)
    assert account_matches(sub, chatgpt) and not account_matches(api, chatgpt)
    assert config_matches(sub, {}) and not config_matches(api, {})
    assert config_matches(api, route_config(api)) and not config_matches(sub, route_config(api))


def test_two_turns_settle_differences_and_duplicate_events_do_not_charge_twice(connect, tmp_path):
    conn, events = connect('duplicate')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Paid', model='gpt-5-nano')
    assert proof.record().provider == 'codex-api' and proof.record().billing == 'api'
    runner = CodexTurnExecutor(conn)
    for n in (1, 2):
        runner.start(proof, 'Hello')
        wait(lambda: len([e for e in events if e.kind == 'finished']) == n)
        runner.drain()
        assert conn.ledger.totals().spent == pytest.approx(.000061*n, abs=.000001)
        assert conn.ledger.totals().held == 0
    assert len([e for e in events if e.kind == 'api_sent']) == 2
    assert all(e.data['estimated'] for e in events)
    assert all('estimated' in e.data['cost_words'] for e in events if e.kind == 'finished')


@pytest.mark.parametrize('scenario', ['missing', 'hang', 'malformed'])
def test_stop_and_disconnect_keep_unknown_cost_held(connect, tmp_path, scenario):
    conn, events = connect(scenario)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Stop', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hello')
    wait(lambda: any(e.kind == 'api_sent' for e in events))
    runner.interrupt(proof)
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.ledger.totals().spent + conn.ledger.totals().held >= .499999
    assert not next(e for e in events if e.kind == 'finished').data['cost_complete']


def test_no_usage_on_disconnect_is_held_after_restart(connect, tmp_path):
    conn, events = connect('missing')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Lost', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    wait(lambda: any(e.kind == 'api_sent' for e in events))
    conn.close()
    assert spend_ledger.SpendLedger(conn.ledger.folder).totals().held == .5


def test_unknown_model_native_inference_and_routing_override_never_send(connect, tmp_path):
    conn, events = connect()
    for method, params in [
        ('thread/start', {'model': 'unpriced-model', 'config': {}}),
        ('thread/start', {'model': 'gpt-5-nano', 'config': {'openai_base_url': 'http://example.invalid'}}),
        ('thread/compact/start', {'threadId': 'not-owned'}),
        ('thread/goal/set', {'threadId': 'not-owned'}),
    ]:
        with pytest.raises(ApiCreditsRefused):
            conn.request(method, params)
    assert not events and conn.ledger.totals().spent == conn.ledger.totals().held == 0


def test_provider_defaults_limits_and_store_keep_routes_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path)
    monkeypatch.setattr(spend_ledger, '_DEFAULT', None)
    monkeypatch.setattr(spend_ledger, '_OPENAI_DEFAULT', None)
    claude, api = spend_ledger.default_ledger(), spend_ledger.default_ledger('openai')
    assert claude.limits() == spend_ledger.Limits(190, 5)
    assert api.limits() == spend_ledger.Limits(5, .5)
    api.set_limits(.5, .5)
    api.reserve('codex-api:one')
    assert api.can_spend() and claude.can_spend() is None
    store = OwnSessionStore(tmp_path / 'sessions.json')
    for provider in ('codex', 'codex-api'):
        store.add(OwnSession('same', 'Same ID', str(tmp_path), provider=provider,
                             billing='api' if provider == 'codex-api' else 'subscription', started=True,
                             provider_options={'model': 'gpt-5-nano'}))
    reread = OwnSessionStore(store.path)
    assert not reread.load_error
    assert reread.get('same', 'codex-api').billing == 'api'
    assert reread.get('same', 'codex').billing == 'subscription'
    assert 'codex-api' not in (tmp_path / 'provider-sessions.json').read_text()
    with pytest.raises(ValueError):
        reread.update('same', provider='codex-api', billing='subscription')


def test_secret_is_named_and_diagnostics_are_scrubbed(tmp_path, monkeypatch):
    kept = {}
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path)
    monkeypatch.setattr(platform_paths, 'save_secret', lambda name, key: kept.update({name: key}))
    openai_billing.save_api_key(FAKE)
    assert kept == {'openai-api-key': FAKE}
    assert FAKE not in (tmp_path / 'openai-api-key-info.json').read_text()
    assert FAKE not in billing.scrub('Failed ' + FAKE)


def test_ephemeral_and_fork_have_a_baseline_without_guessing(connect, tmp_path):
    conn, events = connect()
    ephemeral = CodexOwnedThreads(conn).start(str(tmp_path), 'Test key', model='gpt-5-nano', ephemeral=True)
    assert conn._totals[ephemeral.native_id]['inputTokens'] == 0
    source = CodexOwnedThreads(conn).start(str(tmp_path), 'Source', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(source, 'Hello')
    wait(lambda: any(e.kind == 'finished' for e in events))
    copy = CodexOwnedThreads(conn).fork(source.record().to_info(), 'Copy', model='gpt-5-nano')
    assert conn._totals[copy.native_id] == conn._totals[source.native_id]
    runner.start(copy, 'Hello')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    assert conn.ledger.totals().held == 0


def test_unanswered_stop_is_bounded_and_holds_unknown_cost(connect, tmp_path):
    conn, events = connect('unanswered-stop')
    conn.STOP_TIMEOUT = .1
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Stop timeout', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hello')
    started = time.monotonic()
    with pytest.raises(CodexRpcError):
        runner.interrupt(proof)
    assert time.monotonic()-started < 3
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.closed and conn.ledger.totals().held > 0


def test_effective_unpriced_tools_cannot_override_safety_pins():
    from types import SimpleNamespace
    api = SimpleNamespace(billing_route='api')
    config = route_config(api)
    assert not config_matches(api, {**config, 'web_search': 'live'})
    assert not config_matches(api, {**config, 'features': {**config['features'], 'multi_agent': True}})


def test_runtime_keeps_api_identity_and_subscription_record_cannot_send(connect, tmp_path):
    from thechatplace.providers.codex_runtime import CodexChatRuntime
    conn, events = connect()
    runtime = CodexChatRuntime(factory=lambda exe: conn)
    runtime.provider_id = 'codex-api'
    try:
        record = runtime.create(str(tmp_path), 'API runtime', model='gpt-5-nano')
        assert record.provider == 'codex-api'
        with pytest.raises(CodexRpcError):
            runtime.send(OwnSession(record.cli_session_id, record.title, record.cwd, provider='codex', started=True), 'Wrong route')
        runtime.send(record, 'Hello')
        wait(lambda: any(e.kind == 'finished' for e in events))
        assert runtime.snapshot(record.cli_session_id).outcome == 'completed'
    finally:
        runtime.close()


def test_launcher_keeps_api_home_and_key_out_of_child_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    captured = {}
    monkeypatch.setenv('OPENAI_API_KEY', FAKE)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'subscription'))
    monkeypatch.setenv('CODEX_SQLITE_HOME', str(tmp_path / 'subscription'))
    monkeypatch.setenv('ANTHROPIC_API_KEY', FAKE)
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'extra-ca'))
    monkeypatch.setattr(platform_paths, '_native_codex', lambda path: True)
    monkeypatch.setattr(platform_paths, 'ProcessTree', lambda: SimpleNamespace(
        popen_kwargs=lambda: {}, attach=lambda proc: None, close=lambda: None))
    def popen(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(stdin=None, stdout=None)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    home = tmp_path / 'api'
    platform_paths.start_codex_api_server('native-codex', home=home)
    assert captured['env']['CODEX_HOME'] == captured['env']['CODEX_SQLITE_HOME'] == str(home)
    assert FAKE not in str(captured['env']) and FAKE not in str(captured['args'])
    assert 'SSL_CERT_FILE' not in captured['env']
    assert 'forced_login_method="api"' in captured['args']
    assert 'cli_auth_credentials_store="ephemeral"' in captured['args']


def test_backup_excludes_the_entire_api_home_and_protected_openai_secret(tmp_path, monkeypatch):
    from thechatplace import backup
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path)
    (tmp_path / 'codex-api').mkdir()
    (tmp_path / 'codex-api' / 'history.json').write_text('synthetic')
    (tmp_path / 'openai-api-key.dpapi').write_text('protected synthetic secret')
    (tmp_path / 'openai-billing').mkdir()
    (tmp_path / 'openai-billing' / 'api-spend.jsonl').write_text('synthetic spending')
    names = [item.arcname for item in backup.tcp_inventory().items]
    assert not any('codex-api/' in name or name.endswith('.dpapi') for name in names)
    assert any('openai-billing/api-spend.jsonl' in name for name in names)


def test_anchored_copy_without_verified_counters_is_refused(connect, tmp_path):
    conn, events = connect()
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Source', model='gpt-5-nano')
    with pytest.raises(ApiCreditsRefused, match='baseline'):
        CodexOwnedThreads(conn).fork(proof.record().to_info(), 'Earlier copy', model='gpt-5-nano', last_turn_id='earlier')
    assert not events and conn.ledger.totals().held == 0


def test_reroute_invalidates_previous_baseline_for_continuation_and_restart(connect, tmp_path):
    conn, events = connect('rerouted')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Reroute', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'First priced turn')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 1)
    assert proof.native_id in json.loads(conn._baseline_path.read_text())
    runner.drain()
    runner.start(proof, 'Unknown rerouted turn')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    runner.drain()
    assert proof.native_id not in conn._totals
    assert proof.native_id not in json.loads(conn._baseline_path.read_text())
    assert conn.ledger.totals().spent == pytest.approx(.000061)
    assert conn.ledger.totals().held >= .499999
    with pytest.raises(ApiCreditsRefused, match='baseline'):
        runner.start(proof, 'Must not price unknown earlier tokens')
    restarted, _ = connect(ledger=conn.ledger)
    assert proof.native_id not in restarted._totals


def test_durable_baseline_is_invalidated_before_a_turn_can_crash(connect, tmp_path):
    conn, events = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Crash', model='gpt-5-nano')
    conn._persist_baseline(proof.native_id, conn._totals[proof.native_id])
    CodexTurnExecutor(conn).start(proof, 'Never completes')
    wait(lambda: any(e.kind == 'api_sent' for e in events))
    assert proof.native_id not in json.loads(conn._baseline_path.read_text())
    restarted, _ = connect(ledger=conn.ledger)
    assert proof.native_id not in restarted._totals


def test_queued_rpc_failure_keeps_hold_and_refuses_next_send(connect, tmp_path):
    conn, events = connect('rpc-error')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Ambiguous refusal', model='gpt-5-nano')
    params = {'threadId': proof.native_id, 'model': 'gpt-5-nano',
              'cwd': proof.cwd, 'approvalPolicy': proof.policy.approval,
              'approvalsReviewer': proof.policy.reviewer,
              'sandboxPolicy': proof.policy.turn_policy(), 'config': route_config(conn),
              'input': [{'type': 'text', 'text': 'Hello'}]}
    with pytest.raises(CodexRpcError):
        conn.request('turn/start', params)
    assert conn.ledger.totals().held == .5
    assert proof.native_id not in conn._totals
    with pytest.raises(ApiCreditsRefused, match='baseline'):
        conn.request('turn/start', params)
