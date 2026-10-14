# Snapshot of origin/main 067a76da0209462f5b9bea9886309bea07a5abdf: thechatplace/titles.py.
# Only relative imports changed, so legacy load/save behavior stays exact.
"""Names you've given the desktop app's sessions (File, Rename Session, #93).

The desktop app's session files are never written, so a desktop session's new
name lives here, in ``titles.json``, and shows only in The Chat Place; the
desktop app keeps calling it what it did. The Chat Place's own sessions are
renamed in their own store (``sessions.json``) instead, so they never need an
entry here.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from thechatplace import platform_paths

#: Longer names are cut: a list row or a heading has no use for a paragraph.
MAX_TITLE = 200


def clean_title(text: str) -> str:
    """A name as it is kept: on one line, without surrounding spaces, cut
    to ``MAX_TITLE`` characters. Empty means "go back to the original"."""
    return " ".join((text or "").split())[:MAX_TITLE].strip()


class TitleStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / "titles.json"
        self._titles: Dict[str, str] = {}
        self.load_error = ""
        self.load()

    def load(self) -> None:
        self._titles = {}
        self.load_error = ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
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
            raise OSError(f"Not saving over the unreadable {self.path}.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="titles-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "titles": dict(sorted(titles.items()))}, handle,
                          indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._titles = dict(titles)
