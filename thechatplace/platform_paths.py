"""Every path and OS call. A port changes this file and the speech routes.

The data folder is deliberately not the shipped app's: this experiment must
never touch a real install's chats or settings.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

APP_FOLDER = "TheChatPlaceExperiment"


def app_data_dir() -> Path:
    override = os.environ.get("TCP_EXPERIMENT_DATA")
    if override:
        folder = Path(override)
    elif sys.platform == "win32":
        folder = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_FOLDER
    elif sys.platform == "darwin":
        folder = Path.home() / "Library" / "Application Support" / APP_FOLDER
    else:
        folder = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


#: Run through cmd.exe, a command could be injected from a chat's title, so a
#: script launcher is never started.
_SCRIPT_SUFFIXES = (".cmd", ".bat", ".ps1")


def is_script_launcher(path: str) -> bool:
    return path.lower().endswith(_SCRIPT_SUFFIXES)


def _npm_native(launcher: str, package: str, name: str) -> Optional[str]:
    """The real program behind an npm launcher, found in the package it installed."""
    root = Path(launcher).parent / "node_modules" / Path(*package.split("/"))
    program = name + ".exe" if sys.platform == "win32" else name
    for match in sorted(glob.glob(glob.escape(str(root)) + "/**/" + program, recursive=True)):
        if os.path.isfile(match) and "vendor" in Path(match).parts:
            return match
    return None


def find_program(name: str, npm_package: str = "") -> Optional[str]:
    """A program this app may start directly, or None. Never a script launcher."""
    found = shutil.which(name)
    if not found:
        return None
    if not is_script_launcher(found):
        return found
    return _npm_native(found, npm_package, name) if npm_package else None


def program_version(program: str, args: Sequence[str] = ("--version",), env: Optional[Mapping[str, str]] = None) -> str:
    """What a program says its version is, or "" if it won't say."""
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        done = subprocess.run([program, *args], capture_output=True, text=True, timeout=20,
                              env=dict(env) if env is not None else None, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (done.stdout or "").strip().splitlines()
    return lines[0].strip()[:120] if done.returncode == 0 and lines else ""


def version_is_checked(version: str, checked: Sequence[str]) -> bool:
    """Whether a version line ("GitHub Copilot CLI 1.0.105.") names one that's been checked."""
    words = [word.rstrip(".,") for word in version.split()]
    return any(item in words for item in checked)


def clean_environment(names: Sequence[str] = (), prefixes: Sequence[str] = (),
                      environ: Optional[Mapping[str, str]] = None) -> dict:
    """The environment without these names, matched case-insensitively because
    Windows' are insensitive."""
    source = os.environ if environ is None else environ
    drop, starts = {n.upper() for n in names}, tuple(p.upper() for p in prefixes)
    return {key: value for key, value in source.items()
            if key.upper() not in drop and not (starts and key.upper().startswith(starts))}


class OwnedProcess:
    """A child with no shell and no window, whose whole tree ends with it: a
    kill-on-close job on Windows, a process group elsewhere."""

    def __init__(self, argv: Sequence[str], env: Optional[Mapping[str, str]] = None,
                 cwd: Optional[str] = None):
        if is_script_launcher(argv[0]):
            raise OSError("A script launcher can't be started safely.")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(list(argv), env=dict(env) if env is not None else None,
                                        cwd=cwd or None, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs)
        self.stdin, self.stdout = self.process.stdin, self.process.stdout
        self._job = _job_for(self.process) if sys.platform == "win32" else None

    def kill(self) -> None:
        if sys.platform == "win32":
            job, self._job = self._job, None
            if job is not None:
                _close_job(job)
            try:
                self.process.kill()
            except OSError:
                pass
        else:
            import signal
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self.stdin.close()
        except OSError:
            pass
        self.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _job_for(process):
    """Best effort: without a job the child is still killed, its children aren't."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("Basic", BasicLimits), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    KILL_ON_JOB_CLOSE, EXTENDED_LIMIT_INFORMATION = 0x2000, 9
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    limits.Basic.LimitFlags = KILL_ON_JOB_CLOSE
    if (not kernel32.SetInformationJobObject(job, EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                             ctypes.sizeof(limits))
            or not kernel32.AssignProcessToJobObject(job, int(process._handle))):
        _close_job(job)
        return None
    return job


def _close_job(job) -> None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle(job)


# -- what speech needs, as the shipped app has them ------------------------------

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
