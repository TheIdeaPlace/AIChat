"""Back Up Now (#433): the archive engine and the Claude and Chat Place
collectors. Windowless; every path is in tmp_path."""
import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from thechatplace import backup, platform_paths
from thechatplace.providers import claude_backup


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def homes(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    data = tmp_path / "tcp"
    monkeypatch.setattr(platform_paths, "claude_home", lambda: claude)
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: data)
    monkeypatch.setattr(platform_paths, "desktop_sessions_dirs", lambda: [])
    monkeypatch.setattr(platform_paths, "cowork_sessions_dirs", lambda: [])
    dest = tmp_path / "dest"
    dest.mkdir()
    return {"claude": claude, "data": data, "dest": dest, "tmp": tmp_path}


def names(inv):
    return {item.arcname for item in inv.items}


def test_claude_keeps_history_memory_and_setup_and_leaves_out_the_rest(homes):
    c = homes["claude"]
    write(c / "projects" / "C--repo" / "abc.jsonl", '{"type":"user"}\n')
    write(c / "projects" / "C--repo" / "abc" / "subagents" / "agent-1.jsonl")
    write(c / "projects" / "C--repo" / "memory" / "MEMORY.md")
    # A project folder that happens to be called "cache" is still history.
    write(c / "projects" / "cache" / "s.jsonl")
    write(c / "CLAUDE.md")
    write(c / "settings.json", "{}")
    write(c / "skills" / "todo" / "SKILL.md")
    write(c / "plugins" / "known_marketplaces.json")
    write(c / ".credentials.json", "secret")
    write(c / "cache" / "a.bin")
    write(c / "scratch" / "big.tmp")
    write(c / "sessions" / "123.json")
    write(c / "plugins" / "marketplaces" / "m" / "plugin.json")
    write(c / "daemon.lock")
    got = names(claude_backup.inventory())
    assert {"claude/home/projects/C--repo/abc.jsonl",
            "claude/home/projects/C--repo/abc/subagents/agent-1.jsonl",
            "claude/home/projects/C--repo/memory/MEMORY.md",
            "claude/home/projects/cache/s.jsonl",
            "claude/home/CLAUDE.md", "claude/home/settings.json",
            "claude/home/skills/todo/SKILL.md",
            "claude/home/plugins/known_marketplaces.json"} <= got
    assert not any(n.endswith((".credentials.json", "a.bin", "big.tmp", "123.json",
                               "plugin.json", "daemon.lock")) for n in got)


def test_claude_includes_desktop_session_folders(homes, monkeypatch):
    code = homes["tmp"] / "Claude" / "claude-code-sessions"
    write(code / "o" / "u" / "local_1.json", "{}")
    monkeypatch.setattr(platform_paths, "desktop_sessions_dirs", lambda: [code])
    inv = claude_backup.inventory()
    assert "claude/desktop-code-0/o/u/local_1.json" in names(inv)
    assert any(a.root == str(code) for a in inv.areas)


def test_missing_claude_home_is_a_note_not_a_crash(homes):
    inv = claude_backup.inventory()
    assert inv.items == [] and inv.notes


def test_credentials_are_never_listed_anywhere(tmp_path):
    write(tmp_path / "a" / "auth.json")
    write(tmp_path / "mcp-secrets" / "k")
    write(tmp_path / "deep" / ".credentials.json")
    write(tmp_path / "keep.txt")
    assert [i.arcname for i in backup.walk(tmp_path, "x")] == ["x/keep.txt"]


def test_walk_leaves_sqlite_sidecars_to_the_database_copy(tmp_path):
    db = tmp_path / "state.sqlite"
    con = sqlite3.connect(db)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(v)")
    con.execute("insert into t values (1)")
    con.commit()
    items = backup.walk(tmp_path, "x")
    assert [(i.arcname, i.sqlite) for i in items] == [("x/state.sqlite", True)]
    con.close()


