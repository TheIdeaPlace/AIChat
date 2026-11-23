"""Console output pasted into a message, made a code block (#701).

Text copied from a console window arrives unmarked, every line padded with
spaces to the window's width, so it read as one long paragraph, unlike the
rest of the chat, whichever AI wrote it. Pasted into a message box, it goes
in trimmed and fenced as a "console" code block instead, in view before it's
sent, so it can be changed or taken out of the block.

Only clear console output counts: a shell prompt, PowerShell's or Python's
error lines, or several lines padded with spaces. Text already holding a
fence, or a single line, is left as it is.
"""
from __future__ import annotations

import re

#: The language a pasted block is given: "Code block, Console, 12 lines".
LANGUAGE = "console"

_PROMPT = re.compile(
    r"^(?:PS [^\n>]{1,260}>"            # PowerShell: PS C:\dev>
    r"|[A-Za-z]:\\[^\n>]{0,260}>"       # Command Prompt: C:\dev>
    r"|[\w.-]+@[\w.-]+(?::[^\n$#]*)?[$#] "  # bash: me@host:~/dev$
    r"|[\w.-]+@[\w.-]+ [^\n%]*% )")      # zsh on a Mac: me@host dev %
_ERROR = re.compile(
    r"^(?:\s*\+ ~{3,}\s*$"              # PowerShell's underline
    r"|\s*\+ (?:CategoryInfo|FullyQualifiedErrorId)\s*:"
    r"|At line:\d+ char:\d+"
    r"|Traceback \(most recent call last\):)")
_FENCE = re.compile(r"^[ \t>]*(?:`{3,}|~{3,})")
#: A console pads each line to the window's width: this many spaces at least.
_PADDING = re.compile(r"\S {20,}$")


def is_console(text: str) -> bool:
    """Whether pasted ``text`` is console output to put in a code block."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    filled = [line for line in lines if line.strip()]
    if len(filled) < 2 or any(_FENCE.match(line) for line in lines):
        return False
    if any(_PROMPT.match(line) or _ERROR.match(line) for line in filled):
        return True
    padded = sum(1 for line in filled if _PADDING.search(line))
    return padded >= 3 and padded * 2 >= len(filled)


def as_code_block(text: str) -> str:
    """``text`` with each line's trailing spaces and the blank lines around
    it gone, fenced as a console block. The fence is longer than any run of
    backticks inside, so nothing in it can close the block early."""
    lines = [line.rstrip() for line in
             (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    longest = max((len(run) for run in re.findall(r"`+", "\n".join(lines))), default=0)
    fence = "`" * max(3, longest + 1)
    return "\n".join([fence + LANGUAGE, *lines, fence])


def line_count(block: str) -> int:
    """The lines of code in a block made by as_code_block."""
    return max(0, block.count("\n") - 1)
