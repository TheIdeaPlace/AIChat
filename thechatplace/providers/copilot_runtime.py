"""Running GitHub Copilot CLI chats over ACP, without wx (#352).

The window will drive this the way it drives Codex's CodexChatRuntime: send,
queue, stop, answer a permission request, and get a state snapshot back
through ``on_update`` (from a worker thread; the window must hop to the UI
thread). CopilotChatState and CopilotRequest have the fields and methods the
window reads from their Codex equivalents, so the window can show either.

Safety rules, each with a test:

* **Copilot asks before it acts.** Every chat must be in agent mode with
  allow_all off, checked when the chat is opened and whenever Copilot reports
  a settings change; otherwise nothing is sent. A message starting with "/"
  is refused, because Copilot's slash commands include /allow-all.
* **Only one-time answers.** A permission request offers Allow Once and
  Deny, never Copilot's "always allow": a lasting grant would outlive what
  the person saw (the rule Codex follows, #284).
* **Billing.** The environment that could move billing or the account is
  stripped (platform_paths.copilot_child_environment), and after each turn
  the session's own log must say GitHub Copilot answered. Anything else stops
  every chat until The Chat Place restarts.
* **A prompt is never sent twice.** Stop cancels; it never resends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable, Dict, List, Optional

from .. import platform_paths
from ..sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, WORKING
from . import copilot_acp
from .copilot import EVENTS_FILE, WORKSPACE_FILE, parse_workspace

AGENT_MODE = "https://agentclientprotocol.com/protocol/session-modes#agent"
#: What Copilot records as the client of a session started over ACP; one
#: started in a terminal says "github/cli".
ACP_CLIENT = "github/acp"
ALLOW_ONCE, REJECT = "allow_once", "reject_once"
COMMAND, FILES, OTHER = "command", "files", "other"
#: ACP tool kinds, as Copilot labels a permission request's tool call.
_FILE_KINDS = frozenset({"edit", "delete", "move"})
#: As Codex's queue (codex_runtime): enough to keep working, never unbounded.
MAX_QUEUED = 20
MAX_QUEUED_CHARS = 128_000
#: The session log is written a moment after the turn's answer arrives.
BILLING_WAIT_SECONDS = 15.0
BILLING_TAIL_BYTES = 256 * 1024


class CopilotChatError(Exception):
    """A reason a chat can't do something now, in words to show."""


@dataclass(frozen=True)
class CopilotChatState:
    """The fields of codex_runtime.CodexChatState the window reads."""
    native_id: str
    state: str
    turn_id: str = ""
    outcome: str = ""
    replies: tuple = field(default=(), repr=False)
    requests: tuple = field(default=(), repr=False)
    queued: tuple = field(default=(), repr=False)
    unsent: tuple = field(default=(), repr=False)
    problem: str = ""
    file_changes: tuple = field(default=(), repr=False)
    started_at: float = 0
    activity: str = field(default="", repr=False)
    notice: str = ""
    token_usage: object = None
    context_epoch: int = 0
    tasks: tuple = field(default=(), repr=False)
    plan_received: bool = False
    permission_notices: tuple = field(default=(), repr=False)
    changed_paths: tuple = field(default=(), repr=False)
    #: Context used and its size, from Copilot's usage_update (0 if unknown).
    context_used: int = 0
    context_size: int = 0


def _text(value, limit: int = 65536) -> str:
    return value[:limit] if isinstance(value, str) else ""


