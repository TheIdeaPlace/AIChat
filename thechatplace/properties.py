"""View, Properties (Alt+Enter, #214): what can be known about a session or
a message, as "Property: value" lines to arrow through.

Plain data in, plain lines out, with no wx, so it can be tested on its own.
The session's lines need its transcript read, which the window does on a
background thread (``session_properties`` is safe to call there).
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import List, Optional

from .changes import by_file
from .claude_cli import EFFORT_LABELS, mode_label, model_label, model_spoken
from .codeblocks import find_code_blocks
from .export import message_moment
from .message_links import find_links
from .sessions import STATUS_UNAVAILABLE, WORKING, NEEDS_YOU, SessionInfo, how_long_ago, state_with_detail
from .transcript import (ASSISTANT, PERMISSION_NOTICE, QUEUED, TAIL_KINDS, TOOL, USER, ChatMessage,
                         Transcript)
from . import tasklist, usage
from .ui_text import codex_effort_label


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _date(moment: datetime) -> str:
    """"11 October 2026 at 14:05"."""
    return f"{moment.day} {moment.strftime('%B %Y')} at {moment.strftime('%H:%M')}"


def describe_ms(then_ms: int, now_ms: Optional[int] = None) -> str:
    """"11 October 2026 at 14:05 (2 hours ago)", or "not recorded"."""
    if not then_ms:
        return "not recorded"
    try:
        moment = datetime.fromtimestamp(then_ms / 1000)
    except (OverflowError, OSError, ValueError):
        return "not recorded"
    return f"{_date(moment)} ({how_long_ago(then_ms, now_ms)})"


def describe_size(size: int) -> str:
    """"512 bytes", "14 KB", "2.3 MB"."""
    if size < 1024:
        return _plural(size, "byte")
    kilobytes = round(size / 1024)
    if kilobytes < 1024:  # not "1,024 KB" just under a megabyte
        return f"{kilobytes:,} KB"
    return f"{size / (1024 * 1024):,.1f} MB"


def session_kind(info: SessionInfo) -> str:
    kind = info.kind_label
    return kind[0].upper() + kind[1:]


def _conversation(transcript: Transcript) -> List[ChatMessage]:
    """The messages the list shows with Show Tool Activity off."""
    return [m for m in transcript.messages if not m.is_activity and m.kind not in TAIL_KINDS]


def saved_rule_lines(count=None, error='', *, pending=False):
    if error:
        return ['Saved command rules check: ' + error]
    if pending:
        return ['Saved command rules: checking...']
    if type(count) is int and count >= 0:
        return [f'Saved user/system rule files in effect: {count}']
    return ['Saved user/system rule files: not checked']


def session_properties(info: SessionInfo, transcript: Optional[Transcript] = None,
                       transcript_path: Optional[str] = None, transcript_size: int = 0,
                       own=None, reported_window: int = 0,
                       now_ms: Optional[int] = None, read_error: str = "", saved_rule_count=None) -> List[str]:
    """The session's properties. ``transcript`` is None when it isn't on
    disk, or couldn't be read (``read_error`` says why); ``own`` is its
    ``OwnSession`` for one of The Chat Place's own; ``reported_window`` the
    context window Claude Code reported for its model, 0 if none."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    lines = [f"Title: {info.title or 'Untitled session'}",
             f"Provider: {info.provider.capitalize()}",
             f"Kind: {session_kind(info)}"]
    if info.cowork:
        lines.append(f"Folder: {info.cowork_folder or 'none'}")
        lines.append(f"Working folder: {info.cwd or 'not recorded'}")
    else:
        lines.append(f"Folder: {info.cwd or 'not recorded'}")
    # The state word follows "Status: ", so the "status unavailable" state
    # would say the word twice there (#290); the list and heading keep it.
    state = "unavailable" if info.state == STATUS_UNAVAILABLE else info.state
    status = state_with_detail(state, info.detail)
    lines.append(f"Status: {status[:1].upper()}{status[1:]}")
    lines.append(f"Started: {describe_ms(info.created_ms, now_ms)}")
    lines.append(f"Last activity: {describe_ms(info.last_activity_ms, now_ms)}")
    if transcript is not None and transcript.model:
        model = model_spoken(transcript.model) if info.provider == "claude" else transcript.model
        lines.append(f"Model of the latest reply: {model}")
    if own is not None and info.provider == "claude":
        lines.append(f"Model chosen: {model_label(own.model)}")
        lines.append(f"Effort: {EFFORT_LABELS.get(own.effort, own.effort)}")
    if own is not None and info.provider == 'codex':
        options = own.provider_options
        lines.append(f"Model chosen: {options.get('model') or 'configured Codex model'}")
        lines.append(f"Effort: {codex_effort_label(options['effort']) if options.get('effort') else 'configured Codex effort'}")
        sandbox = options.get('sandbox', 'read-only')
        lines.append('Sandbox: ' + {'read-only': 'read-only', 'workspace-write': 'can edit files in its folder'}.get(sandbox, 'not reported'))
        lines.append('Approval policy: ' + {'on-request': 'on request', 'never': 'never ask'}.get(options.get('approval_policy', 'on-request'), 'not reported'))
        if sandbox == 'workspace-write':
            lines.append('Writable folder: ' + options.get('workspace_root', 'not verified'))
    if info.provider == "codex":
        lines.extend(saved_rule_lines(saved_rule_count))
    mode = own.permission_mode if own is not None else info.permission_mode
    if mode and info.provider == "claude":
        lines.append(f"Permission mode: {mode_label(mode)}")
    if transcript is not None:
        conversation = _conversation(transcript)
        lines.append(f"Messages: {len(conversation):,}")
        lines.append(f"Your messages: {sum(1 for m in conversation if m.kind == USER):,}")
        lines.append(f"{info.provider.capitalize()}'s replies: "
                     f"{sum(1 for m in conversation if m.kind == ASSISTANT):,}")
        lines.append(f"Tool calls: {sum(1 for m in transcript.messages if m.kind == TOOL):,}")
        lines.append(f"Links: {len(find_links(conversation)):,}")
        blocks = sum(len(find_code_blocks(m.text)) for m in conversation)
        lines.append(f"Code blocks: {blocks:,}")
        if info.provider == "claude":
            lines.append(f"Files changed: {len(by_file(transcript.edits)):,}")
        if transcript.tasks:
            # Progress as last reported by the provider (#231, #318).
            lines.append(f"Tasks: {tasklist.progress(transcript.tasks, info.state in (WORKING, NEEDS_YOU))}")
        if transcript.context_tokens:
            tokens = transcript.context_tokens
            window = (usage.context_window(transcript.model, tokens, reported_window)
                      if info.provider == "claude" else 0)
            if window:
                share = round(usage.context_share(tokens, window) * 100)
                lines.append(f"Context: {share}% full, {tokens:,} of {window:,} tokens")
            else:
                lines.append(f"Context: {tokens:,} tokens used (the window's size isn't "
                             "known yet)")
        if transcript.compactions:
            lines.append(f"Times compacted: {transcript.compactions:,}")
        if transcript.unreadable_lines:
            lines.append(f"Unreadable transcript lines: {transcript.unreadable_lines:,}")
    lines.append(f"Groups: {', '.join(info.groups) if info.groups else 'none'}")
    if info.provider == "claude":
        lines.append(f"Remote Control: {'on' if info.remote else 'off'}")
    if info.unread:
        lines.append("New reply: yes")
    if info.archived:
        lines.append("Archived: yes")
    if info.hidden:
        lines.append("Hidden: yes")
    if own is not None and own.forked_from:
        lines.append(f"Continued from: {own.forked_from}")
    lines.append(f"Session ID: {info.cli_session_id or 'none'}")
    if info.desktop_session_id:
        lines.append(f"Desktop app ID: {info.desktop_session_id}")
    if transcript_path:
        lines.append(f"Transcript: {transcript_path}")
        lines.append(f"Transcript size: {describe_size(transcript_size)}")
        if transcript is None:
            # Locked by another program, say: there, but not read.
            lines.append(f"Transcript read: failed ({read_error or 'unknown error'})")
    elif info.provider != "claude":
        lines.append(f"History: {'read' if transcript is not None else 'unavailable'} "
                     f"from {info.provider.capitalize()}")
    else:
        lines.append("Transcript: not on disk")
    return lines


