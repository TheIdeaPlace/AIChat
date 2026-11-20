"""The formatted page's watchdog against a real Edge WebView2 (#693), on CI's
Windows machines: a healthy page must load, keep beating, and never be given
up, so the watchdog can't turn the formatted view off on a working PC.
Windows only, and a window test: never run it on Kelly's PC."""
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Edge WebView2 is Windows-only")


def _pump_until(condition, seconds):
    import wx
    end = time.time() + seconds
    while time.time() < end:
        wx.GetApp().ProcessPendingEvents()
        wx.YieldIfNeeded()
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_a_healthy_page_loads_beats_and_is_never_given_up(app, tmp_path, monkeypatch):
    import wx
    from thechatplace.rendering import message_page
    from thechatplace.ui import dialogs
    monkeypatch.setenv("WEBVIEW2_USER_DATA_FOLDER", str(tmp_path / "webview2"))
    if not dialogs.formatted_view_available():
        pytest.skip("no WebView2 runtime on this machine")
    frame = wx.Frame(None)
    try:
        page = dialogs.FormattedMessageDialog(
            frame, "Message: Claude", message_page("Message: Claude", "## Result\n\nIt works."))
        try:
            page.Show()
            assert _pump_until(lambda: page._loaded or page.failed, 30), "never loaded"
            assert page._loaded and not page.failed
            # Its heartbeat comes through the page's "hub" handler.
            first = page._deadline
            assert _pump_until(lambda: page._deadline > first, 10), "no heartbeat"
            assert not page.failed and not dialogs.formatted_view_broken()
        finally:
            page.Destroy()
    finally:
        frame.Destroy()
