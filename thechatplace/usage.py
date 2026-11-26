"""How full a session's context is, and how much of the plan's usage is
left (#19), as words.

Context comes from the token counts in Claude's latest reply (transcript, or
the turn's own result): input, cached input and output, the same sum Claude
Code records as a compaction's ``preTokens``. The window is what Claude Code
reported for that model in a turn of ours; or 1,000,000 once a session has
gone past 200,000 (a session can't use more than it has). Otherwise it isn't
known, and then no percentage is given and nothing is warned about: today's
models have both 200,000 and 1,000,000 windows, and guessing wrong would say
"80% full" of a session that's 16% full.

Usage limits come from the ``rate_limit_event`` a turn reports, e.g.
``{"status": "allowed", "rateLimitType": "five_hour", "resetsAt": 1791338400,
"unifiedWindows": {"five_hour": {"utilization": 0.08, "resetsAt": ...},
"seven_day": {"utilization": 0.34, "resetsAt": ...}}}``. Seen with Claude Code
2.1.286; read defensively.
"""
from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

STANDARD_WINDOW = 200_000
LARGE_WINDOW = 1_000_000
#: Said once per session when the context passes this share.
CONTEXT_WARNING = 0.8
#: Said once per limit window when usage passes this share.
LIMIT_WARNING = 0.9

_WINDOW_NAMES = {"five_hour": "5-hour limit", "seven_day": "weekly limit",
                 "seven_day_opus": "weekly Opus limit", "seven_day_sonnet": "weekly Sonnet limit",
                 # Claude Code's own names for these (2.1.331): Fable's
                 # weekly allowance, reported only by Fable turns (#722),
                 # and usage credits.
                 "seven_day_overage_included": "weekly Fable limit",
                 "overage": "usage credit limit"}
_LIMIT_REACHED = re.compile(r"usage limit reached\|(\d{9,})", re.IGNORECASE)


def context_window(model: str = "", tokens: int = 0, reported: int = 0) -> int:
    """The window in tokens, or 0 when it isn't known."""
    if reported and reported >= tokens:
        return reported
    if "[1m]" in (model or "") or tokens > STANDARD_WINDOW:
        return LARGE_WINDOW
    return 0


def context_share(tokens: int, window: int) -> float:
    return min(tokens / window, 1.0) if window and tokens else 0.0


def context_text(tokens: int, window: int) -> str:
    """"Context 62% full: 124,000 of 200,000 tokens." """
    if not tokens:
        return "Context: not known until Claude's next reply."
    if not window:
        return (f"Context: {tokens:,} tokens used; the window's size isn't known until this "
                "session or another on the same model runs a turn here.")
    share = context_share(tokens, window)
    return f"Context {round(share * 100)}% full: {tokens:,} of {window:,} tokens."


