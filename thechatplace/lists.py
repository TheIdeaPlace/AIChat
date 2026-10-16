"""What View, Lists shows (#279): one dialog, a tab per kind of thing in a
chat (code blocks, links, changed files, commands, decisions), for the
selected message or the whole chat.

Each tab is a list of ``ListItem``: the line the list reads, the text to read
in full below it, and what Copy copies. Building them needs no window, so it's
all tested without one; ``ui.dialogs.ListsDialog`` shows them.

"This message" means the selected message itself for code blocks and links,
which live in a message's text, and the selected message's turn (your message
and everything up to your next one) for files, commands and decisions, which
happen around it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

from .changes import FileEdit, by_file, file_text
from .codeblocks import find_code_blocks
from .message_links import FoundLink, find_links
from .transcript import ANSWER, QUESTION, ChatMessage, ToolCommand

CODE = "code"
LINKS = "links"
FILES = "files"
COMMANDS = "commands"
DECISIONS = "decisions"

#: The tabs, in order: (id, tab title, the noun a count uses).
CATEGORIES = (
    (CODE, "Code Blocks", "code blocks"),
    (LINKS, "Links", "links"),
    (FILES, "Changed Files", "files"),
    (COMMANDS, "Commands", "commands"),
    (DECISIONS, "Decisions", "decisions"),
)
CATEGORY_IDS = tuple(c[0] for c in CATEGORIES)
TITLES = {c[0]: c[1] for c in CATEGORIES}
NOUNS = {c[0]: c[2] for c in CATEGORIES}

SCOPE_MESSAGE = "message"
SCOPE_CHAT = "chat"
#: The scope choice's wording, in order.
SCOPES = ((SCOPE_MESSAGE, "This message"), (SCOPE_CHAT, "The whole chat"))


@dataclass
class ListItem:
    #: The line the list shows and reads: what it is, then where it's from.
    row: str
    #: The full text, read by line in the box below the list.
    detail: str
    #: What Copy puts on the clipboard, and how the feedback names it.
    copy_text: str = ""
    copy_what: str = ""
    #: The detail box's name for a screen reader ("Code block, Python, 14 lines").
    detail_name: str = ""
    #: Links only: the link Open opens.
    link: Optional[FoundLink] = None

    def matches(self, typed: str) -> bool:
        """Every typed word appears in the row or the full text."""
        haystack = f"{self.row}\n{self.detail}".lower()
        return all(word in haystack for word in typed.lower().split())


def _ago(index: int, newest: int) -> str:
    ago = newest - index
    if ago <= 0:
        return "the latest message"
    return "1 message ago" if ago == 1 else f"{ago} messages ago"


def _first_line(text: str, limit: int = 100) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


# -- code blocks ---------------------------------------------------------------


def code_items(messages: Sequence[ChatMessage], selected: Optional[ChatMessage],
               scope: str) -> List[ListItem]:
    newest = len(messages) - 1
    chosen = ([(i, m) for i, m in enumerate(messages) if m is selected]
              if scope == SCOPE_MESSAGE else list(enumerate(messages)))
    items: List[ListItem] = []
    # Newest first across the chat, as Links does; in the message's own
    # order within one message.
    for index, message in (chosen if scope == SCOPE_MESSAGE else reversed(chosen)):
        for block in find_code_blocks(message.text or ""):
            row = block.row()
            if scope == SCOPE_CHAT:
                row = f"{row}. {message.label}, {_ago(index, newest)}"
            items.append(ListItem(row=row, detail=block.code, copy_text=block.code,
                                  copy_what="the code", detail_name=block.describe()))
    return items


# -- links ---------------------------------------------------------------------


def link_items(messages: Sequence[ChatMessage], selected: Optional[ChatMessage],
               scope: str) -> List[ListItem]:
    if scope == SCOPE_MESSAGE:
        if selected is None:
            return []
        index = next((i for i, m in enumerate(messages) if m is selected), len(messages) - 1)
        found = find_links([selected])
        for link in found:
            link.ago = len(messages) - 1 - index
    else:
        found = find_links(messages)
    return [ListItem(row=link.row(),
                     detail="\n".join(part for part in (link.words, link.url, link.why_not()) if part),
                     copy_text=link.url, copy_what=link.address,
                     detail_name=f"Link, {link.address}", link=link)
            for link in found]


# -- changed files -------------------------------------------------------------


def file_items(edits: Iterable[FileEdit], turn: Optional[int],
               describe: Callable) -> List[ListItem]:
    chosen = [e for e in edits if turn is None or e.turn == turn]
    return [ListItem(row=describe(changes), detail=file_text(changes),
                     copy_text=changes.path, copy_what="the file's path",
                     detail_name=f"Changes to {changes.path}")
            for changes in by_file(chosen)]


# -- commands ------------------------------------------------------------------


def command_items(commands: Iterable[ToolCommand], turn: Optional[int]) -> List[ListItem]:
    chosen = [c for c in commands if turn is None or c.turn == turn]
    items: List[ListItem] = []
    for command in reversed(chosen):  # newest first
        what = command.description or _first_line(command.command)
        state = ("failed" if command.failed else "done") if command.finished else "no result yet"
        row = f"{what}. {state}"
        parts = []
        if command.description:
            parts.append(command.description)
        parts.append(f"Command:\n{command.command}")
        if command.finished:
            parts.append(("Failed. Output:\n" if command.failed else "Output:\n")
                         + (command.output or "(no output)"))
        else:
            parts.append("No result yet.")
        items.append(ListItem(row=row, detail="\n\n".join(parts), copy_text=command.command,
                              copy_what="the command", detail_name=f"Command: {what}"))
    return items


# -- decisions -----------------------------------------------------------------


def _tool_id(message: ChatMessage) -> str:
    """The tool call a question or answer belongs to: their keys both end in
    it (``<record uuid>:<tool_use_id>``)."""
    return message.key.rsplit(":", 1)[-1] if ":" in message.key else ""


def decision_items(messages: Sequence[ChatMessage], turn: Optional[int]) -> List[ListItem]:
    answers = {_tool_id(m): m for m in messages if m.kind == ANSWER and _tool_id(m)}
    items: List[ListItem] = []
    for message in reversed(messages):  # newest first
        if message.kind != QUESTION or (turn is not None and message.turn != turn):
            continue
        answer = answers.get(_tool_id(message))
        question = _first_line(message.text)
        if answer is not None:
            said = _first_line(answer.text, 200)
            # An answer reads "Which colour? — Red"; the row has the question.
            if said.startswith(f"{question} — "):
                said = said[len(question) + 3:]
            said = said if len(said) <= 80 else said[:79] + "…"
            row = f"{question} You answered: {said}"
            detail = f"{message.text}\n\nYour answer:\n{answer.text}"
        else:
            row = f"{question} Not answered"
            detail = f"{message.text}\n\nNot answered."
        items.append(ListItem(row=row, detail=detail,
                              copy_text=answer.text if answer is not None else message.text,
                              copy_what="your answer" if answer is not None else "the question",
                              detail_name="Question and answer"))
    return items


# -- all together --------------------------------------------------------------


def build(category: str, scope: str, messages: Sequence[ChatMessage],
          selected: Optional[ChatMessage], edits: Iterable[FileEdit],
          commands: Iterable[ToolCommand], describe_file: Callable) -> List[ListItem]:
    """One tab's items. ``messages`` are the messages as the list shows them;
    ``selected`` is the message "This message" means (None: there's none)."""
    turn = None
    if scope == SCOPE_MESSAGE:
        if selected is None:
            return []
        turn = selected.turn
    if category == CODE:
        return code_items(messages, selected, scope)
    if category == LINKS:
        return link_items(messages, selected, scope)
    if category == FILES:
        return file_items(edits, turn, describe_file)
    if category == COMMANDS:
        return command_items(commands, turn)
    if category == DECISIONS:
        return decision_items(messages, turn)
    return []


def empty_text(category: str, scope: str) -> str:
    """The list's one line when a tab has nothing."""
    where = "this message" if scope == SCOPE_MESSAGE else "this chat"
    if scope == SCOPE_MESSAGE and category in (FILES, COMMANDS, DECISIONS):
        where = "this message's turn"
    return f"No {NOUNS.get(category, 'items')} in {where}."


def count_text(category: str, shown: int, total: int) -> str:
    """"Commands (12)" or "Commands (3 of 12)" for the list's label."""
    count = f"{total}" if shown == total else f"{shown} of {total}"
    return f"{TITLES.get(category, 'Items')} ({count})"
