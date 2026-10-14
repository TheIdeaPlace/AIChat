"""Route conversation operations by agent backend, without importing wx.

The window owns presentation and refresh scheduling. Backend adapters own
history access; reading a session must never resume it or start inference.
"""
from __future__ import annotations

from typing import Iterable
import threading

from .providers.base import ConversationProvider, ConversationReader
from .sessions import SessionInfo
from .own_store import OwnSession
from .transcript import Transcript


class SessionController:
    def __init__(self, providers: Iterable[ConversationProvider]):
        self._closed = False
        self._close_lock = threading.Lock()
        self._cleanup_failed = False
        self._providers: dict[str, ConversationProvider] = {}
        for provider in providers:
            if provider.provider_id in self._providers:
                raise ValueError(f"Duplicate provider: {provider.provider_id}")
            self._providers[provider.provider_id] = provider

    def _provider(self, session: SessionInfo) -> ConversationProvider:
        if self._closed:
            raise ValueError("Session controller closed")
        try:
            return self._providers[session.provider]
        except KeyError:
            raise ValueError(f"Provider unavailable: {session.provider}") from None

    def open_reader(self, session: SessionInfo) -> ConversationReader | None:
        return self._provider(session).open_reader(session)

    def has_history(self, session: SessionInfo) -> bool:
        return self._provider(session).has_history(session)

    def read(self, session: SessionInfo) -> Transcript | None:
        reader = self.open_reader(session)
        if reader is None:
            return None
        reader.refresh()
        return reader.transcript

    def last_reply(self, session: SessionInfo) -> str:
        return self._provider(session).last_reply(session)

    def open_reader_with_facts(self, session):
        backend = self._provider(session)
        open_reader = getattr(backend, "open_reader_with_facts", None)
        return open_reader(session) if callable(open_reader) else (self.open_reader(session), "", 0)

    def close(self):
        """Close every registered backend even if one cleanup fails."""
        with self._close_lock:
            if not self._closed:
                self._closed = True
                for provider in self._providers.values():
                    close = getattr(provider, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception:  # noqa: BLE001 - clean up remaining providers
                            self._cleanup_failed = True
            if self._cleanup_failed:
                raise RuntimeError("Session provider cleanup failed")
