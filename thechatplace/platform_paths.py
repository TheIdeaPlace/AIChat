"""Everything that knows which operating system it is running on.

The rest of the app asks this module where things live and how to do the
few OS-level things it needs (is a process alive, open a URL with the shell).
A Mac port should only have to touch this file and the speech scripts.
Linux is the third: ``sys.platform`` there is "linux", and its paths follow
the XDG base directories (``~/.config/TheChatPlace``).

Locations, as found on Windows with Claude Code 2.1 and the Claude desktop
app (none of this is documented, so every caller treats it as best effort):

* Desktop app session metadata:
  ``%APPDATA%\\Claude\\claude-code-sessions\\<id>\\<orgId>\\local_<id>.json``,
  or for the Microsoft Store version
  ``%LOCALAPPDATA%\\Packages\\Claude_<id>\\LocalCache\\Roaming\\Claude\\claude-code-sessions``
* Cowork sessions (#91): ``local-agent-mode-sessions\\<id>\\<orgId>\\local_<id>.json``
  in the same two ``Claude`` folders. The ``local_<id>`` folder beside each is
  the session's own Claude Code home (``.claude\\projects\\...\\<cli>.jsonl``).
* Transcripts: ``%USERPROFILE%\\.claude\\projects\\<encoded cwd>\\<cliSessionId>.jsonl``
* Live sessions: ``%USERPROFILE%\\.claude\\sessions\\<pid>.json``
* The Chat Place's own files: ``%APPDATA%\\TheChatPlace\\``

On a Mac the ``%APPDATA%`` folders are in ``~/Library/Application Support``,
and on Linux in ``$XDG_CONFIG_HOME`` (``~/.config``). Anthropic makes no
Claude desktop app for Linux, so there its folders are usually missing and
the list has terminal sessions and The Chat Place's own.
"""
from __future__ import annotations

import json
import ntpath
import os
import platform
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Iterable, List, Optional

APP_DIR_NAME = "TheChatPlace"


def claude_home() -> Path:
    """``~/.claude`` (honours CLAUDE_CONFIG_DIR, as Claude Code does)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def kept_history_dir() -> Path:
    """TCP's own copies; provider homes remain read-only (#438)."""
    return app_data_dir() / "kept-history" / "claude"


def claude_config_file() -> Path:
    """``~/.claude.json``, where Claude Code records which folders you've
    trusted (inside CLAUDE_CONFIG_DIR when that is set)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) / ".claude.json" if override else Path.home() / ".claude.json"


def claude_managed_settings() -> List[Path]:
    """Where an administrator's Claude Code policy can be (#336)."""
    if sys.platform == "win32":
        return [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode"
                / "managed-settings.json",
                Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ClaudeCode"
                / "managed-settings.json"]
    return [Path("/Library/Application Support/ClaudeCode/managed-settings.json")]


def claude_settings_files(folder: str) -> List[tuple]:
    """(whose, path, shared project file?) for every Claude Code settings file
    that can apply to a chat working in ``folder``, listed in this order (the
    note groups the rules by kind). Only the folder itself: a parent's
    ``.claude`` doesn't apply (#336)."""
    home = claude_home()
    files = [("your administrator's policy", path, False) for path in claude_managed_settings()]
    files += [("your settings", home / "settings.json", False)]
    if folder:
        project = Path(folder) / ".claude"
        files += [("this folder's shared settings", project / "settings.json", True),
                  ("this folder's local settings", project / "settings.local.json", False)]
    return files


def claude_folder_trusted(folder: str) -> Optional[bool]:
    """Whether Claude Code's trust dialog was accepted for exactly ``folder``
    (``projects[<folder>].hasTrustDialogAccepted`` in ~/.claude.json), or
    None when that file can't be read (Claude Code rewrites it often)."""
    try:
        data = json.loads(claude_config_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return None
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict) or not folder:
        return False
    want = folder.replace("\\", "/").rstrip("/")
    same = (lambda a, b: a.lower() == b.lower()) if sys.platform == "win32" else (lambda a, b: a == b)
    for key, value in projects.items():
        if isinstance(key, str) and same(key.replace("\\", "/").rstrip("/"), want):
            return isinstance(value, dict) and value.get("hasTrustDialogAccepted") is True
    return False


def projects_dir() -> Path:
    return claude_home() / "projects"


def copilot_home() -> Path:
    """``~/.copilot`` (honours COPILOT_HOME, as GitHub Copilot CLI does)."""
    override = os.environ.get("COPILOT_HOME")
    if override:
        return Path(override)
    return Path.home() / ".copilot"


def copilot_policy_texts() -> list:
    r"""(where, text or None) for each of Copilot CLI's managed policy files and, on
    Windows, its policy registry values: an organisation's hooks live there,
    and Copilot loads them whatever its own settings say (#352).

    Where, from GitHub's Copilot CLI hooks reference ("Hook sources",
    https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference):
    ``C:\ProgramData\GitHub\Copilot\policy.d\*.json`` on Windows,
    ``/etc/github-copilot/policy.d/*.json`` on Linux and macOS, and on Windows
    each subkey of ``HKLM\Software\Policies\GitHub\Copilot`` with a
    ``Policy`` string value. Beyond the reference, and only ever stricter:
    the key's own ``Policy`` value, and the same key under HKCU."""
    texts = []
    if sys.platform == "win32":
        root = Path(os.environ.get("ProgramData") or r"C:\ProgramData")
        folder = root / "GitHub" / "Copilot" / "policy.d"
    else:
        folder = Path("/etc/github-copilot/policy.d")
    try:
        files = sorted(folder.glob("*.json"))
    except OSError:
        files = []
    for path in files:
        # A policy that can't be read in full is given as None: it can't be
        # ruled out.
        try:
            data = path.read_bytes()
        except OSError:
            data = None
        texts.append((str(path), None if data is None or len(data) > COPILOT_SETTINGS_BYTES
                      else data.decode("utf-8-sig", "replace")))
    if sys.platform == "win32":
        import winreg
        for hive, hive_name in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"),
                                (winreg.HKEY_CURRENT_USER, "HKCU")):
            texts += _copilot_policy_values(winreg, hive, hive_name)
    return texts


