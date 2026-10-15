"""Gathering the whole session list in one pass, and noticing turn endings.

Runs on a background thread (it reads ~200 small files); returns plain data
the UI applies on the main thread.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import platform_paths
from .providers.base import Snapshot
from .providers.claude import ClaudeProvider, last_reply_from_tail  # noqa: F401
from .own_store import OwnSession
from .sessions import (IDLE, SORT_STATUS, WORKING, SessionInfo, load_live_status,
                       sort_sessions)
from .transcript import LastMessages, SessionFactsCache
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
    provider = ClaudeProvider(
        own, running_own_ids, desktop_dir=desktop_dir, live_dir=live_dir,
        alive=alive, waiting=waiting, started=started, background=background,
        projects_dir=terminal_projects_dir, terminal_facts=TERMINAL_FACTS,
        load_live_status=load_live_status)
    snapshot = provider.collect()
    if last_messages:
        fill_last_messages(snapshot.sessions)
    snapshot.sessions = sort_sessions(snapshot.sessions, order)
    return snapshot


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


def finished_turns(previous: Dict[str, str], current: Iterable[SessionInfo],
                   *, stale_providers: Optional[Set[str]] = None) -> List[SessionInfo]:
    """Sessions that were working last time and are not now.

    ``previous`` maps session key -> state from the last snapshot. A session
    that has vanished, been archived or comes from cached discovery is not
    reported. Codex must explicitly become idle; unknown status and waiting
    for an approval are not evidence that its turn finished (#184) (#171).
    """
    ended = []
    for info in current:
        if info.archived or info.provider in (stale_providers or ()):
            continue
        if info.provider == "codex" and info.state != IDLE:
            continue
        if previous.get(info.key) == WORKING and info.state != WORKING:
            ended.append(info)
    return ended
