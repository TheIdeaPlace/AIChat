"""Sizes in the units the app is designed in, turned into the screen's pixels.

The app is DPI aware on Windows (#185): Windows no longer stretches it, so
at 150% a size written as 680 pixels would come out a third smaller than
the text in it. Every fixed size goes through here instead, and becomes
680 pixels at 100%, 1020 at 150%. On a Mac (and at 100%) nothing changes.
"""
from __future__ import annotations

from typing import Optional

import wx

#: Room kept between a dialog and the screen's edges (and the taskbar).
SCREEN_MARGIN = 40


def dip(window: Optional[wx.Window], width: int, height: int = -1) -> wx.Size:
    """``width`` by ``height`` design pixels in ``window``'s screen pixels
    (the main screen's when there's no window yet). -1 stays -1."""
    return wx.Window.FromDIP(wx.Size(width, height), window)


def dip_width(window: Optional[wx.Window], width: int) -> int:
    return dip(window, width, -1).width


def screen_area(window: Optional[wx.Window]) -> wx.Rect:
    """The work area (less the taskbar) of the screen ``window`` is on, or
    the main screen's."""
    index = wx.Display.GetFromWindow(window) if window else wx.NOT_FOUND
    return wx.Display(index if index != wx.NOT_FOUND else 0).GetClientArea()


def dialog_size(parent: Optional[wx.Window], width: int, height: int) -> wx.Size:
    """A dialog's size: its design size in screen pixels, made smaller where
    it would be bigger than the screen (#186: at 175% on a 1080-pixel
    screen, a dialog 620 high ran under the taskbar and hid its buttons)."""
    size = dip(parent, width, height)
    area = screen_area(parent)
    return wx.Size(min(size.width, area.width - SCREEN_MARGIN),
                   min(size.height, area.height - SCREEN_MARGIN))
