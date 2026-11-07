"""Synthetic verified archives; no provider executable or wx is needed."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

import pytest

from records import user_text, assistant_block, text_block
from test_copilot_provider import answered_turn, ONE
from thechatplace import backup, platform_paths
from thechatplace.backup_reader import BackupReader, ImportedSession
from thechatplace.providers.codex_archive import CodexArchive, ArchiveCodexReader, ArchiveConnection


def archive(files, computer='source-computer', **extra):
    manifest = dict(format=backup.FORMAT, format_version=1, computer={'id': computer},
                    files=[dict(path=name, size=len(data),sha256=hashlib.sha256(data).hexdigest())
                           for name,data in files.items()], **extra)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w') as out:
        for name, data in files.items(): out.writestr(name,data)
        out.writestr('manifest.json',json.dumps(manifest))
    return zipfile.ZipFile(stream), manifest


def claude_bytes():
    return ''.join(json.dumps(record)+'\n' for record in [
        user_text('Invented question',entrypoint='cli',cwd='/old/workspace'),
        assistant_block(text_block('Invented reply'))]).encode()


def test_claude_and_copilot_share_existing_readers_and_never_get_live_keys():
    copilot='copilot/home/session-state/'+ONE+'/'
    files = {'claude/home/projects/old/native.jsonl': claude_bytes(),
             copilot+'workspace.yaml':f'id: {ONE}\nname: Archive title\ncwd: /old/copilot\n'.encode(),
             copilot+'events.jsonl': ''.join(json.dumps(e)+'\n' for e in answered_turn()).encode()}
    zipped, manifest = archive(files)
    with zipped, BackupReader(zipped,manifest) as reader:
        sessions=reader.sessions();assert len(sessions)==2
        by_provider={s.provider:s for s in sessions}
        claude=by_provider['claude']; copilot_session=by_provider['copilot']
        assert claude.identity==('claude','native','source-computer')
        assert claude.key.startswith('imported:') and claude.cwd=='/old/workspace'
        assert reader.read(claude).last_reply().text=='Invented reply'
        assert reader.read(copilot_session).last_reply().text=='Ready'
        assert copilot_session.title=='Archive title'
        assert not hasattr(claude,'is_own') and not hasattr(reader,'send')
        extracted=reader.root
    assert not extracted.exists()
    assert not zipped.fp


def test_reader_never_closes_callers_archive():
    zipped, manifest = archive({})
    with BackupReader(zipped,manifest): pass
    assert zipped.fp is not None
    zipped.close()


def test_same_id_is_distinct_across_providers_and_computers():
    a=ImportedSession('claude','same','one','A')
    b=ImportedSession('codex','same','one','B')
    c=ImportedSession('claude','same','two','C')
    assert len({s.identity for s in (a,b,c)})==3
    assert len({s.key for s in (a,b,c)})==3


@pytest.mark.parametrize('name',['../outside','/absolute','codex/home/C:/bad',
    'claude/home/..\\outside','claude/home/file.','claude/home//file'])
def test_unsafe_archive_paths_are_rejected_before_reading(name):
    zipped, manifest=archive({name:b'x'})
    with zipped, pytest.raises(ValueError, match='Unsafe'):
        BackupReader(zipped,manifest)


def test_case_collisions_and_hash_damage_are_rejected():
    zipped, manifest=archive({'claude/a':b'a','claude/A':b'b'})
    with zipped, pytest.raises(ValueError,match='Colliding'):
        BackupReader(zipped,manifest)
    zipped, manifest=archive({'claude/a':b'a'})
    manifest['files'][0]['sha256']='0'*64
    with zipped, pytest.raises(ValueError,match='checksum'):
        BackupReader(zipped,manifest)


def test_identity_is_required_and_read_checks_membership():
    zipped, manifest=archive({})
    manifest['computer']={}
    with pytest.raises(ValueError,match='identity'): BackupReader(zipped,manifest)
    manifest['computer']={'id':'one'}
    with zipped, BackupReader(zipped,manifest) as reader:
        with pytest.raises(ValueError,match='belong'):
            reader.read(ImportedSession('claude','not-present','one','A'))


def test_missing_native_codex_is_explicit_and_other_history_remains(monkeypatch):
    from thechatplace.providers import codex_archive
    def unavailable(*args,**kwargs): raise OSError('not installed')
    monkeypatch.setattr(codex_archive,'ArchiveConnection',unavailable)
    zipped,manifest=archive({'claude/home/projects/p/c.jsonl':claude_bytes(),
                             'codex/home/sessions/a.jsonl':b'opaque'})
    with zipped, BackupReader(zipped,manifest) as reader:
        assert [s.provider for s in reader.sessions()]==['claude']
        assert any('Codex history could not be listed' in note for note in reader.notes)


def test_retained_claude_history_is_read_without_native_original():
    record=dict(identity=['claude','kept','old-home'],metadata={'title':'Kept title','cwd':'/old'})
    base='thechatplace/data/kept-history/claude/token/'
    zipped,manifest=archive({base+'record.json':json.dumps(record).encode(),
                             base+'transcript.jsonl':claude_bytes()})
    with zipped, BackupReader(zipped,manifest) as reader:
        session,=reader.sessions()
        assert session.native_id=='kept' and session.title=='Kept title'
        assert reader.read(session).last_reply().text=='Invented reply'


def test_codex_copy_relocates_only_known_index_paths_and_omits_execution_stores(tmp_path):
    home=tmp_path/'codex/home';home.mkdir(parents=True)
    session=home/'sessions/day/opaque.jsonl';session.parent.mkdir(parents=True);session.write_bytes(b'opaque')
    source=home/'state_5.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('create table threads(id text,rollout_path text,cwd text)')
        db.executemany('insert into threads values(?,?,?)',[
            ('one','C:\\old\\.codex\\sessions\\day\\opaque.jsonl','C:\\old\\work'),
            ('outside','C:\\outside.jsonl','C:\\outside')])
    for name in ('auth.json','config.toml','queue_1.sqlite','goals_1.sqlite'):
        (home/name).write_text('must never load')
    class Connection:
        def __init__(self,**kwargs): self.closed=False
        def close(self): self.closed=True
    manifest={'providers':[{'id':'codex','areas':[{'name':'codex/home','root':'C:\\old\\.codex'}]}]}
    codex=CodexArchive(tmp_path,manifest,connection_factory=Connection)
    try:
        assert not (codex.profile/'auth.json').exists()
        assert not (codex.profile/'queue_1.sqlite').exists()
        assert not (codex.profile/'goals_1.sqlite').exists()
        assert 'must never load' not in (codex.profile/'config.toml').read_text()
        with sqlite3.connect(codex.profile/source.name) as db:
            row,=db.execute('select id,rollout_path,cwd from threads').fetchall()
        assert row[0]=='one' and Path(row[1]).is_relative_to(codex.profile)
        assert Path(row[2]).is_relative_to(codex.profile.parent)
        with sqlite3.connect(source) as db:
            assert len(db.execute('select * from threads').fetchall())==2
        assert codex.notes
    finally: codex.close()


def test_archive_environment_has_no_live_home_credentials_or_overrides(tmp_path):
    profile=tmp_path/'profile';profile.mkdir()
    environment=platform_paths.codex_archive_environment(profile,{
        'HOME':'real-home','CODEX_HOME':'real-codex','CODEX_SQLITE_HOME':'real-db',
        'OPENAI_API_KEY':'secret','CODEX_AUTH_TOKEN':'secret','CODEX_CONFIG':'real-config',
        'PATH':'native-executable-path','APPDATA':'real-appdata'})
    assert environment['PATH']=='native-executable-path'
    assert not any('KEY' in k or 'TOKEN' in k for k in environment)
    assert environment['CODEX_HOME']==str(profile.resolve())
    for key in ('HOME','USERPROFILE','APPDATA','XDG_CONFIG_HOME'):
        assert Path(environment[key]).is_relative_to(tmp_path)


def test_legacy_codex_pages_use_full_turns_without_item_api():
    class Connection:
        def __init__(self): self.calls=[]
        def request(self,method,params):
            self.calls.append((method,params))
            if method=='thread/read': return {'thread':{'id':'one','historyMode':'legacy','updatedAt':1}}
            assert method=='thread/turns/list' and params['itemsView']=='full'
            return {'data':[{'id':'turn','items':[{'id':'answer','type':'agentMessage','text':'Recovered'}]}]}
    connection=Connection();reader=ArchiveCodexReader(connection,'one')
    reader.refresh()
    assert reader.transcript.last_reply().text=='Recovered'
    assert {method for method,_ in connection.calls}=={'thread/read','thread/turns/list'}
    assert ArchiveConnection.METHODS=={'thread/list','thread/read','thread/turns/list','thread/items/list'}


def test_cancelled_extraction_cleans_temporary_directory():
    zipped,manifest=archive({'claude/a':b'data'})
    with zipped, pytest.raises(InterruptedError):
        BackupReader(zipped,manifest,cancelled=lambda:True)


def test_saved_names_keep_old_presentation_key_without_importing_ownership():
    from thechatplace.own_store import OwnSession
    from dataclasses import asdict
    record=asdict(OwnSession('native','My saved title','/old/work',created_ms=100))
    zipped, manifest=archive({'claude/home/projects/p/native.jsonl':claude_bytes(),
        'thechatplace/data/sessions.json':json.dumps({'version':1,'sessions':[record]}).encode()})
    with zipped, BackupReader(zipped,manifest) as reader:
        session,=reader.sessions()
        assert session.title=='My saved title' and session.source_key=='own:native'
        assert session.key.startswith('imported:') and not hasattr(session,'is_own')
        assert reader.data_dir.is_relative_to(reader.root)


def test_paginated_codex_uses_bounded_item_and_turn_pages():
    class Connection:
        def __init__(self):self.calls=[]
        def request(self,method,params):
            self.calls.append((method,params))
            if method=='thread/read':return {'thread':{'id':'one','historyMode':'paginated'}}
            if method=='thread/turns/list':
                assert params['itemsView']=='notLoaded'
                return {'data':[{'id':'t','itemsView':'notLoaded'}]}
            if method=='thread/items/list':
                return {'data':[{'turnId':'t','item':{'id':'a','type':'agentMessage','text':'Paged'}}]}
            raise AssertionError(method)
    connection=Connection();reader=ArchiveCodexReader(connection,'one');reader.refresh()
    assert reader.transcript.last_reply().text=='Paged'
    assert all(params.get('limit',0)<=100 for method,params in connection.calls)


def test_aes_archive_handle_works_without_reader_password_storage():
    pyzipper=pytest.importorskip('pyzipper')
    content=claude_bytes();name='claude/home/projects/p/encrypted.jsonl'
    manifest=dict(format=backup.FORMAT,format_version=1,computer={'id':'encrypted-machine'},
        files=[dict(path=name,size=len(content),sha256=hashlib.sha256(content).hexdigest())])
    stream=io.BytesIO()
    with pyzipper.AESZipFile(stream,'w',encryption=pyzipper.WZ_AES) as out:
        out.setpassword(b'synthetic-passphrase');out.writestr(name,content)
        out.writestr('manifest.json',json.dumps(manifest))
    with pyzipper.AESZipFile(stream) as zipped:
        zipped.setpassword(b'synthetic-passphrase')
        with BackupReader(zipped,manifest) as reader:
            session,=reader.sessions()
            assert reader.read(session).last_reply().text=='Invented reply'


def test_extraction_preserves_manifest_utc_timestamp():
    from datetime import datetime
    zipped,manifest=archive({'claude/home/projects/p/native.jsonl':claude_bytes()})
    moment='2026-10-01T12:34:56+00:00'
    manifest['files'][0]['modified']=moment
    with zipped, BackupReader(zipped,manifest) as reader:
        path=reader.root/'claude/home/projects/p/native.jsonl'
        assert path.stat().st_mtime==datetime.fromisoformat(moment).timestamp()


def test_file_import_does_not_consult_live_provider_or_tcp_folders(monkeypatch):
    def forbidden(): raise AssertionError('Consulted a live data folder')
    for name in ('claude_home','projects_dir','copilot_home','copilot_sessions_dir','app_data_dir'):
        monkeypatch.setattr(platform_paths,name,forbidden)
    copilot='copilot/home/session-state/'+ONE+'/'
    zipped,manifest=archive({'claude/home/projects/p/native.jsonl':claude_bytes(),
        copilot+'workspace.yaml':f'id: {ONE}\nname: Archived\n'.encode(),
        copilot+'events.jsonl':''.join(json.dumps(e)+'\n' for e in answered_turn()).encode()})
    with zipped, BackupReader(zipped,manifest) as reader:
        for session in reader.sessions():
            assert reader.read(session).messages


def _closing_reader(temporary, root, codex=None):
    from thechatplace import backup_reader
    reader = object.__new__(backup_reader.BackupReader)
    reader._closed, reader._codex, reader._temporary = False, codex, temporary
    reader.root, reader.left_behind = root, False
    return reader


def test_a_scratch_folder_that_wont_go_at_once_doesnt_fail_the_restore(tmp_path, monkeypatch):
    """Codex can still be writing to its scratch profile as it dies, and
    Windows says the folder isn't empty (WinError 145): the chats are saved."""
    from thechatplace import backup_reader
    monkeypatch.setattr(backup_reader, '_SCRATCH_WAIT', 0)
    tries = []

    class Stubborn:
        def __init__(self, fails):
            self.fails = fails

        def cleanup(self):
            tries.append(1)
            if len(tries) <= self.fails:
                raise OSError(145, 'The directory is not empty')

    root = tmp_path / 'busy'
    root.mkdir()
    removed = []

    def stays_for_three(folder):
        removed.append(folder)
        if len(removed) >= 3:
            root.rmdir()
    monkeypatch.setattr(backup_reader, '_remove_tree', stays_for_three)
    reader = _closing_reader(Stubborn(10 ** 6), root)
    reader.close()
    assert len(tries) == 3 and not reader.left_behind
    monkeypatch.undo()
    monkeypatch.setattr(backup_reader, '_SCRATCH_WAIT', 0)
    tries.clear()
    root = tmp_path / 'scratch'
    (root / 'chats').mkdir(parents=True)
    (root / 'chats' / 'one.jsonl').write_text('{}')
    reader = _closing_reader(Stubborn(10 ** 6), root)
    reader.close()  # never raises, however long it refuses
    assert len(tries) == 1  # the long-form delete got it, with no waiting
    assert not root.exists() and not reader.left_behind


