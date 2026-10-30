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
* **No hook acts for the person.** A Copilot hook can approve a tool
  without a permission request, ACP ignores ``disableAllHooks``, and a
  project's hooks run without the folder being trusted, so nothing is opened
  or sent while copilot_hooks finds one; a turn whose log shows an approving
  hook ran stops the chat until the app restarts.
* **Not sent to while another Copilot has it open.** Copilot still records
  this app as the client when a terminal resumes one of its chats, so two
  other signs are checked. Before opening or sending: the session folder's
  ``inuse.<pid>.lock``, which a running Copilot with the chat open holds (a
  live process other than this app's Copilot means it's open elsewhere now).
  After the fact: the session's log, where another Copilot's
  ``session.resume`` or message shows the chat was used meanwhile; it must
  then be opened again (closed in this app's Copilot, then loaded) before
  anything more is sent. A terminal that opens the chat between the lock
  check and the prompt is caught only by the log, after that turn; every
  turn still asks before acting over ACP, so it's never a permission gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import difflib
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Callable, Dict, List, NamedTuple, Optional

from .. import platform_paths
from ..sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, WORKING
from ..usage import CONTEXT_WARNING
from . import copilot_acp, copilot_hooks
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
#: The most kept of any one text from Copilot. A command longer than this
#: can't be shown in full, so it can't be allowed either.
MAX_TEXT_CHARS = 65_536
#: Permission requests waiting in one chat, at most; more are cancelled.
MAX_REQUESTS = 8
#: How long Copilot gets to honour Stop before its process is closed.
CANCEL_GRACE_SECONDS = 15.0
#: Streamed text is shown at most this often, so a long answer in many tiny
#: chunks can't flood the window or hold up the reader thread.
CHUNK_NOTIFY_SECONDS = 0.05
#: The session log is written a moment after the turn's answer arrives.
BILLING_WAIT_SECONDS = 15.0


def _content_diff(call: dict) -> tuple:
    """(diff, too long) from ACP's standard place, ``toolCall.content`` items
    of type "diff" (path, oldText, newText), as only the changed lines with
    one line around each. Copilot 1.0.103 sends ``rawInput.diff`` instead;
    this keeps edits reviewable if it moves. Never cut short: texts too long
    together aren't diffed at all (this runs on the reader thread), and the
    caller checks the diff's own length too."""
    content = call.get("content")
    if content is None:
        return "", False
    if not isinstance(content, list):
        raise CopilotChatError(UNANSWERABLE)
    items = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "diff":
            old = item.get("oldText") if isinstance(item.get("oldText"), str) else ""
            new = item.get("newText") if isinstance(item.get("newText"), str) else ""
            items.append((_text(item.get("path"), 4096), old, new))
    if sum(len(old) + len(new) for _path, old, new in items) > MAX_DIFF_CHARS:
        return "", True
    return "".join("".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=path or "before", tofile=path or "after", n=1))
        for path, old, new in items), False


def _listed_command(entry):
    """One ``rawInput.commands`` entry's text: Copilot 1.0.103 sends objects
    such as {"identifier": "echo hi", "readOnly": false}; plain text is
    accepted too. None if it has no text."""
    if isinstance(entry, dict):
        entry = entry.get("identifier")
    return entry if isinstance(entry, str) else None


def _command(raw: dict) -> tuple:
    """(command, too long) from ``rawInput``: ``command``, which is what
    runs, then any ``commands`` entry it doesn't already contain (Copilot
    lists each command it found in it). All of it must be shown in full to
    be allowed, so text longer than is kept makes a request that can only be
    denied, as does a command with no readable text at all."""
    command = raw.get("command")
    listed = raw.get("commands")
    listed = listed if isinstance(listed, list) else [] if listed is None else [listed]
    texts = [_listed_command(entry) for entry in listed]
    if command is not None and not isinstance(command, str):
        too_long = True
    elif not command:
        # Nothing to show of what runs but the list: every entry must be readable.
        too_long = any(text is None for text in texts)
    else:
        too_long = False
    parts = [command] if isinstance(command, str) and command else []
    parts += [text for text in texts if text and not (parts and text in parts[0])]
    too_long = too_long or any(len(part) > MAX_TEXT_CHARS for part in parts)
    shown = "\n".join(parts)
    return shown[:MAX_TEXT_CHARS], too_long or len(shown) > MAX_TEXT_CHARS


@dataclass(frozen=True)
class ContextReport:
    """How full the context is, as the window reads a Codex token report:
    ``share`` (0 to 1) of ``context_window`` tokens, from usage_update."""
    share: float
    context_window: int


class Stopped(NamedTuple):
    """What a Stop found, read under the runtime's lock: the window's own
    copy of the chat's state can lag behind it (Kelly, #372)."""
    #: The messages that were still waiting, given back unsent.
    waiting: tuple = ()
    #: Whether a cancel went to Copilot, with the timer that closes it if
    #: it doesn't stop: a turn was running, unanswered, on a live connection.
    cancelled: bool = False
    #: Copilot's prompt had returned and the turn was only being checked,
    #: so nothing was cancelled. Not necessarily an answer: a cancel, a
    #: refusal or its process ending return the prompt too.
    answered: bool = False
    #: The turn hadn't decided to send its message: it sees the stop and
    #: gives the message back unsent, so nothing went to Copilot (#405).
    before_sending: bool = False
    #: A cancel had already gone for this turn, with its timer running: this
    #: Stop sent nothing and left the timer alone, so the forced close comes
    #: when the first Stop said it would, not a full grace from now (#405).
    already_asked: bool = False


#: What _cancel did.
CANCEL_SENT, ANSWERED_FIRST, NOT_SENT = "sent", "answered first", "not sent"
ALREADY_SENT = "already sent"


