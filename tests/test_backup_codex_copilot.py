"""Back Up Now (#433): what's copied for Codex and GitHub Copilot CLI.
Windowless; every path is in tmp_path."""
import os
import sqlite3
from pathlib import Path

import pytest

from thechatplace import backup, platform_paths
from thechatplace.providers import codex_backup, copilot_backup


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def wal_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(v)")
    con.execute("insert into t values (1)")
    con.commit()
    return con


@pytest.fixture
def homes(tmp_path, monkeypatch):
    codex = tmp_path / "codex"
    copilot = tmp_path / "copilot"
    skills = tmp_path / "agents-skills"
    monkeypatch.setattr(platform_paths, "codex_home", lambda: codex)
    monkeypatch.setattr(platform_paths, "copilot_home", lambda: copilot)
    monkeypatch.setattr(platform_paths, "agents_skills_dir", lambda: skills)
    return {"codex": codex, "copilot": copilot, "skills": skills, "tmp": tmp_path}


def items(inv):
    return {i.arcname: i.sqlite for i in inv.items}


def toml_path(path: Path) -> str:
    return path.as_posix()


def test_codex_keeps_threads_databases_and_setup(homes):
    c = homes["codex"]
    write(c / "sessions" / "2026" / "10" / "30" / "rollout-1.jsonl")
    write(c / "archived_sessions" / "rollout-0.jsonl")
    write(c / "config.toml", 'model = "x"\n')
    write(c / "rules" / "default.rules")
    write(c / "skills" / "mine" / "SKILL.md")
    write(c / "memories" / "m.md")
    write(c / "history.jsonl")
    write(c / "session_index.jsonl")
    write(c / "plugins" / "mine" / "plugin.json")
    cons = [wal_db(c / name) for name in ("state_5.sqlite", "thread_history_1.sqlite",
                                          "memories_1.sqlite", "goals_1.sqlite",
                                          "queue_1.sqlite")]
    got = items(codex_backup.inventory())
    for con in cons:
        con.close()
    for name in ("sessions/2026/10/30/rollout-1.jsonl", "archived_sessions/rollout-0.jsonl",
                 "config.toml", "rules/default.rules", "skills/mine/SKILL.md",
                 "memories/m.md", "history.jsonl", "session_index.jsonl",
                 "plugins/mine/plugin.json"):
        assert got.get("codex/home/" + name) is False, name
    for name in ("state_5", "thread_history_1", "memories_1", "goals_1", "queue_1"):
        assert got[f"codex/home/{name}.sqlite"] is True
    assert not any(n.endswith(("-wal", "-shm")) for n in got)


def test_codex_leaves_out_sign_in_logs_caches_and_runtime(homes):
    c = homes["codex"]
    write(c / "sessions" / "r.jsonl")
    for name in ("auth.json", "cap_sid", "installation_id", "models_cache.json",
                 "version.json", ".sqlite-maintenance.lock", "cache/a", "tmp/b",
                 "packages/c/codex.exe", "app-server-daemon/d", "app-server-control/e",
                 "thread-writer-locks/f", "plugins/cache/g", "plugins/openai-curated-remote/h",
                 "skills/.system/i/SKILL.md", ".sandbox-bin/j"):
        write(c / name)
    con = wal_db(c / "logs_2.sqlite")
    inv = codex_backup.inventory()
    con.close()
    assert list(items(inv)) == ["codex/home/sessions/r.jsonl"]
    assert inv.notes == []   # everything there was known


def test_codex_notes_but_keeps_an_unknown_store(homes):
    c = homes["codex"]
    con = wal_db(c / "newthing_1.sqlite")
    write(c / "new-folder" / "x.json")
    inv = codex_backup.inventory()
    con.close()
    got = items(inv)
    assert got["codex/home/newthing_1.sqlite"] is True
    assert "codex/home/new-folder/x.json" in got
    assert any("newthing_1.sqlite" in n for n in inv.notes)
    assert any("new-folder" in n for n in inv.notes)


def test_codex_queue_gets_a_restore_warning(homes):
    con = wal_db(homes["codex"] / "queue_1.sqlite")
    inv = codex_backup.inventory()
    con.close()
    assert any("must not send" in n for n in inv.notes)


