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
    data = dataclasses.asdict(transcript)
    # File checkpoints (#468) belong to the original's Claude Code session;
    # a restored chat is read-only and can't put files back.
    data.pop("checkpoints", None)
    return data


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
    and one file per chat with its history.

    The index is kept in memory and read again only when its file changes,
    since the list asks for it on every refresh. Each chat file holds its own
    record too, so a missing or damaged index is rebuilt from them, never
    written over: losing the index must not lose the chats (#449 review)."""

    def __init__(self, folder: Optional[Path] = None):
        self.folder = Path(folder) if folder else platform_paths.app_data_dir() / FOLDER
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, dict]] = None
        self._cache_stamp = None

    def _chat_file(self, key: str) -> Path:
        # The key is "imported:" + 64 hex digits; only the digits name the file.
        digest = key.split(":", 1)[-1] if isinstance(key, str) else ""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid imported chat key")
        return self.folder / f"{digest}.json"

    def _write(self, path: Path, payload) -> None:
        from .provider_storage import atomic_write_json
        self.folder.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)

    def _stamp(self):
        try:
            info = (self.folder / INDEX).stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def _rebuild(self) -> Dict[str, dict]:
        """The index from the chat files themselves."""
        chats: Dict[str, dict] = {}
        for path in sorted(self.folder.glob("*.json")):
            if path.name == INDEX:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                record = raw["chat"]
                if raw.get("key") == record.get("key") and self._chat_file(raw["key"]) == path:
                    chats[raw["key"]] = record
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return chats

    def _index(self) -> Dict[str, dict]:
        stamp = self._stamp()
        if self._cache is not None and stamp == self._cache_stamp:
            return self._cache
        chats = None
        if stamp is not None:
            try:
                raw = json.loads((self.folder / INDEX).read_text(encoding="utf-8"))
                found = raw.get("chats") if isinstance(raw, dict) else None
                chats = found if isinstance(found, dict) else None
            except (OSError, ValueError):
                chats = None
        if chats is None:
            chats = self._rebuild() if self.folder.is_dir() else {}
            if chats:
                try:
                    self._save_index(chats)
                    stamp = self._stamp()
                except OSError:
                    pass
        self._cache, self._cache_stamp = chats, stamp
        return chats

    def _save_index(self, chats: Dict[str, dict]) -> None:
        self._write(self.folder / INDEX, {"format_version": FORMAT_VERSION, "chats": chats})
        self._cache, self._cache_stamp = chats, self._stamp()

    @staticmethod
    def _chat(key: str, raw) -> Optional[ImportedChat]:
        try:
            chat = ImportedChat(**_known(ImportedChat, raw))
        except TypeError:
            return None
        return chat if chat.key == key else None

    def all(self) -> List[ImportedChat]:
        with self._lock:
            return [chat for key, raw in self._index().items()
                    if (chat := self._chat(key, raw)) is not None]

    def get(self, key: str) -> Optional[ImportedChat]:
        with self._lock:
            raw = self._index().get(key)
            return self._chat(key, raw) if raw is not None else None

    def last_reply(self, key: str) -> str:
        """The last reply's first line, kept in the index (for the list's
        Last message column, without reading every history)."""
        with self._lock:
            raw = self._index().get(key) or {}
            return str(raw.get("last_reply") or "")

    def load(self, key: str) -> Transcript:
        """A chat's history. Raises LookupError if it's gone, ValueError if
        its file is damaged."""
        with self._lock:
            path = self._chat_file(key)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise LookupError("This imported chat's history is gone.") from None
            except (OSError, ValueError) as exc:
                raise ValueError(f"This imported chat's history couldn't be read: {exc}") from None
            if not isinstance(raw, dict) or raw.get("key") != key:
                raise ValueError("This imported chat's history is damaged.")
            try:
                return transcript_from_json(raw.get("transcript"))
            except (TypeError, AttributeError) as exc:
                raise ValueError(f"This imported chat's history is damaged: {exc}") from None

    def _readable(self, key: str) -> bool:
        try:
            self.load(key)
            return True
        except (LookupError, ValueError):
            return False

    def save(self, chat: ImportedChat, transcript: Transcript, *, write_index: bool = True) -> str:
        """Add or update one chat: "added", "updated" or "unchanged". A copy
        from an older backup never replaces a newer one already here; one
        whose file is gone or damaged, or whose title changed, is replaced.
        With ``write_index`` False the index is written later by
        ``flush`` (a restore writes it once, not once per chat)."""
        if chat.key != chat_key(*chat.identity):
            raise ValueError("Imported chat key doesn't match its identity")
        with self._lock:
            index = self._index()
            old = index.get(chat.key)
            if old is not None and self._readable(chat.key):
                old_last = int(old.get("last_activity_ms") or 0)
                older = (old_last > chat.last_activity_ms
                         or (old_last == chat.last_activity_ms
                             and int(old.get("messages") or 0) >= len(transcript.messages)
                             and old.get("title") == chat.title))
                if older:
                    return "unchanged"
            reply = transcript.last_reply()
            chat = dataclasses.replace(
                chat, messages=len(transcript.messages),
                imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self._write(self._chat_file(chat.key), {
                "format_version": FORMAT_VERSION, "key": chat.key,
                "chat": dataclasses.asdict(chat),
                "transcript": transcript_to_json(transcript)})
            index = dict(index)
            index[chat.key] = {**dataclasses.asdict(chat),
                               "last_reply": reply.first_line() if reply is not None else ""}
            if write_index:
                self._save_index(index)
            else:
                self._cache = index    # written by flush()
            return "updated" if old is not None else "added"

    def flush(self) -> None:
        """Write the index after saves made with ``write_index=False``."""
        with self._lock:
            if self._cache is not None:
                self._save_index(self._cache)

    def remove(self, key: str) -> bool:
        """Delete The Chat Place's copy of an imported chat (nothing else)."""
        with self._lock:
            index = dict(self._index())
            if index.pop(key, None) is None:
                return False
            self._save_index(index)
            try:
                self._chat_file(key).unlink()
            except FileNotFoundError:
                pass
            return True


def import_sessions(store: ImportedStore, reader, sessions, *, backup_name: str = "",
                    cancelled=None, progress=None, results=None) -> Dict[str, int]:
    """Read each of ``sessions`` from an open backup_reader.BackupReader and
    keep it in ``store``. Returns how many were added, updated, unchanged
    and failed (one that can't be read doesn't stop the rest)."""
    manifest = reader.manifest
    computer = manifest.get("computer") or {}
    counts = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0}
    sessions = list(sessions)
    try:
        for done, session in enumerate(sessions):
            if cancelled is not None and cancelled():
                raise InterruptedError("Restore cancelled.")
            if progress is not None:
                progress(done, len(sessions), session.title)
            before = dict(counts)
            _import_one(store, reader, session, manifest, computer, backup_name, counts)
            if results is not None:
                results[session.key] = next(k for k in counts if counts[k] != before[k])
            if done % INDEX_EVERY == INDEX_EVERY - 1:
                store.flush()
    finally:
        # Whatever was saved is listed, even after a stop or an error.
        store.flush()
    if progress is not None:
        progress(len(sessions), len(sessions), "")
    return counts


