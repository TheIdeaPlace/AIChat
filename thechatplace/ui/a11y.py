"""Screen reader naming helpers (from Image Description Toolkit's chat app)."""
from __future__ import annotations

import re

import wx

from .. import platform_paths
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
    shortcut of "Alt+ ". It has no shortcut of its own.

    While JAWS runs, its name ends with the line the caret is on (#763).
    JAWS 2027 reads no text at all as focus lands on a multi-line box, plain
    or rich, editable or not, whatever its value, description or role says:
    only "Details, read only edit" (seen in the test VM). It does read the
    name, so the line goes there, as NVDA reads it itself. Not for NVDA,
    which would read the line twice."""

    def GetName(self, childId):
        status, label = super().GetName(childId)
        if childId or not platform_paths.jaws_running():
            return (status, label)
        line = current_line(self.GetWindow())
        return (status, f"{label}, {line}" if line else label)

    def GetKeyboardShortcut(self, childId):
        if childId:
            return (wx.ACC_NOT_IMPLEMENTED, "")
        return (wx.ACC_OK, "")


def current_line(box) -> str:
    """The line the caret is in, in a text box, stripped: all of it, from
    one line break to the next, however the box wraps it. "" if it's blank
    or can't be told.

    Not the line as shown: as Claude's permission dialog opened, JAWS asked
    before the box had its width, and the shown line was "Claude wants to"
    (#763, in the test VM)."""
    try:
        # GetRange gives the text with "\n" breaks, whatever the box counts
        # a break as, so its length is the caret's place in GetValue.
        before = box.GetRange(0, box.GetInsertionPoint())
        text = box.GetValue()
        start = before.rfind("\n") + 1
        end = text.find("\n", len(before))
        return text[start:end if end >= 0 else len(text)].strip()
    except Exception:  # noqa: BLE001 - never break a screen reader's question
        return ""


def give_focus(window) -> None:
    """Put the keyboard focus on ``window``, unless it's there already.

    Windows often has put it there by itself: on a dialog's first control as
    the dialog opens, and back where it was as a message box closes. Setting
    it again had screen readers read the control twice: NVDA said Connected
    Tools' first row twice (#468, #729). Every "focus goes here" in a dialog
    goes through this, so none of them can.
    """
    if window and wx.Window.FindFocus() is not window:
        window.SetFocus()


def give_focus_later(window) -> None:
    """``give_focus`` once the events already queued have run: from a
    dialog's constructor, after Windows has given the dialog its first focus
    as it opens. A window destroyed meanwhile is left alone."""
    wx.CallAfter(give_focus, window)


def set_accessible_name(control: wx.Window, label: str, read_only_box: bool = False) -> None:
    """Name a control for JAWS and NVDA. ``read_only_box``: it's a read-only
    box of text, which mustn't be parsed for an access key.

    ``SetName`` doesn't reach MSAA. Text controls, check boxes and choice
    lists also get a wx.Accessible, so their name does. A list box on Windows
    gets a ``ListAccessible`` that leaves its name to Windows (from the label
    before it) but reads its row once and says when it's empty (#647, #648);
    ``set_list_items_accessible`` gives one a name of its own.

    On macOS neither reaches VoiceOver, so the name goes on the native view
    as well (``mac_a11y``), and on Linux on the GTK widget, for Orca
    (``gtk_a11y``).
    """
    control.SetName(label)
    mac_a11y.set_label(control, label)
    gtk_a11y.set_label(control, label)
    _tie_label_before(control, label)
    if mac_a11y.IS_MACOS and isinstance(control, wx.SpinCtrl):
        # On a Mac a number box is a text box and a stepper of wx's own, and
        # Tab stops on each: VoiceOver reads the part, so both are named.
        # The stepper was read as just "stepper" (#726). Windows' has no parts.
        for part in control.GetChildren():
            if isinstance(part, (wx.TextCtrl, wx.SpinButton)):
                set_accessible_name(part, label)
    if isinstance(control, wx.ListBox):
        if wx.Platform == "__WXMSW__":
            _list_accessible(control)
        return
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


#: What a list reads while it has no rows, when it has no words of its own (#648).
EMPTY_LIST_TEXT = "No items"


class ListAccessible(wx.Accessible):
    """Every list box's accessible on Windows: one row read once, and an empty list read as
    empty (#616, #621, #647, #648).

    - **Focus** (#616, #647). Tabbing to a list box, Windows reports focus on the list, then on
      its row, and NVDA reads the row for each: on the first it asks the list which row has
      focus. While the list has focus, this says the list itself has it, which leaves the row
      to the second report, so it's read once.
    - **Empty** (#621, #648). An empty Windows list box still reports focus on its row 1, which
      doesn't exist, and says row 1 has focus when asked. Every question about that row
      failed, so NVDA read it as "unknown". While the list is empty, row 1 answers as a list
      item named ``empty_text``, with the list's own rectangle.
    - **Rows** (#11). ``item_text(index)``, when given, is the text a screen reader should read
      for that row, or None for the row's own text, as Image Description Toolkit's
      DescriptionListBox does.
    - **Name.** ``name()``, when given, is the list's own name (the messages list's says the
      session and its state). Without it Windows names the list from the label before it
      ("Session list:", with its Alt+L).

    Both callables are asked each time, so the answers follow the list as it changes.
    Everything else (child count, the rows' roles and states) is left to Windows.
    """

    def __init__(self, window: wx.Window, name=None, item_text=None,
                 empty_text: str = EMPTY_LIST_TEXT):
        super().__init__(window)
        self._name = name
        self._item_text = item_text
        self._empty_text = empty_text

    def _empty(self) -> bool:
        window = self.GetWindow()
        return bool(window) and window.GetCount() == 0

    def _has_focus(self) -> bool:
        window = self.GetWindow()
        return bool(window) and wx.Window.FindFocus() is window

    def _empty_row(self, childId) -> bool:
        return childId == 1 and self._empty()

    def GetName(self, childId):
        if not childId:
            if self._name is None:
                return (wx.ACC_NOT_IMPLEMENTED, "")
            return (wx.ACC_OK, self._name())
        if self._empty_row(childId):
            return (wx.ACC_OK, self._empty_text)
        try:
            # childId is 1-based for items.
            text = self._item_text(childId - 1) if self._item_text else None
        except Exception:  # noqa: BLE001 - never break the screen reader
            text = None
        if text:
            return (wx.ACC_OK, text)
        # The row's own text. The name must be a string even here: with None,
        # wxPython can't build the answer and the screen reader gets an error
        # (0x80010105) instead of the row, found reading it from another
        # process the way JAWS and NVDA do.
        return (wx.ACC_NOT_IMPLEMENTED, "")

    def GetRole(self, childId):
        if self._empty_row(childId):
            return (wx.ACC_OK, wx.ROLE_SYSTEM_LISTITEM)
        return (wx.ACC_NOT_IMPLEMENTED, wx.ROLE_NONE)

    def GetState(self, childId):
        if self._empty_row(childId):
            return (wx.ACC_OK, wx.ACC_STATE_SYSTEM_FOCUSED if self._has_focus() else 0)
        return (wx.ACC_NOT_IMPLEMENTED, 0)

    def GetLocation(self, elementId):
        """The empty row is where the list is, so a screen reader's "where is this" has an
        answer (Windows has none for a row that doesn't exist). Screen coordinates, as MSAA's
        accLocation wants."""
        if self._empty_row(elementId):
            return (wx.ACC_OK, self.GetWindow().GetScreenRect())
        return (wx.ACC_NOT_IMPLEMENTED, wx.Rect())

    def GetFocus(self):
        """The list itself while it has focus, not its row (see the class docstring).

        wxPython calls this with no arguments and takes (status, child id, child accessible),
        whatever its docstring says; the accessible itself is what wx gives as CHILDID_SELF.
        Checked through oleacc's accFocus in the test VM: 0 with focus, the row's id without."""
        if self._has_focus():
            return (wx.ACC_OK, 0, self)
        return (wx.ACC_NOT_IMPLEMENTED, 0, None)


def _list_accessible(listbox: wx.ListBox):
    """``listbox``'s ListAccessible, made the first time it's asked for (None where wx has no
    accessibles). One per list, so the empty text, the name and the rows' text can each be set
    in any order without undoing the others."""
    existing = getattr(listbox, "_hub_accessible", None)
    if isinstance(existing, ListAccessible):
        return existing
    try:
        accessible = ListAccessible(listbox)
        listbox.SetAccessible(accessible)
    except (NotImplementedError, AttributeError):
        return None
    listbox._hub_accessible = accessible  # keep it alive
    return accessible


def set_empty_list_text(listbox: wx.ListBox, empty_text: str) -> None:
    """What a screen reader reads on ``listbox`` while it has no rows (#621, #648), in place of
    "No items". Windows only: the missing row is the Windows list box's."""
    if wx.Platform != "__WXMSW__":
        return
    accessible = _list_accessible(listbox)
    if accessible is not None:
        accessible._empty_text = empty_text


def announce_list_emptied(listbox: wx.ListBox) -> None:
    """Tell the screen reader that ``listbox``, which has focus, has just lost its last row
    (#649). Windows says nothing when that happens, so NVDA stayed on the row that had gone;
    this reports focus on the empty row, which reads as the list's empty text. Windows only."""
    if wx.Platform != "__WXMSW__" or not listbox or listbox.GetCount():
        return
    try:
        wx.Accessible.NotifyEvent(wx.ACC_EVENT_OBJECT_FOCUS, listbox, wx.OBJID_CLIENT, 1)
    except (NotImplementedError, AttributeError):
        pass


def set_list_items_accessible(listbox: wx.ListBox, name, item_text,
                              empty_text: str = "") -> None:
    """The list's name, ``name()``, and each row's text, ``item_text(index)`` (see
    ``ListAccessible``); ``empty_text``, if given, for ``set_empty_list_text``."""
    if gtk_a11y.IS_LINUX:
        _describe_rows(listbox, name, item_text)
        return
    if mac_a11y.IS_MACOS:
        _read_rows_on_mac(listbox, item_text)
        return
    accessible = _list_accessible(listbox)
    if accessible is None:
        return
    accessible._name = name
    accessible._item_text = item_text
    if empty_text:
        accessible._empty_text = empty_text


def _read_rows_on_mac(listbox: wx.ListBox, item_text) -> None:
    """macOS (#11): VoiceOver reads each row as ``item_text`` says, the whole
    message in the messages lists, where it read only the row's first line
    (see ``mac_a11y.set_row_text``). The list's name is ``set_accessible_name``'s."""
    if not mac_a11y.set_row_text(listbox, item_text):
        return

    def forget(event):
        if event.GetEventObject() is listbox:
            mac_a11y.set_row_text(listbox, None)
        event.Skip()
    listbox.Bind(wx.EVT_WINDOW_DESTROY, forget)


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
