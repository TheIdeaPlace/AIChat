# The Chat Place User Guide

This guide uses Windows key names. On a Mac, use Command (Cmd) for Ctrl,
Option for Alt and Return for Enter. Open menus from the menu bar (VoiceOver:
VO+M), rather than Windows Alt menu sequences.

The Chat Place is a keyboard and screen reader friendly home for your AI chats. Claude, Codex
and GitHub Copilot CLI chats share one list: read, find and export conversations, hear replies,
and send messages in chats started here. **Provider** is which AI a chat uses; **Kind** is where
the chat started, such as the Claude desktop app, a terminal or The Chat Place. More providers,
including Ollama, are planned. Not every feature works for every provider yet: "What works for
each provider" has a table, and each part of this guide says when something is for one provider.

The app's menus and labels say "session", as in File, New Session and the Session List. This
guide says "chat". They mean the same thing: one conversation with an AI.

This guide starts with the everyday things and goes deeper as it goes on. Each part has its own
heading, so your screen reader's heading keys move between them. For every key in one list, use
Help, Keyboard Shortcuts (F1). A glossary at the end explains the terms the guide uses.

The Chat Place is an independent project by Kelly Ford, not made, sponsored or endorsed by
Anthropic or OpenAI. Claude and Claude Code are trademarks of Anthropic; ChatGPT and Codex are
OpenAI products.

## Before you start

You need:

- Windows 10 or 11, a Mac with Apple silicon, or a Linux PC (x64 or arm64, a preview) at least as
  new as Ubuntu 24.04, Debian 13 or Fedora 40, with Orca for speech.
- For Codex chats: a native Codex CLI signed in to ChatGPT. Codex chats are listed once Codex is
  found; Settings, Include Codex chats turns them off or on. Claude Code is not required.
- For GitHub Copilot CLI chats (a preview): the native GitHub Copilot CLI, signed in with
  `copilot login` to a GitHub account that has Copilot, and **Include GitHub Copilot CLI chats**
  turned on in Settings. On Windows, install it with `winget install GitHub.Copilot`; on a Mac,
  with `brew install copilot-cli` or GitHub's install script. An npm install isn't used. Help,
  **Copilot Sign-in** says whether it's installed and signed in.
- For Claude chats: Claude Code, installed with its native installer and signed in to your
  Claude subscription. This is the `claude` command, using the same sign-in as the Claude
  desktop app. The desktop app's own copy of Claude Code isn't meant for other programs, so the
  command is needed even if you use the desktop app. An old npm install of Claude Code isn't
  used.

For Claude chats, install Claude Code by running its installer in a terminal, then run `claude`
once to sign in:

- Mac (Terminal) and Linux: `curl -fsSL https://claude.ai/install.sh | bash`
- Windows (PowerShell): `irm https://claude.ai/install.ps1 | iex`
- Windows (Command Prompt):
  `curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd`

Or let The Chat Place do it: Help, Claude Code Sign-in offers to install Claude Code when it
can't find it, and to sign you in when it isn't signed in.

The Chat Place never uses an API key. Reading chats costs nothing, because everything it reads
is on your own computer. Each message you send, and the work the AI does to answer it, is a
**turn**. Codex turns use your ChatGPT sign-in. In Claude chats started here, each turn is a
Claude Code turn, which counts against your plan's monthly Agent SDK credit; past that credit,
turns stop unless you've turned on extra usage, which is billed. View, Usage and Context
(Ctrl+Shift+U) shows where you stand.

### Installing

**Windows:** download `TheChatPlace-windows-Setup.exe` from the newest release on the
[releases page](https://github.com/TheIdeaPlace/AIChat/releases) and run it. It installs for you
only, needs no administrator rights, adds The Chat Place to the Start menu, and starts it. The
portable zip from the same release runs without installing, but doesn't update itself.

**Mac:** download `TheChatPlace-macos-arm64.dmg`, open it, and drag The Chat Place onto
Applications. The first time it speaks through VoiceOver, macOS asks whether The Chat Place may
control VoiceOver: allow it, and turn on "Allow VoiceOver to be controlled with AppleScript" in
VoiceOver Utility, General.

**Linux** (a preview): download `TheChatPlace-linux-x64.AppImage`, or
`TheChatPlace-linux-arm64.AppImage` for an Arm PC such as the MNT Reform, put it where you keep
programs, make it executable (`chmod +x`) and run it. Its first start adds The Chat Place to your
desktop's menu. It needs FUSE 2 to run (`sudo apt install libfuse2t64` on Debian 13 and Ubuntu
24.04), and Orca and speech-dispatcher for speech. Claude Code's sign-in opens a web page; without
a web browser, open the link the terminal shows on another computer or phone.

If Claude Code isn't signed in, your chats are still listed; only sending a message needs a
sign-in. Help, Claude Code Sign-in says whether it is signed in, and offers to sign you in if not.
The sign-in opens in its own window (Terminal, on a Mac; your terminal, on Linux), and The Chat Place checks again when
you've finished. If it can't tell, it says why, for example that Claude Code is too old to ask:
running `claude update` in a terminal fixes that.

For Codex, choose Help, **Codex Sign-in**. It says whether Codex is signed in to ChatGPT. If it
isn't, or is using another kind of credential, it offers to open `codex login` in its own window.
Your browser shows ChatGPT's sign-in page, and The Chat Place checks again when you've finished.
If Codex isn't installed or its status can't be read, it says what is needed.

For GitHub Copilot CLI, choose Help, **Copilot Sign-in**. It says which GitHub account Copilot
CLI is signed in to. If it isn't signed in, it offers to open `copilot login` in its own window.
Follow its steps, which may give you a code to enter on GitHub's sign-in page, and The Chat Place
checks again when you've finished.
If Copilot CLI isn't installed, it gives the command to install it, and it says so when the only
copy it finds is an npm install, which isn't used. It shows the account Copilot CLI has
recorded; whether that sign-in still works is only known once a Copilot chat sends a message.

## Your first chat

1. Install The Chat Place, and Claude Code or Codex, and sign in, as in "Before you start".
2. Start The Chat Place. You're in the session list, which lists the chats already on your
   computer.
3. Press **Ctrl+N** (File, New Session). If more than one provider is available, choose one in
   **Provider**. Check the **Folder** the chat will work in, type what you'd like done in
   **First message**, and press **Ctrl+Enter** (or Start).
4. The chat is loaded and the AI starts work. When it finishes, its answer is announced.
5. If you hear that the chat needs you, the AI is asking permission or asking a question. Press
   **Ctrl+Shift+A** to read it and answer.
6. To reply, go to the reply box (**Ctrl+3**), type, and press **Ctrl+Enter**.
7. Press **Escape** to go back to the session list, on the same chat. Press **Enter** on any
   chat in the list to load it and read its messages.

## The window

The window has three parts, in Tab order:

1. The **session list**: every chat, one per line.
2. The **messages** of the chat you've loaded.
3. The **reply box**, with Send and Stop. For a chat you reply to somewhere else (the desktop
   app, a terminal, Codex or Copilot CLI), the same place holds a short note saying where you
   reply, followed by the Open in Claude and Continue Here buttons that apply to it (see Kinds
   of chat).

Tab and Shift+Tab move between them, and Ctrl+1, Ctrl+2 and Ctrl+3 go straight to each one. F6
and Shift+F6 move between the parts and the **status bar**; Ctrl+9 goes straight to the status
bar. Escape in the messages, the reply box or the status bar takes you back to the session
list, on the same chat.

If you haven't started any supported chats yet, the session list is empty and the messages say
so: start a chat with File, New Session (Ctrl+N), or in a supported desktop app or a terminal,
and it appears in the list.

### The status bar

The status bar has several parts. **Left Arrow** and **Right Arrow** move between them, **Home**
and **End** go to the first and last, and **Tab** leaves the status bar. Your screen reader's
read-status-bar key reads all the parts at once.

- **What was said last**, or "Ready".
- **The loaded chat** and what it's doing, or "No session loaded".
- Buttons, shown only when there's something to act on:
    - **How full the context is**, such as "Context 62% full": it opens Usage and Context.
    - **How many chats need you**, such as "2 sessions need you": it selects the first one in the
      session list.
    - **An available update**, such as "Update available: 0.3.1": it offers to install it.

### On a Mac

This guide gives the Windows keys. On a Mac:

- Ctrl is the Command key: Command+N for a new chat, Command+Enter to send, and so on.
- Menus are in the menu bar, which VoiceOver reaches with VO+M, rather than with Alt. Where this
  guide says View, Show Sessions, that's the View menu's Show Sessions. Settings is in the app
  menu, The Chat Place.
- A context menu, the Applications key on Windows, is VO+Shift+M.
- The Delete key differs from place to place. In the session list, and on a queued message, use
  the forward Delete key, Fn+Delete. Each place this guide mentions Delete says which Mac key
  works there.

### On Linux

The keys are the Windows ones: Ctrl+N, Alt for the menus (F10 works too), and the Menu key or
Shift+F10 for a context menu.
Orca reads a message's first line and then the rest of it as you arrow through the messages. Orca
before version 50 doesn't say a list's name as you move into it; Orca's Where Am I says it.

## Reading a chat

### Finding it in the list

Each line in the session list reads the chat's title, its folder, its state and when it was
last active, for example: "Fix the release build, QuickMail, needs you: Choose a version number,
active 1 minute ago". To hear the state first, or to leave some parts out, see "Choosing what
each line says" below.

The states are:

- **Needs you:** the chat is waiting for you, for a permission, a question or a plan. Its line
  says what for when it knows. In a Claude chat started here, a turn that failed, or tools that
  were refused, also show as needs you until the next turn starts, and so does an error Codex
  reports in a Codex chat.
- **Working:** the AI is working on a turn.
- **Idle:** the chat is waiting for your next message.
- **Status unavailable:** The Chat Place can't check what a Codex or Copilot chat is doing right
  now, for example a chat that may be open in another app, or a chat whose connection was lost.
  It doesn't mean the work has finished, and nothing is announced when it does. In a Codex chat
  started here, Stop (Ctrl+Period) checks it again. With the default columns, a Codex or Copilot
  chat's line leaves this state out unless there's more to say.

For a Claude desktop app chat, "needs you" means the desktop app's summary of its latest turn
says it's waiting on you. Answer it in the desktop app (Ctrl+O). The desktop app doesn't write
that summary after every turn, so a desktop chat can be waiting on you and still show as idle.

By default, chats that need you come first, then the ones that are working, then the rest,
newest first. The list refreshes itself every few seconds. While you're in it, rows stay where
they are, so nothing moves under you; F5 puts the list back in order.

To find one chat among many, press **Ctrl+F** in the list and type part of its title, its
folder or what it needs. The list shows only the matches, and its name says so ("Session list,
matching "build", 3 of 139"). Escape shows them all again.

### Loading it

Arrowing through the list doesn't load anything, for any provider. Press **Enter** on a chat to
load it: you move to its messages and hear, for example, "Loaded Fix the build, 12 messages."
Pressing Enter on the chat that's already loaded takes you back to where you were in it.

### Reading the messages

The newest message is last, and you start on it. Each line shows "You:" or the provider's name,
such as "Claude:" or "Codex:", and the message's first line. On Windows, your screen reader
reads the **whole message** as you arrow onto it. Headings and list items are read as sentences,
and a code block is read as what it is, such as "Code block, Python, 14 lines". If you'd rather
hear just the first line, turn that off in Settings, Reading messages. On a Mac, VoiceOver reads
each line as it's shown, the first line only; press Enter to read the whole message.

Press **Enter** on a message to read it as a formatted page. There, your screen reader's browse
mode moves by heading (H), table (T), list (L) and code block (each code block is a region: R in
JAWS, D in NVDA). Links open in your browser. **Read as Plain Text** (Alt+P) shows the message in
a plain text box instead, to read by line, word and character. Escape closes either one and puts
you back on the same message. On Windows the formatted page needs Microsoft's Edge WebView2
runtime, which comes with Windows 11; without it, Enter opens the plain text box. On Linux,
Enter opens the plain text box unless you turn on the formatted page in Settings, Reading
messages; it needs WebKitGTK, and Orca 48 moved through its headings but didn't read it line by
line.

On a Mac the page works the same way. VoiceOver's rotor (VO+U) lists its headings, tables, links
and landmarks, VO+Command+H moves by heading, and with Quick Nav on, H, T and L work as above.
Read as Plain Text is Option+P, and Command+W closes the page as well as Escape.

Other things you can do with a message:

- **Ctrl+C** copies the whole message.
- **Ctrl+Shift+B** lists the message's code blocks, by language and size, with each one's code to
  read and copy. **Ctrl+Shift+C** copies the last code block at once.
- **Ctrl+Shift+I** lists code blocks, links, changed files, commands, decisions and tasks, for this
  message or the whole chat, and **Ctrl+L** goes straight to its links: see "Lists" below.
  **Ctrl+F** and **Ctrl+Shift+S** find text in this chat or in all of them: see "Finding
  things" below.
- The Applications key (or Shift+F10) opens the message's menu.

Claude's questions to you appear as "Claude asked: …" followed by "You answered: …". Tools Claude
was refused appear as "Permission denied: …".

### Seeing what the AI is doing

**Tool activity** is what the AI does between messages: the commands it runs, the files it reads
and changes, and what came back. It's hidden until you turn on **Show Tool Activity** (Ctrl+T,
View, Show Tool Activity, or the "Show tool activity" check box below the messages). With it on,
the tool calls appear among the messages, and the open chat's are also spoken as they happen, so
a long turn isn't silent: "Using Bash: git status; Read: main.py." A run of calls is gathered up
and said together ("Using Read 4 times, then Bash."), and it never cuts off your screen reader.

Show Tool Activity belongs to each chat, and it's remembered. Turn it on in one chat to follow
its work, and the others stay as they were; load that chat again, even after closing The Chat
Place, and its tool calls are shown again. You hear which chat it changed: "Tool activity shown
in Fix the build." Continue Here keeps it on in the copy if it was on in the chat you continued.
With no chat loaded, Ctrl+T says so and changes nothing. A chat's tool calls also count in
Links, Export and Find in All Sessions only when it's on for that chat.

### Command results and compaction

A chat's **context** is the part of the conversation the AI has in front of it as it works. It
has a limit, and **compaction** makes room: it replaces the earlier conversation with a summary.
Claude Code does this by itself when the context is nearly full, or when you send `/compact`.

In Claude chats:

- Claude Code's own commands, such as `/compact`, `/context` and `/cost`, show their output as
  **Claude Code** messages, even with Show Tool Activity off. A command that fails shows as a
  **Claude Code error** message.
