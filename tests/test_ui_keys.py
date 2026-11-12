"""Tools, Customize Keyboard Shortcuts and Change Key (#635)."""
import json
import sys

import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths  # noqa: E402
from thechatplace.own_store import OwnSessionStore  # noqa: E402

import fake_env  # noqa: E402
from thechatplace.ui_text import key_name as K  # noqa: E402  (Cmd+ on a Mac)


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def frame(env):
    from thechatplace.ui.main_frame import MainFrame
    window = MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"),
                       check_updates_at_start=False)
    yield window
    window._runners.clear()
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


@pytest.fixture
def dialog(frame):
    from thechatplace.ui.keys_dialog import KeyboardShortcutsDialog
    said = []
    window = KeyboardShortcutsDialog(frame, frame.keymap, said.append, frame.apply_keys)
    window.said = said
    yield window
    window.Destroy()


def _key_event(code, ctrl=False, alt=False, shift=False):
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(code)
    event.SetControlDown(ctrl)
    event.SetAltDown(alt)
    event.SetShiftDown(shift)
    return event


def _select(dialog, command_id):
    dialog._fill(command_id)
    assert dialog._selected_id() == command_id


def test_the_menu_item_opens_it(frame, monkeypatch):
    opened = []
    monkeypatch.setattr(frame, "_modal", lambda d: opened.append(type(d).__name__) or d.Destroy())
    frame.on_customize_keys()
    assert opened == ["KeyboardShortcutsDialog"]
    assert frame.menu_items["customize_keyboard_shortcuts"].GetItemLabel() == \
        "Customize &Keyboard Shortcuts..."


def test_each_row_says_the_command_its_key_and_where_it_is(dialog):
    rows = dialog.list.GetStrings()
    assert f"Prompts, {K('Ctrl+Shift+P')}, Chat menu" in rows
    assert "Sounds, no key, Tools menu" in rows
    assert "Change Model, no key, Chat, Chat Settings" in rows
    assert f"Stop Running Turn, {K('Ctrl+Period')}, Chat menu" in rows
    # Keys that belong to a part of the window aren't offered.
    assert not any(row.startswith("Hide Session") for row in rows)
    assert dialog.list.GetName() == f"Commands, {len(rows)}"


def test_the_filter_narrows_by_name_key_or_menu(dialog):
    dialog.filter.SetValue("chat settings")
    assert all("Chat Settings" in row for row in dialog.list.GetStrings())
    dialog.filter.SetValue(K('Ctrl+Shift+P').lower())
    assert dialog.list.GetStrings() == [f"Prompts, {K('Ctrl+Shift+P')}, Chat menu"]
    dialog.filter.SetValue("no such thing")
    assert dialog.list.GetStrings() == ["No command matches the filter."]
    assert dialog._selected_id() is None


def test_changing_a_key_saves_shows_and_says_it(dialog, frame, monkeypatch):
    from thechatplace.ui import keys_dialog

    class Pressed:
        def __init__(self, parent, keys, command_id, say):
            self.key = "Ctrl+Shift+P"

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            pass

    monkeypatch.setattr(keys_dialog, "KeyCaptureDialog", Pressed)
    _select(dialog, "sounds")
    dialog.on_change()
    assert dialog.said[-1] == f"Sounds: {K('Ctrl+Shift+P')}. Prompts has no key now."
    assert dialog._selected_id() == "sounds"
    assert dialog.list.GetStringSelection() == f"Sounds, {K('Ctrl+Shift+P')}, changed, Tools menu"
    assert frame.menu_items["sounds"].GetItemLabel() == "S&ounds...\tCtrl+Shift+P"
    assert frame.menu_items["prompts"].GetItemLabel() == "&Prompts..."
    saved = json.loads((platform_paths.app_data_dir() / "keys.json").read_text(encoding="utf-8"))
    assert saved["keys"] == {"sounds": "Ctrl+Shift+P", "prompts": ""}


def test_remove_reset_and_reset_all(dialog, frame, monkeypatch):
    _select(dialog, "find")
    dialog.on_remove()
    assert dialog.said[-1] == "Find: no key."
    assert frame.menu_items["find"].GetAccel() is None
    dialog.on_remove()
    assert dialog.said[-1] == "Find has no key."
    dialog.on_reset()
    assert dialog.said[-1] == f"Find: {K('Ctrl+F')}, its default."
    dialog.on_reset()
    assert dialog.said[-1] == "Find has its default key."
    frame.keymap.set("sounds", "Ctrl+J")
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *a, **k: asked.append(message) or wx.NO)
    dialog.on_reset_all()
    assert asked == ["Put back the default keys for 1 command you changed?"]
    assert frame.keymap.key("sounds") == "Ctrl+J"  # No changes nothing
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    dialog.on_reset_all()
    assert frame.keymap.key("sounds") == "" and dialog.said[-1] == "Every command has its default key again."
    dialog.on_reset_all()
    assert dialog.said[-1] == "Every command has its default key."


def test_enter_changes_and_delete_removes_in_the_list(dialog, monkeypatch):
    done = []
    monkeypatch.setattr(dialog, "on_change", lambda: done.append("change"))
    monkeypatch.setattr(dialog, "on_remove", lambda: done.append("remove"))
    deletes = [wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE]
    if sys.platform == "darwin":
        deletes.append(wx.WXK_BACK)  # a Mac's Delete key
    for code in [wx.WXK_RETURN] + deletes:
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(code)
        dialog._on_list_key(event)
    assert done == ["change"] + ["remove"] * len(deletes)


