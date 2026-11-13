"""The command palette's list (#637): which commands match what's typed, in
what order, and how each row reads. No wx, so it's tested on its own.

Every menu-bar command is in it except the palette itself. Typed words may
come in any order; each must start a word of the command's name, its menu
or its key ("perm mode" finds Change Permission Mode), or failing that be
inside one. A command whose name starts with the first word comes first,
then the others whose words all start a word, then the rest, each in menu
order. With nothing typed, the ones you used this session come first,
newest first, then all the rest in menu order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Sequence

from . import commands as command_list

#: The palette doesn't list itself.
SELF = "command_palette"


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    #: Where it is: "Chat menu", "Chat, Chat Settings".
    place: str


def entries() -> List[Entry]:
    """Every command the palette offers, in menu order."""
    found = []
    for command in command_list.commands():
        if command.id == SELF:
            continue
        trail = command_list.menu_path(command.id).split(", ")[:-1]
        place = f"{trail[0]} menu" if len(trail) == 1 else ", ".join(trail)
        found.append(Entry(command.id, command_list.plain_label(command), place))
    return found


def row(entry: Entry, key: str = "", available: bool = True) -> str:
    """How a row reads: "Change Model, Chat, Chat Settings", "Find, Ctrl+F,
    View menu", with ", unavailable" for one that can't run now. ``key`` is
    as said, "" for none."""
    parts = [entry.name] + ([key] if key else []) + [entry.place]
    if not available:
        parts.append("unavailable")
    return ", ".join(parts)


def _words(text: str) -> List[str]:
    return re.findall(r"[\w+./,-]+", text.lower())


def matches(query: str, items: Sequence[Entry], key_of: Callable[[str], str] = lambda i: "",
            recent: Iterable[str] = ()) -> List[Entry]:
    """``items`` that match ``query``, best first (see the module's note)."""
    wanted = _words(query)
    if not wanted:
        order = {command_id: n for n, command_id in enumerate(recent)}
        return sorted(items, key=lambda e: (order.get(e.id, len(order)), items.index(e)))
    ranked = []
    for index, entry in enumerate(items):
        name_words = _words(entry.name)
        words = name_words + _words(entry.place) + _words(key_of(entry.id))
        text = " ".join(words)
        if not all(w in text for w in wanted):
            continue
        starts = all(any(word.startswith(w) for word in words) for w in wanted)
        first = bool(name_words) and name_words[0].startswith(wanted[0])
        ranked.append(((0 if first and starts else 1 if starts else 2), index, entry))
    return [entry for _, _, entry in sorted(ranked, key=lambda r: (r[0], r[1]))]
