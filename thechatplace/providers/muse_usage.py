"""Usage and Context (Ctrl+Shift+U) for Muse Code chats (#701).

Worded as Codex's and Copilot's lines are: the chat's context first, then the
account's plan. Shapes are installed Muse Code 1.4.24's:

- the chat's view carries ``session/contextUsage`` events (``windowTokens``,
  ``usedTokens``, ``pressure``) and ``session/tokenUsage`` events (a turn's
  ``usage``: input, output, cached, reasoning tokens);
- ``usage/refresh`` answers ``tierName`` and ``subscriptionActive``, and a
  ``usage`` member (``window`` and ``weekly``, each ``usedPercent`` and
  ``resetsAtMs``; the window its ``windowDurationMins``) only once Muse Code
  has observed the plan's usage. Checked on 2026-11-25 it had none, so the
  percentages are read when they come and never required.
"""
from __future__ import annotations

import math
from typing import List, Optional

from ..usage import when

#: Said for a chat started in Muse Code itself: the app reads only its history,
#: which doesn't carry context. Copilot's read-only chats say the same.
CONTEXT_ONLY_HERE = "Context: known only for chats started in The Chat Place."
CONTEXT_NOT_YET = "Context: not known until Muse Code answers here."
PLAN_UNAVAILABLE = ("Muse Code plan usage isn't available. Check Muse Code sign-in "
                    "and try again.")

#: Muse Code's context pressure levels (1.4.24: below its soft threshold, at
#: or above it, at or above its hard one), in plain words.
PRESSURE = {"normal": "normal", "warning": "nearly full", "blocked": "full"}


def _count(value) -> Optional[int]:
    return value if type(value) is int and value >= 0 else None


def context_lines(context: Optional[dict], tokens: Optional[dict]) -> List[str]:
    """The chat's context, from its newest ``session/contextUsage`` event, and
    its last turn's tokens, from its newest ``session/tokenUsage`` event."""
    lines = []
    window = _count((context or {}).get("windowTokens"))
    used = _count((context or {}).get("usedTokens"))
    if window and used is not None:
        share = min(1.0, used / window)
        line = f"Context {round(share * 100)}% full: {used:,} of {window:,} tokens"
        pressure = (context or {}).get("pressure")
        if isinstance(pressure, str) and pressure in PRESSURE:
            line += f", {PRESSURE[pressure]}"
        lines.append(line + ".")
    else:
        lines.append(CONTEXT_NOT_YET)
    usage = (tokens or {}).get("usage")
    if isinstance(usage, dict):
        parts = []
        sent, cached = _count(usage.get("inputTokens")), _count(usage.get("cachedTokens"))
        if sent is not None:
            parts.append(f"{sent:,} input" + (f" ({cached:,} cached)" if cached else ""))
        for key, name in (("outputTokens", "output"), ("reasoningTokens", "reasoning")):
            value = _count(usage.get(key))
            if value is not None:
                parts.append(f"{value:,} {name}")
        if parts:
            lines.append("Last turn: " + ", ".join(parts) + " tokens.")
    return lines


def _percent(block) -> Optional[float]:
    value = block.get("usedPercent") if isinstance(block, dict) else None
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100:
        return float(value)
    return None


def _limit(name: str, block, now) -> Optional[str]:
    used = _percent(block)
    if used is None:
        return None
    text = f"{name}: {used:g}% used, {100 - used:g}% remaining"
    reset = block.get("resetsAtMs")
    if type(reset) is int and 0 < reset < 253402300800000:
        try:
            text += ", resets " + when(reset / 1000, now)
        except (ValueError, OverflowError, OSError):
            pass
    return text + "."


def plan_lines(result, *, now=None) -> List[str]:
    """The plan's lines from a ``usage/refresh`` (or ``usage/read``) answer."""
    if not isinstance(result, dict):
        return [PLAN_UNAVAILABLE]
    tier = result.get("tierName")
    active = result.get("subscriptionActive")
    if isinstance(tier, str) and tier.strip() and len(tier) <= 200:
        state = ", active" if active is True else ", not active" if active is False else ""
        lines = [f"Muse Code plan: {' '.join(tier.split())}{state}."]
    elif active is False:
        lines = ["Muse Code plan: not active."]
    else:
        lines = []
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    window = usage.get("window") if isinstance(usage.get("window"), dict) else None
    minutes = window.get("windowDurationMins") if window else None
    name = (f"{minutes // 60}-hour limit" if type(minutes) is int and minutes > 0 and minutes % 60 == 0
            else f"{minutes}-minute limit" if type(minutes) is int and minutes > 0 else "Usage limit")
    limits = [line for line in (_limit(name, window, now),
                                _limit("Weekly limit", usage.get("weekly"), now)) if line]
    return lines + (limits or ["Muse Code usage limits: not reported."])
