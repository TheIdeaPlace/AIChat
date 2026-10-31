"""Which of Copilot's permission requests The Chat Place may answer for you
(#352, #426): Auto, your own rules, and commands you allowed for one chat.

Copilot asks before every command and file change over ACP, and its own ways
of not asking don't help: its AI judge (``--assisted-approval``) refuses to
start with ``--acp``, and its ``--allow-tool`` rules match too loosely
(``shell(git:*)`` let ``git status; Remove-Item a.txt`` run unasked, probed
against 1.0.105). So The Chat Place decides, from a strict reading of what
the request says, and asks about everything it can't read:

- **Auto** (a chat's permission mode, chosen after a short warning): reading,
  listing and searching inside the chat's folder, changing to a folder inside
  it, ``git status``, ``log``, ``diff`` and the like, and Copilot's own file
  changes inside the folder, except in folders that hold settings which can
  make a program run (``.git``, ``.github``, ``.copilot``, ``.claude``,
  ``.vscode`` and the like, PROTECTED). Nothing that deletes, moves or writes
  through a command, and no file change that deletes.

Nothing at all is answered in a chat whose folder is a whole drive, the home
folder or a folder holding it (folder_too_broad), and nothing anywhere is
answered for a path in the home folder's settings and secrets or PowerShell's
profiles, or with a PROTECTED name anywhere on its path (Kelly's review of
#426, and the reviews of its fixes).
- **Your rules** (``copilot-rules.json`` in The Chat Place's folder, in any
  Agent-mode chat): ``allow`` lists commands you trust, word for word or with
  a trailing ``*`` for any further words; ``ask`` lists ones that always ask,
  and beats everything else.
- **Allowed for this chat**: a command you allowed with Allow for This Chat,
  kept for that chat only, matched letter for letter.

Whatever allows it, a command is read the same strict way. It is answered
only when:

- every part of it (split at ``|``, ``;``, ``&&``, ``||`` and new lines) is
  allowed: a changing of folder, a text filter after a pipe, or something one
  of the above allows;
- it has none of ``( ) @ , < > = $ ` { } ~ & * ? [ ] ^ # ! ..``, no ``:``
  except a drive's ``X:\\`` at the start of a word (Windows only), and no
  quote in the middle of a word (PowerShell joins ``C'':\\x`` into one path);
- every word that isn't an option is a path inside the folder, from every
  folder Copilot's shell may be in (see below), and none is in one of the
  settings folders above;
- it doesn't recurse, delete a folder or make a link (a link inside the
  folder can point outside it, and PowerShell 5.1 follows junctions).

Copilot's shell keeps its current folder between commands, and relative
paths are read from there. So every change of folder answered here is kept
(``locations``: each folder the shell may be in, the chat's own always among
them), and paths are checked from each. After a command you allowed yourself
that could have moved the shell somewhere unknown (``may_move``), no command
is answered for that chat until Copilot is restarted (location_changed).
File changes, which name full paths, still are.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .. import platform_paths

#: Auto's commands on Windows: reading and listing, nothing that writes,
#: runs other code, reaches the network or changes settings.
READ_CMDLETS = frozenset(name.lower() for name in (
    "Get-Content", "Get-ChildItem", "Get-Item", "Test-Path", "Resolve-Path", "Join-Path",
    "Split-Path", "Select-String", "Sort-Object", "Measure-Object", "Select-Object",
    "Format-List", "Format-Table", "Format-Wide", "Out-String", "Get-FileHash",
    "Write-Output", "Get-Location", "Get-Unique", "Get-Date",
))
#: Aliases of those, the same in PowerShell 5.1 and 7. Windows only.
READ_ALIASES = frozenset({
    "ls", "dir", "gci", "cat", "type", "gc", "gi", "select", "sort", "measure",
    "fl", "ft", "fw", "sls", "echo", "write", "pwd", "gl",
})
#: Outside Windows, Copilot's shell is bash or zsh, where these are plain
#: programs on PATH: only the simplest reading ones (sort, for one, can run
#: a program).
POSIX_READ = frozenset({"ls", "cat", "echo", "pwd"})
#: The option letters each of those may use there (letters combine freely,
#: so ls -lL, which lists through every link, is refused letter by letter).
POSIX_OPTION_LETTERS = {"ls": set("l1hsta"), "cat": set("n"), "echo": set("ne"), "pwd": set()}
#: Changing folder: kept track of, never answered as a way out.
_LOCATION = frozenset({"cd", "chdir", "sl", "set-location"})
_POSIX_LOCATION = frozenset({"cd"})
#: Commands a hand-allowed one may use to move the shell, or to run code in
#: it that could (a script runs in the same session; iex runs text).
_MOVERS = frozenset({
    "cd", "chdir", "sl", "set-location", "pushd", "popd", "push-location", "pop-location",
    "iex", "invoke-expression", "icm", "invoke-command", "ipmo", "import-module", ".", "&",
    "source", "eval", "exec", "function", "filter", "set-alias", "new-alias", "sal", "nal",
    # bash's ways of calling its own cd past a function or alias (Copilot's
    # review): builtin cd .., command cd ..
    "builtin", "command",
    # bash's trap runs code before later commands (Copilot's verification).
    "trap",
    # Ways to change what a later command means: an alias or function by
    # another name, a variable, PATH (Claude's review of Auto).
    # (New-Item and Remove-Item on alias: or function: are caught by the
    # drive, below.)
    "set-item", "si", "set-variable", "sv", "new-variable", "nv",
    "export", "alias", "unalias", "set", "unset",
    "hash", "declare", "typeset", "local", "readonly", "enable", "shopt", "setopt",
})
#: git's commands Auto answers: ones that only read the repository.
GIT_READ = frozenset({"status", "log", "diff", "show", "rev-parse", "ls-files", "blame",
                      "branch"})
#: The options they may use. Every other one asks: --output writes a file,
#: --ext-diff and --no-index run or reach elsewhere, -c and -C change git.
GIT_OPTIONS = frozenset({
    "--oneline", "--stat", "--shortstat", "--numstat", "--name-only", "--name-status",
    "--cached", "--staged", "--short", "--porcelain", "--branch", "--graph", "--all",
    "--decorate", "--no-color", "--patch", "--abbrev-commit", "--no-ext-diff",
    "--no-textconv", "--show-current", "--show-toplevel", "--list", "--reverse",
    "--first-parent", "--no-merges", "--merges", "--", "-p", "-s", "-b", "-n", "-v", "-vv",
    "-a", "-r", "-u", "-q", "--quiet",
})
#: git's commands that show differences, through a diff driver if one is set.
_GIT_SHOWS_CHANGES = frozenset({"diff", "show", "log", "blame"})
#: git branch is answered only to list: a name would make a branch.
_GIT_LIST_ONLY = frozenset({"branch"})
#: Folders whose files can make a program run: git's config (core.fsmonitor
#: runs a program on git status), Copilot's and Claude's hooks and settings,
#: and VS Code's tasks. Auto never changes anything in them, and no command
#: naming one is answered.
PROTECTED = frozenset({".git", ".github", ".copilot", ".claude", ".vscode", ".mcp.json",
                       ".githooks", ".idea", ".codex", ".husky", ".devcontainer", ".envrc",
                       ".gitmodules", ".gitattributes", ".pre-commit-config.yaml"})
#: Under the home folder: where secrets, settings and code that runs by itself
#: live. Nothing there is answered for you, wherever the chat's folder is
#: (Kelly's review of #426: a chat in the home folder could read .ssh keys
#: and write PowerShell profiles and the Startup folder).
_HOME_GUARDED = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config",
                 ".copilot", ".claude", ".codex", ".vscode", ".git-credentials", ".gitconfig",
                 ".netrc", ".npmrc", ".pypirc", ".bashrc", ".bash_profile", ".profile",
                 ".zshrc", ".zprofile", ".zshenv", "AppData", "Library")
_GITHUB_CODE = frozenset({"hooks", "copilot", "workflows"})
#: PowerShell's profiles, in the Documents folder wherever it is (OneDrive
#: moves it: Claude's review of the fixes).
_PROFILE_DIRS = ("WindowsPowerShell", "PowerShell")
#: Deleting commands, which mustn't take a folder.
_DELETING = frozenset({"remove-item", "del", "erase", "rm", "ri", "rd", "rmdir"})
#: Words that make a link: a link inside the folder can point outside it.
_LINKS = frozenset({"junction", "symboliclink", "hardlink"})
#: The most folders kept for a chat's shell: past it, commands ask.
MAX_LOCATIONS = 16

_SPLIT = re.compile(r"(\|\||&&|[|;\n\r])")
#: Anything that could run code, join or redirect paths, name a place other
#: than the folder by itself, or match through a link already in the folder
#: (wildcards, and zsh's extended patterns: ^x is "everything but x", and
#: # and ! take part in them too). Anything not plain ASCII too: PowerShell
#: reads smart quotes as quotes and en and em dashes as "-".
_REFUSED = re.compile(r"[()@,<>=$`{}~&*?\[\]^#!]|\.\.|::|[^\x00-\x7f]")
#: What may make a hand-allowed command move the shell in a way not read.
_MOVING = re.compile(r"[$`(){}@&]|[^\x00-\x7f]")
_WORD = re.compile(r"""'[^']*'|"[^"]*"|[^\s'"]+""")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
#: Commands longer than this always ask: the status bar says what was
#: allowed in full, and a long one is more than anyone reads.
MAX_COMMAND = 400


