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
from thechatplace.providers.codex_api import CodexApiConnection, COUNTERS
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
                                                   defaults=spend_ledger.Limits())
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
    if scenario == 'malformed':
        assert conn.ledger.totals().held > 0
    else:
        assert .0648 <= conn.ledger.totals().spent < .065
        assert conn.ledger.totals().held == 0
        assert 'estimated, at most' in next(e for e in events if e.kind == 'finished').data['cost_words']


def test_no_usage_on_disconnect_is_held_after_restart(connect, tmp_path):
    conn, events = connect('missing')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Lost', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    wait(lambda: any(e.kind == 'api_sent' for e in events))
    conn.close()
    assert spend_ledger.SpendLedger(conn.ledger.folder).totals().held == 2


def test_unknown_model_native_inference_and_routing_override_never_send(connect, tmp_path):
    conn, events = connect()
    for method, params in [
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
    assert claude.limits() == spend_ledger.Limits(20, 2)
    assert api.limits() == spend_ledger.Limits(20, 2)
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
    monkeypatch.setenv('RUST_LOG', 'trace')
    monkeypatch.setenv('RUST_BACKTRACE', 'full')
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
    assert not any(k.startswith('RUST_') for k in captured['env'])
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


def test_reroute_invalidates_previous_baseline_and_recovery_uses_last(connect, tmp_path):
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
    assert conn.ledger.totals().held >= 1.999999
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


def test_queued_rpc_failure_keeps_hold_and_preserves_baseline(connect, tmp_path):
    conn, events = connect('rpc-error')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Ambiguous refusal', model='gpt-5-nano')
    params = {'threadId': proof.native_id, 'model': 'gpt-5-nano',
              'cwd': proof.cwd, 'approvalPolicy': proof.policy.approval,
              'approvalsReviewer': proof.policy.reviewer,
              'sandboxPolicy': proof.policy.turn_policy(), 'config': route_config(conn),
              'input': [{'type': 'text', 'text': 'Hello'}]}
    with pytest.raises(CodexRpcError):
        conn.request('turn/start', params)
    assert conn.ledger.totals().held == 2
    assert conn._totals[proof.native_id]['inputTokens'] == 0


@pytest.mark.parametrize('scenario', ['401', 'unauthorized'])
def test_refused_key_releases_hold_and_chat_can_send_again(connect, tmp_path, scenario):
    conn, events = connect(scenario)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Refused', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Refused request')
    wait(lambda: any(e.kind == 'finished' for e in events))
    runner.drain()
    assert conn.ledger.totals().held == conn.ledger.totals().spent == 0
    runner.start(proof, 'Replacement key works')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    assert conn.ledger.totals().spent == pytest.approx(.000061)
    assert conn.ledger.totals().held == 0


def test_stop_then_send_and_missing_baseline_recovery(connect, tmp_path):
    conn, events = connect()
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Recovery', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    for n in (1, 2):
        conn._totals.pop(proof.native_id, None)
        runner.start(proof, 'First usage establishes baseline')
        wait(lambda: len([e for e in events if e.kind == 'finished']) == n)
        runner.drain()
    assert conn.ledger.totals().spent == pytest.approx(.000122)


def test_retry_error_keeps_hold_even_when_turn_completes(connect, tmp_path):
    conn, events = connect('retry')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Retry', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hidden partial request')
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.ledger.totals().held > 0
    assert not next(e for e in events if e.kind == 'finished').data['cost_complete']


def test_chat_can_send_after_stop(connect, tmp_path):
    conn, events = connect('stop-once')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Stop then send', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Stop')
    wait(lambda: conn._totals[proof.native_id]['inputTokens'] > 0)
    runner.interrupt(proof)
    wait(lambda: any(e.kind == 'finished' for e in events))
    runner.drain()
    runner.start(proof, 'Continue')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    assert conn.ledger.totals().held == 0


def test_stop_timeout_does_not_close_another_chat(connect, tmp_path):
    conn, events = connect('unanswered-stop')
    first = CodexOwnedThreads(conn).start(str(tmp_path), 'Slow Stop', model='gpt-5-nano')
    second = CodexOwnedThreads(conn).start(str(tmp_path), 'Other chat', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(first, 'Hello')
    runner.start(second, 'Hello')
    conn._stop_timeout(first.native_id, conn._payments[first.native_id])
    assert not conn.closed and second.native_id in conn._payments
    assert conn.request('account/read', {})['account'] == {'type': 'apiKey'}


def test_cap_trips_stop_and_over_ceiling_reservation_stays_held(connect, tmp_path):
    conn, events = connect('cap')
    conn.ledger.set_limits(5, .01)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Cap', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Exceeds one cent')
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.ledger.totals().spent == pytest.approx(.061)
    assert not next(e for e in events if e.kind == 'finished').data['cost_complete']
    limit = next(e for e in events if e.kind == 'api_error')
    assert limit.data['failure'] == 'message_limit' and limit.data['overall'] is False


