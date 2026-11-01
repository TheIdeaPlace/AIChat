"""Claude discovery behind the common provider boundary.

Keeps desktop ownership guards, terminal deduplication and live state intact.
"""
from __future__ import annotations

import time

import os
from dataclasses import replace
from pathlib import Path

from ..transcript import TranscriptReader, TranscriptParser, split_jsonl
from .. import platform_paths
from ..desktop_groups import load_desktop_groups
from ..sessions import (IDLE, KEPT, NEEDS_YOU, TERMINAL, WORKING, SessionInfo, DesktopLoadResult,
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
        sessions = preserve_sessions(sessions)
        folders = [desktop_dir] if desktop_dir is not None else platform_paths.desktop_sessions_dirs()
        return Snapshot(sessions=sessions,
                        desktop_cli_ids=desktop.desktop_cli_ids,
                        cowork_cli_ids=desktop.cowork_cli_ids,
                        live=live,
                        unreadable_files=desktop.unreadable_files,
                        desktop_groups=load_desktop_groups(folders))


class ClaudeConversations:
    provider_id = "claude"

    def close(self):
        from ..history_keeper import close_all
        close_all(final=True)

    def capabilities(self, session):
        from .base import SessionCapabilities

        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        if session.source == KEPT:
            return SessionCapabilities(usage=True, changes=True)
        own = session.is_own
        return SessionCapabilities(
            send=own, fork=not own and not session.cowork and bool(session.cli_session_id),
            delete=own, attachments=own, usage=True, changes=True,
            instructions=True, remote_control=True, desktop_link=session.can_open_in_claude,
            share_link=True, options=own, approvals=True, steer=own,
            # A branch is a copy, so any chat Claude Code can resume from
            # (#468); putting files back opens the session itself.
            branch=not session.cowork and bool(session.cli_session_id), rewind_files=own,
            # A read-only copy that isn't saved (aside.py): any session whose
            # transcript Claude Code can read, so not Cowork's (#91).
            aside=not session.cowork and bool(session.cli_session_id),
            chat_options=own)

    def has_history(self, session) -> bool:
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        return _history_path(session) is not None

    def open_reader(self, session):
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        path = _history_path(session)
        return KeptClaudeReader(session, path) if path is not None else None

    def last_reply(self, session) -> str:
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        path = _history_path(session)
        return last_reply_from_tail(path) if path is not None else ""

    def open_reader_with_facts(self, session):
        """Resolve once so Properties reads and sizes the same transcript."""
        from ..properties import transcript_facts
        if session.provider != self.provider_id:
            raise ValueError("Not a Claude session")
        path = _history_path(session)
        if path is None:
            return None, "", 0
        name, size = transcript_facts(path)
        return (KeptClaudeReader(session, path), name, size) if name else (None, "", 0)


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


# Metadata is deliberately separate from Claude's JSONL format. Only this
# adapter decides which native file and session the opaque keeper represents.
_KEEP_FIELDS = ('key', 'title', 'cwd', 'cli_session_id', 'desktop_session_id',
                'created_ms', 'last_activity_ms', 'archived', 'cowork', 'cowork_folder')


def kept_identity(session):
    return ('claude', session.cli_session_id,
            str(session.claude_home or platform_paths.claude_home()))


def keep_session(session, path=None):
    from ..history_keeper import keeper
    if (session.provider != 'claude' or session.source == KEPT
            or not platform_paths.is_safe_id(session.cli_session_id)):
        return
    path = path if path is not None else session.transcript_path()
    if path is not None:
        metadata = {key: getattr(session, key) for key in _KEEP_FIELDS}
        metadata['claude_home'] = str(session.claude_home or platform_paths.claude_home())
        metadata['source'] = session.source
        keeper().enqueue(kept_identity(session), path, metadata)


# Originals known to be gone, by kept token: (last activity then, when
# checked). Looking for one globs every project folder, so it's looked for
# again only when the chat shows new activity or after a few minutes.
_MISSING = {}
MISSING_RECHECK_SECONDS = 300


def _known_missing(token, session, saved) -> bool:
    if token not in saved:
        return False
    seen = _MISSING.get(token)
    return (seen is not None and seen[0] == session.last_activity_ms
            and time.monotonic() - seen[1] < MISSING_RECHECK_SECONDS)


def preserve_sessions(sessions):
    from ..history_keeper import keeper
    store = keeper()
    saved = {token: metadata for token, metadata in store.records()}
    seen, result = set(), []
    for session in sessions:
        if not platform_paths.is_safe_id(session.cli_session_id):
            result.append(session)
            continue
        token = store.token(kept_identity(session))
        seen.add(token)
        path = None if _known_missing(token, session, saved) else session.transcript_path()
        if path is not None:
            _MISSING.pop(token, None)
            keep_session(session, path)
        elif token in saved:
            _MISSING[token] = (session.last_activity_ms, time.monotonic())
            session = _as_kept(session, store.path(token))
        result.append(session)
    for token, metadata in saved.items():
        if token in seen:
            continue
        try:
            native_id = metadata['cli_session_id']
            if (not platform_paths.is_safe_id(native_id)
                    or any(not isinstance(metadata.get(key), str) for key in
                           ('key', 'title', 'cwd', 'desktop_session_id', 'cowork_folder'))
                    or any(type(metadata.get(key)) is not int or metadata[key] < 0
                           for key in ('created_ms', 'last_activity_ms'))
                    or any(type(metadata.get(key)) is not bool for key in ('archived', 'cowork'))):
                continue
            values = {key: metadata[key] for key in _KEEP_FIELDS}
            home = metadata.get('claude_home')
            source = metadata.get('source') if isinstance(metadata.get('source'), str) else ''
            session = SessionInfo(source=KEPT, **values, kept_from=source,
                                  claude_home=Path(home) if home else None)
            if store.token(kept_identity(session)) != token:
                continue
            result.append(_as_kept(session, store.path(token)))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _as_kept(session, path):
    if session.is_own and session.state in (WORKING, NEEDS_YOU):
        return session  # A temporary rewrite must not remove Stop from a live turn.
    return replace(session, source=KEPT, state=IDLE, detail='original transcript is gone',
                   kept_transcript=path, remote=False, unread=False,
                   kept_from=session.kept_from if session.source == KEPT else session.source)


class KeptClaudeReader:
    """Switch to the last good copy if cleanup occurs while a chat is open."""
    def __init__(self, session, path):
        self.session = session
        self._reader = TranscriptReader(path)

    @property
    def path(self):
        return self._reader.path

    @property
    def transcript(self):
        return self._reader.transcript

    def refresh(self):
        from ..history_keeper import keeper
        changed = self._reader.refresh()
        if self._reader.path.is_file():
            keep_session(self.session, self._reader.path)
            return changed
        saved = keeper().saved(kept_identity(self.session))
        if saved is not None and saved != self._reader.path:
            _mark_kept(self.session, saved)
            self._reader = TranscriptReader(saved)
            return self._reader.refresh() or changed
        return changed


def _mark_kept(session, path):
    if session.is_own and session.state in (WORKING, NEEDS_YOU):
        return
    session.source = KEPT
    session.state, session.detail = IDLE, 'original transcript is gone'
    session.kept_transcript = path
    session.remote = session.unread = False


def _history_path(session):
    path = session.transcript_path()
    if path is None and platform_paths.is_safe_id(session.cli_session_id):
        from ..history_keeper import keeper
        saved = keeper().saved(kept_identity(session))
        if saved is not None:
            _mark_kept(session, saved)
            return saved
    return path


def check_kept_before_send(session, *, active):
    """A stale busy row cannot authorize resuming a deleted original.

    The window knows whether it still owns a running process. Only that
    positive knowledge permits an active transcript rewrite to keep its turn.
    """
    if session.source == KEPT or active or not platform_paths.is_safe_id(session.cli_session_id):
        return
    from ..history_keeper import keeper
    saved = keeper().saved(kept_identity(session))
    if saved is not None and session.transcript_path() is None:
        session.state = IDLE
        _mark_kept(session, saved)
