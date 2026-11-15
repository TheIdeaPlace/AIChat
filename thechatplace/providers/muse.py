"""Muse Code session discovery and point-in-time read-only history.

Uses session/list and session/read, never session/resume or raw log files.
External history is not evidence of ownership, a live turn, or permission to
send. Experimental schema compatibility is enforced by the connection.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import re
import threading
import time
import uuid

from ..own_store import OwnSession
from ..sessions import SessionInfo, STATUS_UNAVAILABLE
from ..titles import clean_title
from ..transcript import (Transcript, ChatMessage, USER, ASSISTANT, TOOL,
                          TOOL_RESULT, THINKING, ERROR, EVENT, COMPACTION)
from ..ui_text import last_message_line
from .base import Snapshot, SessionCapabilities
from .muse_connection import ExecutionConnection, ManagedMuseConnection, MuseHistoryError, MuseRequestError


def _text(value):
    return value if isinstance(value, str) else ""


def _identity(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and not any(ord(char) < 32 for char in value))


def _milliseconds(value):
    if not isinstance(value, str):
        return 0
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0, int(stamp.timestamp() * 1000)) if stamp.tzinfo else 0
    except (ValueError, OverflowError, OSError):
        return 0


def session_from_muse(value):
    if not isinstance(value, dict) or not _identity(value.get("sessionId")):
        raise MuseHistoryError("Muse Code returned unreadable session metadata. Try refreshing.")
    ident = value["sessionId"]
    title = next((_text(value.get(field)).strip() for field in ("name", "title", "firstUserPrompt")
                  if _text(value.get(field)).strip()), "Untitled Muse Code chat")
    title = clean_title("".join(char if char.isprintable() else " " for char in title))
    title = title or "Untitled Muse Code chat"
    return SessionInfo(source="app_server", key=f"muse:{ident}", title=title,
                       cwd=_text(value.get("workspaceRoot")), cli_session_id=ident,
                       provider="muse", provider_label="Muse Code",
                       created_ms=_milliseconds(value.get("createdAt")),
                       last_activity_ms=_milliseconds(value.get("updatedAt")),
                       # A stored fold's idle/notLoaded is not a live assertion
                       # about another host. Never invent finished turns from it.
                       state=STATUS_UNAVAILABLE)


def _history_items(result):
    history = result.get("history")
    if not isinstance(history, dict):
        raise MuseHistoryError("Muse Code didn't return readable history. Try refreshing.")
    mode = history.get("mode")
    if mode == "inline" and history.get("snapshot") is None:
        items = history.get("items")
    elif mode == "snapshot" and history.get("items") is None:
        snapshot = history.get("snapshot")
        if (not isinstance(snapshot, dict) or type(snapshot.get("schemaVersion")) is not int
                or snapshot["schemaVersion"] != 1
                or snapshot.get("viewCursor") != result.get("viewCursor")
                or not isinstance(snapshot.get("state"), dict)):
            raise MuseHistoryError("Muse Code returned an unsupported history snapshot. Read it in Muse Code.")
        items = snapshot["state"].get("items")
    else:
        # An anchored snapshot can omit older items. Do not silently present
        # that tail as complete, or attach to a session to recover its history.
        raise MuseHistoryError("Complete history isn't available from Muse Code. Read it in Muse Code or try refreshing.")
    if not isinstance(items, list):
        raise MuseHistoryError("Muse Code returned unreadable history. Try refreshing.")
    return items


def transcript_from_muse(result, native_id):
    if (not isinstance(result, dict) or not isinstance(result.get("session"), dict)
            or result["session"].get("sessionId") != native_id
            or not isinstance(result.get("viewCursor"), str) or not result["viewCursor"]):
        raise MuseHistoryError("Muse Code didn't return the requested chat's history. Try refreshing.")
    items = _history_items(result)
    transcript = Transcript(model=_text(result["session"].get("modelId")))
    folded = OrderedDict()
    for item in items:
        if (not isinstance(item, dict) or not _identity(item.get("itemId"))
                or not isinstance(item.get("kind"), str)
                or type(item.get("revision")) is not int or item["revision"] < 0
                or not isinstance(item.get("status"), str)):
            transcript.unreadable_lines += 1
            continue
        old = folded.get(item["itemId"])
        if old is None or item["revision"] > old["revision"]:
            folded[item["itemId"]] = item
        elif item != old:
            # A conflicting or regressing revision cannot replace newer data.
            transcript.unreadable_lines += 1
    for ident, item in folded.items():
        if item.get("retracted") is True:
            continue
        kind = item["kind"]
        key = f"muse:{native_id}:{ident}"
        stamp = _text(item.get("recordedAt"))
        if stamp:
            if not transcript.first_time:
                transcript.first_time = stamp
            transcript.last_time = stamp
        def add(message_kind, text, suffix=""):
            transcript.messages.append(ChatMessage(message_kind, text, timestamp=stamp,
                key=key + suffix, assistant_name="Muse Code", turn=transcript.turns))
        if kind in ("userMessage", "agentMessage"):
            if not isinstance(item.get("text"), str):
                transcript.unreadable_lines += 1
                continue
            if kind == "userMessage":
                transcript.turns += 1
            add(USER if kind == "userMessage" else ASSISTANT, item["text"])
        elif kind in ("toolCall", "userShell"):
            tool = _text(item.get("tool")) or ("Shell" if kind == "userShell" else "Tool")
            args = _text(item.get("args")) or _text(item.get("commandText"))
            add(TOOL, f"{tool}: {args}" if args else tool)
            if isinstance(item.get("visibleOutput"), str):
                add(TOOL_RESULT, item["visibleOutput"], ":output")
        elif kind == "reasoning":
            summary = item.get("summary")
            text = _text(item.get("text"))
            if not text and isinstance(summary, list) and all(isinstance(s, str) for s in summary):
                text = "\n".join(summary)
            if text:
                add(THINKING, text)
        elif kind == "systemNotice" and isinstance(item.get("fallbackText"), str):
            add(ERROR if item.get("noticeSeverity") == "error" else EVENT, item["fallbackText"])
        elif kind == "reminderChild" and isinstance(item.get("fallbackText"), str):
            # A reminder's child summary is activity, not unreadable content.
            add(EVENT, item["fallbackText"])
        elif kind == "compaction":
            add(COMPACTION, _text(item.get("fallbackText")) or "Muse Code compacted the conversation.")
        else:
            # The wire's fallbackText is explicitly intended for unknown kinds.
            # Never dump an unrecognized object as JSON into the conversation.
            fallback = _text(item.get("fallbackText"))
            if fallback:
                add(EVENT, fallback)
            transcript.unreadable_lines += 1
        if item.get("truncated") is True:
            transcript.unreadable_lines += 1
    transcript.lines_read = len(items)
    if transcript.unreadable_lines:
        transcript.messages.append(ChatMessage(ERROR,
            "Some Muse Code history items couldn't be displayed completely. Read the full history in Muse Code.",
            key=f"muse:{native_id}:history-warning", assistant_name="Muse Code"))
    return transcript


class MuseReader:
    def __init__(self, connection, native_id, *, closed=None, on_read=None):
        self.connection, self.native_id = connection, native_id
        self.transcript = Transcript()
        self._closed = closed or threading.Event()
        self._on_read = on_read
        self._loaded = False

    def refresh(self):
        if self._closed.is_set():
            raise MuseHistoryError("Muse Code history is closed.")
        result = self.connection.request("session/read", {"sessionId": self.native_id, "excludeItems": False})
        candidate = transcript_from_muse(result, self.native_id)
        if self._closed.is_set():
            raise MuseHistoryError("Muse Code history is closed.")
        changed = not self._loaded or candidate != self.transcript
        if changed:
            self.transcript = candidate
        self._loaded = True
        if self._on_read is not None:
            self._on_read(self.native_id, candidate)
        return changed


class MuseProvider:
    provider_id = "muse"
    MAX_PAGES = 1000
    DISCOVERY_SECONDS = 30.

    def __init__(self, connection=None, *, executable=None, execution=None):
        self.connection = connection if connection is not None else ManagedMuseConnection(executable)
        self._execution = execution
        self._owned = MuseOwnedChats(execution) if execution is not None else None
        self._closed = threading.Event()
        self._previews = OrderedDict()
        self._preview_lock = threading.Lock()

    def _check(self, session):
        if self._closed.is_set():
            raise MuseHistoryError("Muse Code history is closed.")
        if session.provider != "muse" or not _identity(session.cli_session_id):
            raise MuseHistoryError("Not a Muse Code session.")

    def collect(self):
        if self._closed.is_set():
            raise MuseHistoryError("Muse Code history is closed.")
        refresh = getattr(self.connection, "refresh_catalog", None)
        if callable(refresh):
            refresh()
        sessions, identities, cursors = [], set(), set()
        cursor = None
        deadline = time.monotonic() + self.DISCOVERY_SECONDS
        for _ in range(self.MAX_PAGES):
            if self._closed.is_set() or time.monotonic() >= deadline:
                raise MuseHistoryError("Muse Code session discovery didn't finish. Try refreshing.")
            params = {"limit": 200}
            if cursor is not None:
                params["cursor"] = cursor
            result = self.connection.request("session/list", params)
            if (not isinstance(result, dict) or not isinstance(result.get("sessions"), list)
                    or "nextCursor" not in result or len(result["sessions"]) > 200):
                raise MuseHistoryError("Muse Code returned an incomplete session list. Try refreshing.")
            for raw in result["sessions"]:
                info = session_from_muse(raw)
                if info.identity not in identities:
                    identities.add(info.identity)
                    sessions.append(info)
            cursor = result["nextCursor"]
            if cursor is None:
                if self._closed.is_set():
                    raise MuseHistoryError("Muse Code history is closed.")
                return Snapshot(sessions=sessions)
            if not isinstance(cursor, str) or not cursor or len(cursor) > 8192 or cursor in cursors:
                raise MuseHistoryError("Muse Code returned an incomplete session list. Try refreshing.")
            cursors.add(cursor)
        raise MuseHistoryError("Muse Code's session list is too large to finish. Read it in Muse Code.")

    def capabilities(self, session):
        self._check(session)
        # Foreign chats stay read-only even when execution exists; only
        # chats started here can send, stop, or answer permissions.
        enabled = self._owned is not None and session.is_own
        return SessionCapabilities(send=enabled, approvals=enabled, steer=enabled)

    def execution_available(self):
        return self._owned is not None and self._owned.available()

    def can_create(self):
        return self.execution_available()

    def turn_in_flight_owned(self, record):
        """Whether a turn the window started is still using this chat."""
        if (not isinstance(record, OwnSession) or record.provider != "muse"
                or not _identity(record.cli_session_id)):
            raise MuseHistoryError("Not a Muse Code chat started here.")
        return self._owned is not None and self._owned.in_flight(record.cli_session_id)

    def owned_ready(self, record):
        if (not isinstance(record, OwnSession) or record.provider != "muse"
                or not _identity(record.cli_session_id)):
            raise MuseHistoryError("Not a Muse Code chat started here.")
        return self.execution_available()

    def prepare_owned(self, record):
        if not self.owned_ready(record):
            raise MuseHistoryError("Muse Code execution is unavailable. Check its installation and refresh.")
        return self._owned_chats().prepare(record)

    def set_owned_persistence(self, callback):
        self._owned_chats().set_persistence(callback)

    def poll_owned(self, record):
        return self._owned_chats().poll(record)

    def _owned_chats(self):
        if self._closed.is_set() or self._owned is None or not self._owned.available():
            raise MuseHistoryError("Muse Code execution is unavailable. Check its installation and refresh.")
        return self._owned

    def create_owned(self, cwd, title):
        if self._closed.is_set():
            raise MuseHistoryError("Muse Code history is closed.")
        return self._owned_chats().create(cwd, title)

    def send_owned(self, record, text, *, idempotency_key=None):
        return self._owned_chats().send(record, text, idempotency_key=idempotency_key)

    def settle_owned(self, record):
        if self._owned is not None:
            self._owned.settle(record)

    def prepare_settings_owned(self, record):
        """This chat's models for Change Model: (id, label, active) each."""
        return self._owned_chats().models(record)

    def set_model_owned(self, record, model_id):
        return self._owned_chats().set_model(record, model_id)

    def steer_owned(self, record, text):
        return self._owned_chats().steer(record, text)

    def stop_owned(self, record):
        return self._owned_chats().stop(record)

    def pending_approvals_owned(self, record):
        return self._owned_chats().pending_approvals(record)

    def answer_owned(self, record, approval, decision, answers=None):
        return self._owned_chats().answer(record, approval, decision, answers)

    def has_history(self, session):
        self._check(session)
        return True

    def open_reader(self, session):
        self._check(session)
        connection = self._execution if session.is_own and self._execution is not None else self.connection
        return MuseReader(connection, session.cli_session_id,
                          closed=self._closed, on_read=self._remember)

    def last_reply(self, session):
        reader = self.open_reader(session)
        reader.refresh()
        reply = reader.transcript.last_reply()
        return reply.text if reply else ""

    def _remember(self, ident, transcript):
        message = next((m for m in reversed(transcript.messages) if m.kind in (USER, ASSISTANT)), None)
        with self._preview_lock:
            self._previews[ident] = last_message_line(message.label, message.text) if message else ""
            self._previews.move_to_end(ident)
            while len(self._previews) > 128:
                self._previews.popitem(last=False)

    def cached_last_messages(self, sessions):
        with self._preview_lock:
            for session in sessions:
                session.last_message = self._previews.get(session.cli_session_id, "")

    def forget_last_messages(self):
        with self._preview_lock:
            self._previews.clear()

    def close(self):
        self._closed.set()
        self.connection.close()
        execution = getattr(self, "_execution", None)
        if execution is not None:
            execution.close()


