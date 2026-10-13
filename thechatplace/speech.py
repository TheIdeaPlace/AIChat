"""Speak announcements through the user's screen reader or a system voice.

Adapted from Image Description Toolkit's ``shared/speech_engine.py`` (Kelly's
own code), which in turn bundles ClaudeSpeak's engine scripts. Changes for
The Chat Place: settings live in ``%APPDATA%/TheChatPlace/speech.json`` and
carry the announcement level (full / summary / silent) and whether turn
endings in every listed session are announced; the temp folder is
``thechatplace-speak``. Speech is on by default, through the screen reader
when one is running, because announcements are the point of this app.

On Windows, JAWS and NVDA are called from this process by
``screen_readers.py``, with NV Access's controller client bundled, because
NVDA doesn't install it and its absence sent every NVDA user to a Windows
voice (#98). When a screen reader is running, a Windows voice never speaks
over it. Windows voices, and VoiceOver and ``say`` on macOS, go through
ClaudeSpeak's engine scripts (TheWorkBench repo), bundled verbatim (IDT's
copy, with its middle-of-the-scale default rates):
``thechatplace/speech/speak-engine.ps1`` and ``speak-engine.sh``. On Linux,
Orca is called from this process by ``orca.py``, and the system voice is
speech-dispatcher, through ``speak-engine-linux.sh``. This module
is the harness around them — settings, engine/voice enumeration, the
detached speaker process, and interruption by killing the previous speaker
before starting the next (the same process model ClaudeSpeak uses for its
Claude Code hook).

Design rules inherited from that investigation, kept on purpose:

* **Screen-reader routes never set voice or rate.** The user already chose
  those in JAWS/NVDA/VoiceOver/Orca; overriding them is an accessibility defect,
  not a feature.
* **Speech never blocks and never raises.** The speaker is a detached hidden
  process; a failure leaves at most ``last-route.log`` in the temp dir.
* **A clean exit only proves something spoke.** ``speech.log`` records which
  route took each screen-reader announcement, because a broken route falling
  back to a system voice is indistinguishable by ear (that's how #98 hid).

No wx imports here: the module is used by the wx app but testable without it.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

__all__ = [
    "SpeechOption",
    "SpeechSettings",
    "Speaker",
    "RATE_PRESETS",
    "default_options",
    "list_speech_options",
    "strip_for_speech",
    "speaker",
    "ANNOUNCE_FULL",
    "ANNOUNCE_SUMMARY",
    "ANNOUNCE_SILENT",
    "ANNOUNCE_LEVELS",
    "ANNOUNCE_LABELS",
]

from .platform_paths import app_data_dir  # noqa: E402

DEFAULT_SETTINGS_PATH = app_data_dir() / "speech.json"

#: Numbers each utterance's file pair (#113). ``time.time_ns()`` alone is not
#: unique: on Windows it ticks every 15.6 ms, so two utterances queued back to
#: back got the same name, and the second overwrote the first's text and
#: config, sometimes while the first engine was still reading them.
_UTTERANCE_NUMBERS = itertools.count()

#: Bytes ``speech.log`` may reach before its older half is dropped.
LOG_LIMIT = 200_000

#: How much is said when a session replies or finishes a turn.
ANNOUNCE_FULL = "full"
ANNOUNCE_SUMMARY = "summary"
ANNOUNCE_SILENT = "silent"
ANNOUNCE_LEVELS = [ANNOUNCE_FULL, ANNOUNCE_SUMMARY, ANNOUNCE_SILENT]
ANNOUNCE_LABELS = {
    ANNOUNCE_FULL: "Full: read the whole reply",
    ANNOUNCE_SUMMARY: "Summary: the session's name and the reply's first sentence",
    ANNOUNCE_SILENT: "Silent: status bar only",
}

#: Preset -> per-engine rate value. Scales differ per engine (OneCore 0.5–6,
#: SAPI -10..10, `say` words per minute), so presets are the shared language
#: and the number is resolved at speak time. "default" means "let the engine
#: decide", and screen-reader routes ignore rate entirely.
RATE_PRESETS = {
    "onecore": {"slow": 1.0, "normal": 3.0, "fast": 4.5, "fastest": 6.0},
    "sapi": {"slow": -5, "normal": 0, "fast": 5, "fastest": 10},
    "say": {"slow": 120, "normal": 175, "fast": 300, "fastest": 450},
    # speech-dispatcher's own scale, -100 to 100 around the voice's default.
    "speechd": {"slow": -40, "normal": 0, "fast": 40, "fastest": 80},
}

RATE_PRESET_LABELS = ["default", "slow", "normal", "fast", "fastest"]

#: Windows notifications while The Chat Place isn't the active window (#20).
NOTIFY_ALL = "all"
NOTIFY_NEEDS_YOU = "needs_you"
NOTIFY_OFF = "off"
NOTIFY_LEVELS = [NOTIFY_ALL, NOTIFY_NEEDS_YOU, NOTIFY_OFF]
NOTIFY_LABELS = {
    NOTIFY_ALL: "When your sessions finish a turn, or any session needs you",
    NOTIFY_NEEDS_YOU: "Only when a session needs you",
    NOTIFY_OFF: "Off",
}

_SCREEN_READER_ENGINES = {"auto", "jaws", "nvda", "voiceover", "orca"}
#: Seconds a JAWS or NVDA call may take before the screen reader is treated
#: as not answering. Both take the text in well under a tenth of a second.
SCREEN_READER_TIMEOUT = 3.0
#: The first call also imports comtypes, loads NVDA's client and scans the
#: processes, which a busy machine at start-up can make slow.
SCREEN_READER_FIRST_TIMEOUT = 15.0
#: What a hung JAWS or NVDA call is reported as, the same each time, so one
#: hang is one notification.
NOT_ANSWERING = "not spoken: the screen reader isn't answering"
#: The engine settings that speak through JAWS or NVDA from this process (#98).
_WINDOWS_SCREEN_READER_ENGINES = {"auto", "jaws", "nvda"}
#: The engine settings that speak through Orca from this process (Linux).
_LINUX_SCREEN_READER_ENGINES = {"auto", "orca"}
IS_LINUX = sys.platform.startswith("linux")
#: Full messages open as a formatted page unless you choose plain text. Not
#: on Linux: Orca 48 with WebKitGTK read a page's headings but not its lines
#: when tested, and the plain text box reads by line, word and character.
FORMATTED_BY_DEFAULT = not IS_LINUX
#: What speaks when no screen reader takes an announcement, for the log.
SYSTEM_VOICE = "a Windows voice" if sys.platform == "win32" else "a system voice"


@dataclass
class SpeechOption:
    """One selectable entry for the Speech settings tab."""

    engine: str  # auto | jaws | nvda | onecore | sapi | voiceover | say | orca | speechd
    voice: str  # match string for voice engines, "" otherwise
    label: str  # what the picker shows

    @property
    def is_screen_reader(self) -> bool:
        return self.engine in _SCREEN_READER_ENGINES

    @property
    def has_rate(self) -> bool:
        return self.engine in RATE_PRESETS


def _default_fields() -> List[str]:
    from .sessions import DEFAULT_FIELDS
    return list(DEFAULT_FIELDS)


@dataclass
class SpeechSettings:
    """What the user chose. Persisted as one small JSON file."""

    announce: str = ANNOUNCE_FULL
    announce_all_sessions: bool = True
    #: Read Kelly's own message back when it's sent or queued (issue #178).
    announce_own: bool = True
    engine: str = "auto"
    voice: str = ""
    rate_preset: str = "default"
    #: Read Full Message opens a formatted page (headings, lists, tables) when
    #: Edge WebView2 (or on Linux WebKitGTK) is there, rather than the plain
    #: text box (issue #190).
    formatted_messages: bool = FORMATTED_BY_DEFAULT
    #: How the session list is sorted (View, Sort Sessions): a value from
    #: ``sessions.SORT_VALUES``. Kept here so the choice survives a restart.
    session_order: str = "status"
    #: The screen reader reads each message in the messages list whole, not
    #: just the first line the row shows (#11).
    full_messages_in_list: bool = True
    #: Which sessions the list shows (View, Show Sessions, #32): a value from
    #: ``sessions.VIEWS`` or "group:<name>".
    session_view: str = "all"
    #: Windows notifications when The Chat Place isn't the active window: a
    #: value from ``NOTIFY_LEVELS``.
    notifications: str = NOTIFY_ALL
    #: Remote Control for The Chat Place's sessions unless a session says
    #: otherwise (#72).
    remote_control: bool = False
    #: The session list's columns, in the order each row reads them (View,
    #: Session List Columns, #134): ids from ``sessions.FIELD_IDS``.
    session_fields: List[str] = field(default_factory=_default_fields)
    #: "The Chat Place Update Installed" after an update (#141); Settings
    #: turns it off.
    update_installed_notice: bool = True
    #: The version that last started, recorded by installed copies only, so
    #: the first start after an update can say so (#141).
    last_run_version: str = ""
    #: How many recent folders New Session offers (#215); 0 offers none.
    recent_folder_limit: int = 20

    @property
    def enabled(self) -> bool:
        return self.announce != ANNOUNCE_SILENT

    def resolved_rate(self):
        """The engine-scale rate number, or None for "engine default"."""
        return RATE_PRESETS.get(self.engine, {}).get(self.rate_preset)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "SpeechSettings":
        path = path or DEFAULT_SETTINGS_PATH
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except Exception:
            return cls._unreadable()
        if not isinstance(raw, dict):
            return cls._unreadable()
        settings = cls()
        level = str(raw.get("announce", ANNOUNCE_FULL))
        settings.announce = level if level in ANNOUNCE_LEVELS else ANNOUNCE_FULL
        settings.announce_all_sessions = bool(raw.get("announce_all_sessions", True))
        settings.announce_own = bool(raw.get("announce_own", True))
        settings.engine = str(raw.get("engine", "auto")) or "auto"
        settings.voice = str(raw.get("voice", ""))
        preset = str(raw.get("rate_preset", "default"))
        settings.rate_preset = preset if preset in RATE_PRESET_LABELS else "default"
        settings.formatted_messages = bool(raw.get("formatted_messages", FORMATTED_BY_DEFAULT))
        from .sessions import SORT_STATUS, SORT_VALUES
        order = str(raw.get("session_order", SORT_STATUS))
        settings.session_order = order if order in SORT_VALUES else SORT_STATUS
        settings.full_messages_in_list = bool(raw.get("full_messages_in_list", True))
        from .sessions import GROUP_VIEW_PREFIX, VIEWS
        view = str(raw.get("session_view", "all"))
        known = view in [v for v, _label in VIEWS] or (
            view.startswith(GROUP_VIEW_PREFIX) and len(view) > len(GROUP_VIEW_PREFIX))
        settings.session_view = view if known else "all"
        notify = str(raw.get("notifications", NOTIFY_ALL))
        settings.notifications = notify if notify in NOTIFY_LEVELS else NOTIFY_ALL
        settings.remote_control = raw.get("remote_control") is True
        from .sessions import clean_fields
        settings.session_fields = clean_fields(raw.get("session_fields"))
        settings.update_installed_notice = raw.get("update_installed_notice") is not False
        last = raw.get("last_run_version", "")
        settings.last_run_version = last.strip() if isinstance(last, str) else ""
        from .workplaces import RECENT_LIMIT, RECENT_MAX
        limit = raw.get("recent_folder_limit", RECENT_LIMIT)
        settings.recent_folder_limit = limit if isinstance(limit, int) \
            and not isinstance(limit, bool) and 0 <= limit <= RECENT_MAX else RECENT_LIMIT
        return settings

    #: True when settings.json was there but couldn't be read, so these are
    #: defaults standing in for it. Not a field: never saved. Nothing saves
    #: on its own over such a file (#141), only a choice you make.
    unreadable = False

    @classmethod
    def _unreadable(cls) -> "SpeechSettings":
        settings = cls()
        settings.unreadable = True
        return settings

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path or DEFAULT_SETTINGS_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Bundled script location
# ---------------------------------------------------------------------------


def engine_script_names() -> List[str]:
    """The speech scripts this platform runs, which a build must carry."""
    if sys.platform == "win32":
        return ["speak-engine.ps1", "speak-voices.ps1"]
    if IS_LINUX:
        return ["speak-engine-linux.sh"]
    return ["speak-engine.sh", "speak-voices.sh"]


def _script_dir() -> Path:
    if getattr(sys, "frozen", False):
        # `Path(x) or fallback` never reaches the fallback: Path("") is
        # Path("."), which is truthy, so a missing _MEIPASS resolved the
        # scripts against the current working directory. Test the string.
        meipass = getattr(sys, "_MEIPASS", "")
        base = Path(meipass) if meipass else Path(sys.executable).parent
        return base / "thechatplace" / "speech"
    return Path(__file__).resolve().parent / "speech"


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


#: A Windows voice speaks only when no screen reader is running (#98).
AUTO_LABEL = "Automatic (your screen reader, or a system voice when none is running)"


def default_options() -> List[SpeechOption]:
    """What the picker offers when probing fails or has not finished.

    "Automatic" always works — the running screen reader, else a system
    voice — so a failed probe degrades to fewer choices, never to a broken
    feature.
    """
    options = [
        SpeechOption("auto", "", AUTO_LABEL)
    ]
    if sys.platform == "win32":
        options += [
            SpeechOption("jaws", "", "JAWS screen reader"),
            SpeechOption("nvda", "", "NVDA screen reader"),
        ]
    elif sys.platform == "darwin":
        options += [
            SpeechOption("voiceover", "", "VoiceOver"),
            SpeechOption("say", "", "macOS system voice (default)"),
        ]
    elif IS_LINUX:
        options += [
            SpeechOption("orca", "", "Orca screen reader"),
            SpeechOption("speechd", "", "speech-dispatcher voice (default)"),
        ]
    return options


def _parse_windows_probe(raw: str) -> List[SpeechOption]:
    data = json.loads(raw)
    options = [
        SpeechOption("auto", "", AUTO_LABEL)
    ]

    def _as_list(value):
        # PowerShell 5.1 serialises a one-element array as a bare object.
        if isinstance(value, dict):
            return [value]
        return value or []

    for reader in _as_list(data.get("screenReaders")):
        # "available" means the probe found NVDA's controller client itself.
        # The app ships its own now (#98), so installed is enough; leaving
        # NVDA out of the list, with no reason given, hid the #98 bug.
        usable = reader.get("available") or (
            reader.get("engine") == "nvda" and (reader.get("installed") or reader.get("running")))
        if not usable:
            continue
        name = reader.get("name") or reader.get("engine", "").upper()
        suffix = "" if reader.get("running") else " (not running right now)"
        options.append(
            SpeechOption(reader.get("engine", ""), "", f"{name} screen reader{suffix}")
        )

    for voice in _as_list(data.get("systemVoices")):
        engine = voice.get("engine", "")
        if engine not in ("onecore", "sapi"):
            continue
        display = voice.get("displayName", "") or voice.get("match", "")
        kind = "OneCore" if engine == "onecore" else "SAPI"
        options.append(
            SpeechOption(engine, voice.get("match", display),
                         f"{display} — Windows voice ({kind})")
        )
    return options


def _parse_mac_voices(raw: str) -> List[SpeechOption]:
    """Voices out of ``say -v '?'`` lines, English only (there are 400+)."""
    options: List[SpeechOption] = []
    for line in raw.splitlines():
        match = re.match(r"^(.*?)\s{2,}([a-z]{2}[_-][A-Z]{2})\s+#", line)
        if not match:
            continue
        name, locale = match.group(1).strip(), match.group(2)
        if not locale.startswith("en"):
            continue
        options.append(SpeechOption("say", name, f"{name} ({locale}) — macOS voice"))
    return options


def _voice_language() -> str:
    """The two-letter language voices are offered in: the user's own
    (``LANG``), or English when it's unset or "C"."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        if value:  # the first one set decides, as it does for the system
            if value in ("C", "POSIX") or value.startswith("C."):
                return "en"
            return value[:2].lower()
    return "en"


