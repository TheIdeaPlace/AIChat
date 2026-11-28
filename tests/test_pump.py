"""fake_env.pump, which every window test waits with (#761): a wait that
runs out says where its time went. Needs a wx app, no window."""
import re
import time
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

import fake_env  # noqa: E402


def test_a_wait_that_runs_out_says_where_the_time_went(app, monkeypatch, capsys):
    real = wx.YieldIfNeeded
    calls = []

    def slow_once():
        calls.append(1)
        if len(calls) == 2:
            time.sleep(1.2)  # as the window server stalled one yield (#757)
        return real()
    monkeypatch.setattr(wx, "YieldIfNeeded", slow_once)
    assert not fake_env.pump(lambda: False, timeout=2)
    said = capsys.readouterr().out
    found = re.search(r"pump: not true after (\d+\.\d) s of 2: (\d+) rounds, "
                      r"(\d+\.\d) s running events, the longest one (\d+\.\d) s", said)
    assert found, said
    elapsed, rounds, running, longest = (float(found.group(n)) for n in (1, 2, 3, 4))
    assert elapsed >= 2 and rounds >= 2 and longest >= 1.2 and running >= longest


def test_a_short_wait_on_purpose_says_nothing(app, capsys):
    assert not fake_env.pump(lambda: False, timeout=0.2)
    assert "pump:" not in capsys.readouterr().out


def test_what_was_handed_back_as_the_time_ran_out_still_counts(app, monkeypatch):
    # The deadline can pass while it sleeps between looks, with the answer
    # handed back meanwhile (as a load's is, by wx.CallAfter): it's run, and
    # looked at, once more.
    state = {"done": False}

    def sleep(_seconds):
        time.sleep(0.3)
        wx.CallAfter(state.__setitem__, "done", True)
    # Only pump's own clock and sleep: not every thread's.
    monkeypatch.setattr(fake_env, "time", SimpleNamespace(
        monotonic=time.monotonic, time=time.time, sleep=sleep))
    assert fake_env.pump(lambda: state["done"], timeout=0.2)