def test_cap_at_what_is_left_of_the_overall_limit_says_so(connect, tmp_path):
    # Only a cent overall, under 2 cents a message: the overall limit stopped
    # it, and the connection says so for the window to name it.
    conn, events = connect('cap')
    conn.ledger.set_limits(.01, .02)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Cap', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Exceeds one cent')
    wait(lambda: any(e.kind == 'finished' for e in events))
    limit = next(e for e in events if e.kind == 'api_error')
    assert limit.data['failure'] == 'message_limit'
    assert limit.data['overall'] is True and limit.data['others_held'] is False


def test_hyphenated_text_and_protocol_paths_are_not_scrubbed(connect, tmp_path):
    text = r'C:\Users\kelly\GitHub\task-runner risk-free ask-me disk-space C:\code\task-tracker'
    assert billing.scrub(text) == text
    from thechatplace.claude_cli import scrub_event, TurnEvent
    event = TurnEvent('text', text=text)
    assert scrub_event(event).text == text
    conn, _ = connect()
    path = tmp_path / 'task-tracker'
    path.mkdir()
    proof = CodexOwnedThreads(conn).start(str(path), 'ask-me', model='gpt-5-nano')
    assert proof.cwd == str(path)
    assert conn.request('thread/read', {'threadId': proof.native_id})['thread']['cwd'] == str(path)


def test_new_and_fork_baselines_are_durable(connect, tmp_path):
    conn, _ = connect()
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Empty', model='gpt-5-nano')
    copied = CodexOwnedThreads(conn).fork(proof.record().to_info(), 'Copy', model='gpt-5-nano')
    saved = json.loads(conn._baseline_path.read_text())
    assert saved[proof.native_id] == saved[copied.native_id] == dict.fromkeys(conn._totals[proof.native_id], 0)


def test_damaged_openai_limits_keep_openai_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path)
    monkeypatch.setattr(spend_ledger, '_OPENAI_DEFAULT', None)
    ledger = spend_ledger.default_ledger('openai')
    ledger.limits_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.limits_path.write_text('{')
    assert ledger.limits() == spend_ledger.Limits(20, 2) and ledger.can_spend()


def test_decreasing_counter_is_rejected_even_when_dollar_cost_rises(connect, tmp_path):
    conn, events = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Decreasing', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    wait(lambda: conn._totals[proof.native_id]['inputTokens'] == 1000)
    payment = conn._payments[proof.native_id]
    value = {'inputTokens': 900, 'cachedInputTokens': 200, 'outputTokens': 100, 'reasoningOutputTokens': 10}
    conn._receive({'method': 'thread/tokenUsage/updated', 'params': {'threadId': proof.native_id,
        'turnId': payment.turn_id, 'tokenUsage': {'total': value, 'last': value}}})
    assert payment.inconsistent
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert proof.native_id not in conn._totals and conn.ledger.totals().held > 0


def test_history_redacts_display_text_without_changing_identity():
    from thechatplace.providers.codex import transcript_from_thread, scrub_history
    history = transcript_from_thread({'turns': [{'id': 'task-turn', 'items': [
        {'id': 'task-reply', 'type': 'agentMessage', 'text': 'risk-free '+FAKE},
        {'id': 'task-tool', 'type': 'commandExecution', 'command': 'echo '+FAKE,
         'aggregatedOutput': FAKE, 'status': 'completed', 'exitCode': 0}]}]})
    ids = [m.uuid for m in history.messages]
    from thechatplace.tasklist import TaskStep
    history.tasks = [TaskStep('task-id', 'risk-free '+FAKE, active_form=FAKE)]
    scrub_history(history)
    assert all(FAKE not in m.whole for m in history.messages)
    assert ids == [m.uuid for m in history.messages]
    assert history.messages[0].text.startswith('risk-free ')
    assert FAKE not in history.tasks[0].subject + history.tasks[0].active_form


def test_timeout_claims_draining_before_releasing_payment_lock(connect, tmp_path, monkeypatch):
    conn, _ = connect('unanswered-stop')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Stopping', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    checked = []
    original_close = conn.close
    def close():
        assert conn._api_draining
        with pytest.raises(ApiCreditsRefused, match='connection is stopping'):
            conn.request('turn/start', {'threadId': 'unrelated', 'model': 'gpt-5-nano'})
        checked.append(True)
        original_close()
    monkeypatch.setattr(conn, 'close', close)
    conn._stop_timeout(proof.native_id, conn._payments[proof.native_id])
    assert checked
    monkeypatch.setattr(conn, 'close', original_close)


def test_delete_api_chat_removes_baseline_and_uses_api_stores(tmp_path, monkeypatch):
    from thechatplace.provider_storage import CodexPlanStore
    from thechatplace.providers.codex_permissions import CodexPermissionStore, CodexReviewStore
    removed = []
    for cls in (CodexPlanStore, CodexPermissionStore, CodexReviewStore):
        monkeypatch.setattr(cls, 'remove', lambda self, tid: removed.append(tid))
    store = OwnSessionStore(tmp_path / 'sessions.json')
    store.add(OwnSession('api-chat', 'API', str(tmp_path), provider='codex-api', billing='api'))
    folder = tmp_path / 'openai-billing'
    folder.mkdir()
    path = folder / 'codex-token-baselines.json'
    path.write_text(json.dumps({'api-chat': dict.fromkeys(COUNTERS, 0), 'other': {}}))
    store.remove('api-chat', 'codex-api')
    assert removed == ['api-chat'] * 3
    assert json.loads(path.read_text()) == {'other': {}}


