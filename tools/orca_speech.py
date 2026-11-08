"""What Orca says as you use The Chat Place (Linux), checked a key at a time.

The Windows counterpart is the sr-test skill, which listens to NVDA in a test
VM. On Linux Orca can run with no desktop at all: a virtual screen (Xvfb), an
accessibility bus and Orca's debug log, which records every string it would
speak ("SPEECH OUTPUT: '...'"). So this needs no person, no sound and no VM,
and runs in CI (#230).

    tools/orca_speech.sh                    run the steps, report, exit 1 on a failure
    tools/orca_speech.sh --record           say what each step spoke, check nothing
    tools/orca_speech.sh --json out.json    and save every step's speech

``orca_speech.sh`` makes the session bus, virtual screen and accessibility bus
and then runs ``check`` here. ``check`` starts Orca with a debug log, starts
the app (``serve``) on the visual probe's made-up sessions (``tools/ui_probe.py``,
``tests/fake_env.py``: no real sessions, settings or claude), and presses each
step's keys with xdotool. A step passes when everything it expects was
spoken, in any order, before speech went quiet.

The app's own speech isn't faked, unlike the window tests': announcements go
to Orca as they would for a user, through D-Bus on Orca 49 and later.

Orca runs one copy per user, so on a PC where you use Orca, run this as
another user (the test VM has ``tcptest``). It refuses to start beside an
Orca it didn't start.

Needs: Orca, xdotool, Xvfb, at-spi2-core, dbus and a window manager
(``sudo apt install orca xdotool xvfb at-spi2-core dbus-x11 openbox``), and
``.venv`` (``linuxsetup.sh``).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: Orca's debug log line for each string it speaks: the text in quotes, then
#: the voice (50) or nothing (older), and the next line's time. The text can
#: run over several lines (a message with line breaks).
_SPOKEN = re.compile(r"SPEECH OUTPUT: '(.*?)'(?:[ \t][^\n]*)?(?:\n(?=\d\d:\d\d:\d\d)|\n?\Z)",
                     re.S)
#: Orca echoing a key you pressed: not what the app said.
_KEY_ECHO = {"tab", "escape", "return", "enter", "down", "up", "left", "right", "space",
             "left control", "right control", "left alt", "right alt", "left shift",
             "right shift", "f1", "f10", "home", "end"}
#: Quiet this long after a key, and its speech is taken as finished.
SETTLE = 1.2
#: The longest a step waits for what it expects to be said.
STEP_TIMEOUT = 15.0
#: The first step waits for the app to start, which a slow runner takes a while over.
START_TIMEOUT = 60.0


@dataclass
class Step:
    name: str
    keys: List[str]
    #: Each must be in what's spoken (case-insensitive), in any order.
    expect: List[str] = field(default_factory=list)
    #: None of these may be.
    refuse: List[str] = field(default_factory=list)
    #: Only with Orca 49 and later, which take announcements from the app
    #: through D-Bus whatever window is active.
    expect_dbus: List[str] = field(default_factory=list)
    #: Only with Orca 50 and later, which say the window's and the list's
    #: name as the app starts; 46 says only the row it lands on.
    expect_50: List[str] = field(default_factory=list)
    #: Something to happen in the app before the keys: "question" has Claude
    #: ask one in the open chat, as a turn would; "copilot" opens a Copilot
    #: chat of the app's own that asks to run a command (``serve`` watches
    #: for them, TRIGGERS).
    trigger: str = ""


#: The probe's sessions (tools/ui_probe.py build_world): "Fix the flaky upload
#: test" first, as it needs you, then "Visual probe", the app's own, whose
#: messages are the user's request and Claude's long reply.
#: The Copilot chat "copilot" opens (tools/ui_probe.py s_main_copilot's).
COPILOT_TITLE = "Copilot history preview"

STEPS = [
    Step("start", [], expect=["Fix the flaky upload test", "needs you"],
         expect_50=["The Chat Place", "Session list"]),
    Step("next session", ["Down"], expect=["Visual probe", "Chat Place session"]),
    Step("open it", ["Return"], expect=["Messages in Visual probe", "What changed"],
         expect_dbus=["Loaded Visual probe"]),
    Step("previous message", ["Up"], expect=["Make the uploader flush before it signals"]),
    # Rewind to Here (#468) on that message of yours: it opens on the chosen
    # option of What to Rewind, Tab reads What Happens, Shift+Tab goes back to
    # the option, Escape comes back. The option is checked after Shift+Tab:
    # as it opens, Orca's echo of the key can cut its reading short.
    Step("rewind to here", ["ctrl+shift+z"],
         expect=["Rewind Visual probe to before your message"],
         refuse=["unlabeled", "unlabelled"]),
    Step("rewind, what happens", ["Tab"], expect=["What happens"]),
    Step("rewind, back to the chosen option", ["shift+Tab"],
         expect=["Conversation only"]),
    Step("rewind closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    Step("settings", ["ctrl+comma"], expect=["Settings", "Announcements", "Full"],
         refuse=["unlabeled", "unlabelled"]),
    Step("settings, tab", ["Tab"], expect=["Announce when any listed session", "check box"]),
    Step("settings, history retention", ["alt+y"],
         expect=["History retention and kept copies"]),
    Step("settings closed", ["Escape"], expect=["Messages in Visual probe"]),
    Step("F10, the menu bar", ["F10"], expect=["File", "menu"]),
    Step("menu closed, back in the messages", ["Escape"], expect=["Messages in Visual probe"]),
    Step("Alt alone, the menu bar", ["alt"], expect=["File", "menu"]),
    Step("menu closed again, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Into a submenu and back out: Escape must still close the menu, not go
    # back to the session list (#230).
    Step("View menu", ["alt+v"], expect=["Go to Sessions"]),
    Step("Sort Sessions submenu", ["o"], expect=["radio menu item"]),
    Step("back to View", ["Left"], expect=["Sort Sessions"]),
    Step("View closed, back in the messages", ["Escape"], expect=["Messages in Visual probe"],
         refuse=["Session list"]),
    Step("keyboard shortcuts", ["F1"], expect=["Keyboard Shortcuts"]),
    Step("shortcuts closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Answer (#415): it opens on the Question box, and Tab goes to the first
    # question's choices, not past them to its answer box (#428).
    Step("Claude asks a question", [], trigger="question", expect=["needs you"]),
    # Orca reads the Question box's first line as it lands there.
    Step("answer it", ["ctrl+shift+a"], expect=["Question", "Here's the draft"]),
    Step("Tab to the choices", ["Tab"], expect=["Keep it as is"],
         refuse=["your own answer"]),
    Step("answer later, back in the messages", ["Escape"], expect=["Messages in Visual probe"]),
    # A connected tool asks for input (#468): Answer opens on its message,
    # and Tab goes to the form's first box.
    Step("a connected tool asks", [], trigger="elicitation", expect=["needs you"]),
    Step("answer the tool", ["ctrl+shift+a"], expect=["Message", "Where would you like to go"]),
    # Enter in the read-only box sends nothing, and says so.
    Step("Enter in the message says where to go", ["Return"],
         expect_dbus=["Tab to the form, or to Send"]),
    Step("Tab to the form", ["Tab"], expect=["City", "required"]),
    # What a form's check box and list are called has to reach Orca (#468).
    Step("Tab to a check box", ["Tab"], expect=["Bringing pets", "check box"]),
    Step("Tab to a list", ["Tab"], expect=["Budget"]),
    # Each field has an access key of its own (#468).
    Step("Alt+C goes back to City", ["alt+c"], expect=["City"]),
    # A check box's key moves to it and doesn't tick it (#468).
    Step("Alt+B goes to the check box, leaving it unticked", ["alt+b"],
         expect=["Bringing pets", "not checked"]),
    Step("tool answered later, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # View, Connected Tools (#468), the View menu's last item: Up from the top.
    Step("Connected Tools", ["alt+v", "Up", "Return"], expect=["Connected tools"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Connected Tools closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Ask Aside (#468): it opens on the question box; Escape closes it.
    Step("Ask Aside", ["ctrl+shift+y"], expect=["Ask Aside about Visual probe", "Your question"]),
    Step("Ask Aside closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # File, Chat Options (#468), opened by the trigger: the item has no access
    # key. It opens on Agent, and Tab goes through every control in turn. The
    # list has no folders yet, so its one row says so. Add Folder's picker
    # comes back to Add Folder, as there's no folder to go to in the list.
    Step("Codex Chat Options", [], trigger="codex-chat-options",
         expect=["Chat Options: Codex review", "Instructions for this chat"]),
    Step("Codex more folders", ["Tab"], expect=["More folders"]),
    Step("Codex add folder", ["Tab"], expect=["Add Folder"]),
    Step("Close Codex Chat Options", ["Escape"]),
    Step("Chat Options", [], trigger="chat-options", expect=["Chat Options: Visual probe", "Agent"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Chat Options, output style", ["Tab"], expect=["Output style"]),
    Step("Chat Options, instructions", ["Tab"], expect=["Instructions for this chat"]),
    Step("Chat Options, more folders", ["Tab"], expect=["No more folders"]),
    Step("Chat Options, Add Folder", ["Tab"], expect=["Add Folder"]),
    Step("Add Folder's picker", ["space"], expect=["Choose an additional folder for this chat"]),
    Step("picker closed, back on Add Folder", ["Escape"], expect=["Add Folder"]),
    Step("Chat Options closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Mute All Sounds (#250), said whatever the announcements level, and File, Sounds, by the
    # trigger as the item has no access key: Tab through every control, then Escape back out.
    Step("Mute All Sounds", ["ctrl+shift+x"], expect=["Sounds muted"]),
    Step("Mute All Sounds again", ["ctrl+shift+x"], expect=["Sounds not muted"]),
    Step("Sounds", [], trigger="sounds", expect=["Sounds", "Play sounds"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Sounds, mute", ["Tab"], expect=["Mute all sounds"]),
    Step("Sounds, volume", ["Tab"], expect=["Volume"]),
    Step("Sounds, scheme", ["Tab"], expect=["Sound scheme"]),
    Step("Sounds, tool sounds", ["Tab"], expect=["Tool sounds for"]),
    Step("Sounds, other chats", ["Tab"], expect=["other chats"]),
    Step("Sounds, Scheme Actions", ["Tab"], expect=["Scheme Actions"]),
    Step("Sounds, Browse Gallery", ["Tab"], expect=["Browse Gallery"]),
    Step("Sounds, events", ["Tab"], expect=["Tool ran: Tap"]),
    Step("Sounds, next event", ["Down"], expect=["File changed: Changed"]),
    Step("Sounds, its sound", ["Tab"], expect=["Sound"]),
    Step("Sounds, Preview", ["Tab"], expect=["Preview"]),
    Step("Sounds, Choose File", ["Tab"], expect=["Choose File"]),
    # Sound schemes: the Scheme Actions menu, and the gallery opened and closed (#250).
    # GTK opens the menu with no item highlighted, so Orca says only that it's a menu.
    Step("Sounds, Scheme Actions menu", ["alt+a"], expect=["menu"]),
    Step("Scheme Actions menu closed, focus where it was", ["Escape"], expect=["Choose File"]),
    Step("Sound Scheme Gallery", ["alt+b"], expect=["Sound Scheme Gallery"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Gallery closed, back on Browse Gallery", ["Escape"], expect=["Browse Gallery"]),
    # Back on the message that had focus: Orca reads the window and that message.
    Step("Sounds closed, back in the messages", ["Escape"],
         expect=["Visual probe — The Chat Place"]),
    # Back Up Now (#433, #435): the folder question, and Escape back out.
    Step("Back Up Now", ["alt+f", "w"], expect=["Back up to which folder"]),
    Step("folder question closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Backup Schedule (#437), Restore from Backup (#436) and Export Chats
    # (#461), the last items before Exit: Up from the top goes to Exit first.
    # Lands on the schedule's choice box, which Orca reads with or without
    # its name depending on timing (#454): until that's fixed, only that the
    # menu reached Backup Schedule is checked.
    Step("Backup Schedule", ["alt+f", "Up", "Up", "Up", "Up", "Return"],
         expect=["Backup Schedule"]),
    Step("Backup Schedule closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # GTK's file picker is announced by its file list, not its title.
    Step("Restore from Backup", ["alt+f", "Up", "Up", "Up", "Return"],
         expect=["Restore from Backup", "Files"]),
    Step("restore's file question closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Lands on the chat list, none chosen: its first row starts "Skip".
    Step("Export Chats", ["alt+f", "Up", "Up", "Return"],
         expect=["Export Chats", "Skip"]),
    Step("Space chooses the chat", ["space"], expect=["Export", "1 of"]),
    Step("Export Chats closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # File, Stop Background Task (#468), opened by the trigger once the open
    # chat's turn has background work: the item has no access key.
    Step("Stop Background Task", [], trigger="background",
         expect=["Stop Background Task", "Windows build, running"]),
    Step("the next task", ["Down"], expect=["Watch CI"]),
    Step("Stop Background Task closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # The page's plain text here: serve has no web view. Built off the window's thread.
    Step("Read Full Chat", ["alt+v", "w"], expect=["Full chat: Visual probe"]),
    Step("full chat closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Subagents (#468): "Fix the flaky upload test" has two, a code-reviewer
    # that finished and an Explore at work. View, Subagents opens Lists on
    # them, newest first; Enter reads one's conversation, and Escape goes
    # back a step at a time.
    # Sorted by title, so where it is doesn't depend on which sessions the
    # steps before left needing you. (Ctrl+F in a GTK list is the list's own
    # type-ahead search, not Find.) "A session whose title goes on…" is first.
    Step("back to the session list", ["ctrl+1"], expect=["Session list"]),
    Step("sort by title", ["alt+v", "o", "t"], expect=["Sessions sorted by title"]),
    Step("the session with subagents", ["Home", "Down"], expect=["Fix the flaky upload test"]),
    Step("open the session with subagents", ["Return"],
         expect=["Messages in Fix the flaky upload test"]),
    Step("View, Subagents", ["alt+v", "g"], expect=["Subagents (2)", "Explore", "Running"]),
    Step("a subagent's conversation", ["Down", "Return"],
         expect=["Subagent code-reviewer", "The retry waits too little"]),
    Step("conversation closed, back in Lists", ["Escape"],
         expect=["code-reviewer: Review the retry change"]),
    # Orca says the window and the message you were on, not the list's name.
    Step("Lists closed, back in the messages", ["Escape"],
         expect=["Fix the flaky upload test", "Which branch name should the release use"]),
    Step('Codex command and skill picker', [], trigger='codex-commands',
         expect=['Insert Command or Skill'], refuse=['unlabeled', 'unlabelled']),
    Step('Codex commands, into the list', ['Tab'], expect=['Codex']),
    Step('Codex commands closed', ['Escape'], expect=['Fix the flaky upload test']),
    Step('Codex temporary side question', [], trigger='codex-aside',
         expect=['Ask Aside about Visual probe', 'Your question']),
    # The probe's second question is still being answered: the first Escape
    # stops it and stays, as the guide says, and the next closes the dialog.
    Step('Codex side question stopped', ['Escape'], expect_dbus=['Stopped asking']),
    Step('Codex side question closed', ['Escape'], expect=['Fix the flaky upload test']),
    # New Session's Start from (#561), on a made-up repository whose remote
    # has a main branch, with A new worktree chosen and a new branch typed.
    Step("New Session, a new branch in a new worktree", [], trigger="start-from",
         expect=["Branch", "fix/orca"]),
    # GTK gives an editable dropdown two Tab stops, its text and its button,
    # so Start from is two Tabs on from Branch's text (CI on #562).
    Step("Start from", ["Tab", "Tab"], expect=["Start from", "origin/main, fetched first"]),
    Step("Start from, the current branch", ["Down"], expect=["work, what the folder has"]),
    Step("New Session closed", ["Escape"], expect=["Fix the flaky upload test"]),
    # Copilot's Auto (#426), last, as the Copilot chat replaces the session
    # list's discovery (copilot_chat below): a chat of the app's own in agent
    # mode, with Copilot asking to run npm test and npm run lint already
    # allowed for the chat.
    # Orca 46 reads the row the focus lands on, not the list's name.
    Step("a Copilot chat asks to run a command", [], trigger="copilot",
         expect=["What changed"],
         expect_dbus=["Copilot needs your answer", "npm test"]),
    # Agent, marked "(now)", is selected; Auto is the third choice.
    # Orca 46 reads the choice the list starts on, not the dialog's title
    # (CI on #426: worth a look for the choice dialog in general).
    Step("Change Permission Mode", ["ctrl+shift+o"],
         expect=["Agent: asks before running commands", "(now)"]),
    Step("Auto", ["Down", "Down"], expect=["Auto: reads, and changes files"]),
    # The warning is a Yes/No message box, No the default.
    Step("the Auto warning", ["Return"],
         expect=["In Auto, Copilot reads in", "Turn on Auto for this chat"]),
    # Return takes the default, No: the chat still asks.
    Step("No, the default, so it still asks", ["Return"],
         expect=[f"Messages in {COPILOT_TITLE}"],
         expect_dbus=["still asks before Copilot acts"], refuse=["Auto. It reads"]),
    # Answer opens on the Request box; Tab goes to Allow for This Chat, whose
    # name says what it keeps.
    Step("answer Copilot", ["ctrl+shift+a"], expect=["Copilot needs permission", "Request"]),
    Step("Tab to Allow for This Chat", ["Tab"],
         expect=["Allow for this chat", "exactly this command: npm test"]),
    Step("Alt+C allows it for this chat", ["alt+c"], expect=[f"Messages in {COPILOT_TITLE}"],
         expect_dbus=["Approval kept for this chat and answer sent to Copilot"]),
    # Chat Permissions lists npm run lint, kept before, then npm test.
    Step("Copilot's Chat Permissions", ["ctrl+shift+h"],
         expect=["Chat Permissions", "Allowed for this chat", "npm run lint"]),
    Step("the command just kept", ["Down"], expect=["Exactly this command: npm test"]),
    # Past Remove and Remove All, Restart Copilot, whose name says what it does.
    Step("Tab to Restart Copilot", ["Tab", "Tab", "Tab"], expect=["Restart Copilot"]),
    # Another chat is running something: a Yes/No message box, No the default.
    Step("Restart Copilot warns first", ["space"],
         expect=["1 other Copilot chat is running", "Restart it anyway"]),
    # Return takes the default, No: nothing's said to have stopped, and the
    # focus is back on the button.
    Step("No, the default, so nothing stops", ["Return"], expect=["Restart Copilot"],
         refuse=["Copilot stopped"]),
    # The other chat has finished by now: it stops, and says so.
    # The focus stays on the button, so only the announcement is new.
    Step("Restart Copilot", ["space"], expect_dbus=["Copilot stopped"]),
    # Back on the row it left, which Orca 46 reads without the list's name.
    Step("Chat Permissions closed, back in the messages", ["Escape"],
         expect=[f"{COPILOT_TITLE} — The Chat Place", "What changed"]),
]

#: The question "question" asks (STEPS above).
QUESTION = {"questions": [{
    "header": "Linux notes", "question": "How should it read?",
    "options": [{"label": "Keep it as is", "description": "As drafted",
                 "preview": "Linux (preview). The Chat Place now runs on Linux."},
                {"label": "Shorter", "description": "Just the essentials"}]}]}


# -- the app --------------------------------------------------------------------------------

def serve(root: Path) -> int:
    """The app on the probe's made-up sessions, shown and focused, until it's
    closed. Its speech is real, so Orca gets the announcements."""
    import faulthandler
    faulthandler.enable()  # a crash in GTK or ATK says where, in app.log
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(HERE))
    import wx
    from thechatplace import speech
    app = wx.App(False)
    import ui_probe

    def patch(target, name, value):
        # fake_env records speech for the window tests; here it must reach Orca.
        if target is speech.speaker and name == "speak":
            return
        setattr(target, name, value)
    env = ui_probe.build_world(root, patch, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize((1000, 720))
    frame.Show()
    frame.Raise()
    frame.session_list.SetFocus()

    def watch(_event):
        # A step's trigger: check writes ROOT/<name>.trigger before its keys.
        for name, action in TRIGGERS.items():
            trigger = root / f"{name}.trigger"
            if trigger.exists():
                trigger.unlink()
                action(frame, env)
    timer = wx.Timer(frame)
    frame.Bind(wx.EVT_TIMER, watch, timer)
    timer.Start(200)
    app.MainLoop()
    return 0


def ask_question(frame) -> None:
    """Claude, in the probe's own session (open by then), writes a line and
    asks QUESTION, as a turn reports them (tools/ui_probe.py's _waiting)."""
    import fake_env
    from thechatplace.claude_cli import PermissionRequest, TurnEvent
    frame._pending.clear()
    frame._runners["own-1"] = fake_env.FakeRunner([], "", "", None)
    for kind, text in [("text", "Here's the draft release note for Linux."),
                       ("tool", "AskUserQuestion")]:
        frame._on_turn_event({"id": "own-1"}, "Visual probe", TurnEvent(kind, text=text))
    request = PermissionRequest("q1", "AskUserQuestion", QUESTION, suggestions=[])
    frame._on_turn_event({"id": "own-1"}, "Visual probe",
                         TurnEvent("permission", text=request.summary(), request=request))


#: What the "elicitation" trigger's connected tool asks (#468), shaped as
#: Claude Code 2.1.296 sends it.
ELICITATION = {
    "subtype": "elicitation", "mcp_server_name": "trip-planner",
    "message": "Where would you like to go, and for how long?", "mode": "form",
    "requested_schema": {"type": "object", "required": ["city"], "properties": {
        "city": {"type": "string", "title": "City"},
        "pets": {"type": "boolean", "title": "Bringing pets"},
        "budget": {"type": "string", "title": "Budget", "enum": ["low", "high"]}}}}


def ask_elicitation(frame) -> None:
    """A connected tool asks for input in the probe's own session, as a turn
    reports it."""
    import fake_env
    from thechatplace.claude_cli import TurnEvent
    from thechatplace.claude_mcp import elicitation_from
    frame._pending.clear()
    frame._runners["own-1"] = fake_env.FakeRunner([], "", "", None)
    request = elicitation_from("e1", ELICITATION)
    frame._on_turn_event({"id": "own-1"}, "Visual probe",
                         TurnEvent("permission", text=request.summary(), request=request))


def stop_background_task(frame) -> None:
    """The probe's own session's turn, with two background tasks running,
    and File, Stop Background Task opened on it (#468)."""
    import wx
    import fake_env
    frame._pending.clear()
    runner = fake_env.FakeRunner([], "", "", None)
    runner.background_entries = [{"task_id": "b1", "description": "Windows build", "since": 0.0},
                                 {"task_id": "b2", "description": "Watch CI", "since": 60.0}]
    runner.background_tasks = ["Windows build", "Watch CI"]
    frame._runners["own-1"] = runner
    frame._update_send_state()
    wx.CallAfter(frame.on_stop_background_task)


def chat_options(frame) -> None:
    """File, Chat Options on the probe's own session, open by then (#468). After
    the timer's call, as the dialog is modal."""
    import wx
    wx.CallAfter(frame.on_chat_options)


def new_session_start_from(frame) -> None:
    """New Session on a made-up repository with a remote (#561): A new
    worktree chosen and a new branch typed, so Start from is on, and the
    focus on Branch, one Tab before it. Opened once the folder is read."""
    import subprocess
    import tempfile
    import wx
    from thechatplace.ui.dialogs import NewSessionDialog, WORK_IN_WORKTREE
    base = Path(tempfile.mkdtemp(prefix="tcp-orca-"))
    origin, clone = base / "Origin", base / "Clone"
    origin.mkdir()
    author = ["-c", "user.name=probe", "-c", "user.email=probe@example.invalid"]
    for args, folder in ((["init", "-q", "-b", "main"], origin),
                         ([*author, "commit", "-q", "--allow-empty", "-m", "first"], origin),
                         (["clone", "-q", str(origin), str(clone)], base),
                         (["checkout", "-q", "-b", "work"], clone)):
        subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)
    dialog = NewSessionDialog(frame, str(clone), feedback=frame._feedback)

    def ready(tries=100):
        if not dialog._repo_known and tries:
            wx.CallLater(100, ready, tries - 1)
            return
        dialog.work_in.SetSelection(WORK_IN_WORKTREE)
        dialog._on_work_in()
        dialog.branch.SetValue("fix/orca")
        dialog._sync_start_from()
        dialog.branch.SetFocus()
        frame._modal(dialog)
    wx.CallAfter(ready)


