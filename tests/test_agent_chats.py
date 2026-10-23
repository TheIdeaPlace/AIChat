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
from thechatplace.ui_text import key_name


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
                                      agent_auto_mode=auto_setting,
                                      session_order='newest')
        self._runners, self._pending, self._codex_states = {}, {}, {}
        self._agent_awaiting, self._agent_asked = {}, set()
        self._agent_last_reply, self._agent_last_heard = {}, {}
        self._agent_codex_waiting = {}
        self._agent_unconfirmed, self._agent_last_sent = set(), {}
        self._agent_codex_preparing, self._agent_answered_early = set(), set()
        self._agent_turn_files = {}
        self._codex_send_pending, self._queued = {}, {}
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

    def _modal_open(self):
        return False

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

    def _take_queued(self, native):
        queued = self._queued.pop(native, None)
        return "\n\n".join(queued) if queued else None

    def _current_info(self, key):
        return None

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
    assert w.said[-1].endswith(key_name('Ctrl+Shift+E') + ' hands it to Codex.')
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
    assert not saved.notes  # the receiving chat's history shows the hand-off
    assert w.feedback[-1] == "Passed Claude's reply to Codex."


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
    assert [n.text for n in saved.notes] == ['Stopped after 3 exchanges, the turn limit.']
    assert w.said[-1].endswith(key_name('Ctrl+Shift+E') + ' hands it to Claude.')
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
    assert w.said[-1].endswith('Claude said the work is done. ' + key_name('Ctrl+Shift+E')
                               + " hands Claude's last reply to Codex.")


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


# -- found in review --------------------------------------------------------------------


