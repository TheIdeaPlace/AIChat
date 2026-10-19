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
        try:
            previous = self.foreign_path.read_bytes()
        except FileNotFoundError:
            previous = None
        self._write(self.foreign_path, foreign)
        try:
            if force_legacy or legacy != self._legacy or not self.path.exists():
                self._write(self.path, legacy)
        except Exception:
            if force_legacy:
                # Migration keeps the copied provider data as well as the
                # untouched original, so it can be safely retried.
                raise
            # An edit is not committed if only its provider half was saved.
            # Restore the exact prior companion before callers undo memory.
            if previous is None:
                self.foreign_path.unlink(missing_ok=True)
            else:
                self._replace(self.foreign_path, previous)
            raise
        self._legacy = legacy

    @staticmethod
    def _write(path, payload):
        PartitionedFile._replace(path, json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8'))

    @staticmethod
    def _replace(path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class CodexPlanStore:
    """Latest live plan captured here, never a write to provider history (#318)."""

    def __init__(self, root=None):
        from .platform_paths import app_data_dir
        self.root = Path(root) if root is not None else app_data_dir() / 'provider-tasks' / 'codex'

    def _path(self, native_id):
        import hashlib
        return self.root / (hashlib.sha256(native_id.encode('utf-8')).hexdigest() + '.json')

    def save(self, native_id, turn_id, tasks):
        PartitionedFile._write(self._path(native_id), {
            'provider': 'codex', 'native_id': native_id, 'turn_id': turn_id,
            'tasks': [{'task_id': t.task_id, 'subject': t.subject, 'status': t.status} for t in tasks]})

    def load(self, native_id):
        from .tasklist import TaskStep, TASK_STATUSES
        try:
            raw = json.loads(self._path(native_id).read_text(encoding='utf-8'))
            if (not isinstance(raw, dict) or raw.get('provider') != 'codex'
                    or raw.get('native_id') != native_id or not isinstance(raw.get('turn_id'), str)
                    or not raw['turn_id'] or not isinstance(raw.get('tasks'), list)
                    or len(raw['tasks']) > 256):
                return '', ()
            tasks = []
            for step in raw['tasks']:
                if (not isinstance(step, dict)
                        or any(not isinstance(step.get(k), str) or not step[k].strip()
                               for k in ('task_id', 'subject', 'status'))
                        or len(step['subject']) > 4096 or len(step['task_id']) > 256
                        or step['status'] not in TASK_STATUSES):
                    return '', ()
                tasks.append(TaskStep(step['task_id'], step['subject'], status=step['status']))
            if len({t.task_id for t in tasks}) != len(tasks):
                return '', ()
            return raw['turn_id'], tuple(tasks)
        except (OSError, ValueError, UnicodeError):
            return '', ()
