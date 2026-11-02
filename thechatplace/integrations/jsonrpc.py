"""JSON-RPC over stdio, one object per line: what ACP and Codex both speak.

Writing is on a thread of its own from a bounded queue, so an agent that stops
reading can't freeze the window or stop its replies being read. Lines too long
are refused rather than buffered. Bad lines are counted, never fatal.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Callable, Dict, Optional

from ..contracts import IntegrationError
from ..drift import DriftCounters

MAX_LINE_BYTES = 32 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_PENDING = 64
MAX_OUTGOING = 256
REQUEST_SECONDS = 60.0


class RpcError(IntegrationError):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class NotSent(RpcError):
    """The message never left this app, so the agent can't have seen it."""


class Connection:
    """Every callback is told which connection it's from: an old one's closing
    or late messages must never touch whatever has replaced it."""

    def __init__(self, process, on_notification: Callable[["Connection", str, dict], None],
                 on_request: Callable[["Connection", object, str, dict], None],
                 on_closed: Optional[Callable[["Connection"], None]] = None,
                 drift: Optional[DriftCounters] = None, name: str = "The agent"):
        self._process = process
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_closed = on_closed
        self._drift = drift or DriftCounters()
        self._name = name
        self._lock = threading.Lock()
        self._pending: Dict[int, "queue.Queue"] = {}
        self._next_id = 0
        self._closed = False
        self._outgoing: "queue.Queue" = queue.Queue(maxsize=MAX_OUTGOING)
        threading.Thread(target=self._write_loop, daemon=True, name="rpc-writer").start()
        threading.Thread(target=self._read_loop, daemon=True, name="rpc-reader").start()

    @property
    def closed(self) -> bool:
        return self._closed

    def _write(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(data) > MAX_MESSAGE_BYTES:
            raise NotSent("That message is too long to send.")
        if self._closed:
            raise NotSent(f"{self._name} isn't running.")
        try:
            self._outgoing.put_nowait(data)
        except queue.Full:
            threading.Thread(target=self.close, daemon=True).start()
            raise NotSent(f"{self._name} stopped responding.") from None

    def _write_loop(self) -> None:
        while True:
            data = self._outgoing.get()
            if data is None:
                return
            try:
                self._process.stdin.write(data)
                self._process.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return

    def request(self, method: str, params: dict, timeout: Optional[float] = REQUEST_SECONDS):
        """Send and wait. ``timeout`` None waits until an answer or exit."""
        waiter: "queue.Queue" = queue.Queue(maxsize=1)
        with self._lock:
            if len(self._pending) >= MAX_PENDING:
                raise NotSent(f"{self._name} has too many requests waiting.")
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = waiter
        try:
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        except Exception:
            with self._lock:
                self._pending.pop(request_id, None)
            raise
        try:
            kind, value = waiter.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(request_id, None)
            raise RpcError(f"{self._name} didn't answer in time.") from None
        if kind == "error":
            # The code decides what's said; the agent's own message may hold
            # paths or account names, so it isn't kept.
            raise RpcError(f"{self._name} couldn't do that.", value)
        if kind == "closed":
            raise RpcError(f"{self._name} closed before it answered.")
        return value

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, request_id, result: dict) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def respond_error(self, request_id, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def _read_loop(self) -> None:
        stdout = self._process.stdout
        try:
            while True:
                line = stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    self._drift.malformed("line-too-long")
                    break
                self._dispatch(line)
        except (OSError, ValueError):
            pass
        self.close()

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._drift.malformed("not-json")
            return
        if not isinstance(message, dict):
            self._drift.malformed("not-an-object")
            return
        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            is_request = "id" in message
            try:
                if is_request:
                    self._on_request(self, message["id"], method, params)
                else:
                    self._on_notification(self, method, params)
            except Exception:  # noqa: BLE001 - one bad message mustn't end the chat
                self._drift.malformed(method)
                if is_request:
                    try:
                        self.respond_error(message["id"], -32603, "The Chat Place couldn't handle this request.")
                    except RpcError:
                        pass
            return
        request_id = message.get("id")
        with self._lock:
            waiter = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
        if waiter is None:
            self._drift.malformed("unmatched-response")
            return
        if isinstance(message.get("error"), dict):
            waiter.put(("error", message["error"].get("code")))
        else:
            waiter.put(("result", message.get("result")))

    def close(self) -> None:
        with self._lock:
            first, self._closed = not self._closed, True
            pending, self._pending = self._pending, {}
        for waiter in pending.values():
            waiter.put(("closed", None))
        if not first:
            return
        try:
            self._outgoing.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._process.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
        if self._on_closed is not None:
            try:
                self._on_closed(self)
            except Exception:  # noqa: BLE001 - telling chats is best effort
                pass
