# The Chat Place User Guide

This guide uses Windows key names. On a Mac, use Command (Cmd) for Ctrl,
Option for Alt and Return for Enter. Open menus from the menu bar (VoiceOver:
VO+M), rather than Windows Alt menu sequences.

The Chat Place is a keyboard and screen reader friendly home for your AI chats. Claude and
Codex sessions share one list: read, find and export conversations, hear replies, and send
messages in chats started here. Provider tells you which AI; Kind tells you where the session
started. More providers, including Ollama, are planned. Features that belong to one provider
name it; their availability depends on the selected chat.

This guide starts with the everyday things and goes deeper as it goes on. Each part has its own
heading, so your screen reader's heading keys move between them. For every key in one list, use
Help, Keyboard Shortcuts (F1).

The Chat Place is an independent project by Kelly Ford, not made, sponsored or endorsed by
Anthropic or OpenAI. Claude and Claude Code are trademarks of Anthropic; ChatGPT and Codex are
OpenAI products.

## Before you start

You need:

- Windows 10 or 11, or a Mac with Apple silicon.
- For Codex chats: a native Codex CLI signed in to ChatGPT, with the preview enabled in Settings.
  Claude Code is not required.
- For Claude chats: Claude Code, installed with its native installer and signed in to your Claude subscription.
  This is the `claude` command, using the same sign-in as the Claude desktop app. The desktop
  app's own copy of Claude Code isn't meant for other programs, so the command is needed even if
  you use the desktop app. An old npm install of Claude Code isn't used.

For Claude chats, install Claude Code by running its installer in a terminal, then run `claude` once to sign in:

- Mac (Terminal): `curl -fsSL https://claude.ai/install.sh | bash`
- Windows (PowerShell): `irm https://claude.ai/install.ps1 | iex`
- Windows (Command Prompt):
  `curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd`

Or let The Chat Place do it: Help, Claude Code Sign-in offers to install Claude Code when it
can't find it, and to sign you in when it isn't signed in.

The Chat Place never uses an API key. Reading sessions costs nothing, because everything it reads
is on your own computer. Codex replies use your ChatGPT sign-in. For Claude chats, messages
you send in its own sessions are Claude Code turns, which count
against your plan's monthly Agent SDK credit; past that credit, turns stop unless you've turned on
extra usage, which is billed. View, Usage and Context (Ctrl+Shift+U) shows where you stand.

### Installing

**Windows:** download `TheChatPlace-windows-Setup.exe` from the newest release on the
[releases page](https://github.com/TheIdeaPlace/AIChat/releases) and run it. It installs for you
only, needs no administrator rights, adds The Chat Place to the Start menu, and starts it. The
portable zip from the same release runs without installing, but doesn't update itself.

**Mac:** download `TheChatPlace-macos-arm64.dmg`, open it, and drag The Chat Place onto
Applications. The first time it speaks through VoiceOver, macOS asks whether The Chat Place may
control VoiceOver: allow it, and turn on "Allow VoiceOver to be controlled with AppleScript" in
VoiceOver Utility, General.

If Claude Code isn't signed in, your sessions are still listed; only sending a message needs a
sign-in. Help, Claude Code Sign-in says whether it is signed in, and offers to sign you in if not.
The sign-in opens in its own window (Terminal, on a Mac), and The Chat Place checks again when
you've finished. If it can't tell, it says why, for example that Claude Code is too old to ask:
running `claude update` in a terminal fixes that.
For Codex, choose Help, **Codex Sign-in**. It says whether Codex is signed in to ChatGPT. If it
isn't, or is using another kind of credential, it offers to open `codex login` in its own window.
Your browser shows ChatGPT's sign-in page, and The Chat Place checks again when you've finished.
If Codex isn't installed or its status can't be read, it says what is needed.

## The window

The window has three parts, in Tab order:

1. The **session list**: every session, one per line.
2. The **messages** of the session you've loaded.
3. The **reply box**, with Send and Stop. For a session you reply to somewhere else (the desktop
   app or a terminal), the same place holds a short note saying where you reply, followed by the
   Open in Claude and Continue Here buttons that apply to it (see Kinds of session).

Tab and Shift+Tab move between them, and Ctrl+1, Ctrl+2 and Ctrl+3 go straight to each one. F6
and Shift+F6 move between the parts and the **status bar**, which holds whatever was said last;
Ctrl+9 goes straight to the status bar. Escape in the messages or the reply box takes you back to
the session list, on the same session.

If you haven't started any supported chats yet, the session list is empty and the messages say so: start a
session with File, New Session (Ctrl+N, or Cmd+N on a Mac), or in a supported desktop app or a
terminal, and it
appears in the list.

### On a Mac

This guide gives the Windows keys. On a Mac:

- Ctrl is the Command key: Command+N for a new session, Command+Enter to send, and so on.
- Menus are in the menu bar, which VoiceOver reaches with VO+M, rather than with Alt. Where this
  guide says View, Show Sessions, that's the View menu's Show Sessions. Settings is in the app
  menu, The Chat Place.
- A context menu, the Applications key on Windows, is VO+Shift+M.

## Reading a session

### Finding it in the list

Each line in the session list reads the session's title, its folder, its state (needs you,
working or idle) and when it was last active, for example: "Fix the release build, QuickMail,
needs you: Choose a version number, active 1 minute ago". To hear the state first, or to leave
some parts out, see "Choosing what each line says" below.

By default, sessions that need you come first, then the ones that are working, then the rest,
newest first. The list refreshes itself every few seconds. While you're in it, rows stay where
they are, so nothing moves under you; F5 puts the list back in order.

