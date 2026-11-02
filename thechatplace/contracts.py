"""What the window and the service know about any AI: plain data, no wx.

This is the foundation #489 asks for. Native routes, ACP agents and plain chat
models all describe themselves in these terms, and the shared UI reads only
these terms, so a new integration needs no new branch in the UI.

Two rules run through it, from the review of #489:

* What an agent sends is information, never authorization. Its labels can
  describe an operation; TCP's own policy (``policy.py``) decides which actions
  are offered and what each one means.
* A capability has three levels: what the integration implements, what this
  agent advertises, and what this chat may do. Only all three together enable
  anything (``effective_capabilities``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple


class Capability(str, Enum):
    READ = "read"
    SEND = "send"
    STOP = "stop"
    RESUME = "resume"          # send again after the app restarts
    APPROVALS = "approvals"
    QUESTIONS = "questions"
    OPTIONS = "options"
    USAGE = "usage"
    PLANS = "plans"


#: Actions that change or continue a chat, so only a chat this app started
#: may have them. Everything else stays read-only.
OWNER_ONLY = frozenset({Capability.SEND, Capability.STOP, Capability.RESUME,
                        Capability.APPROVALS, Capability.QUESTIONS, Capability.OPTIONS})


def effective_capabilities(implemented: FrozenSet[Capability], advertised: Optional[FrozenSet[Capability]],
                           owned: bool) -> FrozenSet[Capability]:
    """What this chat may do. ``advertised`` None means the integration has no
    separate advertisement: it speaks for itself. An agent advertising a feature
    can't establish ownership, and a feature no one implemented stays off."""
    allowed = implemented if advertised is None else implemented & advertised
    return frozenset(allowed) if owned else frozenset(allowed - OWNER_ONLY)


@dataclass(frozen=True)
class ChatRef:
    """Native identity: which integration, and that integration's own id. Two
    agents returning the same session id stay two chats."""
    integration: str
    native_id: str

    @property
    def key(self) -> str:
        return f"{self.integration}:{self.native_id}"


@dataclass(frozen=True)
class ChatInfo:
    ref: ChatRef
    title: str
    folder: str = ""
    provider: str = ""       # which AI, as a display name from its integration
    kind: str = ""           # where the chat started
    owned: bool = False
    updated: float = 0.0
    capabilities: FrozenSet[Capability] = frozenset()
    read_only_note: str = ""


@dataclass(frozen=True)
class Message:
    role: str                # "user", "assistant" or "note"
    text: str


# -- describing choices ----------------------------------------------------------

@dataclass(frozen=True)
class OptionValue:
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class OptionGroup:
    """One setting. The dialogs are built from these, for every integration."""
    id: str
    label: str
    values: Tuple[OptionValue, ...]
    current: str = ""
    description: str = ""


# -- what a running turn reports -------------------------------------------------

class Decision(str, Enum):
    ALLOW_ONCE = "allow_once"
    DENY = "deny"


#: TCP's own words. A permission button is never labelled by an agent, which
#: could call a dangerous choice "allow once".
DECISION_LABELS: Mapping[Decision, str] = {Decision.ALLOW_ONCE: "Allow Once", Decision.DENY: "Deny"}


@dataclass(frozen=True)
class PermissionRequest:
    id: str
    summary: str             # TCP's own words for what's being asked
    detail: str = ""         # what the agent says it wants to do, shown as its words
    decisions: Tuple[Decision, ...] = (Decision.DENY,)
    #: Set when this agent's handling of a decision hasn't been checked.
    caution: str = ""


@dataclass(frozen=True)
class QuestionItem:
    id: str
    prompt: str
    choices: Tuple[OptionValue, ...] = ()
    allow_other: bool = True


@dataclass(frozen=True)
class QuestionRequest:
    id: str
    items: Tuple[QuestionItem, ...]