def _parse_speechd_voices(raw: str, language: str = "en") -> List[SpeechOption]:
    """Voices out of ``spd-say -L``: a NAME LANGUAGE VARIANT table. Only the
    user's language, and no "+Variant" voices: espeak-ng alone lists over a
    thousand."""
    options: List[SpeechOption] = []
    for line in raw.splitlines():
        match = re.match(r"^\s*(.+?)\s{2,}([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)\s+(\S+)\s*$", line)
        if not match or match.group(1) == "NAME":
            continue
        name, locale = match.group(1).strip(), match.group(2)
        if not locale.lower().startswith(language) or "+" in name:
            continue
        options.append(SpeechOption("speechd", name, f"{name} ({locale}) — speech-dispatcher voice"))
    return options


def list_speech_options(timeout: float = 25.0) -> List[SpeechOption]:
    """Probe this machine for speech routes. Falls back to defaults on error.

    Windows runs the bundled ClaudeSpeak probe (JAWS COM registration, NVDA
    controller DLL with a PE-architecture check, OneCore and SAPI voice
    lists). macOS asks ``say`` directly and checks for VoiceOver — no shell
    probe, so there is no jq dependency for enumeration. Linux asks
    speech-dispatcher (``spd-say -L``).
    """
    try:
        if sys.platform == "win32":
            probe = _script_dir() / "speak-voices.ps1"
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(probe)],
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                return _parse_windows_probe(result.stdout)
        elif sys.platform == "darwin":
            options = [
                SpeechOption("auto", "",
                             "Automatic (VoiceOver if running, else a system voice)"),
                SpeechOption("voiceover", "", "VoiceOver"),
            ]
            result = subprocess.run(
                ["/usr/bin/say", "-v", "?"],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                options.extend(_parse_mac_voices(result.stdout))
            return options
        elif IS_LINUX:
            options = default_options()
            result = subprocess.run(["spd-say", "-L"], capture_output=True, text=True,
                                    timeout=timeout)
            if result.returncode == 0:
                options.extend(_parse_speechd_voices(result.stdout, _voice_language()))
            return options
    except Exception:
        # A failed probe (no PowerShell, timeout, malformed JSON) must
        # degrade to the static defaults below, never break the settings UI.
        pass
    return default_options()


# ---------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------

#: What a code block is read as.
CODE_NOTE = "Code block omitted."
# An unclosed fence (a pasted, truncated snippet) runs to the end of the text.
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.MULTILINE)
_RULE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3})(\S(?:.*?\S)?)\1")
# Underscores only count as emphasis at a word boundary. Without the
# lookarounds this ate the internal underscores of snake_case identifiers:
# "MAX_TOOL_ROUNDS" was spoken as "MAXTOOLROUNDS" and "some_var_name" as
# "somevarname" — names that do not exist in the code being discussed.
_EMPHASIS_UNDERSCORE = re.compile(r"(?<!\w)(_{1,3})(\S(?:.*?\S)?)\1(?!\w)")


