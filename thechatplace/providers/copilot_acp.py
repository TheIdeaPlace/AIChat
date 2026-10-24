"""GitHub Copilot CLI's Agent Client Protocol: JSON-RPC 2.0 over stdio (#352).

``copilot --acp`` reads one JSON object per line on stdin and writes one per
line on stdout. The client sends requests (initialize, session/new,
session/load, session/prompt) and the notification session/cancel; Copilot
sends session/update notifications while it works and asks before acting
with the request session/request_permission, which the client answers.
dev-notes/copilot-cli-probe.md records what Copilot CLI 1.0.103 sent.

No wx and no policy here: this moves messages. What may be sent, and what a
permission request is answered with, is copilot_runtime's business.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Callable, Optional

PROTOCOL_VERSION = 1
PERMISSION_REQUEST = "session/request_permission"
#: A line longer than this is refused rather than buffered: a runaway or
#: hostile process mustn't be able to fill the app's memory.
MAX_LINE_BYTES = 32 * 1024 * 1024
#: Requests that answer at once (initialize, new, load). A prompt waits for
#: its turn to end however long that takes.
REQUEST_SECONDS = 60
#: Reopening a chat replays its whole history first, so it may take longer.
LOAD_SECONDS = 300
#: One message to Copilot, at most: a prompt of 128,000 characters fits.
#: Larger is refused before it's queued (as codex_rpc's MAX_REQUEST).
MAX_MESSAGE_BYTES = 1024 * 1024
#: Requests waiting for an answer, and messages waiting to be written, at
#: most: a Copilot that stops reading can't make either grow without bound.
MAX_PENDING = 32
MAX_OUTGOING = 256
#: What Copilot records as the client of the sessions this app starts
#: (workspace.yaml's client_name), from clientInfo in initialize.
CLIENT_NAME = "thechatplace"


class AcpError(Exception):
    """Something went wrong talking to Copilot. The message is safe to show:
    never Copilot's own error text, which may hold paths or account names."""


class AcpConnection:
    """One running ``copilot --acp``. Notifications and permission requests
    arrive on the reader thread, through the callbacks.

    Writing is done by a thread of its own, from a bounded queue (as
    codex_rpc does): if Copilot's stdin pipe fills, only that thread waits.
    The reader thread, and the window, never block on a write, so a Copilot
    that stops reading can't stop its replies being read or freeze the app.
    """

    def __init__(self, process, on_notification: Callable[[str, dict], None],
                 on_request: Callable[[object, str, dict], None],
                 on_closed: Optional[Callable[[], None]] = None):
        self._process = process
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_closed = on_closed
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._next_id = 0
        self._closed = False
        self._outgoing: "queue.Queue" = queue.Queue(maxsize=MAX_OUTGOING)
        self._writer = threading.Thread(target=self._write_loop, daemon=True,
                                        name="copilot-acp-writer")
        self._reader = threading.Thread(target=self._read, daemon=True,
                                        name="copilot-acp-reader")
        self._writer.start()
        self._reader.start()

    @property
    def closed(self) -> bool:
        return self._closed

    # -- sending --------------------------------------------------------------

    def _write(self, message: dict) -> None:
        """Queue one message for the writer thread; never waits on the pipe."""
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(data) > MAX_MESSAGE_BYTES:
            raise AcpError("That message is too long to send to Copilot.")
        if self._closed:
            raise AcpError("Copilot isn't running.")
        try:
            self._outgoing.put_nowait(data)
        except queue.Full:
            # Copilot has stopped reading: nothing more can reach it safely.
            self.close()
            raise AcpError("Copilot stopped responding.") from None

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
        """Send a request and wait for its result (None waits until it comes
        or Copilot exits). Raises AcpError on an error reply or a timeout."""
        done = threading.Event()
        with self._lock:
            if self._closed:
                raise AcpError("Copilot isn't running.")
            if len(self._pending) >= MAX_PENDING:
                raise AcpError("Copilot is busy; try again in a moment.")
            self._next_id += 1
            request_id = self._next_id
            slot = {"done": done, "message": None}
            self._pending[request_id] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            if not done.wait(timeout):
                # Only this request gives up: other chats' turns share the
                # process. Its id is never reused, so a late answer is ignored.
                raise AcpError("Copilot didn't answer in time.")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        message = slot["message"]
        if message is None:
            raise AcpError("Copilot stopped before answering.")
        if "error" in message or not isinstance(message.get("result"), dict):
            raise AcpError("Copilot couldn't do that.")
        return message["result"]

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, request_id, result: dict) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _refuse(self, request_id) -> None:
        try:
            self._write({"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": -32601, "message": "Not supported"}})
        except AcpError:
            pass

    # -- receiving ------------------------------------------------------------

    def _read(self) -> None:
        stream = self._process.stdout
        try:
            while True:
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    break  # never buffer a runaway line; treat it as the end
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        except (OSError, ValueError):
            pass
        finally:
            # Ending for any reason ends the process too, so a Copilot that
            # can no longer be heard is never left running.
            self.close()

    def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if isinstance(method, str) and "id" in message:
            if not isinstance(message["id"], (int, str)) or isinstance(message["id"], bool):
                return  # unanswerable; Copilot only uses numbers and strings
            if method == PERMISSION_REQUEST:
                try:
                    self._on_request(message["id"], method, params)
                except Exception:  # noqa: BLE001 - an unanswered request would hang the turn
                    self._refuse(message["id"])
            else:
                # Reading files, terminals and the rest weren't offered in
                # initialize; anything else is refused, never guessed at.
                self._refuse(message["id"])
            return
        if isinstance(method, str):
            try:
                self._on_notification(method, params)
            except Exception:  # noqa: BLE001 - one bad update mustn't stop reading
                pass
            return
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return  # ids are only ever our own numbers
        with self._lock:
            slot = self._pending.get(request_id)
        if slot is not None:
            slot["message"] = message
            slot["done"].set()

    def _fail_all(self) -> bool:
        """Mark closed and wake every waiting request; True the first time."""
        with self._lock:
            first = not self._closed
            self._closed = True
            pending = list(self._pending.values())
        for slot in pending:
            slot["done"].set()
        return first

    def close(self) -> None:
        first = self._fail_all()
        try:
            self._outgoing.put_nowait(None)  # let the writer thread end
        except queue.Full:
            pass
        try:
            self._process.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
        if first and self._on_closed is not None:
            try:
                self._on_closed()
            except Exception:  # noqa: BLE001 - telling chats is best effort
                pass


def check_initialize(result: dict) -> None:
    """Refuse anything but the Copilot CLI this was written against: the
    protocol version, and the ability to reopen a saved session."""
    caps = result.get("agentCapabilities") if isinstance(result.get("agentCapabilities"), dict) else {}
    info = result.get("agentInfo") if isinstance(result.get("agentInfo"), dict) else {}
    if (result.get("protocolVersion") != PROTOCOL_VERSION or caps.get("loadSession") is not True
            or info.get("name") != "Copilot"):
        raise AcpError("This version of Copilot CLI isn't supported.")


def connect(process, on_notification, on_request, on_closed=None) -> AcpConnection:
    """Start talking to a just-started ``copilot --acp`` and check it.
    Offers Copilot nothing beyond the conversation: no file or terminal
    access through the client. Names the client, so Copilot records the
    sessions this app starts as ``client_name: thechatplace``."""
    connection = AcpConnection(process, on_notification, on_request, on_closed)
    try:
        # A Copilot that can't even start talking is closed, process and all.
        check_initialize(connection.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": CLIENT_NAME, "title": "The Chat Place"}}))
    except Exception:
        connection.close()
        raise
    return connection
