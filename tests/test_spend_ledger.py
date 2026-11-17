"""The API credits total and its limits (#658). Windowless; each test has its
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


def test_a_total_that_shrinks_within_a_process_adds_nothing_and_rebaselines():
    tracker = CostTracker(cumulative=True)
    tracker.add(0.30)
    added, models = tracker.add(0.05)
    assert added == 0 and models == {} and tracker.total == pytest.approx(0.30)
    added, _ = tracker.add(0.08)           # carries on from the smaller total
    assert added == pytest.approx(0.03) and tracker.total == pytest.approx(0.33)


# -- the chat's baseline (#658: totals include restored cost state) --------------------------


#: (the result's own tokens, modelUsage's): restored (more in modelUsage) or not
#: (#663; checked with Claude Code 2.1.325).
RESTORED, OWN_ONLY = (120, 5400), (120, 120)


def test_a_resumed_process_counts_only_what_it_added_to_the_baseline():
    # Measured with Claude Code 2.1.325: turn 1 0.00125, turn 2 (resumed) 0.00153.
    tracker = CostTracker(start=0.00125, start_models={"haiku": 0.00125}, cap=5.0)
    added, models = tracker.add(0.00153, {"haiku": 0.00153}, RESTORED)
    assert tracker.restored is True
    assert added == pytest.approx(0.00028) and models == {"haiku": pytest.approx(0.00028)}
    added, _ = tracker.add(0.00200, {"haiku": 0.00200})      # a second result, same process
    assert added == pytest.approx(0.00047) and tracker.total == pytest.approx(0.00075)


def test_a_model_this_turn_didnt_use_still_fits_its_baseline(tmp_path):
    # Restored, an unused model's cost is its baseline exactly; kept exactly,
    # so it isn't counted again.
    book = ledger(tmp_path)
    book.set_baseline("c", 0.0012345678901234, {"opus": 0.0011111111111111,
                                                "haiku": 0.0001234567790123})
    total, models = book.baseline("c")
    tracker = CostTracker(start=total, start_models=models, cap=5.0)
    added, by_model = tracker.add(0.0012345678901234 + 0.002,
                                  {"opus": 0.0011111111111111 + 0.002,
                                   "haiku": 0.0001234567790123}, RESTORED)
    assert added == pytest.approx(0.002) and by_model == {"opus": pytest.approx(0.002)}


@pytest.mark.parametrize("tokens", [RESTORED, None])
def test_an_unknown_baseline_counts_the_first_result_up_to_the_reservation(tokens):
    # Restored with nothing to take off (a desktop chat continued here), or
    # no tokens to tell by: no more than the process itself can spend.
    tracker = CostTracker(start=None, cap=5.0)
    added, models = tracker.add(12.0, {"opus": 9.0, "haiku": 3.0}, tokens)
    assert added == pytest.approx(5.0)
    assert models == {"opus": pytest.approx(3.75), "haiku": pytest.approx(1.25)}
    added, _ = tracker.add(12.5, {"opus": 9.4, "haiku": 3.1})         # then the growth
    assert added == pytest.approx(0.5) and tracker.total == pytest.approx(5.5)
    small = CostTracker(start=None, cap=5.0)
    assert small.add(0.02, None, tokens)[0] == pytest.approx(0.02)


def test_a_restored_total_below_its_baseline_is_counted_up_to_the_reservation():
    # The baseline can't be what was restored: some of the total is still
    # someone else's, so the reservation bounds it.
    tracker = CostTracker(start=0.80, start_models={"opus": 0.80}, cap=0.50, resumed=True)
    assert tracker.add(0.70, {"opus": 0.70}, RESTORED)[0] == pytest.approx(0.50)


@pytest.mark.parametrize("total", [0.30, 0.82, 1.40, 7.0])
def test_a_total_that_wasnt_restored_is_counted_in_full_whatever_its_size(total):
    # The review's case (#663): another chat ran in between, so Claude Code
    # restored nothing. A total above the baseline was taken as restored and
    # only its growth counted; the tokens say it's all this process's, and
    # it's counted in full, past the reservation too (a request's overrun).
    tracker = CostTracker(start=0.80, start_models={"opus": 0.80}, cap=5.0, resumed=True)
    added, models = tracker.add(total, {"opus": total}, OWN_ONLY)
    assert tracker.restored is False
    assert added == pytest.approx(total) and models == {"opus": pytest.approx(total)}
    assert tracker.add(total + 0.1, {"opus": total + 0.1})[0] == pytest.approx(0.1)


def test_two_chats_alternating_in_a_folder_are_each_counted_once():
    # The review's numbers: unrestored totals each above the last baseline
    # were counted as $0.60 against a real $3.47. With the tokens, it's $3.47.
    costs = [0.30, 0.32, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    baselines, counted = {}, 0.0
    for turn, cost in enumerate(costs):
        chat = "A" if turn % 2 == 0 else "B"
        start = baselines.get(chat)
        tracker = CostTracker(start=start if start is not None else 0.0, cap=5.0,
                              resumed=start is not None)
        counted += tracker.add(cost, {"m": cost}, OWN_ONLY)[0]
        baselines[chat] = cost
    assert counted == pytest.approx(sum(costs))


def test_a_new_chat_restores_nothing_whatever_the_tokens_say():
    # Side requests can make modelUsage bigger; a new chat has nothing to restore.
    tracker = CostTracker(start=0.0, cap=0.10)
    assert tracker.add(0.30, {"m": 0.30}, RESTORED)[0] == pytest.approx(0.30)


@pytest.mark.parametrize("tokens", [(1, 2, 3), ("1", 2), (-1, 5), (True, 5), [5], "x"])
def test_odd_token_counts_are_no_evidence(tokens):
    tracker = CostTracker(start=0.5, cap=0.2, resumed=True)
    assert tracker.add(0.9, None, tokens)[0] == pytest.approx(0.2)
    assert tracker.restored is None


def test_baselines_are_kept_across_runs_and_resets(tmp_path):
    book = ledger(tmp_path)
    assert book.baseline("chat-1") is None
    book.set_baseline("chat-1", 0.00153, {"claude-haiku-5-5": 0.00153})
    book.set_baseline("chat-2", 1.5)
    book.reset()
    again = ledger(tmp_path)
    assert again.baseline("chat-1") == (pytest.approx(0.00153),
                                        {"claude-haiku-5-5": pytest.approx(0.00153)})
    assert again.baseline("chat-2") == (pytest.approx(1.5), {})
    again.set_baseline("chat-1", 0.002)
    assert again.baseline("chat-1")[0] == pytest.approx(0.002)
    # Not spending: the total doesn't see them.
    assert again.totals().spent == 0 and again.can_spend() is None


@pytest.mark.parametrize("bad", ["{oops", "[1]", '{"chat-1": {"total": -1}}',
                                 '{"chat-1": {"total": "lots"}}'])
def test_a_damaged_baseline_is_not_known(tmp_path, bad):
    (tmp_path / spend_ledger.BASELINES_FILE).write_text(bad, encoding="utf-8")
    book = ledger(tmp_path)
    assert book.baseline("chat-1") is None
    book.set_baseline("chat-1", 0.5)           # and writing one mends it
    assert book.baseline("chat-1")[0] == pytest.approx(0.5)


def test_odd_baselines_are_not_recorded(tmp_path):
    book = ledger(tmp_path)
    for chat, total in (("", 1.0), ("c", None), ("c", -1), ("c", True), ("c", float("inf"))):
        book.set_baseline(chat, total)
    assert not (tmp_path / spend_ledger.BASELINES_FILE).exists()


# -- a torn last line, and Release Held ------------------------------------------------------


def test_a_torn_last_line_can_still_be_reset(tmp_path):
    book = ledger(tmp_path)
    book.reserve("chat-1")
    with open(tmp_path / spend_ledger.LEDGER_FILE, "ab") as handle:
        handle.write(b'{"v":1,"type":"sett')   # a crash mid-write
    assert "damaged" in book.can_spend()
    book.reset()                               # its own line, not the torn one's end
    assert book.can_spend() is None
    held = book.reserve("chat-2")
    assert held.amount == 5.0
    raw = (tmp_path / spend_ledger.LEDGER_FILE).read_bytes().split(b"\n")
    assert raw[1] == b'{"v":1,"type":"sett'    # left as it was, on a line of its own


def test_release_held_lets_go_of_old_holds_but_not_running_ones_or_spending(tmp_path):
    clock = Clock()
    crashed = SpendLedger(tmp_path, clock=clock)         # an earlier run
    old = crashed.reserve("old")
    crashed.settle(old, 1.0)
    book = SpendLedger(tmp_path, clock=clock)            # this run
    running = book.reserve("running")
    stopped = book.reserve("stopped")
    book.settle(stopped, 0.5)
    book.close(stopped, complete=False)                  # Stop, cost not known
    assert json.loads((tmp_path / spend_ledger.LEDGER_FILE).read_text(
        encoding="utf-8").splitlines()[0])["run"] == crashed.run_id
    totals = book.totals()
    assert totals.held == pytest.approx(4.0 + 5.0 + 4.5)
    assert totals.releasable == pytest.approx(4.0 + 4.5)
    assert book.release_held() == pytest.approx(8.5)
    totals = book.totals()
    assert totals.held == pytest.approx(5.0) and totals.releasable == 0
    assert totals.spent == pytest.approx(1.5)            # spending stays counted
    assert book.release_held() == 0                      # nothing more to let go
    book.settle(running, 0.2)
    book.close(running, complete=True)
    assert book.totals().held == 0 and book.totals().spent == pytest.approx(1.7)
    assert SpendLedger(tmp_path, clock=clock).totals().held == 0    # and in the next run


def test_a_damaged_release_line_is_damage(tmp_path):
    (tmp_path / spend_ledger.LEDGER_FILE).write_bytes(
        b'{"v":1,"at":1,"type":"release","ids":[1]}\n')
    assert "damaged" in ledger(tmp_path).can_spend()


# -- damage that must never free spending or break the ledger (#663) ------------------------


def _ledger_with(tmp_path, *lines):
    book = ledger(tmp_path)
    held = book.reserve("chat-1")
    with open(tmp_path / spend_ledger.LEDGER_FILE, "ab") as handle:
        for line in lines:
            handle.write(line.replace(b"RID", held.id.encode()) + b"\n")
    return book, held


@pytest.mark.parametrize("complete", [b'"false"', b'"true"', b"1", b"0", b"null", b"[]"])
def test_a_close_that_isnt_exactly_true_keeps_its_hold(tmp_path, complete):
    book, held = _ledger_with(
        tmp_path, b'{"v":1,"at":1,"type":"close","id":"RID","complete":' + complete + b"}")
    totals = book.totals()
    assert totals.held == pytest.approx(held.amount) and totals.problem == ""
    book2, held2 = _ledger_with(tmp_path / "x", b'{"v":1,"at":1,"type":"close","id":"RID"}')
    assert book2.totals().held == pytest.approx(held2.amount)


@pytest.mark.parametrize("line", [
    b'{"v":1,"at":' + b"9" * 401 + b',"type":"settle","id":"RID","cost":1}',   # overflow
    b'{"v":1,"at":1e400,"type":"settle","id":"RID","cost":1}',
    b'{"v":1,"at":NaN,"type":"settle","id":"RID","cost":1}',
    b'{"v":1,"at":Infinity,"type":"reserve","id":"x","amount":1}',
    b'{"v":1,"at":1e15,"type":"settle","id":"RID","cost":1}',                 # no time at all
    b'{"v":1,"at":1,"type":"settle","id":"RID","cost":' + b"9" * 401 + b"}",
    b'{"v":1,"at":1,"type":"settle","id":"RID","cost":NaN}',
    b'{"v":1,"at":1,"type":"settle","id":"RID","cost":1e300}',                 # absurd
    b'{"v":1,"at":1,"type":"settle","id":"RID","cost":0.5,"models":{"m":1e300}}',
    b'{"v":1,"at":1,"type":"settle","id":"RID","cost":0.5,"models":{"m":-1}}',
    b'{"v":1,"at":1,"type":"reserve","id":"y","amount":1e300}',
    b'{"v":1,"at":1,"type":"reserve","id":"y","amount":' + b"9" * 401 + b"}",
    b'{"v":1,"at":1,"type":"reset","keep":[1]}',
    b'{"v":1,"at":1,"type":"rename","from":"a"}',
    b'{"v":1,"at":1,"type":"rename","from":"a","to":7}',
    b"[" * 100000 + b"]" * 100000,                                            # too deep
])
def test_any_damaged_line_stops_spending_and_reset_always_recovers(tmp_path, line):
    book, held = _ledger_with(tmp_path, line)
    totals = book.totals()                                # never raises
    assert "damaged" in totals.problem and "damaged" in book.can_spend()
    assert totals.held == pytest.approx(held.amount)      # nothing freed
    with pytest.raises(SpendRefused):
        book.reserve("chat-2")
    book.reset()
    assert book.can_spend() is None
    assert book.reserve("chat-2").amount == pytest.approx(5.0)


def test_a_replay_that_would_raise_is_damage_and_reset_recovers(tmp_path, monkeypatch):
    book, _held = _ledger_with(tmp_path, b'{"v":1,"at":1,"type":"settle","id":"RID","cost":1}')
    real = SpendLedger._replay_line

    def broken(*args):
        raise OverflowError("anything at all")
    monkeypatch.setattr(SpendLedger, "_replay_line", staticmethod(broken))
    assert "damaged" in book.totals().problem
    book.reset()
    assert book.can_spend() is None           # the reset line itself replays fine
    monkeypatch.setattr(SpendLedger, "_replay_line", staticmethod(real))


def test_a_failed_settlement_write_keeps_the_reservation_counted(tmp_path, monkeypatch):
    # The review's case (#663): reserve $5, spend $4, the settle append fails
    # once, the close succeeds. The hold must stay: nothing was recorded.
    book = ledger(tmp_path)
    held = book.reserve("chat-1")
    real = SpendLedger._append
    failing = {"settle": True}

    def flaky(self, record):
        if record["type"] == "settle" and failing.pop("settle", False):
            raise OSError("disk full for a moment")
        return real(self, record)
    monkeypatch.setattr(SpendLedger, "_append", flaky)
    assert book.settle(held, 4.0) is False
    book.close(held, complete=True)
    totals = book.totals()
    assert totals.spent == 0 and totals.held == pytest.approx(5.0)
    assert totals.counted == pytest.approx(5.0)
    close = json.loads((tmp_path / spend_ledger.LEDGER_FILE).read_text(
        encoding="utf-8").splitlines()[-1])
    assert close["type"] == "close" and close["complete"] is False
    # A later reservation's close isn't affected.
    other = book.reserve("chat-2")
    assert book.settle(other, 0.1) is True
    book.close(other, complete=True)
    assert book.totals().held == pytest.approx(5.0)


def test_settle_refuses_a_cost_that_isnt_one(tmp_path):
    book = ledger(tmp_path)
    held = book.reserve("chat-1")
    for odd in (None, float("nan"), -1.0, float("inf"), "1"):
        assert book.settle(held, odd) is False
    book.close(held, complete=True)
    assert book.totals().held == pytest.approx(5.0)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -0.01, "0.1", True])
def test_valid_cost_is_a_finite_number_not_below_zero(value):
    assert spend_ledger.valid_cost(value) is False
    assert spend_ledger.valid_cost(0) and spend_ledger.valid_cost(0.25)


def test_a_renamed_chat_keeps_its_spending_and_baseline(tmp_path):
    # Claude Code gave the chat another id than the one asked for (#663).
    book = ledger(tmp_path)
    held = book.reserve("old-id")
    book.settle(held, 0.4)
    book.set_baseline("old-id", 0.4, {"m": 0.4})
    book.rename_chat("old-id", "new-id")
    book.settle(held, 0.1)                    # the running turn's reservation names the old id
    book.close(held, complete=True)
    totals = book.totals()
    assert totals.by_chat == {"new-id": pytest.approx(0.5)}
    assert totals.chat_all_time == {"new-id": pytest.approx(0.5)}
    assert book.baseline("new-id")[0] == pytest.approx(0.4) and book.baseline("old-id") is None
    book.rename_chat("new-id", "new-id")      # nothing to do
    book.rename_chat("", "x")
    assert ledger(tmp_path).totals().by_chat == {"new-id": pytest.approx(0.5)}


def test_per_result_costs_when_not_cumulative():
    tracker = CostTracker(cumulative=False)
    tracker.add(0.10)
    added, _ = tracker.add(0.25)
    assert added == pytest.approx(0.25) and tracker.total == pytest.approx(0.35)


@pytest.mark.parametrize("cost", [None, "0.1", True, float("nan"), -0.5])
def test_an_odd_cost_adds_nothing(cost):
    assert CostTracker().add(cost, {"m": "x", 5: 1.0}) == (0.0, {})