def _copilot_policy_values(winreg, hive, hive_name) -> list:
    r"""(where, text or None) for the ``Policy`` values under one hive's
    ``Software\Policies\GitHub\Copilot``: the key's own, and each subkey's.
    A value that isn't a string is None: it can't be ruled out."""
    path = r"Software\Policies\GitHub\Copilot"
    try:
        key = winreg.OpenKey(hive, path)
    except OSError:
        return []
    found = []
    with key:
        try:
            value, _kind = winreg.QueryValueEx(key, "Policy")
            found.append((rf"{hive_name}\{path}", value if isinstance(value, str) else None))
        except OSError:
            pass
        index = 0
        while True:
            try:
                name = winreg.EnumKey(key, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(key, name) as sub:
                    value, _kind = winreg.QueryValueEx(sub, "Policy")
            except OSError:
                continue
            found.append((rf"{hive_name}\{path}\{name}", value if isinstance(value, str) else None))
    return found


#: The most read of any one Copilot settings or hook file.
COPILOT_SETTINGS_BYTES = 2 * 1024 * 1024


def copilot_sessions_dir() -> Path:
    """One folder per Copilot CLI session, each named for its id."""
    return copilot_home() / "session-state"


def copilot_has_sessions() -> bool:
    """Whether Copilot CLI has saved a session here: a session folder with a
    conversation log. Until it has, there is nothing for the read-only
    preview to list (#352). Looks at folder names and one file's existence,
    never at what's in them."""
    try:
        with os.scandir(copilot_sessions_dir()) as entries:
            return any(entry.is_dir() and not entry.name.startswith(".")
                       and os.path.isfile(os.path.join(entry.path, "events.jsonl"))
                       for entry in entries)
    except OSError:
        return False


def live_sessions_dir() -> Path:
    return claude_home() / "sessions"


def _roaming_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def desktop_sessions_dir() -> Path:
    """Where the Claude desktop app keeps one JSON file per Code session, when
    it was installed with its own installer."""
    return _roaming_dir() / "Claude" / "claude-code-sessions"


def _store_app_sessions_dirs(name: str = "claude-code-sessions") -> List[Path]:
    """The same folder for the Microsoft Store (MSIX) version of the desktop
    app (issue #183). Windows redirects a packaged app's AppData writes into
    its package folder, so ``%APPDATA%\\Claude`` doesn't exist there. Matched
    on ``Claude_*`` rather than the package id seen on Kelly's PCs
    (``Claude_pzs8sxrjxfjjc``), in case it differs."""
    if sys.platform != "win32":
        return []
    local = os.environ.get("LOCALAPPDATA")
    packages = Path(local) / "Packages" if local else Path.home() / "AppData" / "Local" / "Packages"
    try:
        return sorted(p / "LocalCache" / "Roaming" / "Claude" / name
                      for p in packages.glob("Claude_*"))
    except OSError:
        return []


def desktop_sessions_dirs() -> List[Path]:
    """Every folder that holds desktop app session files, that exists. Both
    kinds of install can be present (switching from one to the other leaves
    the old folder behind); callers read all of them."""
    candidates = [desktop_sessions_dir(), *_store_app_sessions_dirs()]
    return [path for path in candidates if path.is_dir()]


COWORK_DIR_NAME = "local-agent-mode-sessions"


def cowork_sessions_dirs() -> List[Path]:
    """Every folder that holds the desktop app's Cowork session files (#91)
    that exists: the installer version's and the Store version's, as for
    Code sessions. In the long form (``long_path``): the files in them are
    already near Windows' 260-character limit."""
    candidates = [_roaming_dir() / "Claude" / COWORK_DIR_NAME,
                  *_store_app_sessions_dirs(COWORK_DIR_NAME)]
    return [long_path(path) for path in candidates if path.is_dir()]


def cowork_claude_home(metadata_file: Path) -> Path:
    """A Cowork session's own Claude Code home. The ``local_<id>`` folder
    beside its ``local_<id>.json`` holds a ``.claude`` folder with the
    session's ``projects`` (transcripts) and ``sessions`` (pid files); Cowork
    doesn't use ``~/.claude``."""
    return long_path(metadata_file.with_suffix("") / ".claude")


def long_path(path: Path) -> Path:
    """``path`` in a form Windows opens however long it gets. A Cowork
    transcript's path is the session folder (already ~200 characters in the
    Store app's package folder) plus its cwd encoded again, well past the 260
    Windows allows unless long paths are turned on, which they aren't by
    default. The ``\\\\?\\`` form has no such limit."""
    if sys.platform != "win32":
        return path
    text = str(path)
    # Already long, a device (``\\.\pipe\...``), or relative: left as it is.
    if text.startswith(("\\\\?\\", "\\\\.\\")) or not path.is_absolute():
        return path
    # The long form takes ``..`` literally, so it's resolved first.
    text = ntpath.normpath(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def os_name() -> str:
    """The system's name and version, as ``platform.platform()`` gives it.
    That can fail on Windows: its WMI query sometimes comes back without the
    fields it reads (KeyError: 'ProductType', seen on a freshly reset VM,
    #155), and a bug report or a probe run isn't worth losing over it."""
    try:
        if sys.platform.startswith("linux"):
            return _linux_name()
        return platform.platform()
    except Exception:  # noqa: BLE001
        if sys.platform == "win32":  # platform.version() would ask WMI again
            version = ".".join(str(n) for n in sys.getwindowsversion()[:3])
            return f"Windows-{version} (platform.platform failed)"
        return sys.platform


def _linux_name() -> str:
    """"Debian GNU/Linux 13 (trixie), Linux-6.12.48-arm64-aarch64-with-
    glibc2.41, X11": the distribution (the kernel alone says little), the
    kernel and processor, and the display server, which decide what a bug
    report can mean."""
    try:
        pretty = platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        pretty = ""
    session = os.environ.get("XDG_SESSION_TYPE", "")
    session = {"x11": "X11", "wayland": "Wayland", "tty": ""}.get(session, session)
    return ", ".join(part for part in (pretty, platform.platform(), session) if part)


def set_program_name() -> None:
    """Linux: the name the app goes by in the session, before the first
    window. GTK otherwise takes the script's file name, so Orca called the
    app "__main__.py", and the window couldn't be matched to its desktop
    entry (``thechatplace.desktop``). Does nothing elsewhere."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    try:
        glib = ctypes.CDLL("libglib-2.0.so.0")
        glib.g_set_prgname.argtypes = [ctypes.c_char_p]
        glib.g_set_application_name.argtypes = [ctypes.c_char_p]
        glib.g_set_prgname(b"thechatplace")
        glib.g_set_application_name("The Chat Place".encode("utf-8"))
    except (OSError, AttributeError):
        pass


def restore_system_library_path() -> None:
    """Linux, built app: give the programs it starts the system's own
    libraries. PyInstaller's launcher points ``LD_LIBRARY_PATH`` at the
    libraries bundled with the app, and every child inherited it, so
    ``claude``, ``git``, ``xdg-open`` and speech-dispatcher would have loaded
    the app's copies of system libraries. The app itself is unaffected: the
    loader reads the variable once, when the process starts."""
    if not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        return
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def make_dpi_aware() -> None:
    """Windows: draw the app at the screen's own scaling (#185). Unaware,
    Windows drew it at 100% and stretched the picture, so at 150% and above
    its text was soft. Must run before the first window is made; the
    app's fixed sizes then go through ``ui.sizes`` to grow with the scaling.
    Does nothing elsewhere: a Mac scales the app itself."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2):
            return
    except (AttributeError, OSError):
        pass
    try:  # Windows before 10 1703: system aware (wx can't follow per-monitor v1)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def app_icon_path() -> Path:
    """The app's icon (#64), shipped with the package (``assets``)."""
    return Path(__file__).resolve().parent / "assets" / "app.ico"


def user_guide_path() -> Path:
    """The user guide's Markdown (#102), shipped in ``assets`` like the icon so
    every build has it; tools/make_docs.py makes the docs/ copies from it."""
    return Path(__file__).resolve().parent / "assets" / "user-guide.md"


def app_data_dir() -> Path:
    """The Chat Place's own settings and session store."""
    return _roaming_dir() / APP_DIR_NAME


def codex_approval_shell(executable: str) -> str:
    """Only known native shell wrappers can be remembered (#307)."""
    if sys.platform == 'win32':
        trusted = [_windows_program('System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')]
        program_files = os.environ.get('ProgramFiles') or ''
        if ntpath.isabs(program_files):
            trusted += [ntpath.join(program_files, 'PowerShell', '7', 'pwsh.exe'),
                        ntpath.join(program_files, 'PowerShell', '7-preview', 'pwsh.exe')]
        if ntpath.normcase(executable) in {ntpath.normcase(p) for p in trusted}:
            return 'powershell'
        # PowerShell from the Microsoft Store: Codex reports its app alias, so
        # without this nothing could be remembered on such a PC. The folder is
        # the user's own, unlike those above, so the file must be the real alias.
        local = os.environ.get('LOCALAPPDATA') or ''
        if ntpath.isabs(local):
            store = ntpath.join(local, 'Microsoft', 'WindowsApps', 'pwsh.exe')
            if ntpath.normcase(executable) == ntpath.normcase(store) and _is_store_alias(store):
                return 'powershell'
    elif executable in ('/bin/bash', '/bin/zsh', '/bin/sh', '/usr/bin/bash', '/usr/bin/zsh', '/usr/bin/sh'):
        return 'posix'
    return ''


def _is_store_alias(path: str) -> bool:
    """A Microsoft Store app alias, not a program dropped in its place."""
    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_APPEXECLINK
    except (OSError, AttributeError):
        return False


def codex_approval_folder(cwd: str, chat_folder: str) -> str:
    """Remember only the exact existing local chat folder, never a child/alias."""
    paths = [Path(value) for value in (cwd, chat_folder)]
    if any(not p.is_absolute() or str(p).startswith(('\\\\', '//')) for p in paths):
        raise ValueError('Local absolute folders required')
    resolved = [p.resolve(strict=True) for p in paths]
    if resolved[0] != resolved[1] or not resolved[0].is_dir():
        raise ValueError('Remembered approvals require the exact chat folder')
    return str(resolved[0])


def codex_diagnostic_path() -> Path:
    return app_data_dir() / "codex-errors.log"


def default_projects_root() -> Path:
    """Where the New Session folder picker starts: the GitHub folder (in
    the home folder, or in Documents as GitHub Desktop makes it on a Mac)."""
    for github in (Path.home() / "GitHub", Path.home() / "Documents" / "GitHub"):
        if github.is_dir():
            return github
    return Path.home()


def clone_root() -> Path:
    """Where New Session's From GitHub clones (#154): the GitHub folder,
    made in the home folder if there isn't one, never the home folder itself."""
    root = default_projects_root()
    return root if root.name == "GitHub" else Path.home() / "GitHub"


_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def encode_cwd(cwd: str) -> str:
    """The folder name Claude Code uses for a working directory's transcripts.

    Every character that is not an ASCII letter or digit becomes ``-``, so
    ``C:\\Users\\kelly\\GitHub\\QuickMail`` is ``C--Users-kelly-GitHub-QuickMail``.
    Verified against every transcript on Kelly's PC on 2026-10-06 (87 of 87
    existing transcripts matched; the rest had been deleted by retention).
    """
    return _NON_ALNUM.sub("-", cwd or "")


# Session ids end up in file names, glob patterns and command lines. Refuse
# anything that is not a plain id, so a malformed metadata file cannot point us
# at another file, and an id can never be read as a command-line flag (it must
# not start with "-").
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


def direct_transcript_path(cwd: str, cli_session_id: str,
                           root: Optional[Path] = None) -> Path:
    """Where Claude Code puts a session's transcript: its folder's encoded
    name, then its id. It may not be there (see ``transcript_path``)."""
    return (root or projects_dir()) / encode_cwd(cwd) / f"{cli_session_id}.jsonl"


def transcript_path(cwd: str, cli_session_id: str,
                    root: Optional[Path] = None) -> Optional[Path]:
    """Find a session's transcript, or None when it no longer exists.

    The encoded-cwd mapping is tried first; if it misses (a format change, a
    very long path Claude Code shortened), every project folder is searched for
    the session id, which is unique on its own.
    """
    if not cli_session_id or not _SAFE_ID.match(cli_session_id):
        return None
    root = root or projects_dir()
    direct = direct_transcript_path(cwd, cli_session_id, root)
    if direct.is_file():
        return direct
    try:
        for candidate in root.glob(f"*/{cli_session_id}.jsonl"):
            if candidate.is_file():
                return candidate
    except OSError:
        pass
    return None




def is_safe_id(value: str) -> bool:
    return bool(value) and bool(_SAFE_ID.match(value))


def is_specific_folder(value: str) -> bool:
    """A full path to a folder below a drive or share: ``C:\\work`` or
    ``\\\\server\\share\\work`` on Windows, ``/Users/me/work`` on a Mac. Never a
    whole drive (``C:\\``), a share's root, the Mac's root or a Windows device
    path (``\\\\.\\`` or ``\\\\?\\``), which would give far more than one folder (#334)."""
    if sys.platform == "win32":
        if value.startswith(("\\\\.\\", "\\\\?\\", "//./", "//?/")):
            return False
        drive, rest = ntpath.splitdrive(value)
        return bool(drive) and ntpath.isabs(value) and bool(rest.strip("\\/"))
    return value.startswith("/") and bool(value.strip("/"))


#: ``process_start`` for a process you may not look at: not one of yours,
#: so not a Claude Code you started (a system service, say).
NOT_YOURS = -1


def process_start(pid: int) -> Optional[int]:
    """When a running process started, as a Windows FILETIME (100 ns since
    1601): what Claude Code writes as ``procStart`` in its pid files on
    Windows (on Linux it writes ``ps``'s start time, which
    ``sessions._same_process`` compares with this). ``NOT_YOURS`` when
    Windows won't say (access denied), or on Linux when another user owns
    it; None when it can't be known (a Mac, no such process)."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _linux_process_start(pid)
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Limited information is granted for all of your own processes,
        # elevated ones too: access denied means someone else's.
        return NOT_YOURS if ctypes.get_last_error() == 5 else None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        created = times[0]
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


#: A running process whose start can't be told (someone else's): it
#: started before anything, so a lock it may hold counts (#364).
STARTED_UNKNOWN = float("-inf")


def processes_started_at(pids) -> dict:
    """{pid: when it started, as a Unix time} for each running process among
    ``pids``; ``STARTED_UNKNOWN`` for one that's running but won't say (on
    Windows, another user's); left out if it isn't running. Used to tell a
    lock file a live process wrote from one left by a process whose id was
    handed out again (#364)."""
    pids = sorted({pid for pid in pids if isinstance(pid, int) and pid > 0})
    if not pids:
        return {}
    if sys.platform == "win32":
        every = _windows_process_starts()
        if every is not None:
            return {pid: every[pid] for pid in pids if pid in every}
        # The system list wasn't available: ask each process. One that won't
        # say (another user's) can't be ruled out, so it counts.
        started = {}
        for pid in pids:
            if not pid_alive(pid):  # an exited process still answers while a handle is held
                continue
            filetime = process_start(pid)
            started[pid] = (STARTED_UNKNOWN if filetime is None or filetime == NOT_YOURS
                            else filetime / 10_000_000 - 11_644_473_600)
        return started
    import subprocess
    import time as time_module
    # Before ps runs: a slow ps then makes a process look older, never newer
    # than a lock it wrote.
    now = time_module.time()
    try:
        # One call for them all (stale locks pile up), by full path, and
        # elapsed time rather than the start date: its format doesn't depend
        # on the locale.
        out = subprocess.run(["/bin/ps", "-o", "pid=,etime=", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {pid: STARTED_UNKNOWN for pid in pids if pid_alive(pid)}
    started = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) in pids:
            seconds = etime_seconds(parts[1])
            started[int(parts[0])] = STARTED_UNKNOWN if seconds is None else now - seconds
    return started


def _windows_process_starts() -> Optional[dict]:
    """{pid: start, as a Unix time} for every running process, from one
    NtQuerySystemInformation(SystemProcessInformation) call, as Task Manager
    and psutil read it: it needs no access to any of them, so a process of
    another user's (about half of them: services) has its real start too,
    rather than an unknown one that would make every stale Copilot lock
    whose id it reused count (#372). None if the call fails."""
    import ctypes

    try:
        ntdll = ctypes.WinDLL("ntdll")
    except OSError:
        return None
    query = ntdll.NtQuerySystemInformation
    query.restype = ctypes.c_long
    query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                      ctypes.POINTER(ctypes.c_ulong)]
    SYSTEM_PROCESS_INFORMATION = 5
    STATUS_INFO_LENGTH_MISMATCH = -1073741820  # 0xC0000004
    size = ctypes.c_ulong(512 * 1024)
    for _ in range(8):
        buffer = ctypes.create_string_buffer(size.value)
        status = query(SYSTEM_PROCESS_INFORMATION, buffer, size, ctypes.byref(size))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = ctypes.c_ulong(size.value + 64 * 1024)  # it grew meanwhile
            continue
        if status != 0:
            return None
        break
    else:
        return None
    data = buffer.raw
    pointer = ctypes.sizeof(ctypes.c_void_p)
    # SYSTEM_PROCESS_INFORMATION: CreateTime at 32; UniqueProcessId after
    # ImageName (a UNICODE_STRING at 56) and BasePriority.
    pid_at = 80 if pointer == 8 else 68
    starts, offset = {}, 0
    while offset + pid_at + pointer <= len(data):
        following = int.from_bytes(data[offset:offset + 4], "little")
        created = int.from_bytes(data[offset + 32:offset + 40], "little", signed=True)
        pid = int.from_bytes(data[offset + pid_at:offset + pid_at + pointer], "little")
        if pid and created > 0:
            starts[pid] = created / 10_000_000 - 11_644_473_600
        if not following:
            break
        offset += following
    # A guard on the layout: this process must be there, with the start
    # Windows gives for it directly; otherwise the careful way is used.
    own = process_start(os.getpid())
    if (own is None or own == NOT_YOURS or os.getpid() not in starts
            or abs(starts[os.getpid()] - (own / 10_000_000 - 11_644_473_600)) > 1):
        return None
    return starts


def etime_seconds(text: str) -> Optional[int]:
    """``ps``'s elapsed time (``mm:ss``, ``hh:mm:ss`` or ``dd-hh:mm:ss``) in
    seconds, or None if it isn't one."""
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", (text or "").strip())
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


#: Seconds from 1601 (FILETIME's start) to 1970.
_FILETIME_EPOCH_S = 11_644_473_600


def _linux_process_start(pid: int, proc: str = "/proc") -> Optional[int]:
    """``process_start`` from /proc: the start time in clock ticks after
    boot (field 22 of ``stat``), plus the boot time (``btime``)."""
    try:
        if os.stat(f"{proc}/{pid}").st_uid != os.getuid():
            return NOT_YOURS
        with open(f"{proc}/{pid}/stat", "rb") as handle:
            stat_line = handle.read().decode("ascii", "replace")
        # The name, in brackets, can hold spaces and brackets of its own.
        fields = stat_line[stat_line.rindex(")") + 2:].split()
        ticks = int(fields[19])
        with open(f"{proc}/stat", "rb") as handle:
            boot = next(int(line.split()[1]) for line in handle.read().decode().splitlines()
                        if line.startswith("btime "))
        seconds = boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return int((seconds + _FILETIME_EPOCH_S) * 10_000_000)


def pid_alive(pid: int) -> bool:
    """True if a process with this id is running.

    Not ``os.kill(pid, 0)``: on Windows signal 0 is CTRL_C_EVENT, and that call
    would interrupt the process instead of probing it.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Access denied still means the process exists.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def running_process_names() -> set:
    """Lower-case executable names of the processes running in this Windows
    session, e.g. ``nvda.exe``; on Linux, of this user's processes (``orca``).

    Windows and Linux (an empty set on a Mac): speech uses it to tell "no screen
    reader is running" from "a screen reader is running but didn't answer"
    (#98), and those two must not be confused, because only the first may fall
    back to a Windows voice. Only this session counts: JAWS and NVDA also run
    at the sign-in screen and in other users' sessions (fast user switching,
    Remote Desktop), and those can't speak to this user, so counting them
    would silence every announcement. Never raises; a failure is an empty set.
    """
    if sys.platform.startswith("linux"):
        return set(_linux_processes().values())
    if sys.platform != "win32":
        return set()
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        TH32CS_SNAPPROCESS = 0x2
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(kernel32, name).argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(PROCESSENTRY32W)]
            getattr(kernel32, name).restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        mine = wintypes.DWORD()
        if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(mine)):
            return set()
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return set()
        names = set()
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            session = wintypes.DWORD()
            while ok:
                # A process whose session can't be read (a protected system
                # one) isn't ours.
                if (kernel32.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(session))
                        and session.value == mine.value):
                    names.add(entry.szExeFile.lower())
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return names
    except Exception:  # noqa: BLE001 - best effort, see the docstring
        return set()


def process_ids(name: str) -> set:
    """Linux: the ids of this user's processes called ``name`` (an empty set
    elsewhere). Speech remembers which Orca had no D-Bus service by its id."""
    if not sys.platform.startswith("linux"):
        return set()
    return {pid for pid, each in _linux_processes().items() if each == name}


def _linux_processes(proc: str = "/proc") -> dict:
    """This user's processes, id to lower-case name: another user's Orca (on
    the login screen, say) can't speak to this one. A script started by its
    #! line is named after the script, so Orca, a Python program, is "orca"."""
    found = {}
    me = os.getuid()
    try:
        entries = os.listdir(proc)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"{proc}/{entry}").st_uid != me:
                continue
            with open(f"{proc}/{entry}/comm", "rb") as handle:
                found[int(entry)] = handle.read().decode("utf-8", "replace").strip().lower()
        except OSError:
            continue
    return found


def windows_locked() -> bool:
    """True while Windows is locked or showing a secure screen (a UAC prompt,
    Ctrl+Alt+Del). Screen readers keep running there, and NV Access asks apps
    not to send them text then, so a session's reply isn't read out on the
    lock screen (#98). False on other systems, and whenever it can't tell:
    a wrong True would silence every announcement.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        wtsapi = ctypes.WinDLL("wtsapi32")
        wtsapi.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        wtsapi.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        WTS_CURRENT_SESSION = 0xFFFFFFFF
        WTSSessionInfoEx = 25
        WTS_SESSIONSTATE_LOCK = 0
        buffer, size = ctypes.c_void_p(), wintypes.DWORD()
        if wtsapi.WTSQuerySessionInformationW(None, WTS_CURRENT_SESSION, WTSSessionInfoEx,
                                              ctypes.byref(buffer), ctypes.byref(size)):
            try:
                # WTSINFOEXW: a DWORD level, then the level-1 union, aligned to
                # 8 (it holds LARGE_INTEGERs): SessionId at 8, SessionState at
                # 12, SessionFlags at 16.
                if size.value >= 20:
                    flags = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_long))[4]
                    if flags == WTS_SESSIONSTATE_LOCK:
                        return True
            finally:
                wtsapi.WTSFreeMemory(buffer)
        # A secure screen while unlocked: the input desktop isn't ours, and
        # opening it is refused. Only that refusal counts, not other failures.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        DESKTOP_READOBJECTS = 0x0001
        desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not desktop:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
        user32.CloseDesktop(desktop)
        return False
    except Exception:  # noqa: BLE001 - can't tell, so not locked
        return False


def open_url(url: str) -> None:
    """Hand a URL to the shell (the Claude desktop app owns ``claude://``)."""
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        import subprocess

        subprocess.Popen(["open", url])
    else:
        import subprocess

        subprocess.Popen(["xdg-open", url])


def _windows_program(*parts: str) -> str:
    """A Windows program by its full path, never found by searching the
    current folder or the app's own first."""
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", *parts)


def edit_file(path: Path) -> None:
    """Open a text file in your own editor (What AI Knows About You's
    Edit, #92, #305): The Chat Place itself never writes to these files.
    Windows tries the file type's Edit verb, then Open, then Notepad, since
    a ``.md`` file often has no program set for it. Raises OSError if none
    of them could start."""
    import subprocess

    if sys.platform == "win32":
        for verb in ("edit", "open"):
            try:
                os.startfile(str(path), verb)  # type: ignore[attr-defined]  # noqa: S606
                return
            except OSError:
                continue
        subprocess.Popen([_windows_program("System32", "notepad.exe"), str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-t", str(path)])  # the default text editor
    else:
        subprocess.Popen(["xdg-open", str(path)])


def show_in_folder(path: Path) -> None:
    """Show a file selected in Explorer, the Finder or the file manager."""
    import subprocess

    if sys.platform == "win32":
        # One command line, so the path keeps its own quotes: given as a list
        # item, Python quotes "/select,C:\a b\x.md" whole, and Explorer
        # opens Documents instead.
        subprocess.Popen(f'"{_windows_program("explorer.exe")}" /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        # The file selected, through the file manager's standard interface
        # (Files, Dolphin, Caja, Nemo, Thunar); without one, its folder.
        uri = Path(path).resolve().as_uri()
        dbus_send = find_tool("dbus-send")
        # On a thread: dbus-send waits for a file manager that may be slow to
        # start, and this is called from the UI thread.
        def show():
            if dbus_send:
                try:
                    done = subprocess.run(
                        [dbus_send, "--session", "--print-reply",
                         "--dest=org.freedesktop.FileManager1", "--type=method_call",
                         "/org/freedesktop/FileManager1",
                         "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}", "string:"],
                        capture_output=True, timeout=10)
                    if done.returncode == 0:
                        return
                except (OSError, subprocess.SubprocessError):
                    pass  # no file manager answered: open the folder
            try:
                subprocess.Popen(["xdg-open", str(Path(path).parent)])
            except OSError:
                pass

        import threading
        threading.Thread(target=show, name="show-in-folder", daemon=True).start()


class ClaudeLookup:
    """Where ``claude`` is, or why it can't be used: ``reason`` in a sentence
    or two, short enough to be spoken, and ``install_help`` with the
    commands, for Claude Code Sign-in's dialog."""

    def __init__(self, path: Optional[str] = None, reason: str = "",
                 install_help: str = "") -> None:
        self.path = path
        self.reason = reason
        self.install_help = install_help

    @property
    def problem(self) -> str:
        """The reason and where to go about it, for New Session, Send and
        the rest, which can't install Claude Code themselves."""
        if not self.reason:
            return ""
        return f"{self.reason} To install it, choose Claude Code Sign-in on the Help menu."


_SCRIPT_SUFFIXES = (".cmd", ".bat", ".ps1")

#: Claude Code's own install commands (https://code.claude.com/docs/en/setup),
#: shown when it can't be found and run by Claude Code Sign-in.
INSTALL_SH = "curl -fsSL https://claude.ai/install.sh | bash"
INSTALL_PS1 = "irm https://claude.ai/install.ps1 | iex"
INSTALL_CMD = ("curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd "
               "&& del install.cmd")


def claude_install_help() -> str:
    """How to install the native Claude Code, for a message box."""
    if sys.platform == "win32":
        commands = (f"In PowerShell:\n    {INSTALL_PS1}\n"
                    f"Or in Command Prompt:\n    {INSTALL_CMD}")
    elif sys.platform == "darwin":
        commands = f"In Terminal:\n    {INSTALL_SH}"
    else:
        commands = f"In a terminal:\n    {INSTALL_SH}"
    return (f"To install it, run its native installer. {commands}\n"
            "Then run claude once to sign in, or use Claude Code Sign-in here again.")


def _native_candidates() -> List[Path]:
    """Where the native ``claude`` is when it isn't on the PATH. An app
    started from the Finder gets only /usr/bin:/bin:/usr/sbin:/sbin, so
    on a Mac the Homebrew and npm folders are looked in by name; on Windows
    a PATH change made by an installer reaches the app only after signing
    out and in again; on Linux an app started from the desktop's menu gets
    the session's PATH, without what the shell's profile adds."""
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return [home / ".local" / "bin" / "claude.exe",
                Path(local) / "Microsoft" / "WinGet" / "Links" / "claude.exe"]
    if sys.platform == "darwin":
        return [home / ".local" / "bin" / "claude",
                Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude"),
                home / ".npm-global" / "bin" / "claude"]
    # Linux: the native installer's folder, which a desktop session's PATH
    # often lacks (it's added by the shell's profile), then the usual others.
    return [home / ".local" / "bin" / "claude", Path("/usr/local/bin/claude"),
            home / ".npm-global" / "bin" / "claude",
            Path("/home/linuxbrew/.linuxbrew/bin/claude"), Path("/usr/bin/claude")]


#: What an npm shim runs: ``"%dp0%\node_modules\...\claude.exe"   %*``. Only a
#: claude.exe: an old shim also names the node.exe that runs cli.js.
_SHIM_TARGET = re.compile(r'"%~?dp0%?\\((?:[^"%]*?\\)?claude\.exe)"', re.IGNORECASE)


def _native_behind_shim(script: str) -> Optional[str]:
    """The native ``claude.exe`` an npm ``claude.cmd`` starts. Newer npm
    installs of Claude Code put the same native program in node_modules, and
    started directly it takes its arguments as they are, so it is as safe as
    the native installer's."""
    try:
        text = Path(script).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _SHIM_TARGET.search(text)
    if not match:
        return None
    exe = Path(script).parent / match.group(1).replace("\\", "/")
    return str(exe) if exe.is_file() else None


def _is_node_script(path: str) -> bool:
    """An old npm install's ``claude``: a JavaScript file run by Node, which
    an app started from the Finder can't find."""
    try:
        with open(path, "rb") as f:
            first = f.read(128)
    except OSError:
        return False
    return first.startswith(b"#!") and b"node" in first.split(b"\n", 1)[0]


def find_claude(which=None, native_candidates=None) -> ClaudeLookup:
    """Find the native ``claude`` executable.

    A ``claude.cmd`` / ``.bat`` (the npm install) is never run: Windows runs
    those through cmd.exe, which re-parses the command line, so a session
    title containing ``&`` or ``"`` could run a second command. The native
    installer's ``claude.exe`` takes its arguments as they are, and so does the
    native program a newer npm install's shim starts, which is used instead.
    """
    import shutil

    which = which or shutil.which
    if native_candidates is None:
        native_candidates = _native_candidates()
    found = which("claude")
    if found and not found.lower().endswith(_SCRIPT_SUFFIXES) and not _is_node_script(found):
        return ClaudeLookup(found)
    if found and found.lower().endswith(_SCRIPT_SUFFIXES):
        native = _native_behind_shim(found)
        if native:
            return ClaudeLookup(native)
    for candidate in native_candidates:
        if Path(candidate).is_file() and not _is_node_script(str(candidate)):
            return ClaudeLookup(str(candidate))
    if found:
        return ClaudeLookup(None, (
            "The claude command on this computer is an older npm install of Claude Code, "
            "which runs as a script. The Chat Place needs the native claude program, "
            f"because a script would let a session title be read as a command. ({found})"),
            claude_install_help())
    return ClaudeLookup(None, (
        "Claude Code isn't installed, or The Chat Place can't find it. The Claude desktop "
        "app's own copy isn't on the PATH and isn't meant for other programs."),
        claude_install_help())


def claude_executable() -> Optional[str]:
    """Path to the native ``claude`` command, or None (see ``find_claude``)."""
    return find_claude().path


def find_tool(name: str, which=None) -> Optional[str]:
    """The ``git`` or ``gh`` program (#154), or None. Only a real program:
    a ``.cmd`` or ``.bat`` would be run through cmd.exe, which re-reads its
    arguments (a branch name, say) as a command line. As for ``claude``, an
    app started from the Finder has no Homebrew folder on its PATH, so a Mac
    looks there by name."""
    import shutil

    found = (which or shutil.which)(name)
    if sys.platform == "darwin":
        # /usr/bin/git is only a stand-in until Apple's Command Line Tools
        # are installed, and running it asks to install them.
        candidates = [found] if found else []
        candidates += [str(Path(f) / name) for f in ("/opt/homebrew/bin", "/usr/local/bin",
                                                     "/usr/bin")]
        for candidate in candidates:
            if Path(candidate).is_file() and (not candidate.startswith("/usr/bin/")
                                              or _command_line_tools_installed()):
                return candidate
        return None
    if found and not found.lower().endswith(_SCRIPT_SUFFIXES):
        return found
    return None


_CLT = None


def _command_line_tools_installed() -> bool:
    """Whether a Mac has Apple's developer tools (asked once), without
    the dialog offering to install them."""
    global _CLT
    if _CLT is None:
        import subprocess
        try:
            _CLT = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True,
                                  timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _CLT = False
    return _CLT


def run_in_terminal(argv: List[str], script_name: str = "", title: str = "",
                    env: Optional[dict] = None, clear: Iterable[str] = (),
                    clear_prefixes: Iterable[str] = (), export: Iterable[str] = ()):
    """Run a command where the user can see and answer it: a new console
    window on Windows, a Terminal window on a Mac (from a ``.command`` file
    in the app's own folder, so no permission to control Terminal is
    needed; started from the app it would have no window at all), and on
    Linux a terminal window running the same script (``linux_terminal``
    picks the program). Returns the Popen on Windows, to wait for; None
    elsewhere, where the window outlives the program that opened it (GNOME
    Terminal hands the command to its server and exits at once). ``env`` is
    the Windows window's environment; Terminal starts the user's own shell,
    whose profile may set the variables ``env`` leaves out, and a Linux
    terminal's server has an environment of its own, so the script unsets
    ``clear`` and ``clear_prefixes``. Names in ``export`` are copied from
    ``env`` into the script, or unset when absent so the terminal's shell
    cannot supply a different value. Raises OSError if Linux has no
    terminal program."""
    import shlex
    import subprocess

    if sys.platform == "win32":
        return subprocess.Popen(argv, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
    folder = app_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / script_name
    lines = ["#!/bin/bash"]
    for name in sorted(export):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            if env is not None and name in env:
                lines.append(f"export {name}={shlex.quote(str(env[name]))}")
            else:
                lines.append(f"unset {name}")
    names = sorted(n for n in clear if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n))
    if names:
        lines.append("unset " + " ".join(names))
    patterns = "|".join(f"{p}*" for p in clear_prefixes
                        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p))
    if patterns:
        lines.append(f'for v in $(compgen -e); do case "$v" in {patterns}) unset "$v";; '
                     'esac; done')
    lines += [f"echo {shlex.quote(title)}", shlex.join(argv), "status=$?", "echo",
              'if [ "$status" -eq 0 ]; then',
              "  echo 'Finished. You can close this window and go back to The Chat Place.'",
              "else",
              '  echo "It didn\'t finish (exit code $status). You can close this window."',
              "fi"]
    terminal = None
    if sys.platform != "darwin":
        # A Linux terminal closes when its command ends, before the result
        # could be read.
        lines.append("read -r -p 'Press Enter to close this window.' _")
        terminal = linux_terminal(str(script))
        if terminal is None:
            raise OSError("no terminal program was found. Install one (GNOME Terminal, "
                          "Konsole, MATE Terminal or another), or run this in yours: "
                          + shlex.join(argv))
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o700)
    if terminal is None:
        subprocess.Popen(["open", "-a", "Terminal", str(script)])
    else:
        subprocess.Popen(terminal, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return None


#: Linux terminal programs, and what goes before the command each runs.
#: Those with good screen reader support (the VTE terminals: GNOME Terminal,
#: Console, Ptyxis, MATE, Xfce, Tilix; and Konsole) come before the rest;
#: xterm, which Orca can't read, is last.
_LINUX_TERMINALS = [
    ("xdg-terminal-exec", []),          # the user's own choice, where it's set up
    ("gnome-terminal", ["--"]), ("kgx", ["--"]), ("ptyxis", ["--"]),
    ("mate-terminal", ["-x"]), ("xfce4-terminal", ["-x"]), ("konsole", ["-e"]),
    ("tilix", ["-e"]), ("lxterminal", ["-e"]), ("qterminal", ["-e"]),
    ("x-terminal-emulator", ["-e"]),    # Debian's choice of terminal
    ("alacritty", ["-e"]), ("kitty", []), ("foot", []), ("wezterm", ["start", "--"]),
    ("xterm", ["-e"]),
]
#: The terminal that comes with each desktop, tried first on it.
_DESKTOP_TERMINALS = {"kde": "konsole", "mate": "mate-terminal", "xfce": "xfce4-terminal",
                      "lxqt": "qterminal", "lxde": "lxterminal"}


def linux_terminal(script: str, which=None, desktop: Optional[str] = None) -> Optional[List[str]]:
    """The command that opens a terminal window running ``script``, or None
    when there's no terminal program."""
    import shutil

    which = which or shutil.which
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") if desktop is None else desktop).lower()
    order = list(_LINUX_TERMINALS)
    own = [program for name, program in _DESKTOP_TERMINALS.items() if name in desktop.split(":")]
    # The desktop's own next, after only the user's choice (xdg-terminal-exec).
    order.sort(key=lambda entry: (entry[0] != "xdg-terminal-exec", entry[0] not in own))
    for program, before in order:
        found = which(program)
        if found:
            return [found, *before, script]
    return None


def start_claude_install(env: Optional[dict] = None):
    """Run Claude Code's native installer in its own window (see
    ``run_in_terminal`` for what's returned)."""
    if sys.platform == "win32":
        powershell = _windows_program("System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        # In a scriptblock and a try, so an exit or error in the installer
        # leaves the window open to be read.
        return run_in_terminal(
            [powershell, "-NoProfile", "-Command",
             "try { & ([scriptblock]::Create((irm https://claude.ai/install.ps1))) } "
             "catch { Write-Host $_ -ForegroundColor Red } "
             "finally { Read-Host 'Finished. Press Enter to close this window' }"],
            env=env)
    return run_in_terminal(["/bin/bash", "-c", INSTALL_SH], "install-claude-code.command",
                           "Installing Claude Code with its native installer.")


CREATE_SUSPENDED = 0x00000004


class ProcessTree:
    """Lets a child process be killed together with everything it started.

    On Windows the child goes into a Job Object with KILL_ON_JOB_CLOSE, so
    terminating the job, closing it when the turn ends, or The Chat Place
    exiting ends the whole tree: claude runs tools as child processes, and
    killing only claude.exe would leave a long build or test run going. It
    also means anything Claude started and left running ends with the turn.

    The child is created suspended and only resumed once it is in the job, so
    nothing it starts can escape the job in between. If the job can't be set
    up the child is still resumed, and ``kill`` falls back to
    ``taskkill /T /F``. Elsewhere the child gets its own process group.
    """

    def __init__(self) -> None:
        self._job = None
        self._pid: Optional[int] = None

    @staticmethod
    def popen_kwargs() -> dict:
        if sys.platform == "win32":
            return {"creationflags": hidden_window_flags() | CREATE_SUSPENDED}
        return {"start_new_session": True}

    def attach(self, process) -> None:
        """Put a (suspended) child into the job, then let it run. Raises if a
        real Windows child can't be resumed, after killing it, so a turn never
        hangs on a process that was never started."""
        self._pid = getattr(process, "pid", None)
        handle = getattr(process, "_handle", None)
        if sys.platform != "win32" or not isinstance(self._pid, int) or handle is None:
            return
        try:
            self._job = _create_kill_on_close_job()
            if self._job is not None and not _assign_to_job(self._job, int(handle)):
                _close_handle(self._job)
                self._job = None
        except Exception:  # noqa: BLE001 - fall back to taskkill
            self._job = None
        if not _resume_process(int(handle)):
            try:
                process.kill()
            except OSError:
                pass
            raise OSError("Couldn't start claude (it could not be resumed).")

    def kill(self) -> None:
        if self._pid is None:
            return
        if sys.platform == "win32":
            if self._job is not None and _kernel32().TerminateJobObject(self._job, 1):
                return
            import subprocess

            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self._pid)],
                           capture_output=True, creationflags=hidden_window_flags())
            return
        import signal

        try:
            os.killpg(self._pid, signal.SIGKILL)
        except OSError:
            pass

    def holds(self, pid: int) -> bool:
        """Whether a process is this one or anything it started. VS Code's
        ``copilot`` command is a launcher that keeps running with the real
        Copilot as its child (#463). If it can't be told, the answer is no."""
        if pid == self._pid:
            return True
        if sys.platform == "win32":
            return self._job is not None and pid in _job_process_ids(self._job)
        try:
            return self._pid is not None and os.getpgid(pid) == self._pid
        except OSError:
            return False

    def close(self) -> None:
        """Close the job. KILL_ON_JOB_CLOSE ends anything still in it."""
        if self._job is not None:
            _close_handle(self._job)
            self._job = None


_K32 = None


def _kernel32():
    """kernel32 with argument and result types declared, so 64-bit handles
    are passed whole instead of being truncated to C ints."""
    global _K32
    if _K32 is not None:
        return _K32
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                          wintypes.LPVOID, wintypes.DWORD]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    _K32 = k
    return k


def _resume_process(handle: int) -> bool:
    """Resume a process created with CREATE_SUSPENDED (its only thread)."""
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    return ntdll.NtResumeProcess(handle) == 0


def _create_kill_on_close_job():
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k = _kernel32()
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(job, 9,  # JobObjectExtendedLimitInformation
                                     ctypes.byref(info), ctypes.sizeof(info)):
        _close_handle(job)
        return None
    return job


def _job_process_ids(job, room: int = 1024) -> frozenset:
    """The process ids in a job (none if Windows won't say)."""
    import ctypes
    from ctypes import wintypes

    class PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                    ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * room)]

    found = PROCESS_ID_LIST()
    query = _kernel32().QueryInformationJobObject
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                      wintypes.LPVOID]
    query.restype = wintypes.BOOL
    if not query(job, 3, ctypes.byref(found), ctypes.sizeof(found), None):  # BasicProcessIdList
        return frozenset()
    return frozenset(found.ProcessIdList[:found.NumberOfProcessIdsInList])