def message_properties(message: ChatMessage, position: int, total: int,
                       now: Optional[datetime] = None) -> List[str]:
    """A message's properties; ``position`` counts from 1 in the messages
    list as shown, of ``total``."""
    text = message.text or ""
    lines = [f"From: {message.label}"]
    moment = message_moment(message.timestamp)
    if moment is not None:
        now = now or datetime.now(moment.tzinfo)
        ago = how_long_ago(int(moment.timestamp() * 1000), int(now.timestamp() * 1000))
        lines.append(f"Time: {_date(moment)} ({ago})")
    else:
        lines.append("Time: not recorded")
    lines.append(f"Position: {position:,} of {total:,}")
    lines.append(f"Lines: {len(text.splitlines()):,}")
    lines.append(f"Words: {len(text.split()):,}")
    lines.append(f"Characters: {len(text):,}")
    lines.append(f"Links: {len(find_links([message])):,}")
    lines.append(f"Code blocks: {len(find_code_blocks(text)):,}")
    if message.kind == QUEUED:
        lines.append("Queued: yes, sent when Claude's turn ends")
    elif message.kind == PERMISSION_NOTICE:
        lines.append("Allowed by The Chat Place, for a rule kept with this chat")
    elif message.key:  # a queued row's key is the app's own, not Claude Code's
        lines.append(f"Message ID: {message.key}")
    return lines


def transcript_facts(path) -> tuple:
    """(path text, size in bytes) for a transcript file, or ("", 0)."""
    try:
        return os.fspath(path), os.path.getsize(path)
    except (OSError, TypeError):
        return "", 0