- After a compaction you hear that it happened, and the messages have a
  **Conversation compacted** row. When Claude Code reports how many tokens the context used
  before and after, the row and the announcement say so, and Usage and Context shows the new
  figure. The announcement waits until the read-back of a message you've just sent has
  finished.
- The summary that replaced the earlier conversation is shown only with Show Tool Activity on.
- Long command output is shortened, with a note saying so, in the app and in exports. Terminal
  colours and formatting codes are taken out. The conversation itself isn't changed.
- When a command's result is announced, you hear a short summary of it, even at the Full
  announcement level. At Silent, it's on the status bar only.

### The task list

On longer work, Claude keeps a list of its steps: what it plans to do, which step it's on, and
which are done. The Chat Place reads that list for any Claude chat, including desktop app and
terminal chats. In Codex chats started here, it keeps the steps Codex reports, so they're still
there when you open the chat again. Codex chats started elsewhere have no task list here. A
plan Codex proposes in the conversation is separate from the task list.

- **Ctrl+Shift+J** (View, Tasks) lists the steps in the provider's order, each with "to do", "in
  progress" or "done". Enter reads a step in full.
- **Ctrl+Shift+T** (Turn Status) and **Properties** say how far along it is: "Task 3 of 7:
  Running the tests. 2 done." Once work has stopped, they say where: "Stopped at task 3 of 7".
  Turn Status leaves out a list that's finished or from an earlier turn.
- When a step is marked done in the loaded chat, you hear it: "Done: Run the tests. 4 of 7."
  That's only at the Full announcement level, and it never cuts off what's being read.

The AI decides when work is worth a list, so short turns usually have none. The list keeps its
last reported states after a turn finishes, fails or is stopped, or a later message has no task
update. In Claude chats started here, Claude keeps a list only because The Chat Place asks it to,
as the desktop app does. **Settings, "Let Claude keep a task list"** (Alt+K) turns that off from
your next message. That setting applies only to Claude.

## Finding things

### In this chat

**Ctrl+F** in the messages (View, Find) finds the next message containing some text. It searches
each message's whole text, not just the first line you see. **F3** and **Shift+F3** find the next
and previous match, going round from the other end when they reach one, and saying so.

### In every chat

When you can't remember which chat you talked about something in, press **Ctrl+Shift+S**
(View, Find in All Sessions) and type a word or phrase. It works from anywhere in the window. The
Chat Place searches the whole text of every message in every chat the session list shows, so
if you've chosen a view in Show Sessions, or narrowed the list with Ctrl+F, only those are
searched. Case doesn't matter. Tool calls and their results are searched only in chats with
Show Tool Activity on.

You hear "Searching 32 sessions for "migration"" and can carry on while it works. A long search
says how far it's got every ten seconds ("Searched 12 of 32 sessions"). Press Ctrl+Shift+S again
to stop it.

When it's done, a list of the messages found opens. Its title says how many and for what first:
"The Chat Place: 6 results for "migration", Find in All Sessions". Each line gives the chat,
who wrote the message, when, and the words around the match, such as "Fix the build, Claude,
yesterday 10:42: …the database migration ran…". Within a chat the newest message comes first.
The list's name, and the Searched box under it (Alt+S), say how many messages were found in how
many chats, how many chats were searched, and how many had no transcript to read (Claude
Code deletes old ones). It stops at 500 messages.

Press **Enter** (or Go to Message) to load that chat on that message: you hear "Loaded Fix
the build. Message 14 of 30: …". If the message isn't shown there, for example because it was a
tool call and Show Tool Activity is off in that chat, you hear so. Escape closes the list
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
- **Changed Files** (Claude, Codex and Copilot chats): each file changed, with how many lines were added
  and removed, and its changes to read line by line (see "Reviewing changed files" below). A deleted file says "deleted". A renamed file
  says "renamed", with the old path in its details; Copy copies the new path. This works for
  desktop app chats and chats started in Codex or Copilot CLI too. Codex lists its completed file
  changes: pending, declined and failed edits aren't counted, nor are changes made through shell
  commands or other tools that don't report file changes.
- **Commands** (Claude chats): each command Claude ran: what it said it was doing, then "done",
  "failed" or "no result yet". Its full text shows the command and its output.
- **Decisions** (Claude chats): each question Claude asked you, with your answer.
- **Tasks** (Claude chats, and Codex chats started here): the task list, each step with "to do",
  "in progress" or "done" (see "The task list" above).

Code Blocks and Links work in every chat. A kind that isn't available for a chat's provider says
so, such as "Commands aren't listed for Codex chats yet."

**Show items from** (Alt+S) chooses **This message** or **The whole chat**. For code blocks and
links, "this message" is the selected message. For files, commands, decisions and tasks, it's the
selected message's turn: from your message to the AI's reply (for tasks, the steps added or
changed in it). Links, commands and decisions list the newest first; code blocks and files are
in the order they appear.

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
with Show Tool Activity on.

## Reviewing changed files

Changed Files lists each file the AI changed in a chat and lets you read each change a line at
a time. It works for Claude, Codex and Copilot chats, including desktop app chats and chats
started in Codex or Copilot CLI. It's built from the chat's own record, so it needs no git.

### Opening it

Press **Ctrl+Shift+D** (on a Mac, Cmd+Shift+D), or choose View, Changed Files. It opens Lists on
Changed Files, for the selected message's turn: everything from your message to the AI's reply.
With no message selected, it shows your latest message's turn. If that turn changed nothing, it
shows the whole chat. **Show items from** (Alt+S) switches between **This message** and
**The whole chat**.

When a turn in the chat you have open finishes and changed files, you hear a summary after the
reply, at the Full announcement level; otherwise it goes to the status bar:

    Fix the greeting: Changed 2 files: greet.py, 2 lines added, 1 removed; NOTES.md, created, 3 lines. Ctrl+Shift+D shows the changes.

### The file list

Each file is one row: its name, how many lines were added and removed, and its folder within the
chat's folder. Files are in the order they were first changed.

    greet.py, 2 lines added, 1 removed, in src
    NOTES.md, created, 3 lines
    old-notes.txt, deleted
    settings.json, no lines changed

- **"created"** is a new file, with how many lines it has.
- **"deleted"** is a file Codex or Copilot deleted.
- **"renamed"** is a file moved to a new path; its details include the old path.
- **"no lines changed"** is a file that was written again with the same text.

**Type** to narrow the list by name. **Ctrl+C** (or Copy) copies the selected file's full path.

### Reading a file's changes

Press **Enter** on a file to read its changes in the **Details** box below the list. Arrow
through it a line at a time.

- The first line is the file's full path and its counts.
- Each change then starts with a blank line and where it is in the file, after the change:
  "At line 5:" for one line, or "Lines 12 to 16:" for several.
  - "A change:" means the place in the file wasn't recorded. That happens with some older
    chats.
  - For a file Copilot deleted, the lines are where they were before.
- Then come the change's lines, each starting with a mark that says whether it was added,
  removed or left as it was. The unchanged lines around a change are there to show where it is.
- An empty line reads "blank line", after its mark, so it isn't silent.
- "Note: No newline at end of file" means the line before it is the last in the file, with no
  line break after it.

### How lines are marked

Settings (Ctrl+Comma), **Mark changed lines with** (Alt+H), chooses how each line starts. Every
style gives one line for each line of the change, with the mark first. Here is the same change in
each: a greeting function whose print line was replaced with two lines.

**Words** (the default): the plainest, and spoken at any punctuation level.

    C:\Projects\app\src\greet.py: 2 lines added, 1 removed.

    Lines 12 to 16:
    Unchanged: def greet(name):
    Removed:     print('Hello ' + name)
    Added:     message = f'Hello, {name}!'
    Added:     print(message)
    Unchanged: blank line
    Unchanged: def main():

**Short words:** the same, with shorter words that are still spoken at any punctuation level.

    Lines 12 to 16:
    Same def greet(name):
    Del     print('Hello ' + name)
    Add     message = f'Hello, {name}!'
    Add     print(message)
    Same blank line
    Same def main():

**Diff symbols:** the marks git and GitHub use, "+" for added and "-" for removed, with "=" for
an unchanged line, where git leaves a space.

    Lines 12 to 16:
    = def greet(name):
    -     print('Hello ' + name)
    +     message = f'Hello, {name}!'
    +     print(message)
    = blank line
    = def main():

**Diff symbols with changed lines:** as Diff symbols, but a removed line followed by the line
that replaced it reads as a pair, "!-" for the old line, then "!+" for the new one. Lines with
no partner keep "+" or "-". Here the old print line pairs with the first new line, and the second
new line is simply added.

    Lines 12 to 16:
    = def greet(name):
    !-     print('Hello ' + name)
    !+     message = f'Hello, {name}!'
    +     print(message)
    = blank line
    = def main():

When several lines are replaced, the pairs come one after another, each old line followed by
its new one: "!- old 1", "!+ new 1", "!- old 2", "!+ new 2". So these lines aren't in the order
the file has them, which they are in every other style.

Symbols are spoken only if your screen reader's punctuation level reads them. At a lower level,
"+", "-", "=" or "!" may be skipped, and a line can sound unchanged when it isn't. Settings says so
when you move the choice onto a symbol style. If you'd like symbols, raise the punctuation level
while you read changes, or use Short words.

A new file is all added lines:

    C:\Projects\app\NOTES.md: created, 3 lines.

    Lines 1 to 3:
    Added: # Notes
    Added: blank line
    Added: First draft.

### Copying changes

Select text in the Details box and copy it as usual; it's marked in the style you chose. Ctrl+C
in the file list copies the file's path instead.

### What isn't listed

- Files the AI changed by running a command (a script, `sed`, or `cat > file`) rather than with
  its own editing tools. For Copilot chats every change is listed, whichever tool made it.
- Changes made by a subagent Claude hands work to, and edits to notebooks.

## Kinds of chat

A chat's **Kind** says where it was started. You read every kind the same way: arrow to it and
press **Enter**. What differs is where you reply.

Only chats started here can be replied to here. The others are read-only. That's deliberate: if
two programs both worked in one chat at the same time, its conversation would get tangled. Where
the reply box would be, a read-only chat has a short note saying where you reply, followed by
the buttons that apply to it. **Ctrl+3** moves to that note.

To list one kind, use View, Show Sessions (Alt+V, H): **Desktop App Sessions** (Code and
Cowork together), **Cowork Sessions**, **Terminal Sessions** or **Chat Place Sessions**. Ctrl+F in
the session list also finds chats by "cowork" or "terminal".

### Desktop app sessions

You can read, search, export and see what changed in the Claude desktop app's chats, but you
reply in the desktop app. **Open in Claude** (Ctrl+O) switches the desktop app to the chat, to
answer it, approve a permission or answer a question card.

### Cowork sessions

The desktop app's **Cowork** chats are listed too, and like its other chats they're read-only
here. Where a chat's folder would be, its line names the folder you gave Cowork to work on, or
says "no folder". **Open in Claude** (Ctrl+O) answers it in the desktop app.

A Cowork chat can't be continued here: Cowork keeps its conversation inside the desktop app's
own files and its tools were run by the desktop app, so Continue Here isn't offered for it (on
the chat's menu it's unavailable). Whether The Chat Place can tell that a Cowork chat is
working, and so announce when it answers, depends on the version of Claude Code the desktop app
runs it with; older versions don't record it. The desktop app's Chat conversations are kept on
Anthropic's servers rather than on your computer, so they aren't listed.

### Terminal sessions

Chats you started by typing `claude` in a terminal are listed too, read-only, and so are
Copilot CLI chats started in a terminal. Codex chats started in a terminal are listed read-only
as well, but their Kind is whatever Codex reports: often "editor session" rather than "terminal
session" (see "The kinds side by side"). Each one's line names the folder you started it in.
A Claude chat's title is the name you gave it with `/rename`, or else the one Claude Code gave it,
or else the start of your first message. The session list shows one as working while the AI
works in it, and as needing you while it waits on you in the terminal (a permission or a
question), and announces both. When it needs you, its line says what for, such as "needs you:
dialog open".

The AI often makes many tool calls in a terminal chat. Their messages are hidden unless you
turn on **Show Tool Activity** (Ctrl+T), which also speaks each one as it happens; press Ctrl+T
again to hide them.

You reply to a terminal chat in its terminal, or carry a Claude or Codex chat on here with
**Continue Here**. The desktop app doesn't list terminal chats, so Open in Claude isn't offered
for them. Chats that scripts run with `claude -p`, and those of Claude's VS Code extension,
aren't listed.

A terminal chat you load while it's working is one you're watching over someone's shoulder.
Claude writes short notes to whoever is at the terminal as it goes, such as "The issue view
printed nothing; trying again with JSON." That's Claude saying what it's doing about a step that
didn't work, not an error from The Chat Place, and Claude usually carries on by itself. The step
it means is hidden unless **Show Tool Activity** (Ctrl+T) is on: with it on, you also hear each
command Claude runs and what came back, so a note like that follows on from what you've just
heard. See Following a chat as it works.

### Chats started here

These are the chats you start here, with File, New Session (Ctrl+N) or Continue Here. Their Kind
is "Chat Place session". You read and reply to them entirely in The Chat Place, and they don't
appear in the desktop app. If Claude starts something in the background, such as a build or a
wait for GitHub checks, the chat keeps running while it does, and Claude carries on by itself
when it finishes (see Replying).

### Session commands in Claude chats

Commands and skills that manage your sessions, such as `/wrap`, work in Claude chats started
here, as they do in the desktop app. Claude can list the sessions in The Chat Place's list
(desktop app sessions, chats started here, terminal sessions and Codex and Copilot chats), look
one up, and search their messages, like Find in All Sessions but across every chat.

Archiving works for chats started here and for terminal sessions: it hides the chat, as
File, **Hide Session** does, so it moves to View, Show Sessions, Hidden, and File,
**Bring Back Session** returns it. A desktop app session is never changed from here: Claude is
told to archive it in the desktop app's sidebar instead. A chat with a turn running, including
the one you're asking from, isn't archived, and neither is the chat you have open, so nothing
you're reading or typing disappears; hide it yourself once you're done. Archiving never moves
you in the list. Claude can't switch the desktop app to another session from here, but it can
give you the session's link.

### Continue Here

To carry on a chat that was started elsewhere, use **Continue Here** (Ctrl+Shift+N, or the
button after the note, where the reply box would be). It makes a new chat here in the same
folder, with the whole conversation so far, and you type its first message. Its title starts as
the original's, followed by "(continued)". The original chat isn't changed, and what you do in
the copy doesn't appear in it. Continue Here works for Claude desktop app and terminal chats, for
Codex chats and for Copilot chats; not for Cowork chats. A Copilot copy is a little different:
Copilot can't copy a chat, so your first message goes with the conversation so far, as text (see
"Starting a Copilot chat").

