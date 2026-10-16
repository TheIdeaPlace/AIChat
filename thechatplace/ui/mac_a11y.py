"""Name controls for VoiceOver, which wx can't do on macOS (from Image
Description Toolkit's shared/mac_accessibility.py), and make the app active
so VoiceOver finds its menu bar (``activate_app``).

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
