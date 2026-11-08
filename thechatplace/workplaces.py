"""Where a new session works (#154): the folders sessions have used, a
GitHub repository (cloned under the GitHub folder when it isn't there yet),
and a new git worktree on a branch of its own.

Plain data and subprocess calls, no wx, so it can be tested on its own.
Every ``git`` and ``gh`` call is an argument list with no shell, and names
that reach one (a repository, a branch) are checked first.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from . import platform_paths

#: How many recent folders New Session offers unless Settings says otherwise
#: (#215), and the most Settings allows.
RECENT_LIMIT = 20
RECENT_MAX = 100
#: How many of your repositories From GitHub lists.
REPO_LIMIT = 300

#: GitHub's own rule for owner and repository names, near enough: letters,
#: digits, "-", "_" and "."; never starting with "-" (it would read as an
#: option) or being "." or "..".
_PART = r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}"
_REPO = re.compile(rf"^({_PART})/({_PART})$")
_REPO_URL = re.compile(
    rf"^(?:https?://(?:www\.)?github\.com/|git@github\.com:)({_PART})/({_PART}?)(?:\.git)?/?$",
    re.IGNORECASE)


class ToolError(Exception):
    """A git or gh step that failed, with a message to show as it is."""


# -- recent folders -----------------------------------------------------------------------

def repo_of_worktree(folder: str) -> str:
    """The repository a folder in ``<repo>/.claude/worktrees/<name>``
    belongs to (at any depth, as the session list reads it), or the folder
    itself if it isn't in one of those."""
    match = re.search(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+", folder)
    return folder[:match.start()] if match and match.start() > 0 else folder


def folder_key(folder: str) -> str:
    """The same folder however it's written: letter case (on Windows) and
    separators don't matter."""
    return os.path.normcase(os.path.normpath(folder))


def recent_folders(sessions: Iterable, limit: int = RECENT_LIMIT,
                   exists: Callable[[str], bool] = os.path.isdir,
                   removed: Optional["RemovedFolders"] = None) -> List[str]:
    """The folders ``sessions`` (SessionInfo) have worked in, most recently
    used first, each once. A worktree counts as its repository, a Cowork
    session (whose folder is its own "outputs" folder) not at all, and a
    folder that no longer exists is left out. So is one taken off the list
    (``removed``, #215) that no session has worked in since."""
    if limit <= 0:
        return []
    ordered = sorted((s for s in sessions if not getattr(s, "cowork", False) and s.cwd),
                     key=lambda s: s.last_activity_ms or 0, reverse=True)
    seen = set()
    folders = []
    for info in ordered:
        folder = repo_of_worktree(info.cwd.rstrip("\\/") or info.cwd)
        key = folder_key(folder)
        if key in seen:
            continue
        seen.add(key)
        # Newest first, so this is the folder's last use.
        if removed is not None and removed.hides(folder, info.last_activity_ms or 0):
            continue
        if exists(folder):
            folders.append(folder)
            if len(folders) >= limit:
                break
    return folders


class RemovedFolders:
    """The folders taken off New Session's recent list (#215), in
    ``<app data>/recent_folders.json``. The list is made from the sessions,
    which The Chat Place never changes, so a folder is taken off by noting
    when: it comes back once a session works in it again. Clearing the list
    notes one time for every folder."""
    FILE = "recent_folders.json"

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / self.FILE
        self.cleared_ms = 0
        self._removed = {}  # folder_key -> when it was taken off, in ms
        self.load_error = ""
        self.load()

    def load(self) -> None:
        self.cleared_ms, self._removed, self.load_error = 0, {}, ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read your recent folders ({self.path}): {exc}"
            return
        removed = raw.get("removed") if isinstance(raw, dict) else None
        cleared = raw.get("cleared_ms", 0) if isinstance(raw, dict) else None
        if not isinstance(removed, dict) or not _is_ms(cleared):
            self.load_error = (f"Your recent folders file ({self.path}) isn't in the "
                               "expected format, so The Chat Place won't change it.")
            return
        self.cleared_ms = cleared
        self._removed = {k: v for k, v in removed.items() if isinstance(k, str) and k
                         and _is_ms(v)}

    def hides(self, folder: str, last_used_ms: int) -> bool:
        """Whether ``folder``, last used at ``last_used_ms``, stays off the list."""
        since = max(self.cleared_ms, self._removed.get(folder_key(folder), 0))
        return since > 0 and last_used_ms <= since

    def remove(self, folder: str, now_ms: Optional[int] = None) -> None:
        removed = dict(self._removed)
        removed[folder_key(folder)] = _now_ms() if now_ms is None else now_ms
        self._save(self.cleared_ms, removed)

    def clear(self, now_ms: Optional[int] = None) -> None:
        # One time covers every folder, so the ones taken off singly go.
        self._save(_now_ms() if now_ms is None else now_ms, {})

    def _save(self, cleared_ms: int, removed: dict) -> None:
        """Save, then keep: a change that can't be saved isn't made."""
        if self.load_error:
            raise OSError(f"Not saving over the unreadable {self.path}.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="recent_folders-", suffix=".tmp",
                                   dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "cleared_ms": cleared_ms, "removed": removed},
                          handle, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.cleared_ms, self._removed = cleared_ms, removed


def _is_ms(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _now_ms() -> int:
    return int(time.time() * 1000)


# -- running git and gh -------------------------------------------------------------------

def _environment() -> dict:
    """Never stop to ask for a password or a passphrase: there is no
    terminal to answer it in, and the call would hang. gh runs git itself,
    so the folders git and gh were found in go first on the PATH: an app
    started from the Finder has no Homebrew folder on it."""
    env = dict(os.environ)
    env["PATH"] = _tool_path(env.get("PATH", ""))
    env.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1", GIT_ASKPASS="",
               SSH_ASKPASS="", GCM_INTERACTIVE="never", GH_NO_UPDATE_NOTIFIER="1")
    return env


_TOOL_FOLDERS = None


def _tool_path(path: str) -> str:
    """``path`` with the folders git and gh are in added at the front, if
    they aren't on it already. Found once: every git call asks."""
    global _TOOL_FOLDERS
    if _TOOL_FOLDERS is None:
        _TOOL_FOLDERS = list(dict.fromkeys(os.path.dirname(t) for t in (find_git(), find_gh())
                                           if t))
    present = {os.path.normcase(p.rstrip("\\/")) for p in path.split(os.pathsep) if p}
    missing = [f for f in _TOOL_FOLDERS if os.path.normcase(f.rstrip("\\/")) not in present]
    return os.pathsep.join([*missing, path]) if missing else path


def _run(argv: List[str], cwd: Optional[str] = None, timeout: float = 30) -> str:
    """stdout, or ToolError with what the program said."""
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL, env=_environment(),
                              creationflags=platform_paths.hidden_window_flags())
    except subprocess.TimeoutExpired:
        raise ToolError(f"{Path(argv[0]).stem} took too long to answer.") from None
    except OSError as exc:
        raise ToolError(f"Couldn't run {Path(argv[0]).stem}: {exc}") from None
    if done.returncode != 0:
        said = " ".join((done.stderr or done.stdout or "").split())
        raise ToolError(said or f"{Path(argv[0]).stem} failed (exit {done.returncode}).")
    return done.stdout


