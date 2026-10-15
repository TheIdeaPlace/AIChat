"""Callback routing and draft recovery without a native GUI or real Codex."""
from types import SimpleNamespace

import pytest

pytest.importorskip('wx')
from thechatplace.ui.codex_chats import CodexChats
from thechatplace.providers.codex_runtime import CodexChatState
from thechatplace.own_store import OwnSession


class Text:
    def __init__(self, value=''):
        self.value = value
    def GetValue(self):
        return self.value
    def SetValue(self, value):
        self.value = value


class Window(CodexChats):
    def __init__(self):
        self.record = OwnSession('owned', 'My chat', '/workspace', provider='codex')
        self._open = self.record.to_info()
        self._open_generation = 0
        self._reader = object()
        self.reply_text = Text('New draft')
        self._codex_states = {}
        self._codex_drafts = {}
        self._codex_send_pending = {}
        self._codex_delayed_updates = {}
        self._codex_finished = set()
        self._codex_preparing = {}
        self._codex_preparation_running = set()
        self.store = SimpleNamespace(get=lambda *a, **k: self.record, update=lambda *a, **k: None)
        self.speech = SimpleNamespace(announce='full', announce_own=True)
        self.said = []
        self.work = []
        self.sessions = SimpleNamespace(owned_action=lambda *a: None)
    def _gone(self):
        return False
    def _codex_work(self, action, done):
        self.work.append((action, done))
    def _feedback(self, text):
        self.said.append(text)
    def _say(self, text, *, interrupt=True):
        self.said.append(text)
    def _status(self, text):
        pass
    def _store_write(self, method, *a, **k):
        method(*a, **k)
        return True
    def _update_heading(self):
        pass
    _update_send_state = _update_heading
    _rebuild_chat_list = _update_heading
    _refresh_chat = _update_heading
    refresh_sessions = _update_heading


def test_unsent_text_routes_to_its_own_chat_and_completion_is_announced_once():
    w = Window()
    state = CodexChatState('owned', 'idle', 'turn', 'completed', replies=(('item', 'Answer'),),
                           unsent=('Queued text',))
    w._on_codex_update(state)
    assert w.reply_text.value == 'Queued text\n\nNew draft'
    assert w._reader is None and w._open_generation == 1
    w._on_codex_update(CodexChatState('owned', 'idle', 'turn', 'completed', replies=state.replies))
    assert sum('Answer' in (text or '') for text in w.said) == 1
    assert w._open_generation == 1
    w._open = None
    w._on_codex_update(CodexChatState('owned', 'idle', unsent=('Other text',)))
    assert w._codex_drafts['codex:owned'].startswith('Other text')
    assert w.reply_text.value == 'Queued text\n\nNew draft'


def test_updates_wait_for_send_result_and_failed_send_preserves_new_draft():
    w = Window()
    w._send_codex_owned(w.record, 'Submitted')
    state = CodexChatState('owned', 'idle', 'turn', 'completed', replies=(('item', 'Answer'),))
    w._on_codex_update(state)
    assert not w._codex_states
    w.work[0][1](None, True)
    assert w.reply_text.value == 'Submitted\n\nNew draft'
    assert w._codex_states['codex:owned'] == state
    assert not w._codex_send_pending and not w._codex_delayed_updates


def test_preparing_is_reserved_once_and_cleared_after_failure():
    w = Window()
    w._codex_preparing['codex:owned'] = 4
    w._prepare_codex_owned(4)
    w._prepare_codex_owned(4)
    assert len(w.work) == 1
    w.work[0][1](None, True)
    assert not w._codex_preparing and not w._codex_preparation_running
    assert 'Automatic resume is disabled' in w.said[-1]


def test_closing_ui_discards_worker_result(monkeypatch):
    import thechatplace.ui.codex_chats as module
    w = Window()
    callbacks = []
    monkeypatch.setattr(module.wx, 'CallAfter', lambda *a: callbacks.append(a))
    w._pool = SimpleNamespace(submit=lambda f: f())
    CodexChats._codex_work(w, lambda: 'created', lambda *a: w.said.append(a))
    w._gone = lambda: True
    callbacks[0][0](*callbacks[0][1:])
    assert not w.said


def test_worker_reports_only_allowlisted_reason_to_send_callback(monkeypatch, tmp_path):
    import thechatplace.ui.codex_chats as module
    from thechatplace.providers.codex_rpc import CodexTurnRejected
    w = Window()
    callbacks = []
    monkeypatch.setattr(module.wx, 'CallAfter', lambda *args: callbacks.append(args))
    monkeypatch.setattr(module, 'record_failure', lambda error: None)
    w._pool = SimpleNamespace(submit=lambda f: f())
    def fail():
        error = CodexTurnRejected('not_idle')
        error.args = ('private backend content',)
        raise error
    results = []
    CodexChats._codex_work(w, fail, lambda *args: results.append(args))
    callbacks[0][0](*callbacks[0][1:])
    assert results == [(CodexTurnRejected.REASONS['not_idle'], True)]


