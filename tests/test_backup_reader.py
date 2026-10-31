"""Synthetic verified archives; no provider executable or wx is needed."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
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
