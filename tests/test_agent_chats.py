"""Agent conversations (#248, phase 2): the relay's decisions, on a stand-in
window with recorded sends; no native window, no real provider."""
from types import SimpleNamespace

import pytest

pytest.importorskip('wx')
from thechatplace import agents
from thechatplace.agents import (MODE_AUTO, AgentConversationStore, Member, Pending)
from thechatplace.own_store import OwnSession
from thechatplace.sessions import NEEDS_YOU, WORKING
from thechatplace.ui.agent_chats import AgentChats


class Text:
    def __init__(self, value=''):
        self.value = value

    def GetValue(self):
        return self.value

    def SetValue(self, value):
        self.value = value

    def SetFocus(self):
        pass


class Window(AgentChats):
    def __init__(self, tmp_path, mode='approve', auto_setting=False, codex_ready=True):
        self.records = {
            ('claude', 'c1'): OwnSession('c1', 'Builder chat', '/work'),
            ('codex', 'x1'): OwnSession('x1', 'Reviewer chat', '/work', provider='codex'),
        }
        self.store = SimpleNamespace(
            get=lambda native, provider='claude': self.records.get((provider, native)))
        self.agents = AgentConversationStore(tmp_path / 'agents.json')
        members = [Member('claude', 'c1', 'Claude', 'Build'), Member('codex', 'x1', 'Codex', 'Review')]
        self.conversation = agents.new_conversation('Fix 247', members, mode=mode, turn_limit=3)
        self.agents.add(self.conversation)
        self.cid = self.conversation.conversation_id
        self.speech = SimpleNamespace(announce='full', announce_own=True,
                                      agent_auto_mode=auto_setting, agent_announce_each=True,
                                      agent_turn_limit=6)
        self._runners, self._pending, self._codex_states = {}, {}, {}
        self._agent_awaiting, self._agent_asked = {}, set()
        self._agent_last_reply, self._agent_last_heard = {}, {}
        self._agent_codex_waiting = {}
        self._open = agents.rows([], [self.conversation])[-1]
        self.reply_text = Text()
        self._snapshot = SimpleNamespace(sessions=[self._open])
        self.codex_ready = codex_ready
        self.actions = []
        self.sessions = SimpleNamespace(owned_action=self._owned_action)
        self.claude_sent, self.codex_sent, self.work = [], [], []
        self.said, self.feedback, self.notified, self.copied = [], [], [], []
        self.claude_accepts = True
        self.refreshes = 0

    def _owned_action(self, action, record, *args):
        self.actions.append(action)
        return self.codex_ready if action == 'ready' else None

    # what the mixin calls on the window
    def _send_claude(self, info, text, attached, announce_send=True):
        assert announce_send is False
        self.claude_sent.append((info.cli_session_id, text))
        return self.claude_accepts

    def _send_codex_owned(self, record, text, announce_send=True, restore=True, on_result=None):
        assert announce_send is False and restore is False
        self.codex_sent.append((record.cli_session_id, text, on_result))

    def _codex_work(self, action, done):
        self.work.append((action, done))

    def _feedback(self, text):
        self.feedback.append(text)

    def _say(self, text, interrupt=True):
        self.said.append(text)

    def _status(self, text):
        self.said.append('status: ' + text)

    def _notify(self, key, title, text, needs_you=False):
        self.notified.append((title, text, needs_you))

    def _refresh_chat(self):
        self.refreshes += 1

    def _update_send_state(self):
        pass

    def refresh_sessions(self, **_kw):
        pass

    def _copy_text(self, text):
        self.copied.append(text)
        return True

    def _can_edit(self, info):
        return True

    # helpers
    @property
    def saved(self):
        return self.agents.get(self.cid)

    def send(self, text):
        return self._agent_send_from_box(self._open, text)


