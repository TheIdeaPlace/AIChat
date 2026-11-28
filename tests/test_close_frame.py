"""fake_env.close_frame, the one way the tests close a MainFrame (#757):
the window is deleted whatever fails, and the history keepers are left open
for the next window. These build hidden windows: run them in the VM or CI."""
import pytest

wx = pytest.importorskip("wx")

import fake_env  # noqa: E402
from test_ui_hidden import env  # noqa: E402,F401
from thechatplace import history_keeper, speech  # noqa: E402
from thechatplace.own_store import OwnSessionStore  # noqa: E402


def _frame(env):
    from thechatplace.ui.main_frame import MainFrame
    return MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"), check_updates_at_start=False)


def _keepers_open():
    return not history_keeper._closed_all and not history_keeper.keeper()._closed


def test_it_deletes_the_window_and_leaves_the_keepers_open(env):
    window = _frame(env)
    fake_env.close_frame(window)
    assert not window  # deleted, not just scheduled
    assert _keepers_open()  # Claude's provider closed them for good; open again
    assert speech.speaker.on_problem is None  # not left calling into the window


def test_a_backend_that_fails_to_close_still_lets_the_window_go(env, monkeypatch):
    window = _frame(env)

    def fail():
        raise RuntimeError("Session provider cleanup failed")
    monkeypatch.setattr(window.sessions, "close", fail)
    fake_env.close_frame(window)
    assert not window
    assert _keepers_open()


def test_a_window_the_test_closed_itself_is_finished_off(env):
    # Close() runs the app's own close, with its provider cleanup on a
    # thread of its own: that's waited for, and the keepers opened again.
    window = _frame(env)
    window.Close(force=True)
    fake_env.close_frame(window)
    assert not window
    assert not window._provider_cleanup.is_alive()
    assert _keepers_open()
