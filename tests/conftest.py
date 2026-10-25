import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_codex_diagnostics(monkeypatch, tmp_path):
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, 'codex_diagnostic_path', lambda: tmp_path / 'codex-errors.log')
    from thechatplace.providers import codex_runtime
    from thechatplace.providers.codex_permissions import CodexPermissionStore, CodexReviewStore
    monkeypatch.setattr(codex_runtime, 'CodexPermissionStore', lambda: CodexPermissionStore(tmp_path / 'permissions'))
    monkeypatch.setattr(codex_runtime, 'CodexReviewStore', lambda: CodexReviewStore(tmp_path / 'permissions'))


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
    yield instance


@pytest.fixture(autouse=True)
def _fresh_last_messages(monkeypatch):
    """Each test starts with nothing remembered for the Last message column
    (#146): no cached transcript ends, no sessions known to have none."""
    from thechatplace import hub
    from thechatplace.transcript import LastMessages
    monkeypatch.setattr(hub, "LAST_MESSAGES", LastMessages())
    monkeypatch.setattr(hub, "_NO_TRANSCRIPT", {})


@pytest.fixture(autouse=True)
def _no_real_terminal_sessions(monkeypatch, tmp_path):
    """No test lists the terminal sessions (#158) of the computer it runs
    on; one that wants some points ``hub.terminal_projects_dir`` at its own."""
    from thechatplace import hub
    from thechatplace.transcript import SessionFactsCache
    monkeypatch.setattr(hub, "TERMINAL_FACTS", SessionFactsCache())
    monkeypatch.setattr(hub, "terminal_projects_dir", lambda: tmp_path / "no-terminal-sessions")