def when(epoch: float, now: Optional[float] = None) -> str:
    """"at 7:00 AM" today, "tomorrow at 7:00 AM", "on Friday at 3:00 PM"."""
    now = now if now is not None else time.time()
    if epoch <= now:
        return "now"
    moment = time.localtime(epoch)
    today = time.localtime(now)
    # By hand: %p is empty under some locales.
    clock = f"{(moment.tm_hour % 12) or 12}:{moment.tm_min:02d} {'AM' if moment.tm_hour < 12 else 'PM'}"
    days = (time.mktime((moment.tm_year, moment.tm_mon, moment.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    if round(days) <= 0:
        return f"at {clock}"
    if round(days) == 1:
        return f"tomorrow at {clock}"
    if days < 7:
        return f"on {time.strftime('%A', moment)} at {clock}"
    return f"on {time.strftime('%d %B', moment).lstrip('0')} at {clock}"


def _windows(info: dict):
    """(name, utilization 0-1, resets epoch or 0) for each window reported."""
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    found = []
    if isinstance(windows, dict):
        for key, window in windows.items():
            if isinstance(window, dict) and isinstance(window.get("utilization"), (int, float)):
                found.append((_WINDOW_NAMES.get(key, key.replace("_", " ") + " limit"),
                              float(window["utilization"]), float(window.get("resetsAt") or 0)))
    return found


#: The window only Fable turns report (#722): Fable's weekly allowance.
FABLE_WINDOW = "seven_day_overage_included"


def with_fable_window(info: Optional[dict], fable_info: Optional[dict]) -> Optional[dict]:
    """``info`` with the weekly Fable limit from the latest Fable turn's
    report (``fable_info``) when ``info`` hasn't one: other models' turns
    don't report it, and the latest report would otherwise drop it."""
    fable = ((fable_info or {}).get("unifiedWindows") or {}).get(FABLE_WINDOW) \
        if isinstance(fable_info, dict) else None
    if not isinstance(fable, dict) or not isinstance(info, dict):
        return info
    windows = info.get("unifiedWindows")
    windows = dict(windows) if isinstance(windows, dict) else {}
    if FABLE_WINDOW in windows:
        return info
    windows[FABLE_WINDOW] = fable
    return {**info, "unifiedWindows": windows}


def limit_lines(info: Optional[dict], now: Optional[float] = None) -> List[str]:
    """Each usage limit as its own line (#130): "5-hour limit 8% used,
    resets at 7:00 AM.", "Weekly limit 34% used, resets on Friday at 3:00
    PM.", and a limit reached or extra usage as lines of their own. Never
    empty: when nothing is known, that is the line."""
    if not isinstance(info, dict) or not info:
        return ["Usage limits: not known until a Chat Place session runs a turn."]
    lines = []
    for name, used, resets in _windows(info):
        line = f"{name[0].upper()}{name[1:]} {round(used * 100)}% used"
        if resets:
            line += f", resets {when(resets, now)}"
        lines.append(line + ".")
    if info.get("status") not in (None, "allowed", "allowed_warning"):
        reset = info.get("resetsAt")
        lines.insert(0, "You've reached a usage limit" + (
            f"; it resets {when(float(reset), now)}." if reset else "."))
    if info.get("isUsingOverage"):
        lines.append("Turns are now charged to usage credits.")
    return lines or ["Usage limits: Claude Code didn't say."]


def limits_text(info: Optional[dict], now: Optional[float] = None) -> str:
    """"5-hour limit 8% used, resets at 7:00 AM. Weekly limit 34% used,
    resets on Friday at 3:00 PM." """
    return " ".join(limit_lines(info, now))


def usage_lines(context: Optional[Tuple[str, int, int]], info: Optional[dict],
                now: Optional[float] = None) -> List[str]:
    """What Tools, Usage and Context (Ctrl+Shift+U) lists, one line each
    (#130): the loaded session's context, then each usage limit. ``context``
    is the loaded session's (title, tokens, window), or None when no session
    is loaded. Shown rather than spoken, so a person
    who can't follow the system voice can read it by arrowing."""
    if context is None:
        first = "Context: no session is loaded. Load one to see how full its context is."
    else:
        title, tokens, window = context
        first = f"{title}: {context_text(tokens, window)}"
    return [first] + limit_lines(info, now)


#: Said in Usage when the loaded chat is on API credits (#658).
PLAN_LIMITS_NOT_FOR_API = ("This chat is on API credits, so your plan's usage limits don't "
                           "apply to it.")


def api_credit_summary(totals) -> str:
    """Usage's one line about API credits for a chat on the subscription:
    most people never use them, so the whole section (``api_credit_lines``)
    is kept for chats on API credits, and the plan's limits stay last."""
    from .billing import money
    if totals.problem:
        return f"API credits: {totals.problem}"
    spent = money(totals.spent) if totals.spent >= 0.00005 else "nothing"
    return (f"API credits: {spent} spent since the total was reset, {money(totals.left)} left "
            f"of your {money(totals.limits.overall)} limit. For today, the last 7 days and "
            "spending by model and by chat, open Usage and Context from a chat that uses API credits.")


def api_credit_lines(totals, chat_names: Optional[dict] = None) -> List[str]:
    """Usage's API credits section (#658), one line each: today, the last 7
    days, since the total was reset, what's held for running messages, what's
    left of the limit, then spending by model and by chat (chat names, never
    ids; a chat no longer in the list is "a chat that's gone"). ``totals`` is
    a spend_ledger.Totals."""
    from .billing import money as said

    def money(amount):
        # Nothing spent is "nothing", not "less than a cent".
        return "nothing" if not amount or amount < 0.00005 else said(amount)
    names = chat_names or {}
    lines = []
    if totals.problem:
        lines.append(f"API credits: {totals.problem}")
    lines += [
        f"API credits today: {money(totals.today)}",
        f"API credits in the last 7 days: {money(totals.last_7_days)}",
        f"API credits since the total was reset: {money(totals.spent)}",
        f"API credits held for running messages: {money(totals.held)}",
        f"API credits left of your {money(totals.limits.overall)} limit: {money(totals.left)}",
    ]
    for model, cost in sorted(totals.by_model.items(), key=lambda item: -item[1]):
        lines.append(f"API credits on {model}: {money(cost)}")
    gone = 0.0
    for chat, cost in sorted(totals.by_chat.items(), key=lambda item: -item[1]):
        if chat in names:
            lines.append(f"API credits in {names[chat]}: {money(cost)}")
        else:
            gone += cost
    if gone:
        lines.append(f"API credits in chats no longer listed: {money(gone)}")
    return lines


def limit_warning(info: Optional[dict], now: Optional[float] = None) -> Optional[str]:
    """Something worth saying unasked: a limit reached, or nearly."""
    if not isinstance(info, dict):
        return None
    if info.get("status") not in (None, "allowed", "allowed_warning"):
        return limits_text(info, now)
    for name, used, resets in _windows(info):
        if used >= LIMIT_WARNING:
            reset = f"; it resets {when(resets, now)}" if resets else ""
            return f"You've used {round(used * 100)}% of your {name}{reset}."
    return None


def limit_warning_key(info: Optional[dict]) -> Optional[str]:
    """Which warning ``limit_warning`` would give, as a key that stays the same
    while the percentage creeps up: said once per limit window."""
    if not isinstance(info, dict):
        return None
    if info.get("status") not in (None, "allowed", "allowed_warning"):
        return f"reached:{info.get('resetsAt')}"
    for name, used, resets in _windows(info):
        if used >= LIMIT_WARNING:
            return f"{name}:{int(resets)}"
    return None


def friendly_error(text: str, now: Optional[float] = None) -> str:
    """A turn's error in plain words where it's a usage limit
    ("Claude AI usage limit reached|1791338400")."""
    match = _LIMIT_REACHED.search(text or "")
    if match:
        return (f"You've reached your Claude usage limit. It resets "
                f"{when(int(match.group(1)), now)}.")
    return text
