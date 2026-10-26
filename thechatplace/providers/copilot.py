"""Read-only GitHub Copilot CLI history from its own session folders.

Copilot CLI keeps each session in ``~/.copilot/session-state/<id>/``: a small
``workspace.yaml`` (id, folder, name, when it was created and updated) and an
``events.jsonl`` log, one JSON event per line, in the same envelope as
``copilot -p --output-format json`` streams. Neither format is documented
(dev-notes/copilot-cli-probe.md records what 1.0.103 writes), so this module
is the one place that knows them, and it is written the way transcript.py is:
unknown event types are skipped, a line that won't parse is counted, never
raised, and a log is read incrementally as it grows.

This adapter only reads files. It never starts ``copilot``, so it can't
resume a session, send a prompt or spend a premium request.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Iterable, List, Optional, Tuple

from .. import platform_paths
from ..sessions import IDLE, NEEDS_YOU, STATUS_UNAVAILABLE, TERMINAL, WORKING, SessionInfo
from ..transcript import (ASSISTANT, DENIED, ERROR, INTERRUPTED, TOOL, TOOL_RESULT, USER,
                          ChatMessage, Transcript, split_jsonl)
from ..ui_text import last_message_line
from .base import SessionCapabilities, Snapshot

PROVIDER_ID = "copilot"
ASSISTANT_NAME = "Copilot"
WORKSPACE_FILE = "workspace.yaml"
EVENTS_FILE = "events.jsonl"

#: A reply that has written nothing for this long says so: a Copilot that was
#: closed or crashed mid-reply never writes its end, and a long command writes
#: nothing while it runs. It stays working, so a real finish is still
#: announced; only the detail changes.
STALE_TURN_SECONDS = 10 * 60
#: After this long with nothing written, a reply is taken to have been left
#: behind (Copilot closed mid-reply never writes its end): its status is
#: unknown rather than working for ever.
ABANDONED_TURN_SECONDS = 2 * 60 * 60
#: A permission question waits for you, so it stays needs you far longer
#: than a reply stays working; but a Copilot closed while asking never
#: writes its end either, so after a day its status is unknown rather than
#: needs you for ever (#355 review).
ABANDONED_QUESTION_SECONDS = 24 * 60 * 60
#: A session.error ends a reply only once nothing has followed it for this
#: long. No real Copilot log has shown one yet (#355 review), so a Copilot
#: that recovers and carries on mustn't be announced as finished twice.
ERROR_SETTLE_SECONDS = 30
#: How much of the end of a log is read to find a session's state and its
#: last message. The state is in the last few events.
TAIL_BYTES = 256 * 1024
#: A session folder with a log but no workspace.yaml is skipped this long
#: (Copilot writing the one before the other), then counted as unreadable.
WORKSPACE_GRACE_SECONDS = 60
#: How much of the start of a log is read for a title from the first prompt.
HEAD_BYTES = 1024 * 1024

# Events that start, end or pause a reply, used for the row's state. One
# reply is several model steps, each its own turn_start/turn_end: a step that
# asks for tools, then the next, until one answers without asking (#352).
_TURN_START = "assistant.turn_start"
_STEP_END = "assistant.turn_end"
_REPLY_ENDS = frozenset({"session.shutdown", "abort"})
_ERROR = "session.error"
#: _log_state's answer when the last word was an error: idle once it settles.
_ERRORED = "errored"
_PERMISSION_ASKED = "permission.requested"
_PERMISSION_DONE = "permission.completed"

# Argument fields worth naming in a one-line tool summary, in order.
_TOOL_FIELDS = ("description", "command", "fileName", "path", "file_path", "pattern",
                "url", "query")


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _ms(value) -> int:
    """An ISO-8601 time ("2026-10-23T00:16:46.085Z") in ms, or 0."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if stamp.tzinfo is None:
        return 0
    return max(0, int(stamp.timestamp() * 1000))