def test_immediate_resend_rechecks_draining_after_previous_settlement(connect, tmp_path, monkeypatch):
    import threading
    conn, _ = connect('unanswered-stop')
    owned = CodexOwnedThreads(conn)
    first = owned.start(str(tmp_path), 'Timed out', model='gpt-5-nano')
    second = owned.start(str(tmp_path), 'Just ended', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(first, 'Hello')
    runner.start(second, 'Hello')
    conn._stop_timeout(first.native_id, conn._payments[first.native_id])
    conn._payments[second.native_id].terminal = 'completed'
    closed = threading.Event()
    original_close = conn.close
    monkeypatch.setattr(conn, 'close', closed.set)
    with pytest.raises(ApiCreditsRefused, match='connection is stopping'):
        conn.request('turn/start', {'threadId': second.native_id, 'model': 'gpt-5-nano'})
    assert closed.wait(2)
    monkeypatch.setattr(conn, 'close', original_close)


def test_key_replacement_reauthenticates_existing_connection_and_removal_blocks_send(connect, tmp_path, monkeypatch):
    kept = {}
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path)
    monkeypatch.setattr(platform_paths, 'save_secret', lambda name, key: kept.update({name: key}))
    monkeypatch.setattr(platform_paths, 'delete_secret', lambda name: kept.pop(name, None))
    openai_billing.save_api_key(FAKE)
    conn, events = connect()
    loaded = []
    def key_loader():
        key = kept.get(platform_paths.OPENAI_API_KEY_SECRET)
        loaded.append(key)
        return key
    conn._key_loader = key_loader
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Changed key', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'First')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 1)
    runner.drain()
    other = 'sk-proj-' + '2' * 30
    openai_billing.save_api_key(other)
    runner.start(proof, 'Replacement key')
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    runner.drain()
    assert loaded == [other]
    openai_billing.remove_api_key()
    with pytest.raises(ApiCreditsRefused, match='Add an OpenAI API key'):
        runner.start(proof, 'Must not send with a removed key')
    assert len([e for e in events if e.kind == 'api_sent']) == 2
    assert conn.ledger.totals().held == 0


def test_api_model_change_uses_priced_catalog_when_native_catalog_is_empty(connect, tmp_path):
    from thechatplace.providers.codex_runtime import CodexChatRuntime
    conn, _ = connect()
    runtime = CodexChatRuntime(factory=lambda exe: conn)
    runtime.provider_id = 'codex-api'
    try:
        record = runtime.create(str(tmp_path), 'Priced models', model='gpt-5-nano')
        change = runtime.prepare_settings(record, 'gpt-5.4-nano', None)
        assert change.record().provider_options['model'] == 'gpt-5.4-nano'
    finally:
        runtime.close()


def test_unknown_and_known_chat_cost_words_use_reservations(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path, defaults=spend_ledger.Limits())
    chat = 'codex-api:unknown'
    held = ledger.reserve(chat)
    ledger.close(held, complete=False)
    assert openai_billing.chat_cost_words(ledger, chat) == 'cost not known'
    ledger.settle(held, .06)
    assert openai_billing.chat_cost_words(ledger, chat) == 'estimated at least 6 cents'
    ledger.close(held, complete=True)
    assert openai_billing.chat_cost_words(ledger, chat) == 'estimated 6 cents'


def test_key_test_runs_normal_backend_and_counts_its_estimated_cost(connect, tmp_path, monkeypatch):
    from thechatplace.providers.codex_api import OpenAiKeyTestRunner
    monkeypatch.setattr(platform_paths, 'codex_api_home', lambda: tmp_path)
    conn, _ = connect()
    results = []
    def factory(**kwargs):
        conn._api_event = kwargs['on_event']
        conn._purpose = kwargs['purpose']
        return conn
    runner = OpenAiKeyTestRunner(results.append, ledger=conn.ledger, connection_factory=factory)
    runner.start()
    wait(lambda: results)
    assert results[-1].kind == 'finished' and not results[-1].is_error, results[-1].text
    assert results[-1].data['cost_words'].startswith('estimated ')
    assert conn.ledger.totals().spent > 0 and conn.ledger.totals().held == 0
    assert '"purpose":"test"' in conn.ledger.path.read_text()


def test_ephemeral_permission_probe_cannot_become_a_normal_paid_chat(connect, tmp_path):
    conn, _ = connect()
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Probe', ephemeral=True,
        model='gpt-5-nano', approval_policy='never')
    with pytest.raises(CodexRpcError, match='key test configuration'):
        conn.accept_key_test(proof)
    assert not conn.owns_runtime(proof)


def test_retry_does_not_send_an_interrupt(connect, tmp_path, monkeypatch):
    conn, events = connect('retry')
    interrupted = []
    original = conn._interrupt_payment
    monkeypatch.setattr(conn, '_interrupt_payment', lambda *a: interrupted.append(a))
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Retry', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Retry can complete')
    wait(lambda: any(e.kind == 'finished' for e in events))
    runner.drain()
    assert runner.snapshot(proof).status == 'completed'
    assert not interrupted and conn.ledger.totals().held > 0


