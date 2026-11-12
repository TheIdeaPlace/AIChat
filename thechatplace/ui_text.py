"""Text the UI shows that is worth testing without wx."""
from __future__ import annotations

import re
import sys
from typing import List


#: The menu bar's menus by access key (#575), for naming a menu on a Mac.
MENU_NAMES = {"F": "File", "S": "Session", "C": "Chat", "V": "View", "T": "Tools", "H": "Help"}


def key_name(shortcut: str, platform=None) -> str:
    """Translate app-authored key names, before inserting any chat/user text."""
    if (platform or sys.platform) != "darwin":
        return shortcut
    # A Mac has no access keys: "(Attach Files button, Alt+I)" names the button.
    shortcut = shortcut.replace(", Alt+I)", ")")
    shortcut = shortcut.replace("Alt+F4", "Cmd+Q").replace("Alt+Tab", "Cmd+Tab").replace("Applications key / ", "")
    shortcut = re.sub(r"Alt\+([FSCVTH]),\s*[A-Za-z]",
                      lambda match: MENU_NAMES[match.group(1)] + " menu (VO+M)", shortcut)
    text = re.sub(r"\bEnter\b", "Return", shortcut.replace("Ctrl+", "Cmd+").replace("Alt+", "Option+"))
    return (text.replace("Win+Shift+S", "Control+Cmd+Shift+4")
            .replace(" Option+Return on a Mac", ""))


#: The first choice of a Codex chat's model or effort, and how Properties
#: names it: whatever Codex itself is set to use. One name everywhere (#596),
#: not "Configured Codex model" here and "Configured effort" there.
CODEX_DEFAULT = "Codex's default"


#: Your keys for the defaults you changed, as said: {"Ctrl+Shift+A":
#: "Ctrl+K"}, or the command's place ("Chat, Answer") when you took its key
#: away. Set by the window whenever its keys change (#635).
_YOUR_KEYS: dict = {}


def set_your_keys(keys: dict) -> None:
    """The window's keys changed: ``keys`` maps each changed default, as
    said, to what to say instead."""
    _YOUR_KEYS.clear()
    _YOUR_KEYS.update(keys)


def your_key(default: str) -> str:
    """What to say for a command's default key (Windows names, as said:
    "Ctrl+Period"): your key if you changed it, the command's place if you
    took it away, else the default. Put it through key_name for a Mac."""
    return _YOUR_KEYS.get(default, default)


def with_stop_key(text: str, platform=None, key=None) -> str:
    """A provider's "Press Stop to ..." with Stop's key added, the first time
    it's named (#596): the provider says what Stop does, and the window,
    which owns the keys, names the key: yours, if you changed it (#635)."""
    key = your_key("Ctrl+Period") if key is None else key
    if not text or not key or "Press Stop (" in text:
        return text
    return text.replace("Press Stop ", f"Press Stop ({key_name(key, platform)}) ", 1)


SHORTCUTS_PLATFORM_NOTE = (
    "This guide uses Windows key names. On a Mac, use Command (Cmd) for Ctrl, "
    "Option for Alt and Return for Enter. Open menus from the menu bar (VoiceOver: VO+M), "
    "rather than Windows Alt menu sequences."
)

CODEX_SAVED_RULES_NOTE = (
    "Commands you've told Codex to always allow will run without asking and outside this folder's limits, including the network. "
    "Project-supplied command rules are refused."
)


def codex_saved_rules_text(count):
    if type(count) is int and count >= 0:
        # No trailing space with none, or the next sentence follows two (#368).
        return f"Saved user/system rule files in effect: {count}." + (
            " " + CODEX_SAVED_RULES_NOTE if count else "")
    return "Saved command rules have not been checked. " + CODEX_SAVED_RULES_NOTE


