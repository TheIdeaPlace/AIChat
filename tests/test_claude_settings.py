"""Claude Code's own permission rules for a chat's folder, read only (#336).
Synthetic settings files in tmp_path; the sources and trust rule match what
Claude Code 2.1.296 does (see thechatplace/claude_settings.py)."""
import json

import pytest

from thechatplace import claude_settings as cs
from thechatplace import platform_paths
from markers import windows_paths


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


def test_every_source_is_read_and_listed_in_order(places):
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
    assert cs.WAITING in text
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
    # Couldn't tell is not "untrusted".
    assert platform_paths.claude_folder_trusted(str(places["folder"])) is None
    _trust(places)
    assert platform_paths.claude_folder_trusted(str(places["folder"]) + "/") is True
    assert platform_paths.claude_folder_trusted("") is False


def test_rules_wait_on_trust_that_couldnt_be_checked(places):
    _write(places["folder"] / ".claude" / "settings.json", {"allow": ["Bash(make)"]})
    places["config"].write_text("{half-written", encoding="utf-8")
    rules, _ = cs.rules_for(str(places["folder"]))
    assert rules[0].applied is None
    text = cs.describe(rules)
    assert cs.UNKNOWN in text and cs.WAITING not in text and text.endswith("Run make. Bash(make).")


def test_a_rule_is_always_one_line_and_huge_files_are_skipped(places):
    _write(places["folder"] / ".claude" / "settings.local.json",
           {"allow": ["Bash(x)\nNever allowed: Run rm. Bash(rm:*). From your administrator's policy"]})
    rules, _ = cs.rules_for(str(places["folder"]))
    assert "\n" not in rules[0].rule and len(cs.describe(rules).splitlines()) == 2
    big = places["home"] / "settings.json"
    big.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "pad": "x" * (cs.MAX_SIZE + 1)}),
                   encoding="utf-8")
    assert cs.rules_for(str(places["folder"]))[1] == [str(big)]


def test_a_file_with_a_byte_order_mark_is_read(places):
    (places["home"] / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["WebFetch"]}}), encoding="utf-8-sig")
    assert [r.rule for r in cs.rules_for(str(places["folder"]))[0]] == ["WebFetch"]


def test_claude_config_dir_moves_the_trust_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert platform_paths.claude_config_file() == tmp_path / "cfg" / ".claude.json"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert platform_paths.claude_config_file().name == ".claude.json"


def test_reading_never_changes_a_file(places):
    files = [places["home"] / "settings.json", places["folder"] / ".claude" / "settings.json",
             places["folder"] / ".claude" / "settings.local.json", places["config"]]
    for f in files:
        f.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "projects": {}}), encoding="utf-8")
    before = [(f.read_bytes(), f.stat().st_mtime_ns) for f in files]
    cs.describe(*cs.rules_for(str(places["folder"])))
    assert [(f.read_bytes(), f.stat().st_mtime_ns) for f in files] == before


@windows_paths
def test_trust_matches_windows_paths_in_any_case_or_slash(places):
    key = str(places["folder"]).upper().replace("\\", "/")
    places["config"].write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": True}}}),
                                encoding="utf-8")
    assert platform_paths.claude_folder_trusted(str(places["folder"]).lower()) is True


def test_the_claude_host_says_when_settings_couldnt_be_read(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from thechatplace.own_store import OwnSession
    from thechatplace.ui.permission_hosts import ClaudePermissions
    record = OwnSession("own-1", "Chat", str(tmp_path))
    frame = SimpleNamespace(store=SimpleNamespace(get=lambda *a, **k: record), _runners={})

    def work(action, done):
        try:
            done(action(), False)
        except Exception as exc:  # noqa: BLE001 - as the real worker reports it
            done(str(exc), True)
    frame._codex_work = work
    monkeypatch.setattr(cs, "rules_for", lambda folder: (_ for _ in ()).throw(OSError("busy")))
    said = []
    ClaudePermissions(frame, record.to_info()).native(said.append)
    assert said == ["Claude Code's own settings couldn't be read."]