def test_codex_relocated_databases(homes):
    c = homes["codex"]
    elsewhere = homes["tmp"] / "dbs"
    write(c / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    con = wal_db(elsewhere / "state_5.sqlite")
    logs = wal_db(elsewhere / "logs_2.sqlite")
    write(elsewhere / "stray.txt")
    inv = codex_backup.inventory()
    con.close()
    logs.close()
    assert items(inv) == {"codex/home/config.toml": False,
                          "codex/sqlite-home/state_5.sqlite": True}
    assert any(a.root == str(elsewhere) for a in inv.areas)
    assert inv.failures == []


def test_codex_relocated_databases_unreachable_is_a_failure(homes):
    gone = homes["tmp"] / "gone"
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(gone)}'\n")
    inv = codex_backup.inventory()
    assert any("can't be read" in f for f in inv.failures)


def test_codex_bad_config_is_a_failure_but_the_rest_is_kept(homes):
    write(homes["codex"] / "config.toml", "this is [not toml")
    write(homes["codex"] / "sessions" / "r.jsonl")
    inv = codex_backup.inventory()
    assert "codex/home/sessions/r.jsonl" in items(inv)
    assert any("config.toml" in f for f in inv.failures)


def test_codex_shared_agent_skills_are_their_own_area(homes):
    write(homes["skills"] / "mine" / "SKILL.md")
    inv = codex_backup.inventory()
    assert "codex/agents-skills/mine/SKILL.md" in items(inv)
    assert any(a.name == "codex/agents-skills" for a in inv.areas)


def test_codex_missing_home_is_a_note(homes):
    inv = codex_backup.inventory()
    assert inv.items == [] and inv.failures == [] and inv.notes


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_codex_linked_home_is_read_where_it_points(homes):
    real = homes["tmp"] / "real-codex"
    write(real / "sessions" / "r.jsonl")
    try:
        os.symlink(real, homes["codex"], target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("can't make a symlink here")
    inv = codex_backup.inventory()
    assert "codex/home/sessions/r.jsonl" in items(inv)
    assert any("is a link" in n for n in inv.notes)


def test_copilot_keeps_sessions_and_setup(homes):
    p = homes["copilot"]
    s = p / "session-state" / "abc"
    for name in ("events.jsonl", "workspace.yaml", "plan.md", "checkpoints/1.json",
                 "files/snap.txt", "research/r.md"):
        write(s / name)
    session_db = wal_db(s / "session.db")
    store = wal_db(p / "session-store.db")
    for name in ("settings.json", "config.json", "agents/a.agent.md", "skills/k/SKILL.md",
                 "mcp-config.json"):
        write(p / name, "{}")
    inv = copilot_backup.inventory()
    session_db.close()
    store.close()
    got = items(inv)
    for name in ("events.jsonl", "workspace.yaml", "plan.md", "checkpoints/1.json",
                 "files/snap.txt", "research/r.md"):
        assert got.get("copilot/home/session-state/abc/" + name) is False, name
    assert got["copilot/home/session-state/abc/session.db"] is True
    assert got["copilot/home/session-store.db"] is True
    for name in ("settings.json", "config.json", "agents/a.agent.md", "skills/k/SKILL.md",
                 "mcp-config.json"):
        assert "copilot/home/" + name in got
    assert inv.notes == []


def test_copilot_leaves_out_logs_package_locks_and_secrets(homes):
    p = homes["copilot"]
    write(p / "settings.json")
    for name in ("logs/l.log", "pkg/win/copilot.exe", "ide/lock", "settings.json.lock",
                 "vscode.session.metadata.cache.json", "mcp-oauth-config/t.json",
                 "mcp-secrets/s.json"):
        write(p / name)
    assert list(items(copilot_backup.inventory())) == ["copilot/home/settings.json"]


def test_copilot_missing_home_is_a_note(homes):
    inv = copilot_backup.inventory()
    assert inv.items == [] and inv.notes


def test_all_providers_round_trip_into_one_checked_archive(homes, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "claude_home", lambda: tmp_path / "claude")
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "tcp")
    monkeypatch.setattr(platform_paths, "desktop_sessions_dirs", lambda: [])
    monkeypatch.setattr(platform_paths, "cowork_sessions_dirs", lambda: [])
    write(tmp_path / "claude" / "projects" / "p" / "s.jsonl")
    write(tmp_path / "tcp" / "sessions.json", "{}")
    write(homes["codex"] / "sessions" / "r.jsonl")
    con = wal_db(homes["codex"] / "state_5.sqlite")
    write(homes["copilot"] / "session-state" / "a" / "events.jsonl")
    dest = tmp_path / "dest"
    dest.mkdir()
    result = backup.create(dest)
    con.close()
    assert result.complete, result.failures
    assert result.providers == ["The Chat Place", "Claude", "Codex", "GitHub Copilot CLI"]
    assert backup.verify(result.path).ok

