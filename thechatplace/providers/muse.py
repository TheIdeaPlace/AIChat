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

from ..aside import NOTHING_FINISHED, AsideEvent
from ..own_store import OwnSession
from ..sessions import IDLE, NEEDS_YOU, SessionInfo, STATUS_UNAVAILABLE, WORKING
from ..titles import clean_title
from ..transcript import (Transcript, ChatMessage, USER, ASSISTANT, TOOL,
                          TOOL_RESULT, THINKING, ERROR, EVENT, COMPACTION)
from ..ui_text import last_message_line
from .base import Snapshot, SessionCapabilities
from . import muse_usage
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


#: How a workflow item's status is said after its description.
WORKFLOW_STATUS = {"inProgress": " (running in the background)", "completed": " (finished)",
                   "failed": " (failed)", "cancelled": " (stopped)"}


def background_work(result) -> bool:
    """Whether a full session/read shows a workflow still running: Muse ends
    the turn that starts one, then starts another with its results (1.4.24).
    Its sub-agents' permission requests come in between, on this chat."""
    try:
        items = _history_items(result)
    except MuseHistoryError:
        return False
    latest = {}  # the newest revision of each workflow, as the transcript folds them
    for item in items:
        if (isinstance(item, dict) and item.get("kind") == "workflow" and _identity(item.get("itemId"))
                and type(item.get("revision")) is int):
            old = latest.get(item["itemId"])
            if old is None or item["revision"] > old["revision"]:
                latest[item["itemId"]] = item
    return any(item.get("status") == "inProgress" and item.get("retracted") is not True
               for item in latest.values())


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
        # Muse keeps a very long item cut short in its history: say so on
        # the text that was cut (not the command line), rather than count
        # it against the whole chat.
        shortened = item.get("truncated") is True

        def add(message_kind, text, suffix="", whole=False):
            if shortened and not whole:
                text = text.rstrip() + "\n\n(Shortened by Muse Code.)"
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
            add(TOOL, f"{tool}: {args}" if args else tool, whole=isinstance(item.get("visibleOutput"), str))
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
        elif kind == "skillRead":
            # Muse loaded a skill (installed 1.4.20): activity, like a reminder.
            selector = _text(item.get("selector"))
            for source in ("bundled:", "user:", "project:", "plugin:"):
                if selector.startswith(source):
                    selector = selector[len(source):]
                    break
            add(EVENT, f"Muse Code read the {selector} skill." if selector else "Muse Code read a skill.",
                whole=True)
        elif kind == "compaction":
            add(COMPACTION, _text(item.get("fallbackText")) or "Muse Code compacted the conversation.")
        elif kind in ("workflow", "subagent") and _text(item.get("fallbackText")).strip():
            # A workflow runs in the background after its turn ends, and its
            # sub-agents are its parts (1.4.24). Their result comes as Muse's
            # next reply. Only the workflow's status is said: installed 1.4.24
            # leaves a sub-agent item in progress after its workflow completes.
            text = _text(item["fallbackText"]).strip()
            if kind == "workflow":
                text += WORKFLOW_STATUS.get(item["status"], "")
            add(EVENT, text, whole=True)
        else:
            # The wire's fallbackText is explicitly intended for unknown kinds.
            # Never dump an unrecognized object as JSON into the conversation.
            fallback = _text(item.get("fallbackText"))
            if fallback:
                add(EVENT, fallback)
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
    PLAN_CACHE_SECONDS = 60

    def __init__(self, connection=None, *, executable=None, execution=None):
        self.connection = connection if connection is not None else ManagedMuseConnection(executable)
        self._execution = execution
        self._owned = MuseOwnedChats(execution) if execution is not None else None
        self._closed = threading.Event()
        self._previews = OrderedDict()
        self._preview_lock = threading.Lock()
        self._plan, self._plan_until = None, 0.0
        self._plan_lock = threading.Lock()

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
        # Usage and Context is for every chat, read-only ones too, as for the
        # other providers: a chat started elsewhere gets the plan's lines and
        # says its context is known only for chats started here (#701).
        return SessionCapabilities(send=enabled, approvals=enabled, steer=enabled, aside=enabled,
                                   attachments=enabled, usage=True)

    def usage_lines(self, session):
        """Usage and Context (Ctrl+Shift+U, #701): the chat's context and last
        turn's tokens, for a chat started here, from its view; then the
        account's plan, kept a minute as Copilot's quota is (Muse Code
        refreshes it at most once a minute anyway)."""
        self._check(session)
        if not session.is_own:
            lines = [muse_usage.CONTEXT_ONLY_HERE]
        else:
            try:
                lines = muse_usage.context_lines(*self._owned_chats().usage_events(session.cli_session_id))
            except MuseHistoryError:
                lines = ["Context: Muse Code couldn't say how full it is. Try again."]
        return lines + self._plan_lines()

    def _plan_lines(self):
        with self._plan_lock:
            if self._plan is None or time.monotonic() >= self._plan_until:
                try:
                    self._plan = tuple(muse_usage.plan_lines(self._owned_chats().plan_usage()))
                except MuseHistoryError:
                    self._plan = (muse_usage.PLAN_UNAVAILABLE,)
                self._plan_until = time.monotonic() + self.PLAN_CACHE_SECONDS
            return list(self._plan)

    def owned_state(self, record):
        """The Status column for a chat started here, from The Chat Place's
        own Muse host (reads; nothing is loaded or resumed). Muse's status
        there is notLoaded, idle, running or needsInput: notLoaded means no
        turn of it is running in this host, where chats started here run.
        Unknown if Muse can't be asked.

        Idle isn't the whole story (1.4.24): after a turn starts a workflow,
        Muse calls the chat idle while its sub-agents work, and while they
        wait for you, with their requests listed on this chat. So an idle
        chat's pending requests are read (it is loaded here, so they can
        exist), and one with background work is Working."""
        if self._owned is None or not self._owned.available():
            return STATUS_UNAVAILABLE
        try:
            result = self._owned.execution.request("session/read", {
                "sessionId": record.cli_session_id, "excludeItems": True})
            status = self._owned._session(result, record.cli_session_id).get("status")
            if status == "idle":
                pending = self._owned.execution.request("approval/listPending",
                                                        {"sessionId": record.cli_session_id})
                if (isinstance(pending, dict)
                        and (_request_array(pending, "approvals") or _request_array(pending, "userInputs"))):
                    return NEEDS_YOU
                if self._owned.has_background_work(record.cli_session_id):
                    return WORKING
        except Exception:  # noqa: BLE001 - the row says unknown rather than guess
            return STATUS_UNAVAILABLE
        return {"running": WORKING, "needsInput": NEEDS_YOU, "idle": IDLE,
                "notLoaded": IDLE}.get(status, STATUS_UNAVAILABLE)

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

    def create_owned(self, cwd, title, profile=""):
        if self._closed.is_set():
            raise MuseHistoryError("Muse Code history is closed.")
        return self._owned_chats().create(cwd, title, profile)

    def model_choices(self):
        """New Session's models and permission profiles (new_chat_choices)."""
        return self._owned_chats().new_chat_choices()

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

    def aside_owned(self, record, question, on_event, on_side=None):
        """Ask Aside: a started MuseAsideRunner for this chat."""
        return MuseAsideRunner(self._owned_chats(), record, question, on_event, on_side=on_side).start()

    def retry_owned(self, record, message_key, title):
        """Retry from Here: a new chat of this one's history before your message."""
        return self._owned_chats().retry(record, message_key, title)

    def branch_owned(self, record, message_key, title):
        """Branch from Here: a new chat of this one's history through a message's turn."""
        return self._owned_chats().branch(record, message_key, title)

    def tasks_owned(self, record):
        """Stop Background Task's choices: this chat's running background work."""
        return self._owned_chats().tasks(record)

    def stop_task_owned(self, record, task_id):
        return self._owned_chats().stop_task(record, task_id)

    def skills_owned(self, record):
        """This chat's skills for Insert Command or Skill."""
        return self._owned_chats().skills(record)

    def prepare_mode_owned(self, record):
        """Change Permission Mode's choices: ((profile, label), ...), now."""
        return self._owned_chats().modes(record)

    def set_mode_owned(self, record, profile):
        return self._owned_chats().set_mode(record, profile)

    def prepare_effort_owned(self, record):
        """This chat's efforts for Change Effort: ((tier, label), ...), now."""
        return self._owned_chats().efforts(record)

    def set_effort_owned(self, record, tier):
        return self._owned_chats().set_effort(record, tier)

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