@dataclass(frozen=True)
class MuseApproval:
    """A native request, with the host's exact choice and question identities."""
    native_id: str
    request_id: str
    kind: str
    summary: str
    details: str = ""
    choices: tuple = ()
    questions: tuple = ()
    requirement: dict = field(default_factory=dict)


def approval_decision_text(approval: MuseApproval, decision: str) -> str:
    choice = next((c for c in approval.choices if c.get("choiceId") == decision), {})
    allowed = decision == "allow" or choice.get("decision") == "approved" or decision == "allow_once"
    head = "Allowed the request" if allowed else "Denied the request"
    if approval.kind == "question":
        head = "Answered the question" if decision != "deny" else "Cancelled the question"
    lines = [head, f"Summary: {approval.summary}"]
    if approval.details:
        lines.append(f"Details: {approval.details}")
    return "\n".join(lines)


def _request_array(result: dict, name: str) -> list:
    value = result.get(name)
    if not isinstance(value, list) or len(value) > 200:
        raise MuseHistoryError("Muse Code returned unreadable pending requests. Try refreshing.")
    return value


def _request_identity(raw: dict, native_id: str, name: str) -> str:
    if (not isinstance(raw, dict) or raw.get("sessionId") != native_id
            or not _identity(raw.get(name))):
        raise MuseHistoryError("Muse Code returned a request for an unreadable or different chat.")
    return raw[name]


