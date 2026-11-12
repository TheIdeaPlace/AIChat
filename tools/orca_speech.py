"""What Orca says as you use The Chat Place (Linux), checked a key at a time.

The Windows counterpart is the sr-test skill, which listens to NVDA in a test
VM. On Linux Orca can run with no desktop at all: a virtual screen (Xvfb), an
accessibility bus and Orca's debug log, which records every string it would
speak ("SPEECH OUTPUT: '...'"). So this needs no person, no sound and no VM,
and runs in CI (#230).

    tools/orca_speech.sh                    run the steps, report, exit 1 on a failure
    tools/orca_speech.sh --record           say what each step spoke, check nothing
    tools/orca_speech.sh --json out.json    and save every step's speech
    tools/orca_speech.sh --keep DIR         and keep Orca's debug log and the app's output

Orca 46 took new objects for dead ones now and then, and said nothing about
them, which failed a different step on each run; tools/orca-customizations.py
puts that right, and the report says how often it did (#579).

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
#: What tools/orca-customizations.py writes in Orca's debug log as it loads,
#: and each time it puts right a live object Orca 46 took for dead (#579).
LOADED = "TCP #579: dead objects are asked again"
CORRECTED = "TCP #579: an object Orca had taken for dead answers"
#: More corrections than this in one step is warned about. Six runs of every
#: step (#605) made 16 to 27 a run, none more than 8 in a step (Backup
#: Schedule), nearly all as a dialog opened: three times that is a real change.
CORRECTIONS_CEILING = 25


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
    #: Press the keys only once the trigger has finished (``serve`` writes
    #: <name>.done), not straight away. Not for one that opens a dialog,
    #: which finishes only when the dialog closes.
    after_trigger: bool = False


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
    # Retry from Here (#468, #596) on that message of yours: it opens on the
    # chosen option of What Goes Back, Tab reads What Happens, Shift+Tab goes
    # back to the option, Escape comes back. The option is checked after
    # Shift+Tab: as it opens, Orca's echo of the key can cut its reading short.
    Step("retry from here", ["ctrl+shift+z"],
         expect=["Retry Visual probe from before your message"],
         refuse=["unlabeled", "unlabelled"]),
    Step("retry, what happens", ["Tab"], expect=["What happens"]),
    Step("retry, the button", ["Tab"], expect=["Retry", "push button"]),
    Step("retry, back to the chosen option", ["shift+Tab", "shift+Tab"],
         expect=["Conversation only"]),
    Step("retry closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    Step("settings", ["ctrl+comma"], expect=["Settings", "Announcements", "Full"],
         refuse=["unlabeled", "unlabelled"]),
    Step("settings, tab", ["Tab"], expect=["Announce when any listed session", "check box"]),
    Step("settings, history retention", ["alt+y"],
         expect=["History retention and kept copies"]),
    # Its access key may also toggle it (GTK clicks a check box's mnemonic);
    # Escape below cancels Settings, so nothing is kept either way.
    Step("settings, startup update check", ["alt+d"],
         expect=["Check for updates when The Chat Place starts", "check box"]),
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
    # The three menus #575 added, each by its own letter: none is taken by a
    # control in the window, so each opens on its first item.
    Step("Session menu", ["alt+s"], expect=["Load Session"]),
    Step("Session closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"], refuse=["Session list"]),
    Step("Chat menu", ["alt+c"], expect=["Answer"]),
    Step("Chat closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"], refuse=["Session list"]),
    Step("Tools menu", ["alt+t"], expect=["Lists"]),
    Step("Tools closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"], refuse=["Session list"]),
    # Customize Keyboard Shortcuts (#635): the list starts on New Session;
    # Change Key starts in the Key box, a pressed key is said with what
    # Assign would do, and Escape twice changes nothing.
    Step("Customize Keyboard Shortcuts", ["alt+t", "k"],
         expect=["Customize Keyboard Shortcuts", "New Session, Ctrl+N, File menu"]),
    # Orca names the box by its visible label (GTK's labelled-by), so "Key:
    # read only text" is what shows focus reached the box, not the title.
    Step("Change Key", ["Return"], expect=["Change Key: New Session", "Key: read only text"]),
    Step("a key pressed in Change Key", ["ctrl+shift+v"],
         expect=["Ctrl+Shift+V", "Nobody has it"]),
    Step("Change Key closed, back in the list", ["Escape"],
         expect=["New Session, Ctrl+N, File menu"]),
    Step("Customize Keyboard Shortcuts closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    Step("keyboard shortcuts", ["F1"], expect=["Keyboard Shortcuts"]),
    Step("shortcuts closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Help, Sound Tutorial (#250): it opens on the first event, arrows move
    # through them by name, and Escape comes back.
    Step("sound tutorial", ["alt+h", "t"], expect=["Sound Tutorial", "Tool ran"],
         refuse=["unlabeled", "unlabelled"]),
    Step("sound tutorial, next event", ["Down"], expect=["File changed"]),
    Step("sound tutorial closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Answer (#415): it opens on the Question box, and Tab goes to the first
    # question's choices, not past them to its answer box (#428).
    Step("Claude asks a question", [], trigger="question", expect=["needs you"]),
    # Orca reads the Question box's first line as it lands there.
    Step("answer it", ["ctrl+shift+a"], expect=["Question", "Here's the draft"]),
    Step("Tab to the choices", ["Tab"], expect=["Keep it as is"],
         refuse=["your own answer"]),
    # The own answer is a multi-line box now (Shift+Enter starts a new
    # line): Tab still reaches it, and leaves it for the Preview box.
    Step("Tab to its own answer", ["Tab"], expect=["your own answer"]),
    Step("Tab out of the answer box", ["Tab"], expect=["Preview", "The Chat Place now runs on Linux"]),
    # Escape from inside it still answers later.
    Step("Shift+Tab back to its own answer", ["shift+Tab"], expect=["your own answer"]),
    Step("answer later, back in the messages", ["Escape"], expect=["Messages in Visual probe"]),
    # Back to it, to answer in your own words over two lines (#591):
    # Shift+Enter starts a new line and the dialog stays open (Up reads the
    # first line back); Enter sends.
    Step("answer it again", ["ctrl+shift+a"], expect=["Question", "Here's the draft"]),
    Step("Tab twice to its own answer", ["Tab", "Tab"], expect=["your own answer"]),
    Step("type a word", ["z", "e", "b", "r", "a"]),
    Step("Shift+Enter starts a new line", ["shift+Return"],
         refuse=["Messages in Visual probe", "Answer sent"]),
    Step("Up reads the first line: still in the box", ["Up"], expect=["zebra"],
         refuse=["Messages in Visual probe"]),
    # The messages say Claude is working again, not that it still needs you
    # (#596): the state came later, held back while you were in the list.
    Step("Enter sends the answer", ["Return"], expect=["Messages in Visual probe", "working"],
         refuse=["needs you"], expect_dbus=["Answer sent"]),
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
    # Not answered, so it still needs you (#596).
    Step("tool answered later, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"], refuse=["working"]),
    # Tools, Connected Tools (#468).
    Step("Connected Tools", ["alt+t", "c"], expect=["Connected tools"],
         refuse=["unlabeled", "unlabelled"]),
    # Sign In, Reconnect, Turn Off for This Chat, then this one (#626).
    Step("Connected Tools, Turn Off for New Chats", ["Tab", "Tab", "Tab", "Tab"],
         expect=["Turn Off for New Chats"]),
    Step("Connected Tools closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Ask Aside (#468): it opens on the question box; Escape closes it.
    Step("Ask Aside", ["ctrl+shift+y"], expect=["Ask Aside about Visual probe", "Your question"]),
    Step("Ask Aside closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Chat, Chat Settings (#596), by its access key: the submenu opens on
    # Change Model. On GTK one Escape closes it and the Chat menu together.
    Step("Chat, Chat Settings", ["alt+c", "n"], expect=["Chat Settings", "Change Model"]),
    Step("Chat Settings closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"], refuse=["Session list"]),
    # Change Permission Mode for a Codex chat (#596, decision 17): Plan is its
    # last choice. It starts on the chat's own mode, marked "(now)"; in Plan,
    # on Plan, and Up reaches the file access rows. Before the Chat Options
    # steps, not between them: Claude's Chat Options loads its folder's
    # agents in the background, and right after these its first Tab came
    # before that, so focus went back to Run as agent (#609's CI).
    Step("Codex Change Permission Mode", [], trigger="codex-change-mode",
         expect=["automatic approval review", "(now)"]),
    Step("Codex Plan choice", ["Down"], expect=["Plan: discuss and produce a plan first"]),
    Step("Close Codex Change Permission Mode", ["Escape"], expect=["Messages in Visual probe"]),
    Step("Codex Change Permission Mode in Plan", [], trigger="codex-change-mode-plan",
         expect=["Plan: discuss and produce a plan first", "(now)"]),
    Step("Codex file access from Plan", ["Up"],
         expect=["Can edit files in this folder", "(file access now)"]),
    Step("Close Codex Change Permission Mode in Plan", ["Escape"], expect=["Messages in Visual probe"]),
    # Chat Options (#468), opened by the trigger: opened from the menu, the
    # menu's closing put Orca's reading a Tab behind on GTK. It opens on Run
    # as agent, and Tab goes through every control in turn. The
    # list has no folders yet, so its one row says so. Add Folder's picker
    # comes back to Add Folder, as there's no folder to go to in the list.
    Step("Codex Chat Options", [], trigger="codex-chat-options",
         expect=["Chat Options: Codex review", "Instructions for this chat"]),
    Step("Codex more folders", ["Tab"], expect=["More folders"]),
    Step("Codex add folder", ["Tab"], expect=["Add Folder"]),
    Step("Close Codex Chat Options", ["Escape"]),
    Step("Chat Options", [], trigger="chat-options", expect=["Chat Options: Visual probe", "Run as agent"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Chat Options, output style", ["Tab"], expect=["Output style"]),
    Step("Chat Options, instructions", ["Tab"], expect=["Instructions for this chat"]),
    Step("Chat Options, more folders", ["Tab"], expect=["No more folders"]),
    Step("Chat Options, Add Folder", ["Tab"], expect=["Add Folder"]),
    Step("Add Folder's picker", ["space"], expect=["Choose an additional folder for this chat"]),
    Step("picker closed, back on Add Folder", ["Escape"], expect=["Add Folder"]),
    Step("Chat Options closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Mute All Sounds (#250), said whatever the announcements level, and Tools, Sounds, by the
    # trigger: Tab through every control, then Escape back out.
    Step("Mute All Sounds", ["ctrl+shift+x"], expect=["Sounds muted"]),
    Step("Mute All Sounds again", ["ctrl+shift+x"], expect=["Sounds not muted"]),
    Step("Sounds", [], trigger="sounds", expect=["Sounds", "Play sounds"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Sounds, volume", ["Tab"], expect=["Volume"]),
    Step("Sounds, scheme", ["Tab"], expect=["Sound scheme"]),
    Step("Sounds, tool sounds", ["Tab"], expect=["Tool sounds play for"]),
    Step("Sounds, other sounds", ["Tab"], expect=["Other sounds play for"]),
    Step("Sounds, Scheme Actions", ["Tab"], expect=["Scheme Actions"]),
    Step("Sounds, Browse Gallery", ["Tab"], expect=["Browse Gallery"]),
    Step("Sounds, Sound Tutorial", ["Tab"], expect=["Sound Tutorial"]),
    Step("Sounds, events", ["Tab"], expect=["Tool ran: Tick"]),
    Step("Sounds, next event", ["Down"], expect=["File changed: Write"]),
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
    # The Sound Tutorial from Sounds (#596), and back on its button.
    Step("Sound Tutorial from Sounds", ["alt+u"], expect=["Sound Tutorial", "Tool ran"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Tutorial closed, back on Sound Tutorial", ["Escape"], expect=["Sound Tutorial"]),
    # Back on the message that had focus: Orca reads the window and that message.
    Step("Sounds closed, back in the messages", ["Escape"],
         expect=["Visual probe — The Chat Place"]),
    # Back Up Now (#433, #435): the folder question, and Escape back out.
    Step("Back Up Now", ["alt+f", "b", "n"], expect=["Back up to which folder"]),
    Step("folder question closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Backup Schedule (#437) and Restore from Backup (#436), in File, Backup,
    # and Export Chats (#461), each by its access key (#575).
    # Lands on the schedule's choice box, which Orca reads with or without
    # its name depending on timing (#454): until that's fixed, only that the
    # menu reached Backup Schedule is checked.
    Step("Backup Schedule", ["alt+f", "b", "s"],
         expect=["Backup Schedule"]),
    Step("Backup Schedule closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # GTK's file picker is announced by its file list, not its title, and
    # its access key chooses the item without Orca reading it (#575), so
    # the Backup submenu it came from is what's checked.
    Step("Restore from Backup", ["alt+f", "b", "r"],
         expect=["Backup menu", "Files"]),
    Step("restore's file question closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Lands on the chat list, none chosen: its first row starts "Skip".
    Step("Export Chats", ["alt+f", "h"],
         expect=["Export Chats", "Skip"]),
    Step("Space chooses the chat", ["space"], expect=["Export", "1 of"]),
    Step("Export Chats closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Chat, Stop Background Task (#468, #596), opened by the trigger once the
    # open chat's turn has background work, which only the trigger can make.
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
    # that finished and an Explore at work. Tools, Subagents opens Lists on
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
    Step("Tools, Subagents", ["alt+t", "g"], expect=["Subagents (2)", "Explore", "Running"]),
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
    # Connected Tools in a Codex chat started here (#596): Delete in the list
    # makes no copy of the chat, and says which button does.
    Step("Codex Connected Tools", [], trigger="codex-tools",
         expect=["Connected Tools: Codex review", "project-notes"], refuse=["unlabeled", "unlabelled"]),
    Step("Delete says which button makes a copy", ["Delete"],
         expect=["a tool changes only in a new copy", "Copy with Tool Off"],
         refuse=["Make a new copy of this chat"]),
    Step("message closed, back on the tool", ["Return"], expect=["project-notes"]),
    Step("Codex Connected Tools closed", ["Escape"], expect=["Fix the flaky upload test"]),
    # A GTK message box is read as "alert Warning" and its text, not its title
    # (CI on #634), so the question is checked, and No, the default, focused.
    Step("Codex permanent deletion confirmation", [], trigger="codex-delete",
         expect=["permanently?", "subagent chats", "No push button"],
         refuse=["unlabeled", "unlabelled"]),
    Step("Cancel Codex deletion", ["Escape"], expect=["Fix the flaky upload test"]),
    # New Session's Start from (#561), on a made-up repository whose remote
    # has a main branch, with A new worktree chosen and a new branch typed.
    Step("New Session, a new branch in a new worktree", [], trigger="start-from",
         expect=["Branch", "fix/orca"]),
    # GTK gives an editable dropdown two Tab stops, its text and its button,
    # so Start from is two Tabs on from Branch's text (CI on #562).
    Step("Start from", ["Tab", "Tab"], expect=["Start from", "origin/main, fetched first"]),
    Step("Start from, the current branch", ["Down"], expect=["work, what the folder has"]),
    Step("New Session closed", ["Escape"], expect=["Fix the flaky upload test"]),
    # A branch checked out in another worktree is listed, saying so (#587).
    Step("New Session, a branch in use in another worktree", [], trigger="branch-in-use",
         expect=["Branch", "main (in use in another worktree)"]),
    # A branch only the remote has is listed before those in use (#622).
    Step("Branch, a branch only on the remote", ["Up", "Up"],
         expect=["release/next (on origin)"]),
    Step("New Session closed again", ["Escape"], expect=["Fix the flaky upload test"]),
    # Copilot's Auto (#426), last, as the Copilot chat replaces the session
    # list's discovery (copilot_chat below): a chat of the app's own in agent
    # mode, with Copilot asking to run npm test and npm run lint already
    # allowed for the chat.
    # Orca 46 reads the row the focus lands on, not the list's name.
    Step("a Copilot chat", [], trigger="copilot", expect=["What changed"]),
    # Orca 46 says it too, from the window, as the chat is in front.
    Step("it asks to run a command", [], trigger="copilot-asks",
         expect=["Copilot needs your answer", "npm test"]),
    # That update, and a refresh after it, came with the focus in the
    # messages: the rows are updated in place, so the arrows still move from
    # the row Orca was on, and Orca reads each (#579).
    Step("a refresh, then Up to the request", ["Up"], trigger="refresh", after_trigger=True,
         expect=["Explain the upload test"]),
    Step("Down, back to the reply", ["Down"], expect=["What changed"]),
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
    # Copilot carries on: working, not still needing you (#596).
    Step("Alt+C allows it for this chat", ["alt+c"],
         expect=[f"Messages in {COPILOT_TITLE}", "working"], refuse=["needs you"],
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
                (root / f"{name}.done").write_text("", encoding="utf-8")
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
    and Chat, Stop Background Task opened on it (#468)."""
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
    """Chat, Chat Settings, Chat Options on the probe's own session, open by
    then (#468, #596). After the timer's call, as the dialog is modal."""
    import wx
    wx.CallAfter(frame.on_chat_options)


