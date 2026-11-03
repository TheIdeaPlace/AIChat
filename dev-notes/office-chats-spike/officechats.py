"""Turn the add-in's stored chats into something to read. Spike for AIChat#541.

  python officechats.py check              counts per store, no chat text
  python officechats.py export FOLDER      one Markdown file per chat
  add --store PATH (repeatable) to read specific stores instead of finding them

The record layout is undocumented, so a chat or message that doesn't look as
expected is skipped and counted rather than stopping the rest.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
from dataclasses import dataclass, field

import idbread

DATABASE = "claude-chat-history"
APPS = {"sheet": "Excel", "doc": "Word", "word": "Word", "slide": "PowerPoint",
        "powerpoint": "PowerPoint", "mail": "Outlook", "outlook": "Outlook"}
# Long opaque values with nothing for a reader in them.
DROP_KEYS = {"encrypted_content", "signature", "images"}
SPEAKERS = {"you": "You", "claude": "Claude", "thinking": "Thinking", "tool": "Tool"}


@dataclass
class Message:
    kind: str  # you, claude, thinking, tool
    text: str
    when: float | None = None

    @property
    def is_detail(self) -> bool:
        return self.kind in ("thinking", "tool")

    @property
    def speaker(self) -> str:
        return SPEAKERS.get(self.kind, self.kind)


@dataclass
class Chat:
    id: str
    title: str
    app: str
    created: float | None
    updated: float | None
    messages: list[Message]
    store: str = ""
    older_store: bool = False
    archived_parts: int = 0

    def visible(self, details: bool) -> list[Message]:
        return [m for m in self.messages if details or not m.is_detail]

    def matching(self, query: str, details: bool) -> list[Message]:
        q = query.casefold()
        return [m for m in self.visible(details) if q in m.text.casefold()]


@dataclass
class Library:
    chats: list[Chat] = field(default_factory=list)
    stores: list[dict] = field(default_factory=list)


def _clean(value):
    """Make a stored value printable, without opaque blobs or JS undefined."""
    if value is idbread.UNDEFINED:
        return None
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, idbread.HostObject):
        return f"<{value.kind}>"
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()
                if k not in DROP_KEYS and v is not idbread.UNDEFINED}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _pretty(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(_clean(value), indent=1, ensure_ascii=False, default=str)


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _time(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def to_message(raw) -> Message | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    when = _time(raw.get("timestamp"))
    error = _text(raw.get("error"))
    if kind in ("user", "assistant", "thinking"):
        text = _text(raw.get("content"))
        images = raw.get("images")
        if isinstance(images, list) and images:
            text = f"{text}\n[{len(images)} image(s)]".strip()
        if error:
            text = f"{text}\nError: {error}".strip()
        if not text:
            return None
        return Message({"user": "you", "assistant": "claude"}.get(kind, kind), text, when)
    if kind == "tool":
        parts = [f"{_text(raw.get('name')) or 'Unnamed tool'} ({_text(raw.get('status')) or 'no status'})"]
        if raw.get("input") not in (None, idbread.UNDEFINED):
            parts.append("Input:\n" + _pretty(raw["input"]))
        if raw.get("result") not in (None, idbread.UNDEFINED):
            parts.append("Result:\n" + _pretty(raw["result"]))
        if error:
            parts.append("Error: " + error)
        return Message("tool", "\n".join(parts), when)
    return None


def to_chat(raw, stats: dict) -> Chat | None:
    try:
        snapshot = raw["snapshot"]
        rows = snapshot["ui"]["messages"]
        cid = raw["id"]
        if not isinstance(rows, list) or not isinstance(cid, str):
            raise TypeError
    except (KeyError, TypeError):
        stats["chats_not_understood"] = stats.get("chats_not_understood", 0) + 1
        return None
    messages = []
    for row in rows:
        m = to_message(row)
        if m:
            messages.append(m)
        elif not (isinstance(row, dict) and row.get("type") in ("user", "assistant", "thinking")):
            stats["messages_not_understood"] = stats.get("messages_not_understood", 0) + 1
    agent = snapshot.get("agent")
    archive = agent.get("snipArchive") if isinstance(agent, dict) else None
    surface = _text(raw.get("surface"))
    return Chat(cid, _text(raw.get("title")) or "Untitled chat",
                APPS.get(surface.lower(), surface.title() or "Office"),
                _time(raw.get("createdAt")), _time(raw.get("updatedAt")), messages,
                archived_parts=len(archive) if isinstance(archive, list) else 0)


def _newest_write(folder: str) -> float:
    times = []
    try:
        names = os.listdir(folder)
    except OSError:
        return 0.0
    for name in names:
        try:
            times.append(os.path.getmtime(os.path.join(folder, name)))
        except OSError:
            pass
    return max(times, default=0.0)


def load(folders: list[str] | None = None) -> Library:
    """Read every store. The same chat in two stores keeps its newer copy."""
    lib = Library()
    folders = idbread.find_stores() if folders is None else folders
    newest = max(folders, key=_newest_write, default=None)
    by_id: dict[str, Chat] = {}
    for folder in folders:
        info = {"folder": folder, "chats": 0, "stats": {}}
        lib.stores.append(info)
        try:
            store = idbread.read_database(folder, DATABASE)
        except OSError as e:
            info["stats"]["store_unreadable"] = type(e).__name__
            continue
        info["stats"].update(store.stats)
        for _, raw in store.records.get("chats", []):
            chat = to_chat(raw, info["stats"])
            if not chat:
                continue
            info["chats"] += 1
            chat.store = folder
            chat.older_store = folder != newest
            old = by_id.get(chat.id)
            if old is None or (chat.updated or 0) > (old.updated or 0):
                by_id[chat.id] = chat
    lib.chats = sorted(by_id.values(), key=lambda c: c.updated or c.created or 0, reverse=True)
    return lib


def date_text(ms: float | None, with_time: bool = False) -> str:
    if ms is None:
        return "no date"
    try:
        d = datetime.datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return "no date"
    day = f"{d:%b} {d.day}, {d.year}"
    return f"{day} {d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}" if with_time else day


def to_markdown(chat: Chat, details: bool = False) -> str:
    out = [f"# {chat.title}", "",
           f"{chat.app}. Started {date_text(chat.created, True)}, last used {date_text(chat.updated, True)}.", ""]
    for m in chat.visible(details):
        out += [f"## {m.speaker}, {date_text(m.when, True)}", "", m.text, ""]
    return "\n".join(out)


def file_name(chat: Chat) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", chat.title)
    safe = " ".join(safe.split()).rstrip(". ")[:80] or "Chat"
    if safe.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                      *(f"LPT{i}" for i in range(1, 10))}:
        safe = "Chat " + safe
    return f"{safe} ({chat.id[:8]}).md"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check", "export"])
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--store", action="append")
    ap.add_argument("--details", action="store_true", help="include thinking and tool calls")
    a = ap.parse_args(argv)
    lib = load(a.store)
    if a.command == "check":
        for s in lib.stores:
            print(f"{s['chats']} chats, problems {s['stats'] or 'none'}: {s['folder']}")
        total = sum(len(c.messages) for c in lib.chats)
        print(f"{len(lib.chats)} different chats, {total} messages")
        return 0
    if not a.folder:
        ap.error("export needs a folder")
    os.makedirs(a.folder, exist_ok=True)
    for c in lib.chats:
        with open(os.path.join(a.folder, file_name(c)), "w", encoding="utf-8") as f:
            f.write(to_markdown(c, a.details))
    print(f"Saved {len(lib.chats)} chats to {a.folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