def find_git() -> Optional[str]:
    return platform_paths.find_tool("git")


def find_gh() -> Optional[str]:
    return platform_paths.find_tool("gh")


def _gh(gh: str) -> List[str]:
    """How ``gh`` is started (the tests start a fake one through Python)."""
    return [gh]


GH_MISSING = ("The GitHub CLI (gh) isn't installed, or The Chat Place can't find it. Install it "
              "from https://cli.github.com, run gh auth login once, then try again. You can "
              "still type a folder or use Browse.")


# -- GitHub repositories ------------------------------------------------------------------

@dataclass
class GitHubRepo:
    name: str               # owner/name
    description: str = ""
    private: bool = False

    @property
    def short_name(self) -> str:
        return self.name.split("/", 1)[1]

    def row(self) -> str:
        text = f"{self.name}{', private' if self.private else ''}"
        description = " ".join(self.description.split())
        if len(description) > 160:
            description = description[:159] + "…"
        return f"{text}: {description}" if description else text


def parse_repo(text: str) -> Optional[str]:
    """``owner/name`` from what was typed (``owner/name`` or a GitHub
    address), or None if it isn't one."""
    text = (text or "").strip()
    for pattern in (_REPO, _REPO_URL):
        match = pattern.match(text)
        if match:
            owner, name = match.group(1), match.group(2)
            if name.lower().endswith(".git"):
                name = name[:-4]
            if owner in (".", "..") or name in (".", "..", ""):
                return None
            return f"{owner}/{name}"
    return None


