"""Find in All Sessions (#109): which session did we talk about something in?

Each listed session's transcript is read (off the window's thread) and its
messages searched for the text, the whole text of each message, ignoring
case. (Not quite as Find in Messages does: it casefolds, so "STRASSE" finds
"Straße" there; here the offsets must be in the text itself, for the snippet,
so it's a case-insensitive match, which doesn't.) A match is the session, the message's time
and a snippet around the words. Sessions whose transcript isn't on disk,
or can't be read, are counted, so the result can say how many weren't
searched.

Nothing here imports wx, so it's all tested without a window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .export import message_moment
from .transcript import Transcript, TranscriptReader
from .ui_text import markdown_as_text

#: Enough to choose from; past it the list says there were more.
MAX_RESULTS = 500
#: About how much of the message is heard around the words found.
SNIPPET_BEFORE = 60
SNIPPET_LENGTH = 160


@dataclass
class Target:
    """A session to search: its list key, title, how its transcript is
    found (called off the window's thread, as finding it touches the disk),
    and whether its tool calls and results count (Show Tool Activity, #162)."""
    key: str
    title: str
    find_transcript: Callable[[], Optional[Path]]
    with_activity: bool = False
    #: Backends can supply history without a local Claude JSONL file (#184).
    read_history: Optional[Callable[[], Optional[Transcript]]] = None


@dataclass
class Match:
    key: str
    title: str
    message_key: str
    who: str
    timestamp: str
    snippet: str
    #: The message's place among the session's messages, oldest first.
    index: int = 0

    def row(self, now: Optional[datetime] = None) -> str:
        """"Build, Claude, yesterday 10:42: …the database migration ran…"."""
        when = describe_moment(self.timestamp, now)
        who = f"{self.who}, {when}" if when else self.who
        return f"{self.title}, {who}: {self.snippet}"


@dataclass
class Results:
    text: str
    matches: List[Match] = field(default_factory=list)
    searched: int = 0
    #: Sessions with no transcript on disk, or one that couldn't be read.
    unreadable: int = 0
    #: True when it stopped at MAX_RESULTS matches, before every session.
    more: bool = False
    #: How many sessions there were to search.
    total: int = 0
    #: Sessions with at least one match.
    sessions: int = 0

    def title(self, app: str) -> str:
        """The results window's title: what you came to hear first, how many
        and for what, then which window this is. 'The Chat Place: 6 results
        for "migration", Find in All Sessions'."""
        found = len(self.matches)
        count = (f"the first {MAX_RESULTS} results" if self.more
                 else f"{found} result{'s' if found != 1 else ''}")
        return f'{app}: {count} for "{self.text}", Find in All Sessions'

    def summary(self) -> str:
        """What's said when the search ends."""
        searched = f"{self.searched} session{'s' if self.searched != 1 else ''}"
        if self.more:
            searched = f"{self.searched} of {self.total} sessions before stopping"
        skipped = (f" {self.unreadable} had no transcript to read." if self.unreadable else "")
        if not self.matches:
            return f'No messages contain "{self.text}". Searched {searched}.{skipped}'
        found = len(self.matches)
        count = (f"The first {MAX_RESULTS} messages" if self.more
                 else f"{found} message{'s' if found != 1 else ''}")
        where = f"{self.sessions} session{'s' if self.sessions != 1 else ''}"
        verb = "contain" if found != 1 else "contains"
        return f'{count} in {where} {verb} "{self.text}". Searched {searched}.{skipped}'


def describe_moment(timestamp: str, now: Optional[datetime] = None) -> str:
    """"10:42" today, "yesterday 10:42", "3 March 10:42", or "3 March 2025"
    for another year; "" with no time."""
    moment = message_moment(timestamp)
    if moment is None:
        return ""
    now = now or datetime.now(moment.tzinfo)
    clock = moment.strftime("%H:%M")
    days = (now.date() - moment.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"yesterday {clock}"
    day = f"{moment.day} {moment.strftime('%B')}"
    if moment.year != now.year:
        return f"{day} {moment.year}"
    return f"{day} {clock}"


_SPACE = re.compile(r"\s")


def snippet(text: str, at: int, length: int) -> str:
    """About SNIPPET_LENGTH characters of ``text`` around the words found at
    ``at`` (``length`` long), as one plain line, cut at a space or line
    break, with "…" where it was cut. Addresses stay whole (the words may be
    in one)."""
    start = max(0, at - SNIPPET_BEFORE)
    if start:
        space = _SPACE.search(text, start, at)
        if space:
            start = space.end()  # not in the middle of a word
    end = min(len(text), max(start + SNIPPET_LENGTH, at + length))
    if end < len(text):
        spaces = [m.start() for m in _SPACE.finditer(text, at + length, end)]
        if spaces:
            end = spaces[-1]
    piece = " ".join(markdown_as_text(text[start:end]).split())
    return ("…" if start else "") + piece + ("…" if end < len(text) else "")


def search_sessions(targets: Iterable[Target], text: str,
                    cancelled: Callable[[], bool] = lambda: False,
                    progress: Callable[[int], None] = lambda done: None) -> Results:
    """Search ``targets`` for ``text``, ignoring case: within a session the
    newest message first, the sessions in the order given (the list's).
    ``progress(sessions done)`` is called after each; it stops when
    ``cancelled()`` or at MAX_RESULTS matches."""
    targets = list(targets)
    results = Results(text, total=len(targets))
    if not text.strip():
        return results
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    for done, target in enumerate(targets, 1):
        if cancelled() or results.more:
            break
        try:
            if target.read_history is not None:
                history = target.read_history()
            else:
                path = target.find_transcript()
                history = None
                if path is not None and path.is_file():
                    reader = TranscriptReader(path)
                    reader.refresh()
                    history = reader.transcript
            if history is None:
                results.unreadable += 1
                progress(done)
                continue
            messages = history.messages
        except Exception:  # noqa: BLE001 - counted, never raised
            results.unreadable += 1
            progress(done)
            continue
        results.searched += 1
        found_here = False
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.is_activity and not target.with_activity:
                continue
            body = message.whole or ""
            # Offsets in the text itself: casefolding can change its length.
            found = pattern.search(body)
            if found is None:
                continue
            if len(results.matches) >= MAX_RESULTS:
                results.more = True
                break
            found_here = True
            results.matches.append(Match(target.key, target.title, message.key, message.label,
                                         message.timestamp,
                                         snippet(body, found.start(), found.end() - found.start()),
                                         index))
        if found_here:
            results.sessions += 1
        progress(done)
    return results
