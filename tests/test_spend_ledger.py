"""The API credits tally and its limits (#658). Windowless; each test has its
own folder and a clock it sets."""
import json
import threading
import time

import pytest

from thechatplace import spend_ledger
from thechatplace.spend_ledger import CostTracker, SpendLedger, SpendRefused

NOON = time.mktime((2026, 11, 16, 12, 0, 0, 0, 0, -1))
DAY = 86400.0


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


def ledger(tmp_path, clock=None):
    return SpendLedger(tmp_path, clock=clock or Clock())


def lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / spend_ledger.LEDGER_FILE).read_text(
        encoding="utf-8").splitlines()]


def test_defaults_and_a_first_reservation(tmp_path):
    book = ledger(tmp_path)
    limits = book.limits()
    assert (limits.overall, limits.per_message) == (190.0, 5.0)
    assert book.can_spend() is None
    held = book.reserve("chat-1")
    assert held.amount == 5.0 and held.chat_id == "chat-1" and held.purpose == "turn"
    totals = book.totals()
    assert totals.held == 5.0 and totals.spent == 0 and totals.counted == 5.0
    assert lines(tmp_path)[0]["type"] == "reserve"


def test_settle_then_close_frees_the_rest(tmp_path):
    book = ledger(tmp_path)
    held = book.reserve("chat-1")
    book.settle(held, 0.42, {"claude-opus-5-5": 0.40, "claude-haiku-5-5": 0.02})
    totals = book.totals()
    assert totals.spent == pytest.approx(0.42) and totals.held == pytest.approx(4.58)
    book.close(held, complete=True)
    totals = book.totals()
    assert totals.spent == pytest.approx(0.42) and totals.held == 0
    assert totals.by_chat == {"chat-1": pytest.approx(0.42)}
    assert totals.by_model == {"claude-opus-5-5": pytest.approx(0.40),
                               "claude-haiku-5-5": pytest.approx(0.02)}
    assert totals.today == pytest.approx(0.42) and totals.last_7_days == pytest.approx(0.42)


def test_an_incomplete_close_or_a_crash_keeps_the_reservation_counted(tmp_path):
    book = ledger(tmp_path)
    stopped = book.reserve("chat-1")
    book.settle(stopped, 1.0)
    book.close(stopped, complete=False)        # Stop mid-reply: the rest isn't known
    book.reserve("chat-2")                     # never closed: the app crashed
    again = ledger(tmp_path)                   # the next run
    totals = again.totals()
    assert totals.spent == pytest.approx(1.0)
    assert totals.held == pytest.approx(4.0 + 5.0)


def test_the_overall_limit_caps_and_then_refuses(tmp_path):
    book = ledger(tmp_path)
    book.set_limits(7.5, 5.0)
    first = book.reserve("a")
    assert first.amount == 5.0
    second = book.reserve("b")                 # only 2.50 left
    assert second.amount == 2.5
    with pytest.raises(SpendRefused) as refused:
        book.reserve("c")
    assert "limit of $7.50" in str(refused.value) and "Nothing was sent" in str(refused.value)
    assert book.can_spend() and "limit" in book.can_spend()
    book.settle(first, 0.10)
    book.close(first, complete=True)           # 4.90 back
    assert book.reserve("c").amount == pytest.approx(4.9)


