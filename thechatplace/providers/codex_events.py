"""Typed streaming data; no UI calls, tool permissions or inference (#184)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CodexEvent:
    kind: str
    thread_id: str
    turn_id: str
    item_id: str = ""
    text: str = ""
    status: str = ""


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Invalid Codex event identity")
    return value


def _text(value):
    # A bounded queue must also bound each entry, including final item text.
    if not isinstance(value, str) or len(value) > 256 * 1024:
        raise ValueError("Invalid Codex event text")
    return value


def decode_event(method, params):
    """Known malformed events fail closed; future notification types are ignored.

    Keep IDs separate for interleaved chats. Final item text is authoritative,
    rather than assuming deltas describe the final reply. Backend error bodies
    are not copied into these displayable events.
    """
    deltas = {"item/agentMessage/delta": "text_delta",
              "item/commandExecution/outputDelta": "tool_output_delta"}
    if method not in (*deltas, "turn/started", "turn/completed", "item/completed"):
        return None
    if not isinstance(params, dict):
        raise ValueError("Invalid Codex event parameters")
    thread_id = _id(params.get("threadId"))
    if method in deltas:
        return CodexEvent(deltas[method], thread_id, _id(params.get("turnId")),
                          _id(params.get("itemId")), _text(params.get("delta")))
    if method == "item/completed":
        item = params.get("item")
        if not isinstance(item, dict):
            raise ValueError("Invalid Codex completed item")
        if item.get("type") != "agentMessage":
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

    @property
    def replies(self):
        return tuple(self._texts.items())  # immutable snapshot, stable item order

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
        if event.kind == "turn_completed":
            if event.status not in ("completed", "failed", "interrupted"):
                self.disconnect()
                raise ValueError("Invalid Codex terminal outcome")
            if event.status == "completed" and self._texts.keys() - self._final:
                self.disconnect()
                raise ValueError("Codex reply is incomplete; reconcile history")
            self.status = event.status
            return True
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
        characters = self._characters - len(previous) + len(replacement)
        if (characters > self.MAX_TEXT
                or (item_id not in self._texts and len(self._texts) >= self.MAX_ITEMS)):
            self.disconnect()
            raise ValueError("Codex reply buffer limit reached; reconcile history")
        self._texts[item_id] = replacement
        self._characters = characters
        if event.kind == "text_completed":
            self._final.add(item_id)
        return replacement != previous or event.kind == "text_completed"