def _assign_to_job(job, process_handle: int) -> bool:
    return bool(_kernel32().AssignProcessToJobObject(job, process_handle))


def _close_handle(handle) -> None:
    _kernel32().CloseHandle(handle)


def bring_window_forward(title_matches) -> bool:
    """Bring the first top-level window whose title satisfies ``title_matches``
    to the front. Windows only; returns False elsewhere or if none matched."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if title_matches(buffer.value):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    return bool(user32.SetForegroundWindow(hwnd))


def bring_last_popup_forward(hwnd: int) -> bool:
    """Bring forward ``hwnd``'s open dialog: the frontmost visible, enabled
    window it owns, directly or through another dialog, so the innermost of
    nested dialogs, native message and file dialogs included (#103). For a
    main window that was activated while disabled behind a dialog. Windows
    only; False elsewhere, or if there is none.

    Not GetLastActivePopup: activating the main window makes it its own last
    active popup, so that answered with the main window (seen in the VM)."""
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    gw_owner = 4
    found = []

    def owned_by_main(window) -> bool:
        for _ in range(8):  # a dialog over a dialog over the main window
            window = user32.GetWindow(window, gw_owner)
            if not window:
                return False
            if window == hwnd:
                return True
        return False

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(window, _lparam):
        # EnumWindows goes front to back, so the first match is the front one.
        if (window != hwnd and user32.IsWindowVisible(window)
                and user32.IsWindowEnabled(window) and owned_by_main(window)):
            found.append(window)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return bool(found) and bool(user32.SetForegroundWindow(found[0]))


def high_contrast() -> Optional[bool]:
    """Whether Windows' High Contrast is on, for bug reports (#464); None
    where there's no such setting or it can't be read."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class HIGHCONTRASTW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD),
                    ("lpszDefaultScheme", wintypes.LPWSTR)]
    value = HIGHCONTRASTW(ctypes.sizeof(HIGHCONTRASTW), 0, None)
    try:
        # SPI_GETHIGHCONTRAST; HCF_HIGHCONTRASTON is bit 0.
        if not ctypes.windll.user32.SystemParametersInfoW(0x0042, ctypes.sizeof(value),
                                                          ctypes.byref(value), 0):
            return None
    except (OSError, AttributeError):
        return None
    return bool(value.dwFlags & 1)


