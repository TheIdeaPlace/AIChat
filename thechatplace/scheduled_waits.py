"""Claude's waits: work it sets up to happen after its reply (#749).

Claude Code can promise to come back later in three ways, none of which is a
running background task (#161):

- ``CronCreate``: a timer on a 5-field cron expression in local time, once
  (``recurring`` false) or repeating (the default). Its result names the job
  ("Scheduled one-shot task f0d1911f (…)"), and ``CronDelete`` takes that id.
- ``ScheduleWakeup``: ``/loop``'s "come back in N seconds" (``delaySeconds``,
  which Claude Code clamps to 60 to 3600). ``stop`` ends the loop.
- ``Monitor``: a watch on a command's output, named by its ``description``.
  It runs as an *ambient* background task, which the turn doesn't wait for.

Each lives only as long as the ``claude`` process, which The Chat Place ends
when the reply does, so until #749's Part 2 every one of them is lost. This
module keeps track of them from the stream (``WaitBook``), works out when a
timer would have fired (``next_cron_time``), and says in plain words which
waits won't happen (``lost_sentence``). It's plain data with no wx, so it's
tested on every platform; Part 2 drives the window from the same records.

Shapes checked against Claude Code 2.1.331's tool definitions and a live
probe (2026-11-27, ``OneDrive\\testing\\scratch\\session-lifetime-spec``).
"""
from __future__ import annotations

import dataclasses
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

CRON_CREATE = "CronCreate"
CRON_DELETE = "CronDelete"
SCHEDULE_WAKEUP = "ScheduleWakeup"
MONITOR = "Monitor"
#: The tools whose calls are tracked.
WAIT_TOOLS = frozenset({CRON_CREATE, CRON_DELETE, SCHEDULE_WAKEUP, MONITOR})

#: A ``Wait``'s kinds.
CRON = "cron"
WAKEUP = "wakeup"
WATCH = "watch"

#: ScheduleWakeup's delay, as Claude Code clamps it.
WAKEUP_MIN_SECONDS = 60
WAKEUP_MAX_SECONDS = 3600
#: The most waits kept for one turn, and the longest text kept from one:
#: anything more is a runaway loop, and the sentence would be unreadable.
MAX_WAITS = 50
MAX_TEXT = 200
#: How many of one kind the sentence names before "and N more", and the
#: most of a watch's description it says.
MAX_NAMED = 3
MAX_SAID = 80

#: The sentence's start. Part 2 keeps it for the waits that still can't be
#: kept when the reply ends (an API-credit chat, or the limit reached).
LOST_START = "This chat ends when Claude's reply does, so "
#: Part 2: the start when a chat kept open for its waits stops waiting (Stop,
#: quitting, Claude Code ending, or a check that never came).
ENDED_START = "This chat stopped waiting, so "
#: Part 2: the start for a wait too far away to keep the chat open for.
FAR_START = "That's more than {days} days away, too long to keep the chat open, so "
#: Every start a line kept after a reply may have (``clean_notes``).
NOTE_STARTS = (LOST_START, ENDED_START, "That's more than ")

# -- Part 2's limits, each Kelly's to change -------------------------------------
#: The furthest away a wait can be and still keep the chat's process open.
MAX_HOLD_DAYS = 7
#: How many chats Settings lets keep a process open for waits at once, by
#: default (``SpeechSettings.wait_limit``), and the most it offers.
DEFAULT_WAIT_LIMIT = 5
MAX_WAIT_LIMIT = 20
#: Claude Code ends a repeating timer this long after it was set.
RECURRING_EXPIRY_DAYS = 30
#: A one-time wait whose time passed this long ago with no wake seen isn't
#: kept for any longer: Claude Code would have fired it by now.
OVERDUE_GRACE_SECONDS = 600