def _windows() -> bool:
    return os.name == "nt"


# -- rules --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One rule from copilot-rules.json: its words, and whether more may
    follow (a trailing ``*``)."""
    words: tuple
    prefix: bool = False
    text: str = ""

    def matches(self, words, *, as_prefix: bool = False) -> bool:
        said = tuple(_fold(_unquote(w)) for w in words)
        mine = tuple(_fold(w) for w in self.words)
        if len(said) < len(mine) or said[:len(mine)] != mine:
            return False
        return self.prefix or as_prefix or len(said) == len(mine)


def _unquote(word: str) -> str:
    return word[1:-1] if len(word) >= 2 and word[:1] in "'\"" and word[-1] == word[0] else word


def _fold(word: str) -> str:
    # PowerShell's commands and parameters don't care about case; bash's do.
    return word.lower() if _windows() else word


def parse_rule(text) -> Optional[Rule]:
    """A rule as written in the file, or None if it can't be read as one.
    Takes ``npm test``, ``git commit *``, and Claude Code's and Copilot's
    spellings, ``Bash(npm test:*)``, ``shell(git commit:*)``."""
    if not isinstance(text, str):
        return None
    body = text.strip()
    wrapped = re.fullmatch(r"(?i)(?:shell|bash|powershell)\((.*)\)", body)
    if wrapped:
        body = wrapped.group(1).strip()
    prefix = False
    for tail in (":*", " *"):
        if body.endswith(tail):
            body, prefix = body[:-len(tail)].strip(), True
            break
    if not body or len(body) > MAX_COMMAND or _REFUSED.search(body) or _SPLIT.search(body):
        return None
    words = _words(body)
    if not words:
        return None
    return Rule(tuple(_unquote(w) for w in words), prefix, text.strip())


@dataclass(frozen=True)
class Rules:
    """What copilot-rules.json says, and the entries it couldn't read."""
    allow: tuple = ()
    ask: tuple = ()
    unread: tuple = ()
    problem: str = ""


