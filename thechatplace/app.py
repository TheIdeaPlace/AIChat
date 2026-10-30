"""Start The Chat Place."""
from __future__ import annotations

import getpass
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from . import __version__, platform_paths
from .ui_text import key_name

APP_TITLE = "The Chat Place"


def error_log_path() -> Path:
    return platform_paths.app_data_dir() / "error.log"


def log_exception(exc_type, exc, tb, where: str = "", path: Optional[Path] = None) -> None:
    """Append an unhandled exception to error.log. Never raises."""
    path = path or error_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"--- {stamp} The Chat Place {__version__} {where}\n")
            handle.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:  # noqa: BLE001
        pass


def install_error_logging() -> None:
    """Unhandled exceptions (main thread, worker threads, wx handlers) go to
    error.log in The Chat Place's folder as well as stderr, which pythonw drops."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        log_exception(exc_type, exc, tb, "main thread")
        try:
            previous(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

    def thread_hook(args):
        log_exception(args.exc_type, args.exc_value, args.exc_traceback,
                      f"thread {getattr(args.thread, 'name', '?')}")

    sys.excepthook = hook
    threading.excepthook = thread_hook


def is_hub_window_title(title: str) -> bool:
    return title == APP_TITLE or title.endswith(f" — {APP_TITLE}")


def smoke_test(out_path: str) -> int:
    """Check a packaged build has everything it needs, without opening a
    window, and write what was found as JSON. CI runs the built exe with
    ``--smoke-test <file>`` to catch a missing module or data file before
    anything is signed or published."""
    import json

    from . import speech, updater

    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False))}
    problems = []
    try:
        import wx

        report["wx"] = wx.version()
        from .ui import main_frame  # noqa: F401 - imported to prove it's bundled
        from .providers.codex import CodexProvider  # noqa: F401 - no process on import
        report["codex_reader"] = True
        from .providers.codex_runtime import CodexChatRuntime  # noqa: F401
        report["codex_execution"] = True
        from .providers.codex_permissions import CodexPermissionStore  # noqa: F401
        report["codex_permissions"] = True
        from .providers.copilot import CopilotProvider  # noqa: F401 - reads files only
        report["copilot_reader"] = True
        from .providers.copilot_runtime import CopilotChatRuntime  # noqa: F401 - no process on import
        report["copilot_execution"] = True
    except Exception as exc:  # noqa: BLE001
        problems.append(f"wx/ui: {exc}")
    try:
        # Formatted full messages (#190): markdown and its extensions, and
        # wx's WebView2 loader, which a build can leave behind.
        from .rendering import message_page
        report["markdown"] = "<h2>" in message_page("t", "## x")
        import wx.html2

        if sys.platform.startswith("linux"):
            # Informational: WebKitGTK is the system's, when it's installed.
            report["webkit_available"] = bool(
                wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendWebKit))
        elif sys.platform == "win32":
            loader = Path(wx.html2.__file__).parent / "WebView2Loader.dll"
            report["webview2_loader"] = loader.is_file()
            if not loader.is_file():
                problems.append(f"missing {loader}")
            # Informational: the runtime itself is the PC's, not the build's.
            report["webview2_available"] = bool(
                wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendEdge))
        else:
            # The Mac's WebKit is part of the system, and the formatted page
            # uses it (#119), so a build without it is broken.
            report["webkit_available"] = bool(
                wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendWebKit))
            if not report["webkit_available"]:
                problems.append("WebKit web view not available")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"formatted messages: {exc}")
    try:
        import velopack  # noqa: F401

        report["velopack"] = True
    except Exception as exc:  # noqa: BLE001
        report["velopack"] = False
        if report["frozen"]:  # every built copy updates through it
            problems.append(f"velopack: {exc}")
    if report["frozen"] and report["velopack"]:
        # Informational: whether Velopack finds this copy's updater. Only a
        # packed app has one (on a Mac, Contents/MacOS/UpdateMac), so a
        # PyInstaller build that isn't packed yet says why not; build_macos.sh
        # requires a version here for the app that goes in the disk image.
        try:
            manager = updater._velopack_manager(updater.feed_url(__version__))
            report["updater"] = str(manager.get_current_version())
        except Exception as exc:  # noqa: BLE001
            report["updater"] = f"unavailable: {exc}"
    scripts = speech._script_dir()
    for name in speech.engine_script_names():
        if not (scripts / name).is_file():
            problems.append(f"missing speech script {scripts / name}")
    if sys.platform == "win32":
        # NVDA speech (#98): NV Access's client, a data file, and comtypes for
        # JAWS, imported only when JAWS is asked to speak.
        from . import screen_readers
        client = screen_readers.nvda_client_path()
        report["nvda_client"] = client.is_file()
        if not client.is_file():
            problems.append(f"missing NVDA controller client {client}")
        try:
            import comtypes.client  # noqa: F401
            report["comtypes"] = True
        except Exception as exc:  # noqa: BLE001
            report["comtypes"] = False
            problems.append(f"comtypes: {exc}")
    if not platform_paths.app_icon_path().is_file():
        # A build that left out the assets would fall back to a stock icon
        # without a word; the smoke test says so instead (#64).
        problems.append(f"missing icon {platform_paths.app_icon_path()}")
    if not platform_paths.user_guide_path().is_file():
        # Help, User Guide reads it (#104).
        problems.append(f"missing user guide {platform_paths.user_guide_path()}")
    # Links to sessions (#144): the parser, and (informational) what this
    # user's thechatplace:// links run on Windows, "" if nothing.
    from . import links

    report["links"] = links.parse_link(links.PREFIX + "smoke-test") == "smoke-test"
    if not report["links"]:
        problems.append("thechatplace:// links don't parse")
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        report["link_registration"] = platform_paths.url_scheme_command() or ""
    if sys.platform.startswith("linux"):
        # Orca's way in for names and announcements (ui/gtk_a11y.py, orca.py).
        from . import orca  # noqa: F401 - imported lazily by the speaker
        from .ui import gtk_a11y
        report["atk"] = gtk_a11y._load() is not None
        if not report["atk"]:
            problems.append("GTK 3 and ATK can't be loaded, so Orca gets no names")
    # The session tools' server (#165): the app started as Claude Code starts
    # it, answering over its stdin and stdout.
    try:
        from . import session_tools_mcp
        report["session_tools"] = session_tools_mcp.self_test()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"session tools server: {exc}")
    lookup = platform_paths.find_claude()
    report["claude"] = lookup.path or lookup.problem
    report["data_outside_install"] = updater.data_is_outside_install_dir()
    report["problems"] = problems
    try:
        Path(out_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        return 2
    return 1 if problems else 0


def register_links_in_background() -> None:
    """Windows: make sure thechatplace:// links (#144) open this copy, if it's
    the installed one; Velopack's install hook does it too, but a copy
    installed before links existed, or a registration someone removed, is
    put right here. Linux: the same for an AppImage, whose desktop entry
    also puts it in the desktop's menu; nothing installs one, so this is
    where it's made. A portable copy or a source run never registers. Off
    the UI thread, and it never stops the app starting."""
    if not getattr(sys, "frozen", False):
        return
    if sys.platform.startswith("linux"):
        # The AppImage file itself, not the program inside it, which is
        # mounted somewhere new at every start.
        executable, installed = os.environ.get("APPIMAGE", ""), bool(os.environ.get("APPIMAGE"))
    elif sys.platform == "win32":
        executable, installed = sys.executable, None
    else:
        return

    def work():
        from . import links, updater
        try:
            known = installed if installed is not None else updater.is_installed_copy(__version__)
            updater.logger.info("link registration: %s",
                                links.ensure_registered(known, executable))
        except Exception as exc:  # noqa: BLE001
            updater.logger.error("link registration failed: %s", exc)

    threading.Thread(target=work, name="links", daemon=True).start()


def hand_link_to_running_copy(link: str) -> bool:
    """Leave ``link`` for the copy that's running, which looks for it every
    second (platform_paths.drop_link). False if it couldn't be left."""
    try:
        platform_paths.drop_link(link)
        return True
    except OSError:
        return False


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--session-tools-mcp"]:
        # The session tools' MCP server (#165), started by Claude Code for a
        # turn The Chat Place runs: no window, no updater, nothing on stdout
        # but the protocol.
        from . import session_tools_mcp
        try:
            return session_tools_mcp.main(argv[1:])
        except Exception:  # noqa: BLE001 - recorded where the app's errors go
            log_exception(*sys.exc_info(), "session tools server")
            return 1
    # Linux, built app: what it starts gets the system's libraries.
    platform_paths.restore_system_library_path()
    from . import links

    # Started with a thechatplace:// link (#144): that's all this start is
    # about, and nothing else on the command line counts.
    link = links.link_argument(argv)
    if link is not None:
        argv = []
    # Velopack's install/update/uninstall hooks first: Update.exe starts the
    # app with hook arguments and expects it to exit, before anything else.
    from . import updater

    # Logging before the hooks, so a failed install or update hook is recorded.
    updater.configure_logging()
    updater.bootstrap(argv if link is None else [link])
    if "--smoke-test" in argv:
        index = argv.index("--smoke-test")
        target = argv[index + 1] if index + 1 < len(argv) else "smoke-test.json"
        return smoke_test(target)
    install_error_logging()
    try:
        import wx
    except ImportError:
        print("The Chat Place needs wxPython: pip install -r requirements.txt", file=sys.stderr)
        return 1
    from .ui.main_frame import MainFrame

    class ChatPlaceApp(wx.App):
        """A Mac hands a thechatplace:// link to the running app as an Apple
        event, which wx turns into MacOpenURL. One can come before the window
        exists (the app was started by the link), so it waits for it."""
        # Class attributes, not set in OnInit: wxOSX can deliver the link
        # that started the app before OnInit runs. There is only ever one
        # app object, so the class-level list isn't shared with anything.
        frame = None
        waiting: list = []

        def MacOpenURL(self, url):  # noqa: N802 - wx's name
            if self.frame is not None and self.frame:
                self.frame.open_link(url)
            else:
                self.waiting.append(url)

    # Before the first window: Windows then doesn't stretch the app (#185),
    # and Linux knows the app by its name.
    platform_paths.make_dpi_aware()
    platform_paths.set_program_name()
    app = ChatPlaceApp(False)
    app.SetAppName(APP_TITLE)
    # One copy at a time: two would both run turns, announce everything
    # twice, and write the same session list.
    checker = wx.SingleInstanceChecker(f"TheChatPlace-{getpass.getuser()}")
    if checker.IsAnotherRunning():
        if link is not None and hand_link_to_running_copy(link):
            # The running copy opens it and comes forward itself; bringing
            # it forward from here as well gets past Windows' focus rules.
            platform_paths.bring_window_forward(is_hub_window_title)
            return 0
        if not platform_paths.bring_window_forward(is_hub_window_title):
            wx.MessageBox(key_name("The Chat Place is already running. Switch to it with Alt+Tab."),
                          APP_TITLE, wx.OK | wx.ICON_INFORMATION)
        return 0
    frame = MainFrame()
    app.frame = frame
    frame.Centre()
    frame.Show()
    # On a Mac, the window can be in front while another app keeps the menu
    # bar (and VO+M); see mac_a11y.activate_app. Does nothing elsewhere.
    from .ui import mac_a11y
    mac_a11y.activate_app()
    pending = ([link] if link is not None else []) + app.waiting
    app.waiting = []
    for each in pending:
        frame.open_link(each)
    register_links_in_background()
    app.MainLoop()
    del checker
    return 0


if __name__ == "__main__":
    sys.exit(main())
