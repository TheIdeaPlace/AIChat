"""A made-up world for the window to run in: every path, the clipboard,
speech, sign-in, notifications and the web view replaced, so nothing touches
the real %APPDATA%, the real claude, the desktop app's files or the speakers.

Shared by the hidden-window tests (``test_ui_hidden.py``'s ``env`` fixture)
and the visual probe (``tools/ui_probe.py``, #155), so what the probe shows
is the window the tests check. ``patch`` is pytest's
``monkeypatch.setattr`` in the tests, and the built-in ``setattr`` in the probe.
"""
import json
import os
import time
from pathlib import Path

import wx

from thechatplace import platform_paths, speech

from records import lines

FAKE_COMMANDS = [
    {"name": "blog-publish", "description": "Publish a post to the blog", "argumentHint": ""},
    {"name": "compact", "description": "Clear history but keep a summary", "builtin": True,
     "argumentHint": "<optional instructions>"},
    {"name": "context", "description": "Show what's using the context", "builtin": True},
]


def now_ms():
    return int(time.time() * 1000)


def install(root: Path, patch, formatted_view: bool = False) -> dict:
    """Point the app at folders under ``root`` and replace everything that
    would reach outside it. ``formatted_view`` leaves the real web view in
    (the probe photographs it); the tests keep it out, because it opens
    modal and waits."""
    patch(platform_paths, "find_codex", lambda: None)
    # Never this computer's own Copilot CLI sessions (#352).
    patch(platform_paths, "copilot_sessions_dir", lambda: root / "copilot" / "session-state")
    # Nor its Copilot CLI, settings, hooks or organisation policy.
    patch(platform_paths, "find_copilot", lambda *args, **kwargs: None)
    patch(platform_paths, "copilot_home", lambda: root / "copilot")
    patch(platform_paths, "copilot_policy_texts", lambda: [])
    desktop = root / "desktop"
    live = root / "live"
    projects = root / "projects"
    cowork = root / "cowork"
    for folder in (desktop, live, projects, cowork):
        folder.mkdir(parents=True, exist_ok=True)
    patch(platform_paths, "desktop_sessions_dirs", lambda: [desktop])
    patch(platform_paths, "cowork_sessions_dirs", lambda: [cowork])
    patch(platform_paths, "live_sessions_dir", lambda: live)
    patch(platform_paths, "projects_dir", lambda: projects)
    patch(speech, "DEFAULT_SETTINGS_PATH", root / "speech.json")
    # The Chat Place's own files (groups.json) go here, never in the real %APPDATA%.
    patch(platform_paths, "app_data_dir", lambda: root / "appdata")
    # Nor the real home folder, where New Session's folder picker starts.
    patch(platform_paths, "default_projects_root", lambda: root / "Projects")
    # No real claude --version (bug reports) or clipboard.
    from thechatplace import bugreport
    from thechatplace.ui import dialogs, main_frame
    patch(main_frame.mac_a11y, "queue_announcement", lambda *args: False)
    patch(bugreport, "claude_code_version", lambda: "2.1.286 (Claude Code)")
    copied = []
    patch(main_frame.MainFrame, "_copy_text",
            lambda self, text: copied.append(text) or True)
    # Nor the real claude for its sign-in (#52): signed in, unless a test says not.
    from thechatplace import codex_signin, copilot_signin, signin
    patch(signin, "check", lambda *a, **k: signin.SignIn(
        True, signed_in=True, method="claude.ai", plan="max", email="k@example.com"))
    patch(codex_signin, "check", lambda *a, **k: codex_signin.SignIn(
        True, signed_in=True, method="ChatGPT"))
    patch(copilot_signin, "check", lambda *a, **k: copilot_signin.SignIn(
        True, signed_in=True, login="synthetic-user", host="https://github.com"))
    # Nor the real claude for a folder's slash commands (#23).
    patch(main_frame, "fetch_commands", lambda exe, cwd: list(FAKE_COMMANDS))
    spoken = []
    feedback = []

    def speak(text, settings, interrupt=True):
        (spoken if interrupt else feedback).append(text)
    patch(speech.speaker, "speak", speak)
    # The frame points the shared speaker at itself (#98); put it back after,
    # so no later test reaches a destroyed frame through it.
    patch(speech.speaker, "on_problem", None)
    patch(speech.speaker, "last_problem", "")
    patch(main_frame, "list_speech_options", lambda: speech.default_options())
    if formatted_view:
        # WebView2's profile goes under root too, not beside the real app's.
        os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(root / "webview2"))
    else:
        # No real web page: it would open modal and wait. Tests that want the
        # formatted view put a fake in.
        patch(main_frame, "formatted_view_available", lambda: False)
        patch(dialogs, "formatted_view_available", lambda: False)
    opened = []
    patch(platform_paths, "open_url", lambda url: opened.append(url))
    # Nor real Claude Code files, an editor or Explorer (#92).
    patch(platform_paths, "claude_home", lambda: root / "claude")
    patch(platform_paths, "codex_home", lambda: root / "codex")
    patch(platform_paths, "agents_skills_dir", lambda: root / "agents-skills")
    patch(platform_paths, "edit_file", lambda path: opened.append(("edit", path)))
    patch(platform_paths, "show_in_folder", lambda path: opened.append(("show", path)))
    boxes = []
    patch(wx, "MessageBox", lambda *a, **k: boxes.append(a[0]) or wx.YES)
    # No real Windows notifications (#20): they're recorded. The hidden test
    # window is never the active one, so every notification would show.
    notified = []

    class FakeNotifier:
        def __init__(self, on_click, tooltip):
            self.on_click = on_click

        def show(self, title, message, key):
            notified.append((title, message, key))
            return True

        def close(self):
            pass
    patch(main_frame, "Notifier", FakeNotifier)
    # No real connection for the session tools (#165): a turn's --mcp-config
    # is still made, and tests call the window's answers directly.
    from thechatplace import session_tools

    class FakeSessionToolsHost:
        def __init__(self, handle, data_dir=None):
            self.handle = handle
            self.closed = False

        def start(self):
            pass

        def close(self):
            self.closed = True
    patch(session_tools, "Host", FakeSessionToolsHost)
    # Nor take the Mac's menu bar from the app you're using.
    activated = []
    from thechatplace.ui import mac_a11y
    patch(mac_a11y, "activate_app", lambda: activated.append(True) or True)
    return {"desktop": desktop, "cowork": cowork, "projects": projects, "spoken": spoken,
            "copied": copied, "feedback": feedback, "opened": opened, "boxes": boxes, "tmp": root,
            "live": live, "notified": notified, "activated": activated}


