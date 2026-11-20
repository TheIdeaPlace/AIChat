"""The formatted page's engine on each platform (#119): Edge WebView2 on
Windows, WebKit on a Mac, WebKitGTK on Linux. No WebView is made here; CI's Mac job has no display."""
import wx.html2

from thechatplace.ui import dialogs


def test_each_platform_gets_its_own_web_view(monkeypatch):
    monkeypatch.setattr(dialogs.sys, "platform", "win32")
    assert dialogs._webview_backend() == wx.html2.WebViewBackendEdge
    monkeypatch.setattr(dialogs.sys, "platform", "darwin")
    assert dialogs._webview_backend() == wx.html2.WebViewBackendWebKit
    monkeypatch.setattr(dialogs.sys, "platform", "linux")
    assert dialogs._webview_backend() == wx.html2.WebViewBackendWebKit


def test_availability_asks_for_this_platforms_backend(monkeypatch):
    asked = []

    def available(backend):
        asked.append(backend)
        return True

    monkeypatch.setattr(wx.html2.WebView, "IsBackendAvailable", available)
    for platform in ("win32", "darwin", "linux"):
        monkeypatch.setattr(dialogs.sys, "platform", platform)
        assert dialogs.formatted_view_available() is True
    assert asked == [wx.html2.WebViewBackendEdge, wx.html2.WebViewBackendWebKit,
                     wx.html2.WebViewBackendWebKit]


def test_key_relay_reaches_both_engines_and_matches_p_by_its_key():
    relay = dialogs._KEY_RELAY
    assert "window.webkit.messageHandlers.hub" in relay  # WebKit
    assert "window.hub" in relay  # Edge
    # Option+P on a Mac types "π", so P is matched by its code.
    assert "e.code === 'KeyP'" in relay and "e.key === 'p'" not in relay


def test_webview2_data_folder_is_set_only_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("WEBVIEW2_USER_DATA_FOLDER", raising=False)
    monkeypatch.delenv("WEBKIT_DISABLE_DMABUF_RENDERER", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(dialogs.sys, "platform", "darwin")
    dialogs._prepare_webview_data_folder()
    assert "WEBVIEW2_USER_DATA_FOLDER" not in dialogs.os.environ
    assert "WEBKIT_DISABLE_DMABUF_RENDERER" not in dialogs.os.environ
    # Linux: WebKitGTK's DMA-BUF renderer draws a blank page on some drivers.
    monkeypatch.setattr(dialogs.sys, "platform", "linux")
    dialogs._prepare_webview_data_folder()
    assert "WEBVIEW2_USER_DATA_FOLDER" not in dialogs.os.environ
    assert dialogs.os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] == "1"
    monkeypatch.setattr(dialogs.sys, "platform", "win32")
    dialogs._prepare_webview_data_folder()
    assert dialogs.os.environ["WEBVIEW2_USER_DATA_FOLDER"].startswith(str(tmp_path))




F = dialogs.FormattedMessageDialog


class _Page:
    """Just what the watchdog touches (#693), without a web view: the real
    methods run against it, with a clock the test moves."""

    def __init__(self, modal=True, loaded=False, beats=True, active=True):
        self._loaded, self._modal, self._beats, self.active = loaded, modal, beats, active
        self.failed, self.ended, self._page_file, self.now = False, [], None, 100.0
        self._clock = lambda: self.now
        self._heard = False
        self._deadline = self.now + dialogs.FORMATTED_START_TIMEOUT_MS / 1000

    def __bool__(self):
        return True

    def IsBeingDeleted(self):
        return False

    def IsModal(self):
        return self._modal

    def IsActive(self):
        return self.active

    def EndModal(self, code):
        self.ended.append(code)

    def _give_up(self):
        F._give_up(self)

    def _heard_from_page(self):
        F._heard_from_page(self)

    def tick_after(self, seconds):
        self.now += seconds
        F._tick(self)


class _Error:
    def __init__(self, url="", code=None):
        self.url, self.code = url, code if code is not None else wx.html2.WEBVIEW_NAV_ERR_OTHER

    def GetURL(self):
        return self.url

    def GetInt(self):
        return self.code


def _fresh(monkeypatch):
    monkeypatch.setattr(dialogs, "_FORMATTED_BROKEN", False)
    monkeypatch.setattr(wx.html2.WebView, "IsBackendAvailable", lambda backend: True)