@pytest.mark.skipif(sys.platform != 'win32', reason="only Windows refuses a file in use")
def test_a_file_in_use_keeps_only_itself_and_the_restore_says_so(tmp_path, monkeypatch):
    """A real temporary folder: cleanup stops at the first error, which would
    leave the chats that sort after it."""
    import tempfile
    from pathlib import Path
    from thechatplace import backup_reader
    monkeypatch.setattr(backup_reader, '_SCRATCH_WAIT', 0)
    monkeypatch.setattr(backup_reader, '_SCRATCH_TRIES', 2)
    monkeypatch.setattr(backup_reader.tempfile, 'gettempdir', lambda: str(tmp_path))
    temporary = tempfile.TemporaryDirectory(prefix=backup_reader._SCRATCH_PREFIX, dir=tmp_path)
    root = Path(temporary.name)
    held, chat = root / 'a' / 'held', root / 'z' / 'sessions' / 'c.jsonl'
    held.parent.mkdir()
    chat.parent.mkdir(parents=True)
    chat.write_text('{}')
    reader = _closing_reader(temporary, root)
    with open(held, 'w', encoding='utf-8'):
        reader.close()
    assert reader.left_behind and held.exists() and not chat.exists()
    backup_reader.remove_leftover_scratch(older_than=0)  # as the app's next start does
    assert not root.exists()


