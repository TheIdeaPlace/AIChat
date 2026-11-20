"""The formatted page's watchdog against a real Edge WebView2 (#693), on CI's
Windows machines: a healthy page must load, keep beating, and never be given
up, so the watchdog can't turn the formatted view off on a working PC.
Windows only, and a window test: never run it on Kelly's PC.

The page is shown with ShowModal, as the app shows it: WebView2's callbacks
and wx's timers need a real event loop, which the tests' usual pumping isn't.
Checks run from that loop and end it when done, or when time runs out."""
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Edge WebView2 is Windows-only")


def test_a_healthy_page_loads_beats_and_is_never_given_up(app, tmp_path, monkeypatch):
    import wx
    from thechatplace.rendering import message_page
    from thechatplace.ui import dialogs
    monkeypatch.setenv("WEBVIEW2_USER_DATA_FOLDER", str(tmp_path / "webview2"))
    if not dialogs.formatted_view_available():
        pytest.skip("no WebView2 runtime on this machine")
    frame = wx.Frame(None)
    seen = {"loaded_at": None, "beats": 0, "deadline": None}
    started = time.monotonic()
    try:
        page = dialogs.FormattedMessageDialog(
            frame, "Message: Claude", message_page("Message: Claude", "## Result\n\nIt works."))

        def check():
            if not page or page.IsBeingDeleted():
                return
            now = time.monotonic()
            if page._loaded and seen["loaded_at"] is None:
                seen["loaded_at"] = now
                seen["deadline"] = page._deadline
            elif seen["deadline"] is not None and page._deadline > seen["deadline"]:
                seen["beats"] += 1  # the heartbeat moved the deadline on
                seen["deadline"] = page._deadline
            done = page.failed or (seen["loaded_at"] is not None and seen["beats"] >= 2)
            if done or now - started > 60:
                if page.IsModal():
                    page.EndModal(wx.ID_CANCEL)
                return
            wx.CallLater(200, check)

        wx.CallLater(200, check)
        try:
            page.ShowModal()
            assert seen["loaded_at"] is not None, "never loaded"
            assert seen["beats"] >= 2, "no heartbeat"
            assert not page.failed and not dialogs.formatted_view_broken()
        finally:
            page.Destroy()
    finally:
        frame.Destroy()
