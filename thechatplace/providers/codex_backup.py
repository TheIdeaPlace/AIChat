"""What Back Up Now (#433) copies for Codex.

History and durable state stay in Codex's profile, with databases optionally
relocated by config.toml or CODEX_SQLITE_HOME. Files are opaque recovery
copies: readable chat history still goes through app-server. Database names
carry schema numbers, so unknown stores are kept and noted rather than
silently discarded inside Codex home. A separate database folder may be
shared, so only Codex database names at its top level are copied. Shared
personal skills have their own source area.

Authentication, capabilities, caches, packages, logs and live runtime files
are excluded. A restored queue or goal must never execute automatically.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import tomllib
from pathlib import Path

from .. import platform_paths
from ..backup import Area, Inventory, walk

EXCLUDED_DIRS = (
    "cache", ".cache", "tmp", ".tmp", "packages", "app-server-control", "app-server-daemon",
    "thread-writer-locks", "tui-thread-reference-capabilities", "log", "logs",
    "plugins/cache", "plugins/openai-curated*", "skills/.system", ".sandbox*",
)
EXCLUDED_FILES = (
    "auth.json*", "cap_sid*", "app-server-token*", "installation_id", "*.lock",
    "logs_*.sqlite*", "models_cache.json", "version.json",
    ".personality_migration", ".sandbox_migration",
    "app-server-control", "app-server-daemon", "tui-thread-reference-capabilities",
)
# Only retained names are classified here. Excluded files and SQLite sidecars
# must not be described as unknown files that were successfully kept.
KNOWN = {
    "sessions", "archived_sessions", "memories", "rules", "skills", "plugins", "prompts",
    "config.toml", "AGENTS.md", "AGENTS.override.md", "history.jsonl", "session_index.jsonl",
}
KNOWN_PATTERNS = ("state_*.sqlite*", "thread_history_*.sqlite*", "memories_*.sqlite*",
                  "goals_*.sqlite*", "queue_*.sqlite*")
# A relocated folder can be the whole user home: an arbitrary SQLite file
# there is not evidence that it belongs to Codex (#434 review, finding 13).
DATABASE_NAME = re.compile(r"(?:state|thread_history|memories|goals|queue)_[0-9]+\.sqlite")
SKIPPED = [
    "auth.json and its backups, app-server tokens, capabilities and installation identity "
    "(sign-in and runtime authority; sign in again)",
    "logs_*.sqlite, cache/, tmp/ and the model list (rebuilt by Codex)",
    "packages/ and marketplace plugin copies (reinstalled)",
    "app-server control and daemon files, writer locks (only meaningful while Codex runs)",
    "skills/.system (Codex's own built-in skills)",
]


def _known(name: str) -> bool:
    return name in KNOWN or any(fnmatch.fnmatchcase(name, p) for p in KNOWN_PATTERNS)


def _note_unknown(items, area: str, inv: Inventory):
    prefix = area + "/"
    names = {item.arcname[len(prefix):].split("/", 1)[0] for item in items}
    for name in sorted(names):
        if not _known(name):
            inv.notes.append(f"Kept, though not a known Codex file: {area}/{name}")


def _directory(path: Path, inv: Inventory, label: str, *, required: bool = False,
               note_missing: bool = True) -> bool:
    """An absent optional profile is normal; an unreadable or invalid one isn't."""
    try:
        if stat.S_ISDIR(path.stat().st_mode):
            return True
        inv.failures.append(f"{label} at {path} can't be read: it isn't a folder.")
    except FileNotFoundError:
        if required or path.is_symlink():
            inv.failures.append(f"{label} at {path} can't be read: the folder is missing.")
        elif note_missing:
            inv.notes.append(f"No {label} at {path}.")
    except OSError as exc:
        inv.failures.append(f"{label} at {path} can't be read ({exc}).")
    return False


