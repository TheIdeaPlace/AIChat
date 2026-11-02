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
  quote in the middle of a word (PowerShell joins ``C'':\\x`` into one path).
  A rule's or kept command's own program, if it isn't PowerShell's, may have
  those inside plain quotes, which it only receives as an argument (_masked);
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
is answered for that chat until Copilot is restarted (location_changed), which
Restart Copilot in Chat Permissions does. File changes, which name full paths,
still are.

Whichever allows it, git is answered only after asking git itself where it is
and what it's set to (_git_repository_here, _git_config_safe): working that
out by hand kept disagreeing with git about which files count.

When a request that would be answered asks instead, the chat says why:
ask_reason gives this module's part of that, and copilot_runtime the rest.
"""
from __future__ import annotations

import json
import os
import re
import threading
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
    # A drive of a new name, which a later path could then use.
    "new-psdrive", "ndr", "mount",
})
#: PowerShell's own drives other than disks, as the start of a word
#: ("env:PATH", "alias:ls"): writing to one changes what later commands mean.
#: Anything else before a colon ("test:unit", "fix: typo", "https:") is an
#: ordinary word (Kelly's review of 9064712: every colon counted, so a
#: conventional commit message or an npm script name made commands ask).
_PS_DRIVES = frozenset({"env", "alias", "function", "variable", "hklm", "hkcu", "cert",
                        "wsman", "temp", "registry", "filesystem"})


#: Every alias and function name without a hyphen that PowerShell 5.1 and 7
#: define with no profile loaded (Get-Alias, Get-Command -CommandType
#: Function), listed from both on Windows 11 for the verification pass of
#: Kelly's review of 9064712: md, tee, ii, mkdir and more run cmdlets, which
#: read a word before a colon as a drive.
_POWERSHELL_NAMES = frozenset({
    'ac', 'afterall', 'aftereach', 'asnp', 'beforeall', 'beforeeach', 'cat', 'cd', 'cd..',
    'cd\\', 'cd~', 'cfs', 'chdir', 'clc', 'clear', 'clhy', 'cli', 'clp', 'cls', 'clv', 'cnsn',
    'compare', 'configuration', 'context', 'copy', 'cp', 'cpi', 'cpp', 'curl', 'cvpa', 'dbp',
    'del', 'describe', 'diff', 'dir', 'dnsn', 'ebp', 'echo', 'epal', 'epcsv', 'epsn', 'erase',
    'etsn', 'exsn', 'fc', 'fhx', 'fl', 'foreach', 'ft', 'fw', 'gal', 'gbp', 'gc', 'gci', 'gcm',
    'gcs', 'gdr', 'gerr', 'ghy', 'gi', 'gjb', 'gl', 'gm', 'gmo', 'gp', 'gps', 'gpv', 'group',
    'gsn', 'gsnp', 'gsv', 'gu', 'gv', 'gwmi', 'h', 'help', 'history', 'icm', 'iex', 'ihy',
    'ii', 'importsystemmodules', 'in', 'inmodulescope', 'ipal', 'ipcsv', 'ipmo', 'ipsn', 'irm',
    'ise', 'it', 'iwmi', 'iwr', 'kill', 'lp', 'ls', 'man', 'md', 'measure', 'mi', 'mkdir',
    'mock', 'more', 'mount', 'move', 'mp', 'mv', 'nal', 'ndr', 'ni', 'nmo', 'npssc', 'nsn',
    'nv', 'ogv', 'oh', 'oss', 'pause', 'popd', 'prompt', 'ps', 'psconsolehostreadline',
    'pushd', 'pwd', 'r', 'rbp', 'rcjb', 'rcsn', 'rd', 'rdr', 'ren', 'ri', 'rjb', 'rm', 'rmdir',
    'rmo', 'rni', 'rnp', 'rp', 'rsn', 'rsnp', 'rujb', 'rv', 'rvpa', 'rwmi', 'safegetcommand',
    'sajb', 'sal', 'saps', 'sasv', 'sbp', 'sc', 'select', 'set', 'setup', 'shcm', 'should',
    'si', 'sl', 'sleep', 'sls', 'sort', 'sp', 'spjb', 'spps', 'spsv', 'start', 'sujb', 'sv',
    'swmi', 'tabexpansion2', 'tee', 'trcm', 'type', 'wget', 'where', 'wjb', 'write'})


def _powershell_own(name: str) -> bool:
    """Whether a command name is PowerShell's own (a cmdlet, Verb-Noun, or
    one of its aliases or functions), on Windows, where its paths may be any
    drive."""
    if not _windows():
        return False
    return (bool(re.fullmatch(r"[a-z]+-[a-z0-9]+", name)) or name in _ALIAS_OF
            or name in READ_ALIASES or name in _POWERSHELL_NAMES)


def _drive_word(text: str) -> bool:
    """Whether a word names a drive or provider: a disk's letter ("C:",
    "D:x"), one of PowerShell's own drives, or a provider-qualified path."""
    if ":" not in text:
        return False
    head = text.split(":", 1)[0].lower()
    return (len(head) == 1 or head in _PS_DRIVES or "::" in text or "\\" in head
            or "/" in head)
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
_GITHUB_CODE = frozenset({"hooks", "copilot", "workflows", "actions"})
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
#: What may make a hand-allowed command move the shell in a way not read,
#: outside plain quoted text (may_move).
_MOVING = re.compile(r"[$`(){}@&]")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")
#: A quoted string, read left to right so a quote inside the other kind of
#: quotes doesn't start one.
_QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
#: A provider-qualified path: Registry::HKLM\x, or with its module's name.
_PROVIDER = re.compile(r"(^|\\)(filesystem|registry|environment|alias|function|variable"
                       r"|certificate|wsman)::", re.I)