def test_usage_after_completion_grace_is_settled(connect, tmp_path):
    conn, events = connect('late-usage')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Late usage', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.ledger.totals().held > 0
    wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    assert conn.ledger.totals().spent == pytest.approx(.000061)
    assert conn.ledger.totals().held == 0


def test_late_old_usage_does_not_restore_active_turn_baseline_or_double_charge(connect, tmp_path, monkeypatch):
    conn, events = connect('late-usage')
    import threading
    closing, release = threading.Event(), threading.Event()
    original_close = conn.ledger.close
    def close(reservation, complete):
        if complete and conn.ledger.totals().spent >= .000122:
            # Force the snapshot CI observed: settlement is appended, but
            # the reservation has not closed. Spending alone isn't completion.
            closing.set()
            assert release.wait(8), 'Test did not release the final close'
        return original_close(reservation, complete)
    monkeypatch.setattr(conn.ledger, 'close', close)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Overlap', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'First')
    wait(lambda: any(e.kind == 'finished' for e in events))
    runner.drain()
    runner.start(proof, 'Second before first usage')
    # Turn one's late report must not restore a crash-unsafe baseline while
    # turn two has not yet received usage or ended its accounting window.
    wait(lambda: conn.ledger.totals().spent > 0)
    assert proof.native_id not in json.loads(conn._baseline_path.read_text())
    try:
        wait(closing.is_set)
        intermediate = conn.ledger.totals()
        assert intermediate.spent == pytest.approx(.000122) and intermediate.held > 0
    finally:
        release.set()
    wait(lambda: conn.ledger.totals().held == 0)
    assert conn.ledger.totals().spent == pytest.approx(.000122)


def test_slow_stop_can_send_again_while_other_chat_is_still_running(connect, tmp_path):
    conn, events = connect('delayed-stop')
    conn.STOP_TIMEOUT = .05
    first = CodexOwnedThreads(conn).start(str(tmp_path), 'Slow', model='gpt-5-nano')
    second = CodexOwnedThreads(conn).start(str(tmp_path), 'Still working', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(first, 'One')
    runner.start(second, 'Two')
    runner.interrupt(first)
    wait(lambda: any(e.kind == 'api_error' and e.data.get('failure') == 'stop_pending' for e in events))
    wait(lambda: first.native_id not in conn._payments)
    runner.drain()
    assert runner.snapshot(first).status == 'interrupted'
    assert second.native_id in conn._payments and not conn.closed
    runner.start(first, 'Send again now')
    wait(lambda: any(e.kind == 'finished' and e.session_id == first.native_id and e.data.get('outcome') == 'completed' for e in events))
    assert second.native_id in conn._payments and not conn.closed
    assert conn.ledger.totals().held > 0  # unknown stopped request stays held


def test_login_generation_recovers_all_thread_lifecycle_methods(connect, tmp_path, monkeypatch):
    kept = {'key': FAKE}
    monkeypatch.setattr(openai_billing, 'key_generation', lambda: kept.get('generation', 0))
    conn, _ = connect()
    conn._key_loader = lambda: kept.get('key')
    owned = CodexOwnedThreads(conn)
    proof = owned.start(str(tmp_path), 'Existing', model='gpt-5-nano')
    kept.update(key=None, generation=1)
    with pytest.raises(ApiCreditsRefused):
        owned.start(str(tmp_path), 'Removed', model='gpt-5-nano')
    kept.update(key=FAKE, generation=2)
    assert owned.start(str(tmp_path), 'Added again', model='gpt-5-nano')
    assert owned.resume(proof.record())
    assert owned.fork(proof.record().to_info(), 'Copy', model='gpt-5-nano')


def test_ephemeral_key_test_never_persists_a_token_baseline(connect, tmp_path):
    conn, _ = connect()
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Test', model='gpt-5-nano', ephemeral=True)
    assert not conn._baseline_path.exists() or proof.native_id not in json.loads(conn._baseline_path.read_text())


def test_streaming_split_keys_are_scrubbed_after_accumulation(connect, tmp_path):
    conn, _ = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Chunks', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hello')
    for offset in range(1, len(FAKE)):
        item = 'split-' + str(offset)
        for chunk in (FAKE[:offset], FAKE[offset:]):
            conn._receive({'method': 'item/agentMessage/delta', 'params': {
                'threadId': proof.native_id, 'turnId': 'turn-1', 'itemId': item, 'delta': chunk}})
            runner.drain()
            assert FAKE not in str(runner.snapshot(proof).replies)
        assert '(hidden)' in dict(runner.snapshot(proof).replies)[item]


def test_refusal_cost_words_and_friendly_service_names(connect, tmp_path):
    conn, events = connect('401')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Bad key', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    wait(lambda: any(e.kind == 'finished' for e in events))
    report = next(e.data for e in events if e.kind == 'finished')
    assert report['cost_words'] == 'nothing was charged'
    assert billing.cost_sentence(report['cost_words']) == 'Nothing was charged.'
    assert conn._service_name('thread/memoryMode/set') == 'memory'
    assert 'estimated at 3 cents' in billing.cost_sentence('estimated 3 cents')