# ---------------------------------------------------------------------------
# workspace.yaml
# ---------------------------------------------------------------------------


def _scalar(raw: str) -> Optional[str]:
    """One YAML scalar on one line, or None when it isn't one we can read.

    Copilot writes flat ``key: value`` lines: plain, 'single-quoted' (a quote
    inside is doubled) or "double-quoted" (JSON-style escapes). Anything else,
    such as a block scalar or a value continued on the next line, is not
    guessed at: the caller falls back to the session's first message.
    """
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] == "'":
        if len(raw) < 2 or raw[-1] != "'":
            return None
        inner = raw[1:-1]
        if "'" in inner.replace("''", ""):
            return None
        return inner.replace("''", "'")
    if raw[0] == '"':
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, str) else None
    if raw[0] in "|>&*!{[":
        return None
    # A plain scalar ends at a comment.
    hash_at = raw.find(" #")
    return raw[:hash_at].rstrip() if hash_at >= 0 else raw


def parse_workspace(text: str) -> dict:
    """The top-level ``key: value`` pairs of a workspace.yaml we can read.

    A key whose value can't be read is left out rather than guessed.
    """
    values = {}
    for line in text.splitlines():
        if not line or line[0] in " \t#-":
            continue
        key, sep, rest = line.partition(":")
        if not sep or not key.strip() or key != key.strip():
            continue
        value = _scalar(rest)
        if value is not None:
            values[key] = value
    return values


# ---------------------------------------------------------------------------
# events.jsonl
# ---------------------------------------------------------------------------


def _tool_summary(name: str, title: str, arguments) -> str:
    """A tool call: what it is, then what it's on, first line first.

    The command itself leads, so the messages list says what actually ran;
    Copilot's own description of it ("List files") follows on the next line.
    """
    detail = ""
    if isinstance(arguments, dict):
        for key in _TOOL_FIELDS:
            if _text(arguments.get(key)).strip():
                detail = arguments[key].strip()
                break
        description = _text(arguments.get("description")).strip()
        command = _text(arguments.get("command")).strip()
        if command:
            detail = f"{command}\n{description}" if description else command
    elif isinstance(arguments, str):
        detail = arguments.strip()
        if detail.startswith("*** Begin Patch"):
            # apply_patch: say which files ("Add File: notes.txt"), not the
            # patch's first line, which is the same for every change.
            files = [line[4:].strip() for line in detail.splitlines()
                     if line.startswith("*** ") and line[4:].strip()
                     not in ("Begin Patch", "End Patch")]
            detail = "\n".join(files) or detail
    head = title or name or "Tool"
    if name and title and name not in title:
        head = f"{title} ({name})"
    return f"{head}: {detail}" if detail else head


def _tool_output(data: dict) -> str:
    result = data.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("content", "detailedContent", "output", "text"):
            if _text(result.get(key)):
                return result[key]
    return ""