#: A word's first word, as written ("cd" .. is still cd in bash).
_FIRST_WORD = re.compile(r"""\s*('[^']*'|"[^"]*"|\S+)""")
#: Another place inside a word: a drive ("abc:C:\x", "abc:C:x") or a root after
#: a colon ("abc:/x"), but not a URL's "://" (Kelly's review of 5d378b4: a
#: helper program wrote to the place after the first colon).
_EMBEDDED_PLACE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|:[A-Za-z]:|:\\|:/(?!/)")
#: In a word holding punctuation the strict reading refuses (only reached in
#: plain quotes, for a program you chose): what may name a place outside when
#: the program splits it up ("out=../x", "a,/x"), which the check of the whole
#: word as one path wouldn't see.
_REACHES_OUT = re.compile(r"\.\.|~|(?:^|[^\w.-])[\\/]")
_WORD = re.compile(r"""'[^']*'|"[^"]*"|[^\s'"]+""")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
#: Commands longer than this always ask: the status bar says what was
#: allowed in full, and a long one is more than anyone reads.
MAX_COMMAND = 400


def _windows() -> bool:
    return os.name == "nt"


def _plain_quoted(text: str) -> bool:
    """Whether a quoted string is only text, which a program can only receive
    as an argument: single quotes, or double quotes with nothing the shell
    expands in them. bash reads ! there too (history), and PowerShell doesn't.
    (A backslash, bash's escape, doesn't get this far: _masked.)"""
    return text[0] == "'" or not re.search(r"[`$]" if _windows() else r"[`$!]", text)


def _masked(command: str) -> Optional[str]:
    """``command``, the same length, with what's inside each plain quoted
    string blanked, so what's left is what the shell itself acts on. None if
    it can't be read that way: a quote left open, anything not plain ASCII
    (PowerShell takes curly quotes for quotes), or outside Windows a backslash
    outside single quotes, since bash's escape changes where a quote ends:
    in ``"a\\" ; "; cd .. ; "\\"b"``, cd is a command there but looks quoted."""
    if _NON_ASCII.search(command):
        return None
    pieces, end = [], 0

    def readable(gap: str) -> bool:
        return not ("'" in gap or '"' in gap or (not _windows() and "\\" in gap))

    for match in _QUOTED.finditer(command):
        if not readable(command[end:match.start()]):
            return None
        text = match.group()
        if text[0] == '"' and not _windows() and "\\" in text:
            return None
        pieces.append(command[end:match.start()])
        pieces.append(text[0] + "_" * (len(text) - 2) + text[-1] if _plain_quoted(text) else text)
        end = match.end()
    if not readable(command[end:]):
        return None
    return "".join(pieces) + command[end:]


def _pieces(command: str, masked: str) -> list:
    """``command`` split as _SPLIT would, at the separators outside quotes:
    pieces and separators in turn, each piece as written."""
    pieces, end = [], 0
    for separator in _SPLIT.finditer(masked):
        pieces += [command[end:separator.start()], separator.group()]
        end = separator.end()
    return pieces + [command[end:]]


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
            if rule is None or (key == "allow" and _git_rule_unusable(rule)):
                # A git rule that names no git command ("git *") or one no
                # rule answers allows nothing: said, not kept silently
                # (Kelly's review of 9064712).
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
    # AppData and Startup wherever folder redirection put them, but for the
    # temp folder inside them (Kelly's review of 58c5f03).
    if not _under(target, _temp()) and any(
            _under(target, os.path.normcase(os.path.realpath(place)))
            for place in platform_paths.settings_dirs()):
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


def _temp() -> str:
    """The temp folder the shell uses, as a real, normalised path."""
    import tempfile
    return os.path.normcase(os.path.realpath(tempfile.gettempdir()))


def folder_too_broad(folder: str) -> bool:
    """A chat folder where nothing is answered for you: a whole drive, the
    home folder, a folder holding the home folder, or a guarded place itself
    (Kelly's review of #426: everything there would be "inside")."""
    # Written as a network path or a \\?\ path (\\localhost\C$\Users\me), a
    # folder can be the home folder in a spelling the checks below don't
    # compare (Kelly's review of 8345ddd). A mapped drive letter is fine.
    if _windows() and str(folder).replace("/", "\\").startswith("\\\\"):
        return True
    try:
        base = os.path.normcase(os.path.realpath(folder))
    except (OSError, ValueError, TypeError):
        return True
    # The temp folder itself holds every program's scratch files (Kelly's
    # review of 58c5f03); a folder made inside it is fine.
    return (os.path.dirname(base) == base or _under(_home(), base) or base == _temp()
            or base == os.path.normcase(os.path.join(_home(), "AppData", "Local", "Temp"))
            or _guarded(base))


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
    whole = os.path.basename(path.replace("\\", "/")).lower().rstrip(" .")
    # A file named HEAD is what makes a folder with objects and refs a git
    # repository, whose config git then reads and runs (Kelly's review of
    # 58c5f03: Auto's edits built one in sub, then a rule's git status ran
    # its core.fsmonitor). No file change makes one for you.
    if whole == "head":
        return False
    # Nor a git program of its own, which a PATH folder inside the chat's
    # folder would run in place of git (Kelly's review of 8345ddd).
    if whole in _GIT_NAMES or whole == "git.ps1":
        return False
    return whole.split(".")[0] not in _DEVICES


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
        # Whether it's written in quotes or not: "-o:C:\\x" is an option to the
        # program all the same (Kelly's review of 5d378b4).
        if text.startswith("-"):
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
        place = text[3:] if _windows() and _WINDOWS_DRIVE.match(text) else text
        if _EMBEDDED_PLACE.search(place) or ((_REFUSED.search(text) or _SPLIT.search(text))
                                              and _REACHES_OUT.search(place)):
            return False
        if ":" in text and not (_windows() and _WINDOWS_DRIVE.match(text)
                                and text.count(":") == 1):
            # Another drive or provider, or a file's stream. A word for a
            # program you chose (a rule's or a kept command's) may hold an
            # ordinary colon: "fix: typo", test:unit (Kelly's review of
            # 9064712). Auto's own commands stay strict.
            # A cmdlet or its alias reads one as a drive of any name (AD:,
            # IIS:) or a file's stream: only another program's word is
            # relaxed (Claude's review).
            if not long_options or _drive_word(text) or _powershell_own(name):
                return False
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


