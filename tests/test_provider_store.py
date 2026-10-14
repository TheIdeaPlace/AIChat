"""Provider identities and lossless legacy ownership migration (#184) (#171)."""
import json
from dataclasses import asdict

import pytest

from thechatplace import platform_paths
from thechatplace.own_store import OwnSession, OwnSessionStore


def chat(provider="claude", **kw):
    return OwnSession("same-id", "Synthetic chat", "/synthetic", provider=provider, **kw)


def test_equal_native_ids_stay_separate_and_legacy_calls_see_only_claude(tmp_path):
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    claude, codex = chat(), chat("codex", provider_options={"model": "synthetic-model"})
    store.add(claude)
    store.add(codex)
    store = OwnSessionStore(path)
    assert len(store.all(None)) == 2
    assert store.all() == [store.get("same-id")]
    assert store.get("same-id").provider == "claude"
    assert store.get("same-id", "codex").provider_options == {"model": "synthetic-model"}
    assert store.get("same-id").to_info().key == "own:same-id"
    assert store.get("same-id", "codex").to_info().key == "codex:same-id"
    store.update("same-id", provider="codex", title="Changed Codex")
    assert store.get("same-id").title == "Synthetic chat"
    store.remove("same-id")
    assert store.contains("same-id", "codex") and not store.contains("same-id")


def test_v1_stays_legacy_readable_without_migration(tmp_path):
    path = tmp_path / "sessions.json"
    item = asdict(chat())
    item.pop("provider")
    item.pop("provider_options")
    original = json.dumps({"version": 1, "sessions": [item]}, indent=3).encode()
    path.write_bytes(original)
    store = OwnSessionStore(path)
    store.save()
    assert path.read_bytes() == original
    assert store.migration_backup is None
    assert OwnSessionStore(path).get("same-id").to_info().key == "own:same-id"


def test_failed_backup_never_replaces_mixed_store(tmp_path, monkeypatch):
    path = tmp_path / "sessions.json"
    original = json.dumps({"version": 2, "sessions": [asdict(chat()), asdict(chat("codex"))]})
    path.write_text(original)

    def fail(path):
        raise OSError("synthetic backup failure")

    monkeypatch.setattr(platform_paths, "backup_session_store", fail)
    store = OwnSessionStore(path)
    assert store.load_error
    with pytest.raises(OSError):
        store.save()
    assert path.read_text() == original


@pytest.mark.parametrize("raw", [
    {"version": 3, "sessions": []},
    {"version": 2, "sessions": [dict(asdict(chat()), provider="future-provider")]},
    {"version": 2, "sessions": [asdict(chat()), asdict(chat())]},
    {"version": 2, "sessions": [asdict(chat()), None]},
    {"version": 2, "sessions": [dict(asdict(chat("codex")), provider_options={"base_url": "other"})]},
])
def test_unsupported_or_invalid_v2_store_is_not_overwritten(tmp_path, raw):
    path = tmp_path / "sessions.json"
    original = json.dumps(raw)
    path.write_text(original)
    store = OwnSessionStore(path)
    assert store.load_error
    with pytest.raises(OSError):
        store.save()
    assert path.read_text() == original