def test_a_file_named_like_a_database_that_isnt_one_is_copied_as_a_file(tmp_path):
    write(tmp_path / "notes.db", "not sqlite")
    write(tmp_path / "notes.db-wal", "stray")
    items = backup.walk(tmp_path, "x")
    assert sorted((i.arcname, i.sqlite) for i in items) == [
        ("x/notes.db", False), ("x/notes.db-wal", False)]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_links_inside_are_noted_not_followed(tmp_path):
    outside = tmp_path / "outside"
    write(outside / "secret.txt")
    root = tmp_path / "root"
    write(root / "keep.txt")
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("can't make a symlink here")
    inv = backup.Inventory("t", "T")
    items = backup.walk(root, "x", inventory=inv)
    assert [i.arcname for i in items] == ["x/keep.txt"]
    assert any("link" in n for n in inv.notes)


def test_backup_round_trip_with_a_live_wal_database(homes):
    c = homes["claude"]
    write(c / "projects" / "p" / "s.jsonl", "line\n" * 1000)
    write(homes["data"] / "sessions.json", '{"a": 1}')
    write(homes["data"] / "error.log", "noise")
    db = c / "store.sqlite"
    con = sqlite3.connect(db)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(v)")
    con.execute("insert into t values ('committed but only in the WAL')")
    con.commit()   # still open, so the row is in the -wal file, not the main file
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory, claude_backup.inventory])
    con.close()
    assert result.complete and result.path.exists()
    assert not list(homes["dest"].glob("*.partial"))
    check = backup.verify(result.path)
    assert check.ok, check.problems
    with zipfile.ZipFile(result.path) as z:
        listed = set(z.namelist())
        assert "thechatplace/data/sessions.json" in listed
        assert "thechatplace/data/error.log" not in listed
        assert not any(n.endswith(("-wal", "-shm")) for n in listed)
        copy = homes["tmp"] / "copy.sqlite"
        copy.write_bytes(z.read("claude/home/store.sqlite"))
    rows = sqlite3.connect(copy).execute("select v from t").fetchall()
    assert rows == [("committed but only in the WAL",)]
    manifest = backup.read_manifest(result.path)
    assert manifest["format_version"] == backup.FORMAT_VERSION
    assert manifest["computer"]["id"]
    kinds = {f["path"]: f["kind"] for f in manifest["files"]}
    assert kinds["claude/home/store.sqlite"] == "sqlite"


def test_computer_id_is_stable(homes):
    assert backup.computer_identity()["id"] == backup.computer_identity()["id"]


def test_two_backups_with_the_same_name_never_overwrite(homes):
    write(homes["data"] / "a.json", "{}")
    first = backup.create(homes["dest"], collectors=[backup.tcp_inventory], filename="b.zip")
    second = backup.create(homes["dest"], collectors=[backup.tcp_inventory], filename="b.zip")
    assert first.path.name == "b.zip" and second.path.name == "b (2).zip"
    assert backup.verify(first.path).ok and backup.verify(second.path).ok


def test_backup_inside_a_copied_folder_is_refused(homes):
    write(homes["claude"] / "CLAUDE.md")
    inside = homes["claude"] / "backups-here"
    inside.mkdir()
    with pytest.raises(ValueError, match="inside"):
        backup.create(inside, collectors=[claude_backup.inventory])
    assert list(inside.iterdir()) == []


def test_a_failing_collector_makes_it_incomplete_but_the_rest_are_kept(homes):
    write(homes["data"] / "a.json", "{}")

    def broken():
        raise RuntimeError("bad config")

    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory, broken])
    assert not result.complete and result.files == 1
    assert any("bad config" in f for f in result.failures)
    assert backup.read_manifest(result.path)["complete"] is False


def test_collector_listing_failures_make_it_incomplete(homes):
    write(homes["data"] / "a.json", "{}")

    def partly():
        inv = backup.Inventory("t", "T")
        inv.failures.append("Couldn't read /somewhere: denied")
        return inv

    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory, partly])
    assert not result.complete
    assert "incomplete" in backup.summary(result)


def test_unreadable_file_is_a_failure(homes, monkeypatch):
    target = write(homes["data"] / "locked.json", "{}")
    write(homes["data"] / "fine.json", "{}")
    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path) == str(target):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory])
    assert not result.complete
    assert any("locked.json" in f for f in result.failures)