#: Why the last check asked, for the chat to say (ask_reason). Set and read on
#: the thread that checks a request.
_WHY = threading.local()


def ask_reason() -> str:
    """Why the check just made asks though something would have answered it,
    once, as a sentence to say, or ""."""
    reason = getattr(_WHY, "reason", "")
    _WHY.reason = ""
    return reason


def _asks_because(reason: str) -> bool:
    _WHY.reason = reason
    return False


def _git_asks(why: str) -> bool:
    return _asks_because(f"git asks here because {why}.")


_GIT_UNANSWERED = {
    platform_paths.GIT_MISSING: "The Chat Place couldn't find git",
    platform_paths.GIT_TIMED_OUT: f"git took more than {platform_paths.GIT_CHECK_SECONDS} seconds "
                                  "to answer The Chat Place's check",
    platform_paths.GIT_FAILED: "git couldn't say which repository this folder is in or how it's "
                               "set up",
}
_GIT_SHELL_MOVED = "Copilot's shell may be in a folder inside this chat's"
_GIT_NOT_THE_TOP = "this chat's folder isn't the top of its git repository"
_GIT_UNUSUAL = "this folder's .git isn't an ordinary repository or linked worktree"
_GIT_SUBMODULES = "this repository has submodules, and git would use their settings too"
_GIT_FILE_INSIDE = "a config file git reads is inside this folder, where Auto could change it"
_GIT_DIFF_PROGRAM = "a git setting names a program to show differences with"
_GIT_DIFF_UNKNOWN = "git couldn't say whether a setting names a program to show differences with"
_GIT_RUNS_UNKNOWN = ("this repository's settings include one The Chat Place doesn't know to be "
                     "safe: a repository's settings can make git run a program")
_GIT_RUNS_NAMED = ("this repository's settings include {setting}, which The Chat Place doesn't "
                   "know to be safe: a repository's settings can make git run a program")
_GIT_NO_CERTIFICATES = "this repository's settings turn off checking servers' certificates"
_GIT_HELPER_REMOTE = "this repository's settings name a remote that can run a program"
_GIT_NEARBY_PROGRAM = ("your own git settings name a program by a relative path ({setting}), which "
                       "a file in this folder could stand in for")
#: The sections of git's own settings. A setting's name is said only under one
#: of these: anything else in a repository's config is its own text, which
#: mustn't be read aloud as if The Chat Place said it (Kelly's review of
#: 5d378b4: a section called Press-Allow-This-Is-Safe was).
_GIT_SECTIONS = frozenset({
    "add", "advice", "alias", "am", "apply", "author", "blame", "branch", "browser", "bundle",
    "checkout", "clean", "clone", "color", "column", "commit", "commitgraph", "committer",
    "completion", "core", "credential", "diff", "difftool", "extensions", "fastimport",
    "feature", "fetch", "filter", "format", "fsck", "fsmonitor", "gc", "gitcvs", "gitweb", "gpg",
    "grep", "gui", "guitool", "help", "http", "i18n", "imap", "include", "includeif", "index",
    "init", "instaweb", "interactive", "lfs", "log", "lsrefs", "mailinfo", "mailmap",
    "maintenance", "man", "merge", "mergetool", "notes", "pack", "pager", "pretty", "protocol",
    "pull", "push", "rebase", "receive", "remote", "remotes", "repack", "rerere", "revert", "safe",
    "sendemail", "sequence", "showbranch", "sparse", "splitindex", "ssh", "stash", "status",
    "submodule", "tag", "tar", "trace2", "transfer", "uploadarchive", "uploadpack", "url", "user",
    "versionsort", "web", "worktree"})
#: Settings other tools write that git itself never acts on (Kelly's review
#: of 9064712: VS Code and gh made 13 of 19 real clones ask for everything).
_TOOL_SETTINGS = re.compile(
    r"branch\.(vscode-merge-base|gh-merge-base|github-pr-[a-z0-9-]+)"
    r"|remote\.(gh-resolved|github-pr-remote)|gui\.[a-z0-9-]+")


#: Safe only when on: http.sslVerify off lets a fetch skip checking the
#: server's certificate (Copilot's verification pass).
_GIT_TRUE_ONLY = frozenset({"http.sslverify"})