def read_rules(path) -> Rules:
    """The rules in ``path``. A missing file is none; one that can't be read
    is none too, with why, so nothing is answered from half a file."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(256 * 1024 + 1)
    except FileNotFoundError:
        return Rules()
    except OSError:
        return Rules(problem="couldn't be opened")
    if len(data) > 256 * 1024:
        return Rules(problem="is too big to be a rules file")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return Rules(problem="isn't JSON")
    if not isinstance(raw, dict):
        return Rules(problem='needs to be an object with "allow" and "ask" lists')
    lists, unread = {}, []
    for key in ("allow", "ask"):
        entries = raw.get(key, [])
        if not isinstance(entries, list):
            return Rules(problem=f'has an "{key}" that isn\'t a list')
        kept = []
        for entry in entries:
            rule = parse_rule(entry)
            if rule is None:
                unread.append(str(entry)[:200])
            else:
                kept.append(rule)
        lists[key] = tuple(kept)
    return Rules(lists["allow"], lists["ask"], tuple(unread))


@dataclass(frozen=True)
class Policy:
    """What may be answered for one chat: Auto, your rules, and the commands
    allowed for this chat (exactly as Copilot wrote them)."""
    auto: bool = False
    allow: tuple = ()
    ask: tuple = ()
    exact: frozenset = field(default_factory=frozenset)

    @property
    def any(self) -> bool:
        return bool(self.auto or self.allow or self.exact)


# -- paths ----------------------------------------------------------------------------


def _inside(path: str, folder: str) -> bool:
    """Whether ``path`` (relative to ``folder`` if not absolute) is the folder
    or inside it, links and junctions followed. A file with more than one
    hard link isn't: its other name may be outside the folder, and writing
    to either changes both. Hard links have no target to follow, so the
    only sign is the count (Copilot's review of #426)."""
    try:
        base = os.path.normcase(os.path.realpath(folder))
        full = path if os.path.isabs(path) else os.path.join(folder, path)
        target = os.path.normcase(os.path.realpath(full))
        if os.path.isfile(target) and os.stat(target).st_nlink > 1:
            return False
    except (OSError, ValueError, TypeError):
        return False
    return _under(target, base) and not _guarded(target)


def _home() -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser("~")))


def _under(target: str, place: str) -> bool:
    return target == place or target.startswith(place.rstrip(os.sep) + os.sep)


def _plain(path: str) -> str:
    """A path as Windows will make it: it drops trailing dots and spaces from
    each name, so "PowerShell." becomes "PowerShell" (Claude's review of the
    fixes). Elsewhere names are kept as they are."""
    if not _windows():
        return path
    return os.sep.join(part if part in (".", "..") else part.rstrip(". ")
                       for part in path.split(os.sep))


def _git_dir(path: str) -> bool:
    """Whether a folder is a git repository's own folder, whatever its name
    (a bare repository, remote.git, has hooks and config at its top:
    Copilot's review of the fixes)."""
    try:
        return (os.path.isfile(os.path.join(path, "HEAD"))
                and os.path.isdir(os.path.join(path, "objects"))
                and os.path.isdir(os.path.join(path, "refs")))
    except (OSError, ValueError):
        return False


def _guarded(target: str) -> bool:
    """Whether a real, normalised path is somewhere nothing is answered for
    you, whatever the chat's folder: a guarded place in the home folder,
    PowerShell's profiles, a git repository's own folder, or a PROTECTED name
    anywhere on its path (a chat started inside .github\\workflows gets no
    more than one started above it: Claude's review of the fixes)."""
    target = _plain(target)
    home = _home()
    # Temp files aren't settings or startup items, and a folder there is an
    # ordinary place to work (tests and builds use it).
    temp = os.path.normcase(os.path.join(home, "AppData", "Local", "Temp"))
    if not _under(target, temp) and any(
            _under(target, os.path.normcase(os.path.realpath(os.path.join(home, place))))
            for place in _HOME_GUARDED):
        return True
    # PowerShell's profiles, wherever Documents is, temp folder or not.
    if any(_under(target, os.path.normcase(os.path.realpath(os.path.join(documents, name))))
           for documents in platform_paths.documents_dirs() for name in _PROFILE_DIRS):
        return True
    parts = target.lower().split(os.sep)
    if parts[-1].endswith("profile.ps1"):
        return True
    for index, part in enumerate(parts):
        after = parts[index + 1:]
        # Claude Code's worktrees are whole projects inside .claude.
        if part == ".claude" and len(after) > 1 and after[0] == "worktrees":
            continue
        # An organisation's .github repository is an ordinary project; its
        # hooks, Copilot settings and workflows aren't. Below the chat's
        # folder all of .github is protected (_protected).
        if part == ".github":
            if after and after[0] in _GITHUB_CODE:
                return True
            continue
        if part in PROTECTED:
            return True
    folder = target
    while True:
        if _git_dir(folder):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def folder_too_broad(folder: str) -> bool:
    """A chat folder where nothing is answered for you: a whole drive, the
    home folder, a folder holding the home folder, or a guarded place itself
    (Kelly's review of #426: everything there would be "inside")."""
    try:
        base = os.path.normcase(os.path.realpath(folder))
    except (OSError, ValueError, TypeError):
        return True
    return os.path.dirname(base) == base or _under(_home(), base) or _guarded(base)