def _codex_dialog(name, frame, env) -> None:
    """Opens one of the probe's Codex dialogs (#467)."""
    import ui_probe
    getattr(ui_probe, name)(frame, env)()


def copilot_chat(frame, env) -> None:
    """A Copilot chat of the app's own (tools/ui_probe.py's made-up Copilot
    session), open in agent mode, with npm run lint allowed for it and Copilot
    asking to run npm test, as copilot_runtime reports a request. Copilot is a
    stand-in: nothing is run. The Chat Place's own record of the commands
    allowed for a chat (in the made-up app data folder) is real, so Allow for
    This Chat keeps npm test and Chat Permissions lists it."""
    import dataclasses
    from types import SimpleNamespace
    import wx
    import fake_env
    import ui_probe
    from thechatplace.own_store import OwnSession
    from thechatplace.providers import copilot_auto_store
    from thechatplace.providers.copilot_runtime import AGENT_MODE, CopilotChatState
    from thechatplace.sessions import IDLE, NEEDS_YOU, WORKING

    # The real message box from here on (these steps are last): the probe's
    # stand-in answers Yes at once, so Auto's warning would never be shown
    # or read, and Return couldn't take its default, No.
    import wx._core
    wx.MessageBox = wx._core.MessageBox
    # Opening it moves the focus to its messages, which Orca says.
    frame.session_list.SetFocus()
    ui_probe.s_main_copilot(frame, env, title=COPILOT_TITLE)
    info = frame._open
    native = info.cli_session_id
    record = OwnSession(native, info.title, info.cwd, provider="copilot", started=True)
    frame.store.add(record)
    copilot_auto_store.add(native, "npm run lint")
    request = ui_probe._copilot_request({
        "toolCallId": "call-1", "kind": "execute", "title": "Run the tests",
        "rawInput": {"command": "npm test",
                     "commands": [{"identifier": "npm test", "readOnly": False}]}})
    now = {"state": CopilotChatState(native, IDLE, "1", "completed", mode=AGENT_MODE)}
    allowed = set()

    def answer(_native, _request_id, _choice):
        # Copilot goes on with its turn, the request answered.
        now["state"] = dataclasses.replace(now["state"], state=WORKING, requests=())
        wx.CallAfter(frame._on_owned_update, "copilot", now["state"])

    def keep_allowed(_native, commands):
        allowed.clear()
        allowed.update(commands)

    # Warned about once, as if another chat then finished.
    warnings = ["1 other Copilot chat is running a turn or a program. Restarting Copilot ends "
                "them, and what they were doing isn't picked up again. Restart it anyway?"]

    frame.sessions._providers["copilot"].runtime = SimpleNamespace(
        known=lambda: [(native, info.cwd)], snapshot=lambda _native: now["state"],
        ready=lambda _native: True, adopt=lambda _native: None,
        prepare=lambda _native, _cwd: None, close=lambda: None,
        chat_candidate=lambda _native, _request_id: request.command,
        allowed_here=lambda _native: set(allowed), keep_allowed=keep_allowed, answer=answer,
        set_mode=lambda _native, mode: mode, set_auto=lambda _native, on: None,
        restart_warning=lambda _native: warnings.pop() if warnings else "",
        restart=lambda: True)
    frame._codex_states[info.key] = now["state"]
    frame.open_session(dataclasses.replace(record.to_info(), state=IDLE))
    fake_env.pump(lambda: frame._chat_loaded and not frame._reader_busy
                  and not frame._codex_preparing, timeout=10)
    # The row is the app's own from the next refresh (as
    # ui_probe.s_main_copilot_owned), which the File menu's items go by.
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None,
                  timeout=10)
    now["state"] = dataclasses.replace(now["state"], state=NEEDS_YOU, turn_id="2",
                                       outcome="inProgress", requests=(request,))
    frame._on_owned_update("copilot", now["state"])


