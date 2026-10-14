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