def new_session_start_from(frame, in_use: bool = False) -> None:
    """New Session on a made-up repository with a remote (#561): A new
    worktree chosen and a new branch typed, so Start from is on, and the
    focus on Branch, one Tab before it. Opened once the folder is read.
    ``in_use``: main is checked out in a worktree of its own and chosen
    instead (#587), so Branch shows it saying so. The remote also has a
    branch of its own, listed as on origin (#622)."""
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
                         (["branch", "release/next"], origin),
                         (["clone", "-q", str(origin), str(clone)], base),
                         (["checkout", "-q", "-b", "work"], clone)):
        subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)
    if in_use:
        subprocess.run(["git", "worktree", "add", "-q", str(base / "MainTree"), "main"],
                       cwd=clone, check=True, capture_output=True)
    dialog = NewSessionDialog(frame, str(clone), feedback=frame._feedback)

    def ready(tries=100):
        if not dialog._repo_known and tries:
            wx.CallLater(100, ready, tries - 1)
            return
        dialog.work_in.SetSelection(WORK_IN_WORKTREE)
        # The clone was just made, so no fetch (#622): one finishing later
        # would list Branch again while Orca is reading it.
        dialog._fetches[dialog._repo_for] = None
        dialog._on_work_in()
        if in_use:
            dialog.branch.SetStringSelection("main (in use in another worktree)")
        else:
            dialog.branch.SetValue("fix/orca")
        dialog._sync_start_from()
        dialog.branch.SetFocus()
        frame._modal(dialog)
    wx.CallAfter(ready)