class CopilotChatError(Exception):
    """A reason a chat can't do something now, in words to show."""


UNANSWERABLE = "Copilot asked in a way The Chat Place can't answer safely."


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
    #: Copilot has answered; only this app's checks (billing, the log) are
    #: left, so Stop has nothing to cancel.
    checking: bool = False
    #: The model this chat uses, and the ones Copilot offers it.
    model: str = ""
    models: tuple = field(default=(), repr=False)


def _text(value, limit: int = MAX_TEXT_CHARS) -> str:
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
    #: The command or change was too long to show in full: it can only be
    #: denied.
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
        options = params.get("options")
        if not isinstance(options, list):
            raise CopilotChatError(UNANSWERABLE)
        choices = []
        for option in options:
            if isinstance(option, dict) and option.get("kind") in (ALLOW_ONCE, REJECT):
                if isinstance(option.get("optionId"), str) and option["optionId"]:
                    choices.append((option["kind"], option["optionId"]))
        if not any(k == REJECT for k, _ in choices):
            raise CopilotChatError(UNANSWERABLE)
        raw_diff = raw.get("diff") if isinstance(raw.get("diff"), str) else ""
        diff, diff_too_long = (raw_diff, False) if raw_diff else _content_diff(call)
        command, command_too_long = _command(raw)
        return cls(request_id=request_id,
                   method=COMMAND if kind == "execute" else FILES if kind in _FILE_KINDS else OTHER,
                   item_id=_text(call.get("toolCallId"), 200), title=_text(call.get("title"), 500),
                   command=command, file_name=_text(raw.get("fileName"), 4096),
                   diff=diff[:MAX_DIFF_CHARS],
                   too_long=diff_too_long or command_too_long or len(diff) > MAX_DIFF_CHARS,
                   choices=tuple(choices), turn_id=turn_id)

    def offered(self):
        """Allow Once (if Copilot offered it) then Deny; never "always"."""
        kinds = [k for k, _ in self.choices]
        if self.too_long:
            return (REJECT,) if REJECT in kinds else ()  # can't be seen, can't be allowed
        return tuple(k for k in (ALLOW_ONCE, REJECT) if k in kinds)

    def reviewable(self, file_detail: str = "") -> bool:
        """Whether there's enough to review for an allowing answer."""
        if self.too_long:
            return False
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
        if self.method == COMMAND and (self.command or self.too_long):
            if self.command:
                parts.append("Command:\n" + self.command)
            if self.too_long:
                parts.append("This command is too long to show in full, or isn't plain text, "
                             "so it can only be denied. Ask Copilot to run it in smaller steps.")
        elif self.method == FILES:
            if self.file_name:
                parts.append("File:\n" + self.file_name)
            parts.append("Changes:\n" + (self.diff or file_detail
                                        or "The changes aren't available to review. Deny it."))
            if self.too_long:
                parts.append("This change, or something with it, is too long to show in full, "
                             "so it can only be denied. Ask Copilot to make it in smaller steps.")
        elif self.too_long:
            parts.append("Part of this request is too long to show in full, so it can only be "
                         "denied. Ask Copilot to do it in smaller steps.")
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


#: More models than any plan offers: a longer list is cut short.
MAX_MODELS = 100


def _shown(text: str) -> bool:
    """Text to show in a list: something visible, and nothing that moves
    or hides what's around it (control and format characters)."""
    return (any(not (c.isspace() or c in _BLANKS or unicodedata.category(c) in _HIDDEN)
                for c in text)
            and not any(unicodedata.category(c) in ("Cc", "Cf") for c in text))


def _flat_options(items):
    """A select's options, with one level of ACP's option groups
    (SessionConfigSelectGroup: a dict with its own "options" list) read in
    place, in order. Anything else odd is passed on for the caller to skip."""
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict) and "value" not in item and isinstance(item.get("options"), list):
            yield from item["options"]
        else:
            yield item


def _models(options):
    """(current model, ((value, label), …)) from ACP configOptions' "model"
    choice, or None when they don't have one. Copilot lists only what the
    account's plan allows, and each label says the premium requests a turn
    costs ("GPT-5 (1x)"); a model listed twice is listed once, with its
    cost if either says it. ACP lets a select's options come in groups
    ({"group", "name", "options": [...]}); one level of those is read as
    one list, in order."""
    if not isinstance(options, list):
        return None
    for option in options:
        if not isinstance(option, dict) or option.get("id") != "model":
            continue
        choices, costed = {}, set()
        for item in _flat_options(option.get("options")):
            value = _text(item.get("value"), 200) if isinstance(item, dict) else ""
            if not value or not _shown(value):
                continue
            name = " ".join(_text(item.get("name"), 200).split()) or value
            meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
            usage = " ".join(_text(meta.get("copilotUsage"), 20).split())
            label = f"{name} ({usage})" if usage else name
            if value not in choices or (usage and value not in costed):
                choices[value] = label if _shown(label) else value
                if usage:
                    costed.add(value)
            if len(choices) >= MAX_MODELS:
                break
        return _text(option.get("currentValue"), 200), tuple(choices.items())
    return None


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


def log_events(events_path: Path, offset: int, types, end: Optional[int] = None) -> list:
    """(type, data) for each event of one of ``types`` written to the
    session's log after byte ``offset`` (and before byte ``end``, if given),
    oldest first."""
    try:
        handle = open(events_path, "rb")
    except OSError:
        return []
    marks = [f'"{name}"'.encode() for name in types]
    with handle:
        try:
            handle.seek(offset)
            data = handle.read() if end is None else handle.read(max(0, end - offset))
        except OSError:
            return []
    lines = [raw for raw in data.splitlines() if any(mark in raw for mark in marks)]
    found = []
    for raw in lines:
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") in types:
            data_ = event.get("data")
            found.append((event["type"], data_ if isinstance(data_, dict) else {}))
    return found


