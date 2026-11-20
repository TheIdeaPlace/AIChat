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