def test_a_failure_closing_codex_doesnt_fail_a_restore_whose_chats_are_saved(tmp_path):
    """It would throw away the outcome, and with it the groups and hidden state
    that a second restore wouldn't give chats already here."""
    removed = []

    class Codex:
        def close(self):
            raise RuntimeError('Codex process cleanup failed')

    temporary = type('T', (), {'cleanup': lambda self: removed.append(1)})()
    _closing_reader(temporary, tmp_path / 'gone', Codex()).close()
    assert removed


def test_old_scratch_copies_go_and_a_running_restores_stays(tmp_path, monkeypatch):
    import os
    import stat
    import time
    from thechatplace import backup_reader
    monkeypatch.setattr(backup_reader.tempfile, 'gettempdir', lambda: str(tmp_path))
    old, new, other = (tmp_path / 'tcp-restore-reader-old', tmp_path / 'tcp-restore-reader-new',
                       tmp_path / 'something-else')
    for folder in (old, new, other):
        (folder / 'chats').mkdir(parents=True)
        (folder / 'chats' / 'one.jsonl').write_text('{}')
    # Git's packs, from a plugin clone, are read-only.
    pack = old / 'chats' / 'pack'
    pack.write_text('x')
    pack.chmod(stat.S_IREAD)
    long_ago = time.time() - 2 * backup_reader._SCRATCH_STALE_SECONDS
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    backup_reader.remove_leftover_scratch()
    assert not old.exists() and new.exists() and other.exists()
    backup_reader.remove_leftover_scratch(older_than=0)
    assert not new.exists() and other.exists()


