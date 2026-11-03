"""Reading a subagent's conversation (#468): what Claude asked it, what it
did and what it said, in a messages list that behaves like the main one.

Each row is who spoke and the first line ("Claude: Review the branch…",
"code-reviewer: Found two problems…"), and on Windows a screen reader reads
the whole message on arriving at it, as in the main messages list (#11).
Enter reads a message in full, Ctrl+C copies it, the Applications key
offers both, and Ctrl+T shows or hides tool activity. While the subagent is
still working, or sent on again later, its new messages arrive at the end
without moving you, and its ending is said. It's read-only: a subagent's transcript is Claude
Code's, and never written to. Escape closes it, back where you were.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import wx

from .. import announce
from ..subagents import RUNNING, STATUS_WORDS, Subagent
from ..transcript import ChatMessage, TranscriptReader
from ..ui_text import key_name
from .a11y import (refresh_list_item, set_accessible_name, set_list_items_accessible,
                   set_voiceover_menu)
from .dialogs import press_focused_button_on_a_mac
from .sizes import dialog_size

#: How often a running subagent's conversation is read again.
REFRESH_MS = 1000
#: And a finished one's: Claude can send it on with SendMessage at any time,
#: and its conversation should show that without reopening it.
FINISHED_REFRESH_MS = 5000


class SubagentChatDialog(wx.Dialog):
    """``reader`` has the conversation (already read once). ``read_message``
    shows one message in full over this dialog, ``copy(text) -> bool``
    copies, ``feedback`` speaks. ``latest()`` gives the subagent as last
    read by the window (None if it's gone), so a running one's ending is
    noticed; ``whole_in_list`` is Settings' "read whole messages in the
    list"."""

    def __init__(self, parent, subagent: Subagent, chat_title: str, reader: TranscriptReader,
                 read_message: Callable[[ChatMessage, wx.Window], None],
                 copy: Callable[[str], bool], feedback: Callable[[str], None],
                 latest: Optional[Callable[[], Optional[Subagent]]] = None,
                 show_activity: bool = False, whole_in_list: bool = True):
        super().__init__(parent, title=f"Subagent {subagent.name}, in {chat_title}",
                         size=dialog_size(parent, 760, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.subagent = subagent
        self.reader = reader
        self._read_message = read_message
        self._copy = copy
        self.feedback = feedback
        self._latest = latest
        self.show_activity = show_activity
        self._whole_in_list = whole_in_list
        self._keys: List[str] = []
        self._shown: List[ChatMessage] = []
        self._spoken = {}  # key -> ((text), words)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Messages:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, self._list_name())
        set_list_items_accessible(self.list, self.list.GetName, self._item_text)
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.activity = wx.CheckBox(self, label=key_name("Show &tool activity (Ctrl+T)"))
        self.activity.SetValue(show_activity)
        set_accessible_name(self.activity, key_name("Show tool activity (Ctrl+T)"))
        row.Add(self.activity, 1, wx.ALIGN_CENTER_VERTICAL)
        row.Add(wx.Button(self, wx.ID_CANCEL, "&Close"), 0, wx.LEFT, 8)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.activity.Bind(wx.EVT_CHECKBOX, lambda e: self.set_activity(self.activity.GetValue()))
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.read_selected())
        self.list.Bind(wx.EVT_CONTEXT_MENU, lambda e: self._show_menu())
        set_voiceover_menu(self.list, self._show_menu)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.refresh(), self.timer)
        # Not a method: by this event the window itself has gone, so only the
        # timer can be touched (when its parent takes it down without Destroy).
        timer = self.timer

        def stop_timer(event):
            timer.Stop()
            event.Skip()
        self.Bind(wx.EVT_WINDOW_DESTROY, stop_timer)
        self._fill(newest=True)
        self.timer.Start(REFRESH_MS if subagent.status == RUNNING else FINISHED_REFRESH_MS)
        wx.CallAfter(lambda: self and self.list.SetFocus())

    # -- the list ------------------------------------------------------------

    def _list_name(self) -> str:
        """"code-reviewer's conversation, running": heard on arriving."""
        return f"{self.subagent.name}'s conversation, {STATUS_WORDS[self.subagent.status].lower()}"

    def visible(self) -> List[ChatMessage]:
        messages = self.reader.transcript.messages
        return list(messages) if self.show_activity else [m for m in messages if not m.is_activity]

    def _fill(self, newest: bool = False):
        """Bring the rows up to date without moving you: rows change in
        place and new ones are added at the end."""
        shown = self.visible()
        keys = [m.key for m in shown]
        lines = [m.list_line() for m in shown] or ["No messages yet."]
        index = self.list.GetSelection()
        selected = self._keys[index] if 0 <= index < len(self._keys) else None
        current = list(self.list.GetStrings())
        if self._keys and keys[:len(self._keys)] == self._keys and current:
            for i, line in enumerate(lines[:len(current)]):
                if current[i] != line:
                    self.list.SetString(i, line)
            if len(lines) > len(current):
                self.list.Append(lines[len(current):])
        else:
            self.list.Set(lines)
            if selected in keys:
                self.list.SetSelection(keys.index(selected))
        self._keys, self._shown = keys, shown
        if newest or self.list.GetSelection() == wx.NOT_FOUND:
            if self.list.GetCount():
                self.list.SetSelection(self.list.GetCount() - 1)
        refresh_list_item(self.list)

    def _item_text(self, index: int) -> Optional[str]:
        """The whole message, as words, with who said it (#11)."""
        if not self._whole_in_list or not 0 <= index < len(self._shown):
            return None
        message = self._shown[index]
        cached = self._spoken.get(message.key)
        if cached is None or cached[0] != (message.label, message.text):
            spoken = announce.spoken_markdown(message.text, describe_code=True) or message.first_line()
            cached = ((message.label, message.text), f"{message.label}: {spoken}")
            self._spoken[message.key] = cached
        return cached[1]

    def selected(self) -> Optional[ChatMessage]:
        index = self.list.GetSelection()
        return self._shown[index] if 0 <= index < len(self._shown) else None

    # -- actions -------------------------------------------------------------

    def read_selected(self):
        message = self.selected()
        if message is None:
            self.feedback("No message selected.")
            return
        self._read_message(message, self)
        if self:
            self.list.SetFocus()

    def copy_selected(self):
        message = self.selected()
        if message is None:
            self.feedback("No message selected.")
            return
        self.feedback("Message copied." if self._copy(message.full_text())
                      else "Couldn't open the clipboard.")

    def set_activity(self, show: bool):
        """Show Tool Activity, here only: the main window's is left as it is."""
        self.show_activity = show
        if self.activity.GetValue() != show:
            self.activity.SetValue(show)
        message = self.selected()
        self._keys = []  # a new set of rows
        self._fill()
        if message is not None and self._shown:
            self.list.SetSelection(self._row_for(message))
            refresh_list_item(self.list)
        self.feedback("Showing tool activity." if show else "Hiding tool activity.")

    def _row_for(self, message: ChatMessage) -> int:
        """``message``'s row, or if it's hidden (a tool row, with tool
        activity turned off) that of the nearest message shown before it,
        so you stay where you were rather than land on the last row."""
        rows = {m.key: i for i, m in enumerate(self._shown)}
        messages = self.reader.transcript.messages
        at = next((i for i, m in enumerate(messages) if m is message), len(messages))
        for candidate in [message, *reversed(messages[:at])]:
            if candidate.key in rows:
                return rows[candidate.key]
        return 0

    def refresh(self) -> bool:
        """Read what the subagent added, and notice it ending or being sent
        on again."""
        try:
            changed = self.reader.refresh()
        except Exception:  # noqa: BLE001 - a bad read waits for the next tick
            changed = False
        if changed:
            self._fill()
        latest = self._latest() if self._latest is not None else None
        if latest is not None and latest.status != self.subagent.status:
            was_running = self.subagent.status == RUNNING
            self.subagent = latest
            set_accessible_name(self.list, self._list_name())
            refresh_list_item(self.list)
            if was_running and latest.status != RUNNING:
                self.timer.Start(FINISHED_REFRESH_MS)
                self.feedback(latest.ended_text())
            elif latest.status == RUNNING:
                self.timer.Start(REFRESH_MS)  # sent on again
        return changed

    def _show_menu(self):
        menu = wx.Menu()
        read = menu.Append(wx.ID_ANY, "Read &Full Message\tEnter")
        copy = menu.Append(wx.ID_ANY, "&Copy Message\tCtrl+C")
        activity = menu.AppendCheckItem(wx.ID_ANY, "Show &Tool Activity\tCtrl+T")
        activity.Check(self.show_activity)
        menu.Bind(wx.EVT_MENU, lambda e: self.read_selected(), read)
        menu.Bind(wx.EVT_MENU, lambda e: self.copy_selected(), copy)
        menu.Bind(wx.EVT_MENU, lambda e: self.set_activity(not self.show_activity), activity)
        read.Enable(self.selected() is not None)
        copy.Enable(self.selected() is not None)
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers():
            self.read_selected()
            return
        if key == ord("C") and event.ControlDown() and not event.AltDown() and not event.ShiftDown():
            self.copy_selected()
            return
        event.Skip()

    def _on_char_hook(self, event):
        if (event.GetKeyCode() == ord("T") and event.ControlDown()
                and not event.AltDown() and not event.ShiftDown()):
            self.set_activity(not self.show_activity)
            return
        if (event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and not event.HasAnyModifiers() and wx.Window.FindFocus() is self.list):
            # Here, not only in the list's own key handler: Windows gives a
            # list box's Enter to the dialog first, so it did nothing (#468).
            self.read_selected()
            return
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    def Destroy(self):
        # Before the window goes. A check of the destroy event's object never
        # matched under GTK, so the timer outlived the window and its next
        # tick crashed the app (the Orca check on #512).
        self.timer.Stop()
        return super().Destroy()
