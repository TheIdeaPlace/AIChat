"""Counts of what integrations didn't understand (#466): names, never content.

Three counts kept apart, because they mean different things: *unknown* is a
lead on something new upstream, *ignored* is a decision we made, and
*malformed* is a known message that broke its shape, which may be a breakage.
"""
from __future__ import annotations

import re
import threading
from typing import Dict

#: A provider controls these names, so both how many and how long are limited.
MAX_NAMES = 200
_SAFE = re.compile(r"[^A-Za-z0-9_./:-]")


def _clean(name: object) -> str:
    return _SAFE.sub("_", str(name))[:80] or "unnamed"


class DriftCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Dict[str, Dict[str, int]] = {"unknown": {}, "ignored": {}, "malformed": {}}

    def _add(self, kind: str, name: object) -> None:
        with self._lock:
            counts = self._counts[kind]
            key = _clean(name)
            if key in counts or len(counts) < MAX_NAMES:
                counts[key] = counts.get(key, 0) + 1

    def unknown(self, name: object) -> None:
        self._add("unknown", name)

    def ignored(self, name: object) -> None:
        self._add("ignored", name)

    def malformed(self, name: object) -> None:
        self._add("malformed", name)

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            return {kind: dict(counts) for kind, counts in self._counts.items()}