def test_delete_with_damaged_baselines_succeeds(tmp_path):
    store = OwnSessionStore(tmp_path / 'sessions.json')
    store.add(OwnSession('damaged', 'Delete', str(tmp_path), provider='codex-api', billing='api'))
    path = tmp_path / 'openai-billing' / 'codex-token-baselines.json'
    path.parent.mkdir()
    path.write_text('{broken')
    store.remove('damaged', 'codex-api')
    assert store.get('damaged', provider='codex-api') is None


def test_known_baseline_prices_all_usage_not_only_last_request(connect, tmp_path):
    conn, events = connect('multi-first')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Multiple requests', model='gpt-5-nano')
    CodexTurnExecutor(conn).start(proof, 'Hello')
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.ledger.totals().spent == pytest.approx(.000122)
    assert conn.ledger.totals().held == 0



def test_deferred_api_context_survives_store_reload(tmp_path):
    store = OwnSessionStore(tmp_path / 'sessions.json')
    record = OwnSession('copy', 'Copy', str(tmp_path), provider='codex-api', billing='api',
                        api_context='Quoted conversation, not permissions.\n')
    store.add(record)
    assert OwnSessionStore(store.path).get('copy', provider='codex-api').api_context == record.api_context


def test_deferred_context_preserves_native_parts_even_when_long(tmp_path):
    from thechatplace.providers.codex_input import CodexInput, LongCodexInput, prepend_context
    skill = tmp_path / 'SKILL.md'
    skill.write_text('Synthetic skill')
    part = {'type': 'skill', 'path': str(skill), 'name': 'test'}
    original = CodexInput('My message', [part])
    assert prepend_context(original, '') is original
    with_context = prepend_context(original, 'Quoted history.\n')
    assert with_context.items()[1:] == [part]
    assert str(with_context) == 'Quoted history.\nMy message'
    long = prepend_context(original, 'x'*32000)
    assert isinstance(long, LongCodexInput) and list(long.parts) == [part]


@pytest.mark.parametrize('later_reported', [False, True])
def test_late_increment_does_not_get_charged_again_by_next_turn(connect, tmp_path, later_reported):
    conn, events = connect('normal')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Incremental late usage', model='gpt-5-nano')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'First request')
    wait(lambda: conn._payments[proof.native_id].usage)
    wait(lambda: any(e.kind == 'finished' for e in events))
    runner.drain()
    # A late incremental report follows the next turn's initial usage.
    runner.start(proof, 'Second request')
    wait(lambda: conn._payments[proof.native_id].usage)
    later = conn._payments[proof.native_id]
    if later_reported:
        conn._finish_payment(proof.native_id, later)
    total = lambda n: dict(inputTokens=1000*n, cachedInputTokens=200*n, outputTokens=50*n, reasoningOutputTokens=10*n)
    conn._receive({'method': 'thread/tokenUsage/updated', 'params': {
        'threadId': proof.native_id, 'turnId': 'turn-1',
        'tokenUsage': {'total': total(2), 'last': total(1)}}})
    # Later's already-reported cumulative2000 is now correctly zero delta;
    # its final cumulative3000 adds only its own one request.
    conn._receive({'method': 'thread/tokenUsage/updated', 'params': {
        'threadId': proof.native_id, 'turnId': 'turn-2',
        'tokenUsage': {'total': total(3), 'last': total(1)}}})
    assert conn.ledger.totals().spent == pytest.approx(.000183)
    assert not conn.ledger.totals().problem
    assert spend_ledger.SpendLedger(conn.ledger.folder).totals().spent == pytest.approx(.000183)


def test_overlap_correction_preserves_reset_and_refuses_excess_credit(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path)
    r = ledger.reserve('codex-api:chat')
    ledger.settle(r, .03, {'gpt-5-nano': .03})
    ledger.close(r, True)
    ledger.reset()
    assert ledger.correct_openai_overlap(r, .01, 'gpt-5-nano')
    totals = ledger.totals()
    assert totals.spent == 0 and totals.chat_all_time[r.chat_id] == pytest.approx(.02)
    assert not totals.problem
    ledger.correct_openai_overlap(r, .10, 'gpt-5-nano')
    assert ledger.totals().problem


@pytest.mark.parametrize('days', [1, 8])
def test_overlap_correction_is_attributed_to_original_settlement_day(tmp_path, days):
    now = time.time()
    recorded = now - days*86400
    clock = [recorded]
    ledger = spend_ledger.SpendLedger(tmp_path, clock=lambda: clock[0])
    r = ledger.reserve('codex-api:day-test')
    ledger.settle(r, .03, {'gpt-5-nano': .03})
    ledger.close(r, True)
    clock[0] = now
    assert ledger.correct_openai_overlap(r, .01, 'gpt-5-nano')
    totals = ledger.totals(now=now)
    assert totals.spent == pytest.approx(.02) and totals.today == 0
    assert totals.last_7_days == pytest.approx(.02 if days == 1 else 0)
    assert not totals.problem


