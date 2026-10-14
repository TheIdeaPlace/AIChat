"""Names you've given the desktop app's sessions (File, Rename Session, #93).

The desktop app's session files are never written, so a desktop session's new
name lives here, in ``titles.json``, and shows only in The Chat Place; the
desktop app keeps calling it what it did. The Chat Place's own sessions are
renamed in their own store (``sessions.json``) instead, so they never need an
entry here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from . import platform_paths
from .provider_storage import PartitionedFile, foreign_key

#: Longer names are cut: a list row or a heading has no use for a paragraph.
MAX_TITLE = 200


def clean_title(text: str) -> str:
    """A name as it is kept: on one line, without surrounding spaces, cut
    to ``MAX_TITLE`` characters. Empty means "go back to the original"."""
    return " ".join((text or "").split())[:MAX_TITLE].strip()


class TitleStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / "titles.json"
        self._files = PartitionedFile(self.path)
        self._titles: Dict[str, str] = {}
        self.load_error = ""
        self.load()

    def load(self) -> None:
        self._titles = {}
        self.load_error = ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {"version": 1, "titles": {}}
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read your session names ({self.path}): {exc}"
            return
        titles = raw.get("titles") if isinstance(raw, dict) else None
        if not isinstance(titles, dict):
            self.load_error = (f"Your session names file ({self.path}) isn't in the "
                               "expected format, so The Chat Place won't change it.")
            return
        for key, title in titles.items():
            if isinstance(key, str) and key and isinstance(title, str) and clean_title(title):
                self._titles[key] = clean_title(title)
        self._files.baseline(self._payload(self._titles, False))
        try:
            extra = self._files.read_foreign()
            if extra is not None:
                values = extra.get("titles")
                if extra.get("version") != 1 or not isinstance(values, dict) or any(
                        not isinstance(k, str) or not foreign_key(k) or not isinstance(v, str)
                        for k, v in values.items()):
                    raise ValueError("Invalid provider session titles")
                for key, value in values.items():
                    if key in self._titles and self._titles[key] != clean_title(value):
                        raise ValueError("Conflicting provider session titles")
                    self._titles[key] = clean_title(value)
            if any(foreign_key(k) for k in titles):
                self._files.save(self._payload(self._titles, False),
                                 self._payload(self._titles, True), force_legacy=True)
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read or separate provider titles ({self._files.foreign_path}): {exc}"

    @staticmethod
    def _payload(titles, foreign):
        return {"version": 1, "titles": {k: v for k, v in sorted(titles.items())
                                          if foreign_key(k) == foreign}}


    def get(self, key: str) -> str:
        """The name you gave this session, or ""."""
        return self._titles.get(key, "")

    def __contains__(self, key: str) -> bool:
        return key in self._titles

    def set(self, key: str, title: str) -> None:
        """Give a session a name; an empty one removes it."""
        titles = dict(self._titles)
        title = clean_title(title)
        if title:
            titles[key] = title
        else:
            titles.pop(key, None)
        if titles != self._titles:
            self._change(titles)

    def remove(self, key: str) -> None:
        self.set(key, "")

    def _change(self, titles: Dict[str, str]) -> None:
        """Save, then keep: a change that can't be saved isn't made."""
        if self.load_error:
            raise OSError(f"Changes cannot be saved: {self.load_error}")
        self._files.save(self._payload(titles, False), self._payload(titles, True))
        self._titles = dict(titles)
