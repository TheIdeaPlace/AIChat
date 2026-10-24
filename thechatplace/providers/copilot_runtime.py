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
import difflib
import json
import os
from pathlib import Path
import threading
import time
import unicodedata
from typing import Callable, Dict, List, Optional

from .. import platform_paths
from ..sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, WORKING
from . import copilot_acp
from .copilot import EVENTS_FILE, WORKSPACE_FILE, parse_workspace

AGENT_MODE = "https://agentclientprotocol.com/protocol/session-modes#agent"
#: What Copilot records as the client of the sessions this app starts: the
#: name it gives in initialize's clientInfo (another ACP client's chats say
#: "github/acp" or their own name, and a terminal chat says "github/cli").
OWN_CLIENT = copilot_acp.CLIENT_NAME
ALLOW_ONCE, REJECT = "allow_once", "reject_once"
COMMAND, FILES, OTHER = "command", "files", "other"
#: ACP tool kinds, as Copilot labels a permission request's tool call.
_FILE_KINDS = frozenset({"edit", "delete", "move"})
#: As Codex's queue (codex_runtime): enough to keep working, never unbounded.
MAX_QUEUED = 20
MAX_QUEUED_CHARS = 128_000
#: One message, at most (it reaches Copilot as one JSON line under
#: copilot_acp.MAX_MESSAGE_BYTES). Longer is refused before anything is sent.
MAX_MESSAGE_CHARS = 128_000
#: A diff longer than this can't be shown in full, so it can't be allowed.
MAX_DIFF_CHARS = 1_000_000
#: Permission requests waiting in one chat, at most; more are cancelled.
MAX_REQUESTS = 8
#: How long Copilot gets to honour Stop before its process is closed.
CANCEL_GRACE_SECONDS = 15.0
#: Streamed text is shown at most this often, so a long answer in many tiny
#: chunks can't flood the window or hold up the reader thread.
CHUNK_NOTIFY_SECONDS = 0.05
#: The session log is written a moment after the turn's answer arrives.
BILLING_WAIT_SECONDS = 15.0


def _content_diff(call: dict) -> str:
    """A diff from ACP's standard place, ``toolCall.content`` items of type
    "diff" (path, oldText, newText), as only the changed lines with one line
    around each. Copilot 1.0.103 sends ``rawInput.diff`` instead; this keeps
    edits reviewable if it moves. Never cut short: the caller checks length."""
    parts = []
    for item in call.get("content") or ():
        if isinstance(item, dict) and item.get("type") == "diff":
            path = _text(item.get("path"), 4096)
            old = item.get("oldText") if isinstance(item.get("oldText"), str) else ""
            new = item.get("newText") if isinstance(item.get("newText"), str) else ""
            parts.append("".join(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=path or "before", tofile=path or "after", n=1)))
    return "".join(parts)


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
    #: The change was too long to show in full: it can only be denied.
    too_long: bool = False
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
        raw_diff = raw.get("diff") if isinstance(raw.get("diff"), str) else ""
        diff = raw_diff or _content_diff(call)
        command = _text(raw.get("command"))
        if not command and isinstance(raw.get("commands"), list):
            command = "\n".join(_text(c) for c in raw["commands"] if isinstance(c, str))
        return cls(request_id=request_id,
                   method=COMMAND if kind == "execute" else FILES if kind in _FILE_KINDS else OTHER,
                   item_id=_text(call.get("toolCallId"), 200), title=_text(call.get("title"), 500),
                   command=command, file_name=_text(raw.get("fileName"), 4096),
                   diff=diff[:MAX_DIFF_CHARS], too_long=len(diff) > MAX_DIFF_CHARS,
                   choices=tuple(choices), turn_id=turn_id)

    def offered(self):
        """Allow Once (if Copilot offered it) then Deny; never "always"."""
        kinds = [k for k, _ in self.choices]
        if self.too_long:
            return (REJECT,) if REJECT in kinds else ()  # can't be seen, can't be allowed
        return tuple(k for k in (ALLOW_ONCE, REJECT) if k in kinds)

    def reviewable(self, file_detail: str = "") -> bool:
        """Whether there's enough to review for an allowing answer."""
        if self.method == COMMAND:
            return bool(self.command.strip())
        if self.method == FILES:
            return not self.too_long and bool(self.diff.strip() or file_detail)
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
            if self.too_long:
                parts.append("This change is too long to show in full, so it can only be "
                             "denied. Ask Copilot to make it in smaller steps.")
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


