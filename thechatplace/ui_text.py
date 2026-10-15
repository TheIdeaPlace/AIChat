"""Text the UI shows that is worth testing without wx."""
from __future__ import annotations

import re
import sys
from typing import List


def key_name(shortcut: str) -> str:
    """Name wx's primary accelerator modifier on this platform."""
    return shortcut.replace("Ctrl+", "Cmd+") if sys.platform == "darwin" else shortcut

#: The window, in Tab order. The Help dialog and the README both describe it.
LAYOUT = (
    "One window, three parts, in Tab order: the session list; the messages of the "
    "loaded session; and the reply box with Send and Stop (for a desktop app or "
    "terminal session, a read-only note saying where you reply, with provider-specific actions "
    "and Continue Here as they apply, in the same place). "
    "Shift+Tab goes back the same way."
)

#: Every shortcut, grouped. The Help dialog and the README both list these.
SHORTCUTS = [
    ("Moving around", [
        ("Tab, Shift+Tab", "Session list, messages, reply box, and back"),
        ("Ctrl+1", "Go to the session list"),
        ("Ctrl+2", "Go to the messages"),
        ("Ctrl+3", "Go to the reply box (or the note, for a read-only session)"),
        ("F6, Shift+F6", "Next or previous part of the window: session list, messages, "
                         "reply box (when a session is loaded), status bar, and round again"),
        ("Ctrl+9", "Go to the status bar: what was last said, the loaded session, and "
                   "buttons for how full the context is, sessions that need you and an "
                   "update; Left and Right move between them, Tab leaves"),
        ("Escape in the messages or the reply box",
         "Back to the session list, on the same session"),
        ("Backspace in the messages", "Also back to the session list"),
    ]),
    ("Session list", [
        ("Enter", "Load that session and move to its messages"),
        ("Ctrl+O", "Open the selected session in the Claude desktop app"),
        ("Ctrl+Shift+L", "Copy Session Link: Enter copies a Markdown link that opens the "
                         "session in The Chat Place; the list also has the bare link, and "
                         "where they apply its Open in Claude link and claude.ai address"),
        ("Ctrl+N", "New Chat Place session"),
        ("Ctrl+Shift+N", "Continue the selected desktop app or terminal session here, as a "
                         "copy you can reply to (the original isn't changed)"),
        ("Ctrl+Shift+O", "Change Permission Mode: Claude permission modes or Codex file access for the "
                         "selected (or loaded) Chat Place session, from its next turn (File, "
                         "Change Effort and Change Model are beside it)"),
        ("F5", "Refresh the list now and put it back in order (it also refreshes itself "
               "every few seconds, without moving rows while you're in it)"),
        ("Alt+V, O", "Sort Sessions: by status, newest first, oldest first, by title or "
                     "by folder; the choice is remembered"),
        ("Alt+V, H", "Show Sessions: all, needs you or working, needs you, working, with a "
                     "new reply, idle, desktop app, Cowork, The Chat Place, terminal, Remote "
                     "Control, Claude chats, Codex chats, ungrouped (in no group), archived, or one of your groups"),
        ("Alt+V, E", "Session List Columns: choose what each session's line says and in "
                     "what order, such as status first; in the dialog, Enter adds, Delete "
                     "removes, and Alt+Up, Alt+Down, Alt+Home and Alt+End move"),
        ("Ctrl+E", "Export the selected (or loaded) session as Markdown, a web page or "
                   "plain text"),
        ("F2", "Rename the selected session (a desktop app or terminal session's new name "
               "shows only in The Chat Place)"),
        ("Applications key / Shift+F10", "The session's menu: load, provider-specific actions, copy "
                                         "its link, rename, Remote Control, groups, export, "
                                         "hide or delete"),
        ("Alt+F, O", "Remote Control: reach the selected session from claude.ai and your "
                     "phone (a desktop app session can be continued here with it on)"),
        ("Ctrl+G", "Add the selected session to a group (or a new one); File, Remove "
                   "from Group and Manage Groups are on the File menu"),
        ("Delete", "Hide the selected session; View, Show Sessions, Hidden lists hidden "
                   "sessions, and File, Bring Back Session returns one"),
        ("Shift+Delete", "Delete one of The Chat Place's own sessions permanently, "
                         "transcript and all, after you confirm (desktop app and terminal "
                         "sessions can only be hidden)"),
        ("Ctrl+F", "Show only sessions whose title, folder or what they need contains some "
                   "text; Escape in the list shows them all again"),
    ]),
    ("Messages", [
        ("Enter, or Applications key / Shift+F10 then Read Full Message",
         "Read the whole message as a formatted page (move by heading, table, list and "
         "code block); Alt+P reads it as plain text instead; Escape comes back to it"),
        ("Ctrl+C", "Copy the whole message"),
        ("Ctrl+Shift+C", "Copy the message's last code block"),
        ("Ctrl+Enter on a queued message", "Send it now (Claude chats): Claude stops what it's doing and "
                                           "answers it"),
        ("Ctrl+Shift+B, or Applications key / Shift+F10 then Code Blocks",
         "List, read and copy each code block"),
        ("Ctrl+F, then F3 and Shift+F3", "Find a message containing some text (its whole "
                                         "text, not just the first line), then the next or "
                                         "previous one"),
        ("End", "Newest message"),
        ("Ctrl+L", "Links: every link in the loaded session, newest first; Enter opens one "
                   "in your browser, Ctrl+C copies its address, typing narrows the list "
                   "(also from the session list and the reply box)"),
        ("Ctrl+T", "Show or hide tool activity in the loaded session"),
        ("Ctrl+O", "Open this session in the Claude desktop app"),
    ]),
    ("Reply box", [
        ("Ctrl+Enter", "Send (Chat Place sessions only); you stay in the reply box. "
                       "During a turn it queues the message and sends it when the turn ends"),
        ("Ctrl+Shift+Enter", "Send and save as a prompt: asks for the prompt's name (its "
                             "first words to start with), saves it, then sends as Ctrl+Enter "
                             "does; Escape at the name neither saves nor sends"),
        ("Ctrl+Period", "Stop the running turn; a queued message goes back in the reply box"),
        ("Ctrl+/", "Insert a Claude slash command or one of your skills (type to search); it goes at "
                   "the start of the reply box"),
        ("Ctrl+Shift+F", "Attach files or images to your next Claude message (Attach Files button, "
                         "Alt+T); Delete in the attachments list removes one"),
        ("Ctrl+V with a picture copied", "Attach the picture to a Claude message (a screenshot from Win+Shift+S, "
                                         "say)"),
        ("Ctrl+Shift+M", "Other machines: list your sessions on other computers and send one "
                         "a message, through Claude in the loaded Chat Place session (it needs "
                         "Remote Control on)"),
        ("Ctrl+Shift+D", "Changed files (Claude chats): what changed since your latest message or in "
         "the whole session; Enter on a file reads its changes line by line"),
        ("Ctrl+Shift+U", "Usage and context: read the active provider's reported context and "
                         "account allowances and reset times; Ctrl+C copies a line"),
        ("Ctrl+Shift+T", "Turn status: how long the provider has been working, on what, what "
                         "is still running in the background, and whether a message "
                         "is queued"),
    ]),
    ("Anywhere", [
        ("Ctrl+Shift+S", "Find in All Sessions: search the messages of every session in the "
                         "list; Enter on a result loads that session on that message. Pressed "
                         "again while it searches, it stops"),
        ("Ctrl+Shift+A", "Answer: approve or deny a tool, answer the provider's questions, or "
                         "approve its plan (the loaded session first, then another chat "
                         "waiting for an answer). Escape in the dialog answers later"),
        ("Ctrl+Shift+K", "What Claude knows about you: your instructions, memories, "
                         "skills, subagents, commands and settings, to read or open in "
                         "your editor"),
        ("Ctrl+Shift+P", "Prompts: your saved prompts, to use (Enter puts one in the reply "
                         "box), add, edit, reorder or delete"),
        ("Alt+Enter", "Properties: what can be known about the selected session (or, in the "
                      "messages, the selected message), one line each; Ctrl+C copies a line. "
                      "Option+Return on a Mac"),
        ("F1", "This list of shortcuts"),
        ("Alt+H, G", "The user guide, read by heading"),
        ("Ctrl+Comma", "Settings (announcements and speech)"),
        ("Ctrl+Shift+R", "Repeat the last announcement"),
        ("Alt+F4", "Quit"),
    ]),
]


