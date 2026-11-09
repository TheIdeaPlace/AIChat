"""What an approval notice says, for Lists' Approvals tab and the chat (#564).

A Codex chat run here gets two kinds of notice: Codex's automatic review
deciding a request ("Codex automatic review allowed the request." with the
command and the reviewer's reason on the lines after), and a rule you saved
with Allow for This Chat answering for you ("Allowed git status, as you said
for this chat."). The notices are plain text, from ``codex_events`` and
``codex_runtime``, and are kept that way in the review history, so this reads
them back rather than the history keeping a second format.

Routine notices, the allowed ones, stay out of the chat unless Show Tool
Activity is on, and aren't spoken: in a long turn there can be dozens. A
denial, a timeout or a stopped review changed what Codex could do, so it's
always shown and said once (#564).
"""
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

ALLOWED = "allowed"
DENIED = "denied"
TIMED_OUT = "timed out"
STOPPED = "stopped"

BY_REVIEW = "automatic review"
BY_RULE = "saved rule"

#: How a saved-rule notice is named in the review history: Codex names it by
#: its request id, a number, which the history (all strings) can't hold.
RULE_PREFIX = "saved-rule:"
REVIEW_PREFIX = "auto-review:"

_REVIEW_OUTCOMES = (("allowed the request", ALLOWED), ("denied the request", DENIED),
                    ("timed out", TIMED_OUT), ("stopped", STOPPED))
_RULE_START, _RULE_END = "Allowed ", ", as you said for this chat."


@dataclass(frozen=True)
class Approval:
    outcome: str
    by: str
    command: str
    reason: str
    text: str

    @property
    def routine(self) -> bool:
        return self.outcome == ALLOWED

    def row(self) -> str:
        """"Allowed: git status. Automatic review." for the list."""
        what = " ".join(self.command.split())  # one line, even for a heredoc
        what = what if len(what) <= 120 else what[:119] + "…"
        # Said as what you did, in the button's words (#596).
        who = "Automatic review" if self.by == BY_REVIEW else "You allowed it for this chat"
        head = self.outcome.capitalize()
        return f"{head}: {what}. {who}." if what else f"{head}, a request. {who}."


def history_identity(identity) -> str:
    """The identity a notice is kept under in the review history."""
    identity = str(identity)
    return identity if identity.startswith(REVIEW_PREFIX) else RULE_PREFIX + identity


def parse(identity, text: str) -> Approval:
    """What one notice says. Text this doesn't recognise is kept whole, as an
    automatic review's, and counted as not routine, so it's never hidden."""
    identity = str(identity)
    first, _, rest = text.partition("\n")
    if first.startswith(_RULE_START) and first.endswith(_RULE_END):
        command = first[len(_RULE_START):-len(_RULE_END)]
        return Approval(ALLOWED, BY_RULE, command, "", text)
    outcome = next((o for phrase, o in _REVIEW_OUTCOMES if phrase in first), "")
    # The command can span lines (a heredoc, a commit message's body), so
    # it runs to the last "Reason: " line, which codex_events puts last.
    body = "\n" + rest
    reason_at = body.rfind("\nReason: ")
    reason = body[reason_at + len("\nReason: "):] if reason_at >= 0 else ""
    head = body[:reason_at] if reason_at >= 0 else body
    command = head[len("\nCommand: "):] if head.startswith("\nCommand: ") else ""
    # A command codex_events cut short ends in its display note: Copy
    # copies only the command (the detail still has the note).
    from .providers.codex_events import DISPLAY_NOTE
    if command.endswith(DISPLAY_NOTE):
        command = command[:-len(DISPLAY_NOTE)]
    return Approval(outcome or "noted", BY_REVIEW, command, reason, text)


def is_routine(identity, text: str) -> bool:
    return parse(identity, text).routine


def merge(saved: Iterable[Sequence], live: Iterable[Sequence]) -> List[Tuple[str, str, str]]:
    """The review history read from disk, then the notices of this run it
    doesn't have yet (a saved-rule notice is kept under ``saved-rule:``),
    oldest first, each once."""
    rows: List[Tuple[str, str, str]] = []
    seen = set()
    for turn, identity, text in list(saved) + [(t, history_identity(i), x) for t, i, x in live]:
        key = (str(turn), str(identity))
        if key not in seen:
            seen.add(key)
            rows.append((str(turn), str(identity), text))
    return rows


def count_text(notices: Sequence[Sequence]) -> str:
    """"12 requests allowed automatically. 1 denied." for a turn's status:
    counts only, never the commands, so it stays short as it changes."""
    parsed = [parse(identity, text) for _turn, identity, text in notices]
    allowed = sum(1 for a in parsed if a.routine)
    other = len(parsed) - allowed
    parts = []
    if allowed:
        parts.append(f"{allowed} {'request' if allowed == 1 else 'requests'} allowed automatically.")
    if other:
        parts.append(f"{other} not allowed." if allowed else
                     f"{other} {'request' if other == 1 else 'requests'} not allowed.")
    return " ".join(parts)