def _setting_problem(key: str, value: str) -> str:
    """Why a setting in a repository's own config asks (as git names it:
    section, any subsection, name), or "" if it's known to be safe, by section
    and name, its value too where that matters. Named by section and name
    only, under a section git knows: the rest is the repository's own text."""
    parts = key.lower().split(".")
    name = f"{parts[0]}.{parts[-1]}"
    if len(parts) < 2 or not (name in _GIT_SAFE_SETTINGS or _TOOL_SETTINGS.fullmatch(name)):
        known = (len(parts) >= 2 and parts[0] in _GIT_SECTIONS
                 and re.fullmatch(r"[a-z][a-z0-9]{0,30}", parts[-1]))
        return _GIT_RUNS_NAMED.format(setting=name) if known else _GIT_RUNS_UNKNOWN
    plain = value.strip()
    if name in ("remote.url", "remote.pushurl") and "::" in plain:
        return _GIT_HELPER_REMOTE  # ext::sh -c ... runs a program
    if name in _GIT_TRUE_ONLY and plain.lower() not in ("true", "yes", "on", "1"):
        return _GIT_NO_CERTIFICATES  # it lets a fetch your rule allows skip checking them
    if name in ("branch.remote", "branch.pushremote") and not re.fullmatch(
            r"[A-Za-z0-9._-]+", plain):
        return _GIT_HELPER_REMOTE  # a branch's remote may be a URL too: only a remote's name
    return ""


#: Settings of your own config that name a program git runs, or (hookspath) a
#: folder of them.
_GIT_PROGRAM_SETTING = re.compile(
    r"core\.(fsmonitor|pager|editor|sshcommand|gitproxy|askpass|hookspath)"
    r"|diff\.external|diff\..+\.(command|textconv)|difftool\..+\.(cmd|path)"
    r"|merge\..+\.driver|mergetool\..+\.(cmd|path)|filter\..+\.(clean|smudge|process)"
    r"|credential\.(.+\.)?helper|gpg\.(.+\.)?program|sequence\.editor|pager\..+|alias\..+"
    r"|browser\..+\.(cmd|path)|man\..+\.(cmd|path)")
#: A word starting "./" or "../", anywhere in a command line.
_DOT_PATH = re.compile(r"""(?:^|[\s"'=;&|(])\.{1,2}[\\/]""")


def _full_path(word: str) -> bool:
    return (word.startswith(("/", "\\", "~", "$", "%")) or bool(_WINDOWS_DRIVE.match(word))
            or os.path.isabs(word))


def _names_nearby_program(key: str, value: str) -> bool:
    """Whether a setting of your own git config names a program by a relative
    path. git runs that from the top of the repository, so a file Auto
    changes could be what it runs (Kelly's review of 8345ddd). A program's
    bare name (code --wait, delta) is found on PATH and a full path is where
    it is: both stay yours, as what your config sets always has."""
    name, text = key.lower(), value.strip()
    if not _GIT_PROGRAM_SETTING.fullmatch(name):
        return False
    if name.startswith("alias.") and not text.startswith("!"):
        return False  # another git command, not a program
    text = text.lstrip("!").strip()
    if name == "core.hookspath":
        return bool(text) and not _full_path(text.strip("\"'"))
    if _DOT_PATH.search(text):
        return True
    first = _FIRST_WORD.match(text)
    word = _unquote(first.group(1)) if first else ""
    return ("/" in word or "\\" in word) and not _full_path(word)


def _config_files_of(where: str, origin: str, key: str, value: str) -> list:
    """The file a setting came from, and any file it includes, there yet or
    not: an empty or missing one, which Auto could then write, holds no
    setting for git to list (Claude's verification pass)."""
    if not origin.startswith("file:"):
        return []
    # git names the repository's own relative to where it ran.
    source = os.path.join(where, origin[len("file:"):])
    files = [source]
    name = key.lower()
    if value and (name == "include.path" or (name.startswith("includeif.")
                                             and name.endswith(".path"))):
        files.append(os.path.join(os.path.dirname(source), os.path.expanduser(value)))
    return files


def _git_config_safe(locations, gitdir: str, common: str) -> bool:
    """Every setting git really uses from where the shell is, asked of git
    (Kelly's review of 5d378b4: a commondir with a leading space and an empty
    .git each hid the real config from a reading of the files here). The
    repository's own must be known to be safe (_setting_problem). Your own
    (global and system, and any given through the environment) are yours, but
    for a program named by a relative path. And no config file git reads or
    an include names may be inside the folders the shell may be in, where
    Auto's edits could write one (Kelly's review of 8345ddd), other than the
    repository's own, which Auto never changes."""
    where = locations[0]
    try:
        settings = platform_paths.git_settings(where)
        places = [_normal(place) for place in locations]
        own = {os.path.join(common, "config"), os.path.join(gitdir, "config.worktree")}
        for scope, origin, key, value in settings:
            for path in _config_files_of(where, origin, key, value):
                real = _normal(path)
                if real not in own and any(_under(real, place) for place in places):
                    return _git_asks(_GIT_FILE_INSIDE)
            if scope in ("local", "worktree"):
                problem = _setting_problem(key, value)
                if problem:
                    return _git_asks(problem)
            elif scope not in ("system", "global", "command"):
                return _git_asks(_GIT_UNUSUAL)
            elif _names_nearby_program(key, value):
                parts = key.lower().split(".")
                return _git_asks(_GIT_NEARBY_PROGRAM.format(
                    setting=f"{parts[0]}.{parts[-1]}"[:60]))
    except platform_paths.GitUnanswered as exc:
        return _git_asks(_GIT_UNANSWERED[exc.kind])
    except (OSError, ValueError):
        return _git_asks(_GIT_UNANSWERED[platform_paths.GIT_FAILED])
    return True


