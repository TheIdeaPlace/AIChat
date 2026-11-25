"""Name controls for VoiceOver, which wx can't do on macOS (from Image
Description Toolkit's shared/mac_accessibility.py), make the app active
so VoiceOver finds its menu bar (``activate_app``), and keep Command+letter
off wx's buttons (``stop_button_command_keys``, #675).

Measured on wxPython 4.3.1 / wxWidgets 3.3.3: ``SetAccessible()`` raises
``NotImplementedError`` (``wx.Accessible`` is Windows-only), and ``SetName()``
reaches nothing native. Every ``wx.TextCtrl``, ``wx.ListBox`` and ``wx.Choice``
reports ``accessibilityLabel == nil`` however it was named, so VoiceOver read
an edit box's contents and never its label. Buttons and check boxes are fine
without help: AppKit names them from their title.

So the label is set on the native view through NSAccessibility, with ctypes
rather than PyObjC, which isn't a dependency and would be a lot to add to the
app bundle for three selectors.

``GetHandle()`` isn't always the view VoiceOver lands on. A multi-line
``wx.TextCtrl`` is a scroll view around an ``NSTextView``, and a ``wx.ListBox``
a scroll view around an ``NSTableView``. VoiceOver focuses the inner view, so
when the handle has a ``documentView``, that is what gets the label.

This module doesn't import wx, so its logic can be tested anywhere, and
everything in it is a no-op off macOS.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import functools
import sys

IS_MACOS = sys.platform == "darwin"

_objc = None  # the loaded libobjc, or False once loading has failed


def _runtime():
    """The Objective-C runtime, or None where it is unavailable."""
    global _objc
    if _objc is None:
        _objc = False
        if IS_MACOS:
            try:
                path = ctypes.util.find_library("objc")
                lib = ctypes.cdll.LoadLibrary(path) if path else None
            except OSError:
                lib = None
            if lib is not None:
                lib.sel_registerName.restype = ctypes.c_void_p
                lib.sel_registerName.argtypes = [ctypes.c_char_p]
                lib.objc_getClass.restype = ctypes.c_void_p
                lib.objc_getClass.argtypes = [ctypes.c_char_p]
                _objc = lib
    return _objc or None


@functools.lru_cache(maxsize=None)
def _msg_send(restype, argtypes):
    """objc_msgSend cast to one method prototype, made once per prototype:
    the lists' names change on every refresh."""
    signature = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
    return signature(ctypes.cast(_runtime().objc_msgSend, ctypes.c_void_p).value)


@functools.lru_cache(maxsize=None)
def _selector(name: str):
    return _runtime().sel_registerName(name.encode())


def _send(obj, selector, *args, restype=ctypes.c_void_p, argtypes=()):
    """``[obj selector:args]``.

    objc_msgSend is cast to each method's own prototype: calling it through
    the wrong one is undefined on arm64.
    """
    return _msg_send(restype, tuple(argtypes))(obj, _selector(selector), *args)


def _responds(obj, selector) -> bool:
    return bool(_send(obj, "respondsToSelector:", ctypes.c_void_p(_selector(selector)),
                      restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,)))


def _nsstring(text: str):
    objc = _runtime()
    return _send(objc.objc_getClass(b"NSString"), "stringWithUTF8String:",
                 text.encode("utf-8"), argtypes=(ctypes.c_char_p,))


def _appkit_constant(appkit, name):
    return ctypes.c_void_p.in_dll(appkit, name).value