@pytest.mark.parametrize('scenario', ['normal', 'duplicate', 'hang', 'missing', 'late-usage'])
def test_unpriced_tokens_persist_and_allowance_stays_held(connect, tmp_path, scenario):
    conn, events = connect(scenario)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Unknown price', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hello')
    wait(lambda: any(e.kind == 'api_sent' for e in events))
    if scenario in ('hang', 'missing'):
        runner.interrupt(proof)
    wait(lambda: any(e.kind == 'finished' for e in events))
    report = next(e.data for e in events if e.kind == 'finished')
    assert report['unpriced'] and report['cost_usd'] is None
    assert not report['cost_complete'] and not report['cost_upper_bound']
    if scenario == 'late-usage':
        wait(lambda: len([e for e in events if e.kind == 'finished']) == 2)
    totals = spend_ledger.SpendLedger(conn.ledger.folder).totals()
    assert totals.spent == 0 and totals.held == totals.unpriced_allowance == 2
    tokens = totals.unpriced_all_time[proof.record().to_info().key]
    assert tokens['inputTokens'] == (0 if scenario == 'missing' else 1000)
    assert 'unpriced usage' in openai_billing.chat_cost_words(conn.ledger, proof.record().to_info().key)
    if scenario == 'hang':
        assert 'allowance kept' in report['cost_words']
    conn.ledger.release_held()
    assert conn.ledger.totals().held == 0
    assert proof.record().to_info().key in conn.ledger.totals().unpriced_all_time


def test_unpriced_uses_up_overall_allowance(connect, tmp_path):
    conn, events = connect()
    conn.ledger.set_limits(4, 2)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Allowance', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    runner = CodexTurnExecutor(conn)
    for n in (1, 2):
        runner.start(proof, 'Hello')
        wait(lambda: len([e for e in events if e.kind == 'finished']) == n)
        runner.drain()
    with pytest.raises(ApiCreditsRefused, match='reached your limit'):
        runner.start(proof, 'Blocked')
    totals = conn.ledger.totals()
    assert totals.counted == 4 and totals.spent == 0
    assert totals.unpriced_usage[proof.record().to_info().key]['inputTokens'] == 2000


def test_standard_service_is_pinned_despite_priority_default(connect, tmp_path, monkeypatch):
    conn, events = connect()
    writes = []
    encode = conn._encode
    def capture(message):
        if message.get('method') in ('thread/start', 'turn/start'):
            writes.append(message)
        return encode(message)
    monkeypatch.setattr(conn, '_encode', capture)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Standard', model='gpt-6-luna')
    CodexTurnExecutor(conn).start(proof, 'Hi')
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert len(writes) == 2
    assert all(m['params']['serviceTier'] == 'default' for m in writes)
    assert all(m['params']['config']['service_tier'] == 'default' for m in writes)
    with pytest.raises(ApiCreditsRefused, match='standard service'):
        conn.request('turn/start', {'threadId': proof.native_id, 'model': proof.model, 'serviceTier': 'priority'})


def test_unpriced_token_snapshots_reset_rename_and_overlap(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path, defaults=spend_ledger.Limits())
    r = ledger.reserve('codex-api:old', unpriced=True)
    counters = dict(inputTokens=100, cachedInputTokens=20, cacheWriteInputTokens=10,
                    outputTokens=30, reasoningOutputTokens=5)
    assert ledger.record_openai_tokens(r, 'future', counters, unpriced=True)
    assert ledger.record_openai_tokens(r, 'future', counters, unpriced=True)
    ledger.close(r, complete=False)
    assert ledger.totals().unpriced_usage[r.chat_id] == counters
    ledger.rename_chat(r.chat_id, 'codex-api:new')
    assert ledger.totals().unpriced_all_time['codex-api:new'] == counters
    ledger.reset()
    assert not ledger.totals().unpriced_usage
    assert ledger.totals().unpriced_all_time['codex-api:new'] == counters
    # A late corrected absolute snapshot changes history without double-counting.
    assert ledger.record_openai_tokens(r, 'future', {**counters, 'inputTokens': 80}, unpriced=True)
    assert ledger.totals().unpriced_all_time['codex-api:new']['inputTokens'] == 80
    assert not ledger.totals().unpriced_usage