_HEADING = re.compile(r"^#{1,6}\s+", re.M)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")
_CODE = re.compile(r"`([^`]+)`")


def markdown_as_text(text: str) -> str:
    """A document's Markdown as plain text, for the text box (#104): Read as
    Plain Text, and the only view on a Mac. Headings lose their #s, bold and
    code lose their marks, and a link reads "text (address)", so a screen
    reader doesn't read out stars and brackets."""
    text = _HEADING.sub("", text)
    text = _LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2)
                     else f"{m.group(1)} ({m.group(2)})", text)
    text = _EMPHASIS.sub(r"\2", text)
    return _CODE.sub(r"\1", text)


#: About how long the session list's Last message column is (#146): enough
#: to hear where a conversation ended, short enough to arrow past.
LAST_MESSAGE_LIMIT = 150


def codex_effort_label(value: str) -> str:
    """Readable choices; the protocol value stays in the validated catalog."""
    return {'none': 'None', 'minimal': 'Minimal', 'low': 'Low', 'medium': 'Medium',
            'high': 'High', 'xhigh': 'Extra high', 'max': 'Maximum', 'ultra': 'Ultra'}.get(
                value, value.replace('_', ' ').replace('-', ' ').capitalize())
_FENCE = re.compile(r"^\s*(```|~~~).*$", re.M)
_LINE_MARK = re.compile(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|>\s?)+", re.M)
_SINGLE_EMPHASIS = re.compile(r"(?<![\w*_])([*_])(?=\S)([^*_\n]+?)(?<=\S)\1(?![\w*_])")
_BARE_URL = re.compile(r"<?\bhttps?://(?:www\.)?([^/?#\s<>()]+?)(?:[/?#][^\s<>()]*?)?>?(?=[.,;:!?)]*(?:\s|$))")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", re.M)


