"""The work Claude hands to subagents (#468): who was asked what, how it
went, what it said at the end, and what it changed.

Claude Code writes each subagent's conversation to its own transcript,
``<session id>/subagents/agent-<agent id>.jsonl`` beside the session's
(``platform_paths.subagents_dir``), with ``agent-<agent id>.meta.json`` next
to it. Checked on Claude Code 2.1.296 against 700 of them:

* The meta file has ``agentType`` ("code-reviewer", "Explore"),
  ``description``, ``toolUseId`` (the parent's Agent call), ``spawnDepth``,
  ``parentAgentId`` for one started by another subagent, and
  ``stoppedByUser`` once you stopped it.
* Every record is a sidechain one carrying ``agentId``. The first is a user
  record whose text is the prompt Claude wrote for it.
* A subagent run in the background ends with a ``SubagentHandback`` call
  whose ``message`` is its final report; one Claude waited for ends with a
  reply that ends its turn, and the parent's Agent result carries the report.

What the parent's transcript says (its Agent calls, their results and the
``<task-notification>`` that says a background one ended) is read by
``transcript.TranscriptParser`` into ``Transcript.agents``; the subagents'
own files are read here, with the same parser in its sidechain mode. A file
that's still growing is read incrementally, as ``TranscriptReader`` does; one
that has finished is summed up and let go, and read again only if it grows.

No wx: everything here is tested on its own.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import platform_paths
from .changes import FileEdit, summary_text
from .transcript import (AGENT_COMPLETED, AGENT_FAILED, AGENT_STOPPED, ASSISTANT,
                         TOOL, USER, AgentCall, AgentNote, Transcript, TranscriptReader)

RUNNING = "running"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
STATUS_WORDS = {RUNNING: "Running", DONE: "Done", FAILED: "Failed", STOPPED: "Stopped"}
#: What's said when one ends: "Subagent code-reviewer finished."
ENDED_WORDS = {DONE: "finished", FAILED: "failed", STOPPED: "stopped"}
#: Who gives a subagent its work, in its conversation.
GIVER = "Claude"

_FILE = re.compile(r"^agent-([A-Za-z0-9_-]+)\.jsonl$")
_NOTE_TO_STATUS = {AGENT_COMPLETED: DONE, AGENT_FAILED: FAILED, AGENT_STOPPED: STOPPED}


@dataclass
class Subagent:
    """One subagent's work, as far as the files say."""
    agent_id: str
    #: Its transcript, or None when only the parent's Agent call is left.
    path: Optional[Path] = None
    agent_type: str = ""
    description: str = ""
    prompt: str = ""
    status: str = RUNNING
    #: Why it's counted as stopped when nothing said so ("" otherwise).
    note: str = ""
    started: str = ""
    finished: str = ""
    messages: int = 0
    tool_calls: int = 0
    report: str = ""
    #: The files it changed, marked with who changed them, in the turn of
    #: the parent's that started it.
    edits: List[FileEdit] = field(default_factory=list)
    #: The parent's turn that started it (0: not known).
    turn: int = 0
    tool_use_id: str = ""
    background: bool = False
    #: The subagent that started this one, by its type ("" if Claude did).
    started_by: str = ""
    unreadable_lines: int = 0

    @property
    def name(self) -> str:
        return self.agent_type or "subagent"

    @property
    def finished_status(self) -> bool:
        return self.status != RUNNING

    def what(self) -> str:
        """What it was asked to do, in a few words."""
        return self.description or _first_line(self.prompt) or "no description"

    def duration_ms(self, now_ms: Optional[int] = None) -> int:
        start = _ms(self.started)
        if not start:
            return 0
        end = _ms(self.finished) if self.finished_status else (now_ms or _now_ms())
        return max(0, end - start) if end else 0

    def counts(self, now_ms: Optional[int] = None) -> str:
        """"34 messages, 12 tool calls, 4 minutes"."""
        if self.path is None:
            return "its conversation isn't on disk"
        parts = [_plural(self.messages, "message"), _plural(self.tool_calls, "tool call")]
        took = self.duration_ms(now_ms)
        if self.started:
            words = duration_words(took)
            parts.append(f"{words} so far" if self.status == RUNNING else words)
        return ", ".join(parts)

    def row(self, now_ms: Optional[int] = None) -> str:
        """"code-reviewer: Review the branch. Done, 34 messages, 12 tool
        calls, 4 minutes"."""
        return f"{self.name}: {_end(self.what())} {STATUS_WORDS[self.status]}, {self.counts(now_ms)}"

    def detail(self, now_ms: Optional[int] = None) -> str:
        """The prompt it was given and its final report, after what it is."""
        lines = [f"{self.name}: {self.what()}",
                 f"{STATUS_WORDS[self.status]}, {self.counts(now_ms)}."]
        if self.note:
            lines.append(self.note)
        if self.background:
            lines.append("Ran in the background.")
        if self.started_by:
            lines.append(f"Started by the subagent {self.started_by}.")
        changed = summary_text(self.edits)
        if changed:
            lines.append(changed)
        if self.unreadable_lines:
            lines.append(f"Couldn't read {_plural(self.unreadable_lines, 'line')} of its transcript.")
        report = self.report or ("No report yet." if self.status == RUNNING else "No report.")
        return "\n".join(lines) + f"\n\nPrompt:\n{self.prompt or 'Not recorded.'}" \
            f"\n\nFinal report:\n{report}"

    def ended_text(self) -> str:
        """"Subagent code-reviewer finished." once it has ended."""
        return f"Subagent {self.name} {ENDED_WORDS.get(self.status, 'finished')}."


