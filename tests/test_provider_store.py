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


# -- Copilot records have a file of their own (#352) ---------------------------


def copilot_chat(native="copilot-id"):
    return OwnSession(native, "Synthetic Copilot chat", "/synthetic", provider="copilot",
                      started=True)


def test_copilot_records_never_reach_the_files_older_releases_read(tmp_path):
    """Releases that read provider-sessions.json refuse all of it over a
    provider they don't know, so Copilot is kept out of it entirely."""
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    store.add(chat())
    store.add(chat("codex"))
    store.add(copilot_chat())
    codex_file = json.loads((tmp_path / "provider-sessions.json").read_text(encoding="utf-8"))
    assert [item["provider"] for item in codex_file["sessions"]] == ["codex"]
    assert "copilot" not in (tmp_path / "sessions.json").read_text(encoding="utf-8")
    own = json.loads((tmp_path / "provider-sessions-copilot.json").read_text(encoding="utf-8"))
    assert own["version"] == 2 and [item["provider"] for item in own["sessions"]] == ["copilot"]
    again = OwnSessionStore(path)
    assert again.get("copilot-id", "copilot") == store.get("copilot-id", "copilot")
    assert again.get("copilot-id", "copilot").to_info().key == "copilot:copilot-id"
    assert len(again.all(None)) == 3 and not again.load_error


def test_an_older_store_without_a_copilot_file_loads_and_saves_unchanged(tmp_path):
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    store.add(chat())
    store.add(chat("codex"))
    legacy = path.read_bytes()
    codex = (tmp_path / "provider-sessions.json").read_bytes()
    again = OwnSessionStore(path)
    assert not again.load_error and len(again.all(None)) == 2
    again.save()
    assert path.read_bytes() == legacy
    assert (tmp_path / "provider-sessions.json").read_bytes() == codex
    assert not (tmp_path / "provider-sessions-copilot.json").exists()  # no Copilot, no file


def test_removing_the_last_copilot_chat_leaves_an_empty_copilot_file(tmp_path):
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    store.add(copilot_chat())
    store.remove("copilot-id", "copilot")
    own = json.loads((tmp_path / "provider-sessions-copilot.json").read_text(encoding="utf-8"))
    assert own == {"version": 2, "sessions": []}
    assert OwnSessionStore(path).all(None) == []


@pytest.mark.parametrize("raw", ["{not json", json.dumps({"version": 1, "sessions": []}),
                                 json.dumps({"version": 2, "sessions": [asdict(chat("codex"))]}),
                                 json.dumps({"version": 2, "sessions": [None]})])
def test_a_bad_copilot_file_blocks_saving_rather_than_being_overwritten(tmp_path, raw):
    path = tmp_path / "sessions.json"
    own = tmp_path / "provider-sessions-copilot.json"
    own.write_text(raw, encoding="utf-8")
    store = OwnSessionStore(path)
    assert store.load_error
    with pytest.raises(OSError):
        store.add(chat())
    assert own.read_text(encoding="utf-8") == raw


def test_copilot_records_carry_no_claude_or_codex_settings(tmp_path):
    with pytest.raises(ValueError):
        OwnSessionStore(tmp_path / "sessions.json").add(
            OwnSession("x", "t", "/synthetic", provider="copilot", provider_options={"model": "m"}))


def test_a_failed_save_puts_the_copilot_file_back(tmp_path, monkeypatch):
    from thechatplace import provider_storage
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    store.add(copilot_chat("first"))
    before = (tmp_path / "provider-sessions-copilot.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("synthetic failure")
    monkeypatch.setattr(provider_storage.PartitionedFile, "save", fail)
    with pytest.raises(OSError):
        store.add(copilot_chat("second"))
    assert (tmp_path / "provider-sessions-copilot.json").read_bytes() == before


def test_a_copilot_record_in_the_codex_file_is_moved_to_its_own(tmp_path):
    """Written there by some other build: read, and moved out on the next
    save, so releases that read provider-sessions.json can read it again."""
    path = tmp_path / "sessions.json"
    (tmp_path / "provider-sessions.json").write_text(json.dumps(
        {"version": 2, "sessions": [asdict(chat("codex")), asdict(copilot_chat())]}), encoding="utf-8")
    store = OwnSessionStore(path)
    assert not store.load_error and store.get("copilot-id", "copilot") is not None
    store.save()
    codex_file = json.loads((tmp_path / "provider-sessions.json").read_text(encoding="utf-8"))
    assert [item["provider"] for item in codex_file["sessions"]] == ["codex"]
    own = json.loads((tmp_path / "provider-sessions-copilot.json").read_text(encoding="utf-8"))
    assert [item["provider"] for item in own["sessions"]] == ["copilot"]


def test_a_failed_migration_keeps_the_copilot_record_it_moved(tmp_path, monkeypatch):
    """Kelly's review of #372: a migration keeps what it copied out, as
    PartitionedFile keeps provider-sessions.json, so a Copilot record moved out
    of the old file is never left in neither."""
    from thechatplace import provider_storage
    path = tmp_path / "sessions.json"
    # An older mixed store to migrate, and a stray Copilot record in the
    # Codex file from some other build.
    path.write_text(json.dumps({"version": 2, "sessions": [asdict(chat()), asdict(chat("codex"))]}),
                    encoding="utf-8")
    (tmp_path / "provider-sessions.json").write_text(
        json.dumps({"version": 2, "sessions": [asdict(copilot_chat())]}), encoding="utf-8")
    real = provider_storage.PartitionedFile._write

    def legacy_fails(target, payload):
        if target == path:
            raise OSError("synthetic failure")
        real(target, payload)
    monkeypatch.setattr(provider_storage.PartitionedFile, "_write", staticmethod(legacy_fails))
    store = OwnSessionStore(path)
    assert store.load_error  # the migration couldn't finish
    own = json.loads((tmp_path / "provider-sessions-copilot.json").read_text(encoding="utf-8"))
    assert [item["cli_session_id"] for item in own["sessions"]] == ["copilot-id"]


# Fields a newer release writes that this one doesn't know: 0.6 -> 0.5 -> 0.6
# must not lose them when 0.5 saves (marking a chat read, a turn ending), as
# 0.4.1 keeps 0.5's (#611). The names stand in for fields this release lacks;
# Codex's provider_extras mixes a setting this release reads with a later one.
CLAUDE_NEWER = {"later_agent": "reviewer", "later_style": "Explanatory",
                "later_servers": ["github"], "later_flag": True}
CODEX_NEWER = {"provider_extras": {"collaboration": "plan", "later_mode": "pair"}}
COPILOT_NEWER = {"some_later_field": [1, 2]}


def _newer_layout(tmp_path):
    claude = asdict(chat(unread=True))
    claude.pop("provider")
    claude.pop("provider_options")
    (tmp_path / "sessions.json").write_text(json.dumps(
        {"version": 1, "sessions": [{**claude, **CLAUDE_NEWER}]}), encoding="utf-8")
    (tmp_path / "provider-sessions.json").write_text(json.dumps(
        {"version": 2, "sessions": [{**asdict(chat("codex")), **CODEX_NEWER}]}), encoding="utf-8")
    (tmp_path / "provider-sessions-copilot.json").write_text(json.dumps(
        {"version": 2, "sessions": [{**asdict(copilot_chat()), **COPILOT_NEWER}]}), encoding="utf-8")
    return tmp_path / "sessions.json"


def _records(tmp_path, name):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))["sessions"]


