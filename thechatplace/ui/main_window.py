"""The window: chats, messages, reply box. It knows ``ChatService`` and the shared
contracts, and which AI a chat belongs to is only ever a name to show.

Background work runs on threads and comes back through ``wx.CallAfter``.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

import wx

from .. import ui_text
from ..contracts import (DECISION_LABELS, AskPermission, AskQuestion, Capability, ChatInfo, ChatRef, Decision,
                         IntegrationError, Notice, Refused, TurnEnded, TurnStatus)
from ..service import ChatService
from ..speak import Speaker
from . import a11y
from .dialogs import NewChatDialog, OptionsDialog, PermissionDialog, ProvidersDialog, QuestionDialog, TextDialog

REFRESH_SECONDS = 15
#: Streaming text updates the list this often, not on every piece.
STREAM_REFRESH_MS = 400


class MainWindow(wx.Frame):
    def __init__(self, service: ChatService, speaker: Optional[Speaker] = None, auto_refresh: bool = True):
        super().__init__(None, title="The Chat Place (experiment)", size=(1000, 720))
        # Send and Stop mustn't fall off a small screen.
        area = wx.Display(wx.Display.GetFromWindow(self) if wx.Display.GetFromWindow(self) >= 0 else 0).GetClientArea()
        self.SetSize(min(1000, area.width), min(720, area.height))
        self.CentreOnScreen()
        self.service = service
        self.speaker = speaker or Speaker()
        self.service.listener = self._from_service
        self.chats: List[ChatInfo] = []
        self.current: Optional[ChatInfo] = None
        self._closing = False
        self._dialog: Optional[wx.Dialog] = None
        self._stream_dirty = False
        self._refreshing = False
        self._refresh_again = False
        #: Text typed for each chat, so it can't be sent to another.
        self._drafts: Dict[str, str] = {}

        self._build_menu()
        self._build_body()
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self.refresh_timer = wx.Timer(self)
        self.stream_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda _event: self.refresh_chats(), self.refresh_timer)
        self.Bind(wx.EVT_TIMER, self._on_stream_tick, self.stream_timer)
        self.stream_timer.Start(STREAM_REFRESH_MS)
        if auto_refresh:
            self.refresh_timer.Start(REFRESH_SECONDS * 1000)
            self.refresh_chats()
        self.show_reply_for(None)
        self.chat_list.SetFocus()

    # -- building -------------------------------------------------------------

    def _build_menu(self) -> None:
        bar = wx.MenuBar()
        file_menu = wx.Menu()
        self.new_item = file_menu.Append(wx.ID_NEW, "&New Chat\tCtrl+N")
        self.options_item = file_menu.Append(wx.ID_ANY, "Chat &Options\tCtrl+Shift+O")
        self.stop_item = file_menu.Append(wx.ID_ANY, "&Stop Turn\tCtrl+.")
        self.details_item = file_menu.Append(wx.ID_ANY, "Turn &Details\tCtrl+Shift+D")
        self.refresh_item = file_menu.Append(wx.ID_REFRESH, "&Refresh\tF5")
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, "E&xit")
        bar.Append(file_menu, "&File")
        help_menu = wx.Menu()
        providers_item = help_menu.Append(wx.ID_ANY, "AI &Providers…")
        shortcuts_item = help_menu.Append(wx.ID_ANY, "&Keyboard Shortcuts")
        bar.Append(help_menu, "&Help")
        self.SetMenuBar(bar)
        self.Bind(wx.EVT_MENU, lambda _e: self.new_chat(), self.new_item)
        self.Bind(wx.EVT_MENU, lambda _e: self.chat_options(), self.options_item)
        self.Bind(wx.EVT_MENU, lambda _e: self.stop_turn(), self.stop_item)
        self.Bind(wx.EVT_MENU, lambda _e: self.turn_details(), self.details_item)
        self.Bind(wx.EVT_MENU, lambda _e: self.refresh_chats(), self.refresh_item)
        self.Bind(wx.EVT_MENU, lambda _e: self.Close(), exit_item)
        self.Bind(wx.EVT_MENU, lambda _e: self.show_providers(), providers_item)
        self.Bind(wx.EVT_MENU, lambda _e: self._show_modal(TextDialog(self, "Keyboard Shortcuts",
                                                                      ui_text.shortcuts_text())), shortcuts_item)

    def _build_body(self) -> None:
        panel = wx.Panel(self)
        self.panel = panel
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label="&Chats:"), 0, wx.LEFT | wx.TOP, 8)
        self.chat_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        sizer.Add(self.chat_list, 2, wx.EXPAND | wx.ALL, 8)
        sizer.Add(wx.StaticText(panel, label="&Messages:"), 0, wx.LEFT, 8)
        self.message_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        sizer.Add(self.message_list, 3, wx.EXPAND | wx.ALL, 8)
        self.reply_label = wx.StaticText(panel, label="&Reply:")
        sizer.Add(self.reply_label, 0, wx.LEFT, 8)
        self.reply = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        sizer.Add(self.reply, 1, wx.EXPAND | wx.ALL, 8)
        self.note = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        sizer.Add(self.note, 1, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.send_button = wx.Button(panel, label="&Send")
        self.stop_button = wx.Button(panel, label="S&top")
        row.Add(self.send_button, 0, wx.ALL, 4)
        row.Add(self.stop_button, 0, wx.ALL, 4)
        sizer.Add(row, 0, wx.LEFT | wx.BOTTOM, 4)
        panel.SetSizer(sizer)
        self.CreateStatusBar()
        a11y.set_accessible_name(self.reply, "Reply")
        a11y.set_accessible_name(self.note, "About this chat")
        a11y.set_list_items_accessible(self.chat_list, lambda: "Chats", self._chat_item_text)
        a11y.set_list_items_accessible(self.message_list, self._messages_name, self._message_item_text)
        self.send_button.Bind(wx.EVT_BUTTON, lambda _e: self.send())
        self.stop_button.Bind(wx.EVT_BUTTON, lambda _e: self.stop_turn())

    def _messages_name(self) -> str:
        return f"Messages in {self.current.title}" if self.current else "Messages"

    def _chat_item_text(self, index: int) -> Optional[str]:
        return self.chat_list.GetString(index) if 0 <= index < self.chat_list.GetCount() else None

    def _message_item_text(self, index: int) -> Optional[str]:
        if self.current is None:
            return None
        messages = self.service.messages(self.current.ref)
        if 0 <= index < len(messages):
            return ui_text.message_spoken(messages[index], self.current.provider)
        return None

    # -- speech and status ----------------------------------------------------

    def say(self, text: str, interrupt: bool = False) -> None:
        if self._closing:
            return
        self.SetStatusText(" ".join(text.split())[:200])
        self.speaker.say(text, interrupt)

    def _after(self, function, *args) -> None:
        wx.CallAfter(self._guarded, function, *args)

    def _guarded(self, function, *args) -> None:
        if not self._closing and self:
            function(*args)

    # -- the chat list --------------------------------------------------------

    def refresh_chats(self) -> None:
        """One at a time: a slow one must finish, not be overtaken and thrown away."""
        if self._refreshing:
            self._refresh_again = True
            return
        self._refreshing = True
        current = self.current

        def work() -> None:
            # The list first: reading one chat can take far longer.
            try:
                chats, problems = self.service.list_chats()
                self._after(self._show_chats, chats, problems)
            except Exception:  # noqa: BLE001 - the next refresh tries again
                pass
            if current is not None and not current.owned:
                try:
                    self.service.load_history(current.ref)  # it may have moved on
                    self._after(self._show_if_current, current.ref)
                except Exception:  # noqa: BLE001 - what's showing stays until a read works
                    pass
            self._after(self._refresh_done)
        threading.Thread(target=work, daemon=True, name="list-chats").start()

    def _show_if_current(self, ref: ChatRef) -> None:
        if self.current is not None and self.current.ref == ref:
            self.show_messages()

    def _refresh_done(self) -> None:
        self._refreshing = False
        if self._refresh_again:
            self._refresh_again = False
            self.refresh_chats()

    def _row_for(self, info: ChatInfo) -> str:
        state = self.service.state(info.ref)
        needs = bool(state.pending_permission or state.pending_question)
        return ui_text.chat_row(info, state.busy, needs)

    def _show_chats(self, chats: List[ChatInfo], problems: List[str]) -> None:
        old_keys = [c.ref.key for c in self.chats]
        new_keys = [c.ref.key for c in chats]
        if self.chat_list.HasFocus() and sorted(old_keys) == sorted(new_keys) and old_keys != new_keys:
            # Rows don't move under someone arrowing through them; the order
            # catches up when they leave the list, or with F5.
            chats = sorted(chats, key=lambda c: old_keys.index(c.ref.key))
            new_keys = old_keys
        selected = self._selected_chat()
        keep = selected.ref.key if selected else (self.current.ref.key if self.current else None)
        self.chats = chats
        rows = [self._row_for(c) for c in chats]
        if old_keys == new_keys:
            for index, row in enumerate(rows):
                if self.chat_list.GetString(index) != row:
                    self.chat_list.SetString(index, row)
        else:
            self.chat_list.Set(rows)
            if keep in new_keys:
                self.chat_list.SetSelection(new_keys.index(keep))
            elif rows:
                self.chat_list.SetSelection(0)
        if problems:
            self.SetStatusText(f"Couldn't list chats from: {', '.join(problems)}")

    def _selected_chat(self) -> Optional[ChatInfo]:
        index = self.chat_list.GetSelection()
        return self.chats[index] if index != wx.NOT_FOUND and index < len(self.chats) else None

    def _update_row(self, ref: ChatRef) -> None:
        for index, info in enumerate(self.chats):
            if info.ref == ref:
                row = self._row_for(info)
                if self.chat_list.GetString(index) != row:
                    self.chat_list.SetString(index, row)
                return

    def _add_chat(self, info: ChatInfo) -> None:
        self.chats.insert(0, info)
        self.chat_list.Insert(self._row_for(info), 0)
        self.chat_list.SetSelection(0)

    # -- opening a chat -------------------------------------------------------

    def open_selected(self) -> None:
        info = self._selected_chat()
        if info is not None:
            self.open_chat(info)

    def open_chat(self, info: ChatInfo, focus_messages: bool = True) -> None:
        if self.current is not None:
            self._drafts[self.current.ref.key] = self.reply.GetValue()
        self.current = info
        self.reply.SetValue(self._drafts.get(info.ref.key, ""))
        self.message_list.Clear()
        self.show_reply_for(info)
        if focus_messages:
            self.message_list.SetFocus()
        self.say(f"Opening {info.title}.")

        def work() -> None:
            try:
                self.service.load_history(info.ref)
                problem = ""
            except (IntegrationError, OSError, ValueError):
                problem = f"{info.provider} couldn't read this chat's messages."
            self._after(self._chat_loaded, info, problem)
        threading.Thread(target=work, daemon=True, name="load-chat").start()

    def _chat_loaded(self, info: ChatInfo, problem: str) -> None:
        if self.current is None or self.current.ref != info.ref:
            return
        self.show_messages()
        count = self.message_list.GetCount()
        self.say(problem or f"{info.title}: {count} messages.")
        self._ask_if_waiting(info.ref)

    def show_reply_for(self, info: Optional[ChatInfo]) -> None:
        can_send = bool(info and Capability.SEND in info.capabilities)
        self.reply.Show(can_send)
        self.reply_label.Show(can_send)
        self.note.Show(not can_send)
        self.send_button.Enable(can_send)
        self.stop_button.Enable(can_send)
        self.options_item.Enable(bool(info and Capability.OPTIONS in info.capabilities))
        self.details_item.Enable(bool(info and (Capability.USAGE in info.capabilities
                                                or Capability.PLANS in info.capabilities)))
        self.note.SetValue(info.read_only_note or "This chat can't be replied to from here." if info
                           else "Open a chat from the list, or start one with Ctrl+N.")
        self.panel.Layout()

    def show_messages(self) -> None:
        if self.current is None:
            return
        messages = self.service.messages(self.current.ref)
        rows = [ui_text.message_row(m, self.current.provider) for m in messages]
        selection = self.message_list.GetSelection()
        was_last = selection == wx.NOT_FOUND or selection == self.message_list.GetCount() - 1
        existing = self.message_list.GetCount()
        if existing > len(rows):
            self.message_list.Set(rows)
        else:
            for index in range(existing):
                if self.message_list.GetString(index) != rows[index]:
                    self.message_list.SetString(index, rows[index])
            if len(rows) > existing:
                self.message_list.Append(rows[existing:])
        if rows and was_last:
            self.message_list.SetSelection(len(rows) - 1)

    def read_selected_message(self) -> None:
        index = self.message_list.GetSelection()
        if self.current is None or index == wx.NOT_FOUND:
            return
        messages = self.service.messages(self.current.ref)
        if index < len(messages):
            self._show_modal(TextDialog(self, ui_text.author(messages[index], self.current.provider), messages[index].text))

    def copy_selected_message(self) -> None:
        index = self.message_list.GetSelection()
        if self.current is None or index == wx.NOT_FOUND:
            return
        messages = self.service.messages(self.current.ref)
        if index < len(messages) and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(messages[index].text))
            wx.TheClipboard.Close()
            self.say("Copied.", interrupt=True)

    # -- sending --------------------------------------------------------------

    def send(self) -> None:
        text = self.reply.GetValue().strip()
        if self.current is None or not text:
            return
        try:
            outcome = self.service.send(self.current.ref, text)
        except Refused as refusal:
            self.say(str(refusal), interrupt=True)
            return
        self.reply.SetValue("")
        self.say("Your message will be sent when this turn ends." if outcome == "queued" else "Sent.", interrupt=True)
        self._update_row(self.current.ref)
        self.show_messages()

    def stop_turn(self) -> None:
        if self.current is None or not self.service.state(self.current.ref).busy:
            self.say("Nothing to stop.", interrupt=True)
            return
        queued = self.service.stop(self.current.ref)
        self.say("Stopping.", interrupt=True)
        if queued:
            existing = self.reply.GetValue()
            self.reply.SetValue("\n\n".join(([existing] if existing.strip() else []) + queued))
        self._end_dialog(self.current.ref)

    # -- what the service reports ---------------------------------------------

    def _from_service(self, ref: ChatRef, event: object) -> None:
        self._after(self._on_service_event, ref, event)

    def _on_service_event(self, ref: ChatRef, event: object) -> None:
        is_current = self.current is not None and self.current.ref == ref
        if isinstance(event, TurnEnded):
            self._update_row(ref)
            self._end_dialog(ref)
            if is_current:
                self.show_messages()
            self._announce_end(ref, event)
        elif isinstance(event, (AskPermission, AskQuestion)):
            self._update_row(ref)
            # Said even for this chat: a dialog may be open, hiding the request
            # until it closes.
            if not is_current or self._dialog is not None:
                self.say(f"{self._title_of(ref)} needs you.")
            if is_current:
                self._ask_if_waiting(ref)
        elif isinstance(event, Notice):
            if is_current:
                self.show_messages()
            self.say(event.text)
        else:
            self._stream_dirty = self._stream_dirty or is_current
            if event == "changed":
                self._update_row(ref)

    def _on_stream_tick(self, _event) -> None:
        if self._stream_dirty and not self._closing:
            self._stream_dirty = False
            self.show_messages()
            if self.current is not None:
                activity = self.service.state(self.current.ref).activity
                if activity:
                    self.SetStatusText(activity[:200])

    def _title_of(self, ref: ChatRef) -> str:
        return next((c.title for c in self.chats if c.ref == ref), "A chat")

    def _info_of(self, ref: ChatRef) -> Optional[ChatInfo]:
        return next((c for c in self.chats if c.ref == ref), None) or self.service.info(ref)

    def _announce_end(self, ref: ChatRef, event: TurnEnded) -> None:
        info = self._info_of(ref)
        provider = info.provider if info else "The AI"
        title = info.title if info else "a chat"
        if event.status is TurnStatus.STOPPED:
            self.say(event.message or f"Stopped in {title}.")
        elif event.status is TurnStatus.FAILED:
            self.say(event.message or f"{provider} couldn't finish in {title}.")
        else:
            self.say(ui_text.reply_announcement(provider, title, self.service.state(ref).last_reply))

    # -- questions and permissions --------------------------------------------

    def _ask_if_waiting(self, ref: ChatRef) -> None:
        """Ask now if nothing else is open; wx.CallAfter so the event finishes first."""
        if self._dialog is None:
            wx.CallAfter(self._guarded, self._ask, ref)

    def _ask(self, ref: ChatRef) -> None:
        if self._dialog is not None or self.current is None or self.current.ref != ref:
            return
        state = self.service.state(ref)
        title = self.current.title
        if state.pending_permission is not None:
            request = state.pending_permission
            dialog = PermissionDialog(self, title, request)
            dialog.chat_ref = ref
            decision = Decision.DENY
            ended = False

            def read(shown: PermissionDialog) -> None:
                nonlocal decision
                decision = shown.decision
            ended = self._show_modal(dialog, read) == wx.ID_CANCEL
            if not ended and request in state.pending:
                # Off this thread: an answer can wait on a busy program.
                self._in_background(self.service.answer_permission, ref, request, decision)
                self._update_row(ref)
                self.say(f"{DECISION_LABELS[decision]}: done.", interrupt=True)
        elif state.pending_question is not None:
            request = state.pending_question
            dialog = QuestionDialog(self, title, request)
            dialog.chat_ref = ref
            answers = None

            def read_answers(shown: QuestionDialog) -> None:
                nonlocal answers
                answers = shown.answers
            self._show_modal(dialog, read_answers)
            if request in state.pending:
                self._in_background(self.service.answer_question, ref, request, answers)
                self._update_row(ref)

    def _in_background(self, function, *args) -> None:
        threading.Thread(target=function, args=args, daemon=True, name="answer").start()

    def _show_modal(self, dialog: wx.Dialog, read=None) -> int:
        """Show it, read it, then ask anything that came in behind it. Every
        modal comes through here: a request that arrives while one's open would
        otherwise never be asked."""
        self._dialog = dialog
        try:
            result = dialog.ShowModal()
            if read is not None:
                read(dialog)
            return result
        finally:
            self._dialog = None
            dialog.Destroy()
            if self.current is not None and not self._closing:
                self._ask_if_waiting(self.current.ref)

    def _end_dialog(self, ref: ChatRef) -> None:
        """A request that's been overtaken (its own chat's turn ended) can't be
        answered. Another chat's ending leaves it open."""
        dialog = self._dialog
        if (isinstance(dialog, (PermissionDialog, QuestionDialog)) and dialog.IsModal()
                and getattr(dialog, "chat_ref", None) == ref):
            dialog.EndModal(wx.ID_CANCEL)

    # -- other dialogs --------------------------------------------------------

    def new_chat(self) -> None:
        providers = [(i.id, i.name) for i in self.service.enabled_integrations()]
        folder = self.current.folder if self.current and self.current.folder else str(Path.home())
        dialog = NewChatDialog(self, providers, folder, self.service.new_chat_options)
        chosen = {}

        def read(shown: NewChatDialog) -> None:
            chosen.update(provider=shown.provider_id(), title=shown.title_box.GetValue().strip(),
                          folder=shown.folder.GetValue().strip(), options=shown.options())
        if self._show_modal(dialog, read) != wx.ID_OK:
            return
        provider, title, folder, options = chosen["provider"], chosen["title"], chosen["folder"], chosen["options"]
        self.say("Starting the chat.")

        def work() -> None:
            try:
                info = self.service.start_chat(provider, folder, title, options)
            except (IntegrationError, OSError) as error:
                message = str(error) if isinstance(error, IntegrationError) else "The chat couldn't be started."
                self._after(self.say, message, True)
                return
            self._after(self._chat_started, info)
        threading.Thread(target=work, daemon=True, name="start-chat").start()

    def _chat_started(self, info: ChatInfo) -> None:
        self._add_chat(info)
        if self._dialog is not None:
            # Something's being asked: the chat behind it mustn't change.
            self.say(f"{info.title} is ready in the list.")
            return
        self.open_chat(info, focus_messages=False)
        self.reply.SetFocus()

    def chat_options(self) -> None:
        if self.current is None or Capability.OPTIONS not in self.current.capabilities:
            return
        ref, title = self.current.ref, self.current.title
        self.say("Getting this chat's options.")

        def work() -> None:
            try:
                groups = self.service.chat_options(ref)
            except (IntegrationError, OSError):
                groups = ()
            self._after(self._show_chat_options, ref, title, groups)
        threading.Thread(target=work, daemon=True, name="get-options").start()

    def _show_chat_options(self, ref: ChatRef, title: str, groups) -> None:
        if self._dialog is not None:
            return
        if not groups:
            self.say("This chat has no options to change.", interrupt=True)
            return
        chosen = {}
        if self._show_modal(OptionsDialog(self, title, groups), lambda shown: chosen.update(shown.controls.values())) != wx.ID_OK:
            return
        changes = [(g.id, chosen[g.id]) for g in groups if g.id in chosen and chosen[g.id] != g.current]

        def work() -> None:
            try:
                for group, value in changes:
                    self.service.set_option(ref, group, value)
                if not changes:
                    words = "No changes."
                elif self.service.state(ref).busy:
                    words = "Options changed. A turn already running may keep the old ones until your next message."
                else:
                    words = "Options changed."
                self._after(self.say, words, True)
            except (IntegrationError, OSError) as error:
                message = str(error) if isinstance(error, IntegrationError) else "That option couldn't be changed."
                self._after(self.say, message, True)
        threading.Thread(target=work, daemon=True, name="set-options").start()

    def turn_details(self) -> None:
        if self.current is None:
            return
        state = self.service.state(self.current.ref)
        self._show_modal(TextDialog(self, f"Turn details: {self.current.title}",
                                    ui_text.turn_details(state.activity, state.plan, state.usage)))

    def show_providers(self) -> None:
        integrations = self.service.integrations
        rows = [(i.id, i.name) for i in integrations.values()]

        def is_on(provider: str) -> bool:
            return self.service.store.enabled(provider, integrations[provider].enabled_by_default)

        def details(provider: str) -> str:
            status = self.service.status(provider)
            lines = [status.summary]
            if status.hint:
                lines.append(f"If it doesn't work: {status.hint}")
            if status.billing:
                lines.append(f"Who pays: {status.billing}")
            lines.append("This version has been checked with The Chat Place." if status.verified
                         else "This version hasn't been checked with The Chat Place.")
            report = self.service.drift_report().get(provider, {})
            lines.append("")
            lines.append(ui_text.drift_lines(integrations[provider].name, report))
            return "\n".join(lines)

        def set_on(provider: str, value: bool) -> None:
            self.service.set_enabled(provider, value)

        self._show_modal(ProvidersDialog(self, rows, is_on, set_on, details))
        self.refresh_chats()

    # -- keys -----------------------------------------------------------------

    def _parts(self) -> List[wx.Window]:
        return [self.chat_list, self.message_list, self.reply if self.reply.IsShown() else self.note]

    def _move_part(self, step: int) -> None:
        parts = self._parts()
        focus = wx.Window.FindFocus()
        index = parts.index(focus) if focus in parts else -1
        parts[(index + step) % len(parts)].SetFocus()

    def _on_key(self, event: wx.KeyEvent) -> None:
        key, focus = event.GetKeyCode(), wx.Window.FindFocus()
        ctrl = event.ControlDown()
        if key == wx.WXK_F6:
            self._move_part(-1 if event.ShiftDown() else 1)
        elif ctrl and key in (ord("1"), ord("2"), ord("3")):
            self._parts()[key - ord("1")].SetFocus()
        elif key == wx.WXK_ESCAPE and focus in (self.message_list, self.reply, self.note):
            self.chat_list.SetFocus()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and focus is self.chat_list:
            self.open_selected()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and focus is self.message_list:
            self.read_selected_message()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and ctrl and focus is self.reply:
            self.send()
        elif ctrl and key == ord("C") and focus is self.message_list:
            self.copy_selected_message()
        else:
            event.Skip()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self._shut_down()
        event.Skip()

    def _on_destroy(self, event: wx.WindowDestroyEvent) -> None:
        # Destroy skips the close event, and a timer outliving its window
        # crashes on a Mac. Only this window's own destruction counts: a child's
        # event arrives here too.
        if event.GetEventObject() is self:
            self._shut_down()
        event.Skip()

    def _shut_down(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.refresh_timer.Stop()
        self.stream_timer.Stop()
        closing = threading.Thread(target=self.service.close, daemon=True, name="close-service")
        closing.start()
        # Long enough for running turns to be stopped before the app goes. On a
        # Mac or Linux nothing else ends them; a stuck one can't hold the app.
        closing.join(2.0)