@dataclass(frozen=True)
class Wait:
    """One thing Claude set up to happen later, from its tool call (#749)."""

    kind: str                 # CRON | WAKEUP | WATCH
    #: The tool_use id it came from.
    tool_use_id: str = ""
    #: When Claude set it (seconds since the epoch): a cron's next fire time
    #: is the first match after this, a wakeup's is this plus its delay.
    set_at: float = 0.0
    #: CRON: the expression ("9 6 27 11 *") and whether it repeats.
    cron: str = ""
    recurring: bool = False
    #: CRON and WAKEUP: what Claude would be sent when it fires.
    prompt: str = ""
    #: WAKEUP: seconds after ``set_at``, already clamped.
    delay_seconds: int = 0
    #: WATCH: what the watch is ("Watch PR #749's checks"); WAKEUP: its reason.
    description: str = ""
    #: CRON: the job id its result named, which CronDelete takes.
    job_id: str = ""
    #: CRON: written to disk by Claude Code (``durable``), so it can outlive
    #: the process. Not on in Claude Code 2.1.331 by default.
    durable: bool = False
    #: WAKEUP: when Claude Code said it would fire (seconds since the
    #: epoch), from its result's "(in 97s)": it rounds the delay up to a whole
    #: minute, so this is truer than ``set_at`` plus the delay. 0 if not said.
    fire_at: float = 0.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def one_shot(self) -> bool:
        """Fires once: a one-time cron, or a wakeup."""
        return (self.kind == CRON and not self.recurring) or self.kind == WAKEUP

    def fire_time(self) -> Optional[datetime]:
        """When a one-time wait would fire, in local time, or None when
        that can't be told (a repeating one, a watch, a cron this can't read)."""
        if self.kind == WAKEUP:
            try:
                return datetime.fromtimestamp(self.fire_at or self.set_at + self.delay_seconds)
            except (OverflowError, OSError, ValueError):
                return None
        if self.kind == CRON and not self.recurring:
            try:
                after = datetime.fromtimestamp(self.set_at)
            except (OverflowError, OSError, ValueError):
                return None
            return next_cron_time(self.cron, after)
        return None


def from_dicts(items) -> List[Wait]:
    """Waits back from ``Wait.to_dict``'s dicts (an event's data); anything
    malformed is left out."""
    defaults = {f.name: f.default for f in dataclasses.fields(Wait) if f.name != "kind"}
    waits: List[Wait] = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict) or item.get("kind") not in (CRON, WAKEUP, WATCH):
            continue
        values = {"kind": item["kind"]}
        for name, value in item.items():
            if name not in defaults:
                continue
            default = defaults[name]
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, int):
                ok = type(value) is int
            elif isinstance(default, float):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            else:
                ok = isinstance(value, str)
            if ok:
                values[name] = value
        waits.append(Wait(**values))
    return waits[:MAX_WAITS]


# -- Keeping track of them from the stream -------------------------------------


def _text(value, limit: int = MAX_TEXT) -> str:
    """A tool input's string, on one line and not too long."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _flag(value, default: bool) -> bool:
    """A boolean input: Claude Code takes "true" and "false" as well."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return default


def _seconds(value) -> Optional[int]:
    """ScheduleWakeup's delaySeconds, clamped as Claude Code clamps it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or value != value:  # NaN
        return None
    return int(min(max(value, WAKEUP_MIN_SECONDS), WAKEUP_MAX_SECONDS))


def result_text(content) -> str:
    """A tool result's text, whether it came as a string or text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


#: CronCreate's result: "Scheduled one-shot task f0d1911f (9 6 27 11 *)." or
#: "Scheduled recurring job 1a2b3c4d (every 5 minutes)." (2.1.331).
_SCHEDULED = re.compile(r"Scheduled (one-shot task|recurring job) ([A-Za-z0-9_-]{1,64}) \(")
_JOB_ID = re.compile(r"\b([0-9a-f]{8})\b")
#: ScheduleWakeup's result: "Next wakeup scheduled for 10:20:00 (in 97s)."
#: (2.1.331, probed 2026-11-27): the delay it really used, rounded up.
_WAKEUP_IN = re.compile(r"\(in (\d{1,5})s\)")
#: command_lifecycle's ``wakeup_source`` for each kind (probed with 2.1.331):
#: a CronCreate timer, and a ScheduleWakeup (with its ``scheduled_for``).
CRON_WAKE = "schedule_wakeup"
LOOP_WAKE = "loop_wakeup"
#: A wake this far before a one-time job's time is taken to be that job's,
#: in case Claude Code fires it a little early.
WAKE_EARLY_SECONDS = 120


def wall_clock() -> float:
    """Now, in seconds since the epoch: when a wait is set. Looked up at
    each call, so tests can fix it."""
    return time.time()


def local_now() -> datetime:
    """Now, in local time: what the sentence measures fire times against."""
    return datetime.now()


