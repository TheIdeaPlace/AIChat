"""Claude's waits (#749): timers, wakeups and watches recognized from the
stream, their fire times worked out, and the sentence saying which won't run.
No wx: runs on every platform's CI."""
import json
import sys
import threading
from datetime import datetime, timedelta

import pytest

from thechatplace import announce, scheduled_waits as sw
from thechatplace import claude_cli as cli
from thechatplace.claude_cli import StreamParser, TurnRunner
from thechatplace.own_store import OwnSession, OwnSessionStore, _session_from_dict
from thechatplace.scheduled_waits import (CRON, WAKEUP, WATCH, Wait, WaitBook, lost_sentence,
                                          next_cron_time, schedule_words, when_words)
from thechatplace.transcript import ASSISTANT, ChatMessage

#: A Thursday afternoon: Nov 27 2026 at 1:00 PM local time.
NOW = datetime(2026, 11, 27, 13, 0)


def at(dt: datetime) -> float:
    return dt.timestamp()


# -- next_cron_time --------------------------------------------------------------------


@pytest.mark.parametrize("expression, after, expected", [
    # The probe's one-shot: pinned minute, hour, day and month.
    ("9 6 27 11 *", datetime(2026, 11, 27, 6, 7, 30), datetime(2026, 11, 27, 6, 9)),
    # The minute it was made in doesn't count: tomorrow, then.
    ("8 6 * * *", datetime(2026, 11, 27, 6, 8, 30), datetime(2026, 11, 28, 6, 8)),
    # Passed this year: next year's.
    ("0 9 1 1 *", NOW, datetime(2027, 1, 1, 9, 0)),
    ("*/5 * * * *", datetime(2026, 11, 27, 13, 2), datetime(2026, 11, 27, 13, 5)),
    ("*/15 * * * *", datetime(2026, 11, 27, 23, 59), datetime(2026, 11, 28, 0, 0)),
    # Month ends: the 31st skips the months without one.
    ("0 12 31 * *", datetime(2026, 11, 27), datetime(2026, 12, 31, 12, 0)),
    ("0 12 31 * *", datetime(2027, 1, 31, 13, 0), datetime(2027, 3, 31, 12, 0)),
    # Leap day: the next one is 2028.
    ("0 0 29 2 *", NOW, datetime(2028, 2, 29, 0, 0)),
    # Weekdays: a Friday evening goes to Monday.
    ("0 9 * * 1-5", datetime(2026, 11, 27, 18, 0), datetime(2026, 11, 30, 9, 0)),
    ("30 8 * * mon,wed", NOW, datetime(2026, 11, 30, 8, 30)),
    # 7 is Sunday as well as 0.
    ("0 10 * * 7", NOW, datetime(2026, 11, 29, 10, 0)),
    ("0 10 * * sun", NOW, datetime(2026, 11, 29, 10, 0)),
    # Both days given: either matches, as in cron.
    ("0 10 1 * 6", NOW, datetime(2026, 11, 28, 10, 0)),
    ("0 10 28 * 1", NOW, datetime(2026, 11, 28, 10, 0)),
    ("0 10 */10 * *", NOW, datetime(2026, 12, 1, 10, 0)),
    ("15 14 * dec *", NOW, datetime(2026, 12, 1, 14, 15)),
    ("5/20 * * * *", datetime(2026, 11, 27, 13, 26), datetime(2026, 11, 27, 13, 45)),
    ("0 9-17/4 * * *", NOW, datetime(2026, 11, 27, 13, 0) + timedelta(hours=4)),
])
def test_next_cron_time(expression, after, expected):
    assert next_cron_time(expression, after) == expected


@pytest.mark.parametrize("expression", [
    "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *",
    "* * * * 8", "5-1 * * * *", "*/0 * * * *", "a * * * *", "0 9 31 2 *", None, 7,
])
def test_next_cron_time_unreadable_or_never(expression):
    assert next_cron_time(expression, NOW) is None


def test_next_cron_time_across_a_dst_change_stays_on_the_local_clock():
    # Naive local times: 9:00 AM is 9:00 AM before and after US DST ends.
    assert next_cron_time("0 9 * * *", datetime(2026, 10, 31, 12, 0)) == \
        datetime(2026, 11, 1, 9, 0)
    assert next_cron_time("0 9 * * *", datetime(2026, 3, 7, 12, 0)) == \
        datetime(2026, 3, 8, 9, 0)


