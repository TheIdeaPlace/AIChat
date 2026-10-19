"""The visual probe (#155): open each of The Chat Place's screens on made-up
data, and save a picture of it and a JSON description of its controls.

    python tools/ui_probe.py --out probe-run [--tag light-100] [--surface main-own ...]
    python tools/ui_probe.py --list

It shows real windows, so on Windows run it in the test VM (``tools/ui_probe_vm.ps1``
does that, for every variant in ``tools/ui_probe_plan.json``), never on a PC
someone is using with a screen reader. The data is the hidden-window tests'
(``tests/fake_env.py``): no real sessions, %APPDATA%, claude, speech or web
profile.

Each surface gets a new window on fresh data, so no surface's state reaches
the next, and one surface photographed alone looks as it does in a full run.
It's opened the way a person opens it, through the frame's own menu handler,
wherever made-up data can get it there. A modal dialog is caught by a timer
while it is open, photographed, then cancelled.

The JSON beside each picture lists every control with its class, label,
rectangle and best size, and flags text that doesn't fit, controls on top of
each other and controls outside the window. ``tests/test_ui_probe.py`` opens
every surface on a hidden window to check each still shows its dialog, and
that every dialog has a surface.

Ages ("active 1 hour ago") are relative to when the probe runs, so they read
the same in every run; the clock itself isn't fixed.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import wx  # noqa: E402

from thechatplace import platform_paths  # noqa: E402

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

#: How long a window must stay open before it's photographed, so its first
#: paint (and a list's first selection) has happened.
SETTLE_SECONDS = 0.8
#: The formatted view draws in another process; it gets longer.
WEBVIEW_SETTLE_SECONDS = 2.5
DIALOG_TIMEOUT_SECONDS = 20.0
DEFAULT_SIZE = (1000, 720)
NEVER_DREW = "the web page never drew"


# -- capture ------------------------------------------------------------------------------

@contextlib.contextmanager
def _physical_pixels():
    """Measure and copy in the screen's real pixels. The app and the probe
    are DPI aware now (#185), so this changes nothing for them; it's kept for
    a probe run of an older, unaware build, which Windows stretches."""
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
    previous = user32.SetThreadDpiAwarenessContext(PER_MONITOR_AWARE_V2)
    try:
        yield
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def _window_rect(hwnd):
    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    return tuple(rect)


def _frame_bounds(hwnd):
    """The window's visible rectangle on screen: GetWindowRect includes
    Windows 10/11's invisible resize border, which would show as a margin."""
    rect = (ctypes.c_long * 4)()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
            ctypes.sizeof(rect)) != 0:
        return None
    return tuple(rect)


