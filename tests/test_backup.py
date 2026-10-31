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
        if str(path).endswith(target.name):
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


# Review findings on #434 -----------------------------------------------------

def _closed_wal_db(path: Path):
    """A WAL database whose program closed it cleanly: no -wal or -shm."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(v)")
    con.execute("insert into t values (7)")
    con.commit()
    con.close()
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_backup_never_writes_beside_a_closed_database(homes):
    db = homes["claude"] / "store.sqlite"
    _closed_wal_db(db)
    before = sorted(p.name for p in db.parent.iterdir())
    result = backup.create(homes["dest"], collectors=[claude_backup.inventory])
    assert result.complete, result.failures
    assert sorted(p.name for p in db.parent.iterdir()) == before
    with zipfile.ZipFile(result.path) as z:
        copy = homes["tmp"] / "c.sqlite"
        copy.write_bytes(z.read("claude/home/store.sqlite"))
    assert sqlite3.connect(copy).execute("select v from t").fetchall() == [(7,)]


def test_backup_leaves_every_source_folder_as_it_was(homes):
    c = homes["claude"]
    write(c / "projects" / "p" / "s.jsonl", "a\n")
    _closed_wal_db(c / "dbs" / "x.sqlite")
    write(homes["data"] / "sessions.json", "{}")
    backup.computer_identity()   # The Chat Place's own id, its own folder: made first

    def snapshot():
        return sorted((str(p.relative_to(homes["tmp"])), p.stat().st_size,
                       p.stat().st_mtime_ns) for p in homes["tmp"].rglob("*")
                      if p.is_file() and "dest" not in p.parts)

    before = snapshot()
    backup.create(homes["dest"], collectors=[backup.tcp_inventory, claude_backup.inventory])
    assert snapshot() == before


def test_claude_account_file_and_its_copies_are_left_out(homes, monkeypatch):
    c = homes["claude"]
    write(c / "backups" / ".claude.json.backup.1793406267771", '{"oauthAccount": 1}')
    write(c / "backups" / "other.json")
    # Where CLAUDE_CONFIG_DIR puts the account file: inside the folder.
    write(c / ".claude.json", '{"oauthAccount": 1}')
    got = names(claude_backup.inventory())
    assert got == {"claude/home/backups/other.json"}


def test_tcp_leaves_out_the_session_tools_key_and_computer_id(homes):
    write(homes["data"] / "session-tools.json", '{"key": "secret"}')
    write(homes["data"] / "groups.json", "{}")
    backup.computer_identity()   # makes computer.json
    assert names(backup.tcp_inventory()) == {"thechatplace/data/groups.json"}


def test_verify_catches_corrupt_compressed_data(homes, tmp_path):
    write(homes["data"] / "big.json", "abcdefgh" * 50000)
    good = backup.create(homes["dest"], collectors=[backup.tcp_inventory]).path
    data = bytearray(good.read_bytes())
    with zipfile.ZipFile(good) as z:
        info = z.getinfo("thechatplace/data/big.json")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra) + 200
    for i in range(start, start + 8):
        data[i] ^= 0xFF
    bad = tmp_path / "bad.zip"
    bad.write_bytes(bytes(data))
    check = backup.verify(bad)
    assert not check.ok and check.problems


def test_verify_survives_a_malformed_manifest(homes, tmp_path):
    for files in (["not a dict"], "not a list", [{"path": 3}]):
        path = tmp_path / "m.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(backup.MANIFEST, json.dumps(
                {"format": backup.FORMAT, "format_version": 1, "files": files}))
        assert not backup.verify(path).ok


def test_cancel_during_the_check_is_a_cancel_not_damage(homes):
    write(homes["data"] / "a.json", "{}")
    calls = {"n": 0}

    def cancel_on_check():
        calls["n"] += 1
        return calls["n"] > 2   # listing and copying pass; the check is cancelled

    with pytest.raises(InterruptedError):
        backup.create(homes["dest"], collectors=[backup.tcp_inventory],
                      cancelled=cancel_on_check)
    assert list(homes["dest"].iterdir()) == []


def test_a_read_failing_partway_is_incomplete_not_a_lost_backup(homes, monkeypatch):
    target = write(homes["data"] / "big.json", "x" * (3 * 1024 * 1024))
    write(homes["data"] / "fine.json", "{}")
    real_open = open

    class Failing:
        def __init__(self, handle):
            self.handle, self.reads = handle, 0

        def read(self, n):
            self.reads += 1
            if self.reads > 1:
                raise PermissionError(33, "The process cannot access the file", str(target))
            return self.handle.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    def fake_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if str(path).endswith("big.json") and "r" in mode:
            return Failing(handle)
        return handle

    monkeypatch.setattr("builtins.open", fake_open)
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory])
    monkeypatch.undo()
    assert result.path.exists() and not result.complete
    assert any("big.json" in f for f in result.failures)
    check = backup.verify(result.path)
    assert check.ok, check.problems
    assert check.manifest["unusable"] == ["thechatplace/data/big.json"]


def test_a_destination_write_failure_stops_the_backup(homes, monkeypatch):
    write(homes["data"] / "a.json", "{}")
    real = backup._hash_into

    def full_disk(handle, out, digest, cancelled=None):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "_hash_into", full_disk)
    with pytest.raises(OSError, match="No space"):
        backup.create(homes["dest"], collectors=[backup.tcp_inventory])
    monkeypatch.setattr(backup, "_hash_into", real)
    assert list(homes["dest"].iterdir()) == []


def test_publish_without_hard_links(homes, monkeypatch):
    write(homes["data"] / "a.json", "{}")

    def no_links(*_args):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    first = backup.create(homes["dest"], collectors=[backup.tcp_inventory], filename="b.zip")
    second = backup.create(homes["dest"], collectors=[backup.tcp_inventory], filename="b.zip")
    assert (first.path.name, second.path.name) == ("b.zip", "b (2).zip")
    assert backup.verify(first.path).ok and backup.verify(second.path).ok
    assert sorted(p.name for p in homes["dest"].iterdir()) == ["b (2).zip", "b.zip"]


def test_publish_keeps_the_backup_when_the_temporary_name_is_held(homes, monkeypatch):
    write(homes["data"] / "a.json", "{}")
    real_unlink = Path.unlink

    def held(self, *args, **kwargs):
        if self.name.endswith(backup.PARTIAL_SUFFIX):
            raise PermissionError(32, "in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held)
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory], filename="b.zip")
    monkeypatch.undo()
    assert result.path.name == "b.zip" and backup.verify(result.path).ok
    assert any("couldn't be removed" in n for n in result.notes)


def test_a_cloud_folder_isnt_called_the_same_disk(homes, monkeypatch):
    cloud = homes["tmp"] / "OneDrive"
    cloud.mkdir()
    monkeypatch.setenv("OneDrive", str(cloud))
    monkeypatch.setattr(backup, "_inside", lambda child, parent: Path(child) == Path(parent)
                        or Path(parent) in Path(child).parents)
    assert backup.same_drive_as_home(cloud) is False


def test_nothing_to_back_up_saves_nothing(homes):
    with pytest.raises(ValueError, match="nothing to back up"):
        backup.create(homes["dest"], collectors=[backup.tcp_inventory, claude_backup.inventory])
    assert list(homes["dest"].iterdir()) == []


def _orphaned(path: Path, keep: str):
    """A database left with committed changes only in its -wal (or a hot
    -journal) and nothing else beside it, as after a crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if keep == "-wal":
        con.execute("pragma journal_mode=wal")
        con.execute("pragma wal_autocheckpoint=0")
    con.execute("create table t(v)")
    con.execute("insert into t values ('kept')")
    con.commit()
    saved = {s: Path(str(path) + s).read_bytes() for s in ("-wal",)
             if Path(str(path) + s).exists()}
    main = path.read_bytes()
    con.close()   # a clean close checkpoints and deletes -wal; put the crash state back
    path.write_bytes(main)
    for suffix, data in saved.items():
        Path(str(path) + suffix).write_bytes(data)
    for suffix in ("-shm",):
        Path(str(path) + suffix).unlink(missing_ok=True)