# -- words -------------------------------------------------------------------------------


def test_clock_and_when_words():
    assert sw.clock_time(0, 5) == "12:05 AM"
    assert sw.clock_time(12, 0) == "12:00 PM"
    assert sw.clock_time(14, 0) == "2:00 PM"
    assert when_words(datetime(2026, 11, 27, 14, 0), NOW) == "2:00 PM"
    assert when_words(datetime(2026, 11, 28, 9, 0), NOW) == "9:00 AM on Saturday, November 28"
    assert when_words(datetime(2027, 1, 1, 9, 0), NOW) == "9:00 AM on Friday, January 1, 2027"


@pytest.mark.parametrize("expression, words", [
    ("* * * * *", "every minute"),
    ("*/1 * * * *", "every minute"),
    ("*/30 * * * *", "every 30 minutes"),
    ("0 * * * *", "every hour"),
    ("15 * * * *", "every hour at 15 past"),
    ("0 */2 * * *", "every 2 hours"),
    ("30 */2 * * *", "every 2 hours at 30 past"),
    ("0 9 * * *", "every day at 9:00 AM"),
    ("0 9,17 * * *", "every day at 9:00 AM and 5:00 PM"),
    ("30 8 * * 1-5", "every weekday at 8:30 AM"),
    ("0 10 * * 1", "every Monday at 10:00 AM"),
    ("0 10 * * 1,3,5", "every Monday, Wednesday and Friday at 10:00 AM"),
    ("0 10 * * 0,6", "every Saturday and Sunday at 10:00 AM"),
    ("0 7 1 * *", "on the 1st of every month at 7:00 AM"),
    ("0 7 22 * *", "on the 22nd of every month at 7:00 AM"),
    ("0 7 13 * *", "on the 13th of every month at 7:00 AM"),
    # Shapes with no plain words: never cron text.
    ("0 7 1 6 *", ""), ("0 9-17 * * *", ""), ("bad", ""),
])
def test_schedule_words(expression, words):
    assert schedule_words(expression) == words


def one_shot_at(when: datetime, set_at: datetime = NOW) -> Wait:
    return Wait(CRON, set_at=at(set_at), cron=f"{when.minute} {when.hour} {when.day} {when.month} *")


def test_the_sentence_for_one_check():
    said = lost_sentence([one_shot_at(datetime(2026, 11, 27, 14, 0))], NOW)
    assert said == ("This chat ends when Claude's reply does, so the check Claude set "
                    "for 2:00 PM won't run.")


def test_the_sentence_names_every_kind_once():
    waits = [one_shot_at(datetime(2026, 11, 27, 14, 0)),
             one_shot_at(datetime(2026, 11, 28, 9, 0)),
             one_shot_at(datetime(2026, 11, 27, 14, 0)),   # the same time again: said once
             Wait(CRON, set_at=at(NOW), cron="*/30 * * * *", recurring=True),
             Wait(WAKEUP, set_at=at(NOW), delay_seconds=1200),
             Wait(WATCH, set_at=at(NOW), description="Watch PR 749")]
    assert lost_sentence(waits, NOW) == (
        "This chat ends when Claude's reply does, so the checks Claude set for 2:00 PM, "
        "9:00 AM on Saturday, November 28 and 1:20 PM, and the repeating check Claude set "
        "to run every 30 minutes won't run, and the watch Claude started (Watch PR 749) stops.")


def test_two_checks_at_one_time_are_still_checks():
    twice = [one_shot_at(datetime(2026, 11, 27, 14, 0))] * 2
    assert lost_sentence(twice, NOW).endswith("the checks Claude set for 2:00 PM won't run.")


def test_the_kept_copy_has_the_date_even_today():
    assert lost_sentence([one_shot_at(datetime(2026, 11, 27, 14, 0))], NOW, dated=True) == (
        "This chat ends when Claude's reply does, so the check Claude set for 2:00 PM on "
        "Friday, November 27 won't run.")


def test_the_sentence_with_nothing_lost_is_empty():
    assert lost_sentence([], NOW) == ""
    # One Claude Code wrote to disk is Claude Code's to run.
    durable = Wait(CRON, set_at=at(NOW), cron="0 14 27 11 *", durable=True)
    assert lost_sentence([durable], NOW) == ""


