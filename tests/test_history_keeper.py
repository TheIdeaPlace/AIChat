"""Invented history only: preservation never modifies Claude's source files."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from records import user_text, assistant_block, text_block
from thechatplace import history_keeper, platform_paths
from thechatplace.history_keeper import HistoryKeeper
from thechatplace.providers.claude import (ClaudeConversations, kept_identity,
                                          keep_session, preserve_sessions)
from thechatplace.sessions import IDLE, KEPT, OWN, TERMINAL, SessionInfo


def transcript(path, text='Kept answer'):
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [user_text('Synthetic question'), assistant_block(text_block(text))]
    path.write_text(''.join(json.dumps(r)+'\n' for r in records), encoding='utf-8')
    return path


def session(tmp_path, monkeypatch, source=TERMINAL):
    home = tmp_path/'claude'
    monkeypatch.setattr(platform_paths, 'claude_home', lambda: home)
    cwd = str(tmp_path/'work')
    info = SessionInfo(source, 'terminal:synthetic', 'Synthetic title', cwd, 'synthetic',
                       last_activity_ms=123, created_ms=100)
    path = transcript(platform_paths.direct_transcript_path(cwd, 'synthetic'))
    return info, path


def test_discovery_keeps_metadata_and_lists_deleted_terminal_after_restart(tmp_path, monkeypatch):
    info, path = session(tmp_path, monkeypatch)
    before = path.read_bytes()
    assert preserve_sessions([info]) == [info]
    assert history_keeper.keeper().wait_idle()
    assert path.read_bytes() == before
    path.unlink()
    history_keeper.close_all()
    kept, = preserve_sessions([])
    assert kept.key == info.key and kept.title == info.title
    assert (kept.created_ms, kept.last_activity_ms) == (100, 123)
    assert kept.source == KEPT and kept.state == IDLE and not kept.is_own
    assert kept.kind_label == 'kept chat (read-only)'
    reader = ClaudeConversations().open_reader(kept)
    reader.refresh()
    assert reader.transcript.last_reply().text == 'Kept answer'


def test_formerly_owned_chat_never_gets_execution_capabilities(tmp_path, monkeypatch):
    info, path = session(tmp_path, monkeypatch, OWN)
    keep_session(info)
    assert history_keeper.keeper().wait_idle()
    path.unlink()
    kept, = preserve_sessions([info])
    caps = ClaudeConversations().capabilities(kept)
    assert not kept.is_own
    assert caps.read and caps.usage
    for name in ('send', 'fork', 'delete', 'attachments', 'remote_control',
                 'desktop_link', 'share_link', 'options', 'approvals', 'steer'):
        assert not getattr(caps, name), name


def test_open_reader_falls_back_when_cleanup_happens_during_read(tmp_path, monkeypatch):
    info, path = session(tmp_path, monkeypatch)
    reader = ClaudeConversations().open_reader(info)
    reader.refresh()
    assert history_keeper.keeper().wait_idle()
    saved = history_keeper.keeper().saved(kept_identity(info))
    path.unlink()
    reader.refresh()
    assert reader.path == saved
    assert reader.transcript.last_reply().text == 'Kept answer'


def test_after_turn_append_and_rewrite_preserve_previous_bytes(tmp_path):
    source = tmp_path/'native.jsonl'
    source.write_bytes(b'first\n')
    store = HistoryKeeper(tmp_path/'kept')
    identity = ('claude', 'one', 'home')
    assert store.capture(identity, source, {'title': 'First'})
    source.write_bytes(b'first\nsecond\n')
    assert store.capture(identity, source, {'title': 'Renamed'})
    saved = store.saved(identity)
    assert saved.read_bytes() == source.read_bytes()
    assert not (saved.parent/'versions').exists()
    source.write_bytes(b'rewound\n')
    store.capture(identity, source, {'title': 'Renamed'})
    assert saved.read_bytes() == b'rewound\n'
    version, = (saved.parent/'versions').glob('*.jsonl')
    assert version.read_bytes() == b'first\nsecond\n'
    assert HistoryKeeper(store.root).records()[0][1]['title'] == 'Renamed'


def test_disappearing_source_never_replaces_a_good_copy(tmp_path):
    source = tmp_path/'native.jsonl'; source.write_bytes(b'answer\n')
    store = HistoryKeeper(tmp_path/'kept'); identity = ('claude','one','home')
    store.capture(identity, source, {})
    source.unlink()
    assert not store.capture(identity, source, {})
    assert store.saved(identity).read_bytes() == b'answer\n'


def test_copy_failure_keeps_previous_bytes_and_is_reported(tmp_path, monkeypatch):
    source = tmp_path/'native.jsonl'; source.write_bytes(b'answer\n')
    store = HistoryKeeper(tmp_path/'kept'); identity = ('claude','one','home')
    store.capture(identity, source, {})
    source.write_bytes(b'changed\n')
    def fail(*args): raise PermissionError('synthetic failure')
    monkeypatch.setattr(history_keeper.os, 'replace', fail)
    store.enqueue(identity, source, {})
    assert store.wait_idle()
    assert store.problems
    assert store.saved(identity).read_bytes() == b'answer\n'
    store.close()


def test_same_native_id_in_two_homes_never_shares_bytes(tmp_path):
    store = HistoryKeeper(tmp_path/'kept')
    for home, text in [('home', b'one'), ('cowork', b'two')]:
        source = tmp_path/(home+'.jsonl'); source.write_bytes(text)
        store.capture(('claude','same',home), source, {})
    assert store.saved(('claude','same','home')).read_bytes() == b'one'
    assert store.saved(('claude','same','cowork')).read_bytes() == b'two'


def test_foreign_provider_never_queues_claude_history(tmp_path):
    info = SessionInfo(OWN, 'codex:x', 'Codex', '', 'x', provider='codex')
    keep_session(info)
    assert not history_keeper.keeper().records()
    assert not platform_paths.kept_history_dir().exists()


def test_corrupt_catalog_does_not_select_external_files(tmp_path):
    root = tmp_path/'kept'; fake = root/('0'*64); fake.mkdir(parents=True)
    (fake/'record.json').write_text(json.dumps(dict(identity=['claude','one','home'],
                                                 metadata={},path=str(tmp_path/'outside'))))
    store = HistoryKeeper(root)
    assert not store.records() and store.problems


def test_backups_include_kept_history(tmp_path, monkeypatch):
    from thechatplace import backup
    monkeypatch.setattr(platform_paths, 'app_data_dir', lambda: tmp_path/'tcp')
    store = HistoryKeeper(platform_paths.app_data_dir()/'kept-history/claude')
    source = tmp_path/'original'; source.write_bytes(b'kept bytes\n')
    store.capture(('claude','one','home'), source, {})
    inv = backup.tcp_inventory()
    assert any(item.arcname.endswith('/transcript.jsonl') for item in inv.items)


def test_permanent_deletion_removes_kept_copy_and_pending_capture(tmp_path):
    source = tmp_path/'native'; source.write_bytes(b'answer')
    store = HistoryKeeper(tmp_path/'kept'); identity = ('claude','one','home')
    store.capture(identity, source, {})
    store.forget(identity)
    store.enqueue(identity, source, {})
    assert store.wait_idle() and not store.saved(identity) and not store.records()
    assert not list(store.root.glob('*/record.json'))


def test_retention_reports_global_values_and_invalid_config(tmp_path, monkeypatch):
    from thechatplace.retention import describe
    monkeypatch.setattr(platform_paths, 'claude_home', lambda: tmp_path)
    monkeypatch.setattr(platform_paths, 'claude_managed_settings', lambda: [])
    assert '30 days' in describe()
    settings = tmp_path/'settings.json'
    settings.write_text('{"cleanupPeriodDays": 90}')
    assert '90 days' in describe()
    settings.write_text('{"cleanupPeriodDays": false}')
    assert 'could not be checked' in describe()


def test_managed_retention_overrides_user_settings(tmp_path, monkeypatch):
    from thechatplace.retention import describe
    monkeypatch.setattr(platform_paths, 'claude_home', lambda: tmp_path)
    (tmp_path/'settings.json').write_text('{"cleanupPeriodDays": 90}')
    policy = tmp_path/'managed.json'; policy.write_text('{"cleanupPeriodDays": 7}')
    monkeypatch.setattr(platform_paths, 'claude_managed_settings', lambda: [policy])
    assert '7 days' in describe()


def test_cleanup_before_opening_uses_copy_without_waiting_for_discovery(tmp_path, monkeypatch):
    info, path = session(tmp_path, monkeypatch, OWN)
    keep_session(info); assert history_keeper.keeper().wait_idle()
    path.unlink()
    adapter = ClaudeConversations()
    assert adapter.has_history(info) and info.source == KEPT
    reader = adapter.open_reader(info); reader.refresh()
    assert reader.transcript.last_reply().text == 'Kept answer'
    assert adapter.last_reply(info) == 'Kept answer'
    assert not adapter.capabilities(info).send


def test_temporary_missing_file_during_live_turn_does_not_remove_stop(tmp_path, monkeypatch):
    from thechatplace.sessions import WORKING
    info,path = session(tmp_path,monkeypatch,OWN)
    keep_session(info); assert history_keeper.keeper().wait_idle()
    info.state = WORKING
    path.unlink()
    current, = preserve_sessions([info])
    assert current.is_own and current.state == WORKING
    reader=ClaudeConversations().open_reader(current);reader.refresh()
    assert reader.transcript.last_reply().text == 'Kept answer'
    assert current.is_own
