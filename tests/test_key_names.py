"""App-authored shortcuts use the names of the keys that work (#246)."""
import ast
from pathlib import Path

from thechatplace.ui_text import (choose_access_key, key_name, mark_access_key, shortcuts_html,
                                  shortcuts_text)


def test_key_names_on_each_platform():
    assert key_name('Ctrl+Shift+A', 'darwin') == 'Cmd+Shift+A'
    assert key_name('Ctrl+Enter / Alt+Enter', 'darwin') == 'Cmd+Return / Option+Return'
    assert key_name('Press Enter to start.', 'darwin') == 'Press Return to start.'
    assert key_name('Ctrl+Shift+A / Alt+Enter', 'win32') == 'Ctrl+Shift+A / Alt+Enter'
    assert key_name('Win+Shift+S', 'darwin') == 'Control+Cmd+Shift+4'
    assert key_name('Applications key / Shift+F10', 'darwin') == 'Shift+F10'
    assert key_name('Alt+F4', 'darwin') == 'Cmd+Q'
    assert key_name('Alt+V, C', 'darwin') == 'View menu (VO+M)'
    assert key_name('Alt+F, H', 'darwin') == 'File menu (VO+M)'
    assert key_name('Alt+H, G', 'darwin') == 'Help menu (VO+M)'
    # The menus #575 added.
    assert key_name('Alt+S, C', 'darwin') == 'Session menu (VO+M)'
    assert key_name('Alt+C, N', 'darwin') == 'Chat menu (VO+M)'
    assert key_name('Alt+T, G', 'darwin') == 'Tools menu (VO+M)'
    # A control's own Alt key isn't a menu.
    assert key_name('Attach Files (Alt+I)', 'darwin') == 'Attach Files (Option+I)'
    assert key_name('Alt+Tab', 'darwin') == 'Cmd+Tab'
    assert key_name('Enterprise folder', 'darwin') == 'Enterprise folder'


def test_help_names_mac_keys_and_published_help_is_portable():
    for body in (shortcuts_text(platform='darwin'), shortcuts_html(platform='darwin')):
        assert 'Cmd+Shift+A' in body and 'Option+' in body and 'Return' in body
        assert 'Ctrl+' not in body and 'Alt+' not in body
    assert 'Ctrl+Shift+A' in shortcuts_text(platform='win32')
    assert 'Attach Files button, Alt+I' in shortcuts_text(platform='win32')
    assert 'Attach Files button)' in shortcuts_text(platform='darwin')
    assert 'Option+Return on a Mac' not in shortcuts_text(platform='darwin')
    portable = shortcuts_text(platform='all')
    assert 'Command (Cmd) for Ctrl' in portable and 'Ctrl+Shift+A' in portable


def test_ui_shortcut_literals_are_translated_except_native_menu_accelerators():
    ui = Path(__file__).resolve().parents[1] / 'thechatplace'
    failures = []
    for path in ui.rglob('*.py'):
        tree = ast.parse(path.read_text())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not any(key in node.value for key in ('Ctrl+', 'Alt+')):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Expr) or '\t' in node.value:
                continue  # Docstrings and native menu accelerators are not authored prompts.
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == 'key_name':
                continue
            ancestor = parent
            canonical = False
            while ancestor in parents:
                if (path.name == 'commands.py' and isinstance(ancestor, ast.Call)
                        and isinstance(ancestor.func, ast.Name) and ancestor.func.id == 'Command'):
                    # A command's key is data: menu_label makes it a tab
                    # accelerator or puts it through key_name (#575).
                    canonical = True
                if (isinstance(ancestor, ast.Assign) and isinstance(ancestor.targets[0], ast.Name)
                        and (path.name, ancestor.targets[0].id) == ('keymap.py', 'FIXED')):
                    # The keys the window keeps: said only through
                    # keymap.spoken, which is key_name (#635).
                    canonical = True
                if (isinstance(ancestor, ast.Call) and isinstance(ancestor.func, ast.Name)
                        and ancestor.func.id == 'key_name' and _in_your_key(node, parents)):
                    # your_key('Ctrl+Shift+A') inside key_name(...), directly
                    # or in a sentence: your key, translated (#635).
                    canonical = True
                if (path.name == 'ui_text.py' and isinstance(ancestor, ast.FunctionDef)
                        and ancestor.name == 'with_stop_key' and _in_your_key(node, parents)):
                    canonical = True  # it puts the key through key_name itself
                if path.name == 'ui_text.py':
                    if isinstance(ancestor, ast.FunctionDef) and ancestor.name == 'key_name':
                        canonical = True
                    if isinstance(ancestor, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'SHORTCUTS' for target in ancestor.targets):
                        canonical = True
                ancestor = parents[ancestor]
            if not canonical:
                failures.append(f'{path.relative_to(ui)}:{node.lineno}')
    assert failures == []


