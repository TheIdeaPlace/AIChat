"""A dialog's access keys on a Mac (#675): Option+letter, as Alt+letter on
Windows, and never Command+letter.

wxWidgets gives every Mac button with an "&" in its label that letter with
Command as its key equivalent, and AppKit offers a key equivalent to the
window's buttons before the focused control gets the key. So in Tools, API
Keys, "Sa&ve Limits" took Command+V from the key box, and Ask Aside's "&Ask"
would have taken Command+A. ``install`` fixes both for every window, in one
place, so a new dialog can't bring it back:

- every wx button's Command key equivalent is taken away as it's given,
  however its label is set or changed (``mac_a11y.stop_button_command_keys``),
  with a sweep of each window's buttons as it's shown as a fallback;
- Option and a letter in a dialog does what Alt and that letter does on
  Windows (``access_keys``): presses the button, checks the check box,
  chooses the radio button, or moves to the control a label names.

It's the app's own char hook, so it comes after the dialog's: a dialog that
handles Option+letter itself (Change Key's box, which takes Option+letter
as a shortcut to assign; a server's form, whose check boxes only move) gets
it first, and keys a dialog doesn't skip never reach it. Only dialogs: in
the main window, Option+letter keeps typing the accented and other
characters a Mac types with it. In a dialog, an Option+letter that's one of
its access keys is taken for that, as Alt+letter is on Windows.

Nothing here runs on Windows or Linux, which have access keys of their own.
"""
from __future__ import annotations

import logging

import wx

from .. import access_keys
from ..access_keys import BUTTON, CHECK, GROUP, LABEL, OTHER, RADIO, Stop
from . import mac_a11y

IS_MAC = wx.Platform == "__WXMAC__"

_log = logging.getLogger(__name__)
_installed_on = None  # the app the handlers are bound to
_warned: set = set()  # (dialog class, letter) already logged as shared


def install(app) -> bool:
    """Bind the handlers to ``app``, once; before the first window is made, so
    no button is made with a Command key. True if they're bound; False off a
    Mac, where nothing is done."""
    global _installed_on
    if not IS_MAC or app is None:
        return False
    if _installed_on is app:
        return True
    if not mac_a11y.stop_button_command_keys():
        # Then only the sweep as each window is shown keeps Command+letter
        # off buttons, and a label change after it can put one back.
        _log.warning("Couldn't hook wx's buttons; Command keys are cleared as windows show")
    app.Bind(wx.EVT_CHAR_HOOK, on_char_hook)
    app.Bind(wx.EVT_SHOW, on_show)
    _installed_on = app
    return True


def _kind(control) -> str:
    # RadioBox before the rest: it's one control, focused as a group.
    if isinstance(control, wx.RadioBox):
        return GROUP
    if isinstance(control, (wx.StaticText, wx.StaticBox)):
        return LABEL
    if isinstance(control, wx.CheckBox):
        return CHECK
    if isinstance(control, wx.RadioButton):
        return RADIO
    if isinstance(control, wx.Button):
        return BUTTON
    return OTHER


#: Controls that show something and never take the focus.
_NEVER_FOCUSED = (wx.StaticText, wx.StaticBox, wx.StaticLine, wx.StaticBitmap, wx.Gauge)


def _usable(control, top) -> bool:
    """Shown and enabled, with everything it's in up to the dialog (which may
    itself be hidden, in the tests)."""
    window = control
    while window is not None and window is not top:
        if not window.IsShown() or not window.IsEnabled():
            return False
        window = window.GetParent()
    return True


def controls(top) -> list:
    """``top``'s controls in tab order (wx's: the order they were made, each
    container's own before the next), and a ``Stop`` for each."""
    found = []

    def walk(parent):
        for child in parent.GetChildren():
            if child.IsTopLevel():
                continue
            kind = _kind(child)
            key = "" if kind == OTHER else access_keys.access_key(child.GetLabel())
            # Any control but a static one, not only what AcceptsFocus says:
            # on a Mac that's whether Tab stops there, and with the system's
            # keyboard navigation off (its default) Tab passes over pop-ups,
            # buttons and check boxes, so "&Provider:" went on past its menu.
            # A container (a panel) counts only if it takes the focus itself.
            focusable = kind != LABEL and not isinstance(child, _NEVER_FOCUSED) and (
                isinstance(child, wx.Control) or child.AcceptsFocus())
            found.append((child, Stop(kind, key, _usable(child, top), focusable)))
            if kind != GROUP:  # a radio box's own buttons are reached through it
                walk(child)
    walk(top)
    return found


def press(top, letter: str) -> bool:
    """Do what ``letter``'s access key does in dialog ``top``. True if a
    control had it."""
    found = controls(top)
    stops = [stop for _control, stop in found]
    target = access_keys.find(stops, letter)
    if target is None:
        return False
    if letter.lower() in access_keys.duplicates(stops):
        name = type(top).__name__
        if (name, letter.lower()) not in _warned:
            _warned.add((name, letter.lower()))
            _log.warning("%s: more than one control has access key %r; the first has it",
                         name, letter.lower())
    owner = found[target.owner][0]
    if target.kind == BUTTON:
        # Focus stays where it was, as Windows leaves it.
        _send(owner, wx.wxEVT_BUTTON)
    elif target.kind == CHECK:
        owner.SetFocus()
        if not owner.Is3State():
            owner.SetValue(not owner.GetValue())
            _send(owner, wx.wxEVT_CHECKBOX, int(owner.GetValue()))
    elif target.kind == RADIO:
        owner.SetFocus()
        if not owner.GetValue():
            owner.SetValue(True)
            _send(owner, wx.wxEVT_RADIOBUTTON, 1)
    else:
        found[target.focus][0].SetFocus()
    return True


def _send(control, event_type, value=None):
    """The event a click on ``control`` sends, handled now, as a Return on a
    focused button is (``dialogs.press_focused_button_on_a_mac``)."""
    event = wx.CommandEvent(event_type, control.GetId())
    event.SetEventObject(control)
    if value is not None:
        event.SetInt(value)
    control.GetEventHandler().ProcessEvent(event)


def on_char_hook(event):
    """Option+letter in a dialog, after the dialog's own handlers skipped it."""
    event.Skip()
    try:
        letter = access_keys.option_letter(event.GetModifiers() == wx.MOD_ALT,
                                           event.GetKeyCode(), event.GetUnicodeKey())
        if not letter:
            return
        window = event.GetEventObject()
        if not isinstance(window, wx.Window):
            window = wx.Window.FindFocus()
        top = wx.GetTopLevelParent(window) if window else None
        if not isinstance(top, wx.Dialog) or top.IsBeingDeleted():
            return
        if press(top, letter):
            event.Skip(False)
    except Exception:  # noqa: BLE001 - a key must never take the window down
        _log.exception("Option+letter")


def sweep(top) -> None:
    """Take any Command key off ``top``'s buttons: the fallback for a button
    the class hook didn't see (one of a class wx made it from that isn't
    ``wxNSButton``)."""
    def walk(parent):
        for child in parent.GetChildren():
            if child.IsTopLevel():
                continue
            if isinstance(child, wx.AnyButton):
                mac_a11y.drop_command_key(child)
            walk(child)
    walk(top)


def on_show(event):
    event.Skip()
    try:
        window = event.GetEventObject()
        if event.IsShown() and isinstance(window, wx.TopLevelWindow):
            sweep(window)
    except Exception:  # noqa: BLE001
        _log.exception("clearing buttons' Command keys")
