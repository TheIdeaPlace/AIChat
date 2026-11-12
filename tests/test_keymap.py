"""Your keyboard shortcuts, without wx (#635)."""
import json

import pytest

from thechatplace import commands, keymap
from thechatplace.keymap import KeyMap, normalize, refusal, spoken
from thechatplace.ui_text import shortcuts_text, set_stop_key, with_stop_key


@pytest.fixture
def keys(tmp_path):
    return KeyMap(tmp_path / "keys.json")


# -- names ----------------------------------------------------------------------------------

@pytest.mark.parametrize("written, expected", [
    ("Ctrl+Shift+A", "Ctrl+Shift+A"),
    ("shift+ctrl+a", "Ctrl+Shift+A"),
    ("Cmd+Option+P", "Ctrl+Alt+P"),
    ("Ctrl+Period", "Ctrl+."),
    ("Ctrl+.", "Ctrl+."),
    ("Ctrl+Comma", "Ctrl+,"),
    ("Ctrl+/", "Ctrl+/"),
    ("alt+return", "Alt+Enter"),
    ("f5", "F5"),
    ("Shift+F3", "Shift+F3"),
    ("Ctrl+Alt+Shift+F12", "Ctrl+Alt+Shift+F12"),
    ("Ctrl+1", "Ctrl+1"),
])
def test_keys_have_one_name(written, expected):
    assert normalize(written) == expected


@pytest.mark.parametrize("written", ["", "Ctrl+", "Ctrl", "Hyper+A", "Ctrl+Banana", "F25", "Ctrl+é"])
def test_a_key_that_isnt_one_is_refused(written):
    with pytest.raises(ValueError):
        normalize(written)


def test_spoken_names_marks_and_mac_keys():
    assert spoken("Ctrl+.", "win32") == "Ctrl+Period"
    assert spoken("Ctrl+,", "darwin") == "Cmd+Comma"
    assert spoken("Alt+Enter", "darwin") == "Option+Return"
    assert spoken("", "win32") == ""


# -- which keys can be given ----------------------------------------------------------------

@pytest.mark.parametrize("key", ["Ctrl+Shift+Q", "Ctrl+K", "F7", "Shift+F7", "Ctrl+Q",
                                 "Alt+Shift+Enter", "Ctrl+Alt+F7", "Ctrl+Alt+Shift+Enter"])
def test_keys_that_can_be_given(key):
    assert refusal(key, "win32") == ""


@pytest.mark.parametrize("key", ["Ctrl+Alt+Q", "Ctrl+Alt+E", "Ctrl+Alt+7", "Ctrl+Alt+Shift+K"])
def test_ctrl_alt_with_a_character_is_altgr_on_windows(key):
    # AltGr types @, € and braces with these on many keyboards (#639).
    assert "AltGr" in refusal(key, "win32")
    assert refusal(key, "darwin") == ""  # Cmd+Option on a Mac


def test_ctrl_delete_and_alt_f4_with_anything_are_refused():
    assert "deletes text" in refusal("Ctrl+Delete", "win32")
    assert "deletes text" in refusal("Ctrl+Alt+Delete", "win32")
    assert "closes the window" in refusal("Ctrl+Alt+F4", "win32")


@pytest.mark.parametrize("key, why", [
    ("Q", "on its own"),
    ("Shift+Q", "types a character"),
    ("Alt+Q", "opens a menu"),
    ("Ctrl+V", "edits text"),
    ("Ctrl+C", "copies"),
    ("Ctrl+Enter", "sends"),
    ("Delete", "hides a session"),
    ("Ctrl+Insert", "Screen readers use Insert"),
    ("Ctrl+Up", "moves around"),
    ("Ctrl+Tab", "moves around"),
    ("F6", "moves between parts"),
    ("Alt+F4", "closes the window"),
])
def test_keys_that_cant_be_given_say_why(key, why):
    assert why in refusal(key, "win32")


def test_mac_keys_that_belong_to_macos():
    assert "macOS" in refusal("Ctrl+Q", "darwin")
    assert "macOS" in refusal("Ctrl+Shift+Z", "darwin")  # Redo
    assert "macOS" in refusal("Ctrl+Shift+Q", "darwin")  # Log Out
    assert refusal("Ctrl+Shift+Z", "win32") == ""


def test_a_mac_keeps_settings_about_and_quit_and_updates_keeps_its_label():
    stock = [commands.command(i) for i in ("settings", "about", "exit")]
    assert not any(keymap.customizable(c, "darwin") for c in stock)
    assert keymap.customizable(commands.command("settings"), "win32")
    assert not keymap.customizable(commands.command("check_for_updates"), "win32")


