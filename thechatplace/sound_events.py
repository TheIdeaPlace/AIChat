"""What sounds The Chat Place has (#250): each event, its sound, and sound schemes.

No wx, so all of it can be tested. Names on the right of the table are app-kit's (its
``sounds/README.md``): they say what happened, and this maps The Chat Place's events to them.
A sound adds to an announcement or the status bar and never replaces one, so nothing here
decides what's said.

Schemes, scheme files and the gallery follow app-kit's ``sounds/SCHEMES.md``, shared with
QuickMail, through ``theideaplace_appkit.soundschemes``. A scheme's events are kept under the
shared keys (``ToolRan``), the same as in a scheme file; the window uses the ids below.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from theideaplace_appkit import soundschemes as shared
from theideaplace_appkit.sounds import AMBIENT, NORMAL, URGENT

from . import platform_paths

TOOL = "tool"
FILE_CHANGED = "file_changed"
PULSE = "pulse"
SENT = "sent"
QUEUED = "queued"
STEERED = "steered"
STEER_TAKEN = "steer_taken"
QUEUED_SENT = "queued_sent"
NOT_SENT = "not_sent"
FINISHED = "finished"
FINISHED_ELSEWHERE = "finished_elsewhere"
ASIDE_ANSWERED = "aside_answered"
ASIDE_FAILED = "aside_failed"
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
    #: The event's key in a scheme and in app-kit's ``apps.json``.
    key: str
    label: str
    sound: str
    priority: str = NORMAL
    #: The answer to something you just did, so it plays during a presentation
    #: or full-screen program, when sounds that happen on their own hold back.
    answers_you: bool = False


#: One sound for each event, made for it: never shared, never borrowed from another app.
#: dev-notes/sound-design.md has the rules, the families and every sound's notes.
EVENTS: Tuple[SoundEvent, ...] = (
    SoundEvent(TOOL, "ToolRan", "Tool ran", "tool", AMBIENT),
    SoundEvent(FILE_CHANGED, "FileChanged", "File changed", "edit", AMBIENT),
    SoundEvent(PULSE, "StillWorking", "Still working", "working", AMBIENT),
    SoundEvent(SENT, "MessageSent", "Message sent", "send", answers_you=True),
    SoundEvent(QUEUED, "MessageQueued", "Message queued", "queue", answers_you=True),
    SoundEvent(STEERED, "MessageSteered", "Message steered into the turn", "steer", answers_you=True),
    SoundEvent(STEER_TAKEN, "SteerTaken", "Steered message taken", "steertaken"),
    SoundEvent(QUEUED_SENT, "QueuedMessageSent", "Queued message sent", "queuesent"),
    SoundEvent(STOPPED, "TurnStopped", "You stopped a turn", "stop", answers_you=True),
    SoundEvent(FINISHED, "ReplyFinished", "Reply finished", "reply"),
    SoundEvent(FINISHED_ELSEWHERE, "OtherChatFinished", "Another chat finished", "otherreply"),
    SoundEvent(ASIDE_ANSWERED, "AsideAnswered", "Ask Aside answered", "aside"),
    SoundEvent(NEEDS_YOU, "NeedsYou", "Needs you", "needsyou", URGENT),
    SoundEvent(FAILED, "TurnFailed", "Turn failed", "turnfailed", URGENT),
    SoundEvent(NOT_SENT, "MessageNotSent", "Message not sent", "notsent", answers_you=True),
    SoundEvent(ASIDE_FAILED, "AsideNotAnswered", "Ask Aside not answered", "asidefailed"),
    SoundEvent(TROUBLE, "ServiceTrouble", "Service trouble", "trouble"),
    SoundEvent(LIMIT, "UsageWarning", "Usage or context warning", "warning"),
    SoundEvent(COMPACTED, "Compacted", "Conversation compacted", "compacted"),
    SoundEvent(PEER, "PeerMessage", "Message from another session", "peer"),
)
BY_ID: Dict[str, SoundEvent] = {event.id: event for event in EVENTS}
BY_KEY: Dict[str, SoundEvent] = {event.key: event for event in EVENTS}
#: This app as app-kit knows it: the same events, keys and sounds as above (a test checks).
APP = shared.catalog("thechatplace")

#: Tool sounds follow "Tool sounds for"; everything else from a chat that isn't
#: loaded follows "Other chats". A chat that's only watched has no tool sounds unless it's
#: loaded: nothing reads its transcript until then.
TOOL_EVENTS = frozenset({TOOL, FILE_CHANGED})

#: Every built-in sound, in the order the Sound choice lists them, with its name there: what
#: the sound is like, so a row reads "Reply finished: Rise".
BUILT_IN: Dict[str, str] = {
    "tool": "Tick", "edit": "Double tick", "working": "Hum", "send": "Send", "queue": "Hold",
    "steer": "Bend", "steertaken": "Taken", "queuesent": "Release", "stop": "Stop",
    "reply": "Rise", "otherreply": "Low rise", "aside": "Aside", "needsyou": "Call",
    "turnfailed": "Fall", "notsent": "Bump", "asidefailed": "Aside fall", "trouble": "Waver",
    "warning": "Alert", "compacted": "Settle", "peer": "Knock",
}

#: The first set's names, which only 0.5 previews saved: each becomes the new sound for the
#: same event, so a choice made then still means the same thing.
OLD_NAMES: Dict[str, str] = {
    "tap": "tool", "changed": "edit", "pulse": "working", "sent": "send", "queued": "queue",
    "error": "notsent", "arrived": "reply", "elsewhere": "otherreply", "attention": "needsyou",
    "failed": "turnfailed", "stopped": "stop", "disconnected": "trouble", "filed": "compacted",
    "reminder": "warning", "matched": "peer",
}

#: The packs, which are also the built-in schemes, by id.
PACKS: Dict[str, str] = dict(shared.PACKS)
DEFAULT_SCHEME = shared.DEFAULT_PACK
DEFAULT_VOLUME = 80
VOLUMES: List[int] = list(range(10, 101, 10))

TOOLS_LOADED = "loaded"
TOOLS_ALL = "all"
TOOLS_LABELS: Dict[str, str] = {TOOLS_LOADED: "The loaded chat", TOOLS_ALL: "Every chat run here"}

OTHERS_ALL = "all"
OTHERS_OWN = "own"
OTHERS_NONE = "none"
OTHERS_LABELS: Dict[str, str] = {
    OTHERS_ALL: "Every listed chat",
    OTHERS_OWN: "Chats run in The Chat Place",
    OTHERS_NONE: "None",
}

NONE_REF = shared.NONE
USER_PREFIX = "user-"
_USER_ID_LENGTH = len(USER_PREFIX) + 32
#: At most this many schemes of your own, so a settings file can't make the dialog unusable.
MAX_USER_SCHEMES = 100


def built_in_ref(name: str, pack: str = "") -> str:
    return str(shared.SoundRef("builtin", name, pack))


def custom_ref(file_name: str) -> str:
    return "custom:" + file_name


def parse_ref(ref: object) -> Optional[shared.SoundRef]:
    return shared.parse_ref(ref, APP)


# -- schemes -------------------------------------------------------------------------


@dataclass
class Scheme:
    """A sound scheme. A built-in one (Glass, Soft bell, Wood) keeps only what you changed; one
    of your own keeps every event, and an event it doesn't keep is silent."""
    id: str
    name: str
    built_in: bool
    #: Event key -> stored sound, as in a scheme file.
    events: Dict[str, str] = field(default_factory=dict)
    author: Optional[str] = None
    license: Optional[str] = None
    description: Optional[str] = None
    #: Where a scheme from the gallery came from: {"gallery": id, "sha256": hash}.
    source: Optional[Dict[str, str]] = None

    @property
    def pack(self) -> Optional[str]:
        return self.id if self.built_in else None

    def copy(self) -> "Scheme":
        return Scheme(self.id, self.name, self.built_in, dict(self.events), self.author,
                      self.license, self.description, dict(self.source) if self.source else None)


