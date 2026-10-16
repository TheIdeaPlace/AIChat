"""What gets said when a session replies or finishes a turn.

Pure text decisions, separate from speaking them, so they can be tested.
The level comes from the speech settings: full, summary or silent.
"""
from __future__ import annotations

import re
from typing import Optional

from .speech import (ANNOUNCE_FULL, ANNOUNCE_SILENT, ANNOUNCE_SUMMARY, CODE_NOTE,
                     strip_for_speech, without_code_blocks)
from .sessions import NEEDS_YOU, STATUS_UNAVAILABLE, state_with_detail


#: Words a full stop follows without ending the sentence ("e.g. the docs").
_ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf", "dr", "mr", "mrs", "ms", "st", "jr",
                  "sr", "no", "fig", "approx", "dept", "inc", "ltd", "co"}
_SENTENCE_END = re.compile(r"[.?!](?= )")


def _ends_sentence(text: str, index: int) -> bool:
    """Whether the mark at ``index`` (followed by a space) ends a sentence."""
    following = text[index + 2:index + 3]
    if following.islower():
        return False
    if text[index] != ".":
        return True
    word = text[:index].rsplit(" ", 1)[-1].lower()
    # A lone letter is an initial ("J. Smith") or the end of "e.g.".
    return len(word) > 1 and word not in _ABBREVIATIONS


def _cut_at_word(text: str, limit: int) -> str:
    """At most ``limit`` characters, on a word boundary unless one word
    (a URL, say) fills it, without trailing punctuation."""
    head = text[:limit]
    cut = (head.rsplit(" ", 1)[0] if " " in head else head).rstrip(" ,;:.")
    return cut or head


def first_sentence(text: str, limit: int = 200) -> str:
    stripped = " ".join((text or "").split())
    for match in _SENTENCE_END.finditer(stripped, 0, limit):
        if match.start() > 0 and _ends_sentence(stripped, match.start()):
            return stripped[: match.start() + 1]
    if len(stripped) <= limit:
        return stripped
    return _cut_at_word(stripped, limit) + "…"


def reply_text(title: str, reply: str, level: str) -> Optional[str]:
    """A session's reply. None means say nothing."""
    if level == ANNOUNCE_SILENT or not (reply or "").strip():
        return None
    if level == ANNOUNCE_SUMMARY:
        words = len(reply.split())
        return f"{title} replied: {first_sentence(reply)} {words} words."
    return f"{title} replied. {reply.strip()}"


def peer_text(title: str, sender: str, message: str, level: str) -> Optional[str]:
    """Another Claude session sent the open session a message (#123): a reply
    from a session on another computer, say. None means say nothing."""
    if level == ANNOUNCE_SILENT or not (message or "").strip():
        return None
    who = sender or "another session"
    if level == ANNOUNCE_SUMMARY:
        return f"{title}: message from {who}: {first_sentence(message)}"
    return f"{title}: message from {who}. {message.strip()}"


def turn_end_text(title: str, state: str, detail: str, reply: str, level: str) -> Optional[str]:
    """A session finished a turn (used for sessions The Chat Place only watches)."""
    if level == ANNOUNCE_SILENT or state == STATUS_UNAVAILABLE:
        return None
    head = f"{title} finished"
    if state == NEEDS_YOU:
        head = state_with_detail(f"{title} needs you", detail)
    head += "."
    if not (reply or "").strip():
        return head
    if level == ANNOUNCE_SUMMARY:
        return f"{head} {first_sentence(reply)}"
    return f"{head} {reply.strip()}"


#: The most of Kelly's own message read back at the full level. It's an echo
#: of what he just typed, not news: a pasted log shouldn't talk for minutes.
OWN_LIMIT = 300

# Up to two digits: "1. first" is a list item, "2026. That year" is not.
# A task-list box goes with the marker; a marker alone on a line counts too.
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d{1,2}[.)])(?:\s+\[[ xX]\])?(?:\s+|$)")
_HEADING_LINE = re.compile(r"^\s*#{1,6}\s")
_ONLY_MARKS = re.compile(r"[\s\-*_.,;:]*")


