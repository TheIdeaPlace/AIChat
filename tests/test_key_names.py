"""App-authored shortcuts use the names of the keys that work (#246)."""
import ast
from pathlib import Path

from thechatplace.ui_text import key_name, shortcuts_html, shortcuts_text


def test_key_names_on_each_platform():
    assert key_name('Ctrl+Shift+A', 'darwin') == 'Cmd+Shift+A'
    assert key_name('Ctrl+Enter / Alt+Enter', 'darwin') == 'Cmd+Return / Option+Return'
    assert key_name('Press Enter to start.', 'darwin') == 'Press Return to start.'
    assert key_name('Ctrl+Shift+A / Alt+Enter', 'win32') == 'Ctrl+Shift+A / Alt+Enter'
    assert key_name('Win+Shift+S', 'darwin') == 'Control+Cmd+Shift+4'
    assert key_name('Applications key / Shift+F10', 'darwin') == 'Shift+F10'
    assert key_name('Alt+F4', 'darwin') == 'Cmd+Q'
    assert key_name('Alt+V, E', 'darwin') == 'menu bar (VO+M)'
    assert key_name('Enterprise folder', 'darwin') == 'Enterprise folder'


def test_help_names_mac_keys_and_published_help_is_portable():
    for body in (shortcuts_text(platform='darwin'), shortcuts_html(platform='darwin')):
        assert 'Cmd+Shift+A' in body and 'Option+' in body and 'Return' in body
        assert 'Ctrl+' not in body and 'Alt+' not in body
    assert 'Ctrl+Shift+A' in shortcuts_text(platform='win32')
    portable = shortcuts_text(platform='all')
    assert 'Command (Cmd) for Ctrl' in portable and 'Ctrl+Shift+A' in portable


def test_ui_shortcut_literals_are_translated_except_native_menu_accelerators():
    ui = Path(__file__).resolve().parents[1] / 'thechatplace' / 'ui'
    failures = []
    for path in ui.glob('*.py'):
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
            failures.append(f'{path.name}:{node.lineno}')
    assert failures == []
