"""Send Reply to Another Chat (#248): routing to Claude and Codex chats,
without a native window or a real provider."""
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('wx')
from thechatplace import agents
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
        self.sessions = SimpleNamespace(owned_action=lambda *a: None,
                                        can_create=lambda provider: False)
        self.agents = agents.AgentConversationStore(
            Path(tempfile.mkdtemp()) / 'agent-conversations.json')
        self.new_sessions, self.agent_sent, self.agent_opened = [], [], []
        self.refreshed = 0
        self.can_edit = True
        self.said, self.work, self.opened, self.claude_sent = [], [], [], []
        self.claude_accepts = True
        self.codex_ready = True
        self._runners, self._codex_states = {}, {}
        self.copied = []

    # what the relay calls on the window
    def _selected_message(self):
        return self.selected

    def _can_edit(self, info):
        return info.is_own and self.can_edit

    # what a new chat or an agent conversation (#378) calls
    def on_new_session(self, forward=None):
        self.new_sessions.append(forward)

    def refresh_sessions(self):
        self.refreshed += 1

    def _agent_open(self, conversation):
        self.agent_opened.append(conversation.title)

    def _agent_send(self, conversation_id, text, targets):
        self.agent_sent.append((conversation_id, text, targets))
        return True

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

    @property
    def _running_codex_keys(self):
        from thechatplace.ui.main_frame import MainFrame
        return lambda: MainFrame._running_codex_keys(self)

    @property
    def _running_turn_count(self):
        from thechatplace.ui.main_frame import MainFrame
        return lambda: MainFrame._running_turn_count(self)

    def _current_info(self, key):
        return next((s for s in self._snapshot.sessions if s.key == key), None)

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

    joint = None   # (title) to start an agent conversation, or None to just send

    def __init__(self, parent, source_title, choices, text, check, working=False, joint=None):
        FakeDialog.seen = SimpleNamespace(title=source_title, choices=choices, text=text,
                                          check=check, working=working, joint=joint)
        self.chosen = None if FakeDialog.choose is None else FakeDialog.choose(text)
        self.joint_chosen = FakeDialog.joint is not None
        self.joint_title = FakeDialog.joint or ''

    def ShowModal(self):
        import wx
        return wx.ID_CANCEL if self.chosen is None else wx.ID_OK

    def Destroy(self):
        pass


@pytest.fixture
def dialog(monkeypatch):
    monkeypatch.setattr(relay_chats, 'RelayDialog', FakeDialog)
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, 'find_claude', lambda: SimpleNamespace(path=''))
    FakeDialog.joint = None
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
    w.sessions = SimpleNamespace(owned_action=lambda *a: calls.append(a) or True,
                                 can_create=lambda provider: False)
    w._codex_states[w.reviewer.key] = CodexChatState(
        'codex-1', 'needs_you', 'turn', 'inProgress', requests=(object(),))
    dialog.choose = lambda text: (0, 'Yes, approve it. Allow.')
    w.on_send_reply_to()
    action, done = w.work.pop()
    done(action(), False)
    assert [call[0] for call in calls] == ['send']
    assert calls[0][2] == 'Yes, approve it. Allow.'
    assert w.said[-1].startswith('Queued for Reviewer')


def test_a_message_waiting_for_codex_keeps_quitting_and_updating_careful(dialog):
    """Found by Codex reviewing this feature: until Codex has the chat
    ready, the forwarded message is only in memory, so quitting or an
    update must count it, as they count a running turn and a draft."""
    from thechatplace.ui.main_frame import MainFrame
    w = Window()
    w.codex_ready = False
    assert MainFrame._active_owned_turn_count(w) == 0
    assert not MainFrame._unsent_text(w)
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    w._codex_drafts.clear()  # nothing typed anywhere
    assert MainFrame._active_owned_turn_count(w) == 1
    assert MainFrame._running_turn_count(w) == 0
    assert MainFrame._unsent_text(w)
    assert w._relay_waiting_text() == 'the message forwarded to Reviewer'
    # Ready: the send takes over the count, then nothing is left.
    w._prepare_codex_owned(w._open_generation)
    w.work.pop()[1](None, False)
    assert not w._relay_waiting and MainFrame._active_owned_turn_count(w) == 1
    w.work.pop()[1](False, False)
    assert MainFrame._active_owned_turn_count(w) == 0
    assert w._relay_waiting_text() == ''


def test_a_message_that_fails_to_go_to_codex_is_a_draft_not_work(dialog):
    from thechatplace.ui.main_frame import MainFrame
    w = Window()
    w.codex_ready = False
    dialog.choose = lambda text: (0, 'Forwarded words')
    w.on_send_reply_to()
    w._prepare_codex_owned(w._open_generation)
    w.work.pop()[1]('Codex connection interrupted.', True)
    assert MainFrame._active_owned_turn_count(w) == 0
    assert MainFrame._unsent_text(w)  # it's that chat's draft now


# -- a new chat, or an agent conversation (#378) --------------------------------------


@pytest.fixture
def claude_installed(monkeypatch):
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, 'find_claude',
                        lambda: SimpleNamespace(path='C:/claude.exe'))