def list_github_repos(gh: str, limit: int = REPO_LIMIT) -> List[GitHubRepo]:
    """Your repositories, as ``gh repo list`` gives them (most recently
    pushed first). Uses gh's own sign-in; ToolError if it can't."""
    out = _run([*_gh(gh), "repo", "list", "--limit", str(limit),
                "--json", "nameWithOwner,description,isPrivate"], timeout=60)
    try:
        items = json.loads(out or "[]")
    except ValueError:
        raise ToolError("gh gave an answer The Chat Place couldn't read.") from None
    repos = []
    for item in items if isinstance(items, list) else []:
        name = parse_repo(str(item.get("nameWithOwner") or "")) if isinstance(item, dict) else None
        if name:
            repos.append(GitHubRepo(name, str(item.get("description") or ""),
                                    bool(item.get("isPrivate"))))
    return repos


def clone_target(root: Path, repo: str) -> Path:
    """Where ``repo`` is cloned: its own name under the GitHub folder."""
    return Path(root) / repo.split("/", 1)[1]


_REMOTE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)


def is_clone_of(git: str, folder: Path, repo: str) -> bool:
    """Whether ``folder`` is a clone of ``repo``: a repository of its own
    (not a folder inside some other one) whose origin is ``repo``."""
    try:
        top = _run([git, "-C", str(folder), "rev-parse", "--show-toplevel"], timeout=10).strip()
        if not top or os.path.normcase(os.path.realpath(top)) != \
                os.path.normcase(os.path.realpath(folder)):
            return False
        url = _run([git, "-C", str(folder), "remote", "get-url", "origin"], timeout=10).strip()
    except ToolError:
        return False
    match = _REMOTE.search(url)
    return bool(match) and f"{match.group(1)}/{match.group(2)}".lower() == repo.lower()