class WaitBook:
    """The waits one ``claude`` process has, from its top-level tool calls
    and their results (#749). A call counts only once its result comes back
    without an error: one refused or that failed set nothing up. Not
    thread-safe; the stream parser owns it."""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock if clock is not None else (lambda: wall_clock())
        self._waits: List[Wait] = []
        #: Calls waiting for their results: tool_use id -> (tool, input, when).
        self._calls: Dict[str, Tuple[str, dict, float]] = {}
        #: Watches seen among the ambient background tasks, by description:
        #: one that has been listed and isn't any more has ended.
        self._watching: set = set()
        #: The latest list's descriptions: a Monitor's task is listed while
        #: the tool runs, before its result comes.
        self._listed: set = set()

    @property
    def waits(self) -> List[Wait]:
        return list(self._waits)

    @property
    def listed(self) -> frozenset:
        """The descriptions in the latest background list, ambient tasks too:
        a watch is kept open for only while it's there (#749, Part 2)."""
        return frozenset(self._listed)

    @property
    def expecting(self) -> bool:
        """True while a tracked call waits for its result."""
        return bool(self._calls)

    def tool_use(self, name: str, use_id, tool_input) -> None:
        """Claude called a tool. Only the wait tools are kept, until their
        results come back."""
        if name not in WAIT_TOOLS or not isinstance(use_id, str) or not use_id:
            return
        if len(self._calls) >= MAX_WAITS:
            return
        self._calls[use_id] = (name, tool_input if isinstance(tool_input, dict) else {},
                               self._clock())

    def tool_result(self, use_id, content, is_error: bool) -> bool:
        """A tool's result came. True when the waits changed."""
        call = self._calls.pop(str(use_id), None) if use_id is not None else None
        if call is None or is_error:
            return False
        name, tool_input, at = call
        text = result_text(content)
        if name == CRON_CREATE:
            return self._add(self._cron(use_id, tool_input, at, text))
        if name == CRON_DELETE:
            job = tool_input.get("id")
            if not isinstance(job, str) or not job:
                return False
            kept = [w for w in self._waits if not (w.kind == CRON and w.job_id == job)]
            unnamed = [w for w in self._waits if w.kind == CRON and not w.job_id]
            if len(kept) == len(self._waits) and len(unnamed) == 1:
                # Claude Code deleted a job, and the one timer whose id
                # couldn't be read from its result must be it: no other
                # timer of this process could have been deleted.
                kept = [w for w in self._waits if w is not unnamed[0]]
            return self._replace(kept)
        if name == SCHEDULE_WAKEUP:
            # /loop has one wakeup at a time: each call sets the next one in
            # place of the last, and ``stop`` ends the loop.
            kept = [w for w in self._waits if w.kind != WAKEUP]
            if not _flag(tool_input.get("stop"), False):
                delay = _seconds(tool_input.get("delaySeconds"))
                if delay is not None:
                    # "Next wakeup scheduled for 10:20:00 (in 97s)." (2.1.331).
                    said = _WAKEUP_IN.search(text)
                    fire_at = at + int(said.group(1)) if said else 0.0
                    kept.append(Wait(WAKEUP, tool_use_id=str(use_id), set_at=at,
                                     prompt=_text(tool_input.get("prompt")),
                                     delay_seconds=delay,
                                     description=_text(tool_input.get("reason")),
                                     fire_at=fire_at))
            return self._replace(kept[-MAX_WAITS:])
        if name == MONITOR:
            description = _text(tool_input.get("description"))
            if description in self._listed:
                self._watching.add(description)  # listed already, while it started
            return self._add(Wait(WATCH, tool_use_id=str(use_id), set_at=at,
                                  description=description))
        return False

    def woke(self, source: str = "") -> bool:
        """Claude Code started a turn of its own for a timer (``command_lifecycle``
        with a ``wakeup_source``): the one-time waits whose time has come ran,
        and aren't waits any more. One due within ``WAKE_EARLY_SECONDS`` counts
        too, in case Claude Code fired it a little early. True when the
        waits changed. Only a wake seen counts: a one-time time that passed
        while Claude was still working may never have fired before the
        process ended. ``source``, the ``wakeup_source``, says which kind
        fired: a ScheduleWakeup's (there's only ever one) or a timer's."""
        if source == LOOP_WAKE:
            return self._replace([w for w in self._waits if w.kind != WAKEUP])
        kinds = (CRON,) if source == CRON_WAKE else (CRON, WAKEUP)
        # One wake is one job: the soonest due, so a second due a moment
        # later (a wakeup a minute after a timer) is still a wait.
        now = self._clock()
        due = []
        for wait in self._waits:
            when = wait.fire_time() if wait.one_shot and wait.kind in kinds else None
            if when is not None and when.timestamp() <= now + WAKE_EARLY_SECONDS:
                due.append((when, wait))
        if not due:
            return False
        overdue = [pair for pair in due if pair[0].timestamp() <= now]
        if not overdue and self._repeating_due(now):
            return False  # the wake is more likely a repeating timer's, on time
        fired = min(overdue or due, key=lambda pair: pair[0])[1]
        return self._replace([w for w in self._waits if w is not fired])

    def _repeating_due(self, now: float) -> bool:
        """Whether a repeating timer matched in the last ``WAKE_EARLY_SECONDS``."""
        try:
            since = datetime.fromtimestamp(now - WAKE_EARLY_SECONDS)
        except (OverflowError, OSError, ValueError):
            return False
        for wait in self._waits:
            if wait.kind == CRON and wait.recurring:
                when = next_cron_time(wait.cron, since)
                if when is not None and when.timestamp() <= now:
                    return True
        return False

    def tasks_changed(self, tasks) -> bool:
        """``background_tasks_changed``'s whole list, ambient tasks and all.
        A watch that has been listed and is gone has ended (its timeout, or
        Claude stopped it), so it's no longer a wait. True when the waits
        changed."""
        # A task with no description can't be told from a watch with none.
        listed = {_text(t.get("description")) for t in tasks if isinstance(t, dict)} \
            if isinstance(tasks, list) else set()
        listed.discard("")
        self._listed = listed
        kept = []
        for wait in self._waits:
            if wait.kind == WATCH and wait.description in self._watching \
                    and wait.description not in listed:
                continue
            kept.append(wait)
        self._watching = {w.description for w in kept if w.kind == WATCH} & listed
        return self._replace(kept)

    def _cron(self, use_id, tool_input, at: float, text: str) -> Wait:
        recurring = _flag(tool_input.get("recurring"), True)  # Claude Code's default
        job = ""
        found = _SCHEDULED.search(text)
        if found:
            # What Claude Code did, over what was asked.
            recurring = found.group(1) == "recurring job"
            job = found.group(2)
        else:
            # Worded differently in some later Claude Code: its ids are 8
            # hex digits ("f0d1911f"), which is enough for CronDelete.
            ids = _JOB_ID.findall(text)
            if len(ids) == 1:
                job = ids[0]
        return Wait(CRON, tool_use_id=str(use_id), set_at=at,
                    cron=_text(tool_input.get("cron"), 100), recurring=recurring,
                    prompt=_text(tool_input.get("prompt")), job_id=job,
                    durable="Persisted to" in text)

    def _add(self, wait: Wait) -> bool:
        if len(self._waits) >= MAX_WAITS:
            return False
        self._waits.append(wait)
        return True

    def _replace(self, kept: List[Wait]) -> bool:
        if kept == self._waits:
            return False
        self._waits = kept
        return True


