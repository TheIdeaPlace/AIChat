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
    def _session_tools_config(self, caller):
        return None

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


def test_new_copilot_chat_with_too_long_first_message_is_not_created():
    """Copilot still refuses an over-long first message before creating
    anything (its limit is its own); Codex now hands its over-long first
    message over in a file instead, so its chat is created."""
    w = Window()
    w._can_edit = lambda info: False
    w._copy_text = lambda text: True
    w._create_codex_owned('/workspace', 'Title', 'x' * 128001, provider='copilot')
    assert not w.work
    assert 'No Copilot chat was started' in w.said[-1]
    assert 'too long' in w.said[-1] and '128,001' in w.said[-1] and '128,000' in w.said[-1]
    w._create_codex_owned('/workspace', 'Title', 'x' * 32001)
    assert len(w.work) == 1
    w._create_codex_owned('/workspace', 'Title', 'Hello')
    assert len(w.work) == 2


def _long_temp():
    from pathlib import Path
    from thechatplace import codex_long_messages
    return Path(codex_long_messages.temp_dir())


def _saved_path(prompt):
    import re
    return _long_temp() / re.search(r'thechatplace-long-message-\S+\.txt', prompt).group(0)


def _read_saved(path):
    # UTF-8, with a byte order mark on Windows (Windows PowerShell 5.1).
    return path.read_text(encoding='utf-8-sig')


def test_too_long_codex_message_is_saved_and_codex_is_asked_to_read_it():
    """Over the limit: the whole message goes to a temp file and Codex is
    sent a short message asking it to read that file as the message (#625)."""
    w = Window()
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    text = 'head\n' + 'x' * 40000 + '\nTAIL-MARKER é'
    w._send_codex_owned(w.record, text)
    action, done = w.work[0]
    action()
    assert sent[0][0] == 'send' and sent[0][1] is w.record
    prompt = sent[0][2]
    assert len(prompt) < 32000 and '40,019' in prompt
    assert 'placed in this conversation by The Chat Place' in prompt and "as the user's message" in prompt
    path = _saved_path(prompt)
    assert _read_saved(path) == text
    done(False, False)
    # Said without the path: it's gone in moments, and long to hear.
    assert 'given it as a file to read' in w.said[-1] and path.name not in w.said[-1]
    # On failure the original long text, not the short prompt, comes back.
    w._send_codex_owned(w.record, text)
    w.work[1][0]()
    w.work[1][1](None, True)
    assert w.reply_text.value.startswith('head\n' + 'x' * 100)


def test_a_long_reply_is_assembled_then_saved_like_a_first_message():
    """The reply box builds its input before sending; over the limit that
    used to refuse with 'at most 32000 characters' before the saved-file
    handover could run. Now the assembled reply reaches it."""
    w = Window()
    w._attachments = {}
    w._attachment_key = staticmethod(lambda info: info.key)
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    message = w._codex_message(w._open, 'x' * 40000)
    assert message == 'x' * 40000
    w._send_codex_owned(w.record, message)
    action, done = w.work[0]
    action()
    assert len(sent[0][2]) < 32000 and 'path: ' in sent[0][2]
    assert _read_saved(_saved_path(sent[0][2])) == 'x' * 40000
    done(False, False)
    assert 'given it as a file to read' in w.said[-1]


def test_a_long_message_keeps_its_attached_image(tmp_path):
    """An attached image still reaches Codex natively, with the short
    prompt; and the limit is judged on the assembled message, so text just
    under it plus attachment lines goes in a file rather than failing."""
    from thechatplace.providers.codex_input import CodexInput
    image = tmp_path / 'shot.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 64)
    w = Window()
    w._attachments = {w._open.key: [str(image)]}
    w._attachment_key = staticmethod(lambda info: info.key)
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    message = w._codex_message(w._open, 'x' * 31990)
    assert len(message) > 32000
    w._send_codex_owned(w.record, message)
    w.work[0][0]()
    prompt = sent[0][2]
    assert isinstance(prompt, CodexInput) and len(prompt) < 32000
    assert {'type': 'localImage', 'path': str(image.resolve())} in prompt.items()
    assert 'Attached image: ' in _read_saved(_saved_path(prompt))