class Clone:
    """``gh repo clone`` running in the background, which can be cancelled.

    It clones into a folder of its own beside the target (``.thechatplace-
    clone-<random>``) and renames that to the target only once the clone
    has worked, so nothing it cleans up can be anyone else's: cancelled or
    failed, it removes only that folder. gh runs git as a child process, so
    gh and git run as one process tree (a Windows job, a process group
    elsewhere) and Cancel ends them both.

    ``done(folder, error)`` is called on the clone's own thread, once: the
    folder and "" when it worked, None and why not when it failed, or None
    and "" when it was cancelled.
    """

    def __init__(self, gh: str, repo: str, target: Path,
                 done: Callable[[Optional[Path], str], None]) -> None:
        self.repo, self.target = repo, Path(target)
        self.staging = self.target.parent / f".thechatplace-clone-{uuid.uuid4().hex[:10]}"
        self._done = done
        self.kept = False  # a finished clone that couldn't be renamed
        self._cancelled = threading.Event()
        self._tree = platform_paths.ProcessTree()
        self._process: Optional[subprocess.Popen] = None
        self._argv = [*_gh(gh), "repo", "clone", repo, str(self.staging)]
        self._thread = threading.Thread(target=self._run, name="clone", daemon=True)

    def start(self) -> "Clone":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancelled.set()
        self._kill()

    def _kill(self) -> None:
        # Only while gh runs: once it has exited its pid may belong to
        # another program, which taskkill (with no job) or killpg would end.
        # On Windows, closing the job ends anything gh left behind; on a Mac
        # a git that outlives gh is left to finish.
        if self._process is not None and self._process.poll() is None:
            self._tree.kill()

    def wait(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            folder, error = self._clone()
        except Exception as exc:  # noqa: BLE001 - always answer, always clean up
            folder, error = None, f"Couldn't clone {self.repo}: {exc}"
        finally:
            self._kill()
            self._tree.close()
        if folder is None and not self.kept:
            remove_tree(self.staging)
        self._done(folder, "" if self._cancelled.is_set() else error)

    def _clone(self):
        _sweep_old_clones(self.target.parent)
        if self.target.exists():
            # Checked by the caller too; never clone over something that's there.
            return None, f"{self.target} already exists."
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return None, f"Couldn't make {self.target.parent}: {exc}"
        process = subprocess.Popen(
            self._argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env=_environment(), **platform_paths.ProcessTree.popen_kwargs())
        self._tree.attach(process)
        self._process = process
        if self._cancelled.is_set():
            self._kill()
        try:
            _out, err = process.communicate(timeout=60 * 60)
        except subprocess.TimeoutExpired:
            self._kill()
            process.communicate()
            return None, f"Cloning {self.repo} took more than an hour, so it was stopped."
        if self._cancelled.is_set():
            return None, ""
        if process.returncode != 0:
            said = " ".join((err or "").split())
            return None, f"Couldn't clone {self.repo}. {said}".strip()
        if self.target.exists():
            return None, (f"{self.target} appeared while {self.repo} was cloning, so it was "
                          "left as it is.")
        # Antivirus or the search indexer may hold a folder git has just
        # written for a moment, so the rename is tried a few times.
        for attempt in range(8):
            try:
                self.staging.rename(self.target)
                return self.target, ""
            except OSError as exc:
                problem = exc
                time.sleep(0.25 * (attempt + 1))
        # The clone worked, so it's kept, under the name it has.
        self.kept = True
        return None, (f"Cloned {self.repo}, but couldn't rename its folder to {self.target} "
                      f"({problem}). The clone is in {self.staging}.")


#: A clone's temporary folder this old is left from one that never
#: finished (The Chat Place closed during it): clones stop at an hour.
_STALE_SECONDS = 3 * 60 * 60


def _sweep_old_clones(root: Path) -> None:
    try:
        old = [p for p in Path(root).glob(".thechatplace-clone-*")
               if p.is_dir() and time.time() - p.stat().st_mtime > _STALE_SECONDS]
    except OSError:
        return
    for folder in old:
        remove_tree(folder)


def remove_tree(folder: Path) -> bool:
    """Delete ``folder`` (a clone this app started), read-only files too:
    git makes its pack files read-only, which Windows won't delete as they
    are. A process that has just been killed may still hold a file for a
    moment, so it tries a few times. True if the folder is gone."""
    import shutil
    import stat

    def writable_then_retry(function, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            function(path)
        except OSError:
            pass

    for attempt in range(10):
        if not os.path.lexists(folder):
            return True
        if sys.version_info >= (3, 12):
            shutil.rmtree(folder, onexc=writable_then_retry)
        else:  # pragma: no cover - 3.12 is what's built and tested
            shutil.rmtree(folder, onerror=writable_then_retry)
        if not os.path.lexists(folder):
            return True
        time.sleep(0.2 * (attempt + 1))
    return False


# -- branches and worktrees ---------------------------------------------------------------

@dataclass
class RepoInfo:
    top: str                 # the worktree the folder is in
    main: str                # the repository's main worktree, where new ones go
    common: str              # its .git folder
    rel: str                 # the folder, relative to ``top`` ("" at the top)
    branch: str              # checked out now ("" when detached)
    branches: List[str]      # local branches no worktree has checked out
    busy: List[str]          # local branches some worktree has checked out
    remotes: List[str]       # remote branches, "origin/name"
    #: Where each busy branch is checked out: its worktree's folder (#587).
    busy_at: Dict[str, str] = field(default_factory=dict)
    #: Busy branches held only by a worktree whose folder is gone: git still
    #: refuses them until ``git worktree prune``.
    busy_gone: List[str] = field(default_factory=list)


def repo_info(git: str, folder: str) -> Optional[RepoInfo]:
    """The git repository ``folder`` is in, or None if it isn't in one."""
    if not folder or not os.path.isdir(folder):
        return None
    try:
        top = _run([git, "-C", folder, "rev-parse", "--show-toplevel"], timeout=10).strip()
        common = _run([git, "-C", folder, "rev-parse", "--path-format=absolute",
                       "--git-common-dir"], timeout=10).strip()
        branch = _run([git, "-C", folder, "branch", "--show-current"], timeout=10).strip()
        refs = _run([git, "-C", folder, "for-each-ref", "--format=%(refname)",
                     "refs/heads/", "refs/remotes/"], timeout=10)
        listed = _run([git, "-C", folder, "worktree", "list", "--porcelain"], timeout=10)
    except ToolError:
        return None
    if not top or not common:
        return None
    top, common = str(Path(top)), str(Path(common))
    # A worktree's common folder is the main one's .git; new worktrees go
    # beside the others, never inside this one.
    main = str(Path(common).parent) if Path(common).name == ".git" else top
    busy, busy_at, gone = [], {}, set()
    for at, name, prunable in _worktrees(listed):
        if not name:
            continue
        held_by_gone = prunable or not os.path.isdir(at)
        if name not in busy:
            # A branch in two worktrees (git worktree add -f) is listed once.
            busy.append(name)
            busy_at[name] = at
            if held_by_gone:
                gone.add(name)
        elif name in gone and not held_by_gone:
            # Say the worktree that's really there, not one whose folder is gone.
            busy_at[name] = at
            gone.discard(name)
    local, remotes = [], []
    for ref in (r.strip() for r in refs.splitlines()):
        if ref.startswith("refs/heads/"):
            local.append(ref[len("refs/heads/"):])
        elif ref.startswith("refs/remotes/") and not ref.endswith("/HEAD"):
            remotes.append(ref[len("refs/remotes/"):])
    try:
        rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(top))
    except ValueError:
        rel = ""
    if rel == "." or rel.startswith(".."):
        rel = ""
    return RepoInfo(top=top, main=main, common=common, rel=rel, branch=branch,
                    branches=[b for b in local if b not in busy], busy=busy, remotes=remotes,
                    busy_at=busy_at, busy_gone=[b for b in busy if b in gone])


def _worktrees(listed: str) -> List[tuple]:
    """``(folder, branch, prunable)`` for each worktree ``git worktree list
    --porcelain`` lists; branch is "" when it's detached."""
    found, at, name, prunable = [], None, "", False
    for line in listed.splitlines() + [""]:
        if line.startswith("worktree "):
            at = str(Path(line[len("worktree "):].strip()))
        elif line.startswith("branch refs/heads/"):
            name = line[len("branch refs/heads/"):]
        elif line.startswith("prunable"):
            prunable = True
        elif not line.strip() and at is not None:
            found.append((at, name, prunable))
            at, name, prunable = None, "", False
    return found


def local_branch(info: RepoInfo, name: str) -> str:
    """The local branch a name means: ``origin/main`` (as git branch -r
    lists it) means main, as ``create_worktree`` takes it."""
    if name in info.remotes and name not in info.branches:
        return name.split("/", 1)[1]
    return name


# A branch can be checked out in only one worktree, so git worktree add
# refuses one checked out anywhere else, the chosen folder included. New
# Session still lists them, last, with the reason in the item (#587), so
# main isn't simply missing; the path is left to the message, as it's long.
IN_USE_HERE = "checked out in this folder"
IN_USE_ELSEWHERE = "in use in another worktree"
IN_USE_GONE = "held by a removed worktree"


class BranchInUse(ToolError):
    """``create_worktree`` refused a branch checked out somewhere; its words
    are a whole message, said as they are."""


def in_use_reason(info: RepoInfo, branch: str) -> str:
    """Why a new worktree can't have ``branch`` (or the local branch a
    remote name such as origin/main means), in a few words, or "" if it
    can."""
    branch = local_branch(info, branch) if branch else ""
    if not branch or branch not in info.busy:
        return ""
    if branch == info.branch:
        return IN_USE_HERE
    return IN_USE_GONE if branch in info.busy_gone else IN_USE_ELSEWHERE


def branch_choices(info: Optional[RepoInfo]) -> List[str]:
    """New Session's Branch items: the branches a new worktree can use, then
    the ones checked out somewhere, each as "main (in use in another
    worktree)"."""
    if info is None:
        return []
    return list(info.branches) + [f"{b} ({in_use_reason(info, b)})" for b in info.busy]


def branch_from_choice(text: str) -> str:
    """The branch an item from ``branch_choices`` names; anything else as
    typed, trimmed."""
    name = (text or "").strip()
    for reason in (IN_USE_HERE, IN_USE_ELSEWHERE, IN_USE_GONE):
        suffix = f" ({reason})"
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def in_use_message(info: RepoInfo, branch: str, start_remote: str = "") -> str:
    """What Start says when the branch chosen is checked out already: where,
    and how to work from it anyway."""
    branch = local_branch(info, branch)
    where = info.busy_at.get(branch, "")
    here = branch == info.branch
    if not here and branch in info.busy_gone:
        # Its folder was deleted without git worktree remove: there's no
        # folder to choose, and git holds the branch until it's pruned.
        place = f", {where}," if where else ""
        return (f"{branch} was checked out in a worktree whose folder{place} is gone, so git "
                f"still holds it. Run git worktree prune in the repository to free it, then "
                f"choose {branch} again.")
    if here:
        text = f"{branch} is checked out in this folder"
        text += f", {where}," if where else ","
    else:
        text = f"{branch} is in use in another worktree"
        text += f", at {where}," if where else ","
    text += " so a new worktree can't use it."
    remote_name = start_remote.split("/", 1)[1] if "/" in start_remote else ""
    if here:
        text += f" To work on {branch} here, choose The folder as it is."
        if remote_name == branch:
            text += (f" Or type a new branch name, and Start from {start_remote}, "
                     f"to work from {branch}.")
        else:
            text += (f" Or type a new branch name, and Start from {branch}, "
                     f"to work from it.")
    elif remote_name == branch:
        text += (f" Type a new branch name, and Start from {start_remote}, "
                 f"to work from {branch}.")
    else:
        text += (f" To work on {branch}, choose that worktree's folder, with Work in "
                 f"set to The folder as it is. Or choose another branch, or type a new name.")
    return text


def check_branch_name(git: str, name: str) -> str:
    """``name`` if git accepts it as a branch name, else ToolError. Names
    that git would read as an option, as ``@{-1}`` shorthand or as a full
    ref are refused before git sees them."""
    name = (name or "").strip()
    if not name:
        raise ToolError("Type a branch name, or choose one.")
    if name.startswith(("-", "@", "refs/")) or name == "HEAD" or "@{" in name \
            or any(c.isspace() for c in name):
        raise ToolError(f"{name} can't be a branch name.")
    try:
        _run([git, "check-ref-format", "--branch", name], timeout=10)
    except ToolError:
        raise ToolError(f"{name} can't be a branch name.") from None
    return name


def worktree_folder(main: str, branch: str) -> Path:
    """``<repo>/.claude/worktrees/<branch>``, where Claude Code and the
    desktop app put theirs; "/" in a branch name becomes "-", and a number
    is added if the folder is taken."""
    base = Path(main) / ".claude" / "worktrees"
    name = re.sub(r"[^A-Za-z0-9._-]", "-", branch).strip(".-") or "worktree"
    folder, number = base / name, 2
    while folder.exists():
        folder, number = base / f"{name}-{number}", number + 1
    return folder


def _remote_for(info: RepoInfo, branch: str) -> str:
    """``origin/<branch>`` (or another remote's) when only a remote has it."""
    candidates = [r for r in info.remotes if r.split("/", 1)[-1] == branch]
    for remote in candidates:
        if remote.startswith("origin/"):
            return remote
    return candidates[0] if candidates else ""


#: A folder inside Claude Code's and The Chat Place's worktrees folder.
WORKTREE_FOLDER_PATTERN = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+")

#: Where a new branch in a new worktree starts (#561): the remote's default
#: branch, fetched first, as Claude Code's own worktrees do, or what the
#: chosen folder has checked out now.
START_REMOTE = "remote"
START_HERE = "here"


@dataclass
class WorktreeMade:
    """What New Session made, so it can be said rather than guessed (#561)."""
    folder: Path            # the folder to work in (the chosen subfolder, if any)
    worktree: Path          # the worktree's own top folder
    branch: str
    how: str                # "new", "existing" or "tracking"
    base: str = ""          # a new branch's starting point, as named to you
    note: str = ""          # why it didn't start where you asked, if it didn't

    def said(self) -> str:
        where = f"Made worktree {self.worktree.name}"
        if self.how == "existing":
            text = f"{where} on existing branch {self.branch}."
        elif self.how == "tracking":
            text = f"{where} on branch {self.branch}, tracking {self.base}."
        else:
            text = f"{where} on new branch {self.branch}, from {self.base}."
        return f"{text} {self.note}".strip()


def is_new_branch(info: RepoInfo, branch: str) -> bool:
    """Whether ``branch`` would be made new: Start from applies only then."""
    name = branch_from_choice(branch)
    if not name:
        return False
    if name in info.remotes or name in info.branches or name in info.busy:
        return False
    return not _remote_for(info, name)


def default_remote_branch(git: str, info: RepoInfo) -> str:
    """The remote's default branch, such as ``origin/main``, or "" when the
    repository has no remote to start from."""
    try:
        named = _run([git, "-C", info.top, "symbolic-ref", "--quiet", "--short",
                      "refs/remotes/origin/HEAD"], timeout=10).strip()
        if named.startswith("origin/"):
            return named
    except ToolError:
        pass
    for candidate in ("origin/main", "origin/master"):
        if candidate in info.remotes:
            return candidate
    return ""


def _current(info: RepoInfo) -> str:
    return info.branch or "the folder's current commit"


def add_worktree(git: str, info: RepoInfo, branch: str) -> Path:
    """The folder to work in, made as ``create_worktree`` makes it, with a
    new branch starting from what the chosen folder has checked out."""
    return create_worktree(git, info, branch, START_HERE).folder


def create_worktree(git: str, info: RepoInfo, branch: str,
                    start: str = START_REMOTE, start_remote: str = "") -> WorktreeMade:
    """Make a worktree for ``branch``. Its ``folder`` is the same folder
    inside it as the one chosen. An existing branch is checked out there; a
    branch only a remote has is made to track it; a new one starts from
    ``start``: the remote's default branch, fetched just before (falling back
    to the current branch, and saying so, with no remote or a failed fetch,
    never an old copy), or what the chosen folder has checked out now.
    ToolError with git's words if it can't. A branch checked out somewhere
    is refused before git is asked, saying where (#587); ``start_remote``
    (such as origin/main, as New Session offers it) shapes that advice."""
    branch = check_branch_name(git, branch_from_choice(branch))
    remote = ""
    if branch in info.remotes and branch not in info.branches:
        # origin/main, as git branch -r lists it: a local main tracking it,
        # never a local branch called origin/main.
        remote, branch = branch, branch.split("/", 1)[1]
    if branch in info.busy:
        raise BranchInUse(in_use_message(info, branch, start_remote))
    folder = worktree_folder(info.main, branch)
    remote = remote or _remote_for(info, branch)
    base, note = "", ""
    if branch in info.branches:
        how, argv = "existing", ["--", str(folder), branch]
    elif remote:
        how, base = "tracking", remote
        argv = ["--track", "-b", branch, "--", str(folder), remote]
    else:
        how = "new"
        if start == START_REMOTE:
            base, note = _fetched_default(git, info)
        if base:
            # Not tracking origin/main: pushing the new branch must never go to main.
            argv = ["--no-track", "-b", branch, "--", str(folder), base]
        else:
            base = _current(info)
            argv = ["-b", branch, "--", str(folder), *([info.branch] if info.branch else [])]
    try:
        _run([git, "-C", info.top, "worktree", "add", *argv], timeout=600)
    except ToolError as exc:
        said = re.sub(r"^Preparing worktree \([^)]*\)\s*", "", str(exc))
        raise ToolError(said) from None
    _exclude_worktrees(git, info)
    inside = folder / info.rel if info.rel else folder
    return WorktreeMade(folder=inside if inside.is_dir() else folder, worktree=folder,
                        branch=branch, how=how, base=base, note=note)


def _fetched_default(git: str, info: RepoInfo) -> tuple:
    """``(origin/main, "")`` once fetched, or ``("", why not)``."""
    remote_branch = default_remote_branch(git, info)
    if not remote_branch:
        return "", (f"This repository has no origin remote to start from, so it starts "
                    f"from {_current(info)}.")
    remote, name = remote_branch.split("/", 1)
    try:
        _run([git, "-C", info.top, "fetch", "--quiet", "--no-recurse-submodules", remote,
              f"+refs/heads/{name}:refs/remotes/{remote}/{name}"], timeout=120)
    except ToolError:
        return "", (f"{remote_branch} couldn't be fetched, so it starts from "
                    f"{_current(info)} instead.")
    return remote_branch, ""


def branch_facts(git: str, folder: str) -> List[tuple]:
    """``(name, value)`` lines about the branch a folder is on, for
    Properties (#331, #561): the branch, the worktree it's in, and what the
    branch started from when git still records it."""
    info = repo_info(git, folder)
    if info is None:
        return []
    lines = [("Branch", info.branch or "none (detached)")]
    if os.path.normcase(info.top) != os.path.normcase(info.main):
        lines.append(("Worktree", f"{info.top}, of {info.main}"))
    if info.branch:
        start = branch_start(git, info)
        if start:
            lines.append(("Branch started from", start))
    return lines


def branch_start(git: str, info: RepoInfo) -> str:
    """What ``info.branch`` was made from, from its reflog's first entry
    ("branch: Created from origin/main"), as a name where git can give one.
    "" when git no longer records it."""
    try:
        log = _run([git, "-C", info.top, "reflog", "show", "--format=%gs",
                    f"refs/heads/{info.branch}", "--"], timeout=10)
    except ToolError:
        return ""
    entries = [line for line in log.splitlines() if line.strip()]
    if not entries:
        return ""
    first = entries[-1]
    match = re.match(r"branch: Created from (.+)$", first.strip())
    if not match:
        return ""
    source = match.group(1).strip()
    if source == "HEAD":
        return ""
    commit = re.fullmatch(r"([0-9a-f]{7,64})(\^\{commit\})?", source)
    if commit:
        # Claude Code records the commit, not its name: name it if a branch
        # has it. The remote's default branch first, as that's what its
        # worktrees start from, then any remote branch, then a local one.
        default = default_remote_branch(git, info)
        if default:
            try:
                tip = _run([git, "-C", info.top, "rev-parse", "--verify", "--quiet",
                            f"{default}^{{commit}}"], timeout=10).strip()
            except ToolError:
                tip = ""
            if tip and tip.startswith(commit.group(1)):
                return default
        for refs in ("refs/remotes/*", "refs/heads/*"):
            try:
                named = _run([git, "-C", info.top, "name-rev", "--name-only", "--no-undefined",
                              "--exclude=refs/remotes/*/HEAD",
                              f"--refs={refs}", commit.group(1)], timeout=10).strip()
            except ToolError:
                continue
            named = re.sub(r"^(remotes/|heads/)", "", named)
            if named and not re.search(r"[~^]", named):
                return named
        return f"commit {commit.group(1)[:7]}"
    return re.sub(r"^refs/(remotes|heads)/", "", source)


def _exclude_worktrees(git: str, info: RepoInfo) -> None:
    """Keep the worktrees out of the repository's git status unless they're
    ignored already: otherwise .claude shows as new, and a worktree could
    be committed into the repository by mistake. Written to the
    repository's .git/info/exclude, which is never shared."""
    try:
        _run([git, "-C", info.main, "check-ignore", "-q", ".claude/worktrees/x"], timeout=10)
        return  # already ignored
    except ToolError:
        pass
    exclude = Path(info.common) / "info" / "exclude"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        with open(exclude, "a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("# The Chat Place's and Claude Code's worktrees\n/.claude/worktrees/\n")
    except OSError:
        pass  # only tidiness: the worktree is made either way
