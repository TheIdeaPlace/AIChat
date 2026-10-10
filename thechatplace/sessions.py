"""The session list: where its entries come from, their state, and their order.

Three sources:

* **Desktop app sessions**, from the Claude desktop app's metadata files
  (``local_<id>.json``): its Code sessions and its Cowork sessions (#91).
  Read-only, always: The Chat Place never writes there.
* **The Chat Place's own sessions**, from its own store (``own_store``).
* **Terminal sessions** (#158): ones started by typing ``claude`` in a
  terminal, which no metadata file describes, found from their transcripts in
  ``~/.claude/projects``. Read-only, as desktop app sessions are.

Live state comes from ``~/.claude/sessions/<pid>.json`` (``status`` busy or
idle) for desktop sessions, and from The Chat Place's own running turns for its
own sessions. The desktop app's ``postTurnSummary`` (``status_category``,
``needs_action``) decides "needs you" when present. All of it is undocumented,
so every field is read defensively and a file that will not parse is skipped.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from . import platform_paths
from .transcript import SessionFactsCache

WORKING = "working"
NEEDS_YOU = "needs you"
IDLE = "idle"

_STATE_ORDER = {NEEDS_YOU: 0, WORKING: 1, IDLE: 2}

#: postTurnSummary.status_category values that mean "waiting on Kelly".
_NEEDS_YOU_CATEGORIES = {"blocked", "review_ready", "needs_input", "waiting",
                         "needs_action", "needs_you"}

DESKTOP = "desktop"
OWN = "own"
TERMINAL = "terminal"


@dataclass
class SessionInfo:
    source: str                 # DESKTOP, OWN or TERMINAL
    key: str                    # unique in the list: local_ id, own:<cli id> or terminal:<cli id>
    title: str
    cwd: str
    cli_session_id: str         # transcript file name / --resume id
    desktop_session_id: str = ""  # local_... id for claude:// links ("" if none)
    last_activity_ms: int = 0
    #: When the session began, for the Started column (#209); 0 if unknown.
    created_ms: int = 0
    state: str = IDLE
    detail: str = ""            # needs_action text, error, etc.
    permission_mode: str = ""
    unread: bool = False
    #: The desktop app archived it (shown only in the Archived view, #32).
    archived: bool = False
    #: On Remote Control (claude.ai). For a desktop app session: the desktop
    #: app has linked it. For one of The Chat Place's own, set by the window
    #: (#96): its turns ask for Remote Control, and you turned it on for it or
    #: it has connected; not proof it is reachable right now.
    remote: bool = False
    #: Chat Place groups it's in (#31), set by the window, read in its row.
    groups: tuple = ()
    #: Hidden with File, Hide Session: only in the Hidden view.
    hidden: bool = False
    #: A desktop app Cowork session (#91) rather than a Code one.
    cowork: bool = False
    #: The Claude Code home its transcript is under, when it isn't
    #: ``~/.claude`` (each Cowork session has its own).
    claude_home: Optional[Path] = None
    #: The folder a Cowork session was given to work on ("" if none): its
    #: cwd is always its own "outputs" folder, which says nothing.
    cowork_folder: str = ""
    #: The Last message column (#146), "Claude: ..." or "You: ...", filled
    #: in by the snapshot only while that column is shown ("" otherwise).
    last_message: str = ""

    @property
    def is_own(self) -> bool:
        return self.source == OWN

    @property
    def is_terminal(self) -> bool:
        return self.source == TERMINAL

    @property
    def repo(self) -> str:
        if self.cowork:
            name = self.cowork_folder.rstrip("\\/").replace("\\", "/").split("/")[-1]
            # "Cowork session" ends the row already, so not "Cowork" twice.
            return name or "no folder"
        cwd = (self.cwd or "").rstrip("\\/")
        if not cwd:
            return "unknown folder"
        name = cwd.replace("\\", "/").split("/")[-1]
        # A worktree reads better as the repo it belongs to.
        parts = cwd.replace("\\", "/").split("/")
        if ".claude" in parts:
            index = parts.index(".claude")
            if index > 0 and index + 1 < len(parts) and parts[index + 1] == "worktrees":
                return f"{parts[index - 1]} worktree"
        return name

    @property
    def can_open_in_claude(self) -> bool:
        return bool(self.desktop_session_id)

    def transcript_path(self) -> Optional[Path]:
        """Its transcript, or None when it isn't on disk (or has no cli id)."""
        if not self.cli_session_id:
            return None
        root = self.claude_home / "projects" if self.claude_home is not None else None
        return platform_paths.transcript_path(self.cwd, self.cli_session_id, root)

    def transcript_written_ms(self) -> int:
        """When its transcript was last written, in milliseconds, or 0. Only
        where Claude Code puts it (no search of every project folder, as
        ``transcript_path`` does when that misses): this is asked of every
        session on every refresh of the list."""
        if not self.cli_session_id or not platform_paths.is_safe_id(self.cli_session_id):
            return 0
        root = self.claude_home / "projects" if self.claude_home is not None else None
        try:
            path = platform_paths.direct_transcript_path(self.cwd, self.cli_session_id, root)
            return os.stat(path).st_mtime_ns // 1_000_000
        except (OSError, ValueError):
            return 0

    def list_line(self, now_ms: Optional[int] = None,
                  fields: Optional[Iterable[str]] = None) -> str:
        """What a screen reader hears on arrowing to this session: its
        columns (#134) in the order chosen in View, Session List Columns
        (``fields``, ids from ``FIELDS``), each said only when it has
        something to say. A row never comes out empty: with nothing to say
        in any shown column, it reads the title."""
        values = self.field_values(now_ms)
        order = DEFAULT_FIELDS if fields is None else fields
        parts = [values[f] for f in order if values.get(f)]
        return ", ".join(parts) or values[FIELD_TITLE]

    def field_values(self, now_ms: Optional[int] = None) -> Dict[str, str]:
        """Each column's text for this session ("" when it says nothing)."""
        state = self.state
        if self.detail:
            state = f"{state}: {self.detail}"
        kind = []
        if self.is_own:
            kind.append("Chat Place session")
        if self.cowork:
            kind.append("Cowork session")
        if self.is_terminal:
            kind.append("terminal session")
        groups = ""
        if self.groups:
            groups = (("group " if len(self.groups) == 1 else "groups ")
                      + ", ".join(self.groups))
        return {
            FIELD_TITLE: self.title or "Untitled session",
            FIELD_FOLDER: self.repo,
            FIELD_STATUS: state,
            FIELD_NEW_REPLY: "new reply" if self.unread else "",
            FIELD_ACTIVITY: describe_age(self.last_activity_ms, now_ms),
            FIELD_STARTED: describe_started(self.created_ms, now_ms),
            FIELD_KIND: ", ".join(kind),
            # #96: say which can be reached from claude.ai
            FIELD_REMOTE: "Remote Control" if self.remote else "",
            FIELD_ARCHIVED: "archived" if self.archived else "",
            FIELD_HIDDEN: "hidden" if self.hidden else "",
            FIELD_GROUPS: groups,
            FIELD_LAST_MESSAGE: self.last_message,
        }


