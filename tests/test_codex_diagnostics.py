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
    assert facts['Codex error'] == 'just now: ValueError; details redacted.'
    path.write_text('Forged private contents\n')
    assert not codex_diagnostics.last_failure()
    assert 'Codex error' not in dict(bugreport.environment(SpeechSettings(), {}, claude_version='test'))


def _copilot_error(text):
    from thechatplace.providers.copilot_runtime import CopilotChatError
    return CopilotChatError(text)


def test_a_copilot_failure_keeps_the_reason_the_app_wrote(tmp_path, monkeypatch):
    """#463 got "CopilotChatError; details redacted." instead of the words
    the user saw; they're app-written (#370), so the report has them (#464)."""
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    from thechatplace.providers.copilot_runtime import IN_USE
    codex_diagnostics.record_failure(_copilot_error(IN_USE))
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version='test'))
    assert facts['Copilot error'] == f'just now: CopilotChatError: {IN_USE}'
    assert 'Codex error' not in facts


def test_a_copilot_reason_too_long_or_with_control_characters_is_redacted(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    for text in ('x' * 1001, 'Line one\nLine two', 'Bell\x07', 'Escape \x1b[31m',
                 # Copilot's own error text can carry a path, an address or a link.
                 'Couldn\'t open C:\\Users\\kelly\\project', 'No such file /home/kelly/x',
                 'Signed in as kelly@example.com', 'See https://github.com/acme/x',
                 'Folder ~/work isn\'t there', 'Key in /.ssh', 'Host github.acme-corp.com',
                 'Drive C:foo', 'mailto:kelly', 'sftp:h', 'Mail ＠x', 'At ／home',
                 'At ∕home', 'Delete\x7fhere', 'Line break', 'Next\x85line'):
        path.unlink(missing_ok=True)
        codex_diagnostics.record_failure(_copilot_error(text))
        written = path.read_text()
        assert written.endswith('Copilot worker failed: CopilotChatError; details redacted.\n'), text
        assert codex_diagnostics.last_failure() == \
            'Copilot CopilotChatError; details redacted.'
    path.unlink()
    codex_diagnostics.record_failure(_copilot_error('y' * 1000))
    assert codex_diagnostics.last_failure() == 'Copilot CopilotChatError: ' + 'y' * 1000


def test_every_copilot_message_the_app_writes_is_kept_unless_it_names_a_place():
    """The privacy check mustn't hide the app's own words: only the hooks
    refusal, which can name a folder, is a place."""
    import ast
    import pathlib
    root = pathlib.Path(codex_diagnostics.__file__).parent / 'providers'
    texts = []
    for name in ('copilot_runtime.py', 'copilot.py', 'copilot_acp.py', 'copilot_hooks.py'):
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and len(node.value) > 30 and ' ' in node.value and '{' not in node.value:
                texts.append(node.value)
    hidden = [t for t in texts if codex_diagnostics._PRIVATE.search(t)]
    for text in hidden:
        # A file name reads like a host name; hiding it costs only detail.
        assert any(word in text for word in ('/', '\\', 'http', '@', '~', '.json')), text


def test_a_long_reason_never_hides_the_log_from_a_report(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    line = '2026-10-31T12:00:00Z Codex worker failed: A; details redacted.\n'
    path.write_text(line * (65536 // len(line)))  # just under the limit
    codex_diagnostics.record_failure(_copilot_error('é' * 1000))  # 2000 bytes
    assert codex_diagnostics.last_failure() == 'Copilot CopilotChatError: ' + 'é' * 1000


def test_a_forged_copilot_reason_with_a_path_is_not_reported(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    path.write_text('2026-10-31T12:00:00Z Copilot worker failed: CopilotChatError. '
                    'It said: Open C:\\Users\\kelly\\private\n')
    assert codex_diagnostics.recent_failures() == {}


def test_each_provider_keeps_its_own_recent_failures(tmp_path, monkeypatch):
    """One provider's failure no longer hides another's, and each says how
    long ago it happened (#464)."""
    import calendar
    import time
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    now = calendar.timegm(time.strptime('2026-10-31T12:00:00Z', '%Y-%m-%dT%H:%M:%SZ'))
    path.write_text(
        '2026-10-01T12:00:00Z Codex worker failed: OldError; details redacted.\n'
        '2026-10-31T09:00:00Z Copilot worker failed: CopilotChatError. It said: Copilot isn\'t running.\n'
        '2026-10-31T10:00:00Z Codex worker failed: A; details redacted.\n'
        '2026-10-31T11:00:00Z Codex connection failure: unsupported_request.\n'
        '2026-10-31T11:30:00Z Codex worker refused turn: not_idle. Codex said: busy.\n'
        '2026-10-31T11:57:00Z Codex worker failed: B; details redacted.\n'
        'Forged private line\n')
    found = codex_diagnostics.recent_failures(now=now)
    assert [what for _s, what in found['Codex']] == [
        'B; details redacted.',
        'refused turn (not_idle): ' + CodexTurnRejected.REASONS['not_idle'] + ' Codex said: busy.',
        'connection failure (unsupported_request): '
        + CodexConnectionFailure.REASONS['unsupported_request']]
    assert found['Copilot'] == [(3 * 3600, "CopilotChatError: Copilot isn't running.")]
    facts = bugreport.environment(SpeechSettings(), {}, claude_version='test', now=now)
    errors = [(label, value) for label, value in facts if label.endswith(' error')]
    assert errors[0] == ('Codex error', '3 minutes ago: B; details redacted.')
    assert ('Copilot error', "3 hours ago: CopilotChatError: Copilot isn't running.") in errors
    assert len(errors) == 4


def test_an_old_failure_is_still_reported_when_nothing_is_recent(tmp_path, monkeypatch):
    import calendar
    import time
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    now = calendar.timegm(time.strptime('2026-10-31T12:00:00Z', '%Y-%m-%dT%H:%M:%SZ'))
    path.write_text('2026-10-20T12:00:00Z Codex worker failed: A; details redacted.\n'
                    '2026-10-28T12:00:00Z Codex worker failed: B; details redacted.\n')
    assert codex_diagnostics.recent_failures(now=now) == {
        'Codex': [(3 * 86400, 'B; details redacted.')]}


def test_diagnostics_are_bounded_and_do_not_block_when_logging_fails(tmp_path, monkeypatch):
    path = tmp_path / 'errors.log'
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: path)
    path.write_text('x' * 70000)
    codex_diagnostics.record_failure(RuntimeError('secret'))
    assert path.stat().st_size < 65536
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: (_ for _ in ()).throw(OSError('No log')))
    codex_diagnostics.record_failure(ValueError('secret'))
    assert codex_diagnostics.last_failure() == ''
