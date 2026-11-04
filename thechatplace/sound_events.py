"""What sounds The Chat Place has (#250): each event, its sound, and how a choice is kept.

No wx, so all of it can be tested. Names on the right of the table are app-kit's (its
``sounds/README.md``): they say what happened, and this maps The Chat Place's events to them.
A sound adds to an announcement or the status bar and never replaces one, so nothing here
decides what's said.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from theideaplace_appkit.sounds import AMBIENT, NORMAL, URGENT, safe_file_name

from . import platform_paths

TOOL = "tool"
FILE_CHANGED = "file_changed"
PULSE = "pulse"
SENT = "sent"
QUEUED = "queued"
NOT_SENT = "not_sent"
FINISHED = "finished"
FINISHED_ELSEWHERE = "finished_elsewhere"
NEEDS_YOU = "needs_you"
FAILED = "failed"
STOPPED = "stopped"
TROUBLE = "trouble"
COMPACTED = "compacted"
LIMIT = "limit"
PEER = "peer"


@dataclass(frozen=True)
class SoundEvent:
    id: str
    label: str
    sound: str
    priority: str = NORMAL
    #: The answer to something you just did, so it plays during a presentation
    #: or full-screen program, when sounds that happen on their own hold back.
    answers_you: bool = False


EVENTS: Tuple[SoundEvent, ...] = (
    SoundEvent(TOOL, "Tool ran", "tap", AMBIENT),
    SoundEvent(FILE_CHANGED, "File changed", "changed", AMBIENT),
    SoundEvent(PULSE, "Still working", "pulse", AMBIENT),
    SoundEvent(SENT, "Message sent", "sent", answers_you=True),
    SoundEvent(QUEUED, "Message queued", "queued", answers_you=True),
    SoundEvent(NOT_SENT, "Message not sent", "error", answers_you=True),
    SoundEvent(FINISHED, "Reply finished", "arrived"),
    SoundEvent(FINISHED_ELSEWHERE, "Another chat finished", "elsewhere"),
    SoundEvent(NEEDS_YOU, "Needs you", "attention", URGENT),
    SoundEvent(FAILED, "Turn failed", "failed", URGENT),
    SoundEvent(STOPPED, "You stopped a turn", "stopped", answers_you=True),
    SoundEvent(TROUBLE, "Service trouble", "disconnected"),
    SoundEvent(COMPACTED, "Conversation compacted", "filed"),
    SoundEvent(LIMIT, "Usage or context warning", "reminder"),
    SoundEvent(PEER, "Message from another session", "matched"),
)
BY_ID: Dict[str, SoundEvent] = {event.id: event for event in EVENTS}

#: Tool sounds follow "Tool sounds for"; everything else from a chat that isn't
#: loaded follows "Other chats".
TOOL_EVENTS = frozenset({TOOL, FILE_CHANGED})

#: Every built-in sound, in the order the Sound choice lists them, with its name there.
BUILT_IN: Dict[str, str] = {
    "tap": "Tap", "changed": "Changed", "pulse": "Pulse", "sent": "Sent", "queued": "Queued",
    "error": "Error", "arrived": "Arrived", "elsewhere": "Elsewhere", "attention": "Attention",
    "failed": "Failed", "stopped": "Stopped", "disconnected": "Disconnected", "filed": "Filed",
    "reminder": "Reminder", "matched": "Matched",
}

STYLES: Dict[str, str] = {"glass": "Glass", "softbell": "Soft bell", "wood": "Wood"}
DEFAULT_STYLE = "glass"
DEFAULT_VOLUME = 80
VOLUMES: List[int] = list(range(10, 101, 10))

TOOLS_LOADED = "loaded"
TOOLS_ALL = "all"
TOOLS_LABELS: Dict[str, str] = {TOOLS_LOADED: "The loaded chat", TOOLS_ALL: "Every chat"}

OTHERS_ALL = "all"
OTHERS_OWN = "own"
OTHERS_NONE = "none"
OTHERS_LABELS: Dict[str, str] = {
    OTHERS_ALL: "Every listed chat",
    OTHERS_OWN: "Chats run in The Chat Place",
    OTHERS_NONE: "None",
}

NONE_REF = "none"
_BUILT_IN_PREFIX = "builtin:"
_CUSTOM_PREFIX = "custom:"


def built_in_ref(name: str) -> str:
    return _BUILT_IN_PREFIX + name


def custom_ref(file_name: str) -> str:
    return _CUSTOM_PREFIX + file_name


def default_ref(event_id: str) -> str:
    return built_in_ref(BY_ID[event_id].sound)


def valid_ref(ref: object) -> bool:
    """Whether a saved choice can be used. Only a bare file name is ever kept, and what's in
    settings isn't trusted: a settings file may have been edited or restored from elsewhere."""
    if not isinstance(ref, str):
        return False
    if ref == NONE_REF:
        return True
    if ref.startswith(_BUILT_IN_PREFIX):
        return ref[len(_BUILT_IN_PREFIX):] in BUILT_IN
    if ref.startswith(_CUSTOM_PREFIX):
        return safe_file_name(ref[len(_CUSTOM_PREFIX):])
    return False


