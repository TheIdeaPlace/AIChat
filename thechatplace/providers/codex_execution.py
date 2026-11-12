"""Verified owned text turns, typed requests and session-scoped interruption."""
import threading
from dataclasses import dataclass, field, replace

from .codex_events import CodexTurnBuffer, CodexTokenUsageEvent, CodexEvent, _text
from .codex_tokens import CodexTokenUsage
from .codex_owned import CodexLifecycleConnection
from .codex_rpc import (check_startable, CodexRpcError, CodexTurnRejected, CodexConnectionFailure, CodexApprovalNotSent,
                        CodexSteerRefused)
from .codex_requests import CodexRequest, CodexRequestResolved, CodexRequestDeclined, QUESTIONS, ELICITATION
from .codex_input import input_items, validate_input


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
    tasks: tuple = field(default=(), repr=False)
    plan_received: bool = False
    changed_paths: tuple = field(default=(), repr=False)
    review_notices: tuple = field(default=(), repr=False)
    #: A turn /compact started, which ends with no reply to announce.
    compaction: bool = False
    retry: str = ''
    reasoning: tuple = field(default=(), repr=False)
    #: This turn's tools started and file changes made, for sounds (#250).
    tools_started: int = 0
    edits_made: int = 0


class CodexExecutionConnection(CodexLifecycleConnection):
    from .codex_features import ACTION_METHODS, READ_METHODS, DELETE_METHOD
    METHODS = (CodexLifecycleConnection.METHODS | {'turn/start', 'turn/steer', 'turn/interrupt', 'mcpServer/oauth/login', 'config/mcpServer/reload'}
               | ACTION_METHODS | READ_METHODS | {DELETE_METHOD})

    def request(self, method, params):
        from .codex_features import ACTION_METHODS, READ_METHODS, DELETE_METHOD, validate_action, CodexFeatureError
        if method == DELETE_METHOD:
            # Only delete_owned_thread sends it (#634).
            raise CodexFeatureError('invalid')
        if method in ACTION_METHODS | READ_METHODS:
            validate_action(method, params)
            with self._runtime_lock:
                proof = self._runtime_threads.get(params['threadId'])
            if proof is None or not self.owns_runtime(proof):
                raise CodexFeatureError('unavailable')
            proof.policy.verify_workspace(proof.cwd)
            from .codex_rpc import is_chatgpt_account, ReadOnlyCodexConnection
            if not is_chatgpt_account(self.request('account/read', {'refreshToken': True})):
                raise CodexTurnRejected('chatgpt_required')
            ReadOnlyCodexConnection._verify_turn_route(self, {'threadId': proof.native_id, 'cwd': proof.cwd})
            if method in ACTION_METHODS and not (method.startswith('thread/goal/')
                    or method == 'thread/backgroundTerminals/terminate'):
                thread = self.request('thread/read', {'threadId': proof.native_id, 'includeTurns': False}).get('thread')
                check_startable(self, proof.native_id, thread, check_turns=False)
        return super().request(method, params)

    def delete_owned_thread(self, native_id, proof=None):
        """The one way thread/delete is sent (#634), for a chat The Chat Place
        started (the runtime checks its record). ``proof``: the chat's proof if
        it's loaded on this connection, which must be the one held for it; a
        chat not loaded here has none, and this connection mustn't hold one.

        Nothing is resumed or loaded first: Codex deletes a thread that isn't
        loaded, or is archived, and shuts down one that is (codex-rs
        thread_delete.rs, 0.163). Nor are the folder or turn route checked:
        those guard turns, and deleting runs none, so a chat whose folder was
        removed can still be deleted."""
        from .codex_features import DELETE_METHOD, validate_action, CodexFeatureError
        from .codex_rpc import is_chatgpt_account, CodexDeleteUnanswered
        params = {'threadId': native_id}
        validate_action(DELETE_METHOD, params, deleting=True)
        with self._runtime_lock:
            held = self._runtime_threads.get(native_id)
        if held is not proof or (proof is not None and not self.owns_runtime(proof)):
            raise CodexFeatureError('unavailable')
        if not is_chatgpt_account(self.request('account/read', {'refreshToken': True})):
            raise CodexTurnRejected('chatgpt_required')
        sent = []
        try:
            return super().request(DELETE_METHOD, params, on_sent=lambda: sent.append(True))
        except CodexRpcError as error:
            # Codex's own answer (a fresh error for each reply) says what it
            # did. With no answer to a delete that went, it may be gone; one
            # that never went (connection closed, too many waiting) left the
            # chat as it was. The connection's shared failure isn't changed.
            if sent and getattr(error, 'rpc_code', None) is None:
                raise CodexDeleteUnanswered() from error
            raise

    def __init__(self, *args, **kwargs):
        self.session_tools = kwargs.pop("session_tools", False)
        self._runtime_lock = threading.Lock()
        self._runtime_threads = {}
        self._active_turns = {}
        self._oauth_results = {}
        self._child_workers = threading.BoundedSemaphore(8)
        self._child_routing_lock = threading.RLock()
        self._child_pending = set()
        self._child_files = {}
        kwargs['receive_events'] = True
        kwargs['receive_requests'] = True
        super().__init__(*args, **kwargs)

    def _accept_owned_thread(self, proof):
        if proof.connection is not self or proof.ephemeral:
            raise CodexRpcError('Codex execution requires a persistent owned chat')
        with self._runtime_lock:
            self._runtime_threads[proof.native_id] = proof

    def _forget_owned_thread(self, proof) -> None:
        """A deleted thread can no longer authorize requests in this connection."""
        with self._runtime_lock:
            if self._runtime_threads.get(proof.native_id) is proof:
                self._runtime_threads.pop(proof.native_id)

    def loaded_caller(self, native_id):
        with self._runtime_lock:
            proof = self._runtime_threads.get(native_id)
            return proof.session_tool_caller if proof is not None and not self.closed else ""

    def owns_runtime(self, proof):
        with self._runtime_lock:
            return (not self.closed and proof.connection is self
                    and self._runtime_threads.get(proof.native_id) is proof)

    def _allowed_codex_tools(self, thread_id):
        with self._runtime_lock:
            proof = self._runtime_threads.get(thread_id)
            return proof.allowed_tools if proof is not None else ()

    def oauth_result(self, thread_id, name, login_id):
        with self._runtime_lock:
            return self._oauth_results.pop((thread_id, name, login_id), None)

    def _route_child_request(self, message, received_turns):
        """Metadata proves ancestry for display routing, never execution ownership."""
        from .codex_requests import decode_request
        try:
            request = decode_request(message['id'], message['method'], message.get('params'))
            child, ancestor, seen = request.thread_id, request.thread_id, set()
            child_turn = request.turn_id or received_turns.get(child)
            if not child_turn:
                raise ValueError('No active child turn')
            proof = None
            for _ in range(32):
                if ancestor in seen:
                    break
                seen.add(ancestor)
                with self._runtime_lock:
                    proof = self._runtime_threads.get(ancestor)
                if proof is not None:
                    break
                thread = self.request('thread/read', {'threadId': ancestor, 'includeTurns': False}).get('thread')
                if (not isinstance(thread, dict) or thread.get('id') != ancestor or
                        thread.get('modelProvider') != 'openai'):
                    raise ValueError('Unverified child ancestry')
                ancestor = thread.get('parentThreadId')
                if not isinstance(ancestor, str) or not ancestor:
                    break
            if proof is None or not self.owns_runtime(proof):
                raise ValueError('No owned ancestor')
            parent_turn = received_turns.get(proof.native_id)
            with self._child_routing_lock:
                if request.request_id not in self._child_pending:
                    return
                with self._runtime_lock:
                    if not parent_turn or self._active_turns.get(proof.native_id) != parent_turn:
                        raise ValueError('The original parent turn ended')
                    detail = self._child_files.get((child, child_turn, request.item_id), '')
                with self._lock:
                    if (child, child_turn) in self._retired_turns:
                        raise ValueError('Child turn ended')
                routed = replace(request, thread_id=proof.native_id, turn_id=parent_turn,
                    source_thread_id=child, source_turn_id=child_turn, file_detail=detail,
                    reason='Request from a Codex subagent. ' + request.reason)
                self._receive_server_request(routed)
        except (ValueError, CodexRpcError):
            try:
                with self._child_routing_lock:
                    if message["id"] in self._child_pending:
                        self._decline_unsupported_request(message)
            except (ValueError, CodexRpcError):
                pass  # connection closed or the native request already resolved
        finally:
            with self._child_routing_lock:
                self._child_pending.discard(message["id"])
            self._child_workers.release()

    def _receive(self, message):
        if isinstance(message, dict) and 'id' in message and 'method' in message:
            params = message.get('params')
            child = params.get('threadId') if isinstance(params, dict) else None
            with self._runtime_lock:
                owned = child in self._runtime_threads if isinstance(child, str) else False
            if child and not owned:
                from .codex_requests import decode_request
                try:
                    decoded = decode_request(message['id'], message['method'], params)
                except ValueError:
                    self._decline_unsupported_request(message)
                    return
                if self._child_workers.acquire(blocking=False):
                    with self._child_routing_lock:
                        if decoded.request_id in self._child_pending:
                            self._child_workers.release()
                            raise ValueError('Duplicate child request identity')
                        self._child_pending.add(decoded.request_id)
                    with self._runtime_lock:
                        received = dict(self._active_turns)
                    threading.Thread(target=self._route_child_request, args=(message, received), daemon=True).start()
                else:
                    self._decline_unsupported_request(message)
                return
        if isinstance(message, dict) and message.get('method') == 'serverRequest/resolved':
            params = message.get('params')
            if isinstance(params, dict):
                with self._child_routing_lock:
                    self._child_pending.discard(params.get('requestId'))
                    with self._lock:
                        pending = self._server_pending.get(params.get('requestId'))
                    if pending is not None and pending.source_thread_id == params.get('threadId'):
                        message = dict(message, params=dict(params, threadId=pending.thread_id))
                    return super()._receive(message)
        if isinstance(message, dict) and message.get('method') in ('item/started', 'item/completed'):
            from .codex_events import decode_event
            event = decode_event(message['method'], message.get('params'))
            if event is not None and event.kind == 'file_changes':
                with self._runtime_lock:
                    if event.thread_id not in self._runtime_threads:
                        if len(self._child_files) >= 128:
                            self._child_files.pop(next(iter(self._child_files)))
                        self._child_files[(event.thread_id, event.turn_id, event.item_id)] = event.text
        if isinstance(message, dict) and message.get('method') == 'mcpServer/oauthLogin/completed':
            params = message.get('params')
            if isinstance(params, dict) and type(params.get('success')) is bool:
                key = (params.get('threadId'), params.get('name'), params.get('loginId'))
                if all(isinstance(k, str) and k for k in key):
                    with self._runtime_lock:
                        if len(self._oauth_results) >= 100:
                            self._oauth_results.pop(next(iter(self._oauth_results)))
                        self._oauth_results[key] = params['success']
            return

        # MCP turn IDs are optional. Bind them to the observed native turn now,
        # before queuing: a later turn must never inherit an unanswered form.
        if isinstance(message, dict) and message.get('method') in ('turn/started', 'turn/completed'):
            params = message.get('params')
            if isinstance(params, dict) and isinstance(params.get('turn'), dict):
                thread, turn = params.get('threadId'), params['turn'].get('id')
                if isinstance(thread, str) and isinstance(turn, str):
                    with self._runtime_lock:
                        if message['method'] == 'turn/started':
                            if len(self._active_turns) >= 512:
                                self._active_turns.pop(next(iter(self._active_turns)))
                            self._active_turns[thread] = turn
                        elif self._active_turns.get(thread) == turn:
                            self._active_turns.pop(thread, None)
                    if message['method'] == 'turn/completed':
                        with self._lock:
                            ended = [r for r in self._server_pending.values()
                                     if r.source_thread_id == thread and r.source_turn_id == turn]
                            if len(self._retired_turns) >= 128:
                                self._retired_turns.pop(next(iter(self._retired_turns)))
                            self._retired_turns[(thread, turn)] = True
                        for request in ended:
                            super()._receive({'method': 'serverRequest/resolved', 'params': {
                                'threadId': request.thread_id, 'requestId': request.request_id}})
        return super()._receive(message)

    def _bind_server_request(self, request):
        if request.method == ELICITATION and not request.turn_id:
            with self._runtime_lock:
                turn = self._active_turns.get(request.thread_id)
            if turn is None:
                raise ValueError('No active Codex turn for this form')
            return replace(request, turn_id=turn)
        return request

    def forget_turn_requests(self, thread_id, turn_id):
        with self._runtime_lock:
            if self._active_turns.get(thread_id) == turn_id:
                self._active_turns.pop(thread_id, None)
        return super().forget_turn_requests(thread_id, turn_id)

    def respond_request(self, request, result, *, dispatch_guard=None):
        if request.source_thread_id:
            with self._lock:
                if (request.source_thread_id, request.source_turn_id) in self._retired_turns:
                    raise CodexApprovalNotSent('The Codex subagent turn has ended')
            with self._runtime_lock:
                if self._active_turns.get(request.thread_id) != request.turn_id:
                    raise CodexApprovalNotSent('The parent Codex turn has ended')
        return super().respond_request(request, result, dispatch_guard=dispatch_guard)

    def _accept_server_request(self, request):
        with self._runtime_lock:
            proof = self._runtime_threads.get(request.thread_id)
            return (proof is not None
                    and (proof.policy.approval != 'never' or request.method in (QUESTIONS, ELICITATION))
                    and (request.method != ELICITATION or request.server in proof.allowed_tools))

    def _verify_turn_route(self, params):
        with self._runtime_lock:
            proof = self._runtime_threads.get(params.get('threadId'))
        if proof is None or not self.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        proof.policy.verify_workspace(proof.cwd)
        if 'expectedTurnId' in params:
            self._verify_steer_route(proof, params)
            return
        expected = {'threadId': proof.native_id, 'cwd': proof.cwd, 'model': proof.model,
                    'approvalPolicy': proof.policy.approval, 'approvalsReviewer': proof.policy.reviewer,
                    'sandboxPolicy': proof.policy.turn_policy()}
        if proof.effort is not None:
            expected['effort'] = proof.effort
        if proof.instructions is not None:
            expected['additionalContext'] = instruction_context(proof)
        if proof.collaboration is not None:
            expected['collaborationMode'] = collaboration_options(proof)
        if set(params) != set(expected) | {'input'} or any(params[k] != v for k, v in expected.items()):
            raise CodexRpcError('Codex turn options do not match the owned chat')
        self._verify_text_input(params['input'])
        super()._verify_turn_route(params)
        from .codex_tools import verify_apps
        verify_apps(self, proof.native_id, proof.apps)
        metadata = self.request('thread/read', {'threadId': proof.native_id, 'includeTurns': False})
        thread = metadata.get('thread')
        if not isinstance(thread, dict) or thread.get('id') != proof.native_id:
            raise CodexTurnRejected('status_unclear')
        # Idle, or an error status after an ended turn (#448); the turn list
        # is checked only for the latter, as before for idle.
        check_startable(self, proof.native_id, thread, check_turns=False)


    @staticmethod
    def _verify_text_input(inputs):
        try:
            validate_input(inputs)
        except (ValueError, OSError):
            raise CodexRpcError('Codex text input is invalid') from None

    def _verify_steer_route(self, proof, params):
        """turn/steer (#239): only text into the chat's own active turn. It
        carries no turn options (it can't change them), so the billing route
        is checked against the chat's own folder, and the thread must be
        active rather than idle."""
        turn_id = params.get('expectedTurnId')
        if (set(params) != {'threadId', 'expectedTurnId', 'input'}
                or not isinstance(turn_id, str) or not turn_id.strip()):
            raise CodexRpcError('Codex turn options do not match the owned chat')
        self._verify_text_input(params['input'])
        # Pinned to the owned chat's folder; the copy is never sent.
        super()._verify_turn_route({**params, 'cwd': proof.cwd})
        from .codex_tools import verify_apps
        verify_apps(self, proof.native_id, proof.apps)
        metadata = self.request('thread/read', {'threadId': proof.native_id, 'includeTurns': False})
        thread = metadata.get('thread')
        if (not isinstance(thread, dict) or thread.get('id') != proof.native_id
                or not isinstance(thread.get('status'), dict)
                or thread['status'].get('type') != 'active'):
            raise CodexSteerRefused('not_running')


