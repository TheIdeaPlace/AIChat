"""Tools, Customize Keyboard Shortcuts (#635): give a command a key, move
one, take one away, or put the defaults back.

The list is every command whose key can change (``keymap.customizable``),
in menu order. Each row reads the command, its key (or "no key"), whether
you changed it, and where it is, so arrowing through says it all. A key is
chosen in a dialog of its own (``KeyCaptureDialog``) by pressing it: what
was pressed is said, with why it can't be used, or which command has it now.

Changes are saved and take effect as they're made (``apply``), as in
QuickMail; Close or Escape closes.
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional

import wx

from .. import commands as command_list
from ..keymap import KeyMap, customizable, normalize, spoken
from . import gtk_a11y, mac_a11y
from .a11y import set_accessible_name, set_list_items_accessible
from .dialogs import _fit_width, press_focused_button_on_a_mac
from .sizes import dialog_size, dip

#: Keys by wx code, other than letters, digits, marks and F keys.
_NAMED = {
    wx.WXK_RETURN: "Enter", wx.WXK_NUMPAD_ENTER: "Enter",
    wx.WXK_DELETE: "Delete", wx.WXK_NUMPAD_DELETE: "Delete",
    wx.WXK_INSERT: "Insert", wx.WXK_NUMPAD_INSERT: "Insert",
    wx.WXK_HOME: "Home", wx.WXK_END: "End", wx.WXK_PAGEUP: "PageUp", wx.WXK_PAGEDOWN: "PageDown",
    wx.WXK_UP: "Up", wx.WXK_DOWN: "Down", wx.WXK_LEFT: "Left", wx.WXK_RIGHT: "Right",
    wx.WXK_SPACE: "Space", wx.WXK_TAB: "Tab", wx.WXK_ESCAPE: "Escape", wx.WXK_BACK: "Backspace",
}
#: Keys that are only part of a key: pressing one alone chooses nothing.
_MODIFIERS = {wx.WXK_SHIFT, wx.WXK_CONTROL, wx.WXK_ALT, wx.WXK_RAW_CONTROL, wx.WXK_COMMAND,
              wx.WXK_WINDOWS_LEFT, wx.WXK_WINDOWS_RIGHT, wx.WXK_MENU,
              wx.WXK_CAPITAL, wx.WXK_NUMLOCK, wx.WXK_SCROLL}


def event_key(event) -> str:
    """The key ``event`` is, in keymap's names ("Ctrl+Shift+Q"), or "" for
    a modifier alone or a key The Chat Place doesn't name. Ctrl is Command
    on a Mac, as wx's ControlDown is there."""
    code = event.GetKeyCode()
    if code in _MODIFIERS:
        return ""
    if wx.WXK_F1 <= code <= wx.WXK_F24:
        name = f"F{code - wx.WXK_F1 + 1}"
    elif code in _NAMED:
        name = _NAMED[code]
    elif wx.WXK_NUMPAD0 <= code <= wx.WXK_NUMPAD9:
        name = str(code - wx.WXK_NUMPAD0)
    elif 32 < code < 127:
        name = chr(code)
    else:
        return ""
    mods = [m for m, down in (("Ctrl", event.ControlDown()), ("Alt", event.AltDown()),
                              ("Shift", event.ShiftDown())) if down]
    try:
        return normalize("+".join(mods + [name]))
    except ValueError:
        return ""


def _place(command_id: str) -> str:
    """A command's place, as said: "Chat menu", "Chat, Chat Settings"."""
    trail = command_list.menu_path(command_id).split(", ")[:-1]
    return f"{trail[0]} menu" if len(trail) == 1 else ", ".join(trail)


def _name(command_id: str) -> str:
    return command_list.plain_label(command_list.command(command_id))