# -- one subagent's file ---------------------------------------------------------------


def read_meta(path: Path) -> dict:
    """The ``agent-<id>.meta.json`` beside a subagent's transcript, or {}."""
    meta = Path(path).with_name(Path(path).name[:-len(".jsonl")] + ".meta.json")
    try:
        with open(meta, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def open_conversation(path: Path, agent_type: str = "") -> TranscriptReader:
    """A reader for a subagent's whole conversation, to show it (#468):
    the prompt as Claude's, the replies as the subagent's."""
    return TranscriptReader(path, sidechain=True, assistant_name=agent_type or "Subagent",
                            user_name=GIVER)


class SubagentFile:
    """One subagent's transcript and meta file, summed up as they grow."""

    def __init__(self, path: Path, agent_id: str) -> None:
        self.path = Path(path)
        self.agent_id = agent_id
        self.meta: dict = {}
        self._reader: Optional[TranscriptReader] = None
        self._stamp = None
        # The summary, kept when the reader is let go.
        self.first_time = self.last_time = ""
        self.messages = self.tool_calls = self.unreadable = 0
        self.edits: List[FileEdit] = []
        self.handback = ""
        self.ended = False
        self.prompt = ""
        self.last_reply = ""

    @property
    def agent_type(self) -> str:
        value = self.meta.get("agentType")
        return value.strip() if isinstance(value, str) else ""

    def refresh(self) -> bool:
        """Read anything new. True if what it says changed."""
        try:
            stat = os.stat(self.path)
        except OSError:
            return False
        meta_path = self.path.with_name(f"agent-{self.agent_id}.meta.json")
        try:
            meta_stat = os.stat(meta_path)
            meta_stamp = (meta_stat.st_size, meta_stat.st_mtime_ns)
        except OSError:
            meta_stamp = None
        stamp = (stat.st_size, stat.st_mtime_ns, meta_stamp)
        if stamp == self._stamp:
            return False
        self._stamp = stamp
        self.meta = read_meta(self.path)
        if self._reader is None:
            self._reader = open_conversation(self.path, self.agent_type)
        self._reader.refresh()
        self._sum_up(self._reader.transcript)
        return True

    def release(self) -> None:
        """Let go of the conversation (it has finished): the summary stays,
        and the file is read again from the start only if it grows."""
        self._reader = None

    def _sum_up(self, transcript: Transcript) -> None:
        self.first_time, self.last_time = transcript.first_time, transcript.last_time
        self.unreadable = transcript.unreadable_lines
        conversation = [m for m in transcript.messages if not m.is_activity]
        self.messages = len(conversation)
        self.tool_calls = sum(1 for m in transcript.messages if m.kind == TOOL)
        self.edits = list(transcript.edits)
        self.handback = transcript.handback
        self.ended = transcript.ended
        self.prompt = next((m.text for m in transcript.messages if m.kind == USER), "")
        self.last_reply = next((m.text for m in reversed(transcript.messages)
                                if m.kind == ASSISTANT), "")


# -- a session's subagents --------------------------------------------------------------


class SubagentTracker:
    """A session's subagents, kept up as they work. For one caller at a
    time (the window's background reader); ``subagents`` returns copies."""

    def __init__(self, transcript_path: Path) -> None:
        self.folder = platform_paths.subagents_dir(transcript_path)
        self.files: Dict[str, SubagentFile] = {}

    def refresh(self) -> bool:
        """Find new subagents and read what the running ones added. True if
        anything changed."""
        found: Dict[str, Path] = {}
        try:
            with os.scandir(self.folder) as entries:
                for entry in entries:
                    match = _FILE.match(entry.name)
                    if match and entry.is_file():
                        found[match.group(1)] = Path(entry.path)
        except OSError:
            found = {}
        changed = False
        for agent_id in [a for a in self.files if a not in found]:
            del self.files[agent_id]
            changed = True
        for agent_id, path in found.items():
            sub = self.files.get(agent_id)
            if sub is None:
                sub = self.files[agent_id] = SubagentFile(path, agent_id)
            changed = sub.refresh() or changed
        return changed

    def subagents(self, transcript: Optional[Transcript], live: bool,
                  now_ms: Optional[int] = None) -> List[Subagent]:
        """Each subagent, oldest first, paired with the parent's Agent call.
        ``live``: the session is working now, so one that hasn't said it
        ended is still running. Finished ones' conversations are let go."""
        calls = list(transcript.agents) if transcript is not None else []
        notes = dict(transcript.agent_notes) if transcript is not None else {}
        found = pair(self.files.values(), calls, notes, live)
        for sub in found:
            if sub.finished_status and sub.agent_id in self.files:
                self.files[sub.agent_id].release()
        return found


def pair(files: Iterable[SubagentFile], calls: List[AgentCall],
         notes: Dict[str, AgentNote], live: bool) -> List[Subagent]:
    """The subagents, from their files and the parent's calls, oldest first.
    A call whose subagent left no file (an older Claude Code, or one
    removed) is listed too, from what the call says."""
    by_tool = {c.tool_id: c for c in calls if c.tool_id}
    by_agent = {c.agent_id: c for c in calls if c.agent_id}
    files = list(files)
    types = {f.agent_id: f.agent_type for f in files}
    used = set()
    found: List[Subagent] = []
    for sub_file in files:
        meta = sub_file.meta
        tool_id = _text(meta.get("toolUseId"))
        call = by_tool.get(tool_id) or by_agent.get(sub_file.agent_id)
        if call is not None:
            used.add(call.tool_id)
        sub = Subagent(agent_id=sub_file.agent_id, path=sub_file.path,
                       agent_type=sub_file.agent_type or (call.agent_type if call else ""),
                       description=_text(meta.get("description")) or (call.description if call else ""),
                       prompt=(call.prompt if call and call.prompt else sub_file.prompt),
                       started=sub_file.first_time or (call.timestamp if call else ""),
                       messages=sub_file.messages, tool_calls=sub_file.tool_calls,
                       tool_use_id=tool_id or (call.tool_id if call else ""),
                       background=(meta.get("requestShape") == "background"
                                   or bool(call and call.background)),
                       unreadable_lines=sub_file.unreadable,
                       turn=call.turn if call else 0)
        parent = _text(meta.get("parentAgentId"))
        if parent:
            sub.started_by = types.get(parent) or "another subagent"
        note = notes.get(sub_file.agent_id)
        _settle(sub, call, note, live, stopped_by_you=meta.get("stoppedByUser") is True,
                handback=sub_file.handback, ended=sub_file.ended,
                last_time=sub_file.last_time, last_reply=sub_file.last_reply)
        found.append(sub)
    # One started by a subagent belongs to the turn its starter did. Its
    # edits count in that turn, marked with who made them.
    turns = {sub.agent_id: sub.turn for sub in found}
    for sub, sub_file in zip(found, files):
        parent = _text(sub_file.meta.get("parentAgentId"))
        if not sub.turn and parent and turns.get(parent):
            sub.turn = turns[parent]
        sub.edits = [replace(edit, by=sub.name, turn=sub.turn) for edit in sub_file.edits]
    for call in calls:
        if call.tool_id in used or (call.agent_id and call.agent_id in types):
            continue
        sub = Subagent(agent_id=call.agent_id or call.tool_id, agent_type=call.agent_type,
                       description=call.description, prompt=call.prompt,
                       started=call.timestamp, turn=call.turn, tool_use_id=call.tool_id,
                       background=call.background)
        _settle(sub, call, notes.get(call.agent_id) if call.agent_id else None, live)
        found.append(sub)
    found.sort(key=lambda s: _ms(s.started))
    return found


def _settle(sub: Subagent, call: Optional[AgentCall], note: Optional[AgentNote], live: bool,
            stopped_by_you: bool = False, handback: str = "", ended: bool = False,
            last_time: str = "", last_reply: str = "") -> None:
    """Its status, report and finishing time, from the best evidence: what
    the parent was told (its Agent result, or a notification), then you
    stopping it, then its own hand-back or a last reply that ended its turn.
    Failing all of those it's running while the session works, and
    otherwise counted as stopped."""
    status, finished, report = "", "", ""
    if call is not None and call.status in _NOTE_TO_STATUS:
        status, finished, report = _NOTE_TO_STATUS[call.status], call.finished_at, call.report
    elif note is not None and note.status in _NOTE_TO_STATUS:
        status, finished, report = _NOTE_TO_STATUS[note.status], note.timestamp, note.report
    if not status and stopped_by_you:
        status, finished = STOPPED, last_time
    if not status and handback:
        status, finished = DONE, last_time
    if not status and ended:
        # Its last reply ended its turn, with no tool call after it: it has
        # stopped working, whatever the parent has been told so far.
        status, finished = DONE, last_time
    if not status:
        if live:
            status = RUNNING
        else:
            status, finished = STOPPED, last_time
            sub.note = "It didn't finish, or its ending wasn't recorded."
    sub.status = status
    sub.finished = finished or (last_time if status != RUNNING else "")
    sub.report = (handback or report or (note.report if note is not None else "")
                  or (last_reply if status != RUNNING else ""))


def count(transcript_path) -> int:
    """How many subagents' transcripts a session has (Properties, #468)."""
    if transcript_path is None:
        return 0
    try:
        with os.scandir(platform_paths.subagents_dir(transcript_path)) as entries:
            return sum(1 for entry in entries if _FILE.match(entry.name))
    except OSError:
        return 0


def edits_of(subagents: Iterable[Subagent]) -> List[FileEdit]:
    """Every subagent's file changes, for Changed Files (#468)."""
    return [edit for sub in subagents for edit in sub.edits]


def in_turn(subagents: Iterable[Subagent], turn: Optional[int]) -> List[Subagent]:
    return [s for s in subagents if turn is None or s.turn == turn]


def newly_ended(before: Dict[str, str], subagents: Iterable[Subagent]) -> List[Subagent]:
    """The ones running at the last read that have ended since. ``before``
    maps each id to its status then."""
    return [s for s in subagents if before.get(s.agent_id) == RUNNING and s.status != RUNNING]


def statuses(subagents: Iterable[Subagent]) -> Dict[str, str]:
    return {s.agent_id: s.status for s in subagents}


# -- words ------------------------------------------------------------------------------


def duration_words(ms: int) -> str:
    """"under a minute", "4 minutes", "1 hour 5 minutes"."""
    minutes = int(ms // 60_000)
    if minutes < 1:
        return "under a minute"
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return _plural(minutes, "minute")
    return _plural(hours, "hour") + (f" {_plural(minutes, 'minute')}" if minutes else "")


def _plural(number: int, word: str) -> str:
    return f"{number:,} {word}{'' if number == 1 else 's'}"


def _end(text: str) -> str:
    text = text.rstrip()
    return text if text.endswith((".", "?", "!", "…")) else f"{text}."


def _first_line(text: str, limit: int = 100) -> str:
    line = next((part.strip() for part in (text or "").splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _ms(stamp: str) -> int:
    if not isinstance(stamp, str) or not stamp:
        return 0
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
