"""Local references (#253), with real temporary files and no wx windows."""
import html
import subprocess
import types

import pytest

from markers import windows_paths
from thechatplace import lists, platform_paths
from thechatplace.local_files import BLOCKED_SUFFIXES, file_url, local_path
from thechatplace.message_links import FoundLink, links_in_text
from thechatplace.rendering import message_page
from thechatplace.transcript import ASSISTANT, ChatMessage


@windows_paths
@pytest.mark.parametrize('text, expected, code', [
    (r'Open C:\x\notes.txt.', r'C:\x\notes.txt', False),
    (r'(C:\x\notes.txt).', r'C:\x\notes.txt', False),
    ('C:/x/notes.txt:12', r'C:\x\notes.txt', False),
    (r'C:\x\y.cs:12:4', r'C:\x\y.cs', False),
    (r'\\server\share\notes.txt', r'\\server\share\notes.txt', False),
    ('file:///C:/x/my%20notes.txt.', r'C:\x\my notes.txt', False),
    ('file:C:/x/notes.txt', r'C:\x\notes.txt', False),
    ('file://server/share/my%20notes.txt', r'\\server\share\my notes.txt', False),
    (r'`C:\my folder\notes.txt`', r'C:\my folder\notes.txt', True),
    (r'``C:\my folder\notes.txt``', r'C:\my folder\notes.txt', True),
    (r'<C:\my folder\notes.txt>', r'C:\my folder\notes.txt', False),
    ('"C:\\my folder\\notes.txt".', r'C:\my folder\notes.txt', False),
    ("'C:\\my folder\\notes.txt'", r'C:\my folder\notes.txt', False),
    (r'[notes](C:\my folder\notes.txt)', r'C:\my folder\notes.txt', False),
    (r'[notes](<C:\my folder\notes.txt>)', r'C:\my folder\notes.txt', False),
    (r'[notes](C:\x\notes.txt "title")', r'C:\x\notes.txt', False),
    ('[notes](file:///C:/my%20folder/notes.txt)', r'C:\my folder\notes.txt', False),
    (r'C:\x\thing_(one).txt).', r'C:\x\thing_(one).txt', False),
    (r'**C:\x\notes.txt**', r'C:\x\notes.txt', False),
    (r'_C:\x\notes.txt_', r'C:\x\notes.txt', False),
    (r'C:\x\notes.txt_', r'C:\x\notes.txt_', False),
    (r'C:\x\notes.txt~', r'C:\x\notes.txt~', False),
    ('C:/', 'C:\\', False),
    ('```text\nC:\\x\\notes.txt\n```', r'C:\x\notes.txt', True),
])
def test_windows_recognition(text, expected, code):
    found = links_in_text(text)
    assert len(found) == 1
    assert found[0][0] == expected and found[0][2] == code


@pytest.mark.parametrize('text', [
    'a/b', '0.6.2', '10:30', '1.2/3.4', 'https://host/a/b',
    'https://host/C:/path.txt', 'mailto:me@example.com', '#heading',
])
def test_no_false_file_references(text, tmp_path):
    assert not [url for url, _, _ in links_in_text(text, str(tmp_path)) if local_path(url)]


@windows_paths
def test_bare_paths_do_not_guess_spaces():
    assert links_in_text(r'C:\my folder\notes.txt')[0][0] == r'C:\my'


def test_relative_paths_use_only_the_known_session_folder(tmp_path):
    text = '`QuickMail/Views/MainWindow.xaml.cs:2687` and [other](src/other.py)'
    assert links_in_text(text) == []
    assert [p for p, _, _ in links_in_text(text, str(tmp_path))] == [
        str(tmp_path / 'QuickMail' / 'Views' / 'MainWindow.xaml.cs'),
        str(tmp_path / 'src' / 'other.py'),
    ]
    assert links_in_text('[readme](README.md)', str(tmp_path))[0][0] == str(tmp_path / 'README.md')


def _link(path):
    return FoundLink(str(path), '', 'Claude', 0, True)


