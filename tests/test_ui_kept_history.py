"""These tests construct windows: run in the VM only."""
import pytest
wx = pytest.importorskip('wx')
from test_ui_hidden import env, frame  # noqa: F401
from thechatplace import platform_paths, speech
from thechatplace.sessions import KEPT, SessionInfo
from thechatplace.ui.dialogs import SettingsDialog


def test_settings_retention_is_named_and_opens_only_existing_file(frame, env):
    path = platform_paths.claude_home()/'settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"cleanupPeriodDays":90}')
    before = path.read_bytes()
    dialog = SettingsDialog(frame, speech.SpeechSettings(), speech.default_options())
    try:
        assert dialog.retention.GetName() == 'History retention and kept copies'
        assert '90 days' in dialog.retention.GetValue()
        dialog._open_retention_settings(None)
        assert ('edit', path) in env['opened']
        assert path.read_bytes() == before
    finally: dialog.Destroy()


def test_kept_chat_keyboard_command_never_starts_continue_here(frame, env, monkeypatch):
    info = SessionInfo(KEPT, 'terminal:kept', 'Kept synthetic chat', '', 'kept')
    monkeypatch.setattr(frame, '_selected_session', lambda: info)
    monkeypatch.setattr(frame, '_feedback', lambda text: None)
    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only kept chat tried to start a provider')
    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame, 'NewSessionDialog', forbidden)
    frame.on_continue_here()
    frame._open = info
    frame._update_send_state()
    assert not frame.send_btn.IsEnabled() and not frame.stop_btn.IsEnabled()