def test_your_first_message_goes_to_the_first_member_with_the_setup(tmp_path):
    w = Window(tmp_path)
    assert w.send('Build the uploader fix.')
    [(native, text)] = w.claude_sent
    assert native == 'c1' and text.startswith(agents.SETUP_START)
    assert text.endswith('\n\nBuild the uploader fix.')
    assert w.feedback[-1] == 'Sent to Claude.'
    saved = w.saved
    assert saved.briefed == [0] and saved.next_index == 1
    assert w._agent_awaiting[('claude', 'c1')] == (w.cid, True)
    w.send('And the tests.')  # no setup the second time; to Codex now
    assert w.codex_sent[-1][1].startswith(agents.SETUP_START)


def test_addressing_picks_the_member_and_all_goes_to_both_without_relaying(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex what do you think?')
    assert w.codex_sent and not w.claude_sent
    assert w.codex_sent[-1][1].endswith('what do you think?')
    w.send('@all status?')
    assert w._agent_awaiting[('claude', 'c1')] == (w.cid, False)
    assert w._agent_awaiting[('codex', 'x1')] == (w.cid, False)
    assert not w.send('@Claude   ')
    assert w.feedback[-1] == 'Type a message after the name.'


def test_approve_mode_waits_for_you_and_says_how_to_hand_it_on(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it. Tests pass.', False)
    saved = w.saved
    assert saved.pending == Pending(0, 1, 'Built it. Tests pass.')
    assert not w.codex_sent
    assert w.said[-1].startswith('Claude in Fix 247 replied. Built it. Tests pass.')
    assert w.said[-1].endswith('Ctrl+Shift+E hands it to Codex.')
    assert w.notified[-1][2] is True
    row = agents.rows([], [saved])[-1]
    assert row.state == NEEDS_YOU and "Claude's reply waiting to go to Codex" in row.detail


def test_handing_on_sends_the_labelled_reply_and_restarts_a_stopped_relay(tmp_path, monkeypatch):
    from thechatplace.ui import agent_chats
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it.', False)
    seen = {}

    class Dialog:
        def __init__(self, parent, source, choices, text, check, title=''):
            seen.update(choices=choices, text=text, title=title)
            self.chosen = (0, text + '\n\nFocus on locking.')

        def ShowModal(self):
            return agent_chats.wx.ID_OK

        def Destroy(self):
            pass
    monkeypatch.setattr(agent_chats, 'RelayDialog', Dialog)
    w.on_agent_hand_off()
    assert seen['title'] == 'Hand Off: Fix 247'
    assert seen['choices'] == ['Codex, Reviewer chat']
    assert seen['text'] == 'From Claude (agent conversation "Fix 247"):\n\nBuilt it.'
    sent = w.codex_sent[-1][1]
    assert sent.startswith(agents.SETUP_START)
    assert sent.endswith('From Claude (agent conversation "Fix 247"):\n\nBuilt it.\n\nFocus on locking.')
    saved = w.saved
    assert saved.pending is None and saved.next_index == 0
    assert saved.notes[-1].text == "You passed Claude's reply to Codex."


def test_nothing_waiting_to_hand_on(tmp_path):
    w = Window(tmp_path)
    w.on_agent_hand_off()
    assert w.feedback[-1].startswith('No reply is waiting to be handed on.')


def test_automatic_mode_needs_the_setting(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=False)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it.', False)
    assert not w.codex_sent and w.saved.pending is not None


def test_automatic_mode_passes_replies_up_to_the_limit(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    replies = ['Built v1.', 'Bug in v1: the lock.', 'Fixed the lock in v2.', 'v2 looks right now.']
    speakers = [('claude', 'c1'), ('codex', 'x1'), ('claude', 'c1'), ('codex', 'x1')]
    for (provider, native), reply in zip(speakers, replies):
        w._agent_turn_ended(provider, native, reply, False)
    # Three exchanges (the limit), then the fourth reply waits for you.
    assert [t.split('\n\n')[-1] for _n, t, _r in w.codex_sent] == ['Built v1.', 'Fixed the lock in v2.']
    assert w.claude_sent[-1][1].endswith('Bug in v1: the lock.')
    saved = w.saved
    assert saved.exchanges == 3 and saved.paused
    assert saved.stop_reason == 'Stopped after 3 exchanges, the turn limit.'
    assert saved.pending == Pending(1, 0, 'v2 looks right now.')
    assert 'Passed Claude\'s reply to Codex (exchange 1 of 3).' in [n.text for n in saved.notes]
    assert w.said[-1].endswith('Ctrl+Shift+E hands it to Claude.')
    # Your next message starts a fresh run.
    w.send('Carry on.')
    assert w.saved.exchanges == 0 and not w.saved.paused


def test_the_done_marker_stops_the_relay(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'All fixed and tested.\n\nDONE', False)
    assert not w.codex_sent
    saved = w.saved
    assert saved.paused and saved.stop_reason == 'Claude said the work is done.'
    assert w.said[-1].endswith('Claude said the work is done.')


def test_repeating_replies_stop_the_relay(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Thanks, that all looks right to me.', False)
    w._agent_turn_ended('codex', 'x1', 'Thanks, that all looks right to me!', False)
    assert w.saved.stop_reason == 'The replies were repeating, so the relay stopped.'
    assert len(w.codex_sent) == 1 and not w.claude_sent[1:]


def test_a_question_mid_turn_pauses_automatic_mode(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_member_asked('claude', 'c1')
    w._agent_turn_ended('claude', 'c1', 'Built it after you allowed the push.', False)
    assert not w.codex_sent
    assert w.saved.stop_reason == 'Paused: Claude asked you something during its turn.'


def test_a_failed_turn_or_empty_reply_stops_the_relay(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'usage limit', True)
    assert w.saved.stop_reason == "Claude's turn failed."
    assert w.said[-1] == "Fix 247: Claude's turn failed. The relay stopped."
    w.send('Try again.')
    w._agent_turn_ended('codex', 'x1', '   ', False)
    assert w.saved.stop_reason == "Codex's turn ended without a reply."


def test_a_turn_you_started_in_the_member_chat_is_not_relayed(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w._agent_turn_ended('claude', 'c1', 'Something you asked it directly.', False)
    assert not w.codex_sent and w.saved.pending is None


def test_more_queued_waits_for_the_last_turn(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Part one.', False, more_queued=True)
    assert w.saved.pending is None and ('claude', 'c1') in w._agent_awaiting
    w._agent_turn_ended('claude', 'c1', 'All of it.', False)
    assert w.saved.pending.text == 'All of it.'


def test_both_replies_to_at_all_are_said_but_not_passed_on(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('@all ready?')
    w._agent_turn_ended('claude', 'c1', 'Ready.', False)
    w._agent_turn_ended('codex', 'x1', 'Ready too.', False)
    assert len(w.claude_sent) == 1 and len(w.codex_sent) == 1
    assert w.saved.pending is None
    assert w.said[-1] == 'Codex in Fix 247 replied. Ready too.'


def test_a_quiet_member_turn_is_one_the_conversation_started(tmp_path):
    w = Window(tmp_path)
    assert not w._agent_quiet('claude', 'c1')
    w.send('Build it.')
    assert w._agent_quiet('claude', 'c1')


def test_a_codex_member_that_isnt_ready_is_got_ready_first(tmp_path):
    w = Window(tmp_path, codex_ready=False)
    w.send('@Codex review this')
    assert not w.codex_sent and len(w.work) == 1
    row = w._agent_rows([])[-1]
    assert row.state == WORKING  # getting ready counts
    w.send('@Codex and this')  # joins the wait; not a second preparation
    assert len(w.work) == 1
    action, done = w.work.pop()
    action()
    assert w.actions[-1] == 'prepare'
    done(None, False)
    assert [t.split('\n\n')[-1] for _n, t, _r in w.codex_sent] == ['review this', 'and this']
    assert not w._agent_codex_waiting


def test_a_codex_member_that_cant_get_ready_gives_the_message_back(tmp_path):
    w = Window(tmp_path, codex_ready=False)
    w.reply_text.SetValue('half typed')
    w.send('@Codex review this')
    w.work.pop()[1]('Codex connection interrupted.', True)
    assert w.reply_text.value == 'review this\n\nhalf typed'
    saved = w.saved
    assert saved.paused and saved.stop_reason == "Couldn't send to Codex."
    assert ('codex', 'x1') not in w._agent_awaiting


def test_a_codex_send_that_fails_later_gives_the_message_back(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex review this')
    _native, _text, on_result = w.codex_sent[-1]
    on_result('Codex could not confirm this send.', True)
    assert w.reply_text.value == 'review this'


def test_a_conversation_not_loaded_puts_a_failed_message_on_the_clipboard(tmp_path):
    w = Window(tmp_path, codex_ready=False)
    w.send('@Codex review this')
    w._open = None
    w.work.pop()[1]('Codex connection interrupted.', True)
    assert w.copied == ['review this']


def test_a_claude_member_that_cant_take_it(tmp_path):
    w = Window(tmp_path)
    w.claude_accepts = False
    assert not w.send('Build it.')
    assert ('claude', 'c1') not in w._agent_awaiting
    assert w.saved.notes[-1].text == "Couldn't send to Claude."


def test_a_gone_member_chat_is_said(tmp_path):
    w = Window(tmp_path)
    del w.records[('claude', 'c1')]
    assert not w.send('Build it.')
    assert w.feedback[-1] == "Claude's chat is gone. Nothing was sent to it."


def test_stop_stops_running_members_and_the_relay(tmp_path):
    from thechatplace.providers.codex_runtime import CodexChatState
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    runner = SimpleNamespace(cancelled=False)
    runner.cancel = lambda: setattr(runner, 'cancelled', True)
    w._runners['c1'] = runner
    stopped = []
    w._stop_codex_owned = lambda info: stopped.append(info.cli_session_id)
    w._codex_states['codex:x1'] = CodexChatState('x1', 'working', 't', 'inProgress')
    w.send('Build it.')
    w._agent_stop(w._open)
    assert runner.cancelled and stopped == ['x1']
    assert not w._agent_awaiting
    assert w.saved.paused and w.saved.stop_reason == 'Stopped.'
    assert w.feedback[-1] == 'Stopping Claude and Codex. The relay is stopped.'
    # The stopped turn ending is not relayed or called a failure.
    w._agent_turn_ended('claude', 'c1', '', True)
    assert w.saved.stop_reason == 'Stopped.'


def test_status_says_who_is_doing_what_and_where_your_words_go(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w._runners['c1'] = SimpleNamespace(elapsed=lambda: 75.0)
    text = w._agent_status_text(w._open)
    assert text.startswith('Fix 247: Claude is working (1 minute 15 seconds). Codex is idle.')
    assert 'Hand-offs: automatic, 0 of 3 exchanges.' in text
    assert text.endswith('What you type goes to Claude.')
    w.speech.agent_auto_mode = False
    assert '(off in Settings, so you approve each hand-off)' in w._agent_status_text(w._open)
    assert w._agent_send_state(w._open) == 'Claude is working.'


def test_a_new_claude_chats_id_change_is_followed(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_rename_member('claude', 'c1', 'c2')
    assert w.saved.members[0].native_id == 'c2'
    assert ('claude', 'c2') in w._agent_awaiting
    w.records[('claude', 'c2')] = w.records.pop(('claude', 'c1'))
    w._agent_turn_ended('claude', 'c2', 'Built it.', False)
    assert w.saved.pending.text == 'Built it.'


def test_rename_and_properties(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    assert w._agent_rename(w._open, '  Fix   248 ')
    assert w.saved.title == 'Fix 248'
    lines = w._agent_properties(w._open)
    assert lines[:4] == ['Title: Fix 248', 'Kind: agent conversation', 'Hand-offs: automatic',
                         'Turn limit: 3 exchanges (0 so far)']
    assert 'Claude: Builder chat; role: Build' in lines
    assert 'Codex: Reviewer chat; role: Review' in lines


def test_the_mixin_never_answers_a_request(tmp_path):
    """Relayed words are only ever sent as a message: the mixin has no path
    to a member's permission or question answers."""
    import inspect
    from thechatplace.ui import agent_chats
    source = inspect.getsource(agent_chats)
    for forbidden in ('_apply_answer', 'respond(', "'answer'", '"answer"', 'allow_response'):
        assert forbidden not in source
