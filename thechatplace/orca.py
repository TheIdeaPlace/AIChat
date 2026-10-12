"""Speak through Orca from The Chat Place's own process (Linux).

The Linux counterpart of ``screen_readers.py``. Orca has two ways in, and
neither covers every case (see ``dev-notes/linux-port.md``):

* **PresentMessage**, Orca's D-Bus remote controller (Orca 49 and later):
  speech and braille in the user's own Orca settings, whatever window is in
  front. The service was ``org.gnome.Orca.Service`` in Orca 49 and 50 and is
  ``org.gnome.Orca1.Service`` from 51. Called with ``busctl``, ``gdbus`` or
  ``dbus-send``, whichever the system has, so the app needs no D-Bus library.
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

import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import platform_paths

#: Orca's D-Bus service, newest first: (bus name, object path, interface).
SERVICES = [
    ("org.gnome.Orca1.Service", "/org/gnome/Orca1/Service", "org.gnome.Orca1.Service"),
    ("org.gnome.Orca.Service", "/org/gnome/Orca/Service", "org.gnome.Orca.Service"),
]

#: A command line can hold about 128 KB per argument; a whole reply read at
#: the Full level is cut there (it's minutes of speech already).
MESSAGE_LIMIT = 100_000

#: Seconds a D-Bus call may take before Orca is treated as not answering.
CALL_TIMEOUT = 5

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


def present_message_commands(text: str, find=platform_paths.find_tool) -> List[List[str]]:
    """Every command line that would call PresentMessage, for each service
    name and each D-Bus tool there is: tried in order until one works."""
    commands = []
    busctl, gdbus, dbus_send = find("busctl"), find("gdbus"), find("dbus-send")
    for name, path, interface in SERVICES:
        if busctl:
            commands.append([busctl, "--user", "--timeout", str(CALL_TIMEOUT), "call",
                             name, path, interface, "PresentMessage", "s", text])
        if gdbus:
            commands.append([gdbus, "call", "--session", "--timeout", str(CALL_TIMEOUT),
                             "--dest", name, "--object-path", path,
                             "--method", f"{interface}.PresentMessage", _quote_gvariant(text)])
        if dbus_send:
            commands.append([dbus_send, "--session", "--print-reply", f"--dest={name}",
                             f"--reply-timeout={CALL_TIMEOUT * 1000}", path,
                             f"{interface}.PresentMessage", f"string:{text}"])
    return commands


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
    Orca 48 isn't asked again for every announcement; a new Orca (another
    process id) is asked afresh."""

    def __init__(self, run=subprocess.run, find=platform_paths.find_tool,
                 orca_pids: Optional[Callable[[], set]] = None):
        self._run = run
        self._find = find
        self._orca_pids = orca_pids or (lambda: platform_paths.process_ids("orca"))
        self._without_service: set = set()

    def present(self, text: str) -> Optional[str]:
        """None when Orca took the text, else why not."""
        pids = self._orca_pids()
        if pids and pids <= self._without_service:
            return NO_SERVICE
        commands = present_message_commands(text[:MESSAGE_LIMIT], self._find)
        if not commands:
            return "can't be reached: there's no busctl, gdbus or dbus-send"
        unknown = True  # every answer said the service doesn't exist
        for argv in commands:
            try:
                done = self._run(argv, capture_output=True, text=True, timeout=CALL_TIMEOUT + 2)
            except (OSError, subprocess.SubprocessError):
                unknown = False
                continue
            if done.returncode == 0 and _said_true(done.stdout or ""):
                return None
            if not _no_such_service(f"{done.stdout or ''} {done.stderr or ''}"):
                unknown = False
        if unknown:
            self._without_service |= pids
            return NO_SERVICE
        return "didn't answer on D-Bus"


class Orca:
    """Hands an announcement to Orca, if it's running: through D-Bus, else
    through the app's window while it's active."""

    def __init__(self, client: Optional[OrcaClient] = None,
                 processes: Callable[[], set] = platform_paths.running_process_names):
        self._client = client or OrcaClient()
        self._processes = processes
        #: Set by the window: ``(text, interrupt) -> bool``, True when it
        #: sent the text to Orca because The Chat Place is the active app.
        self.window_announcer: Optional[Callable[[str, bool], bool]] = None

    def running(self) -> bool:
        return "orca" in self._processes()

    def speak(self, text: str, engine: str, interrupt: bool) -> Outcome:
        outcome = Outcome(running=["orca"] if self.running() else [])
        if not outcome.running:
            return outcome
        problem = self._client.present(text)
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
