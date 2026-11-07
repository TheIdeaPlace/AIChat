"""The Claude desktop app's own groups of Code sessions (#51), read-only.

The desktop app keeps them in ``claude_desktop_config.json``, beside its
``claude-code-sessions`` folder, under
``preferences.epitaxyPrefs["dframe-group-scopes"]``: one entry per
``<account>/<organisation>``, each with

- ``groups``: ``[{"id": "cg-…", "name": "IDT"}, …]``, in the app's order;
- ``assignments``: ``{"code:local_<id>": "cg-…"}``, a session's one group.

The Chat Place only reads this: the desktop app owns the file and rewrites it,
so its groups are changed in the desktop app. Anything unexpected (no file,
another shape after an update) just means no desktop groups.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List

from .groups import clean_name

CONFIG_NAME = "claude_desktop_config.json"
_SESSION_PREFIX = "code:"


@dataclass
class DesktopGroups:
    #: Group names, in the desktop app's order, each once.
    names: List[str] = field(default_factory=list)
    #: Session key ("local_…") -> its group's name.
    by_session: Dict[str, str] = field(default_factory=dict)
    #: False when a config couldn't be read (mid-rewrite, say): keep the
    #: groups from the last good read rather than lose them for a refresh.
    read_ok: bool = True


def load_desktop_groups(session_dirs: Iterable[Path]) -> DesktopGroups:
    """The groups from the config beside each desktop sessions folder."""
    result = DesktopGroups()
    # One account's groups come from its newest config alone: the desktop
    # app's move to Claude-Data can leave an older copy behind (#542).
    newest: Dict[str, tuple] = {}
    for folder in session_dirs:
        config = Path(folder).parent / CONFIG_NAME
        try:
            text = config.read_text(encoding="utf-8")
            written = config.stat().st_mtime
        except FileNotFoundError:
            continue  # no desktop app settings here: no groups, and that's fine
        except OSError:
            result.read_ok = False
            continue
        try:
            raw = json.loads(text)
        except ValueError:
            result.read_ok = False  # caught mid-rewrite
            continue
        try:
            scopes = raw["preferences"]["epitaxyPrefs"]["dframe-group-scopes"]
        except (KeyError, TypeError):
            continue  # no groups made yet, or another shape: none
        if not isinstance(scopes, dict):
            continue
        for key, scope in scopes.items():
            # Only the account and organisation whose sessions are here: an
            # old sign-in's groups would be empty, or mix with these.
            if isinstance(key, str) and (Path(folder) / key).is_dir():
                if key not in newest or written > newest[key][0]:
                    newest[key] = (written, scope)
    for _written, scope in newest.values():
        _read_scope(scope, result)
    return result


def _read_scope(scope, result: DesktopGroups) -> None:
    if not isinstance(scope, dict):
        return
    names: Dict[str, str] = {}
    for group in scope.get("groups") or []:
        if isinstance(group, dict) and isinstance(group.get("id"), str):
            # Tidied as The Chat Place's own names are, so the same name matches.
            name = clean_name(str(group.get("name") or ""))
            if name:
                names[group["id"]] = name
                if name.casefold() not in {n.casefold() for n in result.names}:
                    result.names.append(name)
    assignments = scope.get("assignments")
    if not isinstance(assignments, dict):
        return
    for session, group_id in assignments.items():
        if (isinstance(session, str) and session.startswith(_SESSION_PREFIX)
                and group_id in names):
            result.by_session[session[len(_SESSION_PREFIX):]] = names[group_id]