#: The only settings a repository's own config may hold for git to be
#: answered for you: what git init, git clone and an ordinary checkout write,
#: and a few more that change nothing but how git shows or stores things.
#: Anything else asks. A list of the ones known to run a program kept missing
#: some (Claude's review of the round answering Kelly's review of 8345ddd:
#: remote.<name>.uploadpack ran on git fetch, merge.<name>.driver on git
#: merge). A subsection's name is left out: [remote "origin"] url is
#: remote.url.
_GIT_SAFE_SETTINGS = frozenset({
    "core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
    "core.symlinks", "core.ignorecase", "core.precomposeunicode", "core.autocrlf",
    "core.safecrlf", "core.eol", "core.longpaths", "core.quotepath", "core.checkstat",
    "core.trustctime", "core.untrackedcache", "core.splitindex", "core.commitgraph",
    "core.multipackindex", "core.compression", "core.abbrev", "core.sharedrepository",
    "remote.url", "remote.pushurl", "remote.fetch", "remote.push", "remote.prune",
    "remote.tagopt", "remote.mirror", "remote.skipdefaultupdate",
    "branch.remote", "branch.merge", "branch.rebase", "branch.pushremote",
    "branch.description", "user.name", "user.email", "pull.rebase", "pull.ff",
    "push.default", "push.autosetupremote", "fetch.prune", "init.defaultbranch",
    "extensions.worktreeconfig", "extensions.objectformat", "extensions.refstorage",
    "gc.auto", "index.version", "feature.manyfiles", "color.ui", "merge.ff",
    "merge.conflictstyle", "rerere.enabled", "lfs.repositoryformatversion",
    "diff.renames", "diff.algorithm", "diff.colormoved", "rebase.autostash", "core.fscache",
    "core.preloadindex", "submodule.active", "lfs.locksverify", "lfs.access", "http.sslverify",
    "safe.directory"})


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
    return _git_checks(folder, locations, sub in _GIT_SHOWS_CHANGES)


def _git_checks(folder: str, locations, shows_changes: bool) -> bool:
    """What every git command answered for you must pass, by Auto, a rule or a
    kept command: the shell in the chat's own repository, its settings naming
    no program, and, for one that shows differences, no diff driver: a
    repository's .gitattributes may choose one, from your own git config too
    (Copilot's review of Auto). git status shows none."""
    found = _git_repository_here(folder, locations)
    if found is None or not _git_config_safe(locations, *found):
        return False
    if not shows_changes:
        return True
    named = platform_paths.git_names_diff_program(folder)
    if named is False:
        return True
    return _git_asks(_GIT_DIFF_PROGRAM if named else _GIT_DIFF_UNKNOWN)


#: git options a rule may never add, however it's written: ones that write a
#: file (--output, format-patch's -o), run a program (--ext-diff, --textconv,
#: --exec, rebase's -x, grep's -O, a tool or editor), or point git at other
#: settings or another repository (Kelly's review of 58c5f03: a "git log *"
#: rule let --ext-diff and --output through).
_GIT_RULE_REFUSED = ("output", "output-directory", "ext-diff", "textconv", "exec",
                     "exec-path", "upload-pack", "receive-pack", "config", "config-env",
                     "git-dir", "work-tree", "open-files-in-pager", "tool", "extcmd",
                     "template", "command", "pager", "editor", "sequence-editor", "namespace",
                     "super-prefix", "strategy", "strategy-option", "run", "include", "hook",
                     "gpg", "gpg-sign", "ssh", "filter", "attr-source", "unsafe-paths",
                     "directory")
#: Short options that write a file (-o), run a program (-x, grep's -O) or
#: point git elsewhere (-C), with their value attached or not, alone or among
#: other letters (-iOcalc): git reads -Ocalc as -O calc (Claude's review of
#: the git rules).
_GIT_RULE_LETTERS = frozenset("oxOC")


def _git_rule_option_refused(text: str) -> bool:
    """Whether an option a git rule can't take: any letter of
    _GIT_RULE_LETTERS among a short option's letters, or a long option that
    is, starts or (git takes any unambiguous start: --exe is --exec) begins
    one of _GIT_RULE_REFUSED."""
    if not text.startswith("--"):
        return bool(set(text[1:]) & _GIT_RULE_LETTERS)
    name = text[2:].split("=")[0].lower()
    # --remote (git archive's, which reaches a transport) or a start of it,
    # but not --remotes, which only lists (Claude's review).
    if name != "remotes" and len(name) >= 3 and "remote".startswith(name):
        return True
    return any(name.startswith(refused) or (len(name) >= 1 and refused.startswith(name))
               for refused in _GIT_RULE_REFUSED)
#: git commands no rule ever answers, whatever it names: they change git's own
#: settings (config, yours as well as the repository's), run a program they're
#: given (bisect run, filter-branch's filters, hook run, difftool), make or
#: reach another repository or working tree (init, clone, worktree,
#: submodule), or reach the network or a browser in ways a rule can't see
#: (Kelly's review of 8345ddd: a "git *" rule ran git bisect run and wrote
#: core.fsmonitor into the global config).
_GIT_RULE_NEVER = frozenset({
    "config", "bisect", "filter-branch", "filter-repo", "hook", "submodule", "worktree",
    "difftool", "mergetool", "instaweb", "web--browse", "daemon", "credential",
    "credential-store", "credential-cache", "send-email", "imap-send", "svn", "p4",
    "archimport", "cvsimport", "cvsserver", "cvsexportcommit", "quiltimport", "fast-import",
    "update-index", "update-ref", "symbolic-ref", "init", "clone", "maintenance",
    "fsmonitor--daemon", "upload-pack", "receive-pack", "upload-archive", "shell",
    "http-backend", "help", "gui", "citool", "bugreport", "remote-ext", "var", "replace",
    "notes", "sparse-checkout", "scalar", "merge-index", "merge-one-file",
    "update-git-for-windows", "lfs", "credential-manager"})
