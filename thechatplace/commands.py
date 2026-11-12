"""Every menu command, once (#575, #635).

The menu bar is built from ``MENU_BAR``, so a command's label, access key and
key live in one place, and tests can check them without wx: no two items in a
menu share an access key, and no two commands share a key. Customizable keys
(#635) will look commands up here by ``id``, so an id is never renamed once
released, even when its command moves to another menu.

Keys use Windows names ("Ctrl+Shift+A"). wx turns Ctrl into Cmd on a Mac in a
menu accelerator; a key that's only shown in a label goes through
``ui_text.key_name`` instead.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple, Union

from .ui_text import key_name


@dataclass(frozen=True)
class Command:
    #: Stable name, used by handlers now and by ``keys.json`` later (#635).
    id: str
    #: The menu label, with "&" before its access key. "{key}" marks where
    #: ``shown_key`` goes when it isn't at the end.
    label: str
    #: A window-wide menu accelerator.
    key: str = ""
    #: A key that belongs to one part of the window (Delete in the session
    #: list, Ctrl+Shift+Enter in the reply box): a menu accelerator would be
    #: window-wide, so it's named in the label, without a tab, and handled
    #: in the window's key hook.
    shown_key: str = ""
    #: A check item (its state is the window's to set).
    check: bool = False
    #: wx's stock id, which moves Settings, About and Exit into a Mac's
    #: application menu: "preferences", "about" or "exit".
    stock: str = ""
    #: No accelerator on a Mac, where the key means something else
    #: (Cmd+Shift+Z is Redo); the window's key hook handles it there.
    not_on_mac: bool = False


@dataclass(frozen=True)
class Menu:
    label: str
    items: Tuple["Item", ...] = ()
    #: A submenu the window fills itself (Sort Sessions' radio items, Show
    #: Sessions' views and groups), named so the window can find it.
    id: str = ""


#: A line between groups of items.
SEPARATOR = None

Item = Union[Command, Menu, None]


MENU_BAR: Tuple[Menu, ...] = (
    Menu("&File", (
        # Enter is handled on the list itself (a menu accelerator for Enter
        # would steal it from every button and text box in the window).
        Command("load_session", "&Load Session"),
        Command("open_in_claude", "Open in &Claude", "Ctrl+O"),
        Command("copy_session_link", "Cop&y Session Link...", "Ctrl+Shift+L"),
        Command("answer", "&Answer...", "Ctrl+Shift+A"),
        # A question about the chat that isn't added to it (#468).
        Command("ask_aside", "Ask Aside...", "Ctrl+Shift+Y"),
        Command("continue_here", "Con&tinue Here...", "Ctrl+Shift+N"),
        # Back to a message of the loaded chat (#468), named for what each is
        # for (#596).
        Command("retry_from_here", "Retry from Here...", "Ctrl+Shift+Z", not_on_mac=True),
        Command("branch_from_here", "Branch &from Here"),
        Command("new_session", "&New Session...", "Ctrl+N"),
        # The loaded chat's own settings, together (#596). Their keys are
        # still window-wide with the submenu shut, as any menu item's are.
        Menu("Chat Sett&ings", (
            Command("change_model", "Change &Model..."),
            Command("change_effort", "Change &Effort..."),
            Command("change_permission_mode", "Change &Permission Mode...", "Ctrl+Shift+O"),
            # What the chat may do without asking, and taking it back (#307).
            Command("chat_permissions", "&Chat Permissions...", "Ctrl+Shift+H"),
            # Agent, instructions, output style and more folders (#468).
            Command("chat_options", "Chat &Options..."),
        )),
        Command("remote_control", "Rem&ote Control..."),
        Command("other_machines", "Other Machines...", "Ctrl+Shift+M"),
        # A reply from the loaded chat to another of yours (#248).
        Command("send_reply_to_another_chat", "Send Reply to Another Chat...", "Ctrl+Shift+E"),
        # Agent conversations (#248).
        Command("new_agent_conversation", "New Agent Conversation...", "Ctrl+Shift+G"),
        Command("agent_conversation_members", "Agent Conversation Members..."),
        Command("agent_conversation_handoffs", "Agent Conversation Hand-offs..."),
        Command("refresh", "&Refresh", "F5"),
        # F2, as in Explorer (#93).
        Command("rename_session", "Rename Session...", "F2"),
        # Delete and Shift+Delete belong to the session list: as menu
        # accelerators they were window-wide, so Delete in the attachments
        # list hid the loaded session instead of removing the file.
        Command("hide_session", "Hi&de Session", shown_key="Delete"),
        Command("bring_back_session", "&Bring Back Session"),
        Command("delete_session_permanently", "Delete Session &Permanently ({key})...",
                shown_key="Shift+Delete"),
        Command("export_session", "&Export Session...", "Ctrl+E"),
        Command("insert_command", "Insert Command or S&kill...", "Ctrl+/"),
        Command("attach_files", "Attac&h Files...", "Ctrl+Shift+F"),
        # Saved prompts (#131). Ctrl+Shift+Enter belongs to the reply box.
        Command("prompts", "Prompts...", "Ctrl+Shift+P"),
        Command("send_and_save_prompt", "Send and Save as Prompt...",
                shown_key="Ctrl+Shift+Enter"),
        # Steer (#238, #239): a reply-box key too.
        Command("steer", "Steer: Send into the Running Turn", shown_key="Ctrl+Alt+Enter"),
        SEPARATOR,
        Command("add_to_group", "Add to &Group...", "Ctrl+G"),
        Command("remove_from_group", "Remove from Gro&up..."),
        Command("manage_groups", "&Manage Groups..."),
        SEPARATOR,
        Command("settings", "&Settings...", "Ctrl+,", stock="preferences"),
        Command("sounds", "Sounds..."),
        SEPARATOR,
        # Back up and check (#433).
        Command("back_up_now", "Back Up No&w..."),
        Command("check_backup", "Check Backup..."),
        Command("backup_schedule", "Backup Schedule..."),
        Command("restore_from_backup", "Restore from Backup..."),
        # Many chats as files (#461).
        Command("export_chats", "Export Chats..."),
        SEPARATOR,
        Command("exit", "E&xit", stock="exit"),
    )),
    Menu("&View", (
        Command("go_to_sessions", "Go to &Sessions", "Ctrl+1"),
        Command("go_to_messages", "Go to &Messages", "Ctrl+2"),
        Command("go_to_reply", "Go to &Reply", "Ctrl+3"),
        Command("go_to_status_bar", "Go to Status &Bar", "Ctrl+9"),
        # Radio items: a screen reader says which order is checked.
        Menu("S&ort Sessions", id="sort_sessions"),
        # Which sessions to list (#32): radio items, groups at the end (#31).
        Menu("S&how Sessions", id="show_sessions"),
        # What each row says, and in what order (#134).
        Command("session_list_columns", "S&ession List Columns..."),
        SEPARATOR,
        Command("read_full_message", "Read &Full Message"),
        Command("read_whole_chat", "Read Full Chat as a &Web Page", "Ctrl+Shift+W"),
        Command("find", "F&ind...", "Ctrl+F"),
        Command("find_next", "Find &Next", "F3"),
        Command("find_previous", "Find Pre&vious", "Shift+F3"),
        Command("links", "Links...", "Ctrl+L"),  # #190
        # One dialog, a tab per kind of thing in the chat (#279).
        Command("lists", "Lists...", "Ctrl+Shift+I"),
        Command("tasks", "Tasks...", "Ctrl+Shift+J"),  # #231
        Command("subagents", "Suba&gents..."),  # #468
        Command("find_in_all_sessions", "Find in &All Sessions...", "Ctrl+Shift+S"),
        # Alt+Enter as in Explorer (#214): the focused session, or message.
        Command("properties", "Properties...", "Alt+Enter"),
        Command("show_tool_activity", "Show &Tool Activity", "Ctrl+T", check=True),
        Command("mute_all_sounds", "Mute All Sounds", "Ctrl+Shift+X", check=True),
        Command("stop_running_turn", "Sto&p Running Turn", "Ctrl+."),
        # One background task ended, the turn kept (#468), beside the other
        # Stop (#596); enabled while there's work to stop.
        Command("stop_background_task", "Stop Background Task..."),
        Command("turn_status", "T&urn Status", "Ctrl+Shift+T"),
        Command("usage_and_context", "Usage and &Context...", "Ctrl+Shift+U"),
        Command("changed_files", "Change&d Files...", "Ctrl+Shift+D"),
        Command("repeat_last_announcement", "Repeat &Last Announcement", "Ctrl+Shift+R"),
        SEPARATOR,
        Command("what_ai_knows", "What AI &Knows About You...", "Ctrl+Shift+K"),
        # A Claude chat's MCP servers and claude.ai connectors (#468).
        Command("connected_tools", "Connected Tools (MCP)..."),
    )),
    Menu("&Help", (
        Command("user_guide", "User &Guide"),
        Command("keyboard_shortcuts", "&Keyboard Shortcuts", "F1"),
        Command("sound_tutorial", "Sound &Tutorial..."),
        # Its label says the version running, or the update a check found
        # (#141); the window sets it (``ui_text.update_item_label``).
        Command("check_for_updates", "Check for &Updates..."),
        Command("claude_sign_in", "Claude Code &Sign-in..."),
        Command("codex_sign_in", "Codex S&ign-in..."),
        Command("copilot_sign_in", "C&opilot Sign-in..."),
        Command("report_bug", "Report a &Bug..."),
        Command("about", "&About", stock="about"),
    )),
)


def walk(items=MENU_BAR) -> Iterator[Item]:
    """Every menu and item, depth first, separators included."""
    for item in items:
        yield item
        if isinstance(item, Menu):
            yield from walk(item.items)


def commands() -> Iterator[Command]:
    return (item for item in walk() if isinstance(item, Command))


def command(command_id: str) -> Command:
    for item in commands():
        if item.id == command_id:
            return item
    raise KeyError(command_id)


def access_key(label: str) -> str:
    """The letter after a single "&", lower case, or "" for none."""
    match = re.search(r"(?<!&)&([^&])", label)
    return match.group(1).lower() if match else ""


def menu_label(item: Command, platform: Optional[str] = None) -> str:
    """The label wx is given: a shown key in brackets, or a tab and the
    accelerator, which is what a screen reader reads as the item's shortcut."""
    platform = platform or sys.platform
    label = item.label
    if item.shown_key:
        shown = key_name(item.shown_key, platform)
        if "{key}" in label:
            return label.replace("{key}", shown)
        return f"{label} ({shown})"
    if item.key and not (item.not_on_mac and platform == "darwin"):
        return f"{label}\t{item.key}"
    return label.replace("{key}", "")