def _choices(raw: dict) -> tuple:
    choices = _request_array(raw, "availableChoices")
    seen = set()
    for choice in choices:
        if (not isinstance(choice, dict) or not _identity(choice.get("choiceId"))
                or not isinstance(choice.get("label"), str) or not choice["label"].strip()
                or not isinstance(choice.get("scope"), str) or choice["choiceId"] in seen
                or not isinstance(choice.get("decision"), str)):
            raise MuseHistoryError("Muse Code returned an unreadable permission choice.")
        seen.add(choice["choiceId"])
    if not choices:
        raise MuseHistoryError("Muse Code supplied no permission choices. Stop the turn or check Muse Code.")
    return tuple(dict(choice) for choice in choices)


def approvals_from_muse(result: dict, native_id: str) -> tuple:
    """Parse the exact 1.4.20 approval/listPending envelope, failing closed."""
    if not isinstance(result, dict):
        raise MuseHistoryError("Muse Code returned unreadable pending requests.")
    requests = []
    for raw in _request_array(result, "approvals"):
        ident = _request_identity(raw, native_id, "approvalId")
        requirement = raw.get("currentRequirementId")
        subject = raw.get("subject")
        if (not isinstance(requirement, dict) or requirement.get("approvalId") != ident
                or type(requirement.get("sourceIndex")) is not int
                or requirement["sourceIndex"] < 0 or not isinstance(subject, dict)
                or not isinstance(subject.get("kind"), str)):
            raise MuseHistoryError("Muse Code returned an unreadable permission requirement.")
        summary = next((_text(subject.get(key)).strip() for key in
                        ("command", "path", "host", "target", "toolName")
                        if _text(subject.get(key)).strip()), _text(raw.get("toolName")))
        if not summary:
            raise MuseHistoryError("Muse Code asked permission without readable details.")
        requests.append(MuseApproval(native_id, ident, "approval", summary,
            _text(raw.get("rawArgs")), _choices(raw), requirement=dict(requirement)))
    for raw in _request_array(result, "userInputs"):
        ident = _request_identity(raw, native_id, "userInputId")
        questions = _request_array(raw, "questions")
        seen = set()
        for question in questions:
            if (not isinstance(question, dict) or not _identity(question.get("id"))
                    or question["id"] in seen or not _text(question.get("question")).strip()
                    or not isinstance(question.get("selection"), dict)
                    or question["selection"].get("mode") not in ("single", "multiple")):
                raise MuseHistoryError("Muse Code returned an unreadable question.")
            seen.add(question["id"])
            options = _request_array(question, "options")
            labels = [option.get("label") if isinstance(option, dict) else None for option in options]
            if any(not isinstance(label, str) or not label for label in labels) or len(set(labels)) != len(labels):
                raise MuseHistoryError("Muse Code returned unreadable question choices.")
            selection = question["selection"]
            for name in ("minSelections", "maxSelections"):
                if name in selection and (type(selection[name]) is not int or selection[name] < 0):
                    raise MuseHistoryError("Muse Code returned unreadable question selection bounds.")
        if not questions:
            raise MuseHistoryError("Muse Code supplied an empty question.")
        requests.append(MuseApproval(native_id, ident, "question",
            "\n".join(q["question"] for q in questions), questions=tuple(questions)))
    return tuple(requests)