def queue_announcement(handle: int, text: str) -> bool:
    """Let VoiceOver finish its current speech before this announcement.

    AppleScript's output command returns before speech finishes and has no
    queue option. Posting from the app's view keeps send read-back intact.
    False asks the caller to use its usual system-voice fallback.
    """
    if not IS_MACOS or not handle or _runtime() is None:
        return False
    try:
        workspace = _send(_runtime().objc_getClass(b"NSWorkspace"), "sharedWorkspace")
        if not _send(workspace, "isVoiceOverEnabled", restype=ctypes.c_bool):
            return False
        appkit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/AppKit.framework/AppKit")
        def constant(name):
            return _appkit_constant(appkit, name)
        info = _send(_runtime().objc_getClass(b"NSMutableDictionary"), "dictionary")
        priority = _send(_runtime().objc_getClass(b"NSNumber"), "numberWithInteger:",
                         10, argtypes=(ctypes.c_long,))  # NSAccessibilityPriorityLow
        for key, value in (("NSAccessibilityAnnouncementKey", _nsstring(text)),
                           ("NSAccessibilityPriorityKey", priority)):
            _send(info, "setObject:forKey:", value, constant(key),
                  argtypes=(ctypes.c_void_p, ctypes.c_void_p))
        post = appkit.NSAccessibilityPostNotificationWithUserInfo
        post.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        post.restype = None
        # AppKit requires the application accessibility element, not a view.
        application = _send(_runtime().objc_getClass(b"NSApplication"), "sharedApplication")
        post(application, constant("NSAccessibilityAnnouncementRequestedNotification"), info)
        return True
    except Exception:
        return False


def _to_str(nsstring) -> str | None:
    if not nsstring:
        return None
    pointer = _send(nsstring, "UTF8String", restype=ctypes.c_char_p)
    return pointer.decode("utf-8") if pointer else None


def _target_view(window):
    """The native view VoiceOver focuses for `window`, or None."""
    if _runtime() is None:
        return None
    try:
        handle = window.GetHandle()
    except (AttributeError, RuntimeError):
        return None
    if not handle:
        return None
    view = ctypes.c_void_p(handle)
    if _responds(view, "documentView"):
        inner = _send(view, "documentView")
        if inner:
            return ctypes.c_void_p(inner)
    return view


def set_label(window, label: str) -> bool:
    """Give `window` the name VoiceOver reads. True if it was applied.

    False, never an exception, off macOS or on a control with no native view:
    a missing name mustn't take the window down with it.
    """
    if not IS_MACOS or not label:
        return False
    try:
        view = _target_view(window)
        if view is None or not _responds(view, "setAccessibilityLabel:"):
            return False
        _send(view, "setAccessibilityLabel:", ctypes.c_void_p(_nsstring(label)),
              argtypes=(ctypes.c_void_p,))
        return True
    except Exception:  # noqa: BLE001 - never break the window over a name
        return False


def set_static_text(window, text: str) -> bool:
    """Make `window`, a view wx draws itself, text VoiceOver can land on and
    read: its words as its value, as for an AppKit label. True if applied.

    A plain ``wxNSView`` is no accessibility element at all, so when one had
    the focus VoiceOver found nothing focused and stayed where it was: Go to
    Status Bar (Command+9) seemed to do nothing (#726). Called again with new
    words whenever they change."""
    if not IS_MACOS:
        return False
    try:
        view = _target_view(window)
        if view is None or not _responds(view, "setAccessibilityValue:"):
            return False
        _send(view, "setAccessibilityElement:", True, argtypes=(ctypes.c_bool,))
        _send(view, "setAccessibilityRole:", ctypes.c_void_p(_nsstring("AXStaticText")),
              argtypes=(ctypes.c_void_p,))
        _send(view, "setAccessibilityValue:", ctypes.c_void_p(_nsstring(text)),
              argtypes=(ctypes.c_void_p,))
        return True
    except Exception:  # noqa: BLE001 - never break the window over a name
        return False