def test_foreign_options_cannot_enter_claude_store_or_change_provider(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(chat())
    with pytest.raises(ValueError):
        store.update("same-id", provider_options={"sandbox": "read-only"})
    assert store.get("same-id").provider_options == {}
    store.update("same-id", provider="codex", title="Unrelated")  # no foreign row
    assert store.get("same-id").title == "Synthetic chat"
    with pytest.raises(ValueError):
        store.add(chat("codex", remote_url="https://synthetic.invalid"))


def test_provider_rename_does_not_overwrite_or_touch_other_agent(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(chat())
    store.add(chat("codex"))
    store.rename_id("same-id", "codex-new", provider="codex")
    assert store.contains("same-id")
    assert store.contains("codex-new", "codex")
    other = chat("codex")
    store.add(other)
    with pytest.raises(ValueError):
        store.rename_id("same-id", "codex-new", provider="codex")
    assert store.contains("same-id", "codex")
    store.rename_id("same-id", "bad/id", provider="codex")
    assert store.contains("same-id", "codex")


def test_verified_workspace_grant_survives_companion_store_roundtrip(tmp_path):
    options = {'sandbox': 'workspace-write', 'approval_policy': 'on-request',
               'workspace_root': str(tmp_path.resolve())}
    path = tmp_path / 'sessions.json'
    store = OwnSessionStore(path)
    store.add(chat('codex', provider_options=options))
    loaded = OwnSessionStore(path)
    assert not loaded.load_error
    assert loaded.get('same-id', 'codex').provider_options == options
    assert loaded.all() == []


def test_corrupt_companion_keeps_valid_claude_chats_and_preserves_files(tmp_path):
    path = tmp_path / 'sessions.json'
    store = OwnSessionStore(path)
    store.add(chat())
    legacy = path.read_bytes()
    companion = tmp_path / 'provider-sessions.json'
    companion.write_bytes(b'{damaged')
    damaged = companion.read_bytes()
    loaded = OwnSessionStore(path)
    assert loaded.get('same-id').title == 'Synthetic chat'
    assert len(loaded.all()) == 1
    assert str(companion) in loaded.load_error
    with pytest.raises(OSError):
        loaded.save()
    assert path.read_bytes() == legacy
    assert companion.read_bytes() == damaged


@pytest.mark.parametrize('filename,key,payload', [
    ('groups.json', 'groups', [{'name': 'Work', 'sessions': ['codex:one']}]),
    ('hidden.json', 'hidden', ['codex:one']),
    ('titles.json', 'titles', {'codex:one': 'Original'})])
def test_second_partition_failure_restores_exact_companion(tmp_path, monkeypatch, filename, key, payload):
    from thechatplace.provider_storage import PartitionedFile
    files = PartitionedFile(tmp_path / filename)
    legacy = {'version': 1, key: []}
    original = {'version': 1, key: payload}
    files.save(legacy, original)
    old_legacy = files.path.read_bytes()
    old_foreign = files.foreign_path.read_bytes()
    write = files._write
    def fail_legacy(path, value):
        if path == files.path:
            raise OSError('synthetic locked legacy file')
        write(path, value)
    monkeypatch.setattr(files, '_write', fail_legacy)
    with pytest.raises(OSError, match='locked'):
        files.save({'changed': True}, {'version': 1, key: []})
    assert files.path.read_bytes() == old_legacy
    assert files.foreign_path.read_bytes() == old_foreign


def test_provider_only_groups_keep_their_position_after_restart(tmp_path):
    from thechatplace.groups import GroupStore
    groups = GroupStore(tmp_path / 'groups.json')
    groups.create('Foreign first')
    groups.add('Foreign first', 'codex:one')
    groups.create('Claude next')
    groups.add('Claude next', 'own:two')
    assert GroupStore(groups.path).names() == ['Foreign first', 'Claude next']


def test_failed_mixed_group_delete_keeps_both_disk_and_memory(tmp_path, monkeypatch):
    from thechatplace.groups import GroupStore
    groups = GroupStore(tmp_path / 'groups.json')
    groups.create('Work')
    groups.add('Work', 'own:one')
    groups.add('Work', 'codex:one')
    write = groups._files._write
    def locked(path, payload):
        if path == groups.path:
            raise OSError('synthetic locked legacy groups')
        write(path, payload)
    monkeypatch.setattr(groups._files, '_write', locked)
    with pytest.raises(OSError):
        groups.delete('Work')
    assert groups.members('Work') == ['own:one', 'codex:one']
    assert GroupStore(groups.path).members('Work') == ['own:one', 'codex:one']


def test_case_variant_companion_group_uses_existing_group_name(tmp_path):
    from thechatplace.groups import GroupStore
    path = tmp_path / 'groups.json'
    path.write_text(json.dumps({'version': 1, 'groups': [{'name': 'Work', 'sessions': ['own:one']}]}))
    path.with_name('provider-groups.json').write_text(json.dumps({'version': 1,
        'groups': [{'name': 'work', 'sessions': ['codex:one']}]}))
    groups = GroupStore(path)
    assert groups.names() == ['Work']
    assert groups.members('Work') == ['own:one', 'codex:one']


def test_conflicting_titles_block_saving_and_preserve_both_files(tmp_path):
    from thechatplace.titles import TitleStore
    path = tmp_path / 'titles.json'
    path.write_text(json.dumps({'version': 1, 'titles': {'codex:one': 'Legacy title'}}))
    companion = path.with_name('provider-titles.json')
    companion.write_text(json.dumps({'version': 1, 'titles': {'codex:one': 'Companion title'}}))
    before = path.read_bytes(), companion.read_bytes()
    store = TitleStore(path)
    assert str(companion) in store.load_error
    with pytest.raises(OSError):
        store.set('own:two', 'Attempted update')
    assert (path.read_bytes(), companion.read_bytes()) == before
