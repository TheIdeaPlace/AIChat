from thechatplace import bugreport, codex_diagnostics, platform_paths
from thechatplace.speech import SpeechSettings
from thechatplace.providers.codex_rpc import CodexTurnRejected, CodexConnectionFailure


def test_known_refusal_reports_only_a_fixed_reason(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    error = CodexTurnRejected('not_idle')
    error.args = ('private command and credentials',)
    codex_diagnostics.record_failure(error)
    assert 'not_idle' in path.read_text() and 'private' not in path.read_text()
    assert 'still working on a turn' in codex_diagnostics.last_failure()


def test_stream_failure_reason_is_specific_without_logging_payload(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    error = CodexConnectionFailure('unsupported_request')
    error.args = ('private request command and credentials',)
    codex_diagnostics.record_failure(error)
    assert 'unsupported_request' in path.read_text() and 'private' not in path.read_text()
    assert 'unsupported approval or question' in codex_diagnostics.last_failure()


def test_failure_log_and_bug_report_never_include_exception_contents(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    codex_diagnostics.record_failure(ValueError('private token and /Users/private/chat'))
    assert 'ValueError' in path.read_text() and 'private' not in path.read_text()
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version='test'))
    assert facts['Last Codex error'] == 'Codex worker failed: ValueError; details redacted.'
    path.write_text('Forged private contents\n')
    assert not codex_diagnostics.last_failure()
    assert 'Last Codex error' not in dict(bugreport.environment(SpeechSettings(), {}, claude_version='test'))


def test_diagnostics_are_bounded_and_do_not_block_when_logging_fails(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    path.write_text('x' * 70000)
    codex_diagnostics.record_failure(RuntimeError('secret'))
    assert path.stat().st_size < 65536
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: (_ for _ in ()).throw(OSError('No log')))
    codex_diagnostics.record_failure(ValueError('secret'))
    assert codex_diagnostics.last_failure() == ''
