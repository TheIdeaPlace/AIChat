"""What VoiceOver lands on, on a Mac, asked of macOS itself (#726).

wx's FindFocus says where the keyboard is; it said the status bar on Go to
Status Bar while VoiceOver, finding no accessibility element there, stayed
where it was. So these tests move the focus as a key would and ask the app
for its focused accessibility element, the one VoiceOver reads: it must be
something other than the window, with a role and words to read. That proves
VoiceOver has something to read, not that it reads well: how it sounds is
still Kelly's to hear.

Every surface the visual probe opens is walked (``tools/ui_probe.py``), so a
new dialog is covered as soon as it has a surface, as it must (#155). The
windows are shown and the app made active: VoiceOver's focus follows only an
active app's key window.

Also here: a real formatted page in WebKit, copied from (#725). The other UI
tests replace the web view, so none of them met the page's own rules.
"""
import re
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fake_env  # noqa: E402
import ui_probe  # noqa: E402
from markers import voiceover  # noqa: E402

pytestmark = voiceover

#: surface -> [(pattern matching one problem, issue)], as in test_layout's
#: KNOWN: a new problem fails, and so does a known one that has gone.
KNOWN = {}

#: Roles VoiceOver has nothing to say for: focus that lands on one is lost.
_NOWHERE = {None, "", "AXWindow", "AXUnknown", "AXApplication"}

#: Containers wx lets take the focus, which hand it on to a child, and a
#: panel's scroll bars, which no key reaches on a Mac.
_CONTAINERS = (wx.Panel, wx.StatusBar, wx.ScrolledWindow, wx.Notebook, wx.Simplebook,
               wx.ScrollBar)


def _activate(window):
    from thechatplace.ui import mac_a11y
    window.Show()
    window.Raise()
    mac_a11y.activate_app()
    fake_env.pump(lambda: False, timeout=0.3)


def _focusable(window):
    """The controls under ``window`` a key can reach, in tree order."""
    for child in window.GetChildren():
        if isinstance(child, wx.TopLevelWindow):
            continue
        if (child.IsShownOnScreen() and child.IsEnabled() and child.AcceptsFocus()
                and not isinstance(child, _CONTAINERS)):
            yield child
        yield from _focusable(child)


def _describe(control) -> str:
    label = control.GetLabel() if not isinstance(control, wx.TextCtrl) else ""
    return f"{type(control).__name__} {(label or control.GetName())!r}"


def _landed(before_id, timeout=1.0):
    """VoiceOver's element once the focus has moved off ``before_id``."""
    from thechatplace.ui import mac_a11y
    found = {}

    def moved():
        found["element"] = mac_a11y.focused_element()
        return found["element"] and found["element"]["id"] != before_id
    fake_env.pump(moved, timeout=timeout)
    return found["element"]


def _problem(element):
    if element is None:
        return "no accessibility focus at all"
    if element["role"] in _NOWHERE:
        return f"VoiceOver lands on {element['role'] or 'nothing'} ({element['class']})"
    if not (element["name"] or "").strip():
        return f"VoiceOver lands on an unnamed {element['role']} ({element['class']})"
    return None


def voiceover_problems(window) -> list:
    """Each focusable control under ``window`` focused in turn; what's wrong
    with what VoiceOver lands on."""
    from thechatplace.ui import mac_a11y
    problems = []
    for control in list(_focusable(window)):
        if not control:
            continue
        before = mac_a11y.focused_element()
        before_wx = wx.Window.FindFocus()
        control.SetFocus()
        element = _landed(before and before["id"])
        after_wx = wx.Window.FindFocus()
        if after_wx is None or after_wx == before_wx:
            continue  # the control turned the focus down: no key reaches it either
        if element and before and element["id"] == before["id"]:
            problems.append(f"{_describe(control)}: VoiceOver stayed where it was "
                            f"({element['role']} {element['name']!r})")
            continue
        problem = _problem(element)
        if problem:
            problems.append(f"{_describe(control)}: {problem}")
    return problems


def _check(name, found):
    known = KNOWN.get(name, [])
    new = [p for p in found if not any(re.search(pattern, p) for pattern, _ in known)]
    gone = [f"{pattern} (#{issue})" for pattern, issue in known
            if not any(re.search(pattern, p) for p in found)]
    assert new == [], f"{name}: VoiceOver can't follow the focus"
    assert gone == [], f"{name}: fixed? remove these from KNOWN and close their issues"


