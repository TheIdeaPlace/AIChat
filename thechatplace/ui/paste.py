"""Pasting console output into a message box as a code block (#701): the
window side of console_paste, shared by the reply box and New Session's
First message box."""
from __future__ import annotations

import wx

from .. import console_paste


def clipboard_text() -> str:
    """The clipboard's text, or "" (tests replace this, as fake_env does)."""
    if not wx.TheClipboard.Open():
        return ""
    try:
        if not wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_UNICODETEXT)):
            return ""
        data = wx.TextDataObject()
        return data.GetText() if wx.TheClipboard.GetData(data) else ""
    finally:
        wx.TheClipboard.Close()


def paste_console_as_code(box: wx.TextCtrl, feedback) -> bool:
    """Put the clipboard's console output into ``box`` as a code block, in
    place of any selection, on lines of its own. True if it did; False
    leaves the paste to the box, as usual."""
    text = clipboard_text()
    if not console_paste.is_console(text):
        return False
    block = console_paste.as_code_block(text)
    start, end = box.GetSelection()
    before = box.GetRange(0, start)
    after = box.GetRange(end, box.GetLastPosition())
    if start != end:
        box.Remove(start, end)
    box.SetInsertionPoint(start)
    # The fences need lines of their own, and what you type next goes
    # after the block, not inside it.
    box.WriteText(("\n" if before and not before.endswith("\n") else "") + block
                  + ("" if after.startswith("\n") else "\n"))
    lines = console_paste.line_count(block)
    feedback(f"Pasted console output as a code block, {lines} line{'s' if lines != 1 else ''}.")
    return True
