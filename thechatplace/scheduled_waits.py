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
#: kept (an API-credit chat, the limit reached, Stop or quitting).
LOST_START = "This chat ends when Claude's reply does, so "


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
                return datetime.fromtimestamp(self.set_at + self.delay_seconds)
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

    @property
    def waits(self) -> List[Wait]:
        return list(self._waits)

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
            kept = [w for w in self._waits if not (w.kind == CRON and job and w.job_id == job)]
            return self._replace(kept)
        if name == SCHEDULE_WAKEUP:
            # /loop has one wakeup at a time: each call sets the next one in
            # place of the last, and ``stop`` ends the loop.
            kept = [w for w in self._waits if w.kind != WAKEUP]
            if not _flag(tool_input.get("stop"), False):
                delay = _seconds(tool_input.get("delaySeconds"))
                if delay is not None:
                    kept.append(Wait(WAKEUP, tool_use_id=str(use_id), set_at=at,
                                     prompt=_text(tool_input.get("prompt")),
                                     delay_seconds=delay,
                                     description=_text(tool_input.get("reason"))))
            return self._replace(kept[-MAX_WAITS:])
        if name == MONITOR:
            return self._add(Wait(WATCH, tool_use_id=str(use_id), set_at=at,
                                  description=_text(tool_input.get("description"))))
        return False

    def tasks_changed(self, tasks) -> bool:
        """``background_tasks_changed``'s whole list, ambient tasks and all.
        A watch that has been listed and is gone has ended (its timeout, or
        Claude stopped it), so it's no longer a wait. True when the waits
        changed."""
        listed = {_text(t.get("description")) for t in tasks if isinstance(t, dict)} \
            if isinstance(tasks, list) else set()
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
    count: a job made at 6:08:30 for "8 6 * * *" first runs tomorrow."""
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


def when_words(when: datetime, now: datetime) -> str:
    """A fire time, as said: "2:00 PM" today, else with its day and date
    ("9:00 AM on Friday, November 28"), and its year if it isn't this one.
    Never "tomorrow": the words are kept with the reply, and read later."""
    at = clock_time(when.hour, when.minute)
    if when.date() == now.date():
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
            return "every hour" if step == 1 else f"every {step} hours"
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


def lost_waits(waits: Iterable[Wait], now: datetime) -> List[Wait]:
    """The waits that end with the process: all but a one-time one whose
    time has come (it ran while the process lived, kept open by background
    work) and one Claude Code wrote to disk."""
    lost = []
    for wait in waits:
        if wait.durable:
            continue  # Claude Code keeps it; whether it runs is its own
        if wait.one_shot:
            when = wait.fire_time()
            if when is not None and when <= now:
                continue
        lost.append(wait)
    return lost


def lost_sentence(waits: Iterable[Wait], now: datetime) -> str:
    """What's said, and kept with the reply, when the chat's process ends
    with waits Claude set (#749): "This chat ends when Claude's reply does,
    so the check Claude set for 2:00 PM won't run." Once per turn, naming
    every wait lost; "" when none is."""
    lost = lost_waits(waits, now)
    if not lost:
        return ""
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
                times.append(when_words(when, now))
        else:
            words = schedule_words(wait.cron)
            if words:
                repeating.append(words)
            else:
                unknown += 1
    times = list(dict.fromkeys(times))
    repeating = list(dict.fromkeys(repeating))
    checks: List[str] = []
    if times:
        checks.append(f"for {_named(times)}")
    if repeating:
        checks.append(f"to run {_named(repeating)}")
    count = len(times) + len(repeating) + unknown
    groups: List[str] = []
    if count:
        noun = "check" if count == 1 else "checks"
        if checks:
            said = f"the {noun} Claude set {_joined(checks)}"
            if unknown:
                said += f", and {unknown} more"
        else:
            said = f"the {noun} Claude set"
        groups.append(said)
    if watches:
        named = [w for w in dict.fromkeys(watches) if w]
        noun = "watch" if len(watches) == 1 else "watches"
        groups.append(f"the {noun} Claude started ({_named(named)})" if named
                      else f"the {noun} Claude started")
    return f"{LOST_START}{_joined(groups)} won't run."


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
            and value.startswith(LOST_START) and len(value) <= MAX_NOTE
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
