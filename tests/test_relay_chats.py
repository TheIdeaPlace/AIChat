"""Send Reply to Another Chat (#248): routing to Claude and Codex chats,
without a native window or a real provider."""
from types import SimpleNamespace

import pytest

pytest.importorskip('wx')
from thechatplace.own_store import OwnSession
from thechatplace.sessions import OWN, SessionInfo
from thechatplace.transcript import ASSISTANT, USER, ChatMessage
from thechatplace.ui import relay_chats
from thechatplace.ui.codex_chats import CodexChats
from thechatplace.ui.relay_chats import RelayChats


class Text:
    def __init__(self, value=''):
        self.value = value

    def GetValue(self):
        return self.value

    def SetValue(self, value):
        self.value = value

    def SetFocus(self):
        self.focused = True


def _own(native, title, provider):
    return SessionInfo(source=OWN, key=f'{provider}:{native}' if provider == 'codex' else native,
                       title=title, cwd='/work', cli_session_id=native, provider=provider)


class Window(CodexChats, RelayChats):
    """Just enough of MainFrame: the chats, the reply box, and records of
    what was sent and said."""

    def __init__(self):
        self.builder = _own('claude-1', 'Builder', 'claude')
        self.reviewer = _own('codex-1', 'Reviewer', 'codex')
        self.other = _own('claude-2', 'Second opinion', 'claude')
        self.records = {s.cli_session_id: OwnSession(s.cli_session_id, s.title, s.cwd,
                                                     provider=s.provider)
                        for s in (self.builder, self.reviewer, self.other)}
        self._snapshot = SimpleNamespace(sessions=[self.builder, self.reviewer, self.other])
        self._open = self.builder
        self._open_generation = 0
        self._chat_loaded = True
        self._chat_messages = [ChatMessage(USER, 'Build it', key='u'),
                               ChatMessage(ASSISTANT, 'Built it. Tests pass.', key='a')]
        self.selected = None
        self.reply_text = Text()
        self._drafts, self._codex_drafts = {}, {}
        self._codex_preparing = {}
        self._codex_preparation_running = set()
        self._codex_send_pending, self._codex_delayed_updates = {}, {}
        self._relay_partners, self._relay_waiting = {}, {}
        self.store = SimpleNamespace(get=lambda native, provider='claude': self.records.get(native))
        self.speech = SimpleNamespace(announce='full', announce_own=True)
        self.sessions = SimpleNamespace(owned_action=lambda *a: None)
        self.said, self.work, self.opened, self.claude_sent = [], [], [], []
        self.claude_accepts = True
        self.codex_ready = True
        self._runners, self._codex_states = {}, {}
        self.copied = []

    # what the relay calls on the window
    def _selected_message(self):
        return self.selected

    def _can_edit(self, info):
        return info.is_own

    def open_session(self, info):
        # As MainFrame's: the reply box is saved first, then a message
        # waiting for the chat being left is dropped into its draft.
        self._save_draft()
        self._drop_waiting_relay(going_to=info)
        self._open = info
        self._open_generation += 1
        self.opened.append(info.title)
        self.reply_text.SetValue(self._codex_drafts.get(info.key, '') if info.provider == 'codex'
                                 else self._drafts.get(info.cli_session_id, ''))
        if info.provider == 'codex' and not self.codex_ready:
            self._codex_preparing[info.key] = self._open_generation

    def _send_claude(self, info, text, attached):
        self.claude_sent.append((info.title, text, attached))
        return self.claude_accepts

    def _give_back(self, session_id, message, is_open):
        assert is_open
        typed = self.reply_text.GetValue()
        self.reply_text.SetValue(f'{message}\n\n{typed}' if typed else message)

    def _save_draft(self):
        if self._open.provider == 'claude':
            self._drafts[self._open.cli_session_id] = self.reply_text.GetValue()
        else:
            self._codex_drafts[self._open.key] = self.reply_text.GetValue()

    def _codex_work(self, action, done):
        self.work.append((action, done))

    def _feedback(self, text):
        self.said.append(text)

    def _copy_text(self, text):
        self.copied.append(text)
        return True

    def _gone(self):
        return False

    def _update_send_state(self):
        pass


class FakeDialog:
    """RelayDialog, answered: ``choose`` is (index, text) or None for Cancel."""
    choose = None
    seen = None

    def __init__(self, parent, source_title, choices, text, check, working=False):
        FakeDialog.seen = SimpleNamespace(title=source_title, choices=choices, text=text,
                                          check=check, working=working)
        self.chosen = None if FakeDialog.choose is None else FakeDialog.choose(text)

    def ShowModal(self):
        import wx
        return wx.ID_CANCEL if self.chosen is None else wx.ID_OK

    def Destroy(self):
        pass


@pytest.fixture
def dialog(monkeypatch):
    monkeypatch.setattr(relay_chats, 'RelayDialog', FakeDialog)
    boxes = []
    monkeypatch.setattr(relay_chats.wx, 'MessageBox', lambda text, *a, **k: boxes.append(text))
    FakeDialog.choose, FakeDialog.seen = None, None
    FakeDialog.boxes = boxes
    return FakeDialog