def test_retry_can_be_given_its_own_default_back_on_a_mac(keys, monkeypatch):
    monkeypatch.setattr(keymap.sys, "platform", "darwin")
    keys.set("retry_from_here", "F7")
    keys.set("retry_from_here", "Ctrl+Shift+Z")
    assert keys.key("retry_from_here") == "Ctrl+Shift+Z" and not keys.changed("retry_from_here")
    with pytest.raises(ValueError, match="macOS"):
        keys.set("find", "Ctrl+Shift+Z")


def test_every_default_can_be_given_back():
    # A command's own default is never a refused key (on a Mac, Retry's
    # isn't an accelerator, so it isn't given there: its default stays).
    for command in commands.commands():
        if command.key and keymap.customizable(command):
            assert refusal(normalize(command.key), "win32") == "", command.id


def test_keys_owned_by_a_part_of_the_window_cant_be_changed(keys):
    ids = dict(keys.items())
    assert "hide_session" not in ids and "steer" not in ids
    assert "sounds" in ids and "find" in ids
    with pytest.raises(ValueError):
        keys.set("hide_session", "Ctrl+Shift+Q")


# -- the map ----------------------------------------------------------------------------------

def test_defaults_until_you_change_one(keys):
    assert keys.key("prompts") == "Ctrl+Shift+P"
    assert keys.key("sounds") == ""
    assert keys.command_for("Ctrl+Shift+P") == "prompts"
    assert keys.command_for("Ctrl+Period") == "stop_running_turn"
    assert not keys.changed("prompts")


def test_a_new_key_moves_and_the_old_one_is_free(keys):
    assert keys.set("prompts", "Ctrl+K") is None
    assert keys.key("prompts") == "Ctrl+K" and keys.changed("prompts")
    assert keys.command_for("Ctrl+Shift+P") is None
    assert keys.command_for("ctrl+k") == "prompts"


def test_taking_a_key_another_command_has_takes_it_from_that_command(keys):
    taken = keys.set("sounds", "Ctrl+Shift+P")
    assert taken == "prompts"
    assert keys.key("sounds") == "Ctrl+Shift+P" and keys.key("prompts") == ""
    assert keys.command_for("Ctrl+Shift+P") == "sounds"


def test_a_refused_key_changes_nothing(keys):
    with pytest.raises(ValueError, match="edits text"):
        keys.set("sounds", "Ctrl+V")
    assert keys.key("sounds") == ""


def test_removing_and_resetting(keys):
    keys.set("find", "")
    assert keys.key("find") == "" and keys.changed("find")
    keys.set("sounds", "Ctrl+F")  # its old key, now free
    assert keys.reset("find") == "sounds"  # find takes it back; sounds loses it
    assert keys.key("find") == "Ctrl+F" and keys.key("sounds") == ""
    keys.set("prompts", "Ctrl+K")
    keys.reset_all()
    assert keys.key("prompts") == "Ctrl+Shift+P"


def test_setting_a_command_back_to_its_default_isnt_a_change(keys):
    keys.set("prompts", "Ctrl+K")
    keys.set("prompts", "Ctrl+Shift+P")
    assert not keys.changed("prompts")


# -- keys.json ----------------------------------------------------------------------------------

def test_saved_and_read_back(tmp_path, keys):
    keys.set("prompts", "Ctrl+K")
    keys.set("find", "")
    assert keys.save()
    data = json.loads((tmp_path / "keys.json").read_text(encoding="utf-8"))
    assert data == {"version": 1, "keys": {"prompts": "Ctrl+K", "find": ""}}
    again = KeyMap(tmp_path / "keys.json")
    assert again.key("prompts") == "Ctrl+K" and again.key("find") == ""
    assert again.load_error == ""


def test_ids_this_release_doesnt_know_are_kept(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"version": 1, "keys": {"from_a_newer_release": "Ctrl+Alt+N"}}),
                    encoding="utf-8")
    keys = KeyMap(path)
    keys.set("sounds", "Ctrl+J")
    keys.save()
    saved = json.loads(path.read_text(encoding="utf-8"))["keys"]
    assert saved == {"from_a_newer_release": "Ctrl+Alt+N", "sounds": "Ctrl+J"}


def test_a_hand_edited_file_is_read_in_any_spelling_and_bad_entries_are_said(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": {"prompts": "shift+ctrl+v", "sounds": "Ctrl+V",
                                         "find": "nonsense+key"}}), encoding="utf-8")
    keys = KeyMap(path)
    assert keys.key("prompts") == "Ctrl+Shift+V"
    assert keys.key("sounds") == "" and keys.key("find") == "Ctrl+F"
    assert "2 of your keyboard shortcuts" in keys.load_error


