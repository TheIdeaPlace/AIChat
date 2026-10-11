"""Sizes that grow with the screen's scaling (#185) and never pass its edge
(#186)."""
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from thechatplace import app as app_module  # noqa: E402
from thechatplace.ui import sizes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    instance = wx.App(False)
    yield instance


@pytest.mark.skipif(sys.platform != "win32", reason="DPI awareness is a Windows setting")
def test_the_app_becomes_per_monitor_dpi_aware():
    # In a process of its own: awareness can be set once per process.
    code = ("import ctypes; from thechatplace import platform_paths; "
            "platform_paths.make_dpi_aware(); u = ctypes.windll.user32; "
            "u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p; "
            "u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]; "
            "print(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == "2", out.stderr  # 2: per-monitor aware


def test_dpi_awareness_comes_before_the_first_window():
    source = inspect.getsource(app_module.main)
    assert source.index("make_dpi_aware()") < source.index("ChatPlaceApp(False)")


def test_design_sizes_become_screen_pixels(app):
    frame = wx.Frame(None)
    try:
        scale = frame.GetDPIScaleFactor()
        assert sizes.dip(frame, 680, 620) == wx.Size(round(680 * scale), round(620 * scale))
        assert sizes.dip(frame, -1, 120).width == -1  # "any width" stays so
        assert sizes.dip_width(frame, 150) == round(150 * scale)
    finally:
        frame.Destroy()


def test_a_dialog_is_never_bigger_than_its_screen(app, monkeypatch):
    # #186: at 175% on a 1080-pixel screen, tall dialogs ran under the taskbar.
    monkeypatch.setattr(sizes, "screen_area", lambda window: wx.Rect(0, 0, 1097, 593))
    monkeypatch.setattr(sizes, "dip", lambda window, w, h=-1: wx.Size(round(w * 1.75),
                                                                      round(h * 1.75)))
    size = sizes.dialog_size(None, 680, 620)
    assert size == wx.Size(1097 - sizes.SCREEN_MARGIN, 593 - sizes.SCREEN_MARGIN)
    small = sizes.dialog_size(None, 300, 200)
    assert small == wx.Size(525, 350)  # fits: left as designed


@pytest.mark.parametrize("module", ["dialogs.py", "main_frame.py", "statusbar.py", "notify.py"])
def test_no_window_has_a_raw_pixel_size(module):
    """A size written as plain pixels would come out small at 150% (#185):
    sizes go through ui.sizes. (A 1-pixel minimum, which only lets a
    control shrink, or a -1 by -1 one that clears it, needs no scaling.)"""
    import re
    text = (ROOT / "thechatplace" / "ui" / module).read_text(encoding="utf-8")
    assert re.findall(r"size=\((?:-1|\d+), *\d+\)", text) == []
    raw = re.findall(r"SetMinSize\(\((?:-1|\d+|[A-Z_]+), *(?:\d+|-1)\)\)", text)
    assert [size for size in raw if size not in ("SetMinSize((1, -1))", "SetMinSize((-1, -1))")] == []


def test_status_bar_widths_are_scaled_at_every_layout(app, monkeypatch):
    from thechatplace.ui.statusbar import StatusParts
    frame = wx.Frame(None, size=(1000, 300))
    try:
        bar = frame.CreateStatusBar(1)
        parts = StatusParts(bar)
        parts.add_text("message", "Status", -3)
        button = parts.add_button("update", 150, lambda: None)
        parts.set("update", "Update 0.2.0 ready")
        widths = []
        real = bar.SetFieldsCount
        monkeypatch.setattr(bar, "SetFieldsCount",
                            lambda n, w: widths.append(list(w)) or real(n, w))
        monkeypatch.setattr(bar, "FromDIP", lambda width: width * 2)  # as at 200%
        parts.layout()
        assert widths[-1] == [-3, 300]  # the button's 150 scaled; the share isn't
        assert button.IsShown()
    finally:
        frame.Destroy()


def test_a_move_to_another_screen_keeps_the_status_bar_buttons_text(app):
    """#185: refitting the bar after a scaling change measured on the live
    Context button and left it blank, a nameless button to a screen reader."""
    from thechatplace.ui.statusbar import StatusParts
    frame = wx.Frame(None, size=(1000, 300))
    try:
        bar = frame.CreateStatusBar(1)
        parts = StatusParts(bar)
        parts.add_text("message", "Status", -3)
        context = parts.add_button("context", 150, lambda: None)
        parts.set("context", "Context 42% full")
        parts._on_dpi_changed(wx.DPIChangedEvent())
        assert context.GetLabel() == "Context 42% full"
        assert context.IsShown()
        assert bar.GetStatusText(1) == "Context 42% full"
    finally:
        frame.Destroy()


def test_usage_is_as_wide_as_its_longest_line_at_any_scaling(app, monkeypatch):
    from thechatplace.ui import dialogs
    frame = wx.Frame(None)
    try:
        monkeypatch.setattr(dialogs, "dip_width", lambda window, width: width * 5 // 4)  # as at 125%
        monkeypatch.setattr(dialogs, "dialog_size", lambda parent, w, h: wx.Size(w * 5 // 4, h * 5 // 4))
        monkeypatch.setattr(dialogs, "screen_area", lambda window: wx.Rect(0, 0, 3840, 2000))
        dialog = dialogs.UsageDialog(frame, ["Short line"], lambda text: None)
        try:
            # Its 640 design width, scaled: small enough that Windows doesn't
            # shrink it to fit CI's 1024-pixel screen.
            assert dialog.GetSize().width == 800
        finally:
            dialog.Destroy()
    finally:
        frame.Destroy()
