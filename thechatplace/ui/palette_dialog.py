"""View, Command Palette (Ctrl+Shift+P, #637): run any of The Chat Place's
commands by typing part of its name.

Focus starts in the Search box. Typing narrows the list (``palette.matches``);
Up and Down (and Page Up and Page Down) move in the list without leaving the
box, and the row arrived on is said, as focus doesn't move to say it. Enter
runs the selected command, by its menu item once the palette has closed (the
window does that), so it does exactly what the menu does. A command that
can't run now is listed as unavailable and says so. Escape closes.
"""
from __future__ import annotations

from typing import Callable, Iterable, List, Optional

import wx

from .. import palette
from ..keymap import KeyMap, spoken
from . import gtk_a11y, mac_a11y
from .a11y import set_accessible_name, set_list_items_accessible
from .dialogs import _fit_width, press_focused_button_on_a_mac
from .sizes import dialog_size, dip

_MOVES = {wx.WXK_UP: -1, wx.WXK_DOWN: 1, wx.WXK_PAGEUP: -10, wx.WXK_PAGEDOWN: 10,
          wx.WXK_NUMPAD_UP: -1, wx.WXK_NUMPAD_DOWN: 1}


class CommandPaletteDialog(wx.Dialog):
    """``keys`` names each command's key; ``recent`` are command ids used
    this session, newest first; ``available(id)`` says whether a command
    can run now; ``say`` says a row arrived on from the Search box.
    ``chosen`` is the id to run once it closes with OK."""

    def __init__(self, parent, keys: KeyMap, recent: Iterable[str],
                 available: Callable[[str], bool], say: Callable[[str], None]):
        super().__init__(parent, title="Command Palette", size=dialog_size(parent, 560, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.keys = keys
        self._recent = list(recent)
        self._available = available
        self._say = say
        self._all = palette.entries()
        self._shown: List[palette.Entry] = []
        self.chosen: Optional[str] = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Search:"), 0, wx.LEFT | wx.TOP, 8)
        self.search = wx.TextCtrl(self)
        set_accessible_name(self.search, "Search commands")
        sizer.Add(self.search, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="&Commands:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Commands")
        self.list.SetMinSize(dip(self, -1, 240))
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.run_btn = wx.Button(self, wx.ID_OK, "&Run")
        self.run_btn.SetDefault()
        row.Add(self.run_btn, 0)
        row.AddStretchSpacer()
        row.Add(wx.Button(self, wx.ID_CANCEL, "C&lose"), 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        _fit_width(self)
        self.SetEscapeId(wx.ID_CANCEL)
        self.run_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_run())
        self.search.Bind(wx.EVT_TEXT, lambda e: self._fill())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_run())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        set_list_items_accessible(self.list, self._list_name, lambda index: None)
        self._fill()
        wx.CallAfter(self.search.SetFocus)

    # -- the list -------------------------------------------------------------

    def _key(self, command_id: str) -> str:
        key = self.keys.key(command_id)
        return spoken(key) if key else ""

    def _match_key(self, command_id: str) -> str:
        """The key as said here and as Windows says it ("Cmd+Slash
        Ctrl+Slash" on a Mac), to match what's typed (palette._as_key)."""
        key = self.keys.key(command_id)
        return f"{spoken(key)} {spoken(key, 'win32')}" if key else ""

    def row(self, entry: palette.Entry) -> str:
        return palette.row(entry, self._key(entry.id), self._available(entry.id))

    def _list_name(self) -> str:
        return f"Commands, {len(self._shown)}"

    def _fill(self):
        self._shown = palette.matches(self.search.GetValue(), self._all, self._match_key,
                                      self._recent)
        #: Whether a move has said a row since the list last changed: the
        #: first Down (or Up) after typing says the top match, unheard so
        #: far, rather than skipping it (#641).
        self._heard = False
        if self._shown:
            self.list.Set([self.row(e) for e in self._shown])
            self.list.SetSelection(0)
        else:
            self.list.Set(["No command matches."])
            self.list.SetSelection(0)
        self.list.SetName(self._list_name())
        mac_a11y.set_label(self.list, self._list_name())
        gtk_a11y.set_label(self.list, self._list_name())

    def selected(self) -> Optional[palette.Entry]:
        index = self.list.GetSelection()
        return self._shown[index] if 0 <= index < len(self._shown) else None

    def _on_key(self, event):
        code = event.GetKeyCode()
        if (wx.Window.FindFocus() is self.search and code in _MOVES
                and not event.HasAnyModifiers()):
            self.move(_MOVES[code])
            return
        if (wx.Window.FindFocus() is self.list and code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and not event.HasAnyModifiers()):
            # Here, not left to the default button, which a Mac's list may
            # not pass Return to (as #640's Enter in a list taught).
            self.on_run()
            return
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    def move(self, step: int):
        """Move in the list from the Search box, and say where it landed."""
        if not self._shown:
            self._say("No command matches.")
            return
        index = self.list.GetSelection()
        if self._heard:
            index = max(0, min(len(self._shown) - 1, index + step))
        self._heard = True
        self.list.SetSelection(index)
        self._say(self.row(self._shown[index]))

    # -- running ----------------------------------------------------------------

    def on_run(self):
        entry = self.selected()
        if entry is None:
            self._say("No command matches. Change the search.")
            self.search.SetFocus()
            return
        if not self._available(entry.id):
            self._say(f"{entry.name} isn't available now.")
            return
        self.chosen = entry.id
        self.EndModal(wx.ID_OK)
