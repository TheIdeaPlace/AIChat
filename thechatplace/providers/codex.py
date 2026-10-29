"""Codex history and optional owned execution through app-server (#184).

The caller supplies an initialized RPC connection. This adapter deliberately
uses a separate verified runtime for start/fork/resume and text turns.
Externally discovered chats remain read-only; raw rollout files are never read.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import OrderedDict
import threading
import time
from typing import Protocol

from ..sessions import IDLE, NEEDS_YOU, WORKING, STATUS_UNAVAILABLE, TERMINAL, SessionInfo
from ..transcript import ASSISTANT, ERROR, PLAN, TOOL, TOOL_RESULT, USER, ChatMessage, Transcript
from ..ui_text import last_message_line
from ..changes import FileEdit, Hunk, parse_unified_diff
from .base import Snapshot
from .codex_connection import ManagedCodexConnection
from .codex_models import read_models
from .codex_usage import read_usage, read_warnings
from .codex_tokens import context_lines


class RpcConnection(Protocol):
    def request(self, method: str, params: dict) -> dict:
        ...


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _time(value) -> int:
    return value * 1000 if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _timestamp(value) -> str:
    if not _time(value):
        return ""
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def _file_edits(item: dict, turn: int):
    """Only completed fileChange items describe edits that were made.

    Updates carry unified hunks; additions and deletions carry the whole
    content. Read app-server's records, never the file's current contents or
    a working-tree diff, which could include somebody else's work.
    """
    if item.get("status") != "completed":
        return
    for change in item["changes"]:
        if not isinstance(change, dict):
            continue
        path, diff, kind = change.get("path"), change.get("diff"), change.get("kind")
        if (not isinstance(path, str) or not path.strip() or not isinstance(diff, str)
                or not isinstance(kind, dict) or kind.get("type") not in ("add", "delete", "update")):
            continue
        operation = kind["type"]
        moved = kind.get("move_path") if operation == "update" else None
        if moved is not None and (not isinstance(moved, str) or not moved.strip()):
            continue
        if operation == "update":
            hunks = parse_unified_diff(diff)
        else:
            content = diff.replace("\r\n", "\n")
            lines = content.removesuffix("\n").split("\n") if content else []
            mark = "+" if operation == "add" else "-"
            hunks = [Hunk(0 if operation == "add" else 1,
                          1 if operation == "add" else 0,
                          [mark + line for line in lines])] if lines else []
        yield FileEdit(moved or path, "Codex", turn, hunks,
                       created=operation == "add", deleted=operation == "delete",
                       moved_from=path if moved else "")


def session_from_thread(thread: dict, archived: bool) -> SessionInfo:
    native_id = _text(thread.get("id"))
    if not native_id:
        raise ValueError("Codex thread has no id")
    status = thread.get("status")
    status = status if isinstance(status, dict) else {}
    kind = status.get("type")
    # This process cannot prove that a thread not loaded here is idle in
    # another client. Keep that uncertainty visible, never guess from age.
    state, detail = STATUS_UNAVAILABLE, ""
    if kind == "idle":
        state = IDLE
    elif kind == "active":
        flags = status.get("activeFlags")
        flags = flags if isinstance(flags, list) else []
        if "waitingOnApproval" in flags or "waitingOnUserInput" in flags:
            state, detail = NEEDS_YOU, "waiting in Codex"
        else:
            state = WORKING
    elif kind == "systemError":
        state, detail = NEEDS_YOU, "Codex reported an error"
    title = _text(thread.get("name")) or _text(thread.get("preview"))
    origin = thread.get("source")
    source = ({"cli": TERMINAL, "vscode": "editor", "exec": "command_line",
               "appServer": "app_server"}.get(origin, "external")
              if isinstance(origin, str) else "external")
    return SessionInfo(source, f"codex:{native_id}", title.split("\n", 1)[0] or "Untitled session",
                       _text(thread.get("cwd")), native_id, provider="codex",
                       state=state, detail=detail, archived=archived,
                       created_ms=_time(thread.get("createdAt")),
                       last_activity_ms=_time(thread.get("updatedAt")))


def transcript_from_thread(thread: dict) -> Transcript:
    """Convert known protocol items; future item types are skipped safely."""
    transcript = Transcript(model=_text(thread.get("model")))
    if thread.get("historyMode") == "paginated":
        raise ValueError("Paginated Codex history needs a supported pagination reader")
    turns = thread.get("turns")
    if not isinstance(turns, list):
        raise ValueError("Codex did not return readable turn history")
    seen = set()
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
            transcript.unreadable_lines += 1
            continue
        if turn.get("itemsView") in ("notLoaded", "summary"):
            raise ValueError("Codex returned incomplete turn history")
        transcript.turns += 1
        first_message = len(transcript.messages)
        timestamp = _timestamp(turn.get("startedAt"))
        for item in turn["items"]:
            if not isinstance(item, dict):
                transcript.unreadable_lines += 1
                continue
            item_id = _text(item.get("id"))
            key = f'{_text(turn.get("id"))}:{item_id}' if item_id else ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            kind, text = item.get("type"), ""
            if kind in ("agentMessage", "plan"):
                text = _text(item.get("text"))
                message_kind = ASSISTANT if kind == "agentMessage" else PLAN
            elif kind == "userMessage":
                content = item.get("content")
                if not isinstance(content, list):
                    transcript.unreadable_lines += 1
                    continue
                pieces = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        pieces.append(_text(part.get("text")))
                    elif part.get("type") in ("image", "localImage"):
                        pieces.append("[Image]")
                    elif part.get("type") in ("skill", "mention"):
                        pieces.append(_text(part.get("name")) or _text(part.get("path")))
                text, message_kind = "\n".join(pieces), USER
            elif kind == "commandExecution":
                text = _text(item.get("command"))
                message_kind = TOOL
            elif kind == "fileChange":
                changes = item.get("changes")
                if not isinstance(changes, list):
                    transcript.unreadable_lines += 1
                    continue
                text = "\n".join(_text(c.get("path")) + "\n" + _text(c.get("diff"))
                                 for c in changes if isinstance(c, dict))
                transcript.edits.extend(_file_edits(item, transcript.turns))
                message_kind = TOOL
            elif kind == "mcpToolCall":
                text = f'{_text(item.get("server"))}: {_text(item.get("tool"))}'
                message_kind = TOOL
            else:
                continue
            if text:
                transcript.messages.append(ChatMessage(message_kind, text, timestamp, key,
                                                       assistant_name="Codex"))
            if kind == "commandExecution" and _text(item.get("aggregatedOutput")):
                transcript.messages.append(ChatMessage(TOOL_RESULT, item["aggregatedOutput"],
                                                       timestamp, key + ":output"))
            if kind == "mcpToolCall":
                result = item.get("result")
                content = result.get("content") if isinstance(result, dict) else None
                if isinstance(content, list):
                    output = "\n".join(_text(part.get("text")) for part in content
                                       if isinstance(part, dict) and part.get("type") == "text")
                    if output:
                        transcript.messages.append(ChatMessage(TOOL_RESULT, output, timestamp,
                                                               key + ":output"))
                error = item.get("error")
                if isinstance(error, dict) and _text(error.get("message")):
                    transcript.messages.append(ChatMessage(ERROR, error["message"], timestamp,
                                                           key + ":error"))
        error = turn.get("error")
        if isinstance(error, dict) and _text(error.get("message")):
            transcript.messages.append(ChatMessage(ERROR, error["message"], timestamp,
                                                   _text(turn.get("id")) + ":error"))
        # Every item, tool output and error belongs to the same 1-based turn
        # used by saved task snapshots and the Lists message scope (#318).
        for message in transcript.messages[first_message:]:
            message.turn = transcript.turns
    return transcript


# A sentinel the runtime returns for a chat it created that has no turn yet.
EMPTY_HISTORY = object()


class CodexReader:
    def __init__(self, connection: RpcConnection, native_id: str, *, route=None, plan_store=None):
        self.connection, self.native_id = connection, native_id
        # route(native_id) -> EMPTY_HISTORY, an owner connection, or None for
        # the reconnecting discovery connection. Asked on every refresh, since
        # the open chat keeps its reader while the runtime's state changes.
        self._plan_store = plan_store
        self._turn_ids = []
        self._route = route
        self._discovery = connection
        self.transcript = Transcript()
        self._history_stamp = None
        self._history_checked = 0.0
        self._failed_stamp = None

    def _pages(self, method, **options):
        cursor, seen = None, set()
        for _ in range(10000):
            params = {"threadId": self.native_id, "sortDirection": "asc", "limit": 100,
                      **options}
            if cursor is not None:
                params["cursor"] = cursor
            page = self.connection.request(method, params)
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise ValueError("Incomplete Codex history page")
            yield from page["data"]
            cursor = page.get("nextCursor")
            if cursor is None:
                return
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError("Invalid Codex history cursor")
            seen.add(cursor)
        raise ValueError("Codex history page limit exceeded")

    def _paginated_thread(self, thread):
        # Official generated protocol: turns supply metadata, item entries
        # carry turnId; item cursors can split a turn across any page boundary.
        turns = {}
        for turn in self._pages("thread/turns/list", itemsView="notLoaded"):
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
                raise ValueError("Invalid Codex turn history")
            if turn["id"] not in turns:
                turns[turn["id"]] = {**turn, "items": [], "itemsView": "full"}
        seen = set()
        for entry in self._pages("thread/items/list"):
            if not isinstance(entry, dict) or entry.get("turnId") not in turns:
                raise ValueError("Invalid Codex item history")
            item = entry.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("Invalid Codex item history")
            key = (entry["turnId"], item["id"])
            if key not in seen:
                turns[entry["turnId"]]["items"].append(item)
                seen.add(key)
        return {**thread, "historyMode": "legacy", "turns": list(turns.values())}

    def refresh(self) -> bool:
        try:
            return self._refresh()
        except Exception:
            # This reader is also used by Find, export and Properties. Never
            # let backend details or private payloads enter their error text.
            raise ValueError("Couldn't load this Codex chat. Try again, or open it in Codex to read it.") from None

    def _restore_tasks(self, history):
        if self._plan_store is None:
            return
        turn_id, tasks = self._plan_store.load(self.native_id)
        if turn_id in self._turn_ids:
            position = self._turn_ids.index(turn_id) + 1
            for task in tasks:
                task.turn = task.changed_turn = position
            history.tasks = list(tasks)
        else:
            history.tasks = []

    def _refresh(self) -> bool:
        # Codex saves a new thread only at its first turn, so until then only
        # the process that created it knows it. Only positive lifecycle
        # knowledge from the runtime allows an empty history here.
        route = self._route(self.native_id) if self._route is not None else None
        if route is EMPTY_HISTORY:
            return False
        connection = route or self._discovery
        if connection is not self.connection:
            # Another process's stamps and failures say nothing about this one.
            self.connection = connection
            self._history_stamp = self._failed_stamp = None
            self._history_checked = 0.0
        response = self.connection.request("thread/read", {"threadId": self.native_id,
                                                         "includeTurns": False})
        thread = response.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != self.native_id:
            raise ValueError("Codex returned a different or missing thread")
        stamp = (thread.get("updatedAt"), thread.get("status"))
        if (self._history_stamp == stamp and self._history_checked
                and time.monotonic() - self._history_checked < 30):
            before = self.transcript.tasks
            self._restore_tasks(self.transcript)
            return before != self.transcript.tasks
        if self._failed_stamp == stamp:
            raise ValueError("History unavailable until the chat changes")
        # Legacy threads can be enormous too. Never ask for an unbounded inline
        # transcript: both history modes use the official bounded page APIs.
        try:
            thread = self._paginated_thread(thread)
            history = transcript_from_thread(thread)
            self._turn_ids = [turn["id"] for turn in thread["turns"]]
            self._restore_tasks(history)
        except Exception:
            self._failed_stamp = stamp
            raise
        self._failed_stamp = None
        changed = history != self.transcript
        self.transcript = history
        self._history_stamp = stamp
        self._history_checked = time.monotonic()
        return changed


class CodexProvider:
    provider_id = "codex"
    LAST_READ_BUDGET = 8
    MAX_LAST_MESSAGES = 256
    LAST_MESSAGE_SECONDS = 30

    def __init__(self, connection: RpcConnection | None = None, *, executable=None,
                 execution=False, on_update=lambda state: None, runtime=None):
        self.connection = (connection if connection is not None
                           else ManagedCodexConnection(executable))
        self._last_messages = OrderedDict()
        self._last_lock = threading.Lock()
        self._last_cursor = 0
        self._last_generation = 0
        self._last_closed = False
        self.runtime = runtime
        if execution and runtime is None:
            from .codex_runtime import CodexChatRuntime
            self.runtime = CodexChatRuntime(on_update, executable=executable)

    def saved_rule_count(self, cwd):
        """Worker-only inventory; never creates or resumes a chat."""
        from .codex_rpc import verify_codex_rule_layers
        result = self.connection.request('config/read', {'cwd': cwd, 'includeLayers': True})
        return verify_codex_rule_layers(result)

    def saved_rules(self, cwd):
        """Worker-only: the rules in Codex's saved rule files, read only (#307),
        as ``codex_saved_rules.describe`` takes them. One scan of the folders,
        so the list can't claim nothing is allowed when they weren't read."""
        from .. import codex_saved_rules, platform_paths
        result = self.connection.request('config/read', {'cwd': cwd, 'includeLayers': True})
        layers = result.get('layers') if isinstance(result, dict) else None
        files, blocked = platform_paths.codex_saved_rule_files(layers)
        if files is None:
            return [], [], 0, codex_saved_rules.UNCHECKED
        problem = codex_saved_rules.project_rules(blocked) if blocked else ''
        return (*codex_saved_rules.rules_in(files), problem)

    def model_choices(self):
        """Worker-only discovery; never creates or resumes an owned chat."""
        return read_models(self.connection)

    def close(self):
        with self._last_lock:
            self._last_closed = True
        self.forget_last_messages()
        failures = []
        for owner in (self.connection, self.runtime):
            close = getattr(owner, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    failures.append(True)
        if failures:
            raise ValueError("Codex cleanup failed")

    def forget_last_messages(self):
        with self._last_lock:
            self._last_messages.clear()
            self._last_cursor = 0
            self._last_generation += 1

    def cached_last_messages(self, sessions):
        """Keep known display lines during an outage without making RPC calls."""
        with self._last_lock:
            if self._last_closed:
                return
            cache = dict(self._last_messages)
        for info in sessions:
            self._check(info)
            cached = cache.get(info.identity)
            info.last_message = cached[2] if cached and cached[0] == info.last_activity_ms else ""

    def fill_last_messages(self, sessions):
        """Warm the optional column fairly, without reading every history at once.

        Cache only a short display line, not private full transcripts. Activity
        changes invalidate it immediately; unchanged rows are periodically
        refreshed. Round-robin budgeting prevents a large catalog starving old
        rows. A read failure stops this pass rather than spawning reconnects for
        every remaining history (#184) (#171).
        """
        rows = list(sessions)
        now = time.monotonic()
        with self._last_lock:
            if self._last_closed:
                return
            cache = dict(self._last_messages)
            start = self._last_cursor % len(rows) if rows else 0
            generation = self._last_generation
        for info in rows:
            self._check(info)
            cached = cache.get(info.identity)
            info.last_message = cached[2] if cached and cached[0] == info.last_activity_ms else ""
        reads = 0
        for offset in range(len(rows)):
            index = (start + offset) % len(rows)
            info = rows[index]
            cached = cache.get(info.identity)
            if (cached and cached[0] == info.last_activity_ms
                    and (cached[3] or now - cached[1] < self.LAST_MESSAGE_SECONDS)):
                continue
            failed = False
            try:
                reader = self.open_reader(info)
                reader.refresh()
                message = next((m for m in reversed(reader.transcript.messages)
                                if m.kind in (USER, ASSISTANT) and m.text.strip()), None)
                line = last_message_line(message.label, message.text) if message else ""
            except Exception:  # noqa: BLE001 - optional history cannot erase the catalog
                line, failed = "", True
            with self._last_lock:
                if self._last_closed or generation != self._last_generation:
                    return  # a hidden column/closed owner cannot regain old reads
                info.last_message = line
                self._last_messages[info.identity] = (info.last_activity_ms, now, line, failed)
                self._last_messages.move_to_end(info.identity)
                while len(self._last_messages) > self.MAX_LAST_MESSAGES:
                    self._last_messages.popitem(last=False)
                self._last_cursor = index + 1
            reads += 1
            if failed or reads >= self.LAST_READ_BUDGET:
                break

    def collect(self) -> Snapshot:
        sessions, ids = [], set()
        for archived in (False, True):
            cursor, cursors = None, set()
            while True:
                params = {"archived": archived, "limit": 100,
                          "sourceKinds": ["cli", "vscode", "exec", "appServer", "unknown"],
                          "sortKey": "updated_at"}
                if cursor is not None:
                    params["cursor"] = cursor
                response = self.connection.request("thread/list", params)
                data = response.get("data")
                if not isinstance(data, list):
                    raise ValueError("Codex returned an invalid thread list")
                for thread in data:
                    if not isinstance(thread, dict):
                        raise ValueError("Codex returned invalid thread metadata")
                    if thread.get("parentThreadId"):
                        continue
                    info = session_from_thread(thread, archived)
                    if info.identity not in ids:
                        ids.add(info.identity)
                        sessions.append(info)
                cursor = response.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise ValueError("Codex returned invalid or repeated pagination cursor")
                cursors.add(cursor)
        if self.runtime is not None:
            for info in sessions:
                if self.runtime.known(info.cli_session_id):
                    state = self.runtime.snapshot(info.cli_session_id)
                    info.state = state.state
                    if getattr(state, 'problem', ''):
                        info.detail = state.problem
        return Snapshot(sessions=sessions)

    def _check(self, session):
        if session.provider != self.provider_id or not session.cli_session_id:
            raise ValueError("Not a Codex session")

    def capabilities(self, session):
        from .base import SessionCapabilities

        self._check(session)
        # Persisted ownership alone cannot send: runtime proofs and fresh
        # subscription/configuration/concurrency gates remain at execution.
        enabled = self.runtime is not None
        return SessionCapabilities(usage=True, changes=True, send=enabled and session.is_own, fork=enabled,
                                   approvals=enabled and session.is_own,
                                   steer=enabled and session.is_own)

    def usage_lines(self, session):
        self._check(session)
        state = (self.runtime.snapshot(session.cli_session_id) if self.runtime is not None
                 and self.runtime.known(session.cli_session_id) else None)
        tokens = state.token_usage if state is not None else None
        lines = context_lines(tokens)
        if state is not None and (state.problem or state.state == STATUS_UNAVAILABLE) and tokens is not None:
            lines.append('Last reported usage before the connection became unavailable; it may be out of date.')
        return lines + read_usage(self.connection)

    def usage_warnings(self, session):
        self._check(session)
        return read_warnings(self.connection)

    def can_create(self):
        return self.runtime is not None

    def create_owned(self, cwd, title, *, model=None, effort=None, sandbox=None, approval_policy=None, approvals_reviewer=None):
        options = {key: value for key, value in (('sandbox', sandbox), ('approval_policy', approval_policy))
                   if value is not None}
        if approvals_reviewer is not None:
            options['approvals_reviewer'] = approvals_reviewer
        return self.runtime.create(cwd, title, model=model, effort=effort, **options)

    def fork_owned(self, source, title, *, model=None, effort=None, sandbox=None, approval_policy=None, approvals_reviewer=None):
        options = {key: value for key, value in (('sandbox', sandbox), ('approval_policy', approval_policy))
                   if value is not None}
        if approvals_reviewer is not None:
            options['approvals_reviewer'] = approvals_reviewer
        return self.runtime.fork(source, title, model=model, effort=effort, **options)

    def prepare_policy_owned(self, record, sandbox, approval_policy, approvals_reviewer='user'):
        return self.runtime.prepare_policy(record, sandbox, approval_policy, approvals_reviewer)

    def finish_policy_owned(self, record, change, saved):
        if record.cli_session_id != change.proof.native_id:
            raise ValueError('Codex permission change belongs to another chat')
        return self.runtime.finish_policy(change, saved)

    def reopen_owned(self, record):
        return self.runtime.reopen(record)

    def recover_owned(self, record):
        return self.runtime.recover(record)

    def prepare_owned(self, record):
        return self.runtime.prepare(record)

    def send_owned(self, record, text):
        return self.runtime.send(record, text)

    def stop_owned(self, record):
        return self.runtime.stop(record)

    def steer_owned(self, record, text):
        return self.runtime.steer(record, text)

    def steer_queued_owned(self, record, index, text):
        return self.runtime.steer(record, text, index=index)

    def answer_owned(self, record, request, result):
        return self.runtime.answer(record, request, result)

    def chat_rule_candidate_owned(self, record, request):
        return self.runtime.chat_rule_candidate(record, request)

    def allow_for_chat_owned(self, record, request):
        return self.runtime.allow_for_chat(record, request)

    def chat_rules_owned(self, record):
        return self.runtime.chat_rules(record)

    def review_history_owned(self, record):
        return self.runtime.review_store.load(record.cli_session_id)

    def remove_chat_rule_owned(self, record, rule_id):
        return self.runtime.remove_chat_rule(record, rule_id)

    def owned_ready(self, record):
        return self.runtime is not None and self.runtime.ready(record)

    def has_history(self, session) -> bool:
        self._check(session)
        return True  # availability is verified by the background thread/read

    def open_reader(self, session) -> CodexReader:
        self._check(session)
        route = self.runtime.history_route if session.is_own and self.runtime is not None else None
        from ..provider_storage import CodexPlanStore
        return CodexReader(self.connection, session.cli_session_id, route=route,
                           plan_store=CodexPlanStore() if session.is_own else None)

    def last_reply(self, session) -> str:
        reader = self.open_reader(session)
        reader.refresh()
        reply = reader.transcript.last_reply()
        return reply.text if reply else ""