def _protected(path: str, folder: str) -> bool:
    """Whether a path is in one of the PROTECTED folders, as written or once
    links are followed."""
    # Only the part below the chat's folder counts: a folder that is itself
    # inside one (~/.claude/projects/x) still has its own files changed.
    full = path if os.path.isabs(path) else os.path.join(folder, path)
    names = []
    try:
        for where, base in ((os.path.abspath(full), os.path.abspath(folder)),
                            (os.path.realpath(full), os.path.realpath(folder))):
            names += re.split(r"[\\/]+", os.path.relpath(where, base))
    except (OSError, ValueError):
        return True  # another drive, or unreadable: taken as protected, which asks
    return any(name.lower().rstrip(" .") in PROTECTED for name in names)


def _inside_from(text: str, folder: str, locations) -> bool:
    """A path inside the folder from every folder the shell may be in."""
    if os.path.isabs(text) or (_windows() and _WINDOWS_DRIVE.match(text)):
        places = [text]
    else:
        places = [os.path.join(where, text) for where in locations]
    return all(_inside(p, folder) and not _protected(p, folder) for p in places)


def _absolute(path: str) -> str:
    """A path Copilot named for a file change, as a full path. Copilot names
    full paths; a diff's "b/Users/me/x" loses its leading slash on a Mac
    (changes._diff_path), so one that isn't full is taken from the root,
    which is outside the chat's folder unless the folder is the root: such a
    change asks rather than being read as inside the folder."""
    if os.path.isabs(path) or (_windows() and _WINDOWS_DRIVE.match(path)):
        return path
    return os.sep + path.lstrip("\\/")


def _plain_file(path: str) -> bool:
    """A path that names a file and nothing else: no ":" but a drive's
    (``.git::$DATA`` writes a file named .git; Claude's review of Auto), and
    no device (``sub\\CON``)."""
    rest = path[2:] if _windows() and _WINDOWS_DRIVE.match(path) else path
    if ":" in rest:
        return False
    name = os.path.basename(path.replace("\\", "/")).lower().rstrip(" .").split(".")[0]
    return name not in _DEVICES


def file_change_allowed(file_name: str, paths, folder: str, kind: str = "") -> bool:
    """A file change: every file it names a plain file inside the folder,
    and none in a settings folder. Deleting always asks, a file as much as a
    folder (Kelly's review of #426 deleted a folder this way)."""
    if kind == "delete":
        return False
    named = [_absolute(p) for p in [file_name, *paths] if p]
    return bool(named) and all(_plain_file(p) and _inside(p, folder)
                               and not _protected(p, folder) for p in named)


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


#: After a pipe, only commands that take what comes in as text, never as
#: paths: Set-Content, Remove-Item, Select-String and the like take a path
#: from each item piped in, which nothing here has checked (Claude's review
#: of #426: Get-ChildItem sub | Set-Content wrote through a hard link).
_AFTER_PIPE = frozenset({"select-object", "sort-object", "measure-object", "format-list",
                         "format-table", "format-wide", "out-string", "get-unique",
                         "select", "sort", "measure", "fl", "ft", "fw"})
#: Outside Windows, these after a pipe read only the text piped in. On
#: Windows cat is Get-Content, which reads each item piped in as a path.
_AFTER_PIPE_POSIX = frozenset({"cat"})
_COPYING = frozenset({"copy-item", "move-item", "copy", "cp", "cpi", "move", "mv", "mi"})
_LISTING = frozenset({"get-childitem", "gci", "dir", "ls"})
#: Windows device names: not files, wherever they're named.
_DEVICES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                      *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))})


def _prefix_of(word: str, name: str, shortest: int = 2) -> bool:
    """Whether ``word`` is a start of ``name`` at least ``shortest`` long,
    as PowerShell lets a parameter be shortened."""
    return len(word) >= shortest and name.startswith(word)


def _existing_dir(text: str, locations) -> bool:
    for where in locations:
        full = text if os.path.isabs(text) else os.path.join(where, text)
        try:
            if os.path.isdir(full):
                return True
        except (OSError, ValueError):
            return True  # can't tell: taken as one, which asks
    return False