def hidden_window_flags() -> int:
    """creationflags that keep a console window from flashing up on Windows."""
    if sys.platform == "win32":
        import subprocess

        return subprocess.CREATE_NO_WINDOW
    return 0


# -- thechatplace:// links (#144) --------------------------------------------------------
#
# A second copy of The Chat Place started with a link hands it to the copy
# that's running and exits. It writes the link as a small file in the app's
# own data folder (``links``), which only this user can write to (%APPDATA% on
# Windows; on a Mac the folder is made private, and a file someone else owns
# is ignored). The running copy looks there every second, takes each file,
# deletes it, and treats its text as a link it was given: parsed strictly,
# and only ever able to select a session (links.py). No socket is opened, so
# nothing on the network, or another user on the same PC, can send one.

LINK_INBOX_NAME = "links"
#: A link file is one short line; anything bigger isn't one of ours.
LINK_FILE_MAX_BYTES = 1024
#: A link nobody took within this long is old news (the running copy closed
#: before it looked, say): it's deleted unopened. One left by a copy that
#: closed just then can still open in a copy started within this time.
LINK_FILE_STALE_SECONDS = 60


def link_inbox_dir() -> Path:
    return app_data_dir() / LINK_INBOX_NAME


def drop_link(text: str, folder: Optional[Path] = None) -> Path:
    """Leave ``text`` (a link) for the running copy. Written under a temporary
    name and renamed, so the running copy never reads half a file. Raises
    OSError if it can't be written."""

    folder = Path(folder or link_inbox_dir())
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(folder, 0o700)
    data = text.encode("utf-8")[:LINK_FILE_MAX_BYTES]
    name = secrets.token_hex(8)
    temporary = folder / f"{name}.tmp"
    final = folder / f"{name}.link"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    handle = os.open(temporary, flags, 0o600)
    try:
        os.write(handle, data)
    finally:
        os.close(handle)
    os.replace(temporary, final)
    return final


