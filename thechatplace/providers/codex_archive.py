"""Ask native Codex to read an isolated recovery copy, never parse rollouts (#436)."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import shutil
import sqlite3

from .. import platform_paths
from .codex import CodexProvider, CodexReader
from .codex_rpc import ReadOnlyCodexConnection


class ArchiveConnection(ReadOnlyCodexConnection):
    METHODS = frozenset({'thread/list', 'thread/read', 'thread/turns/list', 'thread/items/list'})


class ArchiveCodexReader(CodexReader):
    def _paginated_thread(self, thread):
        # Legacy stores support bounded full-turn pages, not item pagination.
        # Paginated stores reject full turn items and use the normal converter.
        if thread.get('historyMode', 'legacy') == 'legacy':
            turns = list(self._pages('thread/turns/list', itemsView='full'))
            return {**thread, 'turns': turns, 'historyMode': 'legacy'}
        return super()._paginated_thread(thread)


class CodexArchive:
    def __init__(self, extracted, manifest, *, connection_factory=None):
        self.root = Path(extracted)
        self.profile = self.root/'codex-reader/profile'
        self.notes = []
        self.connection = None
        self._original_cwds = {}
        try:
            self._prepare(manifest)
            factory = connection_factory or ArchiveConnection
            self.connection = factory(start=lambda executable: platform_paths.start_codex_archive_server(
                self.profile, executable))
        except BaseException:
            self.close()
            raise

    def _prepare(self, manifest):
        self.profile.mkdir(parents=True)
        home = self.root/'codex/home'
        for name in ('sessions', 'archived_sessions'):
            source = home/name
            if source.is_dir():
                shutil.copytree(source, self.profile/name)
        index = home/'session_index.jsonl'
        if index.is_file():
            shutil.copyfile(index, self.profile/index.name)
        # Deliberately omit config, auth, plugins, skills, goals and queues.
        # Nothing in an imported archive may install hooks or resume work.
        (self.profile/'config.toml').write_text('cli_auth_credentials_store = "file"\n', encoding='utf-8')
        databases = {}
        for folder in (home, self.root/'codex/sqlite-home'):
            if not folder.is_dir():
                continue
            for file in folder.iterdir():
                if file.is_file() and re.fullmatch(r'(?:state|thread_history)_[0-9]+\.sqlite', file.name):
                    if file.name in databases:
                        raise ValueError('Ambiguous Codex database copies')
                    databases[file.name] = file
        roots = []
        for provider in manifest.get('providers', []):
            if isinstance(provider, dict) and provider.get('id') == 'codex':
                roots.extend(area['root'] for area in provider.get('areas', [])
                             if isinstance(area, dict) and area.get('name') == 'codex/home'
                             and isinstance(area.get('root'), str))
        for name, source in databases.items():
            target = self.profile/name
            shutil.copyfile(source, target)
            if name.startswith('state_'):
                self._relocate_state(target, roots)

    def _relocate_state(self, path, roots):
        """Change path metadata only, in our copied index.

        CODEX_HOME alone is insufficient: threads.rollout_path contains absolute
        paths to the original machine. Unknown schemas fail closed. An unmapped
        row is removed from this index; native discovery can rebuild only from
        files present inside this copy.
        """
        with closing(sqlite3.connect(path)) as db:
            db.execute('pragma trusted_schema=OFF')
            # FTS-maintenance triggers are normal in Codex's index. Remove
            # triggers only from this disposable copy before changing paths:
            # imported SQL must not rewrite a path back outside scratch.
            for (name,) in db.execute("select name from sqlite_master where type='trigger'").fetchall():
                db.execute('drop trigger "' + name.replace('"', '""') + '"')
            columns = {row[1] for row in db.execute('pragma table_info(threads)')}
            if not {'id', 'rollout_path', 'cwd'} <= columns:
                raise ValueError('Unsupported Codex thread index')
            for native_id, old_path, cwd in db.execute('select id, rollout_path, cwd from threads').fetchall():
                mapped = self._mapped_path(old_path, roots)
                if mapped is None:
                    db.execute('delete from threads where id=?', (native_id,))
                    self.notes.append('A Codex index entry had no transcript in the backup and was omitted.')
                    continue
                self._original_cwds[native_id] = cwd
                db.execute('update threads set rollout_path=?, cwd=? where id=?',
                           (str(mapped), str(self.profile.parent/'workspace'), native_id))
            db.commit()

    def _mapped_path(self, old, roots):
        if not isinstance(old, str):
            return None
        normalized = old.replace('\\', '/')
        for root in roots:
            prefix = root.replace('\\','/').rstrip('/')+'/'
            if normalized.casefold().startswith(prefix.casefold()):
                relative = normalized[len(prefix):]
                parts = relative.split('/')
                if (parts[0] not in ('sessions','archived_sessions') or any(
                        part in ('','.','..') or ':' in part for part in parts)):
                    continue
                candidate = self.profile.joinpath(*parts)
                if candidate.is_file() and candidate.resolve().is_relative_to(self.profile.resolve()):
                    return candidate
        return None

    def sessions(self):
        snapshot = CodexProvider(self.connection).collect()
        for info in snapshot.sessions:
            if info.cli_session_id in self._original_cwds:
                info.cwd = self._original_cwds[info.cli_session_id]
        return snapshot.sessions

    def reader(self, native_id):
        return ArchiveCodexReader(self.connection, native_id)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