def test_a_long_message_whose_image_has_gone_is_refused_not_stuck(tmp_path):
    """The image vanished between assembling and sending: no file is left,
    the send goes through the normal refusal, and the chat isn't left with
    a send that never finishes."""
    image = tmp_path / 'shot.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 64)
    w = Window()
    w._attachments = {w._open.key: [str(image)]}
    w._attachment_key = staticmethod(lambda info: info.key)
    message = w._codex_message(w._open, 'x' * 31990)
    image.unlink()
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    w._send_codex_owned(w.record, message)
    assert sent == [] and w._codex_send_pending
    w.work[0][0]()
    assert sent[0][2] == message  # as it was; the runtime refuses it
    assert not list(_long_temp().iterdir())


def test_too_long_codex_steer_is_saved_and_codex_is_asked_to_read_it():
    w = Window()
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or 'turn-9')
    w._steer_codex_owned(w.record, 'y' * 40000)
    action, done = w.work[0]
    result = action()
    assert result is None and sent[0][0] == 'steer'
    assert len(sent[0][2]) < 32000 and "as the user's message" in sent[0][2]
    path = _saved_path(sent[0][2])
    done(None, False)
    assert path.exists()
    # A steer goes into the running turn, so that turn's end deletes it.
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-9', 'completed'))
    assert not path.exists()


def test_a_refused_long_steer_deletes_its_file():
    from thechatplace.providers.codex_rpc import CodexSteerRefused
    w = Window()
    sent = []
    def refuse(*a):
        sent.append(a)
        raise CodexSteerRefused('ended')
    w.sessions = SimpleNamespace(owned_action=refuse)
    w._steer_codex_owned(w.record, 'y' * 40000)
    action, done = w.work[0]
    done(action(), False)
    path = _saved_path(sent[0][2])
    assert not path.exists()
    assert w.reply_text.value.startswith('y' * 100)


def test_saved_message_file_is_deleted_when_its_turn_ends():
    """The temp file lives only for its turn (#625)."""
    w = Window()
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    w._send_codex_owned(w.record, 'x' * 40000)
    action, done = w.work[0]
    action()
    path = _saved_path(sent[0][2])
    done(False, False)
    assert path.exists()  # the turn is still running
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress'))
    assert path.exists()
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-1', 'completed'))
    assert not path.exists()


def test_a_second_long_message_outlives_the_last_turns_end():
    """In a chat that has had a reply, the runtime keeps reporting that
    turn's "completed" while the new one starts, and the window replays it
    once the send returns. That isn't the long message's turn, so its file
    must still be there for Codex to read."""
    w = Window()
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-1', 'completed'))
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    w._send_codex_owned(w.record, 'x' * 40000)
    action, done = w.work[0]
    action()
    path = _saved_path(sent[0][2])
    # Held while the send is pending, then replayed by done().
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'completed'))
    done(False, False)
    assert path.exists()
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-1', 'completed'))
    assert path.exists()
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-2', 'inProgress'))
    assert path.exists()
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-2', 'completed'))
    assert not path.exists()


