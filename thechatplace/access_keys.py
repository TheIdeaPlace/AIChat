"""Which control a dialog's access key belongs to, and what pressing it does,
without wx, so the rules can be tested anywhere (#675).

On Windows the dialog manager does this for Alt+letter. A Mac has no access
keys of its own, so ``ui/mac_keys.py`` does it for Option+letter, the key a
Mac uses where Windows uses Alt (the formatted page's Option+P, #119, was
the first). It describes a dialog's controls as ``Stop``s, in tab order, and
asks ``find`` which one a letter reaches.

The rules follow Windows':
- a button is pressed, and focus stays where it was;
- a check box or radio button gets the focus and is checked or chosen;
- a label (a static text, or a group's box) sends the focus to the next
  control after it in tab order that can take it, skipping hidden and
  disabled ones;
- a group of radio buttons drawn as one control (``wx.RadioBox``) gets the
  focus itself;
- where two controls share a letter, the first in tab order has it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

#: What a control does with its access key.
BUTTON = "button"  # pressed
CHECK = "check"  # focused and checked or unchecked
RADIO = "radio"  # focused and chosen
LABEL = "label"  # the focus goes on, to the control after it
GROUP = "group"  # focused itself
OTHER = "other"  # has no access key of its own; may take the focus from a label


def access_key(label: str) -> str:
    """The character after the first single "&" in a wx label, lower case,
    or "" for none. "&&" is an ampersand written out, never an access key,
    and neither is an "&" at the end or before a space."""
    index = 0
    while index < len(label) - 1:
        if label[index] == "&":
            following = label[index + 1]
            if following == "&":
                index += 2
                continue
            return "" if following.isspace() else following.lower()
        index += 1
    return ""


@dataclass(frozen=True)
class Stop:
    """One control in a dialog, in tab order.

    ``usable``: shown and enabled, it and everything it's inside. ``focusable``:
    it can take the focus (a label can't; nor can a panel whose controls can)."""
    kind: str
    key: str = ""
    usable: bool = True
    focusable: bool = False


@dataclass(frozen=True)
class Target:
    """What a letter reaches: ``owner`` is the index of the control whose access
    key it is, ``focus`` the index of the control to focus, or None (a button
    keeps the focus where it is), and ``kind`` the owner's kind."""
    owner: int
    focus: Optional[int]
    kind: str


def find(stops: Sequence[Stop], letter: str) -> Optional[Target]:
    """What ``letter`` (an access key, any case) reaches among ``stops``, or None.

    Only usable controls count. A label with nothing after it that can take
    the focus reaches nothing: Windows does nothing then, and so does this."""
    letter = (letter or "").lower()
    if not letter:
        return None
    for index, stop in enumerate(stops):
        if stop.key != letter or not stop.usable:
            continue
        if stop.kind == BUTTON:
            return Target(index, None, BUTTON)
        if stop.kind in (CHECK, RADIO, GROUP):
            return Target(index, index, stop.kind)
        if stop.kind == LABEL:
            for later in range(index + 1, len(stops)):
                if stops[later].usable and stops[later].focusable:
                    return Target(index, later, LABEL)
            return None
    return None


def duplicates(stops: Sequence[Stop]) -> set:
    """The access keys more than one usable control has. A dialog shouldn't
    have any: only the first in tab order is reachable."""
    seen, twice = set(), set()
    for stop in stops:
        if stop.key and stop.usable and stop.kind != OTHER:
            (twice if stop.key in seen else seen).add(stop.key)
    return twice


def option_letter(modifiers_are_option_alone: bool, key_code: int, unicode_key: int) -> str:
    """The letter an Option+letter press names, lower case, or "".

    ``key_code`` is wx's (upper case for a letter); ``unicode_key`` the
    character, used where the key code isn't plain ASCII (a keyboard of
    another alphabet). Only Option alone counts: with Command, Control or
    Shift too, it's some other key."""
    if not modifiers_are_option_alone:
        return ""
    if 32 < key_code < 127:
        char = chr(key_code)
    elif unicode_key and unicode_key > 32:
        char = chr(unicode_key)
    else:
        return ""
    return char.lower() if char.isalnum() else ""