def _codex_dialog(name, frame, env) -> None:
    """Opens one of the probe's Codex dialogs (#467)."""
    import ui_probe
    getattr(ui_probe, name)(frame, env)()


def codex_delete_confirmation(frame, env) -> None:
    """A synthetic chat: exercise only the native confirmation's No path.
    Its message box is real while it's open, as Connected Tools' are: the
    probe's stand-in answers Yes at once, so the confirmation was never shown
    or read (#634 CI). Nothing is sent to Codex even if Yes were pressed."""
    import wx
    import wx._core
    from thechatplace.own_store import OwnSession
    record = OwnSession('orca-delete-preview', 'Codex deletion preview', str(env['tmp']),
                        provider='codex', started=True)
    frame.store.add(record)
    stand_in, work = wx.MessageBox, frame._codex_work
    wx.MessageBox = wx._core.MessageBox
    frame._codex_work = lambda action, done: None
    try:
        frame._delete_codex(record.to_info())
    finally:
        wx.MessageBox, frame._codex_work = stand_in, work
        frame._codex_deleting.discard(record.to_info().key)
        frame.store.remove(record.cli_session_id, provider='codex')


def codex_tools(frame, env) -> None:
    """Connected Tools for a Codex chat started here (#596), over the window
    without opening a Codex chat in it, so the steps after still have the
    probe's sessions as they were. Its message boxes are real while it's
    open: Delete says, in one, which button makes a copy."""
    from types import SimpleNamespace
    import wx
    import wx._core
    from thechatplace.providers.codex_tools import CodexTool
    from thechatplace.ui.codex_tools_host import CodexConnectedTools
    from thechatplace.ui.dialogs import ConnectedToolsDialog
    host = CodexConnectedTools(frame, SimpleNamespace(cli_session_id="codex-orca", is_own=False,
                                                      title="Codex review"))
    host.record = SimpleNamespace(cli_session_id="codex-orca", provider_options={})
    host.can_turn_off = True
    host.load = lambda done: done([CodexTool("project-notes", "connected", ("find_notes",), False)], "")
    stand_in = wx.MessageBox
    wx.MessageBox = wx._core.MessageBox
    try:
        frame._modal(ConnectedToolsDialog(frame, "Codex review", host))
    finally:
        wx.MessageBox = stand_in


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
    # ui_probe.s_main_copilot_owned), which the Session and Chat menus' items go by.
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None,
                  timeout=10)
    # Opened twice and refreshed, the messages were filled three times in
    # half a second with the focus in them, which no one opening a chat
    # does: Orca was often still on a row already replaced, and read none
    # (#579). So the focus goes there again now that they're settled, as
    # Ctrl+2 would, and Copilot asks in a step of its own ("copilot-asks").
    frame.session_list.SetFocus()
    fake_env.pump(lambda: False, timeout=0.3)
    frame.focus_messages()

    def asks():
        now["state"] = dataclasses.replace(now["state"], state=NEEDS_YOU, turn_id="2",
                                           outcome="inProgress", requests=(request,))
        frame._on_owned_update("copilot", now["state"])
    env["copilot_asks"] = asks


