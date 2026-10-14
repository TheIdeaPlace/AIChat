"""Run the actual 0.1.6 persistence code against shared app data (#184)."""
import json
from dataclasses import asdict

import pytest

from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.groups import GroupStore
from thechatplace.hidden import HiddenStore, ToolActivityStore
from thechatplace.titles import TitleStore
from tests.legacy_016_own_store import OwnSessionStore as OldOwnStore
from tests.legacy_016_groups import GroupStore as OldGroups
from tests.legacy_016_hidden import HiddenStore as OldHidden, ToolActivityStore as OldActivity
from tests.legacy_016_titles import TitleStore as OldTitles


def test_016_roundtrip_cannot_see_or_relabel_foreign_owned_sessions(tmp_path):
    path = tmp_path / 'sessions.json'
    store = OwnSessionStore(path)
    claude = OwnSession('same-id', 'Claude', '/synthetic', unread=True)
    codex = OwnSession('same-id', 'Codex', '/synthetic', provider='codex',
                       unread=True, started=False,
                       provider_options={'model': 'synthetic', 'sandbox': 'read-only',
                                         'approval_policy': 'on-request', 'effort': 'high'})
    store.add(claude)
    store.add(codex)
    expected = asdict(codex)
    raw = json.loads(path.read_text())
    assert raw['version'] == 1
    assert len(raw['sessions']) == 1
    assert 'provider' not in raw['sessions'][0]
    old = OldOwnStore(path)
    assert [s.title for s in old.all()] == ['Claude']
    old.update('same-id', title='Edited in 0.1.6', unread=False)
    old.save()
    fresh = OwnSessionStore(path)
    assert asdict(fresh.get('same-id', 'codex')) == expected
    assert fresh.get('same-id').title == 'Edited in 0.1.6'
    # An existing source instance saving only Codex data must not undo the
    # installed app's newer Claude edits.
    store.update('same-id', provider='codex', title='Edited Codex')
    assert OldOwnStore(path).get('same-id').title == 'Edited in 0.1.6'
    old.remove('same-id')
    fresh = OwnSessionStore(path)
    assert fresh.all() == []
    assert [s.provider for s in fresh.all(None)] == ['codex']


def test_experimental_mixed_v2_is_separated_on_load_with_exact_backup(tmp_path):
    path = tmp_path / 'sessions.json'
    codex = OwnSession('same-id', 'Codex', '/synthetic', provider='codex', unread=True)
    original = json.dumps({'version': 2, 'sessions': [asdict(codex)]}).encode()
    path.write_bytes(original)
    new = OwnSessionStore(path)
    assert not new.load_error
    assert new.migration_backup.read_bytes() == original
    assert OldOwnStore(path).all() == []
    OldOwnStore(path).save()
    assert OwnSessionStore(path).get('same-id', 'codex') == codex


@pytest.mark.parametrize('new_cls,old_cls,filename,field', [
    (HiddenStore, OldHidden, 'hidden.json', 'hidden'),
    (ToolActivityStore, OldActivity, 'tool_activity.json', 'show_activity'),
])
def test_016_key_preferences_roundtrip(new_cls, old_cls, filename, field, tmp_path):
    path = tmp_path / filename
    new = new_cls(path)
    new.add('own:same-id')
    new.add('codex:same-id')
    assert json.loads(path.read_text())[field] == ['own:same-id']
    old = old_cls(path)
    assert old.keys() == ['own:same-id']
    old.discard('own:same-id')
    old.add('terminal:another')
    new.rename_key('codex:same-id', 'codex:renamed')
    assert old_cls(path).keys() == ['terminal:another']
    assert new_cls(path).keys() == ['codex:renamed', 'terminal:another']


def test_016_titles_roundtrip(tmp_path):
    path = tmp_path / 'titles.json'
    new = TitleStore(path)
    new.set('local_same-id', 'Claude name')
    new.set('codex:same-id', 'Codex name')
    old = OldTitles(path)
    assert old.get('codex:same-id') == ''
    old.set('local_same-id', 'Older app rename')
    new.set('codex:same-id', 'New Codex name')
    assert OldTitles(path).get('local_same-id') == 'Older app rename'
    assert TitleStore(path).get('codex:same-id') == 'New Codex name'


