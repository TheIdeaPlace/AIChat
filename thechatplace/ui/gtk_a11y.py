"""Name controls for Orca, which wx can't do on Linux, and make announcements
through Orca from the app's own window.

Measured with wxPython 4.2.3 / wxWidgets 3.2.7 (Debian 13) and Orca 48:
``SetName()`` reaches nothing native and ``SetAccessible()`` raises
``NotImplementedError`` (``wx.Accessible`` is Windows-only). wxGTK does link
a label to the control after it (``labelled-by``), but to the control's outer
widget. A ``wx.ListBox`` is a ``GtkScrolledWindow`` around a ``GtkTreeView``,
and a multi-line ``wx.TextCtrl`` one around a ``GtkTextView``; Orca lands on
the inner one, which had no name, so the session list was read as "table
with 0 rows 1 column". A control drawn by wx itself (the status bar's text
parts) was read as "panel".

So, as ``mac_a11y`` does for VoiceOver, the name goes on the native widget,
here through ATK (GTK 3's accessibility layer, which at-spi2 hands to Orca),
with ctypes rather than PyGObject, which isn't a dependency. The widget is
``GetGtkWidget()``, not ``GetHandle()``: on GTK that's an X window id, and
0 until the window is shown.

``announce`` sends text to Orca as an ATK notification (Orca 45.2 and later
present it). Orca only presents those from the active window, so the main
window uses it (for orca.py) only while The Chat Place is active.

A list row's accessible name doesn't help (#11): Orca 48 reads a row's
displayed text. It reads the row's description after it, so the messages
list's rows get the rest of the message there (``set_row_description``).
See dev-notes/linux-port.md.

This module doesn't import wx, so its logic can be tested anywhere, and
everything in it is a no-op off Linux or without GTK 3.
"""
from __future__ import annotations

import ctypes
import re
import sys

IS_LINUX = sys.platform.startswith("linux")

_libs = None  # (gtk, atk, gobject, glib), or False once loading has failed


class _GList(ctypes.Structure):
    """GLib's doubly linked list, as GTK hands back children and labels."""


_GList._fields_ = [("data", ctypes.c_void_p), ("next", ctypes.POINTER(_GList)),
                   ("prev", ctypes.POINTER(_GList))]


def _load():
    """GTK 3, ATK, GObject and GLib, or None. Loaded by soname, so a built
    app gets the copies wx already loaded."""
    global _libs
    if _libs is None:
        _libs = False
        if IS_LINUX:
            try:
                gtk = ctypes.CDLL("libgtk-3.so.0")
                atk = ctypes.CDLL("libatk-1.0.so.0")
                gobject = ctypes.CDLL("libgobject-2.0.so.0")
                glib = ctypes.CDLL("libglib-2.0.so.0")
            except OSError:
                return None
            vp, cp, i, b = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_bool
            gtk.gtk_widget_get_accessible.restype = vp
            gtk.gtk_widget_get_accessible.argtypes = [vp]
            gtk.gtk_scrolled_window_get_type.restype = ctypes.c_size_t
            gtk.gtk_bin_get_child.restype = vp
            gtk.gtk_bin_get_child.argtypes = [vp]
            gtk.gtk_container_get_type.restype = ctypes.c_size_t
            gtk.gtk_container_get_children.restype = vp
            gtk.gtk_container_get_children.argtypes = [vp]
            gtk.gtk_widget_grab_focus.argtypes = [vp]
            gtk.gtk_widget_list_mnemonic_labels.restype = vp
            gtk.gtk_widget_list_mnemonic_labels.argtypes = [vp]
            gtk.gtk_widget_remove_mnemonic_label.argtypes = [vp, vp]
            glib.g_list_free.argtypes = [vp]
            gobject.g_type_check_instance_is_a.restype = b
            gobject.g_type_check_instance_is_a.argtypes = [vp, ctypes.c_size_t]
            gobject.g_signal_lookup.restype = ctypes.c_uint
            gobject.g_signal_lookup.argtypes = [cp, ctypes.c_size_t]
            atk.atk_object_set_name.argtypes = [vp, cp]
            atk.atk_object_set_description.argtypes = [vp, cp]
            atk.atk_object_get_description.restype = cp
            atk.atk_object_get_description.argtypes = [vp]
            atk.atk_object_get_name.restype = cp
            atk.atk_object_get_name.argtypes = [vp]
            atk.atk_object_set_role.argtypes = [vp, i]
            atk.atk_object_get_role.restype = i
            atk.atk_object_get_role.argtypes = [vp]
            atk.atk_role_for_name.restype = i
            atk.atk_role_for_name.argtypes = [cp]
            atk.atk_object_get_type.restype = ctypes.c_size_t
            atk.atk_table_ref_at.restype = vp
            atk.atk_table_ref_at.argtypes = [vp, i, i]
            atk.atk_table_get_type.restype = ctypes.c_size_t
            gobject.g_object_unref.argtypes = [vp]
            # Variadic: its arguments are given types at each call.
            gobject.g_signal_emit_by_name.restype = None
            _libs = (gtk, atk, gobject, glib)
    return _libs or None