#: git commands that may show differences, so run a diff driver or textconv.
_GIT_DIFFS = _GIT_SHOWS_CHANGES | {"format-patch", "range-diff", "whatchanged", "stash", "grep",
                                   "add", "cherry", "difftool", "mergetool"}


#: How git may be named: each gets git's checks (Kelly's review of 8345ddd:
#: a rule naming git.cmd skipped them).
_GIT_NAMES = frozenset({"git", "git.exe", "git.cmd", "git.bat", "git.com"})


def _git_rule_unusable(rule) -> bool:
    """Whether an allow rule for git can never allow anything: it names no
    git command, or names one no rule answers (_GIT_RULE_NEVER)."""
    if not rule.words or rule.words[0].lower() not in _GIT_NAMES:
        return False
    named = [w.lower() for w in rule.words[1:] if not w.startswith("-")]
    return not named or named[0] in _GIT_RULE_NEVER


def _git_rule_ok(words, folder: str, locations, rule=None) -> bool:
    """A git command your rule allows gets the checks Auto's git does
    (Kelly's review of 58c5f03: a rule's git status ran a repository's
    core.fsmonitor): the shell in the chat's own repository, its settings
    naming no program, no submodules, and no diff program for one that shows
    differences. Its options are yours, except ones that write, run a
    program or point git elsewhere (_GIT_RULE_REFUSED). Options before its
    command, other than the two Copilot adds, ask."""
    plain = [_unquote(w) for w in words]
    index = 1
    while index < len(plain) and plain[index].lower() in ("--no-pager", "--no-optional-locks"):
        index += 1
    if index >= len(plain) or plain[index].startswith("-"):
        return False
    sub = plain[index].lower()
    if sub in _GIT_RULE_NEVER:
        return False
    # A rule names the git command it allows ("git commit *"): "git *" alone
    # allows none, so it can't reach one nobody thought of (Tim's choice).
    if rule is not None:
        named = [w.lower() for w in rule.words[1:] if not w.startswith("-")]
        if not named or named[0] != sub:
            return False
    messages = _commit_messages(words)
    for place, text in enumerate(plain[index + 1:], index + 1):
        if text == "--":
            break
        if (place not in messages and text.startswith("-") and len(text) > 1
                and _git_rule_option_refused(text)):
            return False
    return _git_checks(folder, locations, sub in _GIT_DIFFS)


#: git commit's options before -m that take no value of their own: only after
#: these is the next -m sure to be the option, not another option's value
#: (git commit -F -m "x" reads a message file named -m).
_GIT_COMMIT_FLAGS = frozenset({"-a", "-s", "-q", "-n", "-v", "--all", "--amend", "--signoff",
                               "--quiet", "--verbose", "--no-verify", "--allow-empty",
                               "--allow-empty-message", "--no-edit"})
_GIT_MESSAGE_OPTION = re.compile(r"-[asqnv]*m|--message")


def _commit_messages(words) -> set:
    """Which of a command's words are git commit's message text (-m, -am,
    --message). It never reaches a program as a path, and git stores it as it
    is, so a message can say "src/foo.py: fix" or "wait..." without asking
    (Kelly's review of 5d378b4). Read only as far as what's sure: anything
    else from there on is read as a path as usual."""
    plain = [_unquote(w) for w in words]
    if not plain or plain[0].lower() not in _GIT_NAMES:
        return set()
    index = 1
    while index < len(plain) and plain[index].lower() in ("--no-pager", "--no-optional-locks"):
        index += 1
    if index >= len(plain) or plain[index].lower() != "commit":
        return set()
    index += 1
    messages = set()
    while index < len(plain):
        if _GIT_MESSAGE_OPTION.fullmatch(plain[index]):
            messages.add(index + 1)
            index += 2
        elif plain[index] in _GIT_COMMIT_FLAGS or re.fullmatch(r"-[asqnv]+", plain[index]):
            index += 1
        else:
            break
    return messages