def test_progressive_send_snapshots_are_coalesced_without_losing_terminal_or_unsent_text():
    w = Window()
    w._codex_send_pending['codex:owned'] = 1
    for n in range(100):
        w._on_codex_update(CodexChatState('owned', 'working', 'turn', 'inProgress',
                                        replies=(('item', str(n)),)))
    assert len(w._codex_delayed_updates['codex:owned']) == 1
    w._on_codex_update(CodexChatState('owned', 'idle', 'turn', 'completed'))
    w._on_codex_update(CodexChatState('owned', 'idle', 'turn', 'completed', unsent=('Keep me',)))
    assert len(w._codex_delayed_updates['codex:owned']) == 3


def test_send_and_queue_read_back_use_the_own_message_setting():
    w = Window()
    w._send_codex_owned(w.record, 'My words')
    w.work[-1][1](False, False)
    assert 'My words' in w.said[-1]
    w._send_codex_owned(w.record, 'Queued words')
    w.work[-1][1](True, False)
    assert 'Queued words' in w.said[-1] and 'Queued' in w.said[-1]
    w.speech.announce_own = False
    w._send_codex_owned(w.record, 'Do not read back')
    w.work[-1][1](False, False)
    assert 'Do not read back' not in w.said[-1]


def test_working_transition_updates_status_without_interrupting_readback(monkeypatch):
    import thechatplace.ui.codex_chats as module
    w = Window()
    statuses = []
    w._status = statuses.append
    monkeypatch.setattr(module.time, 'monotonic', lambda: 75)
    state = CodexChatState('owned', 'working', 'turn', 'inProgress', started_at=10,
                           activity='Running command: git status')
    w._on_codex_update(state)
    w._on_codex_update(state)
    assert statuses == ['My chat: Codex is working.']
    assert not w.said
    status = w._codex_status_text(state)
    assert 'git status' in status and '1 minute 5 seconds' in status


@pytest.mark.parametrize('level', ['full', 'summary', 'silent'])
@pytest.mark.parametrize('read_back', [True, False])
def test_send_then_immediate_reply_never_interrupts_readback(level, read_back):
    from thechatplace import announce
    w = Window()
    w.speech.announce, w.speech.announce_own = level, read_back
    calls = []
    w._feedback = lambda text: calls.append((text, False)) if text else None
    w._say = lambda text, *, interrupt=True: calls.append((text, interrupt)) if text else None
    w._send_codex_owned(w.record, 'My first sentence. Another sentence.')
    w._on_codex_update(CodexChatState('owned', 'working', 'turn', 'inProgress'))
    w._on_codex_update(CodexChatState('owned', 'idle', 'turn', 'completed', replies=(('reply', 'Answer'),)))
    w.work[-1][1](False, False)
    expected = announce.sent_text(w.record.title, 'My first sentence. Another sentence.', level, read_back)
    assert calls[0] == (expected, False)
    assert all(not interrupt for _, interrupt in calls)
    assert not any('Codex is working' in text for text, _ in calls)


def test_background_unsent_text_names_its_draft_and_never_reads_the_contents():
    w = Window()
    w._open = None
    w._on_codex_update(CodexChatState('owned', 'idle', unsent=('Secret draft', 'More secret')))
    assert w.said == ['2 unsent messages are back in the draft for My chat.']


def test_recovery_and_error_speech_do_not_interrupt_readback():
    w = Window()
    calls = []
    w._say = lambda text, *, interrupt=True: calls.append(('interrupt' if interrupt else 'queue', text))
    w._feedback = lambda text: calls.append(('queue', text))
    w._on_codex_update(CodexChatState('owned', 'status unavailable', problem='Reload this chat.',
                                    unsent=('Recovered text',)))
    assert [kind for kind, _text in calls] == ['queue', 'queue']
    assert 'back in the reply box' in calls[-1][1]


def test_status_distinguishes_unconfirmed_start_and_unavailable_state():
    w = Window()
    assert 'Starting' in w._codex_status_text(CodexChatState('owned', 'working'))
    assert 'unavailable' in w._codex_status_text(CodexChatState('owned', 'status unavailable'))


def test_stop_explicitly_recovers_disconnected_chat_without_sending():
    from thechatplace.providers.codex_rpc import CodexRpcError
    w = Window()
    calls = []
    def action(name, record):
        calls.append(name)
        if name == 'stop':
            raise CodexRpcError('Disconnected')
    w.sessions.owned_action = action
    w._stop_codex_owned(w._open)
    action, done = w.work[-1]
    result = action()
    done(result, False)
    assert calls == ['stop', 'reopen']
    assert 'No message was resent' in w.said[-1]