class CopilotParser:
    """Incremental: ``feed`` lines as events.jsonl grows."""

    def __init__(self) -> None:
        self.transcript = Transcript()
        self._seen: set = set()
        # requestId -> what Copilot asked to do, for a refusal's message.
        self._asked: dict = {}

    def feed(self, lines: Iterable[str]) -> None:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            self.transcript.lines_read += 1
            try:
                event = json.loads(line)
            except ValueError:
                self.transcript.unreadable_lines += 1
                continue
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                self.transcript.unreadable_lines += 1
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                data = {}
            try:
                self._event(event["type"], data, _text(event.get("id")),
                            _text(event.get("timestamp")))
            except (TypeError, AttributeError, KeyError):
                # A known type in a shape we don't expect: counted, not raised.
                self.transcript.unreadable_lines += 1

    def _add(self, kind: str, text: str, timestamp: str, key: str) -> None:
        if not text.strip():
            return
        if key:
            if key in self._seen:
                return
            self._seen.add(key)
        self.transcript.messages.append(ChatMessage(kind, text, timestamp, key,
                                                    assistant_name=ASSISTANT_NAME))

    def _event(self, kind: str, data: dict, event_id: str, timestamp: str) -> None:
        if kind == "user.message":
            # Not transformedContent: that has what Copilot added (the date).
            self.transcript.turns += 1
            self._add(USER, _text(data.get("content")), timestamp, event_id)
        elif kind == "assistant.message":
            if _text(data.get("model")):
                self.transcript.model = data["model"]
            self._add(ASSISTANT, _text(data.get("content")), timestamp, event_id)
        elif kind == "session.model_change":
            if _text(data.get("newModel")):
                self.transcript.model = data["newModel"]
        elif kind == "tool.execution_start":
            call = _text(data.get("toolCallId"))
            self._add(TOOL, _tool_summary(_text(data.get("toolName")),
                                          _text(data.get("toolTitle")), data.get("arguments")),
                      timestamp, f"{call}:call" if call else event_id)
        elif kind == "tool.execution_complete":
            call = _text(data.get("toolCallId"))
            error = data.get("error")
            output = _tool_output(data)
            if not output and isinstance(error, dict) and _text(error.get("message")):
                output = f"Failed: {error['message']}"
            self._add(TOOL_RESULT, output, timestamp, f"{call}:result" if call else event_id)
        elif kind == _PERMISSION_ASKED:
            request = data.get("permissionRequest")
            request = request if isinstance(request, dict) else {}
            what = " ".join(part for part in (
                _text(request.get("intention")).strip(),
                _text(request.get("fileName")).strip()
                or _text(request.get("fullCommandText")).strip()) if part)
            self._asked[_text(data.get("requestId"))] = what
        elif kind == _PERMISSION_DONE:
            result = data.get("result")
            verdict = _text(result.get("kind")) if isinstance(result, dict) else ""
            what = self._asked.pop(_text(data.get("requestId")), "")
            if verdict.startswith("denied"):
                self._add(DENIED, f"Copilot wasn't allowed to: {what}" if what
                          else "Copilot wasn't allowed to go ahead.", timestamp, event_id)
        elif kind == "session.error":
            self._add(ERROR, _text(data.get("message")) or "Copilot reported an error.",
                      timestamp, event_id)
        elif kind == "abort":
            self._add(INTERRUPTED, "Stopped before the turn finished.", timestamp, event_id)


def parse_events(lines: Iterable[str]) -> Transcript:
    parser = CopilotParser()
    parser.feed(lines)
    return parser.transcript