#: JSON-RPC's invalid-request code, which app-server gives a steer it refuses.
INVALID_REQUEST = -32600


def instruction_context(proof):
    return {'tcp_chat_instructions': {'kind': 'application', 'value': proof.instructions}}


def collaboration_options(proof):
    return {'mode': proof.collaboration, 'settings': {'model': proof.model,
        'reasoning_effort': proof.effort, 'developer_instructions': None}}


def verify_frame_size(method, params):
    import json
    from .codex_rpc import ReadOnlyCodexConnection
    # Leave room for the monotonic RPC ID while measuring the full UTF-8
    # payload, including paths duplicated in text and native input parts.
    frame = {'id': 10 ** 30, 'method': method, 'params': params}
    if len((json.dumps(frame, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')) > ReadOnlyCodexConnection.MAX_REQUEST:
        raise CodexTurnRejected('input_size')


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
        self._native_starts = set()
        self._compaction_turns = {}

    def expect_native_turns(self, proof):
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        with self._lock:
            self._native_starts.add(proof.native_id)

    def cancel_native_turns(self, proof):
        with self._lock:
            self._native_starts.discard(proof.native_id)

    def note(self, native_id, text):
        """A notice for the chat's state, until its next turn starts."""
        with self._lock:
            self._notices[native_id] = text

    def start(self, proof, text):
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('Enter a message of at most 32000 characters')
        try:
            inputs = input_items(text)
        except (ValueError, OSError):
            raise CodexTurnRejected('input_unavailable') from None
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
                'approvalPolicy': proof.policy.approval, 'approvalsReviewer': proof.policy.reviewer,
                'sandboxPolicy': proof.policy.turn_policy(),
                'input': inputs}
            if proof.effort is not None:
                params['effort'] = proof.effort
            if proof.instructions is not None:
                params['additionalContext'] = instruction_context(proof)
            if proof.collaboration is not None:
                params['collaborationMode'] = collaboration_options(proof)
            verify_frame_size('turn/start', params)
            result = self.connection.request('turn/start', params)
            turn = result.get('turn')
            if not isinstance(turn, dict) or turn.get('status') != 'inProgress':
                raise CodexRpcError('Codex did not confirm the new turn')
            buffer = CodexTurnBuffer(proof.native_id, turn.get('id'))
            with self._lock:
                if self._broken:
                    raise CodexRpcError('Codex connection interrupted during startup')
                self._turns[proof.native_id] = buffer
                self._native_starts.discard(proof.native_id)
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
            # Internal: safe_reason hides this text; the runtime says what to do.
            raise CodexRpcError('Could not confirm this Codex turn. Use Stop to recheck this chat before sending again.') from None

    def steer(self, proof, text):
        """Add ``text`` to the running turn (``turn/steer``, #239): never a
        new turn. CodexSteerRefused when nothing was sent, or Codex refused it
        as an invalid request (-32600); any other error, a timeout or a lost
        connection leaves it unknown, for the caller to say."""
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('Enter a message of at most 32000 characters')
        with self._lock:
            if self._broken:
                raise CodexSteerRefused('unavailable')
            buffer = self._turns.get(proof.native_id)
            if buffer is None or buffer.status != 'inProgress':
                raise CodexSteerRefused('not_running')
            turn_id = buffer.turn_id
        try:
            params = {
                'threadId': proof.native_id, 'expectedTurnId': turn_id,
                'input': input_items(text)}
            verify_frame_size('turn/steer', params)
            result = self.connection.request('turn/steer', params)
        except (CodexTurnRejected, CodexSteerRefused, CodexConnectionFailure):
            raise
        except CodexRpcError as error:
            answered = type(error) is CodexRpcError and str(error) == 'Codex request failed'
            if not answered:
                raise
            if getattr(error, 'rpc_code', None) != INVALID_REQUEST:
                # Not a known refusal: it may have been taken. Unknown, so
                # it is never queued or sent again on its own.
                raise CodexRpcError('Codex did not confirm the steered message') from None
            # Checked with app-server 0.163.0: a wrong or ended turn, empty
            # input or no active turn is refused as an invalid request, and
            # the text never reaches the model. Most often the turn ended as
            # the steer went (expectedTurnId).
            with self._lock:
                current = self._turns.get(proof.native_id)
                ended = (current is not buffer or current.turn_id != turn_id
                         or current.status != 'inProgress')
            raise CodexSteerRefused('not_running' if ended else 'refused') from None
        if result.get('turnId') != turn_id:
            raise CodexRpcError('Codex did not confirm the steered message')
        return turn_id

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
                                  self._token_usage.get(proof.native_id), self._context_epochs.get(proof.native_id, 0),
                                  tuple(replace(t) for t in buffer.tasks), buffer.plan_received,
                                  buffer.changed_paths, tuple(buffer.review_notices.items()),
                                  self._compaction_turns.get(proof.native_id) == buffer.turn_id, buffer.retry, buffer.reasoning,
                                  tools_started=buffer.tools_started, edits_made=buffer.edits_made)

    def answer(self, proof, request, result, *, dispatch_guard=None):
        if not self.connection.owns_runtime(proof):
            raise CodexApprovalNotSent('Codex execution requires a verified owned chat')
        with self._lock:
            buffer = self._turns.get(proof.native_id)
            if (self._requests.get(request.request_id) is not request or buffer is None
                    or buffer.status != 'inProgress'
                    or (request.thread_id, request.turn_id) != (proof.native_id, buffer.turn_id)):
                raise CodexApprovalNotSent('Codex request is no longer waiting')
        if dispatch_guard is None:
            self.connection.respond_request(request, result)
        else:
            self.connection.respond_request(request, result, dispatch_guard=dispatch_guard)
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
                            'text_delta', 'text_completed', 'activity', 'file_changes', 'plan_updated', 'display_truncated', 'reasoning_delta', 'reasoning_completed')
                        if presentation:
                            for index, old in enumerate(pending):
                                if (isinstance(old, CodexEvent) and old.turn_id == event.turn_id
                                        and old.item_id == event.item_id and old.part == event.part and old.kind in (
                                            ('text_delta', 'text_completed') if event.kind in ('text_delta', 'text_completed') else (event.kind,))):
                                    if event.kind == 'text_delta' and old.kind == 'text_completed':
                                        break
                                    pending[index] = (replace(event, text=_text(old.text + event.text))
                                        if event.kind == old.kind and event.kind in ('text_delta', 'reasoning_delta') else event)
                                    break
                            else:
                                displays = [i for i, old in enumerate(pending) if isinstance(old, CodexEvent)
                                            and old.kind in ('text_delta', 'text_completed', 'activity', 'file_changes', 'plan_updated', 'reasoning_delta', 'reasoning_completed')]
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
                    if (isinstance(event, CodexEvent) and event.kind == 'turn_started'
                            and event.thread_id in self._native_starts
                            and (buffer is None or buffer.status != 'inProgress')):
                        buffer = CodexTurnBuffer(event.thread_id, event.turn_id)
                        self._turns[event.thread_id] = buffer
                        self._busy.add(event.thread_id)
                        self._native_starts.discard(event.thread_id)
                        self._compaction_turns[event.thread_id] = event.turn_id
                        changed.add(event.thread_id)
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
            # Internal: safe_reason hides this text; the runtime says what to do.
            raise CodexRpcError('Codex connection interrupted. Use Stop in each chat to check its reply.') from None
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