def test_a_file_gone_before_copying_makes_it_incomplete(homes):
    write(homes["data"] / "a.json", "{}")

    def ghost():
        inv = backup.Inventory("t", "T")
        inv.items.append(backup.Item(homes["tmp"] / "never.json", "t/never.json"))
        return inv

    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory, ghost])
    assert not result.complete and result.gone == ["t/never.json"]
    assert any("deleted before" in f for f in result.failures)
    assert backup.read_manifest(result.path)["gone_while_copying"] == ["t/never.json"]


def test_cancel_leaves_nothing_behind(homes):
    for n in range(5):
        write(homes["data"] / f"{n}.json", "{}")
    with pytest.raises(InterruptedError):
        backup.create(homes["dest"], collectors=[backup.tcp_inventory], cancelled=lambda: True)
    assert list(homes["dest"].iterdir()) == []


def test_verify_catches_damage_missing_extra_and_unsafe_names(homes, tmp_path):
    write(homes["data"] / "a.json", '{"v": 1}')
    good = backup.create(homes["dest"], collectors=[backup.tcp_inventory]).path
    manifest = backup.read_manifest(good)

    def remake(name, files, manifest_files):
        path = tmp_path / name
        m = dict(manifest, files=manifest_files)
        with zipfile.ZipFile(path, "w") as z:
            for arc, data in files.items():
                z.writestr(arc, data)
            z.writestr(backup.MANIFEST, json.dumps(m))
        return backup.verify(path)

    entry = manifest["files"][0]
    damaged = remake("d.zip", {entry["path"]: b"changed"}, [entry])
    assert not damaged.ok and any("Damaged" in p for p in damaged.problems)
    missing = remake("m.zip", {}, [entry])
    assert any("Missing" in p for p in missing.problems)
    extra = remake("e.zip", {entry["path"]: b'{"v": 1}', "stray.txt": b""}, [entry])
    assert any("Not in the manifest" in p for p in extra.problems)
    unsafe = remake("u.zip", {}, [dict(entry, path="../evil.txt")])
    assert any("Unsafe" in p for p in unsafe.problems)
    newer = tmp_path / "n.zip"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr(backup.MANIFEST, json.dumps(dict(manifest, format_version=99)))
    assert any("newer" in p for p in backup.verify(newer).problems)


def test_verify_of_something_else(tmp_path):
    junk = write(tmp_path / "x.zip", "not a zip")
    assert not backup.verify(junk).ok
    plain = tmp_path / "p.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("hello.txt", "hi")
    assert "no manifest" in backup.verify(plain).problems[0]


def test_summary_says_unencrypted_and_where(homes):
    write(homes["data"] / "a.json", "{}")
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory])
    text = backup.summary(result)
    assert result.path.name in text and "not encrypted" in text


def test_default_filename_is_safe():
    from datetime import datetime
    name = backup.default_filename("Kelly's PC/2", datetime(2026, 10, 30, 9, 5, 1))
    assert name == "TheChatPlace-backup-Kelly-s-PC-2-2026-10-30-090501.zip"


def test_state_remembers_the_last_good_backup(homes):
    assert backup.last_backup_text() == "No backup made here yet."
    write(homes["data"] / "a.json", "{}")
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory])
    backup.record(result)
    state = backup.load_state()
    assert state["folder"] == str(homes["dest"]) and state["last_complete"] is True
    assert backup.last_backup_text().startswith("Last backup: ")
    assert str(homes["dest"]) in backup.last_backup_text()


def test_check_text_good_and_bad(homes, tmp_path):
    write(homes["data"] / "a.json", "{}")
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory]).path
    assert "is good: its 1 file matches" in backup.check_text(path, backup.verify(path))
    junk = write(tmp_path / "j.zip", "x")
    assert "has problems" in backup.check_text(junk, backup.verify(junk))


def test_temporary_files_are_never_listed(tmp_path):
    for name in ("a.tmp", "b.json.tmp.123", "c.partial", "d~", "keep.json"):
        write(tmp_path / name)
    assert [i.arcname for i in backup.walk(tmp_path, "x")] == ["x/keep.json"]