def _paths_ok(name: str, words, folder: str, locations, *, letters: bool,
              long_options: bool) -> bool:
    """The words after a command's name: options that can't reach anywhere,
    and paths inside the folder. ``letters``: POSIX option letters are
    checked against POSIX_OPTION_LETTERS (Auto's own commands);
    ``long_options``: --options may be used (a program you chose)."""
    paths, previous = [], ""
    for word in words:
        text = _unquote(word)
        lowered = text.lower()
        if lowered in _LINKS:
            return False
        if word.startswith("-"):
            previous = lowered
            # A parameter name only: one with its value attached
            # (-Path:C:\x, bash's -o/tmp/x) asks.
            if any(c in text for c in ":/\\"):
                return False
            if lowered.startswith("--") and not long_options:
                return False
            # Recursion, and following links, reach through a link already
            # in the folder: -Recurse and any abbreviation of it,
            # -FollowSymlink, -Depth (Get-ChildItem's), and bash's -r, -R,
            # -rf. Erring toward asking, any short option with an r in it
            # does too. -Stream reaches a file's hidden streams. PowerShell
            # takes any unambiguous start of a parameter's name: -s is
            # -Stream on Set-Content, -rec is -Recurse. On Windows those
            # prefixes are refused, so -Raw and -ReadCount still work. On a
            # Mac or Linux, -L and -H follow links too (cp -aL, for a rule's
            # cp: CI on the merged fixes).
            # The common parameters that set a variable in the shell
            # (-OutVariable, -ov, ...), and -Wait, which never ends (Claude's
            # review of Auto).
            # Shortened, as PowerShell allows (-OutV; the verification pass).
            if (_windows() and ("var" in lowered or lowered in ("-ov", "-pv", "-iv", "-ev", "-wv")
                                or any(_prefix_of(lowered, name, 4) for name in (
                                    "-outvariable", "-errorvariable", "-warningvariable",
                                    "-informationvariable", "-pipelinevariable"))
                                or _prefix_of(lowered, "-wait", 4))):
                return False
            if (_prefix_of(lowered, "-stream") or _prefix_of(lowered, "-recurse", 2)
                    or _prefix_of(lowered, "-followsymlink", 4) or "follow" in lowered
                    or lowered in ("--recursive", "--dereference")
                    or (name in _LISTING and lowered.startswith("-de"))
                    or (name in ("new-item", "ni") and _prefix_of(lowered, "-name"))
                    or (not _windows() and not lowered.startswith("--")
                        and ("r" in lowered or "L" in text or "H" in text))):
                return False
            if (letters and not _windows()
                    and not set(text[1:]) <= POSIX_OPTION_LETTERS.get(name, set())):
                return False
            continue
        # New-Item's -ItemType (or -Type), however shortened, may only be a
        # file or a folder: "s" or "Junc" would make a link.
        if previous.startswith(("-it", "-ty")) or previous in ("-i", "-t"):
            if not lowered or not ("file".startswith(lowered)
                                   or "directory".startswith(lowered)):
                return False
            previous = ""
            continue
        previous = ""
        if os.path.basename(lowered.replace("\\", "/")).rstrip(" .").split(".")[0] in _DEVICES:
            return False  # CON, NUL, COM1: devices, not files
        if ":" in text and not (_windows() and _WINDOWS_DRIVE.match(text)
                                and text.count(":") == 1):
            return False  # another drive or provider, or a file's stream
        if text.startswith(("\\", "/")):
            return False  # a root, or a \\server path
        if not _inside_from(text, folder, locations):
            return False
        paths.append(text)
    # Deleting a folder, even without -Recurse, can be agreed to "Yes to
    # All" at PowerShell's own prompt: it asks here instead.
    if name in _DELETING and any(_existing_dir(p, locations) for p in paths):
        return False
    if name in _COPYING and len(paths) >= 2:
        # Whichever word names the folder (-Destination may come first),
        # each other one written into it is checked too.
        for target in paths:
            if not _existing_dir(target, locations):
                continue
            for source in paths:
                if source is target:
                    continue
                written = os.path.join(target, os.path.basename(source.rstrip("\\/")))
                if not _inside_from(written, folder, locations):
                    return False
    return True


#: Words in a repository's own git config that can make git run a program
#: or read config from elsewhere (core.fsmonitor on git status,
#: diff.external and diff.<driver>.command or textconv on git diff,
#: core.pager, filter.<name>.clean, include.path, ...). A folder can arrive
#: with any of them already set (a downloaded copy with its .git): then git
#: asks (Copilot's review).
_GIT_RUNS = re.compile(r"fsmonitor|pager|external|textconv|command|filter|include|program"
                       r"|editor|askpass|hook|helper|ssh|exec|alias|tool|cmd"
                       # Settings that read files from elsewhere: core.worktree,
                       # core.attributesFile, core.excludesFile (the verification pass).
                       r"|worktree|attributes|excludes", re.IGNORECASE)