#: Muse Code's permission profiles and who reviews restricted actions in
#: each, as installed 1.4.20 reports them (permissionProfile/list and the
#: session's permissionState). A chat starts on Ask me.
PROFILES = {":read-only": "human", ":ask-me": "human", ":auto-review": "autoReview",
            ":unrestricted": "none"}
PROFILE_LABELS = {":read-only": "Read only", ":ask-me": "Ask me", ":auto-review": "Auto-review",
                  ":unrestricted": "Unrestricted"}

#: Muse's closed effort tiers, in plain words.
EFFORT_LABELS = {"none": "none", "minimal": "minimal", "low": "low", "medium": "medium",
                 "high": "high", "xhigh": "extra high", "max": "maximum", "ultra": "ultra"}


def _tiers(entry) -> tuple:
    """The efforts a catalog model offers, in Muse's order."""
    variants = entry.get("variants") if isinstance(entry, dict) else None
    return tuple(t for t in variants if t in EFFORT_LABELS) if isinstance(variants, list) else ()


#: The most a message's parts may hold, base64 images included: under the
#: connection's 10 MB frame, with room for the request around them.
MAX_INPUT_BYTES = 8 * 1024 * 1024


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
        # Chats whose last poll found a workflow still running: their turn
        # has ended, so Muse calls them idle (1.4.24), but they're working.
        self._background = set()

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

    def _wanted(self, record) -> str:
        """The profile this chat should be on: the one chosen with Change
        Permission Mode, else Ask me, which every chat starts on."""
        chosen = record.provider_options.get("muse_profile") if record is not None else None
        return chosen if chosen in PROFILES else self.PROFILE

    def _listed_profiles(self) -> list:
        result = self.execution.request("permissionProfile/list", {})
        return [p for p in _request_array(result, "profiles")
                if isinstance(p, dict) and p.get("profileId") in PROFILES and p.get("available") is True
                and p.get("reviewerAuthority") == PROFILES[p["profileId"]]]

    def _profile(self, wanted=None) -> str:
        wanted = wanted or self.PROFILE
        if not any(p["profileId"] == wanted for p in self._listed_profiles()):
            raise MuseHistoryError(f"Muse Code's {PROFILE_LABELS[wanted]} permission profile is "
                                   "unavailable. Sending is disabled.")
        return wanted

    def _session(self, result: dict, native_id: str) -> dict:
        session = result.get("session") if isinstance(result, dict) else None
        if not isinstance(session, dict) or session.get("sessionId") != native_id:
            raise MuseHistoryError("Muse Code didn't return the requested owned chat.")
        return session

    def _policy(self, session: dict, wanted=None) -> None:
        """Muse's effective policy must be the chat's chosen profile, with
        that profile's reviewer: a human for Ask me and Read only."""
        wanted = wanted or self.PROFILE
        mode, policy = session.get("approvalMode"), session.get("permissionState")
        if (not isinstance(mode, dict) or mode.get("mode") != "promptUnmatched"
                or not isinstance(policy, dict) or policy.get("profileId") != wanted
                or policy.get("reviewerAuthority") != PROFILES[wanted]
                or policy.get("reviewerAttached") is not True):
            raise MuseHistoryError(f"Muse Code couldn't confirm this chat's permission profile, "
                                   f"{PROFILE_LABELS[wanted]}. Sending is disabled.")

    def new_chat_choices(self) -> dict:
        """New Session's choices, read before any chat exists: ``models``,
        ((model id, label, its effort tiers, its default tier), ...), and
        ``profiles``, ((profile, label), ...) Muse offers now. Installed
        1.4.24 answers model/list without a sessionId with the same catalog."""
        with self._lock:
            self._require_available()
            result = self.execution.request("model/list", {})
            models = _request_array(result, "models")
            if len(models) > 64:
                raise MuseHistoryError("Muse Code listed more models than this app can show.")
            listed = []
            for entry in models:
                if (not isinstance(entry, dict) or not _identity(entry.get("modelId"))
                        or not isinstance(entry.get("displayLabel"), str)):
                    raise MuseHistoryError("Muse Code listed a model this app can't read.")
                default = entry.get("defaultReasoningEffort")
                listed.append((entry["modelId"], entry["displayLabel"].strip() or entry["modelId"],
                               _tiers(entry), default if default in EFFORT_LABELS else ""))
            profiles = tuple((p["profileId"], PROFILE_LABELS[p["profileId"]])
                             for p in self._listed_profiles())
            return {"models": tuple(listed), "profiles": profiles}

    def create(self, cwd: str, title: str, profile: str = "") -> OwnSession:
        """A new chat on ``profile`` (Ask me if none), started on it rather
        than changed after, so it never runs a moment on another."""
        with self._lock:
            self._require_available()
            if not isinstance(cwd, str) or not cwd.strip():
                raise MuseHistoryError("A Muse Code chat needs a working folder.")
            if not isinstance(title, str) or not title.strip():
                raise MuseHistoryError("A Muse Code chat needs a title.")
            if profile and profile not in PROFILES:
                raise MuseHistoryError("Muse Code doesn't have that permission profile.")
            profile = self._profile(profile or None)
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
            if profile != self.PROFILE:
                # Kept with the chat, as Change Permission Mode keeps it, so
                # every send and reopen checks and asks for this profile.
                record.provider_options["muse_profile"] = profile
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
                    "permissionProfile": self._profile(self._wanted(record)), "excludeItems": True,
                    "autoRecover": False})
            self._policy(self._session(result, record.cli_session_id), self._wanted(record))
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
            parts = self._input(record, str(text))
            parts += list(getattr(text, "images", ()))  # a MuseInput's attached images
            if sum(len(str(value)) for part in parts for value in part.values()) > MAX_INPUT_BYTES:
                raise MuseHistoryError("These attachments are too large to send together. "
                                       "Attach fewer or smaller images.")
            command = {"command_id": key, "turn_id": "", "state": "submitting"}
            self._remember(record, command, required=True)
            self._in_flight[record.cli_session_id] = command
            try:
                result = self.execution.request("turn/start", {
                    "sessionId": record.cli_session_id, "commandId": key, "input": parts})
            except MuseRequestError as error:
                command = {**command, "state": "rejected"}
                self._in_flight.pop(record.cli_session_id, None)
                self._remember(record, command)
                if error.code == -32032 and parts[0]["type"] == "skill":
                    raise MuseHistoryError("Muse Code doesn't have that skill now. Choose it again "
                                           "with Insert Command or Skill.") from None
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

    def _active_model(self, record: OwnSession, catalog=None):
        catalog = self._catalog(record) if catalog is None else catalog
        model_id = self._read(record)["session"].get("modelId")
        return next((m for m in catalog if m["modelId"] == model_id), None)

    def _standing_effort(self, record: OwnSession):
        """The effort last set on this chat, from its view (session/read
        doesn't carry it): "" if none was ever set (the model's default
        applies), None if it can't be told within the bound."""
        cursor = None
        for _ in range(10):
            params = {"sessionId": record.cli_session_id, "limit": 200, "direction": "backward"}
            if cursor:
                params["cursor"] = cursor
            result = self.execution.request("view/page", params)
            for event in reversed(_request_array(result, "events")):
                if isinstance(event, dict) and event.get("method") == "session/reasoningEffortChanged":
                    tier = (event.get("params") or {}).get("reasoningEffort")
                    return tier if tier in EFFORT_LABELS else None
            cursor = result.get("nextCursor")
            if cursor is None:
                return ""
            if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
                raise MuseHistoryError("Muse Code sent an unreadable view page.")
        return None

    def usage_events(self, native_id: str) -> tuple:
        """(newest ``session/contextUsage`` params, newest ``session/tokenUsage``
        params) in a chat started here, either None if not found within the
        same bound the effort is read in. Read from the view, as the effort
        is: no resume, no turn."""
        with self._lock:
            self._require_available()
            if not _identity(native_id):
                raise MuseHistoryError("Not a Muse Code chat started here.")
            found, cursor = {}, None
            wanted = ("session/contextUsage", "session/tokenUsage")
            for _ in range(10):
                params = {"sessionId": native_id, "limit": 200, "direction": "backward"}
                if cursor:
                    params["cursor"] = cursor
                result = self.execution.request("view/page", params)
                for event in reversed(_request_array(result, "events")):
                    method = event.get("method") if isinstance(event, dict) else None
                    if method in wanted and method not in found and isinstance(event.get("params"), dict):
                        found[method] = event["params"]
                cursor = result.get("nextCursor")
                if len(found) == len(wanted) or cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
                    raise MuseHistoryError("Muse Code sent an unreadable view page.")
            return found.get(wanted[0]), found.get(wanted[1])

    def plan_usage(self) -> dict:
        """The account's plan usage: ``usage/refresh`` (fresh, without a model
        call), or, when Muse Code refuses it because it was asked under a
        minute ago, what it last observed (``usage/read``). usage/read has no
        tier (checked on 1.4.24), so the last refresh's tier and state go
        with it, rather than the plan's line going missing."""
        with self._lock:
            self._require_available()
            try:
                result = self.execution.request("usage/refresh", {})
                if isinstance(result, dict):
                    self._plan_facts = {key: result[key] for key in ("tierName", "subscriptionActive")
                                        if key in result}
            except MuseRequestError:
                result = self.execution.request("usage/read", {})
                if isinstance(result, dict):
                    result = {**getattr(self, "_plan_facts", {}), **result}
            if not isinstance(result, dict):
                raise MuseHistoryError("Muse Code didn't say how much of the plan is used.")
            return result

    def _effort_now(self, record: OwnSession, entry):
        standing = self._standing_effort(record)
        if standing == "":
            default = entry.get("defaultReasoningEffort") if entry else None
            return default if default in EFFORT_LABELS else None
        return standing

    def efforts(self, record: OwnSession) -> tuple:
        """((tier, label), ...) this chat's model offers, and the effort it
        uses now (None if that can't be told)."""
        with self._lock:
            self._check(record)
            self._require_available()
            entry = self._active_model(record)
            if entry is None:
                raise MuseHistoryError("Muse Code doesn't list this chat's model now. Choose one "
                                       "with Change Model first.")
            tiers = _tiers(entry)
            if not tiers:
                raise MuseHistoryError("Muse Code doesn't say which efforts this chat's model offers.")
            return tuple((t, EFFORT_LABELS[t]) for t in tiers), self._effort_now(record, entry)

    def _send_effort(self, record: OwnSession, tier: str) -> None:
        params = {"sessionId": record.cli_session_id, "commandId": _command_id(), "reasoningEffort": tier}
        result = self.execution.request("session/setReasoningEffort", params)
        if (not isinstance(result, dict) or result.get("commandId") != params["commandId"]
                or result.get("status") != "accepted" or self._standing_effort(record) != tier):
            raise MuseHistoryError("Muse Code didn't confirm the effort change. Check the chat's "
                                   "effort before trying again.")

    def set_effort(self, record: OwnSession, tier: str) -> str:
        """Set ``tier``, from Muse's next model call. Installed 1.4.20 accepts
        a tier the model doesn't offer, then fails the turn, so only one of
        the model's own is sent, and the view is read back to confirm it."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            entry = self._active_model(record)
            if entry is None or tier not in _tiers(entry):
                raise MuseHistoryError("This chat's model doesn't offer that effort. "
                                       "Choose Change Effort again.")
            self._send_effort(record, tier)
            return EFFORT_LABELS[tier]

    def set_model(self, record: OwnSession, model_id: str) -> tuple:
        """Switch to ``model_id``, from Muse's next model call: (its label, a
        note: "" or what happened to the effort). Installed 1.4.20 accepts any
        id, even one it doesn't offer, and keeps it, so only a model from the
        catalog read just now is sent, and the session is read back to confirm
        it took. An effort the new model doesn't offer would fail every turn,
        so it becomes the new model's default."""
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
            label = entry["displayLabel"].strip() or model_id
            standing = self._standing_effort(record)
            tiers = _tiers(entry)
            default = entry.get("defaultReasoningEffort")
            if standing == "" or standing in tiers or default not in tiers:
                return label, ""  # the new model's default applies, or nothing better to set
            self._send_effort(record, default)
            was = EFFORT_LABELS[standing] if standing else "the chat's unknown effort"
            return label, (f"Its effort is now {EFFORT_LABELS[default]}, since {label} "
                           f"doesn't offer {was}.")

    def _skill_rows(self, record: OwnSession) -> list:
        result = self.execution.request("skill/list", {"sessionId": record.cli_session_id})
        rows = _request_array(result, "skills")
        if len(rows) > 512:
            raise MuseHistoryError("Muse Code listed more skills than this app can show.")
        for row in rows:
            if (not isinstance(row, dict) or not _identity(row.get("selector"))
                    or not isinstance(row.get("source"), str) or not row["source"]
                    or not isinstance(row.get("description"), str)
                    or not (row.get("argumentHint") is None or isinstance(row.get("argumentHint"), str))):
                raise MuseHistoryError("Muse Code listed a skill this app can't read.")
        return rows

    def skills(self, record: OwnSession) -> tuple:
        """This chat's user-invocable skills: name (the selector you type
        after "/"), description, argument hint and source."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            return tuple({"name": row["selector"], "description": row["description"],
                          "argumentHint": row.get("argumentHint") or "", "source": row["source"]}
                         for row in self._skill_rows(record))

    def _input(self, record: OwnSession, text: str) -> list:
        """The turn's input parts. Installed 1.4.20 passes "/name" in a text
        part to the model as words, so a message whose first word names one
        of this chat's skills is sent as that skill, the rest its arguments,
        as Muse's own terminal does. Anything else is sent as typed."""
        if text.startswith("/"):
            head = text[1:].split(None, 1)
            name = head[0] if head else ""
            if name:
                row = next((r for r in self._skill_rows(record) if r["selector"] == name), None)
                if row is not None:
                    part = {"type": "skill", "selector": name, "source": row["source"]}
                    arguments = head[1].strip() if len(head) > 1 else ""
                    if arguments:
                        part["arguments"] = arguments
                    return [part]
        return [{"type": "text", "text": text}]

    def modes(self, record: OwnSession) -> tuple:
        """((profile, label), ...) Muse offers, and this chat's profile."""
        with self._lock:
            self._check(record)
            self._require_available()
            choices = tuple((p["profileId"], PROFILE_LABELS[p["profileId"]]) for p in self._listed_profiles())
            if not choices:
                raise MuseHistoryError("Muse Code didn't list its permission profiles.")
            return choices, self._wanted(record)

    def set_mode(self, record: OwnSession, profile: str) -> str:
        """Switch the chat to ``profile``, confirmed by Muse's reply and the
        session read back, then saved, so a reopened chat asks for it and
        each send checks it. Not saved unless Muse made it."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            if profile not in PROFILES or not any(p["profileId"] == profile for p in self._listed_profiles()):
                raise MuseHistoryError("Muse Code doesn't offer that permission profile now. "
                                       "Choose Change Permission Mode again.")
            params = {"sessionId": record.cli_session_id, "commandId": _command_id(), "profileId": profile}
            result = self.execution.request("session/setPermissionProfile", params)
            state = result.get("permissionState") if isinstance(result, dict) else None
            try:
                if (not isinstance(result, dict) or result.get("commandId") != params["commandId"]
                        or result.get("status") != "accepted" or not isinstance(state, dict)
                        or state.get("profileId") != profile
                        or state.get("reviewerAuthority") != PROFILES[profile]):
                    raise MuseHistoryError("not applied")
                self._policy(self._read(record)["session"], profile)
            except MuseHistoryError:
                raise MuseHistoryError("Muse Code didn't confirm the permission change. Check the "
                                       "chat's permissions before sending.") from None
            previous = record.provider_options.get("muse_profile")
            record.provider_options["muse_profile"] = profile
            if self._persistence is not None:
                try:
                    self._persistence(record)
                except Exception:
                    if previous is None:
                        record.provider_options.pop("muse_profile", None)
                    else:
                        record.provider_options["muse_profile"] = previous
                    raise MuseSaveError("The Chat Place couldn't save this chat's permission profile. "
                                        "Sending stays disabled until it's set again.") from None
            return PROFILE_LABELS[profile]

    def _history_turns(self, record: OwnSession, message_key: str, kinds: tuple, refusal: str):
        """(the chosen history item, the chat's turn ids in order). The item
        is found by its key ("muse:<chat>:<item>") and must be one of
        ``kinds``; otherwise ``refusal`` is raised."""
        prefix = f"muse:{record.cli_session_id}:"
        if not isinstance(message_key, str) or not message_key.startswith(prefix):
            raise MuseHistoryError(refusal)
        result = self.execution.request("session/read", {"sessionId": record.cli_session_id,
                                                         "excludeItems": False})
        self._session(result, record.cli_session_id)
        items = [item for item in _history_items(result) if isinstance(item, dict)]
        target = next((item for item in items if item.get("itemId") == message_key[len(prefix):]
                       and item.get("kind") in kinds), None)
        if target is None or not _identity(target.get("turnId")):
            raise MuseHistoryError(refusal)
        turns = []
        for item in items:
            turn = item.get("turnId")
            if _identity(turn) and turn not in turns:
                turns.append(turn)
        return target, turns

    def _fork_copy(self, record: OwnSession, last_turn: str, title: str) -> OwnSession:
        """A new chat of this one's history through ``last_turn`` (session/fork
        cuts inclusively, natively), made safe to send in: a fork starts on
        approval mode onRequest, so it's set to promptUnmatched and to this
        chat's profile, then checked like any chat. Nothing is sent for you."""
        try:
            forked = self.execution.request("session/fork", {
                "sessionId": record.cli_session_id, "commandId": _command_id(),
                "cutPoint": {"lastTurnId": last_turn}, "excludeItems": True})
        except MuseRequestError as error:
            if error.code == -32023:
                raise MuseHistoryError("That turn hasn't finished, so it can't be copied yet. "
                                       "Try again once it ends.") from None
            raise
        session = forked.get("session") if isinstance(forked, dict) else None
        fid = session.get("sessionId") if isinstance(session, dict) else None
        source = session.get("forkedFrom") if isinstance(session, dict) else None
        if (not _identity(fid) or fid == record.cli_session_id or not isinstance(source, dict)
                or source.get("sessionId") != record.cli_session_id):
            raise MuseHistoryError("Muse Code didn't make a copy of this chat.")
        params = {"sessionId": fid, "commandId": _command_id(), "mode": "promptUnmatched"}
        mode = self.execution.request("session/setApprovalMode", params)
        effective = mode.get("effectiveMode") if isinstance(mode, dict) else None
        if (not isinstance(mode, dict) or mode.get("status") != "accepted"
                or not isinstance(effective, dict) or effective.get("mode") != "promptUnmatched"):
            raise MuseHistoryError("Muse Code couldn't set the copy to ask about unmatched "
                                   "requests, so nothing can be sent in it.")
        now_ms = int(time.time() * 1000)
        copy = OwnSession(fid, title, record.cwd, provider="muse",
                          created_ms=now_ms, last_activity_ms=now_ms, started=True)
        self._records[fid] = copy
        return self._keep_profile(record, copy)

    def _keep_profile(self, record: OwnSession, copy: OwnSession) -> OwnSession:
        """``copy`` on ``record``'s permission profile, checked before it's used."""
        wanted = self._wanted(record)
        if wanted != self.PROFILE:
            self.execution.request("session/setPermissionProfile", {
                "sessionId": copy.cli_session_id, "commandId": _command_id(), "profileId": wanted})
            copy.provider_options["muse_profile"] = wanted
        self._policy(self._read(copy)["session"], wanted)
        return copy

    def retry(self, record: OwnSession, message_key: str, title: str) -> OwnSession:
        """Retry from Here: a new chat of this one's history up to just before
        your message, titled ``title``, unstarted. Your first message has no
        turn before it: that's a new chat in the same folder."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            target, turns = self._history_turns(
                record, message_key, ("userMessage",),
                "Choose one of your own messages in this chat to retry from.")
            index = turns.index(target["turnId"])
            if index == 0:
                return self._keep_profile(record, self.create(record.cwd, title))
            return self._fork_copy(record, turns[index - 1], title)

    def branch(self, record: OwnSession, message_key: str, title: str) -> OwnSession:
        """Branch from Here: a new chat of this one's history through the
        whole turn of your message or Muse Code's reply, titled ``title``,
        unstarted, ready for your next message."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            target, _turns = self._history_turns(
                record, message_key, ("userMessage", "agentMessage"),
                "Choose one of your messages or one of Muse Code's replies in this chat to "
                "branch from.")
            return self._fork_copy(record, target["turnId"], title)

    def _task_rows(self, record: OwnSession) -> list:
        result = self.execution.request("task/list", {"sessionId": record.cli_session_id})
        rows = _request_array(result, "tasks")
        if len(rows) > 256:
            raise MuseHistoryError("Muse Code listed more background work than this app can show.")
        # Unknown statuses are ignored, as the schema asks: only work Muse
        # says is in progress can be stopped.
        return [row for row in rows if isinstance(row, dict) and _identity(row.get("taskId"))
                and row.get("status") == "inProgress"]

    def tasks(self, record: OwnSession) -> tuple:
        """This chat's running background work: id and a one-line label, the
        tool (or the task's kind) and its command, shortened."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            result = []
            for row in self._task_rows(record):
                name = _text(row.get("tool")) or _text(row.get("kind")) or "Background task"
                command = " ".join(str(row.get("command") or "").split())
                label = f"{name}: {command}" if command else name
                if len(label) > 100:
                    label = label[:97].rstrip() + "..."
                result.append({"id": row["taskId"], "label": label})
            return tuple(result)

    def stop_task(self, record: OwnSession, task_id: str) -> str:
        """Stop one background task: "stopping" once Muse accepts it, or
        "finished" if it had already ended (no longer listed, or -32030,
        natively what task/stop answers for a task it doesn't have)."""
        with self._lock:
            self._check(record)
            self._require_available()
            self.prepare(record)
            if not any(row["taskId"] == task_id for row in self._task_rows(record)):
                return "finished"
            params = {"sessionId": record.cli_session_id, "commandId": _command_id(), "taskId": task_id}
            try:
                result = self.execution.request("task/stop", params)
            except MuseRequestError as error:
                if error.code == -32030:
                    return "finished"
                raise
            if (not isinstance(result, dict) or result.get("commandId") != params["commandId"]
                    or result.get("status") != "accepted" or result.get("taskId") != task_id):
                raise MuseHistoryError("Muse Code didn't confirm stopping that background work. "
                                       "Check the chat before trying again.")
            return "stopping"

    def settle(self, record: OwnSession) -> None:
        # Retained for callers from earlier drafts; only a native read settles.
        self.poll(record)

    def _has_tasks(self, record: OwnSession) -> bool:
        """Whether Muse lists background work still running for this chat.
        A refused or unreadable list isn't proof of either, so the workflow
        check still decides; the chat's own state isn't made unknown by it."""
        try:
            return bool(self._task_rows(record))
        except MuseHistoryError:
            return False

    def has_background_work(self, native_id: str) -> bool:
        """Whether the last poll of this chat found background work running:
        a command Muse lists with task/list, or a workflow."""
        return native_id in self._background

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
                # An idle chat can still be working: a command it left
                # running in the background (a test suite, say), which
                # task/list shows, or a workflow, whose sub-agents ask
                # permission here after the turn has ended and which
                # task/list doesn't show. Only an idle chat is asked.
                background = state == "idle" and (self._has_tasks(record) or background_work(
                    self.execution.request("session/read", {"sessionId": record.cli_session_id,
                                                            "excludeItems": False})))
                if background:
                    self._background.add(record.cli_session_id)
                else:
                    self._background.discard(record.cli_session_id)
                if pending and state not in ("unknown", "cancelling"):
                    state = "waiting"
                return {"state": state, "pending": pending, "background": background,
                        "turn_id": result["session"].get("activeTurnId") or "",
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


class MuseAsideRunner:
    """Ask Aside for a Muse chat started here (installed 1.4.20's
    session/sideChat): a side chat that sees the main chat's history, made
    read-only before the question goes, and never asks you anything; a
    request in it cancels its turn. The main chat is never sent anything.

    Muse keeps the side chat as an ordinary chat, so ``on_side(id)`` is
    called as soon as it exists, for the window to hide it. It is left when
    the aside ends. Interface as CodexAsideRunner: start(), cancel(), and
    AsideEvent("text" | "done" | "failed", text) to ``on_event``."""
    TIMEOUT = 300.0

    def __init__(self, chats, record, question, on_event, *, on_side=None, poll=1.0):
        self.chats, self.record, self.question = chats, record, question
        self.on_event, self.on_side, self.poll = on_event, on_side or (lambda _side: None), poll
        self._cancel = threading.Event()
        self._event_lock = threading.Lock()
        self._terminal = False

    def start(self):
        threading.Thread(target=self._run, name="muse-aside", daemon=True).start()
        return self

    def cancel(self):
        self._cancel.set()
        self._emit(AsideEvent("failed", "Stopped."))

    def _emit(self, event):
        with self._event_lock:
            if self._terminal:
                return
            if event.kind in ("done", "failed"):
                self._terminal = True
            self.on_event(event)

    def _run(self):
        execution = self.chats.execution
        main = self.record.cli_session_id
        side = turn = None
        try:
            self.chats.prepare(self.record)
            result = self.chats._read(self.record)
            last, count = result.get("lastTurn"), result["session"].get("turnCount")
            if not ((isinstance(last, dict) and last.get("terminal") == "completed")
                    or (type(count) is int and count > 0)):
                self._emit(AsideEvent("failed", NOTHING_FINISHED))
                return
            if self._cancel.is_set():
                return
            opened = execution.request("session/sideChat", {"sessionId": main, "commandId": _command_id()})
            session = opened.get("session") if isinstance(opened, dict) else None
            side = session.get("sessionId") if isinstance(session, dict) else None
            source = session.get("sideFrom") if isinstance(session, dict) else None
            if (not _identity(side) or side == main or not isinstance(source, dict)
                    or source.get("sessionId") != main):
                side = None if side == main else side
                self._emit(AsideEvent("failed", "Muse Code didn't open a side chat for this chat."))
                return
            self.on_side(side)
            changed = execution.request("session/setPermissionProfile", {
                "sessionId": side, "commandId": _command_id(), "profileId": ":read-only"})
            state = changed.get("permissionState") if isinstance(changed, dict) else None
            if (not isinstance(changed, dict) or changed.get("status") != "accepted"
                    or not isinstance(state, dict) or state.get("profileId") != ":read-only"
                    or state.get("reviewerAuthority") != "human"):
                self._emit(AsideEvent("failed", "Muse Code couldn't make the side chat read-only, "
                                                "so nothing was asked."))
                return
            if self._cancel.is_set():
                return
            command = _command_id()
            started = execution.request("turn/start", {
                "sessionId": side, "commandId": command,
                "input": [{"type": "text", "text": self.question}]})
            if (not isinstance(started, dict) or started.get("status") != "accepted"
                    or not _identity(started.get("turnId"))):
                self._emit(AsideEvent("failed", "Muse Code didn't start the side question."))
                return
            turn = started["turnId"]
            deadline, previous = time.monotonic() + self.TIMEOUT, ""
            while not self._cancel.wait(self.poll):
                pending = execution.request("approval/listPending", {"sessionId": side})
                if _request_array(pending, "approvals") or _request_array(pending, "userInputs"):
                    execution.request("turn/cancel", {"sessionId": side, "commandId": _command_id(),
                                                      "turnId": turn})
                    turn = None
                    self._emit(AsideEvent("failed", "Muse Code needed to do something or ask you "
                                                    "something to answer, so the side question was "
                                                    "stopped. Ask it in the chat instead."))
                    return
                read = execution.request("session/read", {"sessionId": side, "excludeItems": False})
                answer = "\n\n".join(
                    item["text"] for item in _history_items(read)
                    if isinstance(item, dict) and item.get("kind") == "agentMessage"
                    and item.get("turnId") == turn and isinstance(item.get("text"), str)
                    and item["text"].strip())
                if answer != previous:
                    previous = answer
                    self._emit(AsideEvent("text", answer))
                ended = read.get("lastTurn") if isinstance(read, dict) else None
                session = read.get("session") if isinstance(read, dict) else None
                if (isinstance(session, dict) and session.get("status") == "idle"
                        and not session.get("activeTurnId") and isinstance(ended, dict)
                        and ended.get("turnId") == turn):
                    turn = None
                    self._emit(AsideEvent("done", answer) if ended.get("terminal") == "completed" and answer
                               else AsideEvent("failed", "Muse Code did not complete this side question."))
                    return
                if time.monotonic() > deadline:
                    self._emit(AsideEvent("failed", "Muse Code took too long with this side question."))
                    return
        except Exception:
            if not self._cancel.is_set():
                self._emit(AsideEvent("failed", "Could not ask Muse Code. Check its sign-in and connection."))
        finally:
            if side and turn:
                try:
                    execution.request("turn/cancel", {"sessionId": side, "commandId": _command_id(),
                                                      "turnId": turn})
                except Exception:  # noqa: BLE001 - the side chat is left either way
                    pass
            if side:
                try:
                    execution.request("session/leave", {"sessionId": side, "commandId": _command_id()})
                except Exception:  # noqa: BLE001 - best effort; it is hidden here regardless
                    pass
