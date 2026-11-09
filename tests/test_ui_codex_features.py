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


def test_connected_tools_note_names_the_app_in_full(frame, env):
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    note = CodexConnectedTools(frame, owned(frame, env).to_info()).note()
    assert "something The Chat Place can't show" in note and "TCP" not in note


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


# -- pickers and dialogs that come back from Codex (#467) -------------------------------


def _sync_work(frame, monkeypatch, answer=None):
    """_codex_work on the spot; ``answer(action)`` stands in for Codex."""
    def work(action, done):
        try:
            done(action() if answer is None else answer(action), False)
        except Exception as error:  # noqa: BLE001 - as _codex_work reports it
            done(str(error) or None, True)
    monkeypatch.setattr(frame, '_codex_work', work)


def _no_picker(monkeypatch):
    from thechatplace.ui import codex_extras
    opened = []

    class Picker:
        chosen = None

        def __init__(self, parent, rows):
            opened.append(rows)

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr(codex_extras, 'CommandPickerDialog', Picker)
    return opened


def _focus_moves_during_work(frame, monkeypatch):
    """The reply box changes while Codex is asked: typing went on elsewhere."""
    real = frame._codex_work

    def work(action, done):
        frame.reply_text.SetValue('typed meanwhile')
        real(action, done)
    monkeypatch.setattr(frame, '_codex_work', work)


def test_commands_say_they_are_being_got_and_open_if_nothing_moved(frame, env, monkeypatch):
    record = owned(frame, env)
    opened = _no_picker(monkeypatch)
    _sync_work(frame, monkeypatch)
    monkeypatch.setattr(frame.sessions, 'skills', lambda info: [
        dict(name='review', path='x', description='Review')])
    frame._codex_commands(record.to_info())
    assert any('Getting the Codex commands' in text for text in env['feedback'])
    assert any(row['name'] == 'review' for row in opened[0])


def test_commands_do_not_open_over_typing_or_after_focus_moved(frame, env, monkeypatch):
    record = owned(frame, env)
    opened = _no_picker(monkeypatch)
    _sync_work(frame, monkeypatch)
    monkeypatch.setattr(frame.sessions, 'skills', lambda info: [])
    _focus_moves_during_work(frame, monkeypatch)
    frame._codex_commands(record.to_info())
    assert not opened and "you've moved on" in env['feedback'][-1]


def test_commands_do_not_open_when_another_dialog_is_up(frame, env, monkeypatch):
    record = owned(frame, env)
    opened = _no_picker(monkeypatch)
    _sync_work(frame, monkeypatch)
    monkeypatch.setattr(frame.sessions, 'skills', lambda info: [])
    dialog = wx.Dialog(frame)
    monkeypatch.setattr(dialog, 'IsModal', lambda: True)
    monkeypatch.setattr(wx, 'GetTopLevelWindows', lambda: [frame, dialog])
    try:
        frame._codex_commands(record.to_info())
    finally:
        dialog.Destroy()
    assert not opened and "you've moved on" in env['feedback'][-1]


def test_a_skills_failure_is_said_as_a_failure(frame, env, monkeypatch):
    record = owned(frame, env)
    opened = _no_picker(monkeypatch)
    _sync_work(frame, monkeypatch)

    def fail(info):
        raise ValueError('')
    monkeypatch.setattr(frame.sessions, 'skills', fail)
    frame._codex_commands(record.to_info(), skills_only=True)
    assert not opened
    assert env['feedback'][-1] == "Could not get this folder's Codex skills."
    assert not any('No enabled' in text for text in env['feedback'])
    frame._codex_commands(record.to_info())
    assert opened and all(row.get('builtin') for row in opened[0])
    assert 'only the chat controls' in env['feedback'][-1]


def test_no_skills_at_all_is_still_said_as_none(frame, env, monkeypatch):
    record = owned(frame, env)
    _no_picker(monkeypatch)
    _sync_work(frame, monkeypatch)
    monkeypatch.setattr(frame.sessions, 'skills', lambda info: [])
    frame._codex_commands(record.to_info(), skills_only=True)
    assert env['feedback'][-1] == 'No enabled Codex skills are available for this folder.'


