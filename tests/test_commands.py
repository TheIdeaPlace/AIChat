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


#: Every menu entry as people hear it: (menu, Windows label[, Mac label]).
#: Written out, not computed, so a label, access key or key that changes by
#: accident fails here, and one that changes on purpose shows in the diff
#: (#575). "---" is a separator.
GOLDEN = [
    ('File', '&Load Session'),
    ('File', 'Open in &Claude\tCtrl+O'),
    ('File', 'Cop&y Session Link...\tCtrl+Shift+L'),
    ('File', '&Answer...\tCtrl+Shift+A'),
    ('File', 'Ask Aside...\tCtrl+Shift+Y'),
    ('File', 'Con&tinue Here...\tCtrl+Shift+N'),
    ('File', 'Retry from Here...\tCtrl+Shift+Z', 'Retry from Here...'),
    ('File', 'Branch &from Here'),
    ('File', '&New Session...\tCtrl+N'),
    ('File', 'Chat Sett&ings'),
    ('File > Chat Settings', 'Change &Model...'),
    ('File > Chat Settings', 'Change &Effort...'),
    ('File > Chat Settings', 'Change &Permission Mode...\tCtrl+Shift+O'),
    ('File > Chat Settings', '&Chat Permissions...\tCtrl+Shift+H'),
    ('File > Chat Settings', 'Chat &Options...'),
    ('File', 'Rem&ote Control...'),
    ('File', 'Other Machines...\tCtrl+Shift+M'),
    ('File', 'Send Reply to Another Chat...\tCtrl+Shift+E'),
    ('File', 'New Agent Conversation...\tCtrl+Shift+G'),
    ('File', 'Agent Conversation Members...'),
    ('File', 'Agent Conversation Hand-offs...'),
    ('File', '&Refresh\tF5'),
    ('File', 'Rename Session...\tF2'),
    ('File', 'Hi&de Session (Delete)'),
    ('File', '&Bring Back Session'),
    ('File', 'Delete Session &Permanently (Shift+Delete)...'),
    ('File', '&Export Session...\tCtrl+E'),
    ('File', 'Insert Command or S&kill...\tCtrl+/'),
    ('File', 'Attac&h Files...\tCtrl+Shift+F'),
    ('File', 'Prompts...\tCtrl+Shift+P'),
    ('File', 'Send and Save as Prompt... (Ctrl+Shift+Enter)',
     'Send and Save as Prompt... (Cmd+Shift+Return)'),
    ('File', 'Steer: Send into the Running Turn (Ctrl+Alt+Enter)',
     'Steer: Send into the Running Turn (Cmd+Option+Return)'),
    ('File', '---'),
    ('File', 'Add to &Group...\tCtrl+G'),
    ('File', 'Remove from Gro&up...'),
    ('File', '&Manage Groups...'),
    ('File', '---'),
    ('File', '&Settings...\tCtrl+,'),
    ('File', 'Sounds...'),
    ('File', '---'),
    ('File', 'Back Up No&w...'),
    ('File', 'Check Backup...'),
    ('File', 'Backup Schedule...'),
    ('File', 'Restore from Backup...'),
    ('File', 'Export Chats...'),
    ('File', '---'),
    ('File', 'E&xit'),
    ('View', 'Go to &Sessions\tCtrl+1'),
    ('View', 'Go to &Messages\tCtrl+2'),
    ('View', 'Go to &Reply\tCtrl+3'),
    ('View', 'Go to Status &Bar\tCtrl+9'),
    ('View', 'S&ort Sessions'),
    ('View', 'S&how Sessions'),
    ('View', 'S&ession List Columns...'),
    ('View', '---'),
    ('View', 'Read &Full Message'),
    ('View', 'Read Full Chat as a &Web Page\tCtrl+Shift+W'),
    ('View', 'F&ind...\tCtrl+F'),
    ('View', 'Find &Next\tF3'),
    ('View', 'Find Pre&vious\tShift+F3'),
    ('View', 'Links...\tCtrl+L'),
    ('View', 'Lists...\tCtrl+Shift+I'),
    ('View', 'Tasks...\tCtrl+Shift+J'),
    ('View', 'Suba&gents...'),
    ('View', 'Find in &All Sessions...\tCtrl+Shift+S'),
    ('View', 'Properties...\tAlt+Enter'),
    ('View', 'Show &Tool Activity\tCtrl+T'),
    ('View', 'Mute All Sounds\tCtrl+Shift+X'),
    ('View', 'Sto&p Running Turn\tCtrl+.'),
    ('View', 'Stop Background Task...'),
    ('View', 'T&urn Status\tCtrl+Shift+T'),
    ('View', 'Usage and &Context...\tCtrl+Shift+U'),
    ('View', 'Change&d Files...\tCtrl+Shift+D'),
    ('View', 'Repeat &Last Announcement\tCtrl+Shift+R'),
    ('View', '---'),
    ('View', 'What AI &Knows About You...\tCtrl+Shift+K'),
    ('View', 'Connected Tools (MCP)...'),
    ('Help', 'User &Guide'),
    ('Help', '&Keyboard Shortcuts\tF1'),
    ('Help', 'Sound &Tutorial...'),
    ('Help', 'Check for &Updates...'),
    ('Help', 'Claude Code &Sign-in...'),
    ('Help', 'Codex S&ign-in...'),
    ('Help', 'C&opilot Sign-in...'),
    ('Help', 'Report a &Bug...'),
    ('Help', '&About'),
]


def _entries(platform):
    rows = []

    def walk(items, path):
        for item in items:
            if item is None:
                rows.append((path, "---"))
            elif isinstance(item, Menu):
                rows.append((path, item.label))
                walk(item.items, path + " > " + item.label.replace("&", ""))
            else:
                rows.append((path, menu_label(item, platform)))

    for top in commands.MENU_BAR:
        walk(top.items, top.label.replace("&", ""))
    return rows


def test_every_menu_entry_reads_as_written_down():
    assert _entries("win32") == [(row[0], row[1]) for row in GOLDEN]
    assert _entries("darwin") == [(row[0], row[-1]) for row in GOLDEN]
    assert [m.label for m in commands.MENU_BAR] == ["&File", "&View", "&Help"]
