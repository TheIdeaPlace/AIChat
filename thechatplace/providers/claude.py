"""Claude discovery behind the common provider boundary.

Keeps desktop ownership guards, terminal deduplication and live state intact.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..transcript import TranscriptReader, TranscriptParser, split_jsonl
from .. import platform_paths
from ..desktop_groups import load_desktop_groups
from ..sessions import (NEEDS_YOU, TERMINAL, WORKING, DesktopLoadResult,
                        load_desktop_sessions, load_terminal_sessions)
from .base import Snapshot


class ClaudeProvider:
    provider_id = "claude"

    def __init__(self, own, running_own_ids, *, desktop_dir, live_dir, alive,
                 waiting, started, background, projects_dir, terminal_facts,
                 load_live_status):
        self.own = [item for item in own if getattr(item, "provider", "claude") == "claude"]
        self.running_own_ids = set(running_own_ids)
        self.desktop_dir = desktop_dir
        self.live_dir = live_dir
        self.alive = alive
        self.waiting = waiting
        self.started = started
        self.background = background
        self.projects_dir = projects_dir
        self.terminal_facts = terminal_facts
        self.load_live_status = load_live_status

    def collect(self) -> Snapshot:
        own, running_own_ids = self.own, self.running_own_ids
        desktop_dir, live_dir = self.desktop_dir, self.live_dir
        alive, started = self.alive, self.started
        waiting, background = self.waiting, self.background
        live = self.load_live_status(live_dir, alive=alive, started=started)
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
        sessions.extend(load_terminal_sessions(self.projects_dir(), live, listed, self.terminal_facts))
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
        folders = [desktop_dir] if desktop_dir is not None else platform_paths.desktop_sessions_dirs()
        return Snapshot(sessions=sessions,
                        desktop_cli_ids=desktop.desktop_cli_ids,
                        cowork_cli_ids=desktop.cowork_cli_ids,
                        live=live,
                        unreadable_files=desktop.unreadable_files,
                        desktop_groups=load_desktop_groups(folders))


class ClaudeConversations:
    provider_id = "claude"

    def capabilities(self, session):
        from .base import SessionCapabilities

        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        own = session.is_own
        return SessionCapabilities(
            send=own, fork=not own and not session.cowork and bool(session.cli_session_id),
            delete=own, attachments=own, usage=True, changes=True,
            instructions=True, remote_control=True, desktop_link=session.can_open_in_claude,
            share_link=True, options=own, approvals=True, steer=own)

    def has_history(self, session) -> bool:
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        return session.transcript_path() is not None

    def open_reader(self, session):
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        path = session.transcript_path()
        return TranscriptReader(path) if path is not None else None

    def last_reply(self, session) -> str:
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        path = session.transcript_path()
        return last_reply_from_tail(path) if path is not None else ""

    def open_reader_with_facts(self, session):
        """Resolve once so Properties reads and sizes the same transcript."""
        from ..properties import transcript_facts
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        path = session.transcript_path()
        if path is None:
            return None, "", 0
        name, size = transcript_facts(path)
        return (TranscriptReader(path), name, size) if name else (None, "", 0)


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