def _visible_start(text: str) -> str:
    """The text from its first visible character: spaces, byte-order marks,
    zero-width and other format characters dropped, as Copilot's own
    trimming may drop them, so none of them can hide a leading "/"."""
    index = 0
    while index < len(text) and (text[index].isspace() or text[index] in _BLANKS
                                 or unicodedata.category(text[index]) in _HIDDEN):
        index += 1
    return text[index:]


#: Character kinds with nothing to see: controls, format characters,
#: combining marks and separators.
_HIDDEN = frozenset({"Cc", "Cf", "Mn", "Me", "Zs", "Zl", "Zp"})
#: Letters that draw nothing (Hangul fillers, the blank Braille pattern).
_BLANKS = frozenset("\u115f\u1160\u3164\uffa0\u2800")


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
        handle = open(events_path, "rb")
    except OSError:
        return ()
    with handle:
        # Everything this turn wrote, line by line: one turn can call the
        # model several times, and an early call's record counts too.
        try:
            handle.seek(offset)
            lines = [raw for raw in handle if b"session.usage_record" in raw]
        except OSError:
            return ()  # not readable this moment: no record yet, checked again
    kinds = []
    for raw in lines:
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


def _check_folder(cwd: str) -> None:
    """Copilot works in one project folder: never a whole drive, a share's
    root, / or a device path (platform_paths.is_specific_folder, as #334
    refuses them for Codex)."""
    if (not isinstance(cwd, str) or not platform_paths.is_specific_folder(cwd)
            or not os.path.isdir(cwd)):
        raise CopilotChatError("Choose a project folder for Copilot to work in, not a whole "
                               "drive or the top of the computer.")


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
        self.last_chunk_notice = 0.0
        self.cancel_timer = None

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
                 billing_wait: float = BILLING_WAIT_SECONDS,
                 cancel_grace: float = CANCEL_GRACE_SECONDS):
        self._on_update = on_update
        self._start = start or platform_paths.start_copilot_acp
        self._sessions_dir = sessions_dir
        self._billing_wait = billing_wait
        self._cancel_grace = cancel_grace
        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._connection: Optional[copilot_acp.AcpConnection] = None
        self._chats: Dict[str, _Chat] = {}
        #: Latched for every chat when billing can't be confirmed.
        self._halted = ""
        #: Chats this runtime may reopen: ones it created, and ones the
        #: window says The Chat Place owns (adopt), and only if Copilot also
        #: recorded this app as their client.
        self._owned: set = set()
        self._closed = False

    # -- the connection ------------------------------------------------------

    def _connect(self) -> copilot_acp.AcpConnection:
        try:
            return self._connect_once()
        except copilot_acp.AcpError as exc:
            raise CopilotChatError(str(exc)) from None

    def _connect_once(self) -> copilot_acp.AcpConnection:
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
                lambda rid, method, params: self._on_request(holder[0], rid, method, params),
                lambda: self._on_closed(holder[0]) if holder else None)
            holder.append(connection)
            with self._lock:
                if self._closed:
                    connection.close()
                    raise CopilotChatError("The Chat Place is closing.")
                self._connection = connection
            return connection

    def _on_closed(self, connection) -> None:
        """Copilot exited: every chat open on it says so (not ready any more).
        Not when The Chat Place itself is closing it."""
        with self._lock:
            if self._closed:
                return
            chats = [c for c in self._chats.values() if c.connection is connection]
            for chat in chats:
                if not chat.busy and not chat.notice:
                    chat.notice = "Copilot stopped; open this chat again to send to it."
        for chat in chats:
            self._notify(chat)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            connection, self._connection = self._connection, None
            for chat in self._chats.values():
                if chat.cancel_timer is not None:
                    chat.cancel_timer.cancel()
        if connection is not None:
            connection.close()

    def _events(self, native_id: str) -> Path:
        return (self._sessions_dir or platform_paths.copilot_sessions_dir()) / native_id / EVENTS_FILE

    # -- opening chats -------------------------------------------------------

    def create(self, cwd: str) -> str:
        """A new Copilot chat in ``cwd``; its native id. No prompt is sent.
        Starts Copilot if needed and waits up to a minute for it, so call it
        off the UI thread."""
        _check_folder(cwd)
        connection = self._connect()
        try:
            result = connection.request("session/new", {"cwd": cwd, "mcpServers": []})
        except copilot_acp.AcpError as exc:
            raise CopilotChatError(str(exc)) from None
        native_id = result.get("sessionId")
        if not isinstance(native_id, str) or not platform_paths.is_safe_id(native_id):
            raise CopilotChatError("Copilot didn't start a chat.")
        chat = _Chat(native_id, cwd)
        chat.apply(_settings(result.get("configOptions")))
        chat.connection = connection
        with self._lock:
            self._chats[native_id] = chat
            self._owned.add(native_id)
        self._notify(chat)
        return native_id

    def adopt(self, native_id: str) -> None:
        """Let prepare() reopen a chat The Chat Place started in an earlier
        run. The caller must have checked its own record of owned chats."""
        if platform_paths.is_safe_id(native_id):
            with self._lock:
                self._owned.add(native_id)

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

        Waits for Copilot to start and replay the chat (up to five minutes),
        so call it off the UI thread.
        """
        if not platform_paths.is_safe_id(native_id):
            raise CopilotChatError("That isn't a Copilot chat.")
        _check_folder(cwd)
        if self.ready(native_id):
            return
        with self._lock:
            owned = native_id in self._owned
        if not owned or _client_of(self._events(native_id).parent) != OWN_CLIENT:
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
        except Exception as exc:
            with self._lock:
                chat.loading, chat.connection = False, None
            if isinstance(exc, copilot_acp.AcpError):
                raise CopilotChatError(str(exc)) from None
            raise
        kinds = usage_kinds(self._events(native_id), chat.log_offset)
        with self._lock:
            chat.loading = False
            chat.notice = ""  # opened again: "open it again" no longer applies
            chat.apply(_settings(result.get("configOptions")))
            if chat.problem and not self._halted and kinds and all(k == "copilot" for k in kinds):
                chat.problem = ""  # the late record has arrived, and it's Copilot's
            late_elsewhere = any(k != "copilot" for k in kinds)
        if late_elsewhere:
            self._halt()
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
        if len(text) > MAX_MESSAGE_CHARS:
            raise CopilotChatError("That message is too long to send to Copilot; "
                                   "shorten it or put the long part in a file.")
        if _visible_start(text).startswith("/"):
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
                # A more particular reason (Stop ignored, say) is kept.
                chat.notice = chat.notice or ("Copilot stopped before finishing. Your message "
                                              "wasn't sent again.")
        try:
            self._check_billing(chat, outcome)
        except Exception:  # noqa: BLE001 - an unreadable log is a failed check
            self._halt()
        with self._lock:
            chat.busy, chat.outcome, chat.activity = False, outcome, ""
            if chat.cancel_timer is not None:
                chat.cancel_timer.cancel()  # it stopped in time
                chat.cancel_timer = None
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
            else:
                chat.unsent = held  # anything a stop held back, never dropped
                if chat.queue:
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
                self.stop(chat.native_id, hold=True)
        for chat in chats:
            self._notify(chat)  # every chat shows it, running or not

    def stop(self, native_id: str, *, hold: bool = False) -> tuple:
        """Cancel the running turn; the messages still waiting come back.
        Waiting permission requests are answered "cancelled" first, as ACP
        asks of a client that cancels. With ``hold`` (a stop the person
        didn't ask for) the messages are kept and given back as unsent when
        the turn ends, rather than returned."""
        with self._lock:
            chat = self._chats.get(native_id)
            if chat is None or not chat.busy:
                return ()
            chat.stopping = True
            turn = chat.turns  # the turn being stopped, read under the lock
            waiting, chat.queue = tuple(chat.queue), []
            if hold:
                chat.held += waiting
                waiting = ()
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
            timer = threading.Timer(self._cancel_grace, self._cancel_overdue,
                                    args=(chat, connection, turn))
            timer.daemon = True  # never keeps the app open after it quits
            with self._lock:
                if chat.cancel_timer is not None:
                    chat.cancel_timer.cancel()
                chat.cancel_timer = timer
            timer.start()
        self._notify(chat)
        return waiting

    def _cancel_overdue(self, chat: _Chat, connection, turn: int) -> None:
        """Copilot didn't stop when asked. Ending the turn here alone would
        leave it free to keep acting, so its process is closed: every turn on
        it ends, and chats are opened again to carry on."""
        with self._lock:
            overdue = chat.busy and chat.turns == turn and chat.connection is connection
            if overdue:
                chat.notice = ("Copilot didn't stop when asked, so The Chat Place closed it. "
                               "Open the chat again to carry on.")
                for other in self._chats.values():
                    if other is not chat and other.connection is connection and other.busy:
                        other.notice = ("Copilot was closed because another chat didn't stop "
                                        "when asked. Open this chat again to carry on.")
        if overdue:
            connection.close()

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
        try:
            connection.respond(request_id, reply)
        except copilot_acp.AcpError as exc:
            raise CopilotChatError(str(exc)) from None
        self._notify(chat)

    # -- what Copilot sends ----------------------------------------------------

    def _chat_on(self, connection, native_id) -> Optional[_Chat]:
        """The chat Copilot means, if it's open on this same connection."""
        with self._lock:
            chat = self._chats.get(native_id)
            return chat if chat is not None and chat.connection is connection else None

    def _on_request(self, connection, request_id, method: str, params: dict) -> None:
        chat = self._chat_on(connection, params.get("sessionId"))
        try:
            request = CopilotRequest.decode(request_id, params, str(chat.turns) if chat else "")
            problem = ""
        except CopilotChatError as exc:
            request, problem = None, str(exc)
        with self._lock:
            # Checked and stored in one go, so a Stop can't slip in between
            # and leave this request stored but never answered.
            watched = (chat is not None and not chat.loading and chat.busy
                       and not chat.stopping and not self._halted)
            if watched and request is not None and len(chat.requests) >= MAX_REQUESTS:
                request, problem = None, "Copilot asked too many things at once; some were declined."
            if watched and request is not None:
                chat.requests[request_id] = request
            elif watched:
                chat.notice = problem
        if not watched or request is None:
            # Not a running turn of ours, or not answerable safely: never
            # allow something nobody is watching.
            connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
            if not watched:
                return
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
                    chat.replies.append([f"{chat.turns}:{len(chat.replies)}", []])
                    chat.reply_open = True
                chat.replies[-1][1].append(piece)
                now = time.monotonic()
                if now - chat.last_chunk_notice < CHUNK_NOTIFY_SECONDS:
                    return  # the turn's end, or the next update, shows it
                chat.last_chunk_notice = now
            elif kind in ("tool_call", "tool_call_update"):
                chat.reply_open = False  # text after a tool is a new reply
                if _text(update.get("title"), 500):
                    chat.activity = _text(update.get("title"), 500)
            elif kind == "usage_update":
                used, size = update.get("used"), update.get("size")
                if isinstance(used, int) and isinstance(size, int):
                    chat.context = (used, size)
            else:
                return
        if kind in ("config_option_update", "current_mode_update") and unsafe_turn:
            self.stop(chat.native_id, hold=True)
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
            replies=tuple((rid, "".join(pieces)) for rid, pieces in chat.replies),
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
