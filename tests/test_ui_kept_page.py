"""The kept formatted page's own modal loop (#693), on a real dialog.

wx's ShowModal ended at once each time the kept page was shown again, so
run_kept runs a loop of its own. These drive it for real, with the web view
left out (CI's machines can't draw WebView2 pages).
"""
import wx

from tests.markers import windows_layout
from thechatplace.ui import dialogs


class _Kept(dialogs.FormattedMessageDialog):
    """A real FormattedMessageDialog's loop and endings, without a web view."""

    def __init__(self, parent):
        wx.Dialog.__init__(self, parent, title="Message")
        self._own_loop = None
        self._own_result = wx.ID_CANCEL
        self._parked = False
        self._watchdog = None
        self._page_file = None
        wx.Button(self, wx.ID_CANCEL, "&Close")
        self.Bind(wx.EVT_CLOSE, self._on_close_kept)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_kept)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy_kept)


class _Key:
    def __init__(self, code):
        self.code, self.skipped = code, False

    def GetKeyCode(self):
        return self.code

    def Skip(self):
        self.skipped = True


def _frame():
    frame = wx.Frame(None, title="Main")
    wx.TextCtrl(frame)
    return frame


def _after(ms, call):
    # A safety net too: a loop that never ends would hang the run.
    wx.CallLater(ms, call)


def test_it_shows_again_and_again_and_returns_each_result(app):
    frame = _frame()
    page = _Kept(frame)
    try:
        for code in (wx.ID_CANCEL, dialogs.ID_PLAIN_TEXT, wx.ID_CANCEL):
            seen = {}

            def during(code=code, seen=seen):
                seen["shown"] = page.IsShown()
                seen["modal"] = page.IsModal()
                seen["main_enabled"] = frame.IsEnabled()
                page.EndModal(code)

            _after(50, during)
            assert page.run_kept() == code
            # Shown and modal while open, the main window disabled behind it.
            assert seen == {"shown": True, "modal": True, "main_enabled": False}
            assert not page.IsShown() and not page.IsModal() and frame.IsEnabled()
    finally:
        page.Destroy()
        frame.Destroy()


def test_escape_closes_it_and_other_keys_go_on(app):
    frame = _frame()
    page = _Kept(frame)
    try:
        other = _Key(ord("A"))

        def press():
            page._on_key_kept(other)
            page._on_key_kept(_Key(wx.WXK_ESCAPE))

        _after(50, press)
        assert page.run_kept() == wx.ID_CANCEL
        assert other.skipped
    finally:
        page.Destroy()
        frame.Destroy()


def test_alt_f4_hides_it_and_never_destroys_it(app):
    frame = _frame()
    page = _Kept(frame)
    try:
        _after(50, lambda: page.Close())
        assert page.run_kept() == wx.ID_CANCEL
        assert page and not page.IsBeingDeleted() and not page.IsShown()
    finally:
        page.Destroy()
        frame.Destroy()


def test_a_parked_page_is_never_shown(app):
    # Nothing loaded into it: it would be a blank window.
    frame = _frame()
    page = _Kept(frame)
    try:
        page._parked = True
        assert page.run_kept() == wx.ID_CANCEL
        assert not page.IsShown() and frame.IsEnabled()
    finally:
        page.Destroy()
        frame.Destroy()


def test_end_modal_without_a_loop_does_nothing(app):
    frame = _frame()
    page = _Kept(frame)
    try:
        page.EndModal(wx.ID_CANCEL)
        page.EndModal(wx.ID_CANCEL)
        assert not page.IsModal()
    finally:
        page.Destroy()
        frame.Destroy()


def test_the_loop_ends_if_the_page_is_destroyed_while_open(app):
    frame = _frame()
    page = _Kept(frame)
    _after(50, lambda: page.Destroy())
    assert page.run_kept() == wx.ID_CANCEL
    frame.Destroy()


@windows_layout
def test_focus_goes_back_where_it_was(app):
    frame = _frame()
    frame.Show()
    box = frame.GetChildren()[0]
    page = _Kept(frame)
    try:
        _after(50, lambda: page.EndModal(wx.ID_CANCEL))
        page.run_kept(came_from=box)
        wx.Yield()
        assert wx.Window.FindFocus() is box
    finally:
        page.Destroy()
        frame.Destroy()


def test_never_back_into_the_hidden_page(app):
    # #693 review: a page that loaded while hidden may hold focus itself;
    # going back there would send every key into an invisible window.
    frame = _frame()
    page = _Kept(frame)
    inside = wx.Button(page, label="In the page")
    went = []
    monkey = dialogs._return_to
    dialogs._return_to = went.append
    try:
        _after(50, lambda: page.EndModal(wx.ID_CANCEL))
        page.run_kept(came_from=inside)
        assert went == [None]
    finally:
        dialogs._return_to = monkey
        page.Destroy()
        frame.Destroy()


class _View(wx.Panel):
    """Just enough of a web view to build a real FormattedMessageDialog."""

    def __init__(self, parent):
        super().__init__(parent)
        self.scripts, self.focused = [], 0

    def EnableAccessToDevTools(self, _on):
        pass

    def AddScriptMessageHandler(self, _name):
        pass

    def AddUserScript(self, script):
        self.scripts.append(script)

    def SetPage(self, _html, _base):
        pass

    def LoadURL(self, _url):
        pass

    def GetCurrentTitle(self):
        return "Message"

    def RunScript(self, _script):
        return True, ""

    def SetFocus(self):
        self.focused += 1


class _Event:
    def __init__(self, text=""):
        self.text = text

    def GetString(self):
        return self.text

    def GetURL(self):
        return "about:blank"


def _real_page(monkeypatch, frame, watch):
    import wx.html2
    monkeypatch.setattr(wx.html2.WebView, "New", staticmethod(lambda parent, backend=None: _View(parent)))
    monkeypatch.setattr(dialogs, "_watch_pages", lambda: watch)
    return dialogs.FormattedMessageDialog(frame, "Message", "<p>x</p>")


def test_mac_and_linux_pages_still_answer_escape_and_take_focus(app, monkeypatch):
    # #693 review: with nothing watching, the page's own events still work.
    frame = _frame()
    page = _real_page(monkeypatch, frame, watch=False)
    try:
        assert page._watchdog is None
        assert dialogs._HEARTBEAT not in page.view.scripts
        page.Show()
        page._on_loaded(_Event())
        assert page._loaded and page.view.focused
        ended = []
        monkeypatch.setattr(page, "EndModal", ended.append)
        page._on_script_message(_Event("escape"))
        page._on_navigated(_Event())
        wx.Yield()
        assert ended == [wx.ID_CANCEL]
    finally:
        page.Destroy()
        frame.Destroy()


def test_a_page_that_loads_while_hidden_is_focused_only_on_showing(app, monkeypatch):
    frame = _frame()
    page = _real_page(monkeypatch, frame, watch=True)
    try:
        page._on_loaded(_Event())
        assert page._loaded and page._focus_on_show and not page.view.focused
    finally:
        page.Destroy()
        frame.Destroy()