def _command_id() -> str:
    # UUIDv7 is the native command identity, not a JSON-RPC request id.
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    # Source/build environments before Python 3.14 still need a valid v7.
    import secrets
    bits = ((time.time_ns() // 1_000_000) << 80) | (7 << 76)
    bits |= secrets.randbits(12) << 64 | (2 << 62) | secrets.randbits(62)
    return str(uuid.UUID(int=bits))


def _valid_command(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 7 and str(parsed) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _failure_kind(result: dict) -> str:
    """Why the last turn failed, as Muse's error kind ("modelError") only:
    its message is the host's own text (with a request id), never shown."""
    last = result.get("lastTurn") if isinstance(result, dict) else None
    error = last.get("error") if isinstance(last, dict) and last.get("terminal") == "failed" else None
    kind = error.get("kind") if isinstance(error, dict) else None
    return kind if isinstance(kind, str) and re.fullmatch(r"[A-Za-z]{1,40}", kind) else ""


class MuseSaveError(MuseHistoryError):
    """The app couldn't save a turn's state. Said as itself, not as Muse
    being unavailable: Muse may be fine (a refused save once hid that)."""


class MuseOwnedChats:
    """Owned lifecycle using verified wire shapes and confirmed native state.

    A command is durably recorded before it can leave the process. Unknown
    outcomes remain unknown until native state proves what happened; neither
    a quiet transcript nor a failed Stop is evidence of completion.
    """
    PROFILE = ":ask-me"

    def __init__(self, execution):
        self.execution = execution
        self._lock = threading.RLock()
        self._in_flight = {}
        self._records = {}
        self._persistence = None
        self._pending_events = []

    def set_persistence(self, callback) -> None:
        if not callable(callback):
            raise TypeError("Muse owned persistence must be callable")
        self._persistence = callback

    def _check(self, record: OwnSession) -> None:
        if (not isinstance(record, OwnSession) or record.provider != "muse"
                or not _identity(record.cli_session_id)):
            raise MuseHistoryError("Not a Muse Code chat started here.")

    def available(self) -> bool:
        # A managed host can recover after its child exits. Its own _closed
        # flag, unlike .closed, distinguishes app shutdown from child failure.
        return self.execution is not None and not getattr(self.execution, "_closed", self.execution.closed)

    def _require_available(self) -> None:
        if not self.available():
            raise MuseHistoryError("Muse Code execution is unavailable. Check its installation and refresh.")

    def _profile(self) -> str:
        result = self.execution.request("permissionProfile/list", {})
        profiles = _request_array(result, "profiles")
        if not any(isinstance(p, dict) and p.get("profileId") == self.PROFILE
                   and p.get("available") is True and p.get("reviewerAuthority") == "human"
                   for p in profiles):
            raise MuseHistoryError("Muse Code's Ask Me permission profile is unavailable. Sending is disabled.")
        return self.PROFILE

    def _session(self, result: dict, native_id: str) -> dict:
        session = result.get("session") if isinstance(result, dict) else None
        if not isinstance(session, dict) or session.get("sessionId") != native_id:
            raise MuseHistoryError("Muse Code didn't return the requested owned chat.")
        return session

    def _policy(self, session: dict) -> None:
        mode, policy = session.get("approvalMode"), session.get("permissionState")
        if (not isinstance(mode, dict) or mode.get("mode") != "promptUnmatched"
                or not isinstance(policy, dict) or policy.get("profileId") != self.PROFILE
                or policy.get("reviewerAuthority") != "human"
                or policy.get("reviewerAttached") is not True):
            raise MuseHistoryError("Muse Code couldn't confirm human permission review. Sending is disabled.")

    def create(self, cwd: str, title: str) -> OwnSession:
        with self._lock:
            self._require_available()
            if not isinstance(cwd, str) or not cwd.strip():
                raise MuseHistoryError("A Muse Code chat needs a working folder.")
            if not isinstance(title, str) or not title.strip():
                raise MuseHistoryError("A Muse Code chat needs a title.")
            profile = self._profile()
            result = self.execution.request("session/start", {
                "commandId": _command_id(), "workspaceRoot": cwd,
                "approvalMode": "promptUnmatched", "permissionProfile": profile})
            session = result.get("session") if isinstance(result, dict) else None
            native_id = session.get("sessionId") if isinstance(session, dict) else None
            if not _identity(native_id) or not _identity(result.get("viewCursor")):
                raise MuseHistoryError("Muse Code didn't start a readable chat. Try again.")
            now_ms = int(time.time() * 1000)
            record = OwnSession(native_id, title, cwd, provider="muse",
                created_ms=now_ms, last_activity_ms=now_ms, started=True)
            self._records[native_id] = record
            # Preserve the created identity even if the host cannot establish
            # policy: prepare/send will explain the refusal without losing it.
            return record

    def _read(self, record: OwnSession) -> dict:
        result = self.execution.request("session/read", {
            "sessionId": record.cli_session_id, "excludeItems": True})
        self._session(result, record.cli_session_id)
        return result

    def prepare(self, record: OwnSession) -> bool:
        with self._lock:
            self._check(record)
            self._require_available()
            self._records[record.cli_session_id] = record
            result = self._read(record)
            if result["session"].get("status") == "notLoaded":
                result = self.execution.request("session/resume", {
                    "commandId": _command_id(), "sessionId": record.cli_session_id,
                    "permissionProfile": self._profile(), "excludeItems": True,
                    "autoRecover": False})
            self._policy(self._session(result, record.cli_session_id))
            self._reconcile(record, result)
            return True

    def _remember(self, record: OwnSession, command: dict, *, required: bool = False) -> None:
        previous = record.provider_options.get("muse_command")
        record.provider_options["muse_command"] = dict(command)
        if self._persistence is None:
            if required:
                if previous is None:
                    record.provider_options.pop("muse_command", None)
                else:
                    record.provider_options["muse_command"] = previous
                raise MuseSaveError("Muse Code couldn't save this turn before sending. Try again.")
            return
        try:
            self._persistence(record)
        except Exception:
            if previous is None:
                record.provider_options.pop("muse_command", None)
            else:
                record.provider_options["muse_command"] = previous
            raise MuseSaveError("The Chat Place couldn't save this Muse Code turn's state. Check the chat before sending again.") from None

    def _saved(self, record: OwnSession) -> dict:
        value = record.provider_options.get("muse_command")
        return dict(value) if isinstance(value, dict) else {}

    def _reconcile(self, record: OwnSession, result: dict) -> str:
        session = self._session(result, record.cli_session_id)
        native_id = record.cli_session_id
        command = self._in_flight.get(native_id) or self._saved(record)
        active = session.get("activeTurnId")
        status = session.get("status")
        if status in ("running", "needsInput") and _identity(active):
            state = "waiting" if status == "needsInput" else "running"
            if command.get("state") == "cancelling":
                state = "cancelling"
            if command:
                command = {**command, "turn_id": active, "state": state}
                self._in_flight[native_id] = command
            else:
                self._in_flight[native_id] = {"state": state, "turn_id": active}
            return state
        if status != "idle" or active is not None or result.get("queuedInputs"):
            return "unknown"
        if command and command.get("state") not in ("completed", "rejected"):
            last = result.get("lastTurn")
            expected = command.get("turn_id") or command.get("command_id")
            # A steer becomes its own turn after the steered one (installed
            # 1.4.20), so the chat's turn may end with one of them.
            ends = {expected, *command.get("steers", ())}
            if (not isinstance(last, dict) or last.get("turnId") not in ends
                    or last.get("terminal") not in ("completed", "cancelled", "failed")):
                self._in_flight[native_id] = {**command, "state": "unknown"}
                return "unknown"
            # Keep how it ended: a stopped turn is "cancelled", not finished.
            command = {**command, "state": "completed", "turn_id": expected,
                       "terminal": last["terminal"]}
            command.pop("steers", None)
            self._remember(record, command)
        self._in_flight.pop(native_id, None)
        return "idle"

    def send(self, record: OwnSession, text: str, *, idempotency_key=None) -> str:
        with self._lock:
            self._check(record)
            self._require_available()
            if not isinstance(text, str) or not text.strip():
                raise MuseHistoryError("There is no message to send.")
            key = idempotency_key or _command_id()
            if not _valid_command(key):
                raise MuseHistoryError("The turn's idempotency key must be a UUIDv7 command identity.")
            self.prepare(record)
            result = self._read(record)
            if self._reconcile(record, result) != "idle":
                raise MuseHistoryError("This chat already has a turn running or its outcome is unknown. Check it before sending again.")
            saved = self._saved(record)
            if saved.get("command_id") == key:
                raise MuseHistoryError("That command was already submitted. Refresh the chat instead of sending it again.")
            command = {"command_id": key, "turn_id": "", "state": "submitting"}
            self._remember(record, command, required=True)
            self._in_flight[record.cli_session_id] = command
            try:
                result = self.execution.request("turn/start", {
                    "sessionId": record.cli_session_id, "commandId": key,
                    "input": [{"type": "text", "text": text}]})
            except MuseRequestError:
                command = {**command, "state": "rejected"}
                self._in_flight.pop(record.cli_session_id, None)
                self._remember(record, command)
                raise
            except Exception:
                command = {**command, "state": "unknown"}
                self._in_flight[record.cli_session_id] = command
                self._remember(record, command)
                raise
            if (not isinstance(result, dict) or result.get("commandId") != key
                    or result.get("status") != "accepted" or not _identity(result.get("turnId"))
                    or result.get("disposition") != "started" or result.get("startedNewTurn") is not True):
                command = {**command, "state": "unknown"}
                self._in_flight[record.cli_session_id] = command
                self._remember(record, command)
                raise MuseHistoryError("Muse Code didn't confirm a new running turn. Check the chat before sending again.")
            command = {**command, "state": "running", "turn_id": result["turnId"]}
            self._in_flight[record.cli_session_id] = command
            self._remember(record, command)
            return key

    def steer(self, record: OwnSession, text: str) -> str:
        """Send ``text`` into the running turn. Muse refuses it if that turn
        has ended (expectedTurnId), so it can never land in the next one. The
        steer's id is saved first: its turn may be where the chat's turn ends."""
        with self._lock:
            self._check(record)
            self._require_available()
            if not isinstance(text, str) or not text.strip():
                raise MuseHistoryError("There is no message to send.")
            self.prepare(record)
            result = self._read(record)
            state = self._reconcile(record, result)
            turn = result["session"].get("activeTurnId")
            if state != "running" or not _identity(turn):
                raise MuseHistoryError("Muse Code has no turn running to send this into. "
                                       "Send it as a new message.")
            command = self._in_flight.get(record.cli_session_id) or self._saved(record)
            steers = list(command.get("steers", ()))
            if len(steers) >= 16:
                raise MuseHistoryError("This turn has taken as many messages as it can. "
                                       "Send this once it ends.")
            ident = _command_id()
            self._remember(record, {**command, "steers": [*steers, ident]}, required=True)
            try:
                result = self.execution.request("turn/steer", {
                    "sessionId": record.cli_session_id, "commandId": ident,
                    "expectedTurnId": turn, "input": [{"type": "text", "text": text}]})
            except MuseRequestError as error:
                # Refused, so nothing went: forget the steer.
                self._remember(record, {**command, "steers": steers})
                if error.code == -32030:
                    raise MuseHistoryError("That turn has already ended, so Muse Code didn't take "
                                           "the message. Send it as a new message.") from None
                raise
            except Exception:
                raise MuseHistoryError("Muse Code didn't confirm the message. Check the chat "
                                       "before sending it again.") from None
            if (not isinstance(result, dict) or result.get("commandId") != ident
                    or result.get("status") != "accepted" or result.get("turnId") != turn):
                raise MuseHistoryError("Muse Code didn't confirm the message. Check the chat "
                                       "before sending it again.")
            self._in_flight[record.cli_session_id] = {**self._saved(record)}
            return ident

    def _catalog(self, record: OwnSession) -> list:
        result = self.execution.request("model/list", {"sessionId": record.cli_session_id})
        models = _request_array(result, "models")
        if len(models) > 64:
            raise MuseHistoryError("Muse Code listed more models than this app can show.")
        for entry in models:
            if (not isinstance(entry, dict) or not _identity(entry.get("modelId"))
                    or not isinstance(entry.get("displayLabel"), str)
                    or not isinstance(entry.get("providerId"), str)
                    or type(entry.get("isActive")) is not bool
                    or not (entry.get("profileId") is None or isinstance(entry.get("profileId"), str))):
                raise MuseHistoryError("Muse Code listed a model this app can't read.")
        return models

    def models(self, record: OwnSession) -> tuple:
        """(model id, label, this chat's) for each model Muse offers."""
        with self._lock:
            self._check(record)
            self._require_available()
            return tuple((m["modelId"], m["displayLabel"].strip() or m["modelId"], m["isActive"])
                         for m in self._catalog(record))

    def set_model(self, record: OwnSession, model_id: str) -> str:
        """Switch to ``model_id``, from Muse's next model call. Installed
        1.4.20 accepts any id, even one it doesn't offer, and keeps it, so
        only a model from the catalog read just now is sent, and the session
        is read back to confirm it took."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            entry = next((m for m in self._catalog(record) if m["modelId"] == model_id), None)
            if entry is None:
                raise MuseHistoryError("Muse Code doesn't offer that model for this chat now. "
                                       "Choose Change Model again.")
            model = {"modelId": entry["modelId"], "providerId": entry["providerId"]}
            if entry.get("profileId") is not None:
                model["profileId"] = entry["profileId"]
            params = {"sessionId": record.cli_session_id, "commandId": _command_id(), "model": model}
            result = self.execution.request("session/setModel", params)
            if (not isinstance(result, dict) or result.get("commandId") != params["commandId"]
                    or result.get("status") != "accepted"
                    or self._read(record)["session"].get("modelId") != model_id):
                raise MuseHistoryError("Muse Code didn't confirm the model change. "
                                       "Check the chat's model before trying again.")
            return entry["displayLabel"].strip() or model_id

    def settle(self, record: OwnSession) -> None:
        # Retained for callers from earlier drafts; only a native read settles.
        self.poll(record)

    def in_flight(self, native_id: str) -> bool:
        with self._lock:
            return native_id in self._in_flight

    def stop(self, record: OwnSession) -> str:
        """"idle" when Muse says nothing is running (nothing is sent), else
        "cancelling" once Muse accepts the cancel."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            result = self._read(record)
            if self._reconcile(record, result) == "idle":
                return "idle"
            command = self._in_flight.get(record.cli_session_id) or self._saved(record)
            turn_id = command.get("turn_id")
            if not _identity(turn_id):
                raise MuseHistoryError("Muse Code's running turn is unknown. Refresh before trying Stop again.")
            params = {"sessionId": record.cli_session_id, "commandId": _command_id(), "turnId": turn_id}
            try:
                result = self.execution.request("turn/cancel", params)
                if (not isinstance(result, dict) or result.get("commandId") != params["commandId"]
                        or result.get("turnId") != turn_id or result.get("status") != "accepted"):
                    raise MuseHistoryError("Muse Code didn't confirm the cancellation request.")
            except Exception:
                # Retain the live turn even when a cancellation fails.
                self._in_flight[record.cli_session_id] = command
                raise
            command = {**command, "state": "cancelling"}
            self._in_flight[record.cli_session_id] = command
            self._remember(record, command)
            return "cancelling"

    def pending_approvals(self, record: OwnSession) -> tuple:
        with self._lock:
            self._check(record)
            self._require_available()
            result = self.execution.request("approval/listPending", {"sessionId": record.cli_session_id})
            # No admission/listPending: admissions are peerMessaging (another
            # chat asking to message this one), which this app never requests
            # at initialize. Native 1.4.20 on Windows refused the call with
            # capabilityRequired (-32010), and asking anyway made every poll
            # of a running turn read as "unknown".
            return approvals_from_muse(result, record.cli_session_id)

    def _receipts(self, record: OwnSession, pending: tuple) -> None:
        drain = getattr(self.execution, "drain_events", None)
        if not callable(drain):
            return
        self._pending_events.extend(drain())
        if len(self._pending_events) > 2048:
            raise MuseHistoryError("Muse Code sent too many pending events. Reopen the chat to reconcile its state.")
        kept = []
        for event in self._pending_events:
            params = event.get("params", {})
            if not isinstance(params, dict):
                raise MuseHistoryError("Muse Code sent an unreadable execution event.")
            if "id" not in event:
                # A full state read above reconciles notifications, including
                # item revisions and terminal turns, without replaying them.
                continue
            if params.get("sessionId") != record.cli_session_id:
                kept.append(event)
                continue
            request_id = params.get("approvalId") or params.get("userInputId")
            if not any(request.request_id == request_id for request in pending):
                kept.append(event)
                continue
            respond = getattr(self.execution, "respond_receipt", None)
            if callable(respond):
                respond(event["id"])
        self._pending_events = kept

    def poll(self, record: OwnSession) -> dict:
        with self._lock:
            self._check(record)
            self._records[record.cli_session_id] = record
            try:
                self._require_available()
                result = self._read(record)
                state = self._reconcile(record, result)
                pending = self.pending_approvals(record)
                self._receipts(record, pending)
                if pending and state not in ("unknown", "cancelling"):
                    state = "waiting"
                return {"state": state, "pending": pending, "turn_id": result["session"].get("activeTurnId") or "",
                        "terminal": self._saved(record).get("terminal", "") if state == "idle" else "",
                        "failure": _failure_kind(result) if state == "idle" else "",
                        "notice": "" if state != "unknown" else "Muse Code's turn outcome is unknown. Refresh before sending again."}
            except Exception as error:
                command = self._in_flight.get(record.cli_session_id) or self._saved(record)
                if command and command.get("state") not in ("completed", "rejected"):
                    self._in_flight[record.cli_session_id] = {**command, "state": "unknown"}
                notice = (str(error) if isinstance(error, MuseSaveError) else
                          "Muse Code execution is unavailable. Existing messages remain readable; try refreshing.")
                return {"state": "unknown", "pending": (), "turn_id": "", "notice": notice}

    def _answers(self, approval: MuseApproval, answers: list) -> list:
        if not isinstance(answers, list) or len(answers) != len(approval.questions):
            raise MuseHistoryError("Answer each Muse Code question before sending.")
        supplied = {}
        for answer in answers:
            if not isinstance(answer, dict) or not _identity(answer.get("questionId")) or answer["questionId"] in supplied:
                raise MuseHistoryError("Muse Code received an unreadable question answer.")
            supplied[answer["questionId"]] = answer
        result = []
        for question in approval.questions:
            answer = supplied.get(question["id"])
            if answer is None:
                raise MuseHistoryError("Answer each Muse Code question before sending.")
            fields = [name for name in ("freeText", "selectedLabel", "selectedLabels") if name in answer]
            if len(fields) != 1 or set(answer) - {"questionId", "freeText", "selectedLabel", "selectedLabels", "note"}:
                raise MuseHistoryError("Choose one answer form for each Muse Code question.")
            if "note" in answer and (not isinstance(answer["note"], str) or len(answer["note"]) > 500):
                raise MuseHistoryError("A Muse Code answer note must be at most 500 characters.")
            labels = {option["label"] for option in question["options"]}
            field_name = fields[0]
            value = answer[field_name]
            mode = question["selection"]["mode"]
            if field_name == "freeText":
                valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 500
            elif field_name == "selectedLabel":
                valid = mode == "single" and isinstance(value, str) and value in labels
            else:
                selection = question["selection"]
                valid = (mode == "multiple" and isinstance(value, list)
                    and all(isinstance(label, str) and label in labels for label in value)
                    and len(set(value)) == len(value)
                    and selection.get("minSelections", 1) <= len(value) <= selection.get("maxSelections", len(labels)))
            if not valid:
                raise MuseHistoryError("That answer does not match the Muse Code question.")
            result.append(dict(answer))
        return result

    def answer(self, record: OwnSession, approval: MuseApproval, decision: str, answers=None) -> dict:
        with self._lock:
            self._check(record)
            self._require_available()
            if not isinstance(approval, MuseApproval) or approval.native_id != record.cli_session_id:
                raise MuseHistoryError("That permission belongs to another chat.")
            pending = self.pending_approvals(record)
            current = next((p for p in pending if p.request_id == approval.request_id and p.kind == approval.kind), None)
            if current != approval:
                raise MuseHistoryError("That Muse Code request changed or is no longer pending. Open it again.")
            params = {"sessionId": record.cli_session_id, "commandId": _command_id()}
            if approval.kind == "question":
                params["userInputId"] = approval.request_id
                if decision == "deny":
                    method = "userInput/cancel"
                elif decision in ("allow", "answer"):
                    method = "userInput/answer"
                    params["answers"] = self._answers(approval, answers)
                else:
                    raise MuseHistoryError("A Muse Code question needs an answer or cancellation.")
            else:
                if decision in ("allow", "deny"):
                    # Act on the choice's decision, never its id (the schema
                    # says ids are open). Installed 1.4.20 offers Reject as
                    # decision "abort", which also ends the turn; a plain
                    # "denied" is preferred when offered, as it doesn't.
                    wanted = ("approved",) if decision == "allow" else ("denied", "abort")
                    choices = []
                    for native_decision in wanted:
                        choices = [c for c in approval.choices
                                   if c["decision"] == native_decision and c["scope"] == "once"]
                        if choices:
                            break
                    if len(choices) != 1:
                        raise MuseHistoryError("Muse Code didn't offer that one-time decision. Review its choices again.")
                    choice_id = choices[0]["choiceId"]
                else:
                    # The milestone deliberately supports one-time decisions
                    # only; a native choice may otherwise grant lasting rights.
                    choices = [c for c in approval.choices if c["choiceId"] == decision and c["scope"] == "once"]
                    if len(choices) != 1:
                        raise MuseHistoryError("Muse Code didn't offer that one-time decision.")
                    choice_id = decision
                params["choiceId"] = choice_id
                method = "approval/decide"
                params["approvalId"] = approval.request_id
                params["requirementId"] = dict(approval.requirement)
            result = self.execution.request(method, params)
            ident_name = "userInputId" if approval.kind == "question" else "approvalId"
            if (not isinstance(result, dict) or result.get("commandId") != params["commandId"]
                    or result.get("status") != "accepted" or result.get(ident_name) != approval.request_id):
                raise MuseHistoryError("Muse Code didn't confirm the decision. Check the chat before deciding again.")
            return result
