"""No Command+letter on a Mac's buttons (#675): wx gave every button with an
"&" in its label Command and that letter, which took Command+V from the API
key box ("Sa&ve Limits"). A Mac dialog has no letter shortcuts: Return presses
the default button, Escape cancels, and Command keys do their system jobs.

What only a Mac has, the native buttons' key equivalents and the hook the app
installs, is marked ``mac``; the rest runs everywhere."""
import ctypes
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from thechatplace import spend_ledger  # noqa: E402
from thechatplace.ui import mac_a11y, mac_keys  # noqa: E402

import fake_env  # noqa: E402
from markers import mac  # noqa: E402
from test_api_keys_dialog import _add, make  # noqa: E402

COMMAND = mac_a11y._COMMAND_KEY_MASK


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def parent(env):
    frame = wx.Frame(None, title="Mac keys tests")
    yield frame
    wx.GetApp().ProcessPendingEvents()
    frame.Destroy()
    wx.GetApp().ProcessPendingEvents()


@pytest.fixture
def api_keys(parent, env):
    window = make(parent, spend_ledger.SpendLedger(env["tmp"] / "appdata"))
    yield window
    if window and not window.IsBeingDeleted():
        window.Destroy()
    wx.GetApp().ProcessPendingEvents()


@pytest.fixture
def aside(parent):
    from thechatplace.ui.dialogs import AskAsideDialog
    window = AskAsideDialog(parent, "Mine", lambda question, on_event: None)
    yield window
    if window and not window.IsBeingDeleted():
        window.Destroy()
    wx.GetApp().ProcessPendingEvents()


# -- everywhere --------------------------------------------------------------------------


@pytest.mark.parametrize("key,mask,dropped", [
    ("v", COMMAND, True),  # "Sa&ve Limits", as wx gave it
    ("V", COMMAND | (1 << 17), True),  # with Shift too
    ("v", 0, False),  # no Command: not wx's access key
    ("\r", COMMAND, False),  # Return is never typed
    ("\r", 0, False),  # the default button's
    ("\x1b", 0, False),  # Escape, Cancel's
    ("", COMMAND, False),
    (" ", COMMAND, False),
    ("ab", COMMAND, False),
])
def test_only_a_command_letter_is_taken_away(key, mask, dropped):
    assert mac_a11y.is_command_letter(key, mask) is dropped


def test_install_does_nothing_off_a_mac(monkeypatch):
    monkeypatch.setattr(mac_keys, "IS_MAC", False)
    assert mac_keys.install(wx.GetApp()) is False


def test_the_sweep_leaves_buttons_alone_off_a_mac(parent, monkeypatch):
    monkeypatch.setattr(mac_a11y, "IS_MACOS", False)
    button = wx.Button(parent, label="Sen&d")
    mac_keys.sweep(parent)  # nothing to do, and nothing raised
    assert button.GetLabel() == "Sen&d"
    assert mac_a11y.drop_command_key(button) is False


# -- a Mac's own ------------------------------------------------------------------------


def _buttons(window):
    found = []
    for child in window.GetChildren():
        if isinstance(child, wx.AnyButton):
            found.append(child)
        found.extend(_buttons(child))
    return found


def _command_letters(window):
    """Each button's label and key equivalent where it's Command+letter."""
    found = []
    for button in _buttons(window):
        equivalent = mac_a11y.key_equivalent(button)
        assert equivalent is not None, button.GetLabel()  # read from the native button
        if mac_a11y.is_command_letter(*equivalent):
            found.append((button.GetLabel(), equivalent))
    return found


def _set_key_equivalent(view, key, mask):
    """As wx or AppKit gives a button its key: through the (hooked) setters."""
    mac_a11y._send(view, "setKeyEquivalent:", ctypes.c_void_p(mac_a11y._nsstring(key)),
                   restype=None, argtypes=(ctypes.c_void_p,))
    mac_a11y._send(view, "setKeyEquivalentModifierMask:", mask,
                   restype=None, argtypes=(ctypes.c_uint64,))


def _assert_key(button, key):
    got, mask = mac_a11y.key_equivalent(button)
    assert got == key, (button.GetLabel(), got)
    assert not mask & COMMAND, (button.GetLabel(), hex(mask))


@mac
def test_the_app_fixture_installs_it_as_the_app_does(app):
    assert mac_keys._installed_on is app
    assert mac_a11y._key_methods  # wxNSButton's setters are hooked


@mac
def test_no_button_in_api_keys_has_a_command_key(api_keys):
    assert _command_letters(api_keys) == []


@mac
def test_no_button_in_ask_aside_has_a_command_key(aside):
    assert _command_letters(aside) == []


@mac
def test_the_main_windows_buttons_have_no_command_keys(parent):
    # The hook is on wx's button class, so frames are covered as well as
    # dialogs: Send had Command+D, Stop Command+P, Refresh Command+R.
    for label in ("Sen&d", "Sto&p", "&Refresh", "Open &in Claude", "C&ommands...",
                  "Continue H&ere..."):
        wx.Button(parent, label=label)
    assert _command_letters(parent) == []


@mac
def test_a_label_change_doesnt_give_a_command_key_back(api_keys):
    # wx sets the key equivalent again on every label change.
    assert _add(api_keys)
    assert api_keys.add_btn.GetLabel() == "&Replace Key"
    api_keys.save_limits_btn.SetLabel("Sa&ve Them")  # a new label, so wx sets it again
    api_keys.save_limits_btn.SetLabel("Sa&ve Limits")
    assert _command_letters(api_keys) == []


@mac
def test_the_default_and_cancel_buttons_keep_return_and_escape(parent):
    dialog = wx.Dialog(parent, title="Default")
    try:
        ok = wx.Button(dialog, wx.ID_OK, "&Go")
        ok.SetDefault()
        cancel = wx.Button(dialog, wx.ID_CANCEL, "&Cancel")
        # Escape on Cancel, given as AppKit or wx gives it: the hook keeps it.
        _set_key_equivalent(ctypes.c_void_p(cancel.GetHandle()), "\x1b", 0)
        _assert_key(ok, "\r")
        _assert_key(cancel, "\x1b")
        assert _command_letters(dialog) == []

        # A relabel then SetDefault: wx gives the new letter with Command,
        # the hook takes it (and the Command bit) away, then Return goes on
        # with no Command, not as Command+Return.
        ok.SetLabel("&Run")
        ok.SetDefault()
        _assert_key(ok, "\r")
        other = wx.Button(dialog, label="&Hold")
        other.SetLabel("&Keep")
        other.SetDefault()
        _assert_key(other, "\r")
        _assert_key(cancel, "\x1b")
        assert _command_letters(dialog) == []
    finally:
        dialog.Destroy()


@mac
def test_appkits_own_buttons_keep_their_command_keys(app):
    # An alert's Don't Save has Command+D from AppKit; only wx's class is hooked,
    # and the sweep leaves any other class alone.
    objc = mac_a11y._runtime()
    button = mac_a11y._send(mac_a11y._send(objc.objc_getClass(b"NSButton"), "alloc"), "init")
    assert button
    view = ctypes.c_void_p(button)
    try:
        _set_key_equivalent(view, "d", COMMAND)
        assert mac_a11y._key_equivalent_of(view) == ("d", COMMAND)
        assert mac_a11y.drop_command_key(SimpleNamespace(GetHandle=lambda: button)) is False
        assert mac_a11y._key_equivalent_of(view) == ("d", COMMAND)
    finally:
        mac_a11y._send(view, "release", restype=None)