def take_dropped_links(folder: Optional[Path] = None, now: Optional[float] = None) -> List[str]:
    """The links other copies left (oldest first), each deleted as it's read.
    Stale, oversized, unreadable files, files that aren't plain files, and on
    a Mac files another user owns, are deleted (if possible) and ignored.
    Never raises."""
    import time

    folder = Path(folder or link_inbox_dir())
    now = time.time() if now is None else now
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if entry.name.endswith((".tmp", ".taken")):
            # Being written or read now, or left by a copy that died doing so.
            if now - info.st_mtime > LINK_FILE_STALE_SECONDS:
                _remove_quietly(entry.path)
            continue
        if not entry.name.endswith(".link"):
            continue
        if not stat.S_ISREG(info.st_mode):
            continue  # a folder or a link to elsewhere: not ours, left alone
        usable = (info.st_size <= LINK_FILE_MAX_BYTES
                  and now - info.st_mtime <= LINK_FILE_STALE_SECONDS
                  and (sys.platform == "win32" or info.st_uid == os.getuid()))
        # Claimed by renaming it first, so a file that can't be deleted (a
        # virus scanner holding it, say) is never read a second time.
        claimed = os.path.join(folder, f"{secrets.token_hex(8)}.taken")
        try:
            os.replace(entry.path, claimed)
        except OSError:
            continue
        text = None
        if usable:
            try:
                with open(claimed, "rb") as handle:
                    text = handle.read(LINK_FILE_MAX_BYTES + 1).decode("utf-8")
            except (OSError, UnicodeDecodeError):
                text = None
        _remove_quietly(claimed)
        if text is not None and len(text.encode("utf-8")) <= LINK_FILE_MAX_BYTES:
            found.append((info.st_mtime, entry.name, text.strip()))
    return [text for _when, _name, text in sorted(found)]


