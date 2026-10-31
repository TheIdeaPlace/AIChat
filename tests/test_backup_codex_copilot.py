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
        write(p / name)
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
