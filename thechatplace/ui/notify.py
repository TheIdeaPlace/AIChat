"""Notifications (#20), for when The Chat Place isn't the window you're in.

On a Mac each one is a macOS notification (wx's NotificationMessage, which
macOS shows under The Chat Place's name, or Python's from source) that
carries its own session: choosing any of them opens that session. macOS asks
once whether the app may send them. Linux does the same: wx sends them
through libnotify to the desktop's notification server, which Orca reads.
The rest of this describes Windows.

One notification-area icon, The Chat Place's own, created with the first
notification and removed when the app closes. Windows shows its balloons as
ordinary notifications: read by screen readers like any other, and quiet
under Do Not Disturb. A balloon click doesn't say which balloon it was, so
only the latest one's session is kept: choosing it calls ``on_click`` with
that key, and choosing the icon itself calls it with None (just come
forward).

Deliberately not wx's NotificationMessage: it shares a temporary icon whose
hidden window can keep the app running after it closes, and it can send a
click to the wrong notification.
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional

import wx
import wx.adv

from .. import platform_paths

def app_icon(size: int) -> "wx.Icon":
    """The app's own icon at ``size`` pixels, or a stock one if it's missing."""
    path = platform_paths.app_icon_path()
    if path.is_file():
        icon = wx.IconBundle(str(path)).GetIcon((size, size))
        if icon.IsOk():
            return icon
    return wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (size, size))


#: Windows' own limits for a balloon's title and text.
TITLE_LIMIT = 63
TEXT_LIMIT = 255
#: How many Mac and Linux notifications are kept, so choosing an older one
#: still works.
MAC_KEPT = 20

#: What Settings calls them, and its label (N is the access key either way).
SETTING_NAME = "Windows notifications" if sys.platform == "win32" else "Notifications"
SETTING_LABEL = "Windows &notifications:" if sys.platform == "win32" else "&Notifications:"

_CLICK = wx.PyEventBinder(wx.adv.wxEVT_NOTIFICATION_MESSAGE_CLICK)


def _fit(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Notifier:
    def __init__(self, on_click: Callable[[Optional[str]], None], tooltip: str,
                 factory=None, message_factory=None, mac: Optional[bool] = None):
        self._on_click = on_click
        self._tooltip = tooltip
        self._factory = factory or wx.adv.TaskBarIcon
        self._icon = None
        self._key: Optional[str] = None  # the latest notification's session
        # A Mac's and Linux's way: one NotificationMessage each.
        self._mac = sys.platform != "win32" if mac is None else mac
        self._message_factory = message_factory or wx.adv.NotificationMessage
        self._messages: List = []  # the Mac notifications shown, newest last

    def show(self, title: str, message: str, key: str) -> bool:
        if self._mac:
            return self._show_mac(title, message, key)
        try:
            icon = self._ensure_icon()
            shown = bool(icon.ShowBalloon(_fit(title, TITLE_LIMIT),
                                          _fit(message or title, TEXT_LIMIT), 0,
                                          wx.ICON_INFORMATION))
        except Exception:  # noqa: BLE001 - a notification must never break the app
            return False
        if shown:
            self._key = key
        return shown

    def _show_mac(self, title: str, message: str, key: str) -> bool:
        try:
            note = self._message_factory(_fit(title, TITLE_LIMIT),
                                         _fit(message or title, TEXT_LIMIT))
            note.Bind(_CLICK, lambda event, key=key: self._on_click(key))
            shown = bool(note.Show(wx.adv.NotificationMessage.Timeout_Auto))
        except Exception:  # noqa: BLE001 - a notification must never break the app
            return False
        if shown:
            # Kept, or the object (and its click) goes when Python collects it.
            self._messages = (self._messages + [note])[-MAC_KEPT:]
        return shown

    def close(self):
        """Remove the icon, and with it our notifications: the app is
        closing, and an icon left behind would keep it running. On a Mac,
        withdraw ours from Notification Center, where choosing one would
        start the app again with nothing to show."""
        messages, self._messages = self._messages, []
        for note in messages:
            try:
                note.Close()
            except Exception:  # noqa: BLE001
                pass
        icon, self._icon, self._key = self._icon, None, None
        if icon is None:
            return
        try:
            icon.RemoveIcon()
            icon.Destroy()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_icon(self):
        if self._icon is None:
            icon = self._factory()
            # The size the notification area asks for: 16 at 100%, 32 at 200%.
            image = app_icon(wx.SystemSettings.GetMetric(wx.SYS_SMALLICON_X) or 16)
            icon.SetIcon(image, self._tooltip)
            icon.Bind(wx.adv.EVT_TASKBAR_BALLOON_CLICK, self._balloon_clicked)
            icon.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, lambda event: self._on_click(None))
            self._icon = icon
        return self._icon

    def _balloon_clicked(self, _event):
        self._on_click(self._key)
