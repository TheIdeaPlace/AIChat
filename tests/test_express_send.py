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