#: The session list's columns (#134): what each row can say, as (id, name).
#: Each row is one string a screen reader reads whole, so a "column" is a part
#: of that string, and View, Session List Columns chooses which parts and in
#: what order. The ids are saved in settings.json; never rename one.
FIELD_TITLE = "title"
FIELD_FOLDER = "folder"
FIELD_STATUS = "status"
FIELD_NEW_REPLY = "new_reply"
FIELD_ACTIVITY = "activity"
FIELD_STARTED = "started"
FIELD_KIND = "kind"
FIELD_REMOTE = "remote"
FIELD_ARCHIVED = "archived"
FIELD_HIDDEN = "hidden"
FIELD_GROUPS = "groups"
FIELD_LAST_MESSAGE = "last_message"
FIELDS = [
    (FIELD_TITLE, "Title"),
    (FIELD_FOLDER, "Folder"),
    (FIELD_STATUS, "Status (needs you, working or idle)"),
    (FIELD_NEW_REPLY, "New reply"),
    (FIELD_ACTIVITY, "Last activity"),
    (FIELD_KIND, "Kind (Chat Place, Cowork or terminal)"),
    (FIELD_REMOTE, "Remote Control"),
    (FIELD_ARCHIVED, "Archived"),
    (FIELD_HIDDEN, "Hidden"),
    (FIELD_GROUPS, "Groups"),
    (FIELD_STARTED, "Started (when the session began)"),
    (FIELD_LAST_MESSAGE, "Last message"),
]
FIELD_IDS = [field_id for field_id, _name in FIELDS]
FIELD_NAMES = dict(FIELDS)
#: Columns there are but rows don't read until you add them: Last message
#: (#146) reads each session's transcript, and makes every row long; Started
#: (#209) is there for those who want it, not news for everyone.
OPTIONAL_FIELDS = (FIELD_STARTED, FIELD_LAST_MESSAGE)
#: The columns rows read unless you choose otherwise, in the order they
#: have always been read. A saved order lists its columns, so one saved
#: before a column was added doesn't gain it.
DEFAULT_FIELDS = [f for f in FIELD_IDS if f not in OPTIONAL_FIELDS]


