"""Your keyboard shortcuts (#635): each command's key, yours or its default.

Kept in ``keys.json`` in The Chat Place's folder as ``{"version": 1, "keys":
{command id: key}}``, only for the commands you changed; "" means you removed
its key. Keys are stored with Windows names ("Ctrl+Shift+Q"), and Ctrl means
Command on a Mac, as everywhere in the app, so a backup restores on either.
Ids this release doesn't know are kept as they are, for the release that
wrote them.

Which keys can be given out, and why some can't, is here too, without wx, so
it's tested on its own. The window turns key presses into these names
(``ui/keys.py``) and asks which command one belongs to.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, Optional

from . import commands as command_list
from . import platform_paths
from .provider_storage import atomic_write_json

MODIFIERS = ("Ctrl", "Alt", "Shift")
_MODIFIER_NAMES = {"ctrl": "Ctrl", "control": "Ctrl", "cmd": "Ctrl", "command": "Ctrl",
                   "alt": "Alt", "option": "Alt", "opt": "Alt", "shift": "Shift"}
#: Keys by every name people write them with, to the one name used here.
_KEY_NAMES = {
    "enter": "Enter", "return": "Enter", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "ins": "Insert", "home": "Home", "end": "End",
    "pageup": "PageUp", "pgup": "PageUp", "pagedown": "PageDown", "pgdn": "PageDown",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "space": "Space", "tab": "Tab", "escape": "Escape", "esc": "Escape",
    "backspace": "Backspace", "back": "Backspace",
    "comma": ",", "period": ".", "dot": ".", "slash": "/", "semicolon": ";",
    "minus": "-", "equals": "=", "equal": "=", "backslash": "\\", "quote": "'",
    "apostrophe": "'", "backquote": "`", "grave": "`",
    "leftbracket": "[", "rightbracket": "]",
}
PUNCTUATION = ",./;-=\\'`[]"
#: How a punctuation key is said in a sentence ("Ctrl+Period"); menus keep
#: the character, which is what wx's accelerators read.
_SPOKEN = {",": "Comma", ".": "Period", "/": "Slash", ";": "Semicolon", "-": "Minus",
           "=": "Equals", "\\": "Backslash", "'": "Apostrophe", "`": "Grave accent",
           "[": "Left bracket", "]": "Right bracket"}
_NAVIGATION = {"Up", "Down", "Left", "Right", "Home", "End", "PageUp", "PageDown",
               "Tab", "Escape", "Backspace", "Space"}

#: Keys the window keeps for itself: they mean something in one part of it,
#: or everywhere, and can't be given to a command (#635).
FIXED = {
    "Enter": "loads a session and reads a message",
    "Ctrl+Enter": "sends from the reply box",
    "Ctrl+Shift+Enter": "sends and saves a prompt from the reply box",
    "Ctrl+Alt+Enter": "steers from the reply box",
    "Delete": "hides a session and removes an attachment",
    "Shift+Delete": "deletes a session permanently",
    "Ctrl+C": "copies",
    "Ctrl+Shift+C": "copies a message's last code block",
    "Ctrl+Shift+B": "lists a message's code blocks",
    "F6": "moves between parts of the window",
    "Shift+F6": "moves between parts of the window",
    "F10": "goes to the menu bar",
    "Shift+F10": "opens the context menu",
    "Alt+F4": "closes the window",
}


def normalize(key: str) -> str:
    """``key`` in the one form used here: modifiers in the order Ctrl, Alt,
    Shift, then the key ("Shift+ctrl+q" is "Ctrl+Shift+Q"). Raises
    ValueError for something that isn't a key."""
    text = (key or "").strip()
    if not text:
        raise ValueError("No key.")
    # A key that is itself "+" isn't one we give out; any "+" splits.
    parts = [p.strip() for p in text.split("+")]
    if any(not p for p in parts):
        raise ValueError(f"{key!r} isn't a key.")
    mods = set()
    for part in parts[:-1]:
        name = _MODIFIER_NAMES.get(part.lower())
        if name is None:
            raise ValueError(f"{part!r} isn't Ctrl, Alt or Shift.")
        mods.add(name)
    last = parts[-1]
    if last.lower() in _MODIFIER_NAMES:
        raise ValueError("Press a key with Ctrl, Alt or Shift, not only the modifier.")
    name = _KEY_NAMES.get(last.lower().replace(" ", ""))
    if name is None:
        if len(last) == 1 and (last.isascii() and last.isalnum()):
            name = last.upper()
        elif len(last) == 1 and last in PUNCTUATION:
            name = last
        elif re.fullmatch(r"[Ff]([1-9]|1[0-9]|2[0-4])", last):
            name = last.upper()
        else:
            raise ValueError(f"{last!r} isn't a key The Chat Place knows.")
    return "+".join([m for m in MODIFIERS if m in mods] + [name])


