"""Chats restored from a backup (#436), kept in The Chat Place's own folder.

Each imported chat is one JSON file under ``imported/`` holding where it came
from (provider, native id, the source computer's id and name, the backup it
was read from) and its whole history as the app's own ``Transcript``. Every
provider's history is kept the same way, so reading one back needs no
provider program (Codex's history is read from a backup through app-server,
which isn't there afterwards).

Identity is provider + native id + source computer id (#228): importing the
same backup again, or a newer one from the same computer, updates a chat
rather than adding it twice, and the same chat from two computers is two
chats. An imported chat is never The Chat Place's own: it can be read,
searched and exported, never sent to, resumed or forked. Nothing here
writes outside The Chat Place's folder.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from theideaplace_appkit.difftext import Hunk

from . import platform_paths
from .changes import FileEdit
from .tasklist import TaskStep
from .transcript import ChatMessage, ToolCommand, Transcript

FOLDER = "imported"
INDEX = "index.json"
FORMAT_VERSION = 1


@dataclass
class ImportedChat:
    """What the list needs about one imported chat, without its history."""
    key: str                      # "imported:<sha256 of the identity>"
    provider: str                 # the AI it was with: claude, codex, copilot
    native_id: str
    computer_id: str
    computer_name: str
    title: str
    cwd: str = ""
    created_ms: int = 0
    last_activity_ms: int = 0
    archived: bool = False
    messages: int = 0
    backup_name: str = ""         # the backup file it was last read from
    backup_created: str = ""      # when that backup was made
    imported_at: str = ""
    source_key: str = ""          # its old key on that computer, for names and groups

    @property
    def identity(self):
        return self.provider, self.native_id, self.computer_id


def chat_key(provider: str, native_id: str, computer_id: str) -> str:
    """The same key backup_reader.ImportedSession.key gives."""
    encoded = json.dumps((provider, native_id, computer_id), ensure_ascii=True).encode("ascii")
    return "imported:" + hashlib.sha256(encoded).hexdigest()


# Transcript <-> JSON ---------------------------------------------------------

def _known(cls, data: dict) -> dict:
    """Only the fields ``cls`` has, so a file from a newer release still loads."""
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def transcript_to_json(transcript: Transcript) -> dict:
    return dataclasses.asdict(transcript)


def transcript_from_json(data: dict) -> Transcript:
    if not isinstance(data, dict):
        raise ValueError("Invalid stored transcript")
    edits = []
    for raw in data.get("edits") or []:
        hunks = [Hunk(**_known(Hunk, h)) for h in raw.get("hunks") or []]
        edits.append(FileEdit(**{**_known(FileEdit, raw), "hunks": hunks}))
    return Transcript(
        messages=[ChatMessage(**_known(ChatMessage, m)) for m in data.get("messages") or []],
        unreadable_lines=int(data.get("unreadable_lines") or 0),
        lines_read=int(data.get("lines_read") or 0),
        context_tokens=int(data.get("context_tokens") or 0),
        model=str(data.get("model") or ""),
        compactions=int(data.get("compactions") or 0),
        turns=int(data.get("turns") or 0),
        edits=edits,
        commands=[ToolCommand(**_known(ToolCommand, c)) for c in data.get("commands") or []],
        tasks=[TaskStep(**_known(TaskStep, t)) for t in data.get("tasks") or []])


# The store -------------------------------------------------------------------

class ImportedStore:
    """The imported chats in ``<app data>/imported``: an index for the list,
    and one file per chat with its history."""

    def __init__(self, folder: Optional[Path] = None):
        self.folder = Path(folder) if folder else platform_paths.app_data_dir() / FOLDER
        self._lock = threading.RLock()

    def _chat_file(self, key: str) -> Path:
        # The key is "imported:" + 64 hex digits; only the digits name the file.
        digest = key.split(":", 1)[-1]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid imported chat key")
        return self.folder / f"{digest}.json"

    def _write(self, path: Path, payload) -> None:
        from .provider_storage import atomic_write_json
        self.folder.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)

    def _index(self) -> Dict[str, dict]:
        try:
            raw = json.loads((self.folder / INDEX).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}
        chats = raw.get("chats") if isinstance(raw, dict) else None
        return chats if isinstance(chats, dict) else {}

    def all(self) -> List[ImportedChat]:
        with self._lock:
            chats = []
            for key, raw in self._index().items():
                try:
                    chat = ImportedChat(**_known(ImportedChat, raw))
                except TypeError:
                    continue
                if chat.key == key:
                    chats.append(chat)
            return chats

    def get(self, key: str) -> Optional[ImportedChat]:
        return next((chat for chat in self.all() if chat.key == key), None)

    def load(self, key: str) -> Transcript:
        """A chat's history. Raises LookupError if it's gone, ValueError if
        its file is damaged."""
        with self._lock:
            path = self._chat_file(key)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise LookupError("This imported chat's history is gone.") from None
            if not isinstance(raw, dict) or raw.get("key") != key:
                raise ValueError("This imported chat's history is damaged.")
            return transcript_from_json(raw.get("transcript"))

    def save(self, chat: ImportedChat, transcript: Transcript) -> str:
        """Add or update one chat: "added", "updated" or "unchanged". A copy
        from an older backup never replaces a newer one already here."""
        if chat.key != chat_key(*chat.identity):
            raise ValueError("Imported chat key doesn't match its identity")
        with self._lock:
            index = self._index()
            old = index.get(chat.key)
            if old is not None:
                older = (int(old.get("last_activity_ms") or 0) > chat.last_activity_ms
                         or (int(old.get("last_activity_ms") or 0) == chat.last_activity_ms
                             and int(old.get("messages") or 0) >= len(transcript.messages)))
                if older:
                    return "unchanged"
            chat = dataclasses.replace(
                chat, messages=len(transcript.messages),
                imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self._write(self._chat_file(chat.key), {
                "format_version": FORMAT_VERSION, "key": chat.key,
                "chat": dataclasses.asdict(chat),
                "transcript": transcript_to_json(transcript)})
            index[chat.key] = dataclasses.asdict(chat)
            self._write(self.folder / INDEX, {"format_version": FORMAT_VERSION, "chats": index})
            return "updated" if old is not None else "added"

    def remove(self, key: str) -> bool:
        """Delete The Chat Place's copy of an imported chat (nothing else)."""
        with self._lock:
            index = self._index()
            if index.pop(key, None) is None:
                return False
            self._write(self.folder / INDEX, {"format_version": FORMAT_VERSION, "chats": index})
            try:
                self._chat_file(key).unlink()
            except FileNotFoundError:
                pass
            return True