@pytest.fixture
def active(app):
    """Fails, rather than skips, if the app can't be made active: then
    nothing here would be tested, and CI would say it was."""
    import ctypes
    from thechatplace.ui import mac_a11y
    frame = wx.Frame(None)
    _activate(frame)
    nsapp = ctypes.c_void_p(mac_a11y._send(
        mac_a11y._runtime().objc_getClass(b"NSApplication"), "sharedApplication"))
    is_active = fake_env.pump(
        lambda: mac_a11y._send(nsapp, "isActive", restype=ctypes.c_bool), timeout=5)
    frame.Destroy()
    assert is_active, "the test app couldn't be made the active app"
    return app


@pytest.mark.parametrize("name", list(ui_probe.SURFACES))
def test_voiceover_follows_the_focus_on_each_surface(name, active, tmp_path, monkeypatch):
    kind, function, _about = ui_probe.SURFACES[name]
    found = []

    def walk(dialog):
        _activate(dialog)
        fake_env.pump(lambda: False, timeout=0.3)  # its own background loading
        found.append(voiceover_problems(dialog))
        dialog.Hide()
        return wx.ID_CANCEL

    def native_message_box(_dialog):
        pytest.fail("Native wx.MessageDialog must not be a probe surface")
    monkeypatch.setattr(wx.MessageDialog, "ShowModal", native_message_box)
    for cls in (wx.Dialog, wx.TextEntryDialog, wx.SingleChoiceDialog):
        monkeypatch.setattr(cls, "ShowModal", walk)
    empty = name in ui_probe.EMPTY_WORLD
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, empty=empty, formatted_view=False)
    frame = ui_probe.build_frame(env, empty=empty)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        _activate(frame)
        if kind == "window":
            function(frame, env)
            frame.Layout()
            fake_env.pump(lambda: False, timeout=0.2)
            found.append(voiceover_problems(frame))
        else:
            function(frame, env)()
            assert fake_env.pump(lambda: found, timeout=10), f"{name} opened no dialog"
    finally:
        ui_probe.close_frame(frame)
    _check(name, found[0])


def test_voiceover_follows_every_go_to_command_and_f6(active, tmp_path, monkeypatch):
    # Command+1, 2, 3 and 9, and each F6 stop, as the keys run them.
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    from thechatplace.ui import mac_a11y
    try:
        _activate(frame)
        frame.focus_sessions()
        frame.on_open_session()
        assert fake_env.pump(lambda: frame._chat_loaded, timeout=5)
        frame.focus_sessions()  # opening it went to the messages
        fake_env.pump(lambda: False, timeout=0.3)
        problems = []
        moves = [("Go to Messages", frame.focus_messages), ("Go to Reply", frame.focus_reply),
                 ("Go to Status Bar", frame.focus_status), ("Go to Sessions", frame.focus_sessions)]
        moves += [(f"F6 stop {n + 1}", frame.cycle_focus) for n in range(4)]
        for what, move in moves:
            before = mac_a11y.focused_element()
            move()
            element = _landed(before and before["id"])
            if element and before and element["id"] == before["id"] \
                    and not what.startswith("F6"):
                problems.append(f"{what}: VoiceOver stayed on {element['role']} {element['name']!r}")
                continue
            problem = _problem(element)
            if problem:
                problems.append(f"{what}: {problem}")
        assert problems == []
    finally:
        ui_probe.close_frame(frame)


def test_a_formatted_page_copies_what_you_select(active):
    # Command+C in the page runs wx's Copy, a script it runs through eval,
    # which the page's rules refused: nothing copied, an error shown (#725).
    from thechatplace.rendering import message_page
    from thechatplace.ui.dialogs import FormattedMessageDialog
    errors = []

    class Recorder(wx.Log):
        def DoLogTextAtLevel(self, level, message):
            if level <= wx.LOG_Warning:
                errors.append(message)
    old = wx.Log.SetActiveTarget(Recorder())
    owner = wx.Frame(None)
    dialog = FormattedMessageDialog(owner, "Claude", message_page(
        "Claude", "## Result\n\nThe build **passes**.", heading="Claude"))
    try:
        _activate(dialog)
        assert fake_env.pump(lambda: dialog._loaded, timeout=10), "the page never loaded"
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject("before"))
            wx.TheClipboard.Close()
        dialog.view.SelectAll()
        dialog.view.Copy()

        def copied():
            if not wx.TheClipboard.Open():
                return False
            data = wx.TextDataObject()
            got = wx.TheClipboard.GetData(data)
            wx.TheClipboard.Close()
            return got and "The build passes." in data.GetText()
        assert fake_env.pump(copied, timeout=5), "nothing was copied"
        assert errors == []
    finally:
        wx.Log.SetActiveTarget(old)
        dialog.Destroy()
        owner.Destroy()