@dataclass(frozen=True)
class CopilotRequest:
    """A permission request, with the methods the window's approval dialog
    calls on a codex_requests.CodexRequest."""
    request_id: object
    method: str
    item_id: str
    title: str = ""
    command: str = field(default="", repr=False)
    file_name: str = field(default="", repr=False)
    diff: str = field(default="", repr=False)
    #: ACP kind -> Copilot's optionId, for the one-time choices it offered.
    choices: tuple = ()
    turn_id: str = ""
    questions: tuple = ()

    @classmethod
    def decode(cls, request_id, params: dict, turn_id: str = "") -> "CopilotRequest":
        call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        raw = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
        kind = _text(call.get("kind"))
        choices = []
        for option in params.get("options") or ():
            if isinstance(option, dict) and option.get("kind") in (ALLOW_ONCE, REJECT):
                if isinstance(option.get("optionId"), str) and option["optionId"]:
                    choices.append((option["kind"], option["optionId"]))
        if not any(k == REJECT for k, _ in choices):
            raise CopilotChatError("Copilot asked in a way The Chat Place can't answer safely.")
        command = _text(raw.get("command"))
        if not command and isinstance(raw.get("commands"), list):
            command = "\n".join(_text(c) for c in raw["commands"] if isinstance(c, str))
        return cls(request_id=request_id,
                   method=COMMAND if kind == "execute" else FILES if kind in _FILE_KINDS else OTHER,
                   item_id=_text(call.get("toolCallId"), 200), title=_text(call.get("title"), 500),
                   command=command, file_name=_text(raw.get("fileName"), 4096),
                   diff=_text(raw.get("diff"), 1_000_000), choices=tuple(choices), turn_id=turn_id)

    def offered(self):
        """Allow Once (if Copilot offered it) then Deny; never "always"."""
        kinds = [k for k, _ in self.choices]
        return tuple(k for k in (ALLOW_ONCE, REJECT) if k in kinds)

    def reviewable(self, file_detail: str = "") -> bool:
        """Whether there's enough to review for an allowing answer."""
        if self.method == COMMAND:
            return bool(self.command.strip())
        if self.method == FILES:
            return bool(self.diff.strip() or file_detail)
        return bool(self.title.strip())

    def choice_text(self, decision: str) -> str:
        labels = {ALLOW_ONCE: "&Allow Once", REJECT: "&Deny"}
        if decision not in labels:
            raise ValueError("Unsupported Copilot approval choice")
        return labels[decision]

    def detail(self, file_detail: str = "") -> str:
        """The whole request in plain words, as an approval dialog shows it."""
        parts = [self.title] if self.title else []
        if self.method == COMMAND and self.command:
            parts.append("Command:\n" + self.command)
        elif self.method == FILES:
            if self.file_name:
                parts.append("File:\n" + self.file_name)
            parts.append("Changes:\n" + (self.diff or file_detail
                                        or "The changes aren't available to review. Deny it."))
        return "\n\n".join(parts) or "Copilot wants to use a tool. Deny it if you're not sure."

    def summary(self) -> str:
        """A short spoken line about what's wanted."""
        if self.method == COMMAND:
            return "Command: " + (self.command.splitlines()[0] if self.command else self.title)
        if self.method == FILES:
            return "File change: " + (self.file_name or self.title)
        return self.title or "Copilot wants to use a tool."

    def approval_answer(self, decision: str) -> dict:
        option = dict(self.choices).get(decision)
        if decision not in self.offered() or option is None:
            raise ValueError("Unsupported Copilot approval choice")
        return {"outcome": {"outcome": "selected", "optionId": option}}


def _settings(options) -> dict:
    """{"mode": …, "allow_all": …} from ACP configOptions (missing: "")."""
    values = {"mode": "", "allow_all": ""}
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and option.get("id") in values:
                values[option["id"]] = _text(option.get("currentValue"), 500)
    return values