#: Strings that may name a changeable default key outside your_key (#635),
#: and why: each says what it is, so the list stays short and deliberate.
KEPT_DEFAULTS = {
    # Built once, at import: the no-sessions line in an empty list.
    ("ui/main_frame.py", "NO_SESSIONS_YET"),
    # The New Session dialog's note on permission modes, one long text.
    ("ui/dialogs.py", "PERMISSION_NOTE"),
    # The subagent window's own check box, whose Ctrl+T is that window's.
    ("ui/subagent_chat.py", "SubagentChatDialog"),
}


def test_what_the_app_says_names_your_key_not_the_default():
    """A key you can change is said through ui_text.your_key, so once you
    change it, what's said follows (#635)."""
    import re
    from thechatplace import commands, keymap
    names = set()
    for command in commands.commands():
        if command.key and keymap.customizable(command, 'win32'):
            names.add(keymap.spoken(keymap.normalize(command.key), 'win32'))
    pattern = re.compile(r"(?<![\w+])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                         + r")(?![\w+])")
    ui = Path(__file__).resolve().parents[1] / 'thechatplace'
    failures = []
    for path in ui.rglob('*.py'):
        rel = path.relative_to(ui).as_posix()
        if rel in ('commands.py', 'keymap.py', 'ui_text.py'):
            continue  # where keys are defined, and the published shortcuts list
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if isinstance(parents.get(node), ast.Expr) or '\t' in node.value or _in_your_key(node, parents):
                continue
            if not pattern.search(node.value):
                continue
            # The outermost class, function or module-level name it's in.
            ancestor, owner = node, None
            while ancestor in parents:
                ancestor = parents[ancestor]
                if isinstance(ancestor, (ast.ClassDef, ast.FunctionDef)):
                    owner = ancestor.name
                elif isinstance(ancestor, ast.Assign) and isinstance(ancestor.targets[0], ast.Name):
                    owner = ancestor.targets[0].id
            if (rel, owner) not in KEPT_DEFAULTS:
                failures.append(f"{rel}:{node.lineno} {node.value[:60]!r}")
    assert failures == []


def _in_your_key(node, parents) -> bool:
    """``node`` is the default key given to ui_text.your_key (#635)."""
    parent = parents.get(node)
    return (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
            and parent.func.id == 'your_key')


def test_a_forms_access_keys_keep_clear_of_the_dialogs_and_each_other():
    # Message, Send, Decline and Answer Later have m, s, d and l (#468).
    taken = set("msdl")
    keys = [choose_access_key(label, taken) for label in
            ("City", "Nights", "Bringing pets", "Budget", "Size", "Message", "Ms", "1 of 2", "!")]
    assert keys == ["c", "n", "b", "u", "i", "e", "", "1", ""]
    assert taken == set("msdl") | {"c", "n", "b", "u", "i", "e", "1"}


def test_a_key_is_only_a_plain_letter_or_digit():
    assert choose_access_key("Über", set()) == "b"
    assert choose_access_key("日本語", set()) == ""
    # The start of a later word before a letter inside an earlier one.
    assert choose_access_key("Bring items", {"b"}) == "i"
    assert choose_access_key("Bring pets", {"b"}) == "p"


def test_a_forms_label_shows_its_own_ampersand_and_marks_its_key():
    assert mark_access_key("City", "c") == "&City"
    assert mark_access_key("Bed & breakfast (required)", "b") == "&Bed && breakfast (required)"
    assert mark_access_key("Bed & breakfast", "r") == "Bed && b&reakfast"
    assert mark_access_key("Pick & choose", "") == "Pick && choose"
    # Across a line break, where wrapping put one.
    assert mark_access_key("Long\nword", "w") == "Long\n&word"
    assert mark_access_key("ſ only", "s") == "ſ only"