def test_saving_keeps_fields_a_newer_release_wrote(tmp_path):
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)
    assert not store.load_error
    store.update("same-id", unread=False)                       # a Claude change
    store.update("same-id", provider="codex", title="Renamed")  # a Codex one
    claude, = _records(tmp_path, "sessions.json")
    assert claude["unread"] is False
    assert {k: claude[k] for k in CLAUDE_NEWER} == CLAUDE_NEWER
    codex, = _records(tmp_path, "provider-sessions.json")
    assert codex["title"] == "Renamed" and codex["provider_extras"] == CODEX_NEWER["provider_extras"]
    copilot, = _records(tmp_path, "provider-sessions-copilot.json")
    assert copilot["some_later_field"] == [1, 2]
    # And they still load, as this release reads them.
    again = OwnSessionStore(path)
    assert not again.load_error and again.get("same-id").unread is False


def test_a_newer_field_never_overrides_what_this_release_knows(tmp_path):
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)
    store.update("same-id", title="Mine")
    claude, = _records(tmp_path, "sessions.json")
    assert claude["title"] == "Mine"
    assert "provider" not in claude and "provider_options" not in claude


def test_a_codex_chats_own_setting_wins_over_a_kept_copy_of_it(tmp_path):
    # The one name both could hold: a known extra beside a later one in
    # provider_extras. What the chat has now is what's written.
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)
    store.update("same-id", provider="codex", provider_options={"collaboration": "default"})
    codex, = _records(tmp_path, "provider-sessions.json")
    assert codex["provider_extras"] == {"collaboration": "default", "later_mode": "pair"}


def test_a_renamed_codex_chat_keeps_its_later_fields(tmp_path):
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)
    store.rename_id("same-id", "new-id", provider="codex")
    codex, = _records(tmp_path, "provider-sessions.json")
    assert codex["cli_session_id"] == "new-id"
    assert codex["provider_extras"] == CODEX_NEWER["provider_extras"]


def test_newer_fields_follow_a_renamed_id_and_go_with_a_removed_chat(tmp_path):
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)
    store.rename_id("same-id", "new-id")
    claude, = _records(tmp_path, "sessions.json")
    assert claude["cli_session_id"] == "new-id" and claude["later_agent"] == "reviewer"
    store.remove("new-id")
    assert _records(tmp_path, "sessions.json") == []
    # A new chat later given the old id carries nothing of the removed one.
    store.add(OwnSession("new-id", "Fresh", "/synthetic"))
    fresh, = _records(tmp_path, "sessions.json")
    assert "later_agent" not in fresh


def test_a_failed_remove_keeps_the_newer_fields(tmp_path, monkeypatch):
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)

    def fail(*args, **kwargs):
        raise OSError("synthetic failure")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(OSError):
        store.remove("same-id")
    monkeypatch.undo()
    store.save()
    claude, = _records(tmp_path, "sessions.json")
    assert claude["later_agent"] == "reviewer"


def test_a_codex_setting_cleared_here_stays_cleared_and_a_later_one_stays(tmp_path):
    # provider_extras is this release's own: what it reads from it is written
    # from the chat as it is now, and only a later setting is kept as it was.
    path = _newer_layout(tmp_path)
    store = OwnSessionStore(path)
    assert store.get("same-id", "codex").provider_options == {"collaboration": "plan"}
    store.update("same-id", provider="codex", provider_options={})
    codex, = _records(tmp_path, "provider-sessions.json")
    assert codex["provider_extras"] == {"later_mode": "pair"}
    store.update("same-id", provider="codex", provider_options={"mcp_servers": ["docs"]})
    codex, = _records(tmp_path, "provider-sessions.json")
    assert codex["provider_extras"] == {"later_mode": "pair", "mcp_servers": ["docs"]}
    assert OwnSessionStore(path).get("same-id", "codex").provider_options == {"mcp_servers": ["docs"]}