def field_short_name(field_id: str) -> str:
    """A column's name without its explanation, for "Status moved to top"."""
    return FIELD_NAMES.get(field_id, field_id).split(" (")[0]


def clean_fields(raw) -> List[str]:
    """A saved column order made safe: known ids only (a newer version's
    columns are dropped, not kept as blanks), each once, in the saved order.
    Anything unusable, or a list with no known column left, is the default."""
    if not isinstance(raw, list):
        return list(DEFAULT_FIELDS)
    known = set(FIELD_IDS)
    fields: List[str] = []
    for value in raw:
        if isinstance(value, str) and value in known and value not in fields:
            fields.append(value)
    return fields or list(DEFAULT_FIELDS)


def _how_long_ago(then_ms: int, now_ms: Optional[int]) -> str:
    """'just now', '5 minutes ago', 'yesterday', '3 days ago'."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    seconds = max(0, (now_ms - then_ms) // 1000)
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


def describe_age(then_ms: int, now_ms: Optional[int] = None) -> str:
    """Last activity: 'active just now', 'active 3 days ago'."""
    if not then_ms:
        return "no activity recorded"
    return f"active {_how_long_ago(then_ms, now_ms)}"


def describe_started(then_ms: int, now_ms: Optional[int] = None) -> str:
    """Started (#209): 'started 2 days ago', or nothing when it isn't known."""
    return f"started {_how_long_ago(then_ms, now_ms)}" if then_ms else ""


#: How the session list can be ordered (View, Sort Sessions): (value, menu label).
#: Within the same title or folder, newest comes first.
SORT_STATUS = "status"
SORT_NEWEST = "newest"
SORT_OLDEST = "oldest"
SORT_TITLE = "title"
SORT_FOLDER = "folder"
SORT_ORDERS = [
    (SORT_STATUS, "By &Status (needs you, then working, then the rest)"),
    (SORT_NEWEST, "&Newest First"),
    (SORT_OLDEST, "&Oldest First"),
    (SORT_TITLE, "By &Title (A to Z)"),
    (SORT_FOLDER, "By &Folder (A to Z)"),
]
SORT_VALUES = [value for value, _label in SORT_ORDERS]
#: Said after choosing one: "Sessions sorted by title."
SORT_SPOKEN = {SORT_STATUS: "by status", SORT_NEWEST: "newest first",
               SORT_OLDEST: "oldest first", SORT_TITLE: "by title",
               SORT_FOLDER: "by folder"}


#: Which sessions the list shows (View, Show, #32): (value, menu label). A
#: group's view is "group:<name>".
VIEW_ALL = "all"
VIEW_ACTIVE = "active"
VIEW_NEEDS_YOU = "needs"
#: By status (#134), as well as Needs You above.
VIEW_WORKING = "working"
VIEW_NEW_REPLY = "new_reply"
VIEW_IDLE = "idle"
VIEW_DESKTOP = "desktop"
VIEW_COWORK = "cowork"
VIEW_OWN = "own"
VIEW_TERMINAL = "terminal"
VIEW_REMOTE = "remote"
VIEW_UNGROUPED = "ungrouped"
VIEW_ARCHIVED = "archived"
VIEW_HIDDEN = "hidden"
GROUP_VIEW_PREFIX = "group:"
VIEWS = [
    (VIEW_ALL, "&All Sessions"),
    (VIEW_ACTIVE, "Needs You or &Working"),
    (VIEW_NEEDS_YOU, "&Needs You"),
    (VIEW_WORKING, "Wor&king"),
    (VIEW_NEW_REPLY, "With a New Re&ply"),
    (VIEW_IDLE, "&Idle"),
    (VIEW_DESKTOP, "&Desktop App Sessions"),
    (VIEW_COWORK, "C&owork Sessions"),
    (VIEW_OWN, "&Chat Place Sessions"),
    (VIEW_TERMINAL, "&Terminal Sessions"),
    (VIEW_REMOTE, "&Remote Control Sessions"),
    (VIEW_UNGROUPED, "&Ungrouped"),
    (VIEW_ARCHIVED, "Archi&ved"),
    (VIEW_HIDDEN, "&Hidden"),
]
#: Said and shown in the list's name: "showing needs you or working".
VIEW_SPOKEN = {VIEW_ALL: "all sessions", VIEW_ACTIVE: "needs you or working",
               VIEW_NEEDS_YOU: "needs you", VIEW_WORKING: "working sessions",
               VIEW_NEW_REPLY: "sessions with a new reply", VIEW_IDLE: "idle sessions",
               VIEW_DESKTOP: "desktop app sessions",
               VIEW_COWORK: "Cowork sessions",
               VIEW_OWN: "Chat Place sessions", VIEW_TERMINAL: "terminal sessions",
               VIEW_REMOTE: "Remote Control sessions",
               VIEW_UNGROUPED: "ungrouped sessions",
               VIEW_ARCHIVED: "archived sessions", VIEW_HIDDEN: "hidden sessions"}


def group_view(name: str) -> str:
    return GROUP_VIEW_PREFIX + name


def view_spoken(view: str) -> str:
    if view.startswith(GROUP_VIEW_PREFIX):
        return f"group {view[len(GROUP_VIEW_PREFIX):]}"
    return VIEW_SPOKEN.get(view, VIEW_SPOKEN[VIEW_ALL])


def in_view(info: SessionInfo, view: str) -> bool:
    """Whether the list shows ``info`` in ``view``. Archived sessions are only
    in the Archived view (and in a group they were put in). Hidden sessions
    are only in the Hidden view."""
    if view == VIEW_HIDDEN:
        return info.hidden
    if info.hidden:
        return False
    if view.startswith(GROUP_VIEW_PREFIX):
        return view[len(GROUP_VIEW_PREFIX):] in info.groups
    if view == VIEW_ARCHIVED:
        return info.archived
    if info.archived:
        return False
    if view == VIEW_ACTIVE:
        return info.state in (NEEDS_YOU, WORKING)
    if view == VIEW_NEEDS_YOU:
        return info.state == NEEDS_YOU
    if view == VIEW_WORKING:
        return info.state == WORKING
    if view == VIEW_NEW_REPLY:
        return info.unread
    if view == VIEW_IDLE:
        return info.state not in (NEEDS_YOU, WORKING)
    if view == VIEW_DESKTOP:
        return info.source == DESKTOP
    if view == VIEW_COWORK:
        return info.cowork
    if view == VIEW_OWN:
        return info.is_own
    if view == VIEW_TERMINAL:
        return info.is_terminal
    if view == VIEW_REMOTE:
        return info.remote
    if view == VIEW_UNGROUPED:
        # In none of your groups or the desktop app's: what still needs
        # filing. ``groups`` is filled in by the window before this is asked;
        # with groups.json unreadable, your own groups count as none.
        return not info.groups
    return True


def sort_sessions(sessions: Iterable[SessionInfo],
                  order: str = SORT_STATUS) -> List[SessionInfo]:
    """The list in ``order`` (one of SORT_VALUES; anything else is by status).

    By status: needs you first, then working, then the rest; newest first in
    each. Title and folder compare without regard to case.
    """
    def newest(s: SessionInfo) -> int:
        return -(s.last_activity_ms or 0)

    if order == SORT_NEWEST:
        key = lambda s: (newest(s), s.key)  # noqa: E731
    elif order == SORT_OLDEST:
        key = lambda s: (-newest(s), s.key)  # noqa: E731
    elif order == SORT_TITLE:
        key = lambda s: ((s.title or "").casefold(), newest(s), s.key)  # noqa: E731
    elif order == SORT_FOLDER:
        key = lambda s: (s.repo.casefold(), newest(s), s.key)  # noqa: E731
    else:
        key = lambda s: (_STATE_ORDER.get(s.state, 3), newest(s), s.key)  # noqa: E731
    return sorted(sessions, key=key)


# ---------------------------------------------------------------------------
# Live state (~/.claude/sessions/<pid>.json)
# ---------------------------------------------------------------------------


@dataclass
class LiveStatus:
    status: str          # "busy" | "idle" | "waiting" | other
    pid: int
    updated_ms: int = 0
    #: With "waiting": what Claude Code is waiting on you for ("dialog open",
    #: "input needed", "sandbox request", ...). An interactive session at its
    #: prompt is "idle"; "waiting" is something it can't go on without.
    waiting_for: str = ""


def load_live_status(directory: Optional[Path] = None,
                     alive=platform_paths.pid_alive,
                     started=platform_paths.process_start) -> Dict[str, LiveStatus]:
    """Map of session id (both the cli id and the desktop local_ id) -> status.

    A pid file whose process has exited is stale and ignored, and so is one
    whose pid Windows has since given to another process (#5): its
    ``procStart`` doesn't match when the running process started.
    """
    directory = directory or platform_paths.live_sessions_dir()
    result: Dict[str, LiveStatus] = {}
    try:
        files = list(directory.glob("*.json"))
    except OSError:
        return result
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int):
            try:
                pid = int(path.stem)
            except ValueError:
                continue
        if not alive(pid):
            continue
        if not _same_process(data.get("procStart"), started(pid), data.get("startedAt")):
            continue
        status = LiveStatus(status=str(data.get("status") or ""), pid=pid,
                            updated_ms=_int(data.get("statusUpdatedAt") or data.get("updatedAt")),
                            waiting_for=" ".join(str(data.get("waitingFor") or "").split()))
        for id_field in ("sessionId", "hostSessionId"):
            value = data.get(id_field)
            if isinstance(value, str) and value:
                result[value] = status
    return result


