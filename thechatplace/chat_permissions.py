"""What a chat may do without asking, in words, for File, Chat Permissions (#307, #329).

No wx here, so the rows, notes and confirmations can be tested on their own.
Claude's kept rules are the ``--allowedTools`` rules stored with the chat.
The Chat Place keeps no Codex approvals: no safe way to reuse one has been
found yet (#307, #330). Codex's own saved rule files are its own, and stay in
effect (#257); the dialog says how many there are.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Iterable, List, Mapping, Optional

from .properties import _date
from .ui_text import CODEX_SAVED_RULES_NOTE

#: What the list says when nothing is kept (#307).
EMPTY = "Nothing is allowed beyond the permission mode."

CLAUDE_TAKES_EFFECT = "Removing a rule applies from this chat's next turn."
CLAUDE_RUNNING = ("A turn is running now. It keeps the rules it started with; "
                  "a removal applies from the next turn.")
#: What a Codex chat's list says: The Chat Place keeps nothing for it.
CODEX_EMPTY = "The Chat Place keeps no Codex approvals."
CODEX_ABOUT = ("The Chat Place keeps no Codex approvals: when Codex asks you, your answer "
               "covers that one request. Codex's own saved rule files are separate, below.")
CODEX_NATIVE = ("They are Codex's own, and are changed in Codex, not here.")


@dataclass(frozen=True)
class PermissionRow:
    """One kept rule: its identity for removal, and what the list reads."""
    rule_id: str
    description: str
    syntax: str
    allowed_at_ms: int = 0

    def text(self) -> str:
        """The whole row, read in full: what it allows, the rule itself
        and when it was allowed."""
        parts = [self.description]
        if self.syntax:
            parts.append(self.syntax + ".")
        parts.append(allowed_when(self.allowed_at_ms))
        return " ".join(parts)


def allowed_when(allowed_at_ms: int) -> str:
    if not allowed_at_ms or allowed_at_ms <= 0:
        # Rules kept before #329 have no time; never invent one.
        return "Allowed: time not recorded."
    try:
        moment = datetime.fromtimestamp(allowed_at_ms / 1000)
    except (OverflowError, OSError, ValueError):
        return "Allowed: time not recorded."
    return f"Allowed {_date(moment)}."


_BASH_PREFIX = re.compile(r"(.*):\*")


def describe_claude_rule(rule: str) -> str:
    """A Claude Code permission rule in plain words: "Bash(git push:*)" is
    "Run git push (any arguments)."."""
    match = re.fullmatch(r"([^()]+)(?:\((.*)\))?", rule, flags=re.DOTALL)
    if match is None:
        return rule
    tool, content = match.group(1), match.group(2)
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        if len(parts) >= 3:
            return f"Use the {'__'.join(parts[2:])} tool from {parts[1]}."
        return f"Use any tool from {parts[1] if len(parts) > 1 else tool}."
    if tool == "Bash":
        if not content:
            return "Run any command."
        prefix = _BASH_PREFIX.fullmatch(content)
        if prefix:
            return f"Run {prefix.group(1)} (any arguments)."
        return f"Run {content}."
    verbs = {"Read": "Read", "Edit": "Edit", "Write": "Write", "MultiEdit": "Edit",
             "NotebookEdit": "Edit notebook"}
    if tool in verbs:
        return f"{verbs[tool]} {content}." if content else f"{verbs[tool]} any file."
    if tool == "WebFetch":
        if content and content.startswith("domain:"):
            return f"Fetch pages from {content[len('domain:'):]}."
        return f"Fetch {content}." if content else "Fetch any web page."
    if tool == "WebSearch":
        return "Search the web."
    return f"Use {tool} for {content}." if content else f"Use {tool}."


def claude_rows(rules: Iterable[str], allowed_at: Optional[Mapping[str, int]] = None
                ) -> List[PermissionRow]:
    allowed_at = allowed_at or {}
    return [PermissionRow(rule, describe_claude_rule(rule), rule, int(allowed_at.get(rule) or 0))
            for rule in rules]


def confirm_text(row: Optional[PermissionRow], count: int) -> str:
    """The one confirmation before a removal, naming what goes."""
    if row is None:
        rules = "this rule" if count == 1 else f"all {count} rules"
        return f"Remove {rules} from this chat? Claude will ask again from the next turn."
    return f"Remove this rule? {row.description} Claude will ask again from the next turn."


def native_rule_files(count=None, error: str = "", *, pending: bool = False) -> str:
    """The Codex rule-file count, as Properties says it (#257), read-only."""
    if error:
        return "Saved user/system rule files: couldn't be checked. " + error
    if pending:
        return "Saved user/system rule files: checking..."
    if type(count) is int and count > 0:
        # As Properties words it (#257), with what such rules mean.
        return (f"Saved user/system rule files in effect: {count}. "
                + CODEX_SAVED_RULES_NOTE + " " + CODEX_NATIVE)
    if count == 0:
        return "Saved user/system rule files in effect: 0."
    return "Saved user/system rule files: not checked."
