"""Verified owned text turns, typed requests and session-scoped interruption."""
import threading
from dataclasses import dataclass, field, replace

from .codex_events import CodexTurnBuffer, CodexTokenUsageEvent, CodexEvent, _text
from .codex_tokens import CodexTokenUsage
from .codex_owned import CodexLifecycleConnection
from .codex_rpc import CodexRpcError, CodexTurnRejected, CodexConnectionFailure
from .codex_requests import CodexRequest, CodexRequestResolved, CodexRequestDeclined, QUESTIONS


@dataclass(frozen=True)
class CodexTurnState:
    turn_id: str = ''
    status: str = 'idle'
    replies: tuple = field(default=(), repr=False)
    requests: tuple = field(default=(), repr=False)
    file_changes: tuple = field(default=(), repr=False)
    started_at: float = 0
    activity: str = field(default='', repr=False)
    notice: str = ''
    token_usage: CodexTokenUsage | None = None
    context_epoch: int = 0


class CodexExecutionConnection(CodexLifecycleConnection):
    METHODS = CodexLifecycleConnection.METHODS | {'turn/start', 'turn/interrupt'}

    def __init__(self, *args, **kwargs):
        self._runtime_lock = threading.Lock()
        self._runtime_threads = {}
        kwargs['receive_events'] = True
        kwargs['receive_requests'] = True
        super().__init__(*args, **kwargs)

    def _accept_owned_thread(self, proof):
        if proof.connection is not self or proof.ephemeral:
            raise CodexRpcError('Codex execution requires a persistent owned chat')
        with self._runtime_lock:
            self._runtime_threads[proof.native_id] = proof

    def owns_runtime(self, proof):
        with self._runtime_lock:
            return (not self.closed and proof.connection is self
                    and self._runtime_threads.get(proof.native_id) is proof)

    def _accept_server_request(self, request):
        with self._runtime_lock:
            proof = self._runtime_threads.get(request.thread_id)
            return (proof is not None
                    and (proof.policy.approval != 'never' or request.method == QUESTIONS))

    def _verify_turn_route(self, params):
        with self._runtime_lock:
            proof = self._runtime_threads.get(params.get('threadId'))
        if proof is None or not self.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        proof.policy.verify_workspace(proof.cwd)
        expected = {'threadId': proof.native_id, 'cwd': proof.cwd, 'model': proof.model,
                    'approvalPolicy': proof.policy.approval, 'approvalsReviewer': 'user',
                    'sandboxPolicy': proof.policy.turn_policy()}
        if proof.effort is not None:
            expected['effort'] = proof.effort
        if set(params) != set(expected) | {'input'} or any(params[k] != v for k, v in expected.items()):
            raise CodexRpcError('Codex turn options do not match the owned chat')
        inputs = params['input']
        if (not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict)
                or set(inputs[0]) != {'type', 'text'} or inputs[0]['type'] != 'text'
                or not isinstance(inputs[0]['text'], str) or not inputs[0]['text'].strip()
                or len(inputs[0]['text']) > 32000):
            raise CodexRpcError('Codex text input is invalid')
        super()._verify_turn_route(params)
        metadata = self.request('thread/read', {'threadId': proof.native_id, 'includeTurns': False})
        thread = metadata.get('thread')
        if (not isinstance(thread, dict) or thread.get('id') != proof.native_id
                or not isinstance(thread.get('status'), dict)
                or thread['status'].get('type') != 'idle'):
            raise CodexTurnRejected('not_idle')