#: What another Copilot leaves in a session's log when it uses the chat: it
#: reopens it (session.resume), and sends to it (user.message). Copilot
#: still records this app as the client afterwards, so the log is the sign.
_ELSEWHERE_EVENTS = ("session.resume", "user.message")


def _prompts(events) -> int:
    """The person's messages among ``events``: user.message events that
    aren't a sub-agent's (those name the task in parentAgentTaskId)."""
    return sum(kind == "user.message" and not data_.get("parentAgentTaskId")
               for kind, data_ in events)


def _edited_paths(events) -> tuple:
    """The files ``events`` show a tool changing, in order, once each: a
    successful tool.execution_complete's ``fileEdits`` paths, as Changed
    Files reads them (copilot.CopilotParser). For an agent conversation's
    hand-off (#369)."""
    paths = []
    for kind, data_ in events:
        entries = data_.get("fileEdits") if kind == "tool.execution_complete" else None
        if data_.get("success") is not True or not isinstance(entries, list):
            continue
        for entry in entries:
            path = _text(entry.get("path"), 1000) if isinstance(entry, dict) else ""
            if path and path not in paths:
                paths.append(path)
    return tuple(paths)


def _hook_ran(events) -> bool:
    """Whether an approving hook (copilot_hooks) ran, from hook.start events."""
    return any(kind == "hook.start" and isinstance(data_.get("hookType"), str)
               and data_["hookType"].lower() in copilot_hooks.APPROVING_EVENTS
               for kind, data_ in events)


#: A hook answered though none was found: wherever it is, it's still
#: there, so this stays until The Chat Place restarts.
IN_USE = ("This Copilot chat is open in another Copilot. Close it there, then open it here "
          "again.")
HOOK_ANSWERED = ("A Copilot hook answered a permission question for you in this turn, though "
                 "The Chat Place found no hook that could. It won't send to this chat again; "
                 "check Copilot's hooks and plugins.")


def hooks_refusal(places) -> str:
    """Why nothing is sent while a hook that counts exists, or ""."""
    if not places:
        return ""
    return ("Copilot is set up with a hook that could act without asking you "
            f"({places[0]}), so The Chat Place won't send to Copilot in this folder. "
            "Use Copilot CLI here instead, or remove the hook if you don't need it.")


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


