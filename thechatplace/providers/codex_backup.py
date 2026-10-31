"""What Back Up Now (#433) copies for Codex.

Codex keeps its threads on this computer (``CODEX_HOME``, else ``~/.codex``):
rollout files under ``sessions/`` and ``archived_sessions/``, and SQLite
stores beside them for thread names, projects and relationships
(``state_*``), persisted turns (``thread_history_*``), memories, goals and
queued input. Signing in to ChatGPT on a new computer rebuilds none of it.
Database names carry schema numbers that change between releases, so they're
matched by kind, and any top-level file or folder this module doesn't
recognise is kept and named in the backup's notes rather than dropped.

``config.toml`` can move the databases (``sqlite_home``); that folder is
backed up too, and one that's set but can't be read makes the backup
incomplete. Left out: sign-in and capability files, logs, caches, installed
packages, the app-server's control and daemon files, writer locks, and
marketplace plugin copies. A restored queue or goal must never run by
itself; that's restore's concern, recorded here so it isn't forgotten.
"""
from __future__ import annotations

import fnmatch
import json
import os
import tomllib
from pathlib import Path

from .. import platform_paths
from ..backup import Area, Inventory, is_sqlite, walk

EXCLUDED_DIRS = (
    "cache", ".cache", "tmp", ".tmp", "packages", "app-server-control", "app-server-daemon",
    "thread-writer-locks", "tui-thread-reference-capabilities", "log", "logs",
    "plugins/cache", "plugins/openai-curated*", "skills/.system", ".sandbox*",
)
EXCLUDED_FILES = (
    "auth.json", "cap_sid", "installation_id", "*.lock", "logs_*.sqlite*",
    "models_cache.json", "version.json", ".personality_migration", ".sandbox_migration",
)
# Top-level names this module knows, kept or left out on purpose. Anything
# else is kept and noted, so a new kind of store isn't silently missed.
KNOWN = {
    "sessions", "archived_sessions", "memories", "rules", "skills", "plugins", "prompts",
    "config.toml", "AGENTS.md", "AGENTS.override.md", "history.jsonl", "session_index.jsonl",
    *("cache", ".cache", "tmp", ".tmp", "packages", "app-server-control", "app-server-daemon",
      "thread-writer-locks", "tui-thread-reference-capabilities", "log", "logs"),
    "auth.json", "cap_sid", "installation_id", "models_cache.json", "version.json",
    ".personality_migration", ".sandbox_migration",
}
KNOWN_PATTERNS = ("state_*.sqlite*", "thread_history_*.sqlite*", "memories_*.sqlite*",
                  "goals_*.sqlite*", "queue_*.sqlite*", "logs_*.sqlite*", "*.lock",
                  ".sandbox*")
SKIPPED = [
    "auth.json, cap_sid and installation_id (sign-in and this install's identity; sign in again)",
    "logs_*.sqlite, cache/, tmp/ and the model list (rebuilt by Codex)",
    "packages/ and marketplace plugin copies (reinstalled)",
    "app-server control and daemon files, writer locks (only meaningful while Codex runs)",
    "skills/.system (Codex's own built-in skills)",
]


def _known(name: str) -> bool:
    return name in KNOWN or any(fnmatch.fnmatchcase(name, p) for p in KNOWN_PATTERNS)


def _version(home: Path):
    """The version Codex last recorded, if it's there to read cheaply."""
    try:
        data = json.loads((home / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # Not "latest_version": that's the newest release Codex has heard of.
    for key in ("version", "current_version"):
        if isinstance(data, dict) and isinstance(data.get(key), str):
            return data[key]
    return None


def _sqlite_home(home: Path, inv: Inventory):
    """Where config.toml moved the databases to, if it did. A config that
    can't be read is a failure: whatever it said is unknown."""
    config = home / "config.toml"
    if not config.is_file():
        return None
    try:
        settings = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        inv.failures.append(f"Couldn't read {config} ({exc}); it may move Codex's databases "
                            "elsewhere, so some may be missing.")
        return None
    value = settings.get("sqlite_home")
    if not value:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not path.is_absolute():
        path = home / path
    return path


def inventory() -> Inventory:
    inv = Inventory("codex", "Codex")
    home = platform_paths.codex_home()
    if home.is_dir():
        inv.version = _version(home)
        inv.areas.append(Area("codex/home", str(home),
                              "Codex: conversations, archived conversations, thread databases, "
                              "memories, rules, skills and settings"))
        inv.items += walk(home, "codex/home", exclude_dirs=EXCLUDED_DIRS,
                          exclude_files=EXCLUDED_FILES, inventory=inv)
        inv.skipped += SKIPPED
        try:
            unknown = sorted(entry.name for entry in home.iterdir() if not _known(entry.name))
        except OSError:
            unknown = []
        for name in unknown:
            inv.notes.append(f"Kept, though not a known Codex file: {name}")
        if any(item.arcname.startswith("codex/home/queue_") for item in inv.items):
            inv.notes.append("Includes Codex's queue of unsent input; a restore must not "
                             "send it, or resume goals, by itself.")
        sqlite_home = _sqlite_home(home, inv)
        if sqlite_home is not None and not _same(sqlite_home, home):
            if sqlite_home.is_dir():
                inv.areas.append(Area("codex/sqlite-home", str(sqlite_home),
                                      "Codex's databases, moved by sqlite_home in config.toml"))
                inv.items += [item for item in walk(sqlite_home, "codex/sqlite-home",
                                                    exclude_files=EXCLUDED_FILES, inventory=inv)
                              if is_sqlite(item.source)]
            else:
                inv.failures.append(f"Codex's databases are set to be in {sqlite_home} "
                                    "(sqlite_home in config.toml), which can't be read.")
    else:
        inv.notes.append(f"No Codex folder at {home}.")
    skills = platform_paths.agents_skills_dir()
    if skills.is_dir():
        inv.areas.append(Area("codex/agents-skills", str(skills),
                              "Your own skills shared by agents such as Codex"))
        inv.items += walk(skills, "codex/agents-skills", inventory=inv)
    return inv


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False
