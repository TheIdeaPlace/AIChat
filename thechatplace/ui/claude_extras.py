"""The window's side of Ask Aside and Chat Options (#468), for Claude chats.

Ask Aside (Ctrl+Shift+Y) asks a question about the selected (or loaded)
Claude chat in a read-only copy that isn't saved (aside.py), so nothing is
added to the chat; Chat Options changes the agent, instructions, output style
and more folders of one of The Chat Place's own Claude chats for its later
turns (chat_options.py). New Session and Continue Here use the same dialog
before a chat starts.
"""
import os
import time

import wx

from .. import announce, platform_paths
from ..aside import (COWORK_REFUSED, NOTHING_FINISHED, AsideEvent, AsideRunner,
                     build_aside_command, finished_cut)
from ..chat_options import OptionsChange, choices_from_init
from ..claude_cli import ResumeRefused
from ..sessions import WORKING
from .dialogs import AskAsideDialog, ChatOptionsDialog


def _folder_key(folder: str) -> str:
    from .. import workplaces
    return workplaces.folder_key(folder or "")


def _give_focus_back(window) -> None:
    """Only if Windows hasn't put it back already: setting it again had a
    screen reader say the row twice (#468)."""
    if window and wx.Window.FindFocus() is not window:
        window.SetFocus()


