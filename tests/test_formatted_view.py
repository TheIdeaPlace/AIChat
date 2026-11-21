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
        self._parked = False

    def _from_parked_page(self):
        return self._parked

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
        # A tick a second, as the real timer: a bigger gap is taken as sleep.
        while seconds > 0:
            step = min(1, seconds)
            self.now += step
            seconds -= step
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
    monkeypatch.setattr(dialogs, "_watch_pages", lambda: True)  # as on Windows
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


class _SharedFake:
    """Stands in for FormattedMessageDialog in show_formatted_page (#693)."""
    made = []

    def __init__(self, parent, title, page, start_at="", reusable=False):
        self.parent, self.titles, self.reusable = parent, [title], reusable
        self.app_link, self.failed, self.destroyed, self.stopped = None, False, False, 0
        self.result = wx.ID_CANCEL
        self.parked = 0
        _SharedFake.made.append(self)

    def __bool__(self):
        return not self.destroyed

    def IsBeingDeleted(self):
        return self.destroyed

    def GetParent(self):
        return self.parent

    def reload(self, title, page, start_at=""):
        self.titles.append(title)

    def ShowModal(self):
        return self.result

    def stop_watching(self):
        self.stopped += 1

    def park(self):
        self.parked += 1

    def IsModal(self):
        return False

    def Destroy(self):
        self.destroyed = True


def test_windows_keeps_one_page_and_reloads_it(monkeypatch):
    _SharedFake.made = []
    monkeypatch.setattr(dialogs, "FormattedMessageDialog", _SharedFake)
    monkeypatch.setattr(dialogs, "_SHARED_PAGE", None)
    monkeypatch.setattr(dialogs, "_reuse_pages", lambda: True)
    for index in range(3):
        result, link, failed = dialogs.show_formatted_page(None, f"Message {index}", "<p>x</p>")
        assert result == wx.ID_CANCEL and link is None and not failed
    # One page, never destroyed: closing a web view is what crashed (#693).
    assert len(_SharedFake.made) == 1
    page = _SharedFake.made[0]
    # Each close empties it, so nothing of a message is left behind.
    assert page.reusable and not page.destroyed and page.parked == 3
    assert page.titles == ["Message 0", "Message 1", "Message 2"]


def test_a_page_that_died_is_never_shown_again(monkeypatch):
    _SharedFake.made = []
    monkeypatch.setattr(dialogs, "FormattedMessageDialog", _SharedFake)
    monkeypatch.setattr(dialogs, "_SHARED_PAGE", None)
    monkeypatch.setattr(dialogs, "_reuse_pages", lambda: True)
    dialogs.show_formatted_page(None, "First", "<p>x</p>")
    _SharedFake.made[0].failed = True
    _result, _link, failed = dialogs.show_formatted_page(None, "Second", "<p>x</p>")
    assert failed and dialogs._SHARED_PAGE is None
    assert not _SharedFake.made[0].destroyed  # it goes with the main window


def test_elsewhere_or_with_another_factory_a_page_is_made_each_time(monkeypatch):
    _SharedFake.made = []
    monkeypatch.setattr(dialogs, "_SHARED_PAGE", None)
    monkeypatch.setattr(dialogs, "_reuse_pages", lambda: False)
    monkeypatch.setattr(dialogs, "FormattedMessageDialog", _SharedFake)
    dialogs.show_formatted_page(None, "One", "<p>x</p>")
    dialogs.show_formatted_page(None, "Two", "<p>x</p>")
    assert len(_SharedFake.made) == 2 and all(p.destroyed for p in _SharedFake.made)
    # A test's own factory is never kept either.
    monkeypatch.setattr(dialogs, "_reuse_pages", lambda: True)
    monkeypatch.setattr(dialogs, "FormattedMessageDialog", object)
    dialogs.show_formatted_page(None, "Three", "<p>x</p>", factory=_SharedFake)
    assert len(_SharedFake.made) == 3 and _SharedFake.made[-1].destroyed


def test_reuse_is_windows_only(monkeypatch):
    monkeypatch.setattr(dialogs.sys, "platform", "win32")
    assert dialogs._reuse_pages()
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(dialogs.sys, "platform", platform)
        assert not dialogs._reuse_pages()


def test_the_parked_page_says_nothing_for_the_next_message(monkeypatch):
    # #693 review: the hidden page's heartbeat, or a late load, mustn't count
    # as the next message's.
    _fresh(monkeypatch)
    page = _Page()
    page._parked = True
    F._tick(page)
    page.tick_after(60)
    assert not page.failed
    F._on_error(page, _Error("about:blank"))
    assert not page.failed and not dialogs.formatted_view_broken()


