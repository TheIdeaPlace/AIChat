"""Keep foreign session state out of files understood by Claude-only releases.

Companion files are never opened by 0.1.6. Provider-only edits also avoid
rewriting a legacy file another running release may have updated.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def foreign_key(key: str) -> bool:
    prefix, separator, _ = key.partition(":")
    return bool(separator) and prefix not in ("own", "terminal")


class PartitionedFile:
    def __init__(self, path: Path):
        self.path = path
        self.foreign_path = path.with_name("provider-" + path.name)
        self._legacy = None

    def read_foreign(self):
        try:
            raw = json.loads(self.foreign_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid provider data in {self.foreign_path}")
        return raw

    def baseline(self, legacy):
        self._legacy = legacy

    def save(self, legacy, foreign, *, force_legacy=False):
        # Preserve foreign data first, before removing it from a mixed file.
        self._write(self.foreign_path, foreign)
        if force_legacy or legacy != self._legacy or not self.path.exists():
            self._write(self.path, legacy)
        self._legacy = legacy

    @staticmethod
    def _write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