def test_active_promotional_expiry_keeps_unknown_allowance(connect, tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(openai_prices, 'pricing_time', lambda: datetime(2026, 11, 29, 9, tzinfo=timezone.utc))
    conn, events = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Expiry', model='gpt-5.6-sol')
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hi')
    wait(lambda: conn._payments[proof.native_id].usage)
    monkeypatch.setattr(openai_prices, 'pricing_time', lambda: datetime(2026, 11, 29, 10, tzinfo=timezone.utc))
    runner.interrupt(proof)
    wait(lambda: any(e.kind == 'finished' for e in events))
    report = next(e.data for e in events if e.kind == 'finished')
    assert report['unpriced'] and report['cost_usd'] is None
    assert conn.ledger.totals().held > 0
    assert 'unpriced usage' in openai_billing.spending_words(conn.ledger.totals())


def test_missing_unpriced_tokens_are_not_measured_zero(connect, tmp_path):
    conn, events = connect('missing')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Missing', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    CodexTurnExecutor(conn).start(proof, 'Hi')
    wait(lambda: any(e.kind == 'api_sent' for e in events))
    conn.close()
    words = openai_billing.chat_cost_words(spend_ledger.SpendLedger(conn.ledger.folder), proof.record().to_info().key)
    assert 'tokens not reported' in words and 'used 0 tokens' not in words


def test_unpriced_steers_also_reserve_message_allowances(connect, tmp_path):
    conn, events = connect('hang')
    conn.ledger.set_limits(4, 2)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Steer allowances', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hi')
    wait(lambda: conn._payments[proof.native_id].usage)
    runner.drain()
    runner.steer(proof, 'One short addition')
    assert conn.ledger.totals().unpriced_allowance == 4
    assert conn.ledger.totals().releasable == 0
    with pytest.raises(ApiCreditsRefused, match='reached your limit'):
        runner.steer(proof, 'No allowance left')
    runner.interrupt(proof)
    wait(lambda: any(e.kind == 'finished' for e in events))
    assert conn.ledger.totals().releasable == 4


def test_effective_ultra_cannot_bypass_blocked_automatic_delegation(connect, tmp_path):
    conn, events = connect('default-ultra')
    with pytest.raises(ApiCreditsRefused, match='Automatic task delegation'):
        CodexOwnedThreads(conn).start(str(tmp_path), 'Default ultra', model='future-model')
    assert not events and conn.ledger.totals().counted == 0


def test_unpriced_crash_before_first_token_record_stays_unknown_after_release(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path, defaults=spend_ledger.Limits())
    r = ledger.reserve('codex-api:crash-window', unpriced=True)
    restarted = spend_ledger.SpendLedger(tmp_path)
    assert 'tokens not reported' in openai_billing.spending_words(restarted.totals())
    restarted.release_held()
    assert restarted.totals().held == 0
    assert 'unpriced usage' in openai_billing.spending_words(restarted.totals())
    # A proven unsent reservation does not invent usage.
    unsent = ledger.reserve('codex-api:unsent', unpriced=True)
    ledger.close(unsent, complete=True)
    assert unsent.chat_id not in ledger.totals().unpriced_all_time


def test_measured_steer_tokens_are_counted_once_without_missing_markers(connect, tmp_path):
    conn, events = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Measured steer', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    runner = CodexTurnExecutor(conn)
    runner.start(proof, 'Hi')
    wait(lambda: conn._payments[proof.native_id].usage)
    runner.drain()
    runner.steer(proof, 'Addition')
    turn = conn._payments[proof.native_id].turn_id
    counters = dict(inputTokens=2000, cachedInputTokens=400, cacheWriteInputTokens=0,
                    outputTokens=100, reasoningOutputTokens=20)
    conn._receive({'method': 'thread/tokenUsage/updated', 'params': {
        'threadId': proof.native_id, 'turnId': turn, 'tokenUsage': {'total': counters, 'last': counters}}})
    conn._receive({'method': 'turn/completed', 'params': {
        'threadId': proof.native_id, 'turn': {'id': turn, 'status': 'completed'}}})
    wait(lambda: any(e.kind == 'finished' for e in events))
    restarted = spend_ledger.SpendLedger(conn.ledger.folder)
    totals = restarted.totals()
    assert totals.held == totals.unpriced_allowance == 4
    assert totals.unpriced_all_time[proof.record().to_info().key] == counters
    assert not totals.unpriced_missing_all_time
    restarted.release_held()
    words = openai_billing.spending_words(restarted.totals())
    assert 'used 2,100 tokens' in words and 'not reported' not in words


def test_fast_usage_cannot_be_overwritten_by_late_sent_callback(connect, tmp_path, monkeypatch):
    import threading
    conn, events = connect()
    measured = threading.Event()
    record = conn.ledger.record_openai_tokens
    def tokens(reservation, model, counters, **kwargs):
        result = record(reservation, model, counters, **kwargs)
        if counters['inputTokens']:
            measured.set()
        return result
    monkeypatch.setattr(conn.ledger, 'record_openai_tokens', tokens)
    put = conn._outgoing.put
    def outgoing(message):
        if isinstance(message, tuple):
            payload, callback = message
            def delayed():
                assert measured.wait(4), 'Usage did not arrive before the send callback'
                callback()
            message = (payload, delayed)
        put(message)
    monkeypatch.setattr(conn._outgoing, 'put', outgoing)
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Fast usage', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    CodexTurnExecutor(conn).start(proof, 'Hi')
    wait(lambda: any(e.kind == 'finished' for e in events))
    totals = conn.ledger.totals()
    assert totals.unpriced_all_time[proof.record().to_info().key]['inputTokens'] == 1000
    assert not totals.unpriced_missing_all_time


def test_unpriced_confirmed_first_key_refusal_does_not_invent_spending(connect, tmp_path):
    conn, events = connect('401')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Refused', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    CodexTurnExecutor(conn).start(proof, 'Hi')
    wait(lambda: any(e.kind == 'finished' for e in events))
    totals = conn.ledger.totals()
    assert totals.held == totals.spent == 0
    assert not totals.unpriced_all_time