def clean_choices(raw: object) -> Dict[str, str]:
    """Saved choices, keeping what this build understands."""
    if not isinstance(raw, dict):
        return {}
    return {event_id: ref for event_id, ref in raw.items()
            if event_id in BY_ID and valid_ref(ref)}


def ref_for(choices: Dict[str, str], event_id: str) -> str:
    ref = choices.get(event_id, "")
    return ref if valid_ref(ref) else default_ref(event_id)


def ref_label(ref: str) -> str:
    """How a choice reads in the list and the Sound choice."""
    if ref == NONE_REF:
        return "None"
    if ref.startswith(_CUSTOM_PREFIX):
        return f"Your file, {ref[len(_CUSTOM_PREFIX):]}"
    return BUILT_IN.get(ref[len(_BUILT_IN_PREFIX):], ref)


def row_label(event_id: str, choices: Dict[str, str]) -> str:
    """A row of the Sound events list: "Tool ran: Tap"."""
    return f"{BY_ID[event_id].label}: {ref_label(ref_for(choices, event_id))}"


def built_in_path(name: str, style: str) -> Path:
    style = style if style in STYLES else DEFAULT_STYLE
    return platform_paths.built_in_sounds_dir() / style / f"{name}.wav"


def resolve(event_id: str, choices: Dict[str, str], style: str) -> Optional[Tuple[str, Optional[str]]]:
    """The file to play for an event, and what to play if that one can't be, or None for no
    sound. A person's own file falls back to the event's built-in sound."""
    return resolve_ref(ref_for(choices, event_id), event_id, style)


def resolve_ref(ref: str, event_id: str, style: str) -> Optional[Tuple[str, Optional[str]]]:
    if ref == NONE_REF:
        return None
    default = str(built_in_path(BY_ID[event_id].sound, style))
    if valid_ref(ref) and ref.startswith(_CUSTOM_PREFIX):
        return str(platform_paths.custom_sounds_dir() / ref[len(_CUSTOM_PREFIX):]), default
    if valid_ref(ref):
        return str(built_in_path(ref[len(_BUILT_IN_PREFIX):], style)), None
    return default, None


def custom_files() -> List[str]:
    """Your own sounds, already copied into the data folder, by name."""
    folder = platform_paths.custom_sounds_dir()
    try:
        names = [p.name for p in folder.iterdir() if p.is_file()]
    except OSError:
        return []
    return sorted((n for n in names if safe_file_name(n) and n.lower().endswith(".wav")),
                  key=str.casefold)


def missing_built_ins() -> List[str]:
    """Built-in files this copy of the app lacks, for ``--smoke-test``."""
    return [str(path) for style in STYLES for name in BUILT_IN
            for path in [built_in_path(name, style)] if not path.is_file()]
