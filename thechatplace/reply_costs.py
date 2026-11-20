"""What chats on API credits cost, where you read them (#692).

Two things, both plain data with no wx:

- Each reply's cost, as the last line of the reply ("Cost: 19 cents"), in
  the messages list, the full message view and the formatted page. The
  words are kept with the chat in The Chat Place's own store
  (``OwnSession.reply_costs``), keyed by the reply they belong to, and never
  in Claude Code's or Codex's own files. A Claude reply is keyed by its API
  message id (what a reply merged from several records is keyed by in the
  transcript), or its record uuid when it has none; a Codex reply by its
  turn, ``turn:<id>``, which is put on the turn's last reply.
- The Session list's Cost column: what each chat on API credits has spent
  in all, in the words Properties uses ("$1.42", "an estimated 3 cents").
  It's worked out off the UI thread with each snapshot, and the ledger is
  replayed again only when its file changes.
"""
from __future__ import annotations

import dataclasses
import threading
from typing import Dict, Iterable, List, Optional

from .billing import COST_NOT_KNOWN, cost_words, money

#: How many replies' costs a chat keeps (#692). The oldest go first, so the
#: store can't grow for good; a chat that long has its earliest replies
#: without their cost line.
MAX_REPLY_COSTS = 500
#: The longest key and words kept: anything longer is a hand edit.
MAX_KEY = 200
MAX_WORDS = 200
#: A Codex reply's key: the turn it ended.
TURN_PREFIX = "turn:"
#: How every line starts.
LINE_START = "Cost: "


def estimated(words: str) -> str:
    """Dollars said as an estimate: "an estimated 3 cents", "at least an
    estimated 3 cents", "less than a cent, estimated"."""
    if words.startswith("at least "):
        return "at least " + estimated(words[len("at least "):])
    if words == "less than a cent":
        return "less than a cent, estimated"
    return "an estimated " + words


def cost_line(words: str) -> str:
    """A reply's last line from the words its cost was said in (#692):
    "19 cents" is "Cost: 19 cents", "estimated 3 cents" (Codex) is "Cost: an
    estimated 3 cents", and "used 12,400 tokens, cost unknown" (a model with
    no known price) is "Cost: unknown, 12,400 tokens", never a dollar figure.
    "" for no words."""
    words = (words or "").strip().rstrip(".").strip()
    if not words:
        return ""
    words = words.removesuffix("; allowance kept")
    note = ""
    if words.startswith("price expired during the reply; "):
        words = words[len("price expired during the reply; "):]
        note = " (its price expired during the reply)"
    if words.endswith(", cost unknown") or words == "tokens not reported, cost unknown":
        tokens = words[:-len(", cost unknown")]
        if tokens.startswith("used at least "):
            tokens = "at least " + tokens[len("used at least "):].split(";")[0].strip()
        elif tokens.startswith("used "):
            tokens = tokens[len("used "):]
        body = "unknown, " + tokens
    elif words == COST_NOT_KNOWN:
        body = "not known"
    elif words == "nothing was charged":
        body = "nothing"
    elif words.startswith("estimated, at most "):
        body = "at most " + estimated(words[len("estimated, at most "):])
    elif words.startswith("estimated "):
        body = estimated(words[len("estimated "):])
    else:
        body = words
    return LINE_START + body + note


def claude_reply_key(data: dict) -> str:
    """The key a Claude turn's cost is kept under: its last reply's API
    message id, or record uuid ("" when the turn had no reply)."""
    for name in ("reply_message_id", "reply_uuid"):
        value = data.get(name)
        if isinstance(value, str) and value and len(value) <= MAX_KEY:
            return value
    return ""


def claude_words(data: dict, already: Optional[float] = None) -> str:
    """What a Claude turn on API credits cost, as ``cost_words`` says it,
    whatever Settings says about speaking it; "" for the subscription or a
    turn that sent nothing. ``already``: what was put on an earlier reply
    of the same turn (an answer with background work left), so this says
    only what came after."""
    if data.get("billing") != "api" or not data.get("sent", True):
        return ""
    if data.get("estimated"):
        return str(data.get("cost_words") or COST_NOT_KNOWN)
    cost = data.get("cost_usd")
    complete = data.get("cost_complete", True) is not False
    if already is not None and isinstance(cost, (int, float)) and not isinstance(cost, bool):
        cost = max(0.0, cost - already)
    return cost_words(cost, complete)


def codex_words(data: dict) -> str:
    """What a Codex turn on API credits cost, as it was said (its
    ``cost_words``); "" when nothing was sent."""
    if data.get("billing") != "api" or not data.get("sent"):
        return ""
    return str(data.get("cost_words") or COST_NOT_KNOWN)


def remember(costs: Dict[str, str], key: str, line: str) -> Dict[str, str]:
    """``costs`` with ``line`` kept for ``key``, newest last, and only the
    newest ``MAX_REPLY_COSTS``. A new dict; ``costs`` is left as it was."""
    kept = dict(costs or {})
    if not key or not line or len(key) > MAX_KEY or len(line) > MAX_WORDS:
        return kept
    kept.pop(key, None)
    kept[key] = line
    while len(kept) > MAX_REPLY_COSTS:
        kept.pop(next(iter(kept)))
    return kept