def split(key: str):
    """(modifiers, key name) of a normalized key."""
    parts = key.split("+")
    return set(parts[:-1]), parts[-1]


def spoken(key: str, platform: Optional[str] = None) -> str:
    """``key`` as a sentence names it: "Ctrl+Period", or on a Mac
    "Cmd+Period". "" for no key."""
    if not key:
        return ""
    from .ui_text import key_name
    mods, name = split(key)
    return key_name("+".join([m for m in MODIFIERS if m in mods] + [_SPOKEN.get(name, name)]),
                    platform)


def refusal(key: str, platform: Optional[str] = None) -> str:
    """Why ``key`` (normalized) can't be given to a command, or "" if it can.
    Said in the capture dialog as it is."""
    platform = platform or sys.platform
    if key in FIXED:
        return f"{spoken(key, platform)} can't be changed: it {FIXED[key]}."
    mods, name = split(key)
    if name == "Insert":
        return "Screen readers use Insert, so it can't be a shortcut."
    if name in _NAVIGATION:
        return f"{_SPOKEN.get(name, name)} moves around the window and text, so it can't be a shortcut."
    function_key = name.startswith("F") and name[1:].isdigit()
    if not mods and not function_key:
        return "A key on its own types or moves. Use it with Ctrl or Alt, or choose an F key."
    if mods == {"Shift"} and not function_key:
        return "Shift with it types a character. Use it with Ctrl or Alt, or choose an F key."
    if name == "F4" and "Alt" in mods:
        return f"{spoken(key, platform)} closes the window, so it can't be a shortcut."
    if name == "Delete":
        return f"{spoken(key, platform)} deletes text, so it can't be a shortcut."
    if "Alt" in mods and "Ctrl" not in mods and not function_key and name != "Enter":
        return ("Alt with a letter, number or mark opens a menu or presses a button. "
                "Use Ctrl with it too.")
    if (platform == "win32" and {"Ctrl", "Alt"} <= mods and not function_key
            and name != "Enter"):
        # Windows reads AltGr as Ctrl+Alt: on many keyboards it types @, €,
        # braces and letters, which a window-wide shortcut would take (#639).
        return ("On many keyboards, Ctrl and Alt together with a letter, number or mark "
                "type a character (AltGr). Use Ctrl and Shift, or an F key.")
    if mods == {"Ctrl"} and name in ("X", "V", "A", "Z", "Y"):
        return f"{spoken(key, platform)} edits text, so it can't be a shortcut."
    if platform == "darwin" and mods == {"Ctrl"} and name in ("Q", "H", "M", "W"):
        return f"{spoken(key, platform)} belongs to macOS."
    if platform == "darwin" and mods == {"Ctrl", "Shift"} and name in ("Q", "Z"):
        return f"{spoken(key, platform)} belongs to macOS."
    return ""


def customizable(command: command_list.Command, platform: Optional[str] = None) -> bool:
    """A command whose key you can change. Not one whose key belongs to a
    part of the window (Delete in the session list, Ctrl+Shift+Enter in the
    reply box), one whose label the window writes (Check for Updates), nor,
    on a Mac, Settings, About and Quit, which macOS keeps in its app menu
    with its own keys."""
    platform = platform or sys.platform
    if command.shown_key or command.fixed_key:
        return False
    return not (command.stock and platform == "darwin")


