import pytest

from thechatplace.providers.codex_usage import read_usage, usage_lines
from thechatplace.providers.codex_rpc import CodexRpcError, ReadOnlyCodexConnection


def bucket(percent=25):
    return {'primary': {'usedPercent': percent, 'windowDurationMins': 300, 'resetsAt': 1000}}


def test_named_buckets_take_precedence_over_legacy_duplicate():
    lines = usage_lines({'rateLimits': bucket(99), 'rateLimitsByLimitId': {
        'one': dict(bucket(), limitName='Codex'), 'two': bucket(50)}}, now=100)
    assert sum('5-hour limit' in line for line in lines) == 2
    assert any('25% used, 75% remaining' in line for line in lines)
    assert not any('99%' in line for line in lines)


@pytest.mark.parametrize('value', [None, True, -1, 101, float('nan'), 10**1000, '25'])
def test_missing_or_invalid_usage_is_not_zero(value):
    lines = usage_lines({'rateLimits': bucket(value)}, now=100)
    assert any('usage not reported' in line for line in lines)
    assert not any('0% used' in line for line in lines)


def test_missing_windows_and_control_character_labels_are_honest():
    lines = usage_lines({'rateLimits': {'limitName': 'private\x1btext'}})
    assert lines[-2:] == ['Codex:', 'Usage windows: not reported.']
    assert usage_lines({})[-1] == 'Codex usage limits: not reported.'


def test_read_is_subscription_only_and_never_starts_a_chat():
    class Connection:
        calls = []
        def request(self, method, params):
            self.calls.append(method)
            return ({'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
                    if method == 'account/read' else {'rateLimits': bucket()})
    connection = Connection()
    assert read_usage(connection)
    assert connection.calls == ['account/read', 'account/rateLimits/read']
    assert 'account/rateLimits/read' in ReadOnlyCodexConnection.METHODS


def test_other_auth_refused_before_quota_query():
    class Connection:
        def request(self, method, params):
            assert method == 'account/read'
            return {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True}
    with pytest.raises(CodexRpcError, match='ChatGPT'):
        read_usage(Connection())


def test_account_warning_thresholds_reset_and_named_buckets():
    from thechatplace.providers.codex_usage import usage_warnings
    response = {'rateLimits': bucket(100), 'rateLimitsByLimitId': {
        'main': dict(bucket(80), limitName='Codex', secondary={
            'usedPercent': 100, 'windowDurationMins': 10080, 'resetsAt': 2000})}}
    warnings = usage_warnings(response, now=100)
    assert len(warnings) == 2
    assert warnings[0].key == ('main', 'primary', 300, 1000, 80)
    assert warnings[1].key[-1] == 100
    assert '80% used, 20% remaining' in warnings[0].text
    assert '100% used, 0% remaining' in warnings[1].text
    assert 'resets' in warnings[0].text
    assert usage_warnings(response, now=3000) == ()


@pytest.mark.parametrize('value', [None, True, -1, 79.9, 101, float('nan'), float('inf'), 10**1000, '80'])
def test_unreported_invalid_or_low_account_usage_never_warns(value):
    from thechatplace.providers.codex_usage import usage_warnings
    assert usage_warnings({'rateLimits': bucket(value)}, now=100) == ()


def test_warning_identity_changes_only_at_threshold_or_reset():
    from thechatplace.providers.codex_usage import usage_warnings
    def warning(percent, reset=1000):
        data = bucket(percent)
        data['primary']['resetsAt'] = reset
        return usage_warnings({'rateLimits': data}, now=100)[0]
    assert warning(80).key == warning(99).key
    assert warning(100).key != warning(99).key
    assert warning(80, 2000).key != warning(80).key
    assert warning(80, None).key[-2] is None


def test_warning_labels_cannot_inject_terminal_controls():
    from thechatplace.providers.codex_usage import usage_warnings
    warning, = usage_warnings({'rateLimits': dict(bucket(90), limitName='private\x1bvalue')}, now=100)
    assert warning.text.startswith('Codex account usage:')
    assert 'private' not in warning.text


def test_warning_read_refuses_other_auth_before_quota_request():
    from thechatplace.providers.codex_usage import read_warnings
    class Connection:
        def request(self, method, params):
            assert method == 'account/read'
            return {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True}
    with pytest.raises(CodexRpcError, match='ChatGPT'):
        read_warnings(Connection())