def clean(raw) -> Dict[str, str]:
    """Stored reply costs made safe: string keys and lines of a sane length,
    each line one of ours, at most the newest ``MAX_REPLY_COSTS``."""
    if not isinstance(raw, dict):
        return {}
    kept = {key: value for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str) and key and len(key) <= MAX_KEY
            and value.startswith(LINE_START) and len(value) <= MAX_WORDS
            and "\n" not in value and "\x00" not in key}
    while len(kept) > MAX_REPLY_COSTS:
        kept.pop(next(iter(kept)))
    return kept


def with_costs(messages: Iterable, costs: Dict[str, str], assistant_kind: str = "assistant") -> List:
    """The messages, each reply with a kept cost given it as its ``cost``
    (#692). Copies of those it changes; the rest as they were. A Claude
    reply is matched by its key (the message id) or its uuid; a Codex
    turn's cost goes on that turn's last reply (keys "<turn>:<item>")."""
    messages = list(messages)
    if not costs:
        return messages
    turns = {key[len(TURN_PREFIX):]: line for key, line in costs.items()
             if key.startswith(TURN_PREFIX)}
    last_in_turn: Dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.kind != assistant_kind:
            continue
        key = message.key or ""
        if turns and ":" in key:
            last_in_turn[key.split(":", 1)[0]] = index
    chosen: Dict[int, str] = {}
    for index, message in enumerate(messages):
        if message.kind != assistant_kind:
            continue
        line = costs.get(message.key) if message.key else None
        if line is None and getattr(message, "uuid", ""):
            line = costs.get(message.uuid)
        if line:
            chosen[index] = line
    for turn, index in last_in_turn.items():
        if turn in turns:
            chosen[index] = turns[turn]
    for index, line in chosen.items():
        messages[index] = dataclasses.replace(messages[index], cost=line)
    return messages


# -- the Session list's Cost column ---------------------------------------------------


def claude_column_words(totals, chat_id: str) -> str:
    """A Claude chat's Cost column: what Properties says it has spent
    ("$1.42", "6 cents"), or "" for nothing (#692)."""
    spent = totals.chat_all_time.get(chat_id, 0.0)
    return money(spent) if spent and spent >= 0.00005 else ""


def codex_column_words(totals, chat_id: str) -> str:
    """A Codex chat's Cost column, estimated as Properties says it (#685):
    "an estimated 3 cents", "cost not known", and for a model with no known
    price "cost unknown, 12,400 tokens", never "$0.00". "" for nothing."""
    spent = totals.chat_all_time.get(chat_id, 0.0)
    if chat_id in totals.unpriced_all_time:
        tokens = totals.unpriced_all_time[chat_id]
        count = tokens.get("inputTokens", 0) + tokens.get("outputTokens", 0)
        if chat_id in totals.unpriced_missing_all_time:
            said = f"at least {count:,} tokens" if count else "tokens not reported"
        else:
            said = f"{count:,} tokens"
        if spent >= 0.005:
            return f"{estimated(money(spent))}, plus {said} at an unknown price"
        return "cost unknown, " + said
    unknown = bool(totals.problem) or chat_id in totals.priced_unknown_chats
    if unknown:
        words = cost_words(spent if spent else None, complete=False)
        return words if words == COST_NOT_KNOWN else estimated(words)
    return estimated(money(spent)) if spent and spent >= 0.00005 else ""


class ColumnCosts:
    """The Cost column's words for each chat on API credits, from the two
    ledgers, each replayed only when its files changed since the last time."""

    def __init__(self, ledger_for=None) -> None:
        self._ledger_for = ledger_for
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _ledger(self, which: str):
        if self._ledger_for is not None:
            return self._ledger_for(which)
        from .spend_ledger import default_ledger
        return default_ledger(which)

    def totals(self, which: str):
        """``which`` ledger's totals ('claude' or 'openai'), or None if it
        can't be read."""
        ledger = self._ledger(which)
        stamp = [str(ledger.path)]
        for path in (ledger.path, ledger.limits_path):
            try:
                info = path.stat()
                stamp.append((info.st_mtime_ns, info.st_size))
            except OSError:
                stamp.append(None)
        with self._lock:
            cached = self._cache.get(which)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        try:
            totals = ledger.totals()
        except Exception:  # noqa: BLE001 - the list never fails over it
            return None
        with self._lock:
            self._cache[which] = (stamp, totals)
        return totals

    def words(self, info) -> str:
        """The Cost column for one session ("" for one not on API credits)."""
        if not getattr(info, "api_credits", False):
            return ""
        codex = info.provider == "codex-api"
        totals = self.totals("openai" if codex else "claude")
        if totals is None:
            return ""
        if codex:
            return codex_column_words(totals, info.key)
        return claude_column_words(totals, info.cli_session_id)


COLUMN_COSTS = ColumnCosts()


def fill_api_costs(sessions: Iterable, costs: Optional[ColumnCosts] = None) -> None:
    """Each chat on API credits' ``api_cost``, for its Cost column (#692)."""
    costs = costs if costs is not None else COLUMN_COSTS
    for info in sessions:
        if getattr(info, "api_credits", False):
            try:
                info.api_cost = costs.words(info)
            except Exception:  # noqa: BLE001 - said nothing rather than wrong
                info.api_cost = ""