def _remove_quietly(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


#: Windows: the per-user registration of thechatplace:// (HKCU, never the
#: whole machine), made only by an installed copy (links.py decides).
URL_SCHEME_KEY = r"Software\Classes\thechatplace"


def url_scheme_command() -> Optional[str]:
    """The command Windows or Linux runs for a thechatplace:// link, or None
    if the scheme isn't registered for this user (or this is a Mac)."""
    if sys.platform.startswith("linux"):
        return _desktop_entry_exec()
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            URL_SCHEME_KEY + r"\shell\open\command") as key:
            value, _kind = winreg.QueryValueEx(key, "")
            return str(value)
    except OSError:
        return None


def register_url_scheme(command: str, icon: str) -> None:
    """Register thechatplace:// for this user: Windows runs ``command`` with
    the link. Raises OSError if the registry refuses. On Linux, the desktop
    entry (see ``write_desktop_entry``); ``icon`` is Windows'."""
    if sys.platform.startswith("linux"):
        write_desktop_entry(command)
        return
    if sys.platform != "win32":
        return
    import winreg

    root = winreg.HKEY_CURRENT_USER
    with winreg.CreateKey(root, URL_SCHEME_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:The Chat Place link")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(root, URL_SCHEME_KEY + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(root, URL_SCHEME_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)


def unregister_url_scheme() -> None:
    """Remove this user's thechatplace:// registration, if any. Never raises."""
    if sys.platform.startswith("linux"):
        for path in (desktop_entry_path(), _desktop_icon_path()):
            _remove_quietly(path)
        return
    if sys.platform != "win32":
        return
    import winreg

    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, URL_SCHEME_KEY + sub)
        except OSError:
            pass


