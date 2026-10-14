# Snapshot of origin/main 067a76da0209462f5b9bea9886309bea07a5abdf: thechatplace/groups.py.
# Only relative imports changed, so legacy load/save behavior stays exact.
"""Groups of sessions (#31), like groups in Claude on the web.

A group is a name and the sessions in it, by their list key (a desktop
session's ``local_`` id, or ``own:<id>`` for The Chat Place's own). A session
can be in more than one group. Kept in ``%APPDATA%\\TheChatPlace\\groups.json``
and written the same way as the session store: to a temporary file that then
replaces the real one. The desktop app's files are never touched; they have
no groups of their own.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from thechatplace import platform_paths

#: Longest group name kept; longer ones are cut.
MAX_NAME = 60


def clean_name(name: str) -> str:
    """A group name on one line, without extra spaces, at most MAX_NAME."""
    return " ".join((name or "").split())[:MAX_NAME].strip()


class GroupStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / "groups.json"
        #: name -> session keys, in the order groups were made.
        self._groups: Dict[str, List[str]] = {}
        self.load_error = ""
        self.load()

    # -- persistence ----------------------------------------------------------

    def load(self) -> None:
        self._groups = {}
        self.load_error = ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read your groups ({self.path}): {exc}"
            return
        items = raw.get("groups") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            # Not ours, or damaged: never save over it (save refuses).
            self.load_error = (f"Your groups file ({self.path}) isn't in the expected "
                               "format, so The Chat Place won't change it. Groups are off "
                               "until it's fixed or removed.")
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = clean_name(str(item.get("name") or ""))
            members = item.get("sessions")
            if not name or name in self._groups:
                continue
            self._groups[name] = [m for m in members if isinstance(m, str) and m] \
                if isinstance(members, list) else []

    def save(self) -> None:
        if self.load_error:
            raise OSError(f"Not saving over the unreadable {self.path}.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "groups": [{"name": n, "sessions": m}
                                             for n, m in self._groups.items()]}
        fd, tmp = tempfile.mkstemp(prefix="groups-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- reading --------------------------------------------------------------

    def names(self) -> List[str]:
        return list(self._groups)

    def members(self, name: str) -> List[str]:
        return list(self._groups.get(name, []))

    def groups_of(self, key: str) -> List[str]:
        return [name for name, members in self._groups.items() if key in members]

    def find(self, name: str) -> Optional[str]:
        """The existing group with this name, ignoring case, or None."""
        wanted = clean_name(name).casefold()
        return next((n for n in self._groups if n.casefold() == wanted), None)

    # -- changes (each saves; a change that can't be saved is undone) -------------

    def _commit(self, groups: Dict[str, List[str]]) -> None:
        """Make ``groups`` the groups, if it can be saved."""
        before = self._groups
        self._groups = groups
        try:
            self.save()
        except Exception:
            self._groups = before
            raise

    def _copy(self) -> Dict[str, List[str]]:
        return {name: list(members) for name, members in self._groups.items()}

    def create(self, name: str) -> str:
        name = clean_name(name)
        if not name:
            raise ValueError("A group needs a name.")
        if self.find(name):
            raise ValueError(f"There's already a group called {self.find(name)}.")
        groups = self._copy()
        groups[name] = []
        self._commit(groups)
        return name

    def rename(self, old: str, new: str) -> str:
        new = clean_name(new)
        if old not in self._groups:
            raise ValueError(f"There's no group called {old}.")
        if not new:
            raise ValueError("A group needs a name.")
        clash = self.find(new)
        if clash and clash != old:
            raise ValueError(f"There's already a group called {clash}.")
        # Keep its place in the order.
        self._commit({(new if n == old else n): list(m) for n, m in self._groups.items()})
        return new

    def delete(self, name: str) -> None:
        if name in self._groups:
            groups = self._copy()
            del groups[name]
            self._commit(groups)

    def add(self, name: str, key: str) -> bool:
        """Put a session in a group. False if it was already there."""
        members = self._groups.get(name)
        if members is None:
            raise ValueError(f"There's no group called {name}.")
        if key in members:
            return False
        groups = self._copy()
        groups[name].append(key)
        self._commit(groups)
        return True

    def remove(self, name: str, key: str) -> bool:
        members = self._groups.get(name)
        if not members or key not in members:
            return False
        groups = self._copy()
        groups[name].remove(key)
        self._commit(groups)
        return True

    def forget(self, key: str) -> None:
        """A session is gone: take it out of every group."""
        if any(key in members for members in self._groups.values()):
            self._commit({n: [k for k in m if k != key] for n, m in self._groups.items()})

    def rename_key(self, old: str, new: str) -> None:
        """A session's key changed (Claude Code chose its own id)."""
        if any(old in members for members in self._groups.values()):
            self._commit({n: [new if k == old else k for k in m]
                          for n, m in self._groups.items()})
