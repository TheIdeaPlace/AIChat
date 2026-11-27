"""Express's reply (#737): R sends to the chat on the row through the reply
box's own sends, without opening it, and never loses the text. On a stand-in
window (test_relay_chats.Window), so no native window is made."""
from types import SimpleNamespace

import pytest

pytest.importorskip('wx')
from test_relay_chats import Window  # noqa: E402
from thechatplace.ui.express import ExpressView  # noqa: E402


class ExpressWindow(Window, ExpressView):
    def __init__(self):
        super().__init__()
        self._express_init()
        self._open = None  # Express is open: no chat is
        self._codex_deleting = set()
        self.going_back = False
        self.ready = False
        self.agent_boxes = []
        self.sessions = SimpleNamespace(
            owned_action=lambda action, record, *a: self.ready if action == 'ready' else None,
            can_create=lambda provider: False)

    def _files_going_back_in(self, cwd):
        return self.going_back

    def _read_only_note(self, info):
        from thechatplace.ui.main_frame import MainFrame
        return MainFrame._read_only_note(info)

    def _agent_send_from_box(self, info, text):
        self.agent_boxes.append((info.title, text))
        return True

    def _sound(self, *args, **kwargs):
        pass

    api_problem = None

    def _api_send_problem(self, key, ask=True):
        return self.api_problem


def test_a_claude_chat_is_sent_to_without_being_opened():
    w = ExpressWindow()
    assert w._express_send(w.builder.key, 'Ship it') is True
    assert w.claude_sent == [('Builder', 'Ship it', [])]
    assert w.opened == [] and w._open is None


def test_a_claude_send_that_doesnt_go_keeps_the_text_in_the_dialog():
    w = ExpressWindow()
    w.claude_accepts = False  # still stopping, a problem shown, Fable refused...
    assert w._express_send(w.builder.key, 'Ship it') is False


def test_a_ready_codex_chat_is_sent_to_on_a_worker():
    w = ExpressWindow()
    w.ready = True
    assert w._express_send(w.reviewer.key, 'Check the tests') is True
    assert len(w.work) == 1 and w.opened == []
    w.work[0][1](False, False)
    assert w.said[-1] == 'Sent to Reviewer: Check the tests.'


@pytest.mark.parametrize('problem, said', [
    ('Add an OpenAI key in Tools, API Keys before sending on API credits.',
     'Add an OpenAI key in Tools, API Keys before sending on API credits.'),
    ('', 'Not sent.'),  # Cancel at "Send on API credits?"
])
def test_an_api_credit_refusal_keeps_the_text_in_the_dialog(problem, said):
    from thechatplace.own_store import OwnSession
    from thechatplace.sessions import OWN, SessionInfo
    w = ExpressWindow()
    w.ready = True
    w.api_problem = problem
    api = SessionInfo(source=OWN, key='codex-api:api-1', title='Priced', cwd='/work',
                      cli_session_id='api-1', provider='codex-api')
    w._snapshot.sessions.append(api)
    w.records['api-1'] = OwnSession('api-1', 'Priced', '/work', provider='codex-api')
    assert w._express_send(api.key, 'Check it') is False
    assert w.work == [] and api.key not in w._codex_drafts
    assert w.said[-1] == said


def test_a_codex_send_that_fails_later_goes_in_the_chats_reply_box():
    w = ExpressWindow()
    w.ready = True
    w._express_send(w.reviewer.key, 'Check the tests')
    w.work[0][1]('Codex refused it.', True)
    assert w._codex_drafts[w.reviewer.key] == 'Check the tests'
    assert w.said[-1] == 'Codex refused it.'