### Following a chat as it works

You can follow any kind of chat while the AI works in it, wherever it was started. Load it, and
its new messages appear as they're written. Turn on **Show Tool Activity** (Ctrl+T) to hear each
tool call as it happens, so you know what the AI is doing between messages: "Using Bash: git
status; Read: main.py." Without it, the newest message can be a short note the AI wrote before
its next step, such as "Trying again with JSON", followed by a pause while it works: the note is
about a step you didn't hear. When the turn ends, or the chat needs you, it's announced.

### The kinds side by side

Kind is separate from Provider: a terminal chat can be Claude, Codex or Copilot. A Codex chat's
Kind comes from what Codex reports about where it started. Codex often reports chats from its
own app, from an editor and from `codex` in a terminal all the same way, so they show as
"editor session". Only the ones Codex reports as terminal chats are terminal sessions, and only
those are in View, Show Sessions, Terminal Sessions. A chat from another app, or one Codex
doesn't name, is an "other app session" or an "external session".

| Kind | Started in | You reply | Open in Claude | Continue Here |
|---|---|---|---|---|
| **Desktop app session** | The Claude desktop app's Code tab | in the desktop app | yes | yes |
| **Cowork session** | The desktop app's Cowork tab | in the desktop app | yes | no |
| **Terminal session** | A terminal, with `claude` or `copilot`, and some Codex terminal chats | in that terminal | no | yes |
| **Editor session** | Codex in an editor or the Codex app, and often Codex in a terminal | where it was started | no | yes |
| **Command-line session** | Codex run as a command | in Codex | no | yes |
| **Other app session** or **external session** | Another app | in that app | no | Codex and Copilot |
| **Chat Place session** | The Chat Place (File, New Session) | here, in the reply box | no | not needed |
| **Agent conversation** | The Chat Place (File, New Agent Conversation) | here, in the reply box | no | no |

## What works for each provider

Reading, Find, Find in All Sessions, export, Show Tool Activity, Properties and the Code Blocks
and Links lists work for every provider. The rest depends on the provider, and on
whether the chat was started here:

| Feature | Claude | Codex | GitHub Copilot CLI |
|---|---|---|---|
| Start chats here and reply | yes | yes | yes |
| Send Now, edit or remove a queued message | yes | no: Stop returns it | no: Stop returns it |
| Steer a running turn (Ctrl+Alt+Enter) | yes | yes | no: queue it instead |
| Answer requests (Ctrl+Shift+A) | permissions, questions and plans | permissions and questions | permissions |
| Change Model or Change Effort later | yes | no: choose in New Session | Change Model only |
| Change Permission Mode | yes | yes | agent or plan mode |
| Chat Permissions | yes | yes | no |
| Continue Here | desktop app and terminal chats | yes | yes, with the conversation as text |
| Rewind to Here and Branch from Here | yes; files only in chats started here | no | no |
| Commands and skills (Ctrl+/) | yes | no | no |
| Attachments | yes | no | no |
| Changed Files in Lists | yes | yes | yes |
| Commands and Decisions in Lists | yes | no | no |
| Tasks in Lists | yes | chats started here | no |
| Usage and Context | yes | yes | context and premium requests used |
| Delete Session Permanently | yes | no | no |
| Copy Session Link | yes | no | no |
| Remote Control and Other Machines | yes | no | no |
| Agent conversations | yes | yes | chats started here |
| What AI Knows About You | yes | yes | no |

The parts of this guide that describe each feature say the same.

## Codex chats

### Seeing Codex chats

Codex chats are listed once The Chat Place finds a native Codex CLI: the Codex app's copy on
Windows, the copy bundled with the ChatGPT app on a Mac, or the copy Codex's own installer keeps
in your `.codex` folder. Existing chats are listed even when Codex isn't signed in; starting
chats, sending and usage need the sign-in. Settings offers
**Include Codex chats (restart required)** to turn them off or on, and your choice is kept. If
Codex isn't found, that option says to install the Codex app or CLI, then restart. Turning the
option off and restarting hides Codex chats without deleting anything.

Press **Enter** on a Codex chat to load its history. Reading, formatted reading, Find and export
work as for any chat. Codex chats started elsewhere are read-only here: reply in Codex.

### Starting one and replying

In New Session (Ctrl+N), choose **Codex** as the Provider (see "A new chat"). You can then send,
queue and stop as in any chat started here, and answer its permissions and questions with
Ctrl+Shift+A. **Continue Here** copies a Codex chat started elsewhere into a new chat here.

- Choose the model and effort when you start the chat; Continue Here offers the same choices as
  New Session. Change Model and Change Effort can't change them later yet.
- **Turn Status** (Ctrl+Shift+T) says how long Codex has been working and what it did last.
- Your messages are read back as in Claude chats, following the same setting.
- Loading another chat and coming back doesn't stop a turn that's running.
- Quitting The Chat Place asks Codex to stop the turns it's running in chats started here.
- If a new Codex chat can't be started, your first message is kept: in the open chat's message
  box if you can type there, otherwise on the clipboard.
- If Codex asks for something The Chat Place can't show you safely, it's refused for you, and you
  hear why. The chat stays connected, and Turn Status keeps the notice to read again.

A Continue Here copy of a Codex chat starts read-only. You can choose folder editing instead, for
a project folder: your home folder, the folders above it and the top of a drive can't be made
editable.

### If the connection is lost

If a Codex chat started here loses its connection, or you can't tell whether a message was sent:

1. Press **Stop** (Ctrl+Period). The Chat Place checks the turn Codex saved. Nothing is resent.
2. Once Codex shows that turn has ended, The Chat Place reconnects and says so.
3. If Codex still reports the turn as unfinished, you hear why. Press Stop again later.
4. Read the chat's history before sending a message again, so you don't send it twice.

Loading the chat again doesn't reconnect it; Stop does. The Chat Place won't carry on a chat
whose last turn didn't finish, or that Codex can't say finished, until Codex shows it has ended.

### What a Codex chat may do

How a Codex chat asks before it acts, and automatic approval review, are in "Permissions and
questions". These limits apply to every Codex chat started here:

- **No external tools.** MCP servers, apps and plugins are turned off for these chats, because
  they can reach beyond the chat's folder. The Chat Place checks that they're off before it
  sends. Your Codex settings aren't changed, and other Codex chats can still be read.
- **Your saved command rules still apply.** These are the commands you've told Codex to always
  allow, ask about or never allow, in files such as `~/.codex/rules/default.rules` and in rules
  installed for your whole computer. A command you've told Codex to always allow runs without
  asking and outside the folder's limits: it can change files elsewhere and use the network.
  New Session and Change Permission Mode say so. Properties says how many rule files are in
  effect, and Chat Permissions (Ctrl+Shift+H) lists their commands.
- **No project rules.** The Chat Place won't start or send in a folder that has its own rules
  (`.codex/rules` in the folder or a project folder above it): choose another folder, or move
  those rules out, and try again. It also won't start or send if it can't read your saved rules.
  Existing chats can still be read.

The Chat Place never removes or edits rules, never changes your Codex settings, and never adds a
rule when you answer a request.

### Setting up the Windows sandbox

On Windows, Codex can edit a folder only after its Windows sandbox is set up. The simplest way
is Set up Agent sandbox in the Codex app. With only the command-line tool, add `[windows]` and
`sandbox = "unelevated"` to `~/.codex/config.toml`: that is the setting checked with The Chat
Place, and OpenAI's fallback for when administrator setup isn't possible. OpenAI's
[Windows sandbox guide](https://developers.openai.com/codex/windows) describes its preferred
choices, MXC on compatible PCs and `"elevated"` (which needs setup approved by an
administrator); they haven't been tried with The Chat Place yet.

Until the sandbox is set up, Codex reports chats as read-only, and The Chat Place says so
instead of giving the chat folder editing; choose read-only, or set up the sandbox and try
again. Codex also reports read-only when your Codex settings allow only read-only, such as
requirements an administrator set.

## GitHub Copilot CLI chats (preview)

### Turning them on

Turn on **Include GitHub Copilot CLI chats (preview; restart required)** in Settings (Alt+I),
save and restart. The option appears once Copilot CLI is installed or has saved a session on
this computer: Settings looks for the Copilot CLI program, and checks the names of the folders in
Copilot's session folder and whether each has a conversation log, never what's in them. With the
option off, nothing else of Copilot's is read. Turn the option off and restart to hide Copilot
chats again.

Your Copilot CLI chats are then listed with your other chats, and View, Show Sessions, GitHub
Copilot Chats lists only them. The Chat Place reads the chats Copilot CLI keeps on this
computer; reading never starts Copilot, so it uses none of your Copilot requests. Press Enter on
a Copilot chat to load its messages; formatted reading, Find and export work too. Show Tool
Activity also shows the commands and file changes Copilot ran, and a change you refused shows as
Permission denied, with what was refused.

Chats started in Copilot CLI, or in another app, are read-only here: reply in Copilot CLI.
A Copilot chat that's answering says working, even while Copilot runs a command between steps,
and you hear it once when its answer is finished. If Copilot hasn't written anything for 10
minutes (a long command, or Copilot closed partway through), it still says working and adds "no
activity from Copilot for 10 minutes"; after 2 hours it says status unavailable instead. A chat
waiting for your permission stays needs you for up to a day; after that it says status
unavailable, "waiting in Copilot for over a day", since Copilot may have been closed while
asking. Chats started in Copilot CLI in a terminal are listed as terminal sessions, so View,
Show Sessions, Terminal Sessions includes them; ones started from another app aren't.

### Starting a Copilot chat

In New Session (Ctrl+N), choose **Copilot** as the Provider, then a folder and your first
message. **Model** lists the models Copilot offered your last Copilot chat here, each with the
premium requests a message costs, such as "GPT-5 (1x)". Copilot lists its models only for a
chat, so before your first Copilot chat here only its configured model is offered. Choose one, and
the chat switches to it before your first message goes; if Copilot won't, the chat says so and
uses Copilot's own. Effort and Copilot's other settings are its own. Each message uses requests
from your Copilot plan. Type and send as in any chat started here. Messages you send while Copilot is
answering wait their turn, and Stop (Ctrl+Period) cancels the answer and puts any waiting
messages back in the message box, unsent.

Copilot asks before it runs a command or changes a file. You hear that the chat needs you, and
Answer (Ctrl+Shift+A) shows the whole command or change, with **Allow Once** and **Deny**. Deny
is the default button, and nothing is ever allowed for later. A command or change too long to
show in full can only be denied: ask Copilot to do it in smaller steps.

The Chat Place won't send to Copilot:

- in a folder whose Copilot hooks could act without asking you. That's any hook in the project,
  or one of your own or your organisation's that can approve a request. The message names the
  file; you can still use Copilot CLI there.
- if it can't confirm an answer was billed to GitHub Copilot.
- after the chat was used in Copilot CLI in a terminal since you opened it here. Open it again
  first.
- with a message that starts with `/`, because some of Copilot's commands turn off its
  questions.

**Plan mode.** A Copilot chat started here is in Copilot's agent mode or its plan mode. In plan
mode Copilot works out a plan and doesn't edit your files (it keeps the plan in its own session
folder); it still asks before anything that needs your permission. Choose the mode in New Session or Continue Here (Permission mode),
or switch a chat with **Change Permission Mode** (Ctrl+Shift+O), as Shift+Tab does in Copilot
CLI. The chat's heading ends "plan mode" while it's in plan mode, and if Copilot switches modes
by itself you hear it. If Copilot can't start a new chat in plan mode, or reopens a chat in
another mode than the one you chose and won't switch it back, nothing is sent until you choose a
mode with Change Permission Mode; either choice, the one it's in now too, then lets it send. A
chat started for an agent conversation starts in agent mode. When Copilot says to switch out of
plan mode to make the change, that's Change Permission Mode, then Agent. Copilot's autopilot mode, which stops
it asking, is never offered, and a chat Copilot puts in it isn't sent to.

A Copilot chat started in Copilot CLI or another app can be carried on here with
**Continue Here** (Ctrl+Shift+N). Copilot can't copy a chat, so the copy is a new Copilot chat of The Chat
Place's own in the same folder, and your first message goes with the conversation so far: your
messages and Copilot's replies, labelled as history (the newest kept if it's long), but not the
commands Copilot ran or their output. It goes as part of your first message, so it costs no extra
request. If that message doesn't go, the history waits for your next one, but only while The Chat
Place stays open: the chat says so. A first message too long to go with the history is
refused and kept, so you can shorten it. In the copy's messages the history is one row,
"Earlier conversation", just before your first message; Enter reads it in full. Model
and Permission mode offer the same choices as in New Session. The original chat isn't changed.

Chat Permissions isn't available for Copilot chats yet. A Copilot chat started here can be in an agent conversation, and Send Reply to Another
Chat can send a reply into it.

## Starting a chat and replying

### A new chat

Press **Ctrl+N** (File, New Session). It works from anywhere in the window. Then fill in:

- **Provider:** Claude, Codex or Copilot, when more than one is available. Your last choice is
  remembered. The fields below change to suit it.
- **Folder:** the project folder the chat works in. It starts at the folder you used most
  recently. The list holds the folders your chats have used, The Chat Place's and the desktop
  app's, newest first: on Windows, Up and Down Arrow move through it, and Alt+Down Arrow opens
  it. Delete on a folder chosen from the list takes it off the list (on a Mac, Fn+Delete); it
  comes back once a chat works in it again. You can also type a folder, or choose one with
  **Browse** (Alt+B).
- **From GitHub** (Alt+G) lists your GitHub repositories, and you hear how many. Type to narrow
  the list, or type owner/name (or a GitHub address) for that repository, even one that isn't
  yours, then press Enter. If the repository is already in your GitHub folder, that folder is
  used; otherwise it's cloned there first, and you hear "Cloning" and then "Cloned". Cancel
  stops a clone and leaves nothing behind. This uses the GitHub CLI (gh), signed in with gh auth
  login; if it can't list your repositories, the list says why.
- **Work in** (Alt+I): **The folder as it is** (it says which branch the folder is on), or
  **A new worktree**. A **worktree** is a separate copy of the repository on a branch of its
  own, so the chat's changes stay apart from the folder and from your other chats. With a
  worktree, choose a branch in **Branch** (Alt+R), or type a new name to make that branch from
  the folder's current one. A branch that's already checked out somewhere isn't offered, since a
  branch can be in only one place. When you press Start, you hear "Making the worktree", and
  the chat starts once it's made, inside the repository's .claude folder where Claude Code
  and the desktop app make theirs. It stays there when you're done; remove it with git worktree
  remove.
