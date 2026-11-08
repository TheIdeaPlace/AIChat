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

import os
import re
import shutil
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
QUEUED_DELETED = "queued_deleted"
NOT_SENT = "not_sent"
FINISHED = "finished"
FINISHED_ELSEWHERE = "finished_elsewhere"
ASIDE_ANSWERED = "aside_answered"
ASIDE_FAILED = "aside_failed"
NEEDS_YOU = "needs_you"
ANSWER_SENT = "answer_sent"
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
    # Not ambient, as it was while it was a tick: a file written is a result, heard once per
    # edit, and isn't dropped for a tick that has just played.
    SoundEvent(FILE_CHANGED, "FileChanged", "File changed", "write"),
    SoundEvent(PULSE, "StillWorking", "Still working", "working", AMBIENT),
    SoundEvent(SENT, "MessageSent", "Message sent", "send", answers_you=True),
    SoundEvent(QUEUED, "MessageQueued", "Message queued", "queue", answers_you=True),
    SoundEvent(STEERED, "MessageSteered", "Message steered into the turn", "steer", answers_you=True),
    SoundEvent(STEER_TAKEN, "SteerTaken", "Steered message taken", "steertaken"),
    SoundEvent(QUEUED_SENT, "QueuedMessageSent", "Queued message sent", "queuesent"),
    SoundEvent(QUEUED_DELETED, "QueuedMessageDeleted", "Queued message deleted", "queuedelete",
               answers_you=True),
    SoundEvent(STOPPED, "TurnStopped", "You stopped a turn", "stop", answers_you=True),
    SoundEvent(FINISHED, "ReplyFinished", "Reply finished", "reply"),
    SoundEvent(FINISHED_ELSEWHERE, "OtherChatFinished", "Another chat finished", "otherreply"),
    SoundEvent(ASIDE_ANSWERED, "AsideAnswered", "Ask Aside answered", "aside"),
    SoundEvent(NEEDS_YOU, "NeedsYou", "Needs you", "needsyou", URGENT),
    SoundEvent(ANSWER_SENT, "AnswerSent", "Answer sent", "answer", answers_you=True),
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

#: Tool ran follows "Tool sounds play for"; everything else from a chat that isn't loaded,
#: File changed included, follows "Other sounds play for". Kelly found one setting for both too
#: much: a tool can tick dozens of times a turn, a file changes far less often. A chat that's only
#: watched has no tool sounds unless it's loaded: nothing reads its transcript until then.
TOOL_EVENTS = frozenset({TOOL})

#: Every built-in sound, in the order the Sound choice lists them, with its name there: what
#: the sound is like, so a row reads "Reply finished: Rise".
BUILT_IN: Dict[str, str] = {
    "tool": "Tick", "write": "Write", "working": "Hum", "send": "Send", "queue": "Hold",
    "steer": "Bend", "steertaken": "Taken", "queuesent": "Release",
    "queuedelete": "Withdraw", "stop": "Stop",
    "reply": "Rise", "otherreply": "Low rise", "aside": "Aside", "needsyou": "Call",
    "answer": "Answer", "turnfailed": "Fall", "notsent": "Bump", "asidefailed": "Aside fall", "trouble": "Waver",
    "warning": "Alert", "compacted": "Settle", "peer": "Knock",
}

