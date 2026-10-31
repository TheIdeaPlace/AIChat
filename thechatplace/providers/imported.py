"""Chats restored from a backup (#436), as a backend of their own.

Their rows have ``provider="imported"`` so nothing can route one to the
Claude, Codex or Copilot runners: the controller sends every read here, and
the only things they can do are be read, searched, exported and removed.
The AI each was with is kept for the Provider column and for View, Show
Sessions (``member_providers``), as an agent conversation's are (#248).
"""
from __future__ import annotations

from ..imported_store import ImportedStore
from ..sessions import IDLE, SessionInfo
from ..transcript import Transcript
from .base import SessionCapabilities, Snapshot

IMPORTED = "imported"
SOURCE = "imported"
PROVIDER_NAMES = {"claude": "Claude", "codex": "Codex", "copilot": "GitHub Copilot CLI"}


class StoredReader:
    """An imported chat's history, read once from The Chat Place's folder.
    It never changes, so refresh only says whether it was loaded."""

    def __init__(self, store: ImportedStore, key: str):
        self._store, self._key = store, key
        self.transcript = Transcript()
        self._loaded = False

    def refresh(self) -> bool:
        if self._loaded:
            return False
        self.transcript = self._store.load(self._key)
        self._loaded = True
        return True


class ImportedChats:
    provider_id = IMPORTED

    def __init__(self, store: ImportedStore = None):
        self.store = store or ImportedStore()

    def info(self, chat) -> SessionInfo:
        name = PROVIDER_NAMES.get(chat.provider, chat.provider.capitalize())
        return SessionInfo(
            source=SOURCE, key=chat.key, title=chat.title, cwd=chat.cwd,
            cli_session_id=chat.native_id, last_activity_ms=int(chat.last_activity_ms),
            created_ms=int(chat.created_ms), state=IDLE, archived=bool(chat.archived),
            provider=IMPORTED, provider_label=name, member_providers=(chat.provider,),
            detail=f"imported from {chat.computer_name or 'another computer'}")

    def collect(self) -> Snapshot:
        return Snapshot(sessions=[self.info(chat) for chat in self.store.all()])

    def has_history(self, session: SessionInfo) -> bool:
        return self.store.get(session.key) is not None

    def open_reader(self, session: SessionInfo):
        if session.provider != IMPORTED or self.store.get(session.key) is None:
            return None
        return StoredReader(self.store, session.key)

    def fill_last_messages(self, sessions) -> None:
        """The Last message column (discovery asks when it's shown)."""
        for info in sessions:
            info.last_message = self.last_reply(info)

    def last_reply(self, session: SessionInfo) -> str:
        try:
            reply = self.store.load(session.key).last_reply()
        except (LookupError, ValueError):
            return ""
        return reply.first_line() if reply is not None else ""

    def capabilities(self, session: SessionInfo) -> SessionCapabilities:
        # Read only: no send, steer, fork, attachments, approvals or links.
        # "delete" removes The Chat Place's copy, never anything else.
        return SessionCapabilities(read=True, delete=True, changes=True)

    def remove(self, session: SessionInfo) -> bool:
        return self.store.remove(session.key)