def backup_session_store(path: Path) -> Path:
    """Keep a complete unique original before migrating TCP's own store."""
    import tempfile

    fd, name = tempfile.mkstemp(prefix="sessions-v1-backup-", suffix=".json",
                                dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        os.unlink(name)
        raise
    return Path(name)


def _codex_npm_candidates(roots) -> List[Path]:
    """Use upstream's native package layout without executing its JS shim.

    https://github.com/openai/codex/blob/main/codex-cli/bin/codex.js
    Both the older codex/codex and current bin/codex layouts are supported.
    """
    import platform

    arm = platform.machine().lower() in ("arm64", "aarch64")
    arch = "arm64" if arm else "x64"
    cpu = "aarch64" if arm else "x86_64"
    triple = cpu + ("-apple-darwin" if sys.platform == "darwin"
                    else "-pc-windows-msvc" if sys.platform == "win32"
                    else "-unknown-linux-musl")
    name = "codex.exe" if sys.platform == "win32" else "codex"
    package = "codex-" + sys.platform + "-" + arch
    candidates = []
    for root in roots:
        scope = root / "@openai"
        for vendor in (scope / "codex/vendor", scope / package / "vendor",
                       scope / "codex/node_modules/@openai" / package / "vendor"):
            candidates.extend(vendor / triple / folder / name for folder in ("bin", "codex"))
    return candidates


def codex_home() -> Path:
    """Codex's local profile, including a user's CODEX_HOME override. Only
    read, for What AI Knows About You (#305), so a relative or network
    CODEX_HOME is taken as given; ``_codex_package_candidates`` refuses those
    because it finds programs to run."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def agents_skills_dir() -> Path:
    """Your own skills shared by agents such as Codex (``~/.agents/skills``,
    #305), outside Codex home, so tests can point it elsewhere too."""
    return Path.home() / ".agents" / "skills"


def _codex_package_candidates(home: Path, name: str):
    """Codex's own installer packages under CODEX_HOME (#283).

    https://github.com/openai/codex/blob/main/codex-rs/app-server-daemon/README.md
    and scripts/install/install.ps1: the installer unpacks each release into a
    staging folder, moves it into releases/, then retargets the ``current``
    link. Only ``current`` is used, so a half-written release is never chosen
    and no version sorting is needed. ``packages/standalone`` is the CLI
    install (``bin/`` layout, or the older flat one); ``app-server-daemon`` is
    the daemon-only install with no command on PATH. ``.sandbox-bin`` is a
    sandbox helper copy and is deliberately not a candidate.
    """
    configured = os.environ.get("CODEX_HOME")
    # A relative or network (UNC) CODEX_HOME falls back to the default, as
    # codex_rule_inventory refuses network paths: never launch from a share.
    usable = (configured and os.path.isabs(configured)
              and not configured.startswith(("//", "\\\\")))
    root = (Path(configured) if usable else home / ".codex") / "packages"
    standalone = [root / "standalone/current/bin" / name,
                  root / "standalone/current" / name]
    return standalone, [root / "app-server-daemon/current/bin" / name]


def codex_native_candidates() -> List[Path]:
    """Native install locations, also available to Finder launches (#277)."""
    home = Path.home()
    if sys.platform == "darwin":
        bundled = Path("Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
        standalone, daemon = _codex_package_candidates(home, "codex")
        return ([Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
                 home / ".local/bin/codex", home / ".npm-global/bin/codex"] + standalone
                + [Path("/Applications/ChatGPT.app") / bundled,
                   home / "Applications/ChatGPT.app" / bundled] + _codex_npm_candidates([
                       Path("/opt/homebrew/lib/node_modules"), Path("/usr/local/lib/node_modules"),
                       home / ".npm-global/lib/node_modules"]) + daemon)
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local")
        roaming = Path(os.environ.get("APPDATA") or home / "AppData/Roaming")
        standalone, daemon = _codex_package_candidates(home, "codex.exe")
        candidates = [home / ".local/bin/codex.exe",
                      local / "Microsoft/WinGet/Links/codex.exe",
                      local / "Programs/OpenAI/Codex/bin/codex.exe"] + standalone
        # The Store app relocates its bundled CLI outside WindowsApps. Inspect
        # only direct build folders, never recursively traverse app data.
        cache = local / "OpenAI/Codex/bin"
        try:
            builds = sorted((p for p in cache.iterdir() if p.is_dir()
                             and not p.name.startswith(".")),
                            key=lambda p: p.stat().st_mtime_ns, reverse=True)[:64]
            candidates.extend(p / "codex.exe" for p in builds)
        except OSError:
            pass
        # The app can leave an older top-level executable beside its current,
        # hash-named build. Prefer the newest managed build (#301); an old CLI
        # may no longer understand the config written by the current app.
        candidates.append(cache / "codex.exe")
        return candidates + _codex_npm_candidates([roaming / "npm/node_modules"]) + daemon
    # Linux: an app started from the desktop's menu gets the session's PATH,
    # without what the shell's profile adds (~/.local/bin, nvm's Node).
    standalone, daemon = _codex_package_candidates(home, "codex")
    return ([home / ".local/bin/codex", Path("/usr/local/bin/codex"),
             home / ".npm-global/bin/codex", Path("/home/linuxbrew/.linuxbrew/bin/codex"),
             Path("/usr/bin/codex")] + standalone
            + _codex_npm_candidates([
                Path("/usr/local/lib/node_modules"), Path("/usr/lib/node_modules"),
                home / ".npm-global/lib/node_modules",
                Path("/home/linuxbrew/.linuxbrew/lib/node_modules"), *_nvm_node_modules(home)])
            + daemon)


def _nvm_node_modules(home: Path) -> List[Path]:
    """The global node_modules of each Node that nvm installed, newest
    first: where ``npm install -g`` puts packages on most Linux setups."""
    versions = home / ".nvm/versions/node"
    try:
        folders = [p for p in versions.iterdir() if p.is_dir() and p.name.startswith("v")]
    except OSError:
        return []
    folders.sort(key=lambda p: [int(n) if n.isdigit() else 0
                                for n in p.name[1:].split(".")], reverse=True)
    return [p / "lib/node_modules" for p in folders[:16]]


def find_codex(which=None, native_candidates=None) -> Optional[str]:
    """Find a native CLI, including ChatGPT's bundle; never run shell shims."""
    import shutil

    found = (which or shutil.which)("codex")
    if native_candidates is None:
        native_candidates = codex_native_candidates()
    for candidate in ([found] if found else []) + list(native_candidates):
        if _native_codex(str(candidate)):
            return str(candidate)
    return None


def _native_codex(path: str) -> bool:
    if path.lower().endswith(_SCRIPT_SUFFIXES):
        return False
    try:
        with open(path, "rb") as stream:
            header = stream.read(4)
        native = (header.startswith(b"MZ") or header == b"\x7fELF"
                  or header in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                                b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                                b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                                b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"))
        return native and os.access(path, os.X_OK)
    except OSError:
        return False


def codex_downgrades_workspace_write() -> bool:
    """Codex answers workspace-write with read-only on Windows until its
    Windows sandbox is set up (#257); on a Mac it never does."""
    return sys.platform == "win32"


def codex_rule_inventory(layers):
    """Count user/system rule files and refuse project-supplied rules (#257).

    Codex loads direct .rules files beside active system/user/project layers.
    An allow rule can bypass both the folder sandbox and the approval UI (#257).
    This is a bounded snapshot check, not upstream per-thread rule isolation.
    """
    files, blocked = codex_saved_rule_files(layers)
    return (None, "") if files is None else (len(files), blocked)


def codex_saved_rule_files(layers):
    """The user/system rule files themselves, for Chat Permissions to list
    (#307), and any folder of project-supplied rules; (None, "") when the
    layers can't be trusted, as for the count."""
    if not isinstance(layers, list) or not 1 <= len(layers) <= 64:
        return None, ""
    no_folder = {'packagedDefaults', 'mdm', 'enterpriseManaged', 'sessionFlags',
                 'legacyManagedConfigTomlFromFile', 'legacyManagedConfigTomlFromMdm'}
    folders = {}
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get('name'), dict):
            return None, ""
        source = layer['name']
        kind = source.get('type')
        if not isinstance(kind, str) or kind not in no_folder | {'user', 'system', 'project'}:
            return None, ""
        disabled = layer.get('disabledReason')
        if disabled is not None:
            if not isinstance(disabled, str) or not disabled:
                return None, ""
            continue
        if kind in no_folder:
            continue
        if kind not in ('user', 'system', 'project'):
            return None, ""
        name = source.get('dotCodexFolder' if kind == 'project' else 'file')
        if (not isinstance(name, str) or len(name) > 32768 or '\x00' in name
                or name.startswith(('//', '\\\\'))):
            return None, ""
        path = Path(name)
        if not path.is_absolute():
            return None, ""
        folder = (path if kind == 'project' else path.parent) / 'rules'
        folders[folder] = folders.get(folder, False) or kind == 'project'
    count = 0
    saved = []
    blocked = ""
    for folder, project in folders.items():
        try:
            if folder.is_symlink():
                return None, ""
            with os.scandir(folder) as entries:
                for entry in entries:
                    count += 1
                    if count > 4096:
                        return None, ""
                    if entry.name.endswith('.rules'):
                        if project:
                            blocked = str(folder)
                        else:
                            saved.append(Path(entry.path))
        except FileNotFoundError:
            continue  # Codex also permits a genuinely absent rules directory.
        except OSError:
            return None, ""
    return saved, blocked


def codex_rule_layers_clear(layers) -> bool:
    count, blocked = codex_rule_inventory(layers)
    return count is not None and not blocked


def codex_workspace_root(cwd: str) -> str:
    """Bind write access to an existing canonical folder, never a drive root."""
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("Codex workspace-write requires a folder")
    path = Path(cwd)
    if not path.is_absolute():
        raise ValueError("Codex workspace-write requires an absolute folder")
    root = path.resolve(strict=True)
    home = Path.home().resolve()
    if (not root.is_dir() or root == Path(root.anchor) or root == home
            or root in home.parents
            or any(root.samefile(folder) for folder in (home, *home.parents))
            or os.path.ismount(root)
            or sys.platform == 'darwin' and root == Path('/Volumes')
            or sys.platform.startswith('linux') and _linux_drives_folder(root)):
        raise ValueError("Codex workspace-write requires a project folder")
    return str(root)


def _linux_drives_folder(root: Path) -> bool:
    """A folder Linux mounts drives in (``/media``, ``/run/media``, ``/mnt``,
    or a user's folder in the first two), like a Mac's ``/Volumes``: write
    access there reaches every drive plugged in."""
    parts = root.parts
    if parts[:2] == ("/", "media") or parts[:3] == ("/", "run", "media"):
        base = 3 if parts[1] == "media" else 4  # also the user's own folder
        return len(parts) <= base
    return root == Path("/mnt")


class CodexServerProcess:
    """Own the app-server process and its pipes, including forced cleanup."""
    def __init__(self, process, tree):
        import threading

        self.process, self.tree = process, tree
        self.stdin, self.stdout = process.stdin, process.stdout
        self._lock = threading.Lock()
        self._closed = False

    def close(self):
        import errno
        import subprocess

        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Kill the process group even if the parent has already exited;
            # descendants must not keep pipe readers or the app alive.
            try:
                self.tree.kill()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
            finally:
                failure = None
                try:
                    self.tree.close()
                except Exception as exc:
                    failure = exc
                for stream in (self.stdin, self.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError as exc:
                            # Killing a child during a buffered handshake write
                            # makes stdin's final flush fail. It is already
                            # closed; still close stdout instead of leaking it.
                            # Windows may report EINVAL for the same closed
                            # pipe (CPython bpo-35754). Require completed cleanup
                            # before treating either final flush as harmless.
                            expected_flush = (stream is self.stdin and stream.closed
                                              and self.process.poll() is not None
                                              and (isinstance(exc, BrokenPipeError)
                                                   or (sys.platform == "win32" and exc.errno == errno.EINVAL)))
                            if not expected_flush and failure is None:
                                failure = exc
                        except Exception as exc:
                            if failure is None:
                                failure = exc
                if failure is not None:
                    raise failure


CODEX_BILLING_VARS = frozenset({
    "CODEX_API_KEY", "CODEX_BASE_URL", "CODEX_CHATGPT_BASE_URL",
    "CODEX_AUTH_TOKEN", "CHATGPT_BASE_URL", "AZURE_API_KEY",
})


def codex_child_environment(environ=None) -> dict:
    """Keep ChatGPT sign-in while dropping inherited billing/endpoint overrides.

    Match case-insensitively because Windows environment names are insensitive.
    Preserve PATH, HOME and CODEX_HOME so the native CLI can find its login
    (#184) (#171). Configured providers still need validation before inference.
    """
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items()
            if key.upper() not in CODEX_BILLING_VARS
            and not key.upper().startswith(("OPENAI_", "AZURE_OPENAI_"))}


def start_codex_server(executable: Optional[str] = None) -> CodexServerProcess:
    """Start stdio without a shell, in the existing kill-on-close tree."""
    import subprocess

    executable = executable or find_codex()
    if not executable or not _native_codex(executable):
        raise OSError("Install the Codex app or CLI, then restart.")
    tree = ProcessTree()
    process = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
                               env=codex_child_environment(),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, **tree.popen_kwargs())
    owned = CodexServerProcess(process, tree)
    try:
        tree.attach(process)
    except Exception:
        owned.close()
        raise
    return owned


# ---------------------------------------------------------------------------
# GitHub Copilot CLI (#352)
# ---------------------------------------------------------------------------


def copilot_native_candidates() -> List[Path]:
    """Where the native Copilot CLI is installed: winget, Homebrew and
    GitHub's install script. npm's copilot.cmd / node script is never used,
    for the reason claude.cmd isn't: a command run through a shell could be
    injected from a chat's text."""
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/opt/homebrew/bin/copilot"), Path("/usr/local/bin/copilot"),
                home / ".local/bin/copilot"]
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local")
        packages = local / "Microsoft/WinGet/Packages"
        try:
            installed = sorted(p / "copilot.exe" for p in packages.iterdir()
                               if p.name.startswith("GitHub.Copilot_"))
        except OSError:
            installed = []
        return [local / "Microsoft/WinGet/Links/copilot.exe", *installed,
                home / ".local/bin/copilot.exe"]
    # Linux: GitHub's install script puts it in ~/.local/bin (or
    # /usr/local/bin as root); Homebrew on Linux in its own prefix.
    return [home / ".local/bin/copilot", Path("/usr/local/bin/copilot"),
            Path("/home/linuxbrew/.linuxbrew/bin/copilot"), Path("/usr/bin/copilot")]


def find_copilot(which=None, native_candidates=None) -> Optional[str]:
    """The native Copilot CLI, or None. The same check as Codex's: a real
    executable, never a script (#352)."""
    import shutil

    found = (which or shutil.which)("copilot")
    if native_candidates is None:
        native_candidates = copilot_native_candidates()
    for candidate in ([found] if found else []) + list(native_candidates):
        if _native_codex(str(candidate)):
            return str(candidate)
    return None


#: Environment that would change who Copilot answers as, what pays for it, or
#: whether it asks before acting (#352): ``COPILOT_ALLOW_ALL`` approves every
#: tool without asking; a token picks an account other than the one signed
#: in; ``COPILOT_OFFLINE`` and ``COPILOT_PROVIDER_*`` (bring your own key)
#: send prompts to another model service; ``COPILOT_MODEL`` and
#: ``COPILOT_AUTO_TIER`` would override the model The Chat Place shows.
COPILOT_OVERRIDE_VARS = frozenset({
    "COPILOT_ALLOW_ALL", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
    "COPILOT_OFFLINE", "COPILOT_MODEL", "COPILOT_AUTO_TIER",
})
COPILOT_OVERRIDE_PREFIXES = ("COPILOT_PROVIDER_",)


def copilot_child_environment(environ=None) -> dict:
    """The environment for a Copilot CLI The Chat Place starts: everything
    but the overrides above, matched case-insensitively for Windows. PATH,
    HOME, COPILOT_HOME and GH_HOST stay, so Copilot finds its sign-in.
    GH_HOST is kept on purpose: GitHub Enterprise Cloud with data residency
    (a company's own ghe.com host) needs it to reach that company's Copilot,
    whose answers are still GitHub Copilot's, as each turn's billing check
    confirms."""
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items()
            if key.upper() not in COPILOT_OVERRIDE_VARS
            and not key.upper().startswith(COPILOT_OVERRIDE_PREFIXES)}


#: What start_copilot_acp says when there's no Copilot CLI to start: safe
#: to show, unlike the system's own reasons.
COPILOT_MISSING = "Install GitHub Copilot CLI, then restart."