class KeyCaptureDialog(wx.Dialog):
    """Change Key: press the key you want. What you pressed is said and
    shown, with why it can't be used, or which command has it now (it moves
    here, and that one has no key). Assign (or Enter) gives it; Try Another
    clears it; Escape or Cancel changes nothing.

    In the key box every key is taken as the one you want, except Tab and
    Shift+Tab (to move on), Escape (to cancel), Enter (to assign) and Alt
    with a letter (this dialog's own buttons; no command can have one)."""

    def __init__(self, parent, keys: KeyMap, command_id: str, say: Callable[[str], None]):
        name = _name(command_id)
        super().__init__(parent, title=f"Change Key: {name}", size=dialog_size(parent, 520, 260))
        self.keys = keys
        self.command_id = command_id
        self._say = say
        #: The key pressed, normalized, or "".
        self.key = ""
        current = keys.key(command_id)
        sizer = wx.BoxSizer(wx.VERTICAL)
        now = spoken(current) if current else "no key"
        intro = wx.StaticText(self, label=f"Press the keys you want for {name}. Its key now: {now}.")
        intro.Wrap(dip(self, 480, -1)[0])
        sizer.Add(intro, 0, wx.ALL, 8)
        sizer.Add(wx.StaticText(self, label="&Key:"), 0, wx.LEFT | wx.RIGHT, 8)
        self.box = wx.TextCtrl(self, style=wx.TE_READONLY)
        set_accessible_name(self.box, "Key, press the keys you want")
        mac_a11y.set_label(self.box, "Key, press the keys you want")
        gtk_a11y.set_label(self.box, "Key, press the keys you want")  # Orca
        sizer.Add(self.box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.status = wx.StaticText(self, label="Nothing pressed yet.")
        sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.assign_btn = wx.Button(self, wx.ID_OK, "&Assign")
        self.assign_btn.SetDefault()
        self.again_btn = wx.Button(self, label="&Try Another")
        row.Add(self.assign_btn, 0, wx.RIGHT, 6)
        row.Add(self.again_btn, 0, wx.RIGHT, 6)
        row.AddStretchSpacer()
        row.Add(wx.Button(self, wx.ID_CANCEL, "Cancel"), 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        _fit_width(self)
        self.SetEscapeId(wx.ID_CANCEL)
        self.assign_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_assign())
        self.again_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_again())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        wx.CallAfter(self.box.SetFocus)

    def _on_key(self, event):
        if wx.Window.FindFocus() is not self.box:
            if not press_focused_button_on_a_mac(event):
                event.Skip()
            return
        code = event.GetKeyCode()
        ctrl, alt, shift = event.ControlDown(), event.AltDown(), event.ShiftDown()
        if code == wx.WXK_TAB and not ctrl and not alt:
            event.Skip()  # move on, as anywhere
            return
        if code == wx.WXK_ESCAPE and not (ctrl or alt or shift):
            event.Skip()  # Cancel
            return
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not (ctrl or alt or shift):
            self.on_assign()
            return
        if (alt and not ctrl and 32 < code < 127 and chr(code).isalpha()
                and sys.platform != "darwin"):
            event.Skip()  # this dialog's own access keys
            return
        if code in _MODIFIERS:
            return
        key = event_key(event)
        if key:
            self.capture(key)
        else:
            self.key = ""
            self.box.ChangeValue("")
            self._status("That key can't be used for a shortcut.")

    def capture(self, key: str):
        """``key`` was pressed: show it and say what Assign would do."""
        self.key = key
        name = _name(self.command_id)
        why = self.keys.why_not(self.command_id, key)
        taken = self.keys.command_for(key)
        if why:
            status = why
        elif taken == self.command_id:
            status = f"That's {name}'s key now."
        elif taken:
            status = (f"{_name(taken)} has it now. Assign moves it to {name}, "
                      f"and {_name(taken)} will have no key.")
        else:
            status = f"Nobody has it. Assign gives it to {name}."
        self.box.ChangeValue(spoken(key))
        self._status(status, f"{spoken(key)}. {status}")

    def _status(self, status: str, said: str = ""):
        self.status.SetLabel(status)
        self.status.Wrap(self.GetClientSize().width - dip(self, 16, -1)[0])
        self.Layout()
        self._say(said or status)

    def usable(self) -> bool:
        return bool(self.key) and not self.keys.why_not(self.command_id, self.key)

    def on_assign(self):
        if not self.key:
            self._say("Press the keys you want first.")
            self.box.SetFocus()
            return
        if not self.usable():
            self._say(self.keys.why_not(self.command_id, self.key))
            self.box.SetFocus()
            return
        self.EndModal(wx.ID_OK)

    def on_again(self):
        self.key = ""
        self.box.ChangeValue("")
        self.status.SetLabel("Nothing pressed yet.")
        self.box.SetFocus()