#: A ``procStart`` that is a Windows FILETIME falls between these: 2000 and 2200.
_FILETIME_FROM = 125_911_584_000_000_000
_FILETIME_TO = 189_025_920_000_000_000
#: Milliseconds from 1601 (FILETIME's start) to 1970 (``startedAt``'s).
_FILETIME_EPOCH_MS = 11_644_473_600_000
#: How long after its process starts Claude Code may write ``startedAt``.
#: Seen under a second; a pid Windows hands on is reused much later.
_STARTED_AT_SLACK_MS = 30_000


def _same_process(recorded, actual: Optional[int], started_at=None) -> bool:
    """Whether the pid file's ``procStart`` (or, when that's on another
    clock, its ``startedAt``) is the running process's start. True when
    neither can be compared: then the pid is all there is to go on. A
    process that isn't yours can't be the Claude Code that wrote the file."""
    recorded, started_ms = _number(recorded), _number(started_at)
    if (recorded is None and started_ms is None) or actual is None:
        return True
    if actual == platform_paths.NOT_YOURS:
        return False
    if recorded is not None and _FILETIME_FROM <= recorded <= _FILETIME_TO:
        # The same clock read twice can differ in the last digits: a second
        # apart is still the same process; a reused pid started long after.
        return abs(recorded - actual) < 10_000_000
    # Another clock: Cowork's Claude Code 2.1.205 wrote .NET ticks in local
    # time (#91). Its ``startedAt`` (epoch ms, written just after the process
    # starts) is compared instead; without one the pid is all there is.
    if started_ms is None:
        return True
    actual_ms = actual // 10_000 - _FILETIME_EPOCH_MS
    return -_STARTED_AT_SLACK_MS < started_ms - actual_ms < _STARTED_AT_SLACK_MS