- **Title:** optional; without one, it's made from the first words of your message.
- **Model:**
    - Claude: Default (your Claude Code setting), Opus, Sonnet or Haiku, each the latest of its
      kind. File, Change Model changes it later, from the next turn. Fable isn't offered, because
      on some plans it bills to usage credits without asking; if a turn would run on Fable anyway,
      The Chat Place stops it before anything is sent, and says why.
    - Codex: Configured Codex model (your Codex settings), or one of the models Codex offers. The
      list loads in the background; if it can't be loaded, the configured model is still there.
    - Copilot: Copilot's configured model, which can't be changed here.
- **Effort:** how hard the AI thinks.
    - Claude: Default (your Claude Code setting), Low, Medium, High, Extra high or Max. Models that
      don't support effort levels ignore it. File, Change Effort changes it later, from the next
      turn.
    - Codex: Configured Codex effort, or one of the efforts the chosen model supports.
    - Copilot: Copilot's own setting.
- **Permission mode:** how much the AI may do without asking.
    - Claude: **Auto**, the default, lets Claude decide what's safe to run. **Accept edits** lets
      it change files without asking. **Manual** asks before anything that needs approval.
      **Plan** has Claude make a plan, without changing anything, for you to approve.
    - Codex: read-only, or able to edit files in this folder (the default for a new chat), each
      with you reviewing requests or with **automatic approval review** (see "Permissions and
      questions").
    - Copilot: always asks before running commands or changing files.
- **About permissions:** a note on what the choices mean for the chosen provider.
- **First message:** what you'd like the AI to do.

Press **Ctrl+Enter** from anywhere in the dialog (or Start). For Codex, the model and effort
are checked again as the chat starts. If the first message never reaches Claude (Claude Code
isn't signed in, for example), it goes back into the reply box, and Send starts the chat again.

### Changing a chat's model, effort or permission mode

For a Claude chat started here, these File menu items change what its next turns use. Each opens
a list that starts on the current choice, marked "(now)"; choose another and press Enter, or
Escape to leave it as it is.

- **Change Model** (Alt+F, D): Default, Opus, Sonnet or Haiku.
- **Change Effort** (Alt+F, F): Default, Low, Medium, High, Extra high or Max. Models that don't
  support effort levels ignore it.
- **Change Permission Mode** (Ctrl+Shift+O): auto, accept edits, manual or plan.

You hear the change and when it counts, such as "Permission mode for Fix the build: plan, from its
next turn." In a Claude chat, a turn that's already running carries on as it started, and you hear
that too. The chat's heading, at the top of its messages, says its model, and its effort and mode
when they aren't the defaults: "on Opus at high effort, plan mode". Approving a plan, or allowing something
"for this chat" that switches mode, changes the mode as well; approving a plan starts on the
mode you chose here, unless that was plan.

In a Copilot chat started here, **Change Model** lists the models Copilot offers that chat, with
the premium requests a message costs, and you hear the change, such as "Fix the build now uses GPT
Mini (0.33x), from its next turn." The chat must be open in Copilot first: select it and wait
until it's ready. Once it is, the chat's heading says the model Copilot reports for it, such as
"on GPT Mini (0.33x)". Change Effort and Change Permission Mode don't apply: effort is Copilot's
own, and Copilot always asks before it acts.

For a Codex chat started here, **Change Permission Mode** switches between read-only and editing
files in its folder, with you or automatic approval review checking requests. Finish or stop the
turn first. The chat keeps its history, and the new mode applies from its next message, even
before its first. Until the new mode has been checked with Codex and saved, the old one stays in
effect. If that fails, sending stays off: press **Stop** to check the saved permissions again, or
try Change Permission Mode again. Nothing is sent while it checks. Change Model and Change Effort
aren't available for Codex chats yet.

Desktop app, terminal and Copilot chats are set elsewhere, so for them these items say so and
change nothing.

### Replying

In a chat started here, type in the reply box and press **Ctrl+Enter** (or Send). You stay in
the reply box, and you hear what was sent and where, such as "Sent to Fix the build: Run the
tests." When the AI finishes, its answer is announced.

While the AI is working, you can still send. Your message is **queued** ("Queued for Fix the
build: …"), shown at the end of the messages as "Queued: …", and sent once the current turn
finishes. **Stop** (Ctrl+Period) ends the turn, and anything it started, such as a build; queued
messages then go back into the reply box, to change and send again. What else you can do with a
queued message depends on the provider:

- **Claude:** on a queued message, Ctrl+Enter sends it now, and Claude answers it straight away,
  in the same turn. Its menu can also edit or remove it, and Delete removes it (on a Mac,
  Fn+Delete).
- **Codex and Copilot:** queued messages go after the reply. They can't be sent early, edited or
  removed while they wait: Stop the turn to get them back.

To change what the AI is doing without stopping it, **steer** instead: type your message and
press **Ctrl+Alt+Enter** (Cmd+Option+Return on a Mac), or choose File, Steer: Send into the
Running Turn. Your message goes into the turn that's running, and the AI takes it in at its next
step and carries on with it in mind, such as "also update the README" or "use the other test
file". You hear "Steering Fix the build: …", then "Fix the build has your message" once Claude
takes it in, or "Steered Fix the build: …" once Codex accepts it. Ctrl+Alt+Enter on a queued
message steers that one instead of waiting. With no turn running, Ctrl+Alt+Enter just sends.
Until the chat's own history shows it, a steered message is at the end of the messages list as
"Steering, waiting for Claude: …", and the row says where it is as that changes, such as
"taken in by Claude" or "accepted by Codex".

- If Claude had already finished its reply, you hear that reply, then that it's answering your
  message now.
- If the turn ends or you press Stop before the message is taken in, it comes back to the reply
  box (a queued one stays queued, or comes back with the rest when you press Stop). It's never
  lost or sent twice.
- If Codex doesn't confirm a steered message (an error, or the connection is lost), it comes back
  to the reply box, even a queued one, with a warning to check the chat's history first, since
  Codex may have it. It's never sent again on its own.
- Copilot chats can't steer yet: Ctrl+Enter queues the message.

Sometimes Claude starts a command that runs in the background, such as a build or a wait for
GitHub checks, and answers while it runs. You hear the answer and what is still
running ("Still running in the background: Windows build"), but the turn stays open. When the work
finishes, Claude carries on by itself, and its next answer is announced as usual. While Claude is
waiting, what you send goes to it at once instead of being queued. Stop ends the turn and the
background work with it.

**Ctrl+Shift+T** (Turn Status) says how long the AI has been working on this turn and what it last
did, or what is still running in the background, and how far along the task list is. There's
no time limit on a turn.

### Commands, skills and attachments

These are for Claude chats started here:

- **Ctrl+/** (or the Commands button) lists your skills and slash commands, then Claude Code's
  own, such as /compact and /context, each with its description. Type to filter, Down to the list,
  Enter to choose: it goes at the start of the reply box, ready to finish and send. In a Codex or
  Copilot chat it says the picker isn't available yet.
- **Ctrl+Shift+F** (or Attach Files) adds files and pictures to your next message, and **Ctrl+V**
  in the reply box attaches a picture on the clipboard, such as a screenshot. The attachments are
  listed under the reply box, and Delete there removes one (on a Mac, Delete or Fn+Delete).
  Claude sees pictures as pictures, and reads other files from where they are.

### Saved prompts

Keep messages you send often as prompts, each with a name, and put one in the reply box when you
need it:

- **Ctrl+Shift+P** (File, Prompts) lists your prompts by name, with the selected one's text below
  it (Alt+X to read it). **Enter**, or Use, puts the prompt in the reply box of the loaded chat,
  at the cursor, ready to change and send.
- In the same dialog, **New** and **Edit** ask for a name and the text (Enter starts a new line in
  the text, and Ctrl+Enter saves). **Delete** asks first; so does the Delete key on a prompt (on
  a Mac, Fn+Delete). **Move Up** and **Move Down** change the order. Each change is saved straight
  away, and you hear what happened, such as "Prompt saved: Review". Escape closes.
- **Ctrl+Shift+Enter** in the reply box saves what you've typed as a prompt and sends it. It asks
  for the prompt's name first, starting with the message's first few words. Press Escape there
  and nothing is saved or sent: your message stays in the reply box.

### Going back: Rewind to Here and Branch from Here

In a Claude chat, you can go back to an earlier message and try again from there. The chat you
went back from is always kept as it was, in the list.

- **Rewind to Here** (Ctrl+Shift+Z, File, Rewind to Here, or Rewind to Here on a message's menu)
  works on one of your own messages: select it in the messages list first. It asks what to take
  back to how it was just before that message:
  - **Conversation and files** (chosen to begin with when the files can go back),
  - **Conversation only**,
  - **Files only**.

  **What happens** (Alt+H) says what the choice will do, naming the files Claude changed since
  that message. Press Rewind (Enter) to go ahead, or Escape to change nothing.

  Going back in the conversation makes a new chat of your own, "<title> (rewound)", in the
  same folder. Its conversation is the original's up to just before your message, and your
  message is in its reply box to change and send. Until you send it, the messages list says
  the chat is a branch waiting for its first message. It starts on the original's model,
  effort and permission mode, with nothing allowed for the chat yet. This works for any Claude
  chat on this computer, including desktop app and terminal sessions, since the original isn't
  changed.

  Going back in the files puts back the files Claude changed with its edit tools since your
  message; you hear "Files in Fixes restored to how they were before your message …: 2 files",
  or why they couldn't be. Changes made by commands Claude ran (a build, a script) aren't put
  back. It works only in Claude chats started here, and only for messages sent with this
  version or later, which keep the copies it needs; for other chats and older messages the
  choice says "(unavailable)", and What Happens says why. It isn't available while Claude is
  working in the chat, and the chat waits for it to finish before it sends anything.
- **Branch from Here** (File, Branch from Here, or on a message's menu) works on any of your
  messages or Claude's replies: a new chat, "<title> (branch)", whose conversation ends with
  that message, opened with an empty reply box, to try another way from there. It changes no
  files.

Codex and Copilot chats don't have these yet.

### Sending a reply to another chat

Two agents can work on the same thing, with you passing their words between them: Claude builds
and Codex reviews, say, or you ask both and let each answer the other. **Ctrl+Shift+E** (File,
Send Reply to Another Chat, also on a reply's menu in the messages list) does the copying and
pasting.

1. In the chat with the reply, select it in the messages list. If a message of yours is
   selected instead, the latest reply is used.
2. Press **Ctrl+Shift+E**. **Send to** lists your Claude, Codex and Copilot chats started here,
   other than this one. Desktop app, terminal and hidden chats aren't offered. At the end of the
   list are **New Claude chat**, **New Codex chat** and **New Copilot chat** (each when that
   provider can start one), to start a new chat with the reply.
3. Below it, **Message** has the reply, starting with a line that says which agent wrote it and
   in which chat, so the agent reading it knows these are another agent's words, not yours.
   Change it or add your own words; Enter there starts a new line.
4. Press **Ctrl+Enter** (or **Enter** in the list, or Send). The Chat Place goes to that chat and
   sends it, just as if you'd typed it there: you hear what was sent and where, as when you send,
   and it's queued if that chat is working. A Codex or Copilot chat that isn't ready yet gets
   ready first ("Getting Reviewer ready"), and the message goes when it is; if you go to another chat before
   then, it isn't sent and stays in that chat's reply box.

To a new chat, New Session opens next, with the message as the first message, the folder of
the chat the reply came from, and the title "Forwarded from" and that chat's title. Choose the
permission and model, then press Start.

**Just send it** (Alt+J) is chosen under Send to. Choose
**Start an agent conversation with both chats** (Alt+C) instead to make the two chats an agent
conversation (see the next section), and give it a title in **Agent conversation title** (Alt+I).
The reply goes as its first hand-off and the chat you sent it to answers first; you approve each
hand-off. When it can't be done, because a chat wasn't started in The Chat Place, is already in
an agent conversation, or is still working, the option says "unavailable" and why,
and Send says it in full.

When the answer comes, press **Ctrl+Shift+E** again: the chat the reply came from is first in the
list, so **Enter** sends the answer back. If a message can't be sent, it's left in that chat's
reply box, so nothing is lost. If the dialog's title says the chat is still working, the reply
you're sending may be a step on the way rather than its answer.

A forwarded message is only ever a message. It can't answer a question or approve a tool for the
other agent: those still come to you, in each chat, as they always do.

### Agent conversations

An **agent conversation** puts two chats, any of Claude, Codex and Copilot or two of one, in one
conversation with you, and passes each one's replies to the other. A third chat can join, and then replies
go round in turn: the first agent's to the second, the second's to the third, the third's back
to the first, and each agent is sent the replies it missed along with the one passed to it.
It's Send Reply to Another Chat done for you, turn after turn.

**Starting one:** press **Ctrl+Shift+G** (File, New Agent Conversation).

- **Kind of conversation:** "Your own roles", or a ready-made kind that fills in the roles,
  new chats' permissions and who goes first, which you can still change:
    - **Builder and reviewer:** one builds; the other reviews each change without editing, and
      the builder fixes what it finds. A new reviewer chat starts read-only: Claude in Plan
      mode, Codex asking before any change.
    - **Second opinion:** one answers; the other gives its own answer and says where it agrees
      or differs. Both start read-only.
    - **Debate:** each argues for a different approach and answers the other's points; you
      decide.
    - **Plan and do:** one makes a plan without changing anything; the other carries it out.
- **Title:** the conversation's name. Left empty, it's the opening message's first line.
- **First agent's chat** and **Second agent's chat:** a new Claude, Codex or Copilot chat, or
  one of your chats started here, which keeps its history. New Copilot chats are offered once
  the Copilot preview is on in Settings and Copilot CLI is installed. A chat can be in one conversation at a time.
- **Third agent's chat** (optional): "No third agent" unless you choose one.
- **Role** (optional) for each, such as "Build the fix" and "Review each change; don't edit
  files". Each agent is told its role and the others'.
- **New chat's permission:** for a new chat, its permission mode, as in New Session.
  Codex offers four choices: read-only or edits in its folder, each with human or automatic
  approval review. Templates keep human review unless you explicitly choose automatic review.
  Copilot has one, "Asks you before it changes files or runs commands": it asks you about each
  edit and command, whatever the template, so a reviewer told not to edit files is trusting
  Copilot to keep to its role, and you to refuse if it asks.
  **Folder for new chats** is where they work.
- **Hand-offs:** approve each one, or automatic (offered once it's on in Settings), with a
  **Turn limit**.
- **Who goes first:** any one agent, or all of them (Both, or Everyone with three).
- **Opening message**, then **Ctrl+Enter** (or Start).

The conversation is one row in the session list, with Provider "Claude + Codex" and Kind "agent
conversation". Its chats leave the list while they're in it;
**File, Agent Conversation Members** goes to any of them.

**Reading it:** the messages list has every agent's messages in order. Each starts with who said
it: "Claude: …", "Codex: …", "You, to Codex: …". The relay's own lines start with "Relay", such as
"Relay: Claude's reply passed to Codex." If you changed a reply before handing it on, that line
adds "with your changes". Each is one line in the list, and Enter reads exactly what was sent:
the setup each agent got, word for word, or the reply as it went. When you type with a reply
waiting, your words follow that line as your own message.

**Files that changed:** when an agent edits files in its turn, the reply that's passed on ends
with a list of them, "Files Claude changed in that turn", so a reviewer knows what to look at.
For Copilot, the list is read from Copilot's own log when its turn ends.
The list is part of what was sent, so Enter on the relay line reads it.

**Writing in it:** what you type goes to the agent whose turn it is (Turn Status says which).
Start with @ and a name to choose: "@Codex check the tests", or with the name and a comma, as
you'd write to someone: "Codex, what's broken?". "@all" goes to every agent (their replies
are then shown but not passed on). If a reply is waiting to go to the agent you write to, it goes
along with your words, ahead of them, instead of being dropped; "@all" takes it along too. If you
write only to the agent that sent it, you've carried on with that agent instead, so the waiting
reply is set aside, and you hear so.

**Hand-offs:**

- **Approve each** (the default): when an agent answers, you hear its reply, and it waits to go
  to the other agent; the row says so. Most of the time you just type your next message, as in
  any chat: it goes to the other agent, with the waiting reply ahead of your words.
  **Ctrl+Shift+E** is there when you want to pass the reply on without adding anything, or read
  and change it first: it shows the reply, labelled with who wrote it, and Ctrl+Enter sends it.
- **Automatic:** turn on "Let agent conversations hand replies across automatically" in
  Settings, then choose Automatic in New Agent Conversation, or for a conversation with
  **File, Agent Conversation Hand-offs**, which can also make it say only when it stops or needs
  you. If Claude or Codex is near a usage limit, you're asked first: every exchange uses both
  subscriptions. Copilot doesn't say how much of your plan is left, so it can't be checked:
  each of a Copilot agent's turns uses premium requests. Replies go across by themselves until the turn limit (6 exchanges unless you
  choose another). Approving what an agent asks, such as running its tests, doesn't stop it.
  It pauses when you refused something during a turn, or a request went unanswered: you hear
  why, and the last reply waits for Ctrl+Shift+E. It stops when a turn fails, and you hear
  why; your next message goes back to that agent. A message from you, or a reply you hand on,
  starts it again with a fresh count.

In either mode, the relay finishes when an agent ends its reply with DONE once the others have
replied since your last message, when the replies start repeating each other, or when the
agents are only thanking each other. A DONE before the others have had their say goes on to
them like any reply. You hear that,
and the last reply still waits for **Ctrl+Shift+E**, because a reviewer's DONE usually comes
with its findings, which the other agent needs. The row doesn't say it needs you for it.
Anything you write to the agent it waits for takes it along.

An agent that answers and leaves work running in the background, such as a build, is handed on
when it answers, not when the work finishes.

**Stopping:** **Ctrl+Period** stops every agent and the relay. A reply that was waiting to be
handed on isn't passed on, a message that hadn't gone yet comes back to the reply box, and your
next message goes to the agent you stopped.
**Ctrl+Shift+T** says what each agent is doing, how far the count has got, and who your next
message goes to. **Ctrl+Shift+U** lists each agent's context and each provider's limits, and,
for a Copilot agent, the premium requests its chat has used.

**Safety:** questions and requests needing human approval come to you through **Ctrl+Shift+A**.
Codex uses the approval reviewer selected for its chat; Copilot always asks you. A message from one agent goes to the
other only as a message, labelled as another agent's words; it can never answer a question or
approve anything. Each agent is told to take instructions only from you. Every message uses
that agent's own subscription, so
automatic mode spends both.

**Rename** (F2), **Properties** (Alt+Enter) and **Delete** (Shift+Delete) work on the
conversation's row. Deleting it keeps both chats, with everything said in them, and puts them
back in the list. A chat can't be deleted while it's in a conversation: delete the conversation
first.

## Permissions and questions

In a chat started here, the AI may need your permission before it acts, ask you a question, or
(Claude) have a plan for you to approve. Chats started elsewhere are answered where they run.

### Answer a request

1. You hear that the chat needs you, for example "Fix the build needs you. Claude wants to run
   git push. Ctrl+Shift+A answers." The chat shows as "needs you" in the list.
2. Press **Ctrl+Shift+A** (File, Answer). It answers the loaded chat first, then any other chat
   that's waiting.
3. Read the whole request: the command, or the file and what would change.
4. Choose an answer. The safe answer is the default button, so pressing Enter by mistake never
   allows anything.

**Escape answers later.** Nothing is allowed or refused by waiting: the turn keeps waiting, and
Ctrl+Shift+A brings the same request back.

What each provider offers:

- **Claude, a permission:** **Allow** (this request only), **Allow for This Chat** (keep a rule so
  it doesn't ask again in this chat; shown when Claude Code suggests one, and named in full) or
  **Deny**, the default, with a reason Claude reads if you like.
- **Claude, a question:** it opens on the Question box, to read by line: what Claude wrote just
  before asking (often the thing it's asking about, such as a draft to approve), then each
  question in full. Below it, each question is a group of choices, with Other and a box for your
  own answer. When a choice comes with a preview, the Preview box after the questions shows the
  preview of the choice you're on. Send Answers, or Don't Answer. A question with one answer is a
  single Tab stop, on your choice, or on its first choice until you've made one (the arrow keys
  move between choices); in a question with several answers, each choice is a stop.
- **Claude, a plan:** read it (Read Formatted shows it as a page, by heading), then **Approve**,
  choosing how Claude carries on, or **Keep Planning**, the default, with what to change.
- **Codex, a permission:** the request shows the command, its folder and Codex's reason, and a
  "This also needs" list when Codex names extra access, such as writing to another folder or
  using the network. When it names none, the dialog says the command may run outside the chat's
  folder sandbox, where it can change any file you can. It offers the answers Codex allows for
  that request: **Allow Once**, **Deny** (Codex carries on without it), **Deny and Stop Turn**,
  and always **Answer Later**. Enter chooses Deny, or Answer Later when Deny isn't offered.
  Codex's own "for the rest of the session" and "from now on" choices are never offered: the
  dialog says when Codex offered one, and your answer covers this request only. When Codex asks
  for write access to a whole folder for the rest of the session, Allow isn't offered, since The
  Chat Place can't grant that; deny it, or deny and stop the turn. For some Git commands,
  **Allow for This Chat** remembers your approval for this chat (see "For a Codex chat"
  below).
- **Codex, a question:** the question shows which provider is asking, and a secret answer stays
  hidden as you type it. The Question box starts with what Codex wrote in this turn before
  asking, as for Claude. Copilot's questions work the same way.
- **Copilot:** the whole command or change, with **Allow Once** and **Deny**, the default.
  Nothing is allowed for later.

### Review or change permissions

- **File, Chat Permissions** (Ctrl+Shift+H) shows what a Claude or Codex chat started here may do
  without asking, working or idle, and lets you remove what you allowed for that chat.
- **File, Change Permission Mode** (Ctrl+Shift+O), or **Change** in Chat Permissions, changes a
  chat's permission mode (see "Changing a chat's model, effort or permission mode").

Desktop app, terminal and Copilot chats have no Chat Permissions here. The Chat Place never
changes Claude Code's or Codex's own settings files.

### For a Claude chat

Several things together decide whether a Claude chat asks you before it acts:

1. **Your answer to one request.**

    - **Allow** lets this one request run. Nothing is kept; the same request asks again next time.
    - **Allow for This Chat** lets it run and keeps a rule with this chat, so later requests the
      rule covers don't ask. It's shown only when Claude Code suggests something to keep, and its
      name says what it keeps, such as "Allow, and don't ask again in this chat for
      `Bash(git push:*)`". For a command that writes, such as `mkdir`, it also keeps the folder
      the command works in ("…, and let Claude work in the folder …"), because Claude Code asks
      again without it; Claude can then use that folder as it uses the chat's own. When Claude
      Code suggests only accepting file edits, it switches the chat to Accept edits instead.
    - **Deny** refuses, and can carry a reason Claude reads. It's the default button.

2. **Rules kept with this chat.** What you kept with Allow for This Chat. They last across turns
   and restarts until you remove them in Chat Permissions. No other chat gets them, and a
   Continue Here copy starts with none.

3. **The chat's permission mode.** Auto: Claude decides what is safe to run without asking.
   Accept edits: file edits don't ask; commands that need approval do. Manual: anything that
   needs approval asks. Plan: Claude plans and changes nothing. It counts from the next turn.

4. **Claude Code's own settings.** These apply to every Claude chat in the folder: here, in the
   desktop app and in a terminal. The Chat Place only reads them; change them in Claude Code,
   for example with `/permissions`. They come from:

    - your settings: `.claude\settings.json` in your user folder;
    - the chat's folder: its `.claude\settings.local.json`, and its shared
      `.claude\settings.json`, whose allow rules count only once that exact folder is trusted in
      Claude Code (see below);
    - an administrator's policy file, if your computer has one.

    A parent folder's settings don't count.

#### How the levels combine

- A "never allowed" rule in Claude Code's settings refuses without asking, even if you allowed it
  for this chat or the mode would allow it.
- An "always asks" rule in Claude Code's settings asks every time, even after Allow for This Chat.
- Otherwise, anything allowed at any level runs without asking.
- Claude Code never asks about commands it knows only read, such as `git status`, even in Manual.

#### Trusting a folder in Claude Code

A folder's shared `.claude\settings.json` usually comes with the project, so Claude Code ignores
its allow rules until you've said you trust that folder. To trust it, open a terminal in that
exact folder, run `claude`, and answer yes when it asks whether you trust the files in this
folder. Trust is for that folder only: trusting a parent folder, or the repository a worktree was
made from, doesn't count. Until then, Chat Permissions lists those rules as waiting.

#### Chat Permissions for a Claude chat

- **Permission mode**, in words, with **Change...**.
- **Allowed for this chat:** one row per kept rule or folder, read in full: what it allows, the
  rule itself and when it was allowed, such as "Run git push (any arguments). Bash(git push:*).
  Allowed 19 October 2026 at 14:05." A rule kept before this release says "Allowed: time not
  recorded". With nothing kept, the list says "Nothing is allowed beyond the permission mode."
- **Remove** (or Delete in the list; on a Mac, Delete or Fn+Delete) takes the selected row back,
  and **Remove All** takes them all. Each asks once, naming what goes; No is the default.
  Removing a rule leaves a folder kept with it; remove the folder's row too. A removal counts
  from the next turn: a turn that's running keeps the rules it started with, and the dialog says
  so.
- **About these permissions** (Alt+T) says when a removal counts, then lists the rules from
  Claude Code's own settings that apply to this chat's folder, each with its file, and any shared
  rules waiting for the folder to be trusted. These can't be removed here.

### For a Codex chat

A Codex chat's permission mode is read-only or editing files in its folder, with you reviewing
requests or with automatic approval review. "What a Codex chat may do" lists the limits that
apply to every Codex chat started here.

**Automatic approval review.** Choose it in Permission mode in New Session or Continue Here, or
for a chat started here with Change Permission Mode once its turn has finished. Codex then
reviews eligible requests for you, and can allow or deny them. You reviewing requests is the
default, and existing chats keep the mode they have. "Never ask" doesn't mean automatic
approval.

- Before each turn, The Chat Place checks that Codex will use the reviewer you chose. If your
  Codex version or an administrator's policy doesn't allow it, nothing is sent, and you're told
  how to update Codex or choose to review requests yourself. Your Codex settings aren't changed.
- The chat can read files; folder editing covers its own folder. Network access, and temporary
  folders outside its folder, still need approval, and your saved Codex command rules still
  apply. Chat Permissions explains these limits.
- Each review decision is announced and shown in the chat. The latest 128 for each chat are kept
  on this computer, so Chat Permissions can show them after a restart, even if Codex isn't
  running.

**Allow for This Chat** remembers a Git command for this chat and its exact working folder:
ordinary uses of `git status`, `switch`, `add`, `commit`, `push`, `fetch`, `diff` and `log`. It
isn't offered for a command with force or delete options, unknown options, several commands
joined together, shell expansions, another working folder, or extra access listed separately:
those still ask. The button appears only when the request can be remembered.

A remembered approval is as powerful as **Allow Once**. The command may run outside the sandbox,
reach other files and the network, and run Git hooks or other programs Git is set up to run. The
working folder is where it starts, not a limit on what it can do. A plain push follows the
repository's own settings, which can themselves force or delete.

Approvals survive restarting the app. A Continue Here copy starts with none. The Chat Place
answers Codex for you when a remembered approval covers a request, and says so once. The
current turn's approvals given this way appear after the conversation and in Turn Status. Codex
itself keeps none of them, and neither Codex's nor Claude's settings are changed.

**Chat Permissions** lists each kept approval and its date, with **Remove** (or Delete in the
list; on a Mac, Delete or Fn+Delete) and **Remove All**. A removal counts from the next approval
The Chat Place sends, even during a running turn; work already approved may finish. Changing
the mode clears the kept approvals. The mode can change once the turn has finished.

Below the list, **About these permissions** (Alt+T) also lists Codex's own saved rules: the
commands you've told Codex to always allow, and any it always asks about or never allows,
with the file each comes from. Each rule covers any command that starts with its words, so
a rule for `git push` also covers `git push --force`. They apply to every Codex chat, so a
command there runs without asking even with nothing kept in this chat. In a chat set never to
ask, Codex refuses a command that a rule says to ask about. The Chat Place only reads them;
change them in Codex or edit the file. Removing The Chat Place's own approval does not remove a
Codex rule.

## Organizing your chats

With many chats, these keep the list manageable.

### Choosing which chats are listed

**View, Show Sessions** (Alt+V, H) chooses what the list shows: all chats; the ones that need
you or are working; just the ones that need you; just the working ones; the ones with a new reply
you haven't read yet; the idle ones; desktop app chats (Code and Cowork); just Cowork chats;
chats started here; terminal chats; Remote Control chats; ungrouped chats; Claude chats; Codex
chats; GitHub Copilot chats; archived ones; hidden ones; or one of your groups.
**Claude Chats**, **Codex Chats** and **GitHub Copilot Chats** filter by provider. They remain
available when Codex or Copilot is off; each is empty until its provider is enabled.
**View, Sort Sessions** (Alt+V, O) chooses the order: by status, newest first, oldest first, by
title, or by folder. Both choices are remembered, and you stay on the same chat when it's
still listed.

### Choosing what each line says

**View, Session List Columns** (Alt+V, E) chooses which parts each chat's line reads, and in
what order. Move Status to the top, for example, and each line starts with "needs you", "working"
or "idle", so you can stop on the one you want before hearing its title.

The parts are called columns: Title, Provider (Claude, Codex or Copilot), Folder, Status, New
reply, Last activity, Kind (desktop app, Cowork, terminal, The Chat Place and the others in "The
kinds side by side"), Remote Control, Archived, Hidden and Groups, all read at first. Started and
Last message are read only once you add them. Older saved choices gain Provider once, just after
Title (or first if Title is hidden), without changing their other columns. If you remove
Provider afterward, it stays removed. A column with nothing to say for a chat, such as New reply
when there isn't one, is simply left out of that line.

**Last activity** ("active 5 minutes ago") is the last time the chat did anything, even in the
middle of a long turn, or while it's being run outside The Chat Place. How long the turn now
running has taken is in Turn Status (Ctrl+Shift+T). **Started** ("started 2 days ago") is when
the chat began.

**Last message** tells you where each conversation ended without loading it: the provider's
name ("Claude:" or "Codex:") or "You:" and the start of the last message either of you wrote, as
plain text on one line, cut at about 150 characters with "…". A link reads as its words, and a
bare web address as its site. Tool calls and their results don't count. To read the whole
message, press Enter to load the chat, then Enter on the message. A chat whose conversation is
missing or can't be read says nothing in this column. While a chat is working, the line you're
on keeps its last message until the status changes, so you don't hear the line again each time
the AI writes; the other lines change at once, and you hear the new message when you arrow back.

The dialog has two lists. **Available columns** (Alt+V in the dialog) are the ones not read;
**Shown columns** (Alt+S) are the ones read, in order. The buttons Add, Remove, Move Up, Move
Down, Move to Top, Move to Bottom and Reset to Default do what they say, and each tells you what
happened ("Status moved to top, 1 of 11"). The keys work too:

- **Enter** in Available columns adds the column to the end of the line.
- **Delete** in Shown columns removes one (on a Mac, Delete or Fn+Delete). At least one column
  is always shown.
- **Alt+Up** and **Alt+Down** in Shown columns move the column up or down; **Alt+Home** and
  **Alt+End** move it to the top or bottom. On a Mac these use Option.

You stay on the column you moved. The preview at the bottom reads the selected chat's line as
it will be. **OK** saves your choice and changes the list at once, still on the same chat;
**Escape** or Cancel leaves it as it was.

With Status first, every line starts with "needs you", "working" or "idle"; with Last message
first, it starts with the provider's name or "You". Either way, typing a letter in the list no
longer jumps to a title starting with it. **Ctrl+F** still finds a chat by its title or folder,
whichever columns you show.

### Groups

A group collects related chats, such as everything for one project or everything you're
waiting to hear back on, so you can show just those.

- **Ctrl+G** (File, Add to Group) puts the selected chat in a group, or in a new one.
- File, Remove from Group takes it out.
- File, Manage Groups lists your groups, with how many chats each has, to make, rename or
  delete them. Deleting a group never deletes its chats.

A chat can be in several groups, and its line in the list says which ("group Work"). To see
one group, choose it in View, Show Sessions. Ungrouped, in the same menu, lists the chats that
aren't in any group yet.

The desktop app's own groups show here too: in View, Show Sessions, in each chat's line, and in
Add to Group. They can only be changed in the desktop app. Choosing one of them in Add to Group
makes a Chat Place group of the same name, which is shown together with the desktop app's.

### Renaming, hiding and deleting

- **F2** renames the selected chat. A chat started here is renamed outright. Any other chat's new
  name shows only here, since its own files are never changed; clear the name to go back to its
  own.
- **Delete** hides a chat without changing it (on a Mac, Fn+Delete). View, Show Sessions, Hidden
  lists hidden chats, and File, Bring Back Session returns one.
- **Shift+Delete** deletes a Claude chat started here permanently, conversation and all, after
  you confirm (No is the default; on a Mac, Fn+Shift+Delete). Stop or finish its turn first.
  Codex and Copilot chats can't be deleted here yet, and chats started elsewhere can
  only be hidden; delete those where they were started.
- **Shift+Delete on an agent conversation's row** deletes the conversation, not its chats: they
  come back to the list with everything said in them. A chat that's in a conversation can't be
  deleted until the conversation is.

The chat's menu (the Applications key or Shift+F10 in the session list) has all of these, and
the other commands for the selected chat.

## Announcements and notifications

When the open chat gets a reply, when one of the chats started here finishes a turn, or when
any listed chat stops working, it's announced through your screen reader (or a system voice
when no screen reader is running) and put on the status bar. **Ctrl+Shift+R** repeats the last
announcement.

**Settings** (Ctrl+Comma) chooses how much is said:

- **Full** reads the whole reply.
- **Summary** reads the chat's name and the reply's first sentence.
- **Silent** puts it on the status bar only.

Settings also chooses whether every listed chat is announced or just the open one, which speech
engine and rate to use, whether your own messages are read back when they're sent, and whether
you get notifications: see "Settings" below.

On Windows, **Automatic** speaks through JAWS or NVDA, whichever is running, in your screen
reader's own voice and rate. NVDA works without installing anything else. A system voice speaks
only when no screen reader is running, so you never hear a second voice over your screen reader.
If your screen reader is running but The Chat Place can't reach it, a Windows notification says
announcements aren't being spoken, the status bar gives the reason beside the announcement, and
Settings shows it in a Speech problem box after Speaking rate.

On Linux, **Automatic** speaks through Orca, in Orca's voice and rate. Orca 49 and later take
every announcement, whatever window you're in. Orca 48 and earlier take them while you're in The
Chat Place; while you're in another window, speech-dispatcher speaks them, the speech server
Orca uses too, which never talks over Orca. Without Orca, Automatic is a speech-dispatcher
voice, and Settings chooses the voice and rate.

When you're in another window, The Chat Place can show a notification: when one of the chats
started here finishes a turn, fails or needs you, when the chat you have loaded finishes, or when
a desktop app chat starts needing you. Choosing the notification brings The Chat Place forward
with that chat loaded. Settings, Windows notifications (on a Mac and on Linux, Notifications) chooses every
finished turn (the default), only when a chat needs you, or off. While you're in The Chat Place,
the announcement is enough and no notification is shown.

## Settings

Press **Ctrl+Comma** (File, Settings; on a Mac, Command+Comma, in the app menu, The Chat Place).
Each setting applies to every chat, unless it says otherwise below. **OK** saves them and you
hear "Settings saved."; they count straight away unless it says otherwise. **Cancel** or Escape
leaves everything as it was, except Clear Recent Folders. On Windows, the Alt key after each
setting moves straight to it.

- **Announcements:** Full, Summary or Silent (see "Announcements and notifications"). You start
  here when Settings opens.
- **Announce when any listed session finishes a turn, not just the open one** (Alt+A). On at
  first.
- **Read your own messages back when they're sent** (Alt+M). At Full, you hear the message; at
  Summary, its first sentence; at Silent, nothing. On at first.
- **Reading messages:**
    - **Open full messages as a formatted page** (Alt+P): Enter on a message opens the formatted
      page. Off, it opens the plain text box. On at first.
    - **Read the whole message on each item in the messages list** (Alt+W): off, you hear only
      each message's first line. On at first. This works on Windows only: on a Mac, VoiceOver reads
      each line as it's shown.
- **Speech engine** (Alt+E): Automatic, the default, speaks through your screen reader when one
  is running, otherwise a system voice. You can also choose a screen reader (JAWS or NVDA on
  Windows, VoiceOver on a Mac) or one system voice. With both JAWS and NVDA running, choosing one
  decides which is tried first.
- **Speaking rate** (Alt+R): Default, Slow, Normal, Fast or Fastest, for a system voice. A screen
  reader uses its own voice and rate, and the note beside the rate says so.
- **Speech problem** (Windows): shown only when the last announcement couldn't be spoken, saying
  why.
- **Windows notifications** (Alt+N; on a Mac, **Notifications**): "When your sessions finish a
  turn, or any session needs you" (the default), "Only when a session needs you", or "Off".
- **Mark changed lines with** (Alt+H): how Changed Files starts each line of a change.
    - **Words**, the default: "Added: x", "Removed: x", "Unchanged: x".
    - **Short words:** "Add x", "Del x", "Same x".
    - **Diff symbols:** "+ x", "- x", and "= x" for a line that didn't change.
    - **Diff symbols with changed lines:** as Diff symbols, but a line and the line that
      replaced it read one after the other, "!- old" then "!+ new".

  Symbols are spoken only if your screen reader's punctuation level reads them, and you hear that
  when you move the choice onto one. Text you copy from Changed Files is marked the same way.
  "Reviewing changed files" has an example of each.
- **Recent folders New Session offers** (Alt+F): how many of your recent folders New Session's
  Folder list offers, 20 at first, up to 100; 0 offers none. It counts the next time New Session
  opens.
- **Clear Recent Folders** (Alt+L) asks first, with No as the default, and then clears the list
  at once: Cancel in Settings doesn't bring it back. A folder comes back when a chat works in it
  again.
- **Turn on Remote Control for The Chat Place's sessions** (Alt+U): Claude chats started here
  only, and off at first. It counts from each chat's next turn. File, Remote Control can set one
  chat on or off instead (see "Remote Control").
- **Let Claude keep a task list** (Alt+K): Claude chats started here only, from your next
  message. On at first.
- **Include Codex chats (restart required)** (Alt+X): on once Codex is found. Restart The Chat
  Place for a change to count; you hear that when you press OK.
- **Include GitHub Copilot CLI chats (preview; restart required)** (Alt+I): shown only once
  Copilot CLI is installed or has saved a session on this computer. Off at first. Restart for a
  change to count.
- **Let agent conversations hand replies across automatically** (Alt+G): off at first. With it
  on, each agent conversation can choose automatic hand-offs.
- **Tell me when an update has been installed, with a link to what's new** (Alt+T). On at first.

## Usage and context

**Ctrl+Shift+U** (View, Usage and Context) opens a list to read with the arrow keys. Ctrl+C
copies the selected line, Copy All (Alt+A) copies every line, and Escape closes the list. The
status bar's context button opens it too.

- **In a Claude chat:** how full the chat's context is ("Context 62% full: 124,000 of 200,000
  tokens"), then each of your plan's usage limits on its own line. It's also said once, unasked,
  when the context passes 80%, or a limit passes 90%.
- **In a Codex chat:** your Codex account's limits: how much of each is used and left, and when
  it resets. They're shared by all your Codex chats, and anything Codex doesn't report says "not
  reported". A Codex chat started here also shows how many tokens its latest request used, and
  how full its context is when Codex reports it; the status bar shows that too. Until Codex
  reports them, that line says "not reported on this connection yet". The Chat Place never fills
  them in with Claude's figures.
- **In a Copilot chat:** how full its context is, once Copilot has answered in it since The
  Chat Place started ("Context: not known until Copilot answers here" before that), and how
  many premium requests the chat has used, from Copilot's own log. Copilot doesn't say how many
  your plan has left: GitHub's Copilot settings show that. Reading it never starts Copilot.
- **In an agent conversation:** each agent's context and each provider's limits.

In Codex chats started here, The Chat Place also warns you, without cutting off what's being
read:

- when the context reaches 80%, once until the next compaction;
- when an account limit reaches 80%, and again at 100%, once each until the limit resets. The
  limits are checked in the background after each turn ends.

## Exporting a chat

1. Select the chat in the session list, or load it. **Ctrl+E** (File, Export Session) exports
   the selected chat, or in the messages or the reply box, the loaded one.
2. A save dialog opens in your Documents folder, when there is one, with a file name made of the
   chat's title and today's date, such as "Fix the build 2026-10-26.md".
3. Choose the folder, and the format in **Save as type**: **Markdown**, **Web page** or
   **Plain text**. A file name ending in .md, .html or .txt chooses that format instead.
4. Press **Enter** (or Save). If the file is already there, you're asked whether to replace it.

You hear "Exporting", then where it was saved, such as "Exported Fix the build, 30 messages, to
Fix the build 2026-10-26.md in C:\Users\you\Documents". Each message is a heading with who wrote
it and when, then its text. Tool activity is included only when Show Tool Activity is on for
that chat.

### Exporting many chats

**File, Export Chats** (on Windows and Linux, the last item before Exit: **Alt+F**, then **Up**
twice) exports as many chats as you choose, each to its own file.

1. A list of the chats in the session list's current view opens (choose a group or view first to
   narrow it), newest first, with hidden chats left out.
   None are chosen to begin with. Each chat starts with **Skip** or **Export**; **Space**
   switches the one you're on, and you hear how many are chosen. **Select All** (Alt+A) and
   **Select None** (Alt+N) do every chat.
2. Choose the **Format** (Alt+F): Markdown, Web page or Plain text.
3. Press **Enter** (Export). A folder dialog asks where to save them.

Each chat is saved as Export Session would save it, named after its title and the date. If a
name is already taken, the new file gets a number, such as "Fix the build 2026-10-31 (2).md":
nothing is ever replaced. Export Chats works in the background, and the status bar shows how far
it has got. To stop it, choose **Export Chats** again. When it's done, a message says how many
chats were exported and where, and lists any that couldn't be.

A backup (below) is the complete copy, for restoring on this or another computer. Exported chats
are for reading, sharing or keeping as ordinary files.

## Backing up your chats

Your chats with Claude, Codex and Copilot are kept on this computer, not in your account.
Signing in on a new computer doesn't bring them back, and neither does reinstalling.
**File, Back Up Now** saves them in one file you can keep somewhere else.

**Backups are designed to stay out of your way.** Back Up Now, Check Backup and Restore from
Backup work in the background: once you've started one, nothing takes focus while it runs, and
you can go on using The Chat Place. To see how it's going, read the status bar: Back Up Now and
Restore from Backup show how far they've got, and Check Backup says it's checking. Press
**Ctrl+9** (or **F6** until you reach it), use **Left** and **Right** to move between its parts,
and **Escape** to go back, or use your screen reader's read-status-bar key. When a backup, check
or restore is finished, a message box tells you what happened. Scheduled backups don't show one,
since nobody may be there: you hear when they finish (unless announcements are set to silent),
and the status bar keeps the result.

1. Choose **File, Back Up Now**. A folder dialog opens, with when and where you last backed up.
2. Choose a folder, ideally on another drive, a USB stick or a cloud folder such as OneDrive.
   A backup on the same disk won't survive losing the computer, and the summary says so.
3. Press **Enter**. A box asks for a **passphrase** to encrypt the backup. Type one, then type
   it again, or leave it empty and press Enter for no encryption.
4. You hear "Backing up". It works in the background, and the status bar shows how far it has
   got. To stop it, choose **Back Up Now** again and answer Yes.
5. When it's done, a message says how many files it saved, how big the backup is and where it
   went, and whether anything couldn't be copied.

The backup is one zip file named after the computer and the time, such as
"TheChatPlace-backup-MyPC-2026-10-30-194301.zip". An earlier backup is never replaced.
It holds:

- **Claude:** every Claude Code conversation (from Claude Code, the desktop app's Code tab and
  The Chat Place), memory, CLAUDE.md, settings, skills, agents, commands, plans and file
  checkpoints, and the desktop app's list of Code and Cowork sessions.
- **Codex:** conversations, archived conversations, Codex's own databases of chat names and
  history (wherever `sqlite_home` or `CODEX_SQLITE_HOME` puts them), memories, rules, skills,
  settings, and the skills you share between agents in `~/.agents/skills`.
- **GitHub Copilot CLI:** each session's folder, settings, custom agents and skills. Its
  `config.json` is left out if it holds a sign-in token, as it can where there's no keychain.
- **The Chat Place:** which chats were started here, names, groups, hidden chats, agent
  conversations, speech settings and permissions.

It leaves out sign-ins (you sign in again on a new computer), caches, logs, and plugins you can
reinstall.

**Encrypt it if it's leaving this computer.** Conversations can include file contents, command
output and anything pasted into them. With a passphrase, everything in the backup is encrypted
(AES-256), the names of its files and folders too. The passphrase isn't kept anywhere:
**if you forget it, nobody can open the backup.** Without one, keep the backup somewhere private.

To open an encrypted backup by hand, use 7-Zip or WinZip (on a Mac, Keka): with the passphrase
you get `backup.zip`, an ordinary zip of the backup. Windows' own zip folders and the Mac's
Archive Utility can't open encrypted zips.

**Check Backup** (File menu) reads a backup back and compares every file with the list of what it
should hold, so you can trust a copy before you need it. An encrypted backup asks for its
passphrase, and asks again if it's wrong. It says whether the backup is good, where and when it
was made, and what's wrong if it isn't.

### Backing up by itself

**File, Backup Schedule** backs up every day or every week without asking.

1. Choose **File, Backup Schedule**. Focus starts on **Back up automatically**: choose Every day
   or Every week.
2. In **Folder for the backups**, type a folder or choose **Browse**. Another drive, a USB stick or
   a cloud folder is best. A folder inside one the backup copies is refused.
3. **Backups to keep** is how many of this computer's backups to keep in that folder (5 unless you
   change it). Older ones are deleted, but only after a new backup has been made and checked.
   Only backups this computer made are counted: scheduled ones, and unencrypted ones made by
   hand. Other computers' backups and anything else in the folder are never touched.
4. To encrypt them, check **Encrypt them with a passphrase kept on this computer** (Alt+E).
   When you save, you type a passphrase twice. **Change Passphrase** (Alt+P) changes it later;
   backups already made keep the passphrase they were made with.
5. Press **Enter** (Save). A folder on the same disk as your chats is allowed, but you're asked
   first, since it won't survive losing the computer. If a backup is due, one starts straight
   away.

Scheduled backups run while The Chat Place is open. One that came due while it was closed runs
a couple of minutes after it starts. Each runs in the background, with its progress in the
status bar, and you hear when it's finished. If one fails, for example because the USB stick
isn't plugged in, you hear why. A failure is said at most once a day, and it's tried again an
hour later or as soon as you save the schedule. A backup made by hand counts too: the next
scheduled one comes a day or a week after it. The box at the bottom of Backup Schedule shows
the last backup and the last failure. To stop one, choose **File, Back Up Now**.

**About the scheduled backups' passphrase.** Nobody is there to type it when a backup runs, so
it's kept where the system protects it: on Windows, tied to your Windows sign-in; on a Mac, in
your login keychain; on Linux, in your keyring through secret-tool (without secret-tool,
scheduled backups can't be encrypted). It's never in plain text in The Chat Place's files, or
in a backup. A
backup that leaves this computer needs the passphrase to open, but anyone signed in as you
here could make or open one. If you forget it, the backups can't be opened anywhere else.
Turning encryption off forgets the kept passphrase.

### Restoring from a backup

**File, Restore from Backup** brings the chats in a backup back into the session list, on this
computer or a new one.

1. Choose **File, Restore from Backup**, then the backup's zip file.
2. It's checked first. An encrypted backup asks for its passphrase, and a damaged one is said
   and not used.
3. A list of the backup's chats opens, all chosen. The box at the top says how many it holds,
   for each AI, and which computer they came from. Each chat starts with **Restore** or
   **Skip**; **Space** switches the one you're on, and you hear how many are chosen.
   **Select All** (Alt+A) and **Select None** (Alt+N) do every chat.
4. Press **Enter** (Restore). The status bar shows how far it has got, and when it's done a
   message says how many were restored. They're in the session list.

Chats come back in the groups they were in on the computer they came from, and ones that were
hidden there are hidden here. A group with the same name is used, or made; your other groups
aren't changed.

Restored chats are **read-only**. You can read them, find in them (Find in All Sessions too),
export them and list their changes, but not reply to them or continue them. Each row says
"imported chat", the Provider column says which AI it was with, and View, Show Sessions lists it
with that AI's chats. Properties says which computer it came from.

Restoring writes nothing to Claude's, Codex's or Copilot's folders: the chats are kept in The
Chat Place's own folder. Restoring the same backup again, or a newer one from the same computer,
updates chats instead of adding copies. The same chat from two computers shows twice.
**Delete Session Permanently** (Shift+Delete) on a restored chat deletes only The Chat Place's
copy of it.

Reading a backup's Codex chats needs the Codex app or command-line tool installed. Without it,
the other chats are restored and you're told the Codex ones couldn't be read. To stop a restore,
choose **File, Back Up Now**. Chats already restored stay, and restoring the backup again finishes
the rest.

**Claude deletes old conversations.** Claude Code removes conversations older than its
`cleanupPeriodDays` setting, 30 days unless you change it. Desktop app and Cowork sessions are
exempt in recent versions. Chats started in The Chat Place and in a terminal aren't. To keep
them longer, set a larger number in `~/.claude/settings.json`, such as
`"cleanupPeriodDays": 36500`. A backup keeps what was there when it was made.

### Chats Claude has deleted

The Chat Place keeps its own copy of every Claude chat it lists, and adds to it as the chat
grows. When Claude Code deletes a chat's transcript, the chat stays in the list as a
**kept chat (read-only)**: you can read, find and export it, but not reply to it or continue it. It
stays in the view it was in before, such as Terminal Sessions or Chat Place Sessions. A chat
deleted before its first copy was made can't be recovered.

**Settings** (Ctrl+Comma) shows how long each AI keeps chats. **History** (Alt+Y) is that
report, and the button after it opens Claude's settings file in your editor; The Chat Place
never changes it.

**Delete Session Permanently** (Shift+Delete) on a kept chat deletes The Chat Place's copy, and
on one of The Chat Place's own chats deletes its copy along with the transcript. To take a kept
chat out of the list without deleting it, hide it.

The copies are in `kept-history/claude` in The Chat Place's folder, aren't encrypted and never
expire. If Claude rewrites a transcript, the earlier copy is kept too. Back Up Now includes the
copies of chats whose transcript Claude has deleted, so a backup isn't twice the size of your
Claude history. The copies are on this computer's disk: keep a backup elsewhere for a lost
computer.


## More things you can do

- **Properties** (Alt+Enter, View menu, or the context menu) lists what can be known about a
  chat or a message, one "Property: value" line each. In the session list it's the selected
  chat: its kind, folder, status, when it started and was last active, its model, how many
  messages, tool calls, links, code blocks and changed files it has, how full its context is, its
  groups, its IDs and its transcript file. In the messages it's the selected message: who it's
  from, when, where it is in the list, how many lines, words and characters, links and code blocks
  it has. Elsewhere it's the loaded chat. Ctrl+C copies the selected line, Copy All (Alt+A)
  copies every line, and Escape closes the list. On a Mac the key is Option+Return.
- **What AI Knows About You** (Ctrl+Shift+K) lists the files on this computer that shape an AI's
  work for you. Choose Claude or Codex in the Provider list (Alt+V). For Claude: your instructions
  (CLAUDE.md files), the memories it saved, your skills, subagents, commands and settings. For
  Codex: your instructions (AGENTS.md files), memories, skills, saved prompts and settings.
  Enter reads one, and Edit in Your Editor opens it in your own editor.

## Links to chats

These are for Claude chats. A link to a chat takes you straight to it, from anywhere you can
paste one: a note, an email, a message to another chat.

**Copying one.** In the session list, press **Ctrl+Shift+L** (or File, Copy Session Link, or
Copy Session Link on the chat's menu). A list offers the links that apply, best first, and
Enter copies the first: a Markdown link, with the chat's title as its text, that opens the
chat in The Chat Place. Arrow down for the others:

- the same link without the Markdown, just the address;
- for a Claude desktop app chat, its Open in Claude link, which opens it in the desktop app;
- for a chat started here that's on Remote Control, its claude.ai address, for another
  computer or your phone.

What was copied is said, such as "Copied a Markdown link to Fix the build, to open it in The
Chat Place." Escape copies nothing. For a Codex or Copilot chat, Copy Session Link says it isn't
available yet.

**Following one.** A link that starts `thechatplace://session/` opens The Chat Place, or brings
it forward if it's running, with that chat selected and its messages loaded, and you in the
messages, just as if you'd pressed Enter on it. If the list is showing a view or a search that
leaves it out, it shows all chats (or Archived or Hidden, where the chat is) and says so.
A link in a message works too, from the message's formatted page. If a dialog is open, the link
waits until you close it.

A link only finds a chat, as Enter on it does. It never sends a message, runs a turn or changes a
conversation, so it's safe to follow one from anywhere. A link to a chat that's gone says "No
session with that link", and anything else that starts `thechatplace:` says "That link isn't one
The Chat Place knows."

**Where links work.** On Windows, the installed copy of The Chat Place sets up
`thechatplace://` links for your Windows account when it's installed and each time it starts,
and uninstalling it removes them. The portable copy doesn't, so links open the installed copy
if you have one. On a Mac, links work once The Chat Place is in Applications. On Linux, the
AppImage sets up links when it starts; if you move the AppImage, start it once from its new
place. A link copied on
one computer opens the chat only on that computer.

## Remote Control

Remote Control is for Claude chats started here. It lets you keep an eye on one when you're away
from your computer, on claude.ai or in the Claude app on your phone. Turn it on with File, Remote
Control, or from the chat's menu. Its line in the session list then says "Remote Control", and
File, Remote Control copies the chat's claude.ai address.

**What you can do today:**

- While Claude is working on a message you sent from The Chat Place, open the chat on
  claude.ai or your phone to follow what it's doing.
- Answer Claude there. If it asks a question or wants permission, you can answer on your phone
  instead of at your computer, and it can ask again later in the same answer, as often as it
  needs. Whichever place you answer first counts, and the other one stops waiting.
- Type to Claude there while it's working.

**What you can't do yet:**

- Start a new message from your phone. Once Claude finishes answering, the chat shows as
  offline on claude.ai until you send your next message from The Chat Place.
- See the chat from another computer's copy of The Chat Place. The chat lives on the
  computer that started it; claude.ai is how you reach it from elsewhere.
- Read a chat on another computer from here. You can send it a message and hear its reply
  (see "Your chats on other computers", next), but its own conversation is only on claude.ai
  and in the Claude app.

**Good to know:** turning Remote Control on copies the conversation to claude.ai, and it stays
there even if you turn Remote Control off or delete the chat here (you can archive it on
claude.ai). While Claude is working, anyone signed in to your Claude account can type into it.
Settings can turn Remote Control on for all Claude chats started here; then a chat's line
says so once it has connected.

For a Claude desktop app chat, File, Remote Control offers to open it in the desktop app, where
you turn Remote Control on, or to continue it here as a copy with Remote Control on. Codex and
Copilot chats don't have Remote Control.

## Your chats on other computers

If you use Claude Code on more than one computer, say a laptop, a desktop and a Mac, The Chat
Place can send a message to a Claude chat on another of them and let you hear the answer, without
going to that computer. It goes through Claude: Claude in a chat started here passes your message
on, and the other chat's reply comes back to that chat.

**What you need:**

- A Claude chat started here, loaded, with Remote Control on (File, Remote Control). Any of
  your chats will do. One kept for the purpose, called "Hub" say, keeps these messages out of
  your other work.
- On the other computer, a Claude Code chat with Remote Control on: in the Claude desktop
  app, in a terminal, or in The Chat Place there. That computer has to be awake.

**Sending a message:**

1. Load your chat and press **Ctrl+Shift+M** (File, Other Machines).
2. The first time, the list only offers **Refresh the list (asks Claude)**. Choose it. Claude
   looks up your chats on other computers, as a turn in this chat, and says what it found.
3. When it has answered, press **Ctrl+Shift+M** again. Now the list has each chat on your
   other computers and how it is: "idle", "working", "needs you" or "offline".
4. Choose one, type your message, and press **Enter**. Claude sends it word for word.
5. The answer arrives in the same chat as a message from that chat, "From Mac Hub" for
   example, and it's announced. Enter reads it in full, like any message.

The list is Claude's latest look, so after a while choose Refresh again. Other Machines works
between turns. While Claude is working, wait for it to finish.

**Messages from other chats.** Any message another Claude Code chat sends to one of your
chats shows as "From" and that chat's name, not as something you typed, and it's
announced. It doesn't matter whether you asked for it with Other Machines, or in your own words
("ask the Mac session what it's working on"). A message that arrives while Claude is working
shows up when it does.

**Good to know:**

- **"Offline" doesn't always mean off.** A chat in The Chat Place is offline between turns,
  because each turn is its own Claude Code process. A message to it waits, and it gets the
  message on its next turn, when someone sends it something there. A chat in the Claude
  desktop app or a terminal keeps running between turns instead.
- **Other chats find yours by its name.** A chat started here keeps the same name every turn,
  so rename it (F2) to something easy to ask for.
- **You hear only what comes back.** The other computer's conversation can't be read from here;
  open it on claude.ai or in the Claude app for that.
- **A message from another chat isn't you.** Claude treats it as information, not as your
  say-so, so it still asks you before anything that needs your permission.

## Updates

The installed app checks for a new version shortly after it starts, and says so only if there is
one. **Help, Check for Updates** asks right away and offers to install it (No is the default). It
won't install while any provider is working in one of its chats, and it warns you about a reply
you haven't sent. Updating never touches your chats or settings.

The Help menu's update item always says which version you're running, such as
**Check for Updates (running 0.1.4)**. When a newer version has been found, it says so instead,
such as **Update to 0.1.5 Available**, and choosing it offers the update. Alt+H, U reaches it
either way. The status bar's update button does the same.

The first time The Chat Place starts after an update, **The Chat Place Update Installed** says
which version you now have. **See what's new** (Alt+W) opens that version's release notes in your
browser; **Close** or Escape goes back to the app. To stop it appearing, clear "Tell me when an
update has been installed, with a link to what's new" in Settings (Ctrl+Comma).

## Uninstalling

Quit The Chat Place first.

**Windows, installed copy:** open Settings, Apps, Installed apps (on Windows 10, Apps &
features), choose The Chat Place, then Uninstall. That removes the program, in `%LOCALAPPDATA%\TheChatPlace`, and its Start menu entry,
and takes away the `thechatplace://` links it set up for your Windows account (see "Links to
chats").

**Windows, portable copy:** delete the folder you unzipped it into. The portable copy never sets
up links, so there's nothing else to undo.

**Mac:** drag The Chat Place from Applications (or wherever you keep it) to the Trash. Its
`thechatplace://` links are declared inside the app itself.

**Linux:** delete the AppImage, and the desktop menu entry it made,
`~/.local/share/applications/thechatplace.desktop`, which also holds its `thechatplace://` links.

**What's left behind.** Uninstalling keeps your settings and records, in case you install The
Chat Place again. To remove them too, delete these folders, the main things it leaves:

- The Chat Place's own folder: `%APPDATA%\TheChatPlace` on Windows,
  `~/Library/Application Support/TheChatPlace` on a Mac, `~/.config/TheChatPlace` on Linux. It holds its records of the chats started
  here, your settings, groups, names, hidden chats, saved prompts, pasted pictures, agent
  conversations, remembered Codex approvals and its logs.
- On Windows, the formatted page's WebView2 profile, `%LOCALAPPDATA%\TheChatPlace WebView2`.
- Its speech folder: `%TEMP%\thechatplace-speak` on Windows, `$TMPDIR/thechatplace-speak` on a
  Mac, `$XDG_RUNTIME_DIR/thechatplace-speak` on Linux (emptied when you log out; without a
  runtime folder, `/tmp/thechatplace-speak-` and your user number). On Windows and the Mac its
  speech scripts also keep small logs in `claude-speak` (and, on Windows, `claude-speak-nvda`) in
  the same temporary folder. ClaudeSpeak uses those folders too, so keep them if you use
  ClaudeSpeak. On Linux the speech script's log is in the speech folder itself.

**What it never removes**, because none of it belongs to The Chat Place:

- Your Claude, Codex and GitHub Copilot CLI chats and settings, including the conversations of
  chats you started here. Claude Code keeps them in the `.claude` folder in your home folder,
  Codex in `.codex`, and Copilot CLI in `.copilot`. Claude Code, Codex and Copilot CLI themselves
  stay installed, and so does the Claude desktop app.
- Exports you saved, repositories cloned with From GitHub, and worktrees made with A new
  worktree (in the repository's `.claude/worktrees` folder). Delete them yourself if you no longer
  want them.

## Troubleshooting

Problems are grouped under the headings below. Each starts with what you notice, then says what
causes it and what to do.

### Chats and the list

**I don't see a chat I expected.** Check View, Show Sessions: you may be showing only some
chats, and archived and hidden chats aren't in All. A Find (Ctrl+F) also narrows the list;
Escape in the list clears it. F5 refreshes the list at once. Only supported local chats are
listed, not the desktop app's ordinary chats.

**An old chat's messages are gone.** Claude Code deletes old conversations after a while (its
`cleanupPeriodDays` setting). The chat can still be listed, and the messages list says its
conversation is no longer there.

### Sending and replies

**I can't reply to a chat.** Chats started elsewhere are read-only here. Reply in the app or
terminal they were started in, or use Continue Here (Ctrl+Shift+N) when it's offered. Ctrl+O
opens a Claude desktop app chat in the desktop app; there's no such command for Codex.

**The AI is waiting and nothing is happening.** It may need you: press Ctrl+Shift+A.
Ctrl+Shift+T says what the turn is doing.

**A turn stopped with a usage limit.** The announcement says when the limit resets.
Ctrl+Shift+U shows which limits are used, and how much of each.

### Speech and announcements

**I don't hear announcements.** Check Settings (Ctrl+Comma) first: Silent puts announcements on
the status bar only, and Settings also chooses whether every listed chat is announced or just
the open one. Ctrl+Shift+R repeats the last announcement. On Windows, if your screen reader is
running but The Chat Place can't reach it, the status bar gives the reason beside the
announcement, a notification says announcements aren't being spoken (unless notifications are
off in Settings), and Settings shows the reason in a Speech problem box after Speaking rate.

Everything spoken is also written to `speech.log` (nothing is written while Silent is chosen),
in `%TEMP%\thechatplace-speak` on Windows, `$TMPDIR/thechatplace-speak` on a Mac and
`$XDG_RUNTIME_DIR/thechatplace-speak` on Linux: one line
each, with the time, the speech engine and the opening words. A line means the text was handed
to the speech engine, not that it was heard. On Windows, with a screen reader setting, a `route:`
line usually follows, saying which screen reader or voice spoke it, or why a running screen
reader didn't.

**A reply is spoken twice.** If you've installed ClaudeSpeak, its hook also speaks The Chat
Place's turns.

### Claude Code

**Claude Code can't be found, or is "an older npm install".** The Chat Place uses only the
native `claude` program. An old npm install of Claude Code runs as a script, and isn't used.
Install Claude Code with its native installer (see Before you start), or choose Help, Claude
Code Sign-in, which offers to install it.

**I'm not sure Claude Code is signed in.** Choose Help, Claude Code Sign-in. It says whether
Claude Code is signed in, and offers to sign you in if not. If it says Claude Code is too old to
say, run `claude update` in a terminal, then try again.

**A turn says Claude Code is too old for The Chat Place.** Claude Code doesn't know an option
The Chat Place uses. Run `claude update` in a terminal, then send again.

**A turn stopped because Claude Code would have used Fable.** Claude Code was about to answer
with Fable, which the chat didn't choose: its default model, or a fallback from the model you
chose. Some plans bill Fable to usage credits, so The Chat Place stops the turn, usually before
your message is sent, and puts your message back in the message box. Choose another model with
File, Change Model, then send again.

**A turn stopped because Claude would have used an API key.** Claude Code was set up to use an
API key instead of your subscription sign-in, so The Chat Place stopped the turn before anything
was sent. Remove that key from the environment or settings that set it, then try again.

### Codex

**Codex chats don't appear.** They're listed once The Chat Place finds a native Codex CLI: the
Codex app's copy, the copy in the ChatGPT app on a Mac, or a Codex CLI you installed. In
Settings (Ctrl+Comma), check **Include Codex chats (restart required)** (Alt+X), save and
restart. If Codex isn't found, that option says to install the Codex app or CLI, then restart.
Existing chats are listed even when Codex isn't signed in; only starting chats, sending and
usage need the sign-in.

**I'm not sure Codex is signed in.** Choose Help, Codex Sign-in. It says whether Codex is signed
in to ChatGPT and, if it isn't or uses another kind of credential, offers to open `codex login`
in its own window. Codex chats here are sent only with your ChatGPT sign-in.

**A Codex chat is read-only and can't edit its folder.** On Windows, Codex can edit a folder only
after its Windows sandbox is set up: choose Set up Agent sandbox in the Codex app, or add
`[windows]` and `sandbox = "unelevated"` to `~/.codex/config.toml` (see Setting up the Windows
sandbox). Codex also reports read-only when your Codex settings allow only read-only, such as
requirements an administrator set. Nothing is sent; choose read-only, or set up the sandbox and
try again.

**A Codex chat says its connection was interrupted.** Press Stop (Ctrl+Period). The Chat Place
checks Codex's saved turn and, once it has ended, reconnects and says so. No message is resent.
If Codex hasn't confirmed that the turn stopped, press Stop again later. Selecting the chat
again doesn't reconnect it.

### GitHub Copilot CLI

**Copilot chats aren't listed, or Copilot isn't offered in New Session.** Turn on
**Include GitHub Copilot CLI chats (preview; restart required)** in Settings (Alt+I), save and
restart. The option appears only once Copilot CLI is installed or has saved a session on this
computer. If the option is on and Copilot still isn't offered in New Session, The Chat Place
hasn't found a native Copilot CLI: install it as below. New Session checks again each time.

**Starting a Copilot chat says to install GitHub Copilot CLI.** The Chat Place uses only the
native Copilot CLI: on Windows from winget (`winget install GitHub.Copilot`), on a Mac from
Homebrew (`brew install copilot-cli`), or from GitHub's install script. A copy installed with
npm (`npm install -g @github/copilot`) is never used. Install the native one, then restart.

**Copilot CLI in a terminal says the requested model is not supported.** This is in Copilot
CLI itself, not The Chat Place. An old npm copy of Copilot CLI can come first on your PATH, so
typing `copilot` in a terminal runs it instead of the native one.
An old copy's default model may be one GitHub no longer accepts, so every request fails with
"The requested model is not supported" or "Failed to get response from the AI model". Remove
it with `npm uninstall -g @github/copilot`, then open a new terminal: `copilot --version` should
report the newer version. The Chat Place's own Copilot chats never use the npm copy.

**Copilot isn't signed in.** A Copilot chat says so when you start or open one, or send to it.
The message it refused isn't sent again, and messages still waiting come back to the message
box unsent. Choose Help, Copilot Sign-in. It
says which account Copilot CLI is signed in to and, if it isn't, offers to open `copilot login`
in its own window; sign in to a GitHub account that has Copilot. Chats started here use that
sign-in: The Chat Place starts Copilot without token variables such as `GH_TOKEN`,
`GITHUB_TOKEN` and `COPILOT_GITHUB_TOKEN`, so a token set for the terminal isn't used.

**A Copilot chat says this version of Copilot CLI isn't supported.** Your Copilot CLI doesn't
work the way The Chat Place expects. Update Copilot CLI, then try again; if it still says so,
report it (see Reporting a problem).

**The Chat Place won't send to a Copilot chat.** The message says why. If it couldn't confirm an
answer was billed to GitHub Copilot, open the chat again, or restart The Chat Place if it has
stopped sending to Copilot altogether. The other reasons (a folder's Copilot hooks, a chat used
in Copilot CLI since you opened it, a message starting with `/`) are in Starting a Copilot chat.

### Reporting a problem

**Something else.** Help, Report a Bug asks what happened and shows exactly what the report
includes before it goes anywhere: version numbers and settings, whether Claude, Codex and Copilot
are turned on and signed in, their recent errors and how long ago they happened, and how many
chats of each it lists. Never your chats' titles, folders or messages, or your account. It checks
each sign-in first, which takes a moment. Open on GitHub files it; Copy Report copies it, to email to
support@theideaplace.net.

## Good to know

- Reading a chat never changes it, and The Chat Place never writes to the desktop app's files.
  Each provider keeps its own conversations: when you send a message, the provider saves it as
  usual.
- The Chat Place keeps its own settings and records on Windows in `%APPDATA%\TheChatPlace`, on a
  Mac in `~/Library/Application Support/TheChatPlace`.
- It also writes a few things of its own elsewhere:
    - its speech files and `speech.log`, in `%TEMP%\thechatplace-speak` on Windows,
      `$TMPDIR/thechatplace-speak` on a Mac and `$XDG_RUNTIME_DIR/thechatplace-speak` on Linux;
    - on Windows, the formatted page's WebView2 profile, in
      `%LOCALAPPDATA%\TheChatPlace WebView2`;
    - on Windows, the installed copy sets up `thechatplace://` links for your Windows account
      each time it starts (see "Links to chats").
- Otherwise it writes only when you ask it to: an export goes where you save it, From GitHub
  clones into your GitHub folder, A new worktree is made in the repository's `.claude\worktrees`
  folder, and Shift+Delete deletes a Claude chat's conversation file.
- Copilot CLI keeps its chats in `%USERPROFILE%\.copilot\session-state` (on a Mac,
  `~/.copilot/session-state`), or under `COPILOT_HOME` if you've set it. The Chat Place only
  reads them.
- Conversations Claude hands to a subagent aren't shown.
- The desktop app's file formats aren't documented, so a desktop app update could change what The
  Chat Place can read.

## Glossary

- **Agent conversation:** two or three chats started here that pass their replies to each other,
  with you in the conversation.
- **Chat:** one conversation with an AI. The app's menus and labels call it a session.
- **Chat started here:** a chat you started in The Chat Place, with New Session, Continue Here or
  an agent conversation. Only these can be replied to here. Its Kind is "Chat Place session".
- **Compaction:** replacing the earlier part of a conversation with a summary, to make room in
  the context.
- **Context:** the part of a conversation the AI has in front of it as it works. It has a limit.
- **Kind:** where a chat was started, such as the desktop app, a terminal or The Chat Place.
- **Needs you:** the chat is waiting for your answer to a permission, a question or a plan, or a
  turn that went wrong.
- **Provider:** which AI a chat uses: Claude, Codex or GitHub Copilot CLI.
- **Queued message:** a message you sent while the AI was working, waiting to go when the turn
  ends.
- **Read-only:** a chat you can read here but reply to only where it was started.
- **Status unavailable:** The Chat Place can't check what a chat is doing right now.
- **Tool activity:** what the AI does between messages: the commands it runs, the files it reads
  and changes, and what came back. Show Tool Activity (Ctrl+T) shows it.
- **Turn:** one message you send, and all the work the AI does to answer it.
- **Worktree:** a separate copy of a repository on a branch of its own, so a chat's changes stay
  apart from your folder.

For every keyboard shortcut, see Help, Keyboard Shortcuts (F1), or the
[keyboard shortcuts page](https://theideaplace.github.io/AIChat/keyboard-shortcuts.html).
The [README](https://github.com/TheIdeaPlace/AIChat#readme) has the full details of everything here.