def test_a_reply_given_before_background_work_is_the_one_handed_on(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_answered('claude', 'c1', 'Built it; the tests run in the background.')
    w._agent_turn_ended('claude', 'c1', '', False)  # the turn ends later with no text
    assert w.saved.pending.text == 'Built it; the tests run in the background.'
    assert not w.saved.paused


def test_conversation_rows_take_their_place_in_the_list(tmp_path):
    from thechatplace.sessions import OWN, SessionInfo
    w = Window(tmp_path)
    old = SessionInfo(OWN, 'own:old', 'Old chat', '/w', 'old', last_activity_ms=1)
    rows = w._agent_rows([old])
    assert [r.title for r in rows] == ['Fix 247', 'Old chat']  # newest first


def test_stop_stops_a_codex_send_in_flight_and_takes_back_claudes_queue(tmp_path):
    w = Window(tmp_path)
    stopped = []
    w._stop_codex_owned = lambda info: stopped.append(info.cli_session_id)
    runner = SimpleNamespace(cancel=lambda: None)
    w._runners['c1'] = runner
    w._queued['c1'] = [agents.SETUP_START + '\nsetup\n' + agents.SETUP_END + '\n\nQueued words']
    w._codex_send_pending['codex:x1'] = 1
    w._agent_stop(w._open)
    assert stopped == ['x1']
    assert w.reply_text.value == 'Queued words'
    assert w.feedback[-1].endswith("A message that hadn't gone yet is back in the reply box.")


def test_stop_ends_a_waiting_reply(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it.', False)
    w._agent_stop(w._open)
    assert w.saved.pending is None
    assert w.feedback[-1].endswith("The waiting reply wasn't passed on.")


def test_deleting_a_conversation_forgets_what_waits_for_codex(tmp_path, monkeypatch):
    from thechatplace.ui import agent_chats
    w = Window(tmp_path, codex_ready=False)
    w.session_list = SimpleNamespace(SetFocus=lambda: None)
    w.unload_session = lambda: None
    monkeypatch.setattr(agent_chats.wx, 'MessageBox', lambda *a, **k: agent_chats.wx.YES)
    w.send('@Codex review this')
    w._agent_delete(w._open)
    action, done = w.work.pop()
    done(None, False)
    assert w.codex_sent == []
    assert w.agents.all() == []


def test_a_failed_first_send_sends_the_setup_again_next_time(tmp_path):
    w = Window(tmp_path, codex_ready=False)
    w.send('@Codex review this')
    w.work.pop()[1]('Codex connection interrupted.', True)
    assert 1 not in w.saved.briefed
    w.codex_ready = True
    w.send('@Codex review this')
    assert w.codex_sent[-1][1].startswith(agents.SETUP_START)


def test_a_failed_first_claude_turn_sends_the_setup_again(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Not signed in', True, detail='Claude Code is not signed in.')
    assert 0 not in w.saved.briefed
    assert w.said[-1] == ("Fix 247: Claude's turn failed. Claude Code is not signed in. "
                          "The relay stopped.")
    w.send('@Claude try again')
    assert w.claude_sent[-1][1].startswith(agents.SETUP_START)


def test_a_good_first_turn_keeps_the_setup_sent(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built.', False)
    w._agent_turn_ended('claude', 'c1', 'oops', True)  # a later failure, not awaited
    assert 0 in w.saved.briefed


def test_typing_to_the_agent_a_reply_waits_for_sends_the_reply_too(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built v1.', False)
    w.send('Also check performance.')
    sent = w.codex_sent[-1][1]
    assert sent.endswith('From Claude (agent conversation "Fix 247"):\n\nBuilt v1.\n\n'
                         'The user adds:\n\nAlso check performance.')
    assert w.saved.pending is None
    assert w.feedback[-1] == "Sent Claude's reply and your message to Codex."
    assert not w.saved.notes


def test_refused_tools_are_said_and_pause_automatic_mode(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it, but could not push.', False,
                        detail='1 tool was refused: Bash')
    assert not w.codex_sent
    assert w.saved.stop_reason == 'Paused: 1 tool was refused: Bash'
    assert '1 tool was refused: Bash' in w.said[-1]


def test_done_finishes_without_leaving_the_row_needing_you(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Finished.\nDONE', False)
    saved = w.saved
    # The last reply can still go on, but the row doesn't need you for it.
    assert saved.pending == Pending(0, 1, 'Finished.\nDONE', finished=True) and saved.paused
    row = agents.rows([], [saved])[-1]
    assert row.state != NEEDS_YOU
    assert row.detail == "finished; Claude's last reply can go to Codex"
    assert w.notified[-1][2] is False
    assert w._agent_send_state(w._open) == ('Claude said the work is done. '
                                            + key_name('Ctrl+Shift+E')
                                            + " hands Claude's last reply to Codex.")


def test_the_status_line_says_when_a_member_waits_for_you(tmp_path):
    from thechatplace.providers.codex_runtime import CodexChatState
    w = Window(tmp_path)
    w._pending['c1'] = [object()]
    assert w._agent_send_state(w._open) == 'Claude is waiting for you. ' + key_name('Ctrl+Shift+A') + ' answers.'
    w._pending.clear()
    w._codex_states['codex:x1'] = CodexChatState('x1', 'needs_you', 't', 'inProgress',
                                                 requests=(object(),))
    assert w._agent_send_state(w._open) == 'Codex is waiting for you. ' + key_name('Ctrl+Shift+A') + ' answers.'


def test_a_hand_off_whose_reply_changed_meanwhile_sends_nothing(tmp_path, monkeypatch):
    from thechatplace.ui import agent_chats
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built v1.', False)

    class Dialog:
        def __init__(self, parent, source, choices, text, check, title=''):
            self.chosen = (0, text)
            # A newer reply arrives while the dialog is open.
            conversation = w.agents.get(w.cid)
            conversation.pending = Pending(0, 1, 'Built v2.')
            w.agents.put(conversation)

        def ShowModal(self):
            return agent_chats.wx.ID_OK

        def Destroy(self):
            pass
    monkeypatch.setattr(agent_chats, 'RelayDialog', Dialog)
    sent_before = len(w.codex_sent)
    w.on_agent_hand_off()
    assert len(w.codex_sent) == sent_before
    assert w.saved.pending.text == 'Built v2.'
    assert w.feedback[-1].startswith('The waiting reply changed while the dialog was open.')


def test_a_message_given_back_unsent_ends_the_wait(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex review')
    w._agent_unsent('codex', 'x1')
    assert ('codex', 'x1') not in w._agent_awaiting
    assert w.saved.stop_reason == "Codex didn't take the message."


def test_quitting_counts_a_message_waiting_for_a_codex_member(tmp_path):
    from thechatplace.ui.main_frame import MainFrame
    w = Window(tmp_path, codex_ready=False)
    w._relay_waiting, w._drafts, w._codex_drafts = {}, {}, {}
    w._save_draft = lambda: None
    w._running_codex_keys = lambda: set()
    assert MainFrame._active_owned_turn_count(w) == 0 and not MainFrame._unsent_text(w)
    w.send('@Codex review this')
    assert MainFrame._active_owned_turn_count(w) == 1
    assert MainFrame._unsent_text(w)
    assert w._agent_waiting_names() == ['Codex in Fix 247']


def test_opening_or_renaming_doesnt_move_a_conversation_up(tmp_path):
    w = Window(tmp_path)
    before = w.saved.last_activity_ms
    import time
    time.sleep(0.01)
    w._agent_rename(w._open, 'New name')
    assert w.saved.last_activity_ms == before
    w.send('Hello')
    assert w.saved.last_activity_ms > before



# -- found in the second review -------------------------------------------------------------


def test_a_background_reply_goes_through_the_conversation_at_once(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_answered('claude', 'c1', 'Built; the server keeps running.')
    assert w.codex_sent[-1][1].endswith('Built; the server keeps running.')  # handed on now
    w._agent_turn_ended('claude', 'c1', '', False)  # the turn's real end, an hour later
    assert len(w.codex_sent) == 1 and not w.saved.paused


def test_tools_refused_before_background_work_pause_automatic_mode(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_answered('claude', 'c1', 'Built.', detail='1 tool was refused: Bash')
    assert not w.codex_sent
    assert w.saved.stop_reason == 'Paused: 1 tool was refused: Bash'


def test_text_you_queued_yourself_isnt_the_conversations(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex review')
    w._agent_unsent('codex', 'x1', ('Something you typed in the Codex chat',))
    assert ('codex', 'x1') in w._agent_awaiting and not w.saved.paused
    sent = w._agent_last_sent[('codex', 'x1')]
    w._agent_unsent('codex', 'x1', (sent,))
    assert ('codex', 'x1') not in w._agent_awaiting


def test_a_message_that_never_reached_claude_comes_back_to_the_conversation(tmp_path):
    w = Window(tmp_path)
    w.reply_text.SetValue('typed since')
    w.send('Build it.')
    sent = w.claude_sent[-1][1]
    assert w._agent_take_unsent('claude', 'c1', sent)
    assert w.reply_text.value == 'Build it.\n\ntyped since'
    assert not w._agent_take_unsent('claude', 'nobody', 'x')


def test_a_new_ids_state_follows_it(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_rename_member('claude', 'c1', 'c2')
    assert ('claude', 'c2') in w._agent_unconfirmed and ('claude', 'c2') in w._agent_last_sent


def test_one_preparation_per_codex_member_and_nothing_left_after_stop(tmp_path):
    w = Window(tmp_path, codex_ready=False)
    w._stop_codex_owned = lambda info: None
    w.send('@Codex one')
    w._agent_stop(w._open)
    assert 'codex:x1' not in w._agent_codex_waiting
    w.send('@Codex two')  # the first preparation is still running
    assert len(w.work) == 1
    w.work.pop()[1](None, False)
    assert [t.split('\n\n')[-1] for _n, t, _r in w.codex_sent] == ['two']


def test_a_failed_codex_member_leaves_no_unsaved_claude_chat(tmp_path):
    w = Window(tmp_path)
    w.store.add = lambda record: pytest.fail('nothing should be saved')
    w._store_write = lambda method, *a, **k: method(*a, **k) or True
    plan = SimpleNamespace(
        title='Pair', folder=str(tmp_path), mode='approve', turn_limit=6, message='Hello',
        first=0, to_all=False,
        members=[SimpleNamespace(info=None, provider='claude', role='', permission='auto'),
                 SimpleNamespace(info=None, provider='codex', role='', permission='read-only')])
    w.sessions.create_owned = lambda *a, **k: None
    w._agent_create(plan)
    w.work.pop()[1]('Codex is not signed in.', True)
    assert w.copied == ['Hello']
    assert w.feedback[-1] == ("Couldn't start the Codex chat for the agent conversation, so it "
                              "wasn't started. Codex is not signed in. Your opening message is "
                              "copied to the clipboard.")



# -- found in the final review -----------------------------------------------------------------


def test_queued_conversation_text_keeps_the_wait_after_a_background_answer(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_answered('claude', 'c1', 'Built; tests run in the background.', more_queued=True)
    assert ('claude', 'c1') in w._agent_awaiting  # the queued follow-up's answer is awaited
    assert w.saved.pending is None
    w._agent_turn_ended('claude', 'c1', 'Also checked X.', False)
    assert w.saved.pending.text == 'Also checked X.'


def test_the_late_end_of_a_handled_turn_stays_quiet(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_answered('claude', 'c1', 'Built.')
    assert ('claude', 'c1') not in w._agent_awaiting
    assert w._agent_quiet('claude', 'c1')  # its "finished" an hour later says nothing
    w._agent_turn_ended('claude', 'c1', '', False)
    assert not w._agent_quiet('claude', 'c1')


def test_a_message_that_cant_be_kept_anywhere_stays_with_the_member(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._open = None
    w._copy_text = lambda text: False
    assert not w._agent_take_unsent('claude', 'c1', w.claude_sent[-1][1])


def test_a_codex_message_given_back_goes_to_the_conversation(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex review this')
    sent = w._agent_last_sent[('codex', 'x1')]
    left = w._agent_unsent('codex', 'x1', ('Mine', sent))
    assert left == ('Mine',)  # only your own text goes to the member chat's box
    assert w.reply_text.value == 'review this'
    assert w.saved.notes[-1].text == "Codex didn't take the message."



# -- a real conversation, 2026-10-22 ------------------------------------------------------------


def test_a_reviewers_findings_with_done_still_reach_the_builder(tmp_path):
    """Codex reviewed Claude's page and ended with its findings and DONE.
    Nothing went to Claude, which read Codex's session file to find them.
    Now the findings wait, and what you write to Claude takes them along."""
    w = Window(tmp_path)
    w.send('@Codex review the page')
    review = '1. Minor: make the transcript link self-contained.\n\nDONE'
    w._agent_turn_ended('codex', 'x1', review, False)
    assert w.saved.stop_reason == 'Codex said the work is done.'
    w.send('@Claude see if codex replies')
    sent = w.claude_sent[-1][1]
    assert 'From Codex (agent conversation "Fix 247"):' in sent
    assert 'make the transcript link self-contained' in sent
    assert sent.endswith('The user adds:\n\nsee if codex replies')
    assert w.saved.pending is None


def test_addressing_by_name_and_a_comma(tmp_path):
    """"Codex, what is broken about your tools" went to Claude: only @
    addressed anyone. A name and a comma or colon does now."""
    w = Window(tmp_path)
    w.send('Codex, what is broken about your tools')
    assert w.codex_sent and not w.claude_sent
    assert w.codex_sent[-1][1].endswith('Codex, what is broken about your tools')



# -- found reviewing the DONE fix ---------------------------------------------------------------


def test_writing_to_the_agent_that_said_done_sets_its_reply_aside(tmp_path):
    """Claude said DONE; you carry on with Claude. Its old reply mustn't
    later ride along to Codex, or be offered by Ctrl+Shift+E."""
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Finished v1.\nDONE', False)
    w.send('@Claude also add X')
    saved = w.saved
    assert saved.pending is None
    assert saved.notes[-1].text == "Claude's waiting reply was set aside: you wrote to Claude."
    w._agent_turn_ended('claude', 'c1', 'Added X.', False)  # goes across automatically
    assert w.codex_sent[-1][1].endswith('Added X.')
    w._agent_turn_ended('codex', 'x1', 'oops', True)
    w.send('@Codex try again')
    assert 'Finished v1.' not in w.codex_sent[-1][1]


def test_a_newer_reply_passed_on_clears_an_older_waiting_one(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    conversation = w.agents.get(w.cid)
    conversation.pending = Pending(1, 0, 'old', finished=True)
    w.agents.put(conversation)
    w._agent_turn_ended('claude', 'c1', 'Newer reply.', False)
    assert w.codex_sent[-1][1].endswith('Newer reply.')
    assert w.saved.pending is None


def test_the_status_line_after_the_relay_restarts(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex review')
    w._agent_turn_ended('codex', 'x1', 'Fine.\nDONE', False)
    w._runners['c1'] = SimpleNamespace(elapsed=lambda: 1.0)  # Claude busy on something else
    assert w._agent_send_state(w._open) == 'Claude is working.'
    w._runners.clear()
    w._pending['c1'] = [object()]
    assert w._agent_send_state(w._open).startswith('Claude is waiting for you.')
    w._pending.clear()
    conversation = w.agents.get(w.cid)
    conversation.stop_reason = ''
    w.agents.put(conversation)
    assert w._agent_send_state(w._open) == (key_name('Ctrl+Shift+E')
                                            + " hands Codex's last reply to Claude.")


def test_repeating_keeps_the_last_reply_as_finished(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'All good to me, thanks.', False)
    w._agent_turn_ended('codex', 'x1', 'All good to me, thanks!', False)
    assert w.saved.pending == Pending(1, 0, 'All good to me, thanks!', finished=True)


def test_stop_drops_a_finished_reply_and_says_so(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Done here.\nDONE', False)
    w._agent_stop(w._open)
    assert w.saved.pending is None
    assert w.feedback[-1].endswith("The waiting reply wasn't passed on.")


def test_at_all_takes_a_waiting_reply_along(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it.', False)
    w.send('@all status?')
    assert 'Built it.' in w.codex_sent[-1][1] and w.codex_sent[-1][1].endswith('status?')
    assert w.saved.pending is None
    assert not any('set aside' in n.text for n in w.saved.notes)


def test_a_failed_send_to_the_sender_keeps_the_waiting_reply(tmp_path):
    w = Window(tmp_path)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built it.', False)
    w.claude_accepts = False
    w.send('@Claude also add tests')
    assert w.saved.pending.text == 'Built it.'
    w.claude_accepts = True
    w.send('@Claude also add tests')
    assert w.saved.pending is None
    assert w.feedback[-1] == "Sent to Claude. Claude's waiting reply was set aside."



# -- phase 2's last pieces ------------------------------------------------------------------


def test_the_agents_only_thanking_each_other_stops_the_relay(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Thanks for the review, that all sounds good.', False)
    w._agent_turn_ended('codex', 'x1', 'Glad to help, nothing more from me.', False)
    assert w.saved.stop_reason == 'The agents were only thanking each other, so the relay stopped.'
    assert len(w.codex_sent) == 1 and len(w.claude_sent) == 1


def test_automatic_can_say_only_stops(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    conversation = w.agents.get(w.cid)
    conversation.announce_each = False
    w.agents.put(conversation)
    w._status = lambda text: w.said.append('status: ' + text)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built v1.', False)
    assert w.said[-1].startswith('status: Claude in Fix 247 replied.')
    w._agent_turn_ended('codex', 'x1', 'All fine.\nDONE', False)
    assert not w.said[-1].startswith('status:')  # a stop is always said


def test_hand_offs_offers_automatic_saying_only_stops(tmp_path, monkeypatch):
    w = Window(tmp_path, auto_setting=True)
    w._selected_session = lambda: None
    picks = iter([2, 2])  # automatic, only stops; then a limit of 3
    w._choose = lambda title, prompt, choices, selection=None: next(picks)
    w._agent_check_usage = lambda members, then: then()
    w.on_agent_mode()
    saved = w.saved
    assert saved.mode == MODE_AUTO and saved.turn_limit == 3 and saved.announce_each is False
    assert w.feedback[-1].endswith("you'll hear when it stops or needs you.")


def test_automatic_asks_first_near_a_limit(tmp_path, monkeypatch):
    from thechatplace.ui import agent_chats
    w = Window(tmp_path, auto_setting=True)
    w._limits = {"status": "allowed_warning",
                 "rateLimits": [{"type": "five_hour", "utilization": 0.95}]}
    asked = []
    monkeypatch.setattr(agent_chats.wx, 'MessageBox',
                        lambda text, *a, **k: asked.append(text) or agent_chats.wx.NO)
    monkeypatch.setattr('thechatplace.usage.limit_warning', lambda limits: "You've used 95% of your 5-hour limit.")
    from thechatplace.providers.codex_usage import CodexUsageWarning
    # The shape they really come in: the text is said, not the object.
    w.sessions.usage_warnings = lambda info: [CodexUsageWarning(("codex", 1),
                                                                "Weekly limit 91% used.")]
    ran = []
    w._agent_check_usage(w.saved.members, lambda: ran.append(True))
    action, done = w.work.pop()
    done(action(), False)
    assert asked and "Claude: You've used 95% of your 5-hour limit." in asked[0]
    assert "Codex: Weekly limit 91% used." in asked[0]
    assert not ran and w.feedback[-1] == "Automatic hand-offs weren't turned on."


def test_conversation_usage_lists_each_member(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from thechatplace.ui import dialogs
    w = Window(tmp_path)
    w._limits, w._windows, w._model_windows = None, {}, {}
    w._copy_usage = lambda text, one: None
    transcript = NS(context_tokens=50000, model="claude-opus-5-5")
    w.sessions.open_reader = lambda info: NS(refresh=lambda: True, transcript=transcript)
    w.sessions.usage_lines = lambda info: ["Context 10% full.", "Weekly limit 20% used."]
    shown = []
    w._modal = lambda dialog: shown.append(dialog)
    monkeypatch.setattr(dialogs, 'UsageDialog', lambda parent, lines, copy: lines)
    w.on_agent_usage()
    action, done = w.work.pop()
    done(action(), False)
    lines = shown[-1]
    assert lines[0].startswith('Claude (Builder chat): Context')
    assert lines[1:3] == ['Codex (Reviewer chat): Context 10% full.',
                          'Codex (Reviewer chat): Weekly limit 20% used.']
    assert lines[-1].startswith('Claude plan: Usage limits: not known')



def test_a_new_conversation_answered_no_starts_approving_each(tmp_path, monkeypatch):
    """Both chats new: Claude's known limits are still checked, and No
    starts the conversation approving each hand-off rather than losing it."""
    from thechatplace.ui import agent_chats
    w = Window(tmp_path, auto_setting=True)
    w._limits = {}
    monkeypatch.setattr(agent_chats.wx, 'MessageBox', lambda *a, **k: agent_chats.wx.NO)
    monkeypatch.setattr('thechatplace.usage.limit_warning', lambda limits: "95% used.")
    declined, ran = [], []
    members = [Member('claude', '', ''), Member('codex', '', '')]
    w._agent_check_usage(members, lambda: ran.append(True), declined=lambda: declined.append(1))
    assert w.said[-1] == 'status: Checking usage before automatic hand-offs.'
    w._agent_check_usage(members, lambda: ran.append(True))  # one check at a time
    assert w.feedback[-1] == "Still checking usage."
    action, done = w.work.pop()
    done(action(), False)
    assert declined == [1] and not ran


def test_the_usage_question_never_opens_over_a_dialog(tmp_path, monkeypatch):
    from thechatplace.ui import agent_chats
    w = Window(tmp_path, auto_setting=True)
    w._limits = {}
    monkeypatch.setattr(agent_chats.wx, 'MessageBox', lambda *a, **k: pytest.fail("asked"))
    monkeypatch.setattr('thechatplace.usage.limit_warning', lambda limits: "95% used.")
    w._modal_open = lambda: True
    ran = []
    w._agent_check_usage(w.saved.members, lambda: ran.append(True))
    action, done = w.work.pop()
    done(action(), False)
    assert not ran and w.feedback[-1].startswith("Automatic hand-offs weren't turned on")


def test_your_message_starts_the_thanks_check_afresh(tmp_path):
    """Thanks, then your new request, then a short polite reply to it: the
    relay carries on (the review's case)."""
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_turn_ended('claude', 'c1', 'Built v1.', False)
    w._agent_turn_ended('codex', 'x1', 'Thanks, looks good.', False)
    w.send('@Claude Now add docs')
    w._agent_turn_ended('claude', 'c1', 'Glad to help.', False)
    assert not w.saved.paused


def test_conversation_usage_lists_the_codex_account_once(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from thechatplace.ui import dialogs
    w = Window(tmp_path)
    second = Member('codex', 'x2', 'Codex 2', '')
    w.records[('codex', 'x2')] = OwnSession('x2', 'Second chat', '/work', provider='codex')
    conversation = w.agents.get(w.cid)
    conversation.members[0] = second
    w.agents.put(conversation)
    w._limits, w._windows, w._model_windows = None, {}, {}
    w._copy_usage = lambda text, one: None
    w.sessions.usage_lines = lambda info: ["Context 10% full.",
                                           "Codex account usage is shared across your chats.",
                                           "Weekly limit 20% used."]
    shown = []
    w._modal = lambda dialog: shown.append(dialog)
    monkeypatch.setattr(dialogs, 'UsageDialog', lambda parent, lines, copy: lines)
    w.on_agent_usage()
    action, done = w.work.pop()
    done(action(), False)
    assert shown[-1] == ['Codex 2 (Second chat): Context 10% full.',
                         'Codex (Reviewer chat): Context 10% full.',
                         'Codex account: Codex account usage is shared across your chats.',
                         'Codex account: Weekly limit 20% used.']


def test_conversation_usage_isnt_shown_once_you_move_on(tmp_path, monkeypatch):
    from thechatplace.ui import dialogs
    w = Window(tmp_path)
    w._limits, w._windows, w._model_windows = None, {}, {}
    w.sessions.usage_lines = lambda info: ["Context 10% full."]
    w.sessions.open_reader = lambda info: None
    w._modal = lambda dialog: pytest.fail("shown")
    monkeypatch.setattr(dialogs, 'UsageDialog', lambda parent, lines, copy: lines)
    w.on_agent_usage()
    action, done = w.work.pop()
    w._open = None
    done(action(), False)
    assert not w._agent_usage_busy


# -- phase 3 -------------------------------------------------------------------------------


def _three(w):
    """The window's conversation with a third agent, a second Claude chat."""
    w.records[('claude', 'c2')] = OwnSession('c2', 'Fact chat', '/work')
    conversation = w.agents.get(w.cid)
    conversation.members.append(Member('claude', 'c2', 'Claude 2', 'Check the facts'))
    w.agents.put(conversation)
    return conversation


def test_three_agents_hand_on_round_in_turn(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    _three(w)
    w.turn_limit = 6
    w.send('Plan it.')
    w._agent_turn_ended('claude', 'c1', 'Plan v1.', False)
    assert w.codex_sent[-1][1].split('\n\n')[-1] == 'Plan v1.'
    w._agent_turn_ended('codex', 'x1', 'Codex notes.', False)
    assert [n for n, _t in w.claude_sent] == ['c1', 'c2']
    assert w.claude_sent[-1][1].endswith('Codex notes.')
    w._agent_turn_ended('claude', 'c2', 'Facts checked.', False)
    assert [n for n, _t in w.claude_sent][-1] == 'c1'


def test_three_agents_approve_mode_waits_for_the_next_in_turn(tmp_path):
    w = Window(tmp_path)
    _three(w)
    w.send('@Codex review it.')
    w._agent_turn_ended('codex', 'x1', 'One bug.', False)
    assert (w.saved.pending.from_index, w.saved.pending.to_index) == (1, 2)


def test_files_claude_edited_go_with_its_reply(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_tool_used('claude', 'c1', 'C:/work/app.py')
    w._agent_tool_used('claude', 'c1', 'C:/work/app.py')
    w._agent_tool_used('claude', 'c1', '')
    w._agent_tool_used('codex', 'nobody', 'C:/work/other.py')  # not awaited
    w._agent_turn_ended('claude', 'c1', 'Built it.', False)
    sent = w.codex_sent[-1][1]
    assert sent.endswith('Built it.\n\nFiles Claude changed in that turn:\n- C:/work/app.py')
    assert w._agent_turn_files == {}


def test_files_codex_changed_wait_with_its_reply(tmp_path):
    w = Window(tmp_path)
    w.send('@Codex fix it.')
    w._agent_turn_ended('codex', 'x1', 'Fixed.', False, files=['src/a.py'])
    assert w.saved.pending.files == ('src/a.py',)
    w.send('Thanks, check it.')
    assert w.claude_sent[-1][1].split(agents.USER_ADDS)[0].endswith('- src/a.py')


def test_a_new_turn_starts_its_own_file_list(tmp_path):
    w = Window(tmp_path, mode=MODE_AUTO, auto_setting=True)
    w.send('Build it.')
    w._agent_tool_used('claude', 'c1', 'old.py')
    w._agent_stop(w._open)
    w.send('Build it again.')
    w._agent_turn_ended('claude', 'c1', 'Built.', False)
    assert 'old.py' not in w.codex_sent[-1][1]


def test_three_new_chats_are_named_apart(tmp_path):
    w = Window(tmp_path)
    w.records[('codex', 'x9')] = OwnSession('x9', 'Spare chat', '/work', provider='codex')
    added = []
    w.store.add = added.append
    w._store_write = lambda method, *a, **k: method(*a, **k) or True
    w.refresh_sessions = lambda **k: None
    w._agent_open = lambda conversation: None
    plan = SimpleNamespace(
        title='Pair', folder=str(tmp_path), mode='approve', turn_limit=6, message='',
        first=0, to_all=False,
        members=[SimpleNamespace(info=None, provider='claude', role='Build', permission='auto'),
                 SimpleNamespace(info=w.records[('codex', 'x9')].to_info(), provider='codex',
                                 role='Review', permission=''),
                 SimpleNamespace(info=None, provider='claude', role='Facts', permission='plan')])
    w._agent_create(plan)
    assert [r.title for r in added] == ['Pair: Claude 1', 'Pair: Claude 2']
    assert [r.permission_mode for r in added] == ['auto', 'plan']
    made = [c for c in w.agents.all() if c.title == 'Pair'][0]
    assert [m.name for m in made.members] == ['Claude 1', 'Codex', 'Claude 2']
    assert [m.role for m in made.members] == ['Build', 'Review', 'Facts']
