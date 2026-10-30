"""Copilot's "run freely in this folder" (#352): which of Copilot's permission
requests The Chat Place may answer for you in a chat where you turned it on.

You turn it on for one chat, after a warning, and it lasts until you turn it
off or close The Chat Place. Inside the chat's folder, Copilot's file changes
and its plain file and text commands are allowed without asking; anything
else still asks.

For a file change the boundary is exact: Copilot names the file. For a
command it is a strict reading of the command's text, not a sandbox, so the
reading allows only a small, known set and asks about everything else
(an independent security review of a first, looser version found commands
that ran code or reached outside the folder; each is a test now). A command
is allowed only when:

- every part of it (split at ``|``, ``;``, ``&&``, ``||`` and new lines)
  starts with one of ALLOWED_CMDLETS, or an alias of one;
- it has none of ``( ) @ , < > = $ ` { } ~ & ..``, no ``:`` except a drive's
  ``X:\\`` at the start of a word (Windows only), and no quote in the middle
  of a word (PowerShell joins ``C'':\\x`` into one path);
- every word that isn't a parameter (``-Path``) is a path inside the folder;
- it doesn't delete recursively or make a link (a link inside the folder can
  point outside it, and PowerShell 5.1 follows junctions when deleting).

Copilot's shell keeps its current folder between commands, and paths are
checked against the chat's folder, so changing location is never allowed
(it isn't in the list); and once you've allowed one by hand, the shell may be
somewhere else, so no command is answered for that chat after it
(location_changed). File changes, which name full paths, still are.
"""
from __future__ import annotations

import os
import re
from typing import Optional

#: The only cmdlets a command may use: file and text work, nothing that runs
#: other code, reaches the network or changes location or settings.
ALLOWED_CMDLETS = frozenset(name.lower() for name in (
    "Get-Content", "Set-Content", "Add-Content", "Clear-Content", "Get-ChildItem",
    "Get-Item", "New-Item", "Copy-Item", "Move-Item", "Remove-Item", "Rename-Item",
    "Test-Path", "Resolve-Path", "Join-Path", "Split-Path", "Select-String",
    "Sort-Object", "Measure-Object", "Select-Object", "Format-List", "Format-Table",
    "Format-Wide", "Out-File", "Out-String", "Get-FileHash", "Write-Output",
    "Get-Location", "Get-Unique", "Get-Date",
))
#: Outside Windows, Copilot's shell is bash or zsh, where these are plain
#: programs on PATH: only the simplest file ones (sort, for one, can run a
#: program; ni and write are other tools there). The second security review.
POSIX_ALLOWED = frozenset({"ls", "cat", "cp", "mv", "rm", "echo", "pwd"})
#: The option letters each of those may use there (letters combine freely,
#: so cp -aL, which copies through every link, is refused letter by letter:
#: the third security review).
POSIX_OPTION_LETTERS = {"ls": set("l1hst"), "cat": set("n"), "cp": set("fin"),
                        "mv": set("fin"), "rm": set("fi"), "echo": set("ne"), "pwd": set()}
#: Aliases of those, the same in PowerShell 5.1 and 7 (sc, which is
#: Set-Content only in 5.1, isn't one). Windows only.
ALLOWED_ALIASES = frozenset({
    "ls", "dir", "gci", "cat", "type", "gc", "gi", "ni", "copy", "cp", "cpi", "move",
    "mv", "mi", "del", "erase", "rm", "ri", "ren", "rni", "select", "sort", "measure",
    "fl", "ft", "fw", "sls", "echo", "write", "pwd", "gl",
})
#: Deleting commands, which mustn't be recursive.
_DELETING = frozenset({"remove-item", "del", "erase", "rm", "ri"})
#: Words that make a link: a link inside the folder can point outside it.
_LINKS = frozenset({"junction", "symboliclink", "hardlink"})

_SPLIT = re.compile(r"\|\||&&|[|;\n\r]")
#: Anything that could run code, join or redirect paths, name a place other
#: than the folder by itself, or match through a link already in the folder
#: (wildcards). Anything not plain ASCII too: PowerShell reads smart quotes
#: as quotes and en and em dashes as "-" (the second security review).
_REFUSED = re.compile(r"[()@,<>=$`{}~&*?\[\]]|\.\.|::|[^\x00-\x7f]")
_WORD = re.compile(r"""'[^']*'|"[^"]*"|[^\s'"]+""")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _inside(path: str, folder: str) -> bool:
    """Whether ``path`` (relative to ``folder`` if not absolute) is the folder
    or inside it, links and junctions followed."""
    try:
        base = os.path.normcase(os.path.realpath(folder))
        full = path if os.path.isabs(path) else os.path.join(folder, path)
        target = os.path.normcase(os.path.realpath(full))
    except (OSError, ValueError, TypeError):
        return False
    return target == base or target.startswith(base.rstrip(os.sep) + os.sep)