def activate_app() -> bool:
    """Make The Chat Place the active app. True if it was asked to.

    VO+M opens the active app's menu bar, and a wx window can be in front
    without its app being active: ``Raise()`` only orders the window front,
    and a copy started from a terminal (``python -m thechatplace``, or the
    bare program in ``dist/TheChatPlace/``) isn't activated by macOS the way
    a double-clicked .app is. Then VO+M went to the menus of the app used
    before, the Claude desktop app.
    """
    if not IS_MACOS or _runtime() is None:
        return False
    try:
        app = _send(_runtime().objc_getClass(b"NSApplication"), "sharedApplication")
        if not app:
            return False
        _send(ctypes.c_void_p(app), "activateIgnoringOtherApps:", True,
              restype=None, argtypes=(ctypes.c_bool,))
        return True
    except Exception:  # noqa: BLE001
        return False


#: NSEventModifierFlagCommand, in a key equivalent's modifier mask.
_COMMAND_KEY_MASK = 1 << 20
_BUTTON_CLASS = b"wxNSButton"  # the NSButton subclass wx makes every wx.Button from
_key_methods: dict = {}  # hooked class address -> its methods' ctypes callbacks, kept alive
_SET_KEY_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_SET_MASK_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64)


def is_command_letter(key, mask: int) -> bool:
    """True for a button key equivalent made from its access key (#675): one
    character you could type, with Command. Return (the default button's) and
    Escape (Cancel's) aren't, and stay."""
    return (bool(key) and len(key) == 1 and key.isprintable() and not key.isspace()
            and bool(mask & _COMMAND_KEY_MASK))


def _prepare_class_calls(objc):
    objc.class_getInstanceMethod.restype = ctypes.c_void_p
    objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.method_getImplementation.restype = ctypes.c_void_p
    objc.method_getImplementation.argtypes = [ctypes.c_void_p]
    objc.class_replaceMethod.restype = ctypes.c_void_p
    objc.class_replaceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_char_p]
    objc.object_getClass.restype = ctypes.c_void_p
    objc.object_getClass.argtypes = [ctypes.c_void_p]
    objc.class_getName.restype = ctypes.c_char_p
    objc.class_getName.argtypes = [ctypes.c_void_p]


def _key_equivalent_of(view):
    """``(keyEquivalent, keyEquivalentModifierMask)`` of a native button."""
    key = _to_str(_send(view, "keyEquivalent"))
    mask = _send(view, "keyEquivalentModifierMask", restype=ctypes.c_uint64)
    return key or "", int(mask or 0)


def _hook_button_class(cls) -> bool:
    """Make a wx button class drop every Command key equivalent it's given, once.

    wxWidgets (src/osx/cocoa/button.mm, ``SetAcceleratorFromLabel``) gives a
    button with an "&" in its label that letter with Command as its key
    equivalent, when it's made and again on every label change
    (``wxButton::OSXUpdateAfterLabelChange``). AppKit offers a key equivalent
    to the window's buttons before the focused control sees the key, so
    "Sa&ve Limits" took Command+V from the key box beside it. Both setters
    are replaced on the class: each does what it did, then a Command letter
    left on the button is taken away. That catches the label changes too
    (Add Key and Replace Key), however they're made, with nothing to call
    after each one. Return on the default button and Escape aren't Command
    letters, and are kept. The modifier mask is cleared with the letter:
    left at Command, a later SetDefault's Return would be Command+Return.
    """
    if cls in _key_methods:
        return True
    objc = _runtime()
    if objc is None:
        return False
    _prepare_class_calls(objc)
    key_selector = _selector("setKeyEquivalent:")
    mask_selector = _selector("setKeyEquivalentModifierMask:")
    key_method = objc.class_getInstanceMethod(cls, key_selector)
    mask_method = objc.class_getInstanceMethod(cls, mask_selector)
    if not key_method or not mask_method:
        return False
    original_key_address = objc.method_getImplementation(key_method)
    original_mask_address = objc.method_getImplementation(mask_method)
    original_key = _SET_KEY_TYPE(original_key_address)
    original_mask = _SET_MASK_TYPE(original_mask_address)

    def drop(view):
        try:
            if is_command_letter(*_key_equivalent_of(ctypes.c_void_p(view))):
                # The originals, not messages: those would come back here.
                original_key(view, key_selector, _nsstring(""))
                original_mask(view, mask_selector, 0)
        except Exception:  # noqa: BLE001 - an exception can't cross into AppKit
            pass

    def set_key(view, selector_, key):
        original_key(view, selector_, key)
        drop(view)

    def set_mask(view, selector_, mask):
        original_mask(view, selector_, mask)
        drop(view)

    key_imp = _SET_KEY_TYPE(set_key)
    mask_imp = _SET_MASK_TYPE(set_mask)
    _key_methods[cls] = [original_key, original_mask, key_imp, mask_imp]
    objc.class_replaceMethod(cls, key_selector, ctypes.cast(key_imp, ctypes.c_void_p), b"v@:@")
    objc.class_replaceMethod(cls, mask_selector, ctypes.cast(mask_imp, ctypes.c_void_p),
                             b"v@:Q")

    def unhook():
        # Put AppKit's own methods back before Python shuts down: a button
        # torn down during interpreter finalization would otherwise call
        # into a Python that can no longer run it, and the process hangs
        # instead of exiting (seen once on CI's Mac runner, #677).
        try:
            objc.class_replaceMethod(cls, key_selector, original_key_address, b"v@:@")
            objc.class_replaceMethod(cls, mask_selector, original_mask_address, b"v@:Q")
        except Exception:  # noqa: BLE001 - nothing to do at exit if it can't
            pass
    import atexit
    atexit.register(unhook)
    return True


