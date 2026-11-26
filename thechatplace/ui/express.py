"""Express (#737): the window's side of ``express.py``.

The Express row is the session list's first row while Settings (or View,
Express) has it on. Enter on it shows, in the messages list, one row per
chat the session list is showing: its name, provider and latest reply. In
that list Enter opens the chat, Shift+Enter reads its whole latest reply,
R replies to it by name and Escape goes back to the session list.

Nothing here knows a provider: rows are the snapshot's ``SessionInfo``s,
and a reply goes through the same sends as the reply box (``_send_claude``,
``_send_codex_owned``, ``_agent_send_from_box``), which take the chat they
send to rather than the open one. A Codex or Copilot chat that isn't ready
yet is got ready first, as Send Reply to Another Chat's are, with the
message waiting in ``_relay_waiting`` (so quitting warns about it, and a
failure puts it in that chat's reply box).
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List, Optional

import wx

from .. import express
from ..express import EXPRESS_KEY, ExpressRow
from ..sessions import (EXPRESS_FIELD_IDS, EXPRESS_FIELDS, DEFAULT_EXPRESS_FIELDS,
                        FIELD_ACTIVITY, RUNTIME_PROVIDERS, WORKING, SessionInfo,
                        clean_express_fields,
                        field_short_name)
from ..transcript import ASSISTANT, ChatMessage
from .a11y import refresh_list_item
from .dialogs import ExpressReplyDialog, SessionColumnsDialog

#: Not a chat's open generation (they count up from 0): a chat Express got
#: ready isn't taken for the open one's (``_prepare_codex_owned``).
_EXPRESS_PREPARING = -1


class ExpressView:
    """Mixed into ``MainFrame``; ``_express_init`` sets its state up."""

    def _express_init(self):
        #: The messages list is showing Express rather than a chat.
        self._express_open = False
        #: The chats in the list, in its order, and each row's chat.
        self._express_keys: List[str] = []
        self._express_rows = {}
        #: Each chat's latest reply while it wasn't working (express.keep_replies).
        self._express_memo = {}
        #: The selected row's text without its clock, so a tick doesn't make
        #: a screen reader read it again (as the session list does).
        self._express_steady = {}

    # -- the session list's row -----------------------------------------------

    def _express_listed(self, shown) -> List[SessionInfo]:
        """Every chat Express could show, newest first, Last reply held while
        it's working; the first ``express_count`` are the list."""
        held = express.keep_replies([s for s in shown if s.key != EXPRESS_KEY],
                                    self._express_memo)
        return express.chats(held, len(held))

    def _with_express_row(self, sessions):
        """``sessions`` with the Express row first, when Express is on."""
        if not self.speech.express_enabled:
            return list(sessions)
        rows = self._express_listed(sessions)[:self.speech.express_count]
        return [ExpressRow(express.summary(rows))] + [s for s in sessions if s.key != EXPRESS_KEY]

    # -- opening and leaving ----------------------------------------------------

    def open_express(self):
        """Enter on the Express row: the list in the messages list, on the
        newest chat. Coming back to it puts it in order again."""
        if not self.speech.express_enabled:
            self._feedback("Express is off. View, Express turns it on.")
            return
        if self._express_open:
            self._express_fill(frozen=False, select_first=True)
            self.chat_list.SetFocus()
            self._feedback(f"Back in {express.heading(len(self._express_keys))}.")
            return
        if self._open is not None:
            # Leaving a chat for Express, as for another chat: what's in its
            # reply box is kept, and a message waiting for it to get ready
            # goes in there too.
            self._save_draft()
            self._drop_waiting_relay(going_to=SimpleNamespace(key=EXPRESS_KEY))
            self.unload_session()
        self._express_open = True
        self._express_keys = []
        self._express_rows = {}
        self._express_steady = {}
        self.own_reply.Hide()
        self.desktop_note.SetValue(express.REPLY_NOTE)
        self.reply_claude_btn.Hide()
        self.continue_btn.Hide()
        self.desktop_reply.Show()
        self.desktop_reply.Layout()
        self.session_heading.set_text("Express: the latest reply from each chat. "
                                      "Enter opens a chat, R replies to it.")
        self.SetTitle(f"Express — {self._app_name()}")
        self.session_view.Layout()
        if EXPRESS_KEY in self._list_keys:
            row = self._list_keys.index(EXPRESS_KEY)
            if self.session_list.GetSelection() != row:
                self.session_list.SetSelection(row)
        self._express_fill(frozen=False, select_first=True)
        self.chat_list.SetFocus()

    @staticmethod
    def _app_name():
        from .main_frame import APP_NAME  # imported by now: it mixes this in
        return APP_NAME

    def _express_close(self, show_nothing=False):
        """Express leaves the messages list: a chat is being opened, or
        Express was turned off (``show_nothing``)."""
        if not self._express_open:
            return
        self._express_open = False
        self._express_keys = []
        self._express_rows = {}
        self._express_steady = {}
        if show_nothing:
            self.SetTitle(self._app_name())
            self._show_no_session()

    def _express_back_to_list(self):
        """Escape (or Backspace) in Express: the session list, on its row."""
        if EXPRESS_KEY in self._list_keys:
            row = self._list_keys.index(EXPRESS_KEY)
            if self.session_list.GetSelection() != row:
                self.session_list.SetSelection(row)
        self.focus_sessions()

    # -- the rows -----------------------------------------------------------------

    def _express_fill(self, frozen=None, select_first=False):
        """Bring the list up to date. ``frozen`` (by default, while it has
        focus): rows stay where they are and new chats go at the end, so the
        row you're on never moves; it's put in order when you come back."""
        if not self._express_open:
            return
        if frozen is None:
            frozen = wx.Window.FindFocus() is self.chat_list
        shown = self._in_current_view(list(self._snapshot.sessions))
        listed = self._express_listed(shown)
        rows = express.ordered(self._express_keys, listed[:self.speech.express_count],
                               listed, frozen)
        fields = self.speech.express_fields
        now = int(time.time() * 1000)
        keys = [r.key for r in rows]
        lines = [express.row_text(r, fields, now) for r in rows]
        steady_fields = [f for f in fields if f != FIELD_ACTIVITY]
        steady = {r.key: express.row_text(r, steady_fields, now) for r in rows}
        index = self.chat_list.GetSelection()
        selected = (self._express_keys[index]
                    if 0 <= index < len(self._express_keys) else None)
        if not keys and list(self.chat_list.GetStrings()) == [express.EMPTY_LINE]:
            pass  # still empty: setting it again would make it read again
        elif keys and keys == self._express_keys and not select_first:
            for i, (key, line) in enumerate(zip(keys, lines)):
                if self.chat_list.GetString(i) == line:
                    continue
                if key == selected and self._express_steady.get(key) == steady[key]:
                    continue  # only its age changed: don't make it read again
                self.chat_list.SetString(i, line)
        elif frozen and keys and self._express_keys and not select_first:
            # Vanished rows out from the bottom up, the rest rewritten where
            # changed, new ones added at the end (as the session list does).
            for i in range(len(self._express_keys) - 1, -1, -1):
                if self._express_keys[i] not in steady:
                    self.chat_list.Delete(i)
            kept = [k for k in self._express_keys if k in steady]
            for i, key in enumerate(kept):
                if self.chat_list.GetString(i) != lines[i] and not (
                        key == selected and self._express_steady.get(key) == steady[key]):
                    self.chat_list.SetString(i, lines[i])
            if len(keys) > len(kept):
                self.chat_list.Append(lines[len(kept):])
            if selected in keys:
                if self.chat_list.GetSelection() != keys.index(selected):
                    self.chat_list.SetSelection(keys.index(selected))
            else:
                self.chat_list.SetSelection(min(max(index, 0), len(keys) - 1))
        else:
            self.chat_list.Set(lines or [express.EMPTY_LINE])
            if select_first or selected not in keys:
                self.chat_list.SetSelection(0)
            else:
                self.chat_list.SetSelection(keys.index(selected))
        self._express_keys = keys
        self._express_rows = {r.key: r for r in rows}
        self._express_steady = steady
        # "Messages in", as a chat's list is named, so Alt+M still reaches it.
        label = f"Messages in {express.heading(len(keys))}"
        if (self.chat_list.HasFocus() and not select_first
                and self.messages_label.GetLabel() != self._messages_label_text(label)):
            # Said on leaving, not over the row you're on (_update_messages_label).
            self._pending_messages_label = label
        else:
            self._set_messages_label(label)
        self._update_status_session()
        refresh_list_item(self.chat_list)  # Linux: the row's whole text, for Orca

    def _express_item_text(self, index: int) -> Optional[str]:
        """What a screen reader reads for an Express row: the row with as
        much of the reply as Express keeps, when Settings reads messages
        whole; otherwise the row as shown."""
        if not self.speech.full_messages_in_list or not 0 <= index < len(self._express_keys):
            return None
        info = self._express_rows.get(self._express_keys[index])
        if info is None:
            return None
        return express.row_text(info, self.speech.express_fields, whole=True)

    def _express_selected(self) -> Optional[SessionInfo]:
        """The chat on the selected row, as the snapshot has it now."""
        index = self.chat_list.GetSelection()
        if not 0 <= index < len(self._express_keys):
            return None
        key = self._express_keys[index]
        return self._current_info(key) or self._express_rows.get(key)

    # -- what the keys do ---------------------------------------------------------

    def _express_key(self, event, key, ctrl) -> bool:
        """Express's keys in its list; True if the key was taken."""
        alt, shift = event.AltDown(), event.ShiftDown()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not ctrl and not alt:
            if shift:
                self.express_read_reply()
            else:
                self.express_open_chat()
            return True
        if key in (ord("R"), ord("r")) and not (ctrl or alt or shift):
            self.express_reply()
            return True
        if key in (wx.WXK_ESCAPE, wx.WXK_BACK) and not (ctrl or alt or shift):
            self._express_back_to_list()
            return True
        if key in (ord("C"), ord("c")) and ctrl and not alt and not shift:
            self._express_copy()
            return True
        return False

    def express_open_chat(self):
        """Enter: the chat, opened as Enter on it in the session list opens it."""
        info = self._express_selected()
        if info is None:
            self._feedback("No chat selected.")
            return
        self.open_session(info)

    def _express_copy(self):
        info = self._express_selected()
        if info is None or not self._express_reply_kept(info):
            self._feedback("No reply to copy." if info is not None else "No chat selected.")
            return
        # The whole reply, read from the chat off this thread.
        self._express_whole_reply(info, lambda text: self._feedback(
            "Latest reply copied." if self._copy_text(text) else "Couldn't open the clipboard."))

    def express_read_reply(self):
        """Shift+Enter: the chat's whole latest reply, as Read Full Message
        shows a message. The row is cut short; the whole one is read from
        the chat itself, off the window's thread."""
        info = self._express_selected()
        if info is None:
            self._feedback("No chat selected.")
            return
        if not self._express_reply_kept(info):
            self._feedback(f"{info.title} has no reply yet.")
            return

        def show(text):
            message = ChatMessage(kind=ASSISTANT, text=text,
                                  speaker=f"Latest reply in {info.title}")
            self._read_whole_message(message, cwd=info.cwd or "")
            if self._express_open:
                self.chat_list.SetFocus()
        self._express_whole_reply(info, show)

    def _express_reply_kept(self, info) -> str:
        """The reply ``info``'s row shows (held while it's working)."""
        row = self._express_rows.get(info.key)
        return row.last_reply if row is not None else info.last_reply

    def _express_whole_reply(self, info, then):
        """``then(text)`` with ``info``'s whole latest reply, or what Express
        kept of it when the chat can't say (an agent conversation, a read
        that failed), once it's read, if Express is still on the same row."""
        key = info.key
        kept = self._express_reply_kept(info)
        if info.state == WORKING:
            # Its row holds the reply from before this turn; the chat's own
            # latest is a step on the way. What you read is what the row says.
            then(kept)
            return

        def work():
            try:
                text = self.sessions.last_reply(info) or ""
            except Exception:  # noqa: BLE001 - what Express kept will do
                text = ""
            wx.CallAfter(done, text)

        def done(text):
            if self._gone() or not self._express_open:
                return
            selected = self._express_selected()
            if selected is None or selected.key != key:
                return  # you've moved on: not a dialog for another row
            then(text.strip() or kept)
        self._pool.submit(work)

    # -- replying -----------------------------------------------------------------

    def express_reply(self):
        """R: a reply box addressed to the selected chat by name. Send sends
        it as the reply box would, and you're back on the same row; a chat
        that can't be sent to says why, and offers Continue Here if it can."""
        info = self._express_selected()
        if info is None:
            self._feedback("No chat selected.")
            return
        if not self._can_edit(info):
            self._express_cannot_reply(info)
            return
        key = info.key
        dialog = ExpressReplyDialog(self, express.reply_title(info),
                                    lambda text: self._express_send(key, text),
                                    say=self._feedback)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        if self._express_open:
            self.chat_list.SetFocus()

    def _express_cannot_reply(self, info):
        note = self._read_only_note(info)
        try:
            can_continue = bool(self.sessions.capabilities(info).fork)
        except ValueError:
            can_continue = False
        if not can_continue:
            wx.MessageBox(note, express.reply_title(info), wx.OK | wx.ICON_INFORMATION, self)
            self.chat_list.SetFocus()
            return
        question = wx.MessageDialog(self, f"{note}\n\nContinue it here, as a copy you can reply to?",
                                    express.reply_title(info),
                                    wx.YES_NO | wx.NO_DEFAULT | wx.ICON_INFORMATION)
        question.SetYesNoLabels("&Continue Here...", "Cancel")
        try:
            answer = question.ShowModal()
        finally:
            question.Destroy()
        if answer == wx.ID_YES:
            self.on_continue_here(source=info)
        elif self._express_open:
            self.chat_list.SetFocus()

    def _express_send(self, key, text) -> bool:
        """Send ``text`` to the chat ``key`` through the reply box's own
        sends, with its checks. True once it's on its way (the dialog
        closes); False keeps it in the dialog, with why already said."""
        info = self._current_info(key)
        if info is None:
            self._feedback("That chat is no longer in the list, so nothing was sent.")
            return False
        if info.key in self._codex_deleting:
            self._feedback('This chat is being deleted. Wait for it to finish first.')
            return False
        if not self._can_edit(info):
            self._feedback(self._read_only_note(info))
            return False
        from ..agents import AGENTS
        if info.provider == AGENTS:
            return bool(self._agent_send_from_box(info, text))
        if info.provider in RUNTIME_PROVIDERS:
            return self._express_send_runtime(info, text)
        if info.provider != "claude" or not info.is_own:
            # Only Claude's runner is left, and another provider's ID must
            # never reach it (CLAUDE.md).
            self._feedback(f"The Chat Place can't send to {info.title} yet.")
            return False
        return bool(self._send_claude(info, text, []))

    def _express_send_runtime(self, info, text) -> bool:
        record = self.store.get(info.cli_session_id, provider=info.provider)
        if record is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's chats, so the "
                           "message wasn't sent.")
            return False
        if self._files_going_back_in(getattr(record, 'cwd', '')):
            self._feedback('Still putting files back in this folder. Send again in a moment.')
            return False
        try:
            ready = bool(self.sessions.owned_action("ready", record))
        except Exception:  # noqa: BLE001 - not known to be ready
            ready = False
        if ready and info.key not in self._codex_preparation_running:
            # On API credits, a refusal before anything goes (no key, a
            # limit, Cancel at "Send on API credits?") keeps the text here,
            # in the reply dialog, as a Claude chat's does.
            refusals = []
            self._send_codex_owned(record, text, refused=refusals.append)
            if refusals:
                self._feedback(refusals[0] or "Not sent.")
                return False
            return True
        if info.key in self._relay_waiting:
            self._feedback(f"A message is already waiting for {info.title} to get ready. "
                           "Send this one once it's gone.")
            return False
        # Got ready first, as a chat opened in the window is; the message
        # waits for it (_relay_prepared sends it, or puts it in the chat's
        # reply box if that fails).
        self._relay_waiting[info.key] = text
        self._codex_preparing.setdefault(info.key, _EXPRESS_PREPARING)
        self._prepare_runtime_chat(info)
        self._feedback(f"Getting {info.title} ready. The message goes as soon as it is.")
        return True

    # -- turning it on and off, and its columns -----------------------------------

    def on_express_toggle(self, _event=None):
        """View, Express: the row at the top of the session list, on or off."""
        item = self.menu_items.get("express_toggle")
        on = item.IsChecked() if item is not None else not self.speech.express_enabled
        self._express_set(on)

    def _express_set(self, on: bool, say: bool = True):
        was = self.speech.express_enabled
        self.speech.express_enabled = on
        self._express_sync_menu()
        if on == was:
            return
        try:
            self.speech.save()
        except OSError as exc:
            self._status(f"Couldn't save Express's setting: {exc}")
        if not on:
            self._express_close(show_nothing=True)
        self._refresh_list_in_place()
        if on:
            self.refresh_sessions()  # the chats' latest replies are read from now
        if say:
            self._feedback("Express is on: it's the first row in the session list."
                           if on else "Express is off.")

    def _express_sync_menu(self):
        item = getattr(self, "menu_items", {}).get("express_toggle")
        if item is not None and item.IsChecked() != self.speech.express_enabled:
            item.Check(self.speech.express_enabled)

    def _express_settings_changed(self, before):
        """Settings has saved: Express turned on or off, or its count changed."""
        on, count = self.speech.express_enabled, self.speech.express_count
        self._express_sync_menu()
        if before.express_enabled and not on:
            self._express_close(show_nothing=True)
        if (on, count) != (before.express_enabled, before.express_count):
            self._refresh_list_in_place()
            self._express_fill(frozen=False)

    def on_express_columns(self):
        """View, Session List Columns while Express is open: Express's own
        columns, with the same dialog."""
        sample = self._express_selected() or next(iter(self._express_rows.values()), None)
        dialog = SessionColumnsDialog(
            self, self.speech.express_fields, sample, self._feedback,
            columns=EXPRESS_FIELDS, defaults=DEFAULT_EXPRESS_FIELDS,
            clean=clean_express_fields, title="Express Columns",
            intro=("Each chat in Express is read as one line. Choose what it says, and in "
                   "what order."),
            preview_label="&Preview of the selected chat's line:",
            line=lambda info, fields: express.row_text(info, fields))
        try:
            if dialog.ShowModal() != wx.ID_OK:
                if self._express_open:
                    self.chat_list.SetFocus()
                return
            fields = dialog.fields()
        finally:
            dialog.Destroy()
        if self._express_open:
            self.chat_list.SetFocus()
        if fields == self.speech.express_fields:
            self._feedback("Express columns unchanged.")
            return
        self.speech.express_fields = [f for f in fields if f in EXPRESS_FIELD_IDS]
        self._express_fill(frozen=True)
        names = ", ".join(field_short_name(f, EXPRESS_FIELDS) for f in fields)
        try:
            self.speech.save()
        except OSError as exc:
            self._feedback(f"Express columns changed to {names}, but couldn't be saved, so "
                           f"they last until you close The Chat Place: {exc}")
            return
        self._feedback(f"Express columns saved. Each chat reads: {names}.")