def built_in_scheme(pack: str) -> Scheme:
    return Scheme(pack, PACKS[pack], True)


def new_id() -> str:
    return USER_PREFIX + uuid.uuid4().hex


def _user_id_ok(sid: object) -> bool:
    return (isinstance(sid, str) and len(sid) == _USER_ID_LENGTH and sid.startswith(USER_PREFIX)
            and all(c in "0123456789abcdef" for c in sid[len(USER_PREFIX):]))


def _clean_events(raw: object) -> Dict[str, str]:
    """What this build understands of saved events: what's in settings isn't trusted, as a
    settings file may have been edited or restored from elsewhere."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        ref = parse_ref(_renamed(value))
        if key in BY_KEY and ref is not None:
            out[key] = str(ref)
    return out


def _renamed(value: object) -> object:
    """A 0.5 preview's built-in sound under its new name (OLD_NAMES); anything else as it is."""
    if not isinstance(value, str) or not value.lower().startswith("builtin:"):
        return value
    rest = value[len("builtin:"):]
    pack, slash, name = rest.rpartition("/")
    new = OLD_NAMES.get(name.lower())
    return f"builtin:{pack}{slash}{new}" if new else value


def _clean_source(raw: object) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    gallery, sha = raw.get("gallery"), raw.get("sha256")
    if (isinstance(gallery, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", gallery)
            and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
        return {"gallery": gallery, "sha256": sha}
    return None


def load_schemes(raw: object) -> List[Scheme]:
    """Every scheme: the built-in ones first, then your own by name. Saved settings that can't
    be used are left out rather than trusted."""
    raw = raw if isinstance(raw, dict) else {}
    schemes = []
    for pack in PACKS:
        scheme = built_in_scheme(pack)
        entry = raw.get(pack)
        if isinstance(entry, dict):
            scheme.events = _clean_events(entry.get("events"))
        schemes.append(scheme)
    taken = [s.name for s in schemes]
    own = []
    for sid, entry in raw.items():
        if len(own) >= MAX_USER_SCHEMES:
            break
        if not _user_id_ok(sid) or not isinstance(entry, dict):
            continue
        name = shared.clean_line(entry.get("name"), shared.MAX_NAME)
        if not name:
            continue
        name = shared.unique_name(name, taken)
        taken.append(name)
        own.append(Scheme(sid, name, False, _clean_events(entry.get("events")),
                          shared.clean_line(entry.get("author"), shared.MAX_AUTHOR) or None,
                          shared.clean_line(entry.get("license"), shared.MAX_LICENSE) or None,
                          shared.clean_line(entry.get("description"), shared.MAX_DESCRIPTION) or None,
                          _clean_source(entry.get("source"))))
    return schemes + sorted(own, key=lambda s: s.name.casefold())


def save_schemes(schemes: Iterable[Scheme]) -> Dict[str, dict]:
    """Settings for ``schemes``: a built-in one only if you changed something, with only what
    you changed; one of your own with every event."""
    out: Dict[str, dict] = {}
    for scheme in schemes:
        if scheme.built_in:
            changed = {k: v for k, v in scheme.events.items()
                       if k in BY_KEY and v != built_in_ref(BY_KEY[k].sound)}
            if changed:
                out[scheme.id] = {"events": changed}
            continue
        entry: Dict[str, object] = {"name": scheme.name}
        for key in ("author", "license", "description", "source"):
            if getattr(scheme, key):
                entry[key] = getattr(scheme, key)
        entry["events"] = {e.key: scheme.events.get(e.key, NONE_REF) for e in EVENTS}
        out[scheme.id] = entry
    return out


def migrate(style: object, choices: object) -> Tuple[str, Dict[str, dict]]:
    """0.5 previews kept a style and one set of choices for every style: they become the
    style's scheme. Choices were kept under the window's ids."""
    pack = style if isinstance(style, str) and style in PACKS else DEFAULT_SCHEME
    events = {}
    if isinstance(choices, dict):
        for event_id, ref in choices.items():
            event = BY_ID.get(event_id)
            parsed = parse_ref(_renamed(ref))
            if event is not None and parsed is not None:
                events[event.key] = str(parsed)
    return pack, ({pack: {"events": events}} if events else {})


def find(schemes: Iterable[Scheme], scheme_id: str) -> Scheme:
    """The scheme with ``scheme_id``, or Glass when there isn't one."""
    schemes = list(schemes)
    return next((s for s in schemes if s.id == scheme_id),
                next((s for s in schemes if s.id == DEFAULT_SCHEME), built_in_scheme(DEFAULT_SCHEME)))


# -- what an event plays -------------------------------------------------------------


def ref_for(scheme: Scheme, event_id: str) -> shared.SoundRef:
    return shared.resolve(APP, BY_ID[event_id].key, scheme.events, scheme.built_in)


def ref_label(ref: shared.SoundRef, scheme: Scheme, missing: bool = False) -> str:
    """How a choice reads in the list and the Sound choice."""
    if ref.kind == "none":
        return "None"
    if ref.kind == "custom":
        return f"Your file, {ref.name}" + (" (missing)" if missing else "")
    label = BUILT_IN.get(ref.name, ref.name)
    if ref.pack and ref.pack != scheme.pack:
        return f"{label} ({PACKS[ref.pack]})"
    return label


def custom_missing(ref: shared.SoundRef) -> bool:
    return ref.kind == "custom" and not (platform_paths.custom_sounds_dir() / ref.name).is_file()


def row_label(event_id: str, scheme: Scheme) -> str:
    """A row of the Sound events list: "Tool ran: Tap"."""
    ref = ref_for(scheme, event_id)
    return f"{BY_ID[event_id].label}: {ref_label(ref, scheme, custom_missing(ref))}"


def events_with_sound(scheme: Scheme) -> int:
    return sum(1 for e in EVENTS if ref_for(scheme, e.id).kind != "none")


def count_label(scheme: Scheme) -> str:
    return f"{events_with_sound(scheme)} of {len(EVENTS)} events have a sound"


def offered(scheme: Scheme) -> List[str]:
    """The sounds the Sound choice offers. A built-in scheme's own sounds play in its tone; one
    of your own can take any built-in sound in any tone."""
    refs = [NONE_REF]
    if scheme.built_in:
        refs += [built_in_ref(name) for name in BUILT_IN]
    else:
        refs += [built_in_ref(name, pack) for pack in PACKS for name in BUILT_IN]
    refs += [custom_ref(name) for name in custom_files()]
    return refs


def built_in_path(name: str, pack: str) -> Path:
    pack = pack if pack in PACKS else DEFAULT_SCHEME
    return platform_paths.built_in_sounds_dir() / pack / f"{name}.wav"


def resolve(event_id: str, scheme: Scheme) -> Optional[Tuple[str, Optional[str]]]:
    """The file to play for an event, and what to play if that one can't be, or None for no
    sound. A person's own file falls back to the event's built-in sound."""
    return resolve_ref(ref_for(scheme, event_id), event_id, scheme)


def resolve_ref(ref: shared.SoundRef, event_id: str, scheme: Scheme) -> Optional[Tuple[str, Optional[str]]]:
    if ref.kind == "none":
        return None
    default = str(built_in_path(BY_ID[event_id].sound, shared.pack_for(shared.SILENT, scheme.pack)))
    if ref.kind == "custom":
        return str(platform_paths.custom_sounds_dir() / ref.name), default
    return str(built_in_path(ref.name, shared.pack_for(ref, scheme.pack))), None


def custom_files() -> List[str]:
    """Your own sounds, already copied into the data folder, by name."""
    folder = platform_paths.custom_sounds_dir()
    try:
        names = [p.name for p in folder.iterdir() if p.is_file()]
    except OSError:
        return []
    return sorted((n for n in names if shared.custom_name_ok(n)), key=str.casefold)


def missing_files(scheme: Scheme) -> int:
    """How many of a scheme's events play a file of your own that's no longer there, so an
    export would carry no sound for them."""
    return sum(1 for e in EVENTS if custom_missing(ref_for(scheme, e.id)))


def schemes_using(schemes: Iterable[Scheme], file_name: str) -> List[str]:
    """The names of the schemes with an event that plays ``file_name``."""
    return [s.name for s in schemes
            if any(ref_for(s, e.id) == shared.SoundRef("custom", file_name) for e in EVENTS)]


def missing_built_ins() -> List[str]:
    """Built-in files this copy of the app lacks, for ``--smoke-test``."""
    return [str(path) for pack in PACKS for name in BUILT_IN
            for path in [built_in_path(name, pack)] if not path.is_file()]


# -- scheme files --------------------------------------------------------------------


def export(scheme: Scheme) -> bytes:
    """A scheme file of ``scheme``, every event pinned to the tone it plays in."""
    folder = platform_paths.custom_sounds_dir()

    def read(name: str) -> Optional[bytes]:
        try:
            return (folder / name).read_bytes() if shared.custom_name_ok(name) else None
        except OSError:
            return None
    return shared.write_scheme_file(
        shared.SchemeFile(APP.id, scheme.name, dict(scheme.events), scheme.author, scheme.license,
                          scheme.description), APP, read,
        built_in=scheme.built_in, scheme_pack=scheme.pack)


@dataclass
class Imported:
    scheme: Scheme
    #: Sound files the scheme brought in, and events left silent for a file it couldn't use.
    files: int
    dropped: int

    def message(self) -> str:
        text = f"Added the sound scheme {self.scheme.name}"
        if self.files:
            text += f", with {self.files} sound file{'s' if self.files != 1 else ''}"
        text += "."
        if self.dropped:
            text += (f" {self.dropped} event{'s' if self.dropped != 1 else ''} had a sound file that was "
                     "missing or could not be played, and now "
                     f"{'have' if self.dropped != 1 else 'has'} no sound.")
        return text


def import_scheme(source, taken: Iterable[str], gallery_source: Optional[Dict[str, str]] = None) -> Imported:
    """A new scheme of your own from a scheme file (a path or bytes), its sounds put in the
    sounds folder. Raises ``shared.SchemeFileProblem`` with words to show."""
    folder = platform_paths.custom_sounds_dir()
    read = shared.read_scheme_file(source, APP, have_file=lambda n: (folder / n).is_file())
    installed = shared.install_sounds(read, folder)
    events = {e.key: installed.events.get(e.key, NONE_REF) for e in EVENTS}
    scheme = Scheme(new_id(), shared.unique_name(read.name, taken), False, events, read.author,
                    read.license, read.description, gallery_source)
    return Imported(scheme, len(read.sounds), read.dropped)
