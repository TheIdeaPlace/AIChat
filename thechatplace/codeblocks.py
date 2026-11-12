"""Finding the code blocks in a message (#17), to list, read and copy them.

A fenced block opens with three or more backticks or tildes, at any indent
or in a quote (Claude nests code in list items), or after text on a line,
and closes on a line of the same character, at least as long, with nothing
after it; an unclosed block runs to the end, as Markdown does. The word
after the opening fence is the language ("```python").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple, Union

from .rendering import describe_code_block, language_name

_FENCE = re.compile(r"^([ \t>]*)(`{3,}|~{3,})(.*)$")


@dataclass
class CodeBlock:
    language: str  # as written after the fence, "" if none
    code: str

    @property
    def lines(self) -> int:
        return len(self.code.rstrip("\n").split("\n")) if self.code.strip() else 0

    def describe(self) -> str:
        """"Code block, Python, 14 lines"."""
        return describe_code_block(self.language, self.code)

    def row(self) -> str:
        """"Python, 14 lines: def main(): …" for a list."""
        first = next((line.strip() for line in self.code.split("\n") if line.strip()), "")
        if len(first) > 80:
            first = first[:79] + "…"
        kind = language_name(self.language) or "Code"
        size = f"{self.lines} line{'s' if self.lines != 1 else ''}"
        return f"{kind}, {size}: {first}" if first else f"{kind}, {size}"


def find_code_blocks(text: str) -> List[CodeBlock]:
    return [part for part in _parts(text) if isinstance(part, CodeBlock)]


def split_code_blocks(text: str) -> List[Union[str, CodeBlock]]:
    """The text's prose lines, with each code block as one CodeBlock in its
    place (Tools, Links finds links in each differently, #190)."""
    return _parts(text)


def replace_code_blocks(text: str, replace: Callable[[CodeBlock], str]) -> str:
    """``text`` with each code block, fences and all, replaced by
    ``replace(block)``, so everything that skips or describes code agrees on
    where the blocks are."""
    return "\n".join(part if isinstance(part, str) else replace(part)
                     for part in _parts(text))


def _opener(line: str) -> Optional[Tuple[str, str, int, int]]:
    """(fence, info, quote depth, indent) if the line opens a block. Any
    indent counts, and fences in a quote: Claude nests code in list items."""
    match = _FENCE.match(line)
    if not match:
        return None
    prefix, fence, info = match.groups()
    if fence[0] in info:
        return None  # "```x```" is inline code, "~~~gone~~~" strikethrough
    quotes = prefix.count(">")
    indent = len(prefix.rsplit(">", 1)[-1].expandtabs(4))
    if quotes:
        indent = max(indent - 1, 0)  # the space after ">" belongs to the quote
    return fence, info, quotes, indent


def _closes(line: str, fence: str) -> bool:
    match = _FENCE.match(line)
    return bool(match and match.group(2)[0] == fence[0]
                and len(match.group(2)) >= len(fence) and not match.group(3).strip())


def _content(line: str, quotes: int, indent: int) -> str:
    """A code line without the quote marks and indent its fence had."""
    for _ in range(quotes):
        line = re.sub(r"^\s*>\s?", "", line, count=1)
    strip = 0
    while strip < len(line) and strip < indent and line[strip] == " ":
        strip += 1
    return line[strip:]


def _parts(text: str) -> List[Union[str, CodeBlock]]:
    """The text's lines, with each code block's lines as one CodeBlock."""
    parts: List[Union[str, CodeBlock]] = []
    fence = ""
    language = ""
    quotes = indent = 0
    lines: List[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if fence:
            if _closes(line, fence):
                parts.append(CodeBlock(language, "\n".join(lines)))
                fence, lines = "", []
            else:
                lines.append(_content(line, quotes, indent))
            continue
        opened = _opener(line)
        if opened is None and line.count("```") % 2:
            # "Run this: ```python" opens a block mid-line: the text before
            # it is prose, and the fence that closes it isn't a new opener.
            before, _, after = line.rpartition("```")
            if "`" not in after and len(after.split()) <= 1:
                if before.strip():
                    parts.append(before.rstrip())
                opened = ("```", after, 0, 0)
        if opened is None:
            parts.append(line)
            continue
        fence, info, quotes, indent = opened
        words = info.strip().split()
        language = words[0] if words else ""
    if fence:
        parts.append(CodeBlock(language, "\n".join(lines)))
    return parts