def stop_button_command_keys() -> bool:
    """Keep Command+letter away from every wx button from now on (#675). True
    if it will. Called once as the app starts, before any window is made;
    ``drop_command_key`` is the fallback for a button made some other way."""
    if not IS_MACOS or _runtime() is None:
        return False
    try:
        cls = _runtime().objc_getClass(_BUTTON_CLASS)
        return bool(cls) and _hook_button_class(cls)
    except Exception:  # noqa: BLE001
        return False


def drop_command_key(window) -> bool:
    """Take a Command key equivalent off wx button `window` now, and hook its
    class if it's another of wx's, so a later label change can't put it back.
    True if the button has none afterwards; False off macOS, for a window
    that isn't a native button, or for a button of AppKit's own, which is
    left as it is."""
    if not IS_MACOS or _runtime() is None:
        return False
    try:
        handle = window.GetHandle()
        if not handle:
            return False
        view = ctypes.c_void_p(handle)
        if not _responds(view, "keyEquivalent"):
            return False
        objc = _runtime()
        _prepare_class_calls(objc)
        cls = objc.object_getClass(view)
        # Only wx's own classes: an NSButton of AppKit's (an alert's Don't
        # Save, Command+D) keeps the key the system gave it.
        name = objc.class_getName(cls) if cls else None
        if not (name and name.startswith(b"wx")):
            return False
        _hook_button_class(cls)
        if is_command_letter(*_key_equivalent_of(view)):
            _send(view, "setKeyEquivalent:", ctypes.c_void_p(_nsstring("")),
                  restype=None, argtypes=(ctypes.c_void_p,))
            _send(view, "setKeyEquivalentModifierMask:", 0,
                  restype=None, argtypes=(ctypes.c_uint64,))
        return not is_command_letter(*_key_equivalent_of(view))
    except Exception:  # noqa: BLE001
        return False


def key_equivalent(window):
    """``(key, modifier mask)`` of wx button `window`'s native view, or None.
    For tests."""
    if not IS_MACOS or _runtime() is None:
        return None
    try:
        handle = window.GetHandle()
        if not handle:
            return None
        view = ctypes.c_void_p(handle)
        if not _responds(view, "keyEquivalent"):
            return None
        return _key_equivalent_of(view)
    except Exception:  # noqa: BLE001
        return None