def test_a_first_beat_doesnt_cut_a_big_pages_time_to_finish(monkeypatch):
    # #693 review: DOMContentLoaded beats, then a long chat keeps the page
    # busy (layout, the screen reader's view of it) past the 8 seconds.
    _fresh(monkeypatch)
    page = _Page()
    page.tick_after(1)
    F._on_navigated(page, None)
    F._heard_from_page(page)  # DOMContentLoaded, not loaded yet
    page.tick_after(20)
    assert not page.failed
    page._loaded = True
    F._heard_from_page(page)  # loaded: silence counts from here
    page.tick_after(dialogs.FORMATTED_SILENCE_MS / 1000 + 1)
    assert page.failed


def test_a_long_gap_between_ticks_is_sleep_not_a_dead_page(monkeypatch):
    # #693 review: the lid closed with a page open, and opened again.
    _fresh(monkeypatch)
    page = _Page(loaded=True)
    F._heard_from_page(page)
    page.tick_after(3)
    page.now += 3600  # one tick, an hour later
    F._tick(page)
    assert not page.failed
    page.tick_after(dialogs.FORMATTED_SILENCE_MS / 1000 + 1)  # and then truly silent
    assert page.failed


def test_watching_is_windows_only(monkeypatch):
    monkeypatch.setattr(dialogs.sys, "platform", "win32")
    assert dialogs._watch_pages()
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(dialogs.sys, "platform", platform)
        assert not dialogs._watch_pages()


def test_elsewhere_errors_are_left_alone(monkeypatch):
    # Mac and Linux are as before #693: WebKit's errors don't give up.
    monkeypatch.setattr(dialogs, "_FORMATTED_BROKEN", False)
    monkeypatch.setattr(dialogs, "_watch_pages", lambda: False)
    page = _Page()
    F._on_error(page, _Error("about:blank"))
    assert not page.failed and not dialogs.formatted_view_broken()


def _kept(monkeypatch):
    _SharedFake.made = []
    monkeypatch.setattr(dialogs, "FormattedMessageDialog", _SharedFake)
    monkeypatch.setattr(dialogs, "_SHARED_PAGE", None)
    monkeypatch.setattr(dialogs, "_PAGE_BUSY", False)
    monkeypatch.setattr(dialogs, "_reuse_pages", lambda: True)


def test_a_page_asked_for_while_one_is_opening_gets_plain_text(monkeypatch):
    # #693 review: a queued call (the full chat finishing) run while waiting
    # keys are let through must not load the kept page over this one, or the
    # outer one shows it parked: blank, and Escape does nothing.
    _kept(monkeypatch)
    nested = []

    def yield_runs_a_queued_call():
        try:
            dialogs.show_formatted_page(None, "Full chat", "<p>y</p>")
        except RuntimeError as exc:
            nested.append(str(exc))

    monkeypatch.setattr(dialogs, "_let_waiting_keys_through", yield_runs_a_queued_call)
    result, _link, failed = dialogs.show_formatted_page(None, "Message", "<p>x</p>")
    assert nested and "already open" in nested[0]
    page = _SharedFake.made[0]
    assert len(_SharedFake.made) == 1 and page.titles == ["Message"] and page.parked == 1
    assert result == wx.ID_CANCEL and not failed and not dialogs._PAGE_BUSY


def test_a_page_asked_for_while_one_is_open_gets_plain_text(monkeypatch):
    # Never a second web view: it would be destroyed after, and that's the crash.
    _kept(monkeypatch)
    dialogs.show_formatted_page(None, "First", "<p>x</p>")
    monkeypatch.setattr(_SharedFake, "IsModal", lambda self: True)
    try:
        dialogs.show_formatted_page(None, "Second", "<p>x</p>")
    except RuntimeError as exc:
        assert "already open" in str(exc)
    else:
        raise AssertionError("a second page was made")
    assert len(_SharedFake.made) == 1


def test_a_failed_reload_leaves_nothing_busy(monkeypatch):
    _kept(monkeypatch)
    dialogs.show_formatted_page(None, "First", "<p>x</p>")

    def refuse(self, *args, **kwargs):
        raise RuntimeError("Couldn't show the formatted page")

    monkeypatch.setattr(_SharedFake, "reload", refuse)
    try:
        dialogs.show_formatted_page(None, "Second", "<p>x</p>")
    except RuntimeError:
        pass
    assert not dialogs._PAGE_BUSY
