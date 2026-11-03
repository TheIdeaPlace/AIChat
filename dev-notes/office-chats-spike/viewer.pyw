"""A read-only window onto the Claude add-in's chats. Spike for AIChat#541.

Everything is a native control, so screen readers name and read them without
extra work. Loading happens before the window shows, so focus lands on a list
that's already full.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wx  # noqa: E402

import officechats  # noqa: E402


def one_line(text: str, limit: int = 140) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


class Viewer(wx.Frame):
    def __init__(self, folders=None):
        super().__init__(None, title="Claude add-in chats (read-only)", size=(1000, 720))
        self.folders = folders
        self.lib = officechats.Library()
        self.shown_chats: list[officechats.Chat] = []
        self.shown_messages: list[officechats.Message] = []
        self.query = ""
        p = wx.Panel(self)

        find_label = wx.StaticText(p, label="F&ind in all chats (Enter):")
        self.find = wx.TextCtrl(p, style=wx.TE_PROCESS_ENTER, name="Find in all chats")
        self.details = wx.CheckBox(p, label="Show t&hinking and tool calls")
        chats_label = wx.StaticText(p, label="&Chats:")
        self.chats = wx.ListBox(p, style=wx.LB_SINGLE, name="Chats")
        msgs_label = wx.StaticText(p, label="&Messages:")
        self.messages = wx.ListBox(p, style=wx.LB_SINGLE, name="Messages")
        text_label = wx.StaticText(p, label="Message &text:")
        self.text = wx.TextCtrl(p, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, name="Message text")
        self.copy_btn = wx.Button(p, label="C&opy message")
        self.save_btn = wx.Button(p, label="&Save chat as Markdown…")
        self.reload_btn = wx.Button(p, label="&Reload")
        self.status = self.CreateStatusBar()

        top = wx.BoxSizer(wx.HORIZONTAL)
        top.Add(find_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        top.Add(self.find, 1, wx.RIGHT, 12)
        top.Add(self.details, 0, wx.ALIGN_CENTER_VERTICAL)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for b in (self.copy_btn, self.save_btn, self.reload_btn):
            buttons.Add(b, 0, wx.RIGHT, 8)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(top, 0, wx.EXPAND | wx.ALL, 8)
        for label, ctrl, grow in ((chats_label, self.chats, 2), (msgs_label, self.messages, 3),
                                  (text_label, self.text, 4)):
            s.Add(label, 0, wx.LEFT | wx.RIGHT, 8)
            s.Add(ctrl, grow, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        s.Add(buttons, 0, wx.ALL, 8)
        p.SetSizer(s)
        # The frame already has its size, so nothing else would run the layout.
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(p, 1, wx.EXPAND)
        self.SetSizer(outer)
        self.Layout()

        self.find.Bind(wx.EVT_TEXT_ENTER, self.on_find)
        self.details.Bind(wx.EVT_CHECKBOX, self.on_details)
        self.chats.Bind(wx.EVT_LISTBOX, lambda e: self.show_chat())
        self.messages.Bind(wx.EVT_LISTBOX, lambda e: self.show_message())
        self.copy_btn.Bind(wx.EVT_BUTTON, lambda e: self.copy_message())
        self.save_btn.Bind(wx.EVT_BUTTON, lambda e: self.save_chat())
        self.reload_btn.Bind(wx.EVT_BUTTON, lambda e: self.reload())
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.reload()
        self.chats.SetFocus()

    def reload(self):
        with wx.BusyCursor():
            self.lib = officechats.load(self.folders)
        problems = sum(1 for s in self.lib.stores if s["stats"])
        extra = f", {problems} with records it couldn't read" if problems else ""
        self.status.SetStatusText(f"{len(self.lib.chats)} chats from {len(self.lib.stores)} {'store' if len(self.lib.stores) == 1 else 'stores'}{extra}")
        self.fill_chats()

    def fill_chats(self):
        want = self.details.GetValue()
        rows = []
        self.shown_chats = []
        for c in self.lib.chats:
            found = len(c.matching(self.query, want)) if self.query else 0
            if self.query and not found:
                continue
            self.shown_chats.append(c)
            bits = [c.title, c.app, officechats.date_text(c.updated),
                    f"{len(c.visible(False))} messages"]
            if self.query:
                bits.insert(1, f"{found} {'match' if found == 1 else 'matches'}")
            if c.older_store:
                bits.append("older store")
            rows.append(", ".join(bits))
        self.chats.Set(rows)
        if rows:
            self.chats.SetSelection(0)
        self.show_chat()

    def current_chat(self):
        i = self.chats.GetSelection()
        return self.shown_chats[i] if 0 <= i < len(self.shown_chats) else None

    def current_message(self):
        i = self.messages.GetSelection()
        return self.shown_messages[i] if 0 <= i < len(self.shown_messages) else None

    def show_chat(self):
        chat = self.current_chat()
        want = self.details.GetValue()
        if chat is None:
            self.shown_messages = []
        elif self.query:
            self.shown_messages = chat.matching(self.query, want)
        else:
            self.shown_messages = chat.visible(want)
        self.messages.Set([f"{m.speaker}: {one_line(m.text)}" for m in self.shown_messages])
        if self.shown_messages:
            self.messages.SetSelection(0)
        self.show_message()
        has_chat = chat is not None
        self.save_btn.Enable(has_chat)
        self.copy_btn.Enable(bool(self.shown_messages))

    def show_message(self):
        m = self.current_message()
        self.text.SetValue(f"{m.speaker}, {officechats.date_text(m.when, True)}\n\n{m.text}" if m else "")
        self.text.SetInsertionPoint(0)

    def on_details(self, _event):
        chosen = self.current_chat()
        self.fill_chats()
        if chosen in self.shown_chats:
            self.chats.SetSelection(self.shown_chats.index(chosen))
            self.show_chat()

    def on_find(self, _event):
        self.query = self.find.GetValue().strip()
        self.fill_chats()
        if self.query and not self.shown_chats:
            wx.MessageBox(f'No chats contain "{self.query}".', "Find", wx.OK | wx.ICON_INFORMATION, self)
            self.find.SetFocus()
            return
        self.chats.SetFocus()

    def clear_find(self):
        self.find.SetValue("")
        self.query = ""
        self.fill_chats()

    def copy_message(self):
        m = self.current_message()
        if m and wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(m.text))
                self.status.SetStatusText("Copied")
            finally:
                wx.TheClipboard.Close()

    def save_chat(self):
        chat = self.current_chat()
        if not chat:
            return
        with wx.FileDialog(self, "Save chat as Markdown", defaultFile=officechats.file_name(chat),
                           wildcard="Markdown (*.md)|*.md",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(officechats.to_markdown(chat, self.details.GetValue()))
        except OSError as e:
            wx.MessageBox(f"Couldn't save the chat: {e}", "Save", wx.OK | wx.ICON_ERROR, self)
            return
        self.status.SetStatusText(f"Saved {os.path.basename(path)}")

    def on_key(self, e):
        key = e.GetKeyCode()
        focus = wx.Window.FindFocus()
        if e.ControlDown() and key == ord("F"):
            self.find.SetFocus()
            self.find.SelectAll()
        elif e.ControlDown() and key == ord("S"):
            self.save_chat()
        elif key == wx.WXK_F5:
            self.reload()
        elif e.ControlDown() and key == ord("C") and focus is self.messages:
            self.copy_message()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and focus is self.chats and self.shown_messages:
            self.messages.SetFocus()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and focus is self.messages and self.shown_messages:
            self.text.SetFocus()
        elif key == wx.WXK_ESCAPE and focus is self.text:
            self.messages.SetFocus()
        elif key == wx.WXK_ESCAPE and focus is self.messages:
            self.chats.SetFocus()
        elif key == wx.WXK_ESCAPE and focus is self.find and self.find.GetValue():
            self.clear_find()
        else:
            e.Skip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", action="append")
    args = ap.parse_args()
    app = wx.App()
    Viewer(args.store).Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
