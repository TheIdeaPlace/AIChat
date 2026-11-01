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
from ..aside import COWORK_REFUSED, AsideRunner, build_aside_command
from ..chat_options import OptionsChange, choices_from_init
from ..claude_cli import ResumeRefused
from .dialogs import AskAsideDialog, ChatOptionsDialog


def _folder_key(folder: str) -> str:
    from .. import workplaces
    return workplaces.folder_key(folder or "")


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
        styles (None if Claude Code couldn't say): kept ones at once, else
        asked for in the background (no turn, no cost)."""
        from . import main_frame
        cache = self._chat_choices_cache()
        key = _folder_key(folder)
        if key in cache:
            wx.CallAfter(done, cache[key])
            return
        lookup = platform_paths.find_claude()
        if not lookup.path or not folder:
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
            done(choices)
        try:
            self._pool.submit(work)
        except RuntimeError:  # the pool is shut down: the window is closing
            pass

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
                                   feedback=self._feedback)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                if returning_to:
                    returning_to.SetFocus()
                return
            after, folders = dialog.options(), dialog.chosen_folders()
        finally:
            dialog.Destroy()
        if returning_to:
            returning_to.SetFocus()
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
            if self._session_exists(own):
                # Claude Code keeps the system prompt it recorded on the
                # chat's first turn, which would hide the change (#468).
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
        try:
            command = build_aside_command(lookup.path, info.cli_session_id, model,
                                          cowork_ids=self._snapshot.cowork_cli_ids)
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
            runner = main_frame.AsideRunner(command, cwd, question,
                                            lambda event: wx.CallAfter(on_event, event))
            runner.start()
            return runner

        def say(answer):
            # The answer to a key press: it may cut in (#468), as the reply
            # to what you just did; the level decides how much is said.
            text = announce.aside_text(answer, self.speech.announce)
            if text:
                self._say(text)
        returning_to = wx.Window.FindFocus()
        dialog = AskAsideDialog(self, info.title, ask, say=say, copy=self._copy_text,
                                feedback=self._feedback)
        try:
            dialog.ShowModal()
        finally:
            dialog.stop()
            dialog.Destroy()
        if returning_to:
            returning_to.SetFocus()


# Kept importable from here for tests that stand in for the runner.
__all__ = ["ClaudeExtras", "AsideRunner"]