_MENU_CLASS = b"wxNSTableView"  # the NSTableView inside every wx.ListBox
_menu_handlers: dict = {}  # native view address -> what opens its menu
_menu_methods: list = []  # the installed methods' ctypes callbacks, kept alive
_ACTION_NAMES_TYPE = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_SHOW_MENU_TYPE = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
_PERFORM_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def _install_show_menu() -> bool:
    """Teach wx's list box table VoiceOver's show-menu action, once.

    VO+Shift+M performs AXShowMenu, and a ``wx.ListBox``'s table doesn't
    offer it: wx opens context menus from a right-click or a key, and the
    table has no NSMenu of its own, so AppKit lists no actions and VO+Shift+M
    did nothing. Methods are put on the class: the action list gains
    AXShowMenu for a table with a handler, and performing it calls that
    handler. Tables without one are answered as before. Performing goes
    through both the old ``accessibilityPerformAction:``, which the table
    still answers itself (calling the original found no menu and did
    nothing), and the newer ``accessibilityPerformShowMenu``.
    """
    if _menu_methods:
        return True
    objc = _runtime()
    if objc is None:
        return False
    objc.class_getInstanceMethod.restype = ctypes.c_void_p
    objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.method_getImplementation.restype = ctypes.c_void_p
    objc.method_getImplementation.argtypes = [ctypes.c_void_p]
    objc.class_replaceMethod.restype = ctypes.c_void_p
    objc.class_replaceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_char_p]
    cls = objc.objc_getClass(_MENU_CLASS)
    if not cls:
        return False
    names_selector = _selector("accessibilityActionNames")
    inherited = objc.class_getInstanceMethod(cls, names_selector)
    if not inherited:
        return False
    original_names = _ACTION_NAMES_TYPE(objc.method_getImplementation(inherited))
    perform_selector = _selector("accessibilityPerformAction:")
    inherited = objc.class_getInstanceMethod(cls, perform_selector)
    if not inherited:
        return False
    original_perform = _PERFORM_TYPE(objc.method_getImplementation(inherited))

    def action_names(view, selector):
        names = original_names(view, selector)
        if view not in _menu_handlers:
            return names
        try:
            show_menu = ctypes.c_void_p(_nsstring("AXShowMenu"))
            if names:
                return _send(ctypes.c_void_p(names), "arrayByAddingObject:", show_menu,
                             argtypes=(ctypes.c_void_p,))
            return _send(objc.objc_getClass(b"NSArray"), "arrayWithObject:", show_menu,
                         argtypes=(ctypes.c_void_p,))
        except Exception:  # noqa: BLE001 - an exception can't cross into AppKit
            return names

    def perform_show_menu(view, _selector_):
        handler = _menu_handlers.get(view)
        if handler is None:
            return False
        try:
            handler()
        except Exception:  # noqa: BLE001
            return False
        return True

    def perform_action(view, selector, action):
        if view in _menu_handlers:
            try:
                if _to_str(action) == "AXShowMenu":
                    perform_show_menu(view, selector)
                    return
            except Exception:  # noqa: BLE001
                pass
        original_perform(view, selector, action)

    names_imp = _ACTION_NAMES_TYPE(action_names)
    show_imp = _SHOW_MENU_TYPE(perform_show_menu)
    perform_imp = _PERFORM_TYPE(perform_action)
    _menu_methods.extend([original_names, original_perform, names_imp, show_imp, perform_imp])
    objc.class_replaceMethod(cls, names_selector,
                             ctypes.cast(names_imp, ctypes.c_void_p), b"@@:")
    objc.class_replaceMethod(cls, perform_selector,
                             ctypes.cast(perform_imp, ctypes.c_void_p), b"v@:@")
    objc.class_replaceMethod(cls, _selector("accessibilityPerformShowMenu"),
                             ctypes.cast(show_imp, ctypes.c_void_p), b"B@:")
    return True