def test_a_time_that_passed_with_no_wake_seen_is_still_lost():
    # Claude may have kept working past it: it may never have fired.
    passed = one_shot_at(datetime(2026, 11, 27, 12, 30), set_at=datetime(2026, 11, 27, 12, 0))
    assert lost_sentence([passed], NOW).endswith("for 12:30 PM won't run.")


def test_the_sentence_without_readable_times_or_names():
    odd = [Wait(CRON, set_at=at(NOW), cron="0 9-17 * * *", recurring=True)]
    assert lost_sentence(odd, NOW) == (
        "This chat ends when Claude's reply does, so the check Claude set won't run.")
    assert lost_sentence(odd * 2, NOW).endswith("so the 2 checks Claude set won't run.")
    mixed = odd + [one_shot_at(datetime(2026, 11, 27, 14, 0))]
    assert lost_sentence(mixed, NOW).endswith(
        "the check Claude set for 2:00 PM, and another check Claude set won't run.")
    assert lost_sentence([Wait(WATCH), Wait(WATCH)], NOW).endswith(
        "so the watches Claude started stop.")
    assert lost_sentence([Wait(WATCH), Wait(WATCH, description="x")], NOW).endswith(
        "so the watches Claude started (x and 1 more) stop.")


def test_the_sentence_names_at_most_three_and_counts_the_rest():
    waits = [one_shot_at(datetime(2026, 11, 27, 14, m)) for m in range(5)]
    assert lost_sentence(waits, NOW).endswith(
        "the checks Claude set for 2:00 PM, 2:01 PM, 2:02 PM and 2 more won't run.")


def test_a_long_watch_description_is_cut():
    said = lost_sentence([Wait(WATCH, description="x" * 500)], NOW)
    assert len(said) < 200 and "…" in said


def test_then_joins_sentences():
    assert announce.then("Hub probe replied. Done.", "More.") == "Hub probe replied. Done. More."
    assert announce.then("Ends in code", "More.") == "Ends in code. More."
    assert announce.then("", "More.") == "More."
    assert announce.then("Text.", "") == "Text."


# -- WaitBook ------------------------------------------------------------------------------


def book(clock_at=NOW):
    return WaitBook(lambda: at(clock_at))


CRON_RESULT = ("Scheduled one-shot task f0d1911f (9 6 27 11 *). Next run: Nov 27 6:09am. "
               "Session-only (not written to disk, dies when Claude exits). "
               "It will fire once then auto-delete.")


def test_a_cron_counts_once_its_result_says_it_was_set():
    waits = book()
    waits.tool_use("CronCreate", "t1", {"cron": "9 6 27 11 *", "prompt": "Ping", "recurring": False})
    assert waits.expecting and waits.waits == []
    assert waits.tool_result("t1", CRON_RESULT, False)
    [wait] = waits.waits
    assert (wait.kind, wait.cron, wait.recurring, wait.job_id, wait.prompt, wait.durable) == \
        (CRON, "9 6 27 11 *", False, "f0d1911f", "Ping", False)
    assert wait.set_at == at(NOW) and not waits.expecting


def test_a_refused_or_failed_call_sets_nothing():
    waits = book()
    waits.tool_use("CronCreate", "t1", {"cron": "0 14 * * *"})
    assert not waits.tool_result("t1", "Too many scheduled jobs", True)
    waits.tool_use("Monitor", "t2", {"description": "Watch"})
    assert not waits.tool_result("t2", "denied", True)
    assert waits.waits == [] and not waits.expecting
    # Other tools, and results for calls never seen, are nothing.
    waits.tool_use("Bash", "t3", {"command": "ls"})
    assert not waits.expecting
    assert not waits.tool_result("nobody", "x", False)


def test_recurring_follows_claude_codes_default_and_its_result():
    waits = book()
    waits.tool_use("CronCreate", "t1", {"cron": "*/5 * * * *"})  # recurring by default
    waits.tool_use("CronCreate", "t2", {"cron": "0 14 * * *", "recurring": "false"})
    waits.tool_use("CronCreate", "t3", {"cron": "0 15 * * *", "recurring": True})
    waits.tool_result("t1", [{"type": "text", "text": "Scheduled recurring job aa11 (every 5 "
                                                       "minutes). Session-only."}], False)
    waits.tool_result("t2", "no id in this text", False)
    # Claude Code said one-shot: that's what it is, whatever was asked.
    waits.tool_result("t3", "Scheduled one-shot task bb22 (0 15 * * *).", False)
    assert [(w.recurring, w.job_id) for w in waits.waits] == [(True, "aa11"), (False, ""),
                                                             (False, "bb22")]