def one_line(text: str, limit: int = LAST_MESSAGE_LIMIT) -> str:
    """Markdown as one plain line of about ``limit`` characters: no marks,
    bullets, quote signs or code fences, all whitespace one space, and a
    long one cut at a word with "…", so a screen reader reads words, not
    stars and pipes."""
    text = _FENCE.sub("", text)
    text = _TABLE_RULE.sub("", text)
    # A link is its words, and a bare address its site: a screen reader
    # would spell out the whole address, and use up the line on it.
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _BARE_URL.sub(lambda m: m.group(1), text)
    text = _LINE_MARK.sub("", markdown_as_text(text))
    text = _SINGLE_EMPHASIS.sub(r"\2", text)
    text = " ".join(text.replace("|", " ").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def last_message_line(label: str, text: str) -> str:
    """The Last message column (#146): the provider's name or "You: …", or ""
    when there's nothing to read."""
    plain = one_line(text)
    return f"{label}: {plain}" if plain else ""


def shortcuts_html() -> str:
    """The same list as a page body: a heading and a table per group, with
    the keys as row headers, so a screen reader moves by heading (H) and
    reads each key with what it does as it moves through a table."""
    import html

    parts = ["<h1>Keyboard shortcuts</h1>", f"<p>{html.escape(LAYOUT)}</p>"]
    for number, (group, items) in enumerate(SHORTCUTS, start=1):
        # The table takes its name from the heading above it, so the group
        # is named once, not as a heading and again as a separate label.
        parts.append(f'<h2 id="group{number}">{html.escape(group)}</h2>')
        parts.append(f'<table aria-labelledby="group{number}">'
                     '<thead><tr><th scope="col">Keys</th><th scope="col">What it does</th>'
                     "</tr></thead><tbody>")
        for key, action in items:
            parts.append(f'<tr><th scope="row">{html.escape(key)}</th>'
                         f"<td>{html.escape(action)}</td></tr>")
        parts.append("</tbody></table>")
    return "\n".join(parts)


def shortcuts_text() -> str:
    lines: List[str] = [LAYOUT, ""]
    for group, items in SHORTCUTS:
        lines.append(f"{group}:")
        for key, action in items:
            lines.append(f"  {key}: {action}")
        lines.append("")
    return "\n".join(lines).rstrip()


# -- Help's update item (#141) ----------------------------------------------------------


def update_item_label(running: str, available: str = "") -> str:
    """The Help menu's update item, as QuickMail's: it always answers "what
    am I running?", and says when a check found a newer version. U is its
    access key either way, so Alt+H, U keeps working."""
    if available:
        return f"&Update to {available} Available..."
    return f"Check for &Updates (running {running})..."


def update_item_spoken(running: str, available: str = "") -> str:
    """The same item as it's named in something said ("Help, Update to
    0.1.5 Available installs it"): no access key, no dots, no version
    when there's no update, which would only be noise."""
    if available:
        return f"Update to {available} Available"
    return "Check for Updates"