def _list_items(glist) -> list:
    """The pointers in a GList GTK handed over, which is then freed (its
    items belong to GTK)."""
    items = []
    node = ctypes.cast(glist, ctypes.POINTER(_GList))
    while node:
        items.append(node.contents.data)
        node = node.contents.next
    if glist:
        _load()[3].g_list_free(glist)
    return items


def _widget(window):
    """`window`'s GtkWidget pointer, or None."""
    try:
        pointer = window.GetGtkWidget()
    except (AttributeError, RuntimeError, NotImplementedError):
        return None
    try:
        value = int(pointer)
    except (TypeError, ValueError):
        return None
    return ctypes.c_void_p(value) if value else None


def _target(window):
    """The widget Orca lands on: the one inside a scrolled window (a list's
    tree view, a multi-line text box's text view), else the widget itself."""
    libs = _load()
    widget = _widget(window) if libs else None
    if widget is None:
        return None
    gtk, _atk, gobject, _glib = libs
    if gobject.g_type_check_instance_is_a(widget, gtk.gtk_scrolled_window_get_type()):
        inner = gtk.gtk_bin_get_child(widget)
        if inner:
            return ctypes.c_void_p(inner)
    return widget


def _accessible(window, inner: bool = True):
    libs = _load()
    widget = _target(window) if inner else (_widget(window) if libs else None)
    if widget is None:
        return None
    accessible = libs[0].gtk_widget_get_accessible(widget)
    return ctypes.c_void_p(accessible) if accessible else None


