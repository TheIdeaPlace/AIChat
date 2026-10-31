"""Read a verified backup as imported history, without provider ownership (#436).

The caller owns an open ZipFile-compatible archive, sets its password if needed,
and passes backup.verify's successful manifest. Files are checked again while
extracting, so swapping the archive between preview and import cannot bypass hashes.
Readers and their temporary native profile live only until close().
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import os
import time
from pathlib import Path, PurePosixPath
import stat
import sqlite3
import tempfile

from . import backup, platform_paths
from .transcript import TranscriptReader, SessionFactsCache


_DEVICES = {'CON','PRN','AUX','NUL','CONIN$','CONOUT$',
            *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}


@dataclass(frozen=True)
class ImportedSession:
    provider: str
    native_id: str
    computer_id: str
    title: str
    cwd: str = ''
    created_ms: int = 0
    last_activity_ms: int = 0
    archived: bool = False
    source_key: str = ''

    @property
    def identity(self):
        return self.provider, self.native_id, self.computer_id

    @property
    def key(self):
        encoded = json.dumps(self.identity, ensure_ascii=True).encode('ascii')
        return 'imported:' + hashlib.sha256(encoded).hexdigest()


class BackupReader:
    def __init__(self, archive, manifest, *, cancelled=None):
        if (not isinstance(manifest, dict) or manifest.get('format') != backup.FORMAT
                or manifest.get('format_version') != backup.FORMAT_VERSION):
            raise ValueError('Unsupported backup manifest')
        computer = manifest.get('computer')
        if (not isinstance(computer, dict) or not isinstance(computer.get('id'), str)
                or not computer['id'] or len(computer['id']) > 1024):
            raise ValueError('The backup has no source-computer identity')
        self._cancelled = cancelled or (lambda: False)
        self.computer_id = computer['id']
        self.notes = []
        self._archive, self.manifest = archive, manifest
        self._temporary = tempfile.TemporaryDirectory(prefix='tcp-restore-reader-')
        self.root = Path(self._temporary.name)
        self._sessions = None
        self._readers = {}
        self._codex = None
        self._closed = False
        try:
            self._extract()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *exc):
        self.close()

    def _check_open(self):
        if self._closed:
            raise ValueError('The backup reader is closed')

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._codex is not None:
                self._codex.close()
        finally:
            self._temporary.cleanup()

    def _extract(self):
        files = self.manifest.get('files')
        if not isinstance(files, list):
            raise ValueError('Invalid backup file list')
        members = {}
        for info in self._archive.infolist():
            if info.filename in members:
                raise ValueError('Duplicate archive member')
            members[info.filename] = info
        listed, folded = set(), set()
        unusable = self.manifest.get('unusable') or []
        for entry in files:
            if not isinstance(entry, dict):
                raise ValueError('Invalid backup file entry')
            if self._cancelled():
                raise InterruptedError('Restore cancelled')
            name = entry.get('path')
            if not isinstance(name, str):
                raise ValueError('Invalid backup path')
            parts = PurePosixPath(name).parts
            if (not parts or name != '/'.join(parts) or name.startswith('/')
                    or any(part in ('.','..') or ':' in part or '\\' in part
                           or '\x00' in part or part.rstrip(' .') != part
                           or part.split('.',1)[0].upper() in _DEVICES for part in parts)):
                raise ValueError('Unsafe backup path')
            if name in listed or name.casefold() in folded:
                raise ValueError('Colliding backup paths')
            listed.add(name); folded.add(name.casefold())
            info = members.get(name)
            expected_size = entry.get('size')
            digest = entry.get('sha256')
            if (info is None or info.is_dir() or stat.S_ISLNK(info.external_attr >> 16)
                    or type(expected_size) is not int or expected_size < 0
                    or info.file_size != expected_size
                    or not isinstance(digest, str) or len(digest) != 64):
                raise ValueError('Invalid backup file metadata')
            if name in unusable:
                self.notes.append('A file marked unusable was skipped: '+name)
                continue
            destination = self.root.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            checksum, copied = hashlib.sha256(), 0
            with self._archive.open(info) as source, destination.open('xb') as output:
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > expected_size:
                        raise ValueError('Backup file exceeds its declared size')
                    if self._cancelled():
                        raise InterruptedError('Restore cancelled')
                    checksum.update(chunk); output.write(chunk)
            if copied != expected_size or checksum.hexdigest() != digest:
                raise ValueError('Backup file checksum does not match')
            try:
                modified = datetime.fromisoformat(entry.get('modified') or '')
                if modified.tzinfo is None:
                    raise ValueError('Missing timezone')
                stamp = modified.timestamp()
            except (ValueError, TypeError, OverflowError):
                stamp = time.mktime((*info.date_time, 0, 0, -1))
            os.utime(destination, (stamp, stamp))
        if set(members) - listed - {backup.MANIFEST}:
            raise ValueError('Archive contains unlisted files')
        self._files = listed - set(unusable)

    @property
    def data_dir(self):
        self._check_open()
        return self.root/'thechatplace/data'

    def sessions(self):
        self._check_open()
        if self._sessions is None:
            found = []
            found.extend(self._claude_sessions())
            found.extend(self._copilot_sessions())
            found.extend(self._codex_sessions())
            from .own_store import OwnSessionStore
            from .titles import TitleStore
            own = OwnSessionStore(self.data_dir/'sessions.json')
            own_by_id = {record.identity: record for record in own.all(provider=None)}
            titles = TitleStore(self.data_dir/'titles.json')
            if own.load_error or titles.load_error:
                self.notes.append('Some saved Chat Place names or session metadata could not be read.')
            by_identity = {}
            for session in found:
                record = own_by_id.get((session.provider, session.native_id))
                if record is not None:
                    # Old ownership is used solely to recover a title and
                    # presentation key, never turned into imported ownership.
                    session = replace(session, title=record.title,
                                      source_key=record.to_info().key,
                                      created_ms=record.created_ms or session.created_ms)
                elif session.source_key in titles:
                    session = replace(session, title=titles.get(session.source_key))
                previous = by_identity.get(session.identity)
                if previous is not None:
                    # Never silently choose between differing histories with
                    # the same provider/native id/source-computer identity.
                    self.notes.append('Duplicate archived identity: '+session.provider+'/'+session.native_id)
                    continue
                by_identity[session.identity] = session
            self._sessions = list(by_identity.values())
        return list(self._sessions)

    def read(self, session):
        self._check_open()
        if not isinstance(session, ImportedSession) or session not in self.sessions():
            raise ValueError('This session does not belong to the backup')
        reader = self._readers[session.identity]
        reader.refresh()
        return reader.transcript

    def release(self, session):
        """Forget a chat's parsed history once it's been kept elsewhere, so a
        large backup's histories aren't all in memory at once."""
        self._readers.pop(getattr(session, "identity", None), None)

    def _claude_sessions(self):
        facts_cache = SessionFactsCache()
        from .sessions import load_desktop_sessions
        desktop = {}
        for folder in (self.root/'claude').glob('desktop-*'):
            if not folder.is_dir():
                continue
            snapshot = load_desktop_sessions(
                directory=(self.root/'unused-code') if folder.name.startswith('desktop-cowork-') else folder,
                cowork_directory=folder if folder.name.startswith('desktop-cowork-') else None,
                include_archived=True, alive=lambda pid: False)
            desktop.update({info.cli_session_id: info for info in snapshot.sessions})
        results = []
        for name in sorted(self._files):
            parts = PurePosixPath(name).parts
            native_id = None
            # Top-level transcripts only: subagent logs are not separate chats.
            if (parts[:3] == ('claude','home','projects') and len(parts) == 5
                    and parts[-1].endswith('.jsonl')):
                native_id = Path(parts[-1]).stem
            elif (len(parts) >= 6 and parts[0] == 'claude'
                  and parts[1].startswith('desktop-cowork-')
                  and parts[-3] == 'projects' and parts[-1].endswith('.jsonl')):
                native_id = Path(parts[-1]).stem
            if native_id is None or not platform_paths.is_safe_id(native_id):
                continue
            path = self.root.joinpath(*parts)
            facts = facts_cache.get(path)
            if facts is None:
                self.notes.append('A Claude transcript could not be read: '+name)
                continue
            session = ImportedSession('claude', native_id, self.computer_id,
                                      facts.title or 'Untitled session', facts.cwd,
                                      facts.started_ms, facts.modified_ms,
                                      source_key='terminal:'+native_id)
            if native_id in desktop:
                info = desktop[native_id]
                session = replace(session, title=info.title, cwd=info.cwd,
                                  archived=info.archived, source_key=info.key,
                                  created_ms=info.created_ms or session.created_ms,
                                  last_activity_ms=info.last_activity_ms or session.last_activity_ms)
            self._readers.setdefault(session.identity, TranscriptReader(path))
            results.append(session)
        # Copies kept by #438 may be the only surviving Claude history.
        for name in sorted(self._files):
            parts = PurePosixPath(name).parts
            if (parts[:4] != ('thechatplace','data','kept-history','claude')
                    or len(parts) != 6 or parts[-1] != 'record.json'):
                continue
            transcript_name = '/'.join(parts[:-1]+('transcript.jsonl',))
            if transcript_name not in self._files:
                continue
            try:
                record = json.loads(self.root.joinpath(*parts).read_text(encoding='utf-8'))
                identity, metadata = record['identity'], record['metadata']
                if identity[0] != 'claude' or not platform_paths.is_safe_id(identity[1]):
                    raise ValueError('Invalid identity')
                session = ImportedSession('claude', identity[1], self.computer_id,
                    str(metadata.get('title') or 'Untitled session'), str(metadata.get('cwd') or ''),
                    int(metadata.get('created_ms') or 0), int(metadata.get('last_activity_ms') or 0),
                    bool(metadata.get('archived')), str(metadata.get('key') or 'terminal:'+identity[1]))
                if session.identity not in self._readers:
                    self._readers[session.identity] = TranscriptReader(self.root/transcript_name)
                    results.append(session)
            except (ValueError, TypeError, KeyError, IndexError):
                self.notes.append('A kept Claude record could not be read: '+name)
        return results

    def _copilot_sessions(self):
        from .providers.copilot import CopilotProvider
        root = self.root/'copilot/home/session-state'
        if not root.is_dir():
            return []
        provider = CopilotProvider(sessions_dir=root)
        snapshot = provider.collect()
        results = []
        if snapshot.unreadable_files:
            self.notes.append(f'{snapshot.unreadable_files} Copilot session folders could not be read.')
        for info in snapshot.sessions:
            session = self._from_info(info)
            reader = provider.open_reader(info)
            if reader is not None:
                self._readers[session.identity] = reader
                results.append(session)
        return results

    def _codex_sessions(self):
        if not any(name.startswith(('codex/home/sessions/', 'codex/home/archived_sessions/',
                                    'codex/home/state_', 'codex/sqlite-home/state_'))
                   for name in self._files):
            return []
        from .providers.codex_archive import CodexArchive
        try:
            self._codex = CodexArchive(self.root, self.manifest)
            result = []
            for info in self._codex.sessions():
                session = self._from_info(info)
                self._readers[session.identity] = self._codex.reader(info.cli_session_id)
                result.append(session)
            self.notes.extend(self._codex.notes)
            return result
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            if self._codex is not None:
                self._codex.close()
                self._codex = None
            self.notes.append('Codex history could not be listed. Install a compatible native Codex '
                              'CLI and try again; other providers can still be imported.')
            return []

    def _from_info(self, info):
        return ImportedSession(info.provider, info.cli_session_id, self.computer_id,
                               info.title, info.cwd, info.created_ms, info.last_activity_ms,
                               info.archived, info.key)
