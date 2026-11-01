"""The discovery boundary: plain data, independent of the window."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Protocol, Set

from ..desktop_groups import DesktopGroups
from ..sessions import IDLE, LISTED_PROVIDERS, NEEDS_YOU, WORKING, LiveStatus, SessionInfo
from ..transcript import Transcript


@dataclass
class Snapshot:
    sessions: List[SessionInfo] = field(default_factory=list)
    # Claude compatibility data used by existing ownership guards. Other
    # providers must not put their native IDs in these sets or live mapping.
    desktop_cli_ids: Set[str] = field(default_factory=set)
    cowork_cli_ids: Set[str] = field(default_factory=set)
    live: Dict[str, LiveStatus] = field(default_factory=dict)
    unreadable_files: int = 0
    desktop_groups: DesktopGroups = field(default_factory=DesktopGroups)
    # Cached discovery is still useful when one backend is unavailable, but
    # callers must distinguish it from fresh live state (#184) (#171).
    stale_providers: Set[str] = field(default_factory=set)
    provider_errors: Dict[str, str] = field(default_factory=dict)
    newly_failed_providers: Set[str] = field(default_factory=set)

    pending_providers: Set[str] = field(default_factory=set)

    def fresh_states(self, previous=None) -> Dict[str, str]:
        """Comparable states only; outages must not create finished turns.

        Dropping unknown/stale states also prevents a recovery from comparing
        idle with working remembered before the outage (#184) (#171).
        """
        states = {session.key: session.state for session in self.sessions
                if session.provider not in self.stale_providers
                and (session.provider not in LISTED_PROVIDERS
                     or session.state in (IDLE, WORKING, NEEDS_YOU))}
        # A normal background refresh must not erase working->idle comparison.
        # Cached rows themselves never become fresh evidence of completion.
        if previous:
            states.update({session.key: previous[session.key] for session in self.sessions
                           if session.provider in self.pending_providers
                           and session.key in previous})
        return states


class SessionProvider(Protocol):
    provider_id: str

    def collect(self) -> Snapshot:
        """Return discovery results without starting or resuming a turn."""
        ...


@dataclass(frozen=True)
class SessionCapabilities:
    """Explicit backend actions; ownership alone never enables a foreign runner."""
    read: bool = True
    send: bool = False
    fork: bool = False
    delete: bool = False
    attachments: bool = False
    usage: bool = False
    changes: bool = False
    instructions: bool = False
    remote_control: bool = False
    desktop_link: bool = False
    share_link: bool = False
    options: bool = False
    approvals: bool = False
    #: A message into a running turn without stopping it (#238, #239).
    steer: bool = False
    #: Ask Aside: a question about the chat that isn't added to it (#468).
    aside: bool = False
    #: Chat Options: agent, instructions and output style for later turns (#468).
    chat_options: bool = False


class ConversationReader(Protocol):
    @property
    def transcript(self) -> Transcript:
        ...

    def refresh(self) -> bool:
        """Update readable history without starting a turn."""
        ...


class ConversationProvider(Protocol):
    provider_id: str

    def has_history(self, session: SessionInfo) -> bool:
        """Check local availability without network requests or inference."""
        ...

    def open_reader(self, session: SessionInfo) -> ConversationReader | None:
        ...

    def last_reply(self, session: SessionInfo) -> str:
        ...