def test_reading_a_backups_codex_chats_turns_off_its_plugins(tmp_path):
    """The scratch Codex fetched plugins into its profile, online, during a
    restore, and that's what raced deleting it."""
    from thechatplace.providers.codex_archive import CodexArchive
    archive = CodexArchive(tmp_path, {}, connection_factory=lambda start: None)
    settings = (archive.profile / 'config.toml').read_text(encoding='utf-8')
    assert '[features]' in settings and 'plugins = false' in settings and 'apps = false' in settings


def test_a_restore_says_when_some_of_the_backups_files_stayed():
    from thechatplace.imported_store import RestoreOutcome, restore_summary
    outcome = RestoreOutcome({"added": 1}, left_behind="C:/temp/x")
    assert "couldn't be deleted from C:/temp/x" in restore_summary(outcome)
    assert "deleted" not in restore_summary(RestoreOutcome({"added": 1}))


@pytest.mark.skipif(sys.platform != 'win32', reason="Windows' 260-character limit")
def test_a_path_too_long_for_a_plain_delete_still_goes(tmp_path, monkeypatch):
    """Found in the VM: a plugin Codex fetched into the scratch profile had
    paths of 260 to 275 characters, and retrying could never remove them."""
    import os
    from thechatplace import backup_reader, platform_paths
    monkeypatch.setattr(backup_reader, '_SCRATCH_WAIT', 0)
    monkeypatch.setattr(backup_reader.tempfile, 'gettempdir', lambda: str(tmp_path))
    root = tmp_path / (backup_reader._SCRATCH_PREFIX + 'long')
    deep = str(platform_paths.long_path(root / '/'.join(['d' * 40] * 8)))
    os.makedirs(deep)
    with open(os.path.join(deep, 'file.md'), 'w', encoding='utf-8') as out:
        out.write('x')
    assert len(deep) > 300
    backup_reader.remove_leftover_scratch(older_than=0)
    assert not root.exists()