def usage_kinds(events_path: Path, offset: int = 0) -> tuple:
    """The ``provider.kind`` of each session.usage_record written to the
    session's log after byte ``offset``, oldest first: who answered."""
    try:
        with open(events_path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            # Only the end: the record is written last, and a turn's tool
            # output can be large.
            handle.seek(max(offset, size - BILLING_TAIL_BYTES))
            data = handle.read()
    except OSError:
        return ()
    kinds = []
    for raw in data.split(b"\n"):
        if b"session.usage_record" not in raw:
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "session.usage_record":
            continue
        data_ = event.get("data")
        usage = data_.get("usage") if isinstance(data_, dict) else None
        provider = usage.get("provider") if isinstance(usage, dict) else None
        if isinstance(provider, dict):
            kinds.append(_text(provider.get("kind"), 100))
    return tuple(kinds)


def _client_of(folder: Path) -> str:
    """The client_name Copilot recorded for a session, or "" if unreadable."""
    try:
        return parse_workspace((folder / WORKSPACE_FILE).read_text(encoding="utf-8")).get(
            "client_name", "")
    except (OSError, UnicodeDecodeError):
        return ""


def _size(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class _Chat:
    def __init__(self, native_id: str, cwd: str):
        self.native_id, self.cwd = native_id, cwd
        #: The connection this chat was opened (and safety-checked) on. A new
        #: Copilot process hasn't loaded it, so it must be opened again.
        self.connection = None
        self.mode = ""
        self.allow_all = ""
        self.busy = False
        self.turns = 0
        self.replies: List[list] = []
        self.reply_open = False
        self.requests: Dict[object, CopilotRequest] = {}
        self.queue: List[str] = []
        self.unsent: tuple = ()
        self.problem = ""
        self.notice = ""
        self.outcome = ""
        self.activity = ""
        self.started_at = 0.0
        self.loading = False
        self.stopping = False
        self.context = (0, 0)
        self.log_offset = 0
        #: Waiting messages taken back by a stop the person didn't ask for.
        self.held: tuple = ()

    @property
    def safe(self) -> bool:
        return self.mode == AGENT_MODE and self.allow_all == "off"

    def apply(self, settings: dict) -> None:
        self.mode, self.allow_all = settings["mode"], settings["allow_all"]


class CopilotChatRuntime:
    """Every Copilot chat this app runs, over one ``copilot --acp``.

    Locking: ``_lock`` guards chat state and is never held while waiting on
    Copilot, so the window's reads (ready, snapshot) never block on it.
    ``_connect_lock`` serialises starting Copilot.
    """

    def __init__(self, on_update: Callable[[CopilotChatState], None], *,
                 start: Optional[Callable] = None, sessions_dir: Optional[Path] = None,
                 billing_wait: float = BILLING_WAIT_SECONDS):
        self._on_update = on_update
        self._start = start or platform_paths.start_copilot_acp
        self._sessions_dir = sessions_dir
        self._billing_wait = billing_wait
        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._connection: Optional[copilot_acp.AcpConnection] = None
        self._chats: Dict[str, _Chat] = {}
        #: Latched for every chat when billing can't be confirmed.
        self._halted = ""
        self._closed = False

    # -- the connection ------------------------------------------------------

    def _connect(self) -> copilot_acp.AcpConnection:
        with self._connect_lock:
            with self._lock:
                if self._closed:
                    raise CopilotChatError("The Chat Place is closing.")
                current = self._connection
            if current is not None and not current.closed:
                return current
            if current is not None:
                current.close()  # its process too: never leave one running
            holder = []
            connection = copilot_acp.connect(
                self._start(),
                lambda method, params: self._on_notification(holder[0], method, params),
                lambda rid, method, params: self._on_request(holder[0], rid, method, params))
            holder.append(connection)
            with self._lock:
                if self._closed:
                    connection.close()
                    raise CopilotChatError("The Chat Place is closing.")
                self._connection = connection
            return connection

    def close(self) -> None:
        with self._lock:
            self._closed = True
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _events(self, native_id: str) -> Path:
        return (self._sessions_dir or platform_paths.copilot_sessions_dir()) / native_id / EVENTS_FILE

    # -- opening chats -------------------------------------------------------

    def create(self, cwd: str) -> str:
        """A new Copilot chat in ``cwd``; its native id. No prompt is sent."""
        connection = self._connect()
        result = connection.request("session/new", {"cwd": cwd, "mcpServers": []})
        native_id = result.get("sessionId")
        if not isinstance(native_id, str) or not platform_paths.is_safe_id(native_id):
            raise CopilotChatError("Copilot didn't start a chat.")
        chat = _Chat(native_id, cwd)
        chat.apply(_settings(result.get("configOptions")))
        chat.connection = connection
        with self._lock:
            self._chats[native_id] = chat
        self._notify(chat)
        return native_id

    def ready(self, native_id: str) -> bool:
        """Open on the running Copilot and safe to send to; no I/O."""
        with self._lock:
            chat = self._chats.get(native_id)
            return (chat is not None and chat.safe and not chat.loading and not chat.problem
                    and not self._halted and chat.connection is not None
                    and chat.connection is self._connection and not chat.connection.closed)

    def prepare(self, native_id: str, cwd: str) -> None:
        """Open a saved chat on the running Copilot so it can be sent to.
        Copilot replays its history while it loads; that is history, not new
        replies.

        Only a chat The Chat Place started: Copilot records which client
        made a session, and one made in a terminal or another app stays
        read-only here, as other apps' chats do (CLAUDE.md). The window
        also checks its own record of the chats it owns before calling this.
        """
        if not platform_paths.is_safe_id(native_id):
            raise CopilotChatError("That isn't a Copilot chat.")
        if self.ready(native_id):
            return
        if _client_of(self._events(native_id).parent) != ACP_CLIENT:
            raise CopilotChatError("This Copilot chat was started outside The Chat Place, "
                                   "so it can only be read here.")
        connection = self._connect()
        with self._lock:
            chat = self._chats.get(native_id) or _Chat(native_id, cwd)
            if chat.busy:
                raise CopilotChatError("This Copilot chat is still finishing a turn.")
            if chat.loading:
                raise CopilotChatError("This Copilot chat is still opening.")
            chat.loading, chat.connection = True, connection
            self._chats[native_id] = chat
        try:
            result = connection.request("session/load", {
                "sessionId": native_id, "cwd": cwd, "mcpServers": []},
                timeout=copilot_acp.LOAD_SECONDS)
        except Exception:
            with self._lock:
                chat.loading, chat.connection = False, None
            raise
        kinds = usage_kinds(self._events(native_id), chat.log_offset)
        with self._lock:
            chat.loading = False
            chat.apply(_settings(result.get("configOptions")))
            if chat.problem and not self._halted and kinds and all(k == "copilot" for k in kinds):
                chat.problem = ""  # the late record has arrived, and it's Copilot's
        self._notify(chat)

    # -- sending -------------------------------------------------------------

    def _refusal(self, chat: Optional[_Chat]) -> str:
        """Why this chat can't send now, or "" (call with _lock held)."""
        if self._halted:
            return self._halted
        if chat is None or chat.loading or chat.connection is None:
            return "This Copilot chat isn't open here yet."
        if chat.connection is not self._connection or chat.connection.closed:
            return "Copilot restarted; open this chat again to send to it."
        if chat.problem:
            return chat.problem
        if not chat.safe:
            return ("Copilot isn't set to ask before it acts in this chat "
                    "(agent mode, with allow-all off), so nothing was sent.")
        return ""

    def send(self, native_id: str, text: str) -> bool:
        """Send now, or queue behind the running turn; True if queued."""
        if not isinstance(text, str) or not text.strip():
            raise CopilotChatError("There's nothing to send.")
        if text.lstrip().startswith("/"):
            raise CopilotChatError("Copilot commands starting with / can't be sent from "
                                   "The Chat Place: some of them turn off its permission prompts.")
        with self._lock:
            chat = self._chats.get(native_id)
            refusal = self._refusal(chat)
            if refusal:
                raise CopilotChatError(refusal)
            if chat.busy:
                if (len(chat.queue) >= MAX_QUEUED
                        or sum(map(len, chat.queue)) + len(text) > MAX_QUEUED_CHARS):
                    raise CopilotChatError("Too many messages are waiting; wait for this turn.")
                chat.queue.append(text)
                queued = True
            else:
                self._begin(chat, text)
                queued = False
        self._notify(chat)
        return queued

    def _begin(self, chat: _Chat, text: str) -> None:
        """Start a turn (with _lock held, after _refusal said yes)."""
        chat.busy, chat.outcome, chat.notice = True, "inProgress", ""
        chat.turns += 1
        chat.started_at = time.monotonic()
        chat.reply_open = False
        chat.unsent = ()
        # Billing is checked on what this turn adds to the log, never on an
        # earlier turn's record.
        chat.log_offset = _size(self._events(chat.native_id))
        threading.Thread(target=self._run_turn, args=(chat, chat.connection, text),
                         daemon=True, name="copilot-turn").start()

    def _run_turn(self, chat: _Chat, connection, text: str) -> None:
        outcome = "failed"
        try:
            result = connection.request("session/prompt", {
                "sessionId": chat.native_id, "prompt": [{"type": "text", "text": text}]},
                timeout=None)
            reason = result.get("stopReason")
            outcome = {"end_turn": "completed", "cancelled": "interrupted"}.get(reason, "failed")
            if outcome == "failed":
                with self._lock:
                    chat.notice = ("Copilot ended this turn early (it refused, or reached a "
                                   "limit). Messages waiting after it weren't sent.")
        except Exception:  # noqa: BLE001 - the reason shown is ours, not Copilot's
            with self._lock:
                chat.notice = "Copilot stopped before finishing. Your message wasn't sent again."
        try:
            self._check_billing(chat, outcome)
        except Exception:  # noqa: BLE001 - an unreadable log is a failed check
            self._halt()
        with self._lock:
            chat.busy, chat.outcome, chat.activity = False, outcome, ""
            chat.requests.clear()
            held, chat.held = chat.held, ()
            chat.stopping = False
            refusal = self._refusal(chat)
            if outcome != "completed" or refusal:
                # Never carry on with queued messages after a stop, a failure,
                # or a change that means Copilot won't ask first: they go back
                # to the person, unsent.
                chat.unsent, chat.queue = held + tuple(chat.queue), []
                if refusal and chat.unsent and not chat.notice:
                    chat.notice = refusal
            elif chat.queue:
                self._begin(chat, chat.queue.pop(0))
        self._notify(chat)

    def _check_billing(self, chat: _Chat, outcome: str) -> None:
        """What this turn wrote must say GitHub Copilot answered. A completed
        turn must have such a record; any turn with another provider's
        record stops everything."""
        events = self._events(chat.native_id)
        deadline = time.monotonic() + self._billing_wait
        kinds = usage_kinds(events, chat.log_offset)
        while outcome == "completed" and not kinds and time.monotonic() < deadline:
            time.sleep(0.1)
            kinds = usage_kinds(events, chat.log_offset)
        if any(kind != "copilot" for kind in kinds):
            self._halt()
        elif outcome == "completed" and not kinds:
            # Not proof of anything wrong (the log may be late), but nothing
            # more is sent in this chat until it's confirmed by reopening.
            with self._lock:
                chat.problem = ("The Chat Place couldn't confirm this answer was billed to "
                                "GitHub Copilot, so this chat won't send until it's opened again.")

    def _halt(self) -> None:
        """Stop every chat: no new turns, running ones cancelled, waiting
        permission requests refused."""
        with self._lock:
            self._halted = ("The Chat Place couldn't confirm an answer came from GitHub "
                            "Copilot, so it has stopped sending to Copilot until it restarts.")
            chats = list(self._chats.values())
            for chat in chats:
                chat.problem = self._halted
        for chat in chats:
            if chat.busy:
                waiting = self.stop(chat.native_id)
                with self._lock:
                    chat.held += waiting  # given back unsent when the turn ends
        for chat in chats:
            self._notify(chat)  # every chat shows it, running or not

    def stop(self, native_id: str) -> tuple:
        """Cancel the running turn; the messages still waiting come back.
        Waiting permission requests are answered "cancelled" first, as ACP
        asks of a client that cancels."""
        with self._lock:
            chat = self._chats.get(native_id)
            if chat is None or not chat.busy:
                return ()
            chat.stopping = True
            waiting, chat.queue = tuple(chat.queue), []
            requests = list(chat.requests)
            chat.requests.clear()
            connection = chat.connection
        if connection is not None and not connection.closed:
            for request_id in requests:
                try:
                    connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
                except copilot_acp.AcpError:
                    pass
            try:
                connection.notify("session/cancel", {"sessionId": native_id})
            except copilot_acp.AcpError:
                pass
        self._notify(chat)
        return waiting

    def answer(self, native_id: str, request_id, decision: str) -> None:
        """Answer a waiting permission request once. A choice that wasn't
        offered raises ValueError and leaves the request waiting."""
        with self._lock:
            chat = self._chats.get(native_id)
            request = chat.requests.get(request_id) if chat else None
            if request is None:
                raise CopilotChatError("That request has already been answered.")
            if self._halted and decision != REJECT:
                raise CopilotChatError(self._halted)
            reply = request.approval_answer(decision)  # raises before anything is removed
            del chat.requests[request_id]
            connection = chat.connection
        if connection is None or connection.closed:
            raise CopilotChatError("Copilot isn't running.")
        connection.respond(request_id, reply)
        self._notify(chat)

    # -- what Copilot sends ----------------------------------------------------

    def _chat_on(self, connection, native_id) -> Optional[_Chat]:
        """The chat Copilot means, if it's open on this same connection."""
        with self._lock:
            chat = self._chats.get(native_id)
            return chat if chat is not None and chat.connection is connection else None

    def _on_request(self, connection, request_id, method: str, params: dict) -> None:
        chat = self._chat_on(connection, params.get("sessionId"))
        with self._lock:
            watched = (chat is not None and not chat.loading and chat.busy
                       and not chat.stopping and not self._halted)
        if not watched:
            # Not a running turn of ours: never allow something nobody is watching.
            connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
            return
        try:
            request = CopilotRequest.decode(request_id, params, str(chat.turns))
        except CopilotChatError as exc:
            connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
            with self._lock:
                chat.notice = str(exc)
        else:
            with self._lock:
                chat.requests[request_id] = request
        self._notify(chat)

    def _on_notification(self, connection, method: str, params: dict) -> None:
        if method != "session/update":
            return
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        chat = self._chat_on(connection, params.get("sessionId"))
        if chat is None:
            return
        with self._lock:
            kind = update.get("sessionUpdate")
            # Settings changes count even while history is replayed.
            if kind in ("config_option_update", "current_mode_update"):
                if kind == "config_option_update":
                    chat.apply(_settings(update.get("configOptions")))
                else:
                    chat.mode = _text(update.get("currentModeId"), 500)
                unsafe_turn = chat.busy and not chat.safe and not chat.stopping
                if unsafe_turn:
                    chat.notice = ("Copilot stopped asking before it acts, so The Chat Place "
                                   "stopped this turn.")
            elif chat.loading:
                return  # history replayed by session/load is history
            elif kind == "agent_message_chunk":
                content = update.get("content") if isinstance(update.get("content"), dict) else {}
                piece = _text(content.get("text"))
                if not piece:
                    return
                if not chat.reply_open:
                    chat.replies.append([f"{chat.turns}:{len(chat.replies)}", ""])
                    chat.reply_open = True
                chat.replies[-1][1] += piece
            elif kind in ("tool_call", "tool_call_update"):
                chat.reply_open = False  # text after a tool is a new reply
                if _text(update.get("title"), 500):
                    chat.activity = update["title"]
            elif kind == "usage_update":
                used, size = update.get("used"), update.get("size")
                if isinstance(used, int) and isinstance(size, int):
                    chat.context = (used, size)
            else:
                return
        if kind in ("config_option_update", "current_mode_update") and unsafe_turn:
            waiting = self.stop(chat.native_id)
            with self._lock:
                chat.held += waiting
        self._notify(chat)

    # -- state -----------------------------------------------------------------

    def snapshot(self, native_id: str) -> Optional[CopilotChatState]:
        with self._lock:
            chat = self._chats.get(native_id)
            return None if chat is None else self._state(chat)

    def _state(self, chat: _Chat) -> CopilotChatState:
        if chat.problem:
            state = STATUS_UNAVAILABLE
        elif chat.requests:
            state = NEEDS_YOU
        elif chat.busy:
            state = WORKING
        else:
            state = IDLE
        return CopilotChatState(
            native_id=chat.native_id, state=state, turn_id=str(chat.turns), outcome=chat.outcome,
            replies=tuple((rid, text) for rid, text in chat.replies),
            requests=tuple(chat.requests.values()), queued=tuple(chat.queue), unsent=chat.unsent,
            problem=chat.problem, started_at=chat.started_at, activity=chat.activity,
            notice=chat.notice, context_used=chat.context[0], context_size=chat.context[1])

    def _notify(self, chat: _Chat) -> None:
        with self._lock:
            state = self._state(chat)
        try:
            self._on_update(state)
        except Exception:  # noqa: BLE001 - the window's problem mustn't stop the turn
            pass
