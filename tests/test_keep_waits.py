"""#749 Part 2: a Claude turn kept open for the waits Claude set, its wake
turns, and what ends it. The runner against tests/fake_claude.py's wake
sequence (the probe's, Claude Code 2.1.331), and the plain rules in
scheduled_waits. No wx: runs on every platform's CI."""
import json
import sys
import threading
import time
from datetime import datetime, timedelta

from thechatplace import claude_cli as cli
from thechatplace import scheduled_waits as sw
from thechatplace.billing import ROUTE_API
from thechatplace.claude_cli import TurnRunner
from thechatplace.scheduled_waits import CRON, WAKEUP, WATCH, Wait, WaitSlots

from test_claude_cli import FAKE_CLAUDE, FakeTimers

#: A Friday afternoon: Nov 27 2026 at 1:00 PM local time.
NOW = datetime(2026, 11, 27, 13, 0)
#: A one-time timer due a minute from NOW, so its wake (at NOW) is its own.
SOON = "1 13 27 11 *"


def at(dt: datetime) -> float:
    return dt.timestamp()


# -- What holds the process open, and for how long -------------------------------------


def cron(expression, recurring=False, set_at=None, **extra):
    return Wait(CRON, tool_use_id=f"t-{expression}-{recurring}", cron=expression,
                recurring=recurring, set_at=at(NOW) if set_at is None else set_at, **extra)


def test_timers_due_within_seven_days_hold_and_further_ones_dont():
    soon, later = cron("0 14 27 11 *"), cron("0 14 3 12 *")      # in 1 hour, 6 days
    far = cron("0 14 10 12 *")                                     # 13 days
    assert sw.held_waits([soon, later, far], frozenset(), at(NOW)) == [soon, later]
    assert sw.far_waits([soon, later, far], at(NOW)) == [far]
    # The ceiling is one obvious number.
    assert sw.MAX_HOLD_DAYS == 7 and sw.DEFAULT_WAIT_LIMIT == 5


def test_a_repeating_timer_holds_until_claude_codes_expiry():
    every = cron("*/30 * * * *", recurring=True)
    assert sw.held_waits([every], frozenset(), at(NOW)) == [every]
    month_on = at(NOW + timedelta(days=sw.RECURRING_EXPIRY_DAYS, minutes=1))
    assert sw.held_waits([every], frozenset(), month_on) == []
    # Not too far either: it's past Claude Code's own expiry, not a far one.
    assert sw.far_waits([every], month_on) == []
    monthly = cron("0 9 1 * *", recurring=True)                   # next: Dec 1, 4 days
    assert sw.held_waits([monthly], frozenset(), at(NOW)) == [monthly]
    yearly = cron("0 9 1 6 *", recurring=True)
    assert sw.far_waits([yearly], at(NOW)) == [yearly]


def test_a_one_time_check_that_never_fired_stops_holding_after_a_grace():
    due = cron("0 13 27 11 *", set_at=at(NOW - timedelta(minutes=5)))  # due at NOW
    assert sw.held_waits([due], frozenset(), at(NOW)) == [due]
    late = at(NOW) + sw.OVERDUE_GRACE_SECONDS + 1
    assert sw.held_waits([due], frozenset(), late) == []
    assert sw.next_recheck([due], at(NOW)) == at(NOW) + sw.OVERDUE_GRACE_SECONDS


def test_a_watch_holds_only_while_its_task_is_listed():
    watch = Wait(WATCH, tool_use_id="m", description="Watch PR 749", set_at=at(NOW))
    assert sw.held_waits([watch], frozenset({"Watch PR 749"}), at(NOW)) == [watch]
    assert sw.held_waits([watch], frozenset(), at(NOW)) == []
    assert sw.next_recheck([watch], at(NOW)) is None


def test_an_unreadable_timer_holds_for_the_ceiling_and_a_durable_one_never():
    odd = cron("0 14 31 2 *")    # never matches: its time can't be told
    assert sw.held_waits([odd], frozenset(), at(NOW)) == [odd]
    week_on = at(NOW + timedelta(days=sw.MAX_HOLD_DAYS, minutes=1))
    assert sw.held_waits([odd], frozenset(), week_on) == []
    durable = cron("0 14 27 11 *", durable=True)
    assert sw.held_waits([durable], frozenset(), at(NOW)) == []


