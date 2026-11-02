"""Text the window shows or speaks, testable without wx. It names no provider:
those names come from the integrations."""
from __future__ import annotations

from .contracts import Capability, ChatInfo, Message

#: Long replies stop here when spoken; the whole reply stays in the list.
SPOKEN_LIMIT = 3000

SHORTCUTS = (
    ("Tab, Shift+Tab", "Chats, messages, reply box, Send, Stop"),
    ("Ctrl+1, Ctrl+2, Ctrl+3", "Go to the chats, the messages or the reply box"),
    ("F6, Shift+F6", "Next or previous part of the window"),
    ("Escape", "From the messages or the reply box, back to the chats"),
    ("Enter in the chats", "Open that chat and move to its messages"),
    ("Enter in the messages", "Read the whole message; Escape comes back"),
    ("Ctrl+C in the messages", "Copy the whole message"),
    ("Ctrl+Enter in the reply box", "Send; during a turn it waits its turn"),
    ("Ctrl+Period", "Stop the running turn; waiting messages come back to the reply box"),
    ("Ctrl+N", "New chat"),
    ("Ctrl+Shift+O", "Options for this chat"),
    ("F5", "Refresh the chats"),
)


def shortcuts_text() -> str:
    return "\n".join(f"{keys}: {what}" for keys, what in SHORTCUTS)


def chat_state(info: ChatInfo, busy: bool, needs_you: bool) -> str:
    if needs_you:
        return "Needs you"
    if busy:
        return "Working"
    return "Idle" if Capability.SEND in info.capabilities else "Read only"


def chat_row(info: ChatInfo, busy: bool = False, needs_you: bool = False) -> str:
    parts = [info.title, info.provider, info.kind, chat_state(info, busy, needs_you), info.folder]
    return ", ".join(part for part in parts if part)


def author(message: Message, provider: str) -> str:
    return {"user": "You", "assistant": provider}.get(message.role, "Note")


def message_row(message: Message, provider: str) -> str:
    """One line for the list. The accessible text (``message_spoken``) has more."""
    first = " ".join(message.text.split())
    return f"{author(message, provider)}: {first[:200]}" + ("…" if len(first) > 200 else "")


def message_spoken(message: Message, provider: str) -> str:
    text = " ".join(message.text.split())
    return f"{author(message, provider)}: {text[:1000]}" + (" (more; press Enter to read it all)" if len(text) > 1000 else "")


def reply_announcement(provider: str, title: str, text: str) -> str:
    text = text.strip()
    if not text:
        return f"{provider} finished in {title}."
    if len(text) > SPOKEN_LIMIT:
        text = text[:SPOKEN_LIMIT] + ". That's where reading stops; the rest is in the messages."
    return f"{provider}: {text}"


def permission_text(summary: str, detail: str, caution: str) -> str:
    parts = [summary]
    if detail:
        parts.append(f"What it says it wants to do:\n{detail}")
    if caution:
        parts.append(caution)
    return "\n\n".join(parts)


def drift_lines(name: str, report: dict) -> str:
    """What a provider sent that wasn't understood: names and counts only."""
    lines = []
    for kind, label in (("unknown", "Not understood"), ("malformed", "Damaged"), ("ignored", "Left out on purpose")):
        counts = report.get(kind) or {}
        if counts:
            lines.append(f"{label}: " + ", ".join(f"{key} ({value})" for key, value in sorted(counts.items())))
    return "\n".join(lines) or f"{name} hasn't sent anything The Chat Place didn't expect."