def start_copilot_acp(executable: Optional[str] = None,
                      cwd: Optional[str] = None) -> CodexServerProcess:
    """``copilot --acp`` on stdio, without a shell, in a kill-on-close tree
    (the same process wrapper as Codex's app-server)."""
    import subprocess

    executable = executable or find_copilot()
    if not executable or not _native_codex(executable):
        raise OSError(COPILOT_MISSING)
    tree = ProcessTree()
    process = subprocess.Popen([executable, "--acp", "--disable-client-telemetry"],
                               env=copilot_child_environment(), cwd=cwd or None,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, **tree.popen_kwargs())
    owned = CodexServerProcess(process, tree)
    try:
        tree.attach(process)
    except Exception:
        owned.close()
        raise
    return owned


def start_copilot_sdk(executable: Optional[str] = None) -> CodexServerProcess:
    """Start Copilot CLI's SDK server for read-only account usage queries."""
    import subprocess

    executable = executable or find_copilot()
    if not executable or not _native_codex(executable):
        raise OSError(COPILOT_MISSING)
    tree = ProcessTree()
    process = subprocess.Popen(
        [executable, "--headless", "--no-auto-update", "--stdio", "--log-level", "error",
         "--disable-client-telemetry"],
        env=copilot_child_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, **tree.popen_kwargs())
    owned = CodexServerProcess(process, tree)
    try:
        tree.attach(process)
    except Exception:
        owned.close()
        raise
    return owned


# -- Linux desktop entry -------------------------------------------------------------------
#
# An AppImage is a single file that the desktop knows nothing about. Its
# entry puts The Chat Place in the desktop's menu (found by name, like any
# other program) and makes it the handler for thechatplace:// links. Written
# only by an AppImage, pointing at that file (links.should_register).

DESKTOP_ENTRY_NAME = "thechatplace.desktop"
URL_SCHEME_MIME = "x-scheme-handler/thechatplace"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_entry_path() -> Path:
    return _data_home() / "applications" / DESKTOP_ENTRY_NAME


def _desktop_icon_path() -> Path:
    return _data_home() / "icons" / "hicolor" / "512x512" / "apps" / "thechatplace.png"


def desktop_entry(command: str) -> str:
    """The entry's text, running ``command`` (``"<AppImage>" %u``)."""
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=The Chat Place",
        "GenericName=AI Chat Sessions",
        "Comment=AI chat sessions as keyboard and screen reader friendly lists",
        f"Exec={command}",
        "Icon=thechatplace",
        "Terminal=false",
        "Categories=Utility;Development;",
        f"MimeType={URL_SCHEME_MIME};",
        "StartupWMClass=thechatplace",
        "",
    ])


def _desktop_entry_exec(path: Optional[Path] = None) -> Optional[str]:
    try:
        text = Path(path or desktop_entry_path()).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec="):]
    return None


def write_desktop_entry(command: str) -> None:
    """Write the entry and the icon, and make the entry the handler for
    thechatplace:// (through xdg-mime, which writes the user's
    mimeapps.list). Raises OSError if the entry can't be written."""
    import shutil
    import subprocess

    entry = desktop_entry_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    temporary = entry.with_suffix(".tmp")
    temporary.write_text(desktop_entry(command), encoding="utf-8")
    os.replace(temporary, entry)
    icon = _desktop_icon_path()
    try:
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(app_icon_path().with_suffix(".png"), icon)
    except OSError:
        pass  # the menu shows a stock icon; links still work
    for argv in (["update-desktop-database", str(entry.parent)],
                 ["xdg-mime", "default", DESKTOP_ENTRY_NAME, URL_SCHEME_MIME]):
        program = find_tool(argv[0])
        if program:
            try:
                subprocess.run([program, *argv[1:]], capture_output=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                pass


def codex_archive_environment(profile: Path, environ=None) -> dict:
    """No credentials, endpoint overrides or paths into a live user profile."""
    source = os.environ if environ is None else environ
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE',
               'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL'}
    environment = {key: value for key, value in source.items() if key.upper() in allowed}
    profile = Path(profile).resolve()
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME',
                'XDG_DATA_HOME', 'XDG_STATE_HOME', 'TMP', 'TEMP', 'TMPDIR'):
        folder = profile.parent / key.lower()
        folder.mkdir(parents=True, exist_ok=True)
        environment[key] = str(folder)
    environment['CODEX_HOME'] = str(profile)
    environment['CODEX_SQLITE_HOME'] = str(profile)
    return environment


def start_codex_archive_server(profile: Path, executable=None) -> CodexServerProcess:
    """History-only process rooted at a scratch profile, never the live launcher."""
    import subprocess
    executable = executable or find_codex()
    if not executable or not _native_codex(executable):
        raise OSError('Install the native Codex CLI to import Codex history.')
    tree = ProcessTree()
    try:
        process = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
                                   env=codex_archive_environment(profile), cwd=profile.parent,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, **tree.popen_kwargs())
    except BaseException:
        tree.close()
        raise
    owned = CodexServerProcess(process, tree)
    try:
        tree.attach(process)
    except BaseException:
        owned.close()
        raise
    return owned


# The scheduled-backup passphrase (#442) ---------------------------------------
#
# Kept where the system protects it, never in a file of The Chat Place's in
# plain text: Windows' DPAPI (only this Windows user can unprotect it), the
# macOS login keychain, or the Secret Service through ``secret-tool`` on
# Linux. Whoever is signed in as you can use it, which is what lets a
# schedule run with nobody there to type; a backup that leaves the computer
# stays locked without it.

BACKUP_SECRET_FILE = "backup-passphrase.dpapi"
_SECRET_SERVICE = "The Chat Place"
_SECRET_ACCOUNT = "scheduled-backup-passphrase"


def backup_secret_available() -> bool:
    """Whether this computer can keep the passphrase protected."""
    if sys.platform == "win32" or sys.platform == "darwin":
        return True
    import shutil
    return shutil.which("secret-tool") is not None


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    UI_FORBIDDEN = 0x1
    if not function(ctypes.byref(source), None, None, None, None, UI_FORBIDDEN,
                    ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "Windows couldn't protect the passphrase")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _keychain():
    import ctypes
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    return ctypes, security, core


def _keychain_find(ctypes, security):
    service, account = _SECRET_SERVICE.encode(), _SECRET_ACCOUNT.encode()
    length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account,
        ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
    return status, length, data, item


def save_backup_secret(passphrase: str) -> None:
    """Keep the passphrase protected; raises OSError if it can't be."""
    data = passphrase.encode("utf-8")
    if sys.platform == "win32":
        folder = app_data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / BACKUP_SECRET_FILE
        temporary = target.with_name(target.name + ".tmp")
        try:
            temporary.write_bytes(_dpapi(data, protect=True))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return
    if sys.platform == "darwin":
        ctypes, security, core = _keychain()
        status, _length, found, item = _keychain_find(ctypes, security)
        if status == 0:
            security.SecKeychainItemFreeContent(None, found)
            status = security.SecKeychainItemModifyAttributesAndData(item, None, len(data), data)
            core.CFRelease(item)
        else:
            service, account = _SECRET_SERVICE.encode(), _SECRET_ACCOUNT.encode()
            status = security.SecKeychainAddGenericPassword(
                None, len(service), service, len(account), account, len(data), data, None)
        if status != 0:
            raise OSError(f"The keychain didn't keep the passphrase (error {status}).")
        return
    import subprocess
    try:
        done = subprocess.run(["secret-tool", "store", "--label", "The Chat Place backup passphrase",
                               "service", _SECRET_SERVICE, "account", _SECRET_ACCOUNT],
                              input=data, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError(f"secret-tool couldn't keep the passphrase: {exc}") from None
    if done.returncode != 0:
        raise OSError("secret-tool couldn't keep the passphrase: "
                      + done.stderr.decode("utf-8", "replace").strip())


def has_backup_secret() -> bool:
    """Whether a passphrase is kept, without reading it: opening Backup
    Schedule mustn't raise a keychain prompt or wait on a keyring (#452
    review)."""
    try:
        if sys.platform == "win32":
            return (app_data_dir() / BACKUP_SECRET_FILE).is_file()
        if sys.platform == "darwin":
            ctypes, security, core = _keychain()
            service, account = _SECRET_SERVICE.encode(), _SECRET_ACCOUNT.encode()
            item = ctypes.c_void_p()
            status = security.SecKeychainFindGenericPassword(
                None, len(service), service, len(account), account, None, None,
                ctypes.byref(item))
            if status == 0 and item:
                core.CFRelease(item)
            return status == 0
        import subprocess
        done = subprocess.run(["secret-tool", "search", "service", _SECRET_SERVICE,
                               "account", _SECRET_ACCOUNT], capture_output=True, timeout=5)
        return done.returncode == 0 and bool(done.stdout.strip() or done.stderr.strip())
    except Exception:  # noqa: BLE001 - unknown reads as none
        return False


def load_backup_secret() -> Optional[str]:
    """The kept passphrase, or None if there isn't one or it can't be read."""
    try:
        if sys.platform == "win32":
            path = app_data_dir() / BACKUP_SECRET_FILE
            if not path.is_file():
                return None
            return _dpapi(path.read_bytes(), protect=False).decode("utf-8")
        if sys.platform == "darwin":
            ctypes, security, core = _keychain()
            status, length, found, item = _keychain_find(ctypes, security)
            if status != 0:
                return None
            try:
                return ctypes.string_at(found, length.value).decode("utf-8")
            finally:
                security.SecKeychainItemFreeContent(None, found)
                core.CFRelease(item)
        import subprocess
        done = subprocess.run(["secret-tool", "lookup", "service", _SECRET_SERVICE,
                               "account", _SECRET_ACCOUNT], capture_output=True, timeout=30)
        return done.stdout.decode("utf-8") if done.returncode == 0 and done.stdout else None
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        return None
    except Exception:  # noqa: BLE001 - a subprocess timeout and the like: none to give
        return None


def delete_backup_secret() -> None:
    """Forget the kept passphrase, wherever it is (nothing if there's none)."""
    try:
        if sys.platform == "win32":
            (app_data_dir() / BACKUP_SECRET_FILE).unlink(missing_ok=True)
            return
        if sys.platform == "darwin":
            ctypes, security, core = _keychain()
            status, _length, found, item = _keychain_find(ctypes, security)
            if status == 0:
                security.SecKeychainItemFreeContent(None, found)
                security.SecKeychainItemDelete(item)
                core.CFRelease(item)
            return
        import subprocess
        subprocess.run(["secret-tool", "clear", "service", _SECRET_SERVICE,
                        "account", _SECRET_ACCOUNT], capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001 - forgetting must never raise
        pass