def set_label(window, label: str) -> bool:
    """Give `window` the name Orca reads. True if it was applied. False,
    never an exception, off Linux or without a native widget."""
    if not IS_LINUX or not label:
        return False
    try:
        accessible = _accessible(window)
        if accessible is None:
            return False
        atk = _load()[1]
        if (atk.atk_object_get_name(accessible) or b"").decode("utf-8", "replace") != label:
            atk.atk_object_set_name(accessible, label.encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001 - never break the window over a name
        return False


def get_label(window) -> str | None:
    """The name Orca would read for `window`, or None. For tests."""
    if not IS_LINUX:
        return None
    try:
        accessible = _accessible(window)
        if accessible is None:
            return None
        name = _load()[1].atk_object_get_name(accessible)
        return name.decode("utf-8", "replace") if name is not None else None
    except Exception:  # noqa: BLE001
        return None


def set_role(window, role: str) -> bool:
    """Give `window` an ATK role by ATK's name for it ("label",
    "statusbar"). For controls wx draws itself, which GTK calls "panel"."""
    if not IS_LINUX:
        return False
    try:
        accessible = _accessible(window, inner=False)
        if accessible is None:
            return False
        atk = _load()[1]
        value = atk.atk_role_for_name(role.encode("ascii"))
        if value <= 0:
            return False
        atk.atk_object_set_role(accessible, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def get_role(window) -> int | None:
    """The ATK role number, for tests."""
    if not IS_LINUX:
        return None
    try:
        accessible = _accessible(window, inner=False)
        return None if accessible is None else _load()[1].atk_object_get_role(accessible)
    except Exception:  # noqa: BLE001
        return None


def role_number(role: str) -> int | None:
    libs = _load()
    return libs[1].atk_role_for_name(role.encode("ascii")) if libs else None


def forget_labels(window) -> int:
    """Take away the labels wxGTK gave `window` on its own. When a control
    is made, wxGTK makes the label of the control before it (a static text,
    a group box, a radio box) its label, which is right after a static text
    but not after a radio box: Settings' first check box was read as
    "Announcements, Announce when any listed session finishes a turn". The
    control keeps its own name. Returns how many were removed."""
    if not IS_LINUX:
        return 0
    try:
        libs = _load()
        widget = _widget(window) if libs else None
        if widget is None:
            return 0
        gtk = libs[0]
        found = _list_items(gtk.gtk_widget_list_mnemonic_labels(widget))
        for label in found:
            gtk.gtk_widget_remove_mnemonic_label(widget, ctypes.c_void_p(label))
        return len(found)
    except Exception:  # noqa: BLE001
        return 0


#: ATK's AtkLive values, the "notification" signal's politeness.
_LIVE_POLITE, _LIVE_ASSERTIVE = 1, 2


def announce(window, text: str, interrupt: bool = True) -> bool:
    """Send `text` to Orca as an announcement from `window` (Orca 45.2 and
    later present it, in speech and braille). True if it was sent.

    ATK 2.50's "notification" signal, which carries a politeness: assertive
    for an announcement, which cuts off what Orca is saying, polite for a
    confirmation, which waits. Older ATK has only "announcement". Orca
    presents these only from the active window, so the caller checks that.
    Call on the UI thread."""
    if not IS_LINUX or not text:
        return False
    try:
        accessible = _accessible(window, inner=False)
        if accessible is None:
            return False
        _gtk, atk, gobject, _glib = _load()
        data = text.encode("utf-8")
        if gobject.g_signal_lookup(b"notification", atk.atk_object_get_type()):
            live = _LIVE_ASSERTIVE if interrupt else _LIVE_POLITE
            gobject.g_signal_emit_by_name(accessible, b"notification", ctypes.c_char_p(data),
                                          ctypes.c_int(live))
            return True
        if gobject.g_signal_lookup(b"announcement", atk.atk_object_get_type()):
            gobject.g_signal_emit_by_name(accessible, b"announcement", ctypes.c_char_p(data))
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def set_row_description(listbox, row: int, text: str) -> bool:
    """What Orca reads after row `row`'s own text (#11). Orca reads a list
    row's displayed text, not its accessible name, but it does read the
    description after it, so a row that shows a message's first line gets
    the rest of the message here. "" removes it."""
    if not IS_LINUX or row < 0:
        return False
    try:
        accessible = _accessible(listbox)
        if accessible is None:
            return False
        _gtk, atk, gobject, _glib = _load()
        if not gobject.g_type_check_instance_is_a(accessible, atk.atk_table_get_type()):
            return False
        cell = atk.atk_table_ref_at(accessible, row, 0)
        if not cell:
            return False
        cell = ctypes.c_void_p(cell)
        try:
            if (atk.atk_object_get_description(cell) or b"").decode("utf-8", "replace") != text:
                atk.atk_object_set_description(cell, text.encode("utf-8"))
        finally:
            gobject.g_object_unref(cell)
        return True
    except Exception:  # noqa: BLE001
        return False


def get_row_description(listbox, row: int) -> str | None:
    """Row `row`'s description, for tests."""
    if not IS_LINUX:
        return None
    try:
        accessible = _accessible(listbox)
        if accessible is None:
            return None
        _gtk, atk, gobject, _glib = _load()
        cell = atk.atk_table_ref_at(accessible, row, 0)
        if not cell:
            return None
        cell = ctypes.c_void_p(cell)
        try:
            text = atk.atk_object_get_description(cell)
        finally:
            gobject.g_object_unref(cell)
        return text.decode("utf-8", "replace") if text is not None else None
    except Exception:  # noqa: BLE001
        return None


def rest_of(shown: str, full: str) -> str:
    """What `full` says beyond `shown`, the row's own text, so Orca doesn't
    read the first line twice. Matched word by word, since the row shows
    the first line as written ("Claude: ## Plan") and `full` is it as words
    ("Claude: Plan. ..."). All of `full` when it doesn't start with the
    row's words. Pure, for tests."""
    full = full.strip()
    shortened = shown.rstrip().endswith("…")  # a long first line, cut mid-word
    shown_words = [w.lower() for w in re.findall(r"\w+", shown.rstrip().rstrip("…"))]
    if not full or not shown_words:
        return full
    matches = list(re.finditer(r"\w+", full))

    def same(index: int, match) -> bool:
        word = match.group(0).lower()
        if shortened and index == len(shown_words) - 1:
            return word.startswith(shown_words[index])
        return word == shown_words[index]
    if len(matches) < len(shown_words) or not all(
            same(i, m) for i, m in enumerate(matches[:len(shown_words)])):
        return full
    last = matches[len(shown_words) - 1]
    # A word the "…" cut starts the rest, so it's spoken whole; a "…" after a
    # whole word (the cut fell on a word boundary, or the line ends in one)
    # cuts nothing, and the rest starts after the word.
    cut = shortened and len(last.group(0)) > len(shown_words[-1])
    start = last.start() if cut else last.end()
    return full[start:].lstrip(" .,;:!?—-…)]*_`").strip()


def _webkit_view_type(gobject):
    """WebKitGTK's WebKitWebView type, from the WebKitGTK wx already loaded.
    Loading one by name could bring in a second, incompatible WebKitGTK
    (libsoup 2 and 3 together, or GTK 4 into GTK 3), which aborts."""
    gobject.g_type_from_name.restype = ctypes.c_size_t
    gobject.g_type_from_name.argtypes = [ctypes.c_char_p]
    return gobject.g_type_from_name(b"WebKitWebView") or None


def focus_web_view(window) -> bool:
    """Put the focus in the web page of a ``wx.html2.WebView`` (#190 on
    Linux). wx's SetFocus focuses the scrolled window it wraps WebKitGTK's
    view in, and Orca read that as "scroll pane", greyed, never reaching
    the page. True if the page has the focus."""
    if not IS_LINUX:
        return False
    try:
        libs = _load()
        top = _widget(window) if libs else None
        if top is None:
            return False
        gtk, _atk, gobject, _glib = libs
        view_type = _webkit_view_type(gobject)
        if not view_type:
            return False

        def find(widget, depth=0):
            if gobject.g_type_check_instance_is_a(widget, view_type):
                return widget
            if depth > 8 or not gobject.g_type_check_instance_is_a(
                    widget, gtk.gtk_container_get_type()):
                return None
            for child in _list_items(gtk.gtk_container_get_children(widget)):
                found = find(ctypes.c_void_p(child), depth + 1)
                if found is not None:
                    return found
            return None

        view = find(top)
        if view is None:
            return False
        gtk.gtk_widget_grab_focus(view)
        return True
    except Exception:  # noqa: BLE001
        return False
