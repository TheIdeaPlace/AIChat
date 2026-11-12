"""The links in a session's messages (#190), for Tools, Links (Ctrl+L): a list
to open one in the browser, or copy it, without arrowing through the
conversation to find it.

A link is a Markdown link (``[words](address)``, an image's too), an address
in angle brackets, or a bare web address, in what you and Claude wrote. Links
in code (a fenced block or `inline code`) are listed too, marked as being in
code, since Claude often gives an address that way. Each address is listed
once, at its newest mention, newest first.

Only http, https and mailto links open, in the default browser, as the
formatted view's do (``rendering``), and links to sessions
(``thechatplace://session/<id>``, #144), which The Chat Place opens itself.
Anything else (a ``file:`` path, ``javascript:``) is listed so it can be
copied, and says why it won't open.

Nothing here imports wx, so it's all tested without a window.
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .codeblocks import CodeBlock, split_code_blocks
from .links import is_app_link, parse_link
from .rendering import opens_in_browser

#: ``[words](address "title")`` and ``![alt](address)``. The address may be
#: in angle brackets, and may hold one level of parentheses (Wikipedia's).
#: The words can't hold "[" either, so a line of brackets is read in one pass.
_MARKDOWN = re.compile(r"!?\[([^\[\]\n]*)\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))+)>?"
                       r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
#: ``<https://example.com>`` and ``<mailto:me@example.com>``.
_ANGLE = re.compile(r"<((?:https?://|mailto:)[^<>\s]+)>", re.IGNORECASE)
#: A bare address: starts at a word boundary, runs to whitespace or a mark
#: that can't be in one. Trailing punctuation is trimmed afterwards.
_BARE = re.compile(r"(?<![\w/:@.-])((?:https?://|mailto:)[^\s<>\[\]\"'`]+)", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_TRAILING = ".,;:!?*_~'\""
#: A link's scheme: two letters or more, so "C:\path" is a path, not one.
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]+):")
#: What opens: a web address with a host, or a mail address. Stricter than
#: the scheme alone, so "https:" with nothing after it, or "https:C:\x", is
#: only copied.
_WEB = re.compile(r"(?i)^https?://[^/\\\s?#]")
_MAIL = re.compile(r"(?i)^mailto:[^\s]")
#: Longer than this, an address is read shortened (it's copied and opened whole).
READ_LIMIT = 70


def _plain(url: str) -> bool:
    """No control or format characters (a NUL, a right-to-left override):
    those never open, and would make the row read backwards."""
    return not any(unicodedata.category(c) in ("Cc", "Cf") for c in url)


@dataclass
class FoundLink:
    url: str
    #: The link's own words, "" for a bare address (or words that are just
    #: the address again).
    words: str
    #: Who wrote it, as the messages list says: "Claude", "You", "Tool"...
    who: str
    #: How many messages before the newest one it's in: 0 is the newest.
    ago: int
    in_code: bool = False

    @property
    def is_session_link(self) -> bool:
        return parse_link(self.url) is not None

    @property
    def can_open(self) -> bool:
        if not _plain(self.url):
            return False
        return (bool(_WEB.match(self.url) or _MAIL.match(self.url))
                and opens_in_browser(self.url)) or self.is_session_link

    def why_not(self) -> str:
        """Why it won't open, "" if it will."""
        if self.can_open:
            return ""
        if is_app_link(self.url):
            return "That link isn't one The Chat Place knows. It can only be copied."
        match = _SCHEME.match(self.url)
        scheme = match.group(1).lower() if match else ""
        if scheme in ("http", "https", "mailto"):
            return "That address isn't complete, so it can only be copied."
        kind = f"A {scheme}: link" if scheme else "This link"
        return (f"{kind} isn't opened from here, only web and email links are. "
                "It can only be copied.")

    @property
    def address(self) -> str:
        """The address as it's read: without "https://", a mail link as its
        address, and a long one shortened to its site and its end."""
        return _shorten(_readable(self.url))

    def when(self) -> str:
        if self.ago == 0:
            return "the latest message"
        return "1 message ago" if self.ago == 1 else f"{self.ago} messages ago"

    def row(self) -> str:
        """"release notes, github.com/…/v0.1.5. Claude, 3 messages ago" for
        the list: the words, where it goes, who wrote it and roughly when."""
        said = f"{self.words}, {self.address}" if self.words else self.address
        notes = [f"{self.who}, {self.when()}"]
        if self.in_code:
            notes.append("in code")
        if not self.can_open:
            notes.append("copy only")
        return f"{said}. " + ", ".join(notes)

    def markdown(self) -> str:
        """The link as Markdown, to paste: ``[words](url)`` or ``<url>``."""
        if self.words:
            words = self.words.replace("[", "\\[").replace("]", "\\]")
            return f"[{words}]({self.url})"
        return f"<{self.url}>"


