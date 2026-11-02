"""Speak through Orca from The Chat Place's own process (Linux).

The Linux counterpart of ``screen_readers.py``. Orca has two ways in, and
neither covers every case (see ``dev-notes/linux-port.md``):

* **PresentMessage**, Orca's D-Bus remote controller (Orca 49 and later):
  speech and braille in the user's own Orca settings, whatever window is in
  front. The service was ``org.gnome.Orca.Service`` in Orca 49 and 50 and is
  ``org.gnome.Orca1.Service`` from 51. Called through libgio (ctypes), so
  the text is passed in memory. Never through a D-Bus command-line tool:
  announcements are chat replies, and a command line can be read in /proc by
  other users. GTK 3, which the app needs, needs libgio.
* **An ATK notification from the app's window** (Orca 45.2 and later):
  ``gtk_a11y.announce``. Orca only presents these from the active window, so
  it's used only while The Chat Place is active (``window_announcer``).

When Orca is running but neither works (Orca 48 or older, with The Chat
Place in the background, or libgio can't be loaded), the speaker falls back to speech-dispatcher, the
speech server Orca itself uses. That isn't the #98 defect on Windows (a
second voice over the screen reader): speech-dispatcher speaks one message
at a time, so the two never talk at once, and Orca's next words cut the
announcement off, as they would its own speech.

Calls happen on the speaker's worker thread, never the UI thread. No wx here.
"""
from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import platform_paths

#: Orca's D-Bus service, newest first: (bus name, object path, interface).
SERVICES = [
    ("org.gnome.Orca1.Service", "/org/gnome/Orca1/Service", "org.gnome.Orca1.Service"),
    ("org.gnome.Orca.Service", "/org/gnome/Orca/Service", "org.gnome.Orca.Service"),
]

#: Bytes of UTF-8 text sent. A whole reply read at the Full level is cut
#: here (it's minutes of speech already).
MESSAGE_LIMIT = 100_000

#: Seconds a D-Bus call may take before Orca is treated as not answering.
CALL_TIMEOUT = 2

#: Seconds the calls for one announcement may take together. The speaker
#: gives up on the call after ``speech.SCREEN_READER_TIMEOUT`` (3 s) and
#: drops the announcements behind it, and the window announcer, tried after
#: these, may take 1 s of that (the UI thread answers it).
DEADLINE = 1.5

NAME = "Orca"


@dataclass
class Outcome:
    """What happened when Orca was asked to speak. The same questions the
    speaker asks of ``screen_readers.Outcome``."""

    #: How Orca took the text ("D-Bus", "the window"), else None.
    spoke: Optional[str] = None
    #: ["orca"] when Orca is running.
    running: List[str] = field(default_factory=list)
    #: Why Orca didn't take it, when it's running.
    problem: str = ""

    @property
    def unreachable(self) -> bool:
        """Never on Linux: when Orca can't take the text, speech-dispatcher
        speaks it (see the module docstring)."""
        return False

    @property
    def voice_allowed(self) -> bool:
        """A system voice may speak: Orca isn't running, or couldn't take it."""
        return not self.spoke

    def describe(self) -> str:
        if self.spoke:
            return f"spoke through {NAME} ({self.spoke})"
        if self.running:
            return f"{NAME} is running but {self.problem or 'did not take it'}, so speech-dispatcher"
        return "no screen reader running"


def _truncated(text: str) -> str:
    """``text`` cut to MESSAGE_LIMIT bytes of UTF-8, at a whole character."""
    return text.encode("utf-8", "replace")[:MESSAGE_LIMIT].decode("utf-8", "ignore")


_gio = None


def _load_gio():
    """libgio with the calls ``_gio_present`` makes declared, or False."""
    global _gio
    if _gio is None:
        _gio = False
        if sys.platform.startswith("linux"):
            try:
                gio = ctypes.CDLL("libgio-2.0.so.0")
                glib = ctypes.CDLL("libglib-2.0.so.0")
            except OSError:
                return _gio
            vp, cp, i = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
            gio.g_bus_get_sync.restype = vp
            gio.g_bus_get_sync.argtypes = [i, vp, vp]
            gio.g_dbus_connection_call_sync.restype = vp
            gio.g_dbus_connection_call_sync.argtypes = [vp, cp, cp, cp, cp, vp, vp, i, i, vp, vp]
            glib.g_variant_new_string.restype = vp
            glib.g_variant_new_string.argtypes = [cp]
            glib.g_variant_new_tuple.restype = vp
            glib.g_variant_new_tuple.argtypes = [ctypes.POINTER(vp), ctypes.c_size_t]
            glib.g_variant_n_children.restype = ctypes.c_size_t
            glib.g_variant_n_children.argtypes = [vp]
            glib.g_variant_get_child_value.restype = vp
            glib.g_variant_get_child_value.argtypes = [vp, ctypes.c_size_t]
            glib.g_variant_get_boolean.restype = i
            glib.g_variant_get_boolean.argtypes = [vp]
            glib.g_variant_unref.argtypes = [vp]
            glib.g_error_free.argtypes = [vp]
            _gio = (gio, glib)
    return _gio


class _GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


_bus = None