# -- Cron expressions -----------------------------------------------------------

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
#: The longest a next fire time is looked for: past a leap day's four years.
_SEARCH_DAYS = 366 * 5


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset
    hours: frozenset
    days: frozenset
    months: frozenset
    weekdays: frozenset    # 0 is Sunday
    #: Whether the day-of-month and day-of-week fields were restricted (not
    #: starting "*"): with both, a day matching either counts, as in cron.
    days_set: bool
    weekdays_set: bool


def _field(text: str, low: int, high: int, names: Optional[dict] = None) -> Optional[frozenset]:
    values = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                return None
            step = int(step_text)
        if part == "*":
            start, end = low, high
        else:
            bounds = part.split("-")
            if len(bounds) > 2:
                return None
            numbers = []
            for bound in bounds:
                bound = bound.strip().lower()
                if names is not None and bound in names:
                    numbers.append(names[bound])
                elif bound.isdigit():
                    numbers.append(int(bound))
                else:
                    return None
            start = numbers[0]
            if len(numbers) == 2:
                end = numbers[1]
            else:
                # "5/15" is from 5 to the top, every 15.
                end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            return None
        values.update(range(start, end + 1, step))
    return frozenset(values) if values else None


def parse_cron(expression: str) -> Optional[CronSpec]:
    """A standard 5-field cron expression ("M H DoM Mon DoW"), or None if
    it isn't one. Numbers, names (jan, mon), ranges, lists and steps; 7 is
    Sunday as well as 0."""
    if not isinstance(expression, str):
        return None
    parts = expression.split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, weekday = parts
    minutes = _field(minute, 0, 59)
    hours = _field(hour, 0, 23)
    days = _field(day, 1, 31)
    months = _field(month, 1, 12, _MONTHS)
    weekdays = _field(weekday, 0, 7, _DAYS)
    if None in (minutes, hours, days, months, weekdays):
        return None
    weekdays = frozenset(0 if d == 7 else d for d in weekdays)
    return CronSpec(minutes, hours, days, months, weekdays,
                    days_set=not day.startswith("*"), weekdays_set=not weekday.startswith("*"))