def test_a_wakeup_uses_the_time_claude_code_said():
    book = sw.WaitBook(clock=lambda: at(NOW))
    book.tool_use(sw.SCHEDULE_WAKEUP, "w1", {"delaySeconds": 60, "prompt": "Ping"})
    book.tool_result("w1", "Next wakeup scheduled for 13:02:00 (in 97s). If you owe the user "
                     "a status update this tick, write it now.", False)
    [wait] = book.waits
    assert wait.fire_time() == NOW + timedelta(seconds=97)
    # Its wake (loop_wakeup) settles it, whatever the clock says.
    assert book.woke(sw.LOOP_WAKE) and book.waits == []


def test_what_a_kept_chat_says_it_waits_for():
    soon = cron("0 14 27 11 *")
    every = cron("*/30 * * * *", recurring=True)
    daily = cron("0 9 * * 1-5", recurring=True)
    watch = Wait(WATCH, description="Watch PR 749")
    assert sw.waiting_words([soon], NOW) == "Scheduled check at 2:00 PM."
    assert sw.waiting_words([every], NOW) == (
        "Scheduled check at 1:30 PM, and every 30 minutes after that.")
    assert sw.waiting_words([daily], NOW) == (
        "Scheduled check at 9:00 AM on Monday, November 30, then every weekday at 9:00 AM.")
    assert sw.waiting_words([soon, watch], NOW) == (
        "Scheduled check at 2:00 PM. Watching: Watch PR 749.")
    assert sw.waiting_words([cron("0 14 31 2 *")], NOW) == "A scheduled check is set."
    assert sw.waiting_words([], NOW) == ""
    assert sw.row_detail([every, soon], NOW) == "next check at 1:30 PM"
    assert sw.row_detail([watch], NOW) == "watching: Watch PR 749"
    assert sw.row_detail([cron("0 14 31 2 *")], NOW) == "a scheduled check is set"
    assert sw.row_detail([], NOW) == ""


def test_what_stop_and_quit_say():
    soon = cron("0 14 27 11 *")
    watch = Wait(WATCH, description="Watch PR 749")
    assert sw.stop_words([soon], NOW) == "Stopping ends the check Claude set for 2:00 PM too."
    assert sw.stop_words([soon, watch], NOW) == (
        "Stopping ends the check Claude set for 2:00 PM, and the watch Claude started "
        "(Watch PR 749) too.")
    assert sw.stop_words([], NOW) == ""
    assert sw.quit_words(0) == ""
    assert sw.quit_words(1) == "1 chat is waiting for checks Claude set, which won't run if you quit."
    assert sw.quit_words(2) == "2 chats are waiting for checks Claude set, which won't run if you quit."


def test_the_lines_for_a_far_check_and_a_chat_that_stopped_waiting():
    far = cron("0 14 10 12 *")
    assert sw.lost_sentence([far], NOW, start=sw.far_start()) == (
        "That's more than 7 days away, too long to keep the chat open, so the check Claude "
        "set for 2:00 PM on Thursday, December 10 won't run.")
    assert sw.lost_sentence([cron("0 14 27 11 *")], NOW, start=sw.ENDED_START) == (
        "This chat stopped waiting, so the check Claude set for 2:00 PM won't run.")
    # Both are kept after their reply, as Part 1's line is.
    for line in (sw.lost_sentence([far], NOW, dated=True, start=sw.far_start()),
                 sw.lost_sentence([far], NOW, dated=True, start=sw.ENDED_START)):
        assert sw.clean_notes({"m": line}) == {"m": line}


def test_a_scheduled_checks_answer_is_announced_as_one():
    from thechatplace import announce
    assert announce.reply_text("PR 749", "The checks passed.", announce.ANNOUNCE_FULL,
                               woke=True) == "PR 749, scheduled check: The checks passed."
    assert announce.reply_text("PR 749", "The checks passed. All green.",
                               announce.ANNOUNCE_SUMMARY, cost="6 cents", woke=True) == (
        "PR 749, scheduled check, 6 cents: The checks passed. 5 words.")
    assert announce.reply_text("PR 749", "x", announce.ANNOUNCE_SILENT, woke=True) is None


