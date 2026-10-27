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
        self._chat_turns = 1
        self._chat_messages = []
        self._chat_tasks = []
        self._task_statuses = None
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
        self.notified = []
        self.work = []
        self.sessions = SimpleNamespace(owned_action=lambda *a: None)
    def _apply_tasks(self, tasks, first_load):
        from thechatplace.ui.main_frame import MainFrame
        MainFrame._apply_tasks(self, tasks, first_load)

    def _notify(self, *args, **kwargs):
        self.notified.append((args, kwargs))
    def _gone(self):
        return False
    def _steer_row(self, key, text, where):
        self.steer_rows = getattr(self, 'steer_rows', []) + [(text, where)]
    def _steer_status(self, key, text, where):
        self.steer_rows = getattr(self, 'steer_rows', []) + [(text, where)]
    def _steers_turn_over(self, key):
        self.turn_over = getattr(self, 'turn_over', []) + [key]
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
    w._on_codex_update(CodexChatState('owned', 'status unavailable', problem='Use Stop to recheck this chat.',
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
        return 'resumed'
    w.sessions.owned_action = action
    w._stop_codex_owned(w._open)
    action, done = w.work[-1]
    result = action()
    done(result, False)
    assert calls == ['stop', 'recover']
    assert 'No message was resent' in w.said[-1]


def test_failed_idle_recheck_runs_only_once():
    from thechatplace.providers.codex_rpc import CodexRpcError
    w = Window()
    w._codex_states['codex:owned'] = CodexChatState('owned', 'idle', 'turn', 'completed',
                                                    problem='Synthetic problem')
    calls = []
    def action(name, record):
        calls.append(name)
        if name == 'stop':
            return ()
        if name == 'ready':
            return False
        raise CodexRpcError('Synthetic recheck failure')
    w.sessions.owned_action = action
    w._stop_codex_owned(w._open)
    with pytest.raises(CodexRpcError):
        w.work[-1][0]()
    assert calls == ['stop', 'ready', 'recover']


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
    assert calls == ['stop', 'recover'] and not w.said


class LiveWindow(Window):
    """A stand-in window on a real runtime and controller over a synthetic
    connection. Runtime updates wait, as with wx.CallAfter, and reach the
    window in order before each worker's result."""
    def __init__(self, tmp_path, monkeypatch):
        from test_codex_auto_review import SavedHistoryConnection
        import thechatplace.ui.codex_chats as module
        from thechatplace.providers.codex import CodexProvider
        from thechatplace.providers.codex_runtime import CodexChatRuntime
        from thechatplace.session_controller import SessionController
        super().__init__()
        monkeypatch.setattr(module, 'codex_permission_prompt', lambda *args: 'Synthetic prompt')
        self.updates = []
        self.connection = SavedHistoryConnection()
        self.runtime = CodexChatRuntime(self.updates.append, factory=lambda exe: self.connection)
        self.sessions = SessionController([CodexProvider(self.connection, runtime=self.runtime)])
        self.record = self.runtime.create(str(tmp_path), 'Synthetic chat')
        self._open = self.record.to_info()
        self.key = self._open.key
        self._codex_policy_pending = set()
        self.selected = ('workspace-write', 'on-request', 'auto_review')
        self.saves = True
        self.flush()

    def flush(self):
        while self.updates:
            self._on_codex_update(self.updates.pop(0))

    def _codex_work(self, action, done):
        from thechatplace.codex_diagnostics import safe_reason
        try:
            result, error = action(), False
        except Exception as exc:
            result, error = safe_reason(exc) or None, True
        self.flush()
        done(result, error)

    def _change_setting(self, title, prompt, choices, now):
        return self.selected

    def _store_write(self, method, *args, **kwargs):
        return self.saves

    def finish_turn(self):
        from test_codex_runtime import wait
        from thechatplace.providers.codex_events import decode_event
        native = self.record.cli_session_id
        self.runtime.send(self.record, 'Initial synthetic turn')
        self.connection.events.append(decode_event('turn/completed', {
            'threadId': native, 'turn': {'id': 'turn-1', 'status': 'completed'}}))
        wait(lambda: self.runtime.snapshot(native).outcome == 'completed')
        self.connection.saved_threads.add(native)
        self.flush()

    def spoken_after(self, start):
        self.flush()
        return self.said[start:]


@pytest.fixture
def live(tmp_path, monkeypatch):
    w = LiveWindow(tmp_path, monkeypatch)
    try:
        yield w
    finally:
        w.runtime.close()


@pytest.mark.parametrize('failure', ['auto_review_unavailable', 'private', 'save'])
def test_failed_permission_change_is_said_once_ending_with_stop(live, monkeypatch, failure):
    from thechatplace.providers.codex_runtime import STOP_TO_RECHECK
    live.finish_turn()
    request = live.connection.request
    def refuse(method, params):
        if method == 'thread/start' and params.get('ephemeral'):
            if failure == 'private':
                raise ValueError('private backend detail')
            if failure == 'auto_review_unavailable':
                result = request(method, params)
                result['approvalsReviewer'] = 'user'
                return result
        return request(method, params)
    monkeypatch.setattr(live.connection, 'request', refuse)
    live.saves = failure != 'save'
    start = len(live.said)
    live._change_codex_permissions(live._open)
    spoken = live.spoken_after(start)
    assert spoken[0] == 'Verifying Codex permissions.'
    assert len(spoken) == 2, spoken
    assert spoken[1].endswith(STOP_TO_RECHECK) and spoken[1].count('Use Stop') == 1
    assert 'private' not in spoken[1]
    if failure == 'auto_review_unavailable':
        assert 'automatic approval review' in spoken[1]
    assert live._codex_status_text(live._codex_states[live.key]).endswith(STOP_TO_RECHECK)
    assert not live._codex_policy_pending
    assert not live.runtime.ready(live.record)


def test_refused_permission_change_does_not_replay_an_earlier_problem(live):
    from thechatplace.providers.codex_rpc import CodexRememberedApprovalError
    live.finish_turn()
    native = live.record.cli_session_id
    earlier = CodexRememberedApprovalError.REASONS['kept_uncertain']
    with live.runtime._lock:
        live.runtime._problems[native] = earlier
    live.runtime._notify(native)
    live.flush()
    start = len(live.said)
    live._change_codex_permissions(live._open)  # Refused before anything is checked
    spoken = live.spoken_after(start)
    assert spoken[0] == 'Verifying Codex permissions.'
    assert len(spoken) == 2, spoken
    assert spoken[1].startswith('Could not change Codex permissions.')
    assert earlier not in spoken[1] and spoken[1].lower().count('use stop') == 1
    assert live.runtime.snapshot(native).problem == earlier
    assert not live._codex_policy_pending


def test_successful_permission_change_says_only_the_new_mode(live):
    live.finish_turn()
    start = len(live.said)
    live._change_codex_permissions(live._open)
    spoken = live.spoken_after(start)
    assert spoken[0] == 'Verifying Codex permissions.'
    assert len(spoken) == 2 and spoken[1].startswith('Codex permissions for Synthetic chat: ')
    assert live.runtime.ready(live.record) and not live._codex_policy_pending


@pytest.mark.parametrize('path', ['permissions', 'rechecked', 'resumed'])
def test_stop_announces_the_recovery_reopen_actually_made(live, path):
    from thechatplace.providers.codex_rpc import CodexRememberedApprovalError
    live.finish_turn()
    native = live.record.cli_session_id
    if path == 'permissions':
        live.saves = False
        live._change_codex_permissions(live._open)
    elif path == 'rechecked':
        with live.runtime._lock:
            live.runtime._problems[native] = CodexRememberedApprovalError.REASONS['kept_uncertain']
        live.runtime._notify(native)
    else:
        live.runtime._proofs.clear()  # As if this chat was never opened here
    live.flush()
    assert not live.runtime.ready(live.record)
    start = len(live.said)
    live._stop_codex_owned(live._open)
    spoken = live.spoken_after(start)
    assert live.runtime.ready(live.record)
    resumed = any(method == 'thread/resume' for method, _ in live.connection.calls)
    assert resumed == (path == 'resumed')
    assert live.connection.turns == 1
    expected = {'permissions': 'Saved Codex permissions rechecked. You can send again. No message was sent.',
                'rechecked': 'Codex chat rechecked. You can send again. No message was sent.',
                'resumed': 'Codex chat reconnected after verifying its previous turn ended. No message was resent.'}
    assert spoken[-1] == expected[path]


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


def test_codex_requests_and_completion_notify_once_with_short_request_summary():
    from thechatplace.providers.codex_requests import CodexRequest, COMMAND
    w = Window()
    request = CodexRequest('r', COMMAND, 'owned', 'turn', 'cmd', command='git status')
    waiting = CodexChatState('owned', 'needs you', 'turn', 'inProgress', requests=(request,))
    w._on_codex_update(waiting)
    w._on_codex_update(waiting)
    assert len(w.notified) == 1 and w.notified[0][1] == {'needs_you': True}
    assert 'git status' in w.said[-1]
    done = CodexChatState('owned', 'idle', 'turn', 'completed', replies=(('a', 'Done'),))
    w._on_codex_update(done)
    w._on_codex_update(done)
    assert len(w.notified) == 2 and 'Done' in w.notified[-1][0][-1]


def test_failed_codex_turn_is_a_needs_you_notification_but_stop_is_quiet():
    w = Window()
    w._on_codex_update(CodexChatState('owned', 'idle', 'failed', 'failed'))
    assert w.notified[-1][1] == {'needs_you': True}
    before = len(w.notified)
    w._on_codex_update(CodexChatState('owned', 'idle', 'stopped', 'interrupted'))
    assert len(w.notified) == before


def test_saved_rule_inventory_ignores_stale_and_closed_dialog_callbacks():
    from thechatplace.ui.dialogs import NewSessionDialog
    calls, updates = [], []
    dialog = SimpleNamespace(
        IsBeingDeleted=lambda: False, provider=lambda: 'codex', folder=Text('/first'),
        _load_rules=lambda folder, done: calls.append((folder, done)),
        _rules_for=None, _rule_count=8, _rules_error='',
        _on_provider=lambda: updates.append(True))
    NewSessionDialog._refresh_rules(dialog)
    assert dialog._rule_count is None
    dialog.folder.SetValue('/second')
    NewSessionDialog._refresh_rules(dialog)
    calls[0][1](5, False)
    assert dialog._rule_count is None
    calls[1][1](2, False)
    assert dialog._rule_count == 2
    dialog.IsBeingDeleted = lambda: True
    calls[1][1](9, False)
    NewSessionDialog._refresh_rules(dialog)
    assert dialog._rule_count == 2 and len(calls) == 2


def test_permission_change_feedback_keeps_the_safe_refusal(monkeypatch):
    from thechatplace.ui import codex_chats
    w = Window()
    w._codex_policy_pending = set()
    w.sessions = SimpleNamespace(can_create=lambda provider: True)
    w._change_setting = lambda *args: ('workspace-write', 'on-request')
    monkeypatch.setattr(codex_chats, 'codex_permission_prompt', lambda *args: 'Synthetic prompt')
    w._change_codex_permissions(w._open)
    reason = 'Project rules were refused. Rules folder: /synthetic/.codex/rules'
    w.work[0][1](reason, True)
    assert w.said[-1] == reason



def test_codex_task_updates_announce_once_and_keep_last_states_on_stop():
    from dataclasses import replace
    from thechatplace.tasklist import TaskStep
    window = Window()
    active = CodexChatState('owned', 'working', turn_id='turn', outcome='inProgress',
        tasks=(TaskStep('first', 'Find the cause', status='in_progress'),
               TaskStep('second', 'Run the tests')), plan_received=True)
    window._on_codex_update(active)
    assert not any(text.startswith('Done:') for text in window.said)
    updated = replace(active, tasks=(replace(active.tasks[1], status='in_progress'),
                                    replace(active.tasks[0], status='completed')))
    window._on_codex_update(updated)
    window._on_codex_update(updated)
    assert window.said.count('Done: Find the cause. 1 of 2.') == 1
    window._on_codex_update(replace(updated, state='idle', outcome='interrupted'))
    assert [(t.subject, t.status) for t in window._chat_tasks] == [
        ('Run the tests', 'in_progress'), ('Find the cause', 'completed')]



def test_live_plan_uses_real_codex_history_turns_for_message_scope():
    from thechatplace.tasklist import TaskStep
    from thechatplace.providers.codex import transcript_from_thread
    from test_codex_provider import history
    from thechatplace import lists
    window = Window()
    state = CodexChatState('owned', 'working', 'turn-2', 'inProgress',
                           tasks=(TaskStep('test', 'Run the tests'),), plan_received=True)
    data = history('owned')
    old = transcript_from_thread(data)
    assert window._codex_task_steps(state, old.turns, old.messages)[0].turn == 2
    data['turns'].append({**data['turns'][0], 'id': 'turn-2'})
    current = transcript_from_thread(data)
    tasks = window._codex_task_steps(state, current.turns, current.messages)
    for message in current.messages:
        rows = lists.build(lists.TASKS, lists.SCOPE_MESSAGE, current.messages, message,
                           [], [], str, tasks=tasks)
        assert bool(rows) == (message.turn == 2)
    assert tasks[0].turn == tasks[0].changed_turn == 2


def test_turn_without_plan_keeps_previous_tasks_and_announcement_baseline():
    from dataclasses import replace
    from thechatplace.tasklist import TaskStep
    window = Window()
    planned = CodexChatState('owned', 'working', 'first', 'inProgress',
        tasks=(TaskStep('first', 'Find the cause', status='in_progress'),), plan_received=True)
    window._on_codex_update(planned)
    window._on_codex_update(replace(planned, state='idle', outcome='completed'))
    before = window._chat_tasks[:], window._task_statuses.copy()
    unplanned = CodexChatState('owned', 'working', 'second', 'inProgress')
    window._on_codex_update(unplanned)
    window._on_codex_update(replace(unplanned, state='idle', outcome='completed'))
    assert (window._chat_tasks, window._task_statuses) == before
    window.said.clear()
    completed = replace(unplanned, state='idle', outcome='completed', plan_received=True,
                        tasks=(replace(planned.tasks[0], status='completed'),), replies=(('reply', 'Finished.'),))
    # A first task update in a later turn must compare with the prior list.
    completed = replace(completed, turn_id='third')
    window._on_codex_update(completed)
    assert window.said[0] == 'Done: Find the cause. 1 of 1.'
    assert 'Finished.' in window.said[1]
    window._on_codex_update(completed)
    assert window.said.count('Done: Find the cause. 1 of 1.') == 1


def test_explicit_empty_plan_clears_the_previous_task_list():
    from dataclasses import replace
    from thechatplace.tasklist import TaskStep
    window = Window()
    state = CodexChatState('owned', 'working', 'turn', 'inProgress',
                           tasks=(TaskStep('test', 'Run the tests'),), plan_received=True)
    window._on_codex_update(state)
    window._on_codex_update(replace(state, tasks=()))
    assert not window._chat_tasks


# -- Chat Permissions (#329) ---------------------------------------------------------------


def test_permission_change_tells_chat_permissions_when_it_is_finished(monkeypatch):
    from thechatplace.ui import codex_chats
    w = Window()
    w._codex_policy_pending = set()
    finished = []
    w.sessions = SimpleNamespace(can_create=lambda provider: True)
    monkeypatch.setattr(codex_chats, 'codex_permission_prompt', lambda *args: 'Synthetic prompt')
    w._change_setting = lambda *args: None
    w._change_codex_permissions(w._open, after=lambda: finished.append('cancelled'))
    w._change_setting = lambda *args: ('workspace-write', 'on-request')
    w._change_codex_permissions(w._open, after=lambda: finished.append('refused'))
    w.work[0][1]('Refused', True)
    assert finished == ['cancelled', 'refused']


def test_permission_change_after_runs_once_when_saved_and_when_already_pending(monkeypatch):
    from thechatplace.ui import codex_chats
    from dataclasses import replace
    w = Window()
    w._codex_policy_pending = set()
    finished = []
    w.sessions = SimpleNamespace(can_create=lambda provider: True, owned_action=lambda *a: None)
    monkeypatch.setattr(codex_chats, 'codex_permission_prompt', lambda *args: 'Synthetic prompt')
    w._change_setting = lambda *args: ('workspace-write', 'on-request')
    w._change_codex_permissions(w._open, after=lambda: finished.append('saved'))
    change = SimpleNamespace(record=lambda: replace(w.record, provider_options={
        'sandbox': 'workspace-write', 'approval_policy': 'on-request'}))
    w.work[0][1](change, False)        # prepared, saved, then finished
    w.work[1][1](None, False)
    assert finished == ['saved'] and not w._codex_policy_pending
    w._codex_policy_pending.add(w._open.key)
    w._change_codex_permissions(w._open, after=lambda: finished.append('pending'))
    assert finished == ['saved', 'pending'] and len(w.work) == 2


def _run_work(w, failure=None):
    """Run the queued worker as _codex_work would: an exception is (reason, True)."""
    from thechatplace.codex_diagnostics import safe_reason
    action, done = w.work.pop()
    try:
        result, error = action(), False
    except Exception as exc:  # noqa: BLE001 - what _codex_work does
        result, error = safe_reason(exc) or None, True
    done(result, error)


def test_steer_accepted_is_said_and_the_text_is_not_restored():
    w = Window()
    calls = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: calls.append(a) or 'turn')
    w.reply_text.value = ''
    w._steer_codex_owned(w.record, 'Also the docs')
    _run_work(w)
    assert calls == [('steer', w.record, 'Also the docs')]
    assert w.said[-1] == 'Steered My chat: Also the docs.'
    assert w.reply_text.value == ''


@pytest.mark.parametrize('queued', [False, True])
def test_steer_refused_puts_the_text_back_or_keeps_it_queued(queued):
    from thechatplace.providers.codex_rpc import CodexSteerRefused
    w = Window()
    w.reply_text.value = ''

    def refuse(*a):
        raise CodexSteerRefused('not_running')
    w.sessions = SimpleNamespace(owned_action=refuse)
    w._steer_codex_owned(w.record, 'Too late', index=0 if queued else None)
    _run_work(w)
    reason = CodexSteerRefused.REASONS['not_running']
    if queued:
        assert w.said[-1] == f'{reason} It stays queued.'
        assert w.reply_text.value == ''
    else:
        assert w.said[-1] == f'{reason} Your message is back in the reply box.'
        assert w.reply_text.value == 'Too late'


def test_steer_not_confirmed_comes_back_with_a_warning():
    from thechatplace.providers.codex_rpc import CodexConnectionFailure
    w = Window()
    w.reply_text.value = ''

    def lost(*a):
        raise CodexConnectionFailure('stream_closed')
    w.sessions = SimpleNamespace(owned_action=lost)
    w._steer_codex_owned(w.record, 'Maybe sent')
    _run_work(w)
    assert w.reply_text.value == 'Maybe sent'
    assert 'could not confirm' in w.said[-1]


def test_turn_status_mentions_a_steer_waiting_to_be_accepted():
    state = CodexChatState('owned', 'working', 'turn', 'inProgress', steering=('Also the docs',))
    assert 'steered message waiting for Codex to accept it' in Window._codex_status_text(state)


def test_a_queued_steer_that_stop_sent_back_says_where_it_is():
    from thechatplace.providers.codex_rpc import CodexSteerRefused
    w = Window()
    w.reply_text.value = ''

    def refuse(*a):
        error = CodexSteerRefused('stopping')
        error.returned = True
        raise error
    w.sessions = SimpleNamespace(owned_action=refuse)
    w._steer_codex_owned(w.record, 'Queued one', index=0)
    _run_work(w)
    assert w.said[-1].endswith('It is back in the reply box.')


@pytest.mark.parametrize('outcome', ['accepted', 'refused', 'unconfirmed'])
def test_a_steered_message_shows_until_its_fate_is_known(outcome):
    from thechatplace.providers.codex_rpc import CodexSteerRefused, CodexConnectionFailure
    w = Window()

    def act(*a):
        if outcome == 'refused':
            raise CodexSteerRefused('not_running')
        if outcome == 'unconfirmed':
            raise CodexConnectionFailure('stream_closed')
        return 'turn'
    w.sessions = SimpleNamespace(owned_action=act)
    w._codex_states['codex:owned'] = CodexChatState('owned', 'working', 'turn', 'inProgress')
    w._steer_codex_owned(w.record, 'Also the docs')
    assert w.steer_rows == [('Also the docs', 'waiting for Codex')]  # at once
    _run_work(w)
    expected = 'accepted by Codex' if outcome == 'accepted' else None
    assert w.steer_rows[-1] == ('Also the docs', expected)


def test_a_steer_accepted_after_the_turn_ended_waits_for_the_history():
    # Stop before Codex answered: it may never be used, so the next history
    # read decides whether it shows there or comes back to the reply box.
    w = Window()
    w.sessions = SimpleNamespace(owned_action=lambda *a: 'turn')
    w._codex_states['codex:owned'] = CodexChatState('owned', 'idle', 'turn', 'interrupted')
    w._steer_codex_owned(w.record, 'Also the docs')
    _run_work(w)
    assert w.steer_rows[-1] == ('Also the docs', 'accepted by Codex')
    assert w.turn_over == ['codex:owned']


def test_a_steer_accepted_into_a_turn_since_replaced_is_settled_by_the_history():
    # The turn completed and a queued message's turn started before
    # Codex's answer to the steer came.
    w = Window()
    w._codex_finished = {('codex', 'owned', 'turn-1')}
    w.sessions = SimpleNamespace(owned_action=lambda *a: 'turn-1')
    w._codex_states['codex:owned'] = CodexChatState('owned', 'working', 'turn-2', 'inProgress')
    w._steer_codex_owned(w.record, 'Also the docs')
    _run_work(w)
    assert w.turn_over == ['codex:owned']


def test_a_steer_accepted_into_the_running_turn_waits_for_it():
    w = Window()
    w.sessions = SimpleNamespace(owned_action=lambda *a: 'turn-1')
    w._codex_states['codex:owned'] = CodexChatState('owned', 'working', 'turn-1', 'inProgress')
    w._steer_codex_owned(w.record, 'Also the docs')
    _run_work(w)
    assert not getattr(w, 'turn_over', [])


def test_a_newer_turn_alone_doesnt_end_a_steer_whose_turn_isnt_known_to_be_over():
    w = Window()
    w.sessions = SimpleNamespace(owned_action=lambda *a: 'turn-2')
    w._codex_states['codex:owned'] = CodexChatState('owned', 'working', 'turn-1', 'inProgress')
    w._steer_codex_owned(w.record, 'Also the docs')
    _run_work(w)
    assert not getattr(w, 'turn_over', [])


def test_no_turn_id_back_is_not_taken_for_another_turn():
    w = Window()
    w.sessions = SimpleNamespace(owned_action=lambda *a: None)
    w._codex_states['codex:owned'] = CodexChatState('owned', 'working', 'turn-1', 'inProgress')
    w._steer_codex_owned(w.record, 'Also the docs')
    _run_work(w)
    assert not getattr(w, 'turn_over', [])
