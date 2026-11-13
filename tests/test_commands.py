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
    assert menu_label(commands.command("rename_session"), "win32") == "Re&name Session...\tF2"
    assert menu_label(commands.command("load_session"), "win32") == "&Load Session"


def test_menu_label_names_a_part_of_the_windows_key_without_a_tab():
    # A tab would make it a window-wide accelerator.
    assert menu_label(commands.command("hide_session"), "win32") == "&Hide Session (Delete)"
    assert (menu_label(commands.command("delete_session_permanently"), "win32")
            == "&Delete Session Permanently (Shift+Delete)...")
    assert (menu_label(commands.command("send_and_save_prompt"), "win32")
            == "Send and Sa&ve as Prompt... (Ctrl+Shift+Enter)")
    for command in commands.commands():
        assert "\t" not in menu_label(command, "win32") or command.key


def test_menu_label_uses_mac_key_names_for_a_shown_key():
    # wx turns Ctrl into Cmd only in an accelerator, so a shown key is
    # translated here.
    assert (menu_label(commands.command("steer"), "darwin")
            == "Stee&r: Send into the Running Turn (Cmd+Option+Return)")


def test_retry_from_here_has_no_accelerator_on_a_mac():
    # Cmd+Shift+Z is Redo there; the window's key hook handles it in the
    # messages list instead (#468).
    retry = commands.command("retry_from_here")
    assert menu_label(retry, "win32") == "Retr&y from Here...\tCtrl+Shift+Z"
    assert menu_label(retry, "darwin") == "Retr&y from Here..."


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
    ('File', '&New Session...\tCtrl+N'),
    ('File', 'New &Agent Conversation...\tCtrl+Shift+G'),
    ('File', '&Continue Here...\tCtrl+Shift+N'),
    ('File', '---'),
    ('File', '&Export Session...\tCtrl+E'),
    ('File', 'Export C&hats...'),
    ('File', '&Backup'),
    ('File > Backup', 'Back Up &Now...'),
    ('File > Backup', '&Check Backup...'),
    ('File > Backup', 'Backup &Schedule...'),
    ('File > Backup', '&Restore from Backup...'),
    ('File', '---'),
    ('File', 'E&xit'),
    ('Session', '&Load Session'),
    ('Session', '&Open in Claude\tCtrl+O'),
    ('Session', '&Properties...\tAlt+Enter'),
    ('Session', 'Copy Session Lin&k...\tCtrl+Shift+L'),
    ('Session', 'Re&name Session...\tF2'),
    ('Session', 'Remote &Control...'),
    ('Session', '---'),
    ('Session', 'Add to &Group...\tCtrl+G'),
    ('Session', 'Remove from Gro&up...'),
    ('Session', '&Manage Groups...'),
    ('Session', '---'),
    ('Session', '&Hide Session (Delete)'),
    ('Session', '&Bring Back Session'),
    ('Session', '&Delete Session Permanently (Shift+Delete)...'),
    ('Session', '---'),
    ('Session', 'Agent Conversation M&embers...'),
    ('Session', 'Agent Conversation Hand-o&ffs...'),
    ('Chat', '&Answer...\tCtrl+Shift+A'),
    ('Chat', 'Ask As&ide...\tCtrl+Shift+Y'),
    ('Chat', '&Stop Running Turn\tCtrl+.'),
    ('Chat', 'Stop &Background Task...'),
    ('Chat', '&Turn Status\tCtrl+Shift+T'),
    ('Chat', '---'),
    ('Chat', 'Insert Command or S&kill...\tCtrl+/'),
    ('Chat', 'Attac&h Files...\tCtrl+Shift+F'),
    ('Chat', '&Prompts...'),
    ('Chat', 'Send and Sa&ve as Prompt... (Ctrl+Shift+Enter)', 'Send and Sa&ve as Prompt... (Cmd+Shift+Return)'),
    ('Chat', 'Stee&r: Send into the Running Turn (Ctrl+Alt+Enter)', 'Stee&r: Send into the Running Turn (Cmd+Option+Return)'),
    ('Chat', 'Send Reply to Another &Chat...\tCtrl+Shift+E'),
    ('Chat', '---'),
    ('Chat', 'Retr&y from Here...\tCtrl+Shift+Z', 'Retr&y from Here...'),
    ('Chat', 'Branch &from Here'),
    ('Chat', '---'),
    ('Chat', 'Chat Setti&ngs'),
    ('Chat > Chat Settings', 'Change &Model...'),
    ('Chat > Chat Settings', 'Change &Effort...'),
    ('Chat > Chat Settings', 'Change &Permission Mode...\tCtrl+Shift+O'),
    ('Chat > Chat Settings', '&Chat Permissions...\tCtrl+Shift+H'),
    ('Chat > Chat Settings', 'Chat &Options...'),
    ('Chat', 'Other &Machines...\tCtrl+Shift+M'),
    ('View', 'Go to &Sessions\tCtrl+1'),
    ('View', 'Go to &Messages\tCtrl+2'),
    ('View', 'Go to &Reply\tCtrl+3'),
    ('View', 'Go to Status &Bar\tCtrl+9'),
    ('View', '---'),
    ('View', 'S&ort Sessions'),
    ('View', 'S&how Sessions'),
    ('View', 'Session List &Columns...'),
    ('View', 'Show &Tool Activity\tCtrl+T'),
    ('View', 'Re&fresh\tF5'),
    ('View', '---'),
    ('View', 'R&ead Full Message'),
    ('View', 'Read Full Chat as a &Web Page\tCtrl+Shift+W'),
    ('View', 'F&ind...\tCtrl+F'),
    ('View', 'Find &Next\tF3'),
    ('View', 'Find Pre&vious\tShift+F3'),
    ('View', 'Find in &All Sessions...\tCtrl+Shift+S'),
    ('View', 'Repeat &Last Announcement\tCtrl+Shift+R'),
    ('View', 'Command &Palette...\tCtrl+Shift+P'),
    ('Tools', '&Lists...\tCtrl+Shift+I'),
    ('Tools', 'L&inks...\tCtrl+L'),
    ('Tools', '&Tasks...\tCtrl+Shift+J'),
    ('Tools', 'Suba&gents...'),
    ('Tools', 'Change&d Files...\tCtrl+Shift+D'),
    ('Tools', '&Usage and Context...\tCtrl+Shift+U'),
    ('Tools', '---'),
    ('Tools', 'What AI Knows &About You...\tCtrl+Shift+K'),
    ('Tools', '&Connected Tools (MCP)...'),
    ('Tools', 'Sig&n In'),
    ('Tools > Sign In', '&Claude Code...'),
    ('Tools > Sign In', 'C&odex...'),
    ('Tools > Sign In', 'Co&pilot...'),
    ('Tools', '---'),
    ('Tools', 'S&ounds...'),
    ('Tools', '&Mute All Sounds\tCtrl+Shift+X'),
    ('Tools', 'Customize &Keyboard Shortcuts...'),
    ('Tools', '&Settings...\tCtrl+,'),
    ('Help', 'User &Guide'),
    ('Help', '&Keyboard Shortcuts\tF1'),
    ('Help', 'Sound &Tutorial...'),
    ('Help', 'Check for &Updates...'),
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
    assert [m.label for m in commands.MENU_BAR] == [
        "&File", "&Session", "&Chat", "&View", "&Tools", "&Help"]


def test_every_item_has_an_access_key():
    # #575: none is left to arrowing, in any menu. The Sort and Show Sessions
    # submenus are the window's own radio items, chosen by arrowing as a
    # list is.
    for menu in all_menus():
        for item in menu.items:
            if item is not None:
                assert access_key(item.label), (menu.label, item.label)


#: Access keys of controls always in the main window, or on one of the reply
#: areas (Your message, Send, Stop, Commands, Attach Files; Open in Claude,
#: Continue Here).
#: A visible control's key wins over the menu bar's (#575), so no menu may
#: share one. tests/test_ui_menus.py checks this list against the window.
WINDOW_ACCESS_KEYS = set("lmydpoinera")


def test_no_menu_shares_a_letter_with_a_control_in_the_window():
    letters = [access_key(m.label) for m in commands.MENU_BAR]
    assert letters == ["f", "s", "c", "v", "t", "h"]
    assert not set(letters) & WINDOW_ACCESS_KEYS