def test_unpriced_partial_usage_before_key_refusal_keeps_allowance(connect, tmp_path):
    conn, events = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Partial refusal', model='future-model')
    conn.authorize_unpriced(proof.native_id, proof.model)
    CodexTurnExecutor(conn).start(proof, 'Hi')
    wait(lambda: conn._payments[proof.native_id].usage)
    turn = conn._payments[proof.native_id].turn_id
    conn._receive({'method': 'error', 'params': {'threadId': proof.native_id, 'turnId': turn,
                  'willRetry': False, 'error': {'codexErrorInfo': 'unauthorized'}}})
    wait(lambda: any(e.kind == 'finished' for e in events))
    totals = conn.ledger.totals()
    assert totals.held == totals.unpriced_allowance == 2
    assert proof.record().to_info().key in totals.unpriced_all_time


def test_backend_refuses_unpriced_turn_without_exact_consent(connect, tmp_path):
    conn, events = connect()
    first = CodexOwnedThreads(conn).start(str(tmp_path), 'Consent', model='future-model')
    second = CodexOwnedThreads(conn).start(str(tmp_path), 'Other chat', model='future-model')
    runner = CodexTurnExecutor(conn)
    with pytest.raises(ApiCreditsRefused, match='Confirm the price-unknown question'):
        runner.start(first, 'Must not send')
    assert conn.ledger.totals().counted == 0 and not events
    conn.authorize_unpriced(second.native_id, second.model)
    with pytest.raises(ApiCreditsRefused, match='Confirm the price-unknown question'):
        runner.start(first, 'Another chat cannot authorize it')
    conn.authorize_unpriced(first.native_id, first.model)
    runner.start(first, 'Allowed')
    wait(lambda: any(e.kind == 'finished' for e in events))


def test_queued_messages_after_expiry_return_unsent_with_confirmation_reason(connect, tmp_path, monkeypatch):
    from datetime import timedelta
    from thechatplace.providers.codex_runtime import CodexChatRuntime
    cutoff = openai_prices.EXPIRES['gpt-5.6-sol']
    monkeypatch.setattr(openai_prices, 'pricing_time', lambda: cutoff-timedelta(seconds=1))
    conn, events = connect('hang')
    updates = []
    runtime = CodexChatRuntime(factory=lambda exe: conn, on_update=updates.append)
    runtime.provider_id = 'codex-api'
    try:
        record = runtime.create(str(tmp_path), 'Expiry queue', model='gpt-5.6-sol')
        runtime.send(record, 'First')
        wait(lambda: conn._payments[record.cli_session_id].usage)
        assert runtime.send(record, 'Second') and runtime.send(record, 'Third')
        monkeypatch.setattr(openai_prices, 'pricing_time', lambda: cutoff)
        turn = conn._payments[record.cli_session_id].turn_id
        request = conn.request
        def idle_metadata(method, params=None, **kwargs):
            result = request(method, params, **kwargs)
            if method == 'thread/read':
                result['thread']['status'] = {'type': 'idle'}
            return result
        monkeypatch.setattr(conn, 'request', idle_metadata)
        conn._receive({'method': 'turn/completed', 'params': {
            'threadId': record.cli_session_id, 'turn': {'id': turn, 'status': 'completed'}}})
        wait(lambda: any(s.unsent for s in updates))
        returned = [s for s in updates if s.unsent][-1]
        assert returned.unsent == ('Second', 'Third')
        assert 'Confirm the price-unknown question' in returned.notice
        assert runtime.ready(record) and not runtime.snapshot(record.cli_session_id).queued
        assert len([e for e in events if e.kind == 'api_sent']) == 1
    finally:
        runtime.close()


def test_expiry_update_keeps_running_and_records_only_new_unpriced_tokens(connect, tmp_path, monkeypatch):
    from datetime import timedelta
    cutoff = openai_prices.EXPIRES['gpt-5.6-sol']
    monkeypatch.setattr(openai_prices, 'pricing_time', lambda: cutoff-timedelta(seconds=1))
    conn, events = connect('hang')
    proof = CodexOwnedThreads(conn).start(str(tmp_path), 'Running expiry', model='gpt-5.6-sol')
    CodexTurnExecutor(conn).start(proof, 'Hi')
    wait(lambda: conn._payments[proof.native_id].usage)
    payment = conn._payments[proof.native_id]
    spent = conn.ledger.totals().spent
    monkeypatch.setattr(openai_prices, 'pricing_time', lambda: cutoff)
    counters = dict(inputTokens=2000, cachedInputTokens=400, cacheWriteInputTokens=0,
                    outputTokens=100, reasoningOutputTokens=20)
    conn._receive({'method': 'thread/tokenUsage/updated', 'params': {
        'threadId': proof.native_id, 'turnId': payment.turn_id,
        'tokenUsage': {'total': counters, 'last': counters}}})
    assert payment.tokens == counters and not payment.inconsistent and not payment.stopping
    totals = conn.ledger.totals()
    assert totals.spent == spent
    assert totals.unpriced_all_time[proof.record().to_info().key]['inputTokens'] == 1000
    assert totals.unpriced_all_time[proof.record().to_info().key]['outputTokens'] == 50