def test_a_queued_long_message_waits_for_its_own_turn():
    """Queued behind a running turn, and behind another queued message: the
    turns that end before it starts aren't its own, even once it has left
    the queue (the runtime reports the last turn's end as it starts)."""
    w = Window()
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress'))
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or True)
    w._send_codex_owned(w.record, 'x' * 40000)
    w.work[0][0]()
    w.work[0][1](True, False)
    prompt = sent[0][2]
    path = _saved_path(prompt)
    for state in (CodexChatState('owned', 'working', 'turn-1', 'inProgress', queued=('other', prompt)),
                  CodexChatState('owned', 'working', 'turn-1', 'completed', queued=(prompt,)),
                  CodexChatState('owned', 'working', 'turn-2', 'inProgress', queued=(prompt,)),
                  # turn-2 ends and the runtime takes the prompt off the queue
                  # before it says so: still not the prompt's turn.
                  CodexChatState('owned', 'working', 'turn-2', 'completed'),
                  CodexChatState('owned', 'working', 'turn-3', 'inProgress')):
        w._on_owned_update('codex', state)
        assert path.exists(), state
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-3', 'completed'))
    assert not path.exists()


def test_an_unsent_long_message_deletes_its_file_and_comes_back_as_written():
    w = Window()
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress'))
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or True)
    text = 'head\n' + 'x' * 40000
    w._send_codex_owned(w.record, text)
    w.work[0][0]()
    w.work[0][1](True, False)
    prompt = sent[0][2]
    path = _saved_path(prompt)
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-1', 'interrupted', unsent=(prompt,)))
    assert not path.exists()
    assert w.reply_text.value.startswith('head\n' + 'x' * 100)


def test_closing_and_deleting_a_chat_remove_its_files():
    from thechatplace import codex_long_messages
    w = Window()
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or False)
    w._send_codex_owned(w.record, 'x' * 40000)
    w.work[0][0]()
    path = _saved_path(sent[0][2])
    w._forget_long_messages()
    assert not path.exists() and not w._long_message_files
    assert codex_long_messages.expand(sent[0][2]) == 'x' * 40000
    codex_long_messages.forget_chat('owned')
    assert codex_long_messages.expand(sent[0][2]) == sent[0][2]


def test_saved_message_reads_back_as_written_and_as_yours():
    """History shows the message as written, as yours, labeled as sent as
    a file; and the prompt says to say so if the file can't be read."""
    from thechatplace import codex_long_messages
    from thechatplace.transcript import ChatMessage
    text = 'head\n' + 'x' * 40000 + '\nTAIL'
    path = codex_long_messages.save_message(text)
    prompt = codex_long_messages.prompt_for(text, path)
    assert "can't read" in prompt and 'say so plainly' in prompt
    assert prompt.startswith('This message was placed in this conversation by The Chat Place. '
                             'Your message is too long to send directly')
    assert 'temporary file which will be deleted when the task is finished.\npath: ' in prompt
    codex_long_messages.remember(path, text, 'owned')
    assert codex_long_messages.expand(prompt) == text
    shown = codex_long_messages.expand_messages(
        [ChatMessage('user', prompt), ChatMessage('assistant', 'Fine')])
    assert shown[0].label == 'You, sent as a file' and shown[0].text == text
    assert shown[0].kind == 'user'
    assert shown[1].text == 'Fine' and not shown[1].speaker
    # Not remembered (another computer): still labeled, the prompt kept.
    codex_long_messages.forget_chat('owned')
    shown = codex_long_messages.expand_messages([ChatMessage('user', prompt)])
    assert shown[0].label == 'You, sent as a file' and shown[0].text == prompt


