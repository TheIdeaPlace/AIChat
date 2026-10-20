"""Claude Code's own permission rules for a chat's folder, read only (#336).
Synthetic settings files in tmp_path; the sources and trust rule match what
Claude Code 2.1.296 does (see thechatplace/claude_settings.py)."""
import json

import pytest

from thechatplace import claude_settings as cs
from thechatplace import platform_paths


@pytest.fixture
def places(tmp_path, monkeypatch):
    home, folder = tmp_path / "home" / ".claude", tmp_path / "project"
    (folder / ".claude").mkdir(parents=True)
    home.mkdir(parents=True)
    config = tmp_path / "home" / ".claude.json"
    monkeypatch.setattr(platform_paths, "claude_home", lambda: home)
    monkeypatch.setattr(platform_paths, "claude_config_file", lambda: config)
    monkeypatch.setattr(platform_paths, "claude_managed_settings", lambda: [tmp_path / "managed.json"])
    return {"home": home, "folder": folder, "config": config, "managed": tmp_path / "managed.json"}


def _write(path, permissions):
    path.write_text(json.dumps({"permissions": permissions}), encoding="utf-8")


def _trust(places, trusted=True):
    key = str(places["folder"]).replace("\\", "/")
    places["config"].write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": trusted}}}),
                                encoding="utf-8")


def test_every_source_is_read_in_claude_codes_order(places):
    _write(places["managed"], {"deny": ["WebFetch"]})
    _write(places["home"] / "settings.json", {"allow": ["Bash(npm test)"]})
    _write(places["folder"] / ".claude" / "settings.local.json", {"ask": ["Bash(git push:*)"]})
    _write(places["folder"] / ".claude" / "settings.json", {"allow": ["Read(./docs/**)"]})
    _trust(places)
    rules, unreadable = cs.rules_for(str(places["folder"]))
    assert [(r.kind, r.rule, r.source, r.applied) for r in rules] == [
        ("deny", "WebFetch", "your administrator's policy", True),
        ("allow", "Bash(npm test)", "your settings", True),
        ("allow", "Read(./docs/**)", "this folder's shared settings", True),
        ("ask", "Bash(git push:*)", "this folder's local settings", True)]
    assert unreadable == []


def test_a_shared_files_allow_rules_wait_for_trust_but_its_deny_rules_dont(places):
    _write(places["folder"] / ".claude" / "settings.json",
           {"allow": ["Bash(git add -A)"], "deny": ["Bash(rm:*)"]})
    rules, _ = cs.rules_for(str(places["folder"]))
    assert [(r.kind, r.applied) for r in rules] == [("allow", False), ("deny", True)]
    text = cs.describe(rules)
    assert "Never allowed: Run rm (any arguments). Bash(rm:*). From this folder's shared settings" in text
    assert "Not applied until you trust this folder in Claude Code" in text
    assert text.endswith("Run git add -A. Bash(git add -A).")
    _trust(places)
    assert all(r.applied for r in cs.rules_for(str(places["folder"]))[0])
    _trust(places, trusted=False)
    assert not cs.rules_for(str(places["folder"]))[0][0].applied


def test_a_parent_folders_settings_do_not_count(places):
    _write(places["folder"] / ".claude" / "settings.local.json", {"allow": ["Bash(make)"]})
    sub = places["folder"] / "sub"
    sub.mkdir()
    assert cs.rules_for(str(sub))[0] == []


def test_nothing_extra_unreadable_files_and_odd_entries(places):
    assert cs.describe(*cs.rules_for(str(places["folder"]))) == cs.INTRO + "\n" + cs.NONE
    (places["home"] / "settings.json").write_text("{not json", encoding="utf-8")
    _write(places["folder"] / ".claude" / "settings.local.json",
           {"allow": ["", 7, None, "  Bash(ls)  "], "deny": "not a list"})
    rules, unreadable = cs.rules_for(str(places["folder"]))
    assert [r.rule for r in rules] == ["Bash(ls)"]
    assert unreadable == [str(places["home"] / "settings.json")]
    assert cs.describe(rules, unreadable).endswith(
        f"Couldn't read {places['home'] / 'settings.json'}, so its rules aren't listed.")


def test_trust_is_read_safely(places, monkeypatch):
    assert platform_paths.claude_folder_trusted(str(places["folder"])) is False
    places["config"].write_text("garbage", encoding="utf-8")
    assert platform_paths.claude_folder_trusted(str(places["folder"])) is False
    _trust(places)
    assert platform_paths.claude_folder_trusted(str(places["folder"]) + "/") is True
    assert platform_paths.claude_folder_trusted("") is False
