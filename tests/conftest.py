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


def _report_hang(config, nodeid):
    """Every thread's Python stack, and on a Mac the native ones as well
    (sample), then end the run (#757). The test run hung in the middle on
    macOS CI and was cut off at the job's limit with nothing to say where:
    pytest -q doesn't name the running test, and a thread stuck in native code
    answers no signal."""
    import faulthandler
    import os
    import subprocess
    capture = config.pluginmanager.getplugin("capturemanager")
    try:
        if capture is not None:
            capture.suspend_global_capture(in_=False)  # back to the real stderr
    except Exception:  # noqa: BLE001 - say what's known regardless
        pass
    err = sys.__stderr__
    err.write(f"\n\n#757: {nodeid} has run for {HANG_SECONDS} seconds. Every thread:\n")
    err.flush()
    faulthandler.dump_traceback(file=err, all_threads=True)
    err.flush()
    if sys.platform == "darwin" and os.path.exists("/usr/bin/sample"):
        err.write("\nNative stacks (sample):\n")
        err.flush()
        try:
            subprocess.run(["/usr/bin/sample", str(os.getpid()), "3"], stdout=err, stderr=err,
                           stdin=subprocess.DEVNULL, timeout=60)
        except Exception as exc:  # noqa: BLE001
            err.write(f"sample failed: {exc}\n")
    err.flush()
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