#: What each step's trigger does in ``serve`` (Step.trigger).
TRIGGERS = {
    "question": lambda frame, env: ask_question(frame),
    "elicitation": lambda frame, env: ask_elicitation(frame),
    "background": lambda frame, env: stop_background_task(frame),
    "codex-chat-options": lambda frame, env: _codex_dialog("d_codex_chat_options", frame, env),
    "chat-options": lambda frame, env: chat_options(frame),
    "sounds": lambda frame, env: frame.on_sounds(),
    "start-from": lambda frame, env: new_session_start_from(frame),
    "codex-commands": lambda frame, env: _codex_dialog("d_codex_commands", frame, env),
    "codex-aside": lambda frame, env: _codex_dialog("d_codex_aside", frame, env),
    "copilot": copilot_chat,
}


# -- Orca -----------------------------------------------------------------------------------

class OrcaLog:
    """What Orca spoke, read from its debug log as it grows."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0

    def mark(self) -> None:
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def since_mark(self) -> List[str]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as log:
            log.seek(self.offset)
            text = log.read().decode("utf-8", "replace")
        return [m.group(1) for m in _SPOKEN.finditer(text)]

    def wait_quiet(self, done, timeout: float = STEP_TIMEOUT,
                   settle: float = SETTLE) -> List[str]:
        """Speech since the mark, once ``done(speech)`` and Orca has been quiet
        for ``settle`` seconds, or at ``timeout``. Waiting for what a step
        expects, rather than for the first quiet, lets a slow runner take its
        time without the speech landing in the next step."""
        end = time.time() + timeout
        seen, quiet_since = [], time.time()
        while time.time() < end:
            now = self.since_mark()
            if len(now) != len(seen):
                seen, quiet_since = now, time.time()
            elif time.time() - quiet_since >= settle and done(seen):
                break
            time.sleep(0.1)
        return seen


def orca_version() -> tuple:
    out = subprocess.run(["orca", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _our_orcas() -> List[int]:
    out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "orca"],
                         capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def start_orca(log_path: Path) -> subprocess.Popen:
    if _our_orcas():
        raise SystemExit("Orca is already running as this user. Run this as another user "
                         "(the test VM has tcptest), so it can't take over your Orca.")
    out = log_path.with_name(log_path.name + ".out")
    orca = subprocess.Popen(["orca", "--debug-file", str(log_path)],
                            stdout=open(out, "w"), stderr=subprocess.STDOUT)
    end = time.time() + 30
    while time.time() < end:
        if log_path.exists() and "SPEECH OUTPUT" in log_path.read_text("utf-8", "replace"):
            return orca
        time.sleep(0.2)
    orca.kill()
    said = out.read_text("utf-8", "replace").strip()[-2000:]
    raise SystemExit("Orca didn't start speaking within 30 seconds. Is the accessibility "
                     "bus running? (orca_speech.sh starts one.)"
                     + (f"\nOrca said:\n{said}" if said else ""))


#: Keys xdotool can't send as themselves: it picked F10's Ctrl+Alt level
#: (switch to virtual terminal 10) on Ubuntu's keyboard map, so Orca heard
#: "left alt" and the app nothing. AT-SPI sends these by key code instead.
_KEYCODE_KEYS = {"F1", "F10"}


def _keycode(name: str) -> int:
    out = subprocess.run(["xmodmap", "-pke"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[3] == name:
            return int(parts[1])
    raise SystemExit(f"{name} isn't on this display's keyboard map")


def press(keys: List[str]) -> None:
    for key in keys:
        if key in _KEYCODE_KEYS:
            # The system Python, which has PyGObject; .venv may not.
            subprocess.run(["/usr/bin/python3", "-c",
                            "import sys, gi; gi.require_version('Atspi', '2.0'); "
                            "from gi.repository import Atspi; "
                            "Atspi.generate_keyboard_event(int(sys.argv[1]), None, "
                            "Atspi.KeySynthType.PRESSRELEASE)", str(_keycode(key))],
                           check=True)
        else:
            subprocess.run(["xdotool", "key", key], check=True)
        time.sleep(0.3)


# -- the check ------------------------------------------------------------------------------

def _found(needle: str, spoken: List[str]) -> bool:
    text = " ".join(spoken).casefold()
    return needle.casefold() in text


def run_steps(log: OrcaLog, steps: List[Step], version: tuple, record: bool,
              world: Path) -> List[dict]:
    results = []
    for index, step in enumerate(steps):
        expect = (step.expect + (step.expect_dbus if version >= (49, 0) else [])
                  + (step.expect_50 if version >= (50, 0) else []))

        def done(spoken, expect=expect):
            said = [s for s in spoken if s.casefold().rstrip(".") not in _KEY_ECHO]
            if expect and not record:
                return all(_found(e, said) for e in expect)
            return bool(spoken)
        if step.keys or step.trigger:
            log.mark()
        # The first step's speech is the app starting, from the mark made
        # before it was started.
        if step.trigger:
            (world / f"{step.trigger}.trigger").write_text("", encoding="utf-8")
        press(step.keys)
        spoken = log.wait_quiet(done, timeout=START_TIMEOUT if index == 0 else STEP_TIMEOUT)
        said = [s for s in spoken if s.casefold().rstrip(".") not in _KEY_ECHO]
        missing = [] if record else [e for e in expect if not _found(e, said)]
        refused = [] if record else [r for r in step.refuse if _found(r, said)]
        results.append({"step": step.name, "keys": step.keys, "spoken": said, "heard": spoken,
                         "missing": missing, "refused": refused,
                         "passed": not missing and not refused})
        # As it goes: a job that runs out of time keeps no report at the end.
        print(f"{index + 1}/{len(steps)} {'ok' if results[-1]['passed'] else 'FAIL'} "
              f"{step.name}: {' | '.join(said)[:200] or '(nothing)'}", flush=True)
    return results


def report(results: List[dict], version: tuple, record: bool) -> str:
    lines = [f"Orca {version[0]}.{version[1]}: what The Chat Place said, step by step", ""]
    for r in results:
        mark = "    " if record else ("ok  " if r["passed"] else "FAIL")
        keys = " ".join(r["keys"]) or "(start)"
        lines.append(f"{mark} {r['step']} [{keys}]: {' | '.join(r['spoken']) or '(nothing)'}")
        for m in r["missing"]:
            lines.append(f"       not said: {m!r}")
        for m in r["refused"]:
            lines.append(f"       said, and shouldn't be: {m!r}")
    if not record:
        failed = sum(not r["passed"] for r in results)
        lines += ["", f"{len(results) - failed} of {len(results)} steps passed."]
    return "\n".join(lines)


def check(args) -> int:
    for tool in ("orca", "xdotool"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} isn't installed (see this file's docstring).")
    if not os.environ.get("DISPLAY"):
        raise SystemExit("No display: run this through tools/orca_speech.sh.")
    work = Path(tempfile.mkdtemp(prefix="tcp-orca-"))
    log_path = Path(args.orca_log) if args.orca_log else work / "orca-debug.log"
    version = orca_version()
    orca = start_orca(log_path)
    log = OrcaLog(log_path)
    log.mark()
    app = subprocess.Popen([sys.executable, str(Path(__file__)), "serve", "--root",
                            str(work / "world")], cwd=str(ROOT),
                           stdout=open(work / "app.log", "w"), stderr=subprocess.STDOUT)
    try:
        results = run_steps(log, STEPS, version, record=args.record, world=work / "world")
    finally:
        app.terminate()
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()
        orca.terminate()
        try:
            orca.wait(10)
        except subprocess.TimeoutExpired:
            orca.kill()
    text = report(results, version, args.record)
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps({"orca": list(version), "steps": results},
                                              indent=2), encoding="utf-8")
    if app.returncode not in (0, None, -15) and not args.record:
        print(f"\nThe app exited with {app.returncode}; its output is in {work / 'app.log'}")
        # On a runner the file is gone with the job, so the end of it goes here.
        log = (work / "app.log").read_text(encoding="utf-8", errors="replace").splitlines()
        kept = [line for line in log if "-CRITICAL **" not in line and line.strip()]
        print("\n".join(kept[-60:]))
    return 0 if args.record or all(r["passed"] for r in results) else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="run the app on made-up sessions (check starts it)")
    s.add_argument("--root", required=True)
    c = sub.add_parser("check", help="run the steps against Orca")
    c.add_argument("--record", action="store_true", help="report what was said, check nothing")
    c.add_argument("--json", help="save every step's speech here")
    c.add_argument("--orca-log", help="Orca's debug log (default: a temporary file)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(Path(args.root))
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