To find one session among many, press **Ctrl+F** in the list and type part of its title, its
folder or what it needs. The list shows only the matches, and its name says so ("Session list,
matching "build", 3 of 139"). Escape shows them all again.

### Loading it

Arrowing through the list doesn't load anything. Press **Enter** on a session to load it: you move
to its messages and hear, for example, "Loaded Fix the build, 12 messages." Pressing Enter on the
session that's already loaded takes you back to where you were in it.

### Reading the messages

The newest message is last, and you start on it. Each line shows "You:" or the provider's name, such as "Claude:" or "Codex:", and the
message's first line, but your screen reader reads the **whole message** as you arrow onto it.
Headings and list items are read as sentences, and a code block is read as what it is, such as
"Code block, Python, 14 lines". If you'd rather hear just the first line, turn that off in
Settings, Reading messages.

Press **Enter** on a message to read it as a formatted page. There, your screen reader's browse
mode moves by heading (H), table (T), list (L) and code block (each code block is a region: R in
JAWS, D in NVDA). Links open in your browser. **Read as Plain Text** (Alt+P) shows the message in
a plain text box instead, to read by line, word and character. Escape closes either one and puts
you back on the same message. On Windows the formatted page needs Microsoft's Edge WebView2
runtime, which comes with Windows 11; without it, Enter opens the plain text box.

On a Mac the page works the same way. VoiceOver's rotor (VO+U) lists its headings, tables, links
and landmarks, VO+Command+H moves by heading, and with Quick Nav on, H, T and L work as above.
Read as Plain Text is Option+P, and Command+W closes the page as well as Escape.

Other things you can do with a message:

- **Ctrl+C** copies the whole message.
- **Ctrl+Shift+B** lists the message's code blocks, by language and size, with each one's code to
  read and copy. **Ctrl+Shift+C** copies the last code block at once.
- **Ctrl+Shift+I** lists code blocks, links, changed files, commands, decisions and tasks, for this
  message or the whole chat, and **Ctrl+L** goes straight to its links: see "Lists" below.
  **Ctrl+F** and **Ctrl+Shift+S** find text in this session or in all of them: see "Finding
  things" below.
- The Applications key (or Shift+F10) opens the message's menu.

Claude's questions to you appear as "Claude asked: …" followed by "You answered: …". Tools Claude
was refused appear as "Permission denied: …".

### Command results and compaction

Claude Code's local commands, such as `/compact`, `/context` and `/cost`, show their
output as **Claude Code** messages, even with Show Tool Activity off. A compaction
also leaves a **Conversation compacted** row and announces what happened without
interrupting sent-message read-back. When Claude reports before/after token counts,
the row and announcement include them, and Usage and Context updates to the reported
post-compaction count. The summary of earlier conversation remains tool activity,
which you can show when you want to read it. Failed commands appear as **Claude Code error**
messages too. Terminal formatting is removed, and long command output is shortened
with a notice in the app and its exports; the original transcript stays unchanged.
Command-result notifications use a brief summary even in Full announcements and
respect Summary and Silent.

### Seeing what the provider is doing

The provider's tool calls and their results are hidden until you turn on **Show Tool Activity**
(Ctrl+T, View, Show Tool Activity, or the "Show tool activity" check box below the messages).
With it on, the tool calls appear among the messages, and the open session's are also spoken as
they happen, so a long turn isn't silent: "Using Bash: git status; Read: main.py." A run of calls
is gathered up and said together ("Using Read 4 times, then Bash."), and it never cuts off your
screen reader.

Show Tool Activity belongs to each session, and it's remembered. Turn it on in one session to
follow its work, and the others stay as they were; load that session again, even after closing
The Chat Place, and its tool calls are shown again. You hear which session it changed: "Tool
activity shown in Fix the build." Continue Here keeps it on in the copy if it was on in the
session you continued. With no session loaded, Ctrl+T says so and changes nothing. A session's
tool calls also count in Links, Export and Find in All Sessions only when it's on for that
session.

### The task list

On longer work, Claude keeps a list of its steps: what it plans to do, which step it's on, and
which are done. The Chat Place reads that list for any Claude session, including desktop app
and terminal sessions. For Codex chats run here, it captures the task list and saves the latest
reported steps in The Chat Place's own data folder, so they survive reopening the owned chat.
Codex task lists from earlier sessions and chats run elsewhere are not available. A proposed plan in the
conversation is separate from the task checklist.

- **Ctrl+Shift+J** (View, Tasks) lists the steps in the provider's order, each with "to do", "in
  progress" or "done". Enter reads a step in full.
- **Ctrl+Shift+T** (Turn Status) and **Properties** say how far along it is: "Task 3 of 7:
  Running the tests. 2 done." Once work has stopped, they say where: "Stopped at task 3 of 7".
  Turn Status leaves out a list that's finished or from an earlier turn.
- When a step is marked done in the loaded session, you hear it: "Done: Run the tests. 4 of 7."
  That's only at the Full announcement level, and it never cuts off what's being read.

The provider decides when work is worth a list, so short turns usually have none. The list keeps
its last reported states after a turn finishes, fails or is stopped, or a later message has no
task update. In The Chat Place's own sessions, Claude keeps a list only because The Chat Place asks it to, as the desktop app
does. **Settings, "Let Claude keep a task list"** (Alt+K) turns that off from your next message.
That setting applies only to Claude.

## Finding things

### In this session

**Ctrl+F** in the messages (View, Find) finds the next message containing some text. It searches
each message's whole text, not just the first line you see. **F3** and **Shift+F3** find the next
and previous match, going round from the other end when they reach one, and saying so.

### In every session

When you can't remember which session you talked about something in, press **Ctrl+Shift+S**
(View, Find in All Sessions) and type a word or phrase. It works from anywhere in the window. The
Chat Place searches the whole text of every message in every session the session list shows, so
if you've chosen a view in Show Sessions, or narrowed the list with Ctrl+F, only those are
searched. Case doesn't matter. Tool calls and their results are searched only in sessions with
Show Tool Activity on.

You hear "Searching 32 sessions for "migration"" and can carry on while it works. A long search
says how far it's got every ten seconds ("Searched 12 of 32 sessions"). Press Ctrl+Shift+S again
to stop it.

When it's done, a list of the messages found opens. Its title says how many and for what first:
"The Chat Place: 6 results for "migration", Find in All Sessions". Each line gives the session,
who wrote the message, when, and the words around the match, such as "Fix the build, Claude,
yesterday 10:42: …the database migration ran…". Within a session the newest message comes first.
The list's name, and the Searched box under it (Alt+S), say how many messages were found in how
many sessions, how many sessions were searched, and how many had no transcript to read (Claude
Code deletes old ones). It stops at 500 messages.

Press **Enter** (or Go to Message) to load that session on that message: you hear "Loaded Fix
the build. Message 14 of 30: …". If the message isn't shown there, for example because it was a
tool call and Show Tool Activity is off in that session, you hear so. Escape closes the list
without loading anything.

If you've moved on by the time the search finishes (to another dialog, another place in the
window, or another app), the list doesn't jump in front of you. You hear how many were found and
"Press Ctrl+Shift+S to see them", and Ctrl+Shift+S then opens them. They wait for ten minutes,
until you choose another view of the list, or until you start a new search. With no match, you
hear "No messages contain …" and no list opens.

### Lists: code blocks, links, files, commands, decisions and tasks

**Ctrl+Shift+I** (View, Lists) opens one dialog listing each kind of thing in a chat, from the
session list, the messages or the reply box:

- **Code Blocks:** each code block, by language and size, with its code to read.
- **Links:** each link, its words and where it goes, then who wrote it and roughly when.
- **Changed Files** (Claude chats): each file Claude changed, with how many lines were added and
  removed, and its changes to read line by line.
- **Commands:** each command Claude ran: what it said it was doing, then "done", "failed" or
  "no result yet". Its full text shows the command and its output.
- **Decisions:** each question Claude asked you, with your answer.
- **Tasks:** the task list, each step with "to do", "in progress" or "done" (see "The task list" above).

In Codex chats, Lists has Code Blocks, Links and tasks captured while run here. The other kinds
say they aren't listed yet. Tasks explains when no saved list is available (#318).

**Show items from** (Alt+S) chooses **This message** or **The whole chat**. For code blocks and
links, "this message" is the selected message. For files, commands, decisions and tasks, it's the
selected message's turn: from your message to Claude's reply (for tasks, the steps added or
changed in it). Links, commands and decisions
list the newest first; code blocks and files are in the order they appear.

The kinds are a list of their own, **Kinds** (Alt+K), above the items, each with how many it
has: "Commands (12)". Tab and Shift+Tab reach it like any other list. Arrow through it and the
items below change to that kind; **Enter** moves to that kind's list. From anywhere in Lists,
these change the kind and leave you on its list:

- **On Windows:** **Ctrl+Tab** and **Ctrl+Shift+Tab**, or **Ctrl+Page Down** and **Ctrl+Page Up**.
- **On a Mac:** **Cmd+Shift+]** and **Cmd+Shift+[**, as for tabs in Safari and Finder, or
  **Cmd+Page Down** and **Cmd+Page Up** (with fn on a laptop). Control+Tab is left to macOS, which
  uses it to move between groups of controls. On keyboard layouts where ] and [ need Option, use
  Cmd+Page Down and Page Up.
- **On Windows, Ctrl+1** to **Ctrl+6** go straight to Code Blocks, Links, Changed Files, Commands,
  Decisions or Tasks. (On a Mac, Cmd+1 to Cmd+3 stay the main window's Go To keys.)

Lists opens on the kind you used last. Some keys open it on their own kind:

- **Ctrl+Shift+B** in the messages: Code Blocks, for the selected message.
- **Ctrl+L** (View, Links): Links, for the whole chat.
- **Ctrl+Shift+J** (View, Tasks): Tasks, for the whole chat.
- **Ctrl+Shift+D** (View, Changed Files): Changed Files, for the selected message's turn (your
  latest message's, if none is selected) if it changed any files, otherwise for the whole chat.

For every kind:

- **Enter** reads the item in full in the Details box below the list. For links, Enter (or
  Open) opens the link in your browser and closes Lists.
- **Ctrl+C** (or Copy) copies the item: the code, the link's address, the file's path, the
  command, or your answer. You hear what was copied. For links, **Ctrl+Shift+C** (or Copy as
  Markdown) copies a Markdown link.
- **Type** to narrow the list: the letters go into the Filter box (Alt+F), and the list's label
  says how many match, such as "Links (2 of 12)". Down Arrow goes back to the list.
- **Escape** closes Lists and puts you back where you were.

A kind with nothing to list says so, for example "No commands in this chat." Each link is listed
once, at its newest mention. A long address is shortened to its site and its end, but the whole
address is always what's opened or copied. A link in a code block or in inline code says "in
code". Only web, email and session links open; anything else, such as a file path, is marked
"copy only" and says why if you try to open it. Links in tool calls and results are listed only
with Show Tool Activity on. Changed files, commands, decisions and tasks are listed for Claude
chats for now; in a Codex chat those kinds say so.

## Kinds of session

The session list holds four kinds of session, depending on where each was started. You read
every kind the same way: arrow to it and press **Enter**. What differs is where you reply.

Only The Chat Place's own sessions can be replied to here. The others are read-only. That's
deliberate: if two programs both worked in one session at the same time, its conversation would
get tangled. Where the reply box would be, a read-only session has a short note saying where you
reply, followed by the buttons that apply to it. **Ctrl+3** moves to that note.

To list one kind, use View, Show Sessions (Alt+V, H): **Desktop App Sessions** (Code and
Cowork together), **Cowork Sessions**, **Terminal Sessions** or **Chat Place Sessions**. Ctrl+F in
the session list also finds sessions by "cowork" or "terminal".

### Desktop app sessions

You can read, search, export and see what changed in the Claude desktop app's sessions, but you
reply in the desktop app. **Open in Claude** (Ctrl+O) switches the desktop app to the session, to
answer it, approve a permission or answer a question card.

### Cowork sessions

The desktop app's **Cowork** sessions are listed too, and like its other sessions they're
read-only here. Where a session's folder would be, its line names the folder you gave Cowork to
work on, or says "no folder". **Open in Claude** (Ctrl+O) answers it in the desktop app.

A Cowork session can't be continued here: Cowork keeps its conversation inside the desktop app's
own files and its tools were run by the desktop app, so Continue Here isn't offered for it (on
the session menu it's unavailable). Whether The Chat Place can tell that a Cowork session is
working, and so announce when it answers, depends on the version of Claude Code the desktop app
runs it with; older versions don't record it. The desktop app's Chat conversations are kept on
Anthropic's servers rather than on your computer, so they aren't listed.

### Terminal sessions

Sessions you started by typing `claude` in a terminal are listed too, read-only. Each one's line
names the folder you started it in. Its title is the name you gave it with `/rename`, or else the
one Claude Code gave it, or else the start of your first message. The session list shows one as
working while the provider works in it, and as needing you while it waits on you in the terminal (a
permission or a question), and announces both. When it needs you, its line says what for, such as
"needs you: dialog open".

Claude often makes many tool calls in a terminal session. Their messages are hidden unless you
turn on **Show Tool Activity** (Ctrl+T), which also speaks each one as it happens; press Ctrl+T
again to hide them.

You reply to a terminal session in its terminal, or carry it on here with **Continue Here**. The
desktop app doesn't list terminal sessions, so Open in Claude isn't offered for them. Sessions
that scripts run with `claude -p`, and those of the VS Code extension, aren't listed.

A terminal session you load while it's working is one you're watching over someone's shoulder.
Claude writes short notes to whoever is at the terminal as it goes, such as "The issue view
printed nothing; trying again with JSON." That's Claude saying what it's doing about a step that
didn't work, not an error from The Chat Place, and Claude usually carries on by itself. The step
it means is hidden unless **Show Tool Activity** (Ctrl+T) is on: with it on, you also hear each
command Claude runs and what came back, so a note like that follows on from what you've just
heard. See Following a session as it works.

### The Chat Place's own sessions

These are the sessions you start here, with File, New Session (Ctrl+N) or Continue Here. You read
and reply to them entirely in The Chat Place, and they don't appear in the desktop app. If Claude
starts something in the background, such as a build or a wait for GitHub checks, the session keeps
running while it does, and Claude carries on by itself when it finishes (see Replying).

### Continue Here

To carry on a desktop app or terminal session here, use **Continue Here** (Ctrl+Shift+N, or the
button after the note, where the reply box would be). It makes a new Chat Place session in the same
folder, with the whole conversation so far, and you type its first message. Its title starts as
the original's, followed by "(continued)". The original session isn't changed, and what you do in
the copy doesn't appear in it.

### Following a session as it works

You can follow any kind of session while the provider works in it, wherever it was started. Load it,
and its new messages appear as they're written. Turn on **Show Tool Activity** (Ctrl+T) to hear
each tool call as it happens, so you know what the provider is doing between messages: "Using Bash: git
status; Read: main.py." Without it, the newest message can be a short note the provider wrote before
its next step, such as "Trying again with JSON", followed by a pause while it works: the note is
about a step you didn't hear. When the turn ends, or the session needs you, it's announced.

### The four kinds side by side

Kind describes the source, independently of Provider. Codex also identifies terminal, editor
and command-line origins when it knows them; other or unknown clients remain external sessions.
An Codex chat started here is a Chat Place session, just like an owned Claude chat.

The Claude-specific desktop and Cowork actions are shown in this table:

| Kind | Started in | Its line adds | You reply | Open in Claude | Continue Here |
|---|---|---|---|---|---|
| **Desktop app session** | The Claude desktop app's Code tab | "desktop app session" | in the desktop app | yes | yes |
| **Cowork session** | The desktop app's Cowork tab | "Cowork session" | in the desktop app | yes | no |
| **Terminal session** | A terminal, by typing `claude` | "terminal session" | in that terminal | no | yes |
| **Chat Place session** | The Chat Place (File, New Session) | "Chat Place session" | here, in the reply box | no | not needed |

### Local Codex chats

Codex chats appear automatically when a native Codex CLI is installed and signed in
to ChatGPT. That includes the copy bundled with the ChatGPT app on Mac, the Codex app's copy
on Windows, and the copy Codex's own installer keeps in your `.codex` folder. Settings offers
**Include Codex chats (restart required)** to turn them off or on; your choice is kept.
If Codex isn't found, Settings says to install the Codex app or CLI, then restart. Local Claude and
Codex chats share one list, and selecting a Codex chat opens its history
automatically. Read, Find and export work. New Session offers a Provider choice; select
Codex to start a chat you can reply to here. Continue Here copies an existing
Codex chat into a new chat started here. These chats support text replies, Stop,
queued messages and Answer (Ctrl+Shift+A) for permissions and questions. Turn the option off and restart
to hide Codex without deleting its saved preferences. Quitting The Chat Place
requests a stop for its running Codex replies too. Revisiting a chat preserves
its running turn. The Chat Place refuses to resume a saved chat whose latest turn is
unfinished or uncertain. If the connection is lost, use Stop to check the
saved turn and reconnect once it has ended. No message is resent. Selecting
the chat again does not automatically reconnect. If Codex still reports
unfinished work, The Chat Place refuses recovery and says why. Requests The Chat Place cannot present
safely are declined with a notice without disconnecting the chat; Turn Status
keeps the notice available. Turn Status (Ctrl+Shift+T)
reports elapsed time and the latest activity. Your messages are read back
according to the same announcement setting used for Claude chats. If a send
cannot be confirmed, reopen the chat to read its history before sending again.


Continue Here starts a Codex copy read-only. You can select folder editing for
a project folder, but the home folder, its parents and volume roots cannot be
used as writable folders. If Stop is acknowledged but the turn stays open,
press Stop again to check Codex's saved turn status without resending anything.

The Chat Place disables external tools (MCP servers, apps and plugins) for
Codex chats started here because those tools can reach beyond the selected
folder. It checks that they are disabled before sending. Your global Codex
configuration stays unchanged; existing chats can still be read.

Codex keeps your own saved command rules in effect, including user rules such
as `~/.codex/rules/default.rules` and system rules installed for your computer.
Commands you've told Codex to always allow will run without asking and outside
the folder's limits, so they can change files elsewhere and use the network.
New Session and Change Permission Mode explain this. Properties shows how many
user/system rule files are in effect, and Chat Permissions (Ctrl+Shift+H) lists the
commands in them: allowed without asking, always asks, or never allowed.

The Chat Place refuses rules supplied by a project: `.codex/rules` in the selected
folder or its parent projects. Choose a folder without those rules, or move the
project rules out before trying again. It also refuses to start or send when the
rule inventory cannot be checked. Existing chats can still be read. The Chat Place
does not remove or edit rules, change your Codex settings, or save approval amendments.

On Windows, Codex can edit a folder only after its Windows sandbox is set up.
The simplest way is Set up Agent sandbox in the Codex app. With only the command-line
tool, add `[windows]` and `sandbox = "unelevated"` to `~/.codex/config.toml`: that is
the setting checked with The Chat Place, and OpenAI's fallback for when administrator
setup isn't possible. OpenAI's [Windows sandbox guide](https://developers.openai.com/codex/windows)
describes its preferred choices, MXC on compatible PCs and `"elevated"` (which needs
setup approved by an administrator); they haven't been tried with The Chat Place yet.
Until the sandbox is set up, Codex reports chats as read-only, and The Chat Place says so
instead of giving the chat folder editing; choose read-only, or set up the sandbox and
try again. Codex also reports read-only when your Codex settings allow only read-only,
such as requirements an administrator set. If a new Codex chat can't be started, your
first message is kept: in the open chat's message box if you can type there, otherwise
on the clipboard.

## Starting a session and replying

### A new session

Press **Ctrl+N** (File, New Session). Choose Provider when more than one is available; your
last choice is remembered. For Codex, Model and Effort load the choices available in Codex in the
background. Configured Codex model/effort keeps its settings; choose a specific model to select
one of its supported efforts. Choices are rechecked when the chat starts. If the list cannot be
loaded, configured defaults remain available. Continue Here offers the same choices. Codex
defaults to editing files in its selected folder with on-request approvals. Choose
read-only in New Session, or use File, Change Permission Mode (Ctrl+Shift+O) on
an idle Codex chat started here. Existing chats keep their saved permissions. The model names and
permission modes below describe Claude chats. Then fill in:

- **Folder:** the project folder the session works in. It starts at the folder you used most recently.
  The list holds the folders your sessions have used, The Chat Place's and the desktop app's,
  newest first: on Windows, Up and Down Arrow move through it, and Alt+Down Arrow opens it. You
  can also type a folder, or choose one with **Browse** (Alt+B).
- **From GitHub** (Alt+G) lists your GitHub repositories, and you hear how many. Type to narrow
  the list, or type owner/name (or a GitHub address) for that repository, even one that isn't
  yours, then press Enter. If the repository is already in your GitHub folder, that folder is
  used; otherwise it's cloned there first, and you hear "Cloning" and then "Cloned". Cancel
  stops a clone and leaves nothing behind. This uses the GitHub CLI (gh), signed in with gh auth
  login; if it can't list your repositories, the list says why.
- **Work in** (Alt+I): **The folder as it is** (it says which branch the folder is on), or
  **A new worktree**, a separate copy of the repository on a branch of its own, so the
  session's changes stay apart from the folder and from your other sessions. With a worktree,
  choose a branch in **Branch** (Alt+R), or type a new name to make that branch from the
  folder's current one. A branch that's already checked out somewhere isn't offered, since a
  branch can be in only one place. When you press Start, you hear "Making the worktree", and
  the session starts once it's made, inside the repository's .claude folder where Claude Code
  and the desktop app make theirs. It stays there when you're done; remove it with git worktree
  remove.
- **Title:** optional; without one, it's made from the first words of your message.
- **Model:** Default (your Claude Code setting), Opus, Sonnet or Haiku, each the latest of its
  kind. File, Change Model changes it later, from the next turn. Fable isn't offered, because on
  some plans it bills to usage credits without asking; if a turn would run on Fable anyway, The
  Chat Place stops it before anything is sent, and says why.
- **Effort:** how hard Claude thinks: Default (your Claude Code setting), Low, Medium, High,
  Extra high or Max. Models that don't support effort levels ignore it. File, Change Effort
  changes it later, from the next turn.
- **Permission mode:** how much Claude may do without asking. Auto, the default, lets Claude
  decide what's safe to run. Accept edits lets it change files without asking. Manual asks before
  anything that needs approval. Plan has Claude make a plan, without changing anything, for you
  to approve. File, Change Permission Mode (Ctrl+Shift+O) changes it later, from the next
  turn.
- **First message:** what you'd like the selected provider to do.

If the first message never reaches Claude (Claude Code isn't signed in, for example), it goes back
into the reply box, and Send starts the session again.

### Changing a session's model, effort or permission mode

For a Claude session started by The Chat Place, these File menu items change what its next turns use.
Each opens a list that starts on the current choice, marked "(now)"; choose another and press
Enter, or Escape to leave it as it is.

- **Change Model** (Alt+F, D): Default, Opus, Sonnet or Haiku.
- **Change Effort** (Alt+F, F): Default, Low, Medium, High, Extra high or Max. Models that don't
  support effort levels ignore it.
- **Change Permission Mode** (Ctrl+Shift+O): auto, accept edits, manual or plan.

You hear the change and when it counts, such as "Permission mode for Fix the build: plan, from its
next turn." A turn that's already running carries on as it started, and you hear that too. The
session's heading, at the top of its messages, says its model, and its effort and mode when they
aren't the defaults: "on Opus at high effort, plan mode". Approving a plan, or allowing something
"for this chat" that switches mode, changes the mode as well; approving a plan starts on the
mode you chose here, unless that was plan.

Desktop app and terminal sessions are set in Claude or in the terminal, so for them these items
say so and change nothing.

For an Codex chat started here, **Change Permission Mode** changes an idle chat between
read-only and editing files in its selected folder with on-request approvals.
Existing saved permissions stay in effect until the change is verified and saved.
Choose Codex model and effort in New Session or Continue Here; changing them
later with Change Model or Change Effort is not yet supported.

### Replying

In one of The Chat Place's own sessions, type in the reply box and press **Ctrl+Enter** (or Send).
You stay in the reply box, and you hear what was sent and where, such as "Sent to Fix the build:
Run the tests." When the provider finishes, its answer is announced.

While the provider is working, you can still send. Your message is queued ("Queued for Fix the build:
…"), shown at the end of the messages as "Queued: …", and sent once the current turn finishes.
On a queued message, Ctrl+Enter sends it now (Claude answers it straight away, in the same turn),
and its menu can also edit or remove it. **Stop** (Ctrl+Period) ends the turn, and anything it
started, such as a build; a queued message then goes back into the reply box.

Sometimes Claude starts a command that runs in the background, such as a build or a wait for
GitHub checks, and answers while it runs. You hear the answer and what is still
running ("Still running in the background: Windows build"), but the turn stays open. When the work
finishes, Claude carries on by itself, and its next answer is announced as usual. While Claude is
waiting, what you send goes to it at once instead of being queued. Stop ends the turn and the
background work with it.

**Ctrl+Shift+T** (Turn Status) says how long the provider has been working on this turn and what it last
did, or what is still running in the background, and how far along the task list is. There's
no time limit on a turn.

### Commands, skills and attachments

These are for The Chat Place's own sessions:

- **Ctrl+/** (or the Commands button) lists your skills and slash commands, then Claude Code's
  own, such as /compact and /context, each with its description. Type to filter, Down to the list,
  Enter to choose: it goes at the start of the reply box, ready to finish and send.
- **Ctrl+Shift+F** (or Attach Files, Claude chats) adds files and pictures to your next message, and **Ctrl+V**
  in the reply box attaches a picture on the clipboard, such as a screenshot. The attachments are
  listed under the reply box, and Delete there removes one. Claude sees pictures as pictures, and
  reads other files from where they are.

### Saved prompts

Keep messages you send often as prompts, each with a name, and put one in the reply box when you
need it:

- **Ctrl+Shift+P** (File, Prompts) lists your prompts by name, with the selected one's text below
  it (Alt+X to read it). **Enter**, or Use, puts the prompt in the reply box of the loaded Chat
  Place session, at the cursor, ready to change and send.
- In the same dialog, **New** and **Edit** ask for a name and the text (Enter starts a new line in
  the text, and Ctrl+Enter saves). **Delete** asks first. **Move Up** and **Move Down** change the
  order. Each change is saved straight away, and you hear what happened, such as "Prompt saved:
  Review". Escape closes.
- **Ctrl+Shift+Enter** in the reply box saves what you've typed as a prompt and sends it. It asks
  for the prompt's name first, starting with the message's first few words. Press Escape there
  and nothing is saved or sent: your message stays in the reply box.

### Sending a reply to another chat

Two agents can work on the same thing, with you passing their words between them: Claude builds
and Codex reviews, say, or you ask both and let each answer the other. **Ctrl+Shift+E** (File,
Send Reply to Another Chat, also on a reply's menu in the messages list) does the copying and
pasting.

1. In the chat with the reply, select it in the messages list. If a message of yours is
   selected instead, the latest reply is used.
2. Press **Ctrl+Shift+E**. **Send to** lists The Chat Place's own chats, Claude and Codex, other
   than this one. Desktop app, terminal and hidden chats aren't offered. The chat you send to
   must already be started: start it first with File, New Session (Ctrl+N).
3. Below it, **Message** has the reply, starting with a line that says which agent wrote it and
   in which chat, so the agent reading it knows these are another agent's words, not yours.
   Change it or add your own words; Enter there starts a new line.
4. Press **Ctrl+Enter** (or **Enter** in the list, or Send). The Chat Place goes to that chat and
   sends it, just as if you'd typed it there: you hear what was sent and where, as when you send,
   and it's queued if that chat is working. A Codex chat that isn't ready yet gets ready first
   ("Getting Reviewer ready"), and the message goes when it is; if you go to another chat before
   then, it isn't sent and stays in that chat's reply box.

When the answer comes, press **Ctrl+Shift+E** again: the chat the reply came from is first in the
list, so **Enter** sends the answer back. If a message can't be sent, it's left in that chat's
reply box, so nothing is lost. If the dialog's title says the chat is still working, the reply
you're sending may be a step on the way rather than its answer.

A forwarded message is only ever a message. It can't answer a question or approve a tool for the
other agent: those still come to you, in each chat, as they always do.

### Agent conversations

An agent conversation puts two chats, Claude and Codex or two of either, in one conversation
with you, and passes each one's replies to the other. It's Send Reply to Another Chat done for
you, turn after turn.

**Starting one:** press **Ctrl+Shift+G** (File, New Agent Conversation).

- **Title:** the conversation's name. Left empty, it's the opening message's first line.
- **First agent's chat** and **Second agent's chat:** a new Claude or Codex chat, or one of
  your own chats, which keeps its history. A chat can be in one conversation at a time.
- **Role** (optional) for each, such as "Build the fix" and "Review each change; don't edit
  files". Each agent is told its role and the other's.
- **New chat's permission:** for a new chat, its permission mode, as in New Session.
  **Folder for new chats** is where they work.
- **Hand-offs:** approve each one, or automatic (offered once it's on in Settings), with a
  **Turn limit**.
- **Who goes first:** the first agent, the second, or both.
- **Opening message**, then **Ctrl+Enter** (or Start).

The conversation is one row in the session list, with Provider "Claude + Codex" and Kind "agent
conversation". Its two chats leave the list while they're in it;
**File, Agent Conversation Members** goes to either one.

**Reading it:** the messages list has both agents' messages in order. Each starts with who said
it: "Claude: …", "Codex: …", "You, to Codex: …". The relay's own lines start with "Relay", such as
"Relay: Passed Claude's reply to Codex." Enter reads any of them in full, including the setup
each agent was sent at the start.

**Writing in it:** what you type goes to the agent whose turn it is (Turn Status says which).
Start with @ and a name to choose: "@Codex check the tests", or "@all" for both (their replies
are then shown but not passed on).

**Hand-offs:**

- **Approve each** (the default): when an agent answers, you hear its reply, then "Ctrl+Shift+E
  hands it to Codex". The row says a reply is waiting. **Ctrl+Shift+E** shows the reply,
  labelled with who wrote it; change or add to it, then Ctrl+Enter sends it on.
- **Automatic:** turn on "Let agent conversations hand replies across automatically" in
  Settings, then choose Automatic in New Agent Conversation, or for a conversation with
  **File, Agent Conversation Hand-offs**. Replies go across by themselves until the turn limit (6
  exchanges unless you choose another). It stops sooner when an agent ends its reply with
  DONE, when the replies start repeating each other, when an agent asked you something during
  its turn, or when a turn fails. You hear why, and the last reply waits for Ctrl+Shift+E. A
  message from you, or a reply you hand on, starts it again with a fresh count.

**Stopping:** **Ctrl+Period** stops both agents and the relay. **Ctrl+Shift+T** says what each
agent is doing, how far the count has got, and who your next message goes to.

**Safety:** each agent asks you for permissions and answers as it always does, through
**Ctrl+Shift+A**. A message from one agent goes to the other only as a message, labelled as
another agent's words; it can never answer a question or approve anything. Each agent is told
to take instructions only from you. Every message uses that agent's own subscription, so
automatic mode spends both.

**Rename** (F2), **Properties** (Alt+Enter) and **Delete** (Shift+Delete) work on the
conversation's row. Deleting it keeps both chats, with everything said in them, and puts them
back in the list.

## Answering permissions and questions

**Answer** (Ctrl+Shift+A, File, Answer) handles the loaded chat first. A Codex permission
request shows the command, its folder and Codex's reason, and a "This also needs" list when
Codex names extra access, such as writing to another folder or using the network. When it
names none, the dialog says the command may run outside the chat's folder sandbox, where it
can change any file you can. It offers only the one-time answers Codex allows for that
request: Allow Once, Deny, Deny and Stop Turn, and always Answer Later. Deny lets Codex carry
on without it; Deny and Stop Turn also stops the turn. Enter chooses Deny, or Answer Later
when Deny isn't offered. For eligible Git commands, **Allow for This Chat** remembers your
approval in The Chat Place only, for the current chat. Codex's own session and saved-rule
choices are never sent. Other answers cover one request.
Questions show the provider and keep secret answers masked. New Session and Change Permission Mode offer read-only or editing files in the
selected folder, both with on-request approvals. Finish or Stop active work and
clear queued messages before changing the policy. Verification and saving must
succeed before the new policy can send a turn.

### Claude permissions and plans

In one of The Chat Place's own sessions, when Claude needs a permission, asks you a question, or
has a plan for you to approve, the turn waits for you. You hear it ("Fix the build needs you.
Claude wants to run git push. Ctrl+Shift+A answers."), and the session shows as "needs you" in
the list. Press **Ctrl+Shift+A** to answer:

- **A permission:** read the whole request (the command, or the file and what would change), then
  choose **Allow** (this request only), **Allow for This Chat** (keep a rule so it doesn't ask
  again in this chat; shown when Claude Code suggests one, and named in full) or **Deny**. Deny is
  the default button, so pressing Enter by mistake never allows anything, and you can give a
  reason Claude reads. "Permissions: what a chat may do without asking" below explains every level.
- **A question:** each question is a group of choices, with Other and a box for your own answer.
  Send Answers, or Don't Answer.
- **A plan:** read it (Read Formatted shows it as a page, by heading), then **Approve**, choosing
  how Claude carries on, or **Keep Planning**, the default, with what to change.

**Escape answers later.** Nothing is allowed or refused by waiting: the turn keeps waiting, and
Ctrl+Shift+A brings the same request back. "Allow for This Chat" lasts for the rest of that
chat, not just the turn, until you remove it in File, Chat Permissions. The Chat Place never
changes Claude Code's own settings files.

For a desktop app session, a "needs you" means the desktop app's summary of its latest turn says
it's waiting on you. Answer it in the desktop app (Ctrl+O). The desktop app doesn't write that
summary after every turn, so a desktop session can be waiting on you and still show as idle.

## Permissions: what a chat may do without asking

Several things together decide whether a chat asks you before it acts. **File, Chat Permissions** (Ctrl+Shift+H) shows them for a chat started here, working or idle. Desktop app and terminal sessions are read-only here, so they have no Chat Permissions.

### For a Claude chat

1. **Your answer to one request.** When Claude asks, you hear it, and Ctrl+Shift+A answers:

    - **Allow** lets this one request run. Nothing is kept; the same request asks again next time.
    - **Allow for This Chat** lets it run and keeps a rule with this chat, so later requests the rule covers don't ask. It's shown only when Claude Code suggests something to keep, and its name says what it keeps, such as "Allow, and don't ask again in this chat for `Bash(git push:*)`". For a command that writes, such as `mkdir`, it also keeps the folder the command works in ("…, and let Claude work in the folder …"), because Claude Code asks again without it; Claude can then use that folder as it uses the chat's own. When Claude Code suggests only accepting file edits, it switches the chat to Accept edits instead.
    - **Deny** refuses, and can carry a reason Claude reads. It's the default button.

2. **Rules kept with this chat.** What you kept with Allow for This Chat. They last across turns and restarts until you remove them in Chat Permissions. No other chat gets them, and a Continue Here copy starts with none.

3. **The chat's permission mode.** Auto: Claude decides what is safe to run without asking. Accept edits: file edits don't ask; commands that need approval do. Manual: anything that needs approval asks. Plan: Claude plans and changes nothing. Change it with Change Permission Mode (Ctrl+Shift+O) or Change in Chat Permissions; it counts from the next turn.

4. **Claude Code's own settings.** These apply to every Claude session in the folder: here, in the desktop app and in a terminal. The Chat Place only reads them; change them in Claude Code, for example with `/permissions`. They come from:

    - your settings: `.claude\settings.json` in your user folder;
    - the chat's folder: its `.claude\settings.local.json`, and its shared `.claude\settings.json`, whose allow rules count only once that exact folder is trusted in Claude Code (see below);
    - an administrator's policy file, if your computer has one.

    A parent folder's settings don't count.

### How the levels combine

- A "never allowed" rule in Claude Code's settings refuses without asking, even if you allowed it for this chat or the mode would allow it.
- An "always asks" rule in Claude Code's settings asks every time, even after Allow for This Chat.
- Otherwise, anything allowed at any level runs without asking.
- Claude Code never asks about commands it knows only read, such as `git status`, even in Manual.

### Trusting a folder in Claude Code

A folder's shared `.claude\settings.json` usually comes with the project, so Claude Code ignores its allow rules until you've said you trust that folder. To trust it, open a terminal in that exact folder, run `claude`, and answer yes when it asks whether you trust the files in this folder. Trust is for that folder only: trusting a parent folder, or the repository a worktree was made from, doesn't count. Until then, Chat Permissions lists those rules as waiting.

### Chat Permissions for a Claude chat

- **Permission mode**, in words, with **Change...**.
- **Allowed for this chat:** one row per kept rule or folder, read in full: what it allows, the rule itself and when it was allowed, such as "Run git push (any arguments). Bash(git push:*). Allowed 19 October 2026 at 14:05." A rule kept before this release says "Allowed: time not recorded". With nothing kept, the list says "Nothing is allowed beyond the permission mode."
- **Remove** (or Delete in the list) takes the selected row back, and **Remove All** takes them all. Each asks once, naming what goes; No is the default. Removing a rule leaves a folder kept with it; remove the folder's row too. A removal counts from the next turn: a turn that's running keeps the rules it started with, and the dialog says so.
- **About these permissions** (Alt+T) says when a removal counts, then lists the rules from Claude Code's own settings that apply to this chat's folder, each with its file, and any shared rules waiting for the folder to be trusted. These can't be removed here.

### For a Codex chat

**Allow for This Chat** remembers an eligible Git command prefix for this chat and its exact
working folder. It supports ordinary arguments for `git status`, `switch`, `add`, `commit`,
`push`, `fetch`, `diff` and `log`. Explicit force or delete options in the command, unknown options, compound commands,
shell expansion, other working folders, and requests for separately listed extra access
still ask. The button appears only when this request can be remembered.

A remembered approval has the same authority as **Allow Once**. The command may run outside
the sandbox, access other files and the network, and run Git hooks or configured programs.
The working folder identifies where it starts; it does not confine what it can do.
A plain push follows the repository's configuration, which can itself request forced updates
or deletion.

Approvals survive restarting the app. A Continue Here copy starts with none. The Chat Place sends
one-time answers on your behalf and announces automatic approvals once. The current turn's
automatic approvals appear after the conversation and in Turn Status. Codex keeps no native
approval, and neither Codex's nor Claude's settings are changed.

**Chat Permissions** lists each kept approval and its date, with **Remove**, Delete (Backspace
on Mac) and **Remove All**. Removal applies before the next approval is sent, even during a
running turn; work already approved may finish. Changing mode clears the kept approvals.
The mode can change once the turn has finished.

Below the list, **About these permissions** (Alt+T) also lists Codex's own saved rules: the
commands you've told Codex to always allow, and any it always asks about or never allows,
with the file each comes from. Each rule covers any command that starts with its words, so
a rule for `git push` also covers `git push --force`. They apply to every Codex chat, so a
command there runs without asking even with nothing kept in this chat. In a chat set never to
ask, Codex refuses a command that a rule says to ask about. The Chat Place only reads them; change
them in Codex or edit the file. Removing The Chat Place's own approval does not remove a
Codex rule.

## Organizing your sessions

With many sessions, these keep the list manageable.

### Choosing which sessions are listed

**View, Show Sessions** (Alt+V, H) chooses what the list shows: all sessions; the ones that need
you or are working; just the ones that need you; just the working ones; the ones with a new reply
you haven't read yet; the idle ones; desktop app sessions (Code and Cowork); just
Cowork sessions; The Chat Place's own; terminal sessions; Remote Control sessions; Claude chats;
Codex chats; ungrouped sessions; archived
ones; hidden ones; or one of your groups.
**Claude Chats** and **Codex Chats** filter by provider. They remain available
when Codex is off; Codex Chats is empty until it is enabled.
**View, Sort Sessions** (Alt+V, O) chooses the order: by status, newest first, oldest first, by
title, or by folder. Both choices are remembered, and you stay on the same session when it's
still listed.

### Choosing what each line says

**View, Session List Columns** (Alt+V, E) chooses which parts each session's line reads, and in
what order. Move Status to the top, for example, and each line starts with "needs you", "working"
or "idle", so you can stop on the one you want before hearing its title.

The parts are called columns: Title, Provider (Claude or Codex), Folder, Status, New reply, Last activity, Kind (desktop app,
Cowork, terminal or The Chat Place), Remote Control, Archived, Hidden and Groups, all read at first. Started and Last message are read only once you add them. Provider
says which AI; Kind says where the session started. Older saved choices gain Provider once, immediately
after Title (or first if Title is hidden), without changing their other columns. If you remove Provider afterward, it stays removed. A column with nothing to say for
a session, such as New reply when there isn't one, is simply left out of that line.

**Last activity** ("active 5 minutes ago") is the last time the session did anything, even in the
middle of a long turn, or while it's being run outside The Chat Place. How long the turn now
running has taken is in Turn Status (Ctrl+Shift+T). **Started** ("started 2 days ago") is when
the session began.

**Last message** tells you where each conversation ended without loading it: the provider's
name ("Claude:" or "Codex:") or "You:"
and the start of the last message either of you wrote, as plain text on one line, cut at about
150 characters with "…". A link reads as its words, and a bare web address as its site. Tool calls and their results don't count. To read the whole message,
press Enter to load the session, then Enter on the message. A session whose conversation is
missing or can't be read says nothing in this column. While a session is working, the line you're
on keeps its last message until the status changes, so you don't hear the line again each time
the provider writes; the other lines change at once, and you hear the new message when you arrow back.
With Last message first, lines start with the provider's name or "You", so typing a letter no longer jumps to
a title; Ctrl+F still finds a session.

The dialog has two lists. **Available columns** (Alt+V in the dialog) are the ones not read;
**Shown columns** (Alt+S) are the ones read, in order. The buttons Add, Remove, Move Up, Move
Down, Move to Top, Move to Bottom and Reset to Default do what they say, and each tells you what
happened ("Status moved to top, 1 of 11"). The keys work too:

- **Enter** in Available columns adds the column to the end of the line.
- **Delete** in Shown columns removes one (on a Mac, Delete or Fn+Delete). At least one column
  is always shown.
- **Alt+Up** and **Alt+Down** in Shown columns move the column up or down; **Alt+Home** and
  **Alt+End** move it to the top or bottom. On a Mac these use Option.

You stay on the column you moved. The preview at the bottom reads the selected session's line as
it will be. **OK** saves your choice and changes the list at once, still on the same session;
**Escape** or Cancel leaves it as it was.

With Status first, every line starts with "needs you", "working" or "idle", so typing a letter
in the list no longer jumps to a title starting with it. **Ctrl+F** still finds a session by its
title or folder, whichever columns you show.

### Groups

A group collects related sessions, such as everything for one project or everything you're
waiting to hear back on, so you can show just those.

- **Ctrl+G** (File, Add to Group) puts the selected session in a group, or in a new one.
- File, Remove from Group takes it out.
- File, Manage Groups lists your groups, with how many sessions each has, to make, rename or
  delete them. Deleting a group never deletes its sessions.

A session can be in several groups, and its line in the list says which ("group Work"). To see
one group, choose it in View, Show Sessions. Ungrouped, in the same menu, lists the sessions that
aren't in any group yet.

The desktop app's own groups show here too: in View, Show Sessions, in each session's line, and in
Add to Group. They can only be changed in the desktop app. Choosing one of them in Add to Group
makes a Chat Place group of the same name, which is shown together with the desktop app's.

### Renaming, hiding and deleting

- **F2** renames the selected session. One of The Chat Place's own sessions is renamed outright.
  A desktop app session's new name shows only here, since the desktop app's files are never
  changed; clear the name to go back to the desktop app's.
- **Delete** hides a session without changing it. View, Show Sessions, Hidden lists hidden
  sessions, and File, Bring Back Session returns one.
- **Shift+Delete** deletes one of The Chat Place's own sessions permanently, conversation and all,
  after you confirm (No is the default). Desktop app sessions can only be hidden here; delete
  those in the desktop app.

On a Mac laptop, Delete and Shift+Delete are Fn+Delete and Fn+Shift+Delete.

The session's menu (the Applications key or Shift+F10 in the session list) has all of these, and
the other commands for the selected session.

## Announcements and notifications

When the open session gets a reply, when one of The Chat Place's sessions finishes a turn, or when
any listed session stops working, it's announced through your screen reader (or a system voice
when no screen reader is running) and put on the status bar. **Ctrl+Shift+R** repeats the last announcement.

**Settings** (Ctrl+Comma) chooses how much is said:

- **Full** reads the whole reply.
- **Summary** reads the session's name and the reply's first sentence.
- **Silent** puts it on the status bar only.

Settings also chooses whether every listed session is announced or just the open one, which
speech engine and rate to use, whether your own messages are read back when they're sent, and
whether The Chat Place tells you when an update has been installed.

On Windows, **Automatic** speaks through JAWS or NVDA, whichever is running, in your screen
reader's own voice and rate. NVDA works without installing anything else. A system voice speaks
only when no screen reader is running, so you never hear a second voice over your screen reader.
If your screen reader is running but The Chat Place can't reach it, a Windows notification says
announcements aren't being spoken, the status bar gives the reason beside the announcement, and
Settings shows it in a Speech problem box after the speech engine.

When you're in another window, The Chat Place can show a notification: when one of its sessions
finishes a turn, fails or needs you, when the session you have loaded finishes, or when a desktop
app session starts needing you. Choosing
the notification brings The Chat Place forward with that session loaded. Settings, Windows
notifications (on a Mac, Notifications) chooses every finished turn (the default), only when a
session needs you, or off. While you're in The Chat Place, the announcement is enough and no
notification is shown.

## More things you can do

- **Changed Files** (Ctrl+Shift+D) opens Lists on Changed Files: the files Claude changed
  in the selected message's turn (your latest, if none is selected), or in the whole chat, with
  how many lines were added and removed.
  Enter on a file reads its changes line by line. It works for desktop app sessions too.
- **Usage and Context** (Ctrl+Shift+U) opens a list to read with the arrow keys: how full the
  session's context is ("Context 62% full: 124,000 of 200,000 tokens"), then each of your plan's
  usage limits on its own line. Ctrl+C copies the selected line, Copy All (Alt+A) copies every
  line, and Escape closes the list. It's also said once, unasked, when the context passes 80%, or
  a limit passes 90%.
- **Properties** (Alt+Enter, View menu, or the context menu) lists what can be known about a
  session or a message, one "Property: value" line each. In the session list it's the selected
  session: its kind, folder, status, when it started and was last active, its model, how many
  messages, tool calls, links, code blocks and changed files it has, how full its context is, its
  groups, its IDs and its transcript file. In the messages it's the selected message: who it's
  from, when, where it is in the list, how many lines, words and characters, links and code blocks
  it has. Elsewhere it's the loaded session. Ctrl+C copies the selected line, Copy All (Alt+A)
  copies every line, and Escape closes the list. On a Mac the key is Option+Return.
- **Export** (Ctrl+E) saves a session's conversation as Markdown, a web page or plain text.
- **What Claude Knows About You** (Ctrl+Shift+K) lists what Claude Code keeps about you: your
  instructions (CLAUDE.md files), the memories it saved, your skills, subagents, commands and
  settings. Enter reads one, and Edit in Your Editor opens it in your own editor.

### Codex usage

View, Usage and Context (Ctrl+Shift+U) reads current Codex account limits in
the background. Arrow through the used/remaining percentages and reset times,
and use the same Copy/Copy All controls. Limits are shared across your Codex
chats. Missing values say not reported. Codex chats started here also show the latest
reported model request's input, output and total tokens separately from cumulative
usage. When Codex reports its context window, the latest request's percentage
appears here and in the status bar; at 80% The Chat Place warns once until the next compaction.
After an owned Codex turn ends, account limits are checked in the background.
The Chat Place warns at 80% and 100%, once per allowance and reset window, without
interrupting read-back.
These values are available only after this connection receives a usage report.
Missing windows stay not reported; The Chat Place never substitutes Claude's counts or model
defaults.

## Links to sessions

A link to a session takes you straight to it, from anywhere you can paste one: a note, an
email, a message to another session.

**Copying one.** In the session list, press **Ctrl+Shift+L** (or File, Copy Session Link, or
Copy Session Link on the session's menu). A list offers the links that apply, best first, and
Enter copies the first: a Markdown link, with the session's title as its text, that opens the
session in The Chat Place. Arrow down for the others:

- the same link without the Markdown, just the address;
- for a Claude desktop app session, its Open in Claude link, which opens it in the desktop app;
- for one of The Chat Place's sessions on Remote Control, its claude.ai address, for another
  computer or your phone.

What was copied is said, such as "Copied a Markdown link to Fix the build, to open it in The
Chat Place." Escape copies nothing.

**Following one.** A link that starts `thechatplace://session/` opens The Chat Place, or brings
it forward if it's running, with that session selected and its messages loaded, and you in the
messages, just as if you'd pressed Enter on it. If the list is showing a view or a search that
leaves it out, it shows all sessions (or Archived or Hidden, where the session is) and says so.
A link in a message works too, from the message's formatted page. If a dialog is open, the link
waits until you close it.

A link only finds a session, as Enter on it does. It never sends a message, runs a turn or changes a
conversation, so it's safe to follow one from anywhere. A link to a session that's gone says "No
session with that link", and anything else that starts `thechatplace:` says "That link isn't one The
Chat Place knows."

**Where links work.** On Windows, the installed copy of The Chat Place sets up
`thechatplace://` links for your Windows account when it's installed and each time it starts,
and uninstalling it removes them. The portable copy doesn't, so links open the installed copy
if you have one. On a Mac, links work once The Chat Place is in Applications. A link copied on
one computer opens the session only on that computer.

## Remote Control

Remote Control lets you keep an eye on one of The Chat Place's own sessions when you're away from
your computer, on claude.ai or in the Claude app on your phone. Turn it on with File, Remote
Control, or from the session's menu. Its line in the session list then says "Remote Control", and
File, Remote Control copies the session's claude.ai address.

**What you can do today:**

- While Claude is working on a message you sent from The Chat Place, open the session on
  claude.ai or your phone to follow what it's doing.
- Answer Claude there. If it asks a question or wants permission, you can answer on your phone
  instead of at your computer, and it can ask again later in the same answer, as often as it
  needs. Whichever place you answer first counts, and the other one stops waiting.
- Type to Claude there while it's working.

**What you can't do yet:**

- Start a new message from your phone. Once Claude finishes answering, the session shows as
  offline on claude.ai until you send your next message from The Chat Place.
- See the session from another computer's copy of The Chat Place. The session lives on the
  computer that started it; claude.ai is how you reach it from elsewhere.
- Read a session on another computer from here. You can send it a message and hear its reply
  (see "Your sessions on other computers", next), but its own conversation is only on claude.ai
  and in the Claude app.

**Good to know:** turning Remote Control on copies the conversation to claude.ai, and it stays
there even if you turn Remote Control off or delete the session here (you can archive it on
claude.ai). While Claude is working, anyone signed in to your Claude account can type into it.
Settings can turn Remote Control on for all of The Chat Place's sessions; then a session's line
says so once it has connected.

For a Claude desktop app session, File, Remote Control offers to open it in the desktop app, where
you turn Remote Control on, or to continue it here as a copy with Remote Control on.

## Your sessions on other computers

If you use Claude Code on more than one computer, say a laptop, a desktop and a Mac, The Chat
Place can send a message to a session on another of them and let you hear the answer, without
going to that computer. It goes through Claude: Claude in one of The Chat Place's own sessions
here passes your message on, and the other session's reply comes back to that session.

**What you need:**

- One of The Chat Place's own sessions, loaded, with Remote Control on (File, Remote Control).
  Any of your sessions will do. One kept for the purpose, called "Hub" say, keeps these messages
  out of your other work.
- On the other computer, a Claude Code session with Remote Control on: in the Claude desktop
  app, in a terminal, or in The Chat Place there. That computer has to be awake.

**Sending a message:**

1. Load your session and press **Ctrl+Shift+M** (File, Other Machines).
2. The first time, the list only offers **Refresh the list (asks Claude)**. Choose it. Claude
   looks up your sessions on other computers, as a turn in this session, and says what it found.
3. When it has answered, press **Ctrl+Shift+M** again. Now the list has each session on your
   other computers and how it is: "idle", "working", "needs you" or "offline".
4. Choose one, type your message, and press **Enter**. Claude sends it word for word.
5. The answer arrives in the same session as a message from that session, "From Mac Hub" for
   example, and it's announced. Enter reads it in full, like any message.

The list is Claude's latest look, so after a while choose Refresh again. Other Machines works
between turns. While Claude is working, wait for it to finish.

**Messages from other sessions.** Any message another Claude Code session sends to one of your
sessions shows as "From" and that session's name, not as something you typed, and it's
announced. It doesn't matter whether you asked for it with Other Machines, or in your own words
("ask the Mac session what it's working on"). A message that arrives while Claude is working
shows up when it does.

**Good to know:**

- **"Offline" doesn't always mean off.** A session in The Chat Place is offline between turns,
  because each turn is its own Claude Code process. A message to it waits, and it gets the
  message on its next turn, when someone sends it something there. A session in the Claude
  desktop app or a terminal keeps running between turns instead.
- **Other sessions find yours by its name.** A session of The Chat Place's keeps the same name
  every turn, so rename it (F2) to something easy to ask for.
- **You hear only what comes back.** The other computer's conversation can't be read from here;
  open it on claude.ai or in the Claude app for that.
- **A message from another session isn't you.** Claude treats it as information, not as your
  say-so, so it still asks you before anything that needs your permission.

## Updates

The installed app checks for a new version shortly after it starts, and says so only if there is
one. **Help, Check for Updates** asks right away and offers to install it (No is the default). It
won't install while any provider is working in one of its sessions, and it warns you about a reply you
haven't sent. Updating never touches your sessions or settings.

The Help menu's update item always says which version you're running, such as
**Check for Updates (running 0.1.4)**. When a newer version has been found, it says so instead,
such as **Update to 0.1.5 Available**, and choosing it offers the update. Alt+H, U reaches it
either way.

The first time The Chat Place starts after an update, **The Chat Place Update Installed** says
which version you now have. **See what's new** (Alt+W) opens that version's release notes in your
browser; **Close** or Escape goes back to the app. To stop it appearing, clear "Tell me when an
update has been installed, with a link to what's new" in Settings (Ctrl+Comma).

## When something isn't right

**I don't see a session I expected.** Check View, Show Sessions: you may be showing only some
sessions, and archived and hidden sessions aren't in All. A Find (Ctrl+F) also narrows the list;
Escape in the list clears it. F5 refreshes the list at once. Only supported local sessions are
listed, not the desktop app's ordinary chats.

**I can't reply to a session.** Externally started chats are read-only here. Reply in their
originating app or terminal, or use Continue Here (Ctrl+Shift+N) when available. Ctrl+O opens
supported Claude desktop sessions; it is not an Open in Codex command.

**The provider is waiting and nothing is happening.** It may need you: press Ctrl+Shift+A. Ctrl+Shift+T
says what the turn is doing.

**A turn stopped with a usage limit.** The announcement says when the limit resets.
Ctrl+Shift+U shows which limits are used, and how much of each.

**An old session's messages are gone.** Claude Code deletes old conversations after a while (its
`cleanupPeriodDays` setting). The session can still be listed, and the messages list says its
conversation is no longer there.

**A reply is spoken twice.** If you've installed ClaudeSpeak, its hook also speaks The Chat
Place's turns.

**Something else.** Help, Report a Bug asks what happened and shows exactly what the report
includes before it goes anywhere: version numbers and settings, never your sessions' titles,
folders or messages. Open on GitHub files it; Copy Report copies it, to email to
support@theideaplace.net.

## Good to know

- The Chat Place never writes to the desktop app's files or to any conversation. The only files it
  writes are its own: on Windows in `%APPDATA%\TheChatPlace`, on a Mac in
  `~/Library/Application Support/TheChatPlace`. Conversations belong to their provider; The Chat Place keeps its own settings separately.
- With VoiceOver, each line of the messages list reads as it's shown, "You:" or the provider's name and
  the first line, not the whole message, because reading the whole message on each line uses a
  Windows feature. Enter opens the whole message.
- Conversations Claude hands to a subagent aren't shown.
- The desktop app's file formats aren't documented, so a desktop app update could change what The
  Chat Place can read.

For every keyboard shortcut, see Help, Keyboard Shortcuts (F1), or the
[keyboard shortcuts page](https://theideaplace.github.io/AIChat/keyboard-shortcuts.html).
The [README](https://github.com/TheIdeaPlace/AIChat#readme) has the full details of everything here.