def _number(value) -> Optional[int]:
    """A pid file's number, written as a number or a string; None if it isn't one."""
    try:
        return int(float(str(value)))
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Desktop app metadata
# ---------------------------------------------------------------------------


@dataclass
class DesktopLoadResult:
    sessions: List[SessionInfo] = field(default_factory=list)
    unreadable_files: int = 0
    #: Every cli session id the desktop app owns, archived ones included.
    #: Used by the --resume guard.
    desktop_cli_ids: Set[str] = field(default_factory=set)
    #: The cli ids of its Cowork sessions (#91), archived ones included: these
    #: can't even be copied here (``build_fork_command``).
    cowork_cli_ids: Set[str] = field(default_factory=set)


def load_desktop_sessions(directory: Optional[Path] = None,
                          live: Optional[Dict[str, LiveStatus]] = None,
                          include_archived: bool = False,
                          cowork_directory: Optional[Path] = None,
                          alive=platform_paths.pid_alive,
                          started=platform_paths.process_start) -> DesktopLoadResult:
    """The desktop app's sessions, Code and Cowork (#91). Archived ones are
    left out unless ``include_archived`` (the list's Archived view, #32); they
    come with ``archived`` set. When ``directory`` is given (a test), Cowork
    sessions are read only from ``cowork_directory``."""
    if directory:
        directories = [directory]
        cowork_directories = [cowork_directory] if cowork_directory else []
    else:
        directories = platform_paths.desktop_sessions_dirs()
        cowork_directories = ([cowork_directory] if cowork_directory
                              else platform_paths.cowork_sessions_dirs())
    live = live if live is not None else {}
    result = DesktopLoadResult()
    for path in _newest_files(directories, "**/local_*.json"):
        _add_desktop_session(result, path, lambda: live, include_archived)
    # Each Cowork session's folder is a whole Claude Code home, so only
    # metadata files at the depth the desktop app writes them count. Its live
    # state is in that home too, not in ~/.claude/sessions.
    for path in _newest_files(cowork_directories, "*/*/local_*.json"):
        home = platform_paths.cowork_claude_home(path)
        _add_desktop_session(
            result, path,
            lambda home=home: load_live_status(home / "sessions", alive=alive, started=started),
            include_archived, claude_home=home)
    return result