def test_a_durable_cron_is_marked():
    waits = book()
    waits.tool_use("CronCreate", "t1", {"cron": "0 14 * * *", "durable": True})
    waits.tool_result("t1", "Scheduled recurring job cc33 (daily). Persisted to "
                            ".claude/scheduled_tasks.json.", False)
    assert waits.waits[0].durable


def test_cron_delete_removes_its_job():
    waits = book()
    waits.tool_use("CronCreate", "t1", {"cron": "9 6 27 11 *", "recurring": False})
    waits.tool_result("t1", CRON_RESULT, False)
    waits.tool_use("CronDelete", "t2", {"id": "someone-else"})
    assert not waits.tool_result("t2", "Cancelled job someone-else.", False)
    waits.tool_use("CronDelete", "t3", {"id": "f0d1911f"})
    assert not waits.tool_result("t3", "No scheduled job with id", True)  # refused: kept
    assert len(waits.waits) == 1
    waits.tool_use("CronDelete", "t4", {"id": "f0d1911f"})
    assert waits.tool_result("t4", "Cancelled job f0d1911f.", False)
    assert waits.waits == []


def test_schedule_wakeup_replaces_the_last_and_stop_ends_it():
    waits = book()
    waits.tool_use("ScheduleWakeup", "t1", {"delaySeconds": 1200, "reason": "CI runs",
                                            "prompt": "/loop check CI"})
    waits.tool_result("t1", "ok", False)
    [first] = waits.waits
    assert (first.kind, first.delay_seconds, first.description, first.prompt) == \
        (WAKEUP, 1200, "CI runs", "/loop check CI")
    assert first.fire_time() == NOW + timedelta(seconds=1200)
    waits.tool_use("ScheduleWakeup", "t2", {"delaySeconds": "5"})  # clamped up to 60
    waits.tool_result("t2", "ok", False)
    assert [w.delay_seconds for w in waits.waits] == [60]
    waits.tool_use("ScheduleWakeup", "t3", {"delaySeconds": 99999})
    waits.tool_result("t3", "ok", False)
    assert [w.delay_seconds for w in waits.waits] == [3600]
    waits.tool_use("ScheduleWakeup", "t4", {"stop": True})
    assert waits.tool_result("t4", "ok", False)
    assert waits.waits == []


def test_a_watch_ends_once_its_task_is_gone():
    waits = book()
    waits.tool_use("Monitor", "t1", {"description": "Watch PR 749", "command": "gh pr checks"})
    waits.tool_result("t1", "Monitor started", False)
    assert [w.kind for w in waits.waits] == [WATCH]
    # A list without it, before it was ever listed, says nothing.
    assert not waits.tasks_changed([])
    watching = [{"task_id": "m1", "description": "Watch PR 749", "ambient": True}]
    assert not waits.tasks_changed(watching)
    assert len(waits.waits) == 1
    assert waits.tasks_changed([])  # it was listed, and now isn't: it ended
    assert waits.waits == []
    assert not waits.tasks_changed("not a list")


def test_a_watch_listed_before_its_result_still_ends_when_its_task_goes():
    # The task starts while the tool runs, so it's listed before the result.
    waits = book()
    waits.tool_use("Monitor", "t1", {"description": "Watch PR 749"})
    waits.tasks_changed([{"task_id": "m1", "description": "Watch PR 749", "ambient": True}])
    waits.tool_result("t1", "Monitor started", False)
    assert len(waits.waits) == 1
    assert waits.tasks_changed([])
    assert waits.waits == []


