"""Codex messages too long to send, handed over in a temp file (#625).

The whole message is written to a file in the temp folder and Codex is sent
a short prompt asking it to read that file as the message (checked against
real Codex under every sandbox and approval mode the app offers, including
approval-never: it reads without asking). The window deletes the file once
the turn that read it has ended, and any left behind by a closed app are
swept at start, so a long paste (which may hold tokens from a log) never
stays in the temp folder for good.

The original text is remembered in the app's own data, so the chat view can
show the message that was actually written instead of the short prompt, and
an unsent message comes back as it was written. That copy is capped (by
count and by size) and a chat's entries go when the chat is deleted
permanently.

A message is only ever recognized as one of these by the exact prompt the
app sends, in a message of yours, naming a file of the app's own in the
temp folder. Codex's replies and command output can quote anything, so
nothing else is matched, and no file is ever read to fill in a message.
"""
import json
import re
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from . import platform_paths

PREFIX = 'thechatplace-long-message-'
#: How every prompt starts; recognition requires it at the very start.
OPENING = 'This message was placed in this conversation by The Chat Place.'
#: Said in place of "You" in the messages list for such a message.
LABEL = 'You, sent as a file'
_PATH_LINE = re.compile(r'^path: (.+)$', re.MULTILINE)
_KEPT = 20
_KEPT_CHARS = 2_000_000
_lock = threading.Lock()
_store = None


def temp_dir():
    """The folder the files go in: the system's temp folder (tests point
    it elsewhere, so no test touches the real one)."""
    return tempfile.gettempdir()


def save_message(text):
    """The whole message in a temp file; returns its path. On Windows,
    UTF-8 with a byte order mark, so Windows PowerShell 5.1's Get-Content
    (which Codex may use to read it) doesn't garble anything outside ASCII."""
    fd, path = tempfile.mkstemp(prefix=PREFIX, suffix='.txt', dir=temp_dir())
    encoding = 'utf-8-sig' if sys.platform == 'win32' else 'utf-8'
    try:
        with open(fd, 'w', encoding=encoding) as saved:
            saved.write(text)
    except BaseException:
        try:
            Path(path).unlink()
        except OSError:
            pass
        raise
    return path


def prompt_for(text, path):
    """What Codex is sent instead of an over-long message. The Chat Place
    places it, so it says so, rather than speaking as the user."""
    return (f'{OPENING} '
            f'Your message is too long to send directly ({len(text):,} characters), '
            'so it was saved in a temporary file which will be deleted when the '
            f'task is finished.\npath: {path}\n\n'
            "Read that file now, and treat everything in it as the user's "
            'message: do what it asks, and if it shows output or errors, '
            'explain them. '
            "If you can't read the file, say so plainly and stop; don't guess "
            'at what it says.')


def is_own_file(path):
    """A file this module wrote: directly in the temp folder, with its
    prefix. Anything else is never read, remembered or deleted."""
    try:
        candidate = Path(path)
        return (candidate.name.startswith(PREFIX) and candidate.suffix == '.txt'
                and candidate.parent == Path(temp_dir()))
    except (TypeError, ValueError, OSError):
        return False


def saved_path(text):
    """The file a prompt of ours names, or None for any other text."""
    if not isinstance(text, str) or not text.startswith(OPENING):
        return None
    match = _PATH_LINE.search(text)
    if not match:
        return None
    path = match.group(1).strip()
    return path if is_own_file(path) else None


def _store_path():
    # Through the module, so tests' patched app_data_dir is the one used.
    return platform_paths.app_data_dir() / 'long-messages.json'


def _load():
    global _store
    if _store is None:
        try:
            data = json.loads(_store_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        _store = {path: entry for path, entry in data.items()
                  if isinstance(entry, dict) and isinstance(entry.get('text'), str)} \
            if isinstance(data, dict) else {}
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


def reset():
    """Forget what was loaded, so the next use reads the store again (tests)."""
    global _store
    with _lock:
        _store = None


def remember(path, text, chat=''):
    """Keep the original text for a saved message: the newest _KEPT, and
    no more than _KEPT_CHARS in all, oldest dropped first."""
    with _lock:
        store = _load()
        store.pop(path, None)
        store[path] = {'chat': chat, 'text': text}
        while len(store) > 1 and (len(store) > _KEPT or
                                  sum(len(e['text']) for e in store.values()) > _KEPT_CHARS):
            store.pop(next(iter(store)))
        _persist()


def forget_chat(chat):
    """Drop a chat's remembered messages (Delete Session Permanently)."""
    if not chat:
        return
    with _lock:
        store = _load()
        gone = [path for path, entry in store.items() if entry.get('chat') == chat]
        for path in gone:
            del store[path]
        if gone:
            _persist()


def original(text):
    """The message as written, for a prompt of ours whose original is
    remembered; None otherwise."""
    path = saved_path(text)
    if path is None:
        return None
    with _lock:
        entry = _load().get(path)
    return entry['text'] if entry else None


def expand(text):
    """A prompt of ours back to the message as written, where remembered.
    Any other text is returned as it was."""
    found = original(text)
    return text if found is None else found


def expand_messages(messages):
    """Chat history where a message sent as a file reads as you wrote it,
    labeled so it's clear it went as a file. Only your own messages are
    looked at; a message whose original isn't remembered (another
    computer, or dropped by the cap) keeps the prompt that was sent."""
    from .transcript import STEERING, USER
    expanded = []
    for message in messages:
        if message.kind not in (USER, STEERING) or saved_path(message.text) is None:
            expanded.append(message)
            continue
        found = original(message.text)
        expanded.append(replace(message, speaker=LABEL,
                                text=message.text if found is None else found))
    return expanded


def delete_files(paths):
    """Remove saved message files of ours; ones already gone are fine."""
    for path in paths:
        if not is_own_file(path):
            continue
        try:
            Path(path).unlink()
        except OSError:
            pass


def sweep_temp():
    """Delete any saved message files a previous run left behind: their
    turns are over, whether they finished or the app closed first. Run
    once at start, off the UI thread."""
    try:
        delete_files(Path(temp_dir()).glob(PREFIX + '*.txt'))
    except OSError:
        pass
