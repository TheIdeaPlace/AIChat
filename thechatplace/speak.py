"""Speak through the running screen reader. Never blocks and never raises.

The shipped app's fuller speech module (system voices, rates, levels) isn't
carried over yet. This keeps its first rule: a screen reader's own voice and
rate are the user's, so only the text is handed over.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from typing import Callable, Optional


def _windows_route() -> Callable[[str, bool], None]:
    from .screen_readers import ScreenReaders
    readers = ScreenReaders()
    return lambda text, interrupt: readers.speak(text, "auto", interrupt) and None


def _linux_route() -> Callable[[str, bool], None]:
    from .orca import Orca
    orca = Orca()
    return lambda text, interrupt: orca.speak(text, "auto", interrupt) and None


def _mac_route() -> Callable[[str, bool], None]:
    def say(text: str, interrupt: bool) -> None:
        # The text goes as an argument, so nothing in it can become a script.
        script = ('on run argv\n'
                  'if application "VoiceOver" is running then\n'
                  'tell application "VoiceOver" to output (item 1 of argv)\n'
                  'end if\nend run')
        subprocess.run(["osascript", "-e", script, text], capture_output=True, timeout=10)
    return say


class Speaker:
    """One thread at a time per route, in order, so a slow reader can't hold the window."""

    def __init__(self, route: Optional[Callable[[str, bool], None]] = None):
        self._route = route
        self._queue: "queue.Queue" = queue.Queue(maxsize=64)
        threading.Thread(target=self._run, daemon=True, name="speech").start()

    def say(self, text: str, interrupt: bool = False) -> None:
        if not text.strip():
            return
        try:
            self._queue.put_nowait((text, interrupt))
        except queue.Full:
            pass

    def _run(self) -> None:
        route = self._route
        if route is None:
            try:
                if sys.platform == "win32":
                    route = _windows_route()
                elif sys.platform == "darwin":
                    route = _mac_route()
                else:
                    route = _linux_route()
            except Exception:  # noqa: BLE001 - no speech is better than no app
                route = lambda text, interrupt: None  # noqa: E731
        while True:
            text, interrupt = self._queue.get()
            try:
                route(text, interrupt)
            except Exception:  # noqa: BLE001 - see the module's first line
                pass