def test_stop_never_reports_recovery_when_native_state_is_unfinished():
    from thechatplace.providers.codex_rpc import CodexRpcError, CodexTurnRejected
    w = Window()
    calls = []
    def action(name, record):
        calls.append(name)
        if name == 'stop':
            raise CodexRpcError('Disconnected')
        raise CodexTurnRejected('not_idle')
    w.sessions.owned_action = action
    w._stop_codex_owned(w._open)
    with pytest.raises(CodexTurnRejected):
        w.work[-1][0]()
    assert calls == ['stop', 'reopen'] and not w.said


def test_declined_request_notice_is_spoken_once_without_interrupting_or_failing_turn():
    w = Window()
    calls = []
    w._say = lambda text, *, interrupt=True: calls.append((text, interrupt))
    state = CodexChatState('owned', 'working', 'turn', 'inProgress',
                           notice='An unsupported Codex request was declined. No permission was granted.')
    w._on_codex_update(state)
    w._on_codex_update(state)
    assert len(calls) == 1 and calls[0][1] is False
    assert 'unsupported Codex request was declined' in calls[0][0]
    assert not state.problem and 'declined' in w._codex_status_text(state)


def test_codex_context_warning_is_queued_once_until_compaction():
    from types import MethodType
    from thechatplace.ui.main_frame import MainFrame
    from thechatplace.providers.codex_tokens import CodexTokenUsage
    w = Window()
    w._warned = set()
    w._context = lambda info: (0, 0)
    w._update_status_context = lambda: None
    w._check_context = MethodType(MainFrame._check_context, w)
    calls = []
    w._say = lambda text, *, interrupt=True: calls.append((text, interrupt))
    report = CodexTokenUsage(800, 0, 800, 50000, 1000)
    state = CodexChatState('owned', 'working', 'turn', 'inProgress', token_usage=report)
    w._on_codex_update(state)
    w._on_codex_update(state)
    assert len(calls) == 1 and '80%' in calls[0][0] and calls[0][1] is False
    from dataclasses import replace
    w._on_codex_update(replace(state, context_epoch=1))
    assert len(calls) == 2
    w._on_codex_update(replace(state, context_epoch=2, state='status unavailable'))
    assert len(calls) == 2


def test_account_warnings_are_shared_deduplicated_and_do_not_interrupt_readback():
    from thechatplace.providers.codex_usage import CodexUsageWarning
    w = Window()
    reports = [CodexUsageWarning(('main', 'primary', 300, 1000, 80), 'Codex 80% used.')]
    w.sessions.usage_warnings = lambda info: tuple(reports)
    spoken = []
    w._say = lambda text, **options: spoken.append((text, options))
    w._check_codex_usage(w.record.to_info())
    w._check_codex_usage(w.record.to_info())
    assert len(w.work) == 1  # one in-flight query across chats
    action, done = w.work.pop()
    done(action(), False)
    assert spoken == [('Codex 80% used.', {'interrupt': False})]
    w._check_codex_usage(w.record.to_info())
    action, done = w.work.pop()
    done(action(), False)
    assert len(spoken) == 1
    reports[:] = [CodexUsageWarning(('main', 'primary', 300, 2000, 80), 'New window 80%.')]
    w._check_codex_usage(w.record.to_info())
    action, done = w.work.pop()
    done(action(), False)
    assert len(spoken) == 2 and not spoken[-1][1]['interrupt']


def test_unavailable_account_warning_query_is_silent_and_retryable():
    w = Window()
    w.sessions.usage_warnings = lambda info: ()
    w._check_codex_usage(w.record.to_info())
    _action, done = w.work.pop()
    done(None, True)
    assert not w.said and not w._codex_usage_check_pending
    w._check_codex_usage(w.record.to_info())
    assert len(w.work) == 1


def test_terminal_owned_turn_schedules_account_usage_after_reply():
    w = Window()
    w.sessions.usage_warnings = lambda info: ()
    state = CodexChatState('owned', 'idle', 'turn', 'completed', replies=(('a', 'Done'),))
    w._on_codex_update(state)
    assert len(w.work) == 1 and any('Done' in text for text in w.said)
    w._on_codex_update(state)
    assert len(w.work) == 1


def test_terminal_usage_queries_coalesce_one_followup_for_newer_threshold():
    from thechatplace.providers.codex_usage import CodexUsageWarning
    w = Window()
    report = [CodexUsageWarning(('main', 'primary', 300, 1000, 80), '80 percent')]
    w.sessions.usage_warnings = lambda info: tuple(report)
    w._check_codex_usage(w.record.to_info())
    action, done = w.work.pop()
    sampled = action()
    for _ in range(5):
        w._check_codex_usage(w.record.to_info())
    assert not w.work
    report[:] = [CodexUsageWarning(('main', 'primary', 300, 1000, 100), '100 percent')]
    done(sampled, False)
    assert len(w.work) == 1
    action, done = w.work.pop()
    done(action(), False)
    assert w.said == ['80 percent', '100 percent']
    assert not w.work and not w._codex_usage_check_pending