def _controls(frame, monkeypatch, answers):
    """owned_action('feature', ...) answered from ``answers`` by method."""
    calls = []

    def action(name, record, *args):
        calls.append((name, args))
        answer = answers[args[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(frame.sessions, 'owned_action', action)
    monkeypatch.setattr(frame, '_refresh_chat', lambda *a, **k: None)
    _sync_work(frame, monkeypatch)
    return calls


def test_a_malformed_goal_is_said_not_raised(frame, env, monkeypatch):
    record = owned(frame, env)
    _controls(frame, monkeypatch, {'thread/goal/get': {'goal': {'objective': 3}}})
    frame._codex_control(record.to_info(), '/goal')
    assert "Getting this chat's Codex goal." in env['feedback']
    assert env['feedback'][-1] == "Codex returned a goal The Chat Place can't read."


def test_the_goal_dialog_does_not_open_after_focus_moved(frame, env, monkeypatch):
    from thechatplace.ui import codex_extras
    record = owned(frame, env)
    _controls(frame, monkeypatch, {'thread/goal/get': {'goal': None}})
    monkeypatch.setattr(codex_extras, 'LinesDialog', lambda *a: pytest.fail('Opened by itself'))
    _focus_moves_during_work(frame, monkeypatch)
    frame._codex_control(record.to_info(), '/goal')
    assert "you've moved on" in env['feedback'][-1]


def test_background_list_does_not_open_after_focus_moved(frame, env, monkeypatch):
    record = owned(frame, env)
    _controls(frame, monkeypatch, {'thread/backgroundTerminals/list': {'data': [{'processId': 'one'}]}})
    monkeypatch.setattr(frame, '_choose', lambda *a, **k: pytest.fail('Opened by itself'))
    _focus_moves_during_work(frame, monkeypatch)
    frame._codex_control(record.to_info(), '/background')
    assert "you've moved on" in env['feedback'][-1]


@pytest.mark.parametrize('answer,said', [({'terminated': False}, 'That terminal had already finished.'),
                                         ({'terminated': True}, 'Codex terminal stopped.')])
def test_stop_background_task_says_what_codex_found(frame, env, monkeypatch, answer, said):
    record = owned(frame, env)
    calls = _controls(frame, monkeypatch, {
        'thread/backgroundTerminals/list': {'data': [{'processId': 'one', 'command': 'npm test'}]},
        'thread/backgroundTerminals/terminate': answer})
    offered = []
    monkeypatch.setattr(frame, '_choose', lambda title, prompt, rows, *a, **k: offered.append(rows) or 0)
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: wx.YES)
    frame.on_stop_background_task()
    assert offered == [['npm test']]
    assert calls[-1][1] == ('thread/backgroundTerminals/terminate', {'processId': 'one'})
    assert env['feedback'][-1] == said


def test_stop_background_task_says_none_are_running_and_is_on_for_a_codex_chat(frame, env, monkeypatch):
    record = owned(frame, env)
    _controls(frame, monkeypatch, {'thread/backgroundTerminals/list': {'data': []}})
    frame._update_send_state()
    assert frame.stop_background_item.IsEnabled()
    frame.on_stop_background_task()
    assert env['feedback'][-1] == 'No background terminals are running in this Codex chat.'
    frame._open = None
    frame._update_send_state()
    assert not frame.stop_background_item.IsEnabled()
    frame._open = replace(record.to_info(), source='external')
    assert not frame._can_stop_background()


# -- settings changes are said once (#467) ----------------------------------------------


def _change(frame, monkeypatch, *, prepared=None, saved=True):
    from thechatplace.providers.codex_models import CodexModel
    monkeypatch.setattr(frame, '_load_codex_models', lambda done: done([
        CodexModel('new', 'New model', ('medium',), 'medium', True)], False))
    monkeypatch.setattr(frame, '_choose', lambda *a, **k: 0)
    monkeypatch.setattr(frame, '_store_write', lambda *a, **k: saved)
    _sync_work(frame, monkeypatch)
    calls = []

    def action(name, record, *args):
        calls.append(name)
        if name in ('prepare_settings', 'prepare_mode'):
            if prepared is None:
                raise ValueError('')
            return prepared
    monkeypatch.setattr(frame.sessions, 'owned_action', action)
    return calls


def test_a_failed_setting_change_is_said_once_and_says_what_was_changing(frame, env, monkeypatch):
    record = owned(frame, env)
    _change(frame, monkeypatch)
    said = []
    monkeypatch.setattr(frame, '_say', lambda text, **k: said.append(text))
    frame._change_codex_settings(record.to_info(), 'model')
    assert env['feedback'][-2:] == ['Verifying the Codex model.',
                                    'Could not change the Codex model. The chat keeps its current one.']
    assert not frame._codex_policy_pending and not said


def test_a_problem_arriving_during_a_setting_change_is_held_for_it(frame, env, monkeypatch):
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.sessions import STATUS_UNAVAILABLE
    record = owned(frame, env)
    said = []
    monkeypatch.setattr(frame, '_say', lambda text, **k: said.append(text))
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
    monkeypatch.setattr(frame, '_refresh_chat', lambda *a, **k: None)
    frame._codex_policy_pending.add(record.to_info().key)
    frame._on_codex_update(CodexChatState(record.cli_session_id, STATUS_UNAVAILABLE, problem='Bad. Use Stop.'))
    assert not any('Bad' in text for text in said)
    frame._codex_policy_pending.clear()


def test_only_one_setting_change_at_a_time(frame, env, monkeypatch):
    record = owned(frame, env)
    calls = _change(frame, monkeypatch)
    frame._codex_policy_pending.add(record.to_info().key)
    frame._change_codex_settings(record.to_info(), 'model')
    assert not calls and 'already being changed' in env['feedback'][-1]
    frame._codex_policy_pending.clear()


def test_the_mode_picker_needs_no_models(frame, env, monkeypatch):
    record = owned(frame, env)
    calls = _change(frame, monkeypatch, prepared=SimpleNamespace(record=lambda: replace(
        record, provider_options=dict(record.provider_options, collaboration='plan'))))
    monkeypatch.setattr(frame, '_load_codex_models', lambda done: pytest.fail('Asked Codex for models'))
    frame._change_codex_settings(record.to_info(), 'mode')
    assert calls == ['prepare_mode', 'finish_policy']
    assert env['feedback'][-1] == f'Codex mode for {record.title} changed, from its next turn.'


def test_models_do_not_open_after_focus_moved(frame, env, monkeypatch):
    record = owned(frame, env)
    calls = _change(frame, monkeypatch)
    from thechatplace.providers.codex_models import CodexModel

    def typed_then_loaded(done):
        frame.reply_text.SetValue('typed meanwhile')
        done([CodexModel('new', 'New model', ('medium',), 'medium', True)], False)
    monkeypatch.setattr(frame, '_load_codex_models', typed_then_loaded)
    monkeypatch.setattr(frame, '_choose', lambda *a, **k: pytest.fail('Opened by itself'))
    frame._change_codex_settings(record.to_info(), 'model')
    assert not calls and "you've moved on" in env['feedback'][-1]


def test_a_setting_that_could_not_be_saved_says_so_without_stop(frame, env, monkeypatch):
    record = owned(frame, env)
    _change(frame, monkeypatch, prepared=SimpleNamespace(record=lambda: replace(
        record, provider_options=dict(record.provider_options, model='new'))), saved=False)
    frame._change_codex_settings(record.to_info(), 'model')
    assert env['feedback'][-1] == 'Could not save the Codex model. The chat keeps its current one.'


# -- the rest of what's said (#467) -----------------------------------------------------


def test_a_message_over_the_limit_says_so_not_that_an_attachment_is_missing(frame, env, monkeypatch):
    record = owned(frame, env)
    monkeypatch.setattr(frame, '_can_edit', lambda info: True)
    monkeypatch.setattr(frame, '_still_preparing', lambda info: False)
    frame.reply_text.SetValue('x' * 32001)
    frame.on_send()
    assert '32000' in env['feedback'][-1] and 'attachment is missing' not in env['feedback'][-1]
    assert frame.reply_text.GetValue() == 'x' * 32001


def _compaction_state(record, **kw):
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.sessions import IDLE
    return CodexChatState(record.cli_session_id, IDLE, 'compact-turn', 'completed', **kw)


def test_a_compaction_says_compacted_not_answered(frame, env, monkeypatch):
    record = owned(frame, env)
    said, notified = [], []
    monkeypatch.setattr(frame, '_say', lambda text, **k: said.append(text))
    monkeypatch.setattr(frame, '_notify', lambda key, title, text, **k: notified.append((title, text)))
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
    monkeypatch.setattr(frame, '_refresh_chat', lambda *a, **k: None)
    frame._on_codex_update(_compaction_state(record, compaction=True))
    assert said[-1] == f'{record.title}: context compacted.'
    assert notified[-1] == (f'{record.title}: context compacted', said[-1])
    assert not any('answered' in text for text in said)


def test_a_compaction_that_failed_is_not_called_compacted(frame, env, monkeypatch):
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.sessions import IDLE
    record = owned(frame, env)
    said = []
    monkeypatch.setattr(frame, '_say', lambda text, **k: said.append(text))
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
    monkeypatch.setattr(frame, '_refresh_chat', lambda *a, **k: None)
    frame._on_codex_update(CodexChatState(record.cli_session_id, IDLE, 'compact-turn', 'failed', compaction=True))
    assert said[-1] == f'{record.title}: Codex turn failed.'


def test_a_plan_mode_this_codex_lacks_is_left_out_of_the_heading(frame, env):
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.sessions import IDLE
    record = owned(frame, env)
    record.provider_options['collaboration'] = 'plan'
    frame._update_heading()
    assert 'plan mode' in frame.session_heading.GetLabel()
    frame._codex_states[record.to_info().key] = CodexChatState(record.cli_session_id, IDLE, plan_unavailable=True)
    frame._update_heading()
    assert 'plan mode' not in frame.session_heading.GetLabel()


# -- a selected tool removed from config.toml (#467) -------------------------------------


def test_a_copy_drops_every_removed_server_and_says_it_is_being_made(frame, env, monkeypatch):
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.providers.codex_tools import CodexTool
    record = owned(frame, env)
    record.provider_options['mcp_servers'] = ['kept', 'gone', 'also gone']
    rows = [CodexTool('gone', 'removed', (), False, True), CodexTool('also gone', 'removed', (), False, True),
            CodexTool('kept', 'connected', (), False)]
    asked, copied, boxes = [], [], []
    monkeypatch.setattr(frame.sessions, 'connected_tools', lambda info, **k: asked.append(k) or rows)
    monkeypatch.setattr(frame.sessions, 'copy_with_tools', lambda s, t, names, apps: copied.append(names))
    monkeypatch.setattr(wx, 'MessageBox', lambda text, *a, **k: boxes.append(text) or wx.YES)
    jobs = []
    monkeypatch.setattr(frame, '_codex_work', lambda action, done: jobs.append((action, done)))
    host = CodexConnectedTools(frame, record.to_info())
    host.load(lambda rows, error: None)
    action, done = jobs.pop()
    done(action(), False)
    assert asked == [{'selected': ('kept', 'gone', 'also gone'), 'apps': ()}]
    assert host.toggle_label(rows[0]) == 'Copy with Tool &Off'
    host.set_off(rows[0], True, lambda rows, error: None)
    assert "no longer in Codex's configuration" in boxes[-1]
    assert env['feedback'][-1] == 'Creating the Codex copy.'
    jobs.pop()[0]()
    assert copied == [['kept']]


def test_read_status_on_a_removed_server_says_what_is_wrong(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.providers.codex_tools import CodexTool
    record = owned(frame, env)
    row = CodexTool('gone', 'removed', (), False, True)
    monkeypatch.setattr(frame.sessions, 'connected_tools', lambda info, **k: [row])
    told = []
    dialog = ConnectedToolsDialog(frame, record.title, CodexConnectedTools(frame, record.to_info()))
    try:
        monkeypatch.setattr(dialog, '_tell', lambda text, *a, **k: told.append(text))
        dialog.busy = ''
        dialog._fill([row])
        dialog.on_reconnect()
        assert told == [row.text()]
    finally:
        dialog.Destroy()