def _git_dirs(start: str) -> list:
    """The git folders whose config applies to a command run from
    ``start``: the first .git found going up (a folder, or a worktree's
    .git file naming one, with its common folder). None if one can't be
    read: then git asks."""
    here = os.path.realpath(start)
    while True:
        dot = os.path.join(here, ".git")
        try:
            if os.path.isdir(dot):
                # git reads a commondir file in any git folder, and then the
                # config there (the verification pass).
                return _with_common(dot)
            if os.path.isfile(dot):
                with open(dot, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read(4096)
                found = re.match(r"\s*gitdir:\s*(.+?)\s*$", text)
                if not found:
                    return None
                gitdir = found.group(1)
                gitdir = gitdir if os.path.isabs(gitdir) else os.path.join(here, gitdir)
                return _with_common(gitdir)
        except OSError:
            return None
        parent = os.path.dirname(here)
        if parent == here:
            return []  # not in a repository: git has no repository config to read
        here = parent


def _with_common(gitdir: str) -> list:
    """A git folder, and the common folder its commondir file names, if it
    has one (a worktree's, or any git folder's: git reads it either way)."""
    dirs = [gitdir]
    common = os.path.join(gitdir, "commondir")
    if os.path.isfile(common):
        with open(common, "r", encoding="utf-8", errors="replace") as handle:
            shared = handle.read(4096).strip()
        dirs.append(shared if os.path.isabs(shared) else os.path.join(gitdir, shared))
    return dirs


def _git_config_safe(locations) -> bool:
    """Whether the repository config git would read, from every folder the
    shell may be in, sets nothing that can run a program."""
    for where in locations:
        dirs = _git_dirs(where)
        if dirs is None:
            return False
        for gitdir in dirs:
            for name in ("config", "config.worktree"):
                path = os.path.join(gitdir, name)
                try:
                    with open(path, "rb") as handle:
                        text = handle.read(512 * 1024 + 1)
                except FileNotFoundError:
                    continue
                except OSError:
                    return False
                if len(text) > 512 * 1024 or not _git_names_safe(text.decode("utf-8", "replace")):
                    return False
    return True


def _git_names_safe(text: str) -> bool:
    """A git config file's settings, by name only (a remote's address may
    well contain "tool" or "ssh"): none that can run a program. A line it
    can't read as a setting counts as one that might."""
    section = ""
    # git breaks lines only at \n. Python's splitlines also breaks at form
    # feed, \x0b, \x1c to \x1e, \x85 and \u2028, so a "#\x0c[x]" comment
    # to git hid an [include] from this reading (Claude's review of the
    # merged fixes). Any such character asks.
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85\u2028\u2029]", text):
        return False
    for raw in text.split("\n"):
        line = raw.strip()
        if line.endswith("\\"):
            return False  # carried on to the next line: not read, so taken as unsafe
        if not line or line[0] in "#;":
            continue
        header = re.match(r"\[\s*([A-Za-z0-9.-]+)(?:\s+\"[^\"]*\")?\s*\](.*)$", line)
        if header:
            section = header.group(1)
            line = header.group(2).strip()  # a setting may follow on the same line
            if not line or line[0] in "#;":
                continue
        if line.startswith("["):
            return False
        key = re.match(r"([A-Za-z][A-Za-z0-9-]*)\s*(=|$|[#;])", line)
        if not key:
            # A value carried on from the line before (a backslash at its
            # end) reads as no setting: taken as one that might.
            return False
        if _GIT_RUNS.search(f"{section}.{key.group(1)}"):
            return False
    return True


def _git_ok(words, folder: str, locations) -> bool:
    """git with one of GIT_READ and only GIT_OPTIONS: ``git status``,
    ``git log --oneline -5``, ``git diff -- notes.txt``."""
    words = list(words)
    # Options Copilot puts before the command, which only stop git waiting
    # on a pager or taking a lock.
    while len(words) > 1 and _unquote(words[1]).lower() in ("--no-pager", "--no-optional-locks"):
        del words[1]
    if len(words) < 2 or _unquote(words[1]).lower() not in GIT_READ:
        return False
    sub = _unquote(words[1]).lower()
    rest = words[2:]
    after_dashes = False
    for index, word in enumerate(rest):
        text = _unquote(word)
        if not after_dashes and text.startswith("-"):
            if text == "--":
                after_dashes = True
                continue
            if text.lower() not in GIT_OPTIONS and not re.fullmatch(r"-\d{1,4}", text):
                return False
            continue
        if sub in _GIT_LIST_ONLY:
            return False  # a name would make a branch
        if index and _unquote(rest[index - 1]) == "-n" and re.fullmatch(r"\d{1,4}", text):
            continue  # git log -n 5
        if ":" in text or text.startswith(("\\", "/")):
            return False  # rev:path, another drive, a root
        if os.path.basename(text.lower().replace("\\", "/")).split(".")[0] in _DEVICES:
            return False
        # A revision or a path: either way, read as a path inside the folder,
        # which a branch name is.
        if not _inside_from(text, folder, locations):
            return False
    if not (_git_repository_here(folder, locations) and _git_config_safe(locations)):
        return False
    # Showing differences runs a diff driver the repository's .gitattributes
    # may choose, from your own git config too: only when none names a
    # program (Copilot's review of Auto). git status shows none.
    if sub in _GIT_SHOWS_CHANGES:
        from .. import platform_paths
        return platform_paths.git_names_diff_program(folder) is False
    return True


def _git_repository_here(folder: str, locations) -> bool:
    """git is answered only with the shell in the chat's own folder, and that
    folder holding a real .git folder (not a link, not a .git file): git
    then reads that repository and no other. Elsewhere it could find one
    Copilot made: a folder with HEAD, objects and refs in it is a repository
    to git, and Auto's file changes could write those."""
    base = os.path.normcase(os.path.realpath(folder))
    if any(os.path.normcase(os.path.realpath(where)) != base for where in locations):
        return False
    dot = os.path.join(folder, ".git")
    # git status and diff read each submodule's own config too, which isn't
    # checked here (Claude's review of the merged fixes): a repository with
    # submodules asks.
    if (os.path.lexists(os.path.join(folder, ".gitmodules"))
            or os.path.lexists(os.path.join(dot, "modules"))):
        return False
    try:
        return os.path.isdir(dot) and not os.path.islink(dot) and \
            os.path.normcase(os.path.realpath(dot)) == os.path.normcase(os.path.join(base, ".git"))
    except OSError:
        return False


