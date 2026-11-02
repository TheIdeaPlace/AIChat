"""TCP's own decisions about permissions. Everything an agent sends is data.

ACP's ``kind`` on a permission option is, by its own specification, a UI hint,
and the agent owns what a remembered choice means. So: only one-time choices
are ever offered, a denial must exist or the request is refused, an unverified
agent's Allow carries a caution, and no agent wording reaches a button.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple

from .contracts import Decision

ALLOW_ONCE_KIND, REJECT_ONCE_KIND = "allow_once", "reject_once"

UNVERIFIED_CAUTION = ("The Chat Place hasn't checked how this agent carries out a permission choice, "
                      "so it can only trust the agent to do what it says.")


@dataclass(frozen=True)
class AcpPermission:
    """The one-time choices found in a request, with the agent's option ids."""
    decisions: Tuple[Decision, ...]
    option_ids: Mapping[Decision, str]


def read_acp_options(options: object) -> Optional[AcpPermission]:
    """The choices this app can give, or None to refuse the request: without a
    one-time denial there's no safe answer, so nothing is shown."""
    if not isinstance(options, list):
        return None
    ids: Dict[Decision, str] = {}
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id, kind = option.get("optionId"), option.get("kind")
        if not isinstance(option_id, str) or not option_id:
            continue
        if kind == ALLOW_ONCE_KIND:
            ids.setdefault(Decision.ALLOW_ONCE, option_id)
        elif kind == REJECT_ONCE_KIND:
            ids.setdefault(Decision.DENY, option_id)
    if Decision.DENY not in ids:
        return None
    order = tuple(d for d in (Decision.ALLOW_ONCE, Decision.DENY) if d in ids)
    return AcpPermission(order, ids)


def checked_decision(decision: object, offered: Iterable[Decision]) -> Decision:
    """Anything but a choice that was offered counts as Deny."""
    return decision if isinstance(decision, Decision) and decision in tuple(offered) else Decision.DENY


def mode_problem(mode: str, safe_modes: Mapping[str, object]) -> Optional[str]:
    """Why a mode can't be run in. With safe modes listed, anything else counts,
    including reporting none. An agent with none listed isn't judged here: its
    modes can't be understood, so they aren't offered either."""
    if safe_modes and mode not in safe_modes:
        return "The agent switched to a mode where it may stop asking before it acts, so this turn was stopped."
    return None


def option_problem(values: Mapping[str, str], forbidden: Mapping[str, Iterable[str]]) -> Optional[str]:
    for option, bad in forbidden.items():
        if values.get(option) in tuple(bad):
            return "The agent stopped asking before it acts, so this turn was stopped."
    return None
