"""What Back Up Now (#433) copies for Claude.

Claude Code keeps everything on this computer: transcripts (every CLI,
desktop-app Code and Chat Place session), memory, instructions, settings,
plans and checkpoints. Signing in on a new computer brings none of it back.
The desktop app's own session records (titles, folders, archived state, and
Cowork sessions with their transcripts) are local too.

Left out: caches, logs, runtime state (live session pids, shell snapshots,
the daemon), marketplace copies of plugins (reinstalled from their
marketplace; the lists of what's installed are kept), and sign-in tokens.
``~/.claude.json`` is left out as well: it holds the signed-in account and
can hold MCP servers' keys, and what's useful in it (trusted folders) is
quick to redo.
"""
from __future__ import annotations

from pathlib import Path

from .. import platform_paths
from ..backup import Area, Inventory, walk

# Rebuilt by Claude Code, or only meaningful while it runs.
HOME_EXCLUDED_DIRS = (
    "cache", "debug", "telemetry", "statsig", "shell-snapshots", "session-env",
    "paste-cache", "downloads", "daemon", "sessions", "ide", "scratch", "chrome",
    "plugins/marketplaces", "plugins/cache", "plugins/repos",
)
HOME_EXCLUDED_FILES = (
    "*.lock", "daemon.status.json", "stats-cache.json", "*-cache.json",
    "plugins/plugin-directory-cache*.json",
    # The account file and Claude Code's own copies of it (backups/), and
    # where CLAUDE_CONFIG_DIR puts it: the signed-in account, and MCP
    # servers' keys.
    ".claude.json", ".claude.json.*", "backups/.claude.json*",
)
SKIPPED = [
    "caches, logs and telemetry (rebuilt by Claude Code)",
    "sessions/, session-env/, shell-snapshots/, daemon/ (only meaningful while Claude runs)",
    "scratch/ (Claude's temporary working files)",
    "plugin marketplace copies (reinstalled from the marketplace; the installed lists are kept)",
    ".credentials.json, ~/.claude.json and its copies in backups/ (sign-in and account; "
    "sign in again)",
]


def inventory() -> Inventory:
    inv = Inventory("claude", "Claude")
    home = platform_paths.claude_home()
    if home.is_dir():
        inv.areas.append(Area("claude/home", str(home),
                              "Claude Code: transcripts, memory, instructions, settings, "
                              "skills, plans and checkpoints"))
        inv.items += walk(home, "claude/home", exclude_dirs=HOME_EXCLUDED_DIRS,
                          exclude_files=HOME_EXCLUDED_FILES, inventory=inv)
        inv.skipped += SKIPPED
    else:
        inv.notes.append(f"No Claude Code folder at {home}.")
    # The desktop app's Code and Cowork session records. Both the installer's
    # and the Store app's folders can exist; each gets its own area.
    for kind, folders in (("code", platform_paths.desktop_sessions_dirs()),
                          ("cowork", platform_paths.cowork_sessions_dirs())):
        for index, folder in enumerate(folders):
            area = f"claude/desktop-{kind}-{index}"
            inv.areas.append(Area(area, str(folder),
                                  f"Claude desktop app {'Cowork' if kind == 'cowork' else 'Code'} "
                                  "sessions: titles, folders, archived state"
                                  + (" and their transcripts" if kind == "cowork" else "")))
            inv.items += walk(Path(folder), area, exclude_files=("*.lock",),
                              inventory=inv)
    return inv
