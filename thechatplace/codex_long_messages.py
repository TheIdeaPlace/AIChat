"""Codex messages too long to send, handed over in a temp file.

The whole message is written to a file in the temp folder and Codex is sent
a short prompt asking it to read that file as the message (checked against
real Codex under every sandbox and approval mode the app offers, including
approval-never: it reads without asking). The file is deleted when the turn
ends, and any left behind by a closed app are swept at start, so a long
paste (which may hold tokens from a log) never stays on disk for good. The
original text is remembered here, in the app's own data, so the chat view
can show the message that was actually written instead of the short prompt,
and an unsent message comes back as it was written.
"""
import json
import re
from dataclasses import replace
import tempfile
import threading
from pathlib import Path

from .platform_paths import app_data_dir

PREFIX = 'thechatplace-long-message-'
_PATTERN = re.compile(
    r'^My message is too long to send directly \(([\d,]+) characters\), so '
    r'The Chat Place saved it in full in this file: (.+)$', re.MULTILINE)
_KEPT = 50
_lock = threading.Lock()
_store = None


def save_message(text):
    """The whole message in a temp file; returns its path."""
    fd, path = tempfile.mkstemp(prefix=PREFIX, suffix='.txt')
    try:
        with open(fd, 'w', encoding='utf-8') as saved:
            saved.write(text)
    except BaseException:
        try:
            Path(path).unlink()
        except OSError:
            pass
        raise
    return path


def prompt_for(text, path):
    """What Codex is sent instead of an over-long message."""
    return (f'My message is too long to send directly ({len(text):,} characters), so '
            f'The Chat Place saved it in full in this file: {path}\n\n'
            'Read that file now, and treat everything in it as my message: do what '
            'it asks, and if it shows output or errors, explain them. '
            "If you can't read the file, say so plainly and stop; don't guess "
            'at what it says.')


def _store_path():
    return app_data_dir() / 'long-messages.json'


def _load():
    global _store
    if _store is None:
        try:
            data = json.loads(_store_path().read_text(encoding='utf-8'))
            _store = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _store = {}
    return _store


def _persist():
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(_store), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        pass  # remembering is for display; never block a send on it


def remember(path, text):
    """Keep the original text for a saved message, newest _KEPT only."""
    with _lock:
        store = _load()
        store.pop(path, None)
        store[path] = text
        while len(store) > _KEPT:
            store.pop(next(iter(store)))
        _persist()


def expand(text):
    """A saved message's prompt, back to the message itself, where the
    original is remembered (or the file is still there to read). Anything
    else is returned as it was."""
    if not isinstance(text, str):
        return text
    match = _PATTERN.search(text)
    if not match:
        return text
    path = match.group(2).strip()
    with _lock:
        original = _load().get(path)
    if original is not None:
        return original
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError:
        return text


def expand_messages(messages):
    """Chat history with saved messages shown as they were written."""
    expanded = []
    for message in messages:
        text = expand(message.text)
        expanded.append(replace(message, text=text) if text != message.text else message)
    return expanded


def delete_files(paths):
    """Remove saved message files; ones already gone are fine."""
    for path in paths:
        try:
            Path(path).unlink()
        except OSError:
            pass


def sweep_temp():
    """Delete any saved message files a previous run left behind: their
    turns are over, whether they finished or the app closed first."""
    try:
        leftovers = Path(tempfile.gettempdir()).glob(PREFIX + '*.txt')
        delete_files(leftovers)
    except OSError:
        pass