class KeyboardShortcutsDialog(wx.Dialog):
    """Tools, Customize Keyboard Shortcuts (#635). ``keys`` is the window's
    KeyMap; ``apply()`` shows a change at once in the window; ``say`` says
    what each action did, as the window's answers are said."""

    def __init__(self, parent, keys: KeyMap, say: Callable[[str], None],
                 apply: Callable[[], None], say_key: Optional[Callable[[str], None]] = None):
        super().__init__(parent, title="Customize Keyboard Shortcuts",
                         size=dialog_size(parent, 680, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.keys = keys
        self._say = say
        self._apply = apply
        #: Says Change Key's answer to a key press: always, and at once.
        self._say_key = say_key or say
        self._save_warned = False
        #: Every command whose key can change, in menu order.
        self._all = [c.id for c in command_list.commands() if customizable(c)]
        #: The ids shown, by row.
        self._shown: List[str] = []
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Filter:"), 0, wx.LEFT | wx.TOP, 8)
        self.filter = wx.TextCtrl(self)
        set_accessible_name(self.filter, "Filter")
        sizer.Add(self.filter, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="Co&mmands:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Commands")
        self.list.SetMinSize(dip(self, -1, 260))
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.change_btn = wx.Button(self, label="&Change Key...")
        self.remove_btn = wx.Button(self, label="&Remove Key")
        self.reset_btn = wx.Button(self, label="Reset to &Default")
        self.reset_all_btn = wx.Button(self, label="Reset &All...")
        for button in (self.change_btn, self.remove_btn, self.reset_btn, self.reset_all_btn):
            row.Add(button, 0, wx.RIGHT, 6)
        row.AddStretchSpacer()
        row.Add(wx.Button(self, wx.ID_CANCEL, "C&lose"), 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        _fit_width(self)
        self.SetEscapeId(wx.ID_CANCEL)
        self.change_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_change())
        self.remove_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_remove())
        self.reset_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_reset())
        self.reset_all_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_reset_all())
        self.filter.Bind(wx.EVT_TEXT, lambda e: self._fill(self._selected_id()))
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_change())
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        set_list_items_accessible(self.list, self._list_name, lambda index: None)
        self._fill(None)
        wx.CallAfter(self.list.SetFocus)

    # -- the list -------------------------------------------------------------

    def row(self, command_id: str) -> str:
        """A row as read: "Prompts, Ctrl+K, changed, Chat menu"."""
        key = self.keys.key(command_id)
        parts = [_name(command_id), spoken(key) if key else "no key"]
        if self.keys.changed(command_id):
            parts.append("changed")
        parts.append(_place(command_id))
        return ", ".join(p for p in parts if p)

    def _list_name(self) -> str:
        return f"Commands, {len(self._shown)}"

    def _fill(self, select: Optional[str]):
        words = self.filter.GetValue().lower().split()
        self._shown = [c for c in self._all
                       if all(w in self.row(c).lower() for w in words)]
        if self._shown:
            self.list.Set([self.row(c) for c in self._shown])
            index = self._shown.index(select) if select in self._shown else 0
            self.list.SetSelection(index)
        else:
            self.list.Set(["No command matches the filter."])
            self.list.SetSelection(0)
        self.list.SetName(self._list_name())
        mac_a11y.set_label(self.list, self._list_name())
        gtk_a11y.set_label(self.list, self._list_name())

    def _selected_id(self) -> Optional[str]:
        index = self.list.GetSelection()
        return self._shown[index] if 0 <= index < len(self._shown) else None

    def _on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers():
            self.on_change()
            return
        deletes = (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) + ((wx.WXK_BACK,) if sys.platform == "darwin" else ())
        if key in deletes and not event.HasAnyModifiers():
            self.on_remove()
            return
        event.Skip()

    def _on_key(self, event):
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    # -- changing -----------------------------------------------------------------

    def _none(self) -> Optional[str]:
        """The selected command, or None (said) when no command is shown."""
        command_id = self._selected_id()
        if command_id is None:
            self._say("No command is selected. Clear the filter to see them all.")
            self.list.SetFocus()
        return command_id

    def _saved(self, command_id: str, said: str):
        """Save, show it in the window and the list, and say it."""
        if not self.keys.save() and not self._save_warned:
            self._save_warned = True
            wx.MessageBox(f"{self.keys.load_error}\n\nChanges work until The Chat Place "
                          "closes.", self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
        self._apply()
        self._fill(command_id)
        if command_id is not None and self._selected_id() != command_id:
            # It no longer matches the filter: show everything, on it.
            self.filter.ChangeValue("")
            self._fill(command_id)
        self._say(said)
        self.list.SetFocus()

    def _moved(self, taken: Optional[str]) -> str:
        return f" {_name(taken)} has no key now." if taken else ""

    def on_change(self):
        command_id = self._none()
        if command_id is None:
            return
        dialog = KeyCaptureDialog(self, self.keys, command_id, self._say_key)
        try:
            chosen = dialog.key if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()
        if chosen is None:
            self.list.SetFocus()
            return
        try:
            taken = self.keys.set(command_id, chosen)
        except ValueError as exc:
            self._say(str(exc))
            self.list.SetFocus()
            return
        self._saved(command_id, f"{_name(command_id)}: {spoken(chosen)}.{self._moved(taken)}")

    def on_remove(self):
        command_id = self._none()
        if command_id is None:
            return
        if not self.keys.key(command_id):
            self._say(f"{_name(command_id)} has no key.")
            self.list.SetFocus()
            return
        self.keys.set(command_id, "")
        self._saved(command_id, f"{_name(command_id)}: no key.")

    def on_reset(self):
        command_id = self._none()
        if command_id is None:
            return
        if not self.keys.changed(command_id):
            self._say(f"{_name(command_id)} has its default key.")
            self.list.SetFocus()
            return
        taken = self.keys.reset(command_id)
        default = self.keys.key(command_id)
        now = spoken(default) if default else "no key"
        self._saved(command_id, f"{_name(command_id)}: {now}, its default.{self._moved(taken)}")

    def on_reset_all(self):
        changed = [c for c in self._all if self.keys.changed(c)]
        if not changed:
            self._say("Every command has its default key.")
            self.list.SetFocus()
            return
        count = f"{len(changed)} command{'s' if len(changed) != 1 else ''}"
        if wx.MessageBox(f"Put back the default keys for {count} you changed?", self.GetTitle(),
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            self.list.SetFocus()
            return
        self.keys.reset_all()
        self._saved(self._selected_id(), "Every command has its default key again.")
