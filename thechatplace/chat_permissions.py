"""What a chat may do without asking, in words, for File, Chat Permissions (#307, #329).

No wx here, so the rows, notes and confirmations can be tested on their own.
Claude's kept rules are the ``--allowedTools`` rules stored with the chat;
Codex's are The Chat Place's own kept command rules (never Codex's own saved
or session rules, which stay off).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Iterable, List, Mapping, Optional, Tuple

from .properties import _date

#: What the list says when nothing is kept (#307).
EMPTY = "Nothing is allowed beyond the permission mode."

CLAUDE_TAKES_EFFECT = "Removing a rule applies from this chat's next turn."
CLAUDE_RUNNING = ("A turn is running now. It keeps the rules it started with; "
                  "a removal applies from the next turn.")
CODEX_TAKES_EFFECT = ("Removing a rule applies at once, to Codex's next request, even during "
                      "a running turn. A command already allowed can't be called back.")
CODEX_ABOUT = ("Each rule allows one Git command, such as git commit, in this chat's folder, "
               "with only simple options The Chat Place has checked. Anything else still asks.")
CODEX_NATIVE = ("These are Codex's own rule files, outside The Chat Place. They are "
                "changed in Codex, not here.")


@dataclass(frozen=True)
class PermissionRow:
    """One kept rule: its identity for removal, and what the list reads."""
    rule_id: str
    description: str
    syntax: str
    allowed_at_ms: int = 0
    access: Tuple[str, ...] = ()

    def text(self) -> str:
        """The whole row, read in full: what it allows, the rule itself,
        when it was allowed, and any extra access it carries."""
        parts = [self.description]
        if self.syntax:
            parts.append(self.syntax + ".")
        parts.append(allowed_when(self.allowed_at_ms))
        parts.extend(a if a.endswith(".") else a + "." for a in self.access)
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


def codex_rows(rules) -> List[PermissionRow]:
    """Rows for ``CodexChatRule``s. The scope is opaque and never shown."""
    rows = []
    for rule in rules:
        rows.append(PermissionRow(rule.rule_id, rule.description,
                                  "Prefix: " + " ".join(rule.prefix),
                                  rule.allowed_at_ms, tuple(rule.access)))
    return rows


def confirm_text(row: Optional[PermissionRow], count: int, provider: str) -> str:
    """The one confirmation before a removal, naming what goes."""
    name = "Codex" if provider == "codex" else "Claude"
    when = ("from its next request" if provider == "codex" else "from the next turn")
    if row is None:
        rules = "this rule" if count == 1 else f"all {count} rules"
        return f"Remove {rules} from this chat? {name} will ask again {when}."
    return f"Remove this rule? {row.description} {name} will ask again {when}."


def native_rule_files(count=None, error: str = "", *, pending: bool = False) -> str:
    """The Codex rule-file count, as Properties says it (#257), read-only."""
    if error:
        return "Saved Codex rule files: couldn't be checked. " + error
    if pending:
        return "Saved Codex rule files in effect: checking..."
    if type(count) is int and count >= 0:
        return f"Saved Codex rule files in effect: {count}. " + CODEX_NATIVE
    return "Saved Codex rule files: not checked."


def codex_chat_rule_name(rule) -> str:
    """The full name of Answer's Allow for This Chat button for a Codex rule."""
    words = " ".join(rule.prefix)
    return (f"Allow {words} for the rest of this chat, with simple options The Chat Place "
            f"has checked; anything else still asks")


def codex_chat_rule_note(rule) -> str:
    """What Answer says when Allow for This Chat is offered for Codex."""
    words = " ".join(rule.prefix)
    return (f"Allow for This Chat keeps a rule in The Chat Place: later {words} commands in "
            f"this chat's folder that use only simple options it has checked are allowed "
            f"without asking, and you hear when that happens. Anything else still asks. "
            f"File, Chat Permissions lists and removes the rule. Codex's own session and "
            f"saved rules stay off.")


def notice_key(native_id: str, notice) -> tuple:
    """Chat ID + turn ID + request ID: one notice, however often a snapshot
    repeats it. IDs may be strings or integers, so keep their types."""
    turn_id, request_id = notice[0], notice[1]
    return (native_id, turn_id, type(request_id).__name__, request_id)