def set_show_menu(window, handler) -> bool:
    """Make VO+Shift+M on list box `window` call `handler()`. True if it will.

    `handler` runs inside VoiceOver's request, so it should only schedule the
    menu (``wx.CallAfter``): a menu opened there would hold VoiceOver up until
    it closed. ``handler=None`` removes it, which a destroyed list must do, as
    its view's address can be reused.
    """
    if not IS_MACOS:
        return False
    try:
        view = _target_view(window)
        if view is None or not _install_show_menu():
            return False
        if handler is None:
            _menu_handlers.pop(view.value, None)
        else:
            _menu_handlers[view.value] = handler
        return True
    except Exception:  # noqa: BLE001
        return False


_CELL_PROXY_CLASS = b"NSTableViewCellProxy"  # what VoiceOver reads a table row's text from
_row_texts: dict = {}  # table view address -> item_text(index)
_row_text_methods: list = []  # the installed method's ctypes callbacks, kept alive
_ATTRIBUTE_TYPE = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p)


def _attribute(element, name: str):
    """``[element accessibilityAttributeValue:name]``, the old accessibility
    call, which is all a table's row speaks."""
    return _send(element, "accessibilityAttributeValue:", ctypes.c_void_p(_nsstring(name)),
                 argtypes=(ctypes.c_void_p,))


def _install_row_text() -> bool:
    """Make a wx.ListBox row's text, as VoiceOver reads it, come from the
    list's ``item_text``, once (#11).

    VoiceOver reads a row of the list's table from the row's one cell, an
    ``NSTableViewCellProxy`` whose AXValue is the row's text as drawn: for the
    messages list, a message's first line. Labelling the rows doesn't work, as
    the table makes new row and cell objects each time it's asked, so a label
    set on one is gone by the next question. Instead the proxy class's
    ``accessibilityAttributeValue:`` is replaced: for AXValue, a cell in a
    table with an ``item_text`` answers with ``item_text(row)``. Everything
    else, and every other table, is answered as before. Tried first in a
    standalone list with VoiceOver, which then read whole messages.
    """
    if _row_text_methods:
        return True
    objc = _runtime()
    if objc is None:
        return False
    objc.class_getInstanceMethod.restype = ctypes.c_void_p
    objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.method_getImplementation.restype = ctypes.c_void_p
    objc.method_getImplementation.argtypes = [ctypes.c_void_p]
    objc.class_replaceMethod.restype = ctypes.c_void_p
    objc.class_replaceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_char_p]
    cls = objc.objc_getClass(_CELL_PROXY_CLASS)
    if not cls:
        return False
    selector = _selector("accessibilityAttributeValue:")
    inherited = objc.class_getInstanceMethod(cls, selector)
    if not inherited:
        return False
    original = _ATTRIBUTE_TYPE(objc.method_getImplementation(inherited))

    def attribute_value(proxy, selector_, attribute):
        value = original(proxy, selector_, attribute)
        if not _row_texts:
            return value
        try:
            if _to_str(attribute) != "AXValue":
                return value
            row = original(proxy, selector_, _nsstring("AXParent"))
            if not row or not _responds(ctypes.c_void_p(row), "accessibilityAttributeValue:"):
                return value
            table = _attribute(ctypes.c_void_p(row), "AXParent")
            item_text = _row_texts.get(table)
            if item_text is None:
                return value
            index = _attribute(ctypes.c_void_p(row), "AXIndex")
            if not index or not _responds(ctypes.c_void_p(index), "integerValue"):
                return value
            text = item_text(_send(ctypes.c_void_p(index), "integerValue",
                                   restype=ctypes.c_long))
            if text:
                return _nsstring(text)
        except Exception:  # noqa: BLE001 - an exception can't cross into AppKit
            pass
        return value

    imp = _ATTRIBUTE_TYPE(attribute_value)
    _row_text_methods.extend([original, imp])
    objc.class_replaceMethod(cls, selector, ctypes.cast(imp, ctypes.c_void_p), b"@@:@")
    return True