def _gio_present(name: str, path: str, interface: str, text: str, timeout: float):
    """Call PresentMessage through libgio, with the text passed in memory,
    not on a command line. ``(True, "")`` when Orca returned true, ``(False,
    output)`` otherwise (``output`` is the D-Bus error), or None when libgio
    can't be used. Blocks up to ``timeout``."""
    global _bus
    libs = _load_gio()
    if not libs:
        return None
    gio, glib = libs
    vp = ctypes.c_void_p
    try:
        error = vp()
        if not _bus:
            _bus = gio.g_bus_get_sync(2, None, ctypes.byref(error))  # G_BUS_TYPE_SESSION
        if not _bus:
            return False, _error_message(error, glib)
        data = text.replace("\0", "").encode("utf-8", "replace")
        child = (vp * 1)(glib.g_variant_new_string(data))
        params = glib.g_variant_new_tuple(child, 1)
        reply = gio.g_dbus_connection_call_sync(
            _bus, name.encode(), path.encode(), interface.encode(), b"PresentMessage",
            params, None, 0, int(timeout * 1000), None, ctypes.byref(error))
        if not reply:
            return False, _error_message(error, glib)
        spoke = False
        if glib.g_variant_n_children(reply):
            value = glib.g_variant_get_child_value(reply, 0)
            spoke = bool(glib.g_variant_get_boolean(value))
            glib.g_variant_unref(value)
        glib.g_variant_unref(reply)
        return spoke, ""
    except Exception:  # noqa: BLE001 - speech-dispatcher speaks instead
        return None


def _error_message(error, glib) -> str:
    """The text of a GError (and frees it)."""
    if not error:
        return ""
    message = ctypes.cast(error, ctypes.POINTER(_GError)).contents.message
    glib.g_error_free(error)
    return (message or b"").decode("utf-8", "replace")


NO_SERVICE = "has no D-Bus remote controller (Orca 49 and later have one)"


def _no_such_service(output: str) -> bool:
    """D-Bus's answer when nothing owns the name, or it has no such method:
    an Orca before 49."""
    return any(marker in output for marker in (
        "ServiceUnknown", "was not provided by any", "UnknownMethod", "UnknownObject",
        "NameHasNoOwner"))


class OrcaClient:
    """Orca's D-Bus remote controller. Remembers which Orca had none, so an
    Orca 48 isn't asked again for every announcement (a new Orca, another
    process id, is asked afresh), and which service name worked, so the next
    announcement makes one call, not two."""

    def __init__(self, orca_pids: Optional[Callable[[], set]] = None, gio=_gio_present,
                 clock=time.monotonic):
        self._gio = gio
        self._clock = clock
        self.orca_pids = orca_pids or (lambda: platform_paths.process_ids("orca"))
        self._without_service: set = set()
        #: The service name that last worked.
        self._working: Optional[str] = None

    def present(self, text: str, pids: Optional[set] = None) -> Optional[str]:
        """None when Orca took the text, else why not. Never takes longer
        than DEADLINE (and a little)."""
        pids = self.orca_pids() if pids is None else pids
        if pids and pids <= self._without_service:
            return NO_SERVICE
        text = _truncated(text)
        deadline = self._clock() + DEADLINE
        services = sorted(SERVICES, key=lambda service: service[0] != self._working)  # stable
        unknown = True  # every answer said the service doesn't exist
        for index, (name, path, interface) in enumerate(services):
            left = deadline - self._clock()
            if index and left <= 0:
                unknown = False
                break
            answer = self._gio(name, path, interface, text, max(0.2, min(CALL_TIMEOUT, left)))
            if answer is None:
                return "can't be reached: libgio couldn't be loaded"
            spoke, output = answer
            if spoke:
                self._working = name
                return None
            if not _no_such_service(output):
                unknown = False
        if unknown:
            self._without_service |= pids
            return NO_SERVICE
        return "didn't answer on D-Bus"


class Orca:
    """Hands an announcement to Orca, if it's running: through D-Bus, else
    through the app's window while it's active."""

    def __init__(self, client: Optional[OrcaClient] = None,
                 processes: Optional[Callable[[], set]] = None):
        self._client = client or OrcaClient()
        #: Names of the running processes. Left out, Orca's process ids are
        #: scanned once per announcement and shared with the client.
        self._processes = processes
        #: Set by the window: ``(text, interrupt) -> bool``, True when it
        #: sent the text to Orca because The Chat Place is the active app.
        self.window_announcer: Optional[Callable[[str, bool], bool]] = None

    def running(self) -> bool:
        if self._processes is None:
            return bool(self._client.orca_pids())
        return "orca" in self._processes()

    def speak(self, text: str, engine: str, interrupt: bool) -> Outcome:
        pids = None
        if self._processes is None:
            pids = self._client.orca_pids()
        outcome = Outcome(running=["orca"] if (pids if pids is not None else self.running()) else [])
        if not outcome.running:
            return outcome
        problem = self._client.present(text) if pids is None else self._client.present(text, pids)
        if problem is None:
            outcome.spoke = "D-Bus"
            return outcome
        announcer = self.window_announcer
        if announcer is not None:
            try:
                if announcer(text, interrupt):
                    outcome.spoke = "the window"
                    return outcome
            except Exception:  # noqa: BLE001 - speech never raises
                pass
            problem += ", and The Chat Place isn't the active window"
        outcome.problem = problem
        return outcome