def test_a_wal_without_shm_is_copied_without_writing_beside_it(homes):
    db = homes["claude"] / "state.sqlite"
    _orphaned(db, "-wal")
    before = sorted(p.name for p in db.parent.iterdir())
    assert before == ["state.sqlite", "state.sqlite-wal"]
    result = backup.create(homes["dest"], collectors=[claude_backup.inventory])
    assert result.complete, result.failures
    assert sorted(p.name for p in db.parent.iterdir()) == before
    with zipfile.ZipFile(result.path) as z:
        copy = homes["tmp"] / "c.sqlite"
        copy.write_bytes(z.read("claude/home/state.sqlite"))
    assert sqlite3.connect(copy).execute("select v from t").fetchall() == [("kept",)]


# Passphrase (#435) -------------------------------------------------------------

def test_an_encrypted_backup_round_trips_and_hides_everything(homes):
    write(homes["claude"] / "projects" / "secret-project" / "s.jsonl", "private words\n")
    write(homes["data"] / "groups.json", "{}")
    result = backup.create(homes["dest"], collectors=[backup.tcp_inventory,
                                                      claude_backup.inventory],
                           passphrase="correct horse")
    assert result.complete and result.encrypted
    assert "encrypted with your passphrase" in backup.summary(result)
    raw = result.path.read_bytes()
    assert b"private words" not in raw and b'"format"' not in raw
    # Names too: Claude's project folders are named after whole paths.
    assert b"secret-project" not in raw and b"thechatplace/data" not in raw
    assert backup.is_encrypted(result.path)
    # Every entry is encrypted, the manifest included.
    with zipfile.ZipFile(result.path) as z:
        assert all(info.flag_bits & 0x1 for info in z.infolist())
    check = backup.verify(result.path, passphrase="correct horse")
    assert check.ok and check.encrypted and check.manifest["encrypted"] is True
    assert backup.read_manifest(result.path, "correct horse")["format"] == backup.FORMAT


