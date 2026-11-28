import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """If the process is still alive a while after the tests end, print
    where every thread is (#711). On macOS CI, pytest has sometimes passed
    every test and then hung until the job's 20-minute limit cancelled it,
    with nothing in the log to say why. Exiting normally cancels this."""
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=False)
    session.config._tcp_exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """On macOS CI, leave as soon as pytest has said its last word (#711).
    The hang above was the main thread alone in native code after Python
    had finished, wx's Cocoa teardown at exit, and it took a passing run past
    the job's limit (#731, twice). Nothing is left to report by now, so the
    process ends with pytest's own status rather than wait on it. Elsewhere,
    and on a Mac outside CI, it exits as usual."""
    import os
    status = getattr(config, "_tcp_exit_status", None)
    if status is not None and sys.platform == "darwin" and os.environ.get("CI"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)


#: A test still running after this long has hung (the slowest take under a
#: minute on a busy macOS runner). TCP_HANG_SECONDS changes it, to try it.
HANG_SECONDS = float(__import__("os").environ.get("TCP_HANG_SECONDS", "300"))


import subprocess as _subprocess  # noqa: E402

#: The real one, kept before any test can replace it: a hang inside a test
#: that fakes subprocess must still get its native report.
_RUN = _subprocess.run
_DEVNULL = _subprocess.DEVNULL


def _report_hang(config, nodeid):
    """Say where a hung test is, then end the run (#757): every thread's
    Python stack, and on a Mac each command's output under its own heading:
    the native stacks (sample), whether the display is asleep (pmset), and
    the window server. The test run hung in the middle on macOS CI and was
    cut off at the job's limit with nothing to say where: pytest -q doesn't
    name the running test, and a thread stuck in native code answers no
    signal. Whatever goes wrong while reporting, the run still ends."""
    import faulthandler
    import os
    try:
        capture = config.pluginmanager.getplugin("capturemanager")
        try:
            if capture is not None:
                capture.suspend_global_capture(in_=False)  # back to the real stderr
        except Exception:  # noqa: BLE001 - say what's known regardless
            pass
        err = sys.__stderr__
        err.write(f"\n\n#757: {nodeid} has run for {HANG_SECONDS:g} seconds. Every thread:\n")
        err.flush()
        faulthandler.dump_traceback(file=err, all_threads=True)
        err.flush()
        if sys.platform == "darwin":
            # The window server too: the first hang caught was the main thread
            # waiting on it for a new window's fence (#757). sudo needs no
            # password only on CI; -n makes it fail at once anywhere else.
            for command in (["/usr/bin/sample", str(os.getpid()), "3"],
                            ["/usr/bin/pmset", "-g"], ["/usr/bin/pmset", "-g", "assertions"],
                            ["/usr/bin/sudo", "-n", "/usr/bin/sample", "WindowServer", "2"]):
                err.write(f"\n$ {' '.join(command)}\n")
                err.flush()
                try:
                    _RUN(command, stdout=err, stderr=err, stdin=_DEVNULL, timeout=60)
                except Exception as exc:  # noqa: BLE001
                    err.write(f"failed: {exc}\n")
                err.flush()
    finally:
        os._exit(3)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """On CI, a test that hangs ends the run with every stack (#757), rather
    than running out the job's time. Setup and teardown count too: the frame
    fixtures build and destroy whole windows."""
    import os
    import threading
    if not os.environ.get("CI"):
        yield
        return
    timer = threading.Timer(HANG_SECONDS, _report_hang, args=(item.config, item.nodeid))
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


_LIBOBJC = None


def _libobjc():
    global _LIBOBJC
    if _LIBOBJC is None:
        import ctypes
        lib = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        lib.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        lib.objc_autoreleasePoolPush.argtypes = []
        lib.objc_autoreleasePoolPop.restype = None
        lib.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        _LIBOBJC = lib
    return _LIBOBJC


def _window_key(window):
    try:
        import wx
        return wx.siplib.unwrapinstance(window)
    except Exception:  # noqa: BLE001 - an older wx: the wrapper stands for it
        return id(window)