def _end_sentence(text: str) -> str:
    text = text.rstrip(" ,;:")
    return text if text[-1:] in (".", "?", "!", "…") else text + "."


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def _own_words(message: str, level: str, read_back: bool) -> Optional[str]:
    """The part of Kelly's own message to read back. None means don't.

    Lines are sorted out before markdown is stripped, while a heading still
    has its ``#``: a heading, a list item, a code block or a blank line
    ends a sentence; other line breaks are wrapped prose and read on. Then
    the summary level takes the first sentence and the full level caps the
    length, counting what's left in words and code blocks.
    """
    if not read_back or level == ANNOUNCE_SILENT:
        return None
    flat = spoken_markdown(message)
    if not flat:
        return None
    if level == ANNOUNCE_SUMMARY:
        return _end_sentence(first_sentence(flat))
    # The limit counts the words, not the full stop added after them.
    if len(flat.rstrip(".")) <= OWN_LIMIT:
        return flat
    cut = _cut_at_word(flat, OWN_LIMIT)
    rest = flat[len(cut):]
    blocks = rest.count(CODE_NOTE)
    words = len(rest.replace(CODE_NOTE, " ").split())
    if " " not in flat[:OWN_LIMIT]:
        words = max(words - 1, 0)  # the long word was cut, not left out
    more = [_plural(words, "more word")] if words else []
    if blocks:
        more.append("a code block" if blocks == 1 else _plural(blocks, "code block"))
    return f"{cut}… and {' and '.join(more)}." if more else f"{cut}…"


def spoken_markdown(message: str, describe_code: bool = False) -> str:
    """A message's markdown as words to be spoken, whole: headings, list
    items, code blocks and blank lines end sentences, wrapped lines read on,
    markdown marks are dropped, and a code block is "Code block omitted", or
    with ``describe_code`` says what it is ("Code block, Python, 14 lines.",
    #17). "" if nothing is left. Used for read-back and for reading a whole
    message in the messages list (#11)."""
    flat = _spoken_markdown(message)
    if describe_code and CODE_NOTE in flat:
        from .codeblocks import find_code_blocks
        blocks = find_code_blocks(message)
        # Only when both ways of finding blocks agree; otherwise the plain note.
        if len(blocks) == flat.count(CODE_NOTE):
            for block in blocks:
                flat = flat.replace(CODE_NOTE, block.describe() + "\uE000", 1)
            flat = flat.replace("\uE000", ".")
    return flat


def _spoken_markdown(message: str) -> str:
    text = without_code_blocks(message or "", f"\n{CODE_NOTE}\n")
    lines = []  # (spoken text, whether a sentence must end after it)
    quoting = False
    for raw in text.splitlines():
        structural = bool(_HEADING_LINE.match(raw) or _LIST_MARKER.match(raw)
                          or raw.strip() == CODE_NOTE)
        quote = raw.lstrip().startswith(">")
        spoken = " ".join(strip_for_speech(_LIST_MARKER.sub("", raw)).split())
        if _ONLY_MARKS.fullmatch(spoken):
            if lines:  # a blank or marker-only line ends the line before it
                lines[-1] = (lines[-1][0], True)
            continue
        # Moving into or out of a quote ends a sentence too.
        if lines and (structural or quote != quoting):
            lines[-1] = (lines[-1][0], True)
        quoting = quote
        lines.append((spoken, structural))
    if not lines:
        return ""
    parts = [_end_sentence(spoken) if ends or i == len(lines) - 1 else spoken
             for i, (spoken, ends) in enumerate(lines)]
    # Once more over the joined text: emphasis can span a line break.
    return " ".join(strip_for_speech(" ".join(parts)).split())


def sent_text(title: str, message: str, level: str, read_back: bool,
              queued: bool = False) -> str:
    """Confirmation that Kelly's message went to Claude. Which session comes
    first, so it's heard even if the read-back is cut short. A queued
    message was read back when it was queued, so it isn't read again."""
    if queued:
        return f"Sent your queued message. {title} is working."
    words = _own_words(message, level, read_back)
    if words is None:
        return f"Sent. {title} is working."
    return f"Sent to {title}: {words}"


def queued_text(title: str, message: str, level: str, read_back: bool,
                added: bool = False) -> str:
    """Confirmation that Kelly's message waits for the running turn."""
    words = _own_words(message, level, read_back)
    if words is None:
        head = "Also queued" if added else "Queued"
        return f"{head}. It will be sent when {title} finishes."
    head = "Also queued for" if added else "Queued for"
    return f"{head} {title}: {words}"


