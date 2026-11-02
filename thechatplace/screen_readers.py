"""Speak through JAWS or NVDA from The Chat Place's own process (Windows).

Issue #98: NVDA users heard a Windows voice instead of NVDA. The bundled
ClaudeSpeak script reached NVDA only through ``nvdaControllerClient.dll``,
which NVDA doesn't install and the app didn't ship, so on a normal NVDA
machine the NVDA route always failed and the script fell through to OneCore,
talking over NVDA in a voice the user never chose.

Every other project for blind users we looked at (accessible_output2 in
TWBlue, Tolk, UniversalSpeech, Prism) does two things the script didn't: it
ships the NVDA client with the app, and it calls the screen reader from the
app's own process instead of starting one per utterance. This module does
both (see ``dev-notes/speech-routing.md``):

* NVDA: NV Access's controller client, bundled in ``thechatplace/nvda/<arch>/``
  and loaded once with ctypes.
* JAWS: the ``FreedomSci.JawsApi`` COM object JAWS registers when installed,
  through comtypes.

What it decides is in :class:`Outcome`, and the speaker acts on it: a screen
reader that spoke is done; one that's running but didn't answer must not be
covered by a Windows voice (the user is listening to their screen reader, and a
second voice talking over it is the #98 defect); only when no screen reader is
running may a Windows voice speak.

Calls happen on the speaker's worker thread, never the UI thread. No wx here.
"""
from __future__ import annotations

import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import platform_paths

#: Engine setting -> the screen readers tried, in order. Automatic and JAWS
#: put JAWS first (as Tolk does); choosing NVDA puts NVDA first, which only
#: matters when both are running.
ROUTE_ORDER = {
    "auto": ("jaws", "nvda"),
    "jaws": ("jaws", "nvda"),
    "nvda": ("nvda", "jaws"),
}

#: The processes each screen reader runs as. NVDA's folder has other
#: launchers too, so all three count; NVDA answering its controller client
#: counts as running whatever the process is called.
PROCESS_NAMES = {"jaws": ("jfw.exe",),
                 "nvda": ("nvda.exe", "nvda_uiaccess.exe", "nvda_nouiaccess.exe")}

NAMES = {"jaws": "JAWS", "nvda": "NVDA"}


def error_code(exc: BaseException) -> str:
    """An exception as a code, never its message: Windows and COM messages
    can name files, and this text goes to the status bar and bug reports."""
    for attribute in ("hresult", "winerror"):
        code = getattr(exc, attribute, None)
        if isinstance(code, int):
            if code < 0 or code > 0xFFFF:  # an HRESULT
                return f"error 0x{code & 0xFFFFFFFF:08X}"
            return f"error {code}"
    return type(exc).__name__


def nvda_client_path() -> Path:
    """The bundled controller client for this process's architecture.

    It must match the process that loads it, not NVDA (NVDA is reached over
    local RPC): x64 for the built app and x64 Python, including under
    emulation on ARM64 Windows; arm64 for native ARM64 Python.
    """
    arch = "arm64" if sysconfig.get_platform().endswith("arm64") else "x64"
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        base = Path(meipass) if meipass else Path(sys.executable).parent
        root = base / "thechatplace" / "nvda"
    else:
        root = Path(__file__).resolve().parent / "nvda"
    return root / arch / "nvdaControllerClient.dll"


@dataclass
class Outcome:
    """What happened when a screen reader was asked to speak."""

    #: "jaws" or "nvda" when one of them took the text, else None.
    spoke: Optional[str] = None
    #: Screen readers that were running, in the order tried.
    running: List[str] = field(default_factory=list)
    #: Why each running screen reader that was tried didn't take the text.
    problems: Dict[str, str] = field(default_factory=dict)
    #: Windows was locked or on a secure screen, so nothing was sent.
    locked: bool = False

    @property
    def unreachable(self) -> bool:
        """A screen reader is running but none of them took the text."""
        return not self.spoke and not self.locked and bool(self.running)

    def describe(self) -> str:
        """One line for the speech log and the bug report."""
        if self.spoke:
            return f"spoke through {NAMES[self.spoke]}"
        if self.locked:
            return "not spoken: Windows is locked"
        if self.running:
            return "not spoken: " + "; ".join(
                f"{NAMES[name]} is running but {self.problems.get(name, 'did not answer')}"
                for name in self.running)
        return "no screen reader running"


