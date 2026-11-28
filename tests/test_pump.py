"""fake_env.pump, which every window test waits with (#761): a wait that
runs out says where its time went. Needs a wx app, no window."""
import re
import time

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
    assert re.match(r"pump: not true after \d+\.\d s of 2: \d+ rounds, \d+\.\d s running events, "
                    r"the longest one 1\.[2-9] s", said), said


def test_a_short_wait_on_purpose_says_nothing(app, capsys):
    assert not fake_env.pump(lambda: False, timeout=0.2)
    assert capsys.readouterr().out == ""


def test_a_condition_met_as_the_time_runs_out_still_counts(app, monkeypatch):
    # The deadline can pass while it sleeps between looks: it looks once more.
    state = {"done": False}
    real_sleep = time.sleep

    def sleep(seconds):
        real_sleep(0.3)
        state["done"] = True
    monkeypatch.setattr(fake_env.time, "sleep", sleep)
    assert fake_env.pump(lambda: state["done"], timeout=0.2)