class CodexTurnExecutor:
    """One event consumer for a connection; never reconnect or replay a prompt.

    Interrupt acknowledgement is not completion. Uncertain sends stay blocked
    on this connection until shutdown and a deliberate owned-session resume.
    Tool approvals remain fail-closed in the transport until their next phase.
    """
    def __init__(self, connection):
        self.connection = connection
        self._lock = threading.Lock()
        self._drain_lock = threading.Lock()
        self._turns = {}
        self._busy = set()
        self._pending_events = {}
        self._broken = False
        self._requests = {}
        self._notices = {}
        self._token_usage = {}
        self._context_epochs = {}
        self._compactions = {}
        self._reconciled_turns = {}

    def start(self, proof, text):
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('Enter a message of at most 32000 characters')
        with self._lock:
            if self._broken:
                raise CodexRpcError('Codex connection needs to be reopened')
            if proof.native_id in self._busy:
                raise CodexRpcError('This Codex chat already has work in progress')
            self._busy.add(proof.native_id)
            self._notices.pop(proof.native_id, None)
            self._compactions.pop(proof.native_id, None)
            self._pending_events[proof.native_id] = []
        try:
            params = {
                'threadId': proof.native_id, 'cwd': proof.cwd, 'model': proof.model,
                'approvalPolicy': proof.policy.approval, 'approvalsReviewer': 'user',
                'sandboxPolicy': proof.policy.turn_policy(),
                'input': [{'type': 'text', 'text': text}]}
            if proof.effort is not None:
                params['effort'] = proof.effort
            result = self.connection.request('turn/start', params)
            turn = result.get('turn')
            if not isinstance(turn, dict) or turn.get('status') != 'inProgress':
                raise CodexRpcError('Codex did not confirm the new turn')
            buffer = CodexTurnBuffer(proof.native_id, turn.get('id'))
            with self._lock:
                if self._broken:
                    raise CodexRpcError('Codex connection interrupted during startup')
                self._turns[proof.native_id] = buffer
                for event in self._pending_events.pop(proof.native_id):
                    self._apply_event(buffer, event)
                if buffer.status != 'inProgress':
                    self._busy.discard(proof.native_id)
            return buffer
        except CodexTurnRejected:
            # A refused preflight never sent turn/start. Closing the shared
            # stream here would kill unrelated chats' active tools (#247).
            with self._lock:
                self._busy.discard(proof.native_id)
                self._pending_events.pop(proof.native_id, None)
            raise
        except CodexConnectionFailure:
            self._break_connection()
            raise
        except Exception:
            # A timeout may mean the prompt was accepted. Keep the reservation:
            # another click must not silently duplicate it.
            self._break_connection()
            raise CodexRpcError('Could not confirm this Codex turn. Reopen the chat before sending again.') from None

    def interrupt(self, proof):
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        with self._lock:
            buffer = self._turns.get(proof.native_id)
            if buffer is None or buffer.status != 'inProgress':
                return False
            turn_id = buffer.turn_id
        self.connection.request('turn/interrupt', {'threadId': proof.native_id, 'turnId': turn_id})
        return True

    def reconcile_stop(self, proof):
        """Read-only confirmation of the exact turn; never resume or replay."""
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        with self._lock:
            buffer = self._turns.get(proof.native_id)
            if buffer is None or buffer.status != 'inProgress':
                return True
            turn_id = buffer.turn_id
        response = self.connection.request('thread/read',
            {'threadId': proof.native_id, 'includeTurns': False})
        thread = response.get('thread')
        if (not isinstance(thread, dict) or thread.get('id') != proof.native_id
                or not isinstance(thread.get('status'), dict) or thread['status'].get('type') != 'idle'):
            raise CodexRpcError('Codex has not confirmed that this turn stopped. Try Stop again later.')
        page = self.connection.request('thread/turns/list',
            {'threadId': proof.native_id, 'sortDirection': 'desc', 'limit': 1, 'itemsView': 'notLoaded'})
        turns = page.get('data')
        if (not isinstance(turns, list) or len(turns) != 1 or not isinstance(turns[0], dict)
                or turns[0].get('id') != turn_id
                or turns[0].get('status') not in ('completed', 'failed', 'interrupted')):
            raise CodexRpcError('Codex has not confirmed that this turn stopped. Try Stop again later.')
        with self._lock:
            if not self.connection.owns_runtime(proof):
                raise CodexRpcError('Codex connection changed while checking Stop')
            current = self._turns.get(proof.native_id)
            if current is not buffer or current.turn_id != turn_id:
                raise CodexRpcError('Codex turn changed while checking Stop')
            if buffer.status == 'inProgress':
                # Full history is refreshed by the UI; partial live text is not
                # represented as an authoritative completed reply.
                for key in buffer._texts.keys() - buffer._final:
                    del buffer._texts[key]
                buffer.status = turns[0]['status']
                if len(self._reconciled_turns) >= 128:
                    self._reconciled_turns.pop(next(iter(self._reconciled_turns)))
                self._reconciled_turns[(proof.native_id, turn_id)] = True
                self._busy.discard(proof.native_id)
                self._requests = {k: r for k, r in self._requests.items() if r.thread_id != proof.native_id}
        forget = getattr(self.connection, 'forget_turn_requests', None)
        if callable(forget):
            forget(proof.native_id, turn_id)
        return True

    def waiting(self, proof):
        if not self.connection.owns_runtime(proof):
            return ()
        with self._lock:
            buffer = self._turns.get(proof.native_id)
            return tuple(r for r in self._requests.values() if buffer is not None
                         and buffer.status == 'inProgress'
                         and (r.thread_id, r.turn_id) == (buffer.thread_id, buffer.turn_id))

    def snapshot(self, proof):
        with self._lock:
            buffer = self._turns.get(proof.native_id)
            if buffer is None:
                return CodexTurnState(status='unknown' if self._broken else 'idle', notice=self._notices.get(proof.native_id, ''),
                                      token_usage=self._token_usage.get(proof.native_id),
                                      context_epoch=self._context_epochs.get(proof.native_id, 0))
            requests = tuple(r for r in self._requests.values()
                             if buffer.status == 'inProgress'
                             and (r.thread_id, r.turn_id) == (buffer.thread_id, buffer.turn_id))
            return CodexTurnState(buffer.turn_id, buffer.status, buffer.replies, requests, buffer.file_changes,
                                  buffer.started_at, buffer.activity, self._notices.get(proof.native_id, ''),
                                  self._token_usage.get(proof.native_id), self._context_epochs.get(proof.native_id, 0))

    def answer(self, proof, request, result):
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        with self._lock:
            buffer = self._turns.get(proof.native_id)
            if (self._requests.get(request.request_id) is not request or buffer is None
                    or buffer.status != 'inProgress'
                    or (request.thread_id, request.turn_id) != (proof.native_id, buffer.turn_id)):
                raise CodexRpcError('Codex request is no longer waiting')
        self.connection.respond_request(request, result)
        with self._lock:
            self._requests.pop(request.request_id, None)

    def _apply_event(self, buffer, event):
        if isinstance(event, CodexTokenUsageEvent):
            if (event.thread_id, event.turn_id) != (buffer.thread_id, buffer.turn_id):
                return False
            changed = self._token_usage.get(buffer.thread_id) != event.usage
            self._token_usage[buffer.thread_id] = event.usage
            return changed
        if getattr(event, 'kind', None) == 'context_compacted':
            if (event.thread_id, event.turn_id) != (buffer.thread_id, buffer.turn_id):
                return False
            seen = self._compactions.setdefault(buffer.thread_id, set())
            if event.item_id in seen or len(seen) >= 128:
                return False
            seen.add(event.item_id)
            self._token_usage.pop(buffer.thread_id, None)
            self._context_epochs[buffer.thread_id] = self._context_epochs.get(buffer.thread_id, 0) + 1
            return True
        if isinstance(event, CodexRequestResolved):
            request = self._requests.get(event.request_id)
            if request is not None and request.thread_id == event.thread_id:
                del self._requests[event.request_id]
                return True
            return False
        if isinstance(event, CodexRequest):
            if (event.thread_id, event.turn_id) in self._reconciled_turns:
                return False  # queued before read-only Stop confirmation; never grant it
            if ((event.thread_id, event.turn_id) != (buffer.thread_id, buffer.turn_id)
                    or buffer.status != 'inProgress'):
                raise CodexRpcError('Codex request does not match an active turn')
            self._requests[event.request_id] = event
            return True
        changed = buffer.apply(event)
        if buffer.status != 'inProgress':
            self._requests = {key: request for key, request in self._requests.items()
                              if request.thread_id != buffer.thread_id}
        return changed

    def drain(self):
        # Pop and apply as one ordered consumer even if two worker callbacks
        # happen to overlap. Final text must precede its terminal event.
        with self._drain_lock:
            return self._drain()

    def _drain(self):
        changed = set()
        try:
            while (event := self.connection.pop_event()) is not None:
                with self._lock:
                    if isinstance(event, CodexRequestDeclined):
                        affected = set(self._pending_events) | {native for native, buffer in self._turns.items()
                                                               if buffer.status == 'inProgress'}
                        if event.thread_id:
                            # Only the chat the request named, if it's one of ours (#284).
                            affected &= {event.thread_id}
                        for native in affected:
                            self._notices[native] = event.text
                        changed.update(affected)
                        continue
                    pending = self._pending_events.get(event.thread_id)
                    if pending is not None:
                        presentation = isinstance(event, CodexEvent) and event.kind in (
                            'text_delta', 'text_completed', 'activity', 'file_changes', 'display_truncated')
                        if presentation:
                            for index, old in enumerate(pending):
                                if (isinstance(old, CodexEvent) and old.turn_id == event.turn_id
                                        and old.item_id == event.item_id and old.kind in (
                                            ('text_delta', 'text_completed') if event.kind in ('text_delta', 'text_completed') else (event.kind,))):
                                    if event.kind == 'text_delta' and old.kind == 'text_completed':
                                        break
                                    pending[index] = (replace(event, text=_text(old.text + event.text))
                                        if event.kind == old.kind == 'text_delta' else event)
                                    break
                            else:
                                displays = [i for i, old in enumerate(pending) if isinstance(old, CodexEvent)
                                            and old.kind in ('text_delta', 'text_completed', 'activity', 'file_changes')]
                                if len(displays) >= 128:
                                    pending.pop(displays[0])
                                    if not any(getattr(e, 'kind', '') == 'display_truncated' for e in pending):
                                        pending.append(CodexEvent('display_truncated', event.thread_id, event.turn_id))
                                pending.append(event)
                        else:
                            controls = sum(not isinstance(e, CodexEvent) or e.kind in ('turn_started', 'turn_completed') for e in pending)
                            if controls >= 32:
                                raise CodexRpcError('Codex startup event limit reached')
                            pending.append(event)
                        continue
                    buffer = self._turns.get(event.thread_id)
                    if buffer is None and isinstance(event, CodexRequest):
                        raise CodexRpcError('Codex request has no active turn')
                    if buffer is not None and self._apply_event(buffer, event):
                        changed.add(event.thread_id)
                        if buffer.status != 'inProgress':
                            self._busy.discard(event.thread_id)
        except Exception as error:
            with self._lock:
                changed.update(self._turns)
            self._break_connection()
            if type(error) is CodexConnectionFailure:
                raise
            raise CodexRpcError('Codex connection interrupted. Reload chats to check their replies.') from None
        return frozenset(changed)

    def _break_connection(self):
        with self._lock:
            self._broken = True
            self._pending_events.clear()
            self._requests.clear()
            for buffer in self._turns.values():
                buffer.disconnect()
        close = getattr(self.connection, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass  # preserve the safe reconciliation error