class NvdaClient:
    """NV Access's controller client, loaded once."""

    def __init__(self, path: Optional[Path] = None, loader=None):
        self._path = path
        self._loader = loader
        self._dll = None
        self._load_error = ""

    def _load(self):
        if self._dll is not None or self._load_error:
            return self._dll
        path = self._path or nvda_client_path()
        if not path.is_file():
            self._load_error = "the NVDA controller client is missing from this copy of the app"
            return None
        try:
            import ctypes

            dll = (self._loader or ctypes.WinDLL)(str(path))
            dll.nvdaController_testIfRunning.restype = ctypes.c_ulong
            dll.nvdaController_testIfRunning.argtypes = []
            dll.nvdaController_cancelSpeech.restype = ctypes.c_ulong
            dll.nvdaController_cancelSpeech.argtypes = []
            dll.nvdaController_speakText.restype = ctypes.c_ulong
            dll.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
        except Exception as exc:  # noqa: BLE001
            self._load_error = ("the NVDA controller client couldn't be loaded "
                                f"({error_code(exc)})")
            return None
        self._dll = dll
        return dll

    def speak(self, text: str, interrupt: bool) -> Optional[str]:
        """None when NVDA took the text, else why not.

        ``testIfRunning`` first, because the process alone doesn't prove the
        RPC endpoint is up (NVDA starting, restarting, or another screen
        reader pretending to be NVDA). The same DLL stays loaded across NVDA
        restarts: each call binds afresh.
        """
        dll = self._load()
        if dll is None:
            return self._load_error
        try:
            code = dll.nvdaController_testIfRunning()
            if code:
                return f"didn't answer (Windows error {code})"
            if interrupt:
                dll.nvdaController_cancelSpeech()
            code = dll.nvdaController_speakText(text)
            if code:
                return f"refused the text (Windows error {code})"
            return None
        except Exception as exc:  # noqa: BLE001
            return f"failed ({error_code(exc)})"

    def answers(self) -> bool:
        """NVDA's controller is up in this session (its endpoint is per
        session and desktop), however NVDA's process is named."""
        dll = self._load()
        try:
            return dll is not None and dll.nvdaController_testIfRunning() == 0
        except Exception:  # noqa: BLE001
            return False


class JawsClient:
    """The ``FreedomSci.JawsApi`` COM object JAWS registers when installed."""

    def __init__(self, create: Optional[Callable] = None):
        self._create = create

    def speak(self, text: str, interrupt: bool) -> Optional[str]:
        """None when JAWS took the text, else why not.

        The object is created per call: it's cheap, and a cached one would
        be dead after JAWS restarts. The calling thread must have COM
        initialised (the speaker's worker does).
        """
        try:
            if self._create is None:
                import comtypes.client

                api = comtypes.client.CreateObject("FreedomSci.JawsApi", dynamic=True)
            else:
                api = self._create()
        except Exception as exc:  # noqa: BLE001
            return f"its speech interface couldn't be reached ({error_code(exc)})"
        try:
            if not api.SayString(text, bool(interrupt)):
                return "refused the text"
            return None
        except Exception as exc:  # noqa: BLE001
            return f"failed ({error_code(exc)})"


class ScreenReaders:
    """Picks the running screen reader and hands it the text."""

    def __init__(self, nvda: Optional[NvdaClient] = None, jaws: Optional[JawsClient] = None,
                 processes: Callable[[], set] = platform_paths.running_process_names,
                 locked: Callable[[], bool] = platform_paths.windows_locked):
        self._clients = {"nvda": nvda or NvdaClient(), "jaws": jaws or JawsClient()}
        self._processes = processes
        self._locked = locked

    def running(self, order=("jaws", "nvda")) -> List[str]:
        """The screen readers running in this Windows session, in ``order``."""
        names = self._processes()
        found = []
        for reader in order:
            if any(name in names for name in PROCESS_NAMES[reader]):
                found.append(reader)
            elif reader == "nvda" and self._clients["nvda"].answers():
                found.append(reader)
        return found

    def speak(self, text: str, engine: str, interrupt: bool) -> Outcome:
        order = ROUTE_ORDER.get(engine, ROUTE_ORDER["auto"])
        outcome = Outcome(running=self.running(order))
        if not outcome.running:
            return outcome
        if self._locked():
            outcome.locked = True
            return outcome
        for reader in outcome.running:
            problem = self._clients[reader].speak(text, interrupt)
            if problem is None:
                outcome.spoke = reader
                return outcome
            outcome.problems[reader] = problem
        return outcome