def _moved_to(words, folder: str, locations) -> Optional[tuple]:
    """The folders the shell may be in after ``cd x`` (``Set-Location
    -Path x``), or None if it isn't a plain change to a folder inside the
    chat's. ``cd`` alone (home), ``cd -`` and ``cd ..`` are refused."""
    rest = list(words[1:])
    if _windows() and len(rest) == 2 and _unquote(rest[0]).lower() in ("-path", "-literalpath"):
        rest = rest[1:]
    if len(rest) != 1:
        return None
    text = _unquote(rest[0])
    if not text or text.startswith(("-", "+")) or text in (".", "~"):
        return None
    if ":" in text and not (_windows() and _WINDOWS_DRIVE.match(text) and text.count(":") == 1):
        return None
    # bash and zsh look in CDPATH first, and cdable_vars reads a name as a
    # variable, so "cd .ssh" could go to ~/.ssh (Claude's review of the
    # merged fixes). "./" and a full path skip both: elsewhere only those are
    # followed, and only to a folder that's there.
    if not _windows():
        if not text.startswith(("./", "/")):
            return None
        if not all(os.path.isdir(os.path.join(where, text)) for where in locations):
            return None
    elif text.startswith(("\\", "/")):
        return None
    if not _inside_from(text, folder, locations):
        return None
    if os.path.isabs(text) or (_windows() and _WINDOWS_DRIVE.match(text)):
        new = [os.path.normpath(text)]
    else:
        # "./sub" is kept as sub, so later paths compare as the same folder.
        new = [os.path.normpath(os.path.join(where, text)) for where in locations]
    # Every folder it may have been in stays: a command that fails to
    # change folder leaves the shell where it was.
    kept = list(dict.fromkeys([*locations, *new]))
    return tuple(kept) if len(kept) <= MAX_LOCATIONS else None


# -- commands --------------------------------------------------------------------------


AUTO, RULE, CHAT = "auto", "rule", "chat"


@dataclass(frozen=True)
class Answer:
    """A request The Chat Place may answer for you: what allowed it (AUTO,
    RULE or CHAT), and the folders Copilot's shell may be in after it."""
    by: str
    locations: tuple = ()


def _parts(command: str):
    """[(words, whether a pipe brought it what it reads)], or None if the
    command can't be read: too long, empty, with a refused character, a
    quote inside a word, or a pipe carried on to the next line."""
    if (not command or not command.strip() or len(command) > MAX_COMMAND
            or _REFUSED.search(command.replace("&&", " ; "))):
        return None
    # bash and zsh read a backslash as an escape: sub/\.\./x is ../x there.
    # On Windows it's only the path separator.
    if not _windows() and "\\" in command:
        return None
    # PowerShell carries a pipe on to the next line: "gci sub |" then a
    # line "Set-Content x" is one pipeline (Claude's review of #426).
    if re.search(r"\|[ \t]*[\r\n]", command):
        return None
    pieces = _SPLIT.split(command)
    parts, piped = [], False
    for index, piece in enumerate(pieces):
        if index % 2:
            piped = piece == "|"
        elif piece.strip():
            words = _words(piece.strip())
            if not words:
                return None
            parts.append((words, piped))
        elif index:
            return None  # "a ; ; b", or one ending in a pipe
    return parts or None


#: PowerShell's own aliases (the same in 5.1 and 7), so an ask rule for
#: Remove-Item asks for ri and del too.
_ALIAS_OF = {
    "ri": "remove-item", "rm": "remove-item", "del": "remove-item", "erase": "remove-item",
    "rd": "remove-item", "rmdir": "remove-item", "cpi": "copy-item", "copy": "copy-item",
    "cp": "copy-item", "mi": "move-item", "move": "move-item", "mv": "move-item",
    "rni": "rename-item", "ren": "rename-item", "ni": "new-item", "si": "set-item",
    "sc": "set-content", "ac": "add-content", "clc": "clear-content", "iwr": "invoke-webrequest",
    "curl": "invoke-webrequest", "wget": "invoke-webrequest", "irm": "invoke-restmethod",
    "iex": "invoke-expression", "icm": "invoke-command", "saps": "start-process",
    "start": "start-process", "kill": "stop-process", "spps": "stop-process",
    "gc": "get-content", "cat": "get-content", "type": "get-content", "ls": "get-childitem",
    "dir": "get-childitem", "gci": "get-childitem", "gi": "get-item", "sls": "select-string",
    "cd": "set-location", "sl": "set-location", "chdir": "set-location",
}
#: git's options before its command, and the ones that take the next word.
_GIT_VALUE_OPTIONS = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace",
                                "--exec-path", "--config-env"})


def _ask_forms(words):
    """A part's words as an ask rule may name them: as written, with git's
    own leading options left out, and on Windows with an alias's cmdlet."""
    forms = [list(words)]
    plain = [_unquote(w) for w in words]
    if plain and plain[0].lower() in ("git", "git.exe"):
        rest, index = ["git"], 1
        while index < len(plain) and plain[index].startswith("-"):
            index += 2 if plain[index] in _GIT_VALUE_OPTIONS else 1
        forms.append(rest + plain[index:])
    if _windows() and plain and plain[0].lower() in _ALIAS_OF:
        forms.append([_ALIAS_OF[plain[0].lower()]] + plain[1:])
    return forms


def _asked(words, policy: Policy) -> bool:
    """An ``ask`` rule matches the start of this part, in any of its forms."""
    return any(rule.matches(form, as_prefix=True)
               for rule in policy.ask for form in _ask_forms(words))