def test_cron_delete_still_works_with_a_result_worded_differently():
    waits = book()
    waits.tool_use("CronCreate", "t1", {"cron": "0 14 27 11 *", "recurring": False})
    waits.tool_result("t1", "Timer 9a8b7c6d is set.", False)  # an 8-hex id in other words
    assert waits.waits[0].job_id == "9a8b7c6d"
    waits.tool_use("CronDelete", "t2", {"id": "9a8b7c6d"})
    assert waits.tool_result("t2", "Cancelled job 9a8b7c6d.", False)
    # With no id at all, a deleted job is the one timer whose id is unknown.
    waits.tool_use("CronCreate", "t3", {"cron": "0 15 27 11 *", "recurring": False})
    waits.tool_result("t3", "Done.", False)
    assert waits.waits[0].job_id == ""
    waits.tool_use("CronDelete", "t4", {"id": "abc"})
    assert waits.tool_result("t4", "Cancelled.", False)
    assert waits.waits == []
    # But not when that can't be told: two unknown timers both stay.
    for use in ("t5", "t6"):
        waits.tool_use("CronCreate", use, {"cron": "0 16 27 11 *"})
        waits.tool_result(use, "Done.", False)
    waits.tool_use("CronDelete", "t7", {"id": "abc"})
    assert not waits.tool_result("t7", "Cancelled.", False)
    assert len(waits.waits) == 2


def test_a_wake_removes_the_one_time_waits_that_were_due():
    clock = {"now": at(NOW)}
    waits = WaitBook(lambda: clock["now"])
    for use, cron in (("t1", "5 13 27 11 *"), ("t2", "0 14 27 11 *")):
        waits.tool_use("CronCreate", use, {"cron": cron, "recurring": False})
        waits.tool_result(use, "Done.", False)
    waits.tool_use("CronCreate", "t3", {"cron": "*/5 * * * *"})
    waits.tool_result("t3", "Done.", False)
    clock["now"] = at(datetime(2026, 11, 27, 13, 4, 30))  # a little early: counts
    assert waits.woke()
    assert [w.cron for w in waits.waits] == ["0 14 27 11 *", "*/5 * * * *"]  # repeating stays
    assert not waits.woke()


def test_one_wake_is_one_job_even_with_another_due_a_moment_later():
    clock = {"now": at(NOW)}
    waits = WaitBook(lambda: clock["now"])
    waits.tool_use("CronCreate", "t1", {"cron": "5 13 27 11 *", "recurring": False})
    waits.tool_result("t1", "Done.", False)
    waits.tool_use("ScheduleWakeup", "t2", {"delaySeconds": 360})  # 1:06 PM
    waits.tool_result("t2", "ok", False)
    clock["now"] = at(datetime(2026, 11, 27, 13, 5))
    assert waits.woke()
    assert [w.kind for w in waits.waits] == [WAKEUP]


def test_a_repeating_timers_wake_on_time_leaves_a_one_time_check_due_soon():
    clock = {"now": at(NOW)}
    waits = WaitBook(lambda: clock["now"])
    waits.tool_use("CronCreate", "t1", {"cron": "*/5 * * * *"})
    waits.tool_result("t1", "Done.", False)
    waits.tool_use("CronCreate", "t2", {"cron": "6 13 27 11 *", "recurring": False})
    waits.tool_result("t2", "Done.", False)
    clock["now"] = at(datetime(2026, 11, 27, 13, 5, 10))  # the 1:05 repeat fired
    assert not waits.woke()
    assert len(waits.waits) == 2
    clock["now"] = at(datetime(2026, 11, 27, 13, 6, 5))  # now the 1:06 one, overdue
    assert waits.woke()
    assert [w.recurring for w in waits.waits] == [True]


def test_a_task_without_a_description_isnt_taken_for_a_watch_without_one():
    waits = book()
    waits.tool_use("Monitor", "t1", {"command": "tail -f log"})
    waits.tasks_changed([{"task_id": "b1", "task_type": "local_bash"}])
    waits.tool_result("t1", "started", False)
    waits.tasks_changed([])  # the shell ended; the watch didn't
    assert [w.kind for w in waits.waits] == [WATCH]


