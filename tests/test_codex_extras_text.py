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


def test_a_copy_says_it_cant_change_files_without_asking_and_how_to_let_it(monkeypatch):
    monkeypatch.setattr('sys.platform', 'win32')
    # Before: what the copy doesn't keep, named as the original's.
    assert codex_extras.copy_note() == (
        "The new chat can't change files without asking, and doesn't keep the original's model, "
        'effort, Chat Options or connected tools. Change Permission Mode (Ctrl+Shift+O) lets it edit files.')
    # A tools copy has the tools you chose.
    assert 'connected tools' not in codex_extras.copy_note(tools=False)
    assert "the original's model, effort or Chat Options." in codex_extras.copy_note(tools=False)
    # Once open, the window is on the copy: short, and nothing about "this chat".
    short = codex_extras.copy_note(short=True)
    assert short == "It can't change files without asking; Change Permission Mode (Ctrl+Shift+O) lets it edit."
    for note in (codex_extras.copy_note(), short, codex_extras.REWIND_FILES_NOTE):
        assert 'this chat' not in note and 'read files but not' not in note
    monkeypatch.setattr('sys.platform', 'darwin')
    assert '(Cmd+Shift+O)' in codex_extras.copy_note() and '(Cmd+Shift+O)' in codex_extras.copy_note(short=True)


def test_rewinds_files_note_has_no_checkpoints_and_fits_files_only_too():
    note = codex_extras.REWIND_FILES_NOTE
    assert 'checkpoint' not in note.lower()
    assert 'only the conversation goes back' in note
    # Files only makes no chat, and shows this note too.
    assert 'A rewound Codex chat' in note and 'The new chat' not in note


@pytest.mark.parametrize('platform, key', [('win32', 'Ctrl+Period'), ('darwin', 'Cmd+Period')])
def test_the_window_names_stops_key_in_a_providers_stop_advice(platform, key):
    from thechatplace.providers.codex_runtime import STOP_TO_RECHECK
    from thechatplace.ui_text import with_stop_key
    said = with_stop_key('Codex lost it. ' + STOP_TO_RECHECK, platform)
    assert said == f'Codex lost it. Press Stop ({key}) to have The Chat Place check this chat with Codex before you send again.'
    assert with_stop_key(said, platform) == said  # never twice
    assert with_stop_key('Nothing about it.', platform) == 'Nothing about it.'
    assert with_stop_key('', platform) == ''


def test_codex_chat_options_note_names_no_mode_that_isnt_there():
    note = CODEX_CHAT_OPTIONS_NOTE + ' ' + CODEX_CHAT_OPTIONS_LATER_NOTE
    assert 'Folder edits' not in note and 'grant' not in note
    assert 'only when its permission mode lets it edit files' in note


def _models():
    from thechatplace.providers.codex_models import CodexModel
    return [CodexModel('other', 'Other model', ('low',), 'low', False),
            CodexModel('main', 'Main model', ('low', 'xhigh'), 'low', True)]


def test_change_effort_offers_a_listed_models_own_efforts():
    assert codex_extras.codex_effort_plan(_models(), 'other') == (_models()[0], False)


def test_change_effort_on_codexs_default_offers_its_default_models_efforts():
    # Refused before (#596); choosing an effort names that model (pinned).
    assert codex_extras.codex_effort_plan(_models(), None) == (_models()[1], True)
    assert codex_extras.codex_effort_plan(_models(), '') == (_models()[1], True)


def test_change_effort_says_what_to_do_when_it_cant_offer_any():
    said = codex_extras.codex_effort_plan(_models(), 'retired')
    assert said.startswith("retired isn't in Codex's list of models now") and 'Change Model' in said
    said = codex_extras.codex_effort_plan(_models()[:1], None)
    assert "Codex didn't say which model is its default" in said and 'Change Model' in said
    for said in (codex_extras.codex_effort_plan(_models(), 'retired'), codex_extras.codex_effort_plan([], None)):
        assert 'available Codex model first' not in said


def test_chat_options_during_a_turn_say_what_you_typed_is_kept():
    wait = codex_extras.CHAT_OPTIONS_WAIT
    assert 'still working' in wait and 'what you typed stays here' in wait
    assert 'Wait until' not in wait