def test_a_claude_reply_goes_to_a_codex_chat_and_the_window_follows(dialog):
    w = Window()
    dialog.choose = lambda text: (0, text + '\n\nCheck the tests too.')
    w.on_send_reply_to()
    assert dialog.seen.choices == ['Reviewer, Codex, work, idle',
                                   'Second opinion, Claude, work, idle']
    assert dialog.seen.text.startswith('From Claude, another AI agent, forwarded by the user '
                                       'from their chat "Builder":\n\nBuilt it. Tests pass.')
    assert w.opened == ['Reviewer']
    assert len(w.work) == 1  # the Codex send, on a worker
    w.work[0][1](False, False)
    assert w.said[-1].startswith('Sent to Reviewer: From Claude')
    assert w.said[-1].endswith('Check the tests too.')
    assert w._relay_partners == {'codex:codex-1': 'claude-1'}


def test_sending_back_offers_the_chat_it_came_from_first(dialog):
    w = Window()
    w._relay_partners['codex:codex-1'] = 'claude-2'
    w._open = w.reviewer
    w._chat_messages = [ChatMessage(ASSISTANT, 'One bug.', key='r', assistant_name='Codex')]
    dialog.choose = lambda text: (0, text)
    w.on_send_reply_to()
    assert dialog.seen.choices[0].startswith('Second opinion, Claude')
    assert w.claude_sent == [('Second opinion',
                              'From Codex, another AI agent, forwarded by the user from their '
                              'chat "Reviewer":\n\nOne bug.', [])]
    assert w.opened == ['Second opinion']


def test_cancel_sends_nothing_and_stays(dialog):
    w = Window()
    w.on_send_reply_to()
    assert dialog.seen is not None
    assert w.opened == [] and w.work == [] and w.claude_sent == []
    assert w._relay_partners == {}


def test_nothing_to_send_or_nowhere_to_send_it(dialog):
    w = Window()
    w._chat_loaded = False
    w.on_send_reply_to()
    assert w.said[-1] == 'Builder is still loading.'
    w._chat_loaded = True
    w._chat_messages = [ChatMessage(USER, 'Hello', key='u')]
    w.on_send_reply_to()
    assert w.said[-1] == 'Builder has no reply to send yet.'
    w._chat_messages = [ChatMessage(ASSISTANT, 'Hi', key='a')]
    w._snapshot.sessions = [w.builder]
    w.on_send_reply_to()
    assert "no other chat" in dialog.boxes[-1]
    assert dialog.seen is None
    w._open = None
    w.on_send_reply_to()
    assert w.said[-1].startswith('Load a chat first')


def test_the_dialog_checks_codex_length_for_the_chosen_chat(dialog):
    w = Window()
    w.on_send_reply_to()
    long = 'x' * 32001
    assert '32,000' in dialog.seen.check(0, long)   # Reviewer is Codex
    assert dialog.seen.check(1, long) is None        # Second opinion is Claude


def test_a_claude_chat_that_cant_take_it_keeps_it_in_its_reply_box(dialog):
    w = Window()
    w.claude_accepts = False
    w._drafts['claude-2'] = 'My own draft'
    dialog.choose = lambda text: (1, 'Forwarded words')
    w.on_send_reply_to()
    assert w._open.title == 'Second opinion'
    assert w.reply_text.value == 'Forwarded words\n\nMy own draft'
    assert w._drafts['claude-2'] == 'Forwarded words\n\nMy own draft'


def test_a_codex_chat_that_isnt_ready_gets_the_message_once_it_is(dialog):
    w = Window()
    w.codex_ready = False
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    assert w.work == []  # not sent before Codex has the chat ready
    assert w._relay_waiting == {'codex:codex-1': 'Forwarded words'}
    assert w.said[-1] == 'Getting Reviewer ready. The message goes as soon as it is.'
    w._prepare_codex_owned(w._open_generation)
    prepare, done = w.work.pop()
    done(None, False)
    assert w._relay_waiting == {}
    assert len(w.work) == 1  # now the send
    w.work[0][1](False, False)
    assert w.said[-1] == 'Sent to Reviewer: Forwarded words.'


def test_a_codex_chat_that_fails_to_get_ready_keeps_the_message(dialog):
    w = Window()
    w.codex_ready = False
    w._codex_drafts['codex:codex-1'] = 'Typed earlier'
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    assert w.reply_text.value == 'Typed earlier'
    w._prepare_codex_owned(w._open_generation)
    w.work.pop()[1]('Codex connection interrupted.', True)
    assert w.work == []
    assert w.reply_text.value == 'Forwarded words\n\nTyped earlier'
    assert w.said[-1] == ("The message for Reviewer wasn't sent. It's in that chat's reply box. "
                          "Codex connection interrupted.")