def _one_colour(bitmap) -> bool:
    """True if a grid of samples across the picture are all the same colour:
    what a capture that couldn't draw the window looks like."""
    image = bitmap if isinstance(bitmap, wx.Image) else bitmap.ConvertToImage()
    w, h = image.GetWidth(), image.GetHeight()
    if w < 2 or h < 2:
        return True
    seen = set()
    for y in range(1, h, max(1, h // 12)):
        for x in range(1, w, max(1, w // 12)):
            seen.add((image.GetRed(x, y), image.GetGreen(x, y), image.GetBlue(x, y)))
            if len(seen) > 1:
                return False
    return True


def _screen_copy(left, top, width, height) -> wx.Bitmap:
    bitmap = wx.Bitmap(width, height)
    dc = wx.MemoryDC(bitmap)
    dc.Blit(0, 0, width, height, wx.ScreenDC(), left, top)
    dc.SelectObject(wx.NullBitmap)
    return bitmap


@contextlib.contextmanager
def _on_top(window):
    """The window above every other while the screen is copied, then back.
    Raise() isn't enough: Windows won't let a background process come in
    front of the window that has the foreground, and on a fresh VM that can
    be its own Get Started or OneDrive window, which then came out in the
    picture (#155)."""
    user32 = ctypes.windll.user32
    hwnd = ctypes.c_void_p(window.GetHandle())
    GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x8
    already = bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)
    flags = 0x1 | 0x2 | 0x10  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    try:
        window.Raise()
        if not already and not _set_window_pos(hwnd, -1, flags):  # HWND_TOPMOST
            print(f"  (couldn't put the window on top: error {ctypes.GetLastError()})")
        _pump(0.4)
        yield
    finally:
        if not already:
            _set_window_pos(hwnd, -2, flags)  # HWND_NOTOPMOST


_SetWindowPos = None


def _set_window_pos(hwnd, after: int, flags: int) -> bool:
    global _SetWindowPos
    if _SetWindowPos is None:
        _SetWindowPos = ctypes.WinDLL("user32", use_last_error=False).SetWindowPos
        _SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        _SetWindowPos.restype = ctypes.c_bool
    return _SetWindowPos(hwnd, ctypes.c_void_p(after), 0, 0, 0, 0, flags)


def _work_area():
    """The primary screen less the taskbar, in the calling thread's pixels."""
    rect = (ctypes.c_long * 4)()
    SPI_GETWORKAREA = 0x0030
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return tuple(rect)


def _print_window(window):
    """The window as it draws itself, at its own (unscaled) size, whatever
    covers it on screen: PrintWindow with PW_RENDERFULLCONTENT, which
    includes WebView2's page. None if Windows couldn't."""
    hwnd = window.GetHandle()
    left, top, right, bottom = _window_rect(hwnd)
    bitmap = wx.Bitmap(right - left, bottom - top)
    dc = wx.MemoryDC(bitmap)
    PW_RENDERFULLCONTENT = 2
    ok = ctypes.windll.user32.PrintWindow(ctypes.c_void_p(hwnd), ctypes.c_void_p(dc.GetHandle()),
                                          PW_RENDERFULLCONTENT)
    dc.SelectObject(wx.NullBitmap)
    return bitmap if ok else None


def _capture_windows(window: wx.TopLevelWindow) -> tuple[wx.Bitmap, str]:
    """At 100%: PrintWindow with PW_RENDERFULLCONTENT, which includes the
    formatted view's WebView2 (drawn by another process), or a screen copy
    if that came back blank. When Windows is stretching the window (scaling
    above 100%), a screen copy in real pixels, which is what a person sees.
    Cropped to the visible frame either way."""
    hwnd = window.GetHandle()
    logical = _window_rect(hwnd)
    with _physical_pixels():
        physical = _window_rect(hwnd)
        bounds = _frame_bounds(hwnd) or physical
        stretched = (physical[2] - physical[0]) != (logical[2] - logical[0])
        if stretched:
            # Only what's on screen above the taskbar: a dialog taller than
            # the screen runs under it (#186), and the taskbar's own icons,
            # which differ run to run, would show as changes.
            wl, wt, wr, wb = _work_area()
            bl, bt = max(bounds[0], wl), max(bounds[1], wt)
            br, bb = min(bounds[2], wr), min(bounds[3], wb)
            if br <= bl or bb <= bt:
                raise RuntimeError("the window is outside the screen's work area")
            with _on_top(window):
                return _screen_copy(bl, bt, br - bl, bb - bt), "screen copy (stretched by Windows)"
    left, top, right, bottom = physical
    width, height = right - left, bottom - top
    bitmap = _print_window(window)
    method = "PrintWindow"
    if bitmap is None or _one_colour(bitmap):
        with _on_top(window):
            bitmap = _screen_copy(left, top, width, height)
        method = "screen copy"
    bl, bt, br, bb = bounds
    crop = wx.Rect(bl - left, bt - top, br - bl, bb - bt)
    if crop.width > 0 and crop.height > 0 and wx.Rect(0, 0, width, height).Contains(crop):
        bitmap = bitmap.GetSubBitmap(crop)
    return bitmap, method


def _capture_mac(window, path: Path) -> str:
    """``screencapture -l``: the window alone, without its shadow. Needs
    Screen Recording permission for the terminal running the probe; without
    it the picture is an empty frame, which the blank check catches."""
    from thechatplace.ui import mac_a11y
    view = ctypes.c_void_p(window.GetHandle())
    nswindow = mac_a11y._send(view, "window")
    number = mac_a11y._send(ctypes.c_void_p(nswindow), "windowNumber", restype=ctypes.c_long)
    subprocess.run(["screencapture", "-x", "-o", "-l", str(number), str(path)], check=True)
    return "screencapture"


def capture(window: wx.TopLevelWindow, path: Path) -> str:
    """Save a PNG of ``window`` to ``path``; returns how it was taken."""
    window.Refresh()
    window.Update()
    _pump(0.1)
    if IS_MAC:
        method = _capture_mac(window, path)
    else:
        if IS_WINDOWS:
            bitmap, method = _capture_windows(window)
        else:
            rect = window.GetScreenRect()
            bitmap, method = _screen_copy(rect.x, rect.y, rect.width, rect.height), "screen copy"
        if not bitmap.SaveFile(str(path), wx.BITMAP_TYPE_PNG):
            raise RuntimeError(f"couldn't save {path}")
    if _one_colour(wx.Image(str(path))):
        raise RuntimeError(f"the picture is blank ({method})")
    return method


# -- describing the controls -------------------------------------------------------------

#: Controls whose text is drawn at its natural size: one smaller than its
#: best size is cutting text off.
_TEXT_CONTROLS = (wx.StaticText, wx.Button, wx.CheckBox, wx.RadioButton, wx.StaticBox)


def _class_name(control) -> str:
    """wx's own name for the control's class: wxPython can hand back the wrong
    Python class for a control it didn't create itself (a StaticBoxSizer's
    group box comes back as a StatusBar)."""
    name = control.GetClassName()
    return name[2:] if name.startswith("wx") else name or type(control).__name__


def _label(control) -> str:
    try:
        return control.GetLabel()
    except Exception:  # noqa: BLE001
        return ""


def describe(window: wx.TopLevelWindow) -> dict:
    """Every shown control under ``window``: class, name, label, rectangle
    (in the window's client coordinates), best size, and what looks wrong.
    Plain data, for the review and for comparing two runs."""
    origin = window.ClientToScreen(wx.Point(0, 0))
    client = window.GetClientSize()
    controls = []

    def walk(parent, depth, parent_index):
        for child in parent.GetChildren():
            if isinstance(child, wx.TopLevelWindow) or not child.IsShown():
                continue
            screen = child.GetScreenRect()
            rect = [screen.x - origin.x, screen.y - origin.y, screen.width, screen.height]
            best = child.GetBestSize()
            entry = {"class": _class_name(child), "name": child.GetName(),
                     "label": _label(child), "rect": rect, "best": [best.width, best.height],
                     "depth": depth, "parent": parent_index, "enabled": child.IsEnabled(),
                     "problems": []}
            # A label that ends in "..." on purpose (ST_ELLIPSIZE_*) shows
            # that it's shortened, and screen readers get it whole.
            ellipsized = isinstance(child, wx.StaticText) and child.HasFlag(
                wx.ST_ELLIPSIZE_START | wx.ST_ELLIPSIZE_MIDDLE | wx.ST_ELLIPSIZE_END)
            if ellipsized and entry["label"]:
                # Shortened is fine; too narrow to show even a little of it
                # isn't: less than a third of its text, or of "W...".
                needs = child.GetTextExtent(wx.Control.RemoveMnemonics(entry["label"])).width
                if screen.width + 1 < min(needs, max(needs // 3, child.GetTextExtent("W...").width)):
                    entry["problems"].append("text hidden: too narrow to show more than a sliver of it")
            elif isinstance(child, _TEXT_CONTROLS) and entry["label"]:
                if best.width > screen.width + 1 or best.height > screen.height + 1:
                    entry["problems"].append("text cut off: smaller than its best size")
            if isinstance(child, wx.ListBox):
                entry["items"] = [child.GetString(i) for i in range(min(child.GetCount(), 12))]
                entry["selection"] = child.GetSelection()
            elif isinstance(child, wx.TextCtrl):
                entry["value"] = child.GetValue()[:400]
            elif isinstance(child, wx.Choice):
                entry["value"] = child.GetStringSelection()
            # The status bar sits below the client area by design.
            if depth == 0 and not isinstance(child, wx.StatusBar):
                right, bottom = rect[0] + rect[2], rect[1] + rect[3]
                if (rect[0] < 0 or rect[1] < 0
                        or right > client.width + 1 or bottom > client.height + 1):
                    entry["problems"].append("outside the window")
            controls.append(entry)
            walk(child, depth + 1, len(controls) - 1)
    walk(window, 0, -1)
    _flag_overlaps(controls)
    return {"title": window.GetTitle(), "class": type(window).__name__,
            "size": list(window.GetSize()), "client": [client.width, client.height],
            "dpi": list(window.GetDPI()), "content_scale": window.GetContentScaleFactor(),
            "controls": controls}


def _flag_overlaps(controls):
    """Siblings (children of the same parent) whose rectangles cross. A group
    box's rectangle holds its options on purpose (#121), so a StaticBox is
    never counted, nor the panels that only hold other controls."""
    siblings = {}
    for entry in controls:
        if entry["class"] in ("StaticBox", "Panel", "ScrolledWindow"):
            continue
        siblings.setdefault(entry["parent"], []).append(entry)
    for group in siblings.values():
        for i, a in enumerate(group):
            ra = wx.Rect(*a["rect"])
            for b in group[i + 1:]:
                rb = wx.Rect(*b["rect"])
                if ra.width and ra.height and rb.width and rb.height and ra.Intersects(rb):
                    overlap = ra.Intersect(rb)
                    if overlap.width > 2 and overlap.height > 2:
                        a["problems"].append(f"overlaps {b['class']} {b['label'] or b['name']!r}")


# -- the made-up world ---------------------------------------------------------------------

FENCE = "`" * 3
LONG_MESSAGE = f"""## What changed

The upload test failed one run in ten because it read the progress file before the
writer had flushed it. It now waits for the writer to say it's done.

- `uploader.py`: flush and close before signalling
- `test_upload.py`: wait on the signal, not on a sleep

{FENCE}python
def finish(self):
    self._file.flush()
    self._file.close()
    self.done.set()
{FENCE}

| File | Lines added | Lines removed |
|---|---|---|
| uploader.py | 4 | 1 |
| test_upload.py | 6 | 3 |

See [the issue](https://github.com/example/repo/issues/42) for the history. A long line
follows so wrapping shows: {"wrapping " * 30}
"""


def build_world(root: Path, patch=setattr, empty: bool = False, formatted_view: bool = True) -> dict:
    """Install the fakes under ``root`` and write the probe's sessions. The
    session folders are real (Continue Here checks the folder is there)."""
    import fake_env
    from records import assistant_block, text_block, tool_result, tool_use_block, user_text
    from thechatplace import platform_paths
    from thechatplace.ui import main_frame

    env = fake_env.install(root, patch, formatted_view=formatted_view)
    patch(main_frame, "TurnRunner", fake_env.FakeRunner)
    patch(platform_paths, "find_claude", lambda: platform_paths.ClaudeLookup("claude.exe"))
    # Nor the real gh, for New Session's From GitHub (#154).
    # A date and time read from the clock would change every picture of
    # Properties (#214); the "(just now)" after it still comes from the data.
    from thechatplace import properties
    patch(properties, "_date", lambda moment: "11 October 2026 at 14:05")
    from thechatplace import workplaces
    patch(workplaces, "find_gh", lambda: "gh.exe")
    patch(workplaces, "list_github_repos", lambda gh, limit=0: [
        workplaces.GitHubRepo("probe/AIChat", "The Chat Place: a reader for Claude Code sessions"),
        workplaces.GitHubRepo("probe/website", "The Idea Place website"),
        workplaces.GitHubRepo("probe/notes", "Private notes", private=True)])
    projects = root / "Projects"
    env["folders"] = {}
    for name in ("AIChat", "Website", "Scratch"):
        (projects / name).mkdir(parents=True, exist_ok=True)
        env["folders"][name] = str(projects / name)
    if empty:
        return env

    repo = env["folders"]["AIChat"]
    fake_env.add_desktop(env, "local_a", "cli-a", "Fix the flaky upload test", cwd=repo,
                         postTurnSummary={"status_category": "blocked",
                                          "needs_action": "Pick a name for the release branch"})
    fake_env.add_desktop(env, "local_b", "cli-b", "Write the release notes for 0.2", cwd=repo,
                         ago=3_600_000)
    fake_env.add_desktop(env, "local_c", "cli-c",
                         "A session whose title goes on and on to show what a very long title "
                         "does to the session list and the heading above the messages",
                         cwd=env["folders"]["Website"], ago=86_400_000)
    transcript = fake_env.add_transcript(env, repo, "cli-a", [
        user_text("The upload test fails now and then. Can you find out why?"),
        assistant_block(text_block("I'll look at the test and the uploader."), "m1"),
        assistant_block(tool_use_block("Bash", {"command": "pytest tests/test_upload.py -q"},
                                       "t1"), "m2"),
        tool_result("t1", "1 failed, 11 passed"),
        assistant_block(text_block(LONG_MESSAGE), "m3"),
        user_text("Thanks. Which branch name should the release use?")])
    # Written a minute ago, as its session last worked. Written just now, it
    # tied with the probe's own session for the most recent, and New
    # Session's Folder box showed AIChat in one run and Scratch in the next
    # (#155).
    written = time.time() - 60
    os.utime(transcript, (written, written))
    own_cwd = env["folders"]["Scratch"]
    edit_path = os.path.join(own_cwd, "uploader.py")
    fake_env.add_transcript(env, own_cwd, "own-1", [
        user_text("Make the uploader flush before it signals."),
        assistant_block(tool_use_block("Edit", {"file_path": edit_path,
                                                "old_string": "self.done.set()",
                                                "new_string": "self._file.flush()\n"
                                                              "self._file.close()\n"
                                                              "self.done.set()"}, "e1"), "m1"),
        tool_result("e1", "The file has been updated.", toolUseResult={"filePath": edit_path}),
        assistant_block(text_block(LONG_MESSAGE), "m2")])
    return env


def build_frame(env, empty: bool = False):
    import fake_env
    from thechatplace.own_store import OwnSession, OwnSessionStore
    from thechatplace.ui.main_frame import MainFrame
    store = OwnSessionStore(env["tmp"] / "own.json")
    if not empty:
        store.add(OwnSession("own-1", "Visual probe", env["folders"]["Scratch"],
                             last_activity_ms=fake_env.now_ms()))
    frame = MainFrame(store=store, check_updates_at_start=False)
    if not empty and not fake_env.pump(lambda: frame.session_list.GetCount() == 4, timeout=15):
        raise RuntimeError("the made-up sessions didn't load")
    return frame


def close_frame(frame):
    frame.stop_timers()
    frame._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    frame.Destroy()
    wx.GetApp().ProcessPendingEvents()


# -- surfaces -------------------------------------------------------------------------------

def _pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        wx.GetApp().ProcessPendingEvents()
        wx.YieldIfNeeded()
        time.sleep(0.02)


def _select(frame, title):
    for i in range(frame.session_list.GetCount()):
        if title in frame.session_list.GetString(i):
            frame.session_list.SetSelection(i)
            return
    raise RuntimeError(f"{title} isn't in the session list")


def _open(frame, title):
    import fake_env
    _select(frame, title)
    frame.on_open_session()
    if not fake_env.pump(lambda: frame._chat_loaded and frame.chat_list.GetCount() > 0,
                         timeout=10):
        raise RuntimeError(f"{title} didn't load")


def _select_message(frame, needle):
    for i in range(frame.chat_list.GetCount()):
        if needle in frame.chat_list.GetString(i):
            frame.chat_list.SetSelection(i)
            return
    raise RuntimeError(f"no message containing {needle!r}")


def _waiting(frame, request):
    """Claude waiting on ``request`` in the own session, as a turn reports it."""
    import fake_env
    from thechatplace.claude_cli import TurnEvent
    _open(frame, "Visual probe")
    frame._pending.clear()
    frame._runners["own-1"] = fake_env.FakeRunner([], "", "", None)
    frame._on_turn_event({"id": "own-1"}, "Visual probe",
                         TurnEvent("permission", text=request.summary(), request=request))


def _request(tool, tool_input, request_id="r1"):
    from thechatplace.claude_cli import PermissionRequest
    return PermissionRequest(request_id, tool, tool_input, suggestions=[])


# Each surface: (kind, function, what it shows). A main-window surface's
# function sets the window up; a dialog surface's returns the call that
# opens it (a menu handler, usually), run while the probe waits to catch it.

def s_main_start(frame, env):
    pass


def s_main_own(frame, env):
    _open(frame, "Visual probe")


def s_main_compacted(frame, env):
    from thechatplace.transcript import COMPACTION, COMMAND_OUTPUT, ChatMessage
    s_main_own(frame, env)
    frame._show_activity = False
    frame._chat_messages += [
        ChatMessage(COMPACTION, 'Conversation compacted (you asked), from 968,000 to 9,000 tokens.', key='probe-compaction'),
        ChatMessage(COMMAND_OUTPUT, 'Compacted', key='probe-command-output')]
    frame._rebuild_chat_list(focus_newest=True)


def s_main_own_working(frame, env):
    _open(frame, "Visual probe")
    for text in ("Now run the whole suite.", "And then open a PR."):
        frame.reply_text.SetValue(text)
        frame.on_send()
    frame.reply_text.SetValue("Draft of a message still being typed")
    # The list and heading say "working" from the next refresh, as the
    # app's own timer would bring in a moment.
    import fake_env
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None, timeout=10)


def s_main_attachments(frame, env):
    _open(frame, "Visual probe")
    folder = Path(env["folders"]["Scratch"])
    paths = []
    for name in ("screenshot of the error.png", "upload.log"):
        (folder / name).write_bytes(b"made up")
        paths.append(str(folder / name))
    frame._attachments["own-1"] = paths
    frame._show_attachments()


def s_main_desktop(frame, env):
    _open(frame, "Fix the flaky upload test")


def s_main_codex(frame, env):
    """Synthetic prototype only; no installed CLI/account or UI opt-in."""
    import dataclasses
    from thechatplace.providers.claude import ClaudeConversations
    from thechatplace.providers.codex import CodexProvider, session_from_thread
    from thechatplace.session_controller import SessionController

    # Seconds, as Codex sends them. Two hours ago rather than a fixed date, so
    # the row says "active 2 hours ago" in every run, not "691 months ago".
    updated = int(time.time()) - 2 * 3600
    items = [{"id": "user", "type": "userMessage", "content": [
                 {"type": "text", "text": "Explain the upload test."}]},
             {"id": "answer", "type": "agentMessage", "text": LONG_MESSAGE}]
    thread = {"id": "synthetic-codex", "name": "Codex history preview",
              "cwd": env["folders"]["Scratch"], "createdAt": updated - 600, "updatedAt": updated,
              "status": {"type": "notLoaded"}}
    # No inline turns: the reader asks without them and pages instead, so a
    # reader that went back to inline turns would show "Couldn't load" here.

    class Connection:
        # The reader asks for metadata, then pages of turns and items (#285
        # and earlier): answer each the way app-server does, in one page.
        def request(self, method, params):
            if method == "thread/read":
                return {"thread": thread}
            if method == "thread/list":
                # A refresh during the probe lists this one chat, so the row
                # takes its state from the runtime as the app's would, rather
                # than reporting discovery as unavailable.
                return {"data": [] if params.get("archived") else [thread], "nextCursor": None}
            if method == "thread/turns/list":
                return {"data": [{"id": "turn", "startedAt": updated - 600}], "nextCursor": None}
            if method == "thread/items/list":
                return {"data": [{"turnId": "turn", "item": item} for item in items],
                        "nextCursor": None}
            raise RuntimeError("Unexpected prototype request")

    frame._list_timer.Stop()  # keep the injected fixture, never enable live discovery
    frame.sessions = SessionController([ClaudeConversations(), CodexProvider(Connection())])
    snapshot = dataclasses.replace(frame._snapshot,
                                   sessions=[*frame._snapshot.sessions, session_from_thread(thread, False)])
    frame._apply_snapshot(snapshot, [], {}, False)
    _open(frame, "Codex history preview")


def s_main_codex_filter(frame, env):
    from thechatplace.sessions import FIELD_PROVIDER, VIEW_CODEX

    s_main_codex(frame, env)
    frame.speech.session_fields = ["title", FIELD_PROVIDER, "folder", "status"]
    frame._apply_view(VIEW_CODEX)


def s_main_codex_owned(frame, env):
    import dataclasses
    from types import SimpleNamespace
    from thechatplace.own_store import OwnSession
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.sessions import WORKING
    import fake_env

    s_main_codex(frame, env)
    info = frame._open
    record = OwnSession(info.cli_session_id, info.title, info.cwd, provider="codex")
    frame.store.add(record)
    state = CodexChatState(info.cli_session_id, WORKING, "turn", "inProgress",
                           queued=("Run the unit tests next.",))
    frame.sessions._providers["codex"].runtime = SimpleNamespace(
        known=lambda native: True, snapshot=lambda native: state,
        ready=lambda record: True, close=lambda: None,
        # A chat past its first turn reads through discovery (#285).
        history_route=lambda native: None)
    frame._codex_states[info.key] = state
    frame.open_session(dataclasses.replace(record.to_info(), state=WORKING))
    fake_env.pump(lambda: frame._chat_loaded and not frame._reader_busy
                  and not frame._codex_preparing, timeout=10)
    frame.reply_text.SetValue("Draft of a message still being typed")
    frame._update_send_state()
    # The row says "working" and "Chat Place session" from the next refresh,
    # as the app's own timer would bring in a moment.
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None, timeout=10)


def s_main_codex_last_message(frame, env):
    from thechatplace.sessions import FIELD_LAST_MESSAGE, FIELD_PROVIDER, VIEW_CODEX

    s_main_codex(frame, env)
    frame.speech.session_fields = ["title", FIELD_PROVIDER, FIELD_LAST_MESSAGE]
    frame.sessions._providers["codex"].fill_last_messages(
        s for s in frame._snapshot.sessions if s.provider == "codex")
    frame._apply_view(VIEW_CODEX)


def s_main_codex_interrupted(frame, env):
    from thechatplace.providers.codex_runtime import CodexChatState
    from thechatplace.providers.codex_rpc import CodexConnectionFailure
    from thechatplace.sessions import STATUS_UNAVAILABLE
    s_main_codex_owned(frame, env)
    reason = CodexConnectionFailure.REASONS['unsupported_request']
    state = CodexChatState(frame._open.cli_session_id, STATUS_UNAVAILABLE, 'turn', 'unknown', problem=reason)
    frame.sessions._providers['codex'].runtime.snapshot = lambda native: state
    frame.sessions._providers['codex'].runtime.ready = lambda record: False
    frame._on_codex_update(state)
    info = next(info for info in frame._snapshot.sessions if info.key == frame._open.key)
    info.state, info.detail = STATUS_UNAVAILABLE, reason
    frame._refresh_list_in_place(rewrite=True)


def s_main_codex_request_declined(frame, env):
    import dataclasses
    from thechatplace.providers.codex_requests import CodexRequestDeclined
    s_main_codex_owned(frame, env)
    state = dataclasses.replace(frame._codex_states[frame._open.key], notice=CodexRequestDeclined().text)
    frame._on_codex_update(state)


def s_main_codex_context(frame, env):
    import dataclasses
    from thechatplace.providers.codex_tokens import CodexTokenUsage
    s_main_codex_owned(frame, env)
    state = dataclasses.replace(frame._codex_states[frame._open.key],
                                token_usage=CodexTokenUsage(80000, 5000, 85000, 250000, 100000))
    frame._on_codex_update(state)


def s_main_codex_unavailable(frame, env):
    import dataclasses

    s_main_codex(frame, env)
    snapshot = dataclasses.replace(frame._snapshot, stale_providers={"codex"},
                                  provider_errors={"codex": "Session discovery unavailable"},
                                  newly_failed_providers={"codex"})
    frame._apply_snapshot(snapshot, [], {}, False)


def s_main_activity(frame, env):
    _open(frame, "Fix the flaky upload test")
    frame.activity_check.SetValue(True)
    frame.on_toggle_activity_check(None)


def s_main_last_message(frame, env):
    import fake_env
    from thechatplace.sessions import FIELD_LAST_MESSAGE
    frame.speech.session_fields = list(frame.speech.session_fields) + [FIELD_LAST_MESSAGE]
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None, timeout=10)
    _pump(0.5)


def s_main_status_bar(frame, env):
    _open(frame, "Visual probe")
    frame.status_parts.set("context", "Context 42% full")
    frame.status_parts.set("update", "Update 0.2.0 ready")
    frame.status_parts.layout()


def s_main_narrow(frame, env):
    _open(frame, "Visual probe")
    from thechatplace.ui.sizes import dip
    frame.SetSize(dip(frame, 640, 480))  # design pixels, as the app's own sizes are


def d_shortcuts_page(frame, env):
    return frame.on_shortcuts


def d_shortcuts_plain(frame, env):
    from thechatplace.ui.dialogs import ShortcutsDialog
    return lambda: frame._modal(ShortcutsDialog(frame))


def d_user_guide(frame, env):
    return frame.on_user_guide


def d_message_formatted(frame, env):
    _open(frame, "Fix the flaky upload test")
    _select_message(frame, "What changed")
    frame.speech.formatted_messages = True
    return frame.on_read_message


def d_message_plain(frame, env):
    _open(frame, "Fix the flaky upload test")
    _select_message(frame, "What changed")
    frame.speech.formatted_messages = False
    return frame.on_read_message


def d_settings(frame, env):
    return frame.on_settings


def d_settings_codex(frame, env):
    from thechatplace import platform_paths
    platform_paths.find_codex = lambda: "synthetic-native-codex"
    return frame.on_settings


def d_new_session(frame, env):
    return frame.on_new_session


def d_codex_new(frame, env):
    from thechatplace.ui.dialogs import NewSessionDialog
    return lambda: frame._modal(NewSessionDialog(frame, env["folders"]["Scratch"],
                                                providers=("claude", "codex"), provider="codex",
                                                load_rules=lambda folder, done: done(2, False)))


def d_codex_mode(frame, env):
    from thechatplace.ui.dialogs import codex_permission_prompt
    title = 'Review saved command rules for a deliberately long synthetic chat title'
    folder = str(Path(env['folders']['Scratch']) / 'long-synthetic-project-folder' / 'nested-folder-for-permission-review')
    dialog = wx.SingleChoiceDialog(frame, codex_permission_prompt(frame, title, folder),
        'Change Permission Mode', ['Read-only; asks before allowing changes',
        'Can edit files in this folder; asks before anything else'])
    dialog.SetSelection(1)
    return lambda: frame._modal(dialog)


def d_codex_owned_properties(frame, env):
    from thechatplace.own_store import OwnSession
    from thechatplace.properties import session_properties
    record = OwnSession('synthetic-policy', 'Codex policy preview', str(env['folders']['Scratch']), provider='codex',
        provider_options={'model': 'Synthetic model', 'effort': 'high', 'sandbox': 'workspace-write',
            'approval_policy': 'on-request', 'workspace_root': str(env['folders']['Scratch'])})
    rows = session_properties(record.to_info(), own=record, saved_rule_count=2)
    # The title the app gives it (main_frame.on_properties), not the bare session name.
    return lambda: frame._show_properties('Properties of Codex policy preview', rows, None)


def d_codex_continue(frame, env):
    from thechatplace.ui.dialogs import NewSessionDialog
    from thechatplace.sessions import WORKING
    return lambda: frame._modal(NewSessionDialog(frame, env["folders"]["Scratch"],
        continue_from="Codex still working", providers=("codex",), provider="codex", source_state=WORKING))


def d_codex_models(frame, env):
    from thechatplace.ui.dialogs import NewSessionDialog
    from thechatplace.providers.codex_models import CodexModel
    def show():
        dialog = NewSessionDialog(frame, env["folders"]["Scratch"],
                                  providers=("claude", "codex"), provider="codex")
        dialog._set_codex_models((CodexModel('synthetic', 'Synthetic Codex model',
            ('low', 'medium', 'high', 'xhigh'), 'medium', True),), False)
        dialog.model.SetSelection(1)
        dialog._on_codex_model()
        dialog.effort_choice.SetSelection(4)
        frame._modal(dialog)
    return show


def d_github_repos(frame, env):
    """New Session, From GitHub: opened from inside New Session, so built
    here directly, as New Session builds it."""
    from thechatplace.ui.dialogs import GitHubRepoDialog
    # A GitHub folder like a real one: the temporary folder's long path would
    # make "Cloned into ..." fit or not depending on the machine.
    github = r"C:\Users\probe\GitHub" if IS_WINDOWS else "/Users/probe/GitHub"
    return lambda: frame._modal(GitHubRepoDialog(frame, "gh.exe", github))


def d_continue_here(frame, env):
    _open(frame, "Fix the flaky upload test")
    return frame.on_continue_here


def d_update_installed(frame, env):
    from thechatplace.ui.dialogs import UpdateInstalledDialog
    return lambda: frame._modal(UpdateInstalledDialog(frame, "0.2.0", lambda: None))


def d_permission(frame, env):
    _waiting(frame, _request("Bash", {"command": "git push origin release/0.2",
                                      "description": "Push the release branch"}))
    return frame.on_answer


def d_question(frame, env):
    _waiting(frame, _request("AskUserQuestion", {"questions": [
        {"header": "Branch", "question": "Which name should the release branch use?",
         "options": [{"label": "release/0.2", "description": "Matches the last release"},
                     {"label": "v0.2-prep", "description": "Shorter"}]},
        {"header": "Checks", "question": "Which checks should run first?", "multiSelect": True,
         "options": [{"label": "Unit tests"}, {"label": "Smoke test"}, {"label": "Lint"}]}]},
        "q1"))
    return frame.on_answer


def d_codex_permission(frame, env):
    from thechatplace.providers.codex_requests import CodexRequest, COMMAND
    from thechatplace.ui.dialogs import CodexPermissionDialog
    # The widest current shape (#284): extra access, a lasting offer left
    # out, and every refusal, so the button row's fit is measured.
    folder = env["tmp"].as_posix()
    request = CodexRequest(1, COMMAND, "probe", "turn", "item",
                           command="git checkout -b fix", cwd=folder,
                           reason="The sandbox blocked writing Git's HEAD.lock.",
                           decisions=("accept", "decline", "cancel"),
                           extra_access=(f"Write access to {folder}/.git", "Network access to any site"),
                           saved_rule_offered=True)
    return lambda: frame._modal(CodexPermissionDialog(frame, "Codex preview", request))


def d_chat_permissions(frame, env):
    """File, Chat Permissions on a Claude chat with two kept rules: one from
    before times were kept, one with its time (#329)."""
    _open(frame, "Visual probe")
    frame.store.update("own-1", allowed_tools=["Bash(git push:*)", "WebFetch(domain:docs.python.org)"],
                       allowed_tools_at={"WebFetch(domain:docs.python.org)": 1_792_400_000_000})
    return frame.on_chat_permissions


def d_chat_permissions_empty(frame, env):
    _open(frame, "Visual probe")
    frame.store.update("own-1", allowed_tools=[], allowed_tools_at={})
    return frame.on_chat_permissions


def d_chat_permissions_codex(frame, env):
    """A Codex chat: its mode and Codex's own rule-file count, and nothing
    kept (#307), through the window's own Codex host."""
    from thechatplace.own_store import OwnSession
    from thechatplace.ui.dialogs import ChatPermissionsDialog
    from thechatplace.ui.permission_hosts import CodexPermissions
    record = OwnSession("synthetic-codex", "Codex preview", str(env["folders"]["Scratch"]),
                        provider="codex", provider_options={"sandbox": "workspace-write",
                                                            "approval_policy": "on-request"})
    frame._load_codex_rules = lambda folder, done: done(2, False)
    host = CodexPermissions(frame, record.to_info(), record)
    return lambda: frame._modal(ChatPermissionsDialog(frame, "Codex preview", host, "codex"))


def d_codex_files(frame, env):
    from thechatplace.providers.codex_requests import CodexRequest, FILES
    from thechatplace.ui.dialogs import CodexPermissionDialog
    request = CodexRequest(2, FILES, "probe", "turn", "files")
    detail = "Update: app.py\n@@ -1 +1 @@\n-print('old')\n+print('new')"
    return lambda: frame._modal(CodexPermissionDialog(frame, "Codex preview", request, detail))


def d_codex_question(frame, env):
    from types import SimpleNamespace
    from thechatplace.ui.dialogs import QuestionDialog
    request = SimpleNamespace(questions=lambda: [
        {"id": "choice", "header": "Checks", "question": "Which checks should run?",
         "options": [{"label": "Unit tests"}, {"label": "Smoke test"}]},
        {"id": "secret", "header": "Secret", "question": "Enter the test secret.", "isSecret": True}])
    return lambda: frame._modal(QuestionDialog(frame, "Codex preview", request, provider="Codex"))


def d_plan(frame, env):
    _waiting(frame, _request("ExitPlanMode", {"plan": (
        "# Plan\n\n1. Flush and close the progress file before signalling.\n"
        "2. Wait on the signal in the test.\n3. Run the suite ten times.\n")}, "p1"))
    return frame.on_answer


def d_manage_groups(frame, env):
    frame.groups.create("Releases")
    frame.groups.create("Website")
    for session in frame._snapshot.sessions[:2]:
        frame.groups.add("Releases", session.key)
    return frame.on_manage_groups


def d_command_picker(frame, env):
    import fake_env
    from thechatplace.ui.main_frame import _folder_key
    _open(frame, "Visual probe")
    key = _folder_key(frame._open.cwd)
    if key not in frame._commands:
        frame._fetch_commands(frame._open.cwd)
        fake_env.pump(lambda: key in frame._commands, timeout=10)
    return frame.on_insert_command


def d_bug_report(frame, env):
    return frame.on_report_bug


def d_code_blocks(frame, env):
    _open(frame, "Fix the flaky upload test")
    _select_message(frame, "What changed")
    return frame.on_code_blocks


def d_links(frame, env):
    _open(frame, "Fix the flaky upload test")
    return frame.on_links


def d_find_all(frame, env):
    """Find in All Sessions' results, for a word the made-up sessions use."""
    from thechatplace.search import Target, search_sessions
    targets = [Target(info.key, info.title, info.transcript_path)
               for info in (frame._current_info(key) for key in frame._list_keys) if info]
    results = search_sessions(targets, "the")
    return lambda: frame._open_find_results(results)


def d_changes(frame, env):
    _open(frame, "Visual probe")
    return frame.on_changes


def d_lists_commands(frame, env):
    """View, Lists on Commands (#279)."""
    from thechatplace import lists
    # The session that ran a command, so the picture shows a row and its
    # details rather than the empty "No commands" line.
    _open(frame, "Fix the flaky upload test")
    return lambda: frame.on_lists(lists.COMMANDS, lists.SCOPE_CHAT)


def d_lists_decisions(frame, env):
    """View, Lists on Decisions (#279): one answered question and one not."""
    from thechatplace import lists
    from thechatplace.transcript import ANSWER, QUESTION, ChatMessage
    _open(frame, "Fix the flaky upload test")
    turn = frame._chat_messages[-1].turn if frame._chat_messages else 0
    # Keys end in the tool call, as the transcript's do (lists._tool_id).
    frame._chat_messages += [
        ChatMessage(QUESTION, "Which branch should the release use?\nOptions: release/0.2; main.",
                    key="probe-q1:toolu_q1", turn=turn),
        ChatMessage(ANSWER, "Which branch should the release use? — release/0.2",
                    key="probe-a1:toolu_q1", turn=turn),
        ChatMessage(QUESTION, "Run the slow tests too?", key="probe-q2:toolu_q2", turn=turn)]
    frame._rebuild_chat_list(focus_newest=True)
    return lambda: frame.on_lists(lists.DECISIONS, lists.SCOPE_CHAT)


def d_lists_tasks(frame, env):
    """View, Tasks (#231): Claude's task list, one step of each state."""
    from thechatplace import lists
    from thechatplace.tasklist import TaskStep
    _open(frame, "Fix the flaky upload test")
    frame._chat_tasks = [
        TaskStep("1", "Find why the upload test fails", status="completed", turn=1),
        TaskStep("2", "Make the retry wait for the server", status="in_progress",
                   active_form="Making the retry wait for the server",
                   description="Wait for the server's ready line before retrying.", turn=1),
        TaskStep("3", "Run the whole test suite", turn=1)]
    return lambda: frame.on_lists(lists.TASKS, lists.SCOPE_CHAT)



def _codex_plan_preview(frame, env):
    from dataclasses import replace
    from thechatplace.tasklist import TaskStep
    s_main_codex_owned(frame, env)
    state = replace(frame._codex_states[frame._open.key], plan_received=True, tasks=(
        TaskStep('first', 'Find why the upload test fails', status='completed'),
        TaskStep('second', 'Make the retry wait for the server', status='in_progress'),
        TaskStep('third', 'Run the whole test suite')))
    frame._on_codex_update(state)


def d_lists_codex_tasks(frame, env):
    from thechatplace import lists
    _codex_plan_preview(frame, env)
    return lambda: frame.on_lists(lists.TASKS, lists.SCOPE_CHAT)


def d_lists_codex_no_tasks(frame, env):
    from thechatplace import lists
    s_main_codex(frame, env)
    return lambda: frame.on_lists(lists.TASKS, lists.SCOPE_CHAT)


def d_lists_codex_owned_no_tasks(frame, env):
    from thechatplace import lists
    s_main_codex_owned(frame, env)
    return lambda: frame.on_lists(lists.TASKS, lists.SCOPE_CHAT)


def d_properties_codex_tasks(frame, env):
    _codex_plan_preview(frame, env)
    frame.session_list.SetFocus()
    modal = frame._modal
    def show_progress(dialog):
        for index in range(dialog.list.GetCount()):
            if dialog.list.GetString(index).startswith('Tasks:'):
                dialog.list.SetSelection(index)
                dialog.list.SetFirstItem(max(0, index - 10))
                break
        return modal(dialog)
    frame._modal = show_progress
    return frame.on_properties


def d_usage(frame, env):
    _open(frame, "Visual probe")
    return frame.on_usage


def d_usage_codex(frame, env):
    from thechatplace.ui.dialogs import UsageDialog
    from thechatplace.providers.codex_usage import usage_lines
    from thechatplace.providers.codex_tokens import CodexTokenUsage, context_lines
    lines = context_lines(CodexTokenUsage(70000, 5000, 75000, 250000, 100000)) + usage_lines({
        'rateLimits': {'primary': {'usedPercent': 25, 'windowDurationMins': 300},
                       'secondary': {'usedPercent': 40, 'windowDurationMins': 10080}}})
    return lambda: frame._modal(UsageDialog(frame, lines, frame._copy_usage))


def d_properties(frame, env):
    """Highlighted in the session list, not loaded: then the session's
    properties are shown wherever focus is, never a message's."""
    _select(frame, "Visual probe")
    frame.session_list.SetFocus()
    return frame.on_properties


def d_properties_codex(frame, env):
    s_main_codex(frame, env)
    frame.session_list.SetFocus()
    return frame.on_properties


def d_about_you(frame, env):
    home = env["tmp"] / "claude"
    (home / "skills" / "release").mkdir(parents=True, exist_ok=True)
    (home / "CLAUDE.md").write_text("# How I like to work\n\nTest everything.\n", encoding="utf-8")
    (home / "skills" / "release" / "SKILL.md").write_text(
        "---\nname: release\ndescription: Make a release\n---\nSteps.\n", encoding="utf-8")
    return frame.on_about_you


def d_session_columns(frame, env):
    return frame.on_session_columns


def d_prompts(frame, env):
    frame.prompts.add("Review", "Review this change for bugs, then for accessibility.")
    frame.prompts.add("Release notes", "Draft release notes for what changed since the last tag.")
    return frame.on_prompts


def d_prompt_edit(frame, env):
    from thechatplace.ui.dialogs import PromptEditDialog
    return lambda: frame._modal(PromptEditDialog(frame, "Edit Prompt", "Review",
                                                 "Review this change for bugs."))


def d_rename(frame, env):
    _select(frame, "Visual probe")
    return frame.on_rename


#: name -> (kind, function, what it shows). Order is the order photographed:
#: main-window states first, then dialogs.
SURFACES = {
    "main-compacted": ("window", s_main_compacted,
                       "Compaction and command output remain visible with tool activity off."),
    "main-start": ("window", s_main_start, "Main window as it opens: session list, no session loaded"),
    "main-own": ("window", s_main_own, "An own session loaded: messages and the reply box"),
    "main-own-working": ("window", s_main_own_working,
                         "An own session mid-turn, one more message queued and a draft typed"),
    "main-attachments": ("window", s_main_attachments,
                         "An own session with two files attached to the next message"),
    "main-desktop": ("window", s_main_desktop,
                     "A desktop app session loaded: the read-only panel instead of a reply box"),
    "main-codex": ("window", s_main_codex,
                   "Synthetic Codex history in the shared window: read-only controls and Codex labels"),
    "main-codex-owned": ("window", s_main_codex_owned,
                         "Owned Codex chat with send/stop controls, queued text and a draft"),
    "main-codex-interrupted": ("window", s_main_codex_interrupted,
                               "Interrupted Codex chat with a safe reason and explicit Stop recovery"),
    "main-codex-request-declined": ("window", s_main_codex_request_declined,
                                    "Working Codex chat after an unsupported request was safely declined"),
    "main-codex-context": ("window", s_main_codex_context,
                           "Owned Codex chat with reported live context and a queued warning"),
    "main-codex-filter": ("window", s_main_codex_filter,
                          "Synthetic Codex-only list with explicit Provider column"),
    "main-codex-last-message": ("window", s_main_codex_last_message,
                                "Synthetic Codex last-message column populated through the adapter"),
    "main-codex-unavailable": ("window", s_main_codex_unavailable,
                               "Cached synthetic Codex history with a redacted discovery outage notice"),
    "main-activity": ("window", s_main_activity, "Show tool activity turned on"),
    "main-last-message": ("window", s_main_last_message,
                          "The session list with the Last message column (#146)"),
    "main-status-bar": ("window", s_main_status_bar,
                        "The status bar with every button showing: context, needs you, update"),
    "main-narrow": ("window", s_main_narrow, "An own session in a 640 by 480 window"),
    "shortcuts-page": ("dialog", d_shortcuts_page, "Help, Keyboard Shortcuts (formatted page)"),
    "shortcuts-plain": ("dialog", d_shortcuts_plain, "Keyboard Shortcuts as plain text"),
    "user-guide": ("dialog", d_user_guide, "Help, User Guide (formatted page)"),
    "message-formatted": ("dialog", d_message_formatted,
                          "A message read as a formatted page: heading, list, code, table, link"),
    "message-plain": ("dialog", d_message_plain, "The same message as plain text"),
    "settings": ("dialog", d_settings, "Settings with no Codex installation"),
    "settings-codex": ("dialog", d_settings_codex, "Settings with Codex automatically included"),
    "new-session": ("dialog", d_new_session, "File, New Session"),
    "codex-new": ("dialog", d_codex_new, "New Session with Codex selected as Provider"),
    "codex-change-mode": ("dialog", d_codex_mode, "Change permissions for an owned Codex chat"),
    "properties-codex-owned": ("dialog", d_codex_owned_properties, "Verified owned Codex model and filesystem scope"),
    "codex-models": ("dialog", d_codex_models, "New Codex chat with advertised model and effort choices"),
    "codex-continue": ("dialog", d_codex_continue, "Continue a possibly active Codex chat with explicit runtime policy"),
    "github-repos": ("dialog", d_github_repos, "New Session, From GitHub: your repositories"),
    "continue-here": ("dialog", d_continue_here, "Continue Here (a desktop session)"),
    "update-installed": ("dialog", d_update_installed, "After an update is installed"),
    "permission": ("dialog", d_permission, "Claude asks permission to run a command"),
    "codex-permission": ("dialog", d_codex_permission, "Codex asks for one-time command permission"),
    "chat-permissions": ("dialog", d_chat_permissions, "File, Chat Permissions: a Claude chat's kept rules"),
    "chat-permissions-empty": ("dialog", d_chat_permissions_empty,
                               "File, Chat Permissions with nothing kept"),
    "chat-permissions-codex": ("dialog", d_chat_permissions_codex,
                               "File, Chat Permissions: a Codex chat, nothing kept, and Codex's rule files"),
    "codex-files": ("dialog", d_codex_files, "Codex file changes to review before allowing"),
    "codex-question": ("dialog", d_codex_question, "Codex choices and a masked secret answer"),
    "question": ("dialog", d_question, "Claude asks questions (one choice and several choices)"),
    "plan": ("dialog", d_plan, "Claude's plan to approve"),
    "manage-groups": ("dialog", d_manage_groups, "File, Manage Groups"),
    "command-picker": ("dialog", d_command_picker, "Insert Command or Skill"),
    "bug-report": ("dialog", d_bug_report, "Help, Report a Bug"),
    "code-blocks": ("dialog", d_code_blocks, "View, Lists: a message's code blocks"),
    "links": ("dialog", d_links, "View, Lists: Links"),
    "find-all": ("dialog", d_find_all, "View, Find in All Sessions: the results"),
    "changes": ("dialog", d_changes, "View, Lists: Changed Files"),
    "lists-commands": ("dialog", d_lists_commands, "View, Lists: Commands"),
    "lists-decisions": ("dialog", d_lists_decisions, "View, Lists: Decisions"),
    "lists-tasks": ("dialog", d_lists_tasks, "View, Tasks: Claude's task list"),
    "lists-codex-tasks": ("dialog", d_lists_codex_tasks, "View, Tasks: Codex's live plan"),
    "lists-codex-no-tasks": ("dialog", d_lists_codex_no_tasks, "View, Tasks: no task list from an external Codex chat"),
    "lists-codex-owned-no-tasks": ("dialog", d_lists_codex_owned_no_tasks, "View, Tasks: owned Codex chat with no tasks"),
    "properties-codex-tasks": ("dialog", d_properties_codex_tasks, "Codex Properties with live task progress"),
    "usage": ("dialog", d_usage, "View, Usage and Context"),
    "usage-codex": ("dialog", d_usage_codex, "Codex account usage and reset reports"),
    "properties": ("dialog", d_properties, "View, Properties of a session"),
    "properties-codex": ("dialog", d_properties_codex,
                         "Properties of a synthetic Codex chat with provider-specific history labels"),
    "about-you": ("dialog", d_about_you, "View, What Claude Knows About You"),
    "session-columns": ("dialog", d_session_columns, "View, Session List Columns"),
    "prompts": ("dialog", d_prompts, "File, Prompts"),
    "prompt-edit": ("dialog", d_prompt_edit, "Editing a saved prompt"),
    "rename": ("dialog", d_rename, "Rename Session (wx's own text entry dialog)"),
    "main-empty": ("window", s_main_start, "Main window with no sessions at all (a first run)"),
}

#: Surfaces built on a world with no sessions.
EMPTY_WORLD = {"main-empty"}

#: Dialog classes the probe deliberately doesn't photograph, and why.
#: ``tests/test_ui_probe.py`` fails on any dialog class in neither list.
NOT_PHOTOGRAPHED = {
    "LinesDialog": "the list Usage and Context and Properties are made from, shown as those",
}

#: Which of our dialog classes each dialog surface shows. "rename" shows
#: wx's own TextEntryDialog.
DIALOG_CLASSES = {
    "shortcuts-page": "FormattedMessageDialog",
    "shortcuts-plain": "ShortcutsDialog",
    "user-guide": "FormattedMessageDialog",
    "message-formatted": "FormattedMessageDialog",
    "message-plain": "MessageDialog",
    "settings": "SettingsDialog",
    "settings-codex": "SettingsDialog",
    "new-session": "NewSessionDialog",
    "codex-new": "NewSessionDialog",
    "codex-models": "NewSessionDialog",
    "codex-change-mode": "SingleChoiceDialog",
    "properties-codex-owned": "PropertiesDialog",
    "codex-continue": "NewSessionDialog",
    "github-repos": "GitHubRepoDialog",
    "continue-here": "NewSessionDialog",
    "update-installed": "UpdateInstalledDialog",
    "permission": "PermissionDialog",
    "codex-permission": "CodexPermissionDialog",
    "chat-permissions": "ChatPermissionsDialog",
    "chat-permissions-empty": "ChatPermissionsDialog",
    "chat-permissions-codex": "ChatPermissionsDialog",
    "codex-files": "CodexPermissionDialog",
    "codex-question": "QuestionDialog",
    "question": "QuestionDialog",
    "plan": "PlanDialog",
    "manage-groups": "ManageGroupsDialog",
    "command-picker": "CommandPickerDialog",
    "bug-report": "BugReportDialog",
    "code-blocks": "ListsDialog",
    "links": "ListsDialog",
    "find-all": "FindResultsDialog",
    "changes": "ListsDialog",
    "lists-commands": "ListsDialog",
    "lists-decisions": "ListsDialog",
    "lists-tasks": "ListsDialog",
    "lists-codex-tasks": "ListsDialog",
    "lists-codex-no-tasks": "ListsDialog",
    "lists-codex-owned-no-tasks": "ListsDialog",
    "properties-codex-tasks": "PropertiesDialog",
    "usage": "UsageDialog",
    "usage-codex": "UsageDialog",
    "properties": "PropertiesDialog",
    "properties-codex": "PropertiesDialog",
    "about-you": "AboutYouDialog",
    "session-columns": "SessionColumnsDialog",
    "prompts": "PromptsDialog",
    "prompt-edit": "PromptEditDialog",
    "rename": "TextEntryDialog",
}

#: What a formatted-page surface shows instead where there's no web view
#: (a Mac, or Windows without the WebView2 runtime).
PLAIN_INSTEAD = {"shortcuts-page": "ShortcutsDialog", "user-guide": "MessageDialog",
                 "message-formatted": "MessageDialog"}


def expected_class(name: str) -> str:
    from thechatplace.ui import dialogs
    if name in PLAIN_INSTEAD and not dialogs.formatted_view_available():
        return PLAIN_INSTEAD[name]
    return DIALOG_CLASSES[name]


# -- running --------------------------------------------------------------------------------


def _open_modal_dialog():
    for window in wx.GetTopLevelWindows():
        if isinstance(window, wx.Dialog) and window.IsShown() and window.IsModal():
            return window
    return None


def _webviews(window):
    try:
        import wx.html2
    except ImportError:
        return []
    found, stack = [], list(window.GetChildren())
    while stack:
        child = stack.pop()
        if isinstance(child, wx.html2.WebView):
            found.append(child)
        stack.extend(child.GetChildren())
    return found


def _pages_drawn(dialog, views) -> bool:
    """Every web view in ``dialog`` shows more than one colour. WebView2
    draws in its own process after IsBusy has gone false, and asking the page
    (RunScript) from inside this timer can wait forever, so the picture
    itself is the test: a page that hasn't drawn yet is one flat colour.
    PrintWindow, not the screen: at 175% the taskbar covers the bottom of a
    tall dialog, and its colours would pass for a drawn page."""
    if not IS_WINDOWS:
        return True
    bitmap = _print_window(dialog)
    if bitmap is None:
        return False
    left, top, _right, _bottom = _window_rect(dialog.GetHandle())
    for view in views:
        rect = view.GetScreenRect()
        area = wx.Rect(rect.x - left + 8, rect.y - top + 8, rect.width - 16, rect.height - 16)
        area = area.Intersect(wx.Rect(0, 0, bitmap.GetWidth(), bitmap.GetHeight()))
        if area.width < 4 or area.height < 4 or _one_colour(bitmap.GetSubBitmap(area)):
            return False
    return True


def photograph_dialog(opener, out: Path, stem: str) -> dict:
    """Run ``opener`` (which shows a modal dialog, now or a moment later),
    photograph the dialog once it has settled, and cancel it."""
    result = {"kind": "dialog"}
    state = {"seen": None, "since": 0.0, "done": False, "ticking": False,
             "started": time.time()}

    def tick(_event=None):
        # Taking a picture can pump messages, so the timer could fire inside it.
        if state["done"] or state["ticking"]:
            return
        state["ticking"] = True
        try:
            _tick()
        finally:
            state["ticking"] = False

    def _tick():
        dialog = _open_modal_dialog()
        now = time.time()
        if dialog is None:
            if now - state["started"] > DIALOG_TIMEOUT_SECONDS:
                state["done"] = True
                result["error"] = "no dialog appeared"
            return
        if dialog is not state["seen"]:
            state["seen"], state["since"] = dialog, now
            return
        views = _webviews(dialog)
        waited = now - state["since"]
        if waited < (WEBVIEW_SETTLE_SECONDS if views else SETTLE_SECONDS):
            return
        drawn = not views or _pages_drawn(dialog, views)
        if not drawn and waited < DIALOG_TIMEOUT_SECONDS:
            return
        state["done"] = True
        try:
            result.update(_save(dialog, out, stem))
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"capture failed: {exc}"
        if not drawn:
            result["error"] = (f"{NEVER_DREW}: photographed blank after "
                               f"{DIALOG_TIMEOUT_SECONDS:.0f} seconds")
        dialog.EndModal(wx.ID_CANCEL)

    timer = wx.Timer()
    timer.Bind(wx.EVT_TIMER, tick)
    timer.Start(150)
    try:
        opener()
        # A dialog opened later (after background work) is caught here.
        end = time.time() + DIALOG_TIMEOUT_SECONDS + 5
        while not state["done"] and time.time() < end:
            _pump(0.05)
            tick()
        if not state["done"]:
            result["error"] = "no dialog appeared"
    finally:
        timer.Stop()
    return result


def _save(window, out: Path, stem: str) -> dict:
    png = out / f"{stem}.png"
    method = capture(window, png)
    info = describe(window)
    info["capture"] = method
    (out / f"{stem}.json").write_text(json.dumps(info, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    problems = [f"{c['class']} {c['label'] or c['name']!r}: {p}"
                for c in info["controls"] for p in c["problems"]]
    return {"png": png.name, "json": f"{stem}.json", "title": info["title"],
            "class": info["class"], "size": info["size"], "capture": method,
            "problems": problems}


def _dpi_awareness() -> str:
    if not IS_WINDOWS:
        return ""
    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    value = user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext())
    return {0: "unaware", 1: "system aware", 2: "per-monitor aware"}.get(value, str(value))


def _system_scale() -> int:
    """Windows' display scaling, in percent, as the screen really is."""
    if not IS_WINDOWS:
        return 100
    with _physical_pixels():
        return round(ctypes.windll.user32.GetDpiForSystem() * 100 / 96)


def _high_contrast_on() -> bool:
    class HIGHCONTRAST(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                    ("lpszDefaultScheme", ctypes.c_wchar_p)]
    hc = HIGHCONTRAST(ctypes.sizeof(HIGHCONTRAST), 0, None)
    SPI_GETHIGHCONTRAST = 0x0042
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETHIGHCONTRAST, hc.cbSize, ctypes.byref(hc), 0)
    return bool(hc.dwFlags & 1)


def _system_appearance() -> dict:
    appearance = wx.SystemSettings.GetAppearance()
    screen = wx.Display(0).GetClientArea()
    info = {"dark": appearance.IsDark(), "name": appearance.GetName(), "scale": _system_scale(),
            # The work area the windows are fitted to, in the app's (unscaled) pixels.
            "work_area": [screen.width, screen.height]}
    if IS_WINDOWS:
        info["high_contrast"] = _high_contrast_on()
        info["windows_apps_dark"] = _windows_apps_dark()
    return info


def _windows_apps_dark() -> bool:
    """Windows' own dark mode setting for apps. wx's IsDark says whether the
    app's colours are dark, which is a different question."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def session_locked() -> bool:
    """Windows draws nothing for a locked session, so every picture would be
    blank: OpenInputDesktop fails while the lock screen is up."""
    if not IS_WINDOWS:
        return False
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True
    user32.CloseDesktop(ctypes.c_void_p(desktop))
    return False


def photograph(name: str, out: Path, stem: str, size, world: Path) -> dict:
    """One surface, on a new window over fresh made-up data in ``world``."""
    kind, function, about = SURFACES[name]
    empty = name in EMPTY_WORLD
    frame = None
    try:
        env = build_world(world, empty=empty)
        frame = build_frame(env, empty=empty)
        frame.SetPosition((20, 20))
        # Shrunk to fit the screen, as the app sizes itself: at 150% a
        # 1000 by 720 window is taller than a 1080-pixel screen.
        from thechatplace.ui.main_frame import _fitting_size
        frame.SetSize(_fitting_size(*size))
        frame.Show()
        frame.Raise()
        _pump(SETTLE_SECONDS)
        if kind == "window":
            function(frame, env)
            frame.Layout()
            _pump(SETTLE_SECONDS)
            entry = {"kind": "window", **_save(frame, out, stem)}
        else:
            opener = function(frame, env)
            _pump(0.2)
            entry = photograph_dialog(opener, out, stem)
            expected = expected_class(name)
            if entry.get("class") and entry["class"] != expected:
                entry["error"] = f"showed {entry['class']}, expected {expected}"
    except Exception:  # noqa: BLE001
        entry = {"kind": kind, "error": traceback.format_exc(limit=4)}
    finally:
        if frame is not None:
            close_frame(frame)
    entry["about"] = about
    return entry


def run(out: Path, tag: str, names, size) -> int:
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / (f"manifest-{tag}.json" if tag else "manifest.json")
    manifest = {
        "tag": tag, "platform": sys.platform, "os": platform_paths.os_name(),
        "wx": wx.version(), "python": platform.python_version(),
        # This Python's; a built app's own manifest may say otherwise.
        "dpi_awareness": _dpi_awareness(), "appearance": _system_appearance(),
        "size": list(size),
        # Every surface, or only some (--surface): a comparison expects only
        # what a partial run set out to take.
        "complete": list(names) == list(SURFACES), "surfaces": {},
    }
    started = time.time()
    # The same folder every run, not a random temporary one: its path shows
    # in New Session and elsewhere, and a path that changed every run would
    # show as a change against the baseline.
    tmp = Path(tempfile.gettempdir()) / "tcp-probe"
    shutil.rmtree(tmp, ignore_errors=True)
    if tmp.exists():
        # Something still holds it: an earlier probe, or its msedgewebview2.
        # Reusing a live WebView2 profile, or an old world's files, would
        # show as failures or as changes that aren't the app's.
        print(f"{tmp} couldn't be cleared: an earlier probe (or its msedgewebview2) is still "
              "running. End it and run again.")
        return 3
    tmp.mkdir(parents=True)
    # One WebView2 profile for the run (the runtime keeps using the first
    # one it was given), and none of it in the real local app data.
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(tmp / "webview2")
    for name in names:
        stem = f"{name}-{tag}" if tag else name
        print(f"  {stem}", flush=True)
        entry = photograph(name, out, stem, size, tmp / name)
        if str(entry.get("error", "")).startswith(NEVER_DREW):
            # WebView2 now and then never draws a page in a fresh VM
            # ("WebViewCreated ... Operation aborted"); a new window
            # usually does. Still blank twice is reported.
            print(f"  {stem} (again: its page never drew)", flush=True)
            # A formatted page shows no folder, so a file left here is harmless.
            shutil.rmtree(tmp / name, ignore_errors=True)
            entry = photograph(name, out, stem, size, tmp / name)
            entry["retried"] = True
        manifest["surfaces"][name] = entry
        manifest["seconds"] = round(time.time() - started, 1)
        # Written after every surface, so a crash keeps what was done.
        manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
    results = manifest["surfaces"]
    failed = [n for n, r in results.items() if r.get("error")]
    flagged = [n for n, r in results.items() if r.get("problems")]
    print(f"{len(results) - len(failed)} of {len(results)} surfaces photographed; "
          f"{len(flagged)} with layout problems flagged; manifest: {manifest_path}")
    for name in failed:
        print(f"FAILED {name}: {results[name]['error'].strip().splitlines()[-1]}")
    return 1 if failed else 0


def _size(text: str):
    try:
        width, height = (int(n) for n in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} isn't WIDTHxHEIGHT, e.g. 1000x720")
    return width, height


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="folder for the pictures and descriptions")
    parser.add_argument("--tag", default="", help="variant label added to each file name, "
                                                  "e.g. light-100")
    parser.add_argument("--surface", action="append", choices=list(SURFACES),
                        help="only these surfaces (repeatable); all by default")
    parser.add_argument("--size", type=_size, default=DEFAULT_SIZE,
                        help="main window size, WIDTHxHEIGHT")
    parser.add_argument("--list", action="store_true", help="list the surfaces and stop")
    args = parser.parse_args(argv)
    if args.list:
        for name, (_kind, _f, about) in SURFACES.items():
            print(f"{name:20} {about}")
        return 0
    if args.out is None:
        parser.error("--out is required")
    if session_locked():
        print("The session is locked, so every picture would be blank. Unlock it and run again.")
        return 2
    # As the app does, so the probe photographs it as people see it (#185).
    from thechatplace import platform_paths
    platform_paths.make_dpi_aware()
    app = wx.App(False)  # noqa: F841
    return run(args.out, args.tag, args.surface or list(SURFACES), args.size)


if __name__ == "__main__":
    sys.exit(main())
