"""Notifications (#20): the Notifier, with a stand-in tray icon (Windows) and a
stand-in macOS notification."""
import pytest

pytest.importorskip("wx")
import wx  # noqa: E402
import wx.adv  # noqa: E402

from thechatplace.ui import notify  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return wx.GetApp() or wx.App(False)


class FakeIcon:
    made = []

    def __init__(self):
        self.handlers, self.balloons, self.removed = {}, [], False
        FakeIcon.made.append(self)

    def SetIcon(self, icon, tooltip):
        self.tooltip = tooltip

    def Bind(self, binder, handler):
        self.handlers[binder.typeId] = handler

    def ShowBalloon(self, title, text, msec, flags):
        self.balloons.append((title, text))
        return True

    def RemoveIcon(self):
        self.removed = True

    def Destroy(self):
        pass

    def fire(self, binder):
        self.handlers[binder.typeId](None)


def test_one_icon_the_latest_session_and_removed_on_close(app):
    FakeIcon.made = []
    clicked = []
    notifier = notify.Notifier(clicked.append, "The Chat Place", factory=FakeIcon, mac=False)
    assert notifier.show("Hub probe finished", "All done.", "own:1")
    assert notifier.show("Quiet one needs you", "x" * 400, "local_2")
    assert len(FakeIcon.made) == 1
    icon = FakeIcon.made[0]
    assert icon.tooltip == "The Chat Place"
    assert len(icon.balloons[1][1]) == notify.TEXT_LIMIT
    icon.fire(wx.adv.EVT_TASKBAR_BALLOON_CLICK)
    icon.fire(wx.adv.EVT_TASKBAR_LEFT_UP)
    assert clicked == ["local_2", None]
    notifier.close()
    assert icon.removed
    notifier.close()  # twice is fine


def test_a_failing_notification_is_not_an_error(app):
    def broken():
        raise RuntimeError("no notification area")
    assert notify.Notifier(lambda key: None, "x", factory=broken, mac=False).show("t", "m", "k") \
        is False


class FakeMacNote:
    made = []

    def __init__(self, title, message):
        self.title, self.message, self.handlers, self.closed = title, message, {}, False
        FakeMacNote.made.append(self)

    def Bind(self, binder, handler):
        self.handlers[binder.typeId] = handler

    def Show(self, timeout):
        return True

    def Close(self):
        self.closed = True

    def click(self):
        self.handlers[wx.adv.wxEVT_NOTIFICATION_MESSAGE_CLICK](None)


def test_on_a_mac_each_notification_opens_its_own_session(app):
    FakeMacNote.made = []
    clicked = []
    notifier = notify.Notifier(clicked.append, "The Chat Place", message_factory=FakeMacNote,
                               mac=True)
    assert notifier.show("Hub probe finished", "All done.", "own:1")
    assert notifier.show("Quiet one needs you", "x" * 400, "local_2")
    first, second = FakeMacNote.made
    assert len(second.message) == notify.TEXT_LIMIT
    first.click()   # the older one still knows its session
    second.click()
    assert clicked == ["own:1", "local_2"]
    notifier.close()  # withdrawn from Notification Center as the app closes
    assert first.closed and second.closed


def test_on_a_mac_only_the_latest_notifications_are_kept(app):
    FakeMacNote.made = []
    notifier = notify.Notifier(lambda key: None, "x", message_factory=FakeMacNote, mac=True)
    for n in range(notify.MAC_KEPT + 5):
        notifier.show("t", "m", f"k{n}")
    notifier.close()
    assert sum(note.closed for note in FakeMacNote.made) == notify.MAC_KEPT


def test_a_failing_mac_notification_is_not_an_error(app):
    def broken(title, message):
        raise RuntimeError("not allowed")
    assert notify.Notifier(lambda key: None, "x", message_factory=broken, mac=True) \
        .show("t", "m", "k") is False


def test_the_apps_own_icon_is_shipped_and_used(app):
    from thechatplace import platform_paths
    path = platform_paths.app_icon_path()
    assert path.is_file()
    bundle = wx.IconBundle(str(path))
    assert bundle.GetIconCount() >= 5
    assert notify.app_icon(16).GetWidth() == 16
