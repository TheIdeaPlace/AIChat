"""What Codex's commands, pickers and copies say (#596), without windows:
the module builds none until it's called from the window."""
import pytest

pytest.importorskip('wx')
from thechatplace.ui import codex_extras  # noqa: E402
from thechatplace.ui.dialogs import CODEX_CHAT_OPTIONS_LATER_NOTE, CODEX_CHAT_OPTIONS_NOTE  # noqa: E402


def test_command_descriptions_are_in_a_users_words():
    # Ctrl+/ reads these aloud: no developer words, and Stop Background
    # Task's own word for what /background lists.
    for name, description in codex_extras.COMMANDS:
        assert 'native' not in description.lower(), name
        assert 'terminal' not in description.lower(), name
    described = dict(codex_extras.COMMANDS)
    assert described['plan'] == 'Choose Plan mode, or go back to Default mode.'
    assert described['background'] == 'List background tasks and stop one.'
    assert 'terminal' not in codex_extras.CONTROLS['/background'][2]


def test_every_control_without_a_result_to_show_says_what_is_now_so():
    shown = {'/goal', '/background', '/compact'}
    assert set(codex_extras.CONTROL_DONE) == set(codex_extras.CONTROLS) - shown
    said = {command: text.format(title='Review') for command, text in codex_extras.CONTROL_DONE.items()}
    assert said['/memories on'] == 'Codex memories are on for Review.'
    assert said['/memories off'] == 'Codex memories are off for Review.'
    assert said['/goal pause'] == 'Goal paused for Review.'
    assert said['/goal clear'] == 'Goal cleared for Review.'
    assert not any('accepted' in text for text in said.values())


def test_the_plan_picker_is_called_plan_mode():
    assert codex_extras.PLAN_MODE_TITLE == 'Plan Mode'
    assert [row.split(':')[0] for row in codex_extras.PLAN_MODE_CHOICES] == ['Default', 'Plan']


@pytest.mark.parametrize('now, rows', [
    ('xhigh', ["Codex's default", 'Low', 'Extra high (now)']),
    (None, ["Codex's default (now)", 'Low', 'Extra high']),
])
def test_effort_rows_name_codexs_default_and_read_as_words(now, rows):
    assert codex_extras.effort_rows([None, 'low', 'xhigh'], now) == rows


def test_a_copy_says_it_can_read_but_not_change_files_and_how_to_let_it(monkeypatch):
    monkeypatch.setattr('sys.platform', 'win32')
    note = codex_extras.copy_note()
    assert note.startswith('It can read files but not change them')
    assert "Codex's default model and effort" in note
    assert 'Change Permission Mode (Ctrl+Shift+O)' in note
    assert codex_extras.copy_note('The new chat').startswith('The new chat can read')
    monkeypatch.setattr('sys.platform', 'darwin')
    assert '(Cmd+Shift+O)' in codex_extras.copy_note()


def test_rewinds_files_note_has_no_checkpoints_and_says_the_copy_cant_edit():
    note = codex_extras.REWIND_FILES_NOTE
    assert 'checkpoint' not in note.lower()
    assert 'only the conversation goes back' in note and 'not change them' in note


def test_codex_chat_options_note_names_no_mode_that_isnt_there():
    note = CODEX_CHAT_OPTIONS_NOTE + ' ' + CODEX_CHAT_OPTIONS_LATER_NOTE
    assert 'Folder edits' not in note and 'grant' not in note
    assert 'only when its permission mode lets it edit files' in note