def _whole_lines_end(path: Path) -> int:
    """Where the log's last complete line ends: just past its last newline.
    A line another Copilot is still writing is after this, so the next check
    reads it whole rather than both checks seeing half of it (Kelly, #364)."""
    try:
        handle = open(path, "rb")
    except OSError:
        return 0
    with handle:
        try:
            end = handle.seek(0, os.SEEK_END)
            position = end
            while position > 0:
                start = max(0, position - 65536)
                handle.seek(start)
                chunk = handle.read(position - start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    return start + newline + 1
                position = start
        except OSError:
            return 0
    return 0


_LOCK = re.compile(r"inuse\.(\d{1,10})\.lock")


def locked_elsewhere(folder: Path, own_pid: Optional[int]) -> bool:
    """Whether a running Copilot other than this app's has the chat open.
    Copilot (1.0.103) holds ``inuse.<pid>.lock`` in the session folder while
    it has a chat open, named for the process (checked: the one this app
    starts), and leaves it behind when it exits. So a lock counts only if that
    process is running now and was already running when the lock was written:
    process ids are handed out again, and a later process with a stale lock's
    id has nothing to do with the chat."""
    try:
        names = [entry.name for entry in os.scandir(folder)]
    except FileNotFoundError:
        return False  # no session folder: nothing holds it
    except OSError:
        return True  # can't look: it can't be ruled out
    locks = {}
    for name in names:
        match = _LOCK.fullmatch(name)
        if not match or int(match.group(1)) == own_pid:
            continue
        # Only lock files are looked at, each on its own: others come and go
        # (.hold, temporary files) and must not switch the check off (Kelly).
        try:
            locks[int(match.group(1))] = os.stat(folder / name).st_mtime
        except FileNotFoundError:
            continue  # released while looking
        except OSError:
            return True
    if not locks:
        return False
    started = platform_paths.processes_started_at(locks)
    return any(pid in started and started[pid] <= written + 2  # filesystem times are coarse
               for pid, written in locks.items())


class _Chat:
    def __init__(self, native_id: str, cwd: str):
        self.native_id, self.cwd = native_id, cwd
        #: The connection this chat was opened (and safety-checked) on. A new
        #: Copilot process hasn't loaded it, so it must be opened again.
        self.connection = None
        self.mode = ""
        self.allow_all = ""
        self.busy = False
        #: Copilot has answered this turn's prompt: what's left (the billing
        #: check) is ours, so there's nothing for Stop to cancel.
        self.answered = False
        self.turns = 0
        self.replies: List[list] = []
        #: The files the last turn changed, from its stretch of the log.
        self.changed_paths: tuple = ()
        self.reply_open = False
        self.requests: Dict[object, CopilotRequest] = {}
        self.queue: List[str] = []
        self.unsent: tuple = ()
        #: The unsent messages an update has already carried (_notify).
        self.unsent_told: tuple = ()
        #: Taken before _lock, while a state is made and handed to the
        #: window, so this chat's updates reach it in the order they were
        #: made (a finished turn's before the next one's).
        self.delivering = threading.RLock()
        self.problem = ""
        self.notice = ""
        self.outcome = ""
        self.activity = ""
        self.started_at = 0.0
        self.loading = False
        self.stopping = False
        #: This turn's prompt is queued to be written to Copilot: a cancel
        #: sent before then would reach Copilot first, and be ignored.
        self.prompt_queued = False
        #: This turn has decided to send its prompt (it wasn't stopped
        #: first): only then can a Stop's cancel lead to Copilot being
        #: closed, and the window warn that it will be (Kelly, #405).
        self.sending = False
        self.context = (0, 0)
        #: Counts compactions (context use dropping sharply), as Codex's state
        #: does, so the window's context warning can be said again after one.
        self.context_epoch = 0
        self.log_offset = 0
        #: The log's size when the last turn began: billing is checked on
        #: what was written after it.
        self.billing_offset = 0
        #: Waiting messages taken back by a stop the person didn't ask for.
        self.held: tuple = ()
        #: The log's size when this app last finished with it (opened it, or
        #: ended a turn); anything another Copilot adds after shows there.
        self.log_end = 0
        #: Used by another Copilot since: open it again before sending.
        self.elsewhere = False
        #: The connection whose Copilot may have this chat open (it was
        #: created or loaded there), even if it isn't usable here now:
        #: Copilot won't load it again until it's closed there.
        self.open_on = None
        self.last_chunk_notice = 0.0
        self.cancel_timer = None
        #: The model Copilot uses for this chat, and the ones it offers
        #: ((value, label), …), from its configOptions.
        self.model = ""
        self.models: tuple = ()
        #: Counts each time the chat's settings are asked for whole (opened,
        #: or a setting switched), and which of those was last taken in: a
        #: reply older than one already taken in mustn't overwrite it. A
        #: switch that fails asks for nothing, so it never makes an earlier
        #: one's reply look old.
        self.settings_gen = 0
        self.settings_applied = 0
        #: The turn ``cancel_timer`` is for: a second Stop in that turn
        #: mustn't put the forced close off again (Kelly, #405).
        self.cancel_turn = 0

    @property
    def safe(self) -> bool:
        return self.mode == AGENT_MODE and self.allow_all == "off"

    def apply(self, settings: dict) -> None:
        self.mode, self.allow_all = settings["mode"], settings["allow_all"]

    def apply_models(self, options) -> None:
        """The model Copilot uses for this chat and the ones it offers, when
        ``options`` (configOptions) say."""
        found = _models(options)
        if found is not None:
            self.model, self.models = found


class CopilotChatRuntime:
    """Every Copilot chat this app runs, over one ``copilot --acp``.

    Locking: ``_lock`` guards chat state and is never held while waiting on
    Copilot, so the window's reads (ready, snapshot) never block on it.
    ``_connect_lock`` serialises starting Copilot.
    """

    def __init__(self, on_update: Callable[[CopilotChatState], None], *,
                 start: Optional[Callable] = None, sessions_dir: Optional[Path] = None,
                 billing_wait: float = BILLING_WAIT_SECONDS,
                 cancel_grace: float = CANCEL_GRACE_SECONDS,
                 find_hooks: Optional[Callable[[str], list]] = None):
        self._on_update = on_update
        #: Where an approving hook is defined for a folder (copilot_hooks).
        self._find_hooks = find_hooks or copilot_hooks.blocking_hooks
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
        #: The models Copilot last offered a chat here, for a new one.
        self._last_models: tuple = ()
        self._closed = False

    # -- the connection ------------------------------------------------------

    def _connect(self) -> copilot_acp.AcpConnection:
        try:
            return self._connect_once()
        except copilot_acp.AcpError as exc:
            raise CopilotChatError(str(exc)) from None
        except OSError as exc:
            # Starting it: not installed, or the system refused. Only our own
            # words are shown, never the system's, which may hold paths.
            raise CopilotChatError(
                str(exc) if str(exc) == platform_paths.COPILOT_MISSING
                else "GitHub Copilot CLI couldn't be started. Try again, or restart "
                     "The Chat Place.") from None

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

    def _hooks_refusal(self, cwd: str) -> str:
        """Why nothing may be sent in ``cwd`` because of a hook, or ""
        (reads files: never with _lock held)."""
        try:
            return hooks_refusal(self._find_hooks(cwd))
        except copilot_hooks.CantCheck as exc:
            return (f"The Chat Place couldn't check all of Copilot's hooks ({exc}): a file there "
                    "can't be read, or there are too many files to look through. Until that's "
                    "sorted out, it won't send to Copilot in this folder; use Copilot CLI here "
                    "instead.")
        except Exception:  # noqa: BLE001 - a check that fails refuses
            return ("The Chat Place couldn't check Copilot's hooks, so it won't send to "
                    "Copilot now.")

    @staticmethod
    def _own_pid(connection) -> Optional[int]:
        """The process id of this app's Copilot on ``connection``, if known."""
        process = getattr(getattr(connection, "_process", None), "process", None)
        pid = getattr(process, "pid", None)
        return pid if isinstance(pid, int) else None

    def _events(self, native_id: str) -> Path:
        return (self._sessions_dir or platform_paths.copilot_sessions_dir()) / native_id / EVENTS_FILE

    # -- opening chats -------------------------------------------------------

    def create(self, cwd: str, model: str = "") -> str:
        """A new Copilot chat in ``cwd``; its native id. No prompt is sent.
        Starts Copilot if needed and waits up to a minute for it, so call it
        off the UI thread. ``model``: switch to it before anything is sent,
        if Copilot offers it; if it can't, the chat says so and keeps
        Copilot's own."""
        _check_folder(cwd)
        refusal = self._hooks_refusal(cwd)
        if refusal:
            raise CopilotChatError(refusal)
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
        chat.apply_models(result.get("configOptions"))
        self._seen_models(chat)
        chat.connection = chat.open_on = connection
        chat.log_end = _whole_lines_end(self._events(native_id))
        with self._lock:
            self._chats[native_id] = chat
            self._owned.add(native_id)
        if model and model != chat.model:
            try:
                self.set_model(native_id, model)
            except CopilotChatError:
                with self._lock:
                    chat.notice = ("Copilot couldn't switch this chat to the model chosen, so it "
                                   "uses Copilot's own. File, Change Model chooses another.")
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
                    and not chat.elsewhere and not self._halted and chat.connection is not None
                    and chat.connection is self._connection and not chat.connection.closed)

    def models(self) -> tuple:
        """The models Copilot last offered a chat here ((value, label), …):
        what a new chat can start with. Empty until a chat has been opened."""
        with self._lock:
            return self._last_models

    def _seen_models(self, chat: _Chat) -> None:
        """Keep the list a chat was offered for the next new chat. Call
        with _lock held, or before the chat is shared."""
        if chat.models:
            self._last_models = chat.models

    def set_model(self, native_id: str, model: str) -> None:
        """Switch a chat to ``model``, one Copilot offers it, from its next
        turn. Waits for Copilot, so call it off the UI thread."""
        self._set_option(native_id, "model", model, "Copilot couldn't switch to that model.")

    def _set_option(self, native_id: str, config_id: str, value: str, refused: str) -> None:
        """Set one of a chat's configOptions (``session/set_config_option``)
        and take in the whole set Copilot replies with. ``refused`` is said if
        Copilot says no. A reply older than a later switch or reopen whose
        reply has already been taken in is ignored (a config_option_update
        notification doesn't count as either). Waits for Copilot, so call it
        off the UI thread."""
        with self._lock:
            chat = self._chats.get(native_id)
            if chat is None or chat.connection is None or chat.connection.closed or chat.loading:
                raise CopilotChatError("This Copilot chat isn't open here yet. Select it, wait "
                                       "until it's ready, then try again.")
            if config_id == "model" and value not in dict(chat.models):
                raise CopilotChatError("Copilot doesn't offer that model for this chat.")
            connection = chat.connection
            chat.settings_gen += 1
            generation = chat.settings_gen
        try:
            result = connection.request("session/set_config_option", {
                "sessionId": native_id, "configId": config_id, "value": value})
        except copilot_acp.AcpError:
            raise CopilotChatError(refused) from None
        options = result.get("configOptions") if isinstance(result, dict) else None
        present = {option.get("id") for option in options if isinstance(option, dict)
                   } if isinstance(options, list) else set()
        unsafe_turn = False
        with self._lock:
            if (self._chats.get(native_id) is not chat or chat.loading
                    or chat.connection is not connection
                    or generation <= chat.settings_applied):
                # Closed, or opened again or switched again (and that reply
                # taken in) while Copilot answered: what this reply says is
                # older than what the chat has now.
                return
            chat.settings_applied = generation
            settings = _settings(options)
            # Each setting the reply names counts, unsafe ones included; one
            # it leaves out is left as it was, never read as safe or unsafe.
            if "mode" in present:
                chat.mode = settings["mode"]
            if "allow_all" in present:
                chat.allow_all = settings["allow_all"]
            if _models(options) is not None:
                chat.apply_models(options)
                self._seen_models(chat)
            # It said yes: the switched setting has the value asked for, even
            # if the reply leaves it out.
            # Only the model: a safety setting Copilot didn't report is
            # never assumed.
            if config_id == "model" and config_id not in present:
                chat.model = value
            # As for a config_option_update: a turn running when Copilot stops
            # asking before it acts is stopped.
            unsafe_turn = chat.busy and not chat.safe and not chat.stopping
            if unsafe_turn:
                chat.notice = ("Copilot stopped asking before it acts, so The Chat Place "
                               "stopped this turn.")
        if unsafe_turn:
            self.stop(native_id, hold=True)
        self._notify(chat)

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
        refusal = self._hooks_refusal(cwd)
        if refusal:
            raise CopilotChatError(refusal)
        connection = self._connect()
        if locked_elsewhere(self._events(native_id).parent, self._own_pid(connection)):
            raise CopilotChatError(IN_USE)
        before = _whole_lines_end(self._events(native_id))
        with self._lock:
            chat = self._chats.get(native_id) or _Chat(native_id, cwd)
            if chat.busy:
                raise CopilotChatError("This Copilot chat is still finishing a turn.")
            if chat.loading:
                raise CopilotChatError("This Copilot chat is still opening.")
            # Copilot won't load a chat it already has open (1.0.103), as it
            # is after use elsewhere or a problem: it's closed there first,
            # so what's loaded is what's in the log now.
            open_here = chat.open_on is connection
            chat.loading, chat.connection, chat.open_on = True, connection, connection
            self._chats[native_id] = chat
        if open_here:
            try:
                connection.request("session/close", {"sessionId": native_id})
            except copilot_acp.AcpError:
                pass  # not open after all, or this Copilot can't close: the load says
        try:
            result = connection.request("session/load", {
                "sessionId": native_id, "cwd": cwd, "mcpServers": []},
                timeout=copilot_acp.LOAD_SECONDS)
        except Exception as exc:
            with self._lock:
                # It may yet have opened (a late answer), so open_on stays.
                chat.loading, chat.connection = False, None
            if isinstance(exc, copilot_acp.AcpError):
                raise CopilotChatError(str(exc)) from None
            raise
        log_end = _whole_lines_end(self._events(native_id))  # first: nothing after it goes unread
        kinds = usage_kinds(self._events(native_id), chat.billing_offset)
        in_use = any(data_.get("alreadyInUse") is True for _kind, data_ in
                     log_events(self._events(native_id), before, ("session.resume",), log_end))
        with self._lock:
            chat.loading = False
            if in_use:
                chat.elsewhere = True  # open here, but not to be sent to
            else:
                chat.elsewhere, chat.log_end = False, log_end
                chat.notice = ""  # opened again: "open it again" no longer applies
            chat.apply(_settings(result.get("configOptions")))
            chat.apply_models(result.get("configOptions"))
            chat.settings_gen += 1
            # A reopen supersedes every switch asked for before it.
            chat.settings_applied = chat.settings_gen
            self._seen_models(chat)
            if chat.problem and chat.problem != HOOK_ANSWERED and not self._halted and kinds and all(k == "copilot" for k in kinds):
                chat.problem = ""  # the late record has arrived, and it's Copilot's
            late_elsewhere = any(k != "copilot" for k in kinds)
        if late_elsewhere:
            self._halt()
        if in_use:
            raise CopilotChatError(IN_USE)
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
        if chat.elsewhere:
            return ("This Copilot chat was used outside The Chat Place since it was opened "
                    "here. Open it again to carry on.")
        if not chat.safe:
            return ("Copilot isn't set to ask before it acts in this chat "
                    "(agent mode, with allow-all off), so nothing was sent.")
        return ""

    def send(self, native_id: str, text: str) -> bool:
        """Send now, or queue behind the running turn; True if queued.
        Reads Copilot's settings and the chat's log first, so call it off
        the UI thread."""
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
            cwd, busy = (chat.cwd, chat.busy) if chat else ("", False)
            log_end, turns = (chat.log_end, chat.turns) if chat else (0, 0)
        # Files are read with no lock held. While a turn runs its own
        # messages are in the log, so the end of the turn checks instead.
        hooks = self._hooks_refusal(cwd) if chat else ""
        events = log_events(self._events(native_id), log_end, _ELSEWHERE_EVENTS,
                            _whole_lines_end(self._events(native_id))) if chat and not busy else []
        elsewhere = (any(kind == "session.resume" for kind, _ in events) or _prompts(events) > 0
                     or bool(chat and not busy
                             and _whole_lines_end(self._events(native_id)) < log_end))
        # Only for a chat open on this app's current Copilot: otherwise the
        # refusal below says it isn't open here, which is the truer reason.
        current = chat is not None and chat.connection is not None \
            and chat.connection is self._connection
        open_elsewhere = bool(current and not busy and locked_elsewhere(
            self._events(native_id).parent, self._own_pid(chat.connection)))
        with self._lock:
            # Only if no turn of this app's began meanwhile (its own message
            # would be in the log too).
            if ((elsewhere or open_elsewhere) and not chat.busy and chat.turns == turns
                    and chat.log_end == log_end):
                chat.elsewhere = True
            if open_elsewhere and chat.elsewhere:
                raise CopilotChatError(IN_USE)
            refusal = self._refusal(chat) or hooks
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

    def _begin(self, chat: _Chat, text: str, start: bool = True) -> threading.Thread:
        """Start a turn (with _lock held, after _refusal said yes). With
        ``start`` off, the caller starts the returned thread, once it has
        said the previous turn ended."""
        chat.busy, chat.outcome, chat.notice = True, "inProgress", ""
        chat.answered = False
        chat.prompt_queued = False
        chat.sending = False
        chat.turns += 1
        chat.started_at = time.monotonic()
        chat.reply_open = False
        chat.replies = []  # this turn's replies, as Codex's state has
        chat.changed_paths = ()
        chat.unsent = ()
        # Billing is checked on what this turn adds to the log, never on an
        # earlier turn's record.
        # From where the last check stopped, not the log's size now:
        # whatever another Copilot wrote in between is in this turn's
        # stretch, and seen when it ends (Kelly, #364).
        chat.log_offset = chat.log_end
        # Billing, though, only on what this turn adds: a record our own
        # Copilot wrote after the last check mustn't vouch for this turn.
        chat.billing_offset = _size(self._events(chat.native_id))
        thread = threading.Thread(target=self._run_turn, args=(chat, chat.connection, text),
                                  daemon=True, name="copilot-turn")
        if start:
            thread.start()
        return thread

    def _run_turn(self, chat: _Chat, connection, text: str) -> None:
        outcome = "failed"
        not_sent = ()
        with self._lock:
            # Stopped between the last turn's end and this one's start: the
            # message goes back unsent, never to Copilot.
            stopped_first = chat.stopping
            turn = chat.turns
            # Decided in the same locked step a Stop reads it in, so a Stop
            # either finds the prompt going out or stops it going.
            chat.sending = not stopped_first
        if stopped_first:
            not_sent = (text,)

        def queued():
            # A Stop from here on sends its own cancel, which now reaches
            # Copilot after the prompt. One that came while the prompt was
            # on its way left the cancel to this turn, to send now.
            with self._lock:
                chat.prompt_queued = True
                cancel = chat.stopping and not chat.answered and chat.turns == turn
            if cancel:
                self._cancel(chat, connection, turn)
        try:
            result = {"stopReason": "cancelled"} if stopped_first else connection.request(
                "session/prompt", {"sessionId": chat.native_id,
                                   "prompt": [{"type": "text", "text": text}]},
                timeout=None, on_queued=queued)
            reason = result.get("stopReason")
            outcome = {"end_turn": "completed", "cancelled": "interrupted"}.get(reason, "failed")
            if outcome == "failed":
                with self._lock:
                    chat.notice = ("Copilot ended this turn early (it refused, or reached a "
                                   "limit). Messages waiting after it weren't sent.")
        except copilot_acp.AcpNotSent:
            # It never left (too many requests waiting, or Copilot closed after
            # the checks): Copilot can't have seen it, so it comes back unsent.
            not_sent = (text,)
            with self._lock:
                chat.notice = chat.notice or "Copilot couldn't take your message, so it wasn't sent."
        except copilot_acp.AcpAuthRequired:
            # A sign-in that stopped working: say what to do (#391). The
            # message isn't given back, since Copilot may have recorded or
            # even acted on it first; ones still waiting come back unsent.
            # This deliberately replaces an earlier notice: signing in is
            # what the person has to do next.
            with self._lock:
                chat.notice = copilot_acp.NOT_SIGNED_IN_SENT
        except Exception:  # noqa: BLE001 - the reason shown is ours, not Copilot's
            with self._lock:
                # A more particular reason (Stop ignored, say) is kept.
                chat.notice = chat.notice or ("Copilot stopped before finishing. Your message "
                                              "wasn't sent again.")
        with self._lock:
            # Copilot is done with this turn: a Stop from here on has nothing
            # to cancel, and must not start the timer that closes Copilot.
            chat.answered = True
            if chat.cancel_timer is not None:
                chat.cancel_timer.cancel()  # it stopped in time
                chat.cancel_timer = None
        self._notify(chat)  # the window knows Stop has nothing left to cancel
        try:
            self._check_billing(chat, outcome)
        except Exception:  # noqa: BLE001 - an unreadable log is a failed check
            self._halt()
        # What else this turn's stretch of the log shows: another Copilot
        # using the chat at the same time, or a hook answering for the person.
        events_path = self._events(chat.native_id)
        log_end, hook_ran, hooks, changed = chat.log_end, False, "", ()
        finished, following = None, None
        try:
            log_end = _whole_lines_end(events_path)  # first: nothing after it goes unread
            events = log_events(events_path, chat.log_offset,
                                _ELSEWHERE_EVENTS + ("hook.start", "tool.execution_complete"),
                                log_end)
            # Copilot writes a reopened chat's session.resume when its first
            # prompt arrives, so this app's own reopening shows here too; one
            # from another Copilot, while this one has the chat open, says
            # alreadyInUse (as Copilot 1.0.103 does).
            elsewhere = (any(kind == "session.resume" and data_.get("alreadyInUse") is True
                             for kind, data_ in events)
                         # Ours is one of them, unless it never reached Copilot.
                         or _prompts(events) > (0 if not_sent else 1))
            hook_ran = _hook_ran(events)
            changed = _edited_paths(events)
            hooks = self._hooks_refusal(chat.cwd)
            # Before a queued message goes: is another Copilot holding the chat
            # now? And a log shorter than the last check means it was replaced.
            elsewhere = (elsewhere or log_end < chat.log_end
                         or locked_elsewhere(events_path.parent, self._own_pid(connection)))
        except Exception:  # noqa: BLE001 - a check that fails must not leave the chat busy
            elsewhere = True
        chat.delivering.acquire()  # released once the finished turn is handed over
        with self._lock:
            chat.log_end = log_end
            chat.changed_paths = changed
            if elsewhere:
                chat.elsewhere = True
            if hook_ran:
                chat.problem = HOOK_ANSWERED
            chat.busy, chat.outcome, chat.activity = False, outcome, ""
            chat.requests.clear()
            held, chat.held = chat.held, ()
            chat.stopping = False
            refusal = self._refusal(chat) or hooks
            if outcome != "completed" or refusal or held:
                # Never carry on with queued messages after a stop, a failure,
                # or a change that means Copilot won't ask first: they go back
                # to the person, unsent. That includes a turn that ended as if
                # nothing happened after an automatic stop held messages back
                # (Copilot can finish the turn despite the cancel).
                chat.unsent, chat.queue = not_sent + held + tuple(chat.queue), []
                if refusal and chat.unsent and not chat.notice:
                    chat.notice = refusal
            elif chat.queue:
                # The window hears this turn end, with its replies, before
                # the waiting message's turn starts (Kelly, #372): one update
                # for both would skip this one's announcement.
                chat.unsent = ()
                finished = self._state(chat)
                chat.unsent_told = chat.unsent
                following = self._begin(chat, chat.queue.pop(0), start=False)
            else:
                chat.unsent = ()
        try:
            if following is not None:
                self._deliver(finished)
                following.start()
            self._notify(chat)
        finally:
            chat.delivering.release()

    def _check_billing(self, chat: _Chat, outcome: str) -> None:
        """What this turn wrote must say GitHub Copilot answered. A completed
        turn must have such a record; any turn with another provider's
        record stops everything."""
        events = self._events(chat.native_id)
        deadline = time.monotonic() + self._billing_wait
        kinds = usage_kinds(events, chat.billing_offset)
        while outcome == "completed" and not kinds and time.monotonic() < deadline:
            time.sleep(0.1)
            kinds = usage_kinds(events, chat.billing_offset)
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

    def stop(self, native_id: str, *, hold: bool = False) -> Stopped:
        """Cancel the running turn; the messages still waiting come back.
        Waiting permission requests are answered "cancelled" first, as ACP
        asks of a client that cancels. With ``hold`` (a stop the person
        didn't ask for) the messages are kept and given back as unsent when
        the turn ends, rather than returned."""
        with self._lock:
            chat = self._chats.get(native_id)
            if chat is None or not chat.busy:
                return Stopped()
            chat.stopping = True
            turn = chat.turns  # the turn being stopped, read under the lock
            waiting, chat.queue = tuple(chat.queue), []
            if hold:
                chat.held += waiting
                waiting = ()
            requests = list(chat.requests)
            chat.requests.clear()
            answered = chat.answered
            prompt_queued = chat.prompt_queued
            sending = chat.sending
            connection = chat.connection
        if connection is not None and not connection.closed:
            for request_id in requests:
                try:
                    connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
                except copilot_acp.AcpError:
                    pass
        # Once Copilot has answered, the turn is only being checked:
        # cancelling it, and the timer that closes a Copilot ignoring the
        # cancel, would end every other chat's turn for nothing.
        cancel = connection is not None and not connection.closed and not answered
        # A turn that hasn't decided to send will see this stop and give its
        # message back unsent: there's nothing to cancel, and nothing will be
        # closed, so the window mustn't warn that it will be (Kelly, #405).
        # (The prompt can still fail to go after that, AcpNotSent for a
        # message too big or too many requests waiting: a moment's gap in
        # which the warning is said for nothing. Its message comes back
        # unsent, and says so.)
        before_sending = cancel and not sending
        cancel = cancel and sending
        # Before its prompt is queued, the turn sends the cancel itself, right
        # after the prompt (_run_turn): sent now, it would reach Copilot
        # first, be ignored, and the timer would close a Copilot that was
        # only answering (Kelly, #372). Or the turn sees the stop before
        # sending, and the message comes back unsent.
        sent = self._cancel(chat, connection, turn) if cancel and prompt_queued else None
        if sent == ANSWERED_FIRST:
            # No timer, so nothing will be closed: the window mustn't
            # warn that it will be. Copilot answered first (Kelly, #386).
            cancel, answered = False, True
        elif sent == NOT_SENT:
            # The cancel couldn't go out (Copilot is closing): no timer
            # either, and the turn ends with the connection (Kelly, #405).
            cancel = False
        self._notify(chat)
        return Stopped(waiting, cancelled=cancel, answered=answered,
                       before_sending=before_sending,
                       already_asked=sent == ALREADY_SENT)

    def _cancel(self, chat: _Chat, connection, turn: int) -> str:
        """Ask Copilot to cancel ``turn``, and close it if it doesn't stop in
        time. CANCEL_SENT; ALREADY_SENT if a cancel for this turn already
        went and its timer is running (nothing is sent and that timer is
        kept); ANSWERED_FIRST if Copilot answered first, or NOT_SENT if the
        cancel couldn't go out, neither of which keeps a timer."""
        timer = threading.Timer(self._cancel_grace, self._cancel_overdue,
                                args=(chat, connection, turn))
        timer.daemon = True  # never keeps the app open after it quits
        with self._lock:
            # Checked and queued in one step: once the turn has ended, the
            # next waiting message's turn may have begun, and a cancel
            # queued after its prompt would stop that one instead. Queuing
            # never waits on Copilot, so the lock is held only a moment.
            if chat.answered or chat.turns != turn:
                return ANSWERED_FIRST
            if chat.cancel_timer is not None and chat.cancel_turn == turn:
                # Already asked, and the clock is running: pressing Stop
                # again mustn't put the forced close off (Kelly, #405).
                return ALREADY_SENT
            try:
                connection.notify("session/cancel", {"sessionId": chat.native_id})
            except copilot_acp.AcpError:
                return NOT_SENT  # never queued: Copilot is closing
            if chat.cancel_timer is not None:
                chat.cancel_timer.cancel()
            chat.cancel_timer, chat.cancel_turn = timer, turn
        timer.start()
        return CANCEL_SENT

    def _cancel_overdue(self, chat: _Chat, connection, turn: int) -> None:
        """Copilot didn't stop when asked. Ending the turn here alone would
        leave it free to keep acting, so its process is closed: every turn on
        it ends, and chats are opened again to carry on."""
        with self._lock:
            overdue = (chat.busy and not chat.answered and chat.turns == turn
                       and chat.connection is connection)
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
        except Exception:  # noqa: BLE001 - a malformed request is declined, not refused
            request, problem = None, UNANSWERABLE
        with self._lock:
            # Checked and stored in one go, so a Stop can't slip in between
            # and leave this request stored but never answered.
            watched = (chat is not None and not chat.loading and chat.busy and not chat.answered
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
            try:
                connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
            except copilot_acp.AcpError:
                pass  # Copilot is closing; the turn ends with it
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
                    chat.apply_models(update.get("configOptions"))
                    self._seen_models(chat)
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
                    before, after = chat.context, (used, size)
                    if (before[1] > 0 and after[1] > 0
                            and before[0] / before[1] >= CONTEXT_WARNING > after[0] / after[1]):
                        # Compacted from past the window's 80% warning to below
                        # it: the warning can be said again (Kelly, #372).
                        chat.context_epoch += 1
                    chat.context = (used, size)
            else:
                return
        if kind in ("config_option_update", "current_mode_update") and unsafe_turn:
            self.stop(chat.native_id, hold=True)
        self._notify(chat)

    # -- state -----------------------------------------------------------------

    def known(self) -> list:
        """(native id, folder) of each chat open in this runtime, so a new
        chat can be listed before Copilot has written its log."""
        with self._lock:
            return [(chat.native_id, chat.cwd) for chat in self._chats.values()]

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
            notice=chat.notice, context_used=chat.context[0], context_size=chat.context[1],
            context_epoch=chat.context_epoch, checking=chat.busy and chat.answered,
            changed_paths=chat.changed_paths, model=chat.model, models=chat.models,
            token_usage=(ContextReport(min(1.0, chat.context[0] / chat.context[1]), chat.context[1])
                         if chat.context[1] > 0 and chat.context[0] >= 0 else None))

    def _notify(self, chat: _Chat) -> None:
        with chat.delivering:
            with self._lock:
                state = self._state(chat)
                # The window puts unsent messages back in the message box
                # when an update has them, so each is in one update only (as
                # Codex's); snapshot() still shows them.
                if chat.unsent and chat.unsent_told is chat.unsent:
                    state = replace(state, unsent=())
                chat.unsent_told = chat.unsent
            self._deliver(state)

    def _deliver(self, state: CopilotChatState) -> None:
        try:
            self._on_update(state)
        except Exception:  # noqa: BLE001 - the window's problem mustn't stop the turn
            pass
