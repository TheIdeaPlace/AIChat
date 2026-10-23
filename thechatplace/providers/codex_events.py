"""Typed streaming data; no UI calls, tool permissions or inference (#184)."""
from dataclasses import dataclass
import time
import hashlib
from ..tasklist import TaskStep
from .codex_tokens import CodexTokenUsage, decode_tokens


@dataclass(frozen=True)
class CodexTokenUsageEvent:
    thread_id: str
    turn_id: str
    usage: CodexTokenUsage


@dataclass(frozen=True)
class CodexEvent:
    kind: str
    thread_id: str
    turn_id: str
    item_id: str = ""
    text: str = ""
    status: str = ""
    plan: tuple = ()
    #: A file change's paths, once Codex says it was made (agent
    #: conversations pass them on with the reply).
    paths: tuple = ()


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Invalid Codex event identity")
    return value


DISPLAY_NOTE = '\n[Display truncated; open the chat history to read the rest.]'


def _text(value, limit=256 * 1024):
    if not isinstance(value, str):
        raise ValueError("Invalid Codex event text")
    return value if len(value) <= limit else value[:max(0, limit-len(DISPLAY_NOTE))] + DISPLAY_NOTE[:limit]


def decode_event(method, params):
    """Known malformed events fail closed; future notification types are ignored.

    Keep IDs separate for interleaved chats. Final item text is authoritative,
    rather than assuming deltas describe the final reply. Backend error bodies
    are not copied into these displayable events.
    """
    if method == 'turn/plan/updated':
        # Optional display data must never break execution on a bad shape.
        if not isinstance(params, dict):
            return None
        try:
            thread_id, turn_id = _id(params.get('threadId')), _id(params.get('turnId'))
            plan = params.get('plan')
            if not isinstance(plan, list) or len(plan) > 256:
                return None
            steps = []
            states = {'pending': 'pending', 'inProgress': 'in_progress', 'completed': 'completed'}
            for entry in plan:
                if (not isinstance(entry, dict) or not isinstance(entry.get('step'), str)
                        or not entry['step'].strip() or len(entry['step']) > 4096
                        or not isinstance(entry.get('status'), str)):
                    return None
                steps.append((entry['step'], states.get(entry['status'], 'pending')))
            return CodexEvent('plan_updated', thread_id, turn_id, plan=tuple(steps))
        except ValueError:
            return None
    if method == 'thread/tokenUsage/updated':
        if not isinstance(params, dict):
            return None
        tokens = decode_tokens(params.get('tokenUsage'))
        if tokens is None:
            return None
        try:
            return CodexTokenUsageEvent(_id(params.get('threadId')), _id(params.get('turnId')), tokens)
        except ValueError:
            return None
    deltas = {"item/agentMessage/delta": "text_delta",
              "item/commandExecution/outputDelta": "tool_output_delta"}
    if method not in (*deltas, "turn/started", "turn/completed", "item/completed", "item/started"):
        return None
    if not isinstance(params, dict):
        raise ValueError("Invalid Codex event parameters")
    thread_id = _id(params.get("threadId"))
    if method in deltas:
        return CodexEvent(deltas[method], thread_id, _id(params.get("turnId")),
                          _id(params.get("itemId")), _text(params.get("delta")))
    if method in ("item/completed", "item/started"):
        item = params.get("item")
        if not isinstance(item, dict):
            raise ValueError("Invalid Codex completed item")
        if item.get('type') == 'contextCompaction' and method == 'item/completed':
            try:
                return CodexEvent('context_compacted', thread_id, _id(params.get('turnId')), _id(item.get('id')))
            except ValueError:
                return None
        activity = {"reasoning": "Thinking.", "mcpToolCall": "Using an MCP tool.",
                    "webSearch": "Searching the web.", "commandExecution": "Running a command.",
                    "agentMessage": "Writing a reply."}
        if item.get("type") in activity and (method == "item/started" or item.get("type") != "agentMessage"):
            text = activity[item["type"]] if method == "item/started" else "Codex is working."
            if item["type"] == "commandExecution" and method == "item/started":
                command = " ".join(_text(item.get("command", "")).split())
                if command:
                    text = "Running command: " + command[:200]
            return CodexEvent("activity", thread_id, _id(params.get("turnId")),
                              _id(item.get("id")), text)
        if item.get("type") == "fileChange":
            changes = item.get("changes")
            if not isinstance(changes, list):
                raise ValueError("Invalid Codex file changes")
            pieces, paths = [], []
            for change in changes[:128]:
                if not isinstance(change, dict):
                    raise ValueError("Invalid Codex file change")
                kind = change.get("kind")
                if not isinstance(kind, dict) or kind.get("type") not in ("add", "delete", "update"):
                    raise ValueError("Invalid Codex file change kind")
                path = _text(change.get("path"))
                if not path.strip():
                    raise ValueError("Invalid Codex file change path")
                heading = f"{kind['type'].capitalize()}: {path}"
                paths.append(path)
                if kind.get("move_path") is not None:
                    heading += "\nMove to: " + _text(kind["move_path"])
                    paths.append(_text(kind["move_path"]))
                pieces.append(heading + "\n" + _text(change.get("diff")))
            if len(changes) > 128:
                pieces.append(DISPLAY_NOTE)
            # Only a change that was made counts as changed: not one still
            # starting, nor one you declined or that failed.
            made = method == "item/completed" and item.get("status") in (None, "completed")
            return CodexEvent("file_changes", thread_id, _id(params.get("turnId")),
                              _id(item.get("id")), _text("\n\n".join(pieces)),
                              paths=tuple(p for p in paths if p.strip()) if made else ())
        if item.get("type") != "agentMessage" or method == "item/started":
            return None  # tool/question conversion is a separate checkpoint
        return CodexEvent("text_completed", thread_id, _id(params.get("turnId")),
                          _id(item.get("id")), _text(item.get("text")))
    turn = params.get("turn")
    if not isinstance(turn, dict):
        raise ValueError("Invalid Codex turn event")
    status = turn.get("status")
    allowed = ("inProgress",) if method == "turn/started" else ("completed", "failed", "interrupted")
    if status not in allowed:
        raise ValueError("Invalid Codex turn outcome")
    return CodexEvent("turn_started" if method == "turn/started" else "turn_completed",
                      thread_id, _id(turn.get("id")), status=status)