def test_an_encrypted_backup_without_or_with_the_wrong_passphrase(homes):
    write(homes["data"] / "a.json", "{}")
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory],
                         passphrase="pw").path
    missing = backup.verify(path)
    assert not missing.ok and missing.needs_passphrase
    assert "passphrase is needed" in missing.problems[0]
    wrong = backup.verify(path, passphrase="nope")
    assert not wrong.ok and wrong.needs_passphrase
    assert wrong.problems == ["The passphrase is wrong."]
    with pytest.raises(backup.PassphraseError):
        backup.read_manifest(path)


def test_7zip_style_readers_can_open_an_encrypted_backup(homes):
    """WinZip AES, so tools other than The Chat Place can open it."""
    import pyzipper
    write(homes["data"] / "a.json", '{"v": 1}')
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory],
                         passphrase="pw").path
    import io
    with pyzipper.AESZipFile(path) as z:
        assert z.namelist() == ["backup.zip"]     # nothing else shows
        z.setpassword(b"pw")
        inner = z.read("backup.zip")
    # Inside is an ordinary zip, the backup as it would be unencrypted.
    with zipfile.ZipFile(io.BytesIO(inner)) as plain:
        assert plain.read("thechatplace/data/a.json") == b'{"v": 1}'


def test_an_unencrypted_backup_ignores_a_passphrase_when_checking(homes):
    write(homes["data"] / "a.json", "{}")
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory]).path
    assert not backup.is_encrypted(path)
    check = backup.verify(path, passphrase="anything")
    assert check.ok and not check.encrypted


def test_a_unicode_passphrase(homes):
    write(homes["data"] / "a.json", "{}")
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory],
                         passphrase="pässwörd ✓").path
    assert backup.verify(path, passphrase="pässwörd ✓").ok
    assert backup.verify(path, passphrase="passwort").needs_passphrase


