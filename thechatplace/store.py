"""The app's own state: its chats, their kept history and its settings.

Files are replaced atomically. A file that can't be read is set aside rather
than overwritten. A chat whose integration isn't registered any more is kept
but not listed, so removing an agent from the app doesn't lose anyone's chats.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .contracts import ChatRef, Message, OwnedChat


def _atomic_write(path: Path, text: str) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "history").mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._chats: Dict[str, dict] = {}
        self._settings: dict = {}
        self._chats = self._load("chats.json").get("chats", {}) or {}
        self._settings = self._load("settings.json")

    def _load(self, name: str) -> dict:
        path = self.folder / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Keep what's there for recovery: the next save would replace it.
            try:
                os.replace(path, path.with_suffix(f".bad-{int(time.time())}"))
            except OSError:
                pass
            return {}
        return data if isinstance(data, dict) else {}

    # -- chats ----------------------------------------------------------------

    def _save_chats(self) -> None:
        _atomic_write(self.folder / "chats.json", json.dumps({"chats": self._chats}, ensure_ascii=False, indent=1))

    def save_chat(self, chat: OwnedChat) -> None:
        with self._lock:
            self._chats[chat.ref.key] = {
                "integration": chat.ref.integration, "native_id": chat.ref.native_id, "title": chat.title,
                "folder": chat.folder, "options": dict(chat.options), "turns": chat.turns,
                "created": chat.created, "updated": chat.updated}
            self._save_chats()

    def get_chat(self, ref: ChatRef) -> Optional[OwnedChat]:
        with self._lock:
            record = self._chats.get(ref.key)
        return self._to_chat(record) if record else None

    def chats(self, integrations: Iterable[str]) -> List[OwnedChat]:
        known = set(integrations)
        with self._lock:
            records = [r for r in self._chats.values() if r.get("integration") in known]
        return [self._to_chat(r) for r in records]

    def owned_keys(self) -> Set[str]:
        with self._lock:
            return set(self._chats)

    @staticmethod
    def _to_chat(record: dict) -> OwnedChat:
        return OwnedChat(ChatRef(record["integration"], record["native_id"]), record.get("title", "Untitled"),
                         record.get("folder", ""), dict(record.get("options") or {}), int(record.get("turns") or 0),
                         float(record.get("created") or 0), float(record.get("updated") or 0))

    # -- kept history ---------------------------------------------------------

    def _history_path(self, ref: ChatRef) -> Path:
        return self.folder / "history" / (hashlib.sha256(ref.key.encode("utf-8")).hexdigest()[:40] + ".jsonl")

    def append_history(self, ref: ChatRef, message: Message) -> None:
        line = json.dumps({"role": message.role, "text": message.text}, ensure_ascii=False) + "\n"
        with self._lock:
            with self._history_path(ref).open("a", encoding="utf-8") as handle:
                handle.write(line)

    def read_history(self, ref: ChatRef) -> List[Message]:
        messages: List[Message] = []
        try:
            handle = self._history_path(ref).open(encoding="utf-8")
        except OSError:
            return messages
        with handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    messages.append(Message(str(record["role"]), str(record["text"])))
                except (ValueError, KeyError, TypeError):
                    continue  # a line cut short by a crash loses only itself
        return messages

    # -- settings -------------------------------------------------------------

    def enabled(self, integration_id: str, default: bool) -> bool:
        with self._lock:
            return bool((self._settings.get("enabled") or {}).get(integration_id, default))

    def set_enabled(self, integration_id: str, value: bool) -> None:
        with self._lock:
            self._settings.setdefault("enabled", {})[integration_id] = bool(value)
            _atomic_write(self.folder / "settings.json", json.dumps(self._settings, ensure_ascii=False, indent=1))