#: Built-in sound names that only 0.5 previews used: the first set's, and then "edit", the Double
#: tick File changed played until it was too like Tick to tell apart. Each maps to the new sound
#: for the same event in The Chat Place's own saved settings (load_schemes, migrate), so a choice
#: made in a preview still means the same thing. Scheme files exported from a preview aren't
#: migrated: app-kit reads an unknown built-in name in one as no sound, and Kelly decided that an
#: unreleased feature needs no more than that.
OLD_NAMES: Dict[str, str] = {
    "tap": "tool", "changed": "write", "edit": "write", "pulse": "working", "sent": "send", "queued": "queue",
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
TOOLS_LABELS: Dict[str, str] = {TOOLS_LOADED: "The open chat only",
                               TOOLS_ALL: "The open chat and chats running in The Chat Place"}

OTHERS_ALL = "all"
OTHERS_OWN = "own"
OTHERS_NONE = "none"
OTHERS_LABELS: Dict[str, str] = {
    OTHERS_ALL: "The open chat and every chat in the list",
    OTHERS_OWN: "The open chat and chats running in The Chat Place",
    OTHERS_NONE: "The open chat only",
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


def custom_missing(ref: shared.SoundRef, scheme: Scheme) -> bool:
    """Whether ``ref`` is a file of your own that isn't in ``scheme``'s folder."""
    if ref.kind != "custom":
        return False
    path = custom_path(scheme, ref.name)
    return path is None or not path.is_file()


def row_label(event_id: str, scheme: Scheme) -> str:
    """A row of the Sound events list: "Tool ran: Tap"."""
    ref = ref_for(scheme, event_id)
    return f"{BY_ID[event_id].label}: {ref_label(ref, scheme, custom_missing(ref, scheme))}"


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
    refs += [custom_ref(name) for name in custom_files(scheme)]
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
        path = custom_path(scheme, ref.name)
        return (str(path), default) if path is not None else (default, None)
    return str(built_in_path(ref.name, shared.pack_for(ref, scheme.pack))), None


# -- each scheme's own folder ----------------------------------------------------------
#
# Every sound file of your own belongs to one scheme and is kept in that scheme's folder,
# ``sounds/<scheme id>``: importing a scheme puts its files there, Choose File copies into the
# scheme showing, and deleting a scheme deletes its folder. There's no shared folder and nothing
# is looked for anywhere else (Kelly's decision for 0.5: files in a preview's shared ``sounds``
# folder aren't used). A file name is only ever a plain name in a scheme file, as before.


def scheme_id_ok(scheme_id: object) -> bool:
    """A scheme id this app makes: a built-in scheme's, or ``user-<32 hex>``."""
    return scheme_id in PACKS or _user_id_ok(scheme_id)


def scheme_folder(scheme: Scheme) -> Optional[Path]:
    """``scheme``'s own sound folder, or None for an id that isn't one of ours, which never
    reaches the disk."""
    if not scheme_id_ok(scheme.id):
        return None
    return platform_paths.scheme_sounds_dir(scheme.id)


def custom_path(scheme: Scheme, file_name: str) -> Optional[Path]:
    """Where ``scheme``'s file ``file_name`` is kept, or None when either can't be used safely:
    the file name is checked as app-kit checks one in a scheme file."""
    base = scheme_folder(scheme)
    if base is None or not shared.custom_name_ok(file_name):
        return None
    return base / file_name


def custom_files(scheme: Scheme) -> List[str]:
    """``scheme``'s own sound files, already copied into its folder, by name."""
    base = scheme_folder(scheme)
    if base is None:
        return []
    try:
        names = [p.name for p in base.iterdir() if p.is_file() and not p.is_symlink()]
    except OSError:
        return []
    return sorted((n for n in names if shared.custom_name_ok(n)), key=str.casefold)


def missing_files(scheme: Scheme) -> int:
    """How many of a scheme's events play a file of your own that's no longer there, so an
    export would carry no sound for them."""
    return sum(1 for e in EVENTS if custom_missing(ref_for(scheme, e.id), scheme))


def events_using(scheme: Scheme, file_name: str, leaving_out: str = "") -> List[str]:
    """The labels of ``scheme``'s events that play ``file_name``, apart from ``leaving_out``
    (an event id)."""
    wanted = shared.SoundRef("custom", file_name)
    return [e.label for e in EVENTS if e.id != leaving_out and ref_for(scheme, e.id) == wanted]


def without_file(scheme: Scheme, event_id: str) -> None:
    """Remove File: the event goes back to its built-in sound. A built-in scheme just forgets
    its change; one of your own gets the sound in Glass's tone, as a missing file played."""
    event = BY_ID[event_id]
    if scheme.built_in:
        scheme.events.pop(event.key, None)
    else:
        scheme.events[event.key] = built_in_ref(event.sound, DEFAULT_SCHEME)


def copy_files(source: Scheme, target: Scheme) -> int:
    """Copy Scheme: the files ``source``'s events play, copied into ``target``'s folder. A
    file that's missing is left out, as it would be from an export. Returns how many were
    copied; raises OSError if they can't be written."""
    names = [n for n in shared.files_used(APP, source.events)
             if not custom_missing(shared.SoundRef("custom", n), source)]
    target_folder = scheme_folder(target)
    if not names or target_folder is None:
        return 0
    target_folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(custom_path(source, name), target_folder / name)
    return len(names)


def remove_scheme_folder(scheme_id: str) -> None:
    """Delete a scheme's whole folder. A link in its place is removed, never followed."""
    if not scheme_id_ok(scheme_id):
        return
    path = platform_paths.scheme_sounds_dir(scheme_id)
    try:
        if path.is_symlink():
            path.unlink()
        elif os.path.isjunction(path):
            os.rmdir(path)  # removes the junction, not what it points to
        elif path.is_dir():
            shutil.rmtree(path)  # which doesn't follow links or junctions inside it
    except OSError:
        pass  # a file in use can't stop the dialog closing; the folder is just left


def _remove_unused(scheme: Scheme, only: Optional[Iterable[str]] = None) -> None:
    """Delete files in ``scheme``'s folder that none of its events play (only those named in
    ``only``, if given), and the folder itself once it's empty."""
    base = scheme_folder(scheme)
    if base is None or base.is_symlink() or os.path.isjunction(base) or not base.is_dir():
        return
    # Folded, as Windows matches names: "Chime.wav" in an event is the file "chime.wav".
    used = {shared.fold(n) for n in shared.files_used(APP, scheme.events)}
    names = custom_files(scheme) if only is None else [n for n in only if shared.custom_name_ok(n)]
    for name in names:
        if shared.fold(name) in used:
            continue
        try:
            (base / name).unlink()
        except OSError:
            pass  # gone already, or in use: never a reason the dialog can't close
    try:
        base.rmdir()  # only if nothing's left in it
    except OSError:
        pass


@dataclass
class FileChanges:
    """What File, Sounds did that changes which files are kept, done to disk when it closes:
    files go only when you choose OK, so Cancel can take back a delete, a reset or Remove File.
    Files a scheme brings in, or Choose File copies, are written straight away; a scheme made
    in the dialog and then cancelled takes its folder with it."""
    #: Schemes made here (New, Copy, Import, Add from the gallery).
    made: set = field(default_factory=set)
    #: Schemes of your own deleted here.
    deleted: set = field(default_factory=set)
    #: Schemes whose files may no longer be used: a built-in scheme reset, or a scheme updated
    #: from the gallery.
    reset: set = field(default_factory=set)
    #: (scheme id, file name) for each Remove File.
    removed: set = field(default_factory=set)


def apply_file_changes(changes: FileChanges, kept: Iterable[Scheme], ok: bool) -> None:
    """Tidy the sound folders after File, Sounds closes. ``kept`` is the schemes now in
    settings: the dialog's after OK, what there was before after Cancel. A scheme that isn't
    kept loses its folder; after OK, a reset scheme loses the files it no longer plays, and a
    removed file goes unless another event in its scheme still plays it."""
    by_id = {s.id: s for s in kept}
    for scheme_id in changes.made | changes.deleted:
        if scheme_id not in by_id:
            remove_scheme_folder(scheme_id)
    if not ok:
        return
    for scheme_id in changes.reset:
        if scheme_id in by_id:
            _remove_unused(by_id[scheme_id])
    for scheme_id, name in changes.removed:
        if scheme_id in by_id:
            _remove_unused(by_id[scheme_id], only=[name])


# -- Help, Sound Tutorial ------------------------------------------------------------


def tutorial_intro(scheme: Scheme) -> str:
    """The Sound Tutorial's one line: what to press, and which scheme you'll hear. A scheme of
    your own called "Calm scheme" reads "the Calm scheme", not "the Calm scheme scheme"."""
    name = scheme.name.strip()
    if name.casefold().endswith(" scheme"):
        name = name[:-len(" scheme")].rstrip()
    return (f"Arrow to an event and press Space to hear it in the {name} scheme. "
            "Change sounds in File, Sounds.")


def tutorial_note(event_id: str, scheme: Scheme) -> Optional[str]:
    """What the Sound Tutorial says when Space can't simply play the event's sound, or None when
    it can. A file of your own that's gone still plays: the built-in sound it falls back to,
    which is what you'd hear when the event happens."""
    ref = ref_for(scheme, event_id)
    if ref.kind == "none":
        return "No sound."
    if custom_missing(ref, scheme):
        return "Its sound file is missing, so the built-in sound plays."
    return None


def missing_built_ins() -> List[str]:
    """Built-in files this copy of the app lacks, for ``--smoke-test``."""
    return [str(path) for pack in PACKS for name in BUILT_IN
            for path in [built_in_path(name, pack)] if not path.is_file()]


# -- scheme files --------------------------------------------------------------------


def export(scheme: Scheme) -> bytes:
    """A scheme file of ``scheme``, every event pinned to the tone it plays in, with the files
    from the scheme's folder."""
    def read(name: str) -> Optional[bytes]:
        path = custom_path(scheme, name)
        try:
            return path.read_bytes() if path is not None else None
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


def import_scheme(source, taken: Iterable[str], gallery_source: Optional[Dict[str, str]] = None,
                  scheme_id: Optional[str] = None) -> Imported:
    """A new scheme of your own from a scheme file (a path or bytes), its sounds put in its own
    folder. ``scheme_id`` is the scheme an update replaces, whose folder they go in; otherwise
    it gets a new id, and a new, empty folder. Raises ``shared.SchemeFileProblem`` with words to
    show, or OSError if the files can't be written."""
    scheme = Scheme(scheme_id if scheme_id is not None else new_id(), "", False)
    target = scheme_folder(scheme)
    if target is None or not _user_id_ok(scheme.id):
        raise ValueError(f"Not a scheme of your own: {scheme_id!r}")
    # A file the scheme names but doesn't carry counts only if it's in this scheme's own folder.
    read = shared.read_scheme_file(source, APP, have_file=lambda n: (target / n).is_file())
    try:
        installed = shared.install_sounds(read, target)
    except OSError:
        if scheme_id is None:
            remove_scheme_folder(scheme.id)  # a new scheme that won't be added leaves nothing behind
        raise
    scheme.events = {e.key: installed.events.get(e.key, NONE_REF) for e in EVENTS}
    scheme.name = shared.unique_name(read.name, taken)
    scheme.author, scheme.license, scheme.description = read.author, read.license, read.description
    scheme.source = gallery_source
    return Imported(scheme, len(read.sounds), read.dropped)
