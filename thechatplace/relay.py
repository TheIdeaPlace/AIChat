"""Send Reply to Another Chat (#248, phase 1), without importing wx.

A reply from one chat goes to another chat The Chat Place started, with a
label that says which agent wrote it. The label matters for safety: the
receiving agent must not take another agent's words as the user's own. The
user reads and can edit the whole message before it goes, and it goes as an
ordinary message, so it can never answer a question or approve a tool: those
still come to the user through Answer (Ctrl+Shift+A).
"""
from __future__ import annotations

from typing import Callable, Iterable, List, NamedTuple, Optional, Tuple

from .sessions import FIELD_FOLDER, FIELD_PROVIDER, FIELD_STATUS, FIELD_TITLE, SessionInfo
from .transcript import ASSISTANT, PLAN, ChatMessage

#: What can be sent on: what an agent said, or a plan it proposed. Not
#: errors, questions or command output, which are about that chat itself.
RELAYABLE = (ASSISTANT, PLAN)

#: Codex refuses a longer message (codex_runtime.send, codex_execution).
#: Claude has no limit of its own that The Chat Place knows of.
MAX_LENGTH = {"codex": 32000}

#: How a chat is named in the target list: enough to tell two apart.
TARGET_FIELDS = (FIELD_TITLE, FIELD_PROVIDER, FIELD_FOLDER, FIELD_STATUS)


def provider_name(provider: str) -> str:
    return (provider or "").capitalize() or "Another agent"


def reply_to_send(messages: List[ChatMessage],
                  selected: Optional[ChatMessage] = None) -> Optional[ChatMessage]:
    """The selected message if it's a reply, otherwise the latest reply."""
    if selected is not None and selected.kind in RELAYABLE and selected.text.strip():
        return selected
    for message in reversed(messages):
        if message.kind in RELAYABLE and message.text.strip():
            return message
    return None


def label(agent: str, chat_title: str) -> str:
    """The first line of a forwarded reply. It says it's another agent's
    words, forwarded by the user, so the agent receiving it doesn't take it
    as the user's own instructions."""
    title = " ".join((chat_title or "").split()) or "Untitled chat"
    return f'From {agent}, another AI agent, forwarded by the user from their chat "{title}":'


def message_text(message: ChatMessage, chat_title: str) -> str:
    agent = message.assistant_name or "Another agent"
    return f"{label(agent, chat_title)}\n\n{message.text.strip()}"


def targets(sessions: Iterable[SessionInfo], source_key: str,
            can_send: Callable[[SessionInfo], bool],
            partner_key: Optional[str] = None) -> List[SessionInfo]:
    """The chats a reply can go to: The Chat Place's own chats that can be
    sent to, other than the one it came from. The chat that last sent a
    reply here comes first, so sending back is Enter; then the most recently
    active. A desktop app, terminal or other app's session is never one:
    The Chat Place doesn't send into chats it didn't start. Hidden chats
    aren't offered either: they were put out of the way."""
    found = [s for s in sessions
             if s.key != source_key and s.is_own and not s.archived and not s.hidden
             and can_send(s)]
    found.sort(key=lambda s: (s.key != partner_key, -(s.last_activity_ms or 0)))
    return found


def describe(info: SessionInfo) -> str:
    return info.list_line(fields=TARGET_FIELDS)


def too_long(provider: str, text: str) -> Optional[str]:
    """Why ``text`` can't go to a chat of ``provider``, or None."""
    limit = MAX_LENGTH.get(provider)
    if limit is not None and len(text) > limit:
        return (f"{provider_name(provider)} takes messages of up to {limit:,} characters, "
                f"and this one has {len(text):,}. Shorten it, then send.")
    return None


# -- a new chat, or an agent conversation (#378) -----------------------------------------

#: The providers a reply can start a new chat of, in the order offered.
NEW_KINDS = ("claude", "codex")


def new_target_label(provider: str) -> str:
    """How a new chat is offered in the target list."""
    return f"New {provider_name(provider)} chat"


def new_chat_title(source_title: str) -> str:
    """The new chat's title as New Session starts it: without one, the
    title would be made from the label's first words."""
    title = " ".join((source_title or "").split()) or "Untitled chat"
    return f"Forwarded from {title}"


def split_label(text: str, agent: str, chat_title: str) -> Optional[str]:
    """The reply in ``text`` without the forwarding label, or None when the
    label was changed or taken out: then the words are the user's own, and
    go as they are rather than as another agent's reply."""
    prefix = label(agent or "Another agent", chat_title)
    stripped = (text or "").strip()
    if not stripped.startswith(prefix):
        return None
    body = stripped[len(prefix):].strip()
    return body or None


class Problem(NamedTuple):
    """Why an agent conversation can't be started: ``short`` goes in the
    option's name, without chat titles, so it fits and has no "&" of a
    title's in it; ``full`` is said when Send is pressed."""
    short: str
    full: str


def joint_problem(source: SessionInfo, *, can_edit: bool, in_conversation: bool,
                  working: bool, blocked: str = "") -> Optional[Problem]:
    """Why the chat a reply came from can't start an agent conversation with
    the chat it goes to, or None. ``blocked`` is why conversations can't be
    saved at all."""
    if blocked:
        return Problem("agent conversations can't be saved", blocked)
    if source.is_own and can_edit and source.provider not in NEW_KINDS:
        # Started here, but its provider can't be in one yet (Copilot, #369).
        return Problem("this chat can't be in one",
                       "agent conversations are for Claude and Codex chats")
    if not source.is_own or source.provider not in NEW_KINDS or not can_edit:
        return Problem("this chat wasn't started here",
                       f"{source.title} wasn't started in The Chat Place")
    if in_conversation:
        return Problem("this chat is already in one",
                       f"{source.title} is already in an agent conversation")
    if working:
        return Problem("this chat is still working",
                       f"{source.title} is still working; wait until it has finished")
    return None


def joint_target_problem(target: SessionInfo, in_conversation: bool,
                         working: bool = False) -> Optional[Problem]:
    """Why ``target`` can't be the other half of an agent conversation. A
    working one would end its turn inside the conversation, and that
    unrelated reply would be taken as its answer."""
    if target.provider not in NEW_KINDS:
        return Problem("that chat can't join one",
                       "agent conversations are for Claude and Codex chats")
    if in_conversation:
        return Problem("that chat is already in one",
                       f"{target.title} is already in an agent conversation")
    if working:
        return Problem("that chat is still working",
                       f"{target.title} is still working; wait until it has finished")
    return None


def joint_message(sent: str, agent: str, chat_title: str, reply: str
                  ) -> Tuple[Optional[str], str]:
    """What of ``sent`` is the other agent's reply, and what is the user's
    own: (reply, yours). Words added after the unchanged reply are yours,
    and go as yours (``agents.USER_ADDS``), not inside the other agent's
    words. The label changed, it's all yours: (None, sent). The reply
    itself changed, it's still passed as the reply."""
    body = split_label(sent, agent, chat_title)
    if body is None:
        return None, sent.strip()
    reply = (reply or "").strip()
    if reply and body.startswith(reply):
        return reply, body[len(reply):].strip()
    return body, ""
