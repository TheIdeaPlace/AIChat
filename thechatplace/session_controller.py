"""Route conversation operations by agent backend, without importing wx.

The window owns presentation and refresh scheduling. Backend adapters own
history access; reading a session must never resume it or start inference.
"""
from __future__ import annotations

from typing import Iterable
from copy import deepcopy
import threading

from .providers.base import ConversationProvider, ConversationReader, SessionCapabilities
from .providers.discovery import DiscoveryRegistry
from .sessions import OWN, STATUS_UNAVAILABLE, SessionInfo, sort_sessions
from .own_store import OwnSession
from .transcript import Transcript


class _ClaudeDiscovery:
    provider_id = "claude"

    def __init__(self):
        self.collect_current = None

    def collect(self):
        return self.collect_current()


class SessionController:
    def __init__(self, providers: Iterable[ConversationProvider]):
        self._closed = False
        self._close_lock = threading.Lock()
        self._cleanup_failed = False
        self._discovery_lock = threading.Lock()
        self._claude_discovery = _ClaudeDiscovery()
        self._providers: dict[str, ConversationProvider] = {}
        for provider in providers:
            if provider.provider_id in self._providers:
                raise ValueError(f"Duplicate provider: {provider.provider_id}")
            self._providers[provider.provider_id] = provider
        self._discovery = DiscoveryRegistry([
            self._claude_discovery,
            *(p for p in self._providers.values()
              if p.provider_id != "claude" and callable(getattr(p, "collect", None)))])

    def collect(self, claude_collect, order, *, owned=(), last_messages=False):
        """Merge current Claude inputs with registered backend discovery.

        The window supplies an immutable-input callback for each pass. Shared
        foreign adapters use the same connection for discovery and history.
        Serialize callback replacement with collection so overlapping callers
        cannot use another refresh's ownership/runtime inputs (#184) (#171).
        """
        if not callable(claude_collect):
            raise ValueError("Claude discovery callback required")
        owned = deepcopy(tuple(owned))
        with self._discovery_lock:
            if self._closed:
                raise ValueError("Session controller closed")
            self._claude_discovery.collect_current = claude_collect
            try:
                snapshot = self._discovery.collect(order)
                if self._closed:
                    raise ValueError("Session controller closed")
                for provider_id, provider in self._providers.items():
                    if provider_id == "claude":
                        continue
                    if last_messages and provider_id not in snapshot.stale_providers:
                        fill = getattr(provider, "fill_last_messages", None)
                        if fill is not None:
                            fill(s for s in snapshot.sessions if s.provider == provider_id)
                    elif last_messages:
                        cached = getattr(provider, "cached_last_messages", None)
                        if cached is not None:
                            cached(s for s in snapshot.sessions if s.provider == provider_id)
                    elif not last_messages:
                        forget = getattr(provider, "forget_last_messages", None)
                        if forget is not None:
                            forget()
                if self._closed:
                    raise ValueError("Session controller closed")
                snapshot.sessions = sort_sessions(snapshot.sessions, order)
                return snapshot
            finally:
                self._claude_discovery.collect_current = None

    def _provider(self, session: SessionInfo) -> ConversationProvider:
        if self._closed:
            raise ValueError("Session controller closed")
        try:
            return self._providers[session.provider]
        except KeyError:
            raise ValueError(f"Provider unavailable: {session.provider}") from None

    def open_reader(self, session: SessionInfo) -> ConversationReader | None:
        return self._provider(session).open_reader(session)

    def capabilities(self, session: SessionInfo) -> SessionCapabilities:
        provider = self._provider(session)
        capabilities = getattr(provider, "capabilities", None)
        if capabilities is None:
            return SessionCapabilities()  # an older reader grants no write actions
        result = capabilities(session)
        if (not isinstance(result, SessionCapabilities)
                or any(type(value) is not bool for value in vars(result).values())):
            raise ValueError("Invalid provider capabilities")
        return result

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
