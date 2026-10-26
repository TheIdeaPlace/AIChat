"""Copilot CLI hooks that could act without asking the person (#352).

A ``preToolUse`` hook can answer "allow", and a ``permissionRequest`` hook
"allow", and Copilot then acts without sending session/request_permission.
Checked against real Copilot CLI 1.0.103 over ACP: a project's
``.github/hooks/*.json`` hook ran a shell command unasked, in a folder never
trusted, with ``disableAllHooks: true`` in the user's settings (ACP ignores
that setting, as it ignores the permission-mode ones). So The Chat Place
looks for hooks itself, everywhere Copilot's hooks reference says they load
from, and won't send while one is found:

* in the person's own places (policy, their Copilot folder, their plugins),
  a hook that can approve;
* in the project (its repository's .github and .claude settings), any hook
  at all: Copilot runs a project's hooks over ACP without the folder being
  trusted, so a cloned repository's hook would run its commands unasked.

It errs towards refusing: an approving event counts whatever its matcher, a
file that can't be read in full counts, and a search cut short refuses.
Only reading here; nothing is changed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from .. import platform_paths

#: Hook events whose answer can let a tool run unasked (lower case, since
#: Copilot also takes the PascalCase spellings Claude Code uses).
APPROVING_EVENTS = frozenset({"pretooluse", "permissionrequest"})
#: Inside a repository: hook files, then settings files with a "hooks" key.
_REPO_HOOK_DIR = Path(".github") / "hooks"
_REPO_SETTINGS = (Path(".github") / "copilot" / "settings.json",
                  Path(".github") / "copilot" / "settings.local.json",
                  Path(".claude") / "settings.json",
                  Path(".claude") / "settings.local.json")
#: Plugin manifests, which may hold hooks or name a hooks file.
_MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json")
#: Installed plugins are searched this deep, and through at most this many
#: entries; folders that hold no plugin settings are skipped.
_PLUGIN_DEPTH = 6
_PLUGIN_ENTRIES = 20_000
_SKIPPED = frozenset({"node_modules", ".git", "__pycache__", ".venv"})


class CantCheck(Exception):
    """The hooks couldn't all be looked at, so none can be ruled out."""


def _events(text: str, any_event: bool) -> bool:
    """Whether a hook or settings file's text defines a hook that counts:
    an approving one, or with ``any_event`` any hook at all."""
    try:
        data = json.loads(text)
    except ValueError:
        # Not JSON as Python reads it. Copilot might still read it (a
        # comment, say), so it counts if it seems to define a hook.
        lowered = text.lower()
        if any_event:
            return '"hooks"' in lowered
        return any(event in lowered for event in APPROVING_EVENTS)
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return False
    return any(isinstance(name, str) and entries
               and (any_event or name.lower() in APPROVING_EVENTS)
               for name, entries in hooks.items())


def _read(path: Path) -> Optional[str]:
    """The file's text; None if there's no such file. Raises CantCheck if
    it's there but can't be read in full."""
    try:
        if not path.is_file():
            return None
        with open(path, "rb") as handle:
            data = handle.read(platform_paths.COPILOT_SETTINGS_BYTES + 1)
    except OSError:
        raise CantCheck(str(path)) from None
    if len(data) > platform_paths.COPILOT_SETTINGS_BYTES:
        raise CantCheck(str(path))
    return data.decode("utf-8-sig", "replace")


def _json_files(folder: Path) -> List[Path]:
    try:
        return sorted(p for p in folder.glob("*.json"))
    except OSError:
        return []


def _repository_folders(cwd: str) -> List[Path]:
    """The working folder and each folder above it, up to the repository's
    root (the first with a .git, which a worktree has as a file). Outside a
    repository, only the folder itself: going higher would reach the home
    folder, whose .claude settings Copilot reads only when it is the
    repository. Both as given and with links resolved, since either may be
    what Copilot looks from."""
    starts = [Path(cwd)]
    try:
        resolved = Path(cwd).resolve()
        if resolved != starts[0]:
            starts.append(resolved)
    except (OSError, RuntimeError):
        pass
    folders = []
    for start in starts:
        chain = [start, *start.parents]
        for index, folder in enumerate(chain):
            if (folder / ".git").exists():
                folders += chain[:index + 1]
                break
        else:
            folders.append(start)
    return folders


def _plugin_files(root: Path) -> List[tuple]:
    """(path, any_event) for each file under the installed plugins that may
    hold hooks: hooks.json, JSON files in a folder named hooks, manifests,
    and any hooks file a manifest names. Raises CantCheck if the search
    can't be finished.

    Where: the reference says only "each plugin's hooks.json or
    hooks/hooks.json inside its install directory"; Copilot CLI 1.0.103
    installs plugins under ~/.copilot/installed-plugins (COPILOT_HOME's),
    the folder `copilot plugin` manages. A plugin loaded with --plugin-dir
    is never used: The Chat Place starts Copilot without it."""
    if not root.is_dir():
        return []
    found, seen = [], 0
    for folder, dirs, files in os.walk(root, followlinks=True):
        here = Path(folder)
        depth = len(here.relative_to(root).parts)
        dirs[:] = [] if depth >= _PLUGIN_DEPTH else [d for d in dirs if d not in _SKIPPED]
        seen += len(files) + len(dirs)
        if seen > _PLUGIN_ENTRIES:
            raise CantCheck(str(root))
        for name in files:
            lowered = name.lower()
            if lowered.endswith(".json") and (lowered == "hooks.json" or here.name.lower() == "hooks"):
                found.append(here / name)
        for manifest in _MANIFESTS:
            path = here / manifest
            if path.is_file():
                found.append(path)
                found += _named_hooks(path)
    return found


def _named_hooks(manifest: Path) -> List[Path]:
    """Hooks files a plugin manifest names (``"hooks": "./file.json"``)."""
    text = _read(manifest)
    try:
        data = json.loads(text or "")
    except ValueError:
        return []
    named = data.get("hooks") if isinstance(data, dict) else None
    named = [named] if isinstance(named, str) else named if isinstance(named, list) else []
    return [manifest.parent / item for item in named if isinstance(item, str)]


def blocking_hooks(cwd: str, home: Optional[Path] = None) -> List[str]:
    """Where a hook that counts is defined, for a chat working in ``cwd``:
    file paths or registry keys, in Copilot's loading order (policy, user,
    repository, plugins). Empty if none was found. Raises CantCheck if they
    couldn't all be looked at."""
    home = home if home is not None else platform_paths.copilot_home()
    found = [where for where, text in platform_paths.copilot_policy_texts()
             if text is None or _events(text, False)]
    candidates = [(path, False) for path in (*_json_files(home / "hooks"), home / "settings.json")]
    for folder in _repository_folders(cwd):
        candidates += [(path, True) for path in _json_files(folder / _REPO_HOOK_DIR)]
        candidates += [(folder / name, True) for name in _REPO_SETTINGS]
    try:
        candidates += [(path, False) for path in _plugin_files(home / "installed-plugins")]
    except (OSError, ValueError):
        raise CantCheck(str(home / "installed-plugins")) from None
    for path, any_event in candidates:
        text = _read(path)
        if text is not None and _events(text, any_event) and str(path) not in found:
            found.append(str(path))
    return found