class KeyMap:
    """The keys in effect: your changes over the commands' defaults."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / "keys.json"
        #: Your changes by command id: a normalized key, or "" for none.
        self._changes: Dict[str, str] = {}
        #: Entries for ids this release doesn't know, kept as they were.
        self._unknown: Dict[str, object] = {}
        self.load_error = ""
        self._save_blocked = False
        self._defaults = {c.id: normalize(c.key) if c.key else ""
                          for c in command_list.commands() if customizable(c)}
        self.load()

    # -- reading ------------------------------------------------------------

    def load(self) -> None:
        self._changes, self._unknown = {}, {}
        self.load_error = ""
        self._save_blocked = False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return
        except OSError as exc:
            self.load_error = (f"Couldn't read your keyboard shortcuts ({self.path}): {exc}. "
                               "The default keys are in use, and changes won't be saved "
                               "until The Chat Place can read the file.")
            self._save_blocked = True
            return
        except ValueError as exc:
            self._set_aside(f"isn't valid JSON ({exc})")
            return
        keys = raw.get("keys") if isinstance(raw, dict) else None
        if not isinstance(keys, dict):
            self._set_aside("isn't in the expected format")
            return
        bad = 0
        for command_id, key in keys.items():
            if command_id not in self._defaults:
                self._unknown[command_id] = key
                continue
            if not isinstance(key, str):
                bad += 1
                continue
            try:
                key = normalize(key) if key else ""
            except ValueError:
                bad += 1
                continue
            if key and refusal(key):
                bad += 1
                continue
            self._changes[command_id] = key
        bad += self._settle()
        if bad:
            self.load_error = (f"{bad} of your keyboard shortcuts in {self.path} couldn't be "
                               "used, so those commands have their default keys.")

    def _settle(self) -> int:
        """No two commands on one key after reading a file written by hand
        or by another release (#639). A key you chose wins over a default:
        the command with it by default loses it, as when you choose it
        here. Two of your own on one key: the first keeps it, and the other
        goes back to its default (or none, if that's taken too). Returns how
        many of yours couldn't be kept."""
        dropped = 0
        owner: Dict[str, str] = {}
        for command_id, key in list(self._changes.items()):
            if not key:
                continue
            if key in owner:
                del self._changes[command_id]
                dropped += 1
            else:
                owner[key] = command_id
        for command_id in self._defaults:
            if command_id in self._changes:
                continue
            default = self.default(command_id)
            if default and owner.get(default, command_id) != command_id:
                self._changes[command_id] = ""  # yours took its key
            elif default:
                owner[default] = command_id
        return dropped

    def _set_aside(self, why: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.bad-{stamp}")
        try:
            shutil.move(str(self.path), str(backup))
        except OSError as exc:
            self.load_error = (f"Your keyboard shortcuts file ({self.path}) {why}, and it "
                               f"couldn't be moved aside ({exc}). The default keys are in "
                               "use, and changes won't be saved until it's fixed or removed.")
            self._save_blocked = True
            return
        self.load_error = (f"Your keyboard shortcuts file {why}, so the default keys are in "
                           f"use. It was kept as {backup.name}.")

    # -- asking --------------------------------------------------------------

    def key(self, command_id: str) -> str:
        """The command's key now, normalized; "" for none."""
        if command_id in self._changes:
            return self._changes[command_id]
        return self._defaults.get(command_id, "")

    def default(self, command_id: str) -> str:
        return self._defaults.get(command_id, "")

    def changed(self, command_id: str) -> bool:
        return command_id in self._changes and self._changes[command_id] != self.default(command_id)

    def items(self) -> Iterator:
        """(command id, key) for every command you can change, in menu order."""
        for command_id in self._defaults:
            yield command_id, self.key(command_id)

    def remapped(self) -> Dict[str, str]:
        """{default key: key in effect, "" for none} for every command whose
        default key you changed, for the shortcuts list (F1)."""
        return {self.default(c): self.key(c) for c in self._defaults
                if self.default(c) and self.changed(c)}

    def added(self):
        """(key, command's name) for every command that had no key and you
        gave one."""
        return [(self.key(c), command_list.plain_label(command_list.command(c)))
                for c in self._defaults if not self.default(c) and self.key(c)]

    def command_for(self, key: str) -> Optional[str]:
        """The command ``key`` runs now, or None."""
        try:
            key = normalize(key)
        except ValueError:
            return None
        for command_id, current in self.items():
            if current == key:
                return command_id
        return None

    # -- changing ------------------------------------------------------------

    def set(self, command_id: str, key: str) -> Optional[str]:
        """Give ``command_id`` the key ``key`` ("" removes its key). A
        command that had the key loses it, and its id is returned. Raises
        ValueError, with the reason to say, for a key that can't be given."""
        if command_id not in self._defaults:
            raise ValueError(f"{command_id} has no key you can change.")
        key = normalize(key) if key else ""
        # A command's own default can always go back to it, even where it's
        # not an accelerator (Retry's Cmd+Shift+Z on a Mac).
        why = refusal(key) if key and key != self.default(command_id) else ""
        if why:
            raise ValueError(why)
        taken = self.command_for(key) if key else None
        if taken == command_id:
            taken = None
        if taken:
            self._change(taken, "")
        self._change(command_id, key)
        return taken

    def reset(self, command_id: str) -> Optional[str]:
        """Back to the command's default key. A command that has that key
        now loses it, and its id is returned."""
        default = self.default(command_id)
        taken = self.command_for(default) if default else None
        if taken == command_id:
            taken = None
        if taken:
            self._change(taken, "")
        self._changes.pop(command_id, None)
        return taken

    def reset_all(self) -> None:
        self._changes = {}

    def _change(self, command_id: str, key: str) -> None:
        if key == self.default(command_id):
            self._changes.pop(command_id, None)
        else:
            self._changes[command_id] = key

    def save(self) -> bool:
        """Write ``keys.json``; False (with ``load_error`` saying why) if it
        couldn't be written."""
        if self._save_blocked:
            return False
        keys = dict(self._unknown)
        keys.update(self._changes)
        try:
            atomic_write_json(self.path, {"version": 1, "keys": keys})
        except OSError as exc:
            self.load_error = f"Couldn't save your keyboard shortcuts ({self.path}): {exc}."
            return False
        return True