def test_only_the_apps_own_prompt_and_files_are_recognized(tmp_path):
    """Codex's replies and command output can quote anything: a "path:"
    line, even the prompt itself, never retitles a message or reads a file."""
    from thechatplace import codex_long_messages
    from thechatplace.transcript import ChatMessage
    secret = tmp_path / 'config'
    secret.write_text('TOKEN=abc', encoding='utf-8')
    text = 'x' * 40000
    path = codex_long_messages.save_message(text)
    codex_long_messages.remember(path, text, 'owned')
    prompt = codex_long_messages.prompt_for(text, path)
    elsewhere = codex_long_messages.prompt_for(text, str(secret))
    yaml = f'settings:\npath: {secret}\n'
    shown = codex_long_messages.expand_messages([
        ChatMessage('assistant', prompt),          # Codex quoting it
        ChatMessage('tool_result', prompt),        # or a command printing it
        ChatMessage('user', yaml),                 # your own YAML
        ChatMessage('user', elsewhere),            # a "prompt" naming another file
        ChatMessage('user', 'Note: ' + prompt),    # not at the start
    ])
    assert all(not m.speaker for m in shown)
    assert [m.text for m in shown] == [prompt, prompt, yaml, elsewhere, 'Note: ' + prompt]
    assert codex_long_messages.expand(yaml) == yaml
    assert codex_long_messages.expand(elsewhere) == elsewhere
    codex_long_messages.delete_files([str(secret)])
    assert secret.exists()  # never deletes a file that isn't its own


def test_remembered_messages_are_capped_by_size(monkeypatch):
    from thechatplace import codex_long_messages
    monkeypatch.setattr(codex_long_messages, '_KEPT_CHARS', 100_000)
    paths = []
    for n in range(4):
        path = codex_long_messages.save_message('x')
        codex_long_messages.remember(path, str(n) * 40_000, 'owned')
        paths.append(path)
    codex_long_messages.reset()  # read back from disk
    kept = [codex_long_messages.original(codex_long_messages.prompt_for('x', p)) for p in paths]
    assert kept[:2] == [None, None] and kept[2] == '2' * 40_000 and kept[3] == '3' * 40_000


def test_leftover_saved_messages_are_swept():
    """Files from before this run go; one written since (a send made before
    the sweep got to run) and anything else in the folder stay."""
    import os
    import time
    from thechatplace import codex_long_messages
    temp = _long_temp()
    leftover = temp / 'thechatplace-long-message-old.txt'
    leftover.write_text('secret', encoding='utf-8')
    os.utime(leftover, (time.time() - 3600,) * 2)
    fresh = temp / 'thechatplace-long-message-new.txt'
    fresh.write_text('being read', encoding='utf-8')
    other = temp / 'unrelated.txt'
    other.write_text('keep', encoding='utf-8')
    os.utime(other, (time.time() - 3600,) * 2)
    codex_long_messages.sweep_temp(before=time.time() - 60)
    assert not leftover.exists() and fresh.exists() and other.exists()


def _run(w, index):
    """Run queued work as _codex_work does: an exception becomes an error."""
    from thechatplace.codex_diagnostics import safe_reason
    action, done = w.work[index]
    try:
        result, error = action(), False
    except Exception as exc:  # noqa: BLE001
        result, error = safe_reason(exc) or None, True
    done(result, error)


def test_steering_a_queued_long_message_shows_it_as_written_and_waits_for_that_turn():
    w = Window()
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress'))
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or True)
    text = 'head\n' + 'x' * 40000
    w._send_codex_owned(w.record, text)
    _run(w, 0)
    prompt = sent[0][2]
    path = _saved_path(prompt)
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress',
                                               queued=(prompt,)))
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or 'turn-1')
    w._steer_codex_owned(w.record, prompt, index=0)
    # The steer row, and what history is matched against, is what you wrote.
    assert w.steer_rows[-1][0] == text
    # The prompt leaves the queue during the same turn: not a new turn.
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress'))
    _run(w, 1)
    assert sent[-1][:2] == ('steer_queued', w.record) and sent[-1][3] == prompt
    assert path.exists()
    assert 'Steered' in w.said[-1] and 'path:' not in w.said[-1]
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-1', 'completed'))
    assert not path.exists()