def add_desktop(env, local, cli, title, cwd="C:\\G\\Repo", ago=60_000, **extra):
    folder = env["desktop"] / local / "org"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"sessionId": local, "cliSessionId": cli, "cwd": cwd, "title": title,
            "isArchived": False, "lastActivityAt": now_ms() - ago}
    data.update(extra)
    (folder / f"{local}.json").write_text(json.dumps(data), encoding="utf-8")


def add_transcript(env, cwd, cli, records):
    folder = env["projects"] / platform_paths.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{cli}.jsonl"
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")
    return path


def pump(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        wx.GetApp().ProcessPendingEvents()
        wx.YieldIfNeeded()
        if condition():
            return True
        time.sleep(0.02)
    return False


class FakeRunner:
    """Stands in for TurnRunner: a turn that starts and never ends by itself."""
    instances = []

    def __init__(self, command, cwd, prompt, on_event, images=None, remote_control=None,
                 task_list=False, mcp_config=None):
        self.command, self.cwd, self.prompt, self.on_event = command, cwd, prompt, on_event
        self.mcp_config = mcp_config
        self.images = images or []
        self.remote_control = remote_control
        self.task_list = task_list
        self.session_started = False
        self.cancelled = False
        self.last_activity = "starting"
        self.waiting_on_background = False
        self.background_tasks = []
        FakeRunner.instances.append(self)

    def start(self):
        pass

    def elapsed(self):
        return 75.0

    def cancel(self):
        self.cancelled = True

    def respond(self, request_id, response):
        self.responses = getattr(self, "responses", []) + [(request_id, response)]
        return not getattr(self, "ended", False)

    def send_now(self, prompt):
        self.sent_now = getattr(self, "sent_now", []) + [prompt]
        return not getattr(self, "ended", False)

    def steer(self, prompt):
        if getattr(self, "ended", False):
            return False
        self.steered = getattr(self, "steered", []) + [prompt]
        return True

    @property
    def steering(self):
        return list(getattr(self, "steered", []))

    def send_follow_up(self, prompt, images=None):
        if not self.waiting_on_background:
            return False
        self.follow_ups = getattr(self, "follow_ups", []) + [(prompt, images)]
        self.waiting_on_background = False
        return True


def status_field(bar, index: int = 0) -> str:
    """Field ``index`` of the status bar as the screen reader's read-status-bar
    key gets it: the native field's text, or on Linux, where the fields stay
    empty (statusbar._FIELD_TEXT), the part's own words."""
    from thechatplace.ui import statusbar
    if statusbar._FIELD_TEXT:
        return bar.GetStatusText(index)
    assert bar.GetStatusText(index) == ""
    shown = [c for c in bar.GetChildren()
             if isinstance(c, statusbar.StatusText)
             or (isinstance(c, statusbar.StatusButton) and c.IsShown())]
    part = shown[index]
    return part.latest() if isinstance(part, statusbar.StatusText) else part.GetLabel()