def refresh(frame) -> None:
    """The session list refreshed, as its timer does, wherever the focus is,
    and finished before the step's keys (#579): the messages, updated in
    place, must keep the row Orca is on."""
    import fake_env
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None,
                  timeout=10)


#: What each step's trigger does in ``serve`` (Step.trigger).
TRIGGERS = {
    "question": lambda frame, env: ask_question(frame),
    "elicitation": lambda frame, env: ask_elicitation(frame),
    "background": lambda frame, env: stop_background_task(frame),
    "codex-change-mode": lambda frame, env: _codex_dialog("d_codex_mode", frame, env),
    "codex-change-mode-plan": lambda frame, env: _codex_dialog("d_codex_mode_plan", frame, env),
    "codex-chat-options": lambda frame, env: _codex_dialog("d_codex_chat_options", frame, env),
    "chat-options": lambda frame, env: chat_options(frame),
    "sounds": lambda frame, env: frame.on_sounds(),
    "start-from": lambda frame, env: new_session_start_from(frame),
    "branch-in-use": lambda frame, env: new_session_start_from(frame, in_use=True),
    "codex-commands": lambda frame, env: _codex_dialog("d_codex_commands", frame, env),
    "codex-aside": lambda frame, env: _codex_dialog("d_codex_aside", frame, env),
    "codex-tools": codex_tools,
    "codex-delete": codex_delete_confirmation,
    "copilot": copilot_chat,
    "copilot-asks": lambda frame, env: env.pop("copilot_asks")(),
    "refresh": lambda frame, env: refresh(frame),
}