@pytest.fixture(autouse=True)
def isolated_database_override(monkeypatch):
    # A developer's shell override must never redirect these tests to real data.
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)


@pytest.mark.parametrize("setting", ['""', "false", "0", "123", "[]", "{}"])
def test_codex_invalid_database_location_is_incomplete(homes, setting):
    write(homes["codex"] / "config.toml", f"sqlite_home = {setting}\n")
    # A numeric setting is still invalid when a folder with that name exists.
    (homes["codex"] / "123").mkdir()
    write(homes["codex"] / "sessions" / "keep.jsonl")
    inv = codex_backup.inventory()
    assert inv.failures, "A present but invalid sqlite_home must not look like the default."
    assert "codex/home/sessions/keep.jsonl" in items(inv)


def test_codex_config_that_is_a_directory_is_a_failure(homes):
    (homes["codex"] / "config.toml").mkdir(parents=True)
    assert codex_backup.inventory().failures


def test_codex_unreadable_config_preserves_other_history(homes, monkeypatch):
    config = write(homes["codex"] / "config.toml", 'sqlite_home = "elsewhere"')
    write(homes["codex"] / "sessions" / "keep.jsonl")
    read_text = Path.read_text

    def denied(path, *args, **kwargs):
        if path == config:
            raise PermissionError(13, "Permission denied", str(path))
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    inv = codex_backup.inventory()
    assert inv.failures
    assert "codex/home/sessions/keep.jsonl" in items(inv)


def test_codex_unknown_database_in_shared_folder_is_not_copied(homes):
    elsewhere = homes["tmp"] / "database-folder"
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    con = wal_db(elsewhere / "future_store_2.sqlite")
    try:
        inv = codex_backup.inventory()
    finally:
        con.close()
    assert "codex/sqlite-home/future_store_2.sqlite" not in items(inv)
    assert any("future_store_2.sqlite" in note for note in inv.notes)
    assert not any("future_store_2.sqlite-wal" in note for note in inv.notes)
    assert not any("future_store_2.sqlite-shm" in note for note in inv.notes)


def test_codex_relocated_database_without_a_header_is_not_silently_lost(homes):
    elsewhere = homes["tmp"] / "database-folder"
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    write(elsewhere / "state_5.sqlite", "recoverable bytes from a damaged database")
    inv = codex_backup.inventory()
    assert items(inv)["codex/sqlite-home/state_5.sqlite"] is False


def test_codex_relative_database_location_uses_the_config_directory(homes, monkeypatch):
    write(homes["codex"] / "config.toml", "sqlite_home = 'dbs'\n")
    con = wal_db(homes["codex"] / "dbs" / "state_5.sqlite")
    monkeypatch.chdir(homes["tmp"])
    try:
        inv = codex_backup.inventory()
    finally:
        con.close()
    matches = [i for i in inv.items if i.source.name == "state_5.sqlite"]
    assert len(matches) == 1 and matches[0].sqlite
    assert inv.failures == []


def test_codex_database_environment_override_and_config_precedence(homes, monkeypatch):
    elsewhere = homes["tmp"] / "environment-dbs"
    con = wal_db(elsewhere / "state_5.sqlite")
    write(homes["codex"] / "config.toml", 'model = "example"\n')
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(elsewhere))
    try:
        assert items(codex_backup.inventory())["codex/sqlite-home/state_5.sqlite"] is True
        # An explicit config setting takes precedence over the environment.
        write(homes["codex"] / "config.toml",
              f"sqlite_home = '{toml_path(homes['codex'])}'\n")
        inv = codex_backup.inventory()
        assert not any(i.source.name == "state_5.sqlite" for i in inv.items)
    finally:
        con.close()