def _normal(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _one_line(path: str) -> Optional[str]:
    """A small file's one line of text, without its line ending; else None."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        text = handle.read(4096)
    found = re.fullmatch(r"([^\r\n]+)[\r\n]*", text)
    return found.group(1) if found else None


def _linked_worktree(base: str, dot: str, gitdir: str, common: str) -> bool:
    """Whether git's answer is a normal linked worktree of a repository
    elsewhere, as git worktree add makes one: git folder
    <common>/worktrees/<name>, the chat folder's .git a file naming it, and
    that folder naming this .git back. Its config is judged as git reads it,
    wherever it is. But each folder must be outside the chat's, where Auto
    can't change them, and a .git file that says anything else (a crafted one,
    a submodule's, git init --separate-git-dir) asks."""
    if os.path.dirname(gitdir) != os.path.join(common, "worktrees"):
        return False
    if os.path.islink(dot) or not os.path.isfile(dot):
        return False
    if _under(gitdir, base) or _under(common, base) or _under(base, common):
        return False
    pointer = _one_line(dot)
    if pointer is None or not pointer.startswith("gitdir: "):
        return False
    if _normal(os.path.join(base, pointer[len("gitdir: "):])) != gitdir:
        return False
    back = _one_line(os.path.join(gitdir, "gitdir"))
    return back is not None and _normal(os.path.join(gitdir, back)) == dot


def _git_repository_here(folder: str, locations) -> Optional[tuple]:
    """(git folder, common git folder) if git, asked from the shell's folder,
    says it's in the chat's own repository at its top: either that folder's
    plain .git folder, or a normal linked worktree (_linked_worktree). None,
    with why, for anything else. Elsewhere git could find one Copilot made: a
    folder with HEAD, objects and refs in it is a repository to git, and
    Auto's file changes could write those; an empty .git is passed over for a
    repository above (Kelly's review of 5d378b4); a commondir file can name
    another git folder altogether (the same review)."""
    try:
        base = _normal(folder)
        if any(_normal(where) != base for where in locations):
            _git_asks(_GIT_SHELL_MOVED)
            return None
        top, gitdir, common = (_normal(path) for path in platform_paths.git_where(folder))
        dot = os.path.join(base, ".git")
        if top != base:
            _git_asks(_GIT_NOT_THE_TOP)
            return None
        plain = (gitdir == dot and common == dot
                 and os.path.isdir(dot) and not os.path.islink(dot))
        if not plain and not _linked_worktree(base, dot, gitdir, common):
            _git_asks(_GIT_UNUSUAL)
            return None
        # git status and diff read each submodule's own config too, which
        # isn't checked here (Claude's review of the merged fixes): a repository
        # with submodules asks. Also one whose index holds a submodule however
        # it got there: git status and diff go into it and run its own config,
        # .gitmodules or not (Claude's review of the round answering Kelly's
        # review of 9064712: an embedded sub/.git's core.fsmonitor ran).
        if (os.path.lexists(os.path.join(folder, ".gitmodules"))
                or os.path.lexists(os.path.join(gitdir, "modules"))
                or os.path.lexists(os.path.join(common, "modules"))
                or platform_paths.git_has_submodules(folder) is not False):
            _git_asks(_GIT_SUBMODULES)
            return None
    except platform_paths.GitUnanswered as exc:
        _git_asks(_GIT_UNANSWERED[exc.kind])
        return None
    except (OSError, ValueError):
        _git_asks(_GIT_UNANSWERED[platform_paths.GIT_FAILED])
        return None
    return gitdir, common


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
#: Said when a rule would allow a command but it's longer than MAX_COMMAND.
TOO_LONG_ASKS = "This asks since the command is too long to be answered for you."
#: Said when a rule or kept command names the command, but the strict reading
#: won't answer all of it (a path outside, a redirect, more after it).
CANT_FOLLOW_ASKS = ("This asks since The Chat Place can't be sure all of it stays inside this "
                    "chat's folder.")


@dataclass(frozen=True)
class Answer:
    """A request The Chat Place may answer for you: what allowed it (AUTO,
    RULE or CHAT), and the folders Copilot's shell may be in after it."""
    by: str
    locations: tuple = ()


def _parts(command: str):
    """[(words, whether a pipe brought it what it reads, whether it holds
    punctuation only a program you chose may have)], or None if the command
    can't be read: too long, empty, with a refused character outside plain
    quotes, a quote inside a word, or a pipe carried on to the next line."""
    if not command or not command.strip() or len(command) > MAX_COMMAND:
        return None
    masked = _masked(command)
    if masked is None or _REFUSED.search(masked.replace("&&", " ; ")):
        return None
    # bash and zsh read a backslash as an escape: sub/\.\./x is ../x there.
    # On Windows it's only the path separator.
    if not _windows() and "\\" in masked:
        return None
    # PowerShell carries a pipe on to the next line: "gci sub |" then a
    # line "Set-Content x" is one pipeline (Claude's review of #426).
    if re.search(r"\|[ \t]*[\r\n]", masked):
        return None
    pieces = _pieces(command, masked)
    parts, piped = [], False
    for index, piece in enumerate(pieces):
        if index % 2:
            piped = piece == "|"
        elif piece.strip():
            words = _words(piece.strip())
            if not words:
                return None
            # All that's strict about a part: nothing _REFUSED, or that used
            # to split it, even in quotes (Kelly's review of 5d378b4: only a
            # program you chose gets the quoted text it's handed).
            punctuated = bool(_REFUSED.search(piece) or _SPLIT.search(piece)
                              or (not _windows() and "\\" in piece))
            parts.append((words, piped, punctuated))
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
    if plain and plain[0].lower() in _GIT_NAMES:
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


def _part(words, piped: bool, folder: str, locations, policy: Policy, *, exact: bool,
          punctuated: bool = False):
    """(what allows one part of a command, or None; the folders after it).
    "move" and "filter" go along with what allows the rest. ``punctuated``:
    it holds punctuation only inside plain quotes. That's for a rule's or a
    kept command's program, which only receives it as an argument, unless the
    program is PowerShell's own (a cmdlet reads more into a word), and never
    for Auto, a filter after a pipe or a change of folder (Kelly's review of
    5d378b4: every other real commit message asked under git commit *)."""
    name = _unquote(words[0]).lower()
    if _asked(words, policy):
        return None, locations
    if punctuated and (piped or _powershell_own(name) or name in _LOCATION
                       or _REFUSED.search(words[0])):
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
              and (name not in _GIT_NAMES or _git_ok(words, folder, locations)))
        return (CHAT if ok else None), locations
    if policy.auto and not punctuated:
        reading = (name in READ_CMDLETS or name in READ_ALIASES) if _windows() \
            else name in POSIX_READ
        if reading and _paths_ok(name, words[1:], folder, locations,
                                 letters=True, long_options=False):
            return AUTO, locations
        if name == "git" and _git_ok(words, folder, locations):
            return AUTO, locations
    for rule in policy.allow:
        if rule.matches(words) and not may_move(" ".join(words)):
            messages = _commit_messages(words)
            extra = [word for place, word in enumerate(words)
                     if place >= len(rule.words) and place not in messages]
            if (_paths_ok(name, extra, folder, locations, letters=False, long_options=True)
                    and (name not in _GIT_NAMES
                         or _git_rule_ok(words, folder, locations, rule))):
                return RULE, locations
    return None, locations


