"""Screen reader naming helpers (from Image Description Toolkit's chat app)."""
from __future__ import annotations

import re

import wx

from . import gtk_a11y, mac_a11y


class _NamedAccessible(wx.Accessible):
    """Give a text control a name that reaches MSAA.

    childId 0 is the control itself; children (list items) defer to wx so a
    screen reader reads the item, not the control's label, on every arrow.
    """

    def __init__(self, window: wx.Window, label: str):
        super().__init__(window)
        self._label = label

    def set_label(self, label: str) -> None:
        self._label = label

    def GetName(self, childId):
        if childId:
            return (wx.ACC_NOT_IMPLEMENTED, "")
        return (wx.ACC_OK, self._label)


class _ReadOnlyBoxAccessible(_NamedAccessible):
    """A read-only box holds text to read, which isn't a label: Windows took
    the "&" in a server's "Pick & choose" for an access key and reported a
    shortcut of "Alt+ ". It has no shortcut of its own."""

    def GetKeyboardShortcut(self, childId):
        if childId:
            return (wx.ACC_NOT_IMPLEMENTED, "")
        return (wx.ACC_OK, "")


def set_accessible_name(control: wx.Window, label: str, read_only_box: bool = False) -> None:
    """Name a control for JAWS and NVDA. ``read_only_box``: it's a read-only
    box of text, which mustn't be parsed for an access key.

    ``SetName`` doesn't reach MSAA. Text controls, check boxes and choice
    lists also get a wx.Accessible, so their name does. List boxes don't get
    one here: the messages list has its own (``set_list_items_accessible``),
    which keeps the list's name and its rows' text.

    On macOS neither reaches VoiceOver, so the name goes on the native view
    as well (``mac_a11y``), and on Linux on the GTK widget, for Orca
    (``gtk_a11y``).
    """
    control.SetName(label)
    mac_a11y.set_label(control, label)
    gtk_a11y.set_label(control, label)
    _tie_label_before(control, label)
    # A combo box too (#154): Windows names one by the text in it, so the
    # Folder box was read as its path rather than as Folder. A check box or a
    # choice list named more fully than its visible label (#468): what a form
    # says about being required, or its description, was otherwise lost.
    if not isinstance(control, (wx.TextCtrl, wx.ComboBox, wx.CheckBox, wx.Choice)):
        return
    existing = getattr(control, "_hub_accessible", None)
    if existing is not None:
        existing.set_label(label)
        return
    try:
        accessible = (_ReadOnlyBoxAccessible if read_only_box else _NamedAccessible)(control, label)
        control.SetAccessible(accessible)
        control._hub_accessible = accessible  # keep it alive
    except (NotImplementedError, AttributeError):
        pass


def _msaa_name(control: wx.Window):
    """The name a screen reader gets for ``control`` through the accessible this module gave it,
    or None if it has none (Windows then names it from the label before it)."""
    accessible = getattr(control, "_hub_accessible", None)
    if accessible is None:
        return None
    try:
        status, name = accessible.GetName(0)
    except Exception:  # noqa: BLE001 - never break the screen reader
        return None
    return name if status == wx.ACC_OK and name else None


class _LabelAccessible(wx.Accessible):
    """A visible label whose name is its control's (#616); see ``tie_label``."""

    def __init__(self, label: wx.Window, control: wx.Window):
        super().__init__(label)
        self._control = control

    def GetName(self, childId):
        name = _msaa_name(self._control) if not childId and self._control else None
        if name:
            return (wx.ACC_OK, name)
        return (wx.ACC_NOT_IMPLEMENTED, "")


def tie_label(label: wx.StaticText, control: wx.Window) -> None:
    """Keep NVDA from reading ``label`` as loose dialog text when the dialog opens (#616).

    NVDA reads a dialog's static texts out when it opens, all as one block, except a text
    whose name is the same as that of the control straight after it (its label, NVDA
    decides). A control named here more fully than its label ("Volume" for "&Volume:", a list
    whose name has its count) breaks that match, so the label answers with the control's
    name, asked each time so it follows a name that changes. Create ``label`` just before
    ``control``: the match is with the next window in tab order. What it shows doesn't change.

    Windows only: VoiceOver and Orca don't read dialogs this way, and their names are left
    alone.
    """
    if wx.Platform != "__WXMSW__":
        return
    existing = getattr(label, "_hub_accessible", None)
    if isinstance(existing, _LabelAccessible):
        existing._control = control
        return
    try:
        accessible = _LabelAccessible(label, control)
        label.SetAccessible(accessible)
        label._hub_accessible = accessible  # keep it alive
    except (NotImplementedError, AttributeError):
        pass


