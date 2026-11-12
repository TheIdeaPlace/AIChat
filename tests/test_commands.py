"""The menu commands, checked without wx (#575, #635)."""
import re

from thechatplace import commands
from thechatplace.commands import Command, Menu, access_key, menu_label


def all_menus():
    return [Menu("bar", commands.MENU_BAR)] + [m for m in commands.walk() if isinstance(m, Menu)]


def test_ids_are_unique_and_stable_looking():
    ids = [c.id for c in commands.commands()]
    assert len(ids) == len(set(ids))
    # They'll be keys in keys.json (#635): plain, so a file a person edits
    # by hand can't have one that only differs in case or spacing.
    for command_id in ids:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", command_id), command_id


def test_no_two_items_in_a_menu_share_an_access_key():
    for menu in all_menus():
        keys = [access_key(item.label) for item in menu.items if item is not None]
        keys = [k for k in keys if k]
        assert len(keys) == len(set(keys)), (menu.label, sorted(keys))


def test_no_two_commands_share_a_key():
    keys = []
    for command in commands.commands():
        keys += [k.lower() for k in (command.key, command.shown_key) if k]
    assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)


def test_a_command_has_a_menu_key_or_a_shown_key_not_both():
    for command in commands.commands():
        assert not (command.key and command.shown_key), command.id
        assert ("{key}" in command.label) <= bool(command.shown_key), command.id


def test_stock_items_are_the_ones_a_mac_moves_into_its_app_menu():
    stock = {c.stock: c.id for c in commands.commands() if c.stock}
    assert stock == {"preferences": "settings", "exit": "exit", "about": "about"}


def test_the_dynamic_submenus_are_named_and_empty():
    named = {m.id: m for m in commands.walk() if isinstance(m, Menu) and m.id}
    assert set(named) == {"sort_sessions", "show_sessions"}
    assert all(not m.items for m in named.values())


def test_menu_label_puts_the_accelerator_after_a_tab():
    assert menu_label(commands.command("rename_session"), "win32") == "Rename Session...\tF2"
    assert menu_label(commands.command("load_session"), "win32") == "&Load Session"


def test_menu_label_names_a_part_of_the_windows_key_without_a_tab():
    # A tab would make it a window-wide accelerator.
    assert menu_label(commands.command("hide_session"), "win32") == "Hi&de Session (Delete)"
    assert (menu_label(commands.command("delete_session_permanently"), "win32")
            == "Delete Session &Permanently (Shift+Delete)...")
    assert (menu_label(commands.command("send_and_save_prompt"), "win32")
            == "Send and Save as Prompt... (Ctrl+Shift+Enter)")
    for command in commands.commands():
        assert "\t" not in menu_label(command, "win32") or command.key


def test_menu_label_uses_mac_key_names_for_a_shown_key():
    # wx turns Ctrl into Cmd only in an accelerator, so a shown key is
    # translated here.
    assert (menu_label(commands.command("steer"), "darwin")
            == "Steer: Send into the Running Turn (Cmd+Option+Return)")


def test_retry_from_here_has_no_accelerator_on_a_mac():
    # Cmd+Shift+Z is Redo there; the window's key hook handles it in the
    # messages list instead (#468).
    retry = commands.command("retry_from_here")
    assert menu_label(retry, "win32") == "Retry from Here...\tCtrl+Shift+Z"
    assert menu_label(retry, "darwin") == "Retry from Here..."


def test_command_lookup_fails_loudly():
    import pytest
    with pytest.raises(KeyError):
        commands.command("no_such_command")


def test_every_command_is_a_command():
    for item in commands.walk():
        assert item is None or isinstance(item, (Command, Menu))