def _day_matches(spec: CronSpec, when: datetime) -> bool:
    if when.month not in spec.months:
        return False
    in_month = when.day in spec.days
    in_week = (when.isoweekday() % 7) in spec.weekdays
    if spec.days_set and spec.weekdays_set:
        return in_month or in_week
    return in_month and in_week


def next_cron_time(expression: str, after: datetime) -> Optional[datetime]:
    """The first minute after ``after`` that ``expression`` matches, as a
    naive local time like ``after``; None if it can't be read or never
    matches (the 31st of February). The minute ``after`` is in doesn't
    count: a job made at 6:08:30 for "8 6 * * *" first runs tomorrow.
    Wall-clock time throughout, so a time in the hour a spring DST change
    skips (2:30 AM) is given as it's written, though the clock never shows
    it; that's rare enough to leave as it is."""
    spec = parse_cron(expression)
    if spec is None:
        return None
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    hours = sorted(spec.hours)
    minutes = sorted(spec.minutes)
    for _ in range(_SEARCH_DAYS):
        if _day_matches(spec, day):
            for hour in hours:
                for minute in minutes:
                    candidate = day.replace(hour=hour, minute=minute)
                    if candidate >= start:
                        return candidate
        day = (day + timedelta(days=1)).replace(hour=0, minute=0)
    return None


# -- Saying them ---------------------------------------------------------------


def clock_time(hour: int, minute: int) -> str:
    """"2:00 PM", "12:30 AM": the 12-hour clock, as Kelly's US English says it."""
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def when_words(when: datetime, now: datetime, dated: bool = False) -> str:
    """A fire time, as said: "2:00 PM" today, else with its day and date
    ("9:00 AM on Friday, November 28"), and its year if it isn't this one.
    Never "tomorrow". ``dated``: the date even today, for words kept with
    the reply and read on some later day."""
    at = clock_time(when.hour, when.minute)
    if when.date() == now.date() and not dated:
        return at
    date = f"{_DAY_NAMES[when.isoweekday() % 7]}, {_MONTH_NAMES[when.month - 1]} {when.day}"
    if when.year != now.year:
        date += f", {when.year}"
    return f"{at} on {date}"


