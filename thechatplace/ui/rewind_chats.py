"""Rewind to Here and Branch from Here (#468): the window's side of rewind.py.

Both make a new chat of The Chat Place's own from part of the loaded Claude
chat's history, and Rewind to Here can put files back too. The new chat is
made unstarted, with ``fork_source`` and ``fork_at``, and opened at once:
Claude Code makes the copy with its first turn, when you press Send, through
the same path as a Continue Here copy whose first turn never got going
(``_send_now``). So nothing runs until you've read, and changed, what you'll
send; and a first turn that fails just tries the same copy again.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import wx

from .. import hub, platform_paths, rewind
from ..claude_cli import (DEFAULT_PERMISSION_MODE, ResumeRefused, build_fork_command,
                          new_session_id)
from ..own_store import OwnSession
from ..sessions import SessionInfo
from ..transcript import ASSISTANT, USER, ChatMessage
from .dialogs import RewindDialog

#: The kinds of message Branch from Here cuts after: yours, and Claude's
#: replies. A tool call or its result would leave the copy's history ending
#: half way through a step, which Claude Code can refuse to send.
BRANCHABLE = frozenset({USER, ASSISTANT})

unstarted_line = rewind.unstarted_line


class RewindChats:
    """Mixed into MainFrame."""

    # -- shared checks -------------------------------------------------------

    def _rewind_source(self, verb: str):
        """(info, message) for Rewind to Here or Branch from Here, or None
        once it's said why not."""
        info = self._open
        if info is None:
            self._feedback(f"Load a chat first, then choose a message to {verb} from.")
            return None
        if info.provider == "claude" and info.cowork and info.source != "kept":
            # Said instead of the general "isn't available for Claude chats".
            self._feedback(f"{info.title} is a Cowork session: its history is in the "
                           "desktop app's own files, so it can't be rewound or branched "
                           "here. Open it in Claude.")
            return None
        if not self._require_capability("branch", info):
            return None
        own = self.store.get(info.cli_session_id) if info.is_own else None
        if own is not None and not own.started and self._reader is None:
            # A branch not sent to yet, say: nothing of its own to go back to.
            self._feedback(f"{info.title} has no messages yet.")
            return None
        if not self._chat_loaded or self._reader is None:
            self._feedback(f"{info.title} is still loading.")
            return None
        return info, self._selected_message()

    def _own_settings(self, info: SessionInfo):
        """The source's model, effort and permission mode when it's one of
        ours, so a branch carries on as it did; Claude Code's own defaults
        otherwise, as Continue Here starts. Rules allowed for the chat
        aren't carried: a copy starts with nothing allowed beyond its mode
        (its Chat Options, More folders included, are, in _make_branch)."""
        own = self.store.get(info.cli_session_id) if info.is_own else None
        if own is None:
            return DEFAULT_PERMISSION_MODE, "", ""
        return own.permission_mode, own.model, own.effort

    def _make_branch(self, info: SessionInfo, cut: str, title: str, text: str) -> Optional[OwnSession]:
        """The new, unstarted chat whose history is ``info``'s up to and
        including the record ``cut`` ("": none), opened with ``text`` in its
        reply box. None once it's said why it couldn't be made."""
        if not info.cwd or not os.path.isdir(info.cwd):
            wx.MessageBox(f"This chat's folder ({info.cwd or 'unknown'}) doesn't exist any "
                          "more, so a new chat can't be started in it.", "The Chat Place",
                          wx.OK | wx.ICON_WARNING, self)
            return None
        mode, model, effort = self._own_settings(info)
        new_id = new_session_id()
        if cut:
            # The same refusals the first turn will meet, said now rather
            # than when you press Send: a desktop app id, a Cowork session.
            try:
                build_fork_command("claude", info.cli_session_id, new_id, title, mode, model,
                                   taken_ids={s.cli_session_id for s in self.store.all()}
                                   | set(self._snapshot.desktop_cli_ids),
                                   cowork_ids=self._snapshot.cowork_cli_ids, effort=effort,
                                   resume_at=cut)
            except (ResumeRefused, ValueError) as exc:
                wx.MessageBox(str(exc), "The Chat Place", wx.OK | wx.ICON_WARNING, self)
                return None
        now = int(time.time() * 1000)
        own = OwnSession(cli_session_id=new_id, title=title, cwd=info.cwd,
                         permission_mode=mode, started=False, model=model, effort=effort,
                         # With nothing to keep (your first message), it's a
                         # new session in the same folder, not a copy.
                         fork_source=info.cli_session_id if cut else "", fork_at=cut,
                         forked_from=info.title, created_ms=now, last_activity_ms=now)
        source = self.store.get(info.cli_session_id) if info.is_own else None
        if source is not None:
            # Chat Options (#468) carry over too: the copy inherits the
            # system prompt Claude Code recorded for the source, options and
            # all, so the same options keep later turns saying the same.
            own.agent, own.instructions = source.agent, source.instructions
            own.output_style, own.prompt_fresh = source.output_style, source.prompt_fresh
            own.disabled_mcp = list(source.disabled_mcp)
            # Chat Options' More folders are the chat's kept folders.
            own.allowed_dirs = list(source.allowed_dirs)
            own.allowed_dirs_at = dict(source.allowed_dirs_at)
        if not self._store_write(self.store.add, own):
            return None
        if info.key in self.tool_activity:
            try:
                self.tool_activity.add(own.to_info().key)
            except OSError:
                pass
        if text:
            self._drafts[new_id] = text
        self.open_session(own.to_info())
        self.refresh_sessions()
        # Where you'll type: what you said is there to change, or nothing yet.
        self.reply_text.SetFocus()
        self.reply_text.SetInsertionPointEnd()
        return own

    # -- Rewind to Here --------------------------------------------------------

    def on_rewind_to_here(self):
        """File, Rewind to Here (Ctrl+Shift+Z), and the messages list's menu:
        back to just before one of your messages."""
        found = self._rewind_source("rewind")
        if found is None:
            return
        info, message = found
        if not rewind.rewindable(message):
            self._feedback("Choose one of your own messages in the messages list, then "
                           "Rewind to Here.")
            return
        plan = rewind.plan_rewind(info.title, self._reader.transcript, message,
                                  can_rewind_files=self._can_rewind_files(info),
                                  files_reason=self._files_reason(info))
        focus = wx.Window.FindFocus()
        dialog = RewindDialog(self, plan)
        try:
            chosen = dialog.ShowModal() == wx.ID_OK and dialog.choice
            choice = dialog.choice
        finally:
            dialog.Destroy()
        if not chosen:
            # Back where Ctrl+Shift+Z was pressed: the message, or the reply box.
            (focus if focus else self.chat_list).SetFocus()
            return
        file_count = len({os.path.normcase(e.path) for e in plan.edits})
        if choice == rewind.FILES:
            self._start_rewind_files(info, message, file_count)
            return
        # The new chat first: if it can't be made, the files stay as they are.
        text, pictures = rewind.draft_text(message)
        own = self._make_branch(info, plan.cut, rewind.rewound_title(info.title), text)
        if own is None:
            return
        said = (f"Rewound to before your message in {own.title}. Your message is in the "
                "reply box to change and send.")
        if pictures:
            said += " Attach its pictures again before sending."
        if choice == rewind.BOTH and self._start_rewind_files(info, message, file_count,
                                                              quiet=True):
            said += " Putting the files back."
        self._feedback(said)

    def _can_rewind_files(self, info: SessionInfo) -> bool:
        try:
            supported = self.sessions.capabilities(info).rewind_files
        except ValueError:
            supported = False
        return (supported and info.cli_session_id not in self._runners
                and info.cli_session_id not in self._rewinding_files)

    def _files_reason(self, info: SessionInfo) -> str:
        if info.cli_session_id in self._runners:
            return ("Claude is working in this chat. Stop it, or wait for it to finish, before "
                    "putting its files back.")
        if info.cli_session_id in self._rewinding_files:
            return "The files in this chat are already being put back."
        if info.is_terminal:
            where = "a terminal session"
        elif info.is_own:
            return ""
        else:
            where = "a Claude desktop app session"
        return (f"This is {where}. Only The Chat Place's own chats can have their files put "
                "back: doing it writes to the chat. Conversation only makes a new chat of "
                "your own from it.")

    @property
    def _rewinding_files(self) -> set:
        if not hasattr(self, "_rewinding_files_set"):
            self._rewinding_files_set = set()
        return self._rewinding_files_set

    def _start_rewind_files(self, info: SessionInfo, message: ChatMessage, file_count: int,
                            quiet: bool = False) -> bool:
        """Start putting the files back. True once started; False once it's
        said why not."""
        session_id = info.cli_session_id
        if session_id in self._runners or session_id in self._rewinding_files:
            # A turn may have started while the dialog was open (a hand-off).
            self._feedback(f"{info.title} is busy, so its files weren't put back. Try again "
                           "once it has finished.")
            return False
        live = hub.load_live_status().get(session_id)
        if live is not None and live.status == "busy":
            self._feedback("This chat is running somewhere else right now, so its files "
                           "weren't put back.")
            return False
        lookup = platform_paths.find_claude()
        if not lookup.path:
            wx.MessageBox(lookup.problem, "The Chat Place", wx.OK | wx.ICON_ERROR, self)
            return False
        own_ids = {s.cli_session_id for s in self.store.all()}
        desktop_ids = set(self._snapshot.desktop_cli_ids)
        self._rewinding_files.add(session_id)
        self._update_send_state()
        if not quiet:
            self._feedback(f"Putting the files in {info.title} back.")

        def work():
            return rewind.run_rewind_files(lookup.path, session_id, message, info.cwd,
                                           info.title, file_count, own_ids, desktop_ids)

        def done(future):
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - always say how it went
                result = rewind.RewindResult(False, f"Claude Code couldn't restore the files: "
                                                    f"{exc}.")
            wx.CallAfter(self._rewind_files_done, session_id, result)

        self._pool.submit(work).add_done_callback(done)
        return True

    def _rewind_files_done(self, session_id: str, result):
        if self._gone():
            return
        self._rewinding_files.discard(session_id)
        self._update_send_state()
        self._feedback(result.text)
        if not result.ok:
            wx.MessageBox(result.text, "Rewind to Here", wx.OK | wx.ICON_WARNING, self)

    # -- Branch from Here --------------------------------------------------------

    def on_branch_from_here(self):
        """File, Branch from Here, and the messages list's menu: a new chat
        whose history ends with the chosen message, to try another way."""
        found = self._rewind_source("branch")
        if found is None:
            return
        info, message = found
        if message is None or message.kind not in BRANCHABLE or not message.uuid:
            self._feedback("Choose one of your messages or one of Claude's replies in the "
                           "messages list, then Branch from Here.")
            return
        own = self._make_branch(info, message.uuid, rewind.branch_title(info.title), "")
        if own is not None:
            self._feedback(f"Branched {info.title} at \"{message.first_line(60)}\" into "
                           f"{own.title}. Type your message to carry on from there.")