class TurnStatus(str, Enum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class ReplyText:
    """``final`` text replaces what the same ``block`` has streamed so far."""
    text: str
    block: str = ""
    final: bool = False


@dataclass(frozen=True)
class Activity:
    text: str


@dataclass(frozen=True)
class PlanUpdate:
    steps: Tuple[Tuple[str, str], ...]   # (step, status)


@dataclass(frozen=True)
class UsageUpdate:
    used: int
    size: int


@dataclass(frozen=True)
class Notice:
    text: str


@dataclass(frozen=True)
class AskPermission:
    request: PermissionRequest


@dataclass(frozen=True)
class AskQuestion:
    request: QuestionRequest


@dataclass(frozen=True)
class UnknownUpdate:
    """Something the integration didn't understand (#466). The service tells
    the chat once; it can never become a way of approving anything."""
    name: str


@dataclass(frozen=True)
class OptionsChanged:
    pass


@dataclass(frozen=True)
class TurnEnded:
    status: TurnStatus
    message: str = ""        # safe to show: never an agent's own error text


Emit = Callable[[object], None]


# -- errors ----------------------------------------------------------------------

class IntegrationError(Exception):
    """The message is safe to show: written here, not copied from an agent,
    whose error text may hold paths or account names."""


class Refused(IntegrationError):
    """A rule of this app said no, before anything was sent."""


# -- what every integration provides --------------------------------------------

@dataclass(frozen=True)
class Status:
    installed: bool
    summary: str
    #: What the user can do about it, such as how to sign in.
    hint: str = ""
    #: Whose account or bill a turn goes to, and whether that's been checked.
    billing: str = ""
    verified: bool = False
    version: str = ""


@dataclass
class OwnedChat:
    """A chat this app started, as the store keeps it."""
    ref: ChatRef
    title: str
    folder: str
    options: Dict[str, str] = field(default_factory=dict)
    turns: int = 0
    created: float = 0.0
    updated: float = 0.0


class Integration(ABC):
    id: str = ""
    name: str = ""
    implemented: FrozenSet[Capability] = frozenset()
    #: True when the app keeps this chat's history itself, because the AI
    #: offers no safe way to read it back.
    app_keeps_history: bool = False
    enabled_by_default: bool = False

    @abstractmethod
    def status(self) -> Status:
        """Check installation without starting a turn."""

    def advertised(self) -> Optional[FrozenSet[Capability]]:
        return None

    def capabilities_for(self, chat: OwnedChat) -> FrozenSet[Capability]:
        return effective_capabilities(self.implemented, self.advertised(), True)

    def list_foreign(self) -> List[ChatInfo]:
        """Chats started elsewhere. Always read-only."""
        return []

    def read_history(self, ref: ChatRef) -> List[Message]:
        return []

    def new_chat_options(self) -> Tuple[OptionGroup, ...]:
        return ()

    def check_prompt(self, text: str) -> Optional[str]:
        """Why this text may not be sent, or None."""
        return None

    @abstractmethod
    def start_chat(self, folder: str, title: str, options: Mapping[str, str]) -> OwnedChat:
        ...

    @abstractmethod
    def run_turn(self, chat: OwnedChat, text: str, emit: Emit) -> None:
        """Run one turn, blocking until it ends, reporting through ``emit`` and
        ending with one ``TurnEnded``. Called on a worker thread."""

    @abstractmethod
    def stop(self, ref: ChatRef) -> None:
        """Ask the running turn to stop; safe from any thread, safe if idle."""

    def answer_permission(self, ref: ChatRef, request_id: str, decision: Decision) -> None:
        raise Refused("This chat isn't waiting for a permission answer.")

    def answer_question(self, ref: ChatRef, request_id: str, answers: Optional[Mapping[str, str]]) -> None:
        """``answers`` None declines to answer."""
        raise Refused("This chat isn't waiting for an answer.")

    def chat_options(self, chat: OwnedChat) -> Tuple[OptionGroup, ...]:
        return ()

    def set_option(self, chat: OwnedChat, group: str, value: str) -> None:
        raise Refused("This chat has no options.")

    def close(self) -> None:
        pass