def _close_left_windows(wx, before) -> list:
    """Each top-level window this test made and didn't destroy: closed, as
    the tests' own close_frame does, and named. Ones it destroyed are left
    to settle, which deletes them."""
    app = wx.GetApp()
    left = []
    for window in list(wx.GetTopLevelWindows()):
        if _window_key(window) in before or app.IsScheduledForDestruction(window):
            continue
        left.append(f"{type(window).__name__} {window.GetTitle()!r}")
        if hasattr(window, "stop_timers"):
            window.stop_timers()
        pool = getattr(window, "_pool", None)
        if pool is not None:
            pool.shutdown(wait=True)
        app.ProcessPendingEvents()
        window.Destroy()
    return left


def _hub_threads_left(before) -> list:
    """A MainFrame's pool threads ("hub_N") this test started and didn't end."""
    import threading
    left = []
    for thread in threading.enumerate():
        if thread.ident in before or not thread.name.startswith("hub"):
            continue
        thread.join(timeout=5)  # a pool shut down without waiting is still finishing
        if thread.is_alive():
            left.append(thread.name)
    return left


@pytest.fixture(autouse=True)
def _windows_gone_after_each_test(monkeypatch):
    """Every test ends with its windows deleted and their threads ended, and
    on a Mac with what AppKit made for them released (#757).

    The tests drive wx with no event loop. So nothing did what a running
    app does after each event: delete the windows ``Destroy()`` scheduled
    (wx does that in idle time), and drain the main thread's autorelease
    pool. AppKit's autoreleased objects were never released: native windows
    piled up (336 of them, all hidden: combo box lists and text input
    popups, 2.5 GB by the end), and window creation then sometimes waited
    forever on the window server (#757, and #711's hang at exit). Each test
    now runs in a pool of its own, drained once its windows are deleted.

    A window left undestroyed, or a frame's pool left running, fails the
    test: the tests close frames with ``fake_env.close_frame``.

    It asks for ``monkeypatch`` so that its end comes before the test's
    patches are undone: a window still being deleted never reaches the
    real speech, paths or clipboard."""
    import threading
    pool = _libobjc().objc_autoreleasePoolPush() if sys.platform == "darwin" else None
    wx = sys.modules.get("wx")
    before = ({_window_key(w) for w in wx.GetTopLevelWindows()}
              if wx is not None and wx.GetApp() is not None else set())
    threads_before = {t.ident for t in threading.enumerate()}
    problems = []
    try:
        yield
    finally:
        try:
            wx = sys.modules.get("wx")
            if wx is not None and wx.GetApp() is not None:
                left = _close_left_windows(wx, before)
                if left:
                    problems.append(f"windows left open: {left}")
                import fake_env
                if not fake_env.settle():
                    problems.append("destroyed windows were never deleted")
            threads = _hub_threads_left(threads_before)
            if threads:
                problems.append(f"a window's pool left running: {threads}")
        finally:
            if pool is not None:
                _libobjc().objc_autoreleasePoolPop(pool)
    if problems:
        pytest.fail("; ".join(problems) + " (#757: close frames with fake_env.close_frame)")


@pytest.fixture(autouse=True)
def _isolated_codex_diagnostics(monkeypatch, tmp_path):
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: tmp_path / 'codex-errors.log')
    from thechatplace.providers import codex_runtime
    from thechatplace.providers.codex_permissions import CodexPermissionStore, CodexReviewStore
    monkeypatch.setattr(codex_runtime, 'CodexPermissionStore', lambda: CodexPermissionStore(tmp_path / 'permissions'))
    monkeypatch.setattr(codex_runtime, 'CodexReviewStore', lambda: CodexReviewStore(tmp_path / 'permissions'))


@pytest.fixture(autouse=True)
def _isolated_kept_history(monkeypatch, tmp_path):
    from thechatplace import platform_paths, history_keeper
    original_app_data = platform_paths.app_data_dir
    def kept_root():
        # A probe test can create several independent TCP worlds in one test.
        # Follow their overridden data roots, while the default remains isolated.
        root = (tmp_path/'tcp' if platform_paths.app_data_dir is original_app_data
                else platform_paths.app_data_dir())
        return root/'kept-history/claude'
    monkeypatch.setattr(platform_paths, 'kept_history_dir', kept_root)
    # A window closed by an earlier test shuts keepers down for good, as the
    # app does on exit; each test starts with them open again.
    monkeypatch.setattr(history_keeper, '_closed_all', False)
    yield
    history_keeper.close_all()