def _version(home: Path):
    """The update cache's latest_version is not the installed version."""
    try:
        data = json.loads((home / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in ("version", "current_version"):
        if isinstance(data, dict) and isinstance(data.get(key), str) and data[key]:
            return data[key]
    return None


def _sqlite_home(home: Path, inv: Inventory):
    """Find the database override without launching Codex or interpreting shell code."""
    config = home / "config.toml"
    try:
        settings = tomllib.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        settings = {}
        if config.is_symlink():
            inv.failures.append(f"Couldn't read {config}: its link target is missing.")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        inv.failures.append(f"Couldn't read {config} ({exc}); it may move Codex's databases "
                            "elsewhere, so some may be missing.")
        return None
    if "sqlite_home" in settings:
        value, base = settings["sqlite_home"], home
        setting = "sqlite_home in config.toml"
    else:
        value = os.environ.get("CODEX_SQLITE_HOME")
        if not value:
            return None
        # The public environment-variable reference defines relative overrides
        # against the process's working directory; config paths use their layer.
        base, setting = Path.cwd(), "CODEX_SQLITE_HOME"
    if not isinstance(value, str) or not value or "\x00" in value:
        inv.failures.append(f"Codex's {setting} must be a non-empty folder path.")
        return None
    try:
        path = Path(value).expanduser()
        return path if path.is_absolute() else base / path
    except (OSError, RuntimeError, ValueError) as exc:
        inv.failures.append(f"Couldn't resolve Codex's {setting} ({exc}).")
        return None


def _source_key(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path.absolute()


def inventory() -> Inventory:
    inv = Inventory("codex", "Codex")
    home = platform_paths.codex_home()
    if _directory(home, inv, "Codex folder"):
        inv.version = _version(home)
        inv.areas.append(Area("codex/home", str(home),
                              "Codex: conversations, archived conversations, thread databases, "
                              "memories, rules, skills and settings"))
        kept = walk(home, "codex/home", exclude_dirs=EXCLUDED_DIRS,
                    exclude_files=EXCLUDED_FILES, inventory=inv)
        inv.items += kept
        _note_unknown(kept, "codex/home", inv)
        inv.skipped += SKIPPED

    sqlite_home = _sqlite_home(home, inv)
    if sqlite_home is not None and _source_key(sqlite_home) != _source_key(home):
        if _directory(sqlite_home, inv, "Codex database folder", required=True):
            area = "codex/sqlite-home"
            inv.areas.append(Area(area, str(sqlite_home),
                                  "Codex's databases, moved by sqlite_home or CODEX_SQLITE_HOME"))
            # Codex keeps its databases at the top of this folder, so only the
            # top is read: a broad setting (sqlite_home = "~") mustn't sweep in
            # other programs' databases, such as a browser's saved passwords.
            listed = [item for item in walk(sqlite_home, area, exclude_dirs=("*",),
                                            exclude_files=EXCLUDED_FILES, inventory=inv)
                      if "/" not in item.arcname[len(area) + 1:]]
            # Keep known Codex names even if damaged/uninitialised, but never
            # infer ownership from a SQLite header alone in this shared folder.
            databases = []
            for item in listed:
                if DATABASE_NAME.fullmatch(item.source.name):
                    databases.append(item)
                else:
                    inv.skipped.append(f"{item.arcname} (not a Codex database name)")
                    if item.sqlite:
                        inv.notes.append(f"Not copied: {item.arcname}; an unrecognized database "
                                         "in sqlite_home may belong to another program.")
            seen = {_source_key(item.source) for item in inv.items}
            for item in databases:
                key = _source_key(item.source)
                if key not in seen:
                    inv.items.append(item)
                    seen.add(key)

    skills = platform_paths.agents_skills_dir()
    if _directory(skills, inv, "shared agent skills folder", note_missing=False):
        inv.areas.append(Area("codex/agents-skills", str(skills),
                              "Your own skills shared by agents such as Codex"))
        seen = {_source_key(item.source) for item in inv.items}
        for item in walk(skills, "codex/agents-skills", inventory=inv):
            key = _source_key(item.source)
            if key not in seen:
                inv.items.append(item)
                seen.add(key)
    if any(fnmatch.fnmatchcase(item.source.name, pattern)
           for item in inv.items for pattern in ("queue_*.sqlite*", "goals_*.sqlite*")):
        inv.notes.append("Includes Codex's queued input or goals; a restore must not send "
                         "input, or resume goals, by itself.")
    return inv