# A restore writes the index after this many chats, and at the end, rather
# than after every one (each write is the whole index).
INDEX_EVERY = 50


def _import_one(store, reader, session, manifest, computer, backup_name, counts) -> None:
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
        counts[store.save(chat, transcript, write_index=False)] += 1
    except InterruptedError:
        raise
    except Exception:  # noqa: BLE001 - one chat mustn't stop the others
        counts["failed"] += 1
    finally:
        # Saved: its parsed history needn't stay in memory for the rest.
        release = getattr(reader, "release", None)
        if release is not None:
            release(session)


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
        text += "\n\nSome of it couldn't be read. " + notes_text(plan.notes)
    return text


def notes_text(notes, limit: int = 3) -> str:
    """The reader's notes as sentences: each once, the first few, and how
    many more."""
    unique = []
    for note in notes:
        note = str(note).strip()
        if note and not note.endswith((".", "!", "?")):
            note += "."
        if note and note not in unique:
            unique.append(note)
    text = " ".join(unique[:limit])
    if len(unique) > limit:
        text += f" And {len(unique) - limit} more."
    return text


@dataclass
class RestoreOutcome:
    counts: Dict[str, int]
    notes: List[str] = field(default_factory=list)
    # What the backup's own Chat Place had for the restored chats (#450):
    # imported key -> its group names there, and the keys it had hidden.
    # The window applies these; a worker never writes the window's stores.
    groups: Dict[str, List[str]] = field(default_factory=dict)
    hidden: List[str] = field(default_factory=list)


