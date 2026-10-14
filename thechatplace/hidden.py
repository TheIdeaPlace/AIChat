"""Sessions you've hidden from the list (File, Hide Session): The Chat Place's
own and the desktop app's alike. Nothing about the session itself changes;
the list just leaves it out, except in View, Show Sessions, Hidden, where
File, Bring Back Session shows it again. Kept in ``hidden.json``.

``ToolActivityStore`` is the same kind of file, ``tool_activity.json``: the
sessions you've turned Show Tool Activity on for (#162). It's a setting of
each session, so turning it on to look at one session's tool calls doesn't
fill every other session with them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Set

from . import platform_paths
from .provider_storage import PartitionedFile, foreign_key


class KeySetStore:
    """A saved set of session keys, in ``<app data>/<FILE>`` as
    ``{"version": 1, FIELD: [...]}``."""
    FILE = ""
    FIELD = ""
    #: For the messages when the file can't be used: "Couldn't read <READ>
    #: (path)" and "<FILE_WORDS> (path) isn't in the expected format".
    READ = ""
    FILE_WORDS = ""

    def __init__(self, path: Optional[Path] = None) -> None:
        if not (self.FILE and self.FIELD):
            raise TypeError("A KeySetStore needs a FILE and a FIELD.")
        self.path = Path(path) if path else platform_paths.app_data_dir() / self.FILE
        self._files = PartitionedFile(self.path)
        self._keys: Set[str] = set()
        self.load_error = ""
        self.load()

    def load(self) -> None:
        self._keys = set()
        self.load_error = ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {"version": 1, self.FIELD: []}
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read {self.READ} ({self.path}): {exc}"
            return
        keys = raw.get(self.FIELD) if isinstance(raw, dict) else None
        if not isinstance(keys, list):
            self.load_error = (f"{self.FILE_WORDS} ({self.path}) isn't in the "
                               "expected format, so The Chat Place won't change it.")
            return
        self._keys = {k for k in keys if isinstance(k, str) and k}
        self._files.baseline(self._payload(self._keys, False))
        try:
            extra = self._files.read_foreign()
            if extra is not None:
                values = extra.get(self.FIELD)
                if extra.get("version") != 1 or not isinstance(values, list) or any(
                        not isinstance(k, str) or not foreign_key(k) for k in values):
                    raise ValueError("Invalid provider session keys")
                self._keys.update(values)
            if any(foreign_key(k) for k in keys if isinstance(k, str)):
                self._files.save(self._payload(self._keys, False),
                                 self._payload(self._keys, True), force_legacy=True)
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read or separate provider preferences: {exc}"

    def _payload(self, keys, foreign):
        return {"version": 1, self.FIELD: sorted(k for k in keys
                                                 if foreign_key(k) == foreign)}


    def keys(self) -> List[str]:
        return sorted(self._keys)

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        if key not in self._keys:
            self._change(self._keys | {key})

    def discard(self, key: str) -> None:
        # Nothing to save when it isn't there: off is already what's kept.
        if key in self._keys:
            self._change(self._keys - {key})

    def rename_key(self, old: str, new: str) -> None:
        if old in self._keys:
            self._change((self._keys - {old}) | {new})

    def _change(self, keys: Set[str]) -> None:
        """Save, then keep: a change that can't be saved isn't made."""
        if self.load_error:
            raise OSError(f"Not saving over the unreadable {self.path}.")
        self._files.save(self._payload(keys, False), self._payload(keys, True))
        self._keys = set(keys)


class HiddenStore(KeySetStore):
    FILE = "hidden.json"
    FIELD = "hidden"
    READ = "your hidden sessions"
    FILE_WORDS = "Your hidden sessions file"

    def hide(self, key: str) -> None:
        self.add(key)

    def show(self, key: str) -> None:
        self.discard(key)


class ToolActivityStore(KeySetStore):
    """The sessions Show Tool Activity is on for (#162)."""
    FILE = "tool_activity.json"
    FIELD = "show_activity"
    READ = "which sessions show tool activity"
    FILE_WORDS = "Your Show Tool Activity file"