def _absolute(path: str) -> str:
    """A path Copilot named for a file change, as a full path. Copilot names
    full paths; a diff's "b/Users/me/x" loses its leading slash on a Mac
    (changes._diff_path), so one that isn't full is taken from the root,
    which is outside the chat's folder unless the folder is the root: such a
    change asks rather than being read as inside the folder."""
    if os.path.isabs(path) or (os.name == "nt" and _WINDOWS_DRIVE.match(path)):
        return path
    return os.sep + path.lstrip("\\/")


def file_change_allowed(file_name: str, paths, folder: str) -> bool:
    """A file change: every file it names inside the folder."""
    named = [p for p in [file_name, *paths] if p]
    return bool(named) and all(_inside(_absolute(p), folder) for p in named)


def _words(part: str):
    """The words of one command, or None if a quote sits inside a word
    (``C'':\\x``, ``a"b"``): each quoted string must be a whole word."""
    words, end = [], 0
    for match in _WORD.finditer(part):
        gap = part[end:match.start()]
        if gap.strip() or (words and not gap):
            return None  # two pieces of one word, joined, or something unread
        words.append(match.group())
        end = match.end()
    return None if part[end:].strip() else words


def changes_location(command: str, folder: str) -> bool:
    """Whether a command you allowed by hand may have moved Copilot's shell,
    so its folder isn't known any more. Any command this check doesn't
    itself allow may (cd.., cd\\, a script, iex 'cd x' all can), so it's
    taken that it did: the second security review."""
    return not command_allowed(command, folder)


def command_allowed(command: str, folder: str) -> bool:
    """A command: see the module docstring. False whenever in doubt."""
    if not command or not command.strip() or _REFUSED.search(command.replace("&&", " ; ")):
        return False
    for part in _SPLIT.split(command):
        if not part.strip():
            continue
        words = _words(part.strip())
        if not words:
            return False
        name = words[0].lower()
        if os.name == "nt":
            if name not in ALLOWED_CMDLETS and name not in ALLOWED_ALIASES:
                return False
        elif name not in POSIX_ALLOWED:
            return False
        for word in words[1:]:
            text = word[1:-1] if word[:1] in "'\"" else word
            lowered = text.lower()
            if lowered in _LINKS:
                return False
            if word.startswith("-"):
                # A parameter name only: one with its value attached
                # (-Path:C:\x, bash's -o/tmp/x) asks.
                if any(c in text for c in ":/\\"):
                    return False
                # Long options can name a program or a list of files to read
                # (sort --compress-program, --files0-from): none is allowed.
                if lowered.startswith("--"):
                    return False
                # Recursion, and following links, reach through a link
                # already in the folder: -Recurse and any abbreviation of it,
                # -FollowSymlink, and bash's -r, -R, -rf. Erring toward
                # asking, any short option with an r in it does too.
                if (lowered.startswith(("-r", "-fol", "-de")) or "follow" in lowered
                        or (len(lowered) <= 4 and "r" in lowered)):
                    return False  # -Depth recurses too (PowerShell 5.1 through junctions)
                if os.name != "nt" and not set(text[1:]) <= POSIX_OPTION_LETTERS.get(name, set()):
                    return False
                continue
            if ":" in text and not (os.name == "nt" and _WINDOWS_DRIVE.match(text)
                                    and text.count(":") == 1):
                return False  # another drive or provider, or a file's stream
            if text.startswith(("\\", "/")):
                return False  # a root, or a \\server path
            if not _inside(text, folder):
                return False
    return True


def allowed(request, folder: Optional[str], location_changed: bool = False) -> bool:
    """Whether a Copilot permission request may be answered "allow once" for
    you in a chat running freely in ``folder``. Requests too long to show, a
    file move, or of another kind, always ask; commands, once the shell may
    have moved, too."""
    from .copilot_runtime import COMMAND, FILES
    if not folder or getattr(request, "too_long", True):
        return False
    method = getattr(request, "method", "")
    if method == COMMAND:
        return (not location_changed
                and command_allowed(getattr(request, "command", ""), folder))
    if method == FILES:
        # A move names where it goes in a way not checked here: it asks.
        if getattr(request, "tool_kind", "") == "move":
            return False
        return file_change_allowed(getattr(request, "file_name", ""),
                                   getattr(request, "paths", ()), folder)
    return False