def test_a_failing_encryption_leaves_nothing(homes, monkeypatch):
    write(homes["data"] / "a.json", "{}")

    def broken(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "_encrypt", broken)
    with pytest.raises(OSError, match="No space"):
        backup.create(homes["dest"], collectors=[backup.tcp_inventory], passphrase="pw")
    assert list(homes["dest"].iterdir()) == []


def test_wrong_or_damaged_at_the_final_check(homes, monkeypatch):
    """1 wrong passphrase in 65,536 passes the quick check and fails the
    HMAC at the end, as damage does: asked again, said as either."""
    import pyzipper
    write(homes["data"] / "a.json", "{}")
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory],
                         passphrase="pw").path

    def hmac_fails(*_a, **_k):
        raise pyzipper.BadZipFile("Bad HMAC check for file 'backup.zip'")

    monkeypatch.setattr(pyzipper.AESZipFile, "open", hmac_fails)
    check = backup.verify(path, passphrase="pw")
    assert check.needs_passphrase and check.encrypted
    assert check.problems == ["The passphrase is wrong, or the backup is damaged."]


def test_opened_gives_a_plain_zip_and_cleans_up(homes):
    write(homes["data"] / "a.json", "{}")
    path = backup.create(homes["dest"], collectors=[backup.tcp_inventory],
                         passphrase="pw").path
    with backup.opened(path, "pw") as plain:
        kept = plain
        with zipfile.ZipFile(plain) as z:
            assert "manifest.json" in z.namelist()
    assert not kept.exists()
    with pytest.raises(backup.PassphraseError):
        with backup.opened(path, None):
            pass
# Scheduled backups (#437) ------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402


def _schedule(homes, every="daily", keep=3):
    backup.save_schedule(backup.Schedule(every, str(homes["dest"]), keep))


def test_schedule_off_by_default_and_odd_values_read_as_off(homes):
    assert backup.load_schedule() == backup.Schedule("off", "", backup.DEFAULT_KEEP)
    assert not backup.schedule_due()
    s = backup.load_schedule({"schedule": {"every": "hourly", "folder": 3, "keep": "x"}})
    assert s == backup.Schedule("off", "", backup.DEFAULT_KEEP)
    assert backup.load_schedule({"schedule": {"every": "daily", "folder": "f", "keep": 999}}).keep \
        == backup.MAX_KEEP


def test_schedule_due_after_its_period_since_any_good_backup(homes):
    _schedule(homes, "daily")
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    assert backup.schedule_due(now=now)               # never backed up
    state = backup.load_state()
    state["last_time"] = (now - timedelta(hours=23)).isoformat()
    assert not backup.schedule_due(state, now=now)
    state["last_time"] = (now - timedelta(hours=25)).isoformat()
    assert backup.schedule_due(state, now=now)
    state["schedule"]["every"] = "weekly"
    assert not backup.schedule_due(state, now=now)
    state["last_time"] = (now - timedelta(days=8)).isoformat()
    assert backup.schedule_due(state, now=now)
    # Tried within the hour: waits.
    state["schedule_tried"] = (now - timedelta(minutes=20)).isoformat()
    assert not backup.schedule_due(state, now=now)


def test_run_scheduled_makes_records_and_prunes_only_its_own(homes):
    write(homes["data"] / "a.json", "{}")
    _schedule(homes, keep=2)
    name = backup.computer_identity()["name"]
    other = _fake_backup(homes["dest"], "OtherPC", "someone-else", datetime(2026, 1, 1),
                         1_700_000_000)
    mine_by_hand = write(homes["dest"] / "holiday photos.zip", "x")
    made = []
    for day in range(4):
        result = backup.run_scheduled(collectors=[backup.tcp_inventory],
                                      now=datetime(2026, 10, 1 + day, 9))
        made.append(result.path)
        os.utime(result.path, (1_800_000_000 + day * 100,) * 2)
    left = sorted(p.name for p in homes["dest"].iterdir())
    assert made[-1].name in left and made[-2].name in left
    assert made[0].name not in left and made[1].name not in left
    assert other.exists() and mine_by_hand.exists()   # not this computer's, not touched
    assert backup.load_state()["last_complete"] is True
    assert any("Removed" in n for n in result.notes)
    assert name   # the computer's name is what the files are matched by