def _archive_with(path, name, content):
    entry = {'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name, content)
    return {'format': 'thechatplace-backup', 'format_version': 1,
            'computer': {'id': 'source', 'name': 'Source'}, 'files': [entry], 'providers': []}


@pytest.mark.skipif(sys.platform != 'win32', reason="Windows' 260-character limit")
def test_a_real_backups_long_claude_project_path_is_restored_and_removed(tmp_path):
    """Kelly's real backup: Claude names a project's folder after its whole path,
    which took one file to 282 characters and failed every restore."""
    import os
    from thechatplace.backup_reader import BackupReader
    from thechatplace.transcript import ASSISTANT
    project = 'C--Users-kelly-' + '-'.join(['scratch-workspaces-e2e89692'] * 7)
    native = '11111111-2222-3333-4444-555555555555'
    name = f'claude/home/projects/{project}/{native}.jsonl'
    line = {'type': 'assistant', 'uuid': 'a1', 'sessionId': native, 'cwd': 'C:/work',
            'timestamp': '2026-11-01T00:00:00.000Z',
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Synthetic reply'}]}}
    content = (json.dumps(line) + '\n').encode()
    manifest = _archive_with(tmp_path / 'b.zip', name, content)
    with zipfile.ZipFile(tmp_path / 'b.zip') as archive, BackupReader(archive, manifest) as reader:
        root = reader.root
        path = root.joinpath(*name.split('/'))
        # CI's runners have long paths turned on, where a plain path works too,
        # and most PCs don't: so the form is checked as well as the reading.
        assert str(root).startswith('\\\\?\\') and not str(reader.plain_root).startswith('\\\\')
        assert len(str(path)) > 260 and path.read_bytes() == content
        assert [s.native_id for s in reader.sessions()] == [native]
    assert not os.path.lexists(root)


def test_codex_gets_the_plain_folder_and_the_outcome_says_the_plain_one(tmp_path, monkeypatch):
    from thechatplace import backup as backup_module, backup_reader, imported_store
    from thechatplace.providers import codex_archive
    name = 'codex/home/sessions/rollout.jsonl'
    manifest = _archive_with(tmp_path / 'b.zip', name, b'{}\n')
    given = []

    class Archive:
        notes = []

        def __init__(self, root, _manifest):
            given.append(root)

        def sessions(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(codex_archive, 'CodexArchive', Archive)
    with zipfile.ZipFile(tmp_path / 'b.zip') as archive, backup_reader.BackupReader(archive, manifest) as reader:
        reader.sessions()
        assert given == [reader.plain_root] and not str(given[0]).startswith('\\\\')

    monkeypatch.setattr(backup_reader.BackupReader, '_remove_scratch',
                        lambda self: setattr(self, 'left_behind', True))
    monkeypatch.setattr(backup_module, 'opened', _opened_as_is)
    outcome = imported_store.restore_backup(tmp_path / 'b.zip', manifest, None, confirm=lambda *a: True,
                                            store=imported_store.ImportedStore(tmp_path / 'store'))
    assert outcome.left_behind and not outcome.left_behind.startswith('\\\\')


def _opened_as_is(path, _passphrase, _cancelled=None):
    from contextlib import nullcontext
    return nullcontext(path)


def _scratch_link(target, link):
    import os
    import subprocess
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True,
                       capture_output=True)
    else:
        os.symlink(target, link)


def test_a_link_named_like_a_leftover_is_left_and_what_it_points_at_survives(tmp_path, monkeypatch):
    """A review reproduced this: a junction with the name had its target deleted,
    at every start of the app. Anyone can make one in a shared temp folder."""
    from thechatplace import backup_reader
    temp = tmp_path / 'temp'
    temp.mkdir()
    precious = tmp_path / 'precious'
    precious.mkdir()
    (precious / 'keep.txt').write_text('keep', encoding='utf-8')
    link = temp / (backup_reader._SCRATCH_PREFIX + 'planted')
    _scratch_link(precious, link)
    real = temp / (backup_reader._SCRATCH_PREFIX + 'real')
    real.mkdir()
    monkeypatch.setattr(backup_reader.tempfile, 'gettempdir', lambda: str(temp))
    backup_reader.remove_leftover_scratch(older_than=0)
    assert (precious / 'keep.txt').read_text(encoding='utf-8') == 'keep'
    assert not real.exists()


@pytest.mark.parametrize('fresh_area,stale_area', [(0, 1), (1, 0)])
def test_a_cowork_chat_in_two_folders_restores_the_newer_copy_whole(fresh_area, stale_area):
    # #542: the title came from one copy and the text from the other.
    def copy(area, title, activity, words):
        base = f'claude/desktop-cowork-{area}/acct/org/local_c'
        history = ''.join(json.dumps(record)+'\n' for record in [
            user_text('Invented question', cwd='/old/workspace'),
            assistant_block(text_block(words))]).encode()
        return {base+'.json': json.dumps(dict(
                    sessionId='local_c', cliSessionId='cowork-chat', cwd='/old/workspace',
                    title=title, lastActivityAt=activity)).encode(),
                base+'/.claude/projects/enc/cowork-chat.jsonl': history}
    files = {**copy(fresh_area, 'Newer title', 2000, 'Newer words'),
             **copy(stale_area, 'Older title', 1000, 'Older words')}
    zipped, manifest = archive(files)
    with zipped, BackupReader(zipped, manifest) as reader:
        chat, = reader.sessions()
        assert chat.title == 'Newer title'
        assert reader.read(chat).last_reply().text == 'Newer words'
        assert not [note for note in reader.notes if 'Duplicate' in note]