def _joined(items: Sequence[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _named(items: Sequence[str]) -> str:
    """Up to ``MAX_NAMED`` of them, then how many more."""
    items = list(dict.fromkeys(items))
    if len(items) <= MAX_NAMED:
        return _joined(items)
    more = len(items) - MAX_NAMED
    return _joined(items[:MAX_NAMED] + [f"{more} more"])


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return str(number) + {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")


def _single(text: str, low: int, high: int) -> Optional[int]:
    return int(text) if text.isdigit() and low <= int(text) <= high else None


def _step(text: str) -> Optional[int]:
    if text.startswith("*/") and text[2:].isdigit() and int(text[2:]) > 0:
        return int(text[2:])
    return None


def schedule_words(expression: str) -> str:
    """A repeating cron's schedule in plain words ("every 30 minutes",
    "every weekday at 9:00 AM"), or "" when it's not one of the usual
    shapes. Never cron text: nobody should have to read that."""
    spec = parse_cron(expression)
    if spec is None:
        return ""
    minute, hour, day, month, weekday = expression.split()
    if month != "*":
        return ""
    if day == "*" and weekday == "*":
        if hour == "*":
            if minute == "*":
                return "every minute"
            step = _step(minute)
            if step:
                return "every minute" if step == 1 else f"every {step} minutes"
            fixed = _single(minute, 0, 59)
            if fixed is not None:
                return "every hour" if fixed == 0 else f"every hour at {fixed} past"
            return ""
        step = _step(hour)
        fixed = _single(minute, 0, 59)
        if step and fixed is not None:
            words = "every hour" if step == 1 else f"every {step} hours"
            return words if fixed == 0 else f"{words} at {fixed} past"
    fixed_minute = _single(minute, 0, 59)
    if fixed_minute is None or not all(h.isdigit() for h in hour.split(",")):
        return ""
    times = _joined([clock_time(h, fixed_minute) for h in sorted(spec.hours)])
    if day == "*" and weekday == "*":
        return f"every day at {times}"
    if day == "*":
        days = sorted(spec.weekdays)
        if days == [1, 2, 3, 4, 5]:
            return f"every weekday at {times}"
        if days == [0, 6]:
            return f"every Saturday and Sunday at {times}"
        return f"every {_joined([_DAY_NAMES[d] for d in days])} at {times}"
    if weekday == "*":
        fixed_day = _single(day, 1, 31)
        if fixed_day is not None:
            return f"on the {_ordinal(fixed_day)} of every month at {times}"
    return ""


def lost_waits(waits: Iterable[Wait]) -> List[Wait]:
    """The waits that end with the process: all but one Claude Code wrote to
    disk, which is its own to run. A one-time one that fired while the
    process lived is gone already (``WaitBook.woke``); one whose time passed
    with no wake seen may never have fired, so it's still lost."""
    return [wait for wait in waits if not wait.durable]


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def lost_sentence(waits: Iterable[Wait], now: datetime, dated: bool = False,
                  start: str = LOST_START) -> str:
    """What's said when the chat's process ends with waits Claude set
    (#749): "This chat ends when Claude's reply does, so the check Claude set
    for 2:00 PM won't run." Once per turn, naming every wait lost; "" when
    none is. ``dated``: every time with its date, for the copy kept with the
    reply and read later. ``start``: what comes before the waits
    (``ENDED_START``, ``far_start``)."""
    checks, watches, count = _phrases(lost_waits(waits), now, dated)
    clauses: List[str] = []
    if checks:
        clauses.append(_clause(checks) + " won't run")
    if watches:
        clauses.append(f"{watches} {_plural(count, 'stops', 'stop')}")
    if not clauses:
        return ""
    return f"{start}{', and '.join(clauses)}."


def _clause(phrases: Sequence[str]) -> str:
    """Phrases that may each hold an "and", joined with commas first."""
    phrases = list(phrases)
    return ", ".join(phrases[:-1]) + (", and " if len(phrases) > 1 else "") + phrases[-1]


def _phrases(lost: Sequence[Wait], now: datetime,
             dated: bool) -> Tuple[List[str], str, int]:
    """The waits as noun phrases: the checks ("the check Claude set for
    2:00 PM", one phrase per kind), the watches ("the watch Claude started
    (Watch PR 749)", or ""), and how many watches."""
    times: List[str] = []
    repeating: List[str] = []
    unknown = 0
    watches: List[str] = []
    for wait in lost:
        if wait.kind == WATCH:
            watches.append(_text(wait.description, MAX_SAID))
        elif wait.one_shot:
            when = wait.fire_time()
            if when is None:
                unknown += 1
            else:
                times.append(when_words(when, now, dated))
        else:
            words = schedule_words(wait.cron)
            if words:
                repeating.append(words)
            else:
                unknown += 1
    # Each kind in a clause of its own, so the "and"s inside one never run
    # into the next. Counted before repeats are merged: two checks at 2:00
    # are still "the checks".
    checks: List[str] = []
    if times:
        checks.append(f"the {_plural(len(times), 'check', 'checks')} Claude set "
                      f"for {_named(times)}")
    if repeating:
        checks.append(f"the repeating {_plural(len(repeating), 'check', 'checks')} "
                      f"Claude set to run {_named(repeating)}")
    if unknown:
        if checks:
            checks.append("another check Claude set" if unknown == 1
                          else f"{unknown} other checks Claude set")
        else:
            checks.append("the check Claude set" if unknown == 1
                          else f"the {unknown} checks Claude set")
    watch_phrase = ""
    if watches:
        named = [w for w in dict.fromkeys(watches) if w]
        unnamed = len(watches) - len([w for w in watches if w])
        listed = named[:MAX_NAMED]
        more = len(named) - len(listed) + unnamed
        if listed and more:
            listed.append(f"{more} more")
        noun = _plural(len(watches), "the watch Claude started", "the watches Claude started")
        watch_phrase = f"{noun} ({_joined(listed)})" if listed else noun
    return checks, watch_phrase, len(watches)


# -- Kept with the reply -----------------------------------------------------------

#: How many replies' notes a chat keeps, and the longest kept.
MAX_NOTES = 200
MAX_NOTE = 1000
MAX_KEY = 200


def remember_note(notes: Dict[str, str], key: str, line: str) -> Dict[str, str]:
    """``notes`` with ``line`` kept for the reply ``key``, newest last, and
    only the newest ``MAX_NOTES``. A new dict."""
    kept = dict(notes or {})
    if not key or not line or len(key) > MAX_KEY or len(line) > MAX_NOTE:
        return kept
    kept.pop(key, None)
    kept[key] = line
    while len(kept) > MAX_NOTES:
        kept.pop(next(iter(kept)))
    return kept


def clean_notes(raw) -> Dict[str, str]:
    """Stored notes made safe: each one of ours, on one line, of a sane length."""
    if not isinstance(raw, dict):
        return {}
    kept = {key: value for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str) and key and len(key) <= MAX_KEY
            and value.startswith(NOTE_STARTS) and len(value) <= MAX_NOTE
            and "\n" not in value and "\x00" not in key}
    while len(kept) > MAX_NOTES:
        kept.pop(next(iter(kept)))
    return kept


def with_notes(messages: Iterable, notes: Dict[str, str], assistant_kind: str = "assistant") -> List:
    """The messages, each reply with a kept note given it as its ``note``:
    matched by its key (the API message id) or its uuid, as a reply's cost
    is (#692). Copies of those it changes."""
    messages = list(messages)
    if not notes:
        return messages
    for index, message in enumerate(messages):
        if message.kind != assistant_kind:
            continue
        line = notes.get(message.key) if message.key else None
        if line is None and getattr(message, "uuid", ""):
            line = notes.get(message.uuid)
        if line:
            messages[index] = dataclasses.replace(message, note=line)
    return messages


# -- Keeping the chat open for them (#749, Part 2) ---------------------------------
#
# A Claude turn that's answered and idle, with nothing running in the
# background but with waits Claude set, keeps its ``claude`` process (the
# runner's "scheduled" outcome), so Claude Code can fire them. Which waits
# hold it, and for how long, is decided here, as plain data.

_DAY = 86400.0


def next_time(wait: Wait, now: datetime) -> Optional[datetime]:
    """When a timed wait fires next, in local time: a one-time one's time,
    a repeating one's next match after ``now``. None for a watch, or a time
    that can't be read."""
    if wait.kind == WATCH:
        return None
    if wait.one_shot:
        return wait.fire_time()
    return next_cron_time(wait.cron, now)


def _local(now: float) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(now)
    except (OverflowError, OSError, ValueError):
        return None


def held_waits(waits: Iterable[Wait], listed, now: float) -> List[Wait]:
    """The waits that keep the chat's process open at ``now`` (seconds since
    the epoch): a timer or wakeup due within ``MAX_HOLD_DAYS`` (one whose time
    can't be read, for that long after it was set), and a watch while its
    task is in Claude Code's background list (``listed``, descriptions). Not
    one Claude Code wrote to disk, a repeating one past Claude Code's own
    expiry, or a one-time one ``OVERDUE_GRACE_SECONDS`` past its time with no
    wake seen."""
    local = _local(now)
    if local is None:
        return []
    horizon = now + MAX_HOLD_DAYS * _DAY
    held: List[Wait] = []
    for wait in waits:
        if wait.durable:
            continue
        if wait.kind == WATCH:
            if wait.description and wait.description in listed:
                held.append(wait)
            continue
        if not wait.one_shot and now - wait.set_at > RECURRING_EXPIRY_DAYS * _DAY:
            continue
        when = next_time(wait, local)
        if when is None:
            if now - wait.set_at <= MAX_HOLD_DAYS * _DAY:
                held.append(wait)
            continue
        fires = when.timestamp()
        if wait.one_shot and fires < now - OVERDUE_GRACE_SECONDS:
            continue
        if fires <= horizon:
            held.append(wait)
    return held


def far_waits(waits: Iterable[Wait], now: float) -> List[Wait]:
    """The timed waits more than ``MAX_HOLD_DAYS`` away, which don't keep the
    chat open: said when Claude's reply is, as they won't run."""
    local = _local(now)
    if local is None:
        return []
    horizon = now + MAX_HOLD_DAYS * _DAY
    far = []
    for wait in waits:
        if wait.durable or wait.kind == WATCH:
            continue
        if not wait.one_shot and now - wait.set_at > RECURRING_EXPIRY_DAYS * _DAY:
            continue
        when = next_time(wait, local)
        if when is not None and when.timestamp() > horizon:
            far.append(wait)
    return far


def far_start() -> str:
    """``lost_sentence``'s start for the waits ``far_waits`` gives."""
    return FAR_START.format(days=MAX_HOLD_DAYS)


def next_recheck(held: Iterable[Wait], now: float) -> Optional[float]:
    """When, at the soonest, one of the ``held`` waits may stop holding the
    chat open with nothing to say so (seconds since the epoch): a one-time
    one that never fired, a repeating one Claude Code expires, one whose time
    can't be read. None when only watches hold it, which the background list
    says the end of."""
    local = _local(now)
    if local is None:
        return None
    times = []
    for wait in held:
        if wait.kind == WATCH:
            continue
        if not wait.one_shot:
            times.append(wait.set_at + RECURRING_EXPIRY_DAYS * _DAY)
            continue
        when = next_time(wait, local)
        if when is None:
            times.append(wait.set_at + MAX_HOLD_DAYS * _DAY)
        else:
            times.append(when.timestamp() + OVERDUE_GRACE_SECONDS)
    return min(times) if times else None


def _interval(words: str) -> bool:
    """"every 30 minutes", "every hour": a plain interval, which reads
    well as "and every 30 minutes after that"."""
    return words.startswith("every ") and " at " not in words and " on " not in words


def waiting_words(held: Iterable[Wait], now: datetime) -> str:
    """What a chat kept open is waiting for, for Turn Status, Properties and
    the reply's announcement: "Scheduled check at 2:00 PM, and every 30
    minutes after that." "Watching: Watch PR 749." "" for nothing."""
    held = list(held)
    sentences: List[str] = []
    times = sorted(t for t in (w.fire_time() for w in held if w.one_shot) if t is not None)
    unknown = sum(1 for w in held if w.one_shot and w.fire_time() is None)
    if times:
        noun = _plural(len(times), "check", "checks")
        sentences.append(f"Scheduled {noun} at {_named([when_words(t, now) for t in times])}.")
    repeating = [w for w in held if w.kind == CRON and w.recurring]
    for wait in repeating[:MAX_NAMED]:
        when = next_cron_time(wait.cron, now)
        words = schedule_words(wait.cron)
        if when is None:
            sentences.append(f"A repeating scheduled check is set{', ' + words if words else ''}.")
        elif not words:
            sentences.append(f"Scheduled check at {when_words(when, now)}, repeating.")
        elif _interval(words):
            sentences.append(f"Scheduled check at {when_words(when, now)}, "
                             f"and {words} after that.")
        else:
            sentences.append(f"Scheduled check at {when_words(when, now)}, then {words}.")
    if len(repeating) > MAX_NAMED:
        more = len(repeating) - MAX_NAMED
        sentences.append(f"And {more} more repeating {_plural(more, 'check', 'checks')}.")
    if unknown:
        sentences.append("A scheduled check is set." if unknown == 1
                         else f"{unknown} scheduled checks are set.")
    watches = [_text(w.description, MAX_SAID) for w in held if w.kind == WATCH]
    if watches:
        sentences.append(f"Watching: {_named(watches)}.")
    return " ".join(sentences)


def row_detail(held: Iterable[Wait], now: datetime) -> str:
    """The session list's detail for a chat kept open for its waits: "next
    check at 2:00 PM", else "watching: Watch PR 749"."""
    held = list(held)
    times = sorted(t for t in (next_time(w, now) for w in held) if t is not None)
    if times:
        return f"next check at {when_words(times[0], now)}"
    watch = next((w for w in held if w.kind == WATCH), None)
    if watch is not None:
        return f"watching: {_text(watch.description, MAX_SAID)}"
    return "a scheduled check is set" if held else ""


def stop_words(held: Iterable[Wait], now: datetime) -> str:
    """Said as Stop is pressed on a chat kept open for its waits: "Stopping
    ends the check Claude set for 2:00 PM too." "" for none."""
    checks, watches, _count = _phrases(lost_waits(held), now, False)
    phrases = checks + ([watches] if watches else [])
    if not phrases:
        return ""
    return f"Stopping ends {_clause(phrases)} too."


def quit_words(count: int) -> str:
    """For the "Work is running" question as you quit (#749)."""
    if count <= 0:
        return ""
    if count == 1:
        return "1 chat has scheduled checks, which won't run if you quit."
    return f"{count} chats have scheduled checks, which won't run if you quit."


class WaitSlots:
    """How many chats may keep a process open for waits at once (#749): a
    Settings choice, read each time, so a change applies to the next chat
    that asks. Thread-safe: runners ask from their worker threads."""

    def __init__(self, limit: Callable[[], int]) -> None:
        self._limit = limit
        self._holders: set = set()
        self._lock = threading.Lock()

    def take(self, holder) -> bool:
        """A slot for ``holder``, which may have one already. False when
        they're all taken."""
        try:
            limit = int(self._limit())
        except Exception:  # noqa: BLE001 - a broken setting holds nothing
            limit = 0
        with self._lock:
            if holder in self._holders:
                return True
            if len(self._holders) >= limit:
                return False
            self._holders.add(holder)
            return True

    def release(self, holder) -> None:
        with self._lock:
            self._holders.discard(holder)

    def __len__(self) -> int:
        with self._lock:
            return len(self._holders)
