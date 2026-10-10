"""Turn a Claude Code transcript (``<session>.jsonl``) into a readable chat.

The transcript format is internal to Claude Code and changes between
versions (https://code.claude.com/docs/en/sessions), so this module is the one
place that knows it, and it is written to survive change:

* **Unknown record types are skipped silently.** There are many (attachment,
  custom-title, pr-link, queue-operation, mode, ...) and new ones appear; none
  of them is conversation.
* **A line that will not parse, or a user/assistant record shaped in a way we
  do not expect, is counted, never raised.** The UI says "couldn't read N
  lines" instead of crashing.
* ``message.content`` may be a plain string or a list of blocks; both work.

What the chat shows, and why:

* ``You`` and ``Claude`` text. Assistant replies are written one content block
  per record, so records sharing a ``message.id`` are merged back into one
  message.
* Question cards (``AskUserQuestion``) and their answers, permission denials,
  plans (``ExitPlanMode``), API errors and interruptions are shown, because
  they explain where the conversation went.
* Tool calls, tool results, thinking, meta/context records, compaction
  summaries and harness events (``<task-notification>`` and friends) are
  "activity": hidden unless the reader turns on Show Tool Activity.
* Sidechain (subagent) records are left out; they live in their own files.
* Messages from other Claude sessions (``SendMessage``), on this computer or
  another one through Remote Control, are shown as from that session (#123),
  not as something you typed. One that arrives while Claude is working is an
  ``attachment`` record (``queued_command`` with ``origin.kind == "peer"``);
  one that starts a turn is a user message wrapped in
  ``<cross-session-message from="..." from-name="...">``.

No wx imports: everything here is testable on its own.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .changes import EDIT_TOOLS, FileEdit, edit_from_tool

# Message kinds. The label is what a screen reader hears first on each line.
USER = "user"
ASSISTANT = "assistant"
QUESTION = "question"
ANSWER = "answer"
DENIED = "denied"
PLAN = "plan"
ERROR = "error"
INTERRUPTED = "interrupted"
TOOL = "tool"
#: A message from another Claude session (#123). ``ChatMessage.sender`` names it.
PEER = "peer"
#: Not from the transcript: a message of yours waiting for the turn to end
#: (#50), shown at the end of the messages list.
QUEUED = "queued"
TOOL_RESULT = "tool_result"
CONTEXT = "context"
EVENT = "event"

LABELS = {
    USER: "You",
    ASSISTANT: "Claude",
    QUESTION: "Claude asked",
    ANSWER: "You answered",
    DENIED: "Permission denied",
    PLAN: "Claude's plan",
    ERROR: "Error",
    INTERRUPTED: "Interrupted",
    TOOL: "Tool",
    PEER: "From another session",
    QUEUED: "Queued",
    TOOL_RESULT: "Tool result",
    CONTEXT: "Context",
    EVENT: "Event",
}

#: Kinds hidden unless "show tool activity" is on.
ACTIVITY_KINDS = frozenset({TOOL, TOOL_RESULT, CONTEXT, EVENT})


@dataclass
class ChatMessage:
    kind: str
    text: str
    timestamp: str = ""
    key: str = ""  # stable identity (record uuid, or message id for merged replies)
    #: For PEER messages: the session it came from, as that session named itself.
    sender: str = ""

    @property
    def label(self) -> str:
        if self.kind == PEER and self.sender:
            return f"From {self.sender}"
        return LABELS.get(self.kind, self.kind.capitalize())

    @property
    def is_activity(self) -> bool:
        return self.kind in ACTIVITY_KINDS

    def first_line(self, limit: int = 300) -> str:
        for line in self.text.splitlines():
            line = line.strip()
            if line:
                return line if len(line) <= limit else line[: limit - 1] + "…"
        return "(empty)"

    def list_line(self) -> str:
        """One line for the chat list: who spoke, then the first line."""
        return f"{self.label}: {self.first_line()}"

    def full_text(self) -> str:
        return f"{self.label}:\n{self.text}"


@dataclass
class Transcript:
    messages: List[ChatMessage] = field(default_factory=list)
    unreadable_lines: int = 0
    lines_read: int = 0
    #: How full the context was after Claude's latest reply (#19): its input,
    #: cached input and output tokens. 0 until a reply says, and again after
    #: the conversation is compacted.
    context_tokens: int = 0
    #: The model of Claude's latest reply ("claude-opus-5-5").
    model: str = ""
    compactions: int = 0
    #: How many messages you've sent: the turn a file change belongs to.
    turns: int = 0
    #: Files Claude changed, in order (#18).
    edits: List[FileEdit] = field(default_factory=list)

    def latest_turn_edits(self) -> List[FileEdit]:
        """The changes made since your latest message."""
        return [e for e in self.edits if e.turn == self.turns]

    def visible(self, show_activity: bool = False) -> List[ChatMessage]:
        if show_activity:
            return list(self.messages)
        return [m for m in self.messages if not m.is_activity]

    def last_reply(self) -> Optional[ChatMessage]:
        for message in reversed(self.messages):
            if message.kind in (ASSISTANT, QUESTION, PLAN, ERROR):
                return message
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_HARNESS_TAG = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9_-]*)>")
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_ANY_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9_-]*>")
_INTERRUPT = re.compile(r"^\[Request interrupted by user[^\]]*\]", re.IGNORECASE)
_CROSS_SESSION = re.compile(r"^\s*<cross-session-message\b([^>]*)>(.*?)(?:</cross-session-message>\s*)?$",
                            re.DOTALL)
_ATTRIBUTE = re.compile(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"')

# Tool input fields worth naming in a one-line activity summary, in order.
_TOOL_SUMMARY_FIELDS = ("description", "command", "file_path", "path", "pattern",
                        "url", "query", "prompt", "skill", "subject")


class _Unreadable(Exception):
    """A user/assistant record whose shape we do not understand."""


class TranscriptParser:
    """Incremental parser: ``feed`` lines as the file grows.

    State carried between calls lets a tail of new lines be parsed without
    re-reading a 20 MB file: which tool calls were question cards, and which
    assistant message id the last reply belonged to.
    """

    def __init__(self) -> None:
        self.transcript = Transcript()
        self._tool_names: Dict[str, str] = {}  # tool_use_id -> tool name
        self._edit_inputs: Dict[str, Tuple[str, dict]] = {}  # file-changing calls
        self._last_assistant_id: Optional[str] = None

    # -- public -------------------------------------------------------------

    def feed(self, lines: Iterable[str]) -> List[ChatMessage]:
        """Parse more lines; returns the messages added or changed by them."""
        touched: List[ChatMessage] = []
        for line in lines:
            if not line.strip():
                continue
            self.transcript.lines_read += 1
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                self.transcript.unreadable_lines += 1
                continue
            if not isinstance(record, dict):
                self.transcript.unreadable_lines += 1
                continue
            try:
                touched.extend(self._record(record))
            except _Unreadable:
                self.transcript.unreadable_lines += 1
            except Exception:  # noqa: BLE001 - a format change must never crash the app
                self.transcript.unreadable_lines += 1
        return touched

    # -- records ------------------------------------------------------------

    def _record(self, record: dict) -> List[ChatMessage]:
        kind = record.get("type")
        if record.get("isSidechain"):
            return []
        if kind == "system" and record.get("subtype") == "compact_boundary":
            # Claude Code summarised the conversation to free the context.
            self.transcript.compactions += 1
            self.transcript.context_tokens = 0
            meta = record.get("compactMetadata")
            trigger = meta.get("trigger") if isinstance(meta, dict) else ""
            how = {"manual": " (you asked)", "auto": " (the context was full)"}.get(trigger, "")
            return [self._add(EVENT, f"Conversation compacted{how}.", record)]
        if kind == "attachment":
            return self._attachment(record)
        if kind not in ("user", "assistant"):
            return []  # custom-title, pr-link, ...: not conversation
        message = record.get("message")
        if not isinstance(message, dict):
            raise _Unreadable()
        content = message.get("content")
        if kind == "user":
            self._last_assistant_id = None
            return self._user(record, content)
        self._note_usage(message)
        return self._assistant(record, message, content)

    def _note_usage(self, message: dict) -> None:
        usage = message.get("usage")
        if isinstance(usage, dict):
            total = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                "output_tokens") if isinstance(usage.get(k), (int, float)))
            if total:
                self.transcript.context_tokens = total
        model = message.get("model")
        if isinstance(model, str) and model and not model.startswith("<"):
            self.transcript.model = model

    def _add(self, kind: str, text: str, record: dict, key: str = "") -> ChatMessage:
        item = ChatMessage(kind=kind, text=text.strip(),
                           timestamp=str(record.get("timestamp") or ""),
                           key=key or str(record.get("uuid") or ""))
        self.transcript.messages.append(item)
        return item

    # user ------------------------------------------------------------------

    def _user(self, record: dict, content) -> List[ChatMessage]:
        added: List[ChatMessage] = []
        if record.get("isCompactSummary"):
            added.append(self._add(CONTEXT, "Summary of the earlier conversation:\n"
                                   + _plain_text(content), record))
            return added
        if record.get("isMeta"):
            text = _plain_text(content)
            if text.strip():
                added.append(self._add(CONTEXT, text, record))
            return added

        if isinstance(content, str):
            added.extend(self._user_text(content, record))
            return added
        if not isinstance(content, list):
            raise _Unreadable()

        texts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text") or ""))
            elif btype == "image":
                texts.append("(image attached)")
            elif btype == "tool_result":
                added.append(self._tool_result(block, record))
        if texts:
            added.extend(self._user_text("\n".join(texts), record))
        return added

    def _attachment(self, record: dict) -> List[ChatMessage]:
        """Only one kind of attachment is conversation: a message from another
        session that arrived while Claude was working (#123)."""
        attachment = record.get("attachment")
        if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
            return []
        origin = attachment.get("origin")
        if not isinstance(origin, dict) or origin.get("kind") != "peer":
            return []
        # A subagent's report coming back to its session has the same shape
        # (origin.handback, senderTaskId, an <agent-message> prompt). That's
        # Claude's own helper, not another session: leave it out.
        prompt = str(attachment.get("prompt") or "")
        if (origin.get("handback") or origin.get("senderTaskId")
                or attachment.get("senderTaskId")
                or prompt.lstrip().startswith("<agent-message")):
            return []
        parsed = _cross_session(prompt)
        body = origin.get("body")
        if not isinstance(body, str) or not body.strip():
            if parsed is None:
                return []
            body = parsed[1]
        # The session's own name. An address ("bridge:session_...", a pipe)
        # is noise to listen to, so without a name it's "another session".
        sender = str(origin.get("name") or (parsed[0] if parsed else "") or "")
        key = str(attachment.get("delivery_id") or record.get("uuid") or "")
        return self._peer(sender, body, record, key)

    def _peer(self, sender: str, body: str, record: dict, key: str = "") -> List[ChatMessage]:
        body = _SYSTEM_REMINDER.sub("", body).strip()
        if not body:
            return []
        item = self._add(PEER, body, record, key=key)
        item.sender = " ".join(sender.split()) or "another session"
        return [item]

    def _user_text(self, text: str, record: dict) -> List[ChatMessage]:
        peer = _cross_session(text)
        if peer is not None:
            # Another session's message started this turn. It counts as a
            # turn, so the files Claude changes for it are listed with it.
            added = self._peer(peer[0], peer[1], record)
            if added:
                self.transcript.turns += 1
            return added
        tag = _HARNESS_TAG.match(text)
        if tag:
            # <task-notification>, <ci-monitor-event>, <command-name>, ...:
            # written by the harness, not typed by the person.
            body = _ANY_TAG.sub(" ", _SYSTEM_REMINDER.sub("", text))
            body = re.sub(r"[ \t]+", " ", body).strip()
            name = tag.group(1).replace("-", " ")
            return [self._add(EVENT, f"{name}: {body}" if body else name, record)]
        text = _SYSTEM_REMINDER.sub("", text).strip()
        if not text:
            return []
        if _INTERRUPT.match(text):
            return [self._add(INTERRUPTED, "You stopped Claude.", record)]
        self.transcript.turns += 1
        return [self._add(USER, text, record)]

    def _tool_result(self, block: dict, record: dict) -> ChatMessage:
        tool_id = str(block.get("tool_use_id") or "")
        name = self._tool_names.get(tool_id, "")
        body = _plain_text(block.get("content"))
        edit_call = self._edit_inputs.pop(tool_id, None)
        if edit_call is not None and not block.get("is_error"):
            edit = edit_from_tool(edit_call[0], edit_call[1], record.get("toolUseResult"),
                                  self.transcript.turns)
            if edit is not None:
                self.transcript.edits.append(edit)
        if name == "AskUserQuestion":
            return self._add(ANSWER, _format_answers(record.get("toolUseResult"), body),
                             record, key=f"{record.get('uuid')}:{tool_id}")
        denial = record.get("toolDenialKind")
        if block.get("is_error") and denial:
            reason = _first_nonblank(body) or str(denial)
            what = f"{name}: " if name else ""
            return self._add(DENIED, f"{what}{reason}", record,
                             key=f"{record.get('uuid')}:{tool_id}")
        label = f"{name} " if name else ""
        prefix = "failed" if block.get("is_error") else "returned"
        return self._add(TOOL_RESULT, f"{label}{prefix}: {body}".strip(), record,
                         key=f"{record.get('uuid')}:{tool_id}")

    # assistant ---------------------------------------------------------------

    def _assistant(self, record: dict, message: dict, content) -> List[ChatMessage]:
        msg_id = str(message.get("id") or record.get("uuid") or "")
        if record.get("isApiErrorMessage"):
            self._last_assistant_id = None
            return [self._add(ERROR, _plain_text(content) or "Claude reported an error.",
                              record)]
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            raise _Unreadable()

        touched: List[ChatMessage] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text") or "")
                if not text.strip():
                    continue
                last = self.transcript.messages[-1] if self.transcript.messages else None
                if (last is not None and last.kind == ASSISTANT
                        and self._last_assistant_id == msg_id and msg_id):
                    last.text = f"{last.text}\n\n{text.strip()}"
                    touched.append(last)
                else:
                    touched.append(self._add(ASSISTANT, text, record, key=msg_id))
                    self._last_assistant_id = msg_id
            elif btype == "tool_use":
                touched.append(self._tool_use(block, record))
                # A tool call ends the text run: text after it is a new message.
                self._last_assistant_id = None
            # thinking, redacted_thinking and unknown blocks: not shown
        return touched

    def _tool_use(self, block: dict, record: dict) -> ChatMessage:
        name = str(block.get("name") or "tool")
        tool_id = str(block.get("id") or "")
        if tool_id:
            self._tool_names[tool_id] = name
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        if tool_id and name in EDIT_TOOLS:
            self._edit_inputs[tool_id] = (name, tool_input)
        key = f"{record.get('uuid')}:{tool_id}"
        if name == "AskUserQuestion":
            return self._add(QUESTION, _format_questions(tool_input), record, key=key)
        if name == "ExitPlanMode":
            plan = str(tool_input.get("plan") or "").strip()
            return self._add(PLAN, plan or "Claude proposed a plan.", record, key=key)
        return self._add(TOOL, _summarize_tool(name, tool_input), record, key=key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cross_session(text: str) -> Optional[Tuple[str, str]]:
    """``(sender, body)`` from a ``<cross-session-message ...>`` wrapper, or None.

    The sender is ``from-name`` (``name`` in older versions), or "" when the
    message doesn't name it. ``encoded="1"`` means the body is HTML-escaped.
    Claude Code always writes a ``from`` address, so text you typed that merely
    starts with the tag isn't taken for a message from another session.
    """
    match = _CROSS_SESSION.match(text)
    if not match:
        return None
    attributes = {k: html.unescape(v) for k, v in _ATTRIBUTE.findall(match.group(1))}
    if not attributes.get("from"):
        return None
    sender = attributes.get("from-name") or attributes.get("name") or ""
    body = match.group(2)
    if attributes.get("encoded") == "1":
        body = html.unescape(body)
    return sender, body.strip()


def _plain_text(content) -> str:
    """Text out of a string, a list of blocks, or anything else."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    parts.append("(image attached)")
                elif block.get("type") == "tool_reference":
                    parts.append(f"[tool {block.get('tool_name', '')}]")
        return "\n".join(p for p in parts if p)
    return str(content)


def _first_nonblank(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _format_questions(tool_input: dict) -> str:
    """A question card as words: the question, then its options."""
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return "Claude asked a question (its contents could not be read)."
    parts = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or q.get("header") or "").strip()
        options = []
        for opt in q.get("options") or []:
            if isinstance(opt, dict):
                label = str(opt.get("label") or "").strip()
                desc = str(opt.get("description") or "").strip()
                if label:
                    options.append(f"{label} ({desc})" if desc else label)
            elif isinstance(opt, str):
                options.append(opt)
        line = text
        if options:
            many = " Choose any." if q.get("multiSelect") else ""
            line += f"\nOptions: {'; '.join(options)}.{many}"
        parts.append(line)
    return "\n\n".join(parts) or "Claude asked a question."


def _format_answers(tool_use_result, fallback: str) -> str:
    if isinstance(tool_use_result, dict):
        answers = tool_use_result.get("answers")
        if isinstance(answers, dict) and answers:
            return "\n".join(f"{q} — {a}" for q, a in answers.items())
    text = fallback.strip()
    prefix = "Your questions have been answered:"
    if text.startswith(prefix):
        text = text[len(prefix):].strip()
    return text or "(answered)"


def _summarize_tool(name: str, tool_input: dict) -> str:
    for key in _TOOL_SUMMARY_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            value = " ".join(value.split())
            if len(value) > 160:
                value = value[:159] + "…"
            return f"{name}: {value}"
    return name


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------


class TranscriptReader:
    """Reads a transcript file and keeps up with it as it grows.

    Only whole lines are parsed. A line still being written (no newline yet)
    waits for the next ``refresh``, so a live session never shows a spurious
    "couldn't read" count. If the file shrinks it was rewritten, and the reader
    starts over. Opens the file read-only, always.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._offset = 0
        self._parser = TranscriptParser()

    @property
    def transcript(self) -> Transcript:
        return self._parser.transcript

    def refresh(self) -> bool:
        """Read anything new. Returns True if the transcript changed."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self._offset:
            self._offset = 0
            self._parser = TranscriptParser()
        if size == self._offset:
            return False
        with open(self.path, "rb") as handle:
            handle.seek(self._offset)
            data = handle.read(size - self._offset)
        end = data.rfind(b"\n")
        if end < 0:
            return False
        complete = data[: end + 1]
        self._offset += len(complete)
        lines = split_jsonl(complete)
        before = (len(self.transcript.messages), self.transcript.unreadable_lines)
        touched = self._parser.feed(lines)
        after = (len(self.transcript.messages), self.transcript.unreadable_lines)
        return bool(touched) or before != after


def split_jsonl(data: bytes) -> List[str]:
    """Lines of a JSONL file, split on newline bytes only.

    Not ``str.splitlines()``: that also splits on U+2028, U+2029 and U+0085,
    which JSON writers leave raw inside strings, and so would cut a record in
    half and lose the message.
    """
    return [raw.decode("utf-8", errors="replace").rstrip("\r")
            for raw in data.split(b"\n")]


def read_transcript(path: Path) -> Transcript:
    reader = TranscriptReader(path)
    reader.refresh()
    return reader.transcript


def parse_lines(lines: Iterable[str]) -> Transcript:
    parser = TranscriptParser()
    parser.feed(lines)
    return parser.transcript


# ---------------------------------------------------------------------------
# The last message, from the end of a file (#146)
# ---------------------------------------------------------------------------

#: How much of a transcript's end is read to find its last message, tried in
#: turn: almost always the first is enough. A tail made only of tool output
#: (a long run of commands) needs more; past the last, the column says
#: nothing rather than read tens of megabytes on every refresh.
TAIL_STEPS = (64 * 1024, 256 * 1024, 1024 * 1024, 4 * 1024 * 1024)
#: What counts as the last message: your text and Claude's, as the messages
#: list shows them. Tool calls and results, thinking, context and harness
#: events, question cards, errors and other sessions' messages don't.
LAST_MESSAGE_KINDS = (USER, ASSISTANT)


def last_message_from_tail(path: Path, steps: Tuple[int, ...] = TAIL_STEPS
                           ) -> Optional[ChatMessage]:
    """The last message of yours or Claude's in a transcript, reading only
    its end, or None (missing, unreadable, or no message in the end read).

    Each step reads the file's last ``steps[i]`` bytes and drops the first,
    cut-off line. A reply written in several records can begin before the
    part read, so one that is the first message found is only taken once
    the start of the file, or the last step, has been reached.
    """
    try:
        with open(path, "rb") as handle:
            return _last_message(handle, os.fstat(handle.fileno()).st_size, steps)
    except OSError:
        return None


def _last_message(handle, size: int, steps: Tuple[int, ...]) -> Optional[ChatMessage]:
    for number, limit in enumerate(steps):
        last_step = number == len(steps) - 1
        start = max(0, size - limit)
        if start > 0:
            # One byte more, to tell a whole first line (the byte before it
            # is a newline) from one cut in the middle, which is dropped.
            handle.seek(start - 1)
            data = handle.read(size - start + 1)
            cut = data.find(b"\n")
            if cut < 0 or cut == len(data) - 1:
                continue  # one line longer than this step: read more
            data = data[cut + 1:]
        else:
            handle.seek(0)
            data = handle.read(size)
        # A last line without its newline is parsed too: if it is still
        # being written it won't parse, and is counted, not raised.
        parser = TranscriptParser()
        parser.feed(split_jsonl(data))
        messages = parser.transcript.messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.kind not in LAST_MESSAGE_KINDS or not message.text.strip():
                continue
            if index == 0 and start > 0 and message.kind == ASSISTANT and not last_step:
                break  # may have begun before this step: read more
            return message
        if start == 0:
            return None
    return None


class LastMessages:
    """``last_message_from_tail`` remembered by file size and modification
    time, so a refresh reads only transcripts that changed. Safe to use from
    the background thread that gathers the session list."""

    def __init__(self, steps: Tuple[int, ...] = TAIL_STEPS) -> None:
        self._steps = steps
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[Tuple[int, int], Optional[ChatMessage]]] = {}
        #: How many files were read (not answered from memory); for tests.
        self.reads = 0

    def get(self, path: Optional[Path]) -> Optional[ChatMessage]:
        if path is None:
            return None
        name = str(path)
        with self._lock:
            entry = self._entries.get(name)
        try:
            # A stat, not an open, when nothing changed: most refreshes.
            info = os.stat(path)
            if entry is not None and entry[0] == (info.st_size, info.st_mtime_ns):
                return entry[1]
            with open(path, "rb") as handle:
                # The stamp of what is read, from the handle it's read from.
                info = os.fstat(handle.fileno())
                stamp = (info.st_size, info.st_mtime_ns)
                message = _last_message(handle, info.st_size, self._steps)
        except FileNotFoundError:
            with self._lock:
                self._entries.pop(name, None)
            return None
        except OSError:
            # Busy for a moment (being replaced, scanned): keep what it said,
            # so the row doesn't lose its message and get it back again.
            return entry[1] if entry is not None else None
        with self._lock:
            self._entries[name] = (stamp, message)
            self.reads += 1
        return message

    def keep_only(self, paths: Iterable[Path]) -> None:
        """Forget files not in ``paths``: sessions gone from the list."""
        keep = {str(p) for p in paths}
        with self._lock:
            for name in [n for n in self._entries if n not in keep]:
                del self._entries[name]


# ---------------------------------------------------------------------------
# Sessions started in a terminal (#158)
# ---------------------------------------------------------------------------

#: The ``entrypoint`` Claude Code records for a session someone started by
#: typing ``claude`` in a terminal. ``claude -p`` (scripts, and The Chat
#: Place's own turns) records "sdk-cli", the desktop app "claude-desktop", and
#: the VS Code extension "claude-vscode".
TERMINAL_ENTRYPOINT = "cli"
#: Records that name a session. ``/rename`` writes a custom title and Claude
#: Code an ai-title of its own, and Claude Code writes them again, with the
#: session's other details, as the session goes on: so the newest is near the
#: end of the file.
_TITLE_RECORDS = (b'"custom-title"', b'"ai-title"')
#: Read at a time. Until it's known whose a transcript is, only a small piece
#: is read: its first records say, and most transcripts aren't a terminal
#: session's, so they're left after that, however long they are.
_FACTS_CHUNK = 1024 * 1024
_FACTS_FIRST_CHUNK = 16 * 1024
#: Of a terminal session's transcript, past its start, only this much of its
#: end is read the first time, for its newest title and folder: a long-used
#: session's transcript can be hundreds of megabytes. After that, only what's
#: added to it is read.
_FACTS_TAIL = 1024 * 1024
#: A first prompt not found in this many records isn't looked for further.
_FIRST_PROMPT_RECORDS = 400


@dataclass
class SessionFacts:
    """What a transcript says about its session, for listing one that no
    metadata file describes: how it was started, its folder and its title."""
    entrypoint: str = ""
    #: Its folder: the one it started in, or the worktree it moved to since.
    #: Not just its newest record's, which follows every ``cd`` Claude runs
    #: into a subfolder, a build folder or a temporary one.
    cwd: str = ""
    custom_title: str = ""
    ai_title: str = ""
    first_prompt: str = ""
    #: When the file last changed, in milliseconds.
    modified_ms: int = 0
    #: When the session began: its first record's time, in milliseconds
    #: since the epoch (0 if none says). The session list's Started (#209).
    started_ms: int = 0

    @property
    def title(self) -> str:
        return self.custom_title or self.ai_title or self.first_prompt

    @property
    def terminal(self) -> bool:
        return self.entrypoint == TERMINAL_ENTRYPOINT


_WORKTREE_FOLDER = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+")


def _moved_to(record: dict) -> str:
    """The folder a record says the session moved to, if it says one: Claude
    Code's ``relocatedCwd``, or else the worktree a ``cwd`` is in (its top
    folder, not a subfolder Claude has gone into with ``cd``)."""
    moved = record.get("relocatedCwd")
    if isinstance(moved, str) and moved:
        return moved
    cwd = record.get("cwd")
    match = _WORKTREE_FOLDER.search(cwd) if isinstance(cwd, str) else None
    return cwd[:match.end()] if match else ""


class _FactsReader:
    """One transcript's facts, read as the file grows (whole lines only)."""

    def __init__(self) -> None:
        self.facts = SessionFacts()
        self.offset = 0
        self.records = 0
        self.stamp: Tuple[int, int] = (-1, -1)
        #: The last bytes read, up to ``offset``: if they've changed, the file
        #: was replaced rather than added to.
        self.edge = b""
        self._parser: Optional[TranscriptParser] = TranscriptParser()

    @property
    def done(self) -> bool:
        """Not a terminal session's: nothing more in it matters."""
        return bool(self.facts.entrypoint) and not self.facts.terminal

    @property
    def head_read(self) -> bool:
        """Its start has said all it will: whose it is, and its first prompt."""
        return bool(self.facts.entrypoint) and self._parser is None

    def feed(self, data: bytes) -> None:
        latest_cwd = ""
        for line in data.split(b"\n"):
            if self.done:
                self._parser = None
                return
            if not line.strip():
                continue
            self.records += 1
            # Past its start, only titles matter, and the session's start
            # time if no record so far had one (#209).
            if (self.head_read and not any(marker in line for marker in _TITLE_RECORDS)
                    and (self.facts.started_ms or b'"timestamp"' not in line)):
                continue
            record = _json_line(line)
            if record is not None:
                self._record(record, line)
        if self.head_read:
            # The worktree it moved to, if it did, from the newest record that
            # says: a write often ends with records that don't (titles and such).
            for line in reversed(data.split(b"\n")):
                if b'"cwd"' not in line and b'"relocatedCwd"' not in line:
                    continue
                record = _json_line(line)
                if record is None or record.get("isSidechain"):
                    continue
                latest_cwd = _moved_to(record)
                if latest_cwd:
                    break
            if latest_cwd:
                self.facts.cwd = latest_cwd

    def _record(self, record: dict, line: bytes) -> None:
        facts = self.facts
        kind = record.get("type")
        if kind == "custom-title":
            facts.custom_title = _one_line(record.get("customTitle"))
            return
        if kind == "ai-title":
            facts.ai_title = _one_line(record.get("aiTitle"))
            return
        if record.get("isSidechain"):
            return
        if not facts.started_ms:
            facts.started_ms = record_time_ms(record)
        if not facts.entrypoint and isinstance(record.get("entrypoint"), str):
            facts.entrypoint = record["entrypoint"]
        if not facts.cwd and isinstance(record.get("cwd"), str) and record["cwd"]:
            facts.cwd = record["cwd"]
        elif _moved_to(record):
            facts.cwd = _moved_to(record)
        if self._parser is None:
            return
        if kind == USER:
            for message in self._parser.feed([line.decode("utf-8", errors="replace")]):
                if message.kind == USER:
                    facts.first_prompt = message.first_line(120)
                    self._parser = None
                    return
        if self.records >= _FIRST_PROMPT_RECORDS:
            self._parser = None


def _edge(handle, offset: int, size: int = 64) -> bytes:
    """The ``size`` bytes before ``offset``."""
    start = max(0, offset - size)
    handle.seek(start)
    return handle.read(offset - start)


def record_time_ms(record: dict) -> int:
    """A record's ``timestamp`` (ISO 8601, "Z" for UTC) in milliseconds since
    the epoch, or 0 if it has none that can be read."""
    stamp = record.get("timestamp")
    if not isinstance(stamp, str) or not stamp:
        return 0
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _json_line(line: bytes) -> Optional[dict]:
    try:
        record = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


class SessionFactsCache:
    """``SessionFacts`` for each transcript, remembered by file size and
    modification time and read on from where it left off, so a refresh
    reads only what was added. A terminal session's transcript is read at its
    start and its last ``_FACTS_TAIL`` bytes, not its middle. Claude Code only
    ever adds to a transcript; one that shrinks, or whose bytes before where
    reading stopped change, was replaced and is read again. For one caller at a time (the thread that gathers the session
    list): each file's reader isn't locked while it reads."""

    def __init__(self, chunk: int = _FACTS_CHUNK,
                 first_chunk: int = _FACTS_FIRST_CHUNK, tail: int = _FACTS_TAIL) -> None:
        self._chunk = chunk
        self._first_chunk = min(first_chunk, chunk)
        self._tail = tail
        self._lock = threading.Lock()
        self._readers: Dict[str, _FactsReader] = {}
        #: Bytes read from files (not answered from memory); for tests.
        self.bytes_read = 0

    def get(self, path: Path) -> Optional[SessionFacts]:
        """Its facts, or None when it can't be read."""
        name = str(path)
        with self._lock:
            reader = self._readers.get(name)
        try:
            info = os.stat(path)
            stamp = (info.st_size, info.st_mtime_ns)
            if reader is not None and reader.stamp == stamp:
                return reader.facts
            with open(path, "rb") as handle:
                if reader is not None and (info.st_size < reader.offset
                                           or _edge(handle, reader.offset) != reader.edge):
                    reader = None  # replaced: start over
                if reader is None:
                    reader = _FactsReader()
                if not reader.done:
                    self._read(handle, reader, info.st_size)
                reader.edge = _edge(handle, reader.offset)
            reader.stamp = stamp
            reader.facts.modified_ms = info.st_mtime_ns // 1_000_000
        except FileNotFoundError:
            with self._lock:
                self._readers.pop(name, None)
            return None
        except OSError:
            # Busy for a moment: keep what it said before.
            return reader.facts if reader is not None and reader.offset else None
        with self._lock:
            self._readers[name] = reader
        return reader.facts

    def _read(self, handle, reader: _FactsReader, size: int) -> None:
        handle.seek(reader.offset)
        while not reader.done:
            want = self._chunk if reader.facts.entrypoint else self._first_chunk
            data = handle.read(want)
            if not data:
                break
            end = data.rfind(b"\n")
            if end < 0:
                # One line longer than the piece read (a pasted log as the
                # first prompt): read the rest of it, once it's all written.
                data += handle.readline()
                if not data.endswith(b"\n"):
                    break
                end = len(data) - 1
            piece = data[:end + 1]
            reader.offset += len(piece)
            self.bytes_read += len(piece)
            reader.feed(piece)
            if (reader.head_read and not reader.done
                    and size - reader.offset > self._tail):
                # Its middle says nothing the end doesn't: skip to the end,
                # at the start of a line.
                # From the byte before, so a line starting just there is kept.
                handle.seek(size - self._tail - 1)
                handle.readline()
                reader.offset = handle.tell()
            else:
                handle.seek(reader.offset)

    def keep_only(self, paths: Iterable[Path]) -> None:
        """Forget files not in ``paths``: transcripts gone from the folder."""
        keep = {str(p) for p in paths}
        with self._lock:
            for name in [n for n in self._readers if n not in keep]:
                del self._readers[name]
