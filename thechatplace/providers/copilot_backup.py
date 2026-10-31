"""What Back Up Now (#433) copies for GitHub Copilot CLI.

Keep complete session folders, the history index and personal configuration,
agents, skills, extensions, hooks and plugin data. Account sync is useful but
does not replace recovery of local files and customizations. Configuration
can contain inline keys; the archive must still be treated as private.

Logs, installed CLI packages, IDE runtime state and credential stores are
excluded. The shared walker snapshots real SQLite databases including WAL
data and leaves nested links alone, while allowing a linked profile root.
"""
from __future__ import annotations

import fnmatch
import json
import stat

from .. import platform_paths
from ..backup import Area, Inventory, walk

EXCLUDED_DIRS = ("logs", "pkg", "ide", "cache", ".cache", "tmp",
                 "session-state/.session-operation-locks")
EXCLUDED_FILES = ("*.lock", "*-cache.json", "vscode.session.metadata.cache.json",
                  "vscode.session.worktree.jsonl")
KNOWN = {
    "session-state", "session-store.db", "settings.json", "config.json", "agents", "skills",
    "extensions", "hooks", "instructions", "copilot-instructions.md", "mcp-config.json",
    "lsp-config.json", "permissions-config.json", "providers.json", "installed-plugins",
    "plugin-data", "command-history-state", "command-history-state.json",
}
KNOWN_PATTERNS = ("session-store.db-*",)
SKIPPED = [
    "logs/ and pkg/ (logs and the installed CLI)",
    "ide/, IDE metadata and worktree state, caches (rebuilt by Copilot)",
    "session-state/.session-operation-locks/ and *.lock (only meaningful while Copilot runs)",
    "mcp-oauth-config/ and mcp-secrets/ (sign-ins; sign in again)",
]


def _holds_a_token(path) -> bool:
    """True when a JSON file has a key naming a token, secret or password
    anywhere in it, or can't be read to tell (then it's not risked)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True

    def walk_keys(value) -> bool:
        if isinstance(value, dict):
            return any(any(word in str(key).lower() for word in ("token", "secret", "password"))
                       or walk_keys(item) for key, item in value.items())
        if isinstance(value, list):
            return any(walk_keys(item) for item in value)
        return False
    return walk_keys(data)


def inventory() -> Inventory:
    inv = Inventory("copilot", "GitHub Copilot CLI")
    home = platform_paths.copilot_home()
    try:
        if not stat.S_ISDIR(home.stat().st_mode):
            inv.failures.append(f"Copilot CLI folder at {home} isn't a folder.")
            return inv
    except FileNotFoundError:
        if home.is_symlink():
            inv.failures.append(f"Copilot CLI folder at {home} is a broken link.")
        else:
            inv.notes.append(f"No Copilot CLI folder at {home}.")
        return inv
    except OSError as exc:
        inv.failures.append(f"Couldn't read Copilot CLI folder at {home} ({exc}).")
        return inv
    inv.areas.append(Area("copilot/home", str(home),
                          "GitHub Copilot CLI: sessions, settings, custom agents and skills"))
    inv.items += walk(home, "copilot/home", exclude_dirs=EXCLUDED_DIRS,
                      exclude_files=EXCLUDED_FILES, inventory=inv)
    inv.skipped += SKIPPED
    # Without a system keychain (Linux with no libsecret) Copilot CLI keeps
    # its sign-in token in config.json in plain text: such a file is left
    # out whole rather than trusted to a list of key names (#434 review).
    config = home / "config.json"
    if _holds_a_token(config):
        inv.items = [item for item in inv.items if item.arcname != "copilot/home/config.json"]
        inv.skipped.append("config.json (it holds a sign-in token; sign in again)")
    # Classify what was actually kept: sidecars, links and credentials must
    # never be described as successful copies of unknown files.
    names = {item.arcname[len("copilot/home/"):].split("/", 1)[0] for item in inv.items}
    for name in sorted(names):
        if name not in KNOWN and not any(fnmatch.fnmatchcase(name, p) for p in KNOWN_PATTERNS):
            inv.notes.append(f"Kept, though not a known Copilot CLI file: {name}")
    return inv
