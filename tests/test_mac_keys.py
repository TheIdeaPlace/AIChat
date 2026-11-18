"""A dialog's access keys on a Mac (#675): Option+letter does what Alt+letter
does on Windows, and no button keeps a Command+letter key equivalent, which
took Command+V from the API key box ("Sa&ve Limits").

The handler (ui/mac_keys.py) is plain wx, so what it does with a key is
tested on every platform, on dialogs that are never shown; the app installs
it on a Mac only. What only a Mac has, the native buttons' key equivalents
and the handler bound to the app, is marked ``mac``."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace import access_keys, spend_ledger  # noqa: E402
from thechatplace.ui import mac_a11y, mac_keys  # noqa: E402

import fake_env  # noqa: E402
from markers import mac  # noqa: E402
from test_api_keys_dialog import _add, make  # noqa: E402


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


def _key(letter, source, option=True, command=False, shift=False):
    """A char hook for a letter, from ``source``, as a key press sends it."""
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(ord(letter.upper()))
    event.SetAltDown(option)
    event.SetControlDown(command)
    event.SetShiftDown(shift)
    event.SetEventObject(source)
    return event


def _press(letter, source, **modifiers):
    """Hand the key to the handler. True if it took it (didn't skip it)."""
    event = _key(letter, source, **modifiers)
    mac_keys.on_char_hook(event)
    return not event.GetSkipped()


def _record(monkeypatch, target, name):
    calls = []
    monkeypatch.setattr(target, name, lambda *args: calls.append(args))
    return calls


# -- what Option+letter does, on every platform -----------------------------------------


def test_option_v_presses_save_limits_from_the_key_box(api_keys, monkeypatch):
    saved = _record(monkeypatch, api_keys, "on_save_limits")
    assert _press("v", api_keys.key_box)
    assert saved == [()]


def test_option_a_presses_ask_in_ask_aside(aside, monkeypatch):
    asked = _record(monkeypatch, aside, "on_ask")
    assert _press("a", aside.question)
    assert asked == [()]


def test_option_on_a_labels_letter_moves_to_the_box_it_names(api_keys, monkeypatch):
    moved = _record(monkeypatch, api_keys.key_box, "SetFocus")
    assert _press("k", api_keys.test_btn)  # "Claude API &key:"
    assert moved == [()]
    to_limit = _record(monkeypatch, api_keys.per_message, "SetFocus")
    assert _press("o", api_keys.key_box)  # "Stop any &one message at about $"
    assert to_limit == [()]


def test_a_renamed_label_keeps_its_letter(api_keys, monkeypatch):
    api_keys.key_label.SetLabel("OpenAI API &key:")  # as Provider does
    moved = _record(monkeypatch, api_keys.key_box, "SetFocus")
    assert _press("k", api_keys.key_box)
    assert moved == [()]


def test_command_shift_or_no_option_leaves_the_key_alone(api_keys, monkeypatch):
    saved = _record(monkeypatch, api_keys, "on_save_limits")
    assert not _press("v", api_keys.key_box, option=False, command=True)  # Command+V pastes
    assert not _press("v", api_keys.key_box, command=True)  # Command+Option+V
    assert not _press("v", api_keys.key_box, shift=True)
    assert not _press("v", api_keys.key_box, option=False)
    assert saved == []


def test_a_letter_no_control_has_goes_on_to_be_typed(api_keys):
    assert not _press("z", api_keys.key_box)


def test_a_disabled_button_isnt_pressed(api_keys, monkeypatch):
    # With no key kept, Test Key is off.
    assert not api_keys.test_btn.IsEnabled()
    tested = _record(monkeypatch, api_keys, "on_test")
    assert not _press("t", api_keys.key_box)
    assert tested == []


def test_a_button_whose_label_changes_answers_to_its_new_letter(api_keys, monkeypatch):
    assert access_keys.access_key(api_keys.add_btn.GetLabel()) == "a"
    assert _add(api_keys)
    assert api_keys.add_btn.GetLabel() == "&Replace Key"
    added = _record(monkeypatch, api_keys, "on_add")
    assert _press("r", api_keys.key_box)
    assert added == [()]


def test_a_check_box_is_checked_and_a_radio_button_chosen(parent, monkeypatch):
    dialog = wx.Dialog(parent, title="Kinds")
    try:
        box = wx.CheckBox(dialog, label="&Cats")
        first = wx.RadioButton(dialog, label="&First", style=wx.RB_GROUP)
        second = wx.RadioButton(dialog, label="&Second")
        first.SetValue(True)
        checked, chosen = [], []
        box.Bind(wx.EVT_CHECKBOX, lambda e: checked.append(e.IsChecked()))
        second.Bind(wx.EVT_RADIOBUTTON, lambda e: chosen.append(e.GetEventObject()))
        _record(monkeypatch, box, "SetFocus")
        _record(monkeypatch, second, "SetFocus")
        assert _press("c", first)
        assert box.GetValue() is True and checked == [True]
        assert _press("s", first)
        assert second.GetValue() and chosen == [second]
    finally:
        dialog.Destroy()


def test_a_hidden_control_has_no_access_key(parent, monkeypatch):
    dialog = wx.Dialog(parent, title="Hidden")
    try:
        hidden = wx.Button(dialog, label="&Go")
        hidden.Hide()
        shown = wx.Button(dialog, label="&Go on")
        pressed = []
        hidden.Bind(wx.EVT_BUTTON, lambda e: pressed.append("hidden"))
        shown.Bind(wx.EVT_BUTTON, lambda e: pressed.append("shown"))
        assert _press("g", shown)
        assert pressed == ["shown"]
    finally:
        dialog.Destroy()


def test_the_main_window_and_other_frames_are_left_alone(parent):
    # In the reply box, Option and a letter types an accented letter.
    button = wx.Button(parent, label="&Go")
    pressed = []
    button.Bind(wx.EVT_BUTTON, lambda e: pressed.append(True))
    box = wx.TextCtrl(parent)
    assert not _press("g", box)
    assert pressed == []


@pytest.mark.parametrize("which", ["api_keys", "aside"])
def test_no_two_controls_in_these_dialogs_share_an_access_key(which, request):
    dialog = request.getfixturevalue(which)
    stops = [stop for _control, stop in mac_keys.controls(dialog)]
    assert access_keys.duplicates(stops) == set()
    assert any(stop.key for stop in stops)


def test_the_app_handler_gets_the_key_after_the_dialogs_own(api_keys, monkeypatch):
    """As wx delivers it: the focused box, then the dialog's char hook (which
    skips it), then the app's. Bound here for the test where the app hasn't
    bound it itself (anywhere but a Mac)."""
    app = wx.GetApp()
    bound = mac_keys._installed_on is not app
    if bound:
        app.Bind(wx.EVT_CHAR_HOOK, mac_keys.on_char_hook)
    try:
        saved = _record(monkeypatch, api_keys, "on_save_limits")
        event = _key("v", api_keys.key_box)
        api_keys.key_box.GetEventHandler().ProcessEvent(event)
        assert saved == [()]
    finally:
        if bound:
            app.Unbind(wx.EVT_CHAR_HOOK, handler=mac_keys.on_char_hook)


def test_install_does_nothing_off_a_mac(monkeypatch):
    monkeypatch.setattr(mac_keys, "IS_MAC", False)
    assert mac_keys.install(wx.GetApp()) is False


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
        assert _command_letters(dialog) == []
        # What wx gave them that isn't a Command letter is still there.
        for button in (ok, cancel):
            key, _mask = mac_a11y.key_equivalent(button)
            assert key in ("", "\r", "\x1b"), (button.GetLabel(), key)
    finally:
        dialog.Destroy()


@mac
def test_option_v_reaches_the_installed_handler(api_keys, monkeypatch):
    saved = _record(monkeypatch, api_keys, "on_save_limits")
    event = _key("v", api_keys.key_box)
    api_keys.key_box.GetEventHandler().ProcessEvent(event)
    assert saved == [()]
