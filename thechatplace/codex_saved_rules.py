"""The commands Codex's own saved rule files allow, ask about or refuse, read
only, for File, Chat Permissions (#307).

When you tell Codex to always allow a command, it adds a line such as
``prefix_rule(pattern=["git", "commit"], decision="allow")`` to a ``.rules``
file in your Codex folder, and from then on that command runs without asking,
outside the sandbox, in every Codex chat. The Chat Place used to show only how
many such files there were, so a chat could commit or push without a prompt
and nothing here said why.

The files are Starlark (Codex's ``execpolicy``): ``prefix_rule(pattern, decision,
justification, match, not_match)`` and ``host_executable(name, paths)``. A
pattern is a list of words, and a word may be a list of alternatives;
``decision`` is allow (the default), prompt or forbidden. Their syntax is
Python's for these calls, so they are parsed with ``ast`` and never run.
Anything else in a file is counted, not guessed at.

No wx here, so the reading and the wording can be tested on their own.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

#: What each decision means, as the list words it, strictest last as Codex
#: ranks them (forbidden beats prompt beats allow).
KINDS = (("allow", "Allowed without asking"), ("prompt", "Always asks"),
         ("forbidden", "Never allowed"))

INTRO = ("From Codex's own saved rules. These apply to every Codex chat, here, in the Codex app "
         "or a terminal, and an allowed command runs outside this folder's limits, including "
         "the network. The Chat Place only reads them; change them in Codex, or edit the file.")
NONE = "Codex's own saved rules allow nothing extra."

#: A rules file bigger than this isn't read: Codex's own are a few kilobytes.
MAX_SIZE = 1024 * 1024
#: The list stops here, so a huge file can't bury the rest of the note.
MAX_RULES = 500


@dataclass(frozen=True)
class SavedRule:
    decision: str      # allow, prompt or forbidden
    command: str       # the pattern, one line
    path: str


def _word(value) -> Optional[str]:
    """One pattern word, or alternatives ("add or commit"), on one line."""
    if isinstance(value, str):
        # A word with spaces in it (a whole PowerShell script, say) is
        # quoted, and kept on one line so it can't pass for another rule.
        text = " ".join(value.split())
        return text if text and text == value and " " not in text else f'"{text}"'
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        words = [_word(v) for v in value]
        return None if None in words else "(" + " or ".join(words) + ")"
    return None


def _rule(call: ast.Call) -> Optional[Tuple[str, str]]:
    """A prefix_rule's decision and command, or None if it isn't plain."""
    if call.args:
        return None
    try:
        args = {k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg}
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return None
    if len(args) != len(call.keywords):
        return None
    pattern, decision = args.get("pattern"), args.get("decision", "allow")
    if decision not in dict(KINDS) or not isinstance(pattern, list) or not pattern:
        return None
    words = [_word(w) for w in pattern]
    if None in words:
        return None
    return decision, " ".join(words)


def _read(path: Path) -> Optional[Tuple[List[SavedRule], int]]:
    """A file's rules and how many entries couldn't be shown, or None when
    it can't be read or parsed."""
    try:
        if path.stat().st_size > MAX_SIZE:
            return None
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
    except (OSError, ValueError, SyntaxError, RecursionError, MemoryError):
        return None
    rules, other = [], 0
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        name = (call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                else None)
        if name == "host_executable":
            continue  # Which program paths a rule's first word may mean; not a permission.
        found = _rule(call) if name == "prefix_rule" else None
        if found is None:
            other += 1
        else:
            rules.append(SavedRule(found[0], found[1], str(path)))
    return rules, other


def rules_in(paths: Sequence[Path]) -> Tuple[List[SavedRule], List[str], int]:
    """Every rule in ``paths``, the files that couldn't be read, and how many
    entries couldn't be shown."""
    rules, unreadable, other = [], [], 0
    for path in sorted(paths, key=str):
        found = _read(Path(path))
        if found is None:
            unreadable.append(str(path))
        else:
            rules.extend(found[0])
            other += found[1]
    return rules, unreadable, other


def describe(rules: List[SavedRule], unreadable: Sequence[str] = (), other: int = 0) -> str:
    """The note Chat Permissions shows, one line per rule, readable by line."""
    lines = [INTRO]
    if not rules and not unreadable and not other:
        lines.append(NONE)
    shown = 0
    for path in dict.fromkeys(r.path for r in rules):
        if shown == MAX_RULES:
            break
        lines.append(f"In {path}:")
        for kind, label in KINDS:
            for rule in (r for r in rules if r.path == path and r.decision == kind):
                if shown == MAX_RULES:
                    break
                lines.append(f"{label}: {rule.command}.")
                shown += 1
    if len(rules) > shown:
        lines.append(f"{len(rules) - shown} more rules aren't listed here; open the file to see them.")
    if other:
        lines.append(f"{other} other {'entry' if other == 1 else 'entries'} in the rules "
                     "couldn't be shown here; open the file to see "
                     f"{'it' if other == 1 else 'them'}.")
    for path in unreadable:
        lines.append(f"Couldn't read {path}, so its rules aren't listed.")
    return "\n".join(lines)