def test_properties_say_what_a_kept_chat_waits_for(tmp_path):
    from thechatplace import properties
    from thechatplace.own_store import OwnSession
    own = OwnSession("chat-1", "PR 749", str(tmp_path))
    lines = properties.session_properties(own.to_info(), own=own, now_ms=0,
                                          waits="Scheduled check at 2:00 PM.")
    status = next(i for i, line in enumerate(lines) if line.startswith("Status: "))
    assert lines[status + 1] == "Waiting for: Scheduled check at 2:00 PM."
    assert not any(line.startswith("Waiting for:")
                   for line in properties.session_properties(own.to_info(), own=own, now_ms=0))


def test_two_lines_after_one_reply_are_both_kept():
    far = sw.lost_sentence([cron("0 14 10 12 *")], NOW, start=sw.far_start())
    ended = sw.lost_sentence([cron("*/30 * * * *", recurring=True)], NOW, start=sw.ENDED_START)
    notes = sw.remember_note(sw.remember_note({}, "m1", far), "m1", ended)
    assert notes == {"m1": f"{far} {ended}"}
    assert sw.remember_note(notes, "m1", ended) == notes      # not twice
    assert sw.clean_notes(notes) == notes


def test_wait_slots_follow_the_setting_and_free_on_release():
    limit = [2]
    slots = WaitSlots(lambda: limit[0])
    a, b, c = object(), object(), object()
    assert slots.take(a) and slots.take(a) and slots.take(b)
    assert not slots.take(c) and len(slots) == 2
    slots.release(a)
    assert slots.take(c)
    limit[0] = 0
    assert not slots.take(a)          # a lower limit applies to the next to ask
    assert WaitSlots(lambda: "x").take(a) is False  # a broken setting holds nothing


# -- The runner, against the fake's wake sequence ------------------------------------------


class Run:
    """A turn against fake_claude.py's cron mode, events collected."""

    def __init__(self, tmp_path, monkeypatch, limit=5, command_extra=(), timer=None,
                 slots=None, no_hold=False, **env):
        monkeypatch.setattr(sw, "wall_clock", lambda: at(NOW))
        self.log = tmp_path / "fake.log"
        variables = {"FAKE_CLAUDE_LOG": str(self.log), "FAKE_CLAUDE_MODE": "cron",
                     "FAKE_CLAUDE_CRON": SOON, **env}
        self.events = []
        self.changed = threading.Condition()
        self.slots = slots if slots is not None else WaitSlots(lambda: limit)
        kwargs = {"timer": timer} if timer is not None else {}
        allow = env.get("FAKE_CLAUDE_WAKE_OVERAGE") == "1"
        self.runner = TurnRunner([sys.executable, str(FAKE_CLAUDE), *command_extra],
                                 str(tmp_path), "go", self.on_event,
                                 env=cli.child_environment({**cli.child_environment(),
                                                            **variables}),
                                 wait_slots=self.slots, allow_overage=allow, **kwargs)
        self.runner.no_hold_for_waits = no_hold
        self.runner.start()

    def on_event(self, event):
        with self.changed:
            self.events.append(event)
            self.changed.notify_all()
        if event.kind == "permission" and getattr(self, "answer", None):
            threading.Thread(target=self.answer, args=(event,), daemon=True).start()

    def wait_for(self, test, timeout=30):
        deadline = time.monotonic() + timeout
        with self.changed:
            while not test(self.events):
                left = deadline - time.monotonic()
                assert left > 0, [e.kind for e in self.events]
                self.changed.wait(left)

    def count(self, kind):
        return sum(1 for e in self.events if e.kind == kind)

    def done(self, timeout=30):
        self.wait_for(lambda events: events and events[-1].kind in ("finished", "failed"),
                      timeout)
        self.runner.join(10)
        return self.events[-1]

    def waiting(self):
        return [e for e in self.events if e.kind == "waiting"]


