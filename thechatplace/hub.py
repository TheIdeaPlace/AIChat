"""Gathering the whole session list in one pass, and noticing turn endings.

Runs on a background thread (it reads ~200 small files); returns plain data
the UI applies on the main thread.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import platform_paths
from .desktop_groups import DesktopGroups, load_desktop_groups
from .own_store import OwnSession
from .sessions import (NEEDS_YOU, SORT_STATUS, TERMINAL, WORKING, DesktopLoadResult, LiveStatus, SessionInfo,
                       load_desktop_sessions, load_live_status, load_terminal_sessions,
                       sort_sessions)
from .transcript import LastMessages, SessionFactsCache, TranscriptParser, split_jsonl
# The row's words for it: plain text, no wx, so this stays plain data.
from .ui_text import last_message_line

#: Each session's last message (#146), remembered between snapshots by file
#: size and modification time, so only a transcript that changed is read.
LAST_MESSAGES = LastMessages()
#: Sessions whose transcript wasn't found, by key: (their last activity, when
#: looked). Finding none means searching every project folder, so it isn't
#: done again every refresh: only when the session has been active since, or
#: after ``NO_TRANSCRIPT_RETRY`` seconds.
_NO_TRANSCRIPT: Dict[str, Tuple[int, float]] = {}
_NO_TRANSCRIPT_LOCK = threading.Lock()
NO_TRANSCRIPT_RETRY = 60.0
#: What each transcript says about its session, for finding terminal sessions
#: (#158): read once, then only what's added.
TERMINAL_FACTS = SessionFactsCache()


def terminal_projects_dir() -> Path:
    """Where terminal sessions' transcripts are. Tests point it elsewhere,
    so none of them lists the real ones."""
    return platform_paths.projects_dir()


def forget_last_messages() -> None:
    """The Last message column was taken off: nothing to keep for it."""
    LAST_MESSAGES.keep_only([])
    with _NO_TRANSCRIPT_LOCK:
        _NO_TRANSCRIPT.clear()


@dataclass
class Snapshot:
    sessions: List[SessionInfo] = field(default_factory=list)
    desktop_cli_ids: Set[str] = field(default_factory=set)
    cowork_cli_ids: Set[str] = field(default_factory=set)
    live: Dict[str, LiveStatus] = field(default_factory=dict)
    unreadable_files: int = 0
    #: The desktop app's own groups (#51), read-only here.
    desktop_groups: DesktopGroups = field(default_factory=DesktopGroups)


def collect(own: Iterable[OwnSession], running_own_ids: Set[str],
            desktop_dir: Optional[Path] = None,
            live_dir: Optional[Path] = None,
            alive=platform_paths.pid_alive,
            waiting: Optional[Dict[str, str]] = None,
            started=platform_paths.process_start,
            order: str = SORT_STATUS,
            last_messages: bool = False,
            background: Optional[Set[str]] = None) -> Snapshot:
    """``waiting`` maps a running own session to what Claude is waiting for
    (a permission request, question or plan): it needs you, not working.
    ``background``: running own sessions where Claude has answered and the
    turn is open only for background work (#161).
    ``order`` is how the list is sorted (see ``sessions.SORT_ORDERS``).
    ``last_messages`` fills in each session's Last message column (#146):
    only while that column is shown, since it reads every transcript."""
    live = load_live_status(live_dir, alive=alive, started=started)
    # Archived ones too, flagged: the list shows them only in its Archived view
    # (#32) or a group they're in.
    desktop: DesktopLoadResult = load_desktop_sessions(desktop_dir, live,
                                                       include_archived=True,
                                                       alive=alive, started=started)
    sessions = list(desktop.sessions)
    waiting = waiting or {}
    own = list(own)
    for item in own:
        info = item.to_info()
        if item.cli_session_id in running_own_ids and waiting.get(item.cli_session_id):
            info.state, info.detail = NEEDS_YOU, waiting[item.cli_session_id]
        elif item.cli_session_id in running_own_ids:
            info.state, info.detail = WORKING, (
                "answered, background work running"
                if item.cli_session_id in (background or ()) else "")
        elif (live.get(item.cli_session_id) is not None
              and live[item.cli_session_id].status == "busy"):
            # Someone resumed it elsewhere (a terminal); it is busy there.
            info.state, info.detail = WORKING, "running outside The Chat Place"
        sessions.append(info)
    listed = desktop.desktop_cli_ids | desktop.cowork_cli_ids | {o.cli_session_id for o in own}
    sessions.extend(load_terminal_sessions(terminal_projects_dir(), live, listed, TERMINAL_FACTS))
    for info in sessions:
        # Last activity follows Claude's work (#209): the desktop app's time,
        # and an own session's (set when The Chat Place starts or ends a
        # turn), stand still through a long turn or one run elsewhere. Only
        # while it's working or waiting on you, though: a transcript's file
        # can be rewritten without anything happening in it (68 archived
        # ones all changed in the same minute), and an idle session's own
        # time is the truer one. A terminal session's is its file's already.
        if info.state in (WORKING, NEEDS_YOU) and info.source != TERMINAL:
            info.last_activity_ms = max(info.last_activity_ms, info.transcript_written_ms())
    if last_messages:
        fill_last_messages(sessions)
    folders = [desktop_dir] if desktop_dir is not None else platform_paths.desktop_sessions_dirs()
    return Snapshot(sessions=sort_sessions(sessions, order),
                    desktop_cli_ids=desktop.desktop_cli_ids,
                    cowork_cli_ids=desktop.cowork_cli_ids,
                    live=live,
                    unreadable_files=desktop.unreadable_files,
                    desktop_groups=load_desktop_groups(folders))


def fill_last_messages(sessions: Iterable[SessionInfo],
                       cache: Optional[LastMessages] = None) -> None:
    """Each session's ``last_message``: the end of its transcript, read
    through ``cache`` (only files that changed since the last pass). One with
    no transcript, or none that can be read, says nothing."""
    cache = cache if cache is not None else LAST_MESSAGES
    seen = []
    now = time.monotonic()
    for info in sessions:
        with _NO_TRANSCRIPT_LOCK:
            missing = _NO_TRANSCRIPT.get(info.key)
        if (missing is not None and missing[0] == info.last_activity_ms
                and now - missing[1] < NO_TRANSCRIPT_RETRY):
            info.last_message = ""
            continue
        path = info.transcript_path()
        with _NO_TRANSCRIPT_LOCK:
            if path is None:
                _NO_TRANSCRIPT[info.key] = (info.last_activity_ms, now)
            else:
                _NO_TRANSCRIPT.pop(info.key, None)
        if path is None:
            info.last_message = ""
            continue
        seen.append(path)
        info.last_message = fill_last_message(info, path, cache)
    cache.keep_only(seen)


def fill_last_message(info: SessionInfo, path: Optional[Path] = None,
                      cache: Optional[LastMessages] = None) -> str:
    """One session's Last message column text ("" when there's none)."""
    cache = cache if cache is not None else LAST_MESSAGES
    path = path if path is not None else info.transcript_path()
    message = cache.get(path) if path is not None else None
    return last_message_line(message.label, message.text) if message else ""


def finished_turns(previous: Dict[str, str], current: Iterable[SessionInfo]) -> List[SessionInfo]:
    """Sessions that were working last time and are not now.

    ``previous`` maps session key -> state from the last snapshot. A session
    that has vanished, or been archived, is not reported.
    """
    ended = []
    for info in current:
        if info.archived:
            continue
        if previous.get(info.key) == WORKING and info.state != WORKING:
            ended.append(info)
    return ended


def last_reply_from_tail(path: Path, max_bytes: int = 512 * 1024) -> str:
    """The most recent reply in a transcript, reading only its last part.

    Transcripts can be tens of megabytes; the last reply is near the end. The
    first (probably partial) line of the tail is dropped.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            start = max(0, size - max_bytes)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return ""
    lines = split_jsonl(data)
    if start > 0 and lines:
        lines = lines[1:]
    parser = TranscriptParser()
    parser.feed(lines)
    reply = parser.transcript.last_reply()
    return reply.text if reply else ""
