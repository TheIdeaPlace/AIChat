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
from pathlib import Path
from typing import Dict, List, Optional

from . import platform_paths
from .provider_storage import PartitionedFile, foreign_key

#: Longest group name kept; longer ones are cut.
MAX_NAME = 60


def clean_name(name: str) -> str:
    """A group name on one line, without extra spaces, at most MAX_NAME."""
    return " ".join((name or "").split())[:MAX_NAME].strip()


class GroupStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / "groups.json"
        self._files = PartitionedFile(self.path)
        #: name -> session keys, in the order groups were made.
        self._groups: Dict[str, List[str]] = {}
        self.load_error = ""
        self.load()

    # -- persistence ----------------------------------------------------------

    def load(self) -> None:
        self._groups = {}
        self._foreign_only_groups = set()
        self.load_error = ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {"version": 1, "groups": []}
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
        mixed = any(foreign_key(k) for m in self._groups.values() for k in m)
        self._foreign_only_groups = {n for n, m in self._groups.items()
                                     if m and all(foreign_key(k) for k in m)}
        self._files.baseline(self._payload(False))
        try:
            extra = self._files.read_foreign()
            if extra is not None:
                values = extra.get("groups")
                if extra.get("version") != 1 or not isinstance(values, list):
                    raise ValueError("Invalid provider groups")
                seen = set()
                renamed = {}
                for item in values:
                    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                        raise ValueError("Invalid provider group")
                    name, members = clean_name(item["name"]), item.get("sessions")
                    if not name or name.casefold() in seen or not isinstance(members, list) or any(
                            not isinstance(k, str) or not foreign_key(k) for k in members):
                        raise ValueError("Invalid provider group members")
                    seen.add(name.casefold())
                    canonical = self.find(name)
                    anchor = item.get('legacy_sessions')
                    if anchor is not None and (not isinstance(anchor, list) or any(
                            not isinstance(k, str) or foreign_key(k) for k in anchor)):
                        raise ValueError('Invalid legacy group anchor')
                    if canonical is None and anchor:
                        # 0.1.6 cannot rename the companion. Follow a rename
                        # only when its unchanged Claude membership identifies
                        # exactly one group; never guess from a shared member.
                        candidates = [n for n, keys in self._groups.items()
                            if set(k for k in keys if not foreign_key(k)) == set(anchor)]
                        if len(candidates) > 1:
                            raise ValueError('Ambiguous legacy group rename')
                        if candidates:
                            canonical = candidates[0]
                            renamed[name] = canonical
                    if canonical is None:
                        self._foreign_only_groups.add(name)
                    else:
                        name = canonical
                    target = self._groups.setdefault(name, [])
                    target.extend(k for k in members if k not in target)
                order = extra.get('order')
                if order is not None:
                    if (not isinstance(order, list) or any(not isinstance(n, str) for n in order)
                            or len(order) != len(set(order))):
                        raise ValueError('Invalid provider group order')
                    order = [renamed.get(n, n) for n in order]
                    ordered = {n: self._groups[n] for n in order if n in self._groups}
                    ordered.update(self._groups)
                    self._groups = ordered
            if mixed:
                self._files.save(self._payload(False), self._payload(True), force_legacy=True)
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read or separate provider groups ({self._files.foreign_path}): {exc}"

    def _payload(self, foreign):
        groups = []
        for name, members in self._groups.items():
            selected = [k for k in members if foreign_key(k) == foreign]
            keep_empty = not members and (foreign == (name in self._foreign_only_groups))
            if selected or keep_empty:
                item = {"name": name, "sessions": selected}
                if foreign:
                    item['legacy_sessions'] = [k for k in members if not foreign_key(k)]
                groups.append(item)
        payload = {"version": 1, "groups": groups}
        if foreign:
            payload['order'] = list(self._groups)
        return payload


    def save(self) -> None:
        if self.load_error:
            raise OSError(f"Changes cannot be saved: {self.load_error}")
        self._files.save(self._payload(False), self._payload(True))

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

    def _commit(self, groups: Dict[str, List[str]], foreign_only=None) -> None:
        """Make ``groups`` the groups, if it can be saved."""
        before = self._groups
        before_foreign = self._foreign_only_groups
        self._foreign_only_groups = (before_foreign if foreign_only is None else foreign_only) & groups.keys()
        self._groups = groups
        try:
            self.save()
        except Exception:
            self._groups = before
            self._foreign_only_groups = before_foreign
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
        foreign = {(new if n == old else n) for n in self._foreign_only_groups}
        self._commit({(new if n == old else n): list(m) for n, m in self._groups.items()}, foreign)
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
