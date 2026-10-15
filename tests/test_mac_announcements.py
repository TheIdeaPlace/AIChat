from types import SimpleNamespace

from thechatplace.ui import mac_a11y


def test_queued_voiceover_announcement_targets_application_with_low_priority(monkeypatch):
    calls, posts = [], []
    def send(obj, selector, *args, **kwargs):
        calls.append((obj, selector, args))
        return {'sharedWorkspace': 1, 'isVoiceOverEnabled': True,
                'dictionary': 2, 'numberWithInteger:': 3, 'sharedApplication': 4}.get(selector)
    def post(*args):
        posts.append(args)
    monkeypatch.setattr(mac_a11y, 'IS_MACOS', True)
    monkeypatch.setattr(mac_a11y, '_runtime', lambda: SimpleNamespace(objc_getClass=lambda name: name))
    monkeypatch.setattr(mac_a11y, '_send', send)
    monkeypatch.setattr(mac_a11y, '_nsstring', lambda text: 5)
    monkeypatch.setattr(mac_a11y, '_appkit_constant', lambda appkit, name: name)
    monkeypatch.setattr(mac_a11y.ctypes.cdll, 'LoadLibrary', lambda path:
                        SimpleNamespace(NSAccessibilityPostNotificationWithUserInfo=post))
    assert mac_a11y.queue_announcement(999, 'Synthetic announcement')
    assert posts == [(4, 'NSAccessibilityAnnouncementRequestedNotification', 2)]
    assert (b'NSNumber', 'numberWithInteger:', (10,)) in calls


def test_no_native_announcement_without_voiceover(monkeypatch):
    monkeypatch.setattr(mac_a11y, 'IS_MACOS', True)
    monkeypatch.setattr(mac_a11y, '_runtime', lambda: SimpleNamespace(objc_getClass=lambda name: name))
    monkeypatch.setattr(mac_a11y, '_send', lambda *args, **kwargs: False)
    assert not mac_a11y.queue_announcement(999, 'Synthetic announcement')