def test_existing_file_actions_and_file_url(tmp_path, monkeypatch):
    path = tmp_path / 'my notes.txt'
    path.write_text('hello')
    opened, revealed = [], []
    monkeypatch.setattr(platform_paths, 'open_url', opened.append)
    monkeypatch.setattr(platform_paths, 'show_in_folder', revealed.append)
    link = _link(path)
    assert link.can_open and link.file_status.can_reveal
    assert 'file' in link.row() and 'in code' in link.row()
    assert link.why_not() == ''
    platform_paths.open_local_file(path.as_uri())
    platform_paths.open_local_file(str(path), containing=True)
    assert opened == [str(path)] and revealed == [path]


def test_missing_file_is_copy_only_and_rechecked(tmp_path, monkeypatch):
    path = tmp_path / 'gone.txt'
    path.write_text('hello')
    link = _link(path)
    assert link.can_open
    path.unlink()
    monkeypatch.setattr(platform_paths, 'open_url', lambda _: pytest.fail('opened a missing file'))
    missing = _link(path)
    assert not missing.can_open and not missing.file_status.can_reveal
    assert 'not found' in missing.row().lower() and 'copy only' in missing.row().lower()
    for containing in (False, True):
        with pytest.raises(ValueError, match='not found'):
            platform_paths.open_local_file(str(path), containing=containing)