def test_a_command_changed_out_of_the_filter_stays_in_view(dialog, monkeypatch):
    from thechatplace.ui import keys_dialog

    class Pressed:
        def __init__(self, *a):
            self.key = "Ctrl+J"

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            pass

    monkeypatch.setattr(keys_dialog, "KeyCaptureDialog", Pressed)
    dialog.filter.SetValue("no key")
    _select(dialog, "sounds")
    dialog.on_change()
    assert dialog._selected_id() == "sounds" and dialog.filter.GetValue() == ""


def test_what_is_said_follows_your_keys(frame):
    from thechatplace.ui.main_frame import no_sessions_yet
    from thechatplace.ui.dialogs import permission_note
    frame.keymap.set("new_session", "Ctrl+J")
    frame.keymap.set("answer", "Ctrl+K")
    frame.apply_keys()
    assert f"File, New Session ({K('Ctrl+J')})" in no_sessions_yet()
    assert f"{K('Ctrl+K')} answers" in permission_note()
    frame.keymap.set("answer", "")
    frame.apply_keys()
    assert "Chat, Answer answers" in permission_note()


# -- Change Key ----------------------------------------------------------------------------

@pytest.fixture
def capture(frame):
    from thechatplace.ui.keys_dialog import KeyCaptureDialog
    said = []
    window = KeyCaptureDialog(frame, frame.keymap, "sounds", said.append)
    window.said = said
    yield window
    window.Destroy()


def _press(capture, monkeypatch, code, **mods):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: capture.box))
    event = _key_event(code, **mods)
    event.Skip(False)
    capture._on_key(event)
    return event.GetSkipped()


def test_a_pressed_key_is_shown_and_said_with_what_assign_would_do(capture, monkeypatch):
    assert capture.GetTitle() == "Change Key: Sounds"
    _press(capture, monkeypatch, ord("P"), ctrl=True, shift=True)
    assert capture.key == "Ctrl+Shift+P" and capture.box.GetValue() == K("Ctrl+Shift+P")
    assert capture.said[-1] == (f"{K('Ctrl+Shift+P')}. Prompts has it now. Assign moves it to "
                                "Sounds, and Prompts will have no key.")
    _press(capture, monkeypatch, wx.WXK_F7)
    assert capture.said[-1] == "F7. Nobody has it. Assign gives it to Sounds."
    _press(capture, monkeypatch, ord("V"), ctrl=True)
    assert capture.said[-1].startswith(f"{K('Ctrl+V')}. {K('Ctrl+V')} edits text")
    assert not capture.usable()


def test_tab_escape_and_alt_letters_still_work_in_the_key_box(capture, monkeypatch):
    assert _press(capture, monkeypatch, wx.WXK_TAB)
    assert _press(capture, monkeypatch, wx.WXK_TAB, shift=True)
    assert _press(capture, monkeypatch, wx.WXK_ESCAPE)
    if sys.platform != "darwin":  # a Mac has no access keys
        assert _press(capture, monkeypatch, ord("A"), alt=True)  # Assign's access key
    assert capture.key == ""
    # A modifier alone chooses nothing.
    _press(capture, monkeypatch, wx.WXK_CONTROL, ctrl=True)
    assert capture.key == "" and capture.said == []
    # Alt+Enter is a key a command can have.
    assert not _press(capture, monkeypatch, wx.WXK_RETURN, alt=True, shift=True)
    assert capture.key == "Alt+Shift+Enter"


def test_a_key_it_cant_name_is_answered(capture, monkeypatch):
    # The Applications key has no name here: it's answered, not ignored.
    _press(capture, monkeypatch, wx.WXK_WINDOWS_MENU)
    assert capture.said[-1] == "That key can't be used for a shortcut."
    assert capture.key == ""


def test_change_keys_answer_is_always_said_at_once(frame, monkeypatch):
    said = []
    monkeypatch.setattr(frame, "_say", lambda text, **k: said.append((text, k)))
    frame._say_key_result("F7. Nobody has it.")
    assert said[-1][0] == "F7. Nobody has it."
    assert said[-1][1]["force"] and said[-1][1]["interrupt"]


def test_assign_needs_a_usable_key(capture, monkeypatch):
    ended = []
    monkeypatch.setattr(capture, "EndModal", lambda code: ended.append(code))
    capture.on_assign()
    assert capture.said[-1] == "Press the keys you want first." and ended == []
    capture.capture("Ctrl+V")
    capture.on_assign()
    assert ended == [] and "edits text" in capture.said[-1]
    capture.capture("Ctrl+J")
    _press(capture, monkeypatch, wx.WXK_RETURN)  # Enter assigns
    assert ended == [wx.ID_OK]
    capture.on_again()
    assert capture.key == "" and capture.box.GetValue() == ""


@pytest.mark.parametrize("code, mods, expected", [
    (ord("Q"), dict(ctrl=True, shift=True), "Ctrl+Shift+Q"),
    (wx.WXK_F7, {}, "F7"),
    (ord(","), dict(ctrl=True), "Ctrl+,"),
    (wx.WXK_NUMPAD5, dict(ctrl=True), "Ctrl+5"),
    (wx.WXK_RETURN, dict(alt=True), "Alt+Enter"),
    (wx.WXK_SHIFT, dict(shift=True), ""),
])
def test_event_key_names(code, mods, expected):
    from thechatplace.ui.keys_dialog import event_key
    assert event_key(_key_event(code, **mods)) == expected
