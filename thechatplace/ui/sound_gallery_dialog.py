"""File, Sounds, Browse Gallery: sound schemes others have made, checked before they're shown,
with Add to take one. Laid out and keyed like QuickMail's gallery, so the apps work alike
(app-kit's sounds/SCHEMES.md). The list and the downloads come in on a thread; the window never
waits on the network."""
from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Tuple

import wx
from theideaplace_appkit import soundgallery as shared

from .. import sound_events, sound_gallery
from .a11y import set_accessible_name, set_list_items_accessible
from .sizes import dialog_size, dip

LOADING = "Loading the sound scheme gallery…"
EMPTY = "The gallery has no schemes yet."


class SoundGalleryDialog(wx.Dialog):
    """Ends with OK once a scheme has been downloaded and checked: ``result`` is the entry and
    its bytes. ``make_gallery`` gives the gallery; ``say`` speaks."""

    def __init__(self, parent, schemes: List[sound_events.Scheme],
                 make_gallery: Callable[[], shared.Gallery], say: Callable[..., None]):
        super().__init__(parent, title="Sound Scheme Gallery", size=dialog_size(parent, 560, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._schemes = schemes
        self._make_gallery = make_gallery
        self._say = say
        self._entries: List[shared.Entry] = []
        self._generation = 0
        self._busy = False
        self.result: Optional[Tuple[shared.Entry, bytes]] = None

        outer = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(self, label=LOADING)
        outer.Add(self.status, 0, wx.EXPAND | wx.ALL, 10)
        outer.Add(wx.StaticText(self, label="Sound &schemes:"), 0, wx.LEFT | wx.RIGHT, 10)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Sound schemes")
        # Windows takes a list box's name from here, not from SetName (a11y.set_accessible_name).
        set_list_items_accessible(self.list, lambda: "Sound schemes", lambda index: None)
        outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        outer.Add(wx.StaticText(self, label="&Details:"), 0, wx.LEFT | wx.RIGHT, 10)
        # Read-only text, not a label, so it's in the Tab order and read by line.
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        set_accessible_name(self.details, "Details", read_only_box=True)
        self.details.SetMinSize(dip(self, -1, 70))
        outer.Add(self.details, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.add = wx.Button(self, label="&Add")
        row.Add(self.add, 0, wx.RIGHT, 6)
        self.reload = wx.Button(self, label="&Reload")
        row.Add(self.reload, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "Close"), 0)
        outer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CANCEL)

        self.list.Bind(wx.EVT_LISTBOX, lambda e: self._show())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_add())
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.add.Bind(wx.EVT_BUTTON, lambda e: self.on_add())
        self.reload.Bind(wx.EVT_BUTTON, lambda e: self.on_reload())
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._downloading = False
        self._show()
        self.reload.SetFocus()
        wx.CallAfter(lambda: self and self.load())

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self._generation += 1  # what's still coming in is dropped
        event.Skip()

    def _on_list_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_add()
            return
        event.Skip()

    # -- loading -------------------------------------------------------------------

    def _run(self, work: Callable[[], object], done: Callable[[object, Optional[BaseException]], None]) -> None:
        """``work`` on a thread, then ``done(result, problem)`` back here, unless the dialog has
        closed or something newer was started."""
        self._generation += 1
        generation = self._generation

        def thread():
            try:
                result, problem = work(), None
            except Exception as exc:  # noqa: BLE001 - said in the window
                result, problem = None, exc
            wx.CallAfter(self._finish, generation, done, result, problem)
        threading.Thread(target=thread, daemon=True, name="sound-gallery").start()

    def _finish(self, generation, done, result, problem) -> None:
        if not self or generation != self._generation:
            return
        done(result, problem)

    def on_reload(self) -> None:
        if self._downloading:  # a reload would drop the download without a word
            self._say("Wait for the download to finish.")
            return
        self.load()

    def load(self) -> None:
        """Reload, and the first load: the list, checked."""
        self._busy = True
        self._set_status(LOADING)
        self._say(LOADING, interrupt=False)
        self._show()
        self._run(lambda: self._make_gallery().schemes(), self._loaded)

    def _loaded(self, entries, problem) -> None:
        self._busy = False
        if problem is not None:
            self._entries = []
            self._fill()
            self._fail(problem)
            self.reload.SetFocus()
            return
        self._entries = list(entries)
        self._fill()
        if not self._entries:
            self._set_status(EMPTY)
            self._say(EMPTY)
            self.reload.SetFocus()
            return
        self._set_status(f"{len(self._entries)} scheme{'s' if len(self._entries) != 1 else ''}.")
        self.list.SetSelection(0)
        self._show()
        self.list.SetFocus()  # the row speaks for itself

    def _fail(self, problem: BaseException, entry: Optional[shared.Entry] = None) -> None:
        if isinstance(problem, shared.Unreachable) and entry is not None:
            # Reload reloads the list; it's Add that tries the download again.
            text = (f"{entry.name} couldn't be downloaded. Check your connection, then choose "
                    "Add again.")
        elif isinstance(problem, shared.GalleryProblem):
            text = str(problem)
        elif isinstance(problem, ValueError):  # the gallery's address, from --sound-gallery
            text = str(problem)
        else:
            logging.getLogger(__name__).error("The sound gallery failed", exc_info=problem)
            text = shared.UNREACHABLE
        self._set_status(text)
        self._say(text)

    def _set_status(self, text: str) -> None:
        self.status.SetLabel(text)
        self.Layout()

    def _fill(self) -> None:
        self.list.Set([sound_gallery.row_label(e, self._schemes) for e in self._entries])

    # -- the selected scheme ---------------------------------------------------------

    def selected(self) -> Optional[shared.Entry]:
        index = self.list.GetSelection()
        return self._entries[index] if 0 <= index < len(self._entries) else None

    def _show(self) -> None:
        entry = self.selected()
        self.details.SetValue(sound_gallery.details(entry) if entry else "")
        state = sound_gallery.state(entry, self._schemes) if entry else sound_gallery.NEW
        self.add.SetLabel("&Update" if state == sound_gallery.UPDATE else "&Add")
        self.add.Enable(entry is not None and not self._busy and state != sound_gallery.ADDED)
        self.Layout()

    def on_add(self) -> None:
        entry = self.selected()
        if entry is None or self._busy:
            return
        if sound_gallery.state(entry, self._schemes) == sound_gallery.ADDED:
            self._say(f"You've already added {entry.name}.")
            return
        # Focus off Add first: turning off the focused button leaves focus nowhere on Windows.
        if wx.Window.FindFocus() is self.add:
            self.list.SetFocus()
        self._busy = self._downloading = True
        self._show()
        text = f"Downloading {entry.name}…"
        self._set_status(text)
        self._say(text, interrupt=False)
        self._run(lambda: self._make_gallery().download(entry), lambda data, problem: self._downloaded(entry, data, problem))

    def _downloaded(self, entry: shared.Entry, data, problem) -> None:
        self._busy = self._downloading = False
        if problem is not None:
            self._show()
            self._fail(problem, entry)
            self.list.SetFocus()
            return
        self.result = (entry, data)
        if self.IsModal():
            self.EndModal(wx.ID_OK)