def _part(words, piped: bool, folder: str, locations, policy: Policy, *, exact: bool):
    """(what allows one part of a command, or None; the folders after it).
    "move" and "filter" go along with what allows the rest."""
    name = _unquote(words[0]).lower()
    if _asked(words, policy):
        return None, locations
    if piped:
        after = _AFTER_PIPE if _windows() else _AFTER_PIPE_POSIX
        ok = name in after and _paths_ok(name, words[1:], folder, locations,
                                         letters=True, long_options=False)
        return ("filter" if ok else None), locations
    if name in (_LOCATION if _windows() else _POSIX_LOCATION):
        moved = _moved_to(words, folder, locations)
        return ("move", moved) if moved is not None else (None, locations)
    if exact:
        ok = (not may_move(" ".join(words))
              and _paths_ok(name, words[1:], folder, locations, letters=False, long_options=True)
              # A kept git command gets git's own checks each time (its
              # config, the repository, a diff program).
              and (name not in ("git", "git.exe") or _git_ok(words, folder, locations)))
        return (CHAT if ok else None), locations
    if policy.auto:
        reading = (name in READ_CMDLETS or name in READ_ALIASES) if _windows() \
            else name in POSIX_READ
        if reading and _paths_ok(name, words[1:], folder, locations,
                                 letters=True, long_options=False):
            return AUTO, locations
        if name == "git" and _git_ok(words, folder, locations):
            return AUTO, locations
    for rule in policy.allow:
        if rule.matches(words) and not may_move(" ".join(words)):
            extra = words[len(rule.words):]
            if _paths_ok(name, extra, folder, locations, letters=False, long_options=True):
                return RULE, locations
    return None, locations


def check_command(command: str, folder: str, policy: Policy,
                  locations=None) -> Optional[Answer]:
    """Whether a command may be answered for you, and the folders Copilot's
    shell may be in after it; None if it asks."""
    if not folder or not policy.any:
        return None
    parts = _parts(command)
    if parts is None:
        return None
    places = tuple(locations) if locations else (folder,)
    exact = command.strip() in policy.exact
    found = set()
    for words, piped in parts:
        # After a part your rule or Allow for This Chat allows, only what a
        # pipe hands text to: the rest was checked against the disk as it was
        # before that part ran, and that part may change it (Kelly's review of
        # #426: a move, then a path through a link already in what moved).
        if (RULE in found or CHAT in found) and not piped:
            return None
        by, places = _part(words, piped, folder, places, policy, exact=exact)
        if by is None:
            return None
        found.add(by)
    if exact:
        return Answer(CHAT, places)
    for by in (RULE, AUTO):
        if by in found:
            # A rule's part with Auto's: said as the rule's.
            return Answer(by, places)
    # Only changes of folder and filters: Auto's, if it's on.
    return Answer(AUTO, places) if policy.auto else None


def command_allowed(command: str, folder: str, policy: Optional[Policy] = None,
                    locations=None) -> bool:
    """check_command, as yes or no: Auto alone unless a policy is given."""
    return check_command(command, folder, policy or Policy(auto=True), locations) is not None


def chat_candidate(command: str, folder: str, policy: Policy, locations=None) -> str:
    """The command as Allow for This Chat would keep it, or "" if it can't be
    kept: one the strict reading can't follow, that reaches outside the
    folder, or that an ``ask`` rule covers. Kept as Copilot wrote it, so
    only the very same command is answered later."""
    if not folder or folder_too_broad(folder):
        return ""
    trial = Policy(auto=False, ask=policy.ask, exact=frozenset({command.strip()}))
    return command.strip() if check_command(command, folder, trial, locations) else ""


def may_move(command: str) -> bool:
    """Whether a command you allowed yourself may have moved Copilot's shell
    somewhere not followed here: a change of folder, a script (which runs in
    the same session), or text run as code. Erring toward yes: anything not
    read as plain words is taken as moving."""
    if (not command or len(command) > MAX_COMMAND * 10
            or _MOVING.search(command.replace("&&", " ; "))):
        return True
    for piece in _SPLIT.split(command)[::2]:
        if not piece.strip():
            continue
        words = _words(piece.strip())
        if not words:
            return True
        name = _unquote(words[0]).lower()
        # PowerShell's own cd.. and cd\ are functions, one word each.
        if (name in _MOVERS or re.match(r"(cd|chdir|sl)\W", name) or name.startswith(".")
                or "\\" in name or "/" in name):
            return True
        if name.endswith((".ps1", ".psm1", ".psd1", ".sh", ".bash", ".zsh")):
            return True
        for word in words:
            text = _unquote(word)
            # A PowerShell drive other than a disk's (alias:\ls, env:PATH),
            # or a bash assignment (PATH=sub ls).
            if ":" in text and not (_windows() and _WINDOWS_DRIVE.match(text)):
                return True
            if re.match(r"[A-Za-z_][A-Za-z0-9_]*\+?=", text):
                return True
    return False


def decide(request, folder: Optional[str], policy: Policy, locations=None,
           location_changed: bool = False) -> Optional[Answer]:
    """Whether a Copilot permission request may be answered "allow once" for
    you. Requests too long to show, a file move, or of another kind, always
    ask; commands, once the shell may have moved, too. A file change only
    in Auto: rules and Allow for This Chat are for commands."""
    from .copilot_runtime import COMMAND, FILES
    if (not folder or getattr(request, "too_long", True)
            or getattr(request, "paths_too_long", True) or folder_too_broad(folder)):
        return None
    method = getattr(request, "method", "")
    if method == COMMAND:
        if location_changed:
            return None
        return check_command(getattr(request, "command", ""), folder, policy, locations)
    if method == FILES and policy.auto:
        # A move names where it goes in a way not checked here: it asks.
        if getattr(request, "tool_kind", "") == "move":
            return None
        if file_change_allowed(getattr(request, "file_name", ""),
                               getattr(request, "paths", ()), folder,
                               getattr(request, "tool_kind", "")):
            return Answer(AUTO, tuple(locations) if locations else (folder,))
    return None