def test_a_page_that_never_shows_life_gives_up_once_and_for_the_rest_of_the_run(monkeypatch):
    _fresh(monkeypatch)
    assert dialogs.formatted_view_available() and not dialogs.formatted_view_broken()
    page = _Page()
    page.tick_after(5)
    assert not page.failed  # still within its start time
    page.tick_after(6)
    assert page.failed and page.ended == [dialogs.ID_PLAIN_TEXT]
    page.tick_after(30)
    assert page.ended == [dialogs.ID_PLAIN_TEXT]  # closed once
    # Every later message goes straight to plain text until a restart.
    assert dialogs.formatted_view_broken() and not dialogs.formatted_view_available()


def test_a_page_that_loads_then_goes_silent_gives_up(monkeypatch):
    # Kelly's case: the page loads, then WebView2's browser dies seconds later.
    _fresh(monkeypatch)
    page = _Page()
    page.tick_after(1)
    F._heard_from_page(page)  # loaded
    for _ in range(5):
        page.tick_after(dialogs.FORMATTED_HEARTBEAT_MS / 1000)
        F._heard_from_page(page)  # beating
    assert not page.failed
    page.tick_after(dialogs.FORMATTED_SILENCE_MS / 1000 + 1)  # the beats stop
    assert page.failed and page.ended == [dialogs.ID_PLAIN_TEXT]


def test_a_big_page_has_longer_once_its_navigation_commits(monkeypatch):
    _fresh(monkeypatch)
    page = _Page()
    page.tick_after(2)
    F._on_navigated(page, None)
    page.tick_after(dialogs.FORMATTED_START_TIMEOUT_MS / 1000 + 5)
    assert not page.failed  # a whole long chat still reading in
    page.tick_after(dialogs.FORMATTED_NAVIGATED_TIMEOUT_MS / 1000)
    assert page.failed


def test_silence_counts_only_while_the_window_is_active(monkeypatch):
    # In the background, the page's timers may be slowed: not a crash.
    _fresh(monkeypatch)
    page = _Page()
    F._heard_from_page(page)
    page.active = False
    for _ in range(10):
        page.tick_after(30)
    assert not page.failed
    page.active = True
    page.tick_after(1)
    assert not page.failed  # a fresh grace on coming back
    page.tick_after(dialogs.FORMATTED_SILENCE_MS / 1000 + 1)
    assert page.failed


def test_a_loaded_page_without_a_heartbeat_script_is_left_alone(monkeypatch):
    _fresh(monkeypatch)
    page = _Page(loaded=True, beats=False)
    page.tick_after(600)
    assert not page.failed and not dialogs.formatted_view_broken()


def test_only_the_pages_own_errors_count(monkeypatch):
    _fresh(monkeypatch)
    page = _Page()
    # The first about:blank being replaced by the page is reported as cancelled.
    F._on_error(page, _Error("about:blank", wx.html2.WEBVIEW_NAV_ERR_USER_CANCELLED))
    F._on_error(page, _Error("https://example.com/"))  # not the page
    F._on_error(page, None)
    assert not page.failed
    F._on_error(page, _Error("about:blank"))  # the page itself failed
    assert page.failed and page.ended == [dialogs.ID_PLAIN_TEXT]


def test_an_error_after_loading_is_left_alone(monkeypatch):
    _fresh(monkeypatch)
    page = _Page(loaded=True)
    F._on_error(page, _Error("about:blank"))
    assert not page.failed and not dialogs.formatted_view_broken()


def test_the_timings_and_words():
    assert dialogs.FORMATTED_START_TIMEOUT_MS >= 8000
    assert dialogs.FORMATTED_NAVIGATED_TIMEOUT_MS > dialogs.FORMATTED_START_TIMEOUT_MS
    assert dialogs.FORMATTED_SILENCE_MS >= 3 * dialogs.FORMATTED_HEARTBEAT_MS
    assert str(dialogs.FORMATTED_HEARTBEAT_MS) in dialogs._HEARTBEAT
    assert "plain text" in dialogs.FORMATTED_BROKEN_TEXT
    assert "restart" in dialogs.FORMATTED_BROKEN_TEXT
