"""The Chat Place's window: sessions, messages and reply, all in one view.

Accessibility decisions, and why
--------------------------------
* **One window, three parts, in Tab order** (issue #171): the sessions list,
  the loaded session's messages, then the reply box (or, for a desktop
  session, a read-only note and Open in Claude in the same place) and Send
  and Stop. Nothing is hidden behind a tab or a second screen: Shift+Tab
  from the messages goes back to the sessions list, still on the same
  session. Enter in the sessions list loads that session and moves to its
  messages; arrowing doesn't load anything.
* **Send and Stop never move** (issue #175). Both stay enabled, so Tab from
  the reply box is always Send, then Stop: Kelly tabs and presses Enter from
  habit, and a disabled Send once made that Tab land on Stop and cancel the
  turn. Send during a turn queues the message and sends it when the turn
  ends, as Claude Code does; Stop with nothing running just says so.
* **Both lists are ``wx.ListBox``.** One tab stop, arrow keys, and every item
  is one string a screen reader reads whole (IDT's chat app made the same
  choice). A session reads "title, repo, state, age"; a message reads
  "You: first line" or "Claude: first line".
* **The full message opens from the list** (Enter, or the context menu) in a
  read-only multiline text box in a dialog, to read by line, word and
  character. Escape closes it, back on the same message.
* **Mnemonics stay off the menu bar's letters.** Menus are File, View and
  Help, so no control on a page uses Alt+F, Alt+V or Alt+H (a panel mnemonic
  would take the letter away from the menu).
* **Refreshes do not move the reader.** The list is only rewritten where it
  changed, and the selection follows the same session, not the same row.
* **Announcements are spoken through the screen reader** (or a system
  voice) by the speech engine, never by moving focus, and also go to the
  status bar.
* **Claude waiting on you never pops up a dialog** (#187, #188). A
  permission request, question or plan is announced and the session shows
  as needing you; Ctrl+Shift+A opens it. A dialog appearing by itself would
  take keystrokes typed for the reply box, and a letter could press Allow.
  In each dialog Enter's default is the harmless choice (Deny, Send
  Answers, Keep Planning) and Escape answers later.
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import wx

from ..changes import by_file, summary_text
from ..codeblocks import find_code_blocks
from .. import (__version__, about_you, announce, attachments, bugreport, signin, export, hub, links,
               platform_paths, remote, usage, workplaces)
from ..claude_cli import (DEFAULT_PERMISSION_MODE, EFFORT_NOTE, EFFORTS, MODELS,
                          PERMISSION_MODES, PermissionRequest, ResumeRefused, TurnEvent,
                          TurnRunner, allow_response, answer_questions_response,
                          build_fork_command, build_new_command, build_resume_command,
                          SESSION_INJECTED_PREFIXES, STRIPPED_VARS, child_environment,
                          deny_response, fetch_commands, usable_commands,
                          describe_elapsed, effort_label, mode_label, model_label,
                          model_matches, model_spoken, new_session_id,
                          normalize_permission_mode)
from ..hub import (Snapshot, collect, fill_last_message, finished_turns,
                   forget_last_messages, last_reply_from_tail)
from ..message_links import find_links
from ..own_store import OwnSession, OwnSessionStore
from ..search import Target, search_sessions
from ..groups import GroupStore
from ..prompts import (MAX_NAME as PROMPT_NAME_MAX, PromptStore, clean_text as clean_prompt_text,
                       suggested_name)
from ..hidden import HiddenStore, ToolActivityStore
from ..titles import MAX_TITLE, TitleStore, clean_title
from ..sessions import (FIELD_ACTIVITY, FIELD_LAST_MESSAGE, FIELD_STARTED, GROUP_VIEW_PREFIX, IDLE,
                        DESKTOP, NEEDS_YOU, OWN, SORT_ORDERS,
                        SORT_SPOKEN, VIEW_ALL, VIEW_ARCHIVED, VIEW_HIDDEN, VIEW_NEEDS_YOU, VIEWS,
                        WORKING,
                        SessionInfo, field_short_name, group_view, in_view, view_spoken)
from ..speech import ANNOUNCE_FULL, NOTIFY_ALL, NOTIFY_OFF, SpeechSettings, default_options, list_speech_options, speaker
from ..transcript import (ASSISTANT, ERROR, PEER, PLAN, QUESTION, QUEUED, TOOL, TOOL_RESULT,
                          ChatMessage, TranscriptReader)
from ..updater import (AVAILABLE, FAILED, CheckResult, UpdateService, installed_notice,
                       release_notes_url)
from . import mac_a11y
from .a11y import set_accessible_name, set_list_items_accessible, set_voiceover_menu
from .notify import Notifier
from .statusbar import StatusParts
from ..rendering import html_page, message_page
from ..ui_text import markdown_as_text, shortcuts_html, update_item_label, update_item_spoken
from .dialogs import (ALLOW, ALLOW_SESSION, ID_PLAIN_TEXT, AboutYouDialog, ChangesDialog, CodeBlocksDialog, FindResultsDialog,
                      FormattedMessageDialog, LinksDialog, WrappingText,
                      BugReportDialog, CommandPickerDialog, MessageDialog, NewSessionDialog, PermissionDialog, PlanDialog,
                      ManageGroupsDialog, PromptsDialog, QuestionDialog, SessionColumnsDialog,
                      SettingsDialog, ShortcutsDialog, UpdateInstalledDialog, UsageDialog,
                      formatted_view_available, press_focused_button_on_a_mac)

APP_NAME = "The Chat Place"
LIST_REFRESH_MS = 5000
#: How often the window looks for a link another copy handed it (#144).
LINK_CHECK_MS = 1000
UPDATE_CHECK_DELAY_MS = 4000
#: The update-installed notice (#141) waits this long after the window is
#: made, then until the window is active with no dialog in front of it,
#: trying every UPDATE_NOTICE_RETRY_MS for up to UPDATE_NOTICE_TRIES times.
UPDATE_NOTICE_DELAY_MS = 1500
UPDATE_NOTICE_RETRY_MS = 1000
UPDATE_NOTICE_TRIES = 1800
APPLY_SPEECH_WAIT_S = 4.0
CHAT_REFRESH_MS = 2000
#: Tool calls and in-between text are gathered this long, then said together (#12).
ACTIVITY_DELAY_MS = 1200
#: How often a long Find in All Sessions says how far it's got (#109).
FIND_ALL_PROGRESS_MS = 10_000
#: How long results said but not yet looked at wait for Ctrl+Shift+S (#109).
FIND_ALL_KEEP_S = 600

_REPLY_KINDS = (ASSISTANT, QUESTION, PLAN, ERROR)


#: The read-only note where a desktop app session's reply box would be.
_DESKTOP_NOTE = (
    "This session belongs to the Claude desktop app, so you reply to it "
    "in Claude. The Chat Place only reads it: sending from here while the "
    "desktop app has it open could run two turns at once and tangle "
    "the conversation. Open in Claude switches the desktop app to it. "
    "Continue Here starts a Chat Place copy of it, with the whole "
    "conversation so far, that you can reply to here; the desktop app "
    "session isn't changed.")
#: The same for a Cowork session (#91), which can't be continued here.
_COWORK_NOTE = (
    "This is a Cowork session in the Claude desktop app, so you reply to it "
    "in Claude. The Chat Place only reads it. Open in Claude switches the "
    "desktop app to it. Cowork keeps its conversation inside the desktop app's "
    "own folders, so it can't be continued here.")
#: The same for a session started by typing claude in a terminal (#158).
_TERMINAL_NOTE = (
    "This session was started by typing claude in a terminal, so you reply to "
    "it there. The Chat Place only reads it: sending from here while the "
    "terminal has it open could run two turns at once and tangle the "
    "conversation. Continue Here starts a Chat Place copy of it, with the "
    "whole conversation so far, that you can reply to here; the terminal "
    "session isn't changed.")


def kind_name(info: SessionInfo) -> str:
    """What a session is, for "<title> is a terminal session: ...". """
    if info.is_own:
        return "a Chat Place session"
    if info.is_terminal:
        return "a terminal session"
    if info.cowork:
        return "a desktop app Cowork session"
    return "a desktop app session"


def claude_link(info: SessionInfo) -> str:
    """The desktop app's own link to a session: its Code page, or for a
    Cowork session (#91) the page the app's own notifications open."""
    page = "cowork" if info.cowork else "epitaxy"
    return f"claude://claude.ai/{page}/{info.desktop_session_id}"


#: The narrowest the lists go. Without it a list's minimum is its longest row
#: (see session_list in _build_ui).
LIST_MIN_WIDTH = 200
#: The messages list's line when there are no sessions to choose (#182).
#: Short enough to fit the list at the window's usual size, as a list's rows
#: don't wrap.
NO_SESSIONS_YET = ("No sessions yet. Start one with File, New Session ("
                   + ("Cmd+N" if wx.Platform == "__WXMAC__" else "Ctrl+N")
                   + "), in the Claude desktop app or in a terminal.")


class MainFrame(wx.Frame):
    def __init__(self, store: Optional[OwnSessionStore] = None,
                 updates: Optional[UpdateService] = None,
                 check_updates_at_start: bool = True):
        super().__init__(None, title=APP_NAME, size=_fitting_size(1000, 720))
        self.store = store or OwnSessionStore()
        self.groups = GroupStore()
        self.prompts = PromptStore()  # saved prompts (#131)
        self.hidden = HiddenStore()
        # The sessions Show Tool Activity is on for: it's each session's own (#162).
        self.tool_activity = ToolActivityStore()
        self.titles = TitleStore()  # names given to desktop app sessions (#93)
        self.updates = updates or UpdateService(__version__)
        self._update_busy = False
        # The newer version the last check found, "" if none (#141): Help's
        # update item says it.
        self._update_available = ""
        self._menu_open = False  # a menu is open: no dialog over it (#141)
        self.speech = SpeechSettings.load()
        self._speech_options = None
        threading.Thread(target=self._probe_speech, daemon=True).start()
        # A screen reader that's running but can't be reached is reported, not
        # covered by a Windows voice (#98).
        speaker.on_problem = lambda reason: wx.CallAfter(self._on_speech_problem, reason)

        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="hub")
        self._snapshot = Snapshot()
        self._snapshot_busy = False
        self._previous_states: Dict[str, str] = {}
        self._first_snapshot = True
        self._list_keys: List[str] = []
        #: What each row was last written from, apart from its age and last
        #: message, and that last message: so a refresh can tell when the
        #: row under the reader changed only in those (#146).
        self._row_basis: Dict[str, Tuple[str, str]] = {}
        self._runners: Dict[str, TurnRunner] = {}
        self._denials: Dict[str, List[str]] = {}
        #: What needed you (refusals, an error) in a reply said while the
        #: turn waited on background work (#161): not said again at the
        #: turn's end, but the session is still left needing you.
        self._said_problems: Dict[str, str] = {}
        self._pending_refresh: Optional[bool] = None
        # Set once the window is closing (_on_close). Its destruction waits
        # for idle time, so a menu VO+Shift+M scheduled just before Cmd+Q
        # would otherwise still open, on a hidden window that has stopped.
        self._closing = False
        self._claude_version = ""  # for bug reports; found in the background
        # Slash commands and skills per folder (#23), fetched in the background.
        self._commands: Dict[str, List[dict]] = {}  # by _folder_key
        self._commands_fetching: set = set()
        self._commands_waiting: Dict[str, list] = {}
        self._last_announcement = ""

        # Session view state.
        self._open: Optional[SessionInfo] = None
        self._open_generation = 0
        self._reader: Optional[TranscriptReader] = None
        self._reader_busy = False
        self._chat_messages: List[ChatMessage] = []
        self._chat_loaded = False
        self._show_activity = False
        self._drafts: Dict[str, str] = {}  # unsent reply text, per session
        # Sent during a turn, in order; they go together when it ends (#50).
        self._queued: Dict[str, List[str]] = {}
        self._attachments: Dict[str, List[str]] = {}  # per session, for its next message
        # Search (#21): text the session list is filtered by, and the last
        # text looked for in the messages.
        self._session_filter = ""
        self._find_text = ""
        # Find in All Sessions (#109): the last text, whether a search is
        # running, and the message to select once a session it chose loads.
        self._find_all_text = ""
        self._find_all_busy = False
        self._find_all_cancel = False
        self._find_all_done = 0
        self._find_all_focus = None
        # Results said but not yet shown: (when, results). They're kept for
        # FIND_ALL_KEEP_S, and dropped when the list's view changes.
        self._find_all_waiting = None
        self._find_all_run = 0
        self._find_all_timer = None
        self._select_on_load = None  # a search result, until its session loads
        # Usage limits and context (#19): the latest limits any turn reported
        # (they're the account's), each own session's context window, and
        # what's been warned about already, so it's said once.
        self._limits: Optional[dict] = None
        self._windows: Dict[str, int] = {}  # by session id
        self._model_windows: Dict[str, int] = {}  # by model, from any turn of ours
        self._warned: set = set()
        # The loaded session's turn ended: say what it changed once its
        # transcript has been read (#18). Its file changes and turn count,
        # copied with its messages, and how many changes have been said.
        self._changes_due = False
        self._chat_edits: list = []
        self._chat_turns = 0
        self._edits_said = 0
        self._notifier = Notifier(self._go_to_session, APP_NAME)
        # What Claude is waiting for you to answer, per session, oldest first
        # (#187, #188). The turn is paused until each is answered.
        self._pending: Dict[str, List[PermissionRequest]] = {}
        self._chat_keys: List[str] = []
        self._announce_load = False  # say "Loaded X" once its chat arrives
        # Activity in the open session waiting to be said (#12).
        self._activity: List[tuple] = []
        self._activity_timer = None
        # A thechatplace:// link to open once the session list has been read
        # and no dialog is in front (#144), and whether that wait was said.
        self._pending_link: Optional[str] = None
        self._link_wait_said = False
        self._link_came_forward = False  # once per link, never every tick

        self._build_menu()
        self._build_ui()
        self._build_status_bar()
        icon = platform_paths.app_icon_path()
        if icon.is_file():
            self.SetIcons(wx.IconBundle(str(icon)))  # title bar and Alt+Tab (#64)
        self._status("Loading sessions…")
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_ACTIVATE, self._on_activate)
        self.Bind(wx.EVT_MENU_OPEN, lambda e: self._on_menu_open_close(e, True))
        self.Bind(wx.EVT_MENU_CLOSE, lambda e: self._on_menu_open_close(e, False))

        self._list_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.refresh_sessions(), self._list_timer)
        self._list_timer.Start(LIST_REFRESH_MS)
        self._chat_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_chat_timer, self._chat_timer)
        # Links another copy of The Chat Place handed over (#144).
        # The timer is its own event handler, not the window: it ticks every
        # second, so a tick already queued when the window is destroyed (a
        # Mac destroys it later, at idle time) must never be delivered to a
        # window that's gone. It stops itself once the window is.
        link_timer = wx.Timer()

        def link_tick(_event):
            if self._gone():
                link_timer.Stop()
                return
            self._check_link_inbox()
        link_timer.Bind(wx.EVT_TIMER, link_tick)
        self._link_timer = link_timer
        self._link_timer.Start(LINK_CHECK_MS)

        if self.store.load_error:
            wx.CallAfter(wx.MessageBox, self.store.load_error, APP_NAME,
                         wx.OK | wx.ICON_WARNING, self)
        if self.groups.load_error:
            wx.CallAfter(wx.MessageBox, self.groups.load_error, APP_NAME,
                         wx.OK | wx.ICON_WARNING, self)
        if self.hidden.load_error:
            wx.CallAfter(wx.MessageBox, self.hidden.load_error, APP_NAME,
                         wx.OK | wx.ICON_WARNING, self)
        if self.tool_activity.load_error:
            wx.CallAfter(wx.MessageBox, self.tool_activity.load_error, APP_NAME,
                         wx.OK | wx.ICON_WARNING, self)
        if self.titles.load_error:
            wx.CallAfter(wx.MessageBox, self.titles.load_error, APP_NAME,
                         wx.OK | wx.ICON_WARNING, self)
        if self.prompts.load_error:
            wx.CallAfter(wx.MessageBox, self.prompts.load_error, APP_NAME,
                         wx.OK | wx.ICON_WARNING, self)
        self.refresh_sessions()
        self._check_claude_version()
        # After the session list has been read, like the update check.
        self._startup_sign_in = wx.CallLater(UPDATE_CHECK_DELAY_MS, self._check_sign_in,
                                             manual=False)
        self._pool.submit(attachments.remove_old_pastes)  # pasted pictures over 30 days old
        self.session_list.SetFocus()
        if check_updates_at_start:
            # A few seconds in, so the list is read first.
            self._startup_update_check = wx.CallLater(UPDATE_CHECK_DELAY_MS,
                                                      self.check_for_updates, False)
            # The first start after an update says so (#141), once the
            # window is up.
            self._startup_update_notice = wx.CallLater(UPDATE_NOTICE_DELAY_MS,
                                                       self.check_update_installed)

    # ------------------------------------------------------------------ menus

    def _build_menu(self):
        bar = wx.MenuBar()
        session = wx.Menu()
        # Enter is handled on the list itself (a menu accelerator for Enter
        # would steal it from every button and text box in the window).
        self._item(session, "&Load Session", self.on_open_session)
        self._item(session, "Open in &Claude\tCtrl+O", self.on_open_in_claude)
        self._item(session, "Cop&y Session Link...\tCtrl+Shift+L",
                   lambda e: self.on_copy_session_link())
        self._item(session, "&Answer Claude...\tCtrl+Shift+A", lambda e: self.on_answer())
        self._item(session, "Con&tinue Here...\tCtrl+Shift+N", self.on_continue_here)
        self._item(session, "&New Session...\tCtrl+N", self.on_new_session)
        self._item(session, "Change Mo&del...", lambda e: self.on_change_model())
        # Every letter of "Change Permission Mode" is another item's access
        # key already, so, as with Other Machines, its shortcut is the way in.
        self._item(session, "Change Permission Mode...\tCtrl+Shift+O",
                   lambda e: self.on_change_permission_mode())
        self._item(session, "Change E&ffort...", lambda e: self.on_change_effort())
        self._item(session, "Rem&ote Control...", lambda e: self.on_remote_control())
        # As with Rename Session, every letter is taken, so the key is its shortcut.
        self._item(session, "Other Machines...\tCtrl+Shift+M",
                   lambda e: self.on_other_machines())
        self._item(session, "&Refresh\tF5",
                   lambda e: self.refresh_sessions(force=True, resort=True))
        # Delete and Shift+Delete belong to the session list (its char hook):
        # as menu accelerators they were window-wide, so Delete in the
        # attachments list hid the loaded session instead of removing the file.
        # The keys are in the labels, without a tab, so they're still read out.
        # Every letter of "Rename Session" is another item's access key
        # already, so F2 (as in Explorer) is its only shortcut (#93).
        self._item(session, "Rename Session...\tF2", lambda e: self.on_rename())
        self._item(session, "H&ide Session (Delete)", lambda e: self.on_hide())
        self._item(session, "&Bring Back Session", lambda e: self.on_unhide())
        self._item(session, "Delete Session &Permanently (Shift+Delete)...",
                   lambda e: self.on_delete_permanently())
        self._item(session, "&Export Session...\tCtrl+E", lambda e: self.on_export())
        self._item(session, "Insert Command or S&kill...\tCtrl+/",
                   lambda e: self.on_insert_command())
        self._item(session, "Attac&h Files...\tCtrl+Shift+F", lambda e: self.on_attach_files())
        # Saved prompts (#131). Every free letter would be an odd one, so,
        # as with Other Machines, the keys are the way in. Ctrl+Shift+Enter
        # belongs to the reply box (a menu accelerator for Enter would be
        # window-wide), so it's in the label without a tab, still read out.
        self._item(session, "Prompts...\tCtrl+Shift+P", lambda e: self.on_prompts())
        # wx turns Ctrl into Cmd only in a tab accelerator, so say it here.
        keys = "Cmd+Shift+Return" if wx.Platform == "__WXMAC__" else "Ctrl+Shift+Enter"
        self._item(session, f"Send and Save as Prompt... ({keys})",
                   lambda e: self.on_send_and_save())
        session.AppendSeparator()
        self._item(session, "Add to &Group...\tCtrl+G", lambda e: self.on_add_to_group())
        self._item(session, "Remove from Gro&up...", lambda e: self.on_remove_from_group())
        self._item(session, "&Manage Groups...", lambda e: self.on_manage_groups())
        session.AppendSeparator()
        self._item(session, "&Settings...\tCtrl+,", self.on_settings, wx.ID_PREFERENCES)
        session.AppendSeparator()
        self._item(session, "E&xit", lambda e: self.Close(), wx.ID_EXIT)
        bar.Append(session, "&File")

        view = wx.Menu()
        self._item(view, "Go to &Sessions\tCtrl+1", lambda e: self.focus_sessions())
        self._item(view, "Go to &Messages\tCtrl+2", lambda e: self.focus_messages())
        self._item(view, "Go to &Reply\tCtrl+3", lambda e: self.focus_reply())
        self._item(view, "Go to Status &Bar\tCtrl+9", lambda e: self.focus_status())
        # Radio items: a screen reader says which order is checked.
        sort_menu = wx.Menu()
        self.sort_items = {}
        for order, label in SORT_ORDERS:
            item = sort_menu.AppendRadioItem(wx.ID_ANY, label)
            item.Check(order == self.speech.session_order)
            self.Bind(wx.EVT_MENU, lambda e, o=order: self.on_sort(o), item)
            self.sort_items[order] = item
        view.AppendSubMenu(sort_menu, "S&ort Sessions")
        # Which sessions to list (#32): radio items, groups at the end (#31).
        self.show_menu = wx.Menu()
        self.view_items = {}
        view.AppendSubMenu(self.show_menu, "S&how Sessions")
        self._build_show_menu()
        # What each row says, and in what order (#134).
        self._item(view, "S&ession List Columns...", lambda e: self.on_session_columns())
        view.AppendSeparator()
        self._item(view, "Read &Full Message", lambda e: self.on_read_message())
        self._item(view, "F&ind...\tCtrl+F", lambda e: self.on_find())
        self._item(view, "Find &Next\tF3", lambda e: self.find_again(True))
        self._item(view, "Find Pre&vious\tShift+F3", lambda e: self.find_again(False))
        # Every letter of "Links" is another item's access key already, so
        # its shortcut is the way in (#190).
        self._item(view, "Links...\tCtrl+L", lambda e: self.on_links())
        self._item(view, "Find in &All Sessions...\tCtrl+Shift+S", lambda e: self.on_find_all())
        self.activity_item = view.AppendCheckItem(wx.ID_ANY, "Show &Tool Activity\tCtrl+T")
        self.Bind(wx.EVT_MENU, self.on_toggle_activity_menu, self.activity_item)
        self._item(view, "Sto&p Running Turn\tCtrl+.", self.on_stop)
        self._item(view, "T&urn Status\tCtrl+Shift+T", self.on_turn_status)
        self._item(view, "Usage and &Context...\tCtrl+Shift+U", lambda e: self.on_usage())
        self._item(view, "Change&d Files...\tCtrl+Shift+D", lambda e: self.on_changes())
        self._item(view, "Repeat &Last Announcement\tCtrl+Shift+R",
                   lambda e: self._say(self._last_announcement, force=True))
        view.AppendSeparator()
        self._item(view, "What Claude &Knows About You...\tCtrl+Shift+K",
                   lambda e: self.on_about_you())
        bar.Append(view, "&View")

        help_menu = wx.Menu()
        self._item(help_menu, "User &Guide", self.on_user_guide)
        self._item(help_menu, "&Keyboard Shortcuts\tF1", self.on_shortcuts)
        # Its label says the version running, or the update a check found (#141).
        self.update_item = self._item(help_menu, update_item_label(__version__),
                                      lambda e: self.check_for_updates(True))
        self._item(help_menu, "Claude Code &Sign-in...", lambda e: self.on_sign_in())
        self._item(help_menu, "Report a &Bug...", lambda e: self.on_report_bug())
        self._item(help_menu, "&About", self.on_about, wx.ID_ABOUT)
        bar.Append(help_menu, "&Help")
        self.SetMenuBar(bar)

    def _item(self, menu, label, handler, item_id=wx.ID_ANY):
        item = menu.Append(item_id, label)
        self.Bind(wx.EVT_MENU, handler, item)
        return item

    # --------------------------------------------------------------------- UI

    def _build_ui(self):
        """Sessions on the left, the loaded session on the right. Tab order is
        creation order: sessions list, messages, reply (or the desktop note),
        Send and Stop, then the rest."""
        root = wx.Panel(self)
        outer = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        self.sessions_label = wx.StaticText(root, label="Session &list:")
        left.Add(self.sessions_label, 0, wx.LEFT | wx.TOP, 8)
        self.session_list = wx.ListBox(root, style=wx.LB_SINGLE, name="Session list")
        # A list box's best width is its longest row, and the sizer won't go
        # below it. On a Mac, real session titles made the session list take
        # nearly the whole window and squeezed the reply box to one point
        # wide, so VoiceOver read one letter per line. Fixed minimums let the
        # lists share the width by proportion instead.
        self.session_list.SetMinSize((LIST_MIN_WIDTH, -1))
        set_accessible_name(self.session_list, "Session list")
        left.Add(self.session_list, 1, wx.EXPAND | wx.ALL, 8)
        self.session_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_open_session)
        self.session_list.Bind(wx.EVT_CONTEXT_MENU, self._on_session_menu)
        set_voiceover_menu(self.session_list, self._on_session_menu)
        outer.Add(left, 2, wx.EXPAND)

        vsizer = wx.BoxSizer(wx.VERTICAL)
        # The heading wraps, and the messages label (which repeats the
        # session's title) ends in "..." when it doesn't fit: as single lines
        # both ran past the window's edge with a long title (#176). Screen
        # readers read both in full.
        self.session_heading = WrappingText(root)
        vsizer.Add(self.session_heading, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 8)

        self.messages_label = wx.StaticText(root, label="&Messages:",
                                            style=wx.ST_ELLIPSIZE_END | wx.ST_NO_AUTORESIZE)
        self.messages_label.SetMinSize((1, -1))
        vsizer.Add(self.messages_label, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 8)
        self.chat_list = wx.ListBox(root, style=wx.LB_SINGLE, name="Messages")
        self.chat_list.SetMinSize((LIST_MIN_WIDTH, -1))  # see session_list
        set_accessible_name(self.chat_list, "Messages")
        # Each row shows the first line; the screen reader reads it whole (#11).
        set_list_items_accessible(self.chat_list, self.chat_list.GetName,
                                  self._message_item_text)
        self._spoken: Dict[str, tuple] = {}  # message key -> (text, spoken words)
        vsizer.Add(self.chat_list, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.chat_list.Bind(wx.EVT_CONTEXT_MENU, self._on_message_menu)
        set_voiceover_menu(self.chat_list, self._on_message_menu)
        self.chat_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_read_message())

        # Where the reply goes: one panel for The Chat Place's sessions, one for
        # desktop ones, in the same place so the layout is the same.
        self.own_reply = wx.Panel(root)
        osizer = wx.BoxSizer(wx.VERTICAL)
        osizer.Add(wx.StaticText(self.own_reply,
                                 label="&Your message (Ctrl+Enter sends):"), 0, wx.BOTTOM, 4)
        # TE_RICH2 on purpose: checked in the vmtest VM (wxPython 4.3.1), a
        # plain multiline EDIT reports its contents as its accessible name,
        # while a RichEdit takes its name from the label before it.
        self.reply_text = wx.TextCtrl(self.own_reply, style=wx.TE_MULTILINE | wx.TE_RICH2)
        set_accessible_name(self.reply_text, "Your message")
        self.reply_text.SetMinSize((-1, 120))
        osizer.Add(self.reply_text, 1, wx.EXPAND)
        orow = wx.BoxSizer(wx.HORIZONTAL)
        self.send_btn = wx.Button(self.own_reply, label="Sen&d")
        self.stop_btn = wx.Button(self.own_reply, label="Sto&p")
        # After Stop, so Tab from the reply box is still Send, then Stop (#175).
        self.commands_btn = wx.Button(self.own_reply, label="C&ommands...")
        # Alt+T: Alt+A is Show tool activity, and F, V, H are the menus'.
        self.attach_btn = wx.Button(self.own_reply, label="A&ttach Files...")
        orow.Add(self.send_btn, 0, wx.RIGHT, 6)
        orow.Add(self.stop_btn, 0, wx.RIGHT, 6)
        orow.Add(self.commands_btn, 0, wx.RIGHT, 6)
        orow.Add(self.attach_btn, 0, wx.RIGHT, 12)
        self.turn_status = wx.StaticText(self.own_reply, label="")
        orow.Add(self.turn_status, 1, wx.ALIGN_CENTER_VERTICAL)
        osizer.Add(orow, 0, wx.EXPAND | wx.TOP, 6)
        self.own_reply.SetSizer(osizer)
        self.send_btn.Bind(wx.EVT_BUTTON, self.on_send)
        self.stop_btn.Bind(wx.EVT_BUTTON, self.on_stop)
        self.commands_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_insert_command())
        self.attach_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_attach_files())
        # What goes with the next message (#22): shown only when there's
        # something; Delete removes the selected one.
        self.attach_list = wx.ListBox(self.own_reply, style=wx.LB_SINGLE)
        set_accessible_name(self.attach_list, "Attachments, Delete removes one")
        self.attach_list.SetMinSize((LIST_MIN_WIDTH, 48))  # see session_list
        osizer.Add(self.attach_list, 0, wx.EXPAND | wx.TOP, 6)
        self.attach_list.Hide()
        self.attach_list.Bind(wx.EVT_KEY_DOWN, self._on_attach_key)
        self.reply_text.Bind(wx.EVT_TEXT_PASTE, self._on_reply_paste)

        self.desktop_reply = wx.Panel(root)
        dsizer = wx.BoxSizer(wx.VERTICAL)
        # A read-only text box, not a static label: it is focusable, so the
        # explanation is what's heard on tabbing to where the reply box would be.
        dsizer.Add(wx.StaticText(self.desktop_reply, label="About replying:"), 0, wx.BOTTOM, 4)
        self.desktop_note = wx.TextCtrl(
            self.desktop_reply, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
            value=_DESKTOP_NOTE)
        set_accessible_name(self.desktop_note, "About replying")
        self.desktop_note.SetMinSize((-1, 120))
        dsizer.Add(self.desktop_note, 1, wx.EXPAND)
        drow = wx.BoxSizer(wx.HORIZONTAL)
        self.reply_claude_btn = wx.Button(self.desktop_reply, label="Open in &Claude")
        drow.Add(self.reply_claude_btn, 0, wx.RIGHT, 6)
        self.continue_btn = wx.Button(self.desktop_reply, label="Con&tinue Here...")
        drow.Add(self.continue_btn, 0)
        dsizer.Add(drow, 0, wx.TOP, 6)
        self.desktop_reply.SetSizer(dsizer)
        self.reply_claude_btn.Bind(wx.EVT_BUTTON, self.on_open_in_claude)
        self.continue_btn.Bind(wx.EVT_BUTTON, self.on_continue_here)

        # The reply area takes a third of the height and grows with the
        # window: held at its minimum, the reply box was about four lines
        # however big the window was.
        vsizer.Add(self.own_reply, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        vsizer.Add(self.desktop_reply, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        crow = wx.BoxSizer(wx.HORIZONTAL)
        self.activity_check = wx.CheckBox(root, label="Show tool &activity")
        crow.Add(self.activity_check, 0, wx.ALIGN_CENTER_VERTICAL)
        vsizer.Add(crow, 0, wx.ALL, 8)
        self.activity_check.Bind(wx.EVT_CHECKBOX, self.on_toggle_activity_check)
        self.session_view = root

        # Session-list commands come last in the Tab order; all of them are
        # also on the File menu with shortcuts.
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.new_btn = new_btn = wx.Button(root, label="&New Session...")
        self.refresh_btn = refresh_btn = wx.Button(root, label="&Refresh")
        row.Add(new_btn, 0, wx.RIGHT, 6)
        row.Add(refresh_btn, 0)
        vsizer.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        new_btn.Bind(wx.EVT_BUTTON, self.on_new_session)
        refresh_btn.Bind(wx.EVT_BUTTON,
                         lambda e: self.refresh_sessions(force=True, resort=True))

        outer.Add(vsizer, 3, wx.EXPAND)
        root.SetSizer(outer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(root, 1, wx.EXPAND)
        self.SetSizer(frame_sizer)
        self._show_no_session()

    # ------------------------------------------------------------ helpers

    def _modal(self, dialog):
        try:
            return dialog.ShowModal()
        finally:
            dialog.Destroy()

    def _build_status_bar(self):
        """The status bar, reachable with F6 and Ctrl+9 (#10), in parts as
        in QuickMail: what just happened, the loaded session, and buttons for
        what you can act on (how full the context is, sessions that need
        you, an update). Left and Right move between them; Tab leaves.
        Information is read-only text with no caret; actions are buttons.
        Being children of the status bar, not of the window's panel, none of
        them is in the Tab order."""
        bar = self.CreateStatusBar(1)
        self.status_parts = StatusParts(bar)
        self.status_text = self.status_parts.add_text("message", "Status", -3,
                                                      empty_text="Ready")
        self.status_session = self.status_parts.add_text("session", "Loaded session", -2,
                                                         empty_text="No session loaded")
        self.status_parts.add_button("context", 150, self.on_usage)
        self.status_parts.add_button("needs_you", 170, self._go_to_needs_you)
        self.status_parts.add_button("update", 190, lambda: self.check_for_updates(True))
        self._status_latest = ""

    def _status(self, text: str):
        text = announce.status_text(text)
        self._status_latest = text
        self.status_parts.set("message", text)

    def _update_status_session(self):
        """The loaded session's part: its name and what it's doing."""
        info = self._open
        if info is None:
            text = ""
        elif info.is_own and info.cli_session_id in self._runners:
            text = f"{info.title}: {self.turn_status.GetLabel()}"
        else:
            state = info.state + (f", {info.detail}" if info.detail else "")
            text = f"{info.title}: {state}"
        self.status_parts.set("session", text)

    def _update_status_context(self):
        tokens, window = self._context(self._open)
        text = ""
        if self._open is not None and tokens and window:
            text = f"Context {round(100 * usage.context_share(tokens, window))}% full"
        self.status_parts.set("context", text)

    def _update_status_needs_you(self):
        count = sum(1 for s in self._snapshot.sessions
                    if s.state == NEEDS_YOU and not s.archived and not s.hidden)
        text = "" if not count else (
            "1 session needs you" if count == 1 else f"{count} sessions need you")
        self.status_parts.set("needs_you", text)

    def _go_to_needs_you(self):
        """The status bar's "needs you" button: the first session that needs
        you, selected in the session list (shown there if the view hid it)."""
        waiting = [s for s in self._snapshot.sessions
                   if s.state == NEEDS_YOU and not s.archived and not s.hidden]
        if not waiting:
            self._feedback("No session needs you.")
            return
        key = next((k for k in self._list_keys if k in {s.key for s in waiting}), None)
        if key is None:
            if self._session_filter:
                self._set_session_filter("")
            self.on_view(VIEW_NEEDS_YOU)
            key = next((k for k in self._list_keys if k in {s.key for s in waiting}), None)
        if key is not None:
            self.session_list.SetSelection(self._list_keys.index(key))
        self.focus_sessions()

    def _say(self, text: Optional[str], force: bool = False):
        """Speak an announcement (per the level) and put it in the status bar."""
        if not text:
            return
        self._last_announcement = text
        self._status(text)
        if force or self.speech.enabled:
            speaker.speak(text, self.speech)

    def _feedback(self, text: str):
        """The answer to something Kelly just did: status bar, and spoken
        briefly without interrupting the screen reader (unless announcements
        are set to silent). Background news uses ``_say`` or ``_status``."""
        if not text:
            return
        self._status(text)
        if self.speech.enabled:
            speaker.speak(text, self.speech, interrupt=False)

    def _store_write(self, method, *args, **kwargs) -> bool:
        """Call a store method that writes; report a failed write instead of
        letting it escape an event handler (which would leave the UI half
        updated)."""
        try:
            method(*args, **kwargs)
            return True
        except OSError as exc:
            self._say(f"Couldn't save The Chat Place's session list: {exc}")
            return False

    def _on_speech_problem(self, reason: str):
        """Say why the screen reader didn't speak. The speaker calls this once
        per problem, until speech works again.

        Not spoken: the screen reader is what can't be reached, and a Windows
        voice over it is the #98 defect. So it goes beside the announcement in
        the status bar (which still has the announcement in it), and to one
        Windows notification, which screen readers read from their own
        notification handling even when they can't be reached this way.
        """
        if self._gone():
            return  # the window closed before this ran
        message = f"Speech: {reason[0].upper()}{reason[1:]}."
        # Beside whatever the status bar says now, which is what was just
        # spoken (an announcement or a confirmation), not an older one.
        current = self._status_latest
        self._status(f"{current} ({message})" if current else message)
        if self.speech.notifications != NOTIFY_OFF:
            self._notifier.show("Announcements aren't being spoken",
                                f"{reason[0].upper()}{reason[1:]}.", None)

    def _probe_speech(self):
        try:
            self._speech_options = list_speech_options()
        except Exception:  # noqa: BLE001
            self._speech_options = None

    def _selected_session(self) -> Optional[SessionInfo]:
        """The session a command means: the highlighted one in the sessions
        list while that has focus (or nothing is loaded), otherwise the
        loaded one."""
        if self._open is not None and wx.Window.FindFocus() is not self.session_list:
            return self._current_info(self._open.key) or self._open
        index = self.session_list.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self._list_keys):
            return None
        return self._current_info(self._list_keys[index])

    def _current_info(self, key: str) -> Optional[SessionInfo]:
        for info in self._snapshot.sessions:
            if info.key == key:
                return info
        return None

    # ----------------------------------------------------------- session list

    def refresh_sessions(self, force: bool = False, resort: bool = False):
        """Reload the list in the background.

        ``force`` reports the totals; ``resort`` puts the list back in its
        proper order even while it has focus (F5, and coming back from a
        session). Otherwise a list with focus keeps its order, so nothing moves
        under Kelly while he arrows.
        """
        if self._snapshot_busy:
            # Run again when the current pass lands, so F5 is never lost.
            previous = self._pending_refresh or (False, False)
            self._pending_refresh = (force or previous[0], resort or previous[1])
            return
        self._snapshot_busy = True
        own = [OwnSession(**vars(s)) for s in self.store.all()]
        running = set(self._runners)
        background = {sid for sid, runner in self._runners.items()
                      if getattr(runner, "waiting_on_background", False)}
        waiting = self._waiting()
        order = self.speech.session_order
        # Each transcript's end is read only while the column is shown (#146).
        last_messages = FIELD_LAST_MESSAGE in self.speech.session_fields

        def work():
            try:
                snap = collect(own, running, waiting=waiting, order=order,
                               last_messages=last_messages, background=background)
                ended = finished_turns(self._previous_states, snap.sessions)
                replies = {}
                if not self._first_snapshot and self.speech.announce_all_sessions:
                    for info in ended:
                        if info.is_own or not info.cli_session_id:
                            continue  # own sessions announce from their own turn
                        path = info.transcript_path()
                        replies[info.key] = last_reply_from_tail(path) if path else ""
                wx.CallAfter(self._apply_snapshot, snap, ended, replies, force, resort)
            except Exception as exc:  # noqa: BLE001
                wx.CallAfter(self._snapshot_failed, exc)

        self._pool.submit(work)

    def _snapshot_failed(self, exc):
        self._snapshot_busy = False
        self._status(f"Couldn't read the session list: {exc}")
        self._run_pending_refresh()

    def _run_pending_refresh(self):
        if self._pending_refresh is not None:
            (force, resort), self._pending_refresh = self._pending_refresh, None
            self.refresh_sessions(force=force, resort=resort)

    def _apply_snapshot(self, snap: Snapshot, ended, replies, force, resort=False):
        self._snapshot_busy = False
        if self._gone():
            return
        previous_desktop_groups = self._snapshot.desktop_groups
        self._snapshot = snap
        self._previous_states = {s.key: s.state for s in snap.sessions}
        # Hidden sessions (File, Hide Session) are out of everything here:
        # counts, announcements and notifications as well as the list.
        for info in snap.sessions:
            info.hidden = info.key in self.hidden
            if not info.is_own and info.key in self.titles:
                info.title = self.titles.get(info.key)  # your name for it (#93)
            elif info.is_own:
                # From the store as it is now: a read that began before a
                # rename would otherwise bring the old name back.
                own = self.store.get(info.cli_session_id)
                if own is not None:
                    info.title = own.title
                    # On Remote Control (#96): in that view, and its row says so.
                    info.remote = self._remote_shown(own)
        ended = [info for info in ended if info.key not in self.hidden]
        if not snap.desktop_groups.read_ok:
            snap.desktop_groups = previous_desktop_groups  # keep the last good read
        if self._group_names() != getattr(self, "_menu_group_names", None):
            self._build_show_menu()  # the desktop app's groups changed
        if self._first_snapshot and snap.desktop_groups.read_ok:
            gone = self._view_group_missing()
            if gone is not None:
                # Deleted while The Chat Place was closed: say so, show all.
                self.speech.session_view = VIEW_ALL
                try:
                    self.speech.save()
                except OSError:
                    pass
                self._build_show_menu()
                self._status(f"Group {gone} is gone; showing all sessions.")
        self._update_status_needs_you()
        first = self._first_snapshot
        if not first:
            for info in ended:
                if info.is_own:
                    continue  # its own turn says so, with more to say
                if info.state == NEEDS_YOU:
                    self._notify(info.key, f"{info.title} needs you",
                                 info.detail or "Claude is waiting for you.", needs_you=True)
                elif self._open is not None and info.key == self._open.key:
                    self._notify(info.key, f"{info.title} finished",
                                 replies.get(info.key, "") or "Claude finished its turn.")
        self._first_snapshot = False
        keep_order = (not resort and not first
                      and wx.Window.FindFocus() is self.session_list)
        shown = self._in_current_view(snap.sessions)
        if keep_order:
            # A session leaving the view (it stopped needing you) stays while
            # you're on it, so the row under you doesn't change; F5 or leaving
            # the list puts the view right.
            index = self.session_list.GetSelection()
            if 0 <= index < len(self._list_keys):
                selected = self._list_keys[index]
                if selected not in {s.key for s in shown}:
                    shown += [s for s in snap.sessions if s.key == selected]
        self._update_session_list(shown, keep_order=keep_order)
        self._update_list_label(len(shown))

        if not first and self._open is not None and not self._open.is_own and any(
                info.key == self._open.key for info in ended):
            # The loaded desktop session announces its own messages, and
            # then what the turn changed.
            self._changes_due = True
            self._refresh_chat()
        if not first and self.speech.announce_all_sessions:
            for info in ended:
                if info.is_own:
                    continue
                if self._open is not None and info.key == self._open.key:
                    continue
                text = announce.turn_end_text(info.title, info.state, info.detail,
                                              replies.get(info.key, ""), self.speech.announce)
                if text:
                    self._say(text)
                else:
                    self._status(f"{info.title} finished.")

        if self._open is not None:
            current = self._current_info(self._open.key)
            if current is not None:
                self._open = current
                self._update_heading()
        if first or force:
            current = [s for s in snap.sessions if not s.archived and not s.hidden]
            waiting = sum(1 for s in current if s.state == NEEDS_YOU)
            working = sum(1 for s in current if s.state == WORKING)
            text = (f"{len(current)} sessions: {waiting} need you, "
                    f"{working} working.")
            if self.speech.session_view != VIEW_ALL or self._session_filter:
                what = view_spoken(self.speech.session_view) \
                    if self.speech.session_view != VIEW_ALL else "sessions"
                if self._session_filter:
                    what += f' matching "{self._session_filter}"'
                text += f" Showing {what}: {len(shown)}."
            if snap.unreadable_files:
                text += f" Couldn't read {snap.unreadable_files} session files."
            if force and not first:
                self._feedback(text)  # F5: Kelly asked
            else:
                self._status(text)
        if first and self._pending_link is not None:
            wx.CallAfter(self._open_pending_link)  # a link it was started with (#144)
        self._run_pending_refresh()

    def _update_session_list(self, sessions: List[SessionInfo], keep_order: bool = False,
                             rewrite: bool = False):
        """Rewrite only what changed, keeping the selection on the same session.

        With ``keep_order`` the rows stay where they are (new sessions are
        added at the end, vanished ones removed), so a refresh never moves the
        row under the reader. The proper order comes back on F5, on returning
        from a session, or on a refresh while the list doesn't have focus.

        ``rewrite`` rewrites the selected row even when only its age differs:
        the columns changed (#134), so moving or removing Last activity isn't
        a clock tick.

        The selected row isn't rewritten for a clock tick, nor for a new last
        message (#146) while its session is working: rewriting it makes a
        screen reader read it again, and Claude can write several messages a
        turn. A change of status rewrites it, as before, bringing the last
        message with it; so does a new last message in a session that isn't
        working (its turn ended between two refreshes).
        """
        now = int(time.time() * 1000)
        by_key = {s.key: s for s in sessions}
        fields = self.speech.session_fields
        # Columns a clock moves: a row isn't rewritten (and read again) for them.
        steady_fields = [f for f in fields
                         if f not in (FIELD_ACTIVITY, FIELD_STARTED, FIELD_LAST_MESSAGE)]

        def basis(info: SessionInfo) -> Tuple[str, str]:
            message = info.last_message if FIELD_LAST_MESSAGE in fields else ""
            return info.list_line(now, steady_fields), message

        def quiet(key: str) -> bool:
            """Leave the selected row as it is: only quiet parts changed."""
            if rewrite or key != selected_key:
                return False
            before = self._row_basis.get(key)
            if before is None:
                return False  # not known what it was written from: write it
            steady, message = basis(by_key[key])
            return before[0] == steady and (before[1] == message
                                            or by_key[key].state == WORKING)

        def wrote(key: str):
            self._row_basis[key] = basis(by_key[key])
        index = self.session_list.GetSelection()
        selected_key = (self._list_keys[index]
                        if index != wx.NOT_FOUND and index < len(self._list_keys) else None)

        if keep_order:
            keys = [k for k in self._list_keys if k in by_key]
            keys += [s.key for s in sessions if s.key not in set(self._list_keys)]
        else:
            keys = [s.key for s in sessions]
        lines = [by_key[k].list_line(now, fields) for k in keys]
        self._row_basis = {k: v for k, v in self._row_basis.items() if k in by_key}

        if keys == self._list_keys:
            for i, line in enumerate(lines):
                if self.session_list.GetString(i) == line:
                    wrote(keys[i])
                    continue
                if quiet(keys[i]):
                    continue  # don't make the reader re-read for a clock tick
                self.session_list.SetString(i, line)
                wrote(keys[i])
            self._sync_no_session_line()  # the first, empty, list lands here
            return

        if keep_order:
            # Remove vanished rows from the bottom up, then update and append.
            for i in range(len(self._list_keys) - 1, -1, -1):
                if self._list_keys[i] not in by_key:
                    self.session_list.Delete(i)
            kept = [k for k in self._list_keys if k in by_key]
            for i, key in enumerate(kept):
                line = lines[i]
                if self.session_list.GetString(i) == line:
                    wrote(key)
                elif not quiet(key):
                    self.session_list.SetString(i, line)
                    wrote(key)
            if len(keys) > len(kept):
                self.session_list.Append(lines[len(kept):])
                for key in keys[len(kept):]:
                    wrote(key)
            self._list_keys = keys
            self._sync_no_session_line()
            if selected_key in keys:
                if self.session_list.GetSelection() != keys.index(selected_key):
                    self.session_list.SetSelection(keys.index(selected_key))
            elif keys:
                old = self._list_keys_before_delete(index, len(keys))
                self.session_list.SetSelection(old)
            return

        self.session_list.Set(lines)
        self._list_keys = keys
        for key in keys:
            wrote(key)
        self._sync_no_session_line()
        if not lines:
            return
        if selected_key in keys:
            self.session_list.SetSelection(keys.index(selected_key))
        else:
            self.session_list.SetSelection(min(max(index, 0), len(keys) - 1))

    @staticmethod
    def _list_keys_before_delete(index: int, count: int) -> int:
        """Where the selection goes when its row vanished: the row that took
        its place, or the new last row."""
        return min(max(index, 0), count - 1)

    def on_hide(self):
        """File, Hide Session (Delete): out of the list, into View, Show
        Sessions, Hidden. Nothing about the session changes; File, Bring Back
        Session shows it again."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.key in self.hidden:
            self._feedback(f"{info.title} is already hidden. File, Bring Back Session "
                           "returns it to the list.")
            return
        if info.is_own and info.cli_session_id in self._runners:
            self._feedback("A turn is running in that session. Stop it first.")
            return
        try:
            self.hidden.hide(info.key)
        except OSError as exc:
            wx.MessageBox(f"Couldn't save your hidden sessions: {exc}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            return
        self._leave_list(info)
        self._feedback(f"Hid {info.title}. View, Show Sessions, Hidden lists it; File, Bring "
                       "Back Session shows it again.")

    def on_unhide(self):
        """File, Bring Back Session: a hidden session back in the list."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.key not in self.hidden:
            self._feedback(f"{info.title} isn't hidden.")
            return
        try:
            self.hidden.show(info.key)
        except OSError as exc:
            wx.MessageBox(f"Couldn't save your hidden sessions: {exc}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            return
        self._feedback(f"{info.title} is back in the list.")
        self._refresh_list_in_place()

    def on_rename(self):
        """File, Rename Session (F2, #93). One of The Chat Place's own
        sessions is renamed in its own store. A desktop app session's files
        are never written, so its new name is kept in titles.json and shows
        only here; an empty name goes back to the desktop app's."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.is_own:
            prompt = f"New name for {info.title}:"
        else:
            keeper = "Claude Code" if info.is_terminal else "the Claude desktop app"
            prompt = (f"New name for {info.title}. It shows only in The Chat Place; "
                      f"{keeper} keeps its own name for it. Leave it empty to go "
                      "back to that name.")
        dialog = wx.TextEntryDialog(self, prompt, "Rename Session", info.title)
        dialog.SetMaxLength(MAX_TITLE)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            title = clean_title(dialog.GetValue())
        finally:
            dialog.Destroy()
        self.rename_session(info, title)

    def rename_session(self, info: SessionInfo, title: str):
        title = clean_title(title)
        old = info.title
        if info.is_own:
            if not title:
                self._feedback("A Chat Place session needs a name; it wasn't changed.")
                return
            if self.store.get(info.cli_session_id) is None:
                # Its first turn is just reporting its id; there's nothing to rename yet.
                self._feedback(f"Couldn't rename {old} just now. Try again in a moment.")
                return
            if not self._store_write(self.store.update, info.cli_session_id, title=title):
                return
        else:
            keeper = "Claude Code's" if info.is_terminal else "the desktop app's"
            if not title and info.key not in self.titles:
                self._feedback(f"{old} already has {keeper} name.")
                return
            try:
                self.titles.set(info.key, title)
            except OSError as exc:
                wx.MessageBox(f"Couldn't save your session names: {exc}", APP_NAME,
                              wx.OK | wx.ICON_WARNING, self)
                return
            if not title:
                # Back to its own name, which the next read brings.
                self._feedback(f"{old} goes back to {keeper} name.")
                self.refresh_sessions(force=False)
                return
        for each in [s for s in self._snapshot.sessions if s.key == info.key] + [info]:
            each.title = title
        if self._open is not None and self._open.key == info.key:
            self._open.title = title
            self._update_heading()
        self._refresh_list_in_place()
        self._feedback(f"Renamed {old} to {title}." if title != old
                       else f"{title} keeps its name.")

    def on_delete_permanently(self):
        """File, Delete Session Permanently (Shift+Delete): one of The Chat
        Place's own sessions, gone for good, its Claude Code transcript with
        it. It works from any view, hidden or not: having to hide a session
        first and then find it in the Hidden view was a hunt nobody could
        guess, and the confirmation (No by default) is the safeguard."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.is_terminal:
            self._feedback(f"{info.title} is a terminal session: The Chat Place doesn't "
                           "delete it. Here it can only be hidden.")
            return
        if not info.is_own:
            self._feedback(f"{info.title} is {kind_name(info)}: delete it in the desktop "
                           "app. Here it can only be hidden.")
            return
        # Desktop sessions are read-only, and this deletes files: an own
        # session whose id is one the desktop app knows (a fork that kept its
        # source's id, say) would take the desktop app's transcript with it.
        if (info.cli_session_id in self._snapshot.desktop_cli_ids
                or info.cli_session_id.startswith("local_")):
            self._feedback(f"{info.title} shares its id with a desktop app session, so "
                           "its files aren't deleted here. It can be hidden.")
            return
        if info.cli_session_id in self._runners:
            self._feedback("A turn is running in that session. Stop it first.")
            return
        if info.state == WORKING:
            # Resumed in a terminal: that claude is still writing it.
            self._feedback(f"{info.title} is working outside The Chat Place. Let it "
                           "finish first.")
            return
        answer = wx.MessageBox(
            f"Delete \"{info.title}\" permanently? Its conversation (Claude Code's "
            "transcript) is deleted from this computer and can't be brought back.",
            "Delete Session Permanently", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self)
        if answer != wx.YES:
            return
        path = platform_paths.transcript_path(info.cwd, info.cli_session_id)
        # Out of The Chat Place first: if that can't be saved, nothing is lost.
        if not self._store_write(self.store.remove, info.cli_session_id):
            return
        was_loaded = self._open is not None and self._open.key == info.key
        if was_loaded:
            # Stop reading it before deleting it: Windows won't delete a file
            # a background read has open.
            self.unload_session()
        failed = None
        try:
            _delete_transcript(path)
        except OSError as exc:
            failed = exc
        # A deleted session left in a group or the hidden list is harmless.
        try:
            self.groups.forget(info.key)
        except OSError:
            pass
        if info.key in self.hidden:
            try:
                self.hidden.show(info.key)
            except OSError:
                pass
        try:
            self.tool_activity.discard(info.key)
        except OSError:
            pass  # a key nothing has any more; harmless
        # Nothing of it may linger: an unsent draft would hold back updates
        # (_unsent_text) for a reply box that no longer exists.
        for per_session in (self._attachments, self._drafts, self._queued,
                            self._pending, self._denials):
            per_session.pop(info.cli_session_id, None)
        self._leave_list(info, refocus=was_loaded)
        if failed is not None:
            wx.MessageBox(f"{info.title} is gone from The Chat Place, but its files "
                          f"couldn't all be deleted ({path}): {failed}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            self._feedback(f"Removed {info.title} from The Chat Place; its files are "
                           "still on this computer.")
        else:
            self._feedback(f"Deleted {info.title} permanently.")

    def _leave_list(self, info: SessionInfo, refocus: bool = False):
        """``info`` leaves the list: the neighbour moves into its row, and if
        it was loaded, nothing is. Focus comes to the list only once the row
        is gone, so a screen reader reads the neighbour, not the departed one."""
        if self._open is not None and self._open.key == info.key:
            self.unload_session()
            refocus = True
        index = self._list_keys.index(info.key) if info.key in self._list_keys else -1
        if index >= 0:
            self.session_list.Delete(index)
            del self._list_keys[index]
            if self._list_keys:
                self.session_list.SetSelection(min(index, len(self._list_keys) - 1))
        if refocus:
            # Its messages and reply box are gone: don't leave focus on them.
            self.session_list.SetFocus()
        self.refresh_sessions()

    # ----------------------------------------------------------- session view

    def on_open_session(self, _event=None):
        index = self.session_list.GetSelection()
        info = (self._current_info(self._list_keys[index])
                if index != wx.NOT_FOUND and index < len(self._list_keys) else None)
        if info is None:
            self._feedback("No session selected.")
            return
        if self._open is not None and info.key == self._open.key:
            # Already loaded: go back to it as it was, without reloading.
            self.chat_list.SetFocus()
            self._feedback(f"Back in {info.title}.")
            return
        self.open_session(info)

    def _save_draft(self):
        if self._open is not None and self._open.is_own:
            self._drafts[self._open.cli_session_id] = self.reply_text.GetValue()

    def open_session(self, info: SessionInfo):
        self._save_draft()
        self._clear_activity()  # another session's tool calls aren't news here
        self._open = info
        self._sync_activity_controls(info.key in self.tool_activity)
        self.reply_text.SetValue(self._drafts.get(info.cli_session_id, "") if info.is_own else "")
        self._open_generation += 1
        self._reader = None
        self._chat_messages = []
        self._chat_keys = []
        self._chat_edits, self._chat_turns, self._edits_said = [], 0, 0
        self._changes_due = False
        self._spoken.clear()
        self._chat_loaded = False
        self._announce_load = True
        if info.is_own and info.unread:
            self._store_write(self.store.update, info.cli_session_id, unread=False)
            info.unread = False
        self.chat_list.Set(["Loading messages\u2026"])
        self.chat_list.SetSelection(0)
        self._update_messages_label()
        self.own_reply.Show(info.is_own)
        self.desktop_reply.Show(not info.is_own)
        if not info.is_own:
            self.desktop_note.SetValue(_COWORK_NOTE if info.cowork else
                                       _TERMINAL_NOTE if info.is_terminal else _DESKTOP_NOTE)
            self.continue_btn.Show(not info.cowork)
            # Only the desktop app's sessions can be opened in it.
            self.reply_claude_btn.Show(info.can_open_in_claude)
            self.desktop_reply.Layout()
        self.session_view.Layout()
        self._update_heading()
        self._update_send_state()
        if info.key in self._list_keys:
            row = self._list_keys.index(info.key)
            if self.session_list.GetSelection() != row:
                self.session_list.SetSelection(row)
        self.SetTitle(f"{info.title} \u2014 {APP_NAME}")
        self._show_attachments()
        if info.is_own and _folder_key(info.cwd) not in self._commands:
            self._fetch_commands(info.cwd)  # ready by the time you want them
        self.chat_list.SetFocus()
        self._refresh_chat()
        self._chat_timer.Start(CHAT_REFRESH_MS)

    def unload_session(self):
        """Nothing loaded (the loaded session was forgotten)."""
        self._chat_timer.Stop()
        self._clear_activity()
        self._spoken.clear()
        self._select_on_load = None
        self._open = None
        self._sync_activity_controls(False)
        self._open_generation += 1
        self._reader = None
        self._chat_messages = []
        self._chat_keys = []
        self.SetTitle(APP_NAME)
        self._show_no_session()

    def _no_session_line(self) -> str:
        """What the messages list says with no session loaded. With no
        sessions at all (a first run), it says how to start one, rather than
        to choose from an empty list (#182)."""
        # Every session, not just those the view shows: a group with none in
        # it isn't a first run.
        if not self._first_snapshot and not self._snapshot.sessions:
            return NO_SESSIONS_YET
        return "No session loaded. Choose one in the session list and press Enter."

    def _sync_no_session_line(self):
        """The line above, kept right as sessions come and go."""
        if self._open is None and self.chat_list.GetCount() == 1:
            line = self._no_session_line()
            if self.chat_list.GetString(0) != line:
                self.chat_list.SetString(0, line)

    def _show_no_session(self):
        self.messages_label.SetLabel("&Messages:")
        set_accessible_name(self.chat_list, "Messages")
        self.chat_list.Set([self._no_session_line()])
        self.chat_list.SetSelection(0)
        self.session_heading.set_text("")
        if hasattr(self, "status_parts"):
            self._update_status_session()
            self._update_status_context()
        self.own_reply.Hide()
        self.desktop_reply.Hide()
        self.session_view.Layout()

    def focus_sessions(self):
        """Back to the sessions list, still on the same session."""
        self._save_draft()
        self.session_list.SetFocus()

    def focus_messages(self):
        self.chat_list.SetFocus()

    def focus_reply(self):
        if self._open is None:
            self._feedback("No session loaded.")
            self.session_list.SetFocus()
        elif self._open.is_own:
            self.reply_text.SetFocus()
        else:
            self.desktop_note.SetFocus()

    def _update_heading(self):
        info = self._open
        if info is None:
            return
        if info.is_own:
            kind = "Chat Place session"
        elif info.cowork:
            kind = "Claude desktop app Cowork session, read-only"
        elif info.is_terminal:
            kind = "Terminal session, read-only"
        else:
            kind = "Claude desktop app session, read-only"
        if info.is_own:
            own = self.store.get(info.cli_session_id)
            if own is not None:
                kind += f" on {model_label(own.model)}"
                if own.effort:
                    kind += f" at {effort_label(own.effort)}"
                # Only when it isn't auto, the default: the heading must
                # still fit a narrow window.
                if normalize_permission_mode(own.permission_mode) != DEFAULT_PERMISSION_MODE:
                    kind += f", {mode_label(own.permission_mode)} mode"
                if own.forked_from:
                    kind += f", continued from {own.forked_from}"
                if self._remote_control_on(own):
                    kind += ", Remote Control on"
        elif info.remote:
            kind += ", on Remote Control in the desktop app"
        state = info.state + (f": {info.detail}" if info.detail else "")
        self.session_heading.set_text(f"{info.title}, {info.repo}, {state}. {kind}.")
        self._update_messages_label()
        self._update_status_session()

    def _update_messages_label(self):
        """The chat list's label (its accessible name) carries the session's
        state and whether it is read-only, so it is heard on arriving there."""
        info = self._open
        if info is None:
            return
        state = info.state + (f": {info.detail}" if info.detail else "")
        kind = ", read-only"
        if info.is_own:
            # The model goes here too: this is what's heard on arriving in
            # the messages; the heading above is never focused.
            own = self.store.get(info.cli_session_id)
            kind = f", on {model_label(own.model)}" if own is not None else ""
        label = f"Messages in {info.title} ({state}{kind})"
        if self.messages_label.GetLabel() != f"&{label}:":
            self.messages_label.SetLabel(f"&{label}:")
            set_accessible_name(self.chat_list, label)

    def _on_chat_timer(self, _event=None):
        self._update_send_state()
        self._refresh_chat()

    def _refresh_chat(self):
        info = self._open
        if info is None or self._reader_busy:
            return
        generation = self._open_generation
        if self._reader is None:
            path = info.transcript_path()
            if path is None:
                self._show_missing_transcript(info)
                return
            self._reader = TranscriptReader(path)
        reader = self._reader
        self._reader_busy = True

        def work():
            try:
                changed = reader.refresh()
                transcript = reader.transcript
                copies = [ChatMessage(m.kind, m.text, m.timestamp, m.key, m.sender)
                          for m in transcript.messages]
                wx.CallAfter(self._apply_chat, generation, changed, copies,
                             transcript.unreadable_lines, None,
                             list(transcript.edits), transcript.turns)
            except Exception as exc:  # noqa: BLE001
                wx.CallAfter(self._apply_chat, generation, False, [], 0, exc)

        self._pool.submit(work)

    def _show_missing_transcript(self, info: SessionInfo):
        if self._chat_loaded:
            return
        self._select_on_load = None  # nothing to find a message in
        self._chat_loaded = True
        own = self.store.get(info.cli_session_id) if info.is_own else None
        if info.is_own and (info.cli_session_id in self._runners
                            or (own is not None and not own.started)):
            line = ("No messages yet. Claude is starting this session."
                    if info.cli_session_id in self._runners else
                    "No messages yet. The first message didn't reach Claude; send it again "
                    "from the reply box.")
            self._chat_loaded = False  # keep looking until it appears
        elif info.is_own:
            line = ("No transcript found for this session. Claude Code may not have "
                    "saved it, or it was deleted.")
        elif info.cowork:
            line = ("No transcript: this Cowork session's history isn't in its folder in "
                    "the desktop app's files. Open it in Claude to see it there.")
        else:
            line = ("No transcript: this session's history is no longer on disk. Claude "
                    "Code deletes transcripts after its retention period (cleanupPeriodDays "
                    "in Claude's settings).")
        if self._announce_load:
            self._announce_load = False
            self._feedback(f"Loaded {info.title}. {line}")
        if list(self.chat_list.GetStrings()) == [line]:
            return  # already showing it: don't make the reader re-read every tick
        self.chat_list.Set([line])
        self._chat_keys = []  # a note, not messages: nothing to act on
        self.chat_list.SetSelection(0)

    def _apply_chat(self, generation, changed, messages, unreadable, error,
                    edits=None, turns=0):
        self._reader_busy = False
        if not self or self._open is None:
            return
        if generation != self._open_generation:
            # A load for a session that is no longer loaded: start the
            # current one's now rather than waiting for the next tick.
            self._refresh_chat()
            return
        if error is not None:
            self._select_on_load = None
            if not self._chat_loaded:
                self.chat_list.Set([f"Couldn't read this transcript: {error}"])
                self._chat_keys = []
                self.chat_list.SetSelection(0)
                self._chat_loaded = True
            return
        first_load = not self._chat_loaded
        if edits is not None:
            self._chat_edits, self._chat_turns = edits, turns
        if first_load:
            self._edits_said = len(self._chat_edits)  # what's there is old news
        if not changed and not first_load:
            if self._changes_due:
                self._say_changes(generation)
            return
        before_keys = {m.key for m in self._chat_messages}
        self._chat_messages = messages
        self._check_context()
        self._rebuild_chat_list(focus_newest=first_load)
        self._chat_loaded = True
        if self._changes_due and not first_load:
            wx.CallAfter(self._say_changes, generation)  # after the reply
        if first_load:
            target, self._select_on_load = self._select_on_load, None
            if target is not None and target.key == self._open.key:
                # Loaded from Find in All Sessions (#109): on the message found.
                self._announce_load = False
                self._select_match(target, loaded=True)
                return
            note = f" Couldn't read {unreadable} lines." if unreadable else ""
            count = sum(1 for m in self._visible_messages() if m.kind != QUEUED)
            if self._announce_load:
                self._announce_load = False
                self._feedback(f"Loaded {self._open.title}, {count} "
                               f"message{'s' if count != 1 else ''}.{note}")
            return
        # A message from another session (#123) is news in any session, own
        # or not, and even mid-turn: nothing else would announce it.
        for message in messages:
            if message.key not in before_keys and message.kind == PEER:
                text = announce.peer_text(self._open.title, message.sender, message.text,
                                          self.speech.announce)
                if text:
                    self._say(text)
        if self._open.is_own:
            return  # its turn announces the reply when it finishes
        if self._show_activity:
            # A desktop session's tool calls, as they reach its transcript (#12).
            for message in messages:
                if message.key not in before_keys and message.kind == TOOL:
                    self._queue_activity("tool", message.text)
        fresh = [m for m in messages if m.key not in before_keys and m.kind in _REPLY_KINDS]
        if fresh:
            # The reply is the news now; tool calls before it are old.
            self._clear_activity()
            text = announce.reply_text(self._open.title, fresh[-1].text, self.speech.announce)
            if text:
                self._say(text)
            else:
                self._status(f"{self._open.title}: new message.")

    # Show Tool Activity on: tool calls (and, in The Chat Place's own sessions,
    # what Claude writes between them) are spoken for the open session (#12).
    # They're gathered for a moment and said together, without cutting off
    # the screen reader, so a run of calls is one announcement ("Using Read 4
    # times, then Bash.") and speech never falls far behind.

    def _queue_activity(self, kind: str, text: str):
        self._activity.append((kind, text))
        if self._activity_timer is None:
            self._activity_timer = wx.CallLater(ACTIVITY_DELAY_MS, self._flush_activity)

    def _flush_activity(self):
        if self._gone():
            return
        self._activity_timer = None
        items, self._activity = self._activity, []
        # What Claude wrote last waits for a tool call after it: if none comes,
        # it was the reply, which the end of the turn announces (and drops).
        held = []
        while items and items[-1][0] == "text":
            held.insert(0, items.pop())
        self._activity = held
        if self._open is None or not self._show_activity:
            return
        text = announce.activity_text(items, self.speech.announce)
        if text:
            self._feedback(text)

    def _clear_activity(self):
        """Forget activity not yet said: the session changed, or the turn
        ended and its reply is announced instead."""
        if self._activity_timer is not None:
            self._activity_timer.Stop()
            self._activity_timer = None
        self._activity = []

    def _visible_messages(self) -> List[ChatMessage]:
        if self._show_activity:
            shown = list(self._chat_messages)
        else:
            shown = [m for m in self._chat_messages if not m.is_activity]
        return shown + self._queued_rows()

    def _rebuild_chat_list(self, focus_newest: bool = False,
                           keep_key: Optional[str] = None):
        """Bring the list up to date without moving the reader.

        Rows are updated in place and new ones appended; the selection stays on
        the same message. ``keep_key`` asks for that message (or the nearest
        one still visible) to be selected after a full rebuild.
        """
        visible = self._visible_messages()
        lines = [m.list_line() for m in visible] or ["No messages yet."]
        keys = [m.key for m in visible]
        index = self.chat_list.GetSelection()
        old_keys = self._chat_keys
        selected_key = keep_key or (old_keys[index] if 0 <= index < len(old_keys) else None)

        current = list(self.chat_list.GetStrings())
        # Queued messages (#50) are a tail after the conversation: new
        # messages go in before it, so the rows above never move.
        old_body = [k for k in old_keys if not k.startswith("queued:")]
        body = [k for k in keys if not k.startswith("queued:")]
        if (not focus_newest and keep_key is None and old_keys and keys
                and body[: len(old_body)] == old_body and len(current) == len(old_keys)
                and old_keys[: len(old_body)] == old_body):
            for i, line in enumerate(lines[: len(old_body)]):
                if current[i] != line:
                    self.chat_list.SetString(i, line)
            if len(body) > len(old_body):
                self.chat_list.Insert(lines[len(old_body):len(body)], len(old_body))
            tail_start = len(body)
            new_tail = lines[tail_start:] if keys else []
            old_tail_count = len(old_keys) - len(old_body)
            if [s for s in list(self.chat_list.GetStrings())[tail_start:]] != new_tail:
                for _ in range(old_tail_count):
                    self.chat_list.Delete(tail_start)
                if new_tail:
                    self.chat_list.Append(new_tail)
                if selected_key is not None and selected_key.startswith("queued:") \
                        and keys:
                    self.chat_list.SetSelection(
                        keys.index(selected_key) if selected_key in keys
                        else min(index, len(keys) - 1))
        else:
            self.chat_list.Set(lines)
            if focus_newest or not keys:
                self.chat_list.SetSelection(len(lines) - 1)
            elif selected_key in keys:
                self.chat_list.SetSelection(keys.index(selected_key))
            else:
                self.chat_list.SetSelection(self._nearest_visible(selected_key, keys))
        self._chat_keys = keys

    def _nearest_visible(self, key: Optional[str], visible_keys: List[str]) -> int:
        """Row of the last visible message at or before ``key`` in the full
        transcript; the newest if ``key`` is unknown."""
        if not visible_keys:
            return 0
        order = [m.key for m in self._chat_messages]
        if key not in order:
            return len(visible_keys) - 1
        position = order.index(key)
        best = 0
        for row, visible_key in enumerate(visible_keys):
            if visible_key in order and order.index(visible_key) <= position:
                best = row
        return best

    def _message_item_text(self, index: int) -> Optional[str]:
        """What the screen reader reads for row ``index`` of the messages list
        (#11): the whole message, as words, with who said it. None (the row's
        own text) when the setting is off, or for a row that isn't a message
        ("Loading messages…", "No session loaded…")."""
        if not self.speech.full_messages_in_list:
            return None
        if not (0 <= index < len(self._chat_keys)):
            return None
        visible = self._visible_messages()
        if index >= len(visible) or visible[index].key != self._chat_keys[index]:
            return None  # the list and the messages are mid-update
        message = visible[index]
        cached = self._spoken.get(message.key)
        if cached is None or cached[0] != message.text:
            spoken = announce.spoken_markdown(message.text, describe_code=True) \
                or message.first_line()
            cached = (message.text, f"{message.label}: {spoken}")
            self._spoken[message.key] = cached
        return cached[1]

    def _selected_message(self) -> Optional[ChatMessage]:
        """The selected message, only while the list shows the messages (not
        a "No messages yet" line, or rows from before a change)."""
        visible = self._visible_messages()
        index = self.chat_list.GetSelection()
        if not (0 <= index < len(visible) and index < len(self._chat_keys)):
            return None
        message = visible[index]
        return message if message.key == self._chat_keys[index] else None

    def on_read_message(self):
        """The selected message's full text, in a read-only box to read by
        line, word and character. Closing it returns to the same message."""
        message = self._selected_message()
        if message is None:
            self._feedback("No message selected.")
            return
        if self.speech.formatted_messages and self._show_formatted(message):
            self.chat_list.SetFocus()
            return
        dialog = MessageDialog(self, message.label, message.text)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        self.chat_list.SetFocus()

    def _show_formatted(self, message: ChatMessage) -> bool:
        """The message as a formatted page (#190). False when that can't be
        shown, or Kelly chose Read as Plain Text: the caller opens the text
        box instead."""
        title = (f"Message from {message.label}" if message.label in ("Claude", "You")
                 else message.label)
        return self._show_page(title, message_page(title, message.text))

    def on_user_guide(self, _event=None):
        """Help, User Guide (#104): the guide as a page, read by heading like
        the shortcuts, or in the text box when the page can't be shown or on
        Read as Plain Text. It ships with the app, so it never needs the web."""
        try:
            text = platform_paths.user_guide_path().read_text(encoding="utf-8")
        except OSError as exc:
            # Only a broken build gets here; the smoke test checks for it.
            wx.MessageBox(f"Couldn't open the user guide: {exc}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            return
        if self._show_page("User Guide", message_page("User Guide", text)):
            return
        self._modal(MessageDialog(self, "User Guide", markdown_as_text(text)))

    def on_shortcuts(self, _event=None):
        """The keyboard shortcuts as a page: a heading and a table per group.
        The text box when the page can't be shown, or on Read as Plain Text.
        Deliberately not tied to the "formatted page" setting, which is about
        Claude's messages: the shortcuts read best as tables either way."""
        if self._show_page("Keyboard Shortcuts",
                           html_page("Keyboard Shortcuts", shortcuts_html())):
            return
        self._modal(ShortcutsDialog(self))

    def _show_page(self, title: str, page: str) -> bool:
        """Show ``page`` in the formatted view. False when it can't be shown,
        or Kelly chose Read as Plain Text."""
        if not formatted_view_available():
            return False
        try:
            dialog = FormattedMessageDialog(self, title, page)
        except RuntimeError as exc:
            self._status(str(exc))
            return False
        try:
            shown = dialog.ShowModal() != ID_PLAIN_TEXT
            link = getattr(dialog, "app_link", None)
        finally:
            dialog.Destroy()
        if link:
            wx.CallAfter(self.open_link, link)  # a link to a session in it (#144)
        return shown

    def _message_menu(self) -> wx.Menu:
        """The messages list's context menu. Its handlers are bound on the
        menu itself, so nothing accumulates on the frame."""
        menu = wx.Menu()
        read = menu.Append(wx.ID_ANY, "Read &Full Message\tEnter")
        copy = menu.Append(wx.ID_ANY, "&Copy Message\tCtrl+C")
        message = self._selected_message()
        if message is not None and message.kind == QUEUED:
            now = menu.Append(wx.ID_ANY, "Send &Now\tCtrl+Enter")
            edit = menu.Append(wx.ID_ANY, "&Edit Queued Message")
            remove = menu.Append(wx.ID_ANY, "&Remove Queued Message\tDelete")
            menu.Bind(wx.EVT_MENU, lambda e: self.send_queued_now(), now)
            menu.Bind(wx.EVT_MENU, lambda e: self.edit_queued(), edit)
            menu.Bind(wx.EVT_MENU, lambda e: self.remove_queued(), remove)
            menu.AppendSeparator()
        has_code = message is not None and bool(find_code_blocks(message.text))
        blocks = menu.Append(wx.ID_ANY, "Code &Blocks...\tCtrl+Shift+B")
        copy_code = menu.Append(wx.ID_ANY, "Copy &Last Code Block\tCtrl+Shift+C")
        read.Enable(message is not None)
        copy.Enable(message is not None)
        blocks.Enable(has_code)
        copy_code.Enable(has_code)
        menu.Bind(wx.EVT_MENU, lambda e: self.on_read_message(), read)
        menu.Bind(wx.EVT_MENU, lambda e: self._copy_message(), copy)
        menu.Bind(wx.EVT_MENU, lambda e: self.on_code_blocks(), blocks)
        menu.Bind(wx.EVT_MENU, lambda e: self.copy_last_code_block(), copy_code)
        return menu

    def on_code_blocks(self):
        """The selected message's code blocks (#17): each listed by language
        and size, its code to read by line, and Copy for just that block."""
        message = self._selected_message()
        if message is None:
            self._feedback("No message selected.")
            return
        blocks = find_code_blocks(message.text)
        if not blocks:
            self._feedback("This message has no code blocks.")
            return
        self._modal(CodeBlocksDialog(self, blocks, self._copy_code_block))
        self.chat_list.SetFocus()

    def on_links(self):
        """View, Links (Ctrl+L, #190): the links in the loaded session's
        messages, as the list shows them (tool calls and results only with
        Show Tool Activity on), to open in the browser or copy."""
        info = self._open
        if info is None:
            self._feedback("No session loaded.")
            return
        if not self._chat_loaded:
            self._feedback("The messages are still loading.")
            return
        found = find_links(m for m in self._visible_messages() if m.kind != QUEUED)
        if not found:
            self._feedback("No links in this session.")
            return
        returning_to = wx.Window.FindFocus()
        dialog = LinksDialog(self, info.title, found, self._copy_text, self._feedback)
        try:
            dialog.ShowModal()
            chosen = dialog.chosen
        finally:
            dialog.Destroy()
        if returning_to:
            returning_to.SetFocus()
        if chosen is None:
            return
        if chosen.is_session_link:
            self.open_link(chosen.url)  # The Chat Place's own, never the shell
            return
        try:
            platform_paths.open_url(chosen.url)
        except (OSError, ValueError) as exc:
            wx.MessageBox(f"Couldn't open the link: {exc}\n\nIt's {chosen.url}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            return
        self._feedback(f"Opening {chosen.address}.")

    def on_about_you(self):
        """View, What Claude Knows About You (Ctrl+Shift+K, #92): your
        instructions, memories, skills and the rest, read from Claude Code's
        files, with the projects your sessions work in."""
        cwds = sorted({s.cwd for s in self._snapshot.sessions if s.cwd})
        self._feedback("Reading what Claude knows about you…")
        # On the pool: a session folder on a disconnected or network drive
        # can take seconds to answer.
        self._read_about_you(cwds, lambda kinds: self._show_about_you(kinds, cwds))

    def _read_about_you(self, cwds, done):
        """``about_you.collect`` in the background, then ``done(kinds)`` on
        the UI thread (or a message if it failed)."""
        def work():
            try:
                kinds = about_you.collect(cwds)
            except Exception as exc:  # noqa: BLE001
                wx.CallAfter(self._feedback, f"Couldn't read what Claude knows about you: {exc}")
                return
            wx.CallAfter(done, kinds)
        self._pool.submit(work)

    def _show_about_you(self, kinds, cwds):
        def copy(path: str):
            self._feedback("Copied the file's location." if self._copy_text(path)
                           else "Couldn't open the clipboard.")

        def reload(done):
            def reloaded(fresh):
                self._feedback("Reloaded. " + about_you.summary(fresh))
                done(fresh)
            self._read_about_you(cwds, reloaded)
        self._modal(AboutYouDialog(self, kinds, reload, platform_paths.edit_file,
                                   platform_paths.show_in_folder, copy))

    def copy_last_code_block(self):
        """Ctrl+Shift+C: the last code block of the selected message, usually
        the one Claude means you to run or keep."""
        message = self._selected_message()
        if message is None:
            self._feedback("No message selected.")
            return
        blocks = find_code_blocks(message.text)
        if not blocks:
            self._feedback("This message has no code blocks.")
            return
        self._copy_code_block(blocks[-1])

    def _copy_code_block(self, block):
        if self._copy_text(block.code):
            self._feedback(f"Copied: {block.describe()}.")
        else:
            self._feedback("Couldn't open the clipboard.")

    def _message_menu_position(self, event=None, listbox=None) -> wx.Point:
        """Where a list's menu opens: at the mouse for a right-click, at the
        selected row for the Applications key or Shift+F10 where wx can say
        where that row is (wxPython 4.3's ListBox can't: its top left)."""
        listbox = listbox or self.chat_list
        position = event.GetPosition() if event is not None else wx.DefaultPosition
        if position != wx.DefaultPosition:
            return listbox.ScreenToClient(position)
        index = listbox.GetSelection()
        try:
            rect = listbox.GetItemRect(max(index, 0))
            if rect.height > 0:
                return wx.Point(rect.x + 8, rect.y + rect.height)
        except (AttributeError, NotImplementedError):
            pass
        return wx.Point(8, 8)

    def _on_message_menu(self, event=None):
        """Right-click, the Applications key, Shift+F10 or VO+Shift+M in the
        messages list. As in the session list (_on_session_menu), the menu
        is for the row clicked, and focus goes to the list: VO+Shift+M can
        open it from the VoiceOver cursor while focus is elsewhere."""
        if self._closing:
            return
        position = event.GetPosition() if event is not None else wx.DefaultPosition
        if position != wx.DefaultPosition:
            # A list box doesn't select the row right-clicked, so the menu
            # meant the old highlight while the mouse was on another message.
            row = self.chat_list.HitTest(self.chat_list.ScreenToClient(position))
            if row == wx.NOT_FOUND:
                return
            self.chat_list.SetSelection(row)
        self.chat_list.SetFocus()
        menu = self._message_menu()
        try:
            self.chat_list.PopupMenu(menu, self._message_menu_position(event))
        finally:
            menu.Destroy()

    def _session_menu(self):
        """The session list's context menu (#89), as (menu, {item id:
        handler}). The commands are the File menu's, for the selected row."""
        info = self._selected_session()
        menu = wx.Menu()
        actions = {}
        if info is None:
            return menu, actions

        def add(label, handler, enable=True):
            item = menu.Append(wx.ID_ANY, label)
            item.Enable(enable)
            actions[item.GetId()] = handler
        add("&Load Session\tEnter", self.on_open_session)
        add("Open in &Claude\tCtrl+O", self.on_open_in_claude, info.can_open_in_claude)
        add("Copy Session Lin&k...\tCtrl+Shift+L", self.on_copy_session_link)
        if not info.is_own:
            # Heard as unavailable for a Cowork session (#91), whose button is hidden.
            add("Con&tinue Here...\tCtrl+Shift+N", self.on_continue_here, not info.cowork)
        add("Re&name Session...\tF2", self.on_rename)
        add("&Remote Control...", self.on_remote_control)
        menu.AppendSeparator()
        add("Add to &Group...\tCtrl+G", self.on_add_to_group)
        add("Remove from Gro&up...", self.on_remove_from_group,
            bool(self.groups.groups_of(info.key)))
        add("&Export Session...\tCtrl+E", self.on_export)
        menu.AppendSeparator()
        if info.key in self.hidden:
            add("&Bring Back Session", self.on_unhide)
        else:
            add("H&ide Session\tDelete", self.on_hide)
        if info.is_own:
            add("Delete Session &Permanently...\tShift+Delete", self.on_delete_permanently)
        return menu, actions

    def _on_session_menu(self, event=None):
        """Right-click, the Applications key or Shift+F10 in the session list.
        The choice runs once the menu has closed and focus is back on the
        list, so the command means the highlighted session, not the loaded
        one (see _selected_session)."""
        if self._closing:
            return
        position = event.GetPosition() if event is not None else wx.DefaultPosition
        if position != wx.DefaultPosition:
            # A right-click: a list box neither selects the row clicked nor
            # takes focus, so do both first, or the menu and its command
            # would mean the loaded session or the old highlight.
            row = self.session_list.HitTest(self.session_list.ScreenToClient(position))
            if row == wx.NOT_FOUND:
                return
            self.session_list.SetSelection(row)
        self.session_list.SetFocus()
        menu, actions = self._session_menu()
        if not actions:
            menu.Destroy()
            self._feedback("No session selected.")
            return
        try:
            chosen = self.session_list.GetPopupMenuSelectionFromUser(
                menu, self._message_menu_position(event, self.session_list))
        finally:
            menu.Destroy()
        handler = actions.get(chosen)
        if handler is not None:
            self.session_list.SetFocus()
            handler()

    def on_toggle_activity_menu(self, _event):
        self._set_activity(self.activity_item.IsChecked())

    def on_toggle_activity_check(self, _event):
        self._set_activity(self.activity_check.GetValue())

    def _sync_activity_controls(self, show: bool):
        """The loaded session's Show Tool Activity, on the menu and the check
        box, without saying anything (loading a session)."""
        self._show_activity = show
        self.activity_item.Check(show)
        self.activity_check.SetValue(show)

    def _set_activity(self, show: bool):
        """Show Tool Activity (Ctrl+T), for the loaded session only and kept
        with it (#162)."""
        info = self._open
        if info is None:
            self._sync_activity_controls(False)
            self._feedback("No session loaded. Show Tool Activity is set for each session.")
            return
        self._sync_activity_controls(show)
        if not show:
            self._clear_activity()
        kept = True
        try:
            if show:
                self.tool_activity.add(info.key)
            else:
                self.tool_activity.discard(info.key)
        except OSError:
            kept = False  # still shown (or hidden) now, just not remembered
        if self._chat_loaded and self._chat_messages:
            index = self.chat_list.GetSelection()
            keep = self._chat_keys[index] if 0 <= index < len(self._chat_keys) else None
            self._rebuild_chat_list(keep_key=keep or "")
        said = (f"Tool activity shown in {info.title}." if show
                else f"Tool activity hidden in {info.title}.")
        if not kept:
            said += " The Chat Place couldn't save this, so it won't be remembered."
        self._feedback(said)

    # ------------------------------------------------------- open in Claude

    # --------------------------------------------------------- export (#33)

    def _export_source(self, info: SessionInfo):
        """What to export: the loaded session's messages as the list shows
        them, or the path of another session's transcript (read later, off
        the window's thread). (messages, None), (None, path), or (None, None)
        when there's nothing on disk."""
        if self._open is not None and info.key == self._open.key and self._chat_loaded:
            return [m for m in self._visible_messages() if m.kind != QUEUED], None
        return None, info.transcript_path()

    def on_export(self):
        """File, Export Session (Ctrl+E): save the conversation as
        Markdown, a web page or plain text. Reading a long transcript and
        rendering it happen in the background, so the window never stops
        answering the screen reader."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        messages, transcript = self._export_source(info)
        if not messages and transcript is None:
            wx.MessageBox(f"{info.title} has no messages to export (its transcript isn't on "
                          "disk).", APP_NAME, wx.OK | wx.ICON_INFORMATION, self)
            return
        documents = os.path.join(os.path.expanduser("~"), "Documents")
        dialog = wx.FileDialog(
            self, f"Export {info.title}", defaultDir=documents if os.path.isdir(documents) else "",
            defaultFile=export.default_filename(info.title),
            wildcard="|".join(part for _fmt, part in export.FORMATS),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = dialog.GetPath()
            fmt = export.FORMATS[max(0, dialog.GetFilterIndex())][0]
        finally:
            dialog.Destroy()
        # The file name's extension wins over the list's choice if they differ.
        extension = os.path.splitext(path)[1].lower().lstrip(".")
        if extension in (export.MARKDOWN, export.HTML, export.TEXT):
            fmt = extension
        elif extension == "htm":
            fmt = export.HTML
        else:
            path += "." + fmt
            # The dialog asked about overwriting the name as typed, not this one.
            if os.path.exists(path) and wx.MessageBox(
                    f"{os.path.basename(path)} already exists. Replace it?", "Export Session",
                    wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
                return
        self._feedback(f"Exporting {info.title}.")
        # As the screen shows it for the loaded session (even if saving the
        # setting failed); as kept, for any other.
        show_activity = (self._show_activity if self._open is not None
                         and info.key == self._open.key else info.key in self.tool_activity)
        title, folder = info.title, info.cwd

        def work():
            try:
                items = messages
                if items is None:
                    reader = TranscriptReader(transcript)
                    reader.refresh()
                    items = [m for m in reader.transcript.messages
                             if show_activity or not m.is_activity]
                if not items:
                    raise OSError("the transcript has no messages")
                text = export.render(fmt, title, items, folder=folder)
                with open(path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                wx.CallAfter(self._export_done, title, path, len(items), None)
            except Exception as exc:  # noqa: BLE001 - said, not raised
                wx.CallAfter(self._export_done, title, path, 0, exc)

        self._pool.submit(work)

    def _export_done(self, title: str, path: str, count: int, error):
        if self._gone():
            return
        if error is not None:
            wx.MessageBox(f"Couldn't export {title} to {path}: {error}", APP_NAME,
                          wx.OK | wx.ICON_ERROR, self)
            return
        # Said, not a dialog: where it went is in the status bar to read back.
        self._feedback(f"Exported {title}, {count} message{'s' if count != 1 else ''}, to "
                       f"{os.path.basename(path)} in {os.path.dirname(path)}.")

    def on_open_in_claude(self, _event=None):
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if not info.can_open_in_claude:
            if info.is_terminal:
                text = ("This session was started in a terminal, so the Claude desktop app "
                        "doesn't list it and it can't be opened there. Reply to it in the "
                        "terminal, or use Continue Here to carry it on in The Chat Place.")
            else:
                text = ("This session was started by The Chat Place, so the Claude desktop "
                        "app doesn't list it and it can't be opened there. Read and reply "
                        "to it here.")
            wx.MessageBox(text, APP_NAME, wx.OK | wx.ICON_INFORMATION, self)
            return
        try:
            platform_paths.open_url(claude_link(info))
            self._feedback(f"Opened {info.title} in Claude.")
        except OSError as exc:
            wx.MessageBox(f"Couldn't open the Claude desktop app: {exc}", APP_NAME,
                          wx.OK | wx.ICON_ERROR, self)

    # ------------------------------------------------------------- sending

    def _update_send_state(self):
        info = self._open
        running = info is not None and info.cli_session_id in self._runners
        # Always enabled: a disabled button drops out of the Tab order, and
        # Send and Stop must stay where Kelly's fingers expect them.
        own = bool(info and info.is_own)
        self.send_btn.Enable(own)
        self.stop_btn.Enable(own)
        if info is not None and info.is_own:
            waiting = self._pending.get(info.cli_session_id)
            runner = self._runners.get(info.cli_session_id)
            if running and waiting:
                label = f"Waiting for you: {waiting[0].summary()}. Ctrl+Shift+A answers."
            elif running and getattr(runner, "waiting_on_background", False):
                # Answered; kept open only so the work can finish (#161).
                label = (f"Claude answered. {announce.background_text(runner.background_tasks)} "
                         "What you send goes to Claude now.")
            elif running:
                elapsed = describe_elapsed(self._runners[info.cli_session_id].elapsed())
                label = f"Claude is working ({elapsed})."
                waiting_count = len(self._queued.get(info.cli_session_id, []))
                if waiting_count:
                    # What's queued first: it's yours, and the news (#59).
                    label = f"{self._queued_words(waiting_count)} {label}"
            else:
                label = "Ready."
            if self.turn_status.GetLabel() != label:
                self.turn_status.SetLabel(label)
        self._update_status_session()

    def _context(self, info: Optional[SessionInfo]):
        """(tokens, window) for a session, from its transcript as read."""
        if info is None or self._reader is None or self._open is None \
                or info.key != self._open.key:
            return 0, 0
        transcript = self._reader.transcript
        reported = (self._windows.get(info.cli_session_id, 0) if info.is_own else 0) or \
            self._model_windows.get(transcript.model, 0)
        window = usage.context_window(transcript.model, transcript.context_tokens, reported)
        return transcript.context_tokens, window

    def on_usage(self):
        """View, Usage and Context (Ctrl+Shift+U): how full the loaded
        session's context is, and how much of the plan's limits are used, as
        a list to arrow through (#130). It used to be only spoken, which
        left someone who can't follow the system voice (#98) nothing to
        read. The dialog's title and the focused line are what a screen
        reader says, so nothing is spoken on top."""
        context = None
        if self._open is not None:
            context = (self._open.title, *self._context(self._open))
        lines = usage.usage_lines(context, self._limits)
        self._modal(UsageDialog(self, lines, self._copy_usage))

    def _copy_usage(self, text: str, one_line: bool):
        if self._copy_text(text):
            self._feedback("Copied the line." if one_line else "Copied usage and context.")
        else:
            self._feedback("Couldn't open the clipboard.")

    def on_changes(self):
        """View, Changed Files (Ctrl+Shift+D): the files Claude changed since
        your latest message, or in the whole session, and each change to read
        by line. From the transcript, so desktop sessions work too."""
        info = self._open
        if info is None:
            self._feedback("Load a session first.")
            return
        if not self._chat_loaded:
            self._feedback(f"{info.title} is still loading.")
            return
        everything = by_file(self._chat_edits)
        if not everything:
            self._feedback(f"{info.title}: Claude hasn't changed any files in this session.")
            return
        latest = by_file([e for e in self._chat_edits if e.turn == self._chat_turns])
        self._modal(ChangesDialog(self, info.title, latest, everything,
                                  lambda f: self._describe_changed_file(f, info.cwd)))

    @staticmethod
    def _describe_changed_file(changed, cwd: str) -> str:
        """"main_frame.py, 40 lines added, 12 removed, in thechatplace\\ui"."""
        name = os.path.basename(changed.path)
        folder = os.path.dirname(changed.path)
        try:
            inside = os.path.relpath(folder, cwd) if cwd else folder
            if not inside.startswith(".."):
                folder = inside  # outside the session's folder: all of it
        except ValueError:
            pass  # another drive: the full folder
        where = "" if folder in ("", ".") else f", in {folder}"
        return f"{name}, {changed.counts()}{where}"

    def _say_changes(self, generation: int):
        """After a turn ends: the changes not yet said, at the full
        announcement level, after the reply rather than over it; on the
        status bar otherwise. Nothing if another session has been loaded."""
        if generation != self._open_generation or self._open is None:
            return
        self._changes_due = False
        fresh = self._chat_edits[self._edits_said:]
        self._edits_said = len(self._chat_edits)
        summary = summary_text(fresh)
        if not summary:
            return
        text = f"{self._open.title}: {summary} Ctrl+Shift+D shows the changes."
        if self.speech.announce == ANNOUNCE_FULL:
            self._feedback(text)  # queued behind the reply, not cutting it off
        else:
            self._status(text)

    def _notify(self, key: str, title: str, text: str, needs_you: bool = False):
        """A notification (#20), only while you're in another window:
        here, the announcement already said it."""
        level = self.speech.notifications
        if level == NOTIFY_OFF or (level != NOTIFY_ALL and not needs_you):
            return
        if self._app_is_active():
            return
        self._notifier.show(title, text, key)

    @staticmethod
    def _app_is_active() -> bool:
        """Whether you're in The Chat Place: the main window or any of its
        dialogs (the frame alone says no while a dialog has the focus)."""
        return wx.GetActiveWindow() is not None

    @staticmethod
    def _open_modal() -> Optional[wx.Dialog]:
        """The modal wx dialog in front now (Code Blocks, Settings, a
        question from Claude…), or None. With one dialog over another, the
        inner one: the outer is still modal but disabled under it. Native
        message and file dialogs aren't wx windows and never show here."""
        modals = [w for w in wx.GetTopLevelWindows()
                  if isinstance(w, wx.Dialog) and w.IsModal()]
        return next((w for w in reversed(modals) if w.IsEnabled()), None)

    def _on_activate(self, event: wx.ActivateEvent):
        """Coming back to The Chat Place with a dialog open (#103): if
        Windows activated the main window, which is disabled behind the
        dialog, every key went nowhere and the app seemed hung. The dialog
        is brought forward instead, with focus where you left it."""
        event.Skip()
        if event.GetActive():
            # A timer, not CallAfter: a native message box runs Windows' own
            # loop, which never runs wx's pending calls but does deliver timers.
            wx.CallLater(1, self._raise_open_modal)

    def _raise_open_modal(self):
        # Only when the main window itself is active and disabled, which is
        # only ever the case behind a dialog: if the dialog came forward
        # properly, or you've switched away again, there is nothing to do.
        if not self or self.IsEnabled() or wx.GetActiveWindow() is not self:
            return
        # Windows knows the innermost dialog, native ones included.
        if platform_paths.bring_last_popup_forward(self.GetHandle()):
            return
        modal = self._open_modal()
        if modal is not None:
            modal.Raise()  # activating it gives focus back to its last control

    def _go_to_session(self, key: Optional[str]):
        """A notification was chosen: The Chat Place comes forward with that
        session loaded. With one of its dialogs open, the dialog comes
        forward instead, and the session is left as it is."""
        if self._gone():
            return
        modal = self._come_forward()
        if modal is not None or key is None:
            return
        info = self._current_info(key)
        if info is None:
            return
        if self._open is not None and self._open.key == key:
            self.chat_list.SetFocus()
        else:
            self.open_session(info)

    def _come_forward(self) -> Optional[wx.Dialog]:
        """Bring The Chat Place to the front: its dialog, if one is open
        (returned), otherwise the window."""
        if self.IsIconized():
            self.Iconize(False)
        self.Show()
        modal = self._open_modal()
        (modal or self).Raise()
        mac_a11y.activate_app()  # Raise alone leaves a Mac's menu bar with the last app
        if not self._app_is_active():
            self.RequestUserAttention()  # Windows wouldn't let it come forward
        return modal

    # ------------------------------------------------------- links (#144)

    def on_copy_session_link(self, _event=None):
        """File, Copy Session Link (Ctrl+Shift+L, #144): the links that
        apply to the selected session, best first, each as a Markdown link
        and then bare. Enter on the first copies a Markdown link that opens
        it in The Chat Place."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        own = self.store.get(info.cli_session_id) if info.is_own else None
        # Before its first turn starts, Claude may still give it another id.
        started = not info.is_own or (own is not None and own.started)
        choices = links.copy_choices(
            info, claude_link(info) if info.can_open_in_claude else "",
            own.remote_url if own is not None else "", chat_place=started)
        if not choices:
            self._feedback(f"{info.title} has no link yet. Once Claude has started it, "
                           "it has one.")
            return
        index = self._choose("Copy Session Link", f"Copy a link to {info.title}:",
                             [choice.label for choice in choices], selection=0)
        if index is None:
            return
        if self._copy_text(choices[index].text):
            self._feedback(choices[index].spoken)

    def open_link(self, url: str):
        """Follow a thechatplace:// link (#144): from the command line, the
        system, another copy, or a message. The window comes forward and
        the session is selected and loaded, focus on its messages, as if
        you had chosen it. A link never sends or changes anything. Until the
        session list has been read, or while a dialog is open, it waits."""
        if not self or self._closing:
            return
        self._pending_link = url
        self._link_wait_said = False
        self._link_came_forward = False
        if not self._first_snapshot:
            self._open_pending_link()

    def _check_link_inbox(self):
        if not self or self._closing:
            return
        for url in platform_paths.take_dropped_links():
            self.open_link(url)
        if self._pending_link is not None and not self._first_snapshot:
            self._open_pending_link()  # waiting for a dialog to close

    def _open_pending_link(self):
        url = self._pending_link
        if url is None or not self or self._closing:
            return
        if not self._link_came_forward:
            # Once per link: the timer's retries mustn't pull The Chat Place
            # in front of the app you've gone to every second.
            self._link_came_forward = True
            self._come_forward()
        # Behind one of its dialogs, or a native message or file dialog
        # (which disables the window but isn't a wx window): wait.
        if self._open_modal() is not None or not self.IsEnabled():
            if not self._link_wait_said:
                self._link_wait_said = True
                self._feedback("The link opens when you close this dialog.")
            return
        self._pending_link = None
        session_id = links.parse_link(url)
        if session_id is None:
            self._feedback(links.NOT_OURS)
            return
        info = links.find_session(self._snapshot.sessions, session_id)
        if info is None:
            self._feedback(links.NO_SESSION)
            return
        self._show_in_list(info)
        if self._open is not None and self._open.key == info.key:
            self.chat_list.SetFocus()
            self._feedback(f"Back in {info.title}.")
        else:
            self.open_session(info)

    def _show_in_list(self, info: SessionInfo):
        """Make sure ``info`` is in the session list and selected: if the
        filter or the view leaves it out, show all sessions (or Archived, or
        Hidden, if that's where it is) and say so."""
        if info.key not in self._list_keys:
            info.hidden = info.key in self.hidden
            self._session_filter = ""
            view = self.speech.session_view
            if not self._in_current_view([info]):
                view = (VIEW_HIDDEN if info.hidden else VIEW_ARCHIVED if info.archived
                        else VIEW_ALL)
            self._apply_view(view)
            self._feedback(f"Showing {view_spoken(view)}, to show {info.title}.")
        if info.key in self._list_keys:
            row = self._list_keys.index(info.key)
            if self.session_list.GetSelection() != row:
                self.session_list.SetSelection(row)

    def _notify_turn_end(self, session_id, title, event, denials, detail, stopped=False):
        key = self._own_key(session_id)
        if stopped:
            return  # you stopped it yourself: nothing to tell you
        if event.kind == "failed" or event.is_error:
            self._notify(key, f"{title}: the turn failed", usage.friendly_error(event.text),
                         needs_you=True)
        elif denials:
            self._notify(key, f"{title} needs you", f"{detail}: " + "; ".join(denials),
                         needs_you=True)
        elif not (event.data or {}).get("after_background"):
            # After background work that brought no new answer, the answer
            # before it was already notified (#161).
            self._notify(key, f"{title} finished", event.text or "Claude finished its turn.")

    @staticmethod
    def _own_key(session_id: str) -> str:
        return f"own:{session_id}"

    def _remote_control_on(self, own) -> bool:
        """This session's Remote Control: its own choice, or the default."""
        if own.remote_control in ("on", "off"):
            return own.remote_control == "on"
        return self.speech.remote_control

    def _remote_shown(self, own) -> bool:
        """Whether an own session's row says Remote Control (#96): its turns
        use it, and you chose it for this session or it has connected. With
        only the Settings default on, every row saying so would be noise."""
        return self._remote_control_on(own) and (own.remote_control == "on"
                                                 or bool(own.bridge_session_id))

    def on_remote_control(self):
        """File, Remote Control (#72): on, off, or as Settings says, for
        the selected session from its next turn. While a turn runs, the
        session can be reached from claude.ai and your other devices."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if not info.is_own:
            self._desktop_remote_control(info)
            return
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return
        default = "on" if self.speech.remote_control else "off"
        values = ["", "on", "off"]
        labels = [f"As in Settings (now {default})", "On for this session",
                  "Off for this session"]
        now = values.index(own.remote_control) if own.remote_control in values else 0
        labels[now] += " (now)"
        if own.remote_url:
            values.append("copy")
            labels.append(f"Copy its claude.ai address ({own.remote_url})")
        index = self._choose("Remote Control",
                             f"Remote Control for {info.title}. When it's on, the "
                             "conversation is copied to claude.ai and kept there:",
                             labels, selection=now)
        if index is None or values[index] == own.remote_control:
            return
        if values[index] == "copy":
            if self._copy_text(own.remote_url):
                self._feedback(f"Copied {info.title}'s claude.ai address.")
            return
        if not self._store_write(self.store.update, info.cli_session_id,
                                 remote_control=values[index]):
            return
        own = self.store.get(info.cli_session_id)
        on = self._remote_control_on(own)
        for each in self._snapshot.sessions:
            if each.key == info.key:
                each.remote = self._remote_shown(own)
        if self._open is not None and self._open.key == info.key:
            self._update_heading()
        self._refresh_list_in_place()
        self._feedback(f"Remote Control {'on' if on else 'off'} for {info.title}, "
                       "from its next turn.")

    def on_other_machines(self):
        """File, Other Machines (Ctrl+Shift+M, #123): your sessions on other
        computers, listed and messaged through Claude in the loaded session.
        Only Claude can reach them (ListAgents, SendMessage), and only with
        Remote Control on; see remote.py for why it's done this way."""
        info = self._open
        if info is None or not info.is_own:
            wx.MessageBox("Other machines are reached through one of The Chat Place's own "
                          "sessions with Remote Control on. Load one first, or start one "
                          "with File, New Session.", APP_NAME,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return
        if not self._remote_control_on(own):
            wx.MessageBox(f"Remote Control is off for {info.title}, and Claude can only "
                          "reach your other computers with it on. Turn it on with File, "
                          "Remote Control, then try again.", APP_NAME,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        found = remote.latest_list(m.text for m in self._chat_messages
                                   if m.kind == TOOL_RESULT)
        sessions = found or []
        refresh = "Refresh the list (asks Claude)"
        if found is None:
            prompt = (f"{info.title} hasn't listed your other computers' sessions yet. "
                      "Refresh asks Claude for them; the list is here next time:")
        elif not sessions:
            prompt = ("Claude's latest list had no sessions on other computers. Each needs "
                      "Remote Control on, and its computer awake:")
        else:
            prompt = ("Send a message to which session? From Claude's latest list. "
                      "Offline often only means between turns; a message waits for it:")
        index = self._choose("Other Machines", prompt,
                             [s.describe() for s in sessions] + [refresh])
        if index is None:
            return
        if index == len(sessions):
            self._send_generated(info, remote.LIST_PROMPT,
                                 f"Asking Claude in {info.title} for your other computers' "
                                 "sessions. Ctrl+Shift+M again once it has answered.")
            return
        target = sessions[index]
        address = remote.addresses(sessions)[index]
        # One line, so Enter sends.
        dialog = wx.TextEntryDialog(self, f"Message to {target.name} (Enter sends):",
                                    "Other Machines", "")
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            text = dialog.GetValue().strip()
        finally:
            dialog.Destroy()
        if not text:
            self._feedback("Nothing sent: the message was empty.")
            return
        self._send_generated(info, remote.send_prompt(address, text),
                             f"Claude in {info.title} is sending your message to "
                             f"{target.name}. A reply shows here as from {target.name}.")

    def _send_generated(self, info: SessionInfo, message: str, said: str):
        """Send a message The Chat Place wrote for you (Other Machines) into
        an own session. It isn't read back, or given back to the reply box if
        the turn fails (``spoken=""``): it's instructions to Claude, not your
        words, and ``said`` tells you what happened instead. It isn't queued
        either, where it would show and be editable as if you'd typed it."""
        session_id = info.cli_session_id
        runner = self._runners.get(session_id)
        if runner is not None and runner.waiting_on_background:
            # Answered and only waiting on background work (#161): it goes in.
            if runner.send_follow_up(message):
                self._update_send_state()
                self._feedback(said)
                return
        if session_id in self._runners:
            self._feedback(f"{info.title} is working. Use Other Machines again when "
                           "the turn ends.")
            return
        problem = self._send_now(session_id, message, spoken="")
        if problem:
            wx.MessageBox(problem, APP_NAME, wx.OK | wx.ICON_WARNING, self)
            return
        self._feedback(said)

    def _desktop_remote_control(self, info: SessionInfo):
        """Remote Control for a desktop app session (#96). The Chat Place
        never changes a desktop session, so it offers the two ways there
        are: turn it on in the desktop app, or continue the session here as
        a copy with Remote Control on from its first turn."""
        choices, actions = [], []
        if info.can_open_in_claude:
            choices.append("Open it in the Claude desktop app, where Remote Control is "
                           "turned on and off" if info.remote else
                           "Open it in the Claude desktop app, to turn on Remote Control there")
            actions.append(self.on_open_in_claude)
        # A Cowork session (#91) can't be continued here, so only the desktop app is offered.
        if not info.cowork and info.cli_session_id and info.transcript_path():
            choices.append("Continue it here as a copy, with Remote Control on")
            actions.append(lambda: self.on_continue_here(remote_control="on"))
        if not choices:
            self._feedback(f"{info.title} is {kind_name(info)} that can't be opened in "
                           "the desktop app or continued here, so it can't use Remote Control.")
            return
        if info.is_terminal:
            prompt = (f"{info.title} was started in a terminal, which The Chat Place doesn't "
                      "change; to reach it from claude.ai as it is, type /remote-control in "
                      "that terminal. Or here:")
        elif info.remote:
            prompt = (f"{info.title} is already on Remote Control in the desktop app, so you "
                      "can reach it from claude.ai now; nothing more is needed. The Chat "
                      "Place doesn't change desktop app sessions. Other ways:")
        else:
            prompt = (f"{info.title} is a Claude desktop app session, which The Chat Place "
                      "doesn't change. How do you want to reach it from claude.ai?")
        index = self._choose("Remote Control", prompt, choices)
        if index is not None:
            actions[index]()

    def _on_remote_control(self, session_id: str, title: str, data: dict):
        """A turn's Remote Control answer: remembered, so the next turn joins
        the same Remote Control session; said the first time, or if it failed."""
        if data.get("error"):
            key = ("remote control", session_id, data["error"])
            if key not in self._warned:  # once, not every turn
                self._warned.add(key)
                self._say(f"{title}: couldn't turn on Remote Control: {data['error']}")
            return
        bridge = str(data.get("bridge_session_id") or "")
        url = str(data.get("session_url") or "")
        own = self.store.get(session_id)
        if own is None or not bridge:
            return
        first = own.bridge_session_id != bridge
        self._store_write(self.store.update, session_id, bridge_session_id=bridge,
                          remote_url=url)
        if first:
            self._say(f"{title} is on Remote Control. File, Remote Control copies its "
                      "claude.ai address." if url else f"{title} is on Remote Control.")

    @staticmethod
    def _queued_words(count: int) -> str:
        return "1 message queued." if count == 1 else f"{count} messages queued."

    def _own_for_setting(self, what: str):
        """The selected session and its stored record, for a File, Change
        item; None (and why, said) for a session whose ``what`` is set
        elsewhere."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return None
        if not info.is_own:
            where = "terminal" if info.is_terminal else "desktop app"
            self._feedback(f"{info.title} is {kind_name(info)}: its {what} is set in the "
                           f"{where}.")
            return None
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return None
        return info, own

    def _change_setting(self, title: str, prompt: str, choices, now: str):
        """The value chosen from ``choices`` ((value, label) pairs), with the
        current one marked and selected; None if cancelled or unchanged."""
        values = [value for value, _label in choices]
        labels = [f"{label} (now)" if value == now else label for value, label in choices]
        index = self._choose(title, prompt, labels,
                             selection=values.index(now) if now in values else None)
        if index is None or values[index] == now:
            return None
        return values[index]

    def _from_next_turn(self, own: OwnSession) -> str:
        """When a change to a session's setting starts to count."""
        if own.cli_session_id in self._runners:
            return "from its next turn; the turn running now carries on as it started"
        return "from its next turn"

    def on_change_model(self):
        """File, Change Model: the model for this session's next turns. A
        session's model goes with every turn, so it can change at any time."""
        found = self._own_for_setting("model")
        if found is None:
            return
        info, own = found
        model = self._change_setting("Change Model",
                                     f"Model for {info.title}, now {model_label(own.model)}:",
                                     MODELS, own.model)
        if model is None:
            return
        # own's id, not info's: Claude may have given the session another id
        # while the list was open (rename_id changes own itself).
        if not self._store_write(self.store.update, own.cli_session_id, model=model):
            return
        self._update_heading()
        self._feedback(f"{info.title} now uses {model_label(model)}, from its next turn.")

    def on_change_permission_mode(self):
        """File, Change Permission Mode (Ctrl+Shift+O, #189): the mode this
        session's next turns start in. Every turn is given its stored mode,
        as approving a plan already relies on."""
        found = self._own_for_setting("permission mode")
        if found is None:
            return
        info, own = found
        now = normalize_permission_mode(own.permission_mode)
        mode = self._change_setting(
            "Change Permission Mode",
            f"Permission mode for {info.title}, now {mode_label(now)}:",
            PERMISSION_MODES, now)
        if mode is None:
            return
        if not self._store_write(self.store.update, own.cli_session_id, permission_mode=mode):
            return
        self._update_heading()
        self._feedback(f"Permission mode for {info.title}: {mode_label(mode)}, "
                       f"{self._from_next_turn(own)}.")

    def on_change_effort(self):
        """File, Change Effort (#189): how hard Claude thinks in this
        session's next turns, given as ``--effort`` on every turn."""
        found = self._own_for_setting("effort")
        if found is None:
            return
        info, own = found
        effort = self._change_setting(
            "Change Effort",
            f"Effort for {info.title}, now {effort_label(own.effort)}. {EFFORT_NOTE}",
            EFFORTS, own.effort)
        if effort is None:
            return
        if not self._store_write(self.store.update, own.cli_session_id, effort=effort):
            return
        self._update_heading()
        self._feedback(f"Effort for {info.title}: {effort_label(effort)}, "
                       f"{self._from_next_turn(own)}.")

    def _check_model(self, session_id: str, title: str, actual: str):
        """Say once if Claude Code runs another model than the session chose
        (it falls back to its default when a model isn't allowed, #8). A turn
        that would run on Fable unchosen never gets here: it's stopped (#58)."""
        own = self.store.get(session_id)
        if own is None or not actual:
            return
        key = ("model", session_id, actual)
        if key in self._warned:
            return
        if not own.model or model_matches(own.model, actual):
            return
        text = (f"{title} is using {model_spoken(actual)} instead of "
                f"{model_label(own.model)}, the model this session chose.")
        self._warned.add(key)
        self._say(text)

    def _check_context(self):
        """Say once when the loaded session's context passes 80%."""
        self._update_status_context()
        info = self._open
        tokens, window = self._context(info)
        if info is None:
            return
        compactions = self._reader.transcript.compactions if self._reader else 0
        # A new key after each compaction, so it can be said again.
        key = ("context", info.cli_session_id, compactions)
        if not window or usage.context_share(tokens, window) < usage.CONTEXT_WARNING:
            return
        if key in self._warned:
            return
        self._warned.add(key)
        self._say(f"{info.title}: {usage.context_text(tokens, window)} Claude Code will "
                  "compact the conversation when it's full; /compact does it now.")

    def on_turn_status(self, _event=None):
        """How long the running turn has taken, and what it is doing."""
        info = self._open
        runner = self._runners.get(info.cli_session_id) if info else None
        if runner is not None and self._pending.get(info.cli_session_id):
            request = self._pending[info.cli_session_id][0]
            self._feedback(f"{info.title} is waiting for you: {request.summary()}. "
                           "Ctrl+Shift+A answers.")
            return
        if runner is not None and getattr(runner, "waiting_on_background", False):
            self._feedback(f"{info.title}: Claude answered. "
                           f"{announce.background_text(runner.background_tasks)} "
                           f"The turn has run for {describe_elapsed(runner.elapsed())}; "
                           "Claude carries on when the work finishes.")
            return
        if runner is not None:
            count = len(self._queued.get(info.cli_session_id, []))
            waiting = f"{self._queued_words(count)} " if count else ""
            self._feedback(f"{info.title}: {waiting}Claude has been working for "
                           f"{describe_elapsed(runner.elapsed())}, last {runner.last_activity}.")
            return
        if not self._runners:
            self._feedback("No turns are running.")
            return
        parts = []
        for session_id, other in self._runners.items():
            own = self.store.get(session_id)
            name = own.title if own else "A session"
            parts.append(f"{name}, {describe_elapsed(other.elapsed())}")
        self._feedback("Working: " + "; ".join(parts) + ".")

    # ------------------------------------------- answering Claude (#187, #188)

    def _waiting(self) -> Dict[str, str]:
        """Sessions whose turn is paused on you, and what for."""
        return {sid: queue[0].summary() for sid, queue in self._pending.items() if queue}

    def _next_waiting_session(self) -> Optional[str]:
        """The loaded session if it is waiting, otherwise the one that has
        waited longest."""
        if (self._open is not None and self._open.is_own
                and self._pending.get(self._open.cli_session_id)):
            return self._open.cli_session_id
        return next((sid for sid, queue in self._pending.items() if queue), None)

    def on_answer(self):
        session_id = self._next_waiting_session()
        if session_id is None:
            self._feedback("Claude isn't waiting for an answer.")
            return
        request = self._pending[session_id][0]
        own = self.store.get(session_id)
        title = own.title if own else "A session"
        decision = self._ask(title, request, own)
        if decision is None:
            self._feedback("Not answered yet. Claude is still waiting; Ctrl+Shift+A answers.")
            return
        response, said, changes = decision
        self._apply_answer(session_id, request, response, said, changes)

    def _ask(self, title: str, request: PermissionRequest, own: Optional[OwnSession]):
        """Show the dialog for ``request``. Returns (response for Claude Code,
        what to say, changes to keep with the session), or None to answer
        later."""
        if request.is_question:
            dialog = QuestionDialog(self, title, request)
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return None
                if dialog.declined:
                    return (deny_response("The user chose not to answer these questions. "
                                          "Carry on without the answers, or ask in your "
                                          "reply."), "Declined to answer.", {})
                return answer_questions_response(request, dialog.answers()), "Answer sent.", {}
            finally:
                dialog.Destroy()
        if request.is_plan:
            modes = [(value, label) for value, label in PERMISSION_MODES if value != "plan"]
            # It starts on the mode chosen for the session (Change
            # Permission Mode, #189), unless that's plan itself.
            stored = normalize_permission_mode(own.permission_mode) if own is not None else ""
            dialog = PlanDialog(self, title, request, modes,
                                stored if stored in dict(modes) else "acceptEdits")
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return None
                if dialog.approved:
                    mode = dialog.chosen_mode()
                    name = dict(modes)[mode].split(":")[0].lower()
                    # Kept with the session too: every turn is started with
                    # its stored mode, and it must not go back to planning.
                    return (allow_response(request, mode=mode),
                            f"Plan approved. Claude is carrying on in {name} mode.",
                            {"permission_mode": mode})
                note = dialog.note_text()
                return (deny_response(note or "Keep planning: the plan isn't approved yet."),
                        "Claude will keep planning.", {})
            finally:
                dialog.Destroy()
        dialog = PermissionDialog(self, title, request)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            if dialog.choice == ALLOW:
                return allow_response(request), "Allowed.", {}
            if dialog.choice == ALLOW_SESSION:
                changes = {}
                mode = request.session_mode()
                if mode:
                    changes["permission_mode"] = mode
                else:
                    kept = list(own.allowed_tools) if own is not None else []
                    kept += [r for r in request.session_rules() if r not in kept]
                    changes["allowed_tools"] = kept
                return (allow_response(request, for_session=True),
                        "Allowed for the rest of this session.", changes)
            reason = dialog.reason_text()
            return deny_response(reason), "Refused." + (" Claude has your reason." if reason
                                                         else ""), {}
        finally:
            dialog.Destroy()

    def _apply_answer(self, session_id: str, request: PermissionRequest, response: dict,
                      said: str, changes: dict):
        queue = self._pending.get(session_id) or []
        if request in queue:
            queue.remove(request)
        runner = self._runners.get(session_id)
        if runner is None or not runner.respond(request.request_id, response):
            self._feedback("That isn't waiting any more: the turn has ended.")
            self._update_send_state()
            return
        if changes:
            self._store_write(self.store.update, session_id, **changes)
            self._update_heading()
        if queue:
            said += f" Next: {queue[0].summary()}. Ctrl+Shift+A answers."
        self._feedback(said)
        self._update_send_state()
        self.refresh_sessions()

    def on_new_session(self, _event=None):
        lookup = platform_paths.find_claude()
        if not lookup.path:
            wx.MessageBox(lookup.problem, APP_NAME, wx.OK | wx.ICON_ERROR, self)
            return
        exe = lookup.path
        root = str(platform_paths.default_projects_root())
        # The folders sessions have used (#154): the store's too, as the
        # snapshot may not have the newest of them yet.
        sessions = list(self._snapshot.sessions) + [s.to_info() for s in self.store.all()]
        # Whether each folder still exists is asked off the UI thread, with a
        # limit: a folder on a network drive that's gone can take a long time
        # to answer, and then the list is offered without that check.
        try:
            recent = self._pool.submit(workplaces.recent_folders, sessions).result(timeout=1.5)
        except Exception:  # noqa: BLE001 - timed out, or the pool is closing
            # Unchecked, so the Folder box starts at the projects folder
            # rather than at one that may be gone (asking again could hang).
            same = lambda folder: os.path.normcase(os.path.normpath(folder))  # noqa: E731
            recent = [root] + [folder for folder in workplaces.recent_folders(
                sessions, exists=lambda folder: True) if same(folder) != same(root)]
        dialog = NewSessionDialog(self, root, recent=recent, projects_root=root,
                                  feedback=self._feedback,
                                  clone_root=str(platform_paths.clone_root()))
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            folder, title, mode, message, model = dialog.values()
            effort = dialog.effort()
        finally:
            dialog.Destroy()
        session_id = new_session_id()
        command = build_new_command(exe, session_id, title, mode, model, effort=effort)
        own = OwnSession(cli_session_id=session_id, title=title, cwd=folder,
                         permission_mode=mode, started=False, model=model, effort=effort)
        if not self._store_write(self.store.add, own):
            return
        # Start the turn first, so the session view sees it running.
        self._start_turn(own.cli_session_id, command, folder, message, title)
        self.open_session(own.to_info())
        self.refresh_sessions()

    # ------------------------------------------------- attachments (#22)

    def on_attach_files(self):
        """Attach files or images to the next message (Ctrl+Shift+F)."""
        info = self._open
        if info is None or not info.is_own:
            self._feedback("Attachments are for The Chat Place's own sessions: load one first.")
            return
        wildcard = ("All files (*.*)|*.*|Images (*.png;*.jpg;*.jpeg;*.gif;*.webp)|"
                    "*.png;*.jpg;*.jpeg;*.gif;*.webp")
        dialog = wx.FileDialog(self, "Attach Files", defaultDir=info.cwd or "",
                               wildcard=wildcard,
                               style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                self.reply_text.SetFocus()
                return
            paths = list(dialog.GetPaths())
        finally:
            dialog.Destroy()
        self._add_attachments(info.cli_session_id, paths)

    def _add_attachments(self, session_id: str, paths: List[str]):
        current = self._attachments.setdefault(session_id, [])
        added = [p for p in paths if p not in current]
        current.extend(added)
        self._show_attachments()
        self.reply_text.SetFocus()
        if added:
            names = ", ".join(os.path.basename(p) for p in added)
            self._feedback(f"Attached {names}. {attachments.describe(current)}.")

    def _clear_attachments(self, session_id: str):
        self._attachments.pop(session_id, None)
        self._show_attachments()

    def _show_attachments(self):
        """The loaded session's attachments, under the reply box."""
        info = self._open
        paths = self._attachments.get(info.cli_session_id, []) if info and info.is_own else []
        if paths:
            self.attach_list.Set([os.path.basename(p) for p in paths])
            self.attach_list.SetSelection(0)
            set_accessible_name(self.attach_list,
                                f"{attachments.describe(paths)}. Delete removes one")
        if self.attach_list.IsShown() != bool(paths):
            self.attach_list.Show(bool(paths))
            self.own_reply.Layout()
            self.session_view.Layout()

    def _on_attach_key(self, event):
        if event.GetKeyCode() not in (wx.WXK_DELETE, wx.WXK_BACK) or self._open is None:
            event.Skip()
            return
        paths = self._attachments.get(self._open.cli_session_id, [])
        index = self.attach_list.GetSelection()
        if not (0 <= index < len(paths)):
            return
        gone = paths.pop(index)
        if not paths:
            self.reply_text.SetFocus()  # before the list hides, never after
        self._show_attachments()
        self._feedback(f"Removed {os.path.basename(gone)}. {attachments.describe(paths)}.")
        if paths:
            self.attach_list.SetSelection(min(index, len(paths) - 1))
            self.attach_list.SetFocus()

    def _on_reply_paste(self, event):
        """Ctrl+V with a picture on the clipboard (a screenshot from
        Win+Shift+S, say) attaches it; text pastes as usual."""
        info = self._open
        bitmap = self._clipboard_image() if info is not None and info.is_own else None
        if bitmap is None:
            event.Skip()
            return
        path = attachments.pasted_image_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not bitmap.SaveFile(str(path), wx.BITMAP_TYPE_PNG):
                raise OSError("the picture couldn't be saved")
        except OSError as exc:
            self._feedback(f"Couldn't attach the pasted picture: {exc}")
            return
        self._add_attachments(info.cli_session_id, [str(path)])

    def _clipboard_image(self):
        """The clipboard's picture, when it holds a picture and no text."""
        if not wx.TheClipboard.Open():
            return None
        try:
            if wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_UNICODETEXT)) or \
                    not wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_BITMAP)):
                return None
            data = wx.BitmapDataObject()
            if not wx.TheClipboard.GetData(data):
                return None
            bitmap = data.GetBitmap()
            return bitmap if bitmap.IsOk() else None
        finally:
            wx.TheClipboard.Close()

    def on_continue_here(self, _event=None, remote_control: str = ""):
        """Carry on a desktop app or terminal session (#158) in The Chat Place,
        as a copy (#189).
        ``remote_control`` "on" puts the copy on Remote Control from its
        first turn (#96)."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.is_own:
            self._feedback(f"{info.title} is already a Chat Place session; reply to it here.")
            return
        if info.cowork:
            # Its transcript is in the session's own Claude Code home, where
            # claude -p on this PC can't resume it (#91).
            wx.MessageBox("A Cowork session can't be continued here. Open it in Claude "
                          "to carry on there.", APP_NAME,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        if info.transcript_path() is None:
            wx.MessageBox("This session's conversation is no longer on disk, so there is "
                          "nothing to continue from.", APP_NAME, wx.OK | wx.ICON_INFORMATION,
                          self)
            return
        if not info.cwd or not os.path.isdir(info.cwd):
            wx.MessageBox(f"This session's folder ({info.cwd or 'unknown'}) doesn't exist "
                          "any more, so it can't be continued here.", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            return
        lookup = platform_paths.find_claude()
        if not lookup.path:
            wx.MessageBox(lookup.problem, APP_NAME, wx.OK | wx.ICON_ERROR, self)
            return
        dialog = NewSessionDialog(self, info.cwd, continue_from=info.title)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            _folder, title, mode, message, model = dialog.values()
            effort = dialog.effort()
        finally:
            dialog.Destroy()
        new_id = new_session_id()
        try:
            command = build_fork_command(
                lookup.path, info.cli_session_id, new_id, title, mode, model,
                taken_ids={s.cli_session_id for s in self.store.all()}
                | set(self._snapshot.desktop_cli_ids),
                cowork_ids=self._snapshot.cowork_cli_ids, effort=effort)
        except (ResumeRefused, ValueError) as exc:
            wx.MessageBox(str(exc), APP_NAME, wx.OK | wx.ICON_WARNING, self)
            return
        own = OwnSession(cli_session_id=new_id, title=title, cwd=info.cwd,
                         permission_mode=mode, started=False, model=model, effort=effort,
                         fork_source=info.cli_session_id, forked_from=info.title,
                         remote_control=remote_control)
        if not self._store_write(self.store.add, own):
            return
        if info.key in self.tool_activity:
            # Carried on here, it shows the tool calls you were following.
            try:
                self.tool_activity.add(own.to_info().key)
            except OSError:
                pass
        self._start_turn(new_id, command, info.cwd, message, title)
        self.open_session(own.to_info())
        self.refresh_sessions()
        if remote_control == "on":
            self._feedback(f"{title} starts on Remote Control; it's said once it connects.")

    def on_send(self, _event=None):
        info = self._open
        if info is None or not info.is_own:
            return
        message = self.reply_text.GetValue().strip()
        session_id = info.cli_session_id
        attached = list(self._attachments.get(session_id, []))
        if not message and not attached:
            self._feedback("Type a message first.")
            self.reply_text.SetFocus()
            return
        runner = self._runners.get(session_id)
        if runner is not None and runner.cancelled:
            # Queuing behind a stopped turn would only bounce back when the
            # stop lands, so keep the text and say why.
            self._feedback("Still stopping. Send again in a moment.")
            self.reply_text.SetFocus()
            return
        if runner is not None and runner.waiting_on_background:
            # Claude has answered and is only waiting for background work
            # (#161): the message goes in now, as the next one, rather than
            # waiting for work that may take an hour.
            prompt, images = attachments.build(message, attached)
            if runner.send_follow_up(prompt, images):
                self._clear_attachments(session_id)
                self.reply_text.SetValue("")
                self._drafts.pop(session_id, None)
                self._update_send_state()
                said = announce.sent_text(info.title, message, self.speech.announce,
                                          self.speech.announce_own)
                if images:
                    said += f" With {len(images)} image{'s' if len(images) != 1 else ''}."
                self._feedback(said)
                self.reply_text.SetFocus()
                return
            # Claude started on the finished work just now: queue it.
        if runner is not None:
            # Queue it rather than refuse: the turn's reply comes first, then
            # this goes. More while one waits joins it as one message.
            waiting = bool(self._queued.get(session_id))
            # Queued text carries attachments as @"path": images included.
            queued_text, _images = attachments.build(message, attached, images_inline=False)
            self._queued.setdefault(session_id, []).append(queued_text)
            self._clear_attachments(session_id)
            self.reply_text.SetValue("")
            self._drafts.pop(session_id, None)
            self._update_send_state()
            self._rebuild_chat_list()  # the queued message joins the list
            # Only the newly added words are read back, not the whole queue.
            self._feedback(announce.queued_text(info.title, message, self.speech.announce,
                                                self.speech.announce_own,
                                                added=bool(waiting)))
            self.reply_text.SetFocus()
            return
        prompt, images = attachments.build(message, attached)
        problem = self._send_now(session_id, prompt, images=images, spoken=message,
                                 attached=attached)
        if problem:
            wx.MessageBox(problem, APP_NAME, wx.OK | wx.ICON_WARNING, self)
            return
        self._clear_attachments(session_id)
        self.reply_text.SetValue("")
        self._drafts.pop(session_id, None)
        # Stay in the reply box; new messages arrive at the end of the list.
        self.reply_text.SetFocus()

    def _send_now(self, session_id: str, message: str, queued: bool = False,
                  images: Optional[List[dict]] = None, spoken: Optional[str] = None,
                  attached: Optional[List[str]] = None) -> Optional[str]:
        """Start a turn with ``message``. Returns why it can't, or None once
        sent; the caller decides how to say it (a dialog when Kelly pressed
        Send, speech for a queued message going out on its own)."""
        # Read fresh, not from the list's snapshot: up to 5 seconds old, it
        # still shows The Chat Place's own just-finished turn as busy.
        live = hub.load_live_status().get(session_id)
        if live is not None and live.status == "busy":
            return ("This session is running somewhere else right now, so "
                    "The Chat Place won't send into it.")
        lookup = platform_paths.find_claude()
        if not lookup.path:
            return lookup.problem
        exe = lookup.path
        own = self.store.get(session_id)
        if own is None:
            return "That session is no longer in The Chat Place's list."
        try:
            if self._session_exists(own):
                command = build_resume_command(
                    exe, own.cli_session_id, own.permission_mode,
                    own_ids={s.cli_session_id for s in self.store.all()},
                    desktop_ids=self._snapshot.desktop_cli_ids, model=own.model,
                    allowed_tools=own.allowed_tools, title=own.title, effort=own.effort)
            else:
                # The first turn never got as far as creating the session:
                # start it again rather than resume something that isn't there.
                if own.cli_session_id in self._snapshot.desktop_cli_ids:
                    raise ResumeRefused("That id belongs to a Claude desktop app session.")
                if own.fork_source:
                    # A copy of another session whose first turn didn't get
                    # going: copy it again.
                    command = build_fork_command(exe, own.fork_source, own.cli_session_id,
                                                 own.title, own.permission_mode, own.model,
                                                 taken_ids=self._snapshot.desktop_cli_ids,
                                                 cowork_ids=self._snapshot.cowork_cli_ids,
                                                 effort=own.effort)
                else:
                    command = build_new_command(exe, own.cli_session_id, own.title,
                                                own.permission_mode, own.model,
                                                allowed_tools=own.allowed_tools,
                                                effort=own.effort)
        except (ResumeRefused, ValueError) as exc:
            return str(exc)
        self._start_turn(own.cli_session_id, command, own.cwd, message, own.title,
                         queued=queued, images=images, spoken=spoken, attached=attached)
        return None

    def _session_exists(self, own: OwnSession) -> bool:
        if own.started:
            return True
        return platform_paths.transcript_path(own.cwd, own.cli_session_id) is not None

    def _start_turn(self, session_id: str, command, cwd: str, prompt: str, title: str,
                    queued: bool = False, images: Optional[List[dict]] = None,
                    spoken: Optional[str] = None, attached: Optional[List[str]] = None):
        """``spoken`` is what's read back (what you typed, without the
        attachment lines added to ``prompt``)."""
        holder = {"id": session_id}

        def on_event(event: TurnEvent):
            wx.CallAfter(self._on_turn_event, holder, title, event)

        own = self.store.get(session_id)
        remote = ({"name": title, "reattach": own.bridge_session_id}
                  if own is not None and self._remote_control_on(own) else None)
        runner = TurnRunner(command, cwd, prompt, on_event, images=images,
                            remote_control=remote)
        # What you typed and attached, to give back if the turn never starts.
        runner.typed = spoken if spoken is not None else prompt
        runner.attached = list(attached or [])
        self._runners[session_id] = runner
        self._denials[session_id] = []
        runner.start()
        self._update_send_state()
        said = announce.sent_text(title, spoken if spoken is not None else prompt,
                                  self.speech.announce, self.speech.announce_own,
                                  queued=queued)
        if images:
            said += f" With {len(images)} image{'s' if len(images) != 1 else ''}."
        self._feedback(said)
        self._store_write(self.store.update, session_id, state=IDLE, detail="",
                          last_activity_ms=int(time.time() * 1000))

    def _on_turn_event(self, holder, title, event: TurnEvent):
        if self._gone():
            return
        session_id = holder["id"]
        if event.kind == "started":
            reported = event.session_id
            if reported and reported != session_id and session_id in self._runners:
                # Claude chose a different id than the one we asked for.
                self._store_write(self.store.rename_id, session_id, reported)
                try:
                    self.groups.rename_key(f"own:{session_id}", f"own:{reported}")
                except OSError:
                    pass  # its groups lose it; nothing else does
                try:
                    self.hidden.rename_key(f"own:{session_id}", f"own:{reported}")
                except OSError:
                    pass  # it shows again; nothing else is lost
                try:
                    self.tool_activity.rename_key(f"own:{session_id}", f"own:{reported}")
                except OSError:
                    pass  # its tool activity is hidden next time; nothing else is lost
                self._runners[reported] = self._runners.pop(session_id)
                self._denials[reported] = self._denials.pop(session_id, [])
                for per_session in (self._drafts, self._queued, self._pending,
                                    self._said_problems):
                    if session_id in per_session:
                        per_session[reported] = per_session.pop(session_id)
                if self._open is not None and self._open.cli_session_id == session_id:
                    self._open.cli_session_id = reported
                    self._open.key = f"own:{reported}"
                    # The old reader points at the old id's transcript.
                    self._reader = None
                    self._open_generation += 1
                    self._chat_loaded = False
                holder["id"] = reported
                session_id = reported
            self._check_model(session_id, title, (event.data or {}).get("model", ""))
            own = self.store.get(session_id)
            if own is not None and not own.started:
                self._store_write(self.store.update, session_id, started=True)
            self._status(f"{title}: Claude is working.")
            return
        is_open_now = self._open is not None and self._open.cli_session_id == session_id
        if event.kind == "tool":
            self._status(f"{title}: Claude is using {event.text}.")
            if is_open_now and self._show_activity:
                self._queue_activity("tool", event.detail or event.text)
            return
        if event.kind == "text":
            self._status(f"{title}: {announce.first_sentence(event.text)}")
            if is_open_now and self._show_activity:
                self._queue_activity("text", event.text)
            return
        if event.kind == "limits" and event.data is not None:
            self._limits = event.data
            warning = usage.limit_warning(event.data)
            key = ("limit", usage.limit_warning_key(event.data))
            if warning and key not in self._warned:
                self._warned.add(key)
                self._say(warning)
            return
        if event.kind == "remote_control":
            self._on_remote_control(session_id, title, event.data or {})
            return
        if event.kind == "compacted":
            self._say(f"{title}: Claude Code compacted the conversation to free the context.")
            return
        if event.kind == "denied":
            self._denials.setdefault(session_id, []).append(event.text)
            self._status(f"{title}: permission denied, {event.text}")
            return
        if event.kind == "permission_cancelled":
            queue = self._pending.get(session_id, [])
            kept = [r for r in queue if r.request_id != event.text]
            if len(kept) != len(queue):
                if kept:
                    self._pending[session_id] = kept
                else:
                    self._pending.pop(session_id, None)
                self._status(f"{title}: answered elsewhere.")
                self._update_send_state()
                self.refresh_sessions()
            return
        if event.kind == "permission" and event.request is not None:
            if session_id not in self._runners:
                return  # the turn already ended; nothing is waiting
            queue = self._pending.setdefault(session_id, [])
            queue.append(event.request)
            if len(queue) == 1:
                self._say(f"{title} needs you. {event.request.summary()}. "
                          "Ctrl+Shift+A answers.")
                self._notify(self._own_key(session_id), f"{title} needs you",
                             f"{event.request.summary()}. Ctrl+Shift+A answers.",
                             needs_you=True)
            else:
                self._status(f"{title}: {len(queue)} things are waiting for you.")
            self._update_send_state()
            self.refresh_sessions()
            return
        if event.kind == "background":
            if is_open_now:
                self._update_send_state()  # what's still running
            return
        if event.kind == "waiting":
            self._answered_with_background(session_id, title, event, is_open_now)
            return
        if event.kind in ("finished", "failed"):
            # Nothing can be waiting once the turn is over.
            self._pending.pop(session_id, None)
            parser = getattr(self._runners.get(session_id), "parser", None)
            if parser is not None and getattr(parser, "context_window", 0):
                self._windows[session_id] = parser.context_window
                if self._reader is not None and self._reader.transcript.model:
                    self._model_windows[self._reader.transcript.model] = parser.context_window
            if parser is not None and parser.commands:
                self._commands[_folder_key(self._runners[session_id].cwd)] = \
                    usable_commands(parser.commands)
            if is_open_now:
                # The reply is announced next; activity not yet spoken is
                # older news, and its last text is that same reply.
                self._clear_activity()
            # UI first, store writes after: a failed write must not leave the
            # session looking busy for good.
            runner = self._runners.pop(session_id, None)
            denials = event.denials or self._denials.pop(session_id, [])
            self._denials.pop(session_id, None)
            is_open = self._open is not None and self._open.cli_session_id == session_id
            self._update_send_state()
            # Text to give back, oldest first: a first message that never
            # reached Claude, then anything queued behind it.
            unsent = []
            if (event.kind == "failed" and runner is not None and not runner.cancelled
                    and (not runner.session_started
                         or getattr(runner, "stopped_before_answer", False))):
                typed = getattr(runner, "typed", runner.prompt)
                if typed:
                    unsent.append(typed)
                attached_back = getattr(runner, "attached", [])
                if attached_back:
                    # Images lived only in the turn: put them all back.
                    waiting_now = self._attachments.get(session_id, [])
                    self._attachments[session_id] = attached_back + [
                        p for p in waiting_now if p not in attached_back]
                    if is_open:
                        self._show_attachments()
            queued = self._take_queued(session_id)
            if queued and is_open:
                self._rebuild_chat_list()  # sent, or given back: no longer queued
            if event.kind == "failed" or event.is_error:
                state, detail = NEEDS_YOU, announce.status_text(event.text or "error", 120)
            elif denials:
                count = len(denials)
                state = NEEDS_YOU
                detail = f"{count} tool{'s were' if count != 1 else ' was'} refused"
            else:
                state, detail = IDLE, ""
            said_problem = self._said_problems.pop(session_id, "")
            if state == IDLE and said_problem:
                # Said when Claude answered; still needs you (#161).
                state, detail = NEEDS_YOU, said_problem
            self._store_write(self.store.update, session_id, state=state, detail=detail,
                              unread=not is_open,
                              last_activity_ms=int(time.time() * 1000))
            self._notify_turn_end(session_id, title, event, denials, detail,
                                  stopped=runner is not None and runner.cancelled)
            if event.kind == "failed" or event.is_error:
                spoken = f"{title}: the turn failed. {usage.friendly_error(event.text)}"
                self._say(spoken)
            else:
                text = announce.reply_text(title, event.text, self.speech.announce)
                if denials and self.speech.enabled:
                    text = (text or f"{title} finished.") + f" {detail}: " + "; ".join(denials)
                if text:
                    self._say(text)
                else:
                    reply = announce.first_sentence(event.text) if event.text else ""
                    self._status(f"{title} finished. {reply}".strip())
            if is_open:
                self._changes_due = True
                self._refresh_chat()
            if queued:
                # After a failure the queued message goes back instead: what
                # it follows didn't happen. Before the list refresh, so the
                # list sees the new turn running.
                problem = "the turn before it failed"
                if event.kind != "failed" and not event.is_error:
                    problem = self._send_now(session_id, queued, queued=True)
                if problem:
                    unsent.append(queued)
                    # Spoken, not a dialog: this send wasn't Kelly pressing a
                    # key, and he may be in another session or another app.
                    # Feedback queues behind the reply instead of cutting it
                    # off; Ctrl+Shift+R must still repeat it if a keypress
                    # silenced it.
                    refused = (f"Your queued message for {title} wasn't sent: "
                               f"{problem.rstrip('.')}. It's back in the message box.")
                    self._feedback(refused)
                    self._last_announcement = refused
            if unsent:
                self._give_back(session_id, "\n\n".join(unsent), is_open)
            self._update_send_state()
            self.refresh_sessions()

    def _answered_with_background(self, session_id: str, title: str, event: TurnEvent,
                                  is_open: bool):
        """Claude answered and left work running in the background (#161).
        The turn stays open so the work can finish and Claude can carry on;
        the reply is said now, as a finished turn's is, with what's still
        running, and anything queued goes in now rather than after the work."""
        background = announce.background_text((event.data or {}).get("tasks") or [])
        denials = event.denials or self._denials.get(session_id, [])
        self._denials[session_id] = []  # said now, not again at the turn's end
        if event.is_error:
            self._said_problems[session_id] = announce.status_text(event.text or "error", 120)
        elif denials:
            count = len(denials)
            self._said_problems[session_id] = (
                f"{count} tool{'s were' if count != 1 else ' was'} refused")
        if is_open:
            self._clear_activity()
        self._store_write(self.store.update, session_id, unread=not is_open,
                          last_activity_ms=int(time.time() * 1000))
        key = self._own_key(session_id)
        if event.is_error:
            self._notify(key, f"{title}: something failed",
                         f"{usage.friendly_error(event.text)} {background}", needs_you=True)
            self._say(f"{title}: something failed. {usage.friendly_error(event.text)} "
                      f"{background}")
        else:
            count = len(denials)
            detail = (f"{count} tool{'s were' if count != 1 else ' was'} refused"
                      if denials else "")
            if denials:
                self._notify(key, f"{title} needs you", f"{detail}: " + "; ".join(denials),
                             needs_you=True)
            else:
                self._notify(key, f"{title} answered",
                             f"{event.text or 'Claude answered.'} {background}")
            text = announce.reply_text(title, event.text, self.speech.announce)
            if denials and self.speech.enabled:
                text = (text or f"{title} answered.") + f" {detail}: " + "; ".join(denials)
            if text:
                self._say(f"{text} {background}")
            else:
                reply = announce.first_sentence(event.text) if event.text else ""
                self._status(f"{title} answered. {reply} {background}")
        queued = self._take_queued(session_id)
        runner = self._runners.get(session_id)
        if queued:
            if runner is not None and runner.send_follow_up(queued):
                self._feedback(f"Sent your queued message. {title} is working.")
            else:
                # Claude moved on already: it goes when the turn ends.
                self._queued.setdefault(session_id, []).insert(0, queued)
            if is_open:
                self._rebuild_chat_list()
        if is_open:
            self._changes_due = True
            self._refresh_chat()
        self._update_send_state()
        self.refresh_sessions()

    def _take_queued(self, session_id: str) -> Optional[str]:
        """All of a session's queued messages, as the one message they're
        sent as, and the queue emptied."""
        waiting = self._queued.pop(session_id, None)
        return "\n\n".join(waiting) if waiting else None

    def _queued_rows(self) -> List[ChatMessage]:
        """The loaded session's queued messages, as rows after its messages."""
        info = self._open
        if info is None or not info.is_own:
            return []
        return [ChatMessage(QUEUED, text, key=f"queued:{info.cli_session_id}:{i}")
                for i, text in enumerate(self._queued.get(info.cli_session_id, []))]

    def _selected_queued(self) -> Optional[int]:
        """Which queued message is selected in the messages list, if one is."""
        message = self._selected_message()
        if message is None or message.kind != QUEUED:
            return None
        return int(message.key.rsplit(":", 1)[1])

    def remove_queued(self):
        """Remove the selected queued message (Delete, or the context menu)."""
        index = self._selected_queued()
        info = self._open
        if index is None or info is None:
            return
        waiting = self._queued.get(info.cli_session_id, [])
        if not 0 <= index < len(waiting):
            return
        del waiting[index]
        if not waiting:
            self._queued.pop(info.cli_session_id, None)
        row = self.chat_list.GetSelection()
        self._rebuild_chat_list()
        self.chat_list.SetSelection(min(row, self.chat_list.GetCount() - 1))
        self._update_send_state()
        self._feedback("Queued message removed. " + (
            f"{len(waiting)} still queued." if waiting else "Nothing is queued now."))

    def send_queued_now(self):
        """Send the selected queued message now: Claude stops what it's doing
        and takes it straight away, in the same turn, as the desktop app's
        Send Now does."""
        index = self._selected_queued()
        info = self._open
        if index is None or info is None:
            return
        runner = self._runners.get(info.cli_session_id)
        waiting = self._queued.get(info.cli_session_id, [])
        if runner is None or not 0 <= index < len(waiting):
            return
        text = waiting[index]
        if runner.waiting_on_background and runner.send_follow_up(text):
            # Claude is idle, waiting on background work (#161): nothing to
            # stop, so it simply goes as the next message.
            del waiting[index]
            if not waiting:
                self._queued.pop(info.cli_session_id, None)
            self._rebuild_chat_list()
            self._update_send_state()
            self._feedback(f"Sent now to {info.title}.")
            return
        if not runner.send_now(text):
            self._feedback("The turn is already ending: the message stays queued and goes "
                           "when it's done.")
            return
        del waiting[index]
        if not waiting:
            self._queued.pop(info.cli_session_id, None)
        self._rebuild_chat_list()
        self._update_send_state()
        self._feedback(f"Sent now. {info.title} is stopping what it was doing to answer it.")

    def edit_queued(self):
        """Take the selected queued message back into the reply box, to
        change it and send it again."""
        index = self._selected_queued()
        info = self._open
        if index is None or info is None:
            return
        waiting = self._queued.get(info.cli_session_id, [])
        if not 0 <= index < len(waiting):
            return
        text = waiting.pop(index)
        if not waiting:
            self._queued.pop(info.cli_session_id, None)
        self._rebuild_chat_list()
        self._give_back(info.cli_session_id, text, True)
        self._update_send_state()
        self.reply_text.SetFocus()
        self._feedback("Queued message back in the message box. Send queues it again, "
                       "after any others still queued.")

    def _give_back(self, session_id: str, message: str, is_open: bool):
        """Put ``message`` back in the session's reply box, before anything
        typed since (it was written first), so nothing is lost. The caret
        stays at the end, where Kelly was typing."""
        if is_open:
            typed = self.reply_text.GetValue()
            self.reply_text.SetValue(f"{message}\n\n{typed.lstrip()}" if typed.strip()
                                     else message)
            self.reply_text.SetInsertionPointEnd()
        else:
            typed = self._drafts.get(session_id, "")
            self._drafts[session_id] = (f"{message}\n\n{typed.lstrip()}" if typed.strip()
                                        else message)

    def on_stop(self, _event=None):
        info = self._open
        runner = self._runners.get(info.cli_session_id) if info else None
        if runner is None:
            self._feedback("Nothing is running.")
            return
        runner.cancel()
        queued = self._take_queued(info.cli_session_id)
        if queued:
            self._rebuild_chat_list()
            self._give_back(info.cli_session_id, queued, True)
            self._update_send_state()
            self._feedback("Stopping. Your queued message is back in the message box.")
            return
        self._feedback("Stopping.")

    # ------------------------------------------------------- settings, about

    # ------------------------------------------------- views and groups (#31, #32)

    def _group_names(self) -> List[str]:
        """Every group: The Chat Place's own, then the desktop app's (#51). A
        desktop group with the name of one of ours (in any case) is the same
        group here."""
        names = list(self.groups.names())
        known = {n.casefold() for n in names}
        names += [n for n in self._snapshot.desktop_groups.names if n.casefold() not in known]
        return names

    def _view_group_missing(self) -> Optional[str]:
        """The shown group's name, when no such group exists any more."""
        view = self.speech.session_view
        if not view.startswith(GROUP_VIEW_PREFIX):
            return None
        name = view[len(GROUP_VIEW_PREFIX):]
        return None if name.casefold() in {n.casefold() for n in self._group_names()} \
            else name

    def _build_show_menu(self):
        """View, Show Sessions: the fixed views, then one item per group."""
        self._menu_group_names = self._group_names()
        for item in list(self.show_menu.GetMenuItems()):
            self.Unbind(wx.EVT_MENU, id=item.GetId())
            self.show_menu.Delete(item)
        self.view_items = {}
        choices = list(VIEWS)
        names = self._menu_group_names
        for name in names:
            choices.append((group_view(name), "Group: " + name.replace("&", "&&")))
        missing = self._view_group_missing()
        if missing is not None:
            # Kept, not reset: the desktop app's settings can be caught
            # mid-rewrite. Its own item keeps the radio items honest.
            choices.append((self.speech.session_view,
                            f"Group: {missing.replace('&', '&&')} (not found)"))
        # One run of radio items, no separator: a separator starts a second
        # radio group, and then two items would read as checked.
        for view, label in choices:
            item = self.show_menu.AppendRadioItem(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, v=view: self.on_view(v), item)
            self.view_items[view] = item
        for view, item in self.view_items.items():
            item.Check(view == self.speech.session_view)

    def _in_current_view(self, sessions: List[SessionInfo]) -> List[SessionInfo]:
        """The sessions the list shows now, each told its groups for its row."""
        desktop = self._snapshot.desktop_groups.by_session
        for info in sessions:
            info.hidden = info.key in self.hidden
            groups = list(self.groups.groups_of(info.key))
            if info.key in desktop:
                theirs = desktop[info.key]
                ours = self.groups.find(theirs)
                if (ours or theirs) not in groups:
                    groups.append(ours or theirs)
            info.groups = tuple(groups)
        shown = [s for s in sessions if in_view(s, self.speech.session_view)]
        words = self._session_filter.casefold().split()
        if words:
            shown = [s for s in shown
                     if all(w in self._filter_text(s) for w in words)]
        return shown

    @staticmethod
    def _filter_text(info: SessionInfo) -> str:
        """What Ctrl+F in the list searches: its title, folder and what it
        needs, whichever columns its row shows (#134), with "Cowork" for a
        Cowork session whose folder has another name (#91)."""
        kind = " Cowork" if info.cowork else " terminal" if info.is_terminal else ""
        return f"{info.title} {info.repo} {info.detail}{kind}".casefold()

    def _update_list_label(self, shown: int):
        """The list's label, and so its name, says what it's showing."""
        view = self.speech.session_view
        rest = ""
        if view != VIEW_ALL or self._session_filter:
            total = sum(1 for s in self._snapshot.sessions
                        if not s.archived and not s.hidden)
            what = view_spoken(view) if view != VIEW_ALL else ""
            if self._session_filter:
                matching = f'matching "{self._session_filter}"'
                what = f"{what}, {matching}" if what else matching
            rest = f", {what}, {shown} of {total}"
        label = "Session list" + rest
        # Alt+L stays on "list" whatever follows it.
        shown_label = "Session &list" + rest.replace("&", "&&") + ":"
        if self.sessions_label.GetLabel() != shown_label:
            self.sessions_label.SetLabel(shown_label)
            set_accessible_name(self.session_list, label)

    def on_view(self, view: str):
        """View, Show Sessions: list only these, and remember it."""
        shown = self._apply_view(view)
        self._find_all_waiting = None  # they were for the sessions listed before
        count = "no sessions" if not shown else (
            "1 session" if len(shown) == 1 else f"{len(shown)} sessions")
        self._feedback(f"Showing {view_spoken(view)}: {count}.")

    def _apply_view(self, view: str) -> List[SessionInfo]:
        """Show ``view``'s sessions, tick it on the menu and remember it,
        without saying anything; the sessions now shown."""
        self.speech.session_view = view
        if view in self.view_items:
            self.view_items[view].Check(True)
        try:
            self.speech.save()
        except OSError as exc:
            self._status(f"Couldn't save which sessions to show: {exc}")
        shown = self._in_current_view(list(self._snapshot.sessions))
        self._update_session_list(shown)
        self._update_list_label(len(shown))
        return shown

    # ----------------------------------------------------------- search (#21)

    def on_find(self):
        """Ctrl+F: in the session list, show only sessions matching some text;
        anywhere else, find text in the loaded session's messages."""
        if wx.Window.FindFocus() is self.session_list:
            self._find_sessions()
        else:
            self._find_in_messages()

    def _ask_text(self, title: str, prompt: str, value: str) -> Optional[str]:
        dialog = wx.TextEntryDialog(self, prompt, title, value)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            return " ".join(dialog.GetValue().split())
        finally:
            dialog.Destroy()

    def _find_sessions(self):
        text = self._ask_text("Find Sessions", "Show sessions whose title, folder or what they "
                              "need contains (empty shows them all):", self._session_filter)
        if text is None:
            self.session_list.SetFocus()
            return
        self._set_session_filter(text)

    def _set_session_filter(self, text: str):
        self._session_filter = text
        shown = self._in_current_view(list(self._snapshot.sessions))
        self._update_session_list(shown)
        self._update_list_label(len(shown))
        self.session_list.SetFocus()
        if not text:
            self._feedback(f"Showing all {len(shown)} sessions in this view.")
        elif shown:
            self._feedback(f'{len(shown)} session{"s" if len(shown) != 1 else ""} matching '
                           f'"{text}". Escape shows them all.')
        else:
            self._feedback(f'No sessions matching "{text}". Escape shows them all.')

    def _find_in_messages(self):
        if self._open is None:
            self._feedback("No session loaded. In the session list, Ctrl+F finds sessions.")
            return
        returning_to = wx.Window.FindFocus()
        text = self._ask_text("Find in Messages", "Find messages containing:", self._find_text)
        if not text:
            (returning_to or self.chat_list).SetFocus()
            return
        self._find_text = text
        self.find_again(True, starting=True)

    def on_find_all(self):
        """View, Find in All Sessions (Ctrl+Shift+S, #109): search every
        session the list shows (its Show Sessions view and any Ctrl+F text)
        in the background, then list the messages found; Enter loads that
        session on that message. Pressed while searching, it stops; pressed
        when results are waiting, it shows them."""
        if self._find_all_busy:
            if not self._find_all_cancel:
                self._find_all_cancel = True
                self._stop_find_all_timer()
                self._feedback("Stopped searching.")
            else:
                self._feedback("Stopping. It ends after the session it's reading.")
            return
        waiting, self._find_all_waiting = self._find_all_waiting, None
        if waiting is not None and time.monotonic() - waiting[0] < FIND_ALL_KEEP_S:
            self._open_find_results(waiting[1])
            return
        returning_to = wx.Window.FindFocus()
        text = self._ask_text("Find in All Sessions",
                              "Find messages, in every session in the list, containing:",
                              self._find_all_text)
        if returning_to:
            returning_to.SetFocus()
        if not text:
            return
        self._find_all_text = text
        targets = []
        for key in self._list_keys:
            info = self._current_info(key)
            if info is not None:
                targets.append(Target(info.key, info.title, info.transcript_path,
                                      info.key in self.tool_activity))
        if not targets:
            self._feedback("No sessions in the list to search.")
            return
        self._find_all_busy = True
        self._find_all_cancel = False
        self._find_all_done = 0
        # Which search this is: a progress timer of an earlier one says nothing.
        self._find_all_run += 1
        run = self._find_all_run
        # Where you were: the results open there only if you still are.
        self._find_all_focus = returning_to
        count = len(targets)
        self._feedback(f'Searching {count} session{"s" if count != 1 else ""} for "{text}". '
                       "Ctrl+Shift+S again stops.")
        self._stop_find_all_timer()
        self._find_all_timer = wx.CallLater(FIND_ALL_PROGRESS_MS, self._say_find_all_progress,
                                            run, count)

        def progress(done):
            self._find_all_done = done  # an int, read on the window's thread

        def work():
            try:
                results = search_sessions(
                    targets, text, progress=progress,
                    cancelled=lambda: self._closing or self._find_all_cancel)
            except Exception as exc:  # noqa: BLE001 - said, not raised
                wx.CallAfter(self._find_all_failed, exc)
                return
            wx.CallAfter(self._find_all_finished, results)
        # A thread of its own, not the pool's: a long search mustn't hold up
        # the snapshots and chat loads the pool's few threads are for.
        threading.Thread(target=work, name="find-all", daemon=True).start()

    def _stop_find_all_timer(self):
        if self._find_all_timer is not None:
            self._find_all_timer.Stop()
            self._find_all_timer = None

    def _say_find_all_progress(self, run: int, count: int):
        """A long search says how far it's got, now and then, so it isn't
        silent. Only the search running now: ``run`` is the one this timer
        was started for."""
        self._find_all_timer = None
        if (self._gone() or run != self._find_all_run or not self._find_all_busy
                or self._find_all_cancel):
            return
        self._feedback(f"Searched {self._find_all_done} of {count} sessions.")
        self._find_all_timer = wx.CallLater(FIND_ALL_PROGRESS_MS, self._say_find_all_progress,
                                            run, count)

    def _find_all_failed(self, exc):
        self._find_all_busy = False
        self._stop_find_all_timer()
        if not self._gone() and not self._find_all_cancel:
            self._say(f"Couldn't search the sessions: {exc}")

    def _find_all_finished(self, results):
        """The search ended. Its results open at once only if you're where
        you were when it began, with nothing else open; otherwise they're
        said and wait for Ctrl+Shift+S, so a list never jumps in front of
        what you've gone on to do."""
        self._find_all_busy = False
        self._stop_find_all_timer()
        if self._gone() or self._find_all_cancel:
            return
        if not results.matches:
            self._say(results.summary())
            return
        here = (self.IsActive() and self.IsEnabled() and not self._modal_open()
                and self._find_all_focus is not None
                and wx.Window.FindFocus() is self._find_all_focus)
        if here:
            self._open_find_results(results)
            return
        self._find_all_waiting = (time.monotonic(), results)
        self._say(f"{results.summary()} Press Ctrl+Shift+S to see them.")

    def _open_find_results(self, results):
        returning_to = wx.Window.FindFocus()
        dialog = FindResultsDialog(self, results, app_name=APP_NAME)
        try:
            dialog.ShowModal()
            chosen = dialog.chosen
        finally:
            dialog.Destroy()
        if chosen is None:
            if returning_to:
                returning_to.SetFocus()
            return
        self._go_to_match(chosen)

    def _go_to_match(self, match):
        """Load the session a Find in All Sessions result is in, on that
        message."""
        info = self._current_info(match.key)
        if info is None:
            self._feedback(f"{match.title} isn't in the session list any more.")
            return
        if self._open is not None and self._open.key == info.key and self._chat_loaded:
            self._select_match(match, loaded=False)
            return
        self._select_on_load = match
        self.open_session(info)

    def _select_match(self, match, loaded: bool) -> bool:
        """Select the message a result found, and say which it is (and, just
        loaded, which session). Found by its place among the session's
        messages, as a key can be shared (text either side of a tool call
        is one reply's)."""
        visible = self._visible_messages()
        row = None
        if 0 <= match.index < len(self._chat_messages):
            message = self._chat_messages[match.index]
            if message.key == match.message_key:
                row = next((i for i, m in enumerate(visible) if m is message), None)
        if row is None and match.message_key and \
                [m.key for m in visible].count(match.message_key) == 1:
            row = [m.key for m in visible].index(match.message_key)
        where = f"Loaded {match.title}. " if loaded else ""
        if row is None or row >= self.chat_list.GetCount():
            count = sum(1 for m in visible if m.kind != QUEUED)
            self._feedback(f"{where}{count} message{'s' if count != 1 else ''}. The message "
                           "found isn't in the list now; "
                           "if it was a tool call, Ctrl+T shows them.")
            return False
        self.chat_list.SetSelection(row)
        self.chat_list.SetFocus()
        self._feedback(f"{where}Message {row + 1} of {self.chat_list.GetCount()}: "
                       f"{visible[row].list_line()}")
        return True

    def find_again(self, forward: bool = True, starting: bool = False):
        """F3 and Shift+F3: the next or previous message containing the text,
        searching the whole text of each message, going round from the other
        end (and saying so). A new search (``starting``) begins with the
        message you're on."""
        if not self._find_text:
            self._find_in_messages()
            return
        visible = self._visible_messages()
        keys = self._chat_keys
        # The rows the list shows, and only while they match the messages.
        if not keys or len(visible) < len(keys) or any(
                visible[i].key != key for i, key in enumerate(keys)):
            self._feedback("No messages to search.")
            return
        count = len(keys)
        words = self._find_text.casefold()
        current = self.chat_list.GetSelection()
        if not 0 <= current < count:
            # Nothing selected: start from the end you're searching from.
            current = -1 if forward else count
            starting = False
        step = 1 if forward else -1
        first = current if starting else current + step
        for offset in range(count):
            index = (first + step * offset) % count
            if words not in visible[index].full_text().casefold():
                continue
            if not starting and index == current:
                note = " It's the only message that matches."
            elif (forward and index < first) or (not forward and index > first):
                note = " Searched round from the other end."
            else:
                note = ""
            self.chat_list.SetSelection(index)
            self.chat_list.SetFocus()
            self._feedback(f"Found in message {index + 1} of {count}: "
                           f"{visible[index].list_line()}.{note}")
            return
        self._feedback(f'No message contains "{self._find_text}".')

    def _group_target(self) -> Optional[SessionInfo]:
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
        return info

    def _choose(self, title: str, prompt: str, choices: List[str],
                selection: Optional[int] = None) -> Optional[int]:
        """A standard single-choice list (wx's own dialog, which screen
        readers handle well). The chosen index, or None."""
        dialog = wx.SingleChoiceDialog(self, prompt, title, choices)
        if selection is not None:
            dialog.SetSelection(selection)  # on the current choice, not the first
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            return dialog.GetSelection()
        finally:
            dialog.Destroy()

    def _ask_group_name(self, title: str, value: str = "") -> Optional[str]:
        dialog = wx.TextEntryDialog(self, "Group name:", title, value)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            return dialog.GetValue()
        finally:
            dialog.Destroy()

    def on_add_to_group(self):
        info = self._group_target()
        if info is None:
            return
        names = self._group_names()
        current = set(info.groups) | set(self.groups.groups_of(info.key))
        choices = [f"{n} (already in it)" if n in current else n for n in names]
        choices.append("New group...")
        index = self._choose("Add to Group", f"Add {info.title} to:", choices)
        if index is None:
            return
        try:
            if index == len(names):
                name = self._ask_group_name("New Group")
                if name is None:
                    return
                name = self.groups.create(name)
                self._build_show_menu()
            else:
                name = names[index]
                if name in current:
                    self._feedback(f"{info.title} is already in {name}.")
                    return
                if self.groups.find(name) is None:
                    # A desktop app group: The Chat Place keeps its own of the
                    # same name, so the two show as one.
                    name = self.groups.create(name)
                    self._build_show_menu()
                else:
                    name = self.groups.find(name)
            added = self.groups.add(name, info.key)
        except (ValueError, OSError) as exc:
            wx.MessageBox(str(exc), APP_NAME, wx.OK | wx.ICON_WARNING, self)
            return
        self._feedback(f"Added {info.title} to {name}." if added
                       else f"{info.title} is already in {name}.")
        self._refresh_list_in_place()

    def on_remove_from_group(self):
        info = self._group_target()
        if info is None:
            return
        names = self.groups.groups_of(info.key)
        if not names:
            desktop = self._snapshot.desktop_groups.by_session.get(info.key)
            self._feedback(
                f"{info.title} is in the desktop app's group {desktop}; change that in the "
                "desktop app." if desktop else f"{info.title} is not in any group.")
            return
        index = self._choose("Remove from Group", f"Remove {info.title} from:", names)
        if index is None:
            return
        try:
            self.groups.remove(names[index], info.key)
        except OSError as exc:
            wx.MessageBox(f"Couldn't save your groups: {exc}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
            return
        still = self._snapshot.desktop_groups.by_session.get(info.key)
        note = (f" It's still in the desktop app's group {still}."
                if still and still.casefold() == names[index].casefold() else "")
        self._feedback(f"Removed {info.title} from {names[index]}.{note}")
        self._refresh_list_in_place()

    def on_manage_groups(self):
        present = {s.key for s in self._snapshot.sessions}
        counts = {name: sum(1 for k in self.groups.members(name) if k in present)
                  for name in self.groups.names()}
        dialog = ManageGroupsDialog(self, self.groups, counts)
        try:
            dialog.ShowModal()
            renamed = dict(dialog.renamed)
        finally:
            dialog.Destroy()
        view = self.speech.session_view
        if view.startswith(GROUP_VIEW_PREFIX) and view[len(GROUP_VIEW_PREFIX):] in renamed:
            self.speech.session_view = group_view(renamed[view[len(GROUP_VIEW_PREFIX):]])
            try:
                self.speech.save()
            except OSError:
                pass
        if self._view_group_missing() is not None:
            self.speech.session_view = VIEW_ALL  # you deleted the group it showed
            try:
                self.speech.save()
            except OSError:
                pass
        self._build_show_menu()
        self._refresh_list_in_place()

    def _refresh_list_in_place(self, rewrite: bool = False):
        """The rows and the view again, from the sessions already read: their
        groups (or the columns, ``rewrite``) changed, not the sessions."""
        shown = self._in_current_view(list(self._snapshot.sessions))
        self._update_session_list(shown,
                                  keep_order=wx.Window.FindFocus() is self.session_list,
                                  rewrite=rewrite)
        self._update_list_label(len(shown))

    def on_sort(self, order: str):
        """View, Sort Sessions: put the list in ``order`` now, keeping you on
        the same session, and remember the choice."""
        if order == self.speech.session_order:
            self._feedback(f"Sessions are already sorted {SORT_SPOKEN[order]}.")
            return
        self.speech.session_order = order
        self.sort_items[order].Check(True)
        try:
            self.speech.save()
        except OSError as exc:
            self._status(f"Couldn't save the sort order: {exc}")
        self.refresh_sessions(resort=True)
        self._feedback(f"Sessions sorted {SORT_SPOKEN[order]}.")

    def on_session_columns(self):
        """View, Session List Columns (#134): choose which parts each session
        row reads and in what order; OK saves the choice and rewrites the rows
        at once, on the same session."""
        selected = self._selected_session()
        sample = selected or _sample_session()
        if selected is not None and FIELD_LAST_MESSAGE not in self.speech.session_fields:
            # Not read while the column is off, so the preview reads this
            # one session's now. It's the one transcript read on this thread:
            # one file's end, usually its last 64 KB, once.
            sample = dataclasses.replace(selected, last_message=fill_last_message(selected))
        dialog = SessionColumnsDialog(self, self.speech.session_fields, sample, self._feedback)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            fields = dialog.fields()
        finally:
            dialog.Destroy()
        if fields == self.speech.session_fields:
            self._feedback("Session list columns unchanged.")
            return
        added_message = (FIELD_LAST_MESSAGE in fields
                         and FIELD_LAST_MESSAGE not in self.speech.session_fields)
        self.speech.session_fields = fields
        if added_message and selected is not None:
            # The row you're on has its message from the start, so it's
            # rewritten once, now, not again when the others' arrive.
            for info in self._snapshot.sessions:
                if info.key == selected.key:
                    info.last_message = sample.last_message
        self._refresh_list_in_place(rewrite=True)
        if added_message:
            # The transcripts' ends aren't read while the column is off:
            # read them now, so the rows say their last messages at once.
            self.refresh_sessions()
        elif FIELD_LAST_MESSAGE not in fields:
            forget_last_messages()  # nothing to keep them for
        names = ", ".join(field_short_name(f) for f in fields)
        try:
            self.speech.save()
        except OSError as exc:
            self._feedback(f"Session list columns changed to {names}, but couldn't be "
                           f"saved, so they last until you close The Chat Place: {exc}")
            return
        self._feedback(f"Session list columns saved. Each session reads: {names}.")

    def on_settings(self, _event=None):
        options = self._speech_options or default_options()
        dialog = SettingsDialog(self, self.speech, options)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            self.speech = dialog.get_settings()
        finally:
            dialog.Destroy()
        try:
            self.speech.save()
        except OSError as exc:
            wx.MessageBox(f"Couldn't save settings: {exc}", APP_NAME,
                          wx.OK | wx.ICON_WARNING, self)
        self._feedback("Settings saved.")
        # The Remote Control default shows in rows and the heading (#96).
        self.refresh_sessions()
        self._update_heading()

    # ------------------------------------------------------------- updates

    def check_for_updates(self, manual: bool = True):
        """Look for a newer release in the background.

        At start (``manual=False``) an available update is only announced,
        never asked about: a dialog popping up seconds after launch would take
        a keystroke meant for something else. "Up to date", "no releases yet"
        and errors go to the status bar. From Help, every outcome is spoken,
        even with announcements set to silent, because Kelly asked.
        """
        if self._update_busy:
            if manual:
                self._feedback("Already checking for updates.")
            return
        self._update_busy = True
        if manual:
            self._feedback("Checking for updates.")

        def work():
            result = self.updates.check(manual=manual)
            wx.CallAfter(self._on_update_result, result, manual)

        self._pool.submit(work)

    def _on_update_result(self, result: CheckResult, manual: bool):
        self._update_busy = False
        if self._gone():
            return
        text = result.describe()
        if result.status != AVAILABLE:
            # A failed check says nothing about whether the update found
            # before is still there, so Help and the status bar keep it.
            if result.status != FAILED:
                self.status_parts.set("update", "")
                self._set_update_available("")
            if manual:
                self._say(text, force=True)
            elif result.status == FAILED:
                self._status(text)
            return
        self.status_parts.set("update", f"Update available: {result.version}")
        self._set_update_available(result.version)
        item = self._update_item_spoken()
        if not manual:
            self._say(f"{text} Help, {item} installs it, or the Update "
                      "button on the status bar.")
            return
        if self._runners:
            self._say(f"{text} It can be installed once Claude finishes; use Help, "
                      f"{item} then.", force=True)
            return
        # The dialog says it all; the screen reader reads it, so nothing is
        # spoken first. No is the default, so a stray Enter installs nothing.
        question = (f"{text}\n\nInstall it now? The Chat Place downloads it, closes, and "
                    "starts the new version. Your sessions and settings are kept.")
        if self._unsent_text():
            question += ("\n\nText you haven't sent in a reply box will be lost, so "
                         "you may want to send or copy it first.")
        answer = wx.MessageBox(question, "Update The Chat Place",
                               wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)
        if answer != wx.YES:
            self._feedback(f"Not now. Help, {item} installs it later.")
            return
        self._update_busy = True
        self._feedback(f"Downloading The Chat Place {result.version}.")

        def work():
            ok = self.updates.download()
            wx.CallAfter(self._on_update_downloaded, result, ok)

        self._pool.submit(work)

    def _on_menu_open_close(self, event, is_open: bool):
        event.Skip()
        self._menu_open = is_open

    def _set_update_available(self, version: str):
        """Help's update item names the version running, or the newer one
        a check found (#141). The label changes only while the menu is
        closed, so a screen reader reads the new one the next time Help
        opens; nothing is said about the change itself."""
        self._update_available = version
        label = update_item_label(__version__, version)
        if self.update_item.GetItemLabel() != label:
            self.update_item.SetItemLabel(label)

    def _update_item_spoken(self) -> str:
        return update_item_spoken(__version__, self._update_available)

    # The first start after an update (#141).

    def check_update_installed(self):
        """Decide, once at start, whether to show "The Chat Place Update
        Installed". Whether this copy is an installed one is asked off the
        UI thread (it makes a Velopack manager); the decision and the
        record are made on it, where the settings live."""
        if not self or self._closing:
            return

        def work():
            try:
                installed = bool(self.updates.can_update)
            except Exception:  # noqa: BLE001 - can't tell: not an installed copy
                installed = False
            wx.CallAfter(self._on_update_installed_known, installed)
        try:
            self._pool.submit(work)
        except RuntimeError:
            pass  # closing

    def _on_update_installed_known(self, installed: bool):
        if not self or self._closing:
            return
        decision = installed_notice(self.speech.last_run_version, __version__, installed,
                                    self.speech.update_installed_notice)
        if decision.record and not self.speech.unreadable:
            # Never over a settings file that couldn't be read: that would
            # replace every setting with the defaults. Recorded before anything is shown, so it's shown once even if
            # The Chat Place is closed before the dialog appears.
            self.speech.last_run_version = __version__
            try:
                self.speech.save()
            except OSError:
                pass  # not shown again this run; tried again next start
        if decision.show:
            self._show_update_installed_when_free(__version__, UPDATE_NOTICE_TRIES)

    def _show_update_installed_when_free(self, version: str, tries: int,
                                         free_before: bool = False):
        """Only over the main window, active, with nothing else in front of
        it: never stacked on a startup warning, a sign-in question or a
        dialog you've opened, never over an open menu, and never popping up
        while you're in another app. It must have been free at the last try
        too, so it doesn't arrive the moment you switch back and start
        typing. If that moment doesn't come, the status bar says it instead.

        IsActive, not wx.GetActiveWindow: on a Mac the latter is always
        None. A native message box (the startup warnings) makes the frame
        inactive on Windows, and a wx dialog disables it."""
        if not self or self._closing:
            return
        free = (self.IsShown() and not self.IsIconized() and self.IsActive()
                and self.IsEnabled() and not self._menu_open and not self._modal_open())
        if free and free_before:
            self.show_update_installed(version)
            return
        if tries <= 1:
            self._status(f"The Chat Place was updated to {version}.")
            return
        self._update_notice_wait = wx.CallLater(UPDATE_NOTICE_RETRY_MS,
                                                self._show_update_installed_when_free,
                                                version, tries - 1, free)

    def show_update_installed(self, version: str):
        def open_notes():
            url = release_notes_url(version)
            try:
                platform_paths.open_url(url)
            except OSError as exc:
                wx.MessageBox(f"Couldn't open the release notes: {exc}\n\nThey're at {url}",
                              APP_NAME, wx.OK | wx.ICON_WARNING, self)
                return
            self._feedback(f"Opening what's new in The Chat Place {version}.")
        self._modal(UpdateInstalledDialog(self, version, open_notes))

    def _unsent_text(self) -> bool:
        self._save_draft()
        return any(text.strip() for text in self._drafts.values())

    def _modal_open(self) -> bool:
        """A dialog of ours is open. A native message box isn't among wx's
        windows, but it is the active window while it's up."""
        if any(isinstance(w, wx.Dialog) and w.IsShown() and w.IsModal()
               for w in wx.GetTopLevelWindows()):
            return True
        active = wx.GetActiveWindow()
        return active is not None and active is not self

    def _on_update_downloaded(self, result: CheckResult, ok: bool):
        self._update_busy = False
        if self._gone():
            return
        if not ok:
            self._say(f"Couldn't download The Chat Place {result.version}. Try Help, "
                      f"{self._update_item_spoken()} again later.", force=True)
            return
        later = self._install_later_text(result)
        # The download took a while; things may have changed since the yes.
        if self._runners or self._modal_open():
            self._say(later, force=True)
            return
        if self._unsent_text():
            answer = wx.MessageBox(
                f"The Chat Place {result.version} is downloaded. Restart now to install it? "
                "Text you haven't sent in a reply box will be lost.",
                "Update The Chat Place", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)
            if answer != wx.YES or self._runners:
                self._say(later, force=True)
                return
        self._say(f"Installing The Chat Place {result.version} and restarting.", force=True)
        self._list_timer.Stop()
        self._chat_timer.Stop()
        self._update_busy = True
        # Let the announcement finish (a few seconds at most), then stop the
        # speech process so nothing of ours is left running in the install
        # folder Velopack replaces. Applying exits the process.
        self._apply_update_when_quiet(time.monotonic() + APPLY_SPEECH_WAIT_S, result)

    @staticmethod
    def _install_later_text(result: CheckResult) -> str:
        return (f"The Chat Place {result.version} is downloaded. It will be installed the "
                "next time The Chat Place starts.")

    def _restart_timers(self):
        self._list_timer.Start(LIST_REFRESH_MS)
        if self._open is not None:
            self._chat_timer.Start(CHAT_REFRESH_MS)

    def _apply_update_when_quiet(self, deadline: float, result: CheckResult):
        if self._gone():
            return
        if speaker.busy() and time.monotonic() < deadline:
            wx.CallLater(200, self._apply_update_when_quiet, deadline, result)
            return
        self._update_busy = False
        # Applying exits at once; a turn sent, a dialog opened or a reply begun
        # while the announcement played would be lost.
        if self._runners or self._modal_open() or self._unsent_text():
            self._restart_timers()
            self._say(self._install_later_text(result), force=True)
            return
        speaker.stop()
        if not self.updates.apply_and_restart():
            self._restart_timers()
            self._say("Couldn't install the update. It will be tried again the next time "
                      "The Chat Place starts.", force=True)

    # ------------------------------------------- slash commands and skills (#23)

    def _fetch_commands(self, cwd: str, then=None):
        """Get the folder's commands in the background (no turn, no cost).
        ``then`` runs when they've arrived (or the fetch gave up)."""
        key = _folder_key(cwd)
        if then is not None:
            self._commands_waiting.setdefault(key, []).append(then)
        if not cwd or key in self._commands_fetching:
            return
        lookup = platform_paths.find_claude()
        if not lookup.path:
            self._commands_fetched(key, [])
            return
        self._commands_fetching.add(key)

        def work():
            commands = fetch_commands(lookup.path, cwd)
            wx.CallAfter(self._commands_fetched, key, commands)
        self._pool.submit(work)

    def _commands_fetched(self, key: str, commands: List[dict]):
        self._commands_fetching.discard(key)
        if self._gone():
            return
        if commands:
            self._commands[key] = commands
        for then in self._commands_waiting.pop(key, []):
            then()

    def on_insert_command(self):
        """File, Insert Command or Skill (Ctrl+/): choose one of Claude
        Code's slash commands or your skills; it goes at the start of the
        reply box, ready to finish and send."""
        info = self._open
        if info is None or not info.is_own:
            self._feedback("Commands and skills are for The Chat Place's own sessions: load one "
                           "first.")
            return
        commands = self._commands.get(_folder_key(info.cwd))
        if commands is None:
            self._feedback("Getting the commands for this folder.")

            def when_ready():
                # Said, never opened by itself: a dialog appearing a moment
                # later would take keys you were typing elsewhere.
                if _folder_key(info.cwd) in self._commands:
                    self._feedback("Commands are ready: press Ctrl+/ or Commands.")
                else:
                    self._feedback("Couldn't get the commands from Claude Code.")
            self._fetch_commands(info.cwd, then=when_ready)
            return
        returning_to = wx.Window.FindFocus()
        dialog = CommandPickerDialog(self, commands)
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.chosen is None:
                if returning_to is not None:
                    returning_to.SetFocus()
                return
            chosen = dialog.chosen
        finally:
            dialog.Destroy()
        self._insert_command(chosen["name"], commands)

    def _insert_command(self, name: str, commands: Optional[List[dict]] = None):
        """Put ``/name `` at the start of the reply, replacing a command
        that's already there (only a real one: "/path/x is broken" stays),
        keeping the rest of what you typed, line breaks and all."""
        rest = self.reply_text.GetValue().lstrip(" \t")
        known = {c["name"] for c in commands or []}
        match = re.match(r"/(\S*)[ \t]*", rest)
        if match and (match.group(1) in known or not commands):
            rest = rest[match.end():]
        command = f"/{name} "
        self.reply_text.SetValue(command + rest)
        self.reply_text.SetFocus()
        self.reply_text.SetInsertionPoint(len(command))
        self._feedback(f"Inserted /{name}.")

    # ------------------------------------------------------------ prompts

    def _prompt_refused(self) -> str:
        """Why a prompt can't go into a reply box now, or ""."""
        if self._open is None:
            return ("Load one of The Chat Place's own sessions first: a prompt goes into "
                    "its reply box.")
        if not self._open.is_own:
            return (f"This is {kind_name(self._open)}, which The Chat Place only reads. "
                    "Load one of The Chat Place's own sessions to use a prompt in its reply "
                    "box.")
        return ""

    def on_prompts(self):
        """File, Prompts (#131): look after your saved prompts, and Use one
        to put it in the reply box."""
        returning_to = wx.Window.FindFocus()
        dialog = PromptsDialog(self, self.prompts, self._feedback, self._prompt_refused())
        try:
            chosen = dialog.chosen if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()
        if chosen is None:
            if returning_to:
                returning_to.SetFocus()
            return
        self._insert_prompt(chosen)

    def _insert_prompt(self, prompt):
        """Put a prompt's text in the reply box: in place of nothing, or at
        the cursor in what's already typed, and the cursor after it."""
        refused = self._prompt_refused()
        if refused:
            self._feedback(refused)
            return
        reply = self.reply_text
        if not reply.GetValue().strip():
            reply.SetValue(prompt.text)
            reply.SetInsertionPointEnd()
        else:
            # WriteText goes in at the cursor and leaves it after the text,
            # in the control's own positions (a line break isn't always one
            # position on Windows, so no slicing of GetValue here).
            reply.WriteText(prompt.text)
        reply.SetFocus()
        self._feedback(f"Inserted the prompt {prompt.name}.")

    def on_send_and_save(self):
        """Ctrl+Shift+Enter in the reply box (#131): ask for a name, save
        the message as a prompt, then send it as Ctrl+Enter does. Cancel
        (Escape) at the name does neither and leaves the text where it was,
        so a slip onto Shift never sends something you meant to look at."""
        refused = self._prompt_refused()
        if refused:
            self._feedback(refused)
            return
        session_id = self._open.cli_session_id
        text = clean_prompt_text(self.reply_text.GetValue())
        runner = self._runners.get(session_id)
        if not text or (runner is not None and runner.cancelled):
            # Nothing to save, or it can't go now: Send says why, as ever
            # (with only attachments they're sent; there's no text to keep).
            self.on_send()
            return
        name = self.prompts.unique_name(suggested_name(text))
        while True:
            dialog = wx.TextEntryDialog(
                self, "Save this message as a prompt, then send it. Prompt name:",
                "Send and Save as Prompt", name)
            dialog.SetMaxLength(PROMPT_NAME_MAX)
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    self._feedback("Not saved or sent.")
                    self.reply_text.SetFocus()
                    return
                name = dialog.GetValue()
            finally:
                dialog.Destroy()
            if clean_prompt_text(self.reply_text.GetValue()) != text:
                # Something came into the reply box while the name was asked
                # (a queued message given back when its send failed): what
                # would be sent is no longer what would be saved.
                wx.MessageBox("The reply box changed while the name was being asked, so "
                              "nothing was saved or sent. Check it, then press "
                              "Ctrl+Shift+Enter again.", "Send and Save as Prompt",
                              wx.OK | wx.ICON_INFORMATION, self)
                self.reply_text.SetFocus()
                return
            try:
                index = self.prompts.add(name, text)
                break
            except ValueError as exc:
                # Asked again, with the name that was refused to change.
                wx.MessageBox(str(exc), "Send and Save as Prompt",
                              wx.OK | wx.ICON_WARNING, self)
            except OSError as exc:
                wx.MessageBox(f"Couldn't save your prompts: {exc}\n\nThe message wasn't "
                              "sent either; it's still in the reply box.",
                              "Send and Save as Prompt", wx.OK | wx.ICON_WARNING, self)
                self.reply_text.SetFocus()
                return
        saved = self.prompts.get(index).name
        self._feedback(f"Prompt saved: {saved}.")
        self.on_send()
        if clean_prompt_text(self.reply_text.GetValue()) == text:
            # Send refused (it said why) and left the text: pressing
            # Ctrl+Shift+Enter again would save the prompt twice.
            self._feedback(f"The prompt {saved} is saved; press Ctrl+Enter to send.")

    def on_report_bug(self):
        """Help, Report a Bug (#28): what happened, plus non-sensitive facts
        about the app, to a GitHub issue (see bugreport.py)."""
        listed = [s for s in self._snapshot.sessions if not s.archived]
        code = sum(1 for s in listed if s.source == DESKTOP and not s.cowork)
        cowork = sum(1 for s in listed if s.cowork)
        # Cowork ones are desktop app sessions too: split only when there are some.
        counts = {"desktop app Code": code, "desktop app Cowork": cowork} if cowork \
            else {"desktop app": code}
        counts["Chat Place"] = sum(1 for s in listed if s.is_own)
        counts["terminal"] = sum(1 for s in listed if s.is_terminal)
        facts = bugreport.environment(self.speech, counts,
                                      claude_version=self._claude_version or "checking",
                                      speech_route=speaker.last_route)
        dialog = BugReportDialog(self, [f"{label}: {value}" for label, value in facts])
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            summary, happened, expected, steps = dialog.values()
            action = dialog.action
        finally:
            dialog.Destroy()
        report = bugreport.BugReport(summary, happened, expected, steps, facts)
        copied = self._copy_text(f"{summary}\n\n{bugreport.report_text(report)}")
        if action == "copy":
            self._feedback("Report copied. Email it to support@theideaplace.net." if copied
                           else "Couldn't copy the report.")
            return
        try:
            platform_paths.open_url(bugreport.new_issue_url(report))
        except OSError as exc:
            wx.MessageBox(f"Couldn't open the browser: {exc}. The report is on your clipboard: "
                          "email it to support@theideaplace.net.",
                          APP_NAME, wx.OK | wx.ICON_WARNING, self)
            return
        self._feedback("Opened GitHub's new issue page with your report filled in"
                       + (", and copied it." if copied else "."))

    def _copy_text(self, text: str) -> bool:
        if not wx.TheClipboard.Open():
            return False
        try:
            return bool(wx.TheClipboard.SetData(wx.TextDataObject(text)))
        finally:
            wx.TheClipboard.Close()

    def _check_claude_version(self):
        """Claude Code's version for bug reports, found once in the background:
        asking takes a second or more, too long to wait for in a dialog."""
        def work():
            version = bugreport.claude_code_version()
            wx.CallAfter(setattr, self, "_claude_version", version)
        self._pool.submit(work)

    def on_sign_in(self):
        """Help, Claude Code Sign-in (#52): whether Claude Code is signed in,
        to which plan, and a way to sign in if it isn't."""
        self._feedback("Checking Claude Code's sign-in.")
        self._sign_in_asked = True  # this answer replaces the start-up one
        self._check_sign_in(manual=True)

    def _check_sign_in(self, manual: bool):
        if self._gone():
            return

        def work():
            try:
                status = signin.check()
            except Exception as exc:  # noqa: BLE001 - always answer
                status = signin.SignIn(False, problem=str(exc) or "it failed")
            wx.CallAfter(self._on_sign_in_result, status, manual)
        try:
            self._pool.submit(work)
        except RuntimeError:
            pass  # closing

    def _on_sign_in_result(self, status, manual: bool):
        if self._gone():
            return
        text = signin.describe(status)
        if not manual:
            # At start-up, only a problem is news, and not over the list.
            if getattr(self, "_sign_in_asked", False):
                return
            if status.missing:
                first = re.split(r"(?<=\.) ", status.problem, maxsplit=1)[0]
                self._feedback(f"{first} To install it, choose Claude Code Sign-in on the "
                               "Help menu.")
            elif status.known and not (status.signed_in and status.subscription):
                self._feedback(f"{text} To sign in, choose Claude Code Sign-in on the "
                               "Help menu.")
            return
        self._sign_in_asked = False
        if status.missing:
            self._offer_install(text)
            return
        if not status.known:
            wx.MessageBox(text, "Claude Code Sign-in", wx.OK | wx.ICON_WARNING, self)
            return
        if status.signed_in and status.subscription:
            wx.MessageBox(text, "Claude Code Sign-in", wx.OK | wx.ICON_INFORMATION, self)
            return
        answer = wx.MessageBox(
            f"{text}\n\nSign in now? A window opens, and your browser shows Claude's sign-in "
            "page. When you've finished, The Chat Place checks again and says so.",
            "Claude Code Sign-in", wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)
        if answer != wx.YES:
            return
        command = signin.login_command()
        try:
            if command is None:
                raise OSError("Claude Code wasn't found")
            process = platform_paths.run_in_terminal(
                command, "sign-in-claude-code.command", "Signing in to Claude Code.",
                env=child_environment(), clear=STRIPPED_VARS,
                clear_prefixes=SESSION_INJECTED_PREFIXES)
        except OSError as exc:
            wx.MessageBox(f"Couldn't start the sign-in: {exc}", APP_NAME,
                          wx.OK | wx.ICON_ERROR, self)
            return
        self._feedback("Signing in, in a new window. The Chat Place checks again when you've "
                       "finished.")

        def signed_in():
            status = signin.check()
            return status.signed_in and status.subscription
        self._check_sign_in_after(process, signed_in)

    def _offer_install(self, text: str):
        """Claude Code isn't there, or isn't one The Chat Place can use:
        offer to run its native installer in a window of its own."""
        answer = wx.MessageBox(
            f"{text}\n\nInstall Claude Code now? A window opens and runs its native installer "
            "from claude.ai. When it's finished, The Chat Place checks the sign-in again.",
            "Claude Code Sign-in", wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)
        if answer != wx.YES:
            return
        try:
            process = platform_paths.start_claude_install(env=child_environment())
        except OSError as exc:
            wx.MessageBox(f"Couldn't start the installer: {exc}", APP_NAME,
                          wx.OK | wx.ICON_ERROR, self)
            return
        self._feedback("Installing Claude Code, in a new window. The Chat Place checks again "
                       "when it's finished.")
        self._check_sign_in_after(process, lambda: bool(platform_paths.find_claude().path))

    def _check_sign_in_after(self, process, done):
        """Check the sign-in again, and say the result, once a window The
        Chat Place opened is finished: when its process ends on Windows, or
        on a Mac (where Terminal runs it, out of reach) once ``done()`` says
        so, for up to ten minutes. Its own thread, not the pool's: a wait
        this long would hold up the list's refreshes."""
        def closing():
            try:
                return not self or self._closing
            except RuntimeError:
                return True  # the window is already gone

        def wait():
            if process is not None:
                process.wait()
            else:
                deadline = time.monotonic() + 600
                while not closing() and time.monotonic() < deadline:
                    time.sleep(5)
                    if not closing() and done():
                        break
            # Said even when it never finished, so the promise to check is kept.
            if not closing():
                wx.CallAfter(self._check_sign_in, manual=True)  # the result, said
        threading.Thread(target=wait, name="sign-in-wait", daemon=True).start()

    def on_about(self, _event=None):
        wx.MessageBox(
            f"{APP_NAME} version {__version__}\n\nA home for your Claude Code chats: keyboard and "
            "screen reader friendly, on your existing Claude subscription.\n\n"
            "An independent project by Kelly Ford. It works with Claude Code but isn't made, "
            "sponsored or endorsed by Anthropic. Claude and Claude Code are trademarks of "
            "Anthropic.\n\n"
            "Questions and bug reports: support@theideaplace.net",
            f"About {APP_NAME}", wx.OK | wx.ICON_INFORMATION, self)

    # ------------------------------------------------------------- keyboard

    def _on_char_hook(self, event: wx.KeyEvent):
        key = event.GetKeyCode()
        focus = wx.Window.FindFocus()
        ctrl = event.ControlDown()
        in_session = focus is not None and focus is not self.session_list and \
            self._is_in_session_view(focus)

        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if ctrl and focus is self.reply_text:
                if event.ShiftDown():
                    self.on_send_and_save()  # #131
                else:
                    self.on_send()
                return
            if ctrl and focus is self.chat_list and self._selected_queued() is not None:
                self.send_queued_now()  # Send Now on a queued message
                return
            if not ctrl and not event.AltDown() and focus is self.session_list:
                self.on_open_session()
                return
            if not ctrl and focus is self.chat_list:
                self.on_read_message()
                return
            if press_focused_button_on_a_mac(event):
                # Return did nothing on Send and the rest on a Mac.
                return
        if (key == wx.WXK_INSERT and event.ShiftDown() and not ctrl
                and focus is self.reply_text):
            # Shift+Insert pastes like Ctrl+V, so a picture is attached
            # rather than dropped into the text.
            self.reply_text.Paste()
            return
        if key == wx.WXK_F6 and not ctrl and not event.AltDown():
            self.cycle_focus(forward=not event.ShiftDown())
            return
        if self._is_menu_key(event) and focus in (self.session_list, self.chat_list):
            # Opened here, on key-down, rather than left to Windows (#89).
            # The session list had no menu of its own, so Windows took
            # Shift+F10 as F10 and started the menu bar: focus went to the
            # File menu for a moment and a screen reader said so.
            if focus is self.session_list:
                self._on_session_menu()
            else:
                self._on_message_menu()
            return
        if self.status_parts.contains(focus) and not ctrl and key in (
                wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_HOME, wx.WXK_END):
            # Between the status bar's parts, as in QuickMail.
            self.status_parts.move(focus, -1 if key in (wx.WXK_LEFT, wx.WXK_HOME) else 1,
                                   to_end=key in (wx.WXK_HOME, wx.WXK_END))
            return
        if key == wx.WXK_TAB and not ctrl and self.status_parts.contains(focus):
            # The status bar isn't in the panel's Tab order, so wx would leave
            # Tab nowhere to go. It sits after everything else: Tab wraps to
            # the session list, Shift+Tab goes back to the last control.
            if event.ShiftDown():
                self.refresh_btn.SetFocus()
            else:
                self.focus_sessions()
            return
        if key == wx.WXK_ESCAPE and in_session:
            self.focus_sessions()
            return
        if key == wx.WXK_ESCAPE and focus is self.session_list and self._session_filter:
            self._set_session_filter("")
            return
        if key == wx.WXK_BACK and not ctrl and focus is self.chat_list:
            self.focus_sessions()
            return
        if (key == wx.WXK_DELETE and focus is self.session_list
                and not ctrl and not event.AltDown()):
            if event.ShiftDown():
                self.on_delete_permanently()  # as Shift+Delete is in Explorer
            else:
                self.on_hide()
            return
        if key == wx.WXK_DELETE and focus is self.chat_list \
                and self._selected_queued() is not None:
            self.remove_queued()
            return
        if (ctrl and key in (ord("C"), ord("c")) and not event.AltDown()
                and focus is self.chat_list):
            if event.ShiftDown():
                self.copy_last_code_block()
            else:
                self._copy_message()
            return
        if (ctrl and event.ShiftDown() and key in (ord("B"), ord("b"))
                and not event.AltDown() and focus is self.chat_list):
            self.on_code_blocks()  # straight to the list, as Ctrl+Shift+C copies the last one
            return
        event.Skip()

    @staticmethod
    def _is_menu_key(event: wx.KeyEvent) -> bool:
        """Shift+F10 or the Applications key, the keyboard's right-click."""
        key = event.GetKeyCode()
        plain = not event.ControlDown() and not event.AltDown()
        return plain and ((key == wx.WXK_F10 and event.ShiftDown())
                          or (key == wx.WXK_WINDOWS_MENU and not event.ShiftDown()))

    # F6 and Shift+F6 (#10), as in QuickMail: the window's parts in order,
    # wrapping round. The reply stop is the reply box for The Chat Place's own
    # sessions, the "About replying" note for desktop ones, and is skipped
    # when nothing is loaded.
    PANE_SESSIONS, PANE_MESSAGES, PANE_REPLY, PANE_STATUS = range(4)

    def _panes(self) -> List[int]:
        panes = [self.PANE_SESSIONS, self.PANE_MESSAGES]
        if self._open is not None:
            panes.append(self.PANE_REPLY)
        panes.append(self.PANE_STATUS)
        return panes

    def _current_pane(self) -> int:
        focus = wx.Window.FindFocus()
        if focus is self.session_list:
            return self.PANE_SESSIONS
        if focus is self.chat_list:
            return self.PANE_MESSAGES
        if self.status_parts.contains(focus):
            return self.PANE_STATUS
        # Anything else in the window comes after the messages in Tab order:
        # the reply area, its buttons, Show tool activity, New and Refresh.
        return self.PANE_REPLY if focus is not None else self.PANE_SESSIONS

    def cycle_focus(self, forward: bool = True):
        panes = self._panes()
        current = self._current_pane()
        if current not in panes:  # the reply stop, with nothing loaded
            current = self.PANE_MESSAGES
        step = 1 if forward else -1
        self.focus_pane(panes[(panes.index(current) + step) % len(panes)])

    def focus_pane(self, pane: int):
        if pane == self.PANE_SESSIONS:
            self.focus_sessions()
        elif pane == self.PANE_MESSAGES:
            self.focus_messages()
        elif pane == self.PANE_REPLY:
            self.focus_reply()
        else:
            self.focus_status()

    def focus_status(self):
        """Ctrl+9, and the last F6 stop: the status bar's first part."""
        self.status_parts.focus_first()

    def _is_in_session_view(self, window) -> bool:
        """True for the messages list, the reply area and the controls after
        them (everything but the sessions list)."""
        return window is not None and window.GetTopLevelParent() is self

    def _copy_message(self):
        visible = self._visible_messages()
        index = self.chat_list.GetSelection()
        if not (0 <= index < len(visible)):
            return
        if self._copy_text(visible[index].full_text()):
            self._feedback("Message copied.")
        else:
            self._feedback("Couldn't open the clipboard.")

    # ---------------------------------------------------------------- close

    def _on_close(self, event: wx.CloseEvent):
        if self._runners and event.CanVeto():
            count = len(self._runners)
            answer = wx.MessageBox(
                f"Claude is working in {count} Chat Place session"
                f"{'s' if count != 1 else ''}. Quit anyway? The running turn"
                f"{'s' if count != 1 else ''} will be stopped"
                f"{', and queued messages will not be sent.' if self._queued else '.'}",
                f"Quit {APP_NAME}", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)
            if answer != wx.YES:
                event.Veto()
                return
        self._closing = True
        for runner in list(self._runners.values()):
            runner.cancel()
        self.stop_timers()
        speaker.stop()
        speaker.on_problem = None
        self._notifier.close()  # its icon would keep the app running
        self._pool.shutdown(wait=False, cancel_futures=True)
        event.Skip()

    def _gone(self) -> bool:
        """True once the window is destroyed or being destroyed. A Mac
        destroys a window later, at idle time: until then it still looks
        alive, and a queued call or timer tick that reaches it crashes wx."""
        try:
            return not self or self.IsBeingDeleted()
        except RuntimeError:
            return True

    def stop_timers(self):
        """Every timer and delayed call the window has, stopped: none may
        fire once it's gone. A Mac destroys a window later, at idle time,
        and a call that reaches it then can crash the app."""
        self._clear_activity()
        self._list_timer.Stop()
        self._chat_timer.Stop()
        self._link_timer.Stop()
        for name in ("_startup_sign_in", "_startup_update_check",
                     "_startup_update_notice", "_update_notice_wait", "_find_all_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.Stop()


def _folder_key(cwd: str) -> str:
    """One key per folder, whatever its case or a trailing slash."""
    return os.path.normcase(os.path.normpath(cwd)) if cwd else ""


def _fitting_size(width: int, height: int):
    """The preferred size, shrunk to fit a small screen (such as 1024 by 768)."""
    try:
        area = wx.Display(0).GetClientArea()
        return (min(width, area.width - 40), min(height, area.height - 40))
    except Exception:  # noqa: BLE001
        return (width, height)


def _delete_transcript(path, tries: int = 5) -> None:
    """Delete a transcript and its folder (its subagents' transcripts and tool
    output). A background read that was already under way when the session
    was unloaded can hold the file open for a moment, and Windows won't delete
    an open file, so a refusal is tried again briefly before it's reported."""
    if path is None:
        return
    folder = path.with_suffix("")
    for attempt in range(tries):
        try:
            if path.exists():
                path.unlink()
            if folder.is_dir():
                shutil.rmtree(folder)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.1)


def _sample_session() -> SessionInfo:
    """A made-up session for the Session List Columns preview when none is
    selected, with something to say in every column."""
    return SessionInfo(source=OWN, key="own:sample", title="Example session",
                       cwd="Projects/Example", cli_session_id="", state=NEEDS_YOU,
                       detail="Choose a version number", unread=True,
                       last_activity_ms=int(time.time() * 1000) - 120_000, remote=True,
                       groups=("Work",),
                       last_message="Claude: Which version number should this release be?")
