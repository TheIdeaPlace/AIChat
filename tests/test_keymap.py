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

@pytest.mark.parametrize("key", ["Ctrl+Shift+Q", "Ctrl+Alt+P", "F7", "Shift+F7", "Ctrl+Q",
                                 "Alt+Shift+Enter", "Ctrl+Alt+Shift+K"])
def test_keys_that_can_be_given(key):
    assert refusal(key, "win32") == ""


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
    assert "Redo" in refusal("Ctrl+Shift+Z", "darwin")
    assert refusal("Ctrl+Shift+Z", "win32") == ""


def test_every_default_can_be_given_back():
    # A command's own default is never a refused key (on a Mac, Retry's
    # isn't an accelerator, so it isn't given there: its default stays).
    for command in commands.commands():
        if command.key and keymap.customizable(command):
            assert refusal(normalize(command.key), "win32") == "", command.id


def test_keys_owned_by_a_part_of_the_window_cant_be_changed(keys):
    ids = dict(keys.items())
    assert "hide_session" not in ids and "steer" not in ids
    assert "settings" in ids and "sounds" in ids
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
    assert keys.set("prompts", "Ctrl+Alt+P") is None
    assert keys.key("prompts") == "Ctrl+Alt+P" and keys.changed("prompts")
    assert keys.command_for("Ctrl+Shift+P") is None
    assert keys.command_for("ctrl+alt+p") == "prompts"


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
    keys.set("prompts", "Ctrl+Alt+P")
    keys.reset_all()
    assert keys.key("prompts") == "Ctrl+Shift+P"


def test_setting_a_command_back_to_its_default_isnt_a_change(keys):
    keys.set("prompts", "Ctrl+Alt+P")
    keys.set("prompts", "Ctrl+Shift+P")
    assert not keys.changed("prompts")


# -- keys.json ----------------------------------------------------------------------------------

def test_saved_and_read_back(tmp_path, keys):
    keys.set("prompts", "Ctrl+Alt+P")
    keys.set("find", "")
    assert keys.save()
    data = json.loads((tmp_path / "keys.json").read_text(encoding="utf-8"))
    assert data == {"version": 1, "keys": {"prompts": "Ctrl+Alt+P", "find": ""}}
    again = KeyMap(tmp_path / "keys.json")
    assert again.key("prompts") == "Ctrl+Alt+P" and again.key("find") == ""
    assert again.load_error == ""


def test_ids_this_release_doesnt_know_are_kept(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"version": 1, "keys": {"from_a_newer_release": "Ctrl+Alt+N"}}),
                    encoding="utf-8")
    keys = KeyMap(path)
    keys.set("sounds", "Ctrl+Alt+S")
    keys.save()
    saved = json.loads(path.read_text(encoding="utf-8"))["keys"]
    assert saved == {"from_a_newer_release": "Ctrl+Alt+N", "sounds": "Ctrl+Alt+S"}


def test_a_hand_edited_file_is_read_in_any_spelling_and_bad_entries_are_said(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": {"prompts": "shift+ctrl+q", "sounds": "Ctrl+V",
                                         "find": "nonsense+key"}}), encoding="utf-8")
    keys = KeyMap(path)
    assert keys.key("prompts") == "Ctrl+Shift+Q"
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
    keys.set("prompts", "Ctrl+Alt+P")
    keys.set("find_in_all_sessions", "")
    keys.set("sounds", "Ctrl+Alt+S")
    text = shortcuts_text(platform="win32", keys=keys)
    assert "Ctrl+Alt+P: Prompts:" in text and "(changed from Ctrl+Shift+P)" in text
    assert "No key: Find in All Sessions" in text
    assert "Keys you added:\n  Ctrl+Alt+S: Sounds" in text
    assert "Ctrl+Shift+P: Prompts" not in text
    # The published list is the defaults.
    assert "Ctrl+Shift+P: Prompts" in shortcuts_text(platform="win32")


def test_stop_is_named_by_the_key_in_effect():
    try:
        set_stop_key("Ctrl+Alt+S")
        assert with_stop_key("Press Stop to end it.", "win32") == "Press Stop (Ctrl+Alt+S) to end it."
        set_stop_key("")
        assert with_stop_key("Press Stop to end it.", "win32") == "Press Stop to end it."
    finally:
        set_stop_key("Ctrl+Period")
    assert with_stop_key("Press Stop to end it.", "win32") == "Press Stop (Ctrl+Period) to end it."


def test_menu_labels_use_the_key_in_effect():
    retry = commands.command("retry_from_here")
    assert commands.menu_label(retry, "win32", key="Ctrl+Alt+R") == "Retr&y from Here...\tCtrl+Alt+R"
    assert commands.menu_label(retry, "darwin", key="Ctrl+Alt+R") == "Retr&y from Here...\tCtrl+Alt+R"
    assert commands.menu_label(retry, "darwin", key="Ctrl+Shift+Z") == "Retr&y from Here..."
    assert commands.menu_label(commands.command("prompts"), "win32", key="") == "&Prompts..."