@pytest.fixture(autouse=True)
def _isolated_long_messages(monkeypatch, tmp_path_factory):
    """Codex's long-message files and remembered text (#625) stay in a
    folder of this test's own, beside (not inside) tmp_path, which some
    tests expect to hold only what they wrote: no test writes to the real
    app data or clears the real temp folder. The store follows a test's
    own app_data_dir when it sets one."""
    from thechatplace import codex_long_messages, platform_paths
    original_app_data = platform_paths.app_data_dir
    own = tmp_path_factory.mktemp('long-messages')
    temp = own / 'temp'
    temp.mkdir()
    monkeypatch.setattr(codex_long_messages, 'temp_dir', lambda: str(temp))
    monkeypatch.setattr(codex_long_messages, '_store_path', lambda: (
        own / 'tcp' if platform_paths.app_data_dir is original_app_data
        else platform_paths.app_data_dir()) / 'long-messages.json')
    codex_long_messages.reset()
    yield
    codex_long_messages.reset()


@pytest.fixture(scope="session")
def app():
    """Keep one wx application alive across every GUI test module.

    wx's native application is process-wide. Creating and releasing a
    different Python wrapper per module can tear down Cocoa state while
    later tests still use it. Import lazily so non-GUI tests need no wx.
    GUI tests require an unsandboxed process with access to the desktop.
    """
    wx = pytest.importorskip("wx")
    instance = wx.GetApp() or wx.App(False)
    # As the app does (#675): on a Mac, buttons get no Command+letter.
    # Nothing elsewhere.
    from thechatplace.ui import mac_keys
    mac_keys.install(instance)
    yield instance


@pytest.fixture(autouse=True)
def _no_build_label(monkeypatch):
    """A labelled test build runs the suite with TCP_BUILD_LABEL set (build_info):
    no test may see it, or the release-tag checks refuse the label."""
    monkeypatch.delenv("TCP_BUILD_LABEL", raising=False)
    monkeypatch.delenv("TCP_BUILD_COMMIT", raising=False)


@pytest.fixture(autouse=True)
def _no_keys_left_from_another_test():
    """What's said names the default keys, whatever a window before set (#635)."""
    from thechatplace.ui_text import set_your_keys
    set_your_keys({})
    yield
    set_your_keys({})


@pytest.fixture(autouse=True)
def _formatted_view_not_given_up():
    """A formatted page that timed out in one test (#693) mustn't turn the
    formatted view off for every test after it. Only if the dialogs are
    loaded: windowless test files never import wx."""
    import sys as _sys
    dialogs = _sys.modules.get("thechatplace.ui.dialogs")
    if dialogs is not None:
        dialogs._FORMATTED_BROKEN = False
    yield
    dialogs = _sys.modules.get("thechatplace.ui.dialogs")
    if dialogs is not None:
        dialogs._FORMATTED_BROKEN = False


@pytest.fixture(autouse=True)
def _fresh_last_messages(monkeypatch):
    """Each test starts with nothing remembered for the Last message column
    (#146): no cached transcript ends, no sessions known to have none."""
    from thechatplace import hub
    from thechatplace.transcript import LastMessages
    monkeypatch.setattr(hub, "LAST_MESSAGES", LastMessages())
    monkeypatch.setattr(hub, "_NO_TRANSCRIPT", {})


@pytest.fixture(autouse=True)
def _no_real_copilot_usage(monkeypatch):
    """Usage and Context starts Copilot CLI to ask for the account's quota, so
    no test does it with the real CLI and sign-in; one that wants a report
    patches this itself."""
    from thechatplace.providers import copilot_usage
    def refuse(*_args, **_kwargs):
        raise copilot_usage.CopilotUsageError("not in tests")
    monkeypatch.setattr(copilot_usage, "read_usage_lines", refuse)


@pytest.fixture(autouse=True)
def _no_real_terminal_sessions(monkeypatch, tmp_path):
    """No test lists the terminal sessions (#158) of the computer it runs
    on; one that wants some points ``hub.terminal_projects_dir`` at its own."""
    from thechatplace import hub
    from thechatplace.transcript import SessionFactsCache
    monkeypatch.setattr(hub, "TERMINAL_FACTS", SessionFactsCache())
    monkeypatch.setattr(hub, "terminal_projects_dir", lambda: tmp_path / "no-terminal-sessions")
