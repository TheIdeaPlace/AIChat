"""The one place the window talks to. Routes by integration, and owns the rules
that are the same for all of them: only a chat this app started can be sent to,
a queue per chat, history, and one unknown-update notice per chat (#466).

No wx. Every change is reported to ``listener`` on whatever thread it happened
on, so the window has to hop to its own.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from .contracts import (Activity, AskPermission, AskQuestion, Capability, ChatInfo, ChatRef, Decision, Integration,
                        IntegrationError, Message, Notice, OptionGroup, OptionsChanged, OwnedChat, PermissionRequest,
                        PlanUpdate, QuestionRequest, Refused, ReplyText, Status, TurnEnded, TurnStatus, UnknownUpdate,
                        UsageUpdate)
from .store import Store

KIND_OWNED = "The Chat Place"
STOP_RETRY_SECONDS = 30.0
UNKNOWN_NOTICE = "{name} sent an update The Chat Place can't show yet. Help, AI Providers lists what it didn't understand."


@dataclass
class ChatState:
    messages: List[Message] = field(default_factory=list)
    loaded: bool = False
    busy: bool = False
    queue: List[str] = field(default_factory=list)
    activity: str = ""
    plan: Tuple[Tuple[str, str], ...] = ()
    usage: Optional[Tuple[int, int]] = None
    pending_permission: Optional[PermissionRequest] = None
    pending_question: Optional[QuestionRequest] = None
    unknown_told: bool = False
    #: Counts turns, so a late Stop can't reach the turn after the one it meant.
    generation: int = 0
    #: The block ids streaming now, to the index of their message.
    blocks: Dict[str, int] = field(default_factory=dict)


Listener = Callable[[ChatRef, object], None]


class ChatService:
    def __init__(self, integrations: Sequence[Integration], store: Store, listener: Optional[Listener] = None):
        self.integrations: Dict[str, Integration] = {i.id: i for i in integrations}
        self.store = store
        self.listener: Listener = listener or (lambda ref, event: None)
        self._lock = threading.RLock()
        self._states: Dict[str, ChatState] = {}
        for integration in integrations:
            if integration.app_keeps_history and hasattr(integration, "history"):
                integration.history = store.read_history  # type: ignore[attr-defined]

    # -- what exists ----------------------------------------------------------

    def enabled_integrations(self) -> List[Integration]:
        return [i for i in self.integrations.values() if self.store.enabled(i.id, i.enabled_by_default)]

    def status(self, integration_id: str) -> Status:
        return self.integrations[integration_id].status()

    def set_enabled(self, integration_id: str, value: bool) -> None:
        self.store.set_enabled(integration_id, value)
        if not value:
            self.integrations[integration_id].close()

    def drift_report(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        report = {}
        for integration in self.integrations.values():
            drift = getattr(integration, "drift", None)
            if drift is not None:
                report[integration.id] = drift.snapshot()
        return report

    def list_chats(self) -> Tuple[List[ChatInfo], List[str]]:
        """Every chat, with anything that couldn't be listed as (name) problems.
        One provider failing mustn't take the others' chats with it. Call it
        off the window's thread: discovery may start a program."""
        enabled = self.enabled_integrations()
        chats = [self._owned_info(chat) for chat in self.store.chats(i.id for i in enabled)]
        owned = self.store.owned_keys()
        problems: List[str] = []
        for integration in enabled:
            try:
                foreign = integration.list_foreign()
            except (IntegrationError, OSError):
                problems.append(integration.name)
                continue
            # An integration may also list this app's own chats; ownership comes
            # only from the store, so those rows are dropped here.
            chats.extend(info for info in foreign if info.ref.key not in owned)
        chats.sort(key=lambda info: (-info.updated, info.title.lower()))
        return chats, problems

    def _owned_info(self, chat: OwnedChat) -> ChatInfo:
        integration = self.integrations[chat.ref.integration]
        return ChatInfo(chat.ref, chat.title, chat.folder, integration.name, KIND_OWNED, True, chat.updated,
                        integration.capabilities_for(chat))

    def info(self, ref: ChatRef) -> Optional[ChatInfo]:
        chat = self.store.get_chat(ref)
        return self._owned_info(chat) if chat is not None and ref.integration in self.integrations else None

    # -- chats ----------------------------------------------------------------

    def new_chat_options(self, integration_id: str) -> Tuple[OptionGroup, ...]:
        return self.integrations[integration_id].new_chat_options()

    def start_chat(self, integration_id: str, folder: str, title: str, options: Mapping[str, str]) -> ChatInfo:
        chat = self.integrations[integration_id].start_chat(folder, title.strip() or "New chat", options)
        chat.created = chat.updated = time.time()
        self.store.save_chat(chat)
        with self._lock:
            self._states[chat.ref.key] = ChatState(loaded=True)
        return self._owned_info(chat)

    def state(self, ref: ChatRef) -> ChatState:
        with self._lock:
            return self._states.setdefault(ref.key, ChatState())

    def load_history(self, ref: ChatRef) -> List[Message]:
        """This chat's messages, read once. Off the window's thread."""
        state = self.state(ref)
        with self._lock:
            if state.loaded:
                return list(state.messages)
        integration = self.integrations[ref.integration]
        if integration.app_keeps_history:
            messages = self.store.read_history(ref)
        else:
            messages = integration.read_history(ref)
        with self._lock:
            if not state.loaded:
                # A turn started while this read ran: its messages come after.
                state.messages = list(messages) + state.messages
                state.loaded = True
            return list(state.messages)

    def messages(self, ref: ChatRef) -> List[Message]:
        with self._lock:
            return list(self.state(ref).messages)

    # -- sending --------------------------------------------------------------

    def send(self, ref: ChatRef, text: str) -> str:
        """Returns "sent" or "queued". Raises Refused with words to show."""
        chat = self.store.get_chat(ref)
        if chat is None:
            # The rule for every integration: only a chat this app started.
            raise Refused("The Chat Place only sends messages to chats it started.")
        integration = self.integrations.get(ref.integration)
        if integration is None or Capability.SEND not in integration.capabilities_for(chat):
            raise Refused("This chat can't be replied to from here.")
        problem = integration.check_prompt(text)
        if problem:
            raise Refused(problem)
        state = self.state(ref)
        with self._lock:
            if state.busy:
                state.queue.append(text)
                return "queued"
            state.busy = True
        self._start_turn(integration, ref, text)
        return "sent"

    def _start_turn(self, integration: Integration, ref: ChatRef, text: str) -> None:
        threading.Thread(target=self._run_turn, args=(integration, ref, text), daemon=True,
                         name=f"turn-{ref.integration}").start()

    def _run_turn(self, integration: Integration, ref: ChatRef, text: str) -> None:
        chat = self.store.get_chat(ref)
        state = self.state(ref)
        with self._lock:
            state.generation += 1
            state.blocks.clear()
            state.activity = ""
        self._add_message(ref, state, Message("user", text), integration)
        self.listener(ref, "changed")
        finished = threading.Event()

        def emit(event: object) -> None:
            if finished.is_set():
                return  # an event after the end can't reopen a finished turn
            if isinstance(event, TurnEnded):
                finished.set()
            self._on_event(integration, ref, state, event)

        try:
            integration.run_turn(chat, text, emit)
        except IntegrationError as error:
            emit(TurnEnded(TurnStatus.FAILED, str(error)))
        except Exception:  # noqa: BLE001 - a bug here mustn't leave the chat stuck busy
            emit(TurnEnded(TurnStatus.FAILED, f"Something went wrong running that turn with {integration.name}."))
        if not finished.is_set():
            emit(TurnEnded(TurnStatus.FAILED, f"{integration.name} stopped before the turn ended."))
        chat.turns += 1
        chat.updated = time.time()
        self.store.save_chat(chat)
        self._next_in_queue(integration, ref, state)

    def _next_in_queue(self, integration: Integration, ref: ChatRef, state: ChatState) -> None:
        with self._lock:
            if state.queue:
                next_text = state.queue.pop(0)
            else:
                next_text = None
                state.busy = False
        if next_text is not None:
            self._start_turn(integration, ref, next_text)

    def _add_message(self, ref: ChatRef, state: ChatState, message: Message, integration: Integration) -> None:
        with self._lock:
            state.messages.append(message)
        if integration.app_keeps_history and message.role in ("user", "assistant"):
            self.store.append_history(ref, message)

    def _on_event(self, integration: Integration, ref: ChatRef, state: ChatState, event: object) -> None:
        if isinstance(event, ReplyText):
            with self._lock:
                index = state.blocks.get(event.block)
                if index is None:
                    state.messages.append(Message("assistant", event.text))
                    state.blocks[event.block] = len(state.messages) - 1
                elif event.final:
                    state.messages[index] = Message("assistant", event.text)
                else:
                    state.messages[index] = Message("assistant", state.messages[index].text + event.text)
        elif isinstance(event, Activity):
            state.activity = event.text
        elif isinstance(event, PlanUpdate):
            state.plan = event.steps
        elif isinstance(event, UsageUpdate):
            state.usage = (event.used, event.size)
        elif isinstance(event, Notice):
            with self._lock:
                state.messages.append(Message("note", event.text))
        elif isinstance(event, UnknownUpdate):
            with self._lock:
                told, state.unknown_told = state.unknown_told, True
                if not told:
                    state.messages.append(Message("note", UNKNOWN_NOTICE.format(name=integration.name)))
            if told:
                return
        elif isinstance(event, AskPermission):
            state.pending_permission = event.request
        elif isinstance(event, AskQuestion):
            state.pending_question = event.request
        elif isinstance(event, TurnEnded):
            with self._lock:
                state.activity = ""
                state.pending_permission = None
                state.pending_question = None
                if event.message:
                    state.messages.append(Message("note", event.message))
                elif event.status is TurnStatus.COMPLETED and not state.blocks:
                    # Otherwise the turn would seem to have vanished.
                    state.messages.append(Message("note", f"{integration.name} finished without replying."))
                # Streamed replies are complete now, so the app's copy is too.
                blocks = sorted(state.blocks.values())
                texts = [state.messages[i] for i in blocks]
                state.blocks.clear()
            if integration.app_keeps_history:
                for message in texts:
                    self.store.append_history(ref, message)
        elif not isinstance(event, OptionsChanged):
            return
        self.listener(ref, event)

    def stop(self, ref: ChatRef) -> List[str]:
        """Stop the turn. Queued messages come back, for the reply box."""
        state = self.state(ref)
        with self._lock:
            queued, state.queue = state.queue, []
            state.pending_permission = None
            state.pending_question = None
        threading.Thread(target=self._keep_stopping, args=(ref, state), daemon=True, name="stop-turn").start()
        return queued

    def _keep_stopping(self, ref: ChatRef, state: ChatState) -> None:
        """Ask until the turn has ended. A Stop can arrive before a turn has
        finished starting, when an integration has nothing to stop yet, and an
        agent may ignore a cancel that reaches it before its prompt."""
        integration = self.integrations[ref.integration]
        deadline = time.monotonic() + STOP_RETRY_SECONDS
        generation = state.generation
        while state.busy and state.generation == generation and time.monotonic() < deadline:
            try:
                integration.stop(ref)
            except (IntegrationError, OSError):
                pass
            time.sleep(0.25)

    def answer_permission(self, ref: ChatRef, request: PermissionRequest, decision: Decision) -> None:
        state = self.state(ref)
        state.pending_permission = None
        self.integrations[ref.integration].answer_permission(ref, request.id, decision)

    def answer_question(self, ref: ChatRef, request: QuestionRequest, answers: Optional[Mapping[str, str]]) -> None:
        state = self.state(ref)
        state.pending_question = None
        self.integrations[ref.integration].answer_question(ref, request.id, answers)

    # -- options --------------------------------------------------------------

    def chat_options(self, ref: ChatRef) -> Tuple[OptionGroup, ...]:
        chat = self.store.get_chat(ref)
        return self.integrations[ref.integration].chat_options(chat) if chat else ()

    def set_option(self, ref: ChatRef, group: str, value: str) -> None:
        chat = self.store.get_chat(ref)
        if chat is None:
            raise Refused("Only a chat The Chat Place started has options.")
        self.integrations[ref.integration].set_option(chat, group, value)
        self.store.save_chat(chat)

    def close(self) -> None:
        for integration in self.integrations.values():
            integration.close()