def test_an_incomplete_scheduled_backup_prunes_nothing(homes):
    write(homes["data"] / "a.json", "{}")
    _schedule(homes, keep=1)
    first = backup.run_scheduled(collectors=[backup.tcp_inventory],
                                 now=datetime(2026, 10, 1, 9)).path

    def broken():
        inv = backup.Inventory("t", "T")
        inv.failures.append("couldn't read something")
        return inv

    second = backup.run_scheduled(collectors=[backup.tcp_inventory, broken],
                                  now=datetime(2026, 10, 2, 9))
    assert not second.complete and first.exists() and second.path.exists()


def test_run_scheduled_to_a_missing_folder_raises_and_is_remembered_once_a_day(homes):
    backup.save_schedule(backup.Schedule("daily", str(homes["tmp"] / "unplugged"), 3))
    with pytest.raises(ValueError, match="isn't a folder"):
        backup.run_scheduled(collectors=[backup.tcp_inventory])
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    assert backup.note_scheduled_failure("gone", now=now) is True
    assert backup.note_scheduled_failure("gone", now=now + timedelta(hours=2)) is False
    assert backup.note_scheduled_failure("gone", now=now + timedelta(hours=25)) is True
    assert "failed" in backup.failure_text() and "gone" in backup.failure_text()
    backup.clear_scheduled_failure()
    assert backup.failure_text() == ""


def test_schedule_folder_problems(homes):
    assert "Choose a folder" in backup.schedule_folder_problem("")
    assert "isn't a folder" in backup.schedule_folder_problem(str(homes["tmp"] / "nope"))
    inside = homes["claude"] / "b"
    inside.mkdir(parents=True)
    assert "inside" in backup.schedule_folder_problem(str(inside))
    assert backup.schedule_folder_problem(str(homes["dest"])) is None


def _fake_backup(folder, name, computer_id, when, mtime):
    """A real, minimal backup zip with a manifest naming ``computer_id``."""
    path = folder / backup.default_filename(name, when)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(backup.MANIFEST, json.dumps({
            "format": backup.FORMAT, "format_version": 1,
            "computer": {"id": computer_id, "name": name}, "files": []}))
    os.utime(path, (mtime,) * 2)
    return path


def test_prune_never_deletes_the_newest_or_below_one(homes):
    paths = [_fake_backup(homes["dest"], "PC", "me", datetime(2026, 1, 1 + i),
                          1_800_000_000 + i) for i in range(3)]
    # Even if the "newest" is oldest on disk, it's kept; keep=0 means 1.
    deleted = backup.prune(homes["dest"], 0, "me", paths[0])
    assert paths[0].exists() and not paths[1].exists() and not paths[2].exists()
    assert len(deleted) == 2


def test_prune_goes_by_the_computer_id_not_the_name(homes):
    """#443 review: "PC!" and "pc" clean up to the same file name as "PC";
    sharing a cloud folder, they mustn't delete each other's backups."""
    mine = [_fake_backup(homes["dest"], "PC", "me", datetime(2026, 1, 1 + i),
                         1_800_000_000 + i) for i in range(2)]
    theirs = [_fake_backup(homes["dest"], name, "someone-else", datetime(2026, 2, 1 + i),
                           1_800_000_100 + i) for i, name in enumerate(("PC!", "pc", "PC"))]
    unreadable = write(homes["dest"] / backup.default_filename("PC", datetime(2026, 3, 1)), "x")
    encrypted = homes["dest"] / backup.default_filename("PC", datetime(2026, 3, 2))
    import pyzipper
    with pyzipper.AESZipFile(encrypted, "w", encryption=pyzipper.WZ_AES) as z:
        z.setpassword(b"pw")
        z.writestr("backup.zip", b"x")
    newest = _fake_backup(homes["dest"], "PC", "me", datetime(2026, 4, 1), 1_800_000_900)
    deleted = backup.prune(homes["dest"], 1, "me", newest)
    assert sorted(deleted) == sorted(mine)
    assert all(p.exists() for p in theirs + [unreadable, encrypted, newest])