def test_016_deleting_shared_group_preserves_foreign_membership(tmp_path):
    path = tmp_path / 'groups.json'
    new = GroupStore(path)
    new.create('Shared group')
    new.add('Shared group', 'own:same-id')
    new.add('Shared group', 'codex:same-id')
    old = OldGroups(path)
    assert old.members('Shared group') == ['own:same-id']
    old.delete('Shared group')
    restored = GroupStore(path)
    assert restored.members('Shared group') == ['codex:same-id']
    restored.rename('Shared group', 'Codex group')
    assert OldGroups(path).names() == []
    assert GroupStore(path).members('Codex group') == ['codex:same-id']


@pytest.mark.parametrize('cls,filename,payload', [
    (HiddenStore, 'hidden.json', {'version': 1, 'hidden': ['codex:one']}),
    (ToolActivityStore, 'tool_activity.json', {'version': 1, 'show_activity': ['codex:one']}),
    (TitleStore, 'titles.json', {'version': 1, 'titles': {'codex:one': 'Name'}}),
    (GroupStore, 'groups.json', {'version': 1, 'groups': [{'name': 'Mixed', 'sessions': ['codex:one']}]}),
])
def test_existing_foreign_metadata_is_evacuated_on_load(cls, filename, payload, tmp_path):
    path = tmp_path / filename
    path.write_text(json.dumps(payload))
    store = cls(path)
    assert not store.load_error
    assert 'codex:' not in path.read_text()
    assert 'codex:' in path.with_name('provider-' + filename).read_text()


@pytest.mark.parametrize('cls,filename', [
    (OwnSessionStore, 'sessions.json'), (HiddenStore, 'hidden.json'),
    (ToolActivityStore, 'tool_activity.json'), (TitleStore, 'titles.json'),
    (GroupStore, 'groups.json'),
])
def test_bad_foreign_file_is_never_overwritten(cls, filename, tmp_path):
    path = tmp_path / filename
    foreign = path.with_name('provider-' + filename)
    foreign.write_text('{broken')
    store = cls(path)
    assert store.load_error
    with pytest.raises(OSError):
        if cls is OwnSessionStore:
            store.add(OwnSession('one', 'Claude', '/synthetic'))
        elif cls is GroupStore:
            store.create('Group')
        elif cls is TitleStore:
            store.set('codex:one', 'Title')
        else:
            store.add('codex:one')
    assert foreign.read_text() == '{broken'


def test_corrupt_legacy_ownership_does_not_erase_valid_codex_file(tmp_path):
    path = tmp_path / 'sessions.json'
    store = OwnSessionStore(path)
    codex = OwnSession('one', 'Codex', '/synthetic', provider='codex')
    store.add(codex)
    path.write_text('{broken')
    recovered = OwnSessionStore(path)
    assert recovered.load_error
    assert recovered.get('one', 'codex') == codex
    recovered.save()
    assert OwnSessionStore(path).get('one', 'codex') == codex


def test_removing_last_foreign_group_member_keeps_empty_group_private(tmp_path):
    path = tmp_path / 'groups.json'
    new = GroupStore(path)
    new.create('Group')
    new.add('Group', 'codex:one')
    OldGroups(path).delete('Group')
    new = GroupStore(path)
    new.remove('Group', 'codex:one')
    assert OldGroups(path).names() == []
    assert GroupStore(path).names() == ['Group']


def test_interrupted_split_preserves_original_and_can_retry(tmp_path, monkeypatch):
    from thechatplace.provider_storage import PartitionedFile
    path = tmp_path / 'sessions.json'
    codex = OwnSession('one', 'Codex', '/synthetic', provider='codex')
    original = json.dumps({'version': 2, 'sessions': [asdict(codex)]})
    path.write_text(original)
    write = PartitionedFile._write

    def fail_legacy(target, payload):
        if target == path:
            raise OSError('synthetic interrupted legacy replace')
        write(target, payload)

    with monkeypatch.context() as patch:
        patch.setattr(PartitionedFile, '_write', staticmethod(fail_legacy))
        failed = OwnSessionStore(path)
        assert failed.load_error
        assert path.read_text() == original
        assert json.loads(path.with_name('provider-sessions.json').read_text())['sessions']
    recovered = OwnSessionStore(path)
    assert not recovered.load_error
    assert recovered.get('one', 'codex') == codex
    assert OldOwnStore(path).all() == []


def test_future_backend_metadata_is_not_written_to_legacy_file(tmp_path):
    path = tmp_path / 'hidden.json'
    store = HiddenStore(path)
    store.add('future-agent:one')
    assert OldHidden(path).keys() == []
    assert HiddenStore(path).keys() == ['future-agent:one']