def test_new_chats_are_offered_after_the_existing_ones(dialog, claude_installed):
    w = Window()
    w.sessions.can_create = lambda provider: provider == 'codex'
    w.on_send_reply_to()
    assert dialog.seen.choices[-2:] == ['New Claude chat', 'New Codex chat']
    assert dialog.seen.choices[0].startswith('Reviewer')  # sending back is still Enter
    long = 'x' * 32001
    assert '32,000' in dialog.seen.check(3, long)  # a new Codex chat has Codex's limit
    assert dialog.seen.check(2, long) is None


def test_only_new_chats_when_there_is_no_other(dialog, claude_installed):
    w = Window()
    w._snapshot.sessions = [w.builder]
    w.on_send_reply_to()
    assert dialog.seen.choices == ['New Claude chat']
    assert dialog.boxes == []


def test_a_new_chat_opens_new_session_with_the_reply_and_the_folder(dialog, claude_installed):
    w = Window()
    dialog.choose = lambda text: (2, text)
    w.on_send_reply_to()
    forward = w.new_sessions[-1]
    assert (forward.provider, forward.folder, forward.joint) == ('claude', '/work', False)
    assert forward.title == 'Forwarded from Builder'
    assert forward.message.startswith('From Claude, another AI agent, forwarded by the user')
    assert w.claude_sent == [] and w.opened == []  # New Session sends it
    made = _own('claude-new', 'Forwarded from Builder', 'claude')
    forward.made(made, forward.message)
    assert w._relay_partners == {'claude-new': 'claude-1'}  # sending back is Enter
    assert w.agents.all() == []


def test_a_joint_chat_links_the_two_and_sends_the_reply_as_a_hand_off(dialog):
    w = Window()
    dialog.joint = 'Build and review'
    dialog.choose = lambda text: (0, text + '\n\nCheck the tests too.')
    w.on_send_reply_to()
    [conversation] = w.agents.all()
    assert conversation.title == 'Build and review'
    assert [(m.provider, m.native_id, m.name) for m in conversation.members] == \
        [('claude', 'claude-1', 'Claude'), ('codex', 'codex-1', 'Codex')]
    assert conversation.mode == agents.MODE_APPROVE
    assert conversation.next_index == 1  # Codex answers first
    assert w.agent_opened == ['Build and review']
    [(sent_id, text, targets)] = w.agent_sent
    assert sent_id == conversation.conversation_id and targets == [1]
    assert text == ('From Claude (agent conversation "Build and review"):\n\n'
                    'Built it. Tests pass.\n\nCheck the tests too.')
    assert agents.is_relay(text)
    assert w.opened == [] and w.work == [] and w.claude_sent == []
    assert w._relay_partners == {}


def test_a_joint_chat_with_the_label_changed_sends_your_words(dialog):
    w = Window()
    dialog.joint = 'Pair'
    dialog.choose = lambda text: (1, 'Please review what Builder did.')
    w.on_send_reply_to()
    [(_id, text, targets)] = w.agent_sent
    assert text == 'Please review what Builder did.' and targets == [1]
    [conversation] = w.agents.all()
    assert [m.name for m in conversation.members] == ['Claude 1', 'Claude 2']


def test_the_dialog_says_why_a_joint_chat_cant_be_started(dialog):
    w = Window()
    taken = agents.new_conversation('Other', [agents.Member('codex', 'codex-1', 'Codex'),
                                              agents.Member('claude', 'x', 'Claude')])
    w.agents.add(taken)
    w.on_send_reply_to()
    problem = dialog.seen.joint.problem
    assert problem(0) == 'Reviewer is already in an agent conversation'
    assert problem(1) is None
    assert dialog.seen.joint.title == 'Builder'
    w._runners['claude-1'] = SimpleNamespace(waiting_on_background=False)
    w.on_send_reply_to()
    assert dialog.seen.joint.problem(1) == \
        'Builder is still working; wait until it has finished'


def test_a_chat_that_changed_since_the_dialog_keeps_the_message(dialog, monkeypatch):
    w = Window()
    dialog.joint = 'Pair'

    def choose(text):
        # Between the dialog's check and the send, Builder joined another.
        w.agents.add(agents.new_conversation('Other', [agents.Member('claude', 'claude-1', 'C'),
                                                       agents.Member('claude', 'y', 'D')]))
        return (1, text)
    dialog.choose = choose
    w.on_send_reply_to()
    assert w.agent_sent == []
    assert len(w.agents.all()) == 1
    assert w.copied and w.copied[-1].startswith('From Claude')
    assert w.said[-1].startswith("An agent conversation can't be started: Builder is already")


def test_a_new_chat_made_joint_starts_the_conversation_once_it_exists(dialog, claude_installed):
    w = Window()
    w.sessions.can_create = lambda provider: True
    dialog.joint = 'Second look'
    dialog.choose = lambda text: (3, text)  # New Codex chat
    w.on_send_reply_to()
    forward = w.new_sessions[-1]
    assert (forward.provider, forward.joint, forward.title) == \
        ('codex', True, 'Second look: Codex')
    assert w.agents.all() == []  # nothing until the chat exists
    made = _own('codex-new', 'Second look: Codex', 'codex')
    forward.made(made, forward.message)
    [conversation] = w.agents.all()
    assert [m.native_id for m in conversation.members] == ['claude-1', 'codex-new']
    assert w.agent_sent[-1][2] == [1]
    assert w.agent_sent[-1][1].startswith('From Claude (agent conversation "Second look")')
    assert w._relay_partners == {}