def _read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> List[str]:
    """Whole lines from the end of a log; the first, likely cut, is dropped."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        start = max(0, size - max_bytes)
        handle.seek(start)
        data = handle.read()
    lines = split_jsonl(data)
    return lines[1:] if start > 0 and lines else lines


def _log_state(lines: Iterable[str]) -> str:
    """WORKING, NEEDS_YOU, IDLE, _ERRORED or "status unavailable", from the
    log alone.

    Only an explicit end makes a session idle, as with Codex (#184): idle is
    what announces a finished reply. A step's end counts only when that step
    answered without asking for tools; otherwise Copilot is about to run them
    and take the next step. An error mid-reply gives _ERRORED, which
    _with_detail turns into idle only once nothing has followed it.
    """
    state, asking, tools_asked = STATUS_UNAVAILABLE, set(), False
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind in ("user.message", _TURN_START):
            state, tools_asked = WORKING, False
            if kind == "user.message":
                asking = set()
        elif kind == "assistant.message":
            # Copilot answering after an error has carried on (#355 review).
            if state == _ERRORED:
                state = WORKING
            tools_asked = bool(data.get("toolRequests"))
        elif kind in ("tool.execution_start", "tool.execution_complete"):
            # A step that ran a tool isn't the answer, even when the tail
            # starts after the message that asked for it (a large output).
            state, tools_asked = WORKING, True
        elif kind == _STEP_END:
            if not tools_asked:
                state, asking = IDLE, set()
        elif kind in _REPLY_ENDS:
            state, asking = IDLE, set()
        elif kind == _ERROR:
            # An open permission question still needs you, error or not.
            if state == WORKING and not asking:
                state = _ERRORED
        elif kind == _PERMISSION_ASKED:
            # Asking after an error means Copilot carried on: it's waiting
            # for you, not finished (#355 review).
            if state == _ERRORED:
                state = WORKING
            asking.add(_text(data.get("requestId")))
        elif kind == _PERMISSION_DONE:
            asking.discard(_text(data.get("requestId")))
    if state == WORKING and asking:
        return NEEDS_YOU
    return state


def _with_detail(state: str, written_ms: int, now_ms: Optional[int] = None) -> Tuple[str, str]:
    """The row's (state, detail): the log's state, saying so when a reply
    has been quiet for STALE_TURN_SECONDS, and unknown once it has been
    quiet for ABANDONED_TURN_SECONDS. Needs you is quiet because it's
    waiting for you, so it lasts until ABANDONED_QUESTION_SECONDS (#355
    review)."""
    if state not in (WORKING, NEEDS_YOU, _ERRORED):
        return state, ""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    quiet_ms = now_ms - written_ms if written_ms else 0
    if state == _ERRORED:
        if quiet_ms > ERROR_SETTLE_SECONDS * 1000:
            return IDLE, ""
        state = WORKING
    if state == NEEDS_YOU:
        if quiet_ms > ABANDONED_QUESTION_SECONDS * 1000:
            return STATUS_UNAVAILABLE, "waiting in Copilot for over a day"
        return state, "waiting in Copilot"
    if quiet_ms > ABANDONED_TURN_SECONDS * 1000:
        return STATUS_UNAVAILABLE, "no activity from Copilot for over 2 hours"
    if quiet_ms > STALE_TURN_SECONDS * 1000:
        return state, "no activity from Copilot for 10 minutes"
    return state, ""


def state_from_events(lines: Iterable[str], written_ms: int,
                      now_ms: Optional[int] = None) -> Tuple[str, str]:
    """(state, detail) from the end of a session's log."""
    return _with_detail(_log_state(lines), written_ms, now_ms)


class CopilotReader:
    """One session's log, read incrementally as Copilot appends to it."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._parser = CopilotParser()
        self._offset = 0
        self._identity = None

    @property
    def transcript(self) -> Transcript:
        return self._parser.transcript

    def refresh(self) -> bool:
        """Read what's new since last time; True if the chat changed."""
        stat = os.stat(self.path)
        identity = getattr(stat, "st_ino", 0) or None
        if stat.st_size < self._offset or (identity and self._identity
                                           and identity != self._identity):
            # Rewritten or replaced (rewind, say): start again.
            self._parser, self._offset = CopilotParser(), 0
        self._identity = identity
        if stat.st_size == self._offset:
            return False
        with open(self.path, "rb") as handle:
            handle.seek(self._offset)
            data = handle.read()
        end = data.rfind(b"\n")
        if end < 0:
            return False  # a line still being written
        before = (len(self.transcript.messages), self.transcript.model,
                  self.transcript.unreadable_lines)
        self._parser.feed(split_jsonl(data[:end]))
        self._offset += end + 1
        return before != (len(self.transcript.messages), self.transcript.model,
                          self.transcript.unreadable_lines)


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


class CopilotProvider:
    provider_id = PROVIDER_ID
    MAX_LAST_MESSAGES = 512

    def __init__(self, sessions_dir: Optional[Path] = None, *, execution: bool = False,
                 on_update=lambda state: None, runtime=None):
        self._sessions_dir = sessions_dir
        #: Runs the chats The Chat Place owns (copilot_runtime), if execution
        #: is on; without it, Copilot chats are read only. Built lazily: it
        #: starts no process until a chat is created or opened.
        self.runtime = runtime
        #: Chats once listed from their own log, as last listed: if the
        #: folder goes, they aren't listed again from the runtime alone.
        self._listed_before: dict = {}
        if execution and runtime is None:
            from .copilot_runtime import CopilotChatRuntime
            self.runtime = CopilotChatRuntime(on_update, sessions_dir=sessions_dir)
        # native id -> _Entry, so an unchanged session isn't re-read on every
        # refresh of the list, and a changed log is read once for its state,
        # title and last message together.
        self._entries: dict = {}
        self._last_messages: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir or platform_paths.copilot_sessions_dir()

    def close(self) -> None:
        self.forget_last_messages()
        if self.runtime is not None:
            self.runtime.close()

    def _check(self, session: SessionInfo) -> None:
        if (session.provider != self.provider_id
                or not platform_paths.is_safe_id(session.cli_session_id)):
            raise ValueError("Not a Copilot session")

    def _events_path(self, native_id: str) -> Path:
        return self.sessions_dir / native_id / EVENTS_FILE

    # -- discovery ----------------------------------------------------------

    def collect(self) -> Snapshot:
        snapshot = Snapshot()
        root = self.sessions_dir
        try:
            folders = sorted(os.scandir(root), key=lambda entry: entry.name)
        except FileNotFoundError:
            return snapshot  # Copilot CLI hasn't been used here
        entries = {}
        for entry in folders:
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if not platform_paths.is_safe_id(entry.name):
                continue
            try:
                info = self._session(Path(entry.path), entry.name, entries)
            except (OSError, UnicodeDecodeError, ValueError):
                snapshot.unreadable_files += 1
                continue
            if info is not None:
                snapshot.sessions.append(info)
        with self._lock:
            self._entries = entries
        if self.runtime is not None:
            self._overlay(snapshot)
        return snapshot

    def _overlay(self, snapshot: Snapshot) -> None:
        """What the runtime knows is fresher than the log: a running turn's
        state, and a new chat with no log yet (listed from the runtime, and
        made the app's own by the window's record)."""
        listed = {info.cli_session_id: info for info in snapshot.sessions}
        self._listed_before.update((native_id, replace(info)) for native_id, info in listed.items())
        for native_id, cwd in self.runtime.known():
            state = self.runtime.snapshot(native_id)
            if state is None:
                continue
            info = listed.get(native_id)
            before = self._listed_before.get(native_id)
            if info is None and before is not None:
                if not (self.sessions_dir / native_id).exists():
                    continue  # listed from its log before, and its folder was removed
                # Only missing from this scan: a read that failed while
                # Copilot wrote the log (a sharing violation on Windows) mustn't
                # drop a running chat or move it, so it's listed as it last
                # was, title and times included (Kelly, #372).
                info = replace(before)
                snapshot.sessions.append(info)
            if info is None:
                # Titled from the window's record when there is one; a chat
                # whose record couldn't be saved stays read-only, named.
                info = SessionInfo("external", f"{PROVIDER_ID}:{native_id}", "New Copilot chat",
                                   cwd, native_id, provider=PROVIDER_ID)
                snapshot.sessions.append(info)
            info.state = state.state
            if state.problem:
                info.detail = state.problem

    def _session(self, folder: Path, native_id: str, entries: dict) -> Optional[SessionInfo]:
        events = folder / EVENTS_FILE
        try:
            stat = os.stat(events)
        except FileNotFoundError:
            return None  # stopped before its first prompt: nothing to read
        workspace_path = folder / WORKSPACE_FILE
        try:
            workspace_stat = os.stat(workspace_path)
        except FileNotFoundError:
            if time.time() - stat.st_mtime < WORKSPACE_GRACE_SECONDS:
                return None  # Copilot is still making it: listed once it has
            raise  # gone for good: counted as unreadable, not silently dropped
        stamp = (stat.st_size, stat.st_mtime_ns, workspace_stat.st_mtime_ns)
        with self._lock:
            entry = self._entries.get(native_id)
        if entry is None or entry.stamp != stamp:
            entry = _read_entry(native_id, events, workspace_path, stat, stamp, entry)
        entries[native_id] = entry
        if entry.title is None:
            return None  # no prompt was ever sent (refused at sign-in, say)
        # The detail depends on the clock, so it's worked out every time.
        state, detail = _with_detail(entry.state, entry.written_ms)
        return SessionInfo(entry.source, f"{PROVIDER_ID}:{native_id}", entry.title,
                           entry.cwd, native_id, provider=PROVIDER_ID,
                           state=state, detail=detail, created_ms=entry.created_ms,
                           last_activity_ms=entry.updated_ms)

    # -- the optional Last message column -----------------------------------

    def forget_last_messages(self) -> None:
        with self._lock:
            self._last_messages.clear()

    def cached_last_messages(self, sessions: Iterable[SessionInfo]) -> None:
        with self._lock:
            cache = dict(self._last_messages)
        for info in sessions:
            cached = cache.get(info.identity)
            info.last_message = cached[1] if cached and cached[0] == info.last_activity_ms else ""

    def fill_last_messages(self, sessions: Iterable[SessionInfo]) -> None:
        """Only while the column is shown, and only once per change: the line
        is kept on the session's entry until its log changes."""
        for info in sessions:
            with self._lock:
                entry = self._entries.get(info.cli_session_id)
            if entry is None or entry.updated_ms != info.last_activity_ms:
                # A newer collect() has replaced this row's entry: its line
                # would be for later activity than the row says.
                info.last_message = ""
                continue
            if entry.last_line is None:
                try:
                    transcript = parse_events(_read_tail(self._events_path(info.cli_session_id)))
                except OSError:
                    info.last_message = ""
                    continue
                last = next((m for m in reversed(transcript.messages)
                             if m.kind in (USER, ASSISTANT) and m.text.strip()), None)
                entry.last_line = last_message_line(last.label, last.text) if last else ""
            info.last_message = entry.last_line
            with self._lock:
                self._last_messages[info.identity] = (info.last_activity_ms, info.last_message)
                self._last_messages.move_to_end(info.identity)
                while len(self._last_messages) > self.MAX_LAST_MESSAGES:
                    self._last_messages.popitem(last=False)

    # -- reading ------------------------------------------------------------

    def capabilities(self, session: SessionInfo) -> SessionCapabilities:
        self._check(session)
        # Only a chat The Chat Place owns can be sent to, and only with the
        # runtime; the runtime checks again (client name, hooks, billing,
        # use elsewhere) before anything is sent. Others are read only.
        owned = self.runtime is not None and session.is_own
        return SessionCapabilities(send=owned, approvals=owned)

    # -- owned chats (copilot_runtime) ---------------------------------------

    def can_create(self) -> bool:
        """Offered in New Session only once Copilot CLI is installed."""
        return self.runtime is not None and platform_paths.find_copilot() is not None

    def create_owned(self, cwd: str, title: str):
        """A new chat, as the window's record of it. Waits for Copilot to
        start, so call it off the UI thread."""
        from ..own_store import OwnSession
        native_id = self.runtime.create(cwd)
        return OwnSession(native_id, title, cwd, provider=PROVIDER_ID, started=True,
                          created_ms=int(time.time() * 1000))

    def owned_ready(self, record) -> bool:
        return self.runtime is not None and self.runtime.ready(record.cli_session_id)

    def prepare_owned(self, record) -> None:
        """Open a chat from the window's own record of it (the only place
        the runtime is told a chat is the app's), in the folder recorded."""
        self.runtime.adopt(record.cli_session_id)
        self.runtime.prepare(record.cli_session_id, record.cwd)

    def send_owned(self, record, text: str) -> bool:
        return self.runtime.send(record.cli_session_id, text)

    def stop_owned(self, record):
        """A ``copilot_runtime.Stopped``: the messages that were still
        waiting, given back unsent, and what Stop found."""
        return self.runtime.stop(record.cli_session_id)

    def answer_owned(self, record, request, result) -> None:
        """``result`` is ``request.approval_answer(choice)``, as the window
        makes it for every provider; the runtime is told the choice."""
        option = ((result or {}).get("outcome") or {}).get("optionId")
        choice = next((kind for kind, option_id in request.choices if option_id == option), None)
        if choice is None:
            raise ValueError("Unsupported Copilot approval choice")
        self.runtime.answer(record.cli_session_id, request.request_id, choice)

    def has_history(self, session: SessionInfo) -> bool:
        self._check(session)
        return self._events_path(session.cli_session_id).is_file()

    def open_reader(self, session: SessionInfo) -> Optional[CopilotReader]:
        self._check(session)
        path = self._events_path(session.cli_session_id)
        return CopilotReader(path) if path.is_file() else None

    def last_reply(self, session: SessionInfo) -> str:
        self._check(session)
        try:
            transcript = parse_events(_read_tail(self._events_path(session.cli_session_id)))
        except OSError:
            return ""
        reply = transcript.last_reply()
        return reply.text if reply else ""


def _read_head(path: Path, max_bytes: Optional[int] = None) -> Tuple[List[str], bool]:
    """Whole lines from the start of a log, and whether that was all of it."""
    max_bytes = HEAD_BYTES if max_bytes is None else max_bytes
    with open(path, "rb") as handle:
        data = handle.read(max_bytes + 1)
    whole = len(data) <= max_bytes
    lines = split_jsonl(data[:max_bytes])
    return (lines if whole or not lines else lines[:-1]), whole


class _Entry:
    """What one session's files said, and the stamp they said it at."""

    __slots__ = ("stamp", "source", "title", "title_from_prompt", "cwd", "created_ms",
                 "updated_ms", "written_ms", "state", "last_line")

    def __init__(self, **values):
        for name in self.__slots__:
            setattr(self, name, values[name])


def _read_entry(native_id: str, events: Path, workspace_path: Path, stat,
                stamp: tuple, previous: Optional[_Entry] = None) -> _Entry:
    workspace = parse_workspace(workspace_path.read_text(encoding="utf-8"))
    if workspace.get("id", native_id) != native_id:
        raise ValueError("Copilot session folder and id disagree")
    tail = _read_tail(events)
    title = workspace.get("name", "").strip()
    from_prompt = False
    if not title and previous is not None and previous.title_from_prompt:
        # The first prompt never changes: don't read the head again.
        title, from_prompt = previous.title, True
    elif not title:
        from_prompt = True
        head, whole = _read_head(events)
        first = next((m for m in parse_events(head).messages if m.kind == USER), None)
        if first is not None:
            title = first.text.strip()
        elif whole:
            title = None  # no prompt was ever sent
    if title is not None:
        title = title.split("\n", 1)[0].strip() or "Untitled session"
    written_ms = stat.st_mtime_ns // 1_000_000
    created = _ms(workspace.get("created_at"))
    updated = max(_ms(workspace.get("updated_at")), written_ms) or created
    # Kind is where the session started (CLAUDE.md): Copilot CLI in a
    # terminal, as the Codex adapter files its "cli" chats.
    source = TERMINAL if workspace.get("client_name") == "github/cli" else "external"
    return _Entry(stamp=stamp, source=source, title=title,
                  title_from_prompt=from_prompt and title not in (None, "Untitled session"),
                  cwd=workspace.get("cwd", ""), created_ms=created, updated_ms=updated,
                  written_ms=written_ms, state=_log_state(tail),
                  last_line=None)  # worked out only if the column is shown
