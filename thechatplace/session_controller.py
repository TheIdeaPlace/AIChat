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
                snapshot = self._discovery.collect(order, last_messages=last_messages)
                if self._closed:
                    raise ValueError("Session controller closed")
                self._merge_owned(snapshot, owned)
                for provider_id, provider in self._providers.items():
                    if provider_id == "claude":
                        continue
                    if last_messages:
                        cached = getattr(provider, "cached_last_messages", None)
                        if cached is not None:
                            cached(s for s in snapshot.sessions if s.provider == provider_id)
                    else:
                        forget = getattr(provider, "forget_last_messages", None)
                        if forget is not None:
                            forget()
                        for info in snapshot.sessions:
                            if info.provider == provider_id:
                                info.last_message = ""
                if self._closed:
                    raise ValueError("Session controller closed")
                snapshot.sessions = sort_sessions(snapshot.sessions, order)
                return snapshot
            finally:
                self._claude_discovery.collect_current = None

    def add_provider(self, provider: ConversationProvider):
        """A backend that needs the controller itself, such as agent
        conversations reading their members through it (#248). It has no
        discovery of its own: the window builds its rows."""
        if provider.provider_id in self._providers:
            raise ValueError(f"Duplicate provider: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def _merge_owned(self, snapshot, owned):
        """Overlay TCP metadata without granting writes or loading a thread.

        Foreign ownership lives in companion stores. Disabled providers must
        not reappear just because their records are still saved. Missing catalog
        rows stay available as read-only entries with explicitly unknown status;
        stored idle state cannot establish what another client is doing (#184).
        """
        indexed = {info.identity: info for info in snapshot.sessions}
        seen = set()
        for record in owned:
            if not isinstance(record, OwnSession):
                raise ValueError("Invalid owned session record")
            if record.identity in seen:
                raise ValueError("Duplicate owned session identity")
            seen.add(record.identity)
            if record.provider == "claude" or record.provider not in self._providers:
                continue
            info = indexed.get(record.identity)
            if info is None:
                info = record.to_info()
                info.state, info.detail = STATUS_UNAVAILABLE, ""
                snapshot.sessions.append(info)
                indexed[record.identity] = info
            info.source = OWN
            info.title = record.title
            info.unread = record.unread

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

    def can_create(self, provider):
        backend = self._providers.get(provider)
        return not self._closed and backend is not None and bool(getattr(backend, "can_create", lambda: False)())

    def usage_lines(self, session):
        backend = self._provider(session)
        method = getattr(backend, 'usage_lines', None)
        if not callable(method):
            raise ValueError('This provider cannot report usage')
        return method(session)

    def usage_warnings(self, session):
        method = getattr(self._provider(session), 'usage_warnings', None)
        return method(session) if callable(method) else ()

    def saved_rule_count(self, provider, cwd):
        backend = self._providers.get(provider)
        if self._closed or backend is None or not callable(getattr(backend, 'saved_rule_count', None)):
            raise ValueError("This provider cannot check saved command rules")
        return backend.saved_rule_count(cwd)

    def saved_rules(self, provider, cwd):
        backend = self._providers.get(provider)
        if self._closed or backend is None or not callable(getattr(backend, 'saved_rules', None)):
            raise ValueError("This provider cannot read saved command rules")
        return backend.saved_rules(cwd)

    def model_choices(self, provider):
        backend = self._providers.get(provider)
        if self._closed or backend is None or not callable(getattr(backend, 'model_choices', None)):
            raise ValueError("This provider cannot list model choices")
        return backend.model_choices()

    def create_owned(self, provider, cwd, title, **options):
        if not self.can_create(provider):
            raise ValueError("This provider cannot create chats")
        return self._providers[provider].create_owned(cwd, title, **options)

    def fork_owned(self, source, title, **options):
        if not self.capabilities(source).fork:
            raise ValueError("This provider cannot continue this chat")
        return self._provider(source).fork_owned(source, title, **options)

    def owned_action(self, action, record, *args):
        if action not in ("prepare", "reopen", "send", "steer", "steer_queued", "stop", "answer", "ready", "prepare_policy", "finish_policy",
                          "chat_rule_candidate", "allow_for_chat", "chat_rules", "remove_chat_rule", "review_history",
                          "recover", "set_mode", "set_free"):
            raise ValueError("Unsupported owned chat action")
        if not isinstance(record, OwnSession) or record.provider == "claude":
            raise ValueError("Foreign owned chat required")
        backend = self._provider(record.to_info())
        method = getattr(backend, "owned_ready" if action == "ready" else action + "_owned", None)
        if not callable(method):
            raise ValueError("This provider cannot perform that action")
        return method(record, *args)

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
