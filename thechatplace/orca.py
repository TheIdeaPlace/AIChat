"""Speak through Orca from The Chat Place's own process (Linux).

The Linux counterpart of ``screen_readers.py``. Orca has two ways in, and
neither covers every case (see ``dev-notes/linux-port.md``):

* **PresentMessage**, Orca's D-Bus remote controller (Orca 49 and later):
  speech and braille in the user's own Orca settings, whatever window is in
  front. The service was ``org.gnome.Orca.Service`` in Orca 49 and 50 and is
  ``org.gnome.Orca1.Service`` from 51. Called through libgio (ctypes), which
  keeps the text out of /proc; without it, with ``busctl``, ``gdbus`` or
  ``dbus-send``, whichever the system has.
* **An ATK notification from the app's window** (Orca 45.2 and later):
  ``gtk_a11y.announce``. Orca only presents these from the active window, so
  it's used only while The Chat Place is active (``window_announcer``).

When Orca is running but neither works (Orca 48 or older, with The Chat
Place in the background), the speaker falls back to speech-dispatcher, the
speech server Orca itself uses. That isn't the #98 defect on Windows (a
second voice over the screen reader): speech-dispatcher speaks one message
at a time, so the two never talk at once, and Orca's next words cut the
announcement off, as they would its own speech.

Calls happen on the speaker's worker thread, never the UI thread. No wx here.
"""
from __future__ import annotations

import ctypes
import subprocess
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

#: Bytes of text sent. A command line holds 128 KiB per argument, and the
#: limit is on UTF-8 bytes, not characters; a whole reply read at the Full
#: level is cut here (it's minutes of speech already).
MESSAGE_LIMIT = 100_000

#: Seconds a D-Bus call may take before Orca is treated as not answering.
CALL_TIMEOUT = 2

#: Seconds all the attempts for one announcement may take together. The
#: speaker gives up on the call after ``speech.SCREEN_READER_TIMEOUT`` (3 s)
#: and drops the announcements behind it, and the window announcer, tried
#: after these, may take 1 s of that (the UI thread answers it).
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


def _quote_gvariant(text: str) -> str:
    """``text`` as a GVariant string literal, so gdbus never reads "true" or
    "42" as another type."""
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f"'{escaped}'"


def _truncated(text: str) -> str:
    """``text`` cut to MESSAGE_LIMIT bytes of UTF-8, at a whole character."""
    return text.encode("utf-8", "replace")[:MESSAGE_LIMIT].decode("utf-8", "ignore")


def _attempts(text: str, find, timeout: float = CALL_TIMEOUT):
    """((tool path, service name), command line) for each way to call
    PresentMessage with a D-Bus tool, newest service first."""
    attempts = []
    busctl, gdbus, dbus_send = find("busctl"), find("gdbus"), find("dbus-send")
    seconds = max(1, int(timeout))
    for name, path, interface in SERVICES:
        if busctl:
            attempts.append(((busctl, name), [
                busctl, "--user", "--timeout", str(seconds), "call",
                name, path, interface, "PresentMessage", "s", text]))
        if gdbus:
            attempts.append(((gdbus, name), [
                gdbus, "call", "--session", "--timeout", str(seconds),
                "--dest", name, "--object-path", path,
                "--method", f"{interface}.PresentMessage", _quote_gvariant(text)]))
        if dbus_send:
            attempts.append(((dbus_send, name), [
                dbus_send, "--session", "--print-reply", f"--dest={name}",
                f"--reply-timeout={int(timeout * 1000)}", path,
                f"{interface}.PresentMessage", f"string:{text}"]))
    return attempts


def present_message_commands(text: str, find=platform_paths.find_tool) -> List[List[str]]:
    """Every command line that would call PresentMessage, for each service
    name and each D-Bus tool there is: tried in order until one works. The
    text is on the command line, so other users can read it in /proc; this
    is only the way in when libgio can't be used (``_gio_present``)."""
    return [argv for _key, argv in _attempts(text, find)]


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
    isn't there and a D-Bus tool must do it. Blocks up to ``timeout``."""
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
    except Exception:  # noqa: BLE001 - the D-Bus tools are the other way in
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
    an Orca before 49. Each tool words it its own way."""
    return any(marker in output for marker in (
        "ServiceUnknown", "was not provided by any", "UnknownMethod", "UnknownObject",
        "NameHasNoOwner"))


def _said_true(output: str) -> bool:
    """PresentMessage's answer, as each tool prints it: ``b true``,
    ``(true,)``, or ``boolean true``."""
    return "true" in output.lower()


class OrcaClient:
    """Orca's D-Bus remote controller. Remembers which Orca had none, so an
    Orca 48 isn't asked again for every announcement (a new Orca, another
    process id, is asked afresh), and which service and way in worked, so the
    next announcement makes one call, not up to eight."""

    def __init__(self, run=subprocess.run, find=platform_paths.find_tool,
                 orca_pids: Optional[Callable[[], set]] = None, gio=_gio_present,
                 clock=time.monotonic):
        self._run = run
        self._find = find
        self._gio = gio
        self._clock = clock
        self.orca_pids = orca_pids or (lambda: platform_paths.process_ids("orca"))
        self._without_service: set = set()
        #: The (way in, service) that last worked: ("gio", name) or (tool, name).
        self._working = None
        self._deadline = 0.0

    def _ordered(self, text: str, remaining: float):
        """(key, call) for every way in, the one that worked last first.
        ``call()`` returns ``(spoke, output)`` or None when it can't be made."""
        attempts = []
        for name, path, interface in SERVICES:
            if self._gio is not None:
                attempts.append((("gio", name), lambda n=name, p=path, i=interface:
                                 self._gio(n, p, i, text, min(CALL_TIMEOUT, self._left()))))
        for key, argv in _attempts(text, self._find, min(CALL_TIMEOUT, remaining)):
            attempts.append((key, lambda a=argv: self._tool(a)))
        if self._working in [key for key, _call in attempts]:
            attempts.sort(key=lambda each: each[0] != self._working)  # stable
        return attempts

    def _left(self) -> float:
        return max(0.2, self._deadline - self._clock())

    def _tool(self, argv):
        try:
            done = self._run(argv, capture_output=True, text=True,
                             timeout=self._left())
        except (OSError, subprocess.SubprocessError):
            return False, ""
        output = f"{done.stdout or ''} {done.stderr or ''}"
        return (done.returncode == 0 and _said_true(done.stdout or "")), output

    def present(self, text: str, pids: Optional[set] = None) -> Optional[str]:
        """None when Orca took the text, else why not. Never takes longer
        than DEADLINE (and a little)."""
        pids = self.orca_pids() if pids is None else pids
        if pids and pids <= self._without_service:
            return NO_SERVICE
        text = _truncated(text)
        self._deadline = self._clock() + DEADLINE
        attempts = self._ordered(text, DEADLINE)
        unknown = True  # every answer said the service doesn't exist
        tried = False
        for key, call in attempts:
            if tried and self._clock() >= self._deadline:
                unknown = False
                break
            answer = call()
            if answer is None:  # libgio can't be used: the tools are next
                continue
            tried = True
            spoke, output = answer
            if spoke:
                self._working = key
                return None
            if not _no_such_service(output):
                unknown = False
        if not tried:
            return "can't be reached: there's no libgio, busctl, gdbus or dbus-send"
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