def _words(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def is_label_for(text: str, name: str) -> bool:
    """Whether a visible label ``text`` ("&Volume:", "Sound events (3 of 23 ...):") says what a
    control's accessible ``name`` ("Volume", "Sound events, 3 of 23 ...") says, or begins it, so
    it is that control's label rather than a note before it. Not a label that says more than the
    name ("Role, such as Build the fix:" for "Role"): tied, its extra words would be lost."""
    label, full = _words(text.replace("&", "")), _words(name)
    return bool(label) and full.startswith(label)


def _tie_label_before(control: wx.Window, name: str) -> None:
    """``tie_label`` for the static text just before ``control``, when it is its label (#616).
    Every dialog that names its controls with ``set_accessible_name`` gets this, so NVDA doesn't
    read their labels out in a block as each dialog opens."""
    if wx.Platform != "__WXMSW__":
        return
    try:
        before = control.GetPrevSibling()
    except (AttributeError, RuntimeError):
        return
    if isinstance(before, wx.StaticText) and is_label_for(before.GetLabelText(), name):
        tie_label(before, control)


class ListItemsAccessible(wx.Accessible):
    """A list box whose items tell the screen reader more than they show
    (#11), as Image Description Toolkit's DescriptionListBox does.

    ``name()`` gives the list's own name (it changes: the messages list's
    name says the session and its state). ``item_text(index)`` gives the text
    a screen reader should read for that row, or None for the row's own text.
    Both are asked each time, so the answer follows the list as it changes.
    Everything else (role, state, child count) is left to wx.
    """

    def __init__(self, window: wx.Window, name, item_text):
        super().__init__(window)
        self._name = name
        self._item_text = item_text

    def GetName(self, childId):
        if not childId:
            return (wx.ACC_OK, self._name())
        try:
            text = self._item_text(childId - 1)  # childId is 1-based for items
        except Exception:  # noqa: BLE001 - never break the screen reader
            text = None
        if text:
            return (wx.ACC_OK, text)
        # The row's own text. The name must be a string even here: with None,
        # wxPython can't build the answer and the screen reader gets an error
        # (0x80010105) instead of the row, found reading it from another
        # process the way JAWS and NVDA do.
        return (wx.ACC_NOT_IMPLEMENTED, "")

    def GetFocus(self):
        """While the list has focus, say the list itself has it, not its row (#616).

        Tabbing to a list box, Windows reports focus on the list, then on its row. NVDA reads
        the row for each when they reach it apart, as they do from a wx dialog: on the first it
        asks the list which row has focus and reads that row, then the second reads it again.
        Answering "the list" leaves the row to the second report, so it's read once. A list
        without focus answers as Windows would.

        wxPython calls this with no arguments and takes (status, child id, child accessible),
        whatever its docstring says; the accessible itself is what wx gives as CHILDID_SELF.
        Checked through oleacc's accFocus in the test VM: 0 with focus, the row's id without."""
        window = self.GetWindow()
        if window and wx.Window.FindFocus() is window:
            return (wx.ACC_OK, 0, self)
        return (wx.ACC_NOT_IMPLEMENTED, 0, None)


def set_list_items_accessible(listbox: wx.ListBox, name, item_text) -> None:
    if gtk_a11y.IS_LINUX:
        _describe_rows(listbox, name, item_text)
        return
    try:
        accessible = ListItemsAccessible(listbox, name, item_text)
        listbox.SetAccessible(accessible)
        listbox._hub_accessible = accessible  # keep it alive
    except (NotImplementedError, AttributeError):
        pass


def _describe_rows(listbox: wx.ListBox, name, item_text) -> None:
    """Linux (#11): Orca reads a row's own text, then its description, so
    the row you're on gets the rest of what ``item_text`` says. Set as you
    arrive on it (selection or focus), before Orca asks; GTK makes a row's
    accessible only when it's needed, so doing every row ahead would make
    them all. ``refresh_list_item`` does it after the list changes, and
    keeps the list's own name, ``name()``, as it is now ("Prompts, 3")."""
    def describe(event=None):
        if event is not None:
            event.Skip()
        refresh_list_item(listbox)

    listbox._hub_list_name = name
    listbox._hub_item_text = item_text
    gtk_a11y.set_label(listbox, name())
    listbox.Bind(wx.EVT_LISTBOX, describe)
    listbox.Bind(wx.EVT_SET_FOCUS, describe)


def refresh_list_item(listbox: wx.ListBox) -> None:
    """The selected row's description, and the list's name, after the list
    or the row changed under it (Linux; nothing elsewhere)."""
    item_text = getattr(listbox, "_hub_item_text", None)
    if item_text is None or not listbox:
        return
    try:
        gtk_a11y.set_label(listbox, listbox._hub_list_name())
    except Exception:  # noqa: BLE001 - never break the screen reader
        pass
    index = listbox.GetSelection()
    if index == wx.NOT_FOUND:
        return
    try:
        full = item_text(index)
    except Exception:  # noqa: BLE001 - never break the screen reader
        full = None
    rest = gtk_a11y.rest_of(listbox.GetString(index), full) if full else ""
    gtk_a11y.set_row_description(listbox, index, rest)


def set_voiceover_menu(listbox: wx.ListBox, open_menu) -> None:
    """Open `listbox`'s context menu with VoiceOver's VO+Shift+M, which wx
    doesn't connect to EVT_CONTEXT_MENU on a Mac. ``open_menu()`` is called
    as for the Applications key, with no event, and after VoiceOver's request
    has been answered (see ``mac_a11y.set_show_menu``). Nothing elsewhere."""
    def open_if_alive():
        # The window can close between VO+Shift+M and this call, and a menu
        # for a deleted list would end in a RuntimeError.
        if listbox:
            open_menu()

    if not mac_a11y.set_show_menu(listbox, lambda: wx.CallAfter(open_if_alive)):
        return

    def forget(event):
        if event.GetEventObject() is listbox:
            mac_a11y.set_show_menu(listbox, None)
        event.Skip()
    listbox.Bind(wx.EVT_WINDOW_DESTROY, forget)
