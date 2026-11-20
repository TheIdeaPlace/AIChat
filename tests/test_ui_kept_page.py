"""The kept formatted page's own modal loop (#693), on a real dialog.

wx's ShowModal ended at once each time the kept page was shown again, so
run_kept runs a loop of its own. These drive it for real, with the web view
left out (CI's machines can't draw WebView2 pages).
"""
import wx

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
        _after(500, lambda: page.EndModal(wx.ID_OK))  # never reached
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


def test_focus_goes_back_where_it_was(app):
    frame = _frame()
    frame.Show()
    box = frame.GetChildren()[0]
    box.SetFocus()
    wx.Yield()
    page = _Kept(frame)
    try:
        _after(50, lambda: page.EndModal(wx.ID_CANCEL))
        page.run_kept()
        wx.Yield()
        # Where the window manager lets a test window have focus at all.
        if wx.Window.FindFocus() is not None:
            assert wx.Window.FindFocus() is box
    finally:
        page.Destroy()
        frame.Destroy()
