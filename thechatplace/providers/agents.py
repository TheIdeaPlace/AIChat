"""Agent conversations as a backend (#248): read only, and from the members.

A conversation has no history of its own. Its reader reads each member chat
through that chat's own provider and merges them (agents.combine), so the
window reads it like any other chat. Sending goes through each member's own
provider too, from the window (ui/agent_chats.py); nothing here starts a turn.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from ..agents import AGENTS, AgentConversationStore, combine
from ..sessions import SessionInfo
from ..transcript import Transcript
from .base import SessionCapabilities


class CombinedReader:
    """Reads every member and merges them. A member that can't be read is
    said in the list, and the others stay readable."""

    def __init__(self, store: AgentConversationStore, conversation_id: str,
                 member_info: Callable, open_member: Callable):
        self._store = store
        self._id = conversation_id
        self._member_info = member_info
        self._open_member = open_member
        self._readers: dict = {}
        self._signature = None
        self.transcript = Transcript()

    _GONE = object()

    def _reader(self, member):
        """The member's reader; _GONE when its chat is gone; None while it
        has no history yet (a new chat before its first turn), asked again
        next time."""
        reader = self._readers.get(member.identity)
        if reader is None:
            info = self._member_info(member)
            if info is None:
                return self._GONE
            reader = self._open_member(info)
            if reader is not None:
                self._readers[member.identity] = reader
        return reader

    def refresh(self) -> bool:
        conversation = self._store.get(self._id)
        if conversation is None:
            raise LookupError("This agent conversation is gone.")
        histories: List[Optional[list]] = []
        changed = self._signature is None
        for member in conversation.members:
            try:
                reader = self._reader(member)
                if reader is self._GONE:
                    histories.append(None)
                    continue
                if reader is None:
                    histories.append([])
                    continue
                changed = reader.refresh() or changed
                histories.append(list(reader.transcript.messages))
            except Exception:  # noqa: BLE001 - one member mustn't hide the rest
                self._readers.pop(member.identity, None)
                histories.append(None)
        signature = (tuple(-1 if h is None else len(h) for h in histories), len(conversation.notes),
                     tuple(m.name for m in conversation.members))
        changed = changed or signature != self._signature
        self._signature = signature
        if changed:
            self.transcript = Transcript(messages=combine(conversation, histories))
        return changed


class AgentConversations:
    """The provider for conversation rows: readable and, through the window,
    sendable; nothing else (no fork, delete, usage or approvals of its own)."""

    provider_id = AGENTS

    def __init__(self, store: AgentConversationStore, member_info: Callable,
                 open_member: Callable):
        self.store = store
        self._member_info = member_info
        self._open_member = open_member

    def _check(self, session: SessionInfo):
        if session.provider != AGENTS:
            raise ValueError("Not an agent conversation")

    def capabilities(self, session: SessionInfo) -> SessionCapabilities:
        self._check(session)
        return SessionCapabilities(read=True, send=True)

    def has_history(self, session: SessionInfo) -> bool:
        self._check(session)
        return self.store.by_key(session.key) is not None

    def open_reader(self, session: SessionInfo):
        self._check(session)
        conversation = self.store.by_key(session.key)
        if conversation is None:
            return None
        return CombinedReader(self.store, conversation.conversation_id, self._member_info,
                              self._open_member)

    def last_reply(self, session: SessionInfo) -> str:
        self._check(session)
        return ""