@pytest.mark.parametrize('suffix', sorted(BLOCKED_SUFFIXES))
def test_executable_types_can_only_be_revealed(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('unsafe' + suffix.upper())
    path.touch()
    opened, revealed = [], []
    monkeypatch.setattr(platform_paths, 'open_url', opened.append)
    monkeypatch.setattr(platform_paths, 'show_in_folder', revealed.append)
    link = _link(path)
    assert not link.can_open and link.file_status.can_reveal
    assert 'Executable, script or shortcut type' in link.row()
    with pytest.raises(ValueError, match='Executable'):
        platform_paths.open_local_file(str(path))
    platform_paths.open_local_file(str(path), containing=True)
    assert not opened and revealed == [path]


def test_folder_has_only_open_folder_and_copy(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(platform_paths, 'open_local_folder', opened.append)
    status = _link(tmp_path).file_status
    assert not status.can_open and status.can_reveal
    assert status.folder_label == 'Open folder'
    with pytest.raises(ValueError, match='Folder'):
        platform_paths.open_local_file(str(tmp_path))
    platform_paths.open_local_file(str(tmp_path), containing=True)
    assert opened == [str(tmp_path)]


@pytest.mark.parametrize('platform, program', [('darwin', 'osascript'), ('linux', 'xdg-open')])
def test_folder_action_uses_file_manager_not_bundle_default(monkeypatch, platform, program):
    calls = []
    monkeypatch.setattr(platform_paths, 'sys', types.SimpleNamespace(platform=platform))
    monkeypatch.setattr(subprocess, 'Popen', calls.append)
    platform_paths.open_local_folder('/tmp/Example.app')
    assert calls[0][0] == program and calls[0][-1] == '/tmp/Example.app'
    if platform == 'darwin':
        assert 'open folder' in calls[0][2] and '/tmp/Example.app' not in calls[0][2]


def test_symlink_to_script_is_not_opened(tmp_path):
    target = tmp_path / 'script.ps1'
    target.touch()
    link = tmp_path / 'notes.txt'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('creating symlinks is unavailable')
    status = platform_paths.local_file_status(str(link))
    assert not status.can_open and status.can_reveal


@pytest.mark.parametrize('info, blocked', [(b'alis' + bytes(28), True),
                                         (bytes(8) + b'\x80\x00' + bytes(22), True),
                                         (bytes(32), False)])
def test_finder_alias_metadata_blocks_extensionless_shortcuts(tmp_path, monkeypatch, info, blocked):
    path = tmp_path / 'Alias or document'
    path.touch()
    monkeypatch.setattr(platform_paths, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(platform_paths.os, 'getxattr', lambda *args: info, raising=False)
    assert platform_paths.local_file_status(str(path)).can_open == (not blocked)


@windows_paths
@pytest.mark.parametrize('value', [r'C:\x\a.txt:stream', r'\\?\C:\x\a.txt',
                                   r'C:\x\NUL.txt', 'file:///C:/x/a%00.txt',
                                   'file:///C:/x/a%E2%80%AE.txt'])
def test_unsafe_paths_are_never_opened(value):
    status = platform_paths.local_file_status(value)
    assert not status.can_open and not status.can_reveal


def test_single_path_code_block_gets_actions_but_copy_stays_exact(tmp_path):
    path = tmp_path / 'my notes.txt'
    path.touch()
    message = ChatMessage(ASSISTANT, f'```text\n{path}\n```\n```sh\necho hi\n```')
    items = lists.build(lists.CODE, lists.SCOPE_MESSAGE, [message], message, [], [], str,
                        cwd=str(tmp_path))
    assert items[0].link.can_open and items[0].copy_text == str(path)
    assert items[1].link is None


def test_list_relative_link_has_policy_in_row_and_details(tmp_path):
    message = ChatMessage(ASSISTANT, '`src/gone.txt:12`')
    (item,) = lists.build(lists.LINKS, lists.SCOPE_CHAT, [message], message, [], [], str,
                         cwd=str(tmp_path))
    assert item.link.file_path == str(tmp_path / 'src' / 'gone.txt')
    assert 'Copy only' in item.row and 'not found' in item.detail


@pytest.mark.parametrize('wrap', ['{}', '`{}`', '[my notes]({})', '<{}>', '"{}"'])
def test_formatted_file_links_keep_text_and_accessible_state(tmp_path, wrap):
    path = tmp_path / 'my notes.txt'
    path.touch()
    value = path.as_uri()
    page = message_page('Reply', wrap.format(value))
    assert f'href="{html.escape(value, quote=True)}"' in page
    assert 'aria-label="' in page and ', file' in page


@windows_paths
def test_formatted_windows_path_keeps_backslashes_and_space():
    path = r'C:\folder with spaces\_notes.txt'
    page = message_page('Reply', f'`{path}`')
    assert f'>{path}</a>' in page and f'href="{file_url(path)}"' in page
    assert 'not found' in page


def test_formatted_relative_paths_and_full_chat(tmp_path):
    from thechatplace.export import reading_page, to_html
    message = ChatMessage(ASSISTANT, '`src/notes.txt:12`')
    expected = file_url(str(tmp_path / 'src' / 'notes.txt'))
    assert expected in message_page('Reply', message.text, cwd=str(tmp_path))
    assert expected in reading_page('Chat', [message], folder=str(tmp_path))
    # Exported HTML has no TCP handler to enforce the local opening policy.
    assert 'href="file:' not in to_html('Chat', [message], folder=str(tmp_path))


def test_formatted_markup_cannot_be_injected_via_file_label(tmp_path):
    page = message_page('Reply', f'[<script>](<{(tmp_path / "a.txt").as_uri()}>)')
    assert '<script>' not in page and '&lt;script&gt;' in page


@windows_paths
def test_fence_language_path_is_text_in_attributes():
    path = r'C:\x\notes.txt'
    page = message_page('Reply', f'```{path}\nx\n```')
    assert 'aria-label="Code block, C:\\x\\notes.txt, 1 line"' in page
    assert '<a ' not in page and 'TCPLOCAL' not in page


@windows_paths
def test_reference_link_label_does_not_create_nested_file_links():
    page = message_page('Reply', '[C:\\x\\notes.txt][r]\n\n[r]: https://example.com')
    assert page.count('<a ') == 1 and 'href="https://example.com"' in page
    assert 'TCPLOCAL' not in page


@windows_paths
def test_long_file_rows_shorten_backslashes_but_keep_copy_whole():
    path = r'C:\Users\kelly\GitHub\website\The-Idea-Place-Projects\email\quickmail-0.8.74.txt'
    link = _link(path)
    assert len(link.address) <= 70 and link.address.endswith('quickmail-0.8.74.txt')
    assert link.url == path and not link.address.endswith('/…/')


@windows_paths
def test_same_file_path_and_url_list_once():
    from thechatplace.message_links import find_links
    message = ChatMessage(ASSISTANT, 'C:\\x\\notes.txt file:///C:/X/notes.txt')
    assert len(find_links([message])) == 1