def test_saving_the_schedule_after_a_failure_lets_it_run_now(homes):
    """#443 review: a failed attempt waits an hour, but Save (the user has
    just fixed the folder) starts again straight away."""
    backup.save_schedule(backup.Schedule("daily", str(homes["dest"]), 3))
    backup.note_scheduled_attempt()
    assert not backup.schedule_due()
    backup.save_schedule(backup.Schedule("daily", str(homes["dest"]), 3))
    assert backup.schedule_due()


def test_a_clock_that_ran_ahead_doesnt_stop_the_schedule(homes):
    _schedule(homes, "daily")
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    state = backup.load_state()
    state["last_time"] = (now + timedelta(days=300)).isoformat()
    state["schedule_tried"] = (now + timedelta(days=300)).isoformat()
    assert backup.schedule_due(state, now=now)


def test_odd_keep_values_dont_crash(homes):
    for keep in (float("inf"), "Infinity", None, [], -5):
        s = backup.load_schedule({"schedule": {"every": "daily", "folder": "f", "keep": keep}})
        assert 1 <= s.keep <= backup.MAX_KEEP


def test_run_scheduled_reports_progress(homes):
    write(homes["data"] / "a.json", "{}")
    _schedule(homes)
    seen = []
    backup.run_scheduled(collectors=[backup.tcp_inventory],
                         progress=lambda done, total, name: seen.append((done, total)))
    assert seen and seen[-1][0] == seen[-1][1] > 0


def test_same_disk_folders_are_warned_about_not_refused(homes, monkeypatch):
    monkeypatch.setattr(backup, "same_drive_as_home", lambda folder: True)
    assert "same disk" in backup.schedule_folder_warning(str(homes["dest"]))
    assert backup.schedule_folder_problem(str(homes["dest"])) is None
    monkeypatch.setattr(backup, "same_drive_as_home", lambda folder: False)
    assert backup.schedule_folder_warning(str(homes["dest"])) is None


def test_state_changes_from_two_threads_both_stay(homes):
    import threading
    backup.save_schedule(backup.Schedule("daily", str(homes["dest"]), 3))
    threads = [threading.Thread(target=backup.note_scheduled_attempt) for _ in range(10)]
    threads += [threading.Thread(target=backup.save_schedule,
                                 args=(backup.Schedule("weekly", str(homes["dest"]), 4),))
                for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state = backup.load_state()
    assert state["schedule"]["every"] == "weekly"


def test_kept_copies_of_present_originals_arent_backed_up_twice(homes):
    """#451 review: the kept copy of a chat whose transcript is still in
    Claude's folder is left out; one whose original is gone stays."""
    kept = homes["data"] / "kept-history" / "claude"
    home = str(homes["claude"])
    write(homes["claude"] / "projects" / "C--p" / "present.jsonl", "x")
    for token, native in (("a" * 64, "present"), ("b" * 64, "gone")):
        write(kept / token / "record.json",
              json.dumps({"identity": ["claude", native, home], "metadata": {}}))
        write(kept / token / "transcript.jsonl", "x")
    got = names(backup.tcp_inventory())
    assert not any(("a" * 64) in n for n in got)
    assert f"thechatplace/data/kept-history/claude/{'b' * 64}/transcript.jsonl" in got
    # With saved earlier versions, even a present one is kept.
    write(kept / ("a" * 64) / "versions" / "old.jsonl", "y")
    assert any(("a" * 64) in n for n in names(backup.tcp_inventory()))