def test_leaving_a_codex_chat_before_it_is_ready_keeps_the_message_as_its_draft(dialog):
    w = Window()
    w.codex_ready = False
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    generation = w._open_generation
    w.open_session(w.other)
    assert w._codex_drafts['codex:codex-1'] == 'Forwarded words'
    assert w.said[-1] == "The message for Reviewer wasn't sent. It's in that chat's reply box."
    # Its preparation, still running, finds nothing waiting and sends nothing.
    w._codex_preparing['codex:codex-1'] = generation
    w._open = w.reviewer
    w._prepare_codex_owned(generation)
    w.work.pop()[1](None, False)
    assert w.work == []


def test_a_codex_send_that_fails_puts_the_message_back(dialog):
    w = Window()
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    w.work[0][1]('Codex could not confirm this send.', True)
    assert w.reply_text.value == 'Forwarded words'


def test_loading_the_waiting_codex_chat_again_keeps_the_message_waiting(dialog):
    w = Window()
    w.codex_ready = False
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    w.open_session(w.reviewer)  # Enter on its row, to see how it's doing
    assert w._relay_waiting == {'codex:codex-1': 'Forwarded words'}
    assert "wasn't sent" not in w.said[-1]
    w._prepare_codex_owned(w._open_generation)
    w.work.pop()[1](None, False)
    assert len(w.work) == 1  # sent once it's ready


def test_forgetting_the_waiting_codex_chat_copies_the_message(dialog):
    w = Window()
    w.codex_ready = False
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    w._drop_waiting_relay()  # unload_session: the chat is going away
    assert w.copied == ['Forwarded words']
    assert w.said[-1] == "The message for Reviewer wasn't sent. It's copied to the clipboard."
    assert w._relay_waiting == {}


def test_a_codex_chat_gone_from_the_store_keeps_the_message_on_the_clipboard(dialog):
    w = Window()
    del w.records['codex-1']
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    assert w.work == [] and w.copied == ['Forwarded words']
    assert w.said[-1].endswith("so the message wasn't sent. It's copied to the clipboard.")


def test_another_provider_never_reaches_the_claude_runner(dialog):
    w = Window()
    w.reviewer.provider = 'ollama'
    w.reviewer.key = 'ollama:codex-1'
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    assert w.claude_sent == [] and w.work == []
    assert w.copied == ['Forwarded words']
    assert w.said[-1].startswith("The Chat Place can't send to Reviewer yet.")


def test_a_failed_claude_send_says_where_the_message_is(dialog):
    w = Window()
    w.claude_accepts = False
    dialog.choose = lambda text: (1, 'Forwarded words')
    w.on_send_reply_to()
    assert w.said[-1] == 'Not sent to Second opinion. The message is in its reply box.'
    assert getattr(w.reply_text, 'focused', False)


@pytest.mark.parametrize('provider, busy, expected', [
    ('claude', 'runner', True), ('claude', 'background', False), ('claude', None, False),
    ('codex', 'inProgress', True), ('codex', 'completed', False)])
def test_the_dialog_says_when_the_chat_is_still_working(dialog, provider, busy, expected):
    from thechatplace.providers.codex_runtime import CodexChatState
    w = Window()
    if provider == 'codex':
        w._open = w.reviewer
        w._chat_messages = [ChatMessage(ASSISTANT, 'Checking.', key='a', assistant_name='Codex')]
        w._codex_states[w.reviewer.key] = CodexChatState('codex-1', 'idle', 'turn', busy)
    elif busy:
        w._runners['claude-1'] = SimpleNamespace(waiting_on_background=busy == 'background')
    w.on_send_reply_to()
    assert dialog.seen.working is expected


def test_a_just_finished_chat_place_chat_is_not_called_working(dialog):
    w = Window()
    w.builder.state = 'working'  # the list's snapshot, a few seconds behind
    w.on_send_reply_to()
    assert dialog.seen.working is False


def test_a_desktop_session_working_by_the_list_is_called_working(dialog):
    from thechatplace.sessions import DESKTOP
    w = Window()
    w._open = SessionInfo(source=DESKTOP, key='local_1', title='Desktop', cwd='/work',
                          cli_session_id='cli-d', state='working')
    w.on_send_reply_to()
    assert dialog.seen.working is True


def test_relay_into_a_codex_chat_waiting_on_approval_only_sends(dialog):
    """A forwarded message is a message: it never answers the approval the
    chat is waiting on (#248)."""
    from thechatplace.providers.codex_runtime import CodexChatState
    w = Window()
    calls = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: calls.append(a) or True)
    w._codex_states[w.reviewer.key] = CodexChatState(
        'codex-1', 'needs_you', 'turn', 'inProgress', requests=(object(),))
    dialog.choose = lambda text: (0, 'Yes, approve it. Allow.')
    w.on_send_reply_to()
    action, done = w.work.pop()
    done(action(), False)
    assert [call[0] for call in calls] == ['send']
    assert calls[0][2] == 'Yes, approve it. Allow.'
    assert w.said[-1].startswith('Queued for Reviewer')