def test_codex_relative_environment_override_uses_the_working_directory(homes, monkeypatch):
    con = wal_db(homes["tmp"] / "dbs" / "state_5.sqlite")
    monkeypatch.chdir(homes["tmp"])
    monkeypatch.setenv("CODEX_SQLITE_HOME", "dbs")
    try:
        inv = codex_backup.inventory()
    finally:
        con.close()
    assert items(inv)["codex/sqlite-home/state_5.sqlite"] is True
    assert inv.failures == []


def test_codex_update_cache_does_not_report_an_installed_version(homes):
    write(homes["codex"] / "version.json", '{"latest_version": "999.0.0"}')
    assert codex_backup.inventory().version is None


@pytest.mark.parametrize("collect,home", [
    (codex_backup.inventory, "codex"),
    (copilot_backup.inventory, "copilot"),
])
def test_provider_home_that_is_a_file_is_a_failure(homes, collect, home):
    write(homes[home], "not a profile directory")
    assert collect().failures


def test_codex_excludes_app_server_tokens_and_runtime_files(homes):
    write(homes["codex"] / "sessions" / "keep.jsonl")
    for name in ("app-server-token", "app-server-token.old", "auth.json.bak",
                 "cap_sid.backup", "app-server-control", "app-server-daemon"):
        write(homes["codex"] / name, "credential or runtime state")
    inv = codex_backup.inventory()
    assert list(items(inv)) == ["codex/home/sessions/keep.jsonl"]
    assert not any("Kept" in note for note in inv.notes)


def test_copilot_omits_session_operation_locks_and_ide_runtime(homes):
    write(homes["copilot"] / "session-state" / "a" / "events.jsonl")
    write(homes["copilot"] / "session-state" / ".session-operation-locks" / "a.json")
    write(homes["copilot"] / "vscode.session.worktree.jsonl")
    assert list(items(copilot_backup.inventory())) == [
        "copilot/home/session-state/a/events.jsonl"]


def test_copilot_customizations_and_unknown_stores_are_preserved(homes):
    for name in ("hooks/hook.json", "extensions/mine/main.py", "instructions/a.md",
                 "lsp-config.json", "permissions-config.json", "providers.json",
                 "command-history-state/history.json", "plugin-data/mine/state.json"):
        write(homes["copilot"] / name)
    con = wal_db(homes["copilot"] / "future-index.db")
    try:
        inv = copilot_backup.inventory()
    finally:
        con.close()
    assert items(inv)["copilot/home/future-index.db"] is True
    assert any("future-index.db" in note for note in inv.notes)
    assert not any("future-index.db-wal" in note for note in inv.notes)
    assert not any("future-index.db-shm" in note for note in inv.notes)
    assert len(inv.items) == 9


@pytest.mark.parametrize("collect,home", [
    (codex_backup.inventory, "codex"),
    (copilot_backup.inventory, "copilot"),
])
def test_provider_linked_root_keeps_history_but_skips_child_links(homes, collect, home):
    real = homes["tmp"] / f"real-{home}"
    outside = homes["tmp"] / "outside"
    write(real / "keep.txt")
    write(outside / "excluded.txt")
    try:
        os.symlink(real, homes[home], target_is_directory=True)
        os.symlink(outside, real / "child", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("can't make a symlink here")
    inv = collect()
    assert list(items(inv)) == [f"{home}/home/keep.txt"]
    assert any("is a link" in note for note in inv.notes)
    assert any("Not followed" in note for note in inv.notes)
    assert not inv.failures


def test_codex_linked_database_root_is_backed_up(homes):
    real = homes["tmp"] / "real-databases"
    con = wal_db(real / "state_7.sqlite")
    link = homes["tmp"] / "database-link"
    try:
        try:
            os.symlink(real, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("can't make a symlink here")
        write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(link)}'\n")
        inv = codex_backup.inventory()
        assert items(inv)["codex/sqlite-home/state_7.sqlite"] is True
        assert any("is a link" in note for note in inv.notes)
        assert not inv.failures
    finally:
        con.close()


def test_live_provider_databases_round_trip_committed_wal_data(homes, monkeypatch):
    import zipfile

    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: homes["tmp"] / "tcp")
    elsewhere = homes["tmp"] / "databases"
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    paths = [elsewhere / "state_8.sqlite",
             homes["copilot"] / "session-store.db",
             homes["copilot"] / "session-state" / "a" / "session.db"]
    cons = [wal_db(path) for path in paths]
    dest = homes["tmp"] / "dest"
    dest.mkdir()
    try:
        result = backup.create(dest, collectors=[codex_backup.inventory, copilot_backup.inventory])
        assert result.complete, result.failures
        assert backup.verify(result.path).ok
        with zipfile.ZipFile(result.path) as archive:
            manifest = backup.read_manifest(result.path)
            database_entries = [f for f in manifest["files"] if f["kind"] == "sqlite"]
            assert len(database_entries) == 3
            assert not any(name.endswith(("-wal", "-shm")) for name in archive.namelist())
            for index, entry in enumerate(database_entries):
                restored = homes["tmp"] / f"restored-{index}.sqlite"
                restored.write_bytes(archive.read(entry["path"]))
                with sqlite3.connect(restored) as con:
                    assert con.execute("select v from t").fetchall() == [(1,)]
    finally:
        for con in cons:
            con.close()

