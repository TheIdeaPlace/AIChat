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


def test_missing_original_revokes_sending_even_before_discovery(frame, env):
    from fake_env import add_transcript
    from records import user_text, assistant_block, text_block
    from thechatplace.providers.claude import keep_session
    from thechatplace.history_keeper import keeper
    info=frame.store.get('own-1').to_info()
    path=add_transcript(env,info.cwd,info.cli_session_id,[
        user_text('Invented question'),assistant_block(text_block('Invented reply'))])
    keep_session(info);assert keeper().wait_idle()
    path.unlink()
    assert not frame._send_claude(info,'Must not be sent',[])
    assert info.source==KEPT and not frame._runners
    frame._open=info
    frame.own_reply.Show()
    frame._update_send_state()
    assert not frame.own_reply.IsShown() and frame.desktop_reply.IsShown()
    assert 'kept copy' in frame.desktop_note.GetValue()
    assert not frame.continue_btn.IsShown() and not frame.reply_claude_btn.IsShown()


def test_settings_declared_access_keys_are_unique(frame):
    import re
    dialog=SettingsDialog(frame,speech.SpeechSettings(copilot_enabled=True),speech.default_options())
    seen={}
    def visit(window):
        label=window.GetLabel() if hasattr(window,'GetLabel') else ''
        for key in re.findall(r'(?<!&)&([^&])',label):
            assert key.casefold() not in seen, (key,seen.get(key.casefold()),label)
            seen[key.casefold()]=label
        for child in window.GetChildren():visit(child)
    try:
        visit(dialog)
        assert seen['y']=='Histor&y retention and kept copies:'
    finally:dialog.Destroy()