def _backup_metadata(reader, sessions):
    """Each restored chat's groups and hidden state in the backup, by its
    imported key, read through the stores' own readers from the backup's
    temporary extracted copy of The Chat Place's folder (which their loading
    may tidy; it's deleted afterwards)."""
    from .groups import GroupStore
    from .hidden import HiddenStore
    groups: Dict[str, List[str]] = {}
    hidden: List[str] = []
    try:
        data = reader.data_dir
        their_groups = GroupStore(data / "groups.json")
        their_hidden = HiddenStore(data / "hidden.json")
    except Exception:  # noqa: BLE001 - a backup without them restores without them
        return groups, hidden
    for session in sessions:
        old = session.source_key
        if not old:
            continue
        try:
            names = [] if their_groups.load_error else their_groups.groups_of(old)
            if names:
                groups[session.key] = list(names)
            if not their_hidden.load_error and old in their_hidden:
                hidden.append(session.key)
        except Exception:  # noqa: BLE001 - one chat's mustn't stop the rest
            continue
    return groups, hidden


def chooser_rows(sessions) -> List[str]:
    """One line per chat for the chooser: title, AI, folder, last active."""
    from .export import chat_row
    from .providers.imported import PROVIDER_NAMES
    return [chat_row(session.title, PROVIDER_NAMES.get(session.provider, session.provider),
                     session.cwd, session.last_activity_ms) for session in sessions]


def restore_backup(path, manifest: dict, passphrase: Optional[str], *, confirm,
                   store: Optional[ImportedStore] = None, cancelled=None,
                   progress=None) -> Optional[RestoreOutcome]:
    """Restore a backup that ``backup.verify`` has passed (``manifest`` is
    its manifest): read its chats, ask ``confirm(plan, sessions)`` which to
    restore (True for all, a list of them, or None/False for none), then
    keep those as imported chats. Returns the counts, the reader's notes and
    the backup's groups and hidden state for them, or None if none were
    chosen.
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
        if not sessions:
            # Nothing readable: say why (a Codex-only backup on a computer
            # without Codex, say), rather than "no chats".
            return RestoreOutcome({"added": 0, "updated": 0, "unchanged": 0, "failed": 0},
                                  list(reader.notes))
        if cancelled is not None and cancelled():
            raise InterruptedError("Restore cancelled.")
        # Newest first, as the session list is: easier to find one by.
        sessions = sorted(sessions, key=lambda s: int(s.last_activity_ms or 0), reverse=True)
        answer = confirm(plan, sessions)
        if not answer:
            return None
        offered = {id(s) for s in sessions}
        chosen = sessions if answer is True else [s for s in answer if id(s) in offered]
        if not chosen:
            return None
        results: Dict[str, str] = {}
        counts = import_sessions(store, reader, chosen, backup_name=Path(path).name,
                                 cancelled=cancelled, progress=progress, results=results)
        # Only chats restored for the first time get the backup's groups and
        # hidden state: one already here keeps whatever's been done to it
        # since, and one that failed has nothing to put in a group (#453).
        added = [s for s in chosen if results.get(s.key) == "added"]
        groups, hidden = _backup_metadata(reader, added)
        return RestoreOutcome(counts, list(reader.notes), groups, hidden)


def restore_summary(outcome) -> str:
    """What's said when a restore finishes."""
    counts = outcome.counts if isinstance(outcome, RestoreOutcome) else outcome
    notes = outcome.notes if isinstance(outcome, RestoreOutcome) else []
    def n(count, word):
        return f"{count} {word}{'s' if count != 1 else ''}"
    parts = []
    # Hidden counts only newly restored chats, so it follows "restored".
    hidden = len(outcome.hidden) if isinstance(outcome, RestoreOutcome) else 0
    if counts.get("added"):
        parts.append(f"{n(counts['added'], 'chat')} restored")
        if hidden:
            parts.append(f"{hidden} of them hidden, as {'it was' if hidden == 1 else 'they were'} "
                         "(View, Show Sessions, Hidden)")
    if counts.get("updated"):
        parts.append(f"{n(counts['updated'], 'chat')} updated")
    if counts.get("unchanged"):
        parts.append(f"{counts['unchanged']} already here")
    if counts.get("failed"):
        parts.append(f"{counts['failed']} couldn't be read")
    if not parts:
        if notes:
            return "Nothing could be restored. " + notes_text(notes)
        return "The backup has no chats to restore."
    return ("Restore finished: " + ", ".join(parts) + ". They're in the session list, "
            "marked as imported.")
