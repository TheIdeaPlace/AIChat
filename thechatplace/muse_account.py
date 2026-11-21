"""A bounded, account-only Muse Session Protocol connection.

The account surface is experimental and must be explicitly accepted by the
host. This connection cannot create sessions, send prompts or answer tools.
Wire reference: meta-models.github.io/muse-code-sdk/next/guides/msp-wire/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import queue
import threading

from . import __version__, platform_paths


class MuseAccountError(RuntimeError):
    """Fixed public diagnostics; never raw server errors or stderr."""


@dataclass
class _Pending:
    ready: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None


class AccountConnection:
    """Own one stdio host for a check or a confirmed device-code login."""

    _METHODS = frozenset({"account/read", "account/loginStart", "account/loginCancel"})

    def __init__(self, executable, *, start=None, timeout=15., frame_limit=10 * 1024 * 1024):
        self._timeout = timeout
        self._frame_limit = frame_limit
        self._lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._pending = {}
        self._next_id = 0
        self._error = ""
        self._closed = False
        self._outgoing = queue.Queue(maxsize=8)
        self._notifications = queue.Queue(maxsize=32)
        try:
            self._host = (start or platform_paths.start_muse_server)(executable)
        except Exception:
            raise MuseAccountError("Muse Code's account service couldn't be started.") from None
        self._reader = threading.Thread(target=self._read, name="muse-account-read", daemon=True)
        self._writer = threading.Thread(target=self._write, name="muse-account-write", daemon=True)
        self._reader.start()
        self._writer.start()
        try:
            handshake = self._request("initialize", {
                "clientInfo": {"name": "thechatplace", "version": __version__},
                "capabilities": {"experimentalApi": True, "userInputDialogs": False}})
            schema = handshake.get("schema")
            if (not isinstance(schema, dict) or type(schema.get("version")) is not int
                    or schema["version"] != 1 or not isinstance(handshake.get("serverInfo"), dict)):
                raise MuseAccountError("Muse Code's account protocol isn't compatible with this app.")
            if handshake.get("experimentalApi") is not True:
                raise MuseAccountError("This Muse Code build doesn't support account checks in this app.")
            # MSP does not accept account requests or deliver login events
            # until the client acknowledges the initialize reply (1.4.18).
            self._outgoing.put_nowait(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, method, params=None):
        if method not in self._METHODS:
            raise MuseAccountError("This connection supports only Muse Code account setup.")
        if method == "account/loginStart" and params != {"type": "deviceCode"}:
            raise MuseAccountError("This connection supports only Muse Code browser sign-in.")
        return self._request(method, params)

    def _request(self, method, params):
        # Serialize this small account surface, retaining a reader for unsolicited
        # login completion while no request is outstanding.
        with self._request_lock:
            with self._lock:
                if self._closed or self._error:
                    raise MuseAccountError(self._error or "Muse Code's account connection is closed.")
                self._next_id += 1
                ident = self._next_id
                pending = _Pending()
                self._pending[ident] = pending
            message = {"jsonrpc": "2.0", "id": ident, "method": method}
            if params is not None:
                message["params"] = params
            try:
                data = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
                if len(data) > self._frame_limit:
                    raise MuseAccountError("Muse Code's account request is too large.")
                self._outgoing.put_nowait(data)
                if not pending.ready.wait(self._timeout):
                    raise MuseAccountError("Muse Code's account check timed out. Try again.")
                with self._lock:
                    error = self._error
                if error or pending.result is None:
                    raise MuseAccountError(error or "Muse Code's account connection ended.")
                return pending.result
            except (queue.Full, TypeError, ValueError):
                self.close()
                raise MuseAccountError("Muse Code's account request couldn't be sent.") from None
            except MuseAccountError:
                self.close()
                raise
            finally:
                with self._lock:
                    self._pending.pop(ident, None)

    def next_notification(self, timeout=1.):
        try:
            notification = self._notifications.get(timeout=timeout)
        except queue.Empty:
            notification = None
        with self._lock:
            error = self._error
            closed = self._closed
        if error or closed:
            raise MuseAccountError(error or "Muse Code's account connection is closed.")
        return notification

    def _fail(self, reason):
        with self._lock:
            if not self._error:
                self._error = reason
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
            self._fail("Muse Code's account connection couldn't send a request.")

    def _read(self):
        try:
            while True:
                raw = self._host.stdout.readline(self._frame_limit + 1)
                if not raw:
                    self._fail("Muse Code's account connection ended.")
                    return
                if len(raw) > self._frame_limit or not raw.endswith(b"\n"):
                    raise ValueError("frame")
                if not raw.strip():
                    continue
                message = json.loads(raw.decode("utf-8"))
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("envelope")
                if "method" in message:
                    if "id" in message:
                        raise ValueError("server request")
                    if message["method"] in ("account/changed", "account/loginCompleted"):
                        if not isinstance(message.get("params"), dict):
                            raise ValueError("notification")
                        self._notifications.put_nowait(message)
                    continue
                ident = message.get("id")
                if type(ident) is not int:
                    raise ValueError("id")
                with self._lock:
                    pending = self._pending.get(ident)
                    if pending is None:
                        raise ValueError("unknown response")
                    if "error" in message:
                        raise ValueError("account method unavailable")
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise ValueError("result")
                    pending.result = result
                    pending.ready.set()
        except Exception:
            self._fail("Muse Code couldn't report a compatible account response. Update Muse Code and try again.")

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for pending in self._pending.values():
                pending.ready.set()
        try:
            self._outgoing.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._host.close()
        except Exception:
            pass  # cleanup must not expose process errors or credentials
        for thread in (self._reader, self._writer):
            if thread is not threading.current_thread():
                thread.join(timeout=1.)
