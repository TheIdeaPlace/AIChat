"""The main window's delayed calls (#766): each is one stop_timers stops, and
none runs once the window is closing or gone. A Mac deletes a window at idle
time, and a call that reaches it before then can crash the app.

The first test reads the source, so it needs no window; the others build a
hidden one: run them in the VM or CI."""
import re
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import env, frame  # noqa: E402,F401

SOURCE = Path(__file__).resolve().parent.parent / "thechatplace" / "ui" / "main_frame.py"


def _method(text: str, header: str) -> str:
    body = text[text.index(header):]
    end = re.search(r"\n(    )?(def|class|@) ?", body[len(header):])
    return body[:len(header) + end.start()] if end else body


def test_every_delayed_call_is_one_stop_timers_stops():
    text = SOURCE.read_text(encoding="utf-8")
    # stop_timers, and _clear_activity, which it calls first.
    stop = _method(text, "    def stop_timers(self):") + _method(text, "    def _clear_activity(self")
    stopped = set(re.findall(r'"(_\w+)"', stop)) | set(re.findall(r"self\.(_\w+)\.Stop\(\)", stop))
    later = _method(text, "    def _later(self")
    loose = []
    for number, line in enumerate(text.splitlines(), 1):
        if "wx.CallLater(" not in line or line.lstrip().startswith("#") or line in later:
            continue
        kept = re.match(r"\s*self\.(_\w+) = wx\.CallLater\(", line)
        if not (kept and kept.group(1) in stopped):
            loose.append(f"{number}: {line.strip()}")
    assert loose == [], "use self._later, or keep it on an attribute stop_timers stops"


def test_stop_timers_stops_a_delayed_call(frame):
    ran = []
    timer = frame._later(60_000, ran.append, "ran")
    assert timer.IsRunning() and timer in frame._delayed
    frame.stop_timers()
    assert not timer.IsRunning() and not frame._delayed
    assert ran == []


def test_a_delayed_call_runs_and_is_let_go(frame):
    ran = []
    timer = frame._later(60_000, ran.append, "ran")
    timer.Stop()
    timer.Notify()  # what firing does
    assert ran == ["ran"] and not frame._delayed


def test_a_delayed_call_does_nothing_once_the_window_is_closing(frame):
    ran = []
    timer = frame._later(60_000, ran.append, "ran")
    timer.Stop()
    frame._closing = True
    timer.Notify()
    assert ran == [] and not frame._delayed