class CodexTurnBuffer:
    """One worker's reply state for an explicitly selected thread/turn pair.

    This is not an ownership or billing authorization. The future executor
    supplies IDs from its verified turn response, and handles tool activity
    separately. No restart or prompt retry is performed here.
    """
    MAX_ITEMS = 128
    MAX_TEXT = 1024 * 1024

    def __init__(self, thread_id, turn_id):
        self.thread_id, self.turn_id = _id(thread_id), _id(turn_id)
        self.status = "inProgress"
        self.needs_reconciliation = False
        self._texts = {}
        self._final = set()
        self._characters = 0
        self._file_changes = {}
        self._file_paths = {}
        self._file_characters = 0
        self.truncated = False
        self.started_at = time.monotonic()
        self.activity = "Codex is working."
        self.tasks = ()
        self.plan_received = False

    @property
    def replies(self):
        return tuple(self._texts.items()) + ((("display-limit", DISPLAY_NOTE.strip()),) if self.truncated else ())

    @property
    def file_changes(self):
        return tuple(self._file_changes.items())

    @property
    def changed_paths(self):
        """Each file a change that was made touched, once, in order."""
        return tuple(dict.fromkeys(p for paths in self._file_paths.values() for p in paths))

    def disconnect(self):
        if self.status == "inProgress":
            self.status = "unknown"
            self.needs_reconciliation = True

    def apply(self, event):
        if not isinstance(event, CodexEvent):
            raise ValueError("Invalid Codex event")
        if ((event.thread_id, event.turn_id) != (self.thread_id, self.turn_id)
                or self.status != "inProgress"):
            return False
        if event.kind == 'plan_updated':
            # There are no native step IDs. Text plus occurrence survives a
            # reorder without inventing continuity for renamed steps (#318).
            occurrences, tasks = {}, []
            for subject, status in event.plan:
                occurrence = occurrences.get(subject, 0)
                occurrences[subject] = occurrence + 1
                identity = hashlib.sha256(subject.encode('utf-8')).hexdigest() + ':' + str(occurrence)
                tasks.append(TaskStep(identity, subject, status=status))
            changed = not self.plan_received or self.tasks != tuple(tasks)
            self.tasks, self.plan_received = tuple(tasks), True
            return changed
        if event.kind == 'display_truncated':
            self.truncated = True
            return True
        if event.kind == "turn_completed":
            if event.status not in ("completed", "failed", "interrupted"):
                self.disconnect()
                raise ValueError("Invalid Codex terminal outcome")
            if event.status == "completed" and self._texts.keys() - self._final:
                self.truncated = True  # terminal identity is authoritative; refresh full history
            self.status = event.status
            return True
        if event.kind == "file_changes":
            try:
                item_id, text = _id(event.item_id), _text(event.text)
                if item_id not in self._file_changes and len(self._file_changes) >= self.MAX_ITEMS:
                    self.truncated = True
                    return True
                remaining = max(0, self.MAX_TEXT - self._file_characters + len(self._file_changes.get(item_id, "")))
                if len(text) > remaining:
                    self.truncated = True
                    text = text[:remaining]
                self._file_characters += len(text) - len(self._file_changes.get(item_id, ""))
                self._file_changes[item_id] = text
                self._file_paths[item_id] = tuple(event.paths)
                self.activity = "Reviewing file changes."
                return True
            except ValueError:
                self.disconnect()
                raise
        if event.kind == "activity":
            try:
                _id(event.item_id)
                self.activity = _text(event.text)
                return True
            except ValueError:
                self.disconnect()
                raise
        if event.kind not in ("text_delta", "text_completed"):
            return False
        try:
            item_id, text = _id(event.item_id), _text(event.text)
        except ValueError:
            self.disconnect()
            raise
        if item_id in self._final:
            return False  # completed item is authoritative, including late deltas
        previous = self._texts.get(item_id, "")
        replacement = previous + text if event.kind == "text_delta" else text
        # Keep finished answers available for background speech even after a
        # long commentary stream. Diffs have their own independent budget.
        if event.kind == 'text_completed':
            for old_id in tuple(self._texts):
                if (len(self._texts) < self.MAX_ITEMS or item_id in self._texts) and (
                        self._characters - len(previous) + len(replacement) <= self.MAX_TEXT):
                    break
                if old_id == item_id:
                    continue
                self._characters -= len(self._texts.pop(old_id))
                self._final.discard(old_id)
                self.truncated = True
        if item_id not in self._texts and len(self._texts) >= self.MAX_ITEMS:
            self.truncated = True
            return True
        remaining = max(0, self.MAX_TEXT - self._characters + len(previous))
        if len(replacement) > remaining:
            self.truncated = True
            replacement = replacement[:remaining]
        characters = self._characters - len(previous) + len(replacement)
        self._texts[item_id] = replacement
        self.activity = "Writing a reply."
        self._characters = characters
        if event.kind == "text_completed":
            self._final.add(item_id)
        return replacement != previous or event.kind == "text_completed"