def import_sessions(store: ImportedStore, reader, sessions, *, backup_name: str = "",
                    cancelled=None, progress=None) -> Dict[str, int]:
    """Read each of ``sessions`` from an open backup_reader.BackupReader and
    keep it in ``store``. Returns how many were added, updated, unchanged
    and failed (one that can't be read doesn't stop the rest)."""
    manifest = reader.manifest
    computer = manifest.get("computer") or {}
    counts = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0}
    sessions = list(sessions)
    for done, session in enumerate(sessions):
        if cancelled is not None and cancelled():
            raise InterruptedError("Restore cancelled.")
        if progress is not None:
            progress(done, len(sessions), session.title)
        try:
            transcript = reader.read(session)
            chat = ImportedChat(
                key=session.key, provider=session.provider, native_id=session.native_id,
                computer_id=session.computer_id, computer_name=str(computer.get("name") or ""),
                title=session.title, cwd=session.cwd, created_ms=int(session.created_ms or 0),
                last_activity_ms=int(session.last_activity_ms or 0),
                archived=bool(session.archived), backup_name=backup_name,
                backup_created=str(manifest.get("created") or ""),
                source_key=session.source_key)
            counts[store.save(chat, transcript)] += 1
        except InterruptedError:
            raise
        except Exception:  # noqa: BLE001 - one chat mustn't stop the others
            counts["failed"] += 1
    if progress is not None:
        progress(len(sessions), len(sessions), "")
    return counts


@dataclass
class RestorePlan:
    """What a backup holds, for the question before anything is imported."""
    computer_name: str
    created: str
    counts: Dict[str, int] = field(default_factory=dict)   # provider -> chats
    notes: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def describe_plan(plan: RestorePlan) -> str:
    """The question Restore from Backup asks."""
    from .providers.imported import PROVIDER_NAMES
    try:
        moment = datetime.fromisoformat(plan.created).astimezone()
        when = f", backed up on {moment.day} {moment:%B %Y} at {moment:%H:%M}"
    except (TypeError, ValueError):
        when = ""
    parts = ", ".join(f"{PROVIDER_NAMES.get(p, p)} {n}" for p, n in sorted(plan.counts.items()))
    text = (f"Restore {plan.total} chat{'s' if plan.total != 1 else ''} from "
            f"{plan.computer_name or 'another computer'}{when}? {parts}.\n\n"
            "They come back read-only, marked as imported: you can read, find and export "
            "them, not continue them. Nothing is written to Claude's, Codex's or Copilot's "
            "folders. Restoring the same backup again updates them instead of adding copies.")
    if plan.notes:
        text += "\n\nSome of it couldn't be read: " + " ".join(plan.notes[:5])
    return text


def restore_backup(path, manifest: dict, passphrase: Optional[str], *, confirm,
                   store: Optional[ImportedStore] = None, cancelled=None,
                   progress=None) -> Optional[Dict[str, int]]:
    """Restore a backup that ``backup.verify`` has passed (``manifest`` is
    its manifest): read its chats, ask ``confirm(plan)``, then keep them all
    as imported chats. Returns the counts, or None if the answer was no.
    The decrypted copy and any provider reader live only inside this call."""
    import zipfile
    from . import backup
    from .backup_reader import BackupReader
    store = store or ImportedStore()
    with backup.opened(path, passphrase, cancelled) as plain, zipfile.ZipFile(plain) as archive, \
            BackupReader(archive, manifest, cancelled=cancelled) as reader:
        sessions = reader.sessions()
        counts: Dict[str, int] = {}
        for session in sessions:
            counts[session.provider] = counts.get(session.provider, 0) + 1
        computer = manifest.get("computer") or {}
        plan = RestorePlan(str(computer.get("name") or ""), str(manifest.get("created") or ""),
                           counts, list(reader.notes))
        if not sessions or not confirm(plan):
            return None if sessions else {"added": 0, "updated": 0, "unchanged": 0, "failed": 0}
        return import_sessions(store, reader, sessions, backup_name=Path(path).name,
                               cancelled=cancelled, progress=progress)


def restore_summary(counts: Dict[str, int]) -> str:
    """What's said when a restore finishes."""
    def n(count, word):
        return f"{count} {word}{'s' if count != 1 else ''}"
    parts = []
    if counts.get("added"):
        parts.append(f"{n(counts['added'], 'chat')} restored")
    if counts.get("updated"):
        parts.append(f"{n(counts['updated'], 'chat')} updated")
    if counts.get("unchanged"):
        parts.append(f"{counts['unchanged']} already here")
    if counts.get("failed"):
        parts.append(f"{counts['failed']} couldn't be read")
    if not parts:
        return "The backup has no chats to restore."
    return ("Restore finished: " + ", ".join(parts) + ". They're in the session list, "
            "marked as imported.")
