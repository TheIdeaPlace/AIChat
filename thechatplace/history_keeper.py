"""Keep opaque Claude transcripts here before provider cleanup removes them (#438).

Discovery and history readers enqueue work; a single worker copies on this
computer, never back into a provider's home. Metadata survives terminal discovery
losing the original. This is preservation on one disk, not an off-device backup.
"""
from __future__ import annotations

from collections import OrderedDict
import atexit
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

from . import platform_paths
from .provider_storage import atomic_write_json


class HistoryKeeper:
    def __init__(self, root):
        self.root = Path(root)
        self._condition = threading.Condition(threading.RLock())
        self._pending = OrderedDict()
        self._active = False
        self._copy_lock = threading.Lock()
        self._forgotten = set()
        self._closed = False
        self._worker = None
        self._records = {}
        self._doomed = set()       # forgotten while a copy was running
        self.problems = {}
        try:
            for leftover in self.root.glob('*/copy-*.tmp'):
                leftover.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            paths = sorted(self.root.glob('*/record.json'))
            for path in paths:
                try:
                    record = json.loads(path.read_text(encoding='utf-8'))
                    token = self.token(record['identity'])
                    if token != path.parent.name or not isinstance(record['metadata'], dict):
                        raise ValueError('Invalid kept-history record')
                    if self.path(token).is_file():
                        self._records[token] = record
                except (OSError, ValueError, KeyError, TypeError):
                    self.problems[str(path)] = 'A kept-history record could not be read.'
        except OSError:
            self.problems['catalog'] = 'The kept-history folder could not be read.'

    @staticmethod
    def token(identity):
        if (not isinstance(identity, (list, tuple)) or len(identity) != 3
                or any(not isinstance(value, str) or not value for value in identity)):
            raise ValueError('Kept history needs provider, native id and provider home')
        return hashlib.sha256(json.dumps(list(identity), ensure_ascii=True).encode('ascii')).hexdigest()

    def path(self, token):
        # Tokens are computed here; a record cannot select an arbitrary file.
        if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
            raise ValueError('Invalid kept-history token')
        return self.root / token / 'transcript.jsonl'

    def records(self):
        with self._condition:
            return [(token, dict(record['metadata'])) for token, record in self._records.items()]

    def saved(self, identity):
        token = self.token(identity)
        with self._condition:
            return self.path(token) if token in self._records and self.path(token).is_file() else None

    def enqueue(self, identity, source, metadata):
        token = self.token(identity)
        with self._condition:
            if self._closed or token in self._forgotten:
                return
            self._pending[token] = (list(identity), Path(source), dict(metadata))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name='kept-history', daemon=True)
                self._worker.start()
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending or self._closed)
                if self._closed or not self._pending:
                    return
                token, args = self._pending.popitem(last=False)
                self._active = True
            try:
                self.capture(*args)
            except (OSError, ValueError) as error:
                with self._condition:
                    self.problems[token] = f'Could not keep a transcript ({type(error).__name__}).'
            finally:
                self._delete_doomed()
                with self._condition:
                    self._active = False
                    self._condition.notify_all()

    def wait_idle(self, timeout=10):
        with self._condition:
            return self._condition.wait_for(lambda: not self._pending and not self._active, timeout)

    def close(self):
        """Stop: what's queued is dropped (it's copied again next start), and
        a copy under way is the only thing waited for (#451 review)."""
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=10)

    def forget(self, identity):
        """An explicit permanent deletion must not resurrect a kept copy."""
        token = self.token(identity)
        target = self.path(token).parent
        if target.parent.resolve() != self.root.resolve():
            raise ValueError('Invalid kept-history location')
        with self._condition:
            self._forgotten.add(token)
            self._pending.pop(token, None)
            self._records.pop(token, None)
            self.problems.pop(token, None)
            self._doomed.add(token)
        # Deleted now if no copy is running; otherwise by the worker as soon
        # as it finishes, so Delete never freezes the window (#451 review).
        if self._copy_lock.acquire(blocking=False):
            try:
                self._delete_doomed(locked=True)
            finally:
                self._copy_lock.release()

    def _delete_doomed(self, locked=False):
        if not locked:
            with self._copy_lock:
                return self._delete_doomed(locked=True)
        with self._condition:
            doomed, self._doomed = self._doomed, set()
        for token in doomed:
            target = self.path(token).parent
            try:
                if target.exists():
                    shutil.rmtree(target)
            except OSError:
                with self._condition:
                    self._doomed.add(token)   # tried again after the next copy

    @staticmethod
    def _append(source, destination, old_stamp, stat):
        """A transcript usually only grows: then just the new bytes are
        copied, not the whole file again every few seconds (#451 review).
        Anything else (rewritten, shorter, another file, a copy that no
        longer matches what was recorded) takes the full copy below."""
        try:
            old_size, _old_mtime, old_inode = old_stamp
            if (old_inode != stat.st_ino or stat.st_size < old_size
                    or not destination.is_file() or destination.stat().st_size != old_size):
                return False
            return _append_new_bytes(source, destination, old_size, stat.st_size)
        except (TypeError, ValueError, OSError):
            return False

    def capture(self, identity, source, metadata):
        """Copy a bounded prefix from one open file; publish bytes before metadata.

        Appends made during a copy are picked up next pass. Rewrites retain the
        previous copy in versions/, so compaction or a rewind cannot erase it.
        A failed copy never replaces the last successful one.
        """
        with self._copy_lock:
            return self._capture(identity, source, metadata)

    def _capture(self, identity, source, metadata):
        token = self.token(identity)
        if token in self._forgotten:
            return False
        source, destination = Path(source), self.path(token)
        try:
            stat = source.stat()
        except FileNotFoundError:
            with self._condition:
                if token not in self._records:
                    self.problems[token] = 'An original disappeared before its first copy could be kept.'
            return False
        stamp = [stat.st_size, stat.st_mtime_ns, stat.st_ino]
        with self._condition:
            previous = self._records.get(token)
        record = dict(identity=list(identity), metadata=dict(metadata), stamp=stamp)
        if previous and previous.get('stamp') == stamp and destination.is_file():
            if record != previous:
                atomic_write_json(destination.parent/'record.json', record)
                with self._condition:
                    self._records[token] = record
            return False
        if previous and self._append(source, destination, previous.get('stamp'), stat):
            atomic_write_json(destination.parent/'record.json', record)
            with self._condition:
                self._records[token] = record
                self.problems.pop(token, None)
            return True
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='copy-', suffix='.tmp', dir=destination.parent)
        try:
            remaining = stat.st_size
            with os.fdopen(fd, 'wb') as output, source.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                if opened.st_ino != stat.st_ino or opened.st_size < remaining:
                    raise OSError('Transcript changed before copying')
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError('Transcript shrank while copying')
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if destination.is_file() and not _is_prefix(destination, Path(temporary)):
                versions = destination.parent/'versions'
                versions.mkdir(exist_ok=True)
                shutil.copyfile(destination, versions/(uuid.uuid4().hex+'.jsonl'))
            os.replace(temporary, destination)
            atomic_write_json(destination.parent/'record.json', record)
            with self._condition:
                self._records[token] = record
                self.problems.pop(token, None)
            return True
        finally:
            Path(temporary).unlink(missing_ok=True)