def without_code_blocks(text: str, replacement: str) -> str:
    """Fenced code blocks (backticks or tildes), closed or not, replaced by
    ``replacement``."""
    from .codeblocks import replace_code_blocks
    return replace_code_blocks(text, lambda block: replacement)


def strip_for_speech(text: str) -> str:
    """Markdown → something worth hearing.

    Code blocks become a short note instead of minutes of punctuation
    soup; links keep their text and lose their URL; heading and emphasis
    markers vanish, and so do quote markers and horizontal rules.
    Deliberately light-handed — the goal is listenable, not a full renderer.
    """
    text = without_code_blocks(text or "", f" {CODE_NOTE} ")
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _RULE.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _EMPHASIS_UNDERSCORE.sub(r"\2", text)
    text = text.replace("|", " ")
    return text.strip()


class Speaker:
    """Runs the bundled engine script, one utterance at a time.

    Utterances are queued and spoken in order by a background thread, each
    engine process finishing before the next starts, so two system-voice
    utterances never talk over each other. An interrupting utterance (an
    announcement) first stops everything: the queue is emptied and every
    engine process still running is killed (ClaudeSpeak's model, held
    in-process). A non-interrupting one (a short confirmation) waits its turn.
    For screen-reader routes the interrupt also happens at the API level —
    ``SayString(text, true)`` / ``nvdaController_cancelSpeech()`` — because
    killing our process cannot silence speech the reader already queued.
    """

    def __init__(self, popen=subprocess.Popen, screen_readers=None):
        self._popen = popen
        self._screen_readers = screen_readers
        self._lock = threading.Lock()
        self._queue: "deque[tuple]" = deque()
        self._running: List[subprocess.Popen] = []
        self._worker: Optional[threading.Thread] = None
        self._generation = 0
        #: Called on the speech thread with a one-line reason when a screen
        #: reader is running but didn't take an announcement (#98).
        self.on_problem: Optional[Callable[[str], None]] = None
        #: What the last screen-reader announcement did, for the bug report.
        self.last_route = ""
        #: Why the last one wasn't spoken while a screen reader was running;
        #: "" once one is spoken again (Settings shows it).
        self.last_problem = ""
        self._reader_call: Optional[threading.Thread] = None
        self._readers_warm = False
        #: Linux: ``(text, interrupt) -> bool``, which the window sets so
        #: Orca can be reached through it while it's active (orca.py).
        self.window_announcer: Optional[Callable[[str, bool], bool]] = None

    def _readers(self):
        if self._screen_readers is None:
            if IS_LINUX:
                from .orca import Orca
                self._screen_readers = Orca()
            else:
                from .screen_readers import ScreenReaders
                self._screen_readers = ScreenReaders()
        if hasattr(self._screen_readers, "window_announcer"):
            self._screen_readers.window_announcer = self.window_announcer
        return self._screen_readers

    @property
    def workdir(self) -> Path:
        path = Path(tempfile.gettempdir()) / "thechatplace-speak"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _command(self, text_file: Path, config_file: Path) -> Optional[list]:
        script_dir = _script_dir()
        if sys.platform == "win32":
            return [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script_dir / "speak-engine.ps1"),
                "-Path", str(text_file), "-ConfigPath", str(config_file),
            ]
        if sys.platform == "darwin":
            return [
                "/bin/bash", str(script_dir / "speak-engine.sh"),
                "--path", str(text_file), "--config", str(config_file),
            ]
        if IS_LINUX:
            return [
                "/bin/bash", str(script_dir / "speak-engine-linux.sh"),
                "--path", str(text_file), "--config", str(config_file),
            ]
        return None

    def speak(self, text: str, settings: SpeechSettings, interrupt: bool = True) -> bool:
        """Queue ``text``; returns False when speech is unavailable.

        Never raises and never blocks. ``interrupt=True`` stops whatever is
        being said or waiting first; ``interrupt=False`` (The Chat Place's short
        confirmations) waits for it, and asks a screen reader to queue rather
        than cut itself off.
        """
        spoken = strip_for_speech(text)
        if not spoken:
            return False
        if interrupt:
            self.stop()
        try:
            # One file pair per utterance: two utterances close together
            # must not overwrite each other's text before it is read. The
            # pid and counter keep names unique within a clock tick and
            # between two copies of the app sharing the folder (#113).
            self._sweep_old_files()
            stem = f"say-{time.time_ns()}-{os.getpid()}-{next(_UTTERANCE_NUMBERS)}"
            text_file = self.workdir / f"{stem}.txt"
            config_file = self.workdir / f"{stem}.json"
            # UTF-8 without BOM on purpose: the engine scripts read UTF-8,
            # and a BOM breaks ConvertFrom-Json in Windows PowerShell.
            text_file.write_text(spoken, encoding="utf-8")
            engine, voice, rate = settings.engine, settings.voice, settings.resolved_rate()
            request = None
            if sys.platform == "win32" and settings.engine in _WINDOWS_SCREEN_READER_ENGINES:
                # JAWS and NVDA are called from this process (#98). The engine
                # script only runs if no screen reader is, so it gets a plain
                # Windows voice and can't reach for a screen reader itself.
                request = (spoken, settings.engine, interrupt)
                engine, voice, rate = "onecore", "", None
            elif IS_LINUX and settings.engine in _LINUX_SCREEN_READER_ENGINES:
                # Orca is called from this process; the script is the
                # speech-dispatcher voice for when it isn't running or
                # can't take the text.
                request = (spoken, settings.engine, interrupt)
                engine, voice, rate = "speechd", "", None
            config_file.write_text(
                json.dumps(
                    {
                        "engine": engine,
                        "voice": voice,
                        "rate": rate,
                        "interrupt": interrupt,
                        "nvdaClientDll": "",
                    }
                ),
                encoding="utf-8",
            )
            command = self._command(text_file, config_file)
            if command is None:
                return False
        except Exception:
            return False
        self._log(spoken, settings, interrupt)
        with self._lock:
            self._queue.append((command, request))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._drain, name="speech",
                                                daemon=True)
                self._worker.start()
        return True

    def _drain(self) -> None:
        self._drain_queue()

    def _drain_queue(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                command, request = self._queue.popleft()
                generation = self._generation
            if request is not None:
                if generation != self._generation:
                    continue  # stop() ran after it left the queue
                if self._to_screen_reader(request):
                    continue
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                # Its own process group, so stopping it stops what it started
                # too: speak-engine.sh runs osascript as a child (to fall back
                # to say when VoiceOver refuses), and killing only the script
                # left osascript to hand VoiceOver stale text.
                kwargs["start_new_session"] = True
            try:
                process = self._popen(command, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,
                                      stdin=subprocess.DEVNULL, **kwargs)
            except Exception:
                continue
            with self._lock:
                if generation != self._generation:
                    # stop() ran while this one was starting.
                    _kill_quietly(process)
                    continue
                self._running.append(process)
            # No timeout: a system voice reading a long reply at the "Full"
            # level can take minutes, and cutting it off mid-sentence is worse
            # than waiting. stop() (an interrupting announcement, or closing
            # the app) kills it when it needs to end early.
            try:
                process.wait()
            except Exception:
                _kill_quietly(process)
            with self._lock:
                if process in self._running:
                    self._running.remove(process)

    def _to_screen_reader(self, request) -> bool:
        """Hand an announcement to the running screen reader. True when that
        settles it, so the Windows voice must not speak; False when no screen
        reader is running and it may.

        A screen reader that's running but didn't answer also settles it: a
        Windows voice talking over the user's screen reader, in a voice they
        didn't choose, is the defect in #98. The reason goes to
        ``on_problem`` instead, once until speech works again, and the
        announcement is still in the status bar.
        """
        outcome, failure = self._ask_screen_readers(request)
        if failure:
            self._settle(failure, problem=failure)
            return True
        if outcome is None:
            self._settle(f"screen readers couldn't be checked, so {SYSTEM_VOICE}")
            return False
        if not outcome.running:
            self._settle(f"no screen reader running, so {SYSTEM_VOICE}")
            return False
        if getattr(outcome, "voice_allowed", False):
            # Linux: Orca couldn't take it, and speech-dispatcher, which
            # speaks one message at a time, may (orca.py).
            self._settle(outcome.describe())
            return False
        self._settle(outcome.describe(),
                     problem=outcome.describe() if outcome.unreachable else "")
        return True

    def _ask_screen_readers(self, request):
        """(outcome, "") from screen_readers, or (None, why) when a screen
        reader hung, or (None, "") when asking failed outright.

        On a thread of its own with a deadline: the old model ran every
        utterance in a process stop() could kill, but these are calls into
        JAWS and NVDA, and one that hangs mustn't stop all speech. While a
        hung call is still out, the next announcements aren't sent at all,
        so hung threads can't pile up.
        """
        with self._lock:
            if self._reader_call is not None and self._reader_call.is_alive():
                return None, NOT_ANSWERING
        result = {}

        def call():
            # JAWS is a COM object, so COM is set up on the thread that calls
            # it. (Importing comtypes first may set it up too; the thread's
            # end undoes both.)
            com = None
            if sys.platform == "win32":
                try:
                    import comtypes
                    comtypes.CoInitialize()
                    com = comtypes
                except Exception:  # noqa: BLE001 - JAWS then reports why it failed
                    com = None
            try:
                result["outcome"] = self._readers().speak(*request)
            except Exception:  # noqa: BLE001 - speech never raises
                pass
            finally:
                if com is not None:
                    try:
                        com.CoUninitialize()
                    except Exception:  # noqa: BLE001
                        pass

        thread = threading.Thread(target=call, name="screen-reader", daemon=True)
        with self._lock:
            self._reader_call = thread
        thread.start()
        thread.join(SCREEN_READER_TIMEOUT if self._readers_warm else SCREEN_READER_FIRST_TIMEOUT)
        if thread.is_alive():
            return None, NOT_ANSWERING
        self._readers_warm = True
        return result.get("outcome"), ""

    def _settle(self, route: str, problem: str = "") -> None:
        """Record what happened to a screen-reader announcement, and tell
        ``on_problem`` about a new problem, once until speech works again."""
        self.last_route = route
        self._log_route(route)
        if not problem:
            self.last_problem = ""
            return
        if problem == self.last_problem:
            return
        self.last_problem = problem
        if self.on_problem is not None:
            try:
                self.on_problem(problem)
            except Exception:  # noqa: BLE001
                pass

    def busy(self) -> bool:
        with self._lock:
            return bool(self._queue or self._running)

    def stop(self) -> None:
        """Empty the queue and kill every engine process. Never raises.

        Stops system-voice audio immediately (the synthesizer lives in those
        processes). Speech already queued inside JAWS/NVDA/VoiceOver keeps
        their own silence key as the off switch — same behaviour as
        ClaudeSpeak.
        """
        with self._lock:
            self._generation += 1
            self._queue.clear()
            running, self._running = self._running, []
        for process in running:
            _kill_quietly(process)
        if running and IS_LINUX:
            _cancel_speech_dispatcher()

    def _log(self, text: str, settings: SpeechSettings, interrupt: bool) -> None:
        """One line per utterance in ``speech.log`` beside the engine files.

        The Chat Place's own record of what it handed to the engine and when;
        a later interrupting utterance can still cut one off, so a line is not
        proof it was heard. The engine script's ``last-route.log`` can't
        serve: ClaudeSpeak's hook
        writes the same file, and it holds only the latest utterance. Kept
        under ``LOG_LIMIT`` bytes by dropping the older half.
        """
        try:
            path = self.workdir / "speech.log"
            flat = " ".join(text.split())
            opening = flat if len(flat) <= 80 else flat[:79] + "…"
            line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"{'interrupt' if interrupt else 'queue'} {settings.engine} "
                    f"{len(text)} chars: {opening}\n")
            if path.exists() and path.stat().st_size > LOG_LIMIT:
                kept = path.read_text(encoding="utf-8", errors="replace")[-(LOG_LIMIT // 2):]
                kept = kept[kept.find("\n") + 1:]  # start on a whole line
                path.write_text(kept, encoding="utf-8")
            with path.open("a", encoding="utf-8") as log:
                log.write(line)
        except Exception:  # noqa: BLE001 - a log must never stop speech
            pass

    def _log_route(self, route: str) -> None:
        """What happened to the announcement logged just before, so a bug
        report can say whether the screen reader spoke (#98)."""
        try:
            with (self.workdir / "speech.log").open("a", encoding="utf-8") as log:
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} route: {route}\n")
        except Exception:  # noqa: BLE001 - a log must never stop speech
            pass

    def _sweep_old_files(self, max_age: float = 300.0) -> None:
        cutoff = time.time() - max_age
        try:
            for old in self.workdir.glob("say-*"):
                try:
                    if old.stat().st_mtime < cutoff:
                        old.unlink()
                except OSError:
                    pass
        except OSError:
            pass


def _cancel_speech_dispatcher() -> None:
    """Linux: killing ``spd-say`` doesn't stop text speech-dispatcher already
    has, so it's told to cancel. Never raises, never waits."""
    tool = shutil.which("spd-say")
    if not tool:
        return
    try:
        subprocess.Popen([tool, "-C"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def _kill_quietly(process) -> None:
    try:
        if process.poll() is None:
            if sys.platform != "win32":
                try:
                    # The whole group (see start_new_session where it starts).
                    os.killpg(process.pid, signal.SIGKILL)
                    return
                except (OSError, AttributeError, TypeError):
                    pass
            process.kill()
    except Exception:
        # It may have exited between poll and kill, or the OS may refuse;
        # either way there is nothing further to stop.
        pass


#: Module-level speaker shared by the app — one voice at a time is the point.
speaker = Speaker()
