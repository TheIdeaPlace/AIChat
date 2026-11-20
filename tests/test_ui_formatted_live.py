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


def test_the_shared_page_loads_each_message_in_the_same_web_view(app, tmp_path, monkeypatch):
    # #693: on Windows one page is kept and loaded again, never destroyed
    # during the run. Each message must load and be read in it.
    import wx
    from thechatplace.rendering import message_page
    from thechatplace.ui import dialogs
    monkeypatch.setenv("WEBVIEW2_USER_DATA_FOLDER", str(tmp_path / "webview2"))
    monkeypatch.setattr(dialogs, "_SHARED_PAGE", None)
    if not dialogs.formatted_view_available():
        pytest.skip("no WebView2 runtime on this machine")
    frame = wx.Frame(None)
    pages, titles, bodies = [], [], []

    def close_once_loaded(started):
        page = dialogs._SHARED_PAGE
        if page and page._loaded:
            pages.append(page)
            titles.append(page.GetTitle())
            try:
                ok, text = page.view.RunScript("document.body.innerText")
            except Exception:  # noqa: BLE001 - recorded as unread
                ok, text = False, ""
            bodies.append(text if ok else "")
            page.EndModal(wx.ID_CANCEL)
        elif time.monotonic() - started < 60:
            wx.CallLater(200, close_once_loaded, started)
        elif page and page.IsModal():
            page.EndModal(wx.ID_CANCEL)

    try:
        for index in range(2):
            title = f"Message: Claude {index}"
            wx.CallLater(200, close_once_loaded, time.monotonic())
            result, _link, failed = dialogs.show_formatted_page(
                frame, title, message_page(title, f"## Reply {index}\n\nText."))
            assert result == wx.ID_CANCEL and not failed
        assert len(pages) == 2 and pages[0] is pages[1], "the page wasn't reused"
        assert titles == ["Message: Claude 0", "Message: Claude 1"]
        # The second message really replaced the first, in the same web view.
        assert "Reply 0" in bodies[0] and "Reply 1" in bodies[1] and "Reply 0" not in bodies[1]
        # Hidden, it holds nothing of the last message.
        assert pages[1]._parked
        assert not dialogs.formatted_view_broken()
    finally:
        page = dialogs._SHARED_PAGE
        dialogs._SHARED_PAGE = None
        if page:
            page.Destroy()
        frame.Destroy()
