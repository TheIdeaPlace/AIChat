"""The window's side of subagents (#468): keeping the loaded Claude chat's
subagents up to date in the background, saying when one ends, and opening
one's conversation. What the files say is worked out in
``thechatplace.subagents``; the conversation is shown by
``subagent_chat.SubagentChatDialog``.
"""
from __future__ import annotations

import dataclasses
import os
from typing import List, Optional

import wx

from .. import subagents
from ..sessions import NEEDS_YOU, WORKING, SessionInfo
from ..speech import ANNOUNCE_FULL
from ..transcript import Transcript
from .subagent_chat import SubagentChatDialog


#: A conversation this big takes a moment to read, so that's said first.
LARGE_CONVERSATION = 1024 * 1024


class SubagentsHost:
    """Mixed into MainFrame."""

    #: The loaded chat's subagents as last read, oldest first.
    _chat_subagents: list = []
    #: Each one's status at the last read, to tell when one ends; None
    #: until a read has found them, so what's there on loading isn't news.
    _subagent_statuses: Optional[dict] = None
    _subagent_tracker = None
    _subagents_busy = False
    _subagents_pending = False
    #: The subagent whose conversation is open: it says its own ending.
    _subagent_open_id: Optional[str] = None

    def _reset_subagents(self) -> None:
        """Another chat was loaded, or none."""
        self._chat_subagents = []
        self._subagent_statuses = None
        self._subagent_tracker = None
        self._subagents_pending = False

    def _subagents_live(self, info: SessionInfo) -> bool:
        """The chat is working now, so a subagent that hasn't said it ended
        is still at work."""
        current = self._current_info(info.key) or info
        return (current.state in (WORKING, NEEDS_YOU)
                or (info.is_own and info.cli_session_id in self._runners))

    def _refresh_subagents(self, generation: int, reader) -> None:
        """After the loaded chat's transcript is read: read its subagents'
        transcripts too, in the background. Only a Claude transcript has
        them; another backend's reader is never given a Claude path."""
        info = self._open
        if (info is None or info.provider != "claude" or generation != self._open_generation
                or not isinstance(getattr(reader, "transcript", None), Transcript)
                or getattr(reader, "path", None) is None):
            return
        if self._subagents_busy:
            self._subagents_pending = True
            return
        tracker = self._subagent_tracker
        if tracker is None:
            tracker = self._subagent_tracker = subagents.SubagentTracker(
                reader.path, subagents.source_transcripts(self.store, info))
        # A copy taken here, while no read of the transcript is running.
        parent = Transcript(agents=[dataclasses.replace(c) for c in reader.transcript.agents],
                            agent_notes=dict(reader.transcript.agent_notes))
        live = self._subagents_live(info)
        self._subagents_busy = True

        def work():
            try:
                tracker.refresh()
                found = tracker.subagents(parent, live)
            except Exception:  # noqa: BLE001 - a format change mustn't break the chat
                found = None
            wx.CallAfter(self._apply_subagents, generation, tracker, found)

        try:
            self._pool.submit(work)
        except RuntimeError:  # the pool is shut down: the window is closing
            self._subagents_busy = False

    def _apply_subagents(self, generation: int, tracker, found) -> None:
        self._subagents_busy = False
        if self._gone() or self._closing:
            return
        pending, self._subagents_pending = self._subagents_pending, False
        if (found is not None and generation == self._open_generation
                and tracker is self._subagent_tracker):
            self._take_subagents(found)
        # A read asked for while this one ran (perhaps for a chat loaded since)
        # runs now, unless the transcript is being read: copying its Agent
        # calls then could catch one half updated, and that read's end asks
        # again anyway.
        if pending and self._reader is not None and not self._reader_busy:
            self._refresh_subagents(self._open_generation, self._reader)

    def _take_subagents(self, found) -> None:
        before = self._subagent_statuses
        self._chat_subagents = found
        self._subagent_statuses = subagents.statuses(found)
        if before is not None:
            for sub in subagents.newly_ended(before, found):
                if sub.agent_id == self._subagent_open_id:
                    continue  # its conversation is open: that says so
                if self.speech.announce == ANNOUNCE_FULL:
                    # Nothing else says a subagent ended: the tool activity
                    # speech says only that Claude used Agent.
                    self._say(sub.ended_text(), interrupt=False)

    def _subagent_edits(self) -> list:
        """The files the loaded chat's subagents changed (#468)."""
        return subagents.edits_of(self._chat_subagents)

    def _subagents_in_turn(self, turn: int) -> List[subagents.Subagent]:
        return subagents.in_turn(self._chat_subagents, turn) if turn else []

    def _latest_subagent(self, agent_id: str) -> Optional[subagents.Subagent]:
        return next((s for s in self._chat_subagents if s.agent_id == agent_id), None)

    def _open_subagent(self, sub: subagents.Subagent, parent=None) -> None:
        """Show a subagent's conversation over ``parent`` (Lists). Read once
        here; a running one is then kept up by the dialog."""
        info = self._open
        if info is None or sub.path is None:
            return
        try:
            if os.path.getsize(sub.path) > LARGE_CONVERSATION:
                self._feedback(f"Reading {sub.name}'s conversation…")
            with wx.BusyCursor():
                reader = subagents.open_conversation(sub.path, sub.agent_type)
                reader.refresh()
        except Exception as exc:  # noqa: BLE001
            self._feedback(f"Couldn't read {sub.name}'s conversation: {exc}")
            return
        dialog = SubagentChatDialog(
            parent or self, self._latest_subagent(sub.agent_id) or sub, info.title, reader,
            read_message=self._read_whole_message, copy=self._copy_text,
            feedback=self._feedback, latest=lambda: self._latest_subagent(sub.agent_id),
            show_activity=self._show_activity,
            whole_in_list=self.speech.full_messages_in_list)
        self._subagent_open_id = sub.agent_id
        try:
            self._show_subagent_chat(dialog)
        finally:
            self._subagent_open_id = None
            dialog.Destroy()

    def _show_subagent_chat(self, dialog) -> int:
        """Show a subagent's conversation modally. Tests replace this."""
        return dialog.ShowModal()

    def on_subagents(self) -> None:
        """View, Subagents (#468): Lists on Subagents, for the whole chat."""
        from .. import lists
        self.on_lists(lists.SUBAGENTS, lists.SCOPE_CHAT)

    def on_message_subagents(self) -> None:
        """The messages' Subagent Conversations: the subagents started in the
        selected message's turn, in Lists."""
        from .. import lists
        self.on_lists(lists.SUBAGENTS, lists.SCOPE_MESSAGE)