# -- Orca -----------------------------------------------------------------------------------

class OrcaLog:
    """What Orca spoke, read from its debug log as it grows."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0

    def size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def mark(self) -> None:
        self.offset = self.size()

    def count_since(self, offset: int, needle: str) -> int:
        """How many times ``needle`` is in the log after ``offset``."""
        if not self.path.exists():
            return 0
        with self.path.open("rb") as log:
            log.seek(offset)
            return log.read().decode("utf-8", "replace").count(needle)

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
        started, before = time.time(), log.size()
        if step.trigger:
            done_file = world / f"{step.trigger}.done"
            done_file.unlink(missing_ok=True)
            (world / f"{step.trigger}.trigger").write_text("", encoding="utf-8")
            end = time.time() + STEP_TIMEOUT
            while step.after_trigger and not done_file.exists() and time.time() < end:
                time.sleep(0.1)
        press(step.keys)
        spoken = log.wait_quiet(done, timeout=START_TIMEOUT if index == 0 else STEP_TIMEOUT)
        took = time.time() - started
        said = [s for s in spoken if s.casefold().rstrip(".") not in _KEY_ECHO]
        missing = [] if record else [e for e in expect if not _found(e, said)]
        refused = [] if record else [r for r in step.refuse if _found(r, said)]
        results.append({"step": step.name, "keys": step.keys, "spoken": said, "heard": spoken,
                         "missing": missing, "refused": refused,
                         "passed": not missing and not refused,
                         "started": time.strftime("%H:%M:%S", time.localtime(started)),
                         "seconds": round(took, 1),
                         "corrected": log.count_since(before, CORRECTED)})
        # As it goes: a job that runs out of time keeps no report at the end.
        # The time it started finds the step in Orca's debug log (--keep).
        print(f"{index + 1}/{len(steps)} {'ok' if results[-1]['passed'] else 'FAIL'} "
              f"[{results[-1]['started']}, {took:.1f}s] "
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


def orca_corrections(text: str, results: List[dict],
                     ceiling: int = CORRECTIONS_CEILING) -> List[str]:
    """What tools/orca-customizations.py did in this run (Orca's debug log,
    ``text``): how often Orca 46 took a live object for dead and was put
    right, step by step, with a warning for any step over ``ceiling``, so a
    change that makes many more of them shows (#579)."""
    if LOADED not in text:
        return ["Orca's dead objects weren't corrected: newer Orca checks them itself, and "
                "Orca 46 needs tools/orca-customizations.py to have loaded."]
    lines = [f"Orca took a live object for dead {text.count(CORRECTED)} times, and was put "
             "right each time (tools/orca-customizations.py, #579). By step:"]
    for r in results:
        count = r.get("corrected", 0)
        if count:
            lines.append(f"  {count:5}  {r['step']}")
        if count > ceiling:
            lines.append(f"::warning::{r['step']}: Orca took {count} live objects for dead, "
                         f"more than the {ceiling} any step took in testing (#579). Did the "
                         "app start replacing more of its rows or windows?")
    return lines


def check(args) -> int:
    for tool in ("orca", "xdotool"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} isn't installed (see this file's docstring).")
    if not os.environ.get("DISPLAY"):
        raise SystemExit("No display: run this through tools/orca_speech.sh.")
    work = Path(tempfile.mkdtemp(prefix="tcp-orca-"))
    # With --keep, the logs are written there from the start, so a run that
    # fails, crashes or runs out of time still leaves them.
    logs = Path(args.keep).resolve() if args.keep else work
    logs.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.orca_log) if args.orca_log else logs / "orca-debug.log"
    app_log = logs / "app.log"
    version = orca_version()
    orca = start_orca(log_path)
    log = OrcaLog(log_path)
    log.mark()
    app = subprocess.Popen([sys.executable, str(Path(__file__)), "serve", "--root",
                            str(work / "world")], cwd=str(ROOT),
                           stdout=open(app_log, "w"), stderr=subprocess.STDOUT)
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
    debug_text = log_path.read_text("utf-8", "replace") if log_path.exists() else ""
    print("\n".join(orca_corrections(debug_text, results)))
    if args.json:
        Path(args.json).write_text(json.dumps({"orca": list(version), "steps": results},
                                              indent=2), encoding="utf-8")
    if app.returncode not in (0, None, -15) and not args.record:
        print(f"\nThe app exited with {app.returncode}; its output is in {app_log}")
        # On a runner the file is gone with the job, so the end of it goes here.
        log = app_log.read_text(encoding="utf-8", errors="replace").splitlines()
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
    c.add_argument("--keep", help="write Orca's debug log and the app's output here")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(Path(args.root))
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
