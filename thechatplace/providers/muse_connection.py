"""Bounded read-only MSP connection for verified Muse history schemas.

Account setup keeps its separate allowlist. History never attaches, resumes,
subscribes, answers permissions or submits a turn. Experimental wire changes
must be verified before accepting a different fingerprint.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import queue
import threading

from .. import __version__, platform_paths


SCHEMA_FINGERPRINT = "sha256:77dda18550adef14e257b015fe4e6490b722723f7d526df5ba4867939fff68a3"
EXPERIMENTAL_FINGERPRINT = "sha256:43a76702a69cdfe355db9edac2c7efdc8f75ae40daaae28789ad1d6ec68c3603"

# Muse's launcher can update the binary when the user starts a CLI chat. A
# different stable or experimental fingerprint needs native schema/read checks;
# accept verified pairs together, never an arbitrary mix of their components.
VERIFIED_SCHEMAS = frozenset({
    (SCHEMA_FINGERPRINT, EXPERIMENTAL_FINGERPRINT),  # 1.4.18
    ("sha256:283b1b125dcc9172928fb5d6711a92a353796d030a7c186988ed527a13cf07f0",
     "sha256:c2f9255c3e027d8c167a64f638429655f2121d5b28d7b5f6b5da437a99dbe57a"),  # 1.4.20
})


class MuseHistoryError(RuntimeError):
    """Public diagnostics never contain raw host errors or private history."""


class MuseRequestError(MuseHistoryError):
    """A rejected request leaves the transport usable; retain only its code."""

    def __init__(self, code: int):
        super().__init__("Muse Code could not perform that request. Check the chat and try again.")
        self.code = code


@dataclass
class _Pending:
    ready: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error_code: int | None = None


class HistoryConnection:
    METHODS = frozenset({"session/list", "session/read"})
    EVENTS = False

    def __init__(self, executable, *, start=None, timeout=15.,
                 frame_limit=10 * 1024 * 1024, initialize=True,
                 event_limit=256, event_bytes_limit=10 * 1024 * 1024):
        if (type(event_limit) is not int or event_limit < 1
                or type(event_bytes_limit) is not int or event_bytes_limit < 1):
            raise ValueError("Muse event bounds must be positive integers.")
        self._timeout, self._frame_limit = timeout, frame_limit
        self._lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._cleanup_complete = False
        self._pending = {}
        self._next_id = 0
        self._error = ""
        self._closed = False
        self._initialized = False
        self._events = deque()
        self._server_requests = set()
        self._event_bytes = 0
        self._event_limit, self._event_bytes_limit = event_limit, event_bytes_limit
        self._outgoing = queue.Queue(maxsize=8)
        try:
            self._host = (start or platform_paths.start_muse_history_server)(executable)
        except Exception:
            raise MuseHistoryError("Muse Code history couldn't be started. Check its installation and refresh.") from None
        self._reader = threading.Thread(target=self._read, name="muse-history-read", daemon=True)
        self._writer = threading.Thread(target=self._write, name="muse-history-write", daemon=True)
        self._reader.start()
        self._writer.start()
        if initialize:
            self.initialize()

    @property
    def closed(self):
        with self._lock:
            return self._closed or bool(self._error)

    def initialize(self):
        try:
            handshake = self._request("initialize", {
                "clientInfo": {"name": "thechatplace", "version": __version__},
                "capabilities": {"experimentalApi": True, "userInputDialogs": self.EVENTS}})
            schema = handshake.get("schema")
            if (not isinstance(schema, dict) or type(schema.get("version")) is not int
                    or schema["version"] != 1
                    or not isinstance(schema.get("fingerprint"), str)
                    or not isinstance(schema.get("experimentalFingerprint"), str)
                    or (schema["fingerprint"], schema["experimentalFingerprint"]) not in VERIFIED_SCHEMAS
                    or not isinstance(handshake.get("serverInfo"), dict)
                    or handshake.get("experimentalApi") is not True):
                raise MuseHistoryError("Muse Code's history protocol isn't compatible with this app. "
                                       "Use a supported Muse Code build and refresh.")
            self._outgoing.put_nowait(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
            self._initialized = True
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, method, params):
        if method not in self.METHODS or not isinstance(params, dict):
            raise MuseHistoryError("This is a read-only Muse Code history connection.")
        if not self._initialized:
            raise MuseHistoryError("Muse Code history isn't initialized.")
        return self._request(method, params)

    def _request(self, method, params):
        with self._request_lock:
            with self._lock:
                if self._closed or self._error:
                    raise MuseHistoryError(self._error or "Muse Code history is closed.")
                self._next_id += 1
                ident = self._next_id
                pending = _Pending()
                self._pending[ident] = pending
            try:
                data = json.dumps({"jsonrpc": "2.0", "id": ident,
                                   "method": method, "params": params},
                                  separators=(",", ":")).encode("utf-8") + b"\n"
                if len(data) > self._frame_limit:
                    raise MuseHistoryError("Muse Code's history request is too large.")
                self._outgoing.put_nowait(data)
                if not pending.ready.wait(self._timeout):
                    raise MuseHistoryError("Muse Code history timed out. Try refreshing.")
                with self._lock:
                    error = self._error
                if pending.error_code is not None and not error:
                    raise MuseRequestError(pending.error_code)
                if error or pending.result is None:
                    raise MuseHistoryError(error or "Muse Code history is unavailable. Try refreshing.")
                return pending.result
            except (queue.Full, ValueError, TypeError):
                self.close()
                raise MuseHistoryError("Muse Code's history request couldn't be sent.") from None
            except MuseRequestError:
                raise
            except MuseHistoryError:
                self.close()
                raise
            finally:
                with self._lock:
                    self._pending.pop(ident, None)

    def _fail(self):
        with self._lock:
            self._error = "Muse Code history is unavailable. Check its installation and try refreshing."
            for pending in self._pending.values():
                pending.ready.set()

    def _write(self):
        try:
            while True:
                data = self._outgoing.get()
                if data is None:
                    return
                self._host.stdin.write(data)
                self._host.stdin.flush()
        except Exception:
            self._fail()

    def _read(self):
        try:
            while True:
                raw = self._host.stdout.readline(self._frame_limit + 1)
                if not raw or len(raw) > self._frame_limit or not raw.endswith(b"\n"):
                    raise ValueError("frame")
                if not raw.strip():
                    continue
                message = json.loads(raw.decode("utf-8"))
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("envelope")
                if "method" in message:
                    if self.EVENTS:
                        self._receive_event(message, len(raw))
                    elif "id" in message:
                        raise ValueError("server request")
                    # No subscriptions on this connection. Ignore unsolicited
                    # notifications without storing unbounded private content.
                    continue
                ident = message.get("id")
                if type(ident) is not int:
                    raise ValueError("id")
                with self._lock:
                    pending = self._pending.get(ident)
                    if pending is None or pending.ready.is_set():
                        raise ValueError("response")
                    if "error" in message:
                        error = message["error"]
                        if ("result" in message or not isinstance(error, dict)
                                or type(error.get("code")) is not int
                                or not isinstance(error.get("message"), str)):
                            raise ValueError("request error")
                        pending.error_code = error["code"]
                    elif isinstance(message.get("result"), dict):
                        pending.result = message["result"]
                    else:
                        raise ValueError("history response")
                    pending.ready.set()
        except Exception:
            self._fail()

    def close(self):
        with self._close_lock:
            if self._cleanup_complete:
                return
            with self._lock:
                self._closed = True
                self._events.clear()
                self._server_requests.clear()
                self._event_bytes = 0
                for pending in self._pending.values():
                    pending.ready.set()
            try:
                self._outgoing.put_nowait(None)
            except queue.Full:
                pass
            try:
                self._host.close()
                self._cleanup_complete = True
            except Exception:
                raise MuseHistoryError("Muse Code history cleanup failed. Restart The Chat Place.") from None
            finally:
                for thread in (self._reader, self._writer):
                    if thread is not threading.current_thread():
                        thread.join(timeout=1.)


class ExecutionConnection(HistoryConnection):
    """Owned-turn connection: session start, turns, approvals, views.

    Same handshake, framing, and shutdown as history. The allowlist covers
    only starting an owned session, running and cancelling its turns,
    deciding its approvals/questions, and reading its views and models.
    Owned resume is needed to reopen an app-created chat. The provider must
    verify ownership before calling it. History keeps its separate allowlist.
    Fork, rewind, settings, plugins, hooks, credentials and account methods
    remain refused, as does admission/* (peerMessaging: another chat asking
    to message this one), which this app never requests.
    """

    METHODS = frozenset({
        "session/start",
        "session/resume",
        "session/read",
        "session/leave",
        "turn/start",
        "turn/cancel",
        "turn/steer",
        "session/setModel",
        "turn/interrupt",
        "approval/listPending",
        "approval/decide",
        "userInput/answer",
        "userInput/cancel",
        "view/subscribe",
        "view/unsubscribe",
        "view/page",
        "model/list",
        "usage/read",
        "permissionProfile/list",
    })
    EVENTS = True

    def _receive_event(self, message: dict, size: int) -> None:
        if (not isinstance(message.get("method"), str) or not message["method"]
                or not isinstance(message.get("params", {}), dict)
                or ("id" in message and type(message["id"]) not in (int, str))
                or (isinstance(message.get("id"), str) and len(message["id"]) > 8192)):
            raise ValueError("event envelope")
        with self._lock:
            if self._closed:
                return
            if "id" in message:
                if (message["method"] not in {
                        "approval/request", "userInput/request"}
                        or message["id"] in self._server_requests
                        or len(self._server_requests) >= self._event_limit):
                    raise ValueError("server request")
            if (len(self._events) >= self._event_limit
                    or self._event_bytes + size > self._event_bytes_limit):
                # Losing a completion or approval would leave unsafe local
                # state. Fail the connection so the caller must reconcile.
                raise ValueError("event overflow")
            if "id" in message:
                self._server_requests.add(message["id"])
            self._events.append(message)
            self._event_bytes += size

    def drain_events(self) -> list[dict]:
        """Return pending wire events once, without waiting or answering them."""
        with self._lock:
            if self._closed or self._error:
                raise MuseHistoryError(self._error or "Muse Code execution is closed.")
            events = list(self._events)
            self._events.clear()
            self._event_bytes = 0
            return events

    def respond_receipt(self, request_id: str | int) -> None:
        """Acknowledge presentation only; native decisions use separate commands."""
        with self._lock:
            if self._closed or self._error:
                raise MuseHistoryError(self._error or "Muse Code execution is closed.")
            if type(request_id) not in (int, str) or request_id not in self._server_requests:
                raise MuseHistoryError("That Muse Code request is no longer awaiting presentation.")
            data = json.dumps({"jsonrpc": "2.0", "id": request_id,
                               "result": {}}, separators=(",", ":")).encode() + b"\n"
            try:
                self._outgoing.put_nowait(data)
            except queue.Full:
                # Keep the id pending, so a successful later receipt still
                # corresponds to a request this connection actually received.
                raise MuseHistoryError("Muse Code's presentation receipt couldn't be sent.") from None
            self._server_requests.remove(request_id)

    def request(self, method, params):
        if method not in self.METHODS or not isinstance(params, dict):
            raise MuseHistoryError("This Muse Code execution connection cannot perform that action.")
        return super().request(method, params)


class ManagedMuseConnection:
    """One lazy host, published before initialize so shutdown can interrupt it."""
    METHODS = HistoryConnection.METHODS

    def __init__(self, executable=None, *, factory=None):
        self._executable = executable
        self._factory = factory or (lambda exe: HistoryConnection(exe, initialize=False))
        self._condition = threading.Condition()
        self._operation_lock = threading.Lock()
        self._connection = None
        self._connecting = False
        self._closed = False

    def request(self, method, params):
        if method not in self.METHODS or not isinstance(params, dict):
            raise MuseHistoryError("This is a read-only Muse Code history connection.")
        with self._operation_lock:
            return self._get().request(method, params)

    @property
    def closed(self):
        """True after close, or when the current host (if any) has failed."""
        with self._condition:
            if self._closed:
                return True
            connection = self._connection
        return connection is not None and connection.closed

    def refresh_catalog(self):
        """Start the next catalog pass on a host that scans newly saved logs.

        Muse 1.4.20's existing host missed an externally created echo session
        until a fresh host initialized the index. Wait for any current history
        request before rotating; closing the app can still interrupt requests.
        """
        with self._operation_lock:
            with self._condition:
                if self._closed:
                    raise MuseHistoryError("Muse Code history is closed.")
                previous = self._connection
            if previous is not None:
                # Keep ownership until cleanup succeeds, so a later attempt
                # cannot launch a replacement alongside an unclosed host.
                previous.close()
            with self._condition:
                if self._connection is previous:
                    self._connection = None
                if self._closed:
                    raise MuseHistoryError("Muse Code history is closed.")

    def _get(self):
        with self._condition:
            while self._connecting and not self._closed:
                self._condition.wait()
            if self._closed:
                raise MuseHistoryError("Muse Code history is closed.")
            if self._connection is not None and not self._connection.closed:
                return self._connection
            previous = self._connection
            self._connecting = True
        connection = None
        try:
            if previous is not None:
                previous.close()
            connection = self._factory(self._executable)
            with self._condition:
                self._connection = connection
                if self._closed:
                    raise MuseHistoryError("Muse Code history is closed.")
            connection.initialize()
            with self._condition:
                if self._closed:
                    raise MuseHistoryError("Muse Code history is closed.")
            return connection
        except Exception:
            if connection is not None:
                connection.close()
            raise MuseHistoryError("Muse Code history is unavailable. Check its installation and refresh.") from None
        finally:
            with self._condition:
                self._connecting = False
                self._condition.notify_all()

    def close(self):
        # A late factory result observes _closed and closes itself. Do not
        # hold up shutdown waiting for an uninterruptible process factory.
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            connection = self._connection
        if connection is not None:
            connection.close()


class ManagedMuseExecution(ManagedMuseConnection):
    """Lazy execution host: nothing starts until the first owned request."""
    METHODS = ExecutionConnection.METHODS

    def __init__(self, executable=None, *, factory=None):
        super().__init__(executable, factory=factory
                         or (lambda exe: ExecutionConnection(exe, initialize=False)))

    def drain_events(self) -> list[dict]:
        """Polling never starts or replaces a host and cannot replay a turn."""
        with self._condition:
            if self._closed:
                raise MuseHistoryError("Muse Code execution is closed.")
            connection = self._connection
        return [] if connection is None else connection.drain_events()

    def respond_receipt(self, request_id: str | int) -> None:
        with self._condition:
            if self._closed or self._connection is None:
                raise MuseHistoryError("Muse Code execution is closed.")
            connection = self._connection
        connection.respond_receipt(request_id)
