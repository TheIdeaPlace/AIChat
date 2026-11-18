"""What The Chat Place has spent on API credits (#658), and the limits on it.

The API can't tell the app what's left on an account (other tools, IDT among
them, spend from the same key), so the app keeps its own total and its own
limits: an overall one ("stop API chats once The Chat Place has spent $20")
and a per-message one ("stop any one message at about $2").

The ledger
----------
``api-spend.jsonl`` in the app's folder: JSON lines, only ever appended to,
so history is never lost and a crash can cost at most the line being
written. Each line is one of:

* ``reserve``: before a turn starts, the most it may spend (the smaller of
  the per-message limit and what's left of the overall one). Claude Code is
  given that as ``--max-budget-usd``.
* ``settle``: what Claude Code said a result cost (``total_cost_usd``), by
  model too. A turn can have several (Send Now, follow-ups), each the cost
  since the one before.
* ``correction``: an OpenAI late-usage attribution reduces an already
  recorded settlement; the earlier turn is charged first, so a crash can
  over-count, never free unaccounted credit.
* ``close``: the turn's process is over. ``complete`` (exactly ``true``)
  when every message sent got its result, so what was settled is the whole
  cost; otherwise (Stop mid-reply, a crash, a result without a usable cost,
  a settlement that couldn't be written) the cost isn't all known, and the
  reservation stays counted.
* ``rename``: Claude Code gave a chat another id than the one asked for
  (#663); its spending so far, and anything recorded under the old id
  later, counts under the new one.
* ``reset``: Reset in API Keys starts a new total. Earlier lines are kept,
  for Usage's today and last 7 days.
* ``release``: Release Held in API Keys lets go of reservations no running
  turn holds (left by an earlier run, a crash, or a Stop whose cost wasn't
  known), without touching what was spent.

Each reservation carries the id of the app run that made it (``run``), so a
line can be traced to the run it came from; which holds belong to turns
still running is known in memory (``SpendLedger._live``).

Counting is conservative everywhere, because it decides whether money may be
spent: a reservation that's open, or closed incomplete, counts at its full
amount (less what was settled against it) until Reset. One left by a crashed
run is still counted when the app starts again. A line that can't be read
(not JSON, a field of the wrong kind, a number that isn't finite or is far
past any limit, a time that isn't one) stops all API spending, with a
reason, until Reset; it can never free spending up, and nothing in a line
can make reading the ledger fail, so Reset always gets the ledger going
again. Reset clears every reservation except those of turns running in
this process, which settle as usual afterwards.

One lock for the whole process covers reading the ledger, deciding and
appending a reservation, so two chats starting at once can't both be given
the same remaining limit. (One copy of The Chat Place runs at a time; a
second process isn't locked out.)

The limits
----------
Kept in ``api-limits.json`` beside the ledger, not with the window's
settings (speech.SpeechSettings): they're about money, so they're read under
the ledger's lock, they're wx-free, and an unreadable file stops spending
(rather than falling back to defaults, as unreadable window settings do).
A missing file means the defaults.

What a result cost
------------------
``claude -p`` reports ``total_cost_usd`` (and modelUsage's ``costUSD``) as
a running total, not the latest result's cost. Checked with Claude Code
2.1.325 on the real API: the total is the process's so far *plus* the cost
state it restored, so a ``--resume``d turn's includes the chat's earlier
turns, and a ``--fork-session`` turn's the source chat's (turn 1 0.00125;
turn 2, resumed, 0.00153, though it cost about 0.00028 itself).
``--max-budget-usd`` counts only the process's own spending, so budgets are
unaffected.

So the app keeps, for every chat it runs, the last total Claude Code
reported for it (its *baseline*, ``claude-cost-baselines.json`` beside the
ledger), recorded after every result of every turn, subscription chats
included (for them it's only ever a baseline, never spending). Each result
after a process's first adds what the total grew by.

Whether the first result's total includes restored cost is read from its
tokens, not from how big the total is (#663; the review found two chats
alternating in a folder under-counted by comparing totals). Checked with
2.1.325: a result's ``usage`` holds that turn's own tokens and
``modelUsage`` the cumulative ones, restored included, so a result whose
modelUsage tokens are more than its own were restored. Two chats
alternating in one folder each restored their own cost every time, and so
did a fork (from its source). Then:

* Not restored (or a new chat, which has nothing to restore): its whole
  total is this process's, counted in full, however big.
* Restored, with the chat's baseline known: the total less the baseline
  that was restored (below).
* Restored with no baseline (a lost record, Continue Here from a desktop
  session, a fork of a chat run elsewhere), a baseline the total is below,
  or no tokens to tell by (an older Claude Code): the total, but no more
  than the turn's reservation. The restored part is spending the app can't
  see, and ``--max-budget-usd`` keeps what this process itself spends to
  the reservation (give or take one request), so that's the most it can
  have added that's known to be this process's. That count is a guess, so
  the turn says its cost isn't known (``CostTracker.guessed``), rather than
  saying the guess as what it cost.

Which baseline was restored (#663). Claude Code saves a chat's cost only
when its process ends cleanly. Checked with 2.1.325 (Stop is an interrupt,
then a kill): turn 1 ended cleanly at 0.003942; turn 2 was stopped, its
result 0.007856; turn 3, resumed, reported 0.004945, which is turn 1's
0.003942 plus its own 0.001003. Turn 2's cost was never saved. So each chat
keeps two baselines: the *latest* total reported, and the *clean* one, the
total at the end of its last process that ended cleanly. A resumed first
result counts from the clean one: counting from the latest would take the
stopped turn's cost (already counted at Stop) off this turn's, an
under-count, or with a cheap turn give a total below it. The latest is
used only when the tokens show it was what was restored (each baseline
keeps modelUsage's cumulative tokens, and the restored part of a result's
tokens matches the baseline it came from), in case a later Claude Code
saves a stopped turn's cost. A chat with no clean end on record (an
earlier build's entry, or one whose processes have all been stopped) has
no usable baseline: what was saved isn't known, so a restored first result
is counted up to the reservation and its cost said as not known.

Within one process a total never shrinks; if it does, that result adds
nothing and the tracker starts again from it. ``CUMULATIVE_COST`` False
would take each total as that result's own cost.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import platform_paths

LEDGER_FILE = "api-spend.jsonl"
LIMITS_FILE = "api-limits.json"
#: Each chat's last reported total_cost_usd (see "What a result cost").
BASELINES_FILE = "claude-cost-baselines.json"
#: Where every provider's limits start until the user sets their own: modest
#: for anyone's first key, the same for Claude and OpenAI. $2 a message
#: still covers a real agent turn (the largest in Kelly's first real use was
#: $1.44 on Opus) and stops a runaway one early. Saved limits are kept.
DEFAULT_OVERALL_LIMIT = 20.00
DEFAULT_MESSAGE_LIMIT = 2.00
#: The largest limit accepted, so a typo can't make a limit meaningless.
MAX_LIMIT = 100000.0
#: Less than this left is "the limit is reached": a turn can't do anything
#: useful for less (billing.MIN_BUDGET).
MIN_RESERVATION = 0.01
#: Whether total_cost_usd is a running total (restored cost state included),
#: as checked with Claude Code 2.1.325 (see "What a result cost").
CUMULATIVE_COST = True

#: What a purpose is called, for Usage.
PURPOSES = ("turn", "aside", "test")

#: One lock for every ledger in the process.
_LOCK = threading.RLock()


class SpendRefused(Exception):
    """A reservation was refused; ``str()`` says why, in words."""


@dataclass
class Limits:
    overall: float = DEFAULT_OVERALL_LIMIT
    per_message: float = DEFAULT_MESSAGE_LIMIT


@dataclass
class Reservation:
    id: str
    amount: float
    chat_id: str = ""
    purpose: str = "turn"


@dataclass
class _Open:
    amount: float
    chat_id: str
    settled: float = 0.0
    closed: bool = False


@dataclass
class Totals:
    """The total, as Usage and the status bar show it."""

    #: Settled spending since the last Reset.
    spent: float = 0.0
    #: Still held by reservations (running turns, or ones that ended
    #: without their whole cost known).
    held: float = 0.0
    #: Settled spending today and in the last 7 days (local time), across
    #: Resets.
    today: float = 0.0
    last_7_days: float = 0.0
    #: Since the last Reset, by chat and by model.
    by_chat: Dict[str, float] = field(default_factory=dict)
    by_model: Dict[str, float] = field(default_factory=dict)
    #: Settled spending by chat across Resets: what each chat has cost in
    #: all (Properties and the status bar, #658).
    chat_all_time: Dict[str, float] = field(default_factory=dict)
    #: When the total was last reset (epoch seconds), or 0 for never.
    reset_at: float = 0.0
    #: Why nothing may be spent (a damaged line, unreadable limits), or "".
    problem: str = ""
    limits: Limits = field(default_factory=Limits)
    #: Of ``held``, what no turn running in this app run holds: left by an
    #: earlier run or a crash, or by a Stop whose cost wasn't known. Release
    #: Held lets it go.
    releasable: float = 0.0

    @property
    def counted(self) -> float:
        """What counts against the overall limit."""
        return self.spent + self.held

    @property
    def left(self) -> float:
        return max(0.0, self.limits.overall - self.counted)


def _money(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):  # an integer too big for a float
        return None
    return value if math.isfinite(value) and value >= 0 else None


#: The most a ledger line may say anything cost or held: far past any limit
#: (MAX_LIMIT), so a bigger one can only be damage.
_MOST = MAX_LIMIT * 10
#: The latest time a line may carry (the year 5138): later is damage.
_LATEST = 1e11


def _amount(value) -> Optional[float]:
    """A ledger line's dollars, or None if they can't be (#663)."""
    value = _money(value)
    return value if value is not None and value <= _MOST else None


def _when(value) -> Optional[float]:
    """A ledger line's time, or None if it isn't one."""
    value = _money(value)
    return value if value is not None and value <= _LATEST else None


def valid_cost(value) -> bool:
    """Whether a result's ``total_cost_usd`` can be counted (#663): a
    finite number, not negative. Missing, null, NaN or infinite leaves the
    cost unknown, and the reservation held."""
    return _money(value) is not None


def _limit(value) -> Optional[float]:
    value = _money(value)
    return value if value is not None and 0 < value <= MAX_LIMIT else None


def cents_down(amount: float) -> float:
    """Dollars rounded down to the cent. Rounded to a millionth of a cent
    first, so 7.5 - 2.6 (4.8999999999999995 as a float) is 4.90, not 4.89."""
    return math.floor(round(amount * 100, 6)) / 100


#: Costs closer than this are the same (a float's last digits), so a
#: restored cost that's its baseline is never taken as smaller than it.
_SAME = 1e-9


def _model_costs(model_costs) -> Dict[str, float]:
    """modelUsage's costs, checked: {model: dollars}."""
    clean = {}
    for model, cost in (model_costs or {}).items() if isinstance(model_costs, dict) else ():
        cost = _money(cost)
        if isinstance(model, str) and cost is not None:
            clean[model] = cost
    return clean


def _tokens(tokens) -> Optional[Tuple[int, int]]:
    """(this turn's own tokens, cumulative tokens) from a result, checked;
    None when there's nothing to tell by (claude_cli.result_tokens)."""
    if not isinstance(tokens, (tuple, list)) or len(tokens) != 2:
        return None
    own, everything = tokens
    if type(own) is not int or type(everything) is not int or own < 0 or everything < 0:
        return None
    return own, everything


def _count(value) -> Optional[int]:
    """A baseline's cumulative tokens, checked, or None."""
    return value if type(value) is int and value >= 0 else None


@dataclass
class Baseline:
    """What Claude Code reported a chat had cost after one result: its
    total, by model, and modelUsage's cumulative tokens (None if not
    known), so a later result's restored part can be matched to it."""

    total: float
    models: Dict[str, float] = field(default_factory=dict)
    tokens: Optional[int] = None


class CostTracker:
    """Turns one process's ``total_cost_usd`` (and modelUsage's ``costUSD``)
    into what each result added (see "What a result cost" above).

    ``start``: the latest baseline of the chat the process resumes or forks
    (0.0 for a new chat, None when it isn't known), ``start_models`` its
    costs by model and ``start_tokens`` its cumulative tokens. ``clean``:
    the chat's clean baseline (a Baseline: the total when its last process
    that ended cleanly ended), or None. ``resumed``: the process restores a
    chat's cost state (``--resume``, a fork's included); by default, when
    ``start`` isn't 0.0. ``cap``: the turn's reservation, the most a
    restored first result with no usable baseline is counted at; None for
    no cap (a subscription turn, which spends nothing)."""

    def __init__(self, cumulative: Optional[bool] = None, start: Optional[float] = 0.0,
                 start_models: Optional[Dict[str, float]] = None,
                 cap: Optional[float] = None, resumed: Optional[bool] = None,
                 clean: Optional[Baseline] = None,
                 start_tokens: Optional[int] = None) -> None:
        self.cumulative = CUMULATIVE_COST if cumulative is None else cumulative
        self.resumed = (start != 0.0) if resumed is None else bool(resumed)
        start = _money(start) if start is not None else None
        self._last_total: Optional[float] = start
        self._last_models: Dict[str, float] = _model_costs(start_models) \
            if start is not None else {}
        self._start_tokens = _count(start_tokens) if start is not None else None
        self._clean = clean if isinstance(clean, Baseline) and _money(clean.total) is not None \
            else None
        self._first = True
        self.cap = _money(cap) if cap is not None else None
        #: What the first result showed: True restored, False not, None no
        #: tokens to tell by (or no result yet).
        self.restored: Optional[bool] = None
        #: The first result was counted up to the cap, as what was restored
        #: couldn't be told (#663): what this process cost isn't known, and
        #: the turn says so rather than saying the guess.
        self.guessed = False
        #: Everything this process has cost so far, and by model.
        self.total = 0.0
        self.models: Dict[str, float] = {}

    @property
    def known_total(self) -> Optional[float]:
        """What this process cost, to be said, or None when it's a guess."""
        return None if self.guessed else self.total

    def _restored_base(self, total: float, counted) -> Optional[Baseline]:
        """The baseline a restored first result's total includes, or None
        if neither fits (see "Which baseline was restored")."""
        latest = (Baseline(self._last_total, dict(self._last_models), self._start_tokens)
                  if self._last_total is not None else None)
        fits = [b for b in (self._clean, latest)
                if b is not None and total >= b.total - _SAME]
        if counted is not None:
            restored_tokens = counted[1] - counted[0]
            for base in fits:
                if base.tokens is not None and base.tokens == restored_tokens:
                    return base
        # No tokens to match by: the clean one, as only a clean end saves
        # the cost; never an under-count, as it's the smaller of the two.
        return fits[0] if fits else None

    def add(self, total_cost, model_costs=None, tokens=None):
        """(cost added, {model: cost added}) for one result. ``tokens``:
        (its own tokens, modelUsage's cumulative tokens), or None."""
        total = _money(total_cost)
        if total is None:
            return 0.0, {}
        models = _model_costs(model_costs)
        first, self._first = self._first, False
        base = self._last_total
        if not self.cumulative:
            delta, by_model = total, dict(models)
        elif not first:
            if base is not None and total >= base - _SAME:
                # The growth since this process's last result.
                delta = max(0.0, total - base)
                by_model = {m: max(0.0, c - self._last_models.get(m, 0.0))
                            for m, c in models.items()}
            else:
                # A running total that shrank within one process: nothing
                # is known to be added; carry on from it.
                delta, by_model = 0.0, {}
        else:
            counted = _tokens(tokens)
            if not self.resumed:
                restored = False  # a new chat has nothing to restore
            elif counted is None:
                restored = None
            else:
                restored = counted[1] > counted[0]
            self.restored = restored
            restored_from = self._restored_base(total, counted) if restored else None
            if restored is False:
                # Nothing restored: the whole total is this process's own,
                # counted in full, even past the reservation (a turn can run
                # over --max-budget-usd by one request).
                delta, by_model = total, dict(models)
            elif restored_from is not None:
                # The chat's own cost restored: what this process added.
                delta = max(0.0, total - restored_from.total)
                by_model = {m: max(0.0, c - restored_from.models.get(m, 0.0))
                            for m, c in models.items()}
            else:
                # Restored with no baseline to take off (or none the total
                # is at or above, so neither is what was restored), or no
                # tokens to tell by: some of the total is earlier spending
                # the app can't see. --max-budget-usd keeps this process's
                # own spending to the reservation (give or take one
                # request), so the total is counted up to that, and never
                # more. A guess, so the cost is said as not known.
                self.guessed = True
                delta = total if self.cap is None else min(total, self.cap)
                scale = delta / total if total > 0 else 0.0
                by_model = {m: c * scale for m, c in models.items()}
        self._last_total = total
        self._last_models = models
        self.total += delta
        for model, cost in by_model.items():
            self.models[model] = self.models.get(model, 0.0) + cost
        return delta, {m: c for m, c in by_model.items() if c > 0}


class SpendLedger:
    def __init__(self, folder: Optional[Path] = None,
                 clock: Callable[[], float] = time.time, defaults: Optional[Limits] = None) -> None:
        self._folder = Path(folder) if folder is not None else None
        self._defaults = defaults or Limits()
        self._clock = clock
        #: Reservations made in this process and not yet closed: Reset keeps
        #: them, and Release Held leaves them be.
        self._live: set = set()
        #: Reservations with a settlement that couldn't be written (#663):
        #: their close is never "complete", so they stay counted.
        self._unrecorded: set = set()
        #: This app run's id, written with each reservation it makes.
        self.run_id = uuid.uuid4().hex

    @property
    def folder(self) -> Path:
        return self._folder if self._folder is not None else platform_paths.app_data_dir()

    @property
    def path(self) -> Path:
        return self.folder / LEDGER_FILE

    @property
    def limits_path(self) -> Path:
        return self.folder / LIMITS_FILE

    @property
    def baselines_path(self) -> Path:
        return self.folder / BASELINES_FILE

    # -- each chat's cost baseline ---------------------------------------------------

    @staticmethod
    def _entry(raw) -> Optional[dict]:
        """One baseline as kept ({"total", "models", "tokens"}), checked, or
        None."""
        if not isinstance(raw, dict):
            return None
        total = _money(raw.get("total"))
        if total is None:
            return None
        return {"total": total, "models": _model_costs(raw.get("models")),
                "tokens": _count(raw.get("tokens"))}

    def _read_baselines(self) -> Dict[str, dict]:
        """{chat: {"total", "models", "tokens", "clean"}}, "clean" being the
        same shape or None (see "Which baseline was restored"). An entry
        with no "clean" at all is a legacy one, kept as it is: written by
        an earlier build, or since a process whose history wasn't known, so
        what Claude Code last saved for the chat isn't known (``baselines``).
        A missing or damaged file is none known, which counts each first
        result in full up to its cap."""
        try:
            raw = json.loads(self.baselines_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return {}
        if not isinstance(raw, dict):
            return {}
        kept = {}
        for chat, entry in raw.items():
            if not isinstance(chat, str):
                continue
            latest = self._entry(entry)
            if latest is not None:
                if "clean" in entry:
                    latest["clean"] = self._entry(entry.get("clean"))
                kept[chat] = latest
        return kept

    def recorded_baseline(self, chat_id: str) -> Optional[Tuple[float, Dict[str, float]]]:
        """(total, {model: cost}) Claude Code last reported for ``chat_id``,
        or None if it isn't known: what's recorded, a legacy entry's too.
        For looking at only (tests, diagnostics): never count from it, as a
        legacy or stopped entry's total under-counts (#663 review 5). What a
        turn counts from is ``baselines``."""
        if not chat_id:
            return None
        with _LOCK:
            entry = self._read_baselines().get(chat_id)
        return (entry["total"], dict(entry["models"])) if entry else None

    def baselines(self, chat_id: str) -> Optional[Tuple[Baseline, Optional[Baseline]]]:
        """(latest, clean) baselines for ``chat_id``, or None if none is
        known. An entry with no clean end on record (a legacy one, with no
        "clean", ``_read_baselines``; or a "clean" that's empty or damaged)
        is none known too (#663): its latest total may include a stopped
        turn's cost Claude Code never saved, so counting from it would
        under-count. A restored first result is then counted up to the
        reservation, and its cost said as not known."""
        if not chat_id:
            return None
        with _LOCK:
            entry = self._read_baselines().get(chat_id)
        if not entry or not entry.get("clean"):
            return None
        clean = entry["clean"]
        return (Baseline(entry["total"], dict(entry["models"]), entry["tokens"]),
                Baseline(clean["total"], dict(clean["models"]), clean["tokens"])
                if clean else None)

    def _mentions(self, chat_id: str) -> bool:
        """Whether the ledger has ever recorded anything for ``chat_id``."""
        totals, reservations = self._replay()
        return (chat_id in totals.chat_all_time or chat_id in totals.by_chat
                or any(e.chat_id == chat_id for e in reservations.values()))

    def rename_chat(self, old_id: str, new_id: str, api_chat: bool = True) -> None:
        """Claude Code gave the chat ``old_id`` the id ``new_id`` (#663;
        own_store.rename_id): its spending and its baseline go with it, so
        Usage doesn't list it under chats no longer listed. The ledger gets
        a line only for a chat on API credits (``api_chat``) or one it
        already names, so a subscription chat's rename never makes the
        ledger of someone who doesn't use API credits; the baseline moves
        for any chat. Let go if it can't be written: the spending still
        counts in the total."""
        if not old_id or not new_id or old_id == new_id:
            return
        from .provider_storage import atomic_write_json
        with _LOCK:
            try:
                if api_chat or (self.path.exists() and self._mentions(old_id)):
                    self._append({"type": "rename", "from": old_id, "to": new_id})
            except OSError:
                pass
            baselines = self._read_baselines()
            if old_id in baselines:
                baselines[new_id] = baselines.pop(old_id)
                try:
                    atomic_write_json(self.baselines_path, baselines)
                except OSError:
                    pass

    def set_baseline(self, chat_id: str, total, models=None, tokens=None,
                     clean: bool = False) -> None:
        """Record what Claude Code reported for ``chat_id`` after a result:
        its total, by model, and modelUsage's cumulative ``tokens``.
        ``clean``: the chat's process ended cleanly after it, so this is
        also what Claude Code saved for it (see "Which baseline was
        restored"); otherwise the clean one recorded before is kept. With
        none recorded before (a legacy entry, or none at all), what Claude
        Code saved isn't known until a process ends cleanly, so the entry
        is left legacy (no "clean") until then. Not part of the total
        (Reset leaves it), and a failure to write is let go: the next turn
        then counts its first result up to its cap."""
        total = _money(total)
        if not chat_id or total is None:
            return
        from .provider_storage import atomic_write_json
        with _LOCK:
            baselines = self._read_baselines()
            # Exactly as reported, never rounded: a baseline rounded up
            # would make the restored total look smaller than it, and so not
            # restored (CostTracker).
            latest = {"total": total, "models": _model_costs(models), "tokens": _count(tokens)}
            before = baselines.get(chat_id) or {}
            entry = dict(latest)
            if clean:
                entry["clean"] = dict(latest)
            elif "clean" in before:
                entry["clean"] = before["clean"]
            baselines[chat_id] = entry
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self.baselines_path, baselines)
            except OSError:
                pass

    # -- limits -----------------------------------------------------------------

    def _read_limits(self):
        """(Limits, problem)."""
        try:
            raw = json.loads(self.limits_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._defaults, ""
        except (OSError, ValueError, RecursionError) as exc:
            return self._defaults, (f"The Chat Place couldn't read its API spending limits ({exc}), "
                              "so it won't spend API credits until they're set again in "
                              "Tools, API Keys.")
        overall = _limit(raw.get("overall")) if isinstance(raw, dict) else None
        per_message = _limit(raw.get("per_message")) if isinstance(raw, dict) else None
        if overall is None or per_message is None:
            return self._defaults, ("The Chat Place's API spending limits are damaged, so it won't "
                              "spend API credits until they're set again in Tools, API Keys.")
        return Limits(overall, per_message), ""

    def limits(self) -> Limits:
        with _LOCK:
            return self._read_limits()[0]

    def set_limits(self, overall: float, per_message: float) -> None:
        """Save the limits; raises ValueError for one that isn't at least a
        cent and at most MAX_LIMIT, OSError if it can't be written."""
        overall, per_message = _limit(overall), _limit(per_message)
        if overall is None or per_message is None or min(overall, per_message) < MIN_RESERVATION:
            raise ValueError(f"A limit must be at least 1 cent and at most ${MAX_LIMIT:,.0f}.")
        from .provider_storage import atomic_write_json
        with _LOCK:
            self.folder.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.limits_path, {"overall": round(overall, 2),
                                                 "per_message": round(per_message, 2)})

    # -- reading ----------------------------------------------------------------

    def _replay(self, now: Optional[float] = None):
        """(Totals, open reservations by id), read from the file."""
        now = self._clock() if now is None else now
        limits, problem = self._read_limits()
        totals = Totals(limits=limits, problem=problem)
        reservations: Dict[str, _Open] = {}
        local = time.localtime(now)
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        week_start = midnight - 6 * 86400
        damage = ""
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            data = b""
        except OSError as exc:
            totals.problem = totals.problem or (
                f"The Chat Place couldn't read its record of API spending ({exc}), so it won't "
                "spend API credits until it can.")
            return totals, reservations
        lines = data.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        #: Chats renamed (#663): old id -> new id.
        renamed: Dict[str, str] = {}
        settled_all, settled_counted, settlement_dates = {}, {}, {}

        def chat_of(chat: str) -> str:
            seen = set()
            while chat in renamed and chat not in seen:
                seen.add(chat)
                chat = renamed[chat]
            return chat
        for number, raw in enumerate(lines, 1):
            record = self._parse(raw)
            if record is None:
                damage = damage or f"line {number}"
                continue
            try:
                if record["type"] == "reset":
                    keep = record.get("keep", [])
                    reservations = {rid: entry for rid, entry in reservations.items()
                                    if rid in keep and not entry.closed}
                    totals.spent, totals.by_chat, totals.by_model = 0.0, {}, {}
                    settled_counted.clear()
                    totals.reset_at = record["at"]
                    damage = ""  # Reset is how damage is put behind you
                    continue
                if record['type'] in ('settle', 'correction'):
                    rid, cost = record['id'], record['cost']
                    if record['type'] == 'correction' and -cost > settled_all.get(rid, 0) + .000001:
                        raise ValueError('Correction exceeds recorded spending')
                    counted = cost if cost >= 0 else -min(-cost, settled_counted.get(rid, 0))
                    settled_all[rid] = settled_all.get(rid, 0) + cost
                    settled_counted[rid] = settled_counted.get(rid, 0) + counted
                    today_cost = cost if record['at'] >= midnight else 0
                    week_cost = cost if record['at'] >= week_start else 0
                    dated = settlement_dates.setdefault(rid, [])
                    if cost >= 0:
                        dated.append([cost, record['at']])
                    else:
                        left, today_cost, week_cost = -cost, 0, 0
                        for batch in reversed(dated):
                            taken = min(left, batch[0])
                            if batch[1] >= midnight:
                                today_cost -= taken
                            if batch[1] >= week_start:
                                week_cost -= taken
                            batch[0] -= taken
                            left -= taken
                            if left <= .0000001:
                                break
                    record = dict(record, _counted_cost=counted,
                                  _today_cost=today_cost, _week_cost=week_cost)
                self._replay_line(record, reservations, totals, renamed, chat_of,
                                  midnight, week_start)
            except Exception:  # noqa: BLE001 - a line can only ever be damage (#663)
                damage = damage or f"line {number}"
        # Settlements before a reset still count in today and the last 7
        # days, which are kept across resets: worked out apart.
        totals.held = sum(max(0.0, e.amount - e.settled) for e in reservations.values())
        totals.releasable = sum(max(0.0, e.amount - e.settled)
                                for rid, e in reservations.items() if rid not in self._live)
        if not (math.isfinite(totals.held) and math.isfinite(totals.spent)):
            damage = damage or "its totals"
        if damage:
            totals.problem = totals.problem or (
                f"The Chat Place's record of API spending is damaged ({damage} of "
                f"{LEDGER_FILE}), so it won't spend API credits until you reset the total in "
                "Tools, API Keys.")
        return totals, reservations

    @staticmethod
    def _replay_line(record, reservations, totals, renamed, chat_of, midnight,
                     week_start) -> None:
        """One checked line other than a reset, into the totals."""
        kind = record["type"]
        if kind == "release":
            for rid in record["ids"]:
                reservations.pop(rid, None)
            return
        if kind == "rename":
            old, new = record["from"], chat_of(record["to"])
            if old == new:
                return
            renamed[old] = new
            for book in (totals.by_chat, totals.chat_all_time):
                if old in book:
                    book[new] = book.get(new, 0.0) + book.pop(old)
            for entry in reservations.values():
                if entry.chat_id == old:
                    entry.chat_id = new
            return
        rid = record["id"]
        if kind == "reserve":
            reservations[rid] = _Open(record["amount"], chat_of(record.get("chat", "")))
            return
        entry = reservations.get(rid)
        if kind in ("settle", "correction"):
            cost = record["cost"]
            counted = record.get("_counted_cost", cost)
            totals.spent += counted
            chat = chat_of(record.get("chat") or (entry.chat_id if entry else ""))
            if chat:
                totals.by_chat[chat] = totals.by_chat.get(chat, 0.0) + counted
                totals.chat_all_time[chat] = totals.chat_all_time.get(chat, 0.0) + cost
            for model, amount in (record.get("models") or {}).items():
                totals.by_model[model] = totals.by_model.get(model, 0.0) + (amount * counted / cost if cost else 0)
            totals.today += record.get("_today_cost", cost if record["at"] >= midnight else 0)
            totals.last_7_days += record.get("_week_cost", cost if record["at"] >= week_start else 0)
            if entry is not None:
                entry.settled += cost
            return
        if kind == "close" and entry is not None:
            # Only exactly true frees the rest (#663): "false", 1 or a
            # missing field keeps it counted.
            if record.get("complete") is True:
                reservations.pop(rid, None)
            else:
                entry.closed = True

    @staticmethod
    def _parse(raw: bytes) -> Optional[dict]:
        """A ledger line, checked, or None if it's damaged: anything a line
        could hold that the replay can't take (#663)."""
        try:
            record = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return None
        if not isinstance(record, dict) or _when(record.get("at")) is None:
            return None
        kind = record.get("type")
        if kind == "reset":
            keep = record.get("keep", [])
            return record if isinstance(keep, list) and all(
                isinstance(x, str) for x in keep) else None
        if kind == "release":
            ids = record.get("ids")
            return record if isinstance(ids, list) and all(
                isinstance(x, str) for x in ids) else None
        if kind == "rename":
            old, new = record.get("from"), record.get("to")
            return record if isinstance(old, str) and old and isinstance(new, str) \
                and new else None
        if kind not in ("reserve", "settle", "correction", "close") or not isinstance(record.get("id"), str) \
                or not record["id"]:
            return None
        if not isinstance(record.get("chat", ""), str):
            return None
        if kind == "reserve" and _amount(record.get("amount")) is None:
            return None
        if kind in ("settle", "correction"):
            sign = -1 if kind == "correction" else 1
            if not isinstance(record.get("cost"), (int, float)) or isinstance(record["cost"], bool) or _amount(sign * record["cost"]) is None:
                return None
            models = record.get("models", {})
            if not isinstance(models, dict) or any(
                    not isinstance(m, str) or not isinstance(c, (int, float)) or isinstance(c, bool) or _amount(sign*c) is None for m, c in models.items()):
                return None
        return record

    def totals(self, now: Optional[float] = None) -> Totals:
        with _LOCK:
            return self._replay(now)[0]

    def chat_cost(self, chat_id):
        """Recorded amount, retained hold, and whether any cost is unresolved."""
        with _LOCK:
            totals, reservations = self._replay()
            pending = [entry for entry in reservations.values() if entry.chat_id == chat_id]
            return (totals.chat_all_time.get(chat_id, 0.0),
                    sum(max(0, entry.amount-entry.settled) for entry in pending),
                    bool(pending) or bool(totals.problem))

    def can_spend(self) -> Optional[str]:
        """Why an API message can't start now, or None."""
        with _LOCK:
            totals = self._replay()[0]
            return self._refusal(totals)

    def _refusal(self, totals: Totals) -> Optional[str]:
        if totals.problem:
            return totals.problem
        if totals.left < MIN_RESERVATION:
            from .billing import money
            return (f"The Chat Place has reached your limit of {money(totals.limits.overall)} "
                    "for API credits, so it didn't send this message. Nothing was sent. Raise "
                    "the limit or reset the total in Tools, API Keys.")
        return None

    # -- writing ----------------------------------------------------------------

    def _append(self, record: dict) -> None:
        """One line, written whole and flushed to disk. Call with the lock.
        A half-written last line (a crash mid-write) is ended first, so this
        record, a reset above all, is a line of its own rather than part of
        the damaged one, where Reset could never clear the damage."""
        record = {"v": 1, "at": self._clock(), **record}
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    line = b"\n" + line
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def reserve(self, chat_id: str = "", purpose: str = "turn") -> Reservation:
        """Hold the most the next API process may spend, or raise SpendRefused.
        Reading the total and writing the reservation are one step under the
        lock, so another chat can't be given the same money."""
        with _LOCK:
            totals = self._replay()[0]
            refusal = self._refusal(totals)
            if refusal:
                raise SpendRefused(refusal)
            # Down to the cent, as --max-budget-usd is given (billing.budget_text).
            amount = cents_down(min(totals.limits.per_message, totals.left))
            if amount < MIN_RESERVATION:
                raise SpendRefused("The per-message limit for API credits is less than a cent, "
                                   "so this message wasn't sent. Nothing was sent. Raise it in "
                                   "Tools, API Keys.")
            reservation = Reservation(str(uuid.uuid4()), amount, chat_id or "",
                                      purpose if purpose in PURPOSES else "turn")
            try:
                self._append({"type": "reserve", "id": reservation.id, "amount": amount,
                              "chat": reservation.chat_id, "purpose": reservation.purpose,
                              "run": self.run_id})
            except OSError as exc:
                raise SpendRefused(f"The Chat Place couldn't record API spending ({exc}), so "
                                   "it didn't send this message. Nothing was sent.") from None
            self._live.add(reservation.id)
            return reservation

    def settle(self, reservation: Reservation, cost: float,
               models: Optional[Dict[str, float]] = None) -> bool:
        """Record what one result cost (already the difference, CostTracker).
        False if it couldn't be written (or wasn't a cost): the reservation
        is then never closed complete (``close``), so it stays counted in
        full, less only what was recorded (#663)."""
        cost = _money(cost)
        if cost is None:
            with _LOCK:
                self._unrecorded.add(reservation.id)
            return False
        if cost == 0 and not models:
            return True
        clean = {m: round(c, 6) for m, c in (models or {}).items()
                 if isinstance(m, str) and _money(c) is not None}
        with _LOCK:
            try:
                self._append({"type": "settle", "id": reservation.id, "cost": round(cost, 6),
                              "chat": reservation.chat_id, "models": clean})
                return True
            except OSError:
                self._unrecorded.add(reservation.id)
                return False

    def correct_openai_overlap(self, reservation, credit, model):
        """Remove usage already charged to a later OpenAI turn, after the
        matching earlier turn was charged. Only this API ledger uses it."""
        credit = _money(credit)
        if credit is None or not reservation.chat_id.startswith('codex-api:'):
            return False
        with _LOCK:
            try:
                self._append({'type': 'correction', 'id': reservation.id,
                              'chat': reservation.chat_id, 'cost': -round(credit, 6),
                              'models': {model: -round(credit, 6)}})
                return True
            except OSError:
                self._unrecorded.add(reservation.id)
                return False

    def close(self, reservation: Reservation, complete: bool) -> None:
        """The process is over. ``complete``: its whole cost was settled (or
        nothing was ever sent), so the rest of the reservation is freed;
        never if a settlement for it couldn't be written (#663)."""
        with _LOCK:
            complete = complete is True and reservation.id not in self._unrecorded
            try:
                self._append({"type": "close", "id": reservation.id, "complete": complete})
            except OSError:
                pass  # left open: still counted
            self._live.discard(reservation.id)
            self._unrecorded.discard(reservation.id)

    def reset(self) -> None:
        """Start a new total (Tools, API Keys, Reset). Raises OSError if it
        can't be recorded."""
        with _LOCK:
            self._append({"type": "reset", "keep": sorted(self._live)})

    def release_held(self) -> float:
        """Let go of every reservation no turn running in this app run holds
        (Tools, API Keys, Release Held), leaving what was spent as it is.
        Returns the amount let go (0 if there was none). Raises OSError if
        it can't be recorded."""
        with _LOCK:
            reservations = self._replay()[1]
            ids = sorted(rid for rid, entry in reservations.items()
                         if rid not in self._live and entry.amount - entry.settled > 0)
            if not ids:
                return 0.0
            amount = sum(reservations[rid].amount - reservations[rid].settled for rid in ids)
            self._append({"type": "release", "ids": ids})
            return amount


_DEFAULT: Optional[SpendLedger] = None
_OPENAI_DEFAULT: Optional[SpendLedger] = None


def default_ledger(provider='claude') -> SpendLedger:
    """The ledger in the app's folder, one for the whole process."""
    global _DEFAULT, _OPENAI_DEFAULT
    with _LOCK:
        if provider == 'openai':
            if _OPENAI_DEFAULT is None:
                _OPENAI_DEFAULT = SpendLedger(platform_paths.app_data_dir() / 'openai-billing',
                                               defaults=Limits())
            return _OPENAI_DEFAULT
        if provider != 'claude':
            raise ValueError('Unknown API-credit provider')
        if _DEFAULT is None:
            _DEFAULT = SpendLedger()
        return _DEFAULT
