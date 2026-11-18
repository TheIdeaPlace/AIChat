"""No Command+letter on a Mac's buttons (#675).

wxWidgets gives every Mac button with an "&" in its label that letter with
Command as its key equivalent, and AppKit offers a key equivalent to the
window's buttons before the focused control gets the key. So in Tools, API
Keys, "Sa&ve Limits" took Command+V from the key box, and Ask Aside's "&Ask"
would have taken Command+A. In the main window, "Sen&d", "Sto&p", "&Refresh"
and the rest had Command keys nobody chose (Command+D, P, R and so on).

A Mac dialog has no letter shortcuts: Return presses the default button,
Escape cancels, and Tab or VoiceOver reach the rest, and Command keys do
their system jobs. ``install`` keeps it that way for every window, in one
place, so a new dialog can't bring the bug back:

- every wx button's Command key equivalent is taken away as it's given,
  however its label is set or changed (``mac_a11y.stop_button_command_keys``);
  Return on the default button and Escape on Cancel are kept;
- each window's buttons are swept as it's shown, as a fallback.

Nothing here runs on Windows or Linux, where a button's "&" is an Alt key.
"""
from __future__ import annotations

import logging

import wx

from . import mac_a11y

IS_MAC = wx.Platform == "__WXMAC__"

_log = logging.getLogger(__name__)
_installed_on = None  # the app the sweep is bound to


def install(app) -> bool:
    """Hook wx's buttons and bind the sweep to ``app``, once; before the first
    window is made, so no button is made with a Command key. True if it's
    done; False off a Mac, where nothing is done."""
    global _installed_on
    if not IS_MAC or app is None:
        return False
    if _installed_on is app:
        return True
    if not mac_a11y.stop_button_command_keys():
        # Then only the sweep as each window is shown keeps Command+letter
        # off buttons, and a label change after it can put one back.
        _log.warning("Couldn't hook wx's buttons; Command keys are cleared as windows show")
    app.Bind(wx.EVT_SHOW, on_show)
    _installed_on = app
    return True


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
