"""What Back Up Now (#433) copies for GitHub Copilot CLI.

Copilot CLI keeps each session in its own folder under ``session-state/``
(events, workspace, plans, checkpoints, file snapshots, research) in
``COPILOT_HOME``, else ``~/.copilot``, with ``session-store.db`` as an index
it can rebuild. Copilot can sync history to a GitHub account, depending on
settings and policy, but that isn't a copy of this computer's sessions, so
they're backed up anyway. Settings, custom agents, skills, extensions, hooks
and MCP definitions come too. MCP definitions can hold keys typed into them;
the user guide says to treat a backup as private.

Left out: logs, the installed CLI package, IDE locks and caches, locks, and
the OAuth and secret stores (``mcp-oauth-config/``, ``mcp-secrets/``, always
left out by the backup itself).
"""
from __future__ import annotations

import fnmatch

from .. import platform_paths
from ..backup import Area, Inventory, walk

EXCLUDED_DIRS = ("logs", "pkg", "ide", "cache", ".cache", "tmp")
EXCLUDED_FILES = ("*.lock", "*-cache.json", "vscode.session.metadata.cache.json")
KNOWN = {
    "session-state", "session-store.db", "settings.json", "config.json", "agents", "skills",
    "extensions", "hooks", "instructions", "copilot-instructions.md", "mcp-config.json",
    "lsp-config.json", "installed-plugins", "command-history-state.json",
    "vscode.session.worktree.jsonl", "mcp-oauth-config", "mcp-secrets",
    *EXCLUDED_DIRS,
}
KNOWN_PATTERNS = ("session-store.db-*", *EXCLUDED_FILES)
SKIPPED = [
    "logs/ and pkg/ (logs and the installed CLI)",
    "ide/ and caches (rebuilt by Copilot)",
    "mcp-oauth-config/ and mcp-secrets/ (sign-ins; sign in again)",
]


def inventory() -> Inventory:
    inv = Inventory("copilot", "GitHub Copilot CLI")
    home = platform_paths.copilot_home()
    if not home.is_dir():
        inv.notes.append(f"No Copilot CLI folder at {home}.")
        return inv
    inv.areas.append(Area("copilot/home", str(home),
                          "GitHub Copilot CLI: sessions, settings, custom agents and skills"))
    inv.items += walk(home, "copilot/home", exclude_dirs=EXCLUDED_DIRS,
                      exclude_files=EXCLUDED_FILES, inventory=inv)
    inv.skipped += SKIPPED
    try:
        unknown = sorted(entry.name for entry in home.iterdir()
                         if entry.name not in KNOWN
                         and not any(fnmatch.fnmatchcase(entry.name, p) for p in KNOWN_PATTERNS))
    except OSError:
        unknown = []
    for name in unknown:
        inv.notes.append(f"Kept, though not a known Copilot CLI file: {name}")
    return inv
