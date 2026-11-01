"""The window's side of live control of a running Claude turn, and the
turn events it says or keeps (#468). The words are in ``live_turn``; the
protocol is ``TurnRunner``'s (set_model, set_permission_mode, stop_task,
get_context_usage).

Each part stands alone, so one can be dropped without the others:
live Change Model and Change Permission Mode (``_live_change``), keeping
the chat's mode when Claude Code changes it (``mode`` events), Stop
Background Task, retries, fallbacks and memories, and the context
breakdown in Usage and Context.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

import wx

from .. import live_turn
from ..claude_cli import (PERMISSION_MODE_VALUES, TURN_ENDED, mode_label,
                          normalize_permission_mode)
from ..speech import ANNOUNCE_FULL


class LiveTurns:
    """Mixed into MainFrame."""

    #: The latest get_context_usage answer per Claude chat (session id).
    _context_usage: Dict[str, dict] = {}
    #: The mode a chat had when Claude Code put it in plan mode itself, so
    #: approving the plan offers that mode again rather than accept edits.
    _mode_before_plan: Dict[str, str] = {}
    _retry_throttle: Optional[live_turn.RetryThrottle] = None

    # -- Change Model and Change Permission Mode, now -----------------------

    def _live_change(self, info, own, send: Callable, done_text: str, later_text: str,
                     ended_text: str = "") -> None:
        """A setting changed for a Claude chat of ours. While a turn runs,
        it's sent to that turn too (``send(runner, on_done)``): said as
        ``done_text`` once Claude Code takes it, else ``later_text`` (from
        its next turn, as before), or ``ended_text`` if the turn ended
        first. With no turn running, ``later_text`` at once."""
        runner = self._runners.get(own.cli_session_id) if info.provider == "claude" else None
        if runner is None:
            self._feedback(later_text)
            return

        def answered(ok, _response, error):
            wx.CallAfter(self._live_changed, ok, done_text,
                         (ended_text or later_text) if error == TURN_ENDED else later_text)
        if not send(runner, answered):
            self._feedback(later_text)

    def _live_changed(self, ok: bool, done_text: str, later_text: str) -> None:
        if self._gone():
            return
        self._feedback(done_text if ok else later_text)

    # -- Turn events -----------------------------------------------------------

    def _live_turn_event(self, session_id: str, title: str, event, is_open_now: bool) -> bool:
        """Say or keep what a running turn reports beyond its reply. True if
        ``event`` was one of these and is dealt with."""
        kind = event.kind
        data = event.data or {}
        if kind in ("finished", "failed"):
            self._retry_throttle_for().turn_ended(session_id)
            return False
        if kind == "mode":
            self._claude_changed_mode(session_id, title, event.text)
            return True
        if kind == "retry":
            text = live_turn.retry_text(data)
            said = text if is_open_now else f"{title}: {text}"
            if self._retry_throttle_for().should_speak(session_id, time.monotonic()):
                self._say(said, interrupt=False)  # news, not over what's being read
            else:
                self._status(said)
            if is_open_now:
                self._update_send_state()
            return True
        if kind == "fallback":
            text = live_turn.fallback_text(data)
            self._say(text if is_open_now else f"{title}: {text}", interrupt=False)
            return True
        if kind == "memory":
            text = live_turn.memory_text(data)
            text = text if is_open_now else f"{title}: {text}"
            if self.speech.announce == ANNOUNCE_FULL:
                self._say(text, interrupt=False)
            else:
                self._status(text)
            return True
        if kind == "context":
            self._context_usage = {**self._context_usage, session_id: data}
            return True
        return False

    def _retry_throttle_for(self) -> live_turn.RetryThrottle:
        if self._retry_throttle is None:
            self._retry_throttle = live_turn.RetryThrottle()
        return self._retry_throttle

    def _claude_changed_mode(self, session_id: str, title: str, mode: str) -> None:
        """Claude Code says the chat's permission mode changed: a live Change
        Permission Mode (already kept), approving a plan (kept too), or
        Claude entering plan mode itself. Kept with the chat, so the next
        turn starts in it and Chat Permissions and the heading say it."""
        own = self.store.get(session_id)
        if own is None or mode not in PERMISSION_MODE_VALUES:
            return
        now = normalize_permission_mode(own.permission_mode)
        if mode != "plan":
            self._forget_mode_before_plan(session_id)  # out of plan mode
        if now == mode:
            return
        if mode == "plan":
            self._mode_before_plan = {**self._mode_before_plan, session_id: now}
        if not self._store_write(self.store.update, session_id, permission_mode=mode):
            return
        self._update_heading()
        self._status(f"{title}: Claude Code switched to {mode_label(mode)} mode.")

    def _forget_mode_before_plan(self, session_id: str) -> None:
        """The chat left plan mode, or you chose its mode yourself."""
        if session_id in self._mode_before_plan:
            self._mode_before_plan = {k: v for k, v in self._mode_before_plan.items()
                                      if k != session_id}

    def _mode_to_offer_after_plan(self, session_id: str, stored: str) -> str:
        """The mode Approve Plan starts on: the chat's, or, when Claude
        entered plan mode itself, the one it had before."""
        if stored == "plan":
            return self._mode_before_plan.get(session_id, "")
        return stored

    # -- Stop Background Task ----------------------------------------------------

    def _background_entries(self, info=None) -> List[dict]:
        """The running background tasks of the loaded Claude chat's turn."""
        info = info if info is not None else self._open
        if info is None or info.provider != "claude" or not info.is_own:
            return []
        runner = self._runners.get(info.cli_session_id)
        return list(getattr(runner, "background_entries", None) or [])

    def on_stop_background_task(self):
        """File, Stop Background Task: end one shell command or agent Claude
        left running in the background, keeping the turn and the rest."""
        info = self._open
        if info is None or info.provider != "claude" or not info.is_own:
            self._feedback("Load one of your Claude chats with background work running first.")
            return
        runner = self._runners.get(info.cli_session_id)
        entries = [e for e in self._background_entries(info) if e.get("task_id")]
        if runner is None or not entries:
            self._feedback(f"No background work is running in {info.title}.")
            return
        now = runner.started_at + runner.elapsed()
        index = self._choose("Stop Background Task",
                             f"Background work in {info.title}. Stop which?",
                             [live_turn.background_choice(e, now) for e in entries])
        if index is None:
            return
        entry = entries[index]
        description = " ".join(str(entry.get("description") or "the task").split())
        if wx.MessageBox(f"Stop {description}? Claude Code ends it at once; the turn and any "
                         "other background work carry on.", "Stop Background Task",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return
        session_id = info.cli_session_id

        def answered(ok, _response, error):
            wx.CallAfter(self._background_task_stopped, session_id, entry, description, ok,
                         error)
        if not runner.stop_task(entry["task_id"], answered):
            self._feedback(f"{info.title}'s turn has ended, so there's nothing to stop.")
            return
        self._status(f"Stopping {description}.")

    def _background_task_stopped(self, session_id: str, entry: dict, description: str,
                                 ok: bool, error: str) -> None:
        if self._gone():
            return
        runner = self._runners.get(session_id)
        live = getattr(runner, "background_entries", None) or []
        still = any(e.get("task_id") == entry.get("task_id") for e in live)
        if not ok and (runner is None or error == TURN_ENDED):
            ok, still = True, False  # the turn ended, and the work with it
        self._feedback(live_turn.stop_result_text(description, ok, still, error))
        self._update_send_state()

    # -- Turn Status and Usage and Context ----------------------------------------

    def _retry_words(self, runner) -> str:
        """For Turn Status: " Claude is retrying: …" while it is, else ""."""
        text = live_turn.retry_status_text(getattr(runner, "retry", None))
        return f" {text}" if text else ""

    def _context_breakdown(self, info) -> List[str]:
        """What fills a Claude chat's context, as Claude Code last said."""
        if info is None or info.provider != "claude" or not info.is_own:
            return []
        return live_turn.context_lines(self._context_usage.get(info.cli_session_id))
