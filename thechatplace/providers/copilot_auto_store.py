"""What The Chat Place keeps for each Copilot chat started here (#426):
whether it's in Auto, and the commands allowed for it with Allow for This
Chat. Its own file, ``copilot-auto.json``, beside copilot-modes.json, so
releases that don't know these never see or drop them.

Like the modes file, one that can't be read as what's written here is moved
aside (``.bad-<when>``), never written over, and one that can't be opened is
left alone and not saved to: a chat then opens asking, as if nothing were
kept. Nothing is ever answered for you from a file only half understood.

Copilot's own rules, ``copilot-rules.json``, are the person's to write; The
Chat Place only reads them (copilot_folder.read_rules).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import platform_paths

#: Said when the file couldn't be read and was moved aside.
SET_ASIDE = ("What The Chat Place kept for your Copilot chats (Auto, and commands allowed for "
             "a chat) couldn't be read, so the file was kept beside it as "
             "copilot-auto.json.bad-<date and time>. Copilot asks before it acts until you "
             "choose them again.")
#: Said when it's there but can't be opened (locked, say).
UNREADABLE = ("What The Chat Place kept for your Copilot chats couldn't be opened, so Copilot "
              "asks before it acts in this chat for now.")
#: Said when a change couldn't be saved.
NOT_SAVED = ("The Chat Place couldn't save that change for this chat, so the next time the chat "
             "opens it may be as it was before.")
#: The most commands kept for one chat.
MAX_KEPT = 200

_lock = threading.Lock()


@dataclass(frozen=True)
class Kept:
    """A command allowed for one chat, as Chat Permissions lists it (the
    shape of a Codex chat rule: rule_id, description, allowed_at_ms)."""
    command: str
    allowed_at_ms: int = 0

    @property
    def rule_id(self) -> str:
        return self.command

    @property
    def description(self) -> str:
        # Read after "Allow for this chat: " in Answer, and as a row in Chat
        # Permissions.
        return f"exactly this command: {self.command}"

    @property
    def prefix(self) -> tuple:
        return (self.command,)


def store_file() -> Path:
    return platform_paths.app_data_dir() / "copilot-auto.json"


def rules_file() -> Path:
    """Your own rules for Copilot (copilot_folder.read_rules)."""
    return platform_paths.app_data_dir() / "copilot-rules.json"


def _set_aside_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bad-{stamp}")
    n = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bad-{stamp}-{n}")
        n += 1
    return backup


def _read() -> tuple:
    """(native id -> {"auto": bool, "allowed": [Kept]}, what to say or "").
    Call with _lock held. None for the chats if the file can't be opened,
    so it isn't written over."""
    path = store_file()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}, ""
    except OSError:
        return None, UNREADABLE
    try:
        raw = json.loads(data.decode("utf-8"))
        chats = raw.get("chats") if isinstance(raw, dict) else None
        if not isinstance(chats, dict):
            raise ValueError("no chats")
    except ValueError:
        try:
            os.replace(path, _set_aside_path(path))
        except OSError:
            return None, UNREADABLE
        return {}, SET_ASIDE
    read = {}
    for native_id, entry in chats.items():
        if not (isinstance(native_id, str) and platform_paths.is_safe_id(native_id)
                and isinstance(entry, dict)):
            continue
        allowed = []
        for item in entry.get("allowed", ()) if isinstance(entry.get("allowed"), list) else ():
            command = item.get("command") if isinstance(item, dict) else None
            at = item.get("at") if isinstance(item, dict) else 0
            if isinstance(command, str) and command.strip() == command and command:
                allowed.append(Kept(command, at if isinstance(at, int) else 0))
        read[native_id] = {"auto": entry.get("auto") is True, "allowed": allowed[:MAX_KEPT]}
    return read, ""


def _write(chats: dict) -> bool:
    from ..provider_storage import atomic_write_json
    payload = {"version": 1, "chats": {
        native_id: {"auto": entry["auto"],
                    "allowed": [{"command": k.command, "at": k.allowed_at_ms}
                                for k in entry["allowed"]]}
        for native_id, entry in chats.items() if entry["auto"] or entry["allowed"]}}
    try:
        atomic_write_json(store_file(), payload)
    except OSError:
        return False
    return True


def load(native_id: str) -> tuple:
    """(whether the chat is in Auto, its allowed commands, what to say or "")."""
    with _lock:
        chats, problem = _read()
    entry = (chats or {}).get(native_id, {"auto": False, "allowed": []})
    return entry["auto"], tuple(entry["allowed"]), problem


def _change(native_id: str, change) -> tuple:
    """Apply ``change(entry)`` to one chat's entry and save; (its allowed
    commands afterwards, what to say or "")."""
    with _lock:
        chats, problem = _read()
        if chats is None:
            return (), NOT_SAVED
        entry = chats.setdefault(native_id, {"auto": False, "allowed": []})
        change(entry)
        if not _write(chats):
            return tuple(entry["allowed"]), NOT_SAVED
        return tuple(entry["allowed"]), problem


def save_auto(native_id: str, on: bool) -> str:
    """Keep whether a chat is in Auto; what to say if it couldn't be."""
    def change(entry):
        entry["auto"] = bool(on)
    return _change(native_id, change)[1]


def add(native_id: str, command: str) -> tuple:
    """Keep a command for a chat (once); (its commands, what to say or "")."""
    def change(entry):
        if not any(k.command == command for k in entry["allowed"]):
            entry["allowed"] = (entry["allowed"] + [Kept(command, int(time.time() * 1000))])[-MAX_KEPT:]
    return _change(native_id, change)


def remove(native_id: str, command: Optional[str]) -> tuple:
    """Stop keeping one command for a chat, or all of them (None)."""
    def change(entry):
        entry["allowed"] = [] if command is None else [
            k for k in entry["allowed"] if k.command != command]
    return _change(native_id, change)