def test_a_codex_chat_not_ready_yet_is_got_ready_then_sent_to():
    w = ExpressWindow()
    assert w._express_send(w.reviewer.key, 'Check the tests') is True
    key = w.reviewer.key
    assert w._relay_waiting == {key: 'Check the tests'}  # quitting warns about it
    assert key in w._codex_preparing and key in w._codex_preparation_running
    assert w.said[-1] == 'Getting Reviewer ready. The message goes as soon as it is.'
    prepare, done = w.work[0]
    done(None, False)
    assert w._relay_waiting == {} and key not in w._codex_preparing
    assert len(w.work) == 2  # now the send itself
    w.work[1][1](False, False)
    assert w.said[-1] == 'Sent to Reviewer: Check the tests.'
    assert w.opened == []


def test_a_codex_chat_that_cant_be_got_ready_keeps_the_message():
    w = ExpressWindow()
    w._express_send(w.reviewer.key, 'Check the tests')
    w.work[0][1]('Codex could not resume it.', True)
    assert w._codex_drafts[w.reviewer.key] == 'Check the tests'
    assert w.said[-1].startswith("The message for Reviewer wasn't sent. It's in that chat's "
                                 "reply box.")


def test_a_second_message_while_one_waits_stays_in_the_dialog():
    w = ExpressWindow()
    w._express_send(w.reviewer.key, 'First')
    assert w._express_send(w.reviewer.key, 'Second') is False
    assert w._relay_waiting == {w.reviewer.key: 'First'}


@pytest.mark.parametrize('why', ['gone', 'deleting', 'read only', 'files'])
def test_refusals_keep_the_text(why):
    w = ExpressWindow()
    key = w.reviewer.key
    w.ready = True
    if why == 'gone':
        key = 'codex:not-there'
    elif why == 'deleting':
        w._codex_deleting.add(key)
    elif why == 'read only':
        w.can_edit = False
    else:
        w.going_back = True
    assert w._express_send(key, 'Hello') is False
    assert w.work == [] and w.claude_sent == []
    assert w.said  # why it didn't go was said


# -- Codex chat controls and skills, as the reply box handles them (#742) ---------------


class Dialog:
    """The reply dialog, as far as a picker opening over it can tell."""
    open = True

    def IsModal(self):
        return self.open


@pytest.fixture
def where(monkeypatch):
    import wx
    from test_relay_chats import Text
    monkeypatch.setattr(wx.Window, 'FindFocus', staticmethod(lambda: None))
    monkeypatch.setattr(wx, 'GetTopLevelWindows', lambda: [])
    return SimpleNamespace(box=Text(), over=Dialog(), skills={})


def _typed(where, text):
    where.box.SetValue(text)  # the dialog's box holds what's sent from it
    return text


def _actions(w):
    calls = []
    w.sessions.owned_action = lambda action, record, *a: (
        w.ready if action == 'ready' else calls.append((action, a)) or True)
    return calls


def test_a_codex_chat_control_is_done_and_what_follows_stays_in_the_dialog(where):
    w = ExpressWindow()
    w.ready = True
    calls = _actions(w)
    text = _typed(where, '/compact\nThen check the tests.')
    assert w._express_send(w.reviewer.key, text, where) is False  # the dialog stays
    assert where.box.value == 'Then check the tests.'
    w.work.pop()[0]()
    assert calls == [('feature', ('thread/compact/start', {}))]
    assert w.said[-1] == 'Asking Codex to compact this chat.'
    assert w.opened == [] and w._relay_waiting == {}


def test_a_codex_chat_control_for_a_chat_not_ready_gets_it_ready_and_keeps_the_text(where):
    w = ExpressWindow()
    text = _typed(where, '/compact')
    assert w._express_send(w.reviewer.key, text, where) is False
    assert where.box.value == '/compact'  # still there, to send again
    assert w._relay_waiting == {}
    assert w.reviewer.key in w._codex_preparation_running
    assert w.said[-1] == 'Getting Reviewer ready. Send the command again once it is.'