def test_a_damaged_file_is_set_aside_and_the_defaults_used(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("{not json", encoding="utf-8")
    keys = KeyMap(path)
    assert keys.key("prompts") == "Ctrl+Shift+P"
    assert "isn't valid JSON" in keys.load_error
    assert not path.exists() and list(tmp_path.glob("keys.json.bad-*"))


# -- what the window says ---------------------------------------------------------------------

def test_the_shortcuts_list_shows_your_keys(keys):
    keys.set("prompts", "Ctrl+K")
    keys.set("find_in_all_sessions", "")
    keys.set("sounds", "Ctrl+J")
    text = shortcuts_text(platform="win32", keys=keys)
    assert "Ctrl+K: Prompts:" in text and "(changed from Ctrl+Shift+P)" in text
    assert "No key: Find in All Sessions" in text
    assert "Keys you added:\n  Ctrl+J: Sounds" in text
    assert "Ctrl+Shift+P: Prompts" not in text
    # The published list is the defaults.
    assert "Ctrl+Shift+P: Prompts" in shortcuts_text(platform="win32")


def test_stop_is_named_by_the_key_in_effect():
    try:
        set_stop_key("Ctrl+J")
        assert with_stop_key("Press Stop to end it.", "win32") == "Press Stop (Ctrl+J) to end it."
        set_stop_key("")
        assert with_stop_key("Press Stop to end it.", "win32") == "Press Stop to end it."
    finally:
        set_stop_key("Ctrl+Period")
    assert with_stop_key("Press Stop to end it.", "win32") == "Press Stop (Ctrl+Period) to end it."


def test_a_file_cant_put_two_commands_on_one_key(tmp_path):
    # Yours wins over a default, which loses its key; of two of yours on one
    # key, the first keeps it and the other is named as unusable (#639).
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": {"sounds": "Ctrl+N", "user_guide": "Ctrl+K",
                                         "report_bug": "ctrl+k"}}), encoding="utf-8")
    keys = KeyMap(path)
    assert keys.key("sounds") == "Ctrl+N" and keys.key("new_session") == ""
    assert keys.command_for("Ctrl+N") == "sounds"
    assert keys.key("user_guide") == "Ctrl+K" and keys.key("report_bug") == ""
    assert "1 of your keyboard shortcuts" in keys.load_error
    owners = [k for _, k in keys.items() if k]
    assert len(owners) == len(set(owners))


@pytest.mark.parametrize("value", [None, 0, False, [], {"key": "Ctrl+K"}])
def test_a_key_that_isnt_text_is_unusable_not_removed(tmp_path, value):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": {"find": value}}), encoding="utf-8")
    keys = KeyMap(path)
    assert keys.key("find") == "Ctrl+F" and "1 of your" in keys.load_error


def test_every_key_in_the_shortcuts_list_follows_yours(keys):
    # Each command with a default key can be found in the list, even in a
    # row naming several keys, so changing it changes the list (#639).
    from thechatplace.ui_text import SHORTCUTS, shortcuts_with_yours
    for command_id, default in list(keys.items()):
        if not default:
            continue
        keys.set(command_id, "F24")
        rows = [row for _, items in shortcuts_with_yours(keys) for row in items]
        assert any(key.startswith("F24") or " F24" in key or ", F24" in key
                   for key, _ in rows), command_id
        assert any(f"changed from {spoken(default, 'win32')}" in action for _, action in rows), command_id
        keys.reset_all()
    assert shortcuts_with_yours(None) is SHORTCUTS


def test_rows_with_several_keys_change_only_the_one_you_moved(keys):
    keys.set("find_next", "F7")
    text = shortcuts_text(platform="win32", keys=keys)
    assert "Ctrl+F, then F7 and Shift+F3:" in text and "(changed from F3)" in text


def test_menu_labels_use_the_key_in_effect():
    retry = commands.command("retry_from_here")
    assert commands.menu_label(retry, "win32", key="Ctrl+Alt+R") == "Retr&y from Here...\tCtrl+Alt+R"
    assert commands.menu_label(retry, "darwin", key="Ctrl+Alt+R") == "Retr&y from Here...\tCtrl+Alt+R"
    assert commands.menu_label(retry, "darwin", key="Ctrl+Shift+Z") == "Retr&y from Here..."
    assert commands.menu_label(commands.command("prompts"), "win32", key="") == "&Prompts..."
