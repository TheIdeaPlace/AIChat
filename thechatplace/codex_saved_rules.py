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
import os
from pathlib import Path
import stat
from typing import List, Optional, Sequence, Tuple
import unicodedata

#: What each decision means, as the list words it, strictest last as Codex
#: ranks them (forbidden beats prompt beats allow).
KINDS = (("allow", "Allowed without asking"), ("prompt", "Always asks"),
         ("forbidden", "Never allowed"))
#: Codex refuses a rule that says to ask when the chat never asks
#: (exec_policy.rs: "approval required by policy, but AskForApproval is set
#: to Never"), so such a chat's list says that instead.
PROMPT_NEVER_ASKS = "Refused in this chat, which never asks"

INTRO = ("From Codex's own saved rules. These apply to every Codex chat, here, in the Codex app "
         "or a terminal. Each rule covers any command that starts with its words, and an "
         "allowed command runs outside this folder's limits: it can change files elsewhere "
         "and use the network. The Chat Place only reads these rules; change them in Codex, "
         "or edit the file.")
NONE = "Codex's own saved rules allow nothing extra."
ERROR = "Couldn't list Codex's own saved rules. "
UNCHECKED = ("Codex's rules folders couldn't be checked, so its saved rules aren't listed, and "
             "Codex chats here won't start or send until they can be.")


def project_rules(folder: str) -> str:
    """What the list says when the chat's folder brings rules of its own."""
    return (f"This folder supplies its own Codex rules, in {_visible(folder)}. The Chat Place "
            "refuses those, so Codex chats here won't start or send until they're moved out "
            "of the project.")

#: A rules file bigger than this isn't read: Codex's own are a few kilobytes.
MAX_SIZE = 1024 * 1024
#: The list stops here, so a huge file can't bury the rest of the note.
MAX_RULES = 500
#: A rule longer than this is cut, so each stays a line or two to read.
MAX_COMMAND = 300


@dataclass(frozen=True)
class SavedRule:
    decision: str      # allow, prompt or forbidden
    command: str       # the pattern, one line
    path: str


def _visible(text: str) -> str:
    """``text`` on one line, with control and format characters (NUL, ESC,
    bidi overrides) shown as escapes, so nothing in a file can hide a line
    of the note, reorder it, or pass for another rule."""
    text = " ".join(text.split())
    return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co")
                   else c for c in text)


def _word(value) -> Optional[str]:
    """One pattern word, or alternatives ("add or commit"), on one line."""
    if isinstance(value, str):
        # A word with spaces or quotes in it (a whole PowerShell script, say)
        # is quoted, with its own quotes escaped, so it reads as one word.
        text = _visible(value)
        if text and text == value and not any(c in text for c in ' "'):
            return text
        # Backslashes first, then quotes, then the control escapes, so each
        # reads only one way.
        text = " ".join(value.split()).replace('\\', '\\\\').replace('"', '\\"')
        return '"' + _visible(text) + '"'
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
    except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
        return None
    if len(args) != len(call.keywords):
        return None
    pattern, decision = args.get("pattern"), args.get("decision", "allow")
    if decision not in dict(KINDS) or not isinstance(pattern, list) or not pattern:
        return None
    words = [_word(w) for w in pattern]
    if None in words:
        return None
    command = " ".join(words)
    if len(command) > MAX_COMMAND:
        command = command[:MAX_COMMAND] + "..."
    return decision, command


def _text(path: Path) -> str:
    """A rules file's text: only a regular file, and never more than
    MAX_SIZE, whatever its size claims (a pipe or device says 0)."""
    with open(path, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("not a regular file")
        data = f.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise ValueError("too big")
    return data.decode("utf-8-sig")


def _read(path: Path) -> Optional[Tuple[List[SavedRule], int]]:
    """A file's rules and how many entries couldn't be shown, or None when
    it can't be read or parsed."""
    try:
        if not path.is_file():  # Before opening, so a pipe never blocks the worker.
            return None
        tree = ast.parse(_text(path), str(path))
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


def describe(rules: List[SavedRule], unreadable: Sequence[str] = (), other: int = 0,
             problem: str = "", never_asks: bool = False) -> str:
    """The note Chat Permissions shows, one line per rule, readable by line.
    ``never_asks``: the chat's approval policy is never, so Codex refuses
    what a rule says to ask about."""
    labels = dict(KINDS)
    if never_asks:
        labels["prompt"] = PROMPT_NEVER_ASKS
    lines = [INTRO]
    if not rules and not unreadable and not other and not problem:
        lines.append(NONE)
    shown = 0
    for path in dict.fromkeys(r.path for r in rules):
        if shown == MAX_RULES:
            break
        lines.append(f"In {_visible(path)}:")
        for kind, _label in KINDS:
            for rule in (r for r in rules if r.path == path and r.decision == kind):
                if shown == MAX_RULES:
                    break
                lines.append(f"{labels[kind]}: commands starting with {rule.command}.")
                shown += 1
    left = len(rules) - shown
    if left:
        lines.append(f"{left} more {'rule isn' if left == 1 else 'rules aren'}'t listed here; "
                     "open the rules files to see them.")
    if other:
        lines.append(f"{other} other {'entry' if other == 1 else 'entries'} in the rules "
                     "files couldn't be shown here; open the files to see "
                     f"{'it' if other == 1 else 'them'}.")
    for path in unreadable:
        lines.append(f"Couldn't read {_visible(path)}, so its rules aren't listed.")
    if problem:
        lines.append(problem)
    return "\n".join(lines)
