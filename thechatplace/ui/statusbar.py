"""The status bar as separate parts you can move between, as in QuickMail.

Each part is its own stop: information is focusable read-only text (a
screen reader says its words, with no caret to move), and anything you can
act on is a button. Left and Right move between the parts; Tab leaves the
status bar. The native status bar keeps every part's text in its own field
too, so the screen reader's read-status-bar key reads them all.

Parts with nothing to say are hidden, buttons especially: "Update
available" is there only when there's an update.

On Linux the native fields stay empty: wx draws GTK's status bar itself, so
Orca can't read their text, and a button there has no background, so the
field's text showed through it, doubled. Orca's read-status-bar command reads
the parts themselves instead, the bar having the status bar role.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

import wx

from . import gtk_a11y


class _TextAccessible(wx.Accessible):
    """Read-only text that can have the focus: its words as its name."""

    def __init__(self, window: "StatusText"):
        super().__init__(window)
        self._window = window

    def GetName(self, childId):
        if childId:
            return (wx.ACC_NOT_IMPLEMENTED, "")
        return (wx.ACC_OK, self._window.GetLabel() or self._window.empty_text)

    def GetRole(self, childId):
        if childId:
            return (wx.ACC_NOT_IMPLEMENTED, 0)
        return (wx.ACC_OK, wx.ROLE_SYSTEM_STATICTEXT)

    def GetState(self, childId):
        if childId:
            return (wx.ACC_NOT_IMPLEMENTED, 0)
        state = wx.ACC_STATE_SYSTEM_FOCUSABLE | wx.ACC_STATE_SYSTEM_READONLY
        if wx.Window.FindFocus() is self._window:
            state |= wx.ACC_STATE_SYSTEM_FOCUSED
        return (wx.ACC_OK, state)


def shown_text(dc, text: str, width: int) -> str:
    """``text`` as it fits ``width`` pixels in ``dc``'s font: whole, or
    shortened with "..." at the end rather than cut mid-word (#179). Drawn
    as written: an "&" is a character, not an access key."""
    return wx.Control.Ellipsize(text, dc, wx.ELLIPSIZE_END, max(width, 0),
                                wx.ELLIPSIZE_FLAGS_NONE)


class StatusText(wx.Control):
    """A status bar part that holds information: focusable, read-only, no
    caret. Drawn by hand, with a focus rectangle while it has the focus."""

    def __init__(self, parent: wx.Window, name: str, empty_text: str = ""):
        super().__init__(parent, style=wx.BORDER_NONE, name=name)
        self.empty_text = empty_text
        # New words while you're on the part wait until you leave or come
        # back: a name change on the focus is spoken, and a turn's elapsed
        # time would otherwise be read out every couple of seconds.
        self._waiting: Optional[str] = None
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SET_FOCUS, self._on_focus_change)
        self.Bind(wx.EVT_KILL_FOCUS, self._on_focus_change)
        try:
            self._accessible = _TextAccessible(self)
            self.SetAccessible(self._accessible)
        except (NotImplementedError, AttributeError):
            pass
        # Linux: GTK calls a control wx draws itself a "panel", with no name,
        # so Orca said "panel". It's a label, named by its words.
        gtk_a11y.set_role(self, "label")
        gtk_a11y.set_label(self, empty_text)

    def AcceptsFocus(self):
        return True

    def AcceptsFocusFromKeyboard(self):
        return False  # reached with F6, Ctrl+9 and the arrows, not Tab

    def SetLabel(self, label):
        if label != self.GetLabel():
            super().SetLabel(label)
            gtk_a11y.set_label(self, label or self.empty_text)
            self.Refresh()

    def show_text(self, text: str) -> None:
        """New words: now, or when you next arrive if you're reading it."""
        if wx.Window.FindFocus() is self:
            self._waiting = text if text != self.GetLabel() else None
        else:
            self._waiting = None
            self.SetLabel(text)

    def latest(self) -> str:
        """The newest words, waiting or shown, or the empty text."""
        text = self._waiting if self._waiting is not None else self.GetLabel()
        return text or self.empty_text

    def _on_focus_change(self, event):
        if self._waiting is not None:
            text, self._waiting = self._waiting, None
            self.SetLabel(text)
        self.Refresh()
        event.Skip()

    def _on_paint(self, _event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetParent().GetBackgroundColour()))
        dc.Clear()
        dc.SetFont(self.GetParent().GetFont())
        dc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNTEXT))
        width, height = self.GetClientSize()
        text = self.GetLabel() or self.empty_text
        _w, text_height = dc.GetTextExtent(text or "Ag")
        dc.SetClippingRegion(0, 0, width, height)
        # Shortened with "..." when it doesn't fit, rather than cut mid-word
        # ("Pick a name for the rel", #179). What screen readers read is the
        # whole label, from the accessible above.
        dc.DrawText(shown_text(dc, text, width - 6), 3, max((height - text_height) // 2, 0))
        if self.HasFocus():
            wx.RendererNative.Get().DrawFocusRect(self, dc, wx.Rect(0, 0, width, height))


class StatusButton(wx.Button):
    """A status bar part you can act on: a real button, Enter or Space."""

    def __init__(self, parent, on_hidden_focus: Callable[[], None], **kwargs):
        super().__init__(parent, **kwargs)
        self._on_hidden_focus = on_hidden_focus
        self.Bind(wx.EVT_KEY_DOWN, self._on_key)
        self.Bind(wx.EVT_SET_FOCUS, self._on_focus)

    def _on_key(self, event):
        # Outside a dialog or panel nothing turns Enter into a click.
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) \
                and not event.HasAnyModifiers():
            self.Command(wx.CommandEvent(wx.wxEVT_BUTTON, self.GetId()))
            return
        event.Skip()

    def _on_focus(self, event):
        # Windows can hand the focus back to a button hidden while you were
        # in another window: move on to a part that's there.
        if not self.IsShown():
            wx.CallAfter(self._on_hidden_focus)
        event.Skip()

    def AcceptsFocusFromKeyboard(self):
        return False  # reached with F6, Ctrl+9 and the arrows, not Tab


#: Space kept around each part inside its field.
_INSET = 2
#: The native fields carry the parts' text (see the module docstring).
_FIELD_TEXT = not gtk_a11y.IS_LINUX


@dataclass
class _Part:
    key: str
    control: wx.Window
    width: int  # as for SetStatusWidths: negative shares what's left
    always: bool  # shown even with nothing to say


class StatusParts:
    """The parts, in order, laid over the native status bar's fields."""

    def __init__(self, bar: wx.StatusBar):
        self.bar = bar
        self._parts: List[_Part] = []
        self._fitted = False  # the bar's height is set for its buttons (#175)
        bar.Bind(wx.EVT_SIZE, self._on_size)
        bar.Bind(wx.EVT_DPI_CHANGED, self._on_dpi_changed)
        gtk_a11y.set_role(bar, "statusbar")

    def add_text(self, key: str, name: str, width: int, empty_text: str = "") -> StatusText:
        control = StatusText(self.bar, name, empty_text)
        self._parts.append(_Part(key, control, width, True))
        self.layout()
        return control

    def add_button(self, key: str, width: int,  # width: in design pixels (#185)
                   on_press: Callable[[], None]) -> "StatusButton":
        control = StatusButton(self.bar, self.focus_first, label="",
                               style=wx.BORDER_NONE | wx.BU_EXACTFIT)
        control.Hide()
        if not self._fitted:
            self._fit_height(control)
        control.Bind(wx.EVT_BUTTON, lambda e: on_press())
        self._parts.append(_Part(key, control, width, False))
        self.layout()
        return control

    def _fit_height(self, button: wx.Button) -> None:
        """Make the bar tall enough for a button's text (#175): its fields
        were 20 pixels and a button with letters below the line needs 23, so
        "needs you" read as "needs vou". SetMinHeight takes the height the
        controls need and adds the bar's own borders. Every button has the
        same font, so the first one's height does for all. (A Mac's status
        bar keeps its own fixed height whatever this asks.)"""
        # Measured on a button of its own: the one passed may be showing
        # text, which a screen reader reads as its name (after a move to a
        # screen with other scaling it's a live button, #185).
        probe = wx.Button(self.bar, label="Ag")
        probe.SetFont(button.GetFont())
        try:
            needed = probe.GetBestSize().height
        finally:
            probe.Destroy()
        before = self.bar.GetFieldRect(0).height
        self.bar.SetMinHeight(needed)
        self._fitted = True
        if self.bar.GetFieldRect(0).height != before:
            self.bar.GetParent().SendSizeEvent()

    def get(self, key: str) -> wx.Window:
        return next(p.control for p in self._parts if p.key == key)

    def set(self, key: str, text: str) -> None:
        """A part's words. A button with nothing to say is hidden."""
        part = next(p for p in self._parts if p.key == key)
        text = text or ""
        shown_before = part.control.IsShown()
        if isinstance(part.control, StatusText):
            part.control.show_text(text)
        elif part.control.GetLabel() != text:
            part.control.SetLabel(text)
        show = part.always or bool(text)
        if show != shown_before:
            if not show and wx.Window.FindFocus() is part.control:
                self.move(part.control, -1)  # don't leave the focus on nothing
            part.control.Show(show)
            self.layout()
        else:
            self._sync_fields()

    def shown(self) -> List[wx.Window]:
        return [p.control for p in self._parts if p.always or p.control.IsShown()]

    def contains(self, window: Optional[wx.Window]) -> bool:
        return window is not None and any(p.control is window for p in self._parts)

    def focus_first(self) -> None:
        shown = self.shown()
        if shown:
            shown[0].SetFocus()

    def move(self, current: wx.Window, step: int, to_end: bool = False) -> None:
        """Left/Right (step -1/+1) from ``current``, stopping at either end
        rather than wrapping, as list items do; Home/End with ``to_end``."""
        shown = self.shown()
        if not shown:
            return
        if to_end:
            shown[0 if step < 0 else -1].SetFocus()
            return
        index = shown.index(current) if current in shown else 0
        shown[max(0, min(len(shown) - 1, index + step))].SetFocus()

    def layout(self) -> None:
        shown = [p for p in self._parts if p.always or p.control.IsShown()]
        if not shown:
            return
        # A part's width is in design pixels (#185): scaled here, every time,
        # so it follows a move to a screen with other scaling. A negative
        # width is a share of what's left, and isn't scaled. A button's field
        # is never narrower than its text needs: Linux's fonts and taller
        # bar (whose size grip is as wide as it's tall) cut "1 session needs
        # you" to "l session needs you".
        grip = self.bar.GetSize().height
        widths = []
        for index, part in enumerate(shown):
            width = self.bar.FromDIP(part.width) if part.width > 0 else part.width
            if part.width > 0 and isinstance(part.control, StatusButton):
                needed = part.control.GetBestSize().width + 2 * _INSET
                if index == len(shown) - 1:
                    needed += grip
                width = max(width, needed)
            widths.append(width)
        self.bar.SetFieldsCount(len(shown), widths)
        # A field holds a little less than asked for (its borders): made up.
        short = False
        for index, part in enumerate(shown):
            if part.width > 0 and isinstance(part.control, StatusButton):
                room = self.bar.GetFieldRect(index).width - 2 * _INSET
                if index == len(shown) - 1:
                    room -= grip
                missing = part.control.GetBestSize().width - room
                if missing > 0:
                    widths[index] += missing
                    short = True
        if short:
            self.bar.SetFieldsCount(len(shown), widths)
        for index, part in enumerate(shown):
            rect = self.bar.GetFieldRect(index)
            width = rect.width - 2 * _INSET
            if index == len(shown) - 1:
                width -= grip  # keep the size grip visible
            # Never below 1: before the bar is laid out its fields are 0
            # high, and GTK refuses a negative height (a warning each time).
            part.control.SetSize(rect.x + _INSET, rect.y + _INSET, max(width, 20),
                                 max(rect.height - 2 * _INSET, 1))
        self._sync_fields()

    def _sync_fields(self) -> None:
        if not _FIELD_TEXT:
            return
        shown = [p for p in self._parts if p.always or p.control.IsShown()]
        for index, part in enumerate(shown):
            if index < self.bar.GetFieldsCount():
                text = part.control.latest() if isinstance(part.control, StatusText) \
                    else part.control.GetLabel()
                if self.bar.GetStatusText(index) != text:
                    self.bar.SetStatusText(text, index)

    def _on_dpi_changed(self, event):
        """Moved to a screen with other scaling (#185): the buttons' text is
        another size, so the bar's height is fitted again, and the parts laid
        out at the new scale."""
        event.Skip()
        buttons = [p.control for p in self._parts if isinstance(p.control, StatusButton)]
        if buttons:
            self._fit_height(buttons[0])
        self.layout()

    def _on_size(self, event):
        self.layout()
        event.Skip()