def _newest_files(directories: Iterable[Path], pattern: str) -> List[Path]:
    """The files matching ``pattern`` in ``directories``. A session in two
    folders (both kinds of desktop app install, #183) is read once, from the
    copy written last."""
    newest: Dict[str, Path] = {}
    for folder in directories:
        try:
            found = list(folder.glob(pattern))
        except OSError:
            continue
        for path in found:
            known = newest.get(path.name)
            if known is None or _mtime(path) > _mtime(known):
                newest[path.name] = path
    return list(newest.values())


def _add_desktop_session(result: DesktopLoadResult, path: Path,
                         live: Callable[[], Dict[str, LiveStatus]], include_archived: bool,
                         claude_home: Optional[Path] = None) -> None:
    """Read one metadata file into ``result``. ``live`` gives the live state
    to look it up in, read only for a session that isn't archived (each
    Cowork session's is a folder of its own). ``claude_home`` is given only
    for a Cowork session."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result.unreadable_files += 1
        return
    if not isinstance(data, dict):
        result.unreadable_files += 1
        return
    cli_id = data.get("cliSessionId")
    if isinstance(cli_id, str) and cli_id:
        result.desktop_cli_ids.add(cli_id)
        if claude_home is not None:
            result.cowork_cli_ids.add(cli_id)
    if data.get("isArchived") and not include_archived:
        return
    info = desktop_session_from_metadata(data, {} if data.get("isArchived") else live())
    if info is not None:
        info.cowork = claude_home is not None
        info.claude_home = claude_home
        if info.cowork:
            folders = data.get("userSelectedFolders")
            if isinstance(folders, list) and folders and isinstance(folders[0], str):
                info.cowork_folder = folders[0]
        result.sessions.append(info)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def desktop_session_from_metadata(data: dict,
                                  live: Dict[str, LiveStatus]) -> Optional[SessionInfo]:
    local_id = data.get("sessionId")
    if not isinstance(local_id, str) or not platform_paths.is_safe_id(local_id):
        return None
    cli_id = data.get("cliSessionId")
    cli_id = cli_id if isinstance(cli_id, str) and platform_paths.is_safe_id(cli_id) else ""
    info = SessionInfo(
        source=DESKTOP,
        key=local_id,
        title=str(data.get("title") or "").strip() or "Untitled session",
        cwd=str(data.get("cwd") or ""),
        cli_session_id=cli_id,
        desktop_session_id=local_id,
        last_activity_ms=_int(data.get("lastActivityAt") or data.get("createdAt")),
        created_ms=_int(data.get("createdAt")),
        permission_mode=str(data.get("permissionMode") or ""),
        archived=bool(data.get("isArchived")),
        remote=isinstance(data.get("bridgeSessionIds"), list) and bool(data["bridgeSessionIds"]),
    )
    info.state, info.detail = desktop_state(data, live.get(cli_id) or live.get(local_id))
    return info


def desktop_state(data: dict, live: Optional[LiveStatus]) -> "tuple[str, str]":
    """(state, detail) for a desktop session. Best effort by design."""
    if live is not None and live.status == "busy":
        return WORKING, ""
    summary = data.get("postTurnSummary")
    if isinstance(summary, dict) and _summary_is_current(data, summary):
        needs = str(summary.get("needs_action") or "").strip()
        category = str(summary.get("status_category") or "").strip().lower()
        if needs:
            return NEEDS_YOU, needs
        if category in _NEEDS_YOU_CATEGORIES:
            detail = (str(summary.get("status_detail") or "").strip()
                      or category.replace("_", " "))
            return NEEDS_YOU, detail
    return IDLE, ""


def _summary_is_current(data: dict, summary: dict) -> bool:
    """A summary of an older turn says nothing about the latest one."""
    last = data.get("lastAssistantUuid")
    summarized = summary.get("summarizes_uuid") or data.get("postTurnSummaryFor")
    if isinstance(last, str) and last and isinstance(summarized, str) and summarized:
        return last == summarized
    return True


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_terminal_sessions(projects: Path, live: Dict[str, LiveStatus],
                           listed: Set[str], facts: SessionFactsCache) -> List[SessionInfo]:
    """Sessions started by typing ``claude`` in a terminal (#158), from their
    transcripts in ``projects`` (``~/.claude/projects``). ``listed`` are the
    cli ids already in the list (the desktop app's, archived ones included,
    and The Chat Place's own), which aren't listed again. Transcripts of
    ``claude -p`` runs, the desktop app's and subagents' aren't sessions
    someone typed into, so they aren't listed. A terminal session's state is
    its live file's: working while busy, needs you while Claude Code waits on
    you (a permission or a question), and idle at its prompt or once it has
    closed."""
    sessions: List[SessionInfo] = []
    seen: List[Path] = []
    try:
        # Subagents' transcripts are a level deeper (<session>/subagents/).
        paths = sorted(projects.glob("*/*.jsonl"))
    except OSError:
        paths = []
    for path in paths:
        cli_id = path.stem
        if cli_id in listed or not platform_paths.is_safe_id(cli_id):
            continue
        seen.append(path)
        about = facts.get(path)
        if about is None or not about.terminal:
            continue
        state, detail = terminal_state(live.get(cli_id))
        sessions.append(SessionInfo(
            source=TERMINAL, key=f"terminal:{cli_id}",
            title=about.title or f"Untitled terminal session in {_folder_name(about.cwd)}",
            cwd=about.cwd, cli_session_id=cli_id,
            last_activity_ms=about.modified_ms, created_ms=about.started_ms,
            state=state, detail=detail))
    facts.keep_only(seen)
    return sessions


def terminal_state(live: Optional[LiveStatus]) -> "tuple[str, str]":
    """(state, detail) for a terminal session, from its live file."""
    if live is None:
        return IDLE, ""
    if live.status == "busy":
        return WORKING, ""
    if live.status == "waiting":
        return NEEDS_YOU, live.waiting_for or "waiting for you"
    return IDLE, ""


def _folder_name(cwd: str) -> str:
    return (cwd or "").rstrip("\\/").replace("\\", "/").split("/")[-1] or "an unknown folder"