def check_command(command: str, folder: str, policy: Policy,
                  locations=None) -> Optional[Answer]:
    """Whether a command may be answered for you, and the folders Copilot's
    shell may be in after it; None if it asks, with why (ask_reason) if your
    rule or a kept command names it, or it sounds like that stopped working."""
    answer = _checked(command, folder, policy, locations)
    if answer is None and command and not getattr(_WHY, "reason", ""):
        words = command.split()
        named = command.strip() in policy.exact or any(
            rule.matches(words[:len(rule.words)]) for rule in policy.allow)
        if named and not any(rule.matches(words, as_prefix=True) for rule in policy.ask):
            _asks_because(TOO_LONG_ASKS if len(command) > MAX_COMMAND else CANT_FOLLOW_ASKS)
    return answer


def _checked(command: str, folder: str, policy: Policy, locations) -> Optional[Answer]:
    if not folder or not policy.any:
        return None
    parts = _parts(command)
    if parts is None:
        return None
    places = tuple(locations) if locations else (folder,)
    exact = command.strip() in policy.exact
    found = set()
    for words, piped, punctuated in parts:
        # After a part your rule or Allow for This Chat allows, only what a
        # pipe hands text to: the rest was checked against the disk as it was
        # before that part ran, and that part may change it (Kelly's review of
        # #426: a move, then a path through a link already in what moved).
        if (RULE in found or CHAT in found) and not piped:
            return None
        by, places = _part(words, piped, folder, places, policy, exact=exact,
                           punctuated=punctuated)
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


#: What starts a command in bash without being the command: "time cd ..",
#: "! cd ..", and the words of a compound command, one split at its ";".
_KEYWORDS = frozenset({"time", "if", "then", "elif", "else", "while", "until", "do", "for",
                       "case", "coproc"})


def _moves_by_path(name: str) -> bool:
    """Whether a program named by a path may run in the shell itself. On
    Windows PowerShell may find a .ps1 for any path without an extension of a
    real program. Elsewhere a path is a child process, which can't move the
    shell (.venv/bin/python), though a ".." gets no such benefit of the doubt."""
    if _windows():
        return name.startswith(".") or "\\" in name or "/" in name
    return "\\" in name or ".." in name.split("/")


def _moving_word(text: str) -> bool:
    """A word that, given to one of PowerShell's own commands, reaches a drive
    other than a disk's (alias:\\ls, env:PATH, or a disk's current folder, C:x)
    or goes through a provider: writing there changes what later commands
    mean. test:unit isn't one, nor x.py::test_y."""
    head = text.split(":", 1)[0].lower()
    if ":" in text and (head in _PS_DRIVES or (len(head) == 1
                                               and not _WINDOWS_DRIVE.match(text))):
        return True
    return bool(_PROVIDER.search(text))


def may_move(command: str) -> bool:
    """Whether a command you allowed yourself may have moved Copilot's shell
    somewhere not followed here: a change of folder, a script (which runs in
    the same session), or text run as code. Erring toward yes, but reading
    quotes: what's in plain quotes is only an argument, and a program that
    isn't the shell's own runs as a child process, which can't move it (Kelly's
    review of 5d378b4: one allowed git commit -m "fix(parser): x" made every
    later command ask)."""
    if not command or len(command) > MAX_COMMAND * 10:
        return True
    text = command.replace("2>&1", " ")  # git and npm output, not a redirect to read
    masked = _masked(text)
    if masked is None:
        return True
    for piece in _pieces(text, masked)[::2]:
        if not piece.strip():
            continue
        first = _FIRST_WORD.match(piece)
        rest = _QUOTED.sub(lambda quoted: "Q" if _plain_quoted(quoted.group()) else quoted.group(),
                           piece[first.end():])
        shown = piece[:first.end()] + rest
        if _MOVING.search(shown):
            return True
        words = _words(shown.strip())
        if not words:
            return True
        raw = _unquote(words[0])
        name = raw.lower()
        # PowerShell's own cd.. and cd\\ are functions, one word each.
        if (name in _MOVERS or name in _KEYWORDS or re.match(r"(cd|chdir|sl)\W", name)
                or name[:1] in ("", "[", "!")):
            return True
        if name.endswith((".ps1", ".psm1", ".psd1", ".sh", ".bash", ".zsh")):
            return True
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*\+?=", raw):  # PATH=sub ls
            return True
        program = name.endswith((".exe", ".com")) and ".." not in name
        if not program and (re.match(r"[a-z]:", name) or _moves_by_path(name)):  # D: changes drive
            return True
        if program or not _powershell_own(name):
            continue
        # Its words as they are: Set-Content "env:PATH" reaches the drive
        # just the same, in quotes or not.
        said = _words(piece.strip())
        if said is None or any(_moving_word(_unquote(word)) for word in said):
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