class ClaudeExtras:
    # -- the agents and output styles Claude Code offers in a folder

    def _chat_choices_cache(self) -> dict:
        cache = getattr(self, "_chat_choices", None)
        if cache is None:
            cache = self._chat_choices = {}
        return cache

    def _remember_chat_choices(self, cwd: str, init_body) -> None:
        """Keep a turn's ``initialize`` answer's agents and styles for its
        folder, so Chat Options has them without asking Claude Code again."""
        choices = choices_from_init(init_body)
        if choices is not None and cwd:
            self._chat_choices_cache()[_folder_key(cwd)] = choices

    def _load_chat_choices(self, folder: str, done) -> None:
        """``done(choices)`` on the UI thread with the folder's agents and
        styles (None if Claude Code couldn't say): kept ones at once, and
        always asked for in the background (no turn, no cost), when ``done`` is
        called again. Kept ones alone would miss an agent added since the last
        turn, however long ago that was."""
        from . import main_frame
        cache = self._chat_choices_cache()
        key = _folder_key(folder)
        known = cache.get(key)
        if known is not None:
            wx.CallAfter(done, known)
        lookup = platform_paths.find_claude()
        if not lookup.path or not folder:
            if known is None:
                wx.CallAfter(done, None)
            return

        def work():
            choices = main_frame.fetch_chat_choices(lookup.path, folder)
            wx.CallAfter(finished, choices)

        def finished(choices):
            if self._gone():
                return
            if choices is not None:
                cache[key] = choices
            if choices is not None or known is None:
                done(choices)  # a failed refresh leaves the kept ones standing
        try:
            self._pool.submit(work)
        except RuntimeError:  # the pool is shut down: the window is closing
            pass

    def _aside_turn_running(self, info) -> bool:
        """Whether a Claude chat has a turn under way, for Ask Aside (#559):
        one of ours running here, or any chat whose live file says Claude Code
        is busy or waiting on you mid-turn (a permission, a question). Not the
        list's Needs You as such: for a desktop chat that's also the summary
        of a turn that has ended, and cutting then would leave that turn out."""
        if info.cli_session_id in self._runners:
            return True
        live = self._snapshot.live
        for session_id in (info.cli_session_id, info.desktop_session_id):
            status = live.get(session_id) if session_id else None
            if status is not None and status.status in ("busy", "waiting"):
                return True
        current = self._current_info(info.key) or info
        return current.state == WORKING

    def _aside_history(self, info) -> list:
        """This chat's Ask Aside questions, for as long as the app is open: in
        memory only, as an aside is throwaway, but closing the dialog
        shouldn't lose an answer you've just had. Claude Code's /btw keeps
        them about as long. Codex's chats use it too."""
        from ..aside import KEPT_QUESTIONS
        history = self._aside_histories.setdefault((info.provider, info.cli_session_id), [])
        del history[:-KEPT_QUESTIONS]
        return history

    # -- File, Chat Options

    def on_chat_options(self):
        """File, Chat Options (#468): agent, instructions, output style and
        more folders for one of The Chat Place's own Claude chats, from its
        next turn."""
        from .main_frame import kind_name
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if ((info.provider != "claude" or info.source == "kept")
                and not self._require_capability("chat_options", info)):
            return  # another provider's chat, or a kept copy: says why
        if not info.is_own:
            self._feedback(f"{info.title} is {kind_name(info)}: Chat Options are for Claude "
                           "chats started here.")
            return
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return
        before = own.chat_options()
        returning_to = wx.Window.FindFocus()
        dialog = ChatOptionsDialog(self, info.title, own.cwd, options=before,
                                   folders=list(own.allowed_dirs),
                                   choices=self._chat_choices_cache().get(_folder_key(own.cwd)),
                                   load_choices=self._load_chat_choices, existing=True,
                                   feedback=self._feedback, model=own.model)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                _give_focus_back(returning_to)
                return
            after, folders = dialog.options(), dialog.chosen_folders()
        finally:
            dialog.Destroy()
        _give_focus_back(returning_to)
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback("This chat is no longer in The Chat Place.")
            return
        change = OptionsChange(before, after,
                               added_dirs=[f for f in folders if f not in own.allowed_dirs],
                               removed_dirs=[f for f in own.allowed_dirs if f not in folders])
        if not change.options_changed and not change.added_dirs and not change.removed_dirs:
            self._feedback("Chat options unchanged.")
            return
        now = int(time.time() * 1000)
        times = {f: t for f, t in own.allowed_dirs_at.items() if f in folders}
        times.update({f: now for f in change.added_dirs})
        changes = dict(allowed_dirs=folders, allowed_dirs_at=times)
        if change.options_changed:
            changes.update(agent=after.agent, instructions=after.instructions,
                           output_style=after.output_style)
            if self._session_exists(own) or own.fork_source:
                # Claude Code keeps the system prompt it recorded on the
                # chat's first turn, which would hide the change (#468). A
                # copy not yet started (a branch) inherits its source's
                # recorded prompt the same way.
                changes["prompt_fresh"] = True
        if not self._store_write(self.store.update, own.cli_session_id, **changes):
            return
        said = change.said(info.title)
        if own.cli_session_id in self._runners:
            said = said[:-1] + "; the turn running now carries on as it started."
        self._feedback(said)
        self._update_heading()

    # -- File, Ask Aside

    def on_ask_aside(self):
        """File, Ask Aside (Ctrl+Shift+Y, #468): a question about the
        selected (or loaded) Claude chat, answered without being added to it."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.provider == 'codex':
            self._codex_ask_aside(info)
            return
        if info.provider == "claude" and info.cowork:
            self._feedback(COWORK_REFUSED)
            return
        if not self._require_capability("aside", info):
            return  # says why: another provider's chat, or a kept copy
        own = self.store.get(info.cli_session_id) if info.is_own else None
        if own is not None and not self._session_exists(own):
            self._feedback(f"{info.title} has no conversation yet to ask about.")
            return
        if own is None and info.transcript_path() is None:
            self._feedback(f"{info.title}'s conversation is no longer on disk, so there's "
                           "nothing to ask about.")
            return
        lookup = platform_paths.find_claude()
        if not lookup.path:
            wx.MessageBox(lookup.problem, "Ask Aside", wx.OK | wx.ICON_ERROR, self)
            return
        model = own.model if own is not None else ""
        cowork_ids = self._snapshot.cowork_cli_ids
        allowed_dirs = own.allowed_dirs if own is not None else ()

        def command_at(cut=""):
            return build_aside_command(lookup.path, info.cli_session_id, model,
                                       cowork_ids=cowork_ids, allowed_dirs=allowed_dirs,
                                       resume_at=cut)
        try:
            command = command_at()
        except (ResumeRefused, ValueError) as exc:
            self._feedback(str(exc))
            return
        cwd = own.cwd if own is not None else info.cwd
        if not cwd or not os.path.isdir(cwd):
            self._feedback(f"{info.title}'s folder ({cwd or 'unknown'}) doesn't exist any more, "
                           "so Claude can't be asked about it.")
            return
        from . import main_frame

        def ask(question, on_event):
            def start(at):
                runner = main_frame.AsideRunner(at, cwd, question,
                                                lambda event: wx.CallAfter(on_event, event))
                runner.start()
                return runner
            if not self._aside_turn_running(info):
                return start(command)
            # Decided as each question is asked: a turn may have started or
            # ended since the dialog opened (#559).
            try:
                return _AsideAtFinishedTurn(lambda: self.sessions.read(info), command_at, start,
                                            on_event).begin(self._pool.submit)
            except RuntimeError:  # the pool is shut down: the window is closing
                return "The Chat Place is closing, so Claude can't be asked now."

        def say(answer, failed=False):
            # The answer to a key press: it may cut in (#468), as the reply
            # to what you just did; the level decides how much is said.
            text = (f"Not answered. {answer}" if failed
                    else announce.aside_text(answer, self.speech.announce))
            if text:
                self._say(text)
        returning_to = wx.Window.FindFocus()
        dialog = AskAsideDialog(self, info.title, ask, say=say, copy=self._copy_text,
                                feedback=self._feedback, history=self._aside_history(info))
        try:
            dialog.ShowModal()
        finally:
            dialog.settle()
            dialog.Destroy()
        _give_focus_back(returning_to)


class _AsideAtFinishedTurn:
    """An aside asked while the chat has a turn running (#559). The copy has
    to end with the last finished turn, or Claude answers the running turn's
    message in it too, so the transcript is read afresh off the UI thread to
    find where that is (the open chat's reader can be a refresh behind a turn
    that has just started, which would cut a finished turn off), then the
    copy cut there is asked. It stands in for the runner: ``cancel`` stops
    it, and until the runner starts says "Stopped." itself, as a runner does.
    Everything after ``begin`` reaches ``on_event`` on the UI thread."""

    def __init__(self, read, command_at, start, on_event):
        self._read, self._command_at, self._start = read, command_at, start
        self._on_event = on_event
        self._runner = None
        self._stopped = False

    def begin(self, submit):
        submit(self._find_cut)
        return self

    def _find_cut(self):
        try:
            transcript = self._read()
            cut = None if transcript is None else finished_cut(transcript)
        except Exception:  # noqa: BLE001 - said in the dialog, below
            cut = None
        wx.CallAfter(self._ask, cut)

    def _ask(self, cut):
        if self._stopped:
            return  # stopped, or the dialog closed, while the chat was read
        if cut is None:
            self._on_event(AsideEvent("failed", "Couldn't read this chat's conversation to "
                                                "find its last finished turn."))
            return
        if not cut:
            self._on_event(AsideEvent("failed", NOTHING_FINISHED))
            return
        try:
            command = self._command_at(cut)
        except (ResumeRefused, ValueError) as exc:
            self._on_event(AsideEvent("failed", str(exc)))
            return
        self._runner = self._start(command)

    def cancel(self):
        if self._stopped:
            return
        self._stopped = True
        if self._runner is not None:
            self._runner.cancel()
        else:
            self._on_event(AsideEvent("failed", "Stopped."))


# Kept importable from here for tests that stand in for the runner.
__all__ = ["ClaudeExtras", "AsideRunner"]