TAIL_CHECK = 64 * 1024


def _append_new_bytes(source, destination, old_size, new_size):
    """Append source[old_size:new_size] to destination, after checking the
    copy's last bytes still match the original's (a cheap test that the
    original only grew). False, changing nothing, if they don't."""
    with source.open('rb') as handle, destination.open('rb+') as output:
        start = max(0, old_size - TAIL_CHECK)
        handle.seek(start)
        output.seek(start)
        if handle.read(old_size - start) != output.read(old_size - start):
            return False
        output.seek(old_size)
        output.truncate()
        handle.seek(old_size)
        remaining = new_size - old_size
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise OSError('Transcript shrank while copying')
            output.write(chunk)
            remaining -= len(chunk)
        output.flush()
        os.fsync(output.fileno())
    return True


def _is_prefix(old, new):
    if old.stat().st_size > new.stat().st_size:
        return False
    with old.open('rb') as before, new.open('rb') as after:
        while chunk := before.read(1024 * 1024):
            if after.read(len(chunk)) != chunk:
                return False
    return True


_keepers = {}
_lock = threading.Lock()


_closed_all = False


def keeper():
    root = platform_paths.kept_history_dir()
    with _lock:
        if root not in _keepers:
            _keepers[root] = HistoryKeeper(root)
            if _closed_all:
                # Asked for after shutdown (a refresh still finishing): one
                # that does nothing, never a second worker on the folder.
                _keepers[root]._closed = True
        return _keepers[root]


def close_all(final=False):
    """Close every keeper. ``final`` (the app's shutdown) also means none is
    made again: a refresh finishing late gets one that does nothing."""
    global _closed_all
    with _lock:
        _closed_all = _closed_all or final
        instances = list(_keepers.values())
        _keepers.clear()
    for instance in instances:
        instance.close()


atexit.register(close_all, final=True)
