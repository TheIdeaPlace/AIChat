"""Express (#737): the latest reply from every chat, in one list.

One row pinned at the top of the session list opens a list with a row per
chat: its name, provider and latest reply. It's built only from what the
session list already has (``SessionInfo``s from the snapshot, with
``last_reply`` filled in by each provider), so it works the same for every
provider and needs nothing of their own.

Plain data and words, no wx: ``ui/express.py`` is the window's side.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .sessions import FIELD_LAST_REPLY, FIELD_TITLE, WORKING, SessionInfo

#: The Express row's key in the session list. Never a chat's: no provider's
#: keys look like this ("claude:...", "codex:...", a bare UUID), so nothing
#: that takes a chat's key ever finds it in the snapshot.
EXPRESS_KEY = "express"


@dataclass
class ExpressRow:
    """The session list's Express row, standing in for a ``SessionInfo``
    where the list writes its rows: it has a key and a line, nothing else.
    It's never in the snapshot, so ``MainFrame._current_info`` never finds
    it, and no chat command can act on it."""

    text: str
    key: str = EXPRESS_KEY
    state: str = ""
    last_message: str = ""

    def list_line(self, now_ms: Optional[int] = None, fields=None) -> str:
        return self.text


def has_something(info: SessionInfo) -> bool:
    """A chat with something in it yet: a reply, or at least a message."""
    return bool(info.last_reply or info.last_message)


def chats(shown: Iterable[SessionInfo], count: int) -> List[SessionInfo]:
    """The chats Express lists: those the session list shows (its view,
    groups, hidden chats and filter already applied), with something in
    them, newest activity first, at most ``count``."""
    rows = [s for s in shown if s.key != EXPRESS_KEY and has_something(s)]
    rows.sort(key=lambda s: s.last_activity_ms, reverse=True)
    return rows[:max(count, 0)]


def keep_replies(rows: Sequence[SessionInfo], memo: Dict[str, str]) -> List[SessionInfo]:
    """While a chat is working its Last reply stays the one it had (a turn
    writes "Let me check the tests" on its way to the answer): copies of
    ``rows`` with that reply put back, and ``memo`` (key -> the last reply
    seen while it wasn't working) brought up to date."""
    kept = []
    for info in rows:
        if info.state == WORKING and memo.get(info.key):
            if info.last_reply != memo[info.key]:
                info = dataclasses.replace(info, last_reply=memo[info.key])
        elif info.last_reply:
            memo[info.key] = info.last_reply
        kept.append(info)
    return kept


def ordered(previous_keys: Sequence[str], rows: Sequence[SessionInfo],
            listed: Sequence[SessionInfo], frozen: bool) -> List[SessionInfo]:
    """The rows in the order to show them. ``frozen`` (the list has focus):
    the rows already shown stay where they are, even one that's dropped out
    of the newest ``count`` (``listed`` has every chat Express could show),
    and new ones go at the end, so the row you're on never moves. Otherwise
    ``rows``, newest first."""
    if not frozen or not previous_keys:
        return list(rows)
    by_key = {s.key: s for s in listed}
    by_key.update({s.key: s for s in rows})
    kept = [by_key[key] for key in previous_keys if key in by_key]
    before = set(previous_keys)
    return kept + [s for s in rows if s.key not in before]


def _chats(count: int) -> str:
    return "no chats" if count == 0 else "1 chat" if count == 1 else f"{count} chats"


def summary(rows: Sequence[SessionInfo]) -> str:
    """The session list's Express row: "Express, 7 chats, 2 with new
    replies". New replies are the chats' own New reply marks, as their rows
    in the session list say "new reply"."""
    text = f"Express, {_chats(len(rows))}"
    new = sum(1 for s in rows if s.unread)
    if new:
        text += ", 1 with a new reply" if new == 1 else f", {new} with new replies"
    return text


def heading(count: int) -> str:
    """What the list's label and the status bar say: "Express, 7 chats"."""
    return f"Express, {_chats(count)}"


#: Where the reply box would be while Express is open.
REPLY_NOTE = "Press R to reply to the chat you're on."
#: The list with no chats in it.
EMPTY_LINE = ("No chats to show yet. Express lists the chats the session list is showing "
              "that have something in them.")


def row_text(info: SessionInfo, fields: Sequence[str], now_ms: Optional[int] = None,
             whole: bool = False) -> str:
    """One Express row: the chosen columns in order, each only when it has
    something to say, never empty. ``whole``: the latest reply as kept
    (``ui_text.REPLY_LIMIT``), for a screen reader, rather than cut short
    as the row shows it."""
    values = info.field_values(now_ms)
    if whole:
        values[FIELD_LAST_REPLY] = info.last_reply
    parts = [values[f] for f in fields if values.get(f)]
    return ", ".join(parts) or values[FIELD_TITLE]


def reply_title(info: SessionInfo) -> str:
    """The reply dialog's title and its box's label, naming where the text
    goes: "Reply to Release notes (Claude)"."""
    provider = info.provider_label or (
        "Codex" if info.provider == "codex-api" else info.provider.capitalize())
    return f"Reply to {info.title or 'Untitled session'} ({provider})"