def test_two_chats_at_once_never_share_the_same_money(tmp_path):
    book = ledger(tmp_path)
    book.set_limits(10.0, 5.0)
    got, errors = [], []
    start = threading.Barrier(6)

    def take():
        start.wait()
        try:
            got.append(book.reserve("x").amount)
        except SpendRefused as exc:
            errors.append(str(exc))
    threads = [threading.Thread(target=take) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(got) == [5.0, 5.0] and len(errors) == 4
    assert book.totals().held == 10.0


def test_a_damaged_line_stops_spending_until_reset(tmp_path):
    book = ledger(tmp_path)
    held = book.reserve("chat-1")
    book.settle(held, 0.5)
    book.close(held, complete=True)
    with open(tmp_path / spend_ledger.LEDGER_FILE, "ab") as handle:
        handle.write(b'{"v":1,"type":"settle","id":"x","cost":"lots"\n')   # damaged
    problem = book.can_spend()
    assert "damaged (line 4" in problem
    with pytest.raises(SpendRefused):
        book.reserve("chat-1")
    # It never frees anything up: what was counted still is.
    assert book.totals().spent == pytest.approx(0.5)
    book.reset()
    assert book.can_spend() is None
    assert book.totals().spent == 0


@pytest.mark.parametrize("bad", [
    b"not json", b"[1, 2]", b'{"type": "reserve", "id": "a", "amount": 5}',      # no time
    b'{"type": "reserve", "id": "a", "amount": -1, "at": 1}',
    b'{"type": "reserve", "id": "", "amount": 1, "at": 1}',
    b'{"type": "settle", "id": "a", "cost": 1, "at": 1, "models": {"m": "x"}}',
    b'{"type": "spend", "id": "a", "at": 1}', b'{"type": "reset", "at": 1, "keep": "a"}',
    b'\xff\xfe', b'{"type": "settle", "id": "a", "cost": true, "at": 1}'])
def test_every_kind_of_damage_is_caught(tmp_path, bad):
    (tmp_path / spend_ledger.LEDGER_FILE).write_bytes(bad + b"\n")
    assert "damaged" in ledger(tmp_path).can_spend()


def test_a_half_written_last_line_is_damage_too(tmp_path):
    book = ledger(tmp_path)
    book.reserve("chat-1")
    with open(tmp_path / spend_ledger.LEDGER_FILE, "ab") as handle:
        handle.write(b'{"v":1,"type":"sett')
    assert "damaged" in book.can_spend()


def test_reset_keeps_history_and_this_runs_live_turns(tmp_path):
    clock = Clock()
    book = ledger(tmp_path, clock)
    done = book.reserve("old")
    book.settle(done, 2.0)
    book.close(done, complete=True)
    running = book.reserve("running")          # a turn still going
    crashed = SpendLedger(tmp_path, clock=clock).reserve("crashed")   # another run's
    assert crashed.id != running.id
    book.reset()
    totals = book.totals()
    assert totals.spent == 0 and totals.held == 5.0          # only the running turn
    assert totals.today == pytest.approx(2.0)                  # history is kept
    assert totals.reset_at == NOON and totals.by_chat == {}
    book.settle(running, 0.25)                 # it settles after the reset
    book.close(running, complete=True)
    totals = book.totals()
    assert totals.spent == pytest.approx(0.25) and totals.held == 0
    assert totals.by_chat == {"running": pytest.approx(0.25)}
    assert [x["type"] for x in lines(tmp_path)].count("reset") == 1


def test_today_and_last_7_days_follow_the_clock(tmp_path):
    clock = Clock(NOON - 8 * DAY)
    book = ledger(tmp_path, clock)
    for when, cost in ((NOON - 8 * DAY, 1.0), (NOON - 3 * DAY, 2.0), (NOON - 60, 4.0)):
        clock.now = when
        held = book.reserve("c")
        book.settle(held, cost)
        book.close(held, complete=True)
    totals = book.totals(now=NOON)
    assert totals.today == pytest.approx(4.0)
    assert totals.last_7_days == pytest.approx(6.0)
    assert totals.spent == pytest.approx(7.0)


def test_limits_are_checked_and_an_unreadable_file_stops_spending(tmp_path):
    book = ledger(tmp_path)
    for overall, per_message in ((0, 5), (-1, 5), (190, 0), (1e9, 5), (True, 5), ("5", 5),
                                 (float("nan"), 5)):
        with pytest.raises(ValueError):
            book.set_limits(overall, per_message)
    book.set_limits(50, 2.5)
    assert (book.limits().overall, book.limits().per_message) == (50, 2.5)
    assert book.reserve("c").amount == 2.5
    (tmp_path / spend_ledger.LIMITS_FILE).write_text("{oops", encoding="utf-8")
    assert "couldn't read its API spending limits" in book.can_spend()
    (tmp_path / spend_ledger.LIMITS_FILE).write_text('{"overall": 0, "per_message": 5}',
                                                     encoding="utf-8")
    assert "damaged" in book.can_spend()
    book.set_limits(50, 2.5)
    assert book.can_spend() is None


def test_a_per_message_limit_under_a_cent_is_refused(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(ValueError):
        book.set_limits(10, 0.001)
    # One edited in by hand is let be, but nothing is sent on it.
    (tmp_path / spend_ledger.LIMITS_FILE).write_text('{"overall": 10, "per_message": 0.004}',
                                                     encoding="utf-8")
    with pytest.raises(SpendRefused) as refused:
        book.reserve("c")
    assert "less than a cent" in str(refused.value)


def test_cents_down_survives_float_drift():
    assert spend_ledger.cents_down(7.5 - 2.6) == 4.9
    assert spend_ledger.cents_down(4.999) == 4.99
    assert spend_ledger.cents_down(0.004) == 0


def test_purpose_is_kept_and_test_key_spending_counts(tmp_path):
    book = ledger(tmp_path)
    test = book.reserve("", purpose="test")
    assert test.purpose == "test"
    book.settle(test, 0.0043, {"claude-haiku-5-5": 0.0043})
    book.close(test, complete=True)
    assert book.totals().spent == pytest.approx(0.0043)
    assert lines(tmp_path)[0]["purpose"] == "test"
    assert book.reserve("c", purpose="odd").purpose == "turn"


def test_a_failed_write_refuses_rather_than_spends(tmp_path, monkeypatch):
    book = ledger(tmp_path)

    def broken(self, record):
        raise OSError("disk full")
    monkeypatch.setattr(SpendLedger, "_append", broken)
    with pytest.raises(SpendRefused) as refused:
        book.reserve("c")
    assert "couldn't record" in str(refused.value)


# -- several results in one process -------------------------------------------------------


def test_cumulative_totals_settle_the_difference():
    tracker = CostTracker(cumulative=True)
    assert tracker.add(0.10, {"opus": 0.10}) == (pytest.approx(0.10), {"opus": pytest.approx(0.10)})
    added, models = tracker.add(0.25, {"opus": 0.20, "haiku": 0.05})
    assert added == pytest.approx(0.15)
    assert models == {"opus": pytest.approx(0.10), "haiku": pytest.approx(0.05)}
    assert tracker.total == pytest.approx(0.25)
    assert tracker.models == {"opus": pytest.approx(0.20), "haiku": pytest.approx(0.05)}


def test_a_total_that_goes_down_is_a_fresh_amount_never_negative():
    tracker = CostTracker(cumulative=True)
    tracker.add(0.30)
    added, _ = tracker.add(0.05)           # per-result after all: counted, not subtracted
    assert added == pytest.approx(0.05) and tracker.total == pytest.approx(0.35)


def test_per_result_costs_when_not_cumulative():
    tracker = CostTracker(cumulative=False)
    tracker.add(0.10)
    added, _ = tracker.add(0.25)
    assert added == pytest.approx(0.25) and tracker.total == pytest.approx(0.35)


@pytest.mark.parametrize("cost", [None, "0.1", True, float("nan"), -0.5])
def test_an_odd_cost_adds_nothing(cost):
    assert CostTracker().add(cost, {"m": "x", 5: 1.0}) == (0.0, {})
