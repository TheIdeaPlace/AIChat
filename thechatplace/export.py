"""Saving a session's conversation to a file (#33): Markdown, a web page,
or plain text.

Pure text, separate from the window, so it can be tested. Each message is a
heading ("You, 10:42" / "Claude, 10:43") and its text as written. A
message's own headings are moved down two levels, so the export's outline
stays session title, then message, then the message's sections, which is
what a screen reader moves through by heading. The web page uses the same
inert page as the formatted message view (nothing remote, no scripts).
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable, List, Optional

from .rendering import html_page, markdown_to_html

MARKDOWN, HTML, TEXT = "md", "html", "txt"
#: (format, file dialog wildcard part), in the order the dialog offers them.
FORMATS = [
    (MARKDOWN, "Markdown (*.md)|*.md"),
    (HTML, "Web page (*.html)|*.html"),
    (TEXT, "Plain text (*.txt)|*.txt"),
]

# A fence opens with 3+ backticks or tildes (up to 3 spaces in); it closes on a
# line of the same character, at least as long, with nothing after it.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^( {0,3})(#{1,6})(\s|$)")
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def message_moment(timestamp: str) -> Optional[datetime]:
    """Local time from a transcript's ISO timestamp, or None."""
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone() if moment.tzinfo is not None else moment


def message_time(timestamp: str) -> str:
    """"10:42" (local time), or ""."""
    moment = message_moment(timestamp)
    return moment.strftime("%H:%M") if moment else ""


def shift_headings(text: str, by: int = 2) -> str:
    """Markdown headings one level deeper per ``by`` (at most level 6),
    leaving code blocks alone."""
    lines = []
    fence = ""  # the open fence's marker, or "" outside code
    for line in (text or "").split("\n"):
        match = _FENCE.match(line)
        if fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                    and not match.group(2).strip():
                fence = ""
        elif match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
            fence = match.group(1)
        else:
            heading = _HEADING.match(line)
            if heading:
                level = min(len(heading.group(2)) + by, 6)
                line = heading.group(1) + "#" * level + line[len(heading.group(1))
                                                             + len(heading.group(2)):]
        lines.append(line)
    return "\n".join(lines)


def default_filename(title: str, when: Optional[datetime] = None, extension: str = MARKDOWN) -> str:
    """"Fix the build 2026-10-07.md": the title made safe for a file name."""
    name = _UNSAFE_FILENAME.sub(" ", title or "Session")
    name = " ".join(name.split()).strip(" .")[:80].strip(" .") or "Session"
    return f"{name} {(when or datetime.now()).strftime('%Y-%m-%d')}.{extension}"


def _headings(messages) -> List[str]:
    """"You, 10:42" for each message; the date too ("Claude, 7 October, 09:15")
    on the first message and whenever the day changes."""
    headings, last_day = [], None
    for message in messages:
        moment = message_moment(getattr(message, "timestamp", ""))
        if moment is None:
            headings.append(message.label)
            continue
        day = moment.date()
        when = moment.strftime("%H:%M") if day == last_day else \
            f"{moment.day} {moment.strftime('%B %Y')}, {moment.strftime('%H:%M')}"
        last_day = day
        headings.append(f"{message.label}, {when}")
    return headings


def _intro(title: str, folder: str, when: datetime) -> List[str]:
    lines = [f"Exported from The Chat Place on {when.strftime('%Y-%m-%d at %H:%M')}."]
    if folder:
        lines.append(f"Folder: {folder}")
    return lines


def to_markdown(title: str, messages: Iterable, folder: str = "",
                when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    messages = list(messages)
    parts = [f"# {title}", "", *[line + "  " for line in _intro(title, folder, when)], ""]
    for message, heading in zip(messages, _headings(messages)):
        parts += [f"## {heading}", "", shift_headings(message.text).rstrip(), ""]
    return "\n".join(parts).rstrip() + "\n"


def to_text(title: str, messages: Iterable, folder: str = "",
            when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    messages = list(messages)
    parts = [title, "=" * min(len(title), 70), *_intro(title, folder, when), ""]
    for message, heading in zip(messages, _headings(messages)):
        parts += [heading, "-" * min(len(heading), 70), (message.text or "").rstrip(), ""]
    return "\n".join(parts).rstrip() + "\n"


def to_html(title: str, messages: Iterable, folder: str = "",
            when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    body = [f"<h1>{html.escape(title)}</h1>"]
    body += [f"<p>{html.escape(line)}</p>" for line in _intro(title, folder, when)]
    messages = list(messages)
    for number, (message, heading) in enumerate(zip(messages, _headings(messages)), start=1):
        body.append(f'<section aria-labelledby="m{number}">'
                    f'<h2 id="m{number}">{html.escape(heading)}</h2>'
                    f"{markdown_to_html(shift_headings(message.text))}</section>")
    # A saved file is opened in a browser, like the published pages (#341).
    return html_page(title, "\n".join(body), landmark=True)


def render(fmt: str, title: str, messages: Iterable, folder: str = "",
           when: Optional[datetime] = None) -> str:
    messages = list(messages)
    if fmt == HTML:
        return to_html(title, messages, folder, when)
    if fmt == TEXT:
        return to_text(title, messages, folder, when)
    return to_markdown(title, messages, folder, when)