#: The window, in Tab order. The Help dialog and the README both describe it.
LAYOUT = (
    "One window, three parts, in Tab order: the session list; the messages of the "
    "loaded session; and the reply box with Send and Stop (for a desktop app or "
    "terminal session, a read-only note saying where you reply, with provider-specific actions "
    "and Continue Here as they apply, in the same place). "
    "Shift+Tab goes back the same way. Provider (which AI) and Kind (where the session started) "
    "are default session-list columns; View, Session List Columns can remove or reorder them."
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
        ("Ctrl+Shift+L", "Copy Session Link (Claude chats): Enter copies a Markdown link that "
                         "opens the session in The Chat Place; the list also has the bare link, and "
                         "where they apply its Open in Claude link and claude.ai address"),
        ("Ctrl+Shift+N", "Continue the selected chat from the desktop app, a terminal or "
                         "another app here, as a copy you can reply to (the original isn't "
                         "changed)"),
        ("Ctrl+Shift+O", "Change Permission Mode: Claude permission modes, Codex file access, "
                         "approval reviewer or Plan mode, or Copilot agent mode, plan mode or Auto for the "
                         "selected (or loaded) Chat Place session (in Chat, Chat Settings, with "
                         "Change Model and Change Effort)"),
        ("Ctrl+Shift+W", "Read the loaded chat as one page, starting at the selected message: 2 "
                         "moves from message to message; Alt+P reads it as plain text instead"),
        ("Ctrl+Shift+H", "Chat Permissions: the selected (or loaded) Chat Place chat's permission "
                         "mode, and the commands and tools it may use without asking: for a "
                         "Claude chat, those it keeps and the rules from Claude Code's own "
                         "settings for its folder; for a Copilot chat, the commands allowed for it "
                         "and your rules for Copilot (Delete or Remove takes one back, and Remove "
                         "All takes them all)"),
        ("F5", "Refresh the list now and put it back in order (it also refreshes itself "
               "every few seconds, without moving rows while you're in it)"),
        ("Alt+V, O", "Sort Sessions: by status, newest first, oldest first, by title or "
                     "by folder; the choice is remembered"),
        ("Alt+V, H", "Show Sessions: all, needs you or working, needs you, working, with a "
                     "new reply, idle, desktop app, Cowork, The Chat Place, terminal, Remote "
                     "Control, ungrouped (in no group), Claude chats, Codex chats, GitHub "
                     "Copilot chats, archived, hidden, or one of your groups"),
        ("Alt+V, C", "Session List Columns: choose what each session's line says and in "
                     "what order, such as status first; in the dialog, Enter adds, Delete "
                     "removes, and Alt+Up, Alt+Down, Alt+Home and Alt+End move"),
        ("Ctrl+E", "Export the selected (or loaded) session as Markdown, a web page or "
                   "plain text"),
        ("F2", "Rename the selected session (a desktop app or terminal session's new name "
               "shows only in The Chat Place)"),
        ("Applications key / Shift+F10", "The session's menu: load, provider-specific actions, copy "
                                         "its link, rename, Remote Control, groups, export, "
                                         "hide or delete"),
        ("Alt+C, N", "Chat Settings: the selected (or loaded) chat's Change Model (M), Change "
                     "Effort (E), Change Permission Mode (P), Chat Permissions (C) and Chat "
                     "Options (O)"),
        ("Alt+S, C", "Remote Control (Claude chats): reach the selected session from "
                     "claude.ai and your phone (a desktop app session can be continued here "
                     "with it on)"),
        ("Ctrl+G", "Add the selected session to a group (or a new one); Remove from "
                   "Group and Manage Groups are on the Session menu"),
        ("Delete", "Hide the selected session; View, Show Sessions, Hidden lists hidden "
                   "sessions, and Session, Bring Back Session returns one"),
        ("Shift+Delete", "Delete a Claude or Codex chat started here permanently, conversation and "
                         "all, after you confirm, or The Chat Place's copy of a restored or kept chat "
                         "(other chats can only be hidden); on an agent "
                         "conversation, delete the conversation and keep its chats"),
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
        ("Ctrl+Alt+Enter on a queued message", "Steer it: into the running turn now, without "
                                               "stopping it"),
        ("Ctrl+Shift+Z on one of your messages", "Retry from Here (Claude and Codex chats): start a new "
                                                 "chat just before it, with your message ready to change "
                                                 "and send. Claude can also restore files changed with "
                                                 "its edit tools in chats started here; Codex doesn't. "
                                                 "The new chat keeps the original's settings, and the "
                                                 "original chat is kept. Branch from Here (Alt+C, F, or on "
                                                 "a message's menu) "
                                                 "starts a new chat from that point; in Codex it includes "
                                                 "the whole turn containing that message"),
        ("Ctrl+Shift+B, or Applications key / Shift+F10 then Code Blocks",
         "Lists, on Code Blocks for this message: read and copy each code block"),
        ("Ctrl+F, then F3 and Shift+F3", "Find a message containing some text (its whole "
                                         "text, not just the first line), then the next or "
                                         "previous one"),
        ("End", "Newest message"),
        ("Ctrl+Shift+I", "Lists: code blocks, links, changed files, commands, decisions, "
                         "tasks, subagents and auto approvals, for this message or the whole chat; it opens on the kind you used last, and "
                         "the Kinds list (Alt+K) chooses another. From anywhere in it, Ctrl+Page Down "
                         "and Ctrl+Page Up change the kind (also Cmd+Shift+] and Cmd+Shift+[ on a "
                         "Mac, and Control+Tab on Windows); Enter reads an item in full "
                         "(a link opens, a subagent's conversation opens), Ctrl+C copies, typing narrows the list (also from the "
                         "session list and the reply box)"),
        ("Ctrl+L", "Lists, on Links for the whole chat: Enter opens a link in your "
                   "browser, Ctrl+C copies its address (also from the session list and the "
                   "reply box)"),
        ("Ctrl+Shift+J", "Lists, on Tasks for the whole chat: the task list, each step "
                         "with whether it's to do, in progress or done"),
        ("Ctrl+T", "Show or hide tool activity in the loaded session, and in Codex chats "
                   "started here, the requests allowed for it"),
        ("Ctrl+O", "Open the loaded session in the Claude desktop app"),
    ]),
    ("Reply box", [
        ("Ctrl+Enter", "Send (Chat Place sessions only); you stay in the reply box. "
                       "During a turn it queues the message and sends it when the turn ends"),
        ("Ctrl+Shift+Enter", "Send and save as a prompt: asks for the prompt's name (its "
                             "first words to start with), saves it, then sends as Ctrl+Enter "
                             "does; Escape at the name neither saves nor sends"),
        ("Ctrl+Alt+Enter", "Steer: during a turn, send the message into it without stopping "
                           "it; the AI takes it in at its next step and carries on. With no "
                           "turn running, it sends as Ctrl+Enter does"),
        ("Ctrl+Period", "Stop the running turn; a queued message goes back in the reply box. "
                        "Chat, Stop Background Task, just below it, ends one background task "
                        "and lets the turn carry on"),
        ("Ctrl+/", "Insert a command or skill (Claude and Codex chats; type to search); it goes at "
                   "the start of the reply box. Codex offers enabled skills and chat controls "
                   "such as /plan and /compact"),
        ("Ctrl+Shift+F", "Attach files or images to your next Claude or Codex message (Attach Files button, Alt+I); "
                         "Delete in the attachments list removes one"),
        ("Ctrl+V with a picture or files copied", "Attach the picture (a screenshot from Win+Shift+S, say), "
                                                  "or files copied in File Explorer or the Finder, to a "
                                                  "Claude or Codex message"),
        ("Ctrl+Shift+M", "Other machines: list your sessions on other computers and send one "
                         "a message, through Claude in the loaded Chat Place session (it needs "
                         "Remote Control on)"),
        ("Ctrl+Shift+E", "Send Reply to Another Chat: the selected reply (or the latest one) "
                         "goes to another Chat Place chat, Claude, Codex or Copilot, or a new "
                         "one, labelled with who wrote it; read and change it first, then Enter "
                         "sends and you go to that chat. In an agent conversation, hands the "
                         "waiting reply to the other agent"),
        ("Ctrl+Shift+G", "New Agent Conversation: two chats, Claude, Codex or Copilot, new or "
                         "your own, that pass replies to each other; you approve each hand-off, "
                         "or, once "
                         "Settings allows it, let them go automatically up to a turn limit"),
        ("Ctrl+Shift+D", "Lists, on Changed Files (Claude, Codex and Copilot chats): what changed in the selected "
         "message's turn (your latest, if none is selected) or in the whole chat; Enter on a file "
         "reads its changes line by line"),
        ("Ctrl+Shift+U", "Usage and context: read the active provider's reported context and "
                         "account allowances and reset times; Ctrl+C copies a line"),
        ("Ctrl+Shift+T", "Turn status: how long the provider has been working, on what, what "
                         "is still running in the background, whether a message "
                         "is queued, and how far along the task list is"),
    ]),
    ("Anywhere", [
        ("Ctrl+Shift+S", "Find in All Sessions: search the messages of every session in the "
                         "list; Enter on a result loads that session on that message. Pressed "
                         "again while it searches, it stops"),
        ("Ctrl+N", "New Session: start a chat here, from anywhere in the window"),
        ("Ctrl+Shift+A", "Answer: approve or deny a tool, answer the provider's questions, or "
                         "approve its plan (the loaded session first, then another chat "
                         "waiting for an answer). Escape in the dialog answers later. In "
                         "your own answer, What to Change, or a reason for Deny, Shift+Enter "
                         "starts a new line"),
        ("Ctrl+Shift+Y", "Ask Aside (Claude and Codex chats): a quick question about the selected (or "
                         "loaded) chat, answered without the question or answer being added "
                         "to it; Ctrl+Enter asks, and the answer is read and goes in the "
                         "Answer box"),
        ("Ctrl+Shift+K", "What AI knows about you: choose Claude or Codex, then read "
                         "local instructions, memories, skills and settings, or open "
                         "them in your editor"),
        ("Ctrl+Shift+P", "Prompts: your saved prompts, to use (Enter puts one in the reply "
                         "box), add, edit, reorder or delete"),
        ("Alt+Enter", "Properties: what can be known about the selected session (or, in the "
                      "messages, the selected message), one line each; Ctrl+C copies a line. "
                      "Option+Return on a Mac"),
        ("F1", "This list of shortcuts"),
        ("Alt+H, G", "The user guide, read by heading"),
        ("Ctrl+Comma", "Settings: announcements, speech, notifications and more"),
        ("Ctrl+Shift+R", "Repeat the last announcement"),
        ("Ctrl+Shift+X", "Mute All Sounds: silence every sound at once, or bring them back "
                         "(Tools, Sounds chooses them)"),
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
    reader doesn't read out stars and brackets. A table becomes one line per
    row, "First cell: Header value; Header value", rather than pipes."""
    text = _tables_as_text(text)
    text = _HEADING.sub("", text)
    text = _LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2)
                     else f"{m.group(1)} ({m.group(2)})", text)
    text = _EMPHASIS.sub(r"\2", text)
    return _CODE.sub(r"\1", text)


def _table_cells(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _tables_as_text(text: str) -> str:
    """Each Markdown table (a header row, then a |---| rule) as readable
    lines: the header row and the rule go, and each body row reads its first
    cell, then each other cell after its column's name. Lines that only look
    like a table, with no rule under the first, are left as they are."""
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (line.lstrip().startswith("|") and i + 1 < len(lines)
                and _TABLE_RULE.fullmatch(lines[i + 1])):
            headers = _table_cells(line)
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _table_cells(lines[i])
                rest = [f"{headers[n]} {cell}" if n < len(headers) and headers[n] else cell
                        for n, cell in enumerate(cells[1:], start=1) if cell]
                out.append(cells[0] + (": " + "; ".join(rest) if rest else ""))
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


#: About how long the session list's Last message column is (#146): enough
#: to hear where a conversation ended, short enough to arrow past.
LAST_MESSAGE_LIMIT = 150


def choose_access_key(label: str, taken: set) -> str:
    """A key for a field a server named (#468), "" if every letter in ``label``
    is taken: the first free letter or digit that starts a word, else any in
    it, lower case. It's added to ``taken``, the keys already spoken for. Only
    plain ones, as a key that isn't on the keyboard is no use."""
    plain = [c.lower() for c in label if c.isascii() and c.isalnum()]
    starts = [m.group().lower() for m in re.finditer(r"\b[A-Za-z0-9]", label)]
    for key in starts + plain:
        if key not in taken:
            taken.add(key)
            return key
    return ""


def mark_access_key(text: str, key: str) -> str:
    """A server's ``text`` as a control's label: each "&" of its own shown, not
    taken as an access key, and ``key`` marked where it first comes."""
    text = text.replace("&", "&&")
    for index, char in enumerate(text):
        if key and char.isascii() and char.lower() == key:
            return f"{text[:index]}&{text[index:]}"
    return text


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


#: A key named in the shortcuts list's key column: modifiers, then an F key,
#: a named key or one letter, digit or mark.
_KEY_IN_TEXT = re.compile(r"(?<![\w+])(?:(?:Ctrl|Alt|Shift)\+)*"
                          r"(?:F\d{1,2}|Period|Comma|Slash|Enter|Delete|[A-Z0-9/.,])(?![\w+])")


def shortcuts_with_yours(keys=None):
    """SHORTCUTS with your keys (#635): a key you changed is shown in place
    of the default, which is named after what it does; one you removed reads
    "No key"; keys you gave commands that had none are a group of their own.
    ``keys`` is a keymap.KeyMap, or None for the defaults (the README)."""
    if keys is None:
        return SHORTCUTS
    from .keymap import normalize, spoken  # keymap needs this module first
    remapped = keys.remapped()
    groups = []
    for group, items in SHORTCUTS:
        rows = []
        for key, action in items:
            # Each key the row names ("Ctrl+F, then F3 and Shift+F3", "Ctrl+Shift+Z
            # on one of your messages"), in place, with what it was (#639).
            was = []

            def yours(match):
                try:
                    default = normalize(match.group(0))
                except ValueError:
                    return match.group(0)
                if default not in remapped:
                    return match.group(0)
                was.append(spoken(default, "win32"))
                return spoken(remapped[default], "win32") if remapped[default] else "No key"

            key = _KEY_IN_TEXT.sub(yours, key)
            if was:
                action = f"{action} (changed from {', '.join(was)})"
            rows.append((key, action))
        groups.append((group, rows))
    added = keys.added()
    if added:
        groups.append(("Keys you added", [(spoken(key, "win32"), name) for key, name in added]))
    return groups


def shortcuts_html(*, platform=None, keys=None) -> str:
    """The same list as a page body: a heading and a table per group, with
    the keys as row headers, so a screen reader moves by heading (H) and
    reads each key with what it does as it moves through a table."""
    import html

    parts = ["<h1>Keyboard shortcuts</h1>", f"<p>{html.escape(LAYOUT)}</p>"]
    if platform == "all":
        parts.append(f"<p>{html.escape(SHORTCUTS_PLATFORM_NOTE)}</p>")
    for number, (group, items) in enumerate(shortcuts_with_yours(keys), start=1):
        # The table takes its name from the heading above it, so the group
        # is named once, not as a heading and again as a separate label.
        parts.append(f'<h2 id="group{number}">{html.escape(group)}</h2>')
        parts.append(f'<table aria-labelledby="group{number}">'
                     '<thead><tr><th scope="col">Keys</th><th scope="col">What it does</th>'
                     "</tr></thead><tbody>")
        for key, action in items:
            if platform != "all":
                key, action = key_name(key, platform), key_name(action, platform)
            parts.append(f'<tr><th scope="row">{html.escape(key)}</th>'
                         f"<td>{html.escape(action)}</td></tr>")
        parts.append("</tbody></table>")
    return "\n".join(parts)


def shortcuts_text(*, platform=None, keys=None) -> str:
    lines: List[str] = [LAYOUT, ""]
    if platform == "all":
        lines += [SHORTCUTS_PLATFORM_NOTE, ""]
    for group, items in shortcuts_with_yours(keys):
        lines.append(f"{group}:")
        for key, action in items:
            if platform != "all":
                key, action = key_name(key, platform), key_name(action, platform)
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
