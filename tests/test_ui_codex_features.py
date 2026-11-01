"""Shared Codex workflows. These tests create windows: VM or CI only."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

wx = pytest.importorskip('wx')
from test_ui_hidden import env, frame  # noqa: F401,E402
from fake_env import pump  # noqa: E402
from thechatplace.own_store import OwnSession  # noqa: E402
from thechatplace.providers.codex import transcript_from_thread  # noqa: E402
from test_codex_provider import history  # noqa: E402


def owned(frame, env):
    record = OwnSession('native-owned', 'Codex original', str(env['tmp']), provider='codex',
                        provider_options={'model': 'old', 'sandbox': 'read-only', 'effort': 'high'})
    frame.store.add(record)
    frame._open = record.to_info()
    return record


def test_codex_rewind_routes_real_native_anchor_and_preserves_original(frame, env, monkeypatch):
    from thechatplace.ui.codex_extras import RewindDialog
    record = owned(frame, env)
    message = transcript_from_thread(history()).messages[0]
    calls, opened = [], []
    def branch(info, title, anchor, before):
        calls.append((info, title, anchor, before))
        return OwnSession('new-copy', title, info.cwd, provider='codex')
    monkeypatch.setattr(frame.sessions, 'branch', branch)
    monkeypatch.setattr(frame, '_require_capability', lambda *a: True)
    monkeypatch.setattr(frame, 'open_session', opened.append)
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
    monkeypatch.setattr(RewindDialog, 'ShowModal', lambda self: wx.ID_OK)
    frame._branch_codex(record.to_info(), message, before=True)
    assert pump(lambda: opened)
    assert calls[0][2:] == ('turn-1', True)
    assert opened[0].cli_session_id != record.cli_session_id
    assert frame._codex_drafts[opened[0].key] == 'Hi'
    assert frame.store.get(record.cli_session_id, provider='codex') is record


def test_codex_attachments_are_separate_from_a_claude_chat_with_the_same_id(frame, env):
    record = owned(frame, env)
    document = env['tmp'] / 'spec.txt'
    document.write_text('Review this.')
    frame._attachments[record.cli_session_id] = ['claude-only.txt']
    frame._attachments[record.to_info().key] = [str(document)]
    value = frame._codex_message(record.to_info(), 'Review')
    assert str(document) in value
    assert 'claude-only' not in value
    frame._show_attachments()
    assert frame.attach_list.GetString(0) == 'spec.txt'


def test_settings_save_failure_restores_the_record_and_does_not_activate_it(frame, env, monkeypatch):
    from thechatplace.providers.codex_models import CodexModel
    record = owned(frame, env)
    before = dict(record.provider_options)
    proposed = replace(record, provider_options=dict(before, model='new'))
    change = SimpleNamespace(record=lambda: proposed)
    calls = []
    def action(name, rec, *args):
        calls.append((name, args))
        return change if name == 'prepare_settings' else None
    def work(action, done):
        done(action(), False)
    def failed_save(method, *args, **kwargs):
        record.provider_options = kwargs['provider_options']
        return False
    monkeypatch.setattr(frame, '_load_codex_models', lambda done: done([
        CodexModel('new', 'New model', ('medium',), 'medium', True)], False))
    monkeypatch.setattr(frame, '_choose', lambda *a, **k: 0)
    monkeypatch.setattr(frame, '_codex_work', work)
    monkeypatch.setattr(frame, '_store_write', failed_save)
    monkeypatch.setattr(frame.sessions, 'owned_action', action)
    frame._change_codex_settings(record.to_info(), 'model')
    assert record.provider_options == before
    assert calls[-1] == ('finish_policy', (change, False))


def test_codex_side_question_names_its_provider(frame, env):
    from thechatplace.ui.dialogs import AskAsideDialog
    said = []
    dialog = AskAsideDialog(frame, 'Codex example',
        lambda question, event: SimpleNamespace(cancel=lambda: None),
        provider='Codex', feedback=said.append)
    try:
        dialog.question.SetValue('Which file matters?')
        dialog.on_ask()
        assert said[-1] == 'Asking Codex.'
    finally:
        dialog.stop()
        dialog.Destroy()


def test_stopping_codex_aside_restores_ask_and_allows_another_question(frame, env):
    from thechatplace.ui.dialogs import AskAsideDialog
    from thechatplace.providers.codex_aside import CodexAsideRunner
    def ask(question, event):
        return CodexAsideRunner(None, question, event)  # no native process in this test
    dialog = AskAsideDialog(frame, 'Codex example', ask, provider='Codex')
    try:
        dialog.question.SetValue('First question')
        dialog.on_ask()
        assert dialog.asking
        dialog.stop()
        assert not dialog.asking and dialog.ask_btn.GetLabel() == '&Ask'
        dialog.question.SetValue('Another question')
        dialog.on_ask()
        assert dialog.asking and len(dialog.items) == 2
    finally:
        dialog.stop()
        dialog.Destroy()


def test_client_command_keeps_the_draft_below_it(frame, env, monkeypatch):
    record = owned(frame, env)
    controls = []
    monkeypatch.setattr(frame, '_codex_control', lambda info, command: controls.append(command))
    frame.reply_text.SetValue('/compact\nDraft to finish later')
    assert frame._handle_codex_command(record.to_info(), frame.reply_text.GetValue())
    assert controls == ['/compact']
    assert frame.reply_text.GetValue() == 'Draft to finish later'


def test_tool_copy_is_serial_and_does_not_navigate_after_dialog_closes(frame, env, monkeypatch):
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.providers.codex_tools import CodexTool
    record = owned(frame, env)
    host = CodexConnectedTools(frame, record.to_info())
    jobs, results, opened = [], [], []
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: wx.YES)
    monkeypatch.setattr(frame, '_codex_work', lambda action, done: jobs.append((action, done)))
    monkeypatch.setattr(frame, 'open_session', opened.append)
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
    done = lambda rows, error: results.append((rows, error))
    host.set_off(CodexTool('notes', 'off'), False, done)
    host.set_off(CodexTool('notes', 'off'), False, done)
    assert len(jobs) == 1 and 'Still creating' in results[-1][1]
    host.close()
    jobs[0][1](OwnSession('saved-copy', 'Tools copy', record.cwd, provider='codex'), False)
    assert frame.store.get('saved-copy', provider='codex') is not None
    assert not opened


def test_direct_steering_keeps_image_and_skill_inputs(frame, env, monkeypatch):
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.sessions import WORKING
    record = owned(frame, env)
    info = record.to_info()
    path = env['tmp'] / 'image.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 32)
    skill = env['tmp'] / 'SKILL.md'
    skill.write_text('Review only.')
    frame.reply_text.SetValue('$review this')
    frame._attachments[info.key] = [str(path)]
    frame._codex_skills = {info.key: {'review': str(skill)}}
    frame._codex_states[info.key] = CodexChatState(info.cli_session_id, WORKING, 'turn', 'inProgress')
    monkeypatch.setattr(frame, '_can_edit', lambda info: True)
    monkeypatch.setattr(frame, '_can_steer', lambda info: True)
    monkeypatch.setattr(frame, '_history_unknown', lambda: False)
    steered = []
    monkeypatch.setattr(frame, '_steer_codex_owned', lambda rec, text: steered.append(text))
    frame.on_steer()
    assert [part['type'] for part in steered[0].items()] == ['text', 'localImage', 'skill']
    assert info.key not in frame._attachments and info.key not in frame._codex_skills
