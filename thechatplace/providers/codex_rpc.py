"""A managed, read-only app-server connection for background workers.

Concurrent list/history requests share one reader and are matched by ID, not
arrival order. Notifications are discarded in this phase. Unexpected server
requests close the connection rather than granting tools or credentials.
No inference or automatic request replay is supported (#184) (#171).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import queue
import threading

from .. import __version__, platform_paths
from .codex_events import decode_event, CodexTokenUsageEvent, CodexEvent, _text
from .codex_requests import (decode_request, decode_resolved, CodexRequestDeclined, COMMAND, FILES, QUESTIONS,
                             ACCEPT, ACCEPT_SESSION)


class CodexRpcError(RuntimeError):
    """Public diagnostics never include raw RPC payloads or stderr."""


class CodexApprovalNotSent(CodexRpcError):
    """Approval failed before queueing bytes; manual Answer is still usable."""


class CodexRememberedApprovalError(CodexRpcError):
    REASONS = {
        'not_sent': 'The answer was not sent and no new approval was kept. Open Answer to try again.',
        'kept_uncertain': 'The approval was kept, but its answer could not be confirmed. Use Stop to end any running turn and recheck this chat; the answer will not be resent.',
        'removed_uncertain': 'The approval was removed, but its earlier answer could not be confirmed. Use Stop to end any running turn and recheck this chat; the answer will not be resent.',
        'cleanup_failed': 'The answer was not sent, but removing the new approval failed. Check File, Chat Permissions before continuing.',
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.REASONS[code])


#: Said after any lost connection: Stop checks the chat's turn and reconnects (#390).
STOP_TO_RECONNECT = "Use Stop to reconnect."


class CodexConnectionFailure(CodexRpcError):
    """Fixed connection reasons, never server payloads or exception text."""
    REASONS = {
        'stream_closed': 'Codex connection interrupted: the stream closed. ' + STOP_TO_RECONNECT,
        'request_timeout': 'Codex connection interrupted: a request timed out. ' + STOP_TO_RECONNECT,
        'write_failed': 'Codex connection interrupted: a request could not be written. ' + STOP_TO_RECONNECT,
        'invalid_protocol': 'Codex connection interrupted: an invalid protocol message was received. ' + STOP_TO_RECONNECT,
        'unsupported_request': 'Codex connection interrupted: an unsupported approval or question was received. ' + STOP_TO_RECONNECT,
    }

    def __init__(self, code):
        self.code = code if code in self.REASONS else 'invalid_protocol'
        super().__init__(self.REASONS[self.code])


class CodexRequestTimeout(CodexRpcError):
    REASONS = {'request_timeout': 'Codex request timed out. The connection is still available.'}
    code = 'request_timeout'

    def __init__(self):
        super().__init__(self.REASONS[self.code])


class CodexSteerRefused(CodexRpcError):
    """A steer (#239) refused before anything was sent; fixed reasons are safe to report."""
    REASONS = {
        'not_running': 'Codex had already finished that turn, so the message was not sent.',
        'busy': 'Codex is still taking your last steered message, so this one was not sent. Try again in a moment.',
        'stopping': 'This Codex turn is stopping, so the message was not sent.',
        'changed': 'That queued message changed before it could be steered.',
        'refused': 'Codex did not take the message into its turn, so it was not sent.',
    }

    def __init__(self, code):
        self.code = code if code in self.REASONS else 'refused'
        super().__init__(self.REASONS[self.code])


class CodexTurnRejected(CodexRpcError):
    """The inference request was never queued; fixed reasons are safe to report."""
    REASONS = {
        'auto_review_unavailable': ('Codex could not confirm automatic approval review. This Codex version or managed policy '
                                  'may not allow it. No message was sent. Update Codex or choose human review.'),
        'chatgpt_required': 'Codex turns require ChatGPT sign-in.',
        'not_idle': 'This Codex chat is not confirmed idle. No new turn was sent.',
        'preflight_failed': 'Codex could not verify this chat before sending. No new turn was sent.',
        'billing_route': 'Codex turns require the default ChatGPT billing route',
        'verified_thread': 'Codex turn requires a verified thread',
        'configuration': 'Codex turn configuration is unavailable',
        'provider': 'Codex turns require the built-in OpenAI provider',
        'command_rules': 'The Chat Place could not check Codex saved command rules. No new turn was sent.',
        'project_rules': 'This folder supplies Codex saved command rules. Choose a folder without project rules, or move those rules out of the project before trying again. No new turn was sent.',
        'mcp_tools': 'Codex external tools could not be disabled for this chat. No new turn was sent.',
        # Likely, not certain: Codex also falls back to read-only when managed
        # requirements allow only read-only, or a profile can't be met (#257).
        'sandbox_unavailable': "Codex reported this chat as read-only, so it can't edit its folder. This usually means Codex's Windows sandbox isn't set up: set it up with Set up Agent sandbox in the Codex app, or with the windows sandbox setting in Codex's config.toml. Otherwise your Codex settings may allow only read-only. Try again, or choose read-only. No new turn was sent.",
    }

    def __init__(self, code):
        self.code = code if code in self.REASONS else 'preflight_failed'
        super().__init__(self.REASONS[self.code])


def is_chatgpt_account(result):
    """Whether an ``account/read`` answer is the account turns accept."""
    return (isinstance(result, dict) and isinstance(result.get("account"), dict)
            and result["account"].get("type") == "chatgpt"
            and result.get("requiresOpenaiAuth") is True)


def verify_codex_rule_layers(result):
    count, blocked = platform_paths.codex_rule_inventory(result.get('layers'))
    if count is None:
        raise CodexTurnRejected('command_rules')
    if blocked:
        error = CodexTurnRejected('project_rules')
        error.rules_folder = blocked
        raise error
    return count


def codex_tool_overrides(config):
    servers = config.get('mcp_servers')
    if servers is None:
        servers = {}
    if not isinstance(servers, dict) or any(not isinstance(v, dict) for v in servers.values()):
        raise CodexTurnRejected('mcp_tools')
    # External services are outside the selected folder's sandbox. Disable them
    # in this thread only, including app/plugin-provided servers (#255).
    return {'mcp_servers': {name: {'enabled': False} for name in servers},
            'features': {'plugins': False, 'apps': False}}


def verify_codex_tools(connection, thread_id):
    result = connection.request('mcpServerStatus/list', {
        'threadId': thread_id, 'limit': 100, 'detail': 'toolsAndAuthOnly'})
    rows = result.get('data')
    # A partial inventory cannot prove isolation. Fail closed on older CLIs,
    # an unknown status, active tools, or an unexpectedly large inventory.
    if (not isinstance(rows, list) or len(rows) > 100
            or result.get('nextCursor') not in (None, '')
            or (result.get('nextCursor') is not None and type(result.get('nextCursor')) is not str)
            or any(not isinstance(row, dict) or row.get('runtimeStatus') != 'disabled'
                   or row.get('tools') != {} for row in rows)):
        raise CodexTurnRejected('mcp_tools')


@dataclass
class _Pending:
    ready: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: CodexRpcError | None = None


class ReadOnlyCodexConnection:
    METHODS = frozenset({"account/read", "account/rateLimits/read", "thread/list", "thread/read", "config/read",
                         "thread/turns/list", "thread/items/list", "model/list", "mcpServerStatus/list"})
    MAX_FRAME = 32 * 1024 * 1024
    MAX_REQUEST = 64 * 1024
    MAX_PENDING = 32
    MAX_EVENTS = 32

    def __init__(self, executable=None, *, timeout=20.0, start=None, initialize=True,
                 receive_events=False, receive_requests=False):
        if timeout <= 0:
            raise ValueError("Codex request timeout must be positive")
        if type(receive_events) is not bool:
            raise ValueError("Codex event selection must be boolean")
        if type(receive_requests) is not bool or (receive_requests and not receive_events):
            raise ValueError("Codex requests require an event consumer")
        self._receive_requests = receive_requests
        self._server_pending = {}
        self._receive_events = receive_events
        self._events = queue.Queue(maxsize=self.MAX_EVENTS)
        self._token_events = {}
        self._display_events = {}
        self._display_truncated = {}
        self._deferred_event = None
        self._abandoned = set()
        self._retired_turns = {}
        self._abandoned_before = 0
        self._timeout = timeout
        self._lock = threading.Lock()
        self._initialize_lock = threading.Lock()
        self._initialized = False
        self._pending: dict[int, _Pending] = {}
        self._next_id = 0
        self._closed = False
        self._failure = None
        self._cleanup_done = threading.Event()
        self._cleanup_failed = False
        self._outgoing = queue.Queue()
        self._process = (start or platform_paths.start_codex_server)(executable)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._writer = threading.Thread(target=self._write, daemon=True)
        self._reader.start()
        self._writer.start()
        if initialize:
            self.initialize()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def initialize(self):
        """Allow the owner to register this process before the handshake.

        Shutdown can then interrupt a blocked handshake instead of waiting
        until the constructor returns to learn which process it owns.
        """
        with self._initialize_lock:
            if self._initialized:
                return
            self._initialize()

    def _initialize(self):
        try:
            self._request("initialize", {"clientInfo": {"name": "thechatplace",
                           "title": "The Chat Place", "version": __version__},
                           "capabilities": {"experimentalApi": True}})
            self._enqueue({"method": "initialized", "params": {}})
            self._initialized = True
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def request(self, method: str, params: dict) -> dict:
        if method not in self.METHODS:
            raise ValueError("Method is not supported by the read-only Codex connection")
        if not self._initialized:
            raise CodexRpcError("Codex connection is not initialized")
        return self._request(method, params)

    def pop_event(self):
        """Keep controls prompt, but flush final display data before completion."""
        with self._lock:
            if self._closed:
                raise getattr(self, '_failure', None) or CodexConnectionFailure('stream_closed')
            event = getattr(self, '_deferred_event', None)
            if event is None:
                try:
                    event = self._events.get_nowait()
                except queue.Empty:
                    pass
            displays = getattr(self, '_display_events', {})
            truncated = getattr(self, '_display_truncated', {})
            terminal = getattr(event, 'kind', None) == 'turn_completed'
            pair = (event.thread_id, event.turn_id) if terminal else None
            key = next((k for k in displays if pair is None or k[:2] == pair), None)
            if key is not None and (event is None or terminal):
                self._deferred_event = event
                return displays.pop(key)
            cut = next((k for k in truncated if pair is None or k == pair), None)
            if cut is not None and (event is None or terminal):
                self._deferred_event = event
                truncated.pop(cut)
                return CodexEvent('display_truncated', *cut)
            self._deferred_event = None
            if event is not None:
                return event
            pending = getattr(self, '_token_events', {})
            return pending.pop(next(iter(pending))) if pending else None

    def _display_event(self, event):
        # Presentation volume must never close a healthy inference stream.
        with self._lock:
            pending = self._display_events
            category = 'reply' if event.kind in ('text_delta', 'text_completed') else event.kind
            key = (event.thread_id, event.turn_id, event.item_id, category)
            previous = pending.get(key)
            if event.kind == 'text_delta' and previous is not None:
                if previous.kind == 'text_completed':
                    return
                event = replace(event, text=_text(previous.text + event.text))
            if key not in pending and len(pending) >= 128:
                old = pending.pop(next(iter(pending)))
                cuts = self._display_truncated
                if len(cuts) >= 128:
                    cuts.pop(next(iter(cuts)))
                cuts[(old.thread_id, old.turn_id)] = True
            pending[key] = event

    def _accept_server_request(self, request):
        return False  # read-only callers never receive executable approvals

    def respond_request(self, request, result, *, dispatch_guard=None):
        result = request.validate_answer(result)
        try:
            if result.get("decision") in (ACCEPT, ACCEPT_SESSION):
                account = self.request("account/read", {"refreshToken": True})
                if not is_chatgpt_account(account):
                    raise CodexRpcError("Codex approvals require ChatGPT sign-in")
            payload = self._encode({"id": request.request_id, "result": result})
        except Exception as error:
            raise CodexApprovalNotSent("Codex approval was not sent. Check the connection and ChatGPT sign-in.") from error
        from contextlib import nullcontext
        # Sign-in can take seconds. The caller's revocation lock covers only
        # the final check and enqueue, never the account/read round trip.
        with dispatch_guard() if dispatch_guard is not None else nullcontext():
            with self._lock:
                if self._closed or self._server_pending.get(request.request_id) is not request:
                    raise CodexApprovalNotSent("Codex request is no longer waiting")
                self._outgoing.put(payload)
                del self._server_pending[request.request_id]

    def _request(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("Codex request parameters must be an object")
        if method in ("turn/start", "turn/steer"):
            # Guard the wire boundary even when execution is added to METHODS.
            # Never cache this across turns or check a different connection:
            # sign-in/provider state can change while the server stays alive.
            try:
                account = self.request("account/read", {"refreshToken": True})
                if not is_chatgpt_account(account):
                    raise CodexTurnRejected('chatgpt_required')
                self._verify_turn_route(params)
            except (CodexTurnRejected, CodexSteerRefused):
                raise
            except Exception as error:
                code = next((code for code, message in CodexTurnRejected.REASONS.items()
                             if type(error) is CodexRpcError and str(error) == message), 'preflight_failed')
                raise CodexTurnRejected(code) from None
        pending = _Pending()
        with self._lock:
            if self._closed:
                raise CodexRpcError("Codex connection closed")
            if len(self._pending) >= self.MAX_PENDING:
                raise CodexRpcError("Too many pending Codex requests")
            self._next_id += 1
            request_id = self._next_id
            payload = self._encode({"id": request_id, "method": method, "params": params})
            self._pending[request_id] = pending
            self._outgoing.put(payload)
        if not pending.ready.wait(self._timeout):
            with self._lock:
                expired = self._pending.pop(request_id, None) is pending
                if expired:
                    self._abandoned.add(request_id)
                    if len(self._abandoned) > 1024:
                        oldest = min(self._abandoned)
                        self._abandoned.remove(oldest)
                        self._abandoned_before = max(self._abandoned_before, oldest)
            if expired:
                # Only an uncertain inference send can poison other turns.
                if method in ('turn/start', 'turn/steer'):
                    self._fail("Codex request timed out")
                    raise CodexConnectionFailure('request_timeout')
                raise CodexRequestTimeout()
        if pending.error:
            raise pending.error
        return pending.result

    def _verify_turn_route(self, params):
        """Reject alternate billing routes before any future turn is queued.

        Account type alone cannot establish the target thread's model provider.
        Inspect metadata without loading turns, then resolve configuration for
        its working directory. Production inference remains disabled; execution
        must also pin and validate the active start/resume configuration (#184).
        """
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise CodexRpcError("Codex turn requires a verified thread")
        result = self.request("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread")
        if (not isinstance(thread, dict) or thread.get("id") != thread_id
                or thread.get("modelProvider") != "openai"):
            raise CodexRpcError("Codex turns require the built-in OpenAI provider")
        cwd = params.get("cwd", thread.get("cwd"))
        if not isinstance(cwd, str) or not cwd.strip():
            raise CodexRpcError("Codex turn configuration is unavailable")
        result = self.request("config/read", {"cwd": cwd, "includeLayers": True})
        config = result.get("config")
        if not isinstance(config, dict):
            raise CodexRpcError("Codex turn configuration is unavailable")
        verify_codex_tools(self, thread_id)
        providers = config.get("model_providers", {})
        if (config.get("model_provider") not in (None, "openai")
                or config.get("forced_login_method") not in (None, "chatgpt")
                or config.get("openai_base_url") is not None
                or config.get("chatgpt_base_url") not in (None, "https://chatgpt.com/backend-api/")
                or not isinstance(providers, dict) or "openai" in providers):
            # Reject rather than silently changing a user's configured route.
            # The older CLI allowed overriding built-in provider definitions.
            raise CodexRpcError("Codex turns require the default ChatGPT billing route")
        verify_codex_rule_layers(result)

    def _encode(self, message):
        payload = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > self.MAX_REQUEST:
            raise ValueError("Codex request is too large")
        return payload

    def _enqueue(self, message):
        payload = self._encode(message)
        with self._lock:
            if self._closed:
                raise CodexRpcError("Codex connection closed")
            self._outgoing.put(payload)

    def _write(self):
        try:
            while (payload := self._outgoing.get()) is not None:
                self._process.stdin.write(payload)
                self._process.stdin.flush()
        except (OSError, ValueError):
            self._fail("Codex connection write failed")

    def _read(self):
        try:
            while True:
                raw = self._process.stdout.readline(self.MAX_FRAME + 1)
                if not raw:
                    self._fail("Codex connection closed")
                    return
                if len(raw) > self.MAX_FRAME or not raw.endswith(b"\n"):
                    raise ValueError("Invalid frame")
                message = json.loads(raw.decode("utf-8"))
                self._receive(message)
        except (OSError, ValueError, TypeError, RecursionError):
            self._fail("Codex returned an invalid protocol message")

    def _receive(self, message):
        if not isinstance(message, dict):
            raise ValueError("Invalid protocol object")
        if "method" in message:
            if not isinstance(message["method"], str):
                raise ValueError("Unexpected server request")
            if "id" in message:
                if not self._receive_requests:
                    raise ValueError("Unexpected server request")
                try:
                    request = decode_request(message["id"], message["method"], message.get("params"))
                except ValueError as error:
                    self._decline_unsupported_request(message, getattr(error, 'reason', ''))
                    return
                if not self._accept_server_request(request):
                    raise ValueError("Unowned Codex request")
                with self._lock:
                    if (request.thread_id, request.turn_id) in self._retired_turns:
                        self._outgoing.put(self._encode({'id': request.request_id, 'error': {
                            'code': -32600, 'message': 'This Codex turn has ended.'}}))
                        return
                    if (request.request_id in self._server_pending
                            or len(self._server_pending) >= self.MAX_EVENTS):
                        raise ValueError("Codex request limit exceeded")
                    self._server_pending[request.request_id] = request
                try:
                    self._events.put_nowait(request)
                except queue.Full:
                    raise ValueError("Codex request queue overflow") from None
                return
            if self._receive_events:
                if self._receive_requests and message["method"] == "serverRequest/resolved":
                    event = decode_resolved(message.get("params"))
                    with self._lock:
                        request = self._server_pending.get(event.request_id)
                        if request is not None and request.thread_id != event.thread_id:
                            raise ValueError("Codex resolution belongs to another chat")
                        self._server_pending.pop(event.request_id, None)
                else:
                    event = decode_event(message["method"], message.get("params"))
                if event is not None:
                    if isinstance(event, CodexTokenUsageEvent):
                        # Optional counters are replaceable snapshots. Keep
                        # them out of the bounded approval/completion queue.
                        with self._lock:
                            key = (event.thread_id, event.turn_id)
                            pending = self._token_events
                            if key not in pending and len(pending) >= self.MAX_EVENTS:
                                pending.pop(next(iter(pending)))
                            pending[key] = event
                        return
                    if getattr(event, 'kind', None) == 'context_compacted':
                        with self._lock:
                            self._token_events.pop((event.thread_id, event.turn_id), None)
                    # Command stdout is read from completed history, not this
                    # runtime's reply/approval queue. Unconsumed output bursts
                    # must not crowd out Stop, approvals or completion (#247).
                    if getattr(event, 'kind', None) == 'tool_output_delta':
                        return
                    if getattr(event, 'kind', None) in ('text_delta', 'text_completed', 'activity', 'file_changes', 'plan_updated'):
                        self._display_event(event)
                        return
                    if getattr(event, "kind", None) == "turn_completed":
                        with self._lock:
                            self._server_pending = {key: request for key, request in self._server_pending.items()
                                if (request.thread_id, request.turn_id) != (event.thread_id, event.turn_id)}
                    try:
                        self._events.put_nowait(event)
                    except queue.Full:
                        # Losing a terminal event would leave a turn stuck or
                        # invite an unsafe retry. Disconnect for reconciliation.
                        raise ValueError("Codex event queue overflow") from None
            return
        request_id = message.get("id")
        if type(request_id) is not int or request_id <= 0:
            raise ValueError("Invalid response id")
        with self._lock:
            if self._closed:
                return
            if (request_id in getattr(self, '_abandoned', ())
                    or request_id <= getattr(self, '_abandoned_before', 0)):
                return  # a late reply cannot resurrect a timed-out request
            pending = self._pending.get(request_id)
            if pending is None:
                raise ValueError("Unknown response id")
            if "error" in message and "result" not in message:
                pending.error = CodexRpcError("Codex request failed")
            elif "error" not in message and isinstance(message.get("result"), dict):
                pending.result = message["result"]
            else:
                raise ValueError("Invalid response")
            del self._pending[request_id]
            pending.ready.set()

    def forget_turn_requests(self, thread_id, turn_id):
        """Confirmed ended turns may not emit serverRequest/resolved."""
        with self._lock:
            if len(self._retired_turns) >= 128:
                self._retired_turns.pop(next(iter(self._retired_turns)))
            self._retired_turns[(thread_id, turn_id)] = True
            self._server_pending = {key: request for key, request in self._server_pending.items()
                if (request.thread_id, request.turn_id) != (thread_id, turn_id)}

    def _decline_unsupported_request(self, message, reason=''):
        identity = message['id']
        if (not (type(identity) is int or isinstance(identity, str))
                or isinstance(identity, str) and (not identity.strip() or len(identity) > 256)):
            raise ValueError('Invalid Codex request identity')
        # Error responses grant nothing. Do not echo params, command text or
        # an untrusted method name into logs or speech (#247).
        code = -32602 if message['method'] in (COMMAND, FILES, QUESTIONS) else -32601
        payload = self._encode({'id': identity, 'error': {
            'code': code, 'message': 'The Chat Place does not support this request or its permission scope.'}})
        with self._lock:
            if self._closed or identity in self._server_pending:
                raise ValueError('Duplicate or expired Codex request identity')
            self._outgoing.put(payload)
        try:
            kind = {COMMAND: 'command approval', FILES: 'file approval', QUESTIONS: 'question'}.get(message['method'], 'request')
            params = message.get('params')
            thread = params.get('threadId') if isinstance(params, dict) else None
            if not isinstance(thread, str) or not thread.strip() or len(thread) > 256:
                thread = ''  # unknown chat: every running chat hears about it
            self._events.put_nowait(CodexRequestDeclined(
                kind, reason if kind == 'command approval' else '', thread))
        except queue.Full:
            raise ValueError('Codex request notice queue overflow') from None

    def _fail(self, reason):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            code = {'Codex request timed out': 'request_timeout',
                    'Codex connection closed': 'stream_closed',
                    'Codex connection write failed': 'write_failed',
                    'Codex unsupported server request': 'unsupported_request'}.get(reason, 'invalid_protocol')
            self._failure = CodexConnectionFailure(code)
            for pending in self._pending.values():
                pending.error = self._failure
                pending.ready.set()
            self._pending.clear()
            self._server_pending.clear()
            getattr(self, '_token_events', {}).clear()
            getattr(self, '_display_events', {}).clear()
            getattr(self, '_display_truncated', {}).clear()
            self._deferred_event = None
            self._outgoing.put(None)
        try:
            self._process.close()
        except Exception:  # noqa: BLE001 - redact cleanup failures too
            self._cleanup_failed = True
        finally:
            self._cleanup_done.set()

    def close(self):
        """Wait for process cleanup, including a failure on another worker."""
        self._fail("Codex connection closed")
        if not self._cleanup_done.wait(10) or self._cleanup_failed:
            raise CodexRpcError("Codex process cleanup failed")