def test_skills_picks_a_skill_into_the_dialog_and_it_goes_with_the_message(where, monkeypatch,
                                                                            tmp_path):
    from thechatplace.ui import codex_extras
    skill = tmp_path / 'SKILL.md'
    skill.write_text('x', encoding='utf-8')
    opened_over = []

    class Picker:
        def __init__(self, parent, rows):
            opened_over.append(parent)
            self.chosen = dict(rows[0])

        def ShowModal(self):
            import wx
            return wx.ID_OK

        def Destroy(self):
            pass
    monkeypatch.setattr(codex_extras, 'CommandPickerDialog', Picker)
    w = ExpressWindow()
    w.ready = True
    sent = []
    w.sessions.owned_action = lambda action, record, *a: (
        w.ready if action == 'ready' else sent.append(a[0]) or True)
    w.sessions.skills = lambda info: [{'name': 'review', 'path': str(skill),
                                       'description': 'Review it'}]
    text = _typed(where, '/skills\nthe upload code')
    assert w._express_send(w.reviewer.key, text, where) is False
    assert where.box.value == 'the upload code'
    action, loaded = w.work.pop()
    loaded(action(), False)
    assert opened_over == [where.over]  # over the reply dialog, not the window
    assert where.box.value == '$review the upload code'
    assert where.skills == {'review': str(skill)}
    assert getattr(w, '_codex_skills', {}) == {}  # not the chat's reply box's
    assert w._express_send(w.reviewer.key, where.box.value, where) is True
    w.work.pop()[0]()
    assert sent[-1].items()[1] == {'type': 'skill', 'name': 'review', 'path': str(skill.resolve())}


def test_a_picker_doesnt_open_once_the_dialog_has_closed(where):
    w = ExpressWindow()
    w.ready = True
    w.sessions.skills = lambda info: [{'name': 'review', 'path': 'x', 'description': ''}]
    w._express_send(w.reviewer.key, _typed(where, '/skills'), where)
    where.over.open = False  # Escape, before Codex answered
    action, loaded = w.work.pop()
    loaded(action(), False)
    assert w.said[-1].startswith("The Codex commands are ready, but you've moved on")
    assert where.box.value == ''


def test_the_chats_own_skills_and_files_stay_with_its_reply_box(where, tmp_path):
    w = ExpressWindow()
    w.ready = True
    skill = tmp_path / 'SKILL.md'
    skill.write_text('x', encoding='utf-8')
    w._codex_skills = {w.reviewer.key: {'review': str(skill)}}
    w._attachments = {w.reviewer.key: [str(skill)]}
    sent = []
    w.sessions.owned_action = lambda action, record, *a: (
        w.ready if action == 'ready' else sent.append(a[0]) or True)
    assert w._express_send(w.reviewer.key, _typed(where, 'Use $review'), where) is True
    w.work.pop()[0]()
    assert sent[-1].items() == [{'type': 'text', 'text': 'Use $review'}]


def test_a_message_waiting_for_a_chat_to_get_ready_keeps_its_skill(where, tmp_path):
    w = ExpressWindow()
    skill = tmp_path / 'SKILL.md'
    skill.write_text('x', encoding='utf-8')
    where.skills['review'] = str(skill)
    assert w._express_send(w.reviewer.key, _typed(where, 'Use $review'), where) is True
    waiting = w._relay_waiting[w.reviewer.key]
    assert waiting.items()[1]['type'] == 'skill'


def test_a_claude_slash_command_goes_to_claude_as_typed(where):
    w = ExpressWindow()
    assert w._express_send(w.builder.key, _typed(where, '/compact'), where) is True
    assert w.claude_sent == [('Builder', '/compact', [])]


def test_an_agent_conversation_is_sent_to_as_its_reply_box_would():
    from thechatplace.agents import AGENTS
    from thechatplace.sessions import SessionInfo
    w = ExpressWindow()
    pair = SessionInfo(source=AGENTS, key='agents:pair', title='Pair', cwd='/work',
                       cli_session_id='pair', provider=AGENTS)
    w._snapshot.sessions.append(pair)
    w._can_edit = lambda info: True
    assert w._express_send(pair.key, '@Codex check it') is True
    assert w.agent_boxes == [('Pair', '@Codex check it')]