def test_a_long_steer_taken_by_the_next_turn_waits_for_that_turn():
    """The steer can reach Codex after the turn it was meant for has ended,
    and go into the next one: the earlier turn's end mustn't delete it."""
    w = Window()
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-1', 'inProgress'))
    sent = []
    w.sessions = SimpleNamespace(owned_action=lambda *a: sent.append(a) or 'turn-2')
    w._steer_codex_owned(w.record, 'y' * 40000)
    action, done = w.work[0]
    result = action()
    path = _saved_path(sent[0][2])
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-1', 'completed'))
    w._on_owned_update('codex', CodexChatState('owned', 'working', 'turn-2', 'inProgress'))
    assert path.exists()
    done(result, False)
    assert path.exists()
    w._on_owned_update('codex', CodexChatState('owned', 'idle', 'turn-2', 'completed'))
    assert not path.exists()


def test_a_refused_long_send_deletes_its_file_and_an_uncertain_one_keeps_it():
    from thechatplace.providers.codex_rpc import CodexRpcError, CodexTurnRejected
    w = Window()
    sent = []
    def gone(*a):
        sent.append(a)
        raise CodexTurnRejected('thread_gone')
    w.sessions = SimpleNamespace(owned_action=gone)
    w._send_codex_owned(w.record, 'x' * 40000)
    _run(w, 0)
    assert not _saved_path(sent[0][2]).exists()
    assert w.reply_text.value.startswith('x' * 100)
    assert 'no longer has this chat' in w.said[-1]
    def lost(*a):
        sent.append(a)
        raise CodexRpcError('connection lost')
    w.sessions = SimpleNamespace(owned_action=lost)
    w._send_codex_owned(w.record, 'z' * 40000)
    _run(w, 1)
    assert _saved_path(sent[1][2]).exists()  # it may have reached Codex


def test_no_file_is_written_while_files_are_being_put_back():
    w = Window()
    w._files_going_back_in = lambda folder: True
    w._send_codex_owned(w.record, 'x' * 40000)
    assert not list(_long_temp().iterdir())
    assert 'putting files back' in w.said[-1]


def test_a_queued_long_message_row_reads_as_written():
    from thechatplace import codex_long_messages
    from thechatplace.ui.main_frame import MainFrame
    text = 'head\n' + 'x' * 40000
    path = codex_long_messages.save_message(text)
    prompt = codex_long_messages.prompt_for(text, path)
    codex_long_messages.remember(path, text, 'owned')
    row = MainFrame._queued_row(prompt, 'queued:codex:owned:0')
    assert row.text == text and row.label == 'Queued, sent as a file'
    plain = MainFrame._queued_row('Short one', 'queued:codex:owned:1')
    assert plain.text == 'Short one' and not plain.speaker


def test_an_agent_conversation_reads_a_long_hand_on_as_written():
    """Unlabeled for an agent conversation, which names and sorts its rows
    by what they say (a reply passed on, its setup)."""
    from thechatplace import codex_long_messages
    from thechatplace.transcript import ChatMessage
    text = "Claude's reply, passed on:\n" + 'x' * 40000
    path = codex_long_messages.save_message(text)
    prompt = codex_long_messages.prompt_for(text, path)
    codex_long_messages.remember(path, text, 'owned')
    shown = codex_long_messages.expand_messages([ChatMessage('user', prompt)], label=False)
    assert shown[0].text == text and not shown[0].speaker


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
    from thechatplace.ui_text import with_stop_key
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
    assert spoken[0] == 'Checking the new permissions with Codex.'
    assert len(spoken) == 2, spoken
    # The provider says what Stop does; the window names its key (#596).
    assert spoken[1].endswith(with_stop_key(STOP_TO_RECHECK)) and spoken[1].count('Press Stop') == 1
    assert 'Use Stop' not in spoken[1]
    assert 'private' not in spoken[1]
    if failure == 'auto_review_unavailable':
        assert 'automatic approval review' in spoken[1]
    assert live._codex_status_text(live._codex_states[live.key]).endswith(with_stop_key(STOP_TO_RECHECK))
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
    assert spoken[0] == 'Checking the new permissions with Codex.'
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
    assert spoken[0] == 'Checking the new permissions with Codex.'
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