def test_the_parser_takes_a_wake_turn_as_the_due_timer_having_run():
    parser = StreamParser()
    parser.waits = book()
    feed(parser, type="assistant", parent_tool_use_id=None, message={"content": [
        {"type": "tool_use", "id": "t1", "name": "ScheduleWakeup", "input": {"delaySeconds": 60}}]})
    feed(parser, type="user", parent_tool_use_id=None, message={"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})
    parser.waits._clock = lambda: at(NOW) + 61
    assert feed(parser, type="command_lifecycle", state="completed") == []
    # A CronCreate timer's wake (schedule_wakeup) isn't the wakeup's (Part 2's
    # probe: a ScheduleWakeup's is loop_wakeup, with scheduled_for).
    [woke] = feed(parser, type="command_lifecycle", command_uuid="c", state="started",
                  wakeup_source="schedule_wakeup", session_id="abc-1")
    assert woke.kind == "woke" and woke.data == {"source": "schedule_wakeup"}
    assert [w.kind for w in parser.waits.waits] == [WAKEUP]
    event, woke = feed(parser, type="command_lifecycle", command_uuid="d", state="started",
                       wakeup_source="loop_wakeup", scheduled_for=1795796400000,
                       session_id="abc-1")
    assert event.kind == "waits" and event.data["waits"] == []
    assert woke.kind == "woke" and woke.data == {"source": "loop_wakeup"}


def test_waits_round_trip_through_an_events_data():
    original = [Wait(CRON, "t1", 12.5, "0 14 * * *", True, "Ping", 0, "", "aa", False),
                Wait(WAKEUP, "t2", 1.0, delay_seconds=60), Wait(WATCH, description="W")]
    assert sw.from_dicts([w.to_dict() for w in original]) == original
    assert sw.from_dicts([{"kind": "other"}, "x", {"kind": CRON, "delay_seconds": "60",
                                                    "recurring": 1}]) == [Wait(CRON)]
    assert sw.from_dicts(None) == []


# -- The stream parser -----------------------------------------------------------------------


def ev(**data):
    return json.dumps(data)


def feed(parser, **data):
    return parser.feed(ev(**data))


def test_the_parser_says_the_waits_after_each_change():
    parser = StreamParser()
    parser.waits = book()
    events = feed(parser, type="assistant", parent_tool_use_id=None, message={"content": [
        {"type": "tool_use", "id": "t1", "name": "CronCreate",
         "input": {"cron": "0 14 27 11 *", "recurring": False}}]})
    assert [e.kind for e in events] == ["tool"]
    events = feed(parser, type="user", parent_tool_use_id=None, message={"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": CRON_RESULT}]})
    [event] = events
    assert event.kind == "waits"
    assert [w["cron"] for w in event.data["waits"]] == ["0 14 27 11 *"]
    # A subagent's timer isn't the chat's.
    assert feed(parser, type="assistant", parent_tool_use_id="sub", message={"content": [
        {"type": "tool_use", "id": "t2", "name": "CronCreate", "input": {"cron": "* * * * *"}}]}
    ) == []
    assert not parser.waits.expecting
    # A watch gone from the background list is said too, with the list.
    feed(parser, type="assistant", parent_tool_use_id=None, message={"content": [
        {"type": "tool_use", "id": "t3", "name": "Monitor", "input": {"description": "Logs"}}]})
    feed(parser, type="user", parent_tool_use_id=None, message={"content": [
        {"type": "tool_result", "tool_use_id": "t3", "content": "started"}]})
    feed(parser, type="system", subtype="background_tasks_changed",
         tasks=[{"task_id": "m", "description": "Logs", "ambient": True}])
    kinds = [e.kind for e in feed(parser, type="system", subtype="background_tasks_changed",
                                  tasks=[])]
    assert kinds == ["background", "waits"]
    assert [w.kind for w in parser.waits.waits] == [CRON]


