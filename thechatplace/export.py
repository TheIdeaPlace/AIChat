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
        parts += [f"## {heading}", "", shift_headings(message.whole).rstrip(), ""]
    return "\n".join(parts).rstrip() + "\n"


def to_text(title: str, messages: Iterable, folder: str = "",
            when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    messages = list(messages)
    parts = [title, "=" * min(len(title), 70), *_intro(title, folder, when), ""]
    for message, heading in zip(messages, _headings(messages)):
        parts += [heading, "-" * min(len(heading), 70), (message.whole or "").rstrip(), ""]
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
                    f"{markdown_to_html(shift_headings(message.whole))}</section>")
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


# -- Many chats at once (#461) -----------------------------------------------------------

def chat_row(title: str, ai: str, cwd: str, last_activity_ms: int) -> str:
    """One chat in a chooser list: "Fix the build, Claude, AIChat, last
    active 30 October 2026". Restore from Backup's list uses it too."""
    folder = (cwd or "").replace("\\", "/").rstrip("/").split("/")[-1] or "no folder"
    when = ""
    try:
        if last_activity_ms and last_activity_ms > 0:
            moment = datetime.fromtimestamp(last_activity_ms / 1000).astimezone()
            when = f", last active {moment.day} {moment:%B %Y}"
    except (OSError, OverflowError, ValueError, TypeError):
        when = ""
    return f"{title}, {ai}, {folder}{when}"


def _numbered(name: str, number: int) -> str:
    stem, dot, extension = name.rpartition(".")
    return f"{stem} ({number}).{extension}" if dot else f"{name} ({number})"


class ManyResult:
    """What an export of many chats did: (title, path, message count) for
    each one written, (title, reason) for each that couldn't be, and
    whether it was stopped before the end."""

    def __init__(self):
        self.written: List[tuple] = []
        self.failed: List[tuple] = []
        self.stopped = False


def export_many(chats, folder: str, fmt: str, read, *, cancelled=lambda: False,
                progress=lambda done, total: None, when: Optional[datetime] = None) -> ManyResult:
    """Write each of ``chats`` ((title, cwd, source) tuples) to its own file
    in ``folder``: ``read(source)`` gives its messages, and a chat that
    can't be read or written is noted without stopping the rest. Names are
    Export Session's; one already taken, by a file or by another chat here,
    gets a number, and no file is ever replaced (exclusive creation)."""
    import os
    result = ManyResult()
    when = when or datetime.now()
    total = len(chats)
    for done, (title, cwd, source) in enumerate(chats):
        if cancelled():
            result.stopped = True
            break
        progress(done, total)
        try:
            messages = list(read(source) or [])
            if not messages:
                raise OSError("it has no messages")
            text = render(fmt, title, messages, folder=cwd, when=when)
            name = default_filename(title, when, fmt)
            for number in range(1, 1000):
                path = os.path.join(folder, name if number == 1 else _numbered(name, number))
                try:
                    handle = open(path, "x", encoding="utf-8", newline="\n")
                except FileExistsError:
                    continue
                try:
                    with handle:
                        handle.write(text)
                except BaseException:
                    # Nothing half written stays to take the name (#462 review).
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    raise
                break
            else:
                raise OSError("too many files with its name")
            result.written.append((title, path, len(messages)))
        except Exception as error:  # noqa: BLE001 - one chat doesn't stop the rest
            result.failed.append((title, str(error) or type(error).__name__))
    else:
        progress(total, total)
    return result


def many_summary(result: ManyResult, folder: str) -> str:
    """The message an export of many chats ends with."""
    count = len(result.written)
    parts = []
    if result.stopped:
        parts.append(f"Export stopped. {count} chat{'s' if count != 1 else ''} "
                     f"{'was' if count == 1 else 'were'} exported to {folder} before it stopped.")
    elif count:
        parts.append(f"Exported {count} chat{'s' if count != 1 else ''} to {folder}.")
    else:
        parts.append("No chats were exported.")
    if result.failed:
        shown = result.failed[:10]
        parts.append(f"{len(result.failed)} couldn't be exported:")
        parts.extend(f"- {title}: {reason}" for title, reason in shown)
        if len(result.failed) > len(shown):
            parts.append(f"and {len(result.failed) - len(shown)} more.")
    return "\n".join(parts)