def _readable(url: str) -> str:
    lowered = url.lower()
    for prefix in ("https://", "http://"):
        if lowered.startswith(prefix):
            url = url[len(prefix):]
            break
    else:
        if lowered.startswith("mailto:"):
            return url[len("mailto:"):]
    return url[4:] if url.lower().startswith("www.") else url


def _shorten(address: str) -> str:
    """"github.com/…/releases/tag/v0.1.5" for a long address: its site, then
    "…", then as much of its end as fits, from a "/"."""
    if len(address) <= READ_LIMIT:
        return address
    site, _, path = address.partition("/")
    room = max(READ_LIMIT - len(site) - 3, 20)
    tail = path[-room:]
    cut = tail.find("/")
    if 0 <= cut < len(tail) - 1:
        tail = tail[cut + 1:]
    return f"{site}/…/{tail}"


def _trim(url: str) -> str:
    """A bare address without the punctuation after it: "(see https://x.y/a)."
    is https://x.y/a, but https://en.wikipedia.org/wiki/Foo_(bar) keeps its
    closing parenthesis."""
    opened, closed = url.count("("), url.count(")")
    end = len(url)
    while end:
        last = url[end - 1]
        if last in _TRAILING:
            end -= 1
        elif last == ")" and closed > opened:
            end -= 1
            closed -= 1
        else:
            break
    return url[:end]


_EMPHASIS_MARKS = re.compile(r"(\*\*|__|(?<!\w)[*_]|[*_](?!\w))")


def _words(words: str, url: str) -> str:
    words = " ".join(_EMPHASIS_MARKS.sub("", words.replace("`", "")).split())
    if not words or words == url or _readable(words) == _readable(url):
        return ""
    return words


def _blank(chars: List[str], start: int, end: int) -> None:
    chars[start:end] = " " * (end - start)


def links_in_text(text: str) -> List[Tuple[str, str, bool]]:
    """(url, words, in code) for each link in ``text``, in order. A Markdown
    link without a scheme (``[main.py](src/main.py)``, ``#heading``) is a
    reference to a file or a place in the page, not a link to follow, and
    isn't listed."""
    found: List[Tuple[int, str, str, bool]] = []
    offset = 0
    for part in split_code_blocks(text):
        if isinstance(part, CodeBlock):
            # In code, Markdown isn't Markdown: only bare addresses.
            for match in _BARE.finditer(part.code):
                url = _trim(match.group(1))
                if url:
                    found.append((offset + match.start(), url, "", True))
            offset += len(part.code) + 1
            continue
        line = part
        starts: List[int] = []
        ends: List[int] = []
        for match in _INLINE_CODE.finditer(line):
            starts.append(match.start())
            ends.append(match.end())

        def in_code(at: int) -> bool:
            index = bisect.bisect_right(starts, at) - 1
            return index >= 0 and at < ends[index]
        taken = list(line)
        for match in _MARKDOWN.finditer(line):
            url = match.group(2)
            if in_code(match.start()):
                continue  # `[x](y)` is shown as code; its address is found below
            _blank(taken, match.start(), match.end())
            if _SCHEME.match(url):
                found.append((offset + match.start(), url, _words(match.group(1), url), False))
        rest = "".join(taken)
        for match in _ANGLE.finditer(rest):
            found.append((offset + match.start(), match.group(1), "", in_code(match.start())))
            _blank(taken, match.start(), match.end())
        rest = "".join(taken)
        for match in _BARE.finditer(rest):
            url = _trim(match.group(1))
            if url:
                found.append((offset + match.start(), url, "", in_code(match.start())))
        offset += len(line) + 1
    found.sort(key=lambda item: item[0])
    return [(url, words, code) for _at, url, words, code in found]


def _key(url: str) -> str:
    """The same address written two ways is listed once: the scheme and the
    site don't care about case, and a trailing "/" makes no difference."""
    match = re.match(r"(?i)^(https?://)([^/?#]*)(.*)$", url)
    if not match:
        return url
    return (match.group(1) + match.group(2)).lower() + match.group(3).rstrip("/")


def find_links(messages: Iterable) -> List[FoundLink]:
    """Every link in ``messages`` (oldest first, as the messages list has
    them; each has ``label`` and ``text``): the newest message's first, in
    the order they're written within a message, each address once, at its
    newest mention, with the words it had there (or, if it had none there,
    the newest words it had anywhere)."""
    messages = list(messages)
    newest = len(messages) - 1
    seen: dict = {}
    order: List[str] = []
    for index in range(newest, -1, -1):
        message = messages[index]
        for url, words, code in links_in_text(message.text or ""):
            key = _key(url)
            link: Optional[FoundLink] = seen.get(key)
            if link is None:
                seen[key] = FoundLink(url, words, message.label, newest - index, code)
                order.append(key)
            elif not link.words and words:
                link.words = words
    return [seen[key] for key in order]


def matches(link: FoundLink, typed: str) -> bool:
    """Whether a link is kept by what's typed in the filter: every word, in
    its words, address or who wrote it."""
    haystack = f"{link.words} {link.url} {link.who}".lower()
    return all(word in haystack for word in typed.lower().split())