def background_text(tasks) -> str:
    """What a session left running when it answered (#161): the turn stays
    open for it, and Claude carries on when it finishes."""
    names = [" ".join(str(t).split()) for t in tasks if str(t).strip()]
    if not names:
        return "Still running in the background."
    return "Still running in the background: " + "; ".join(names) + "."


def status_text(text: str, limit: int = 150) -> str:
    """The same news, short enough for the status bar."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


__all__ = ["first_sentence", "reply_text", "turn_end_text", "status_text",
           "sent_text", "queued_text", "activity_text", "background_text",
           "ANNOUNCE_FULL", "ANNOUNCE_SUMMARY", "ANNOUNCE_SILENT"]


#: Tool calls in a row, each this many characters at most when read one by one.
ACTIVITY_DETAIL_LIMIT = 120
#: How many tool calls in a row are read one by one before they're counted.
ACTIVITY_LIST_MAX = 3


def activity_text(items, level: str) -> Optional[str]:
    """What to say for a burst of activity in the open session (#12), with
    Show Tool Activity on. ``items`` are ("tool", "Bash: git status") and
    ("text", "Let me check the build.") pairs, oldest first; text is what
    Claude wrote between tool calls.

    Full: up to three tool calls in a row are read with what they act on
    ("Using Bash: git status; Read: main.py."); more are counted ("Using Read
    4 times, then Bash."), so speech never falls far behind. Text is read
    as written, up to about a sentence or two. Summary: tools are always
    counted, text is its first sentence. Silent: nothing (the status bar
    still shows it). None if there's nothing to say.
    """
    if level == ANNOUNCE_SILENT or not items:
        return None
    parts = []
    run = []

    def end_run():
        if not run:
            return
        if level == ANNOUNCE_FULL and len(run) <= ACTIVITY_LIST_MAX:
            def short(detail):
                name, sep, rest = detail.partition(":")
                detail = " ".join((tool_name_spoken(name) + sep + rest).split())
                return detail if len(detail) <= ACTIVITY_DETAIL_LIMIT \
                    else _cut_at_word(detail, ACTIVITY_DETAIL_LIMIT) + "…"
            listed = "; ".join(short(d) for d in run)
            parts.append(_end_sentence(f"Using {listed}"))
        else:
            names = []  # (name, count), consecutive repeats merged
            for detail in run:
                name = tool_name_spoken(detail.split(":", 1)[0])
                if names and names[-1][0] == name:
                    names[-1] = (name, names[-1][1] + 1)
                else:
                    names.append((name, 1))
            if len(names) > ACTIVITY_LIST_MAX:
                # Back and forth (Read, Bash, Read, Bash): count each tool once.
                totals = {}
                for name, count in names:
                    totals[name] = totals.get(name, 0) + count
                said = [_times(n, c) for n, c in totals.items()]
                parts.append(_end_sentence("Using " + _and_list(said)))
            else:
                said = [_times(n, c) for n, c in names]
                parts.append(_end_sentence("Using " + ", then ".join(said)))
        run.clear()

    for kind, text in items:
        if kind == "tool":
            run.append(text or "a tool")
            continue
        end_run()
        words = " ".join(strip_for_speech(without_code_blocks(text or "", f" {CODE_NOTE} "))
                         .split())
        if not words:
            continue
        if level == ANNOUNCE_SUMMARY:
            parts.append(_end_sentence(first_sentence(words)))
        elif len(words) <= OWN_LIMIT:
            parts.append(_end_sentence(words))
        else:
            parts.append(_cut_at_word(words, OWN_LIMIT) + "…")
    end_run()
    return " ".join(parts) or None


def tool_name_spoken(name: str) -> str:
    """A tool's name as it reads aloud: an MCP tool's "mcp__github__create_issue"
    is "github create issue"."""
    name = (name or "").strip() or "a tool"
    if name.startswith("mcp__"):
        name = " ".join(part.replace("_", " ") for part in name[5:].split("__") if part)
    return name or "a tool"


def _times(name: str, count: int) -> str:
    if count == 1:
        return name
    return f"{name} twice" if count == 2 else f"{name} {count} times"


def _and_list(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