def test_edits_still_come_through_beside_waits():
    parser = StreamParser()
    feed(parser, type="assistant", parent_tool_use_id=None, message={"content": [
        {"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "a.py"}},
        {"type": "tool_use", "id": "c1", "name": "CronCreate", "input": {"cron": "0 9 * * *"}}]})
    events = feed(parser, type="user", parent_tool_use_id=None, message={"content": [
        {"type": "tool_result", "tool_use_id": "e1", "content": "ok"},
        {"type": "tool_result", "tool_use_id": "c1", "content": "Scheduled recurring job x1 (a)."}]})
    assert [e.kind for e in events] == ["edited", "waits"]


# -- A real child process (tests/fake_claude.py) ----------------------------------------------


def run_fake_cron(tmp_path, monkeypatch, **extra):
    from test_claude_cli import FAKE_CLAUDE
    monkeypatch.setattr(sw, "wall_clock", lambda: at(NOW))
    log = tmp_path / "fake.log"
    env = cli.child_environment({**cli.child_environment(), "FAKE_CLAUDE_LOG": str(log),
                                 "FAKE_CLAUDE_MODE": "cron", **extra})
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner([sys.executable, str(FAKE_CLAUDE)], str(tmp_path), "go", on_event,
                        env=env)
    runner.start()
    assert done.wait(30)
    runner.join(10)
    return runner, events


def test_a_timer_claude_sets_ends_with_the_turn_and_is_on_its_last_event(tmp_path, monkeypatch):
    runner, events = run_fake_cron(tmp_path, monkeypatch)
    kinds = [e.kind for e in events]
    assert "waits" in kinds and kinds[-1] == "finished"
    assert [w.job_id for w in runner.pending_waits] == ["f0d1911f"]
    final = events[-1]
    assert final.text == "I'll check the pull request again at 2:00 PM."
    assert final.data["reply_message_id"] == "msg_reply"
    [wait] = sw.from_dicts(final.data["waits"])
    assert (wait.cron, wait.recurring, wait.set_at) == ("0 14 27 11 *", False, at(NOW))
    assert lost_sentence([wait], NOW) == (
        "This chat ends when Claude's reply does, so the check Claude set for 2:00 PM "
        "won't run.")


@pytest.mark.parametrize("then", ["delete", "refused"])
def test_a_deleted_or_refused_timer_isnt_on_the_last_event(tmp_path, monkeypatch, then):
    runner, events = run_fake_cron(tmp_path, monkeypatch, FAKE_CLAUDE_CRON_THEN=then)
    assert events[-1].kind == "finished"
    assert runner.pending_waits == []
    assert "waits" not in (events[-1].data or {})


def test_a_watch_and_a_repeating_timer_are_on_the_last_event(tmp_path, monkeypatch):
    runner, events = run_fake_cron(tmp_path, monkeypatch, FAKE_CLAUDE_CRON="*/30 * * * *",
                                   FAKE_CLAUDE_RECURRING="1", FAKE_CLAUDE_CRON_THEN="monitor")
    waits = sw.from_dicts(events[-1].data["waits"])
    assert [(w.kind, w.recurring) for w in waits] == [(CRON, True), (WATCH, False)]
    assert lost_sentence(waits, NOW) == (
        "This chat ends when Claude's reply does, so the repeating check Claude set to run "
        "every 30 minutes won't run, and the watch Claude started (Watch PR 749) stops.")


# -- Kept with the reply -------------------------------------------------------------------


LINE = "This chat ends when Claude's reply does, so the check Claude set for 2:00 PM won't run."


def test_notes_are_kept_cleaned_and_shown_on_their_reply(tmp_path):
    store = OwnSessionStore(tmp_path / "own.json")
    store.add(OwnSession("chat-1", "Chat", str(tmp_path)))
    assert store.add_reply_note("chat-1", "msg_1", LINE)
    assert not store.add_reply_note("chat-1", "msg_1", LINE)  # nothing new
    assert not store.add_reply_note("nobody", "msg_1", LINE)
    assert OwnSessionStore(tmp_path / "own.json").get("chat-1").reply_notes == {"msg_1": LINE}
    base = {"cli_session_id": "c", "title": "T", "cwd": "/w"}
    assert _session_from_dict({**base, "reply_notes": {"m": "Not one of ours"}}).reply_notes == {}
    assert _session_from_dict({**base, "reply_notes": ["x"]}).reply_notes == {}
    assert _session_from_dict({**base, "provider": "codex",
                               "reply_notes": {"m": LINE}}).reply_notes == {}
    many = {f"m{n}": LINE for n in range(sw.MAX_NOTES + 5)}
    assert len(sw.clean_notes(many)) == sw.MAX_NOTES
    assert sw.remember_note({}, "", LINE) == {}

    reply = ChatMessage(ASSISTANT, "I'll check at 2.", key="msg_1", cost="Cost: 1 cent")
    other = ChatMessage(ASSISTANT, "Hi.", key="msg_2")
    shown = sw.with_notes([reply, other], {"msg_1": LINE}, ASSISTANT)
    assert shown[1] is other
    assert shown[0].note == LINE
    assert shown[0].list_line() == f"Claude: I'll check at 2. {LINE} Cost: 1 cent"
    assert shown[0].whole == f"I'll check at 2.\n\n{LINE}\n\nCost: 1 cent"
    assert shown[0].copy_text() == "Claude:\nI'll check at 2."  # Copy leaves it out