def fake_log(run, field, timeout=10):
    """A field of the fake's log, once written whole: the fake rewrites it
    as it goes, and a read can land mid-write (seen on a Linux arm64 runner)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = json.loads(run.log.read_text(encoding="utf-8"))
            if field in value:
                return value[field]
        except (OSError, ValueError):
            pass
        assert time.monotonic() < deadline, f"no {field} in the fake's log"
        time.sleep(0.05)


def test_a_one_time_timer_holds_the_turn_then_wakes_and_closes(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_WAKE="0.5")
    final = run.done()
    kinds = [e.kind for e in run.events]
    # Held: the reply said with what it waits for, the wake, its answer last.
    assert kinds.index("waiting") < kinds.index("woke") < len(kinds) - 1
    [waiting] = run.waiting()
    assert waiting.text == "I'll check the pull request again at 2:00 PM."
    assert waiting.data["scheduled"] is True and waiting.data["far"] == []
    assert [w.cron for w in sw.from_dicts(waiting.data["waits"])] == [SOON]
    assert waiting.data["reply_message_id"] == "msg_reply"
    assert final.kind == "finished" and final.text == "Scheduled check 1: the checks passed."
    assert final.data["woke"] == "schedule_wakeup"
    assert "waits" not in final.data          # it ran: nothing lost
    assert run.runner.held_for_waits and not run.runner.waiting_for_waits
    assert len(run.slots) == 0                # its place is free again
    assert fake_log(run, "wakes") == 1


def test_a_repeating_timer_keeps_the_turn_through_each_wake(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_CRON="*/30 * * * *",
              FAKE_CLAUDE_RECURRING="1", FAKE_CLAUDE_WAKE="0.3", FAKE_CLAUDE_WAKES="2")
    run.wait_for(lambda events: len([e for e in events if e.kind == "waiting"]) == 3)
    first, second, third = run.waiting()
    assert "woke" not in first.data
    assert (second.text, second.data["woke"]) == ("Scheduled check 1: the checks passed.",
                                                  "schedule_wakeup")
    assert third.text == "Scheduled check 2: the checks passed."
    run.wait_for(lambda events: run.runner.waiting_for_waits)
    assert run.runner.kept_open and len(run.slots) == 1
    assert run.runner.last_activity == "waiting for a scheduled check"
    # Stop ends the process and the timer with it: "stopped waiting".
    run.runner.cancel()
    final = run.done()
    assert final.kind == "failed" and final.text == "Stopped."
    assert final.data["held"] is True
    assert [w.recurring for w in sw.from_dicts(final.data["waits"])] == [True]
    assert len(run.slots) == 0


def test_cron_delete_in_a_wake_turn_releases_the_turn(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_CRON="*/30 * * * *",
              FAKE_CLAUDE_RECURRING="1", FAKE_CLAUDE_WAKE="0.3",
              FAKE_CLAUDE_WAKE_DELETE="1")
    final = run.done()
    assert final.kind == "finished" and final.data["woke"] == "schedule_wakeup"
    assert "waits" not in final.data and run.runner.pending_waits == []


def test_no_place_under_the_limit_ends_the_turn_with_the_reply(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, limit=0, FAKE_CLAUDE_WAKE="0.3")
    final = run.done()
    assert run.count("waiting") == 0 and run.count("woke") == 0
    assert final.kind == "finished" and "held" not in final.data
    assert [w.cron for w in sw.from_dicts(final.data["waits"])] == [SOON]  # Part 1's line
    # Taken by another chat: the same.
    slots = WaitSlots(lambda: 1)
    slots.take("another chat")
    run = Run(tmp_path, monkeypatch, slots=slots, FAKE_CLAUDE_WAKE="0.3")
    assert run.done().kind == "finished" and run.count("waiting") == 0


def test_a_turn_told_not_to_hold_ends_with_its_reply(tmp_path, monkeypatch):
    # A folder taken away mid-answer (#749): the window sets the flag, and
    # the process mustn't then stay open with the folder still reachable.
    monkeypatch.setattr(sw, "wall_clock", lambda: at(NOW))
    runner = TurnRunner(["claude"], ".", "go", lambda e: None, wait_slots=WaitSlots(lambda: 5))
    runner._stdin_open = True
    runner.pending_waits = [cron(SOON)]
    assert runner._hold_for_waits_locked()
    runner.wait_slots.release(runner)
    runner.no_hold_for_waits = True
    assert not runner._hold_for_waits_locked()
    # And through the real fake: the turn ends with the reply, saying the check won't run.
    run = Run(tmp_path, monkeypatch, no_hold=True, FAKE_CLAUDE_WAKE="5")
    final = run.done()
    assert run.count("waiting") == 0 and final.kind == "finished"
    assert "held" not in final.data
    assert [w.cron for w in sw.from_dicts(final.data["waits"])] == [SOON]


def test_a_far_timer_doesnt_hold_and_is_said_once(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_CRON="0 14 10 12 *")
    final = run.done()
    assert run.count("waiting") == 0
    assert final.kind == "finished" and "held" not in final.data
    assert [w.cron for w in sw.from_dicts(final.data["waits"])] == ["0 14 10 12 *"]


def test_an_api_credit_chat_never_keeps_waits():
    runner = TurnRunner(["claude"], ".", "go", lambda e: None, billing=ROUTE_API,
                        ledger=object(), wait_slots=WaitSlots(lambda: 5))
    runner._stdin_open = True
    runner.pending_waits = [Wait(WAKEUP, "w", set_at=sw.wall_clock(), delay_seconds=60)]
    assert not runner._hold_for_waits_locked()
    runner.billing = "subscription"
    assert runner._hold_for_waits_locked()
    # Without the window's slots (Part 1's runner), never.
    runner.wait_slots = None
    assert not runner._hold_for_waits_locked()


def test_a_wake_turn_on_an_api_key_is_refused(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_WAKE="0.3",
              FAKE_CLAUDE_WAKE_SOURCE="ANTHROPIC_API_KEY")
    final = run.done()
    assert run.count("woke") == 1
    assert final.kind == "failed" and final.is_error
    assert "API" in final.text or "key" in final.text.lower()
    # Your message was answered long ago: nothing comes back to the box.
    assert run.runner.stopped_mid_turn and not run.runner.stopped_before_answer
    assert run.runner.held_for_waits and len(run.slots) == 0


def test_a_wake_turn_never_goes_on_usage_credits(tmp_path, monkeypatch):
    # The turn that set the timer was allowed usage credits (on Fable); the
    # wake isn't: that yes was for that message.
    run = Run(tmp_path, monkeypatch, command_extra=("--model", "fable"),
              FAKE_CLAUDE_MODEL="claude-fable-5-2", FAKE_CLAUDE_WAKE="0.3",
              FAKE_CLAUDE_WAKE_OVERAGE="1")
    final = run.done()
    assert run.count("waiting") == 1 and run.count("woke") == 0
    assert final.kind == "failed" and "usage credits" in final.text
    assert run.runner.stopped_for_overage and run.runner.stopped_mid_turn


def test_a_wake_turn_asking_permission_waits_for_the_answer(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_WAKE="0.3", FAKE_CLAUDE_WAKE_ASK="1")
    run.wait_for(lambda events: any(e.kind == "permission" for e in events))
    [request] = [e.request for e in run.events if e.kind == "permission"]
    time.sleep(1.5)  # nothing answers it by timing out
    assert run.runner.pending and not run.events[-1].kind in ("finished", "failed")
    assert run.runner.respond(request.request_id, {"behavior": "allow",
                                                   "updatedInput": request.input})
    final = run.done()
    assert final.kind == "finished" and final.data["woke"] == "schedule_wakeup"
    assert fake_log(run, "answers") == ["allow"]


def test_a_message_sent_while_kept_open_goes_straight_in(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch)            # no wake: it just waits
    run.wait_for(lambda events: run.runner.waiting_for_waits)
    assert run.runner.send_follow_up("Any news?")
    run.wait_for(lambda events: len([e for e in events if e.kind == "waiting"]) == 2)
    assert run.waiting()[1].text == "Got it: Any news?"
    assert "woke" not in run.waiting()[1].data
    run.wait_for(lambda events: run.runner.waiting_for_waits)  # Claude Code keeps its timer
    run.runner.cancel()
    assert run.done().kind == "failed"


def test_a_check_that_never_comes_ends_the_turn_and_says_so(tmp_path, monkeypatch):
    timers = FakeTimers()
    run = Run(tmp_path, monkeypatch, timer=timers)  # no wake
    run.wait_for(lambda events: run.runner.waiting_for_waits)
    rechecks = [t for t in timers.made
                if t.started and not t.cancelled and t.function == run.runner._recheck_waits]
    assert len(rechecks) == 1
    # Still due: checking early keeps it.
    rechecks[0].function()
    assert run.runner.waiting_for_waits
    # Long past its time with no wake: the process ends.
    monkeypatch.setattr(sw, "wall_clock",
                        lambda: at(NOW) + 60 + sw.OVERDUE_GRACE_SECONDS + 5)
    live = [t for t in timers.made
            if t.started and not t.cancelled and t.function == run.runner._recheck_waits]
    live[-1].function()
    # Given one more grace first, from now: after a sleep, Claude Code
    # fires a missed timer late, and this mustn't beat it.
    assert run.runner.waiting_for_waits
    assert [w.cron for w in run.runner.held_waits()] == [SOON]  # still said as waiting
    regrace = [t for t in timers.made
               if t.started and not t.cancelled and t.function == run.runner._recheck_waits]
    assert regrace[-1].interval == sw.OVERDUE_GRACE_SECONDS
    regrace[-1].function()
    final = run.done()
    assert final.kind == "finished" and final.data.get("after_background")
    assert final.data["held"] is True
    assert [w.cron for w in sw.from_dicts(final.data["waits"])] == [SOON]


def test_a_wakeup_holds_the_turn_and_its_loop_wakeup_settles_it(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_WAKEUP="1", FAKE_CLAUDE_WAKE="0.3")
    final = run.done()
    [waiting] = run.waiting()
    [wait] = sw.from_dicts(waiting.data["waits"])
    assert wait.kind == WAKEUP and wait.fire_time() == NOW + timedelta(seconds=60)
    assert final.kind == "finished" and final.data["woke"] == "loop_wakeup"
    assert "waits" not in final.data


def test_a_wake_announced_before_running_still_gets_its_answer(tmp_path, monkeypatch):
    # command_lifecycle before "running": the timer leaving the list mustn't
    # close the turn it just started.
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_WAKE="0.3",
              FAKE_CLAUDE_WAKE_ORDER="lifecycle-first")
    final = run.done()
    assert final.kind == "finished" and final.text == "Scheduled check 1: the checks passed."
    assert final.data["woke"] == "schedule_wakeup"


def test_your_answer_is_said_even_when_the_check_fires_with_no_idle_between(tmp_path,
                                                                           monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_WAKE="0.3", FAKE_CLAUDE_WAKE_ORDER="no-idle")
    final = run.done()
    [waiting] = run.waiting()
    assert waiting.text == "I'll check the pull request again at 2:00 PM."
    assert "woke" not in waiting.data
    assert waiting.data["waits"] == []        # the check starting now isn't still to come
    assert final.text == "Scheduled check 1: the checks passed."
    assert final.data["woke"] == "schedule_wakeup"


def test_a_yes_to_usage_credits_isnt_kept_once_the_turn_waits(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, command_extra=("--model", "fable"),
              FAKE_CLAUDE_MODEL="claude-fable-5-2", FAKE_CLAUDE_WAKE_OVERAGE="1")
    run.wait_for(lambda events: run.runner.waiting_for_waits)
    assert run.runner.allow_overage is False   # it was True for the message that set it
    run.runner.cancel()
    run.done()


def test_a_watch_listed_as_ambient_holds_until_it_goes(tmp_path, monkeypatch):
    run = Run(tmp_path, monkeypatch, FAKE_CLAUDE_CRON="0 14 10 12 *",
              FAKE_CLAUDE_CRON_THEN="monitor")
    run.wait_for(lambda events: run.runner.waiting_for_waits)
    [waiting] = run.waiting()
    assert [w.kind for w in sw.from_dicts(waiting.data["waits"])] == [WATCH]
    # The far timer is said with the reply, once, and not again at the end.
    assert [w.cron for w in sw.from_dicts(waiting.data["far"])] == ["0 14 10 12 *"]
    run.runner.cancel()
    final = run.done()
    assert [w.kind for w in sw.from_dicts(final.data["waits"])] == [WATCH]
