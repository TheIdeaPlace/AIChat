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