def set_row_text(window, item_text) -> bool:
    """Make VoiceOver read row ``i`` of list box `window` as ``item_text(i)``,
    or the row's own text when that's None or empty. True if it will.

    ``item_text`` is asked each time VoiceOver reads a row, so it follows the
    list as it changes. ``item_text=None`` removes it, which a destroyed list
    must do, as its view's address can be reused.
    """
    if not IS_MACOS:
        return False
    try:
        view = _target_view(window)
        if view is None or not _install_row_text():
            return False
        if item_text is None:
            _row_texts.pop(view.value, None)
        else:
            _row_texts[view.value] = item_text
        return True
    except Exception:  # noqa: BLE001
        return False


def row_text(window, index: int) -> str | None:
    """The text VoiceOver reads for row `index` of list box `window`, or None.
    For tests."""
    if not IS_MACOS:
        return None
    try:
        view = _target_view(window)
        if view is None or not _responds(view, "accessibilityRows"):
            return None
        rows = _send(view, "accessibilityRows")
        if not rows or index >= _send(ctypes.c_void_p(rows), "count", restype=ctypes.c_ulong):
            return None
        row = _send(ctypes.c_void_p(rows), "objectAtIndex:", index,
                    argtypes=(ctypes.c_ulong,))
        cells = _attribute(ctypes.c_void_p(row), "AXChildren")
        if not cells or not _send(ctypes.c_void_p(cells), "count", restype=ctypes.c_ulong):
            return None
        cell = _send(ctypes.c_void_p(cells), "objectAtIndex:", 0, argtypes=(ctypes.c_ulong,))
        return _to_str(_attribute(ctypes.c_void_p(cell), "AXValue"))
    except Exception:  # noqa: BLE001
        return None


def get_label(window) -> str | None:
    """The name VoiceOver would read for `window`, or None. For tests, and for
    checking a control reported as unlabelled."""
    if not IS_MACOS:
        return None
    try:
        view = _target_view(window)
        if view is None or not _responds(view, "accessibilityLabel"):
            return None
        return _to_str(_send(view, "accessibilityLabel"))
    except Exception:  # noqa: BLE001
        return None


def _string_attribute(element, selector: str) -> str | None:
    """``[element selector]`` when it's a string; a check box's value is a number."""
    if not _responds(element, selector):
        return None
    value = _send(element, selector)
    if not value:
        return None
    string_class = _runtime().objc_getClass(b"NSString")
    if not _send(ctypes.c_void_p(value), "isKindOfClass:", ctypes.c_void_p(string_class),
                 restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,)):
        return None
    return _to_str(ctypes.c_void_p(value))


def focused_element() -> dict | None:
    """What VoiceOver lands on now: the app's focused accessibility element,
    with its role and the words it would read (label, title or value). For
    tests (#726): wx's FindFocus says where the keyboard is, and this says
    whether VoiceOver can follow it. None off macOS."""
    if _runtime() is None:
        return None
    try:
        app = _send(_runtime().objc_getClass(b"NSApplication"), "sharedApplication")
        element = _send(ctypes.c_void_p(app), "accessibilityFocusedUIElement")
        if not element:
            return {"role": None, "name": None, "class": None, "id": None}
        element = ctypes.c_void_p(element)
        role = _string_attribute(element, "accessibilityRole")
        name = (_string_attribute(element, "accessibilityLabel")
                or _string_attribute(element, "accessibilityTitle")
                or _string_attribute(element, "accessibilityValue"))
        if not name and _responds(element, "controlView"):
            # A text box's focus is its cell, which AppKit names from its
            # control: VoiceOver reads the field's label (checked from
            # outside the app, through the AX API, as VoiceOver reads it).
            control = _send(element, "controlView")
            if control:
                name = _string_attribute(ctypes.c_void_p(control), "accessibilityLabel")
        cls = _to_str(_send(element, "className"))
        return {"role": role, "name": name, "class": cls, "id": element.value}
    except Exception:  # noqa: BLE001
        return None