def test_codex_unreadable_database_folder_preserves_other_history(homes, monkeypatch):
    elsewhere = homes["tmp"] / "protected-databases"
    elsewhere.mkdir()
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    write(homes["codex"] / "sessions" / "keep.jsonl")
    real_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == elsewhere:
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    inv = codex_backup.inventory()
    assert "codex/home/sessions/keep.jsonl" in items(inv)
    assert any(str(elsewhere) in failure for failure in inv.failures)


@pytest.mark.parametrize("collect,home", [
    (codex_backup.inventory, "codex"),
    (copilot_backup.inventory, "copilot"),
])
def test_unreadable_provider_root_is_a_listing_failure(homes, monkeypatch, collect, home):
    root = homes[home]
    root.mkdir()
    real_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == root:
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    assert collect().failures


def test_codex_relocated_folder_is_read_only_at_the_top(homes):
    """A broad sqlite_home (say "~") mustn't sweep in other programs'
    databases from folders below it (#434 review)."""
    elsewhere = homes["tmp"] / "broad"
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    top = wal_db(elsewhere / "state_5.sqlite")
    deep = wal_db(elsewhere / "AppData" / "Firefox" / "key4.db")
    foreign = [wal_db(elsewhere / name) for name in
               ("key4.db", "cookies.sqlite", "state_other.sqlite", "state_5.sqlite.bak.db")]
    inv = codex_backup.inventory()
    for con in (top, deep, *foreign):
        con.close()
    got = items(inv)
    assert got["codex/sqlite-home/state_5.sqlite"] is True
    assert {name for name in got if name.startswith("codex/sqlite-home/")} == {
        "codex/sqlite-home/state_5.sqlite"}


def test_copilot_config_with_a_plaintext_token_is_left_out(homes):
    """Copilot CLI's fallback without a keychain (#434 review)."""
    p = homes["copilot"]
    write(p / "settings.json", "{}")
    write(p / "config.json", '{"logged_in_users": [{"login": "me"}], '
                             '"copilot_tokens": {"https://github.com:me": "gho_x"}}')
    inv = copilot_backup.inventory()
    assert "copilot/home/config.json" not in items(inv)
    assert any("sign-in token" in s for s in inv.skipped)


def test_copilot_config_without_a_token_is_kept(homes):
    write(homes["copilot"] / "config.json", '{"banner": "never", "firstLaunchAt": "x"}')
    assert "copilot/home/config.json" in items(copilot_backup.inventory())


def test_copilot_unreadable_config_is_not_risked(homes):
    write(homes["copilot"] / "config.json", "{not json")
    assert "copilot/home/config.json" not in items(copilot_backup.inventory())


@pytest.mark.parametrize("name", ["state_99.sqlite", "thread_history_2.sqlite",
                                 "memories_3.sqlite", "goals_4.sqlite", "queue_5.sqlite"])
def test_codex_relocated_folder_keeps_each_codex_database_kind(homes, name):
    elsewhere = homes["tmp"] / "databases"
    write(homes["codex"] / "config.toml", f"sqlite_home = '{toml_path(elsewhere)}'\n")
    con = wal_db(elsewhere / name)
    try:
        inv = codex_backup.inventory()
    finally:
        con.close()
    assert items(inv)["codex/sqlite-home/" + name] is True
    assert not inv.failures
