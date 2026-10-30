# The Chat Place

This guide uses Windows key names. On a Mac, use Command (Cmd) for Ctrl,
Option for Alt and Return for Enter. Open menus from the menu bar (VoiceOver:
VO+M), rather than Windows Alt menu sequences.

A keyboard and screen reader friendly home for your AI chats, together in one session list.

The Chat Place is an independent project by Kelly Ford, not made, sponsored or endorsed by
Anthropic or OpenAI. Claude and Claude Code are trademarks of Anthropic; ChatGPT and Codex are
OpenAI products.

New to The Chat Place? The [user guide](https://theideaplace.github.io/AIChat/user-guide.html) starts
with the everyday things and goes deeper; it's also in the app, under Help, User Guide. This README
has the full details.

Keyboard shortcuts: [a web page](https://theideaplace.github.io/AIChat/keyboard-shortcuts.html), also as
[Markdown](docs/keyboard-shortcuts.md) and [plain text](docs/keyboard-shortcuts.txt): the same list
as Help, Keyboard Shortcuts (F1) in the app.

> **Status: version 0.3.2, a pre-release** (0.1.0, the first release, came out on 7 October 2026). The Linux
> build is a preview. How it has been tested is in [DEVELOPING.md](DEVELOPING.md#how-its-been-tested).

The Chat Place brings Claude and Codex chats into one accessible list. Read each conversation,
find or export its messages, hear when it answers, and reply to chats started here. Provider
identifies the AI; Kind identifies where a session started. Support for other providers, such as
Ollama, is planned. Provider-specific features name their provider and are available where that
provider supports them.

For Claude chats, replies use your existing Claude subscription, signed in to Claude Code, and never uses an API
key. Its own sessions' turns are Claude Code turns in headless mode (`claude -p`). Anthropic's
help article [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
says those count against a monthly credit; past it, turns stop unless you've turned on extra
usage, which is billed. View, Usage and Context (Ctrl+Shift+U) shows where you
stand. Reading the desktop app's sessions costs nothing.

## What it does

One window, three parts, in Tab order: the **session list**, the **messages** of the loaded
session, and the **reply box** with Send and Stop. Shift+Tab goes back the same way, and the
session list is always there. **F6** and **Shift+F6** move between those parts and the **status
bar**, which holds whatever was said last ("12 sessions: 1 need you", "Claude is using Bash.") in
a read-only box to read by line, word and character; **Ctrl+9** goes straight there. The status
bar isn't a Tab stop, and your screen reader's own read-status-bar key still works (Orca's
too).

### The session list

- **Session list.** Each item reads its title, its repo folder, its state (needs you, working or
  idle), and when it was last active: "Fix the release build, QuickMail, needs you: Choose a
  version number, active 1 minute ago". By default sessions that need you come first, then
  working ones, then the rest, newest first. **View, Sort Sessions** (Alt+V, O) chooses another
  order: by status (that default), newest first, oldest first, by title, or by folder (A to Z,
  newest first within one title or folder). The choice is remembered, and choosing it reorders
  the list at once, keeping you on the same session. The list refreshes itself every five
  seconds. While you're in the list, rows don't move: a changed session is updated where it is
  and a new one is added at the end. F5, or a refresh while you're elsewhere, puts it back in
  order, keeping you on the same session. If discovery becomes unavailable, the last
  known chats stay listed and a notice warns that they may be out of date. F5 reports
  whether discovery is still unavailable.
  **View, Show Sessions** (Alt+V, H) chooses which sessions are listed: all, needs you or
  working, needs you, working, with a new reply (a Chat Place session whose reply you haven't
  loaded yet), idle, desktop app sessions (Code and Cowork), just Cowork sessions, The Chat
  Place's own, terminal sessions, Remote Control sessions (desktop app sessions linked for Remote Control, and Chat
  Place sessions you've turned it on for or that have connected), ungrouped (in none of your
  groups or the desktop app's), archived (the desktop app's archived sessions, otherwise
  hidden), hidden (the ones you've hidden), or one of your groups. The list's name says what it
  shows and how many ("Session list, needs you, 2 of 139"), and the choice is remembered.
  Choosing one keeps you on the same session when it's still listed.
  **Claude Chats**, **Codex Chats** and **GitHub Copilot Chats** filter by provider. They remain
  available with Codex or Copilot turned off; each is empty until its provider is enabled.
  **View, Session List Columns** (Alt+V, E) chooses what each line says and in what order, so
  it can start with the status rather than the title. The columns are Title, Provider (Claude, Codex or Copilot), Folder,
  Status, New reply, Last activity, Kind (desktop app, Cowork, terminal or The Chat Place),
  Remote Control, Archived, Hidden and Groups, all shown at first, and **Started** and **Last message**, which aren't shown
  until you add them. Provider says which AI; Kind says where the session started. Other
  clients may be identified as an editor, command-line or external session. Saved custom choices
  gain Provider immediately after Title (or first if Title is hidden), without changing their other columns; removing it afterward is remembered. A column with nothing to say for a session is left out of its line. Last
  activity is the last time the session did anything. While a session is working or waiting on
  you, that's when its transcript was last written, so one working through a long turn, or run
  outside The Chat Place, reads "active just now" rather than the time the turn began;
  an idle one keeps the time its own record gives. How long a turn has been running is in Turn Status (Ctrl+Shift+T). **Started**
  says when the session began ("started 2 days ago"). Last message says
  where each conversation ended without loading it: the provider's name ("Claude:" or "Codex:") or "You:" and the start of the
  last message either of you wrote, as plain text on one line (about 150 characters, cut at a
  word with "…"; a link reads as its words, a bare web address as its site). Tool calls and their results don't count. Press Enter to load the session and
  read the whole message. A session whose conversation is missing or can't be read says nothing
  there. For Claude chats, only the end of each conversation file is read, in the background, and again only when
  the file has changed. Codex reads its supported history API in cached background batches;
  neither provider reads Last message while the column isn't shown. While a session is working,
  the line you're on keeps its last message until the status changes, so a screen reader doesn't
  read it again each time the provider writes; other lines change at once. The
  dialog has Available columns and Shown columns (in the order they're read), with Add, Remove,
  Move Up, Move Down, Move to Top, Move to Bottom and Reset to Default, and a preview of the
  selected session's line. Enter in Available adds a column, Delete in Shown removes one (on a
  Mac, Delete or Fn+Delete), and
  Alt+Up, Alt+Down, Alt+Home and Alt+End in Shown move it (Option on a Mac); each is said
  ("Status moved to top, 1 of 11") and you stay on the column you moved. OK saves the choice
  in The Chat Place's settings and rewrites the list at once; Escape cancels. With Status first,
  typing a letter no longer jumps to a title (nor with Last message first, where lines start
  with a provider's name or "You"); Ctrl+F still finds a session by title or folder.
  Arrowing doesn't load anything; **Enter loads that session** into the messages list, moves you
  there, and says "Loaded Quiet one, 12 messages."
- **What "needs you" means.** For a Claude desktop app session: it isn't working right now (it can
  still be open in the desktop app), and the desktop
  app's summary of its latest turn says it's waiting on you, either with a "needs action" note
  (read after "needs you:") or a status such as blocked, needs input or review ready. The desktop
  app doesn't write that summary after every turn, so a session can be waiting on you and still
  show as idle. For a Claude chat started in The Chat Place: Claude is waiting right now for a
  permission, a question or a plan (Ctrl+Shift+A), which ends when you answer; or its last turn
  failed, or tools were refused in it, which is saved with the session and lasts, even across a
  restart, until its next turn starts. Codex chats say needs you for permissions or questions;
  unavailable status means live state could not be verified. After an uncertain send, use Stop
  (Ctrl+Period) to recheck the chat, and check its history before sending again.
- **The session's menu** (Applications key or Shift+F10 in the session list, VO+Shift+M with
  VoiceOver on a Mac, or a right-click) has the File menu's commands for the selected session:
  load, open in Claude, continue here, rename, Remote Control, groups, export, and hide or delete.

### Reading a chat

- **Messages:** newest last, with focus on the newest. Each row shows "You:" or the provider's name, such as "Claude:" or "Codex:", and its
  first line, but on Windows your screen reader reads the **whole message** as you arrow onto it, as words
  (headings and list items as sentences, and each code block as what it is: "Code block, Python, 14
  lines"). Settings, Reading messages turns that off, to hear just the first line. The list's name
  says the session's state and whether it's read-only. **Enter** (or the context menu's Read Full
  Message, with the Applications key, Shift+F10, VO+Shift+M or a right-click) shows the whole
  message as a formatted page. It starts with who wrote it as heading 1, and your screen reader's
  browse mode moves by heading (H), table (T, then its cell commands), list (L) and code block
  (each is a region: R in JAWS, D in NVDA, named like "Code block, Python, 14 lines"). Links open in your browser. **Read as Plain Text** (Alt+P)
  switches to a read-only text box, to read by line, word and character (Option+P on a Mac), and
  Settings can make that the default. Escape closes either, back on the same message. On a Mac the
  page is shown by the system's WebKit, and VoiceOver moves through it with the rotor (VO+U) and its
  heading and table commands; Command+W also closes it. On Windows the page needs Microsoft's Edge
  WebView2 runtime, which comes with Windows 11; without it, the text box opens. Nothing in the
  page is fetched from the internet, and HTML in a message is shown as text, never run. **Lists**
  (View, Lists, **Ctrl+Shift+I**, from the session list, the messages or the reply box) is
  one dialog for each kind of thing in a chat: **Code Blocks**, **Links**, **Changed
  Files**, **Commands** (each command Claude ran: what it was for, "done", "failed" or "no result
  yet", and its output), **Decisions** (each question Claude asked, with your answer) and
  **Tasks** (the task list: each step, "to do", "in progress" or "done", in the provider's
  order). Codex chats also list tasks captured while run here, alongside Code Blocks and Links.
  **Show items from** (Alt+S) chooses This message or The whole chat; for files, commands, decisions and
  tasks, "this message" is the selected message's turn, from your message to Claude's reply (for
  tasks, the ones added or changed in it). Links,
  commands and decisions list the newest first; code blocks and files are in the order they appear.
  The kinds are a list of their own, **Kinds**
  (Alt+K), each with its count ("Commands (12)"), which Tab and Shift+Tab reach on Windows and on a
  Mac; arrowing it changes the items shown, and Enter goes to them. From anywhere in Lists,
  **Ctrl+Tab** and **Ctrl+Shift+Tab** or **Ctrl+Page Down** and **Page Up** on Windows, and
  **Cmd+Shift+]** and **Cmd+Shift+[** or **Cmd+Page Down** and **Page Up** on a Mac, change the
  kind and stay on its list (Control+Tab is left to macOS, which uses it to move between groups of
  controls; where ] and [ need Option, use Cmd+Page Down and Up); on Windows, **Ctrl+1** to
  **Ctrl+6** go straight to one. It opens on the kind you used last.
  **Ctrl+Shift+B** opens it on Code Blocks for the selected message, **Ctrl+L** on Links for the
  whole chat, **Ctrl+Shift+J** (View, Tasks) on Tasks for the whole chat, and **Ctrl+Shift+D** on
  Changed Files, for the selected message's turn (or your
  latest) if it changed any files, else for the whole chat. Enter reads an item in full in the Details box
  (on Links, Enter or Open opens the link in your browser and closes Lists); Ctrl+C (or Copy)
  copies it (the code, the address, the file's path, the command or your answer), and on Links
  Ctrl+Shift+C (or Copy as Markdown) copies a Markdown link. Typing narrows the list (the letters go
  to its Filter box); Down Arrow comes back. A kind with nothing says so ("No commands in this
  chat."). **Ctrl+Shift+C** copies the message's last code block straight away. A link reads as
  its words and then where it goes, a bare address as itself (a long one shortened to its site and
  its end), then who wrote it and roughly when, and "in code" for one in a code block or inline
  code: "release notes, github.com/…/tag/v0.1.5. Claude, 3 messages ago". Each address is listed
  once. References to files in the repository, such as `[main.py](src/main.py)`, aren't listed.
  Only web, email and session links open; anything else, such as a file: path, is marked "copy
  only" and says why if you try. Links in tool calls and results are listed only with Show Tool
  Activity on. Changed files are listed for Claude, Codex and Copilot chats; commands and decisions for
  Claude chats for now. Codex chats
  also list live tasks captured while run here; older chats and chats run elsewhere may have no
  saved list. Question cards read as
  "Claude asked: Which version?" with the options in the full text,
  then "You answered: ...". Refused tools read "Permission denied: ...". Tool calls and tool
  results are hidden unless you turn on Show Tool Activity (Ctrl+T). It's set for each session and
  remembered, so turning it on for one session leaves the others as they were. With it on, the open session's
  tool calls are also **spoken as they happen**, so a long turn isn't silent: "Using Bash: git
  status; Read: main.py." In The Chat Place's own sessions, what Claude writes between tool calls
  ("Let me check the build.") is spoken too. A run of calls is gathered for a moment and said
  together, counted when there are many ("Using Read 4 times, then Bash."), and never cuts off your
  screen reader. At the summary level tools are always counted; at silent only the status bar shows
  them. Tool results aren't spoken. New messages arrive at the end without moving you.
- **Escape** in the messages or the reply box (or Backspace in the messages) goes back to the
  session list, on the same session. Enter there on the session that's already loaded takes you
  back to its messages where you left them, without reloading.
- **Command results and compaction:** local Claude Code output, such as `/context`
  and `/cost`, appears as Claude Code messages with tool activity off. After `/compact`,
  a Conversation compacted row and a queued announcement confirm it happened. Reported
  before/after token counts appear there and update Usage and Context. The earlier
  conversation summary remains available with tool activity on. Terminal formatting is
  removed; long command output is shortened with a notice, including in exports.
  Command-result notifications use a brief summary even in Full announcements;
  Silent keeps them in the status bar.
- **Changed Files** (Ctrl+Shift+D, View menu) opens Lists on Changed Files: the files
  Claude, Codex or Copilot changed in the selected message's turn (your latest, if none is selected), or in the
  whole chat, each with its line counts ("main_frame.py, 40 lines added, 12
  removed, in thechatplace\ui"). Enter on a file reads its changes in a text box, a line at a
  time: where each change is, then its lines, each starting with how it changed. Settings, "Mark
  changed lines with" (Alt+H) chooses words ("Removed:", "Added:", "Unchanged:", the default), short
  words ("Del", "Add", "Same"), diff symbols ("-", "+", and "=" for an unchanged line), or diff
  symbols with changed lines, where a line and the line that replaced it read "!- old", then
  "!+ new". Symbols are spoken only at a screen reader punctuation level that reads them. Copied
  text is marked the same way. It's built from the
  transcript, so it works for desktop app sessions too, with no git needed. Changes made by a
  subagent Claude hands work to, and notebook edits, aren't counted yet. For Copilot chats it's
  built from Copilot's own log, whichever tool made the change, so chats started in Copilot CLI
  have it too, and reading it never starts Copilot; a deleted file says "deleted". For Codex,
  it uses completed file changes from Codex's own history, including chats started outside The Chat Place.
  Renames say "renamed" and show the old path in the details; Copy copies the new path.
  Pending, declined and failed edits aren't counted. Changes made through shell commands or other
  tools that don't report file changes aren't counted either. When the loaded
  session's turn ends, the announcement (at the full level) is followed by a one-line summary:
  "Changed 3 files: main_frame.py, 40 lines added, 12 removed; …".
- **The task list.** On longer work Claude keeps a list of its steps (Claude Code's
  TaskCreate and TaskUpdate, or the older TodoWrite), and The Chat Place reads it from the
  transcript, for desktop app and terminal sessions as well as its own. View, Tasks
  (**Ctrl+Shift+J**) lists it; Turn Status (Ctrl+Shift+T) and Properties say how far along it is
  ("Task 3 of 7: Running the tests. 2 done.", or "Stopped at task 3 of 7: …" once Claude isn't
  working; Turn Status leaves out a list that's finished or from an earlier turn); and when the
  loaded chat marks a step done, it's
  announced ("Done: Run the tests. 4 of 7."), at the Full announcement level only, without
  cutting off anything being read. Claude Code leaves the task list out on its newer models
  unless asked, while the desktop app's sessions have it, so The Chat Place asks for it in its
  own sessions (`CLAUDE_CODE_ENABLE_TODO_TOOLS=1`, from Claude Code 2.1.233). Settings, "Let
  Claude keep a task list" (Alt+K) turns that off, from the next message; it's on to start
  with. The list costs a little context each turn, and Claude decides when a task is worth a
  list, so short turns usually have none.
  Codex's task list appears in the same Tasks list, Turn Status and Properties while a chat
  runs here. Its latest reported steps are saved in The Chat Place's own data folder
  and restored when that owned chat is reopened. The list keeps its last
  reported states after a turn finishes, fails or is stopped, and remains available if a later
  message produces no task update. Codex task lists from earlier sessions or chats
  run elsewhere are not available. Proposed plan text is separate from this checklist.
  The "Let Claude keep a task list" setting applies only to Claude.

### Finding things

- **Find** (Ctrl+F, View, Find). In the session list it asks for some text and shows only
  sessions whose title, folder or "needs you" note contains it, within the current view; the
  list's name says so ("Session list, matching \"build\", 3 of 139") and Escape shows them
  all again. In the messages it finds the next message containing the text, searching each
  message's whole text, not just the first line you see; F3 and Shift+F3 find the next and
  previous, going round from the other end when they reach one (and saying so).
- **Find in All Sessions** (Ctrl+Shift+S, View, Find in All Sessions) answers "which session did
  we talk about that in?". It asks for some text and searches the whole text of every message in
  every session the list shows (its Show Sessions view, and any Ctrl+F text), in the background,
  ignoring case; tool calls and results count only in sessions with Show Tool Activity on. You
  hear "Searching 32 sessions" (and, in a long search, how far it's got every ten seconds;
  Ctrl+Shift+S again stops it), and then a list of the messages found, each read as the session,
  who wrote it, when, and the words around the match ("Build, Claude, yesterday 10:42: …the
  database migration ran…"), newest first within each session. The list's name says how many
  sessions were searched and how many couldn't be read (a transcript no longer on disk, say).
  Enter (or Go to Message) loads that session on that message. If you've moved on by the time
  it finishes (to another dialog, another place in the window, or another app), the list waits:
  you hear how many were found and "Press Ctrl+Shift+S to see them" (for ten minutes, until the
  list's view changes or you start another search). With no match, you hear so and no list opens. It stops at 500 messages.

### Desktop app, Cowork and terminal sessions

- **Open in Claude** (Ctrl+O) switches the desktop app to the session, for approving a permission
  prompt or answering a question card in a desktop app session.
- **Cowork sessions.** The desktop app's Cowork sessions are listed too, read-only, as desktop app
  sessions are. Each row says "Cowork session", and in place of a repo folder it names the folder
  you gave Cowork to work on, or says "no folder" if there isn't one. View, Show Sessions, Cowork
  Sessions lists only them, and Ctrl+F in the list finds them by "Cowork". Enter reads the
  conversation, and Open in Claude switches the desktop app to it. Whether the list can tell that
  a Cowork session is working (and so announce its finished turn) depends on the version of
  Claude Code the desktop app runs it with: older ones don't record it. They can't be continued
  here (see Limitations), so Continue Here isn't offered for them, and on the session menu it's
  unavailable. The desktop app's Chat conversations are kept on Anthropic's servers rather than on
  your PC, so they aren't listed.
- **Terminal sessions.** Sessions you started by typing `claude` in a terminal are listed too,
  read-only, as desktop app sessions are. Each row says "terminal session" and names the folder
  you started it in. Its title is the name you gave it with `/rename`, or else the one Claude Code
  gave it, or else the start of your first message. View, Show Sessions, Terminal Sessions lists
  only them, and Ctrl+F in the list finds them by "terminal". Enter reads the conversation.
  Claude often makes many tool calls in a terminal session: they're hidden unless you turn on Show
  Tool Activity (Ctrl+T), which also speaks each one as it happens, and Ctrl+T hides them again. The
  list shows one as working while Claude works in it, and as needing you while it waits on you in
  the terminal (a permission or a question), and both are announced. You reply to it in its
  terminal, or carry it on here with Continue Here; Open in Claude isn't offered, since the
  desktop app doesn't list it. Sessions run by scripts with `claude -p`, and those of the VS Code
  extension, aren't listed.
- **Continue Here** (Ctrl+Shift+N, or the Continue Here button after the reply box's note) carries a desktop
  app or terminal session on in The Chat Place, as a copy: a new Chat Place session in the same
  folder, with the whole conversation so far in its messages, that you reply to here. You type its
  first message in the same dialog as New Session; the title starts as "<title> (continued)". The
  original session isn't changed, and what you do in the copy doesn't appear in it.

### Starting a session and replying

- **New Session** (Ctrl+N) starts a session of The Chat Place's own: choose a folder, a title,
  the model (Alt+D), the effort (Alt+E), a permission mode (auto by default; accept edits, manual
  and plan are offered) and the first message. The models are Default (your Claude Code setting), Opus,
  Sonnet and Haiku, each the latest of its family. The model is kept with the session and
  passed to every turn. Arriving in the messages, you hear it ("Messages in Build (idle, on
  Opus)"). Fable isn't offered: on some plans it bills to usage credits, and in the headless
  mode The Chat Place uses, Claude Code does that without asking. So if a turn would run on
  Fable anyway (Claude Code's own default, or a fallback), The Chat Place stops it before
  anything is sent and says why. **File, Change Model** changes a session's model from its
  next turn. **Effort** is Default (your Claude Code setting), Low, Medium, High, Extra high or
  Max, passed to every turn as Claude Code's `--effort`; models that don't support effort levels
  ignore it. **File, Change Effort** (Alt+F, F) and **File, Change Permission Mode**
  (Ctrl+Shift+O) change them from the next turn: the list starts on the current choice, marked
  "(now)", and you hear, for example, "Permission mode for Build: plan, from its next turn." A
  turn that's running carries on as it started. The session's heading says its model, and its
  effort and mode when they aren't the defaults ("on Opus at high effort, plan mode"). For desktop app and terminal sessions these are set
  in Claude or the terminal, and the items say so. If that first message never reaches Claude (Claude Code not signed in, say), it goes
  back into the reply box and Send starts the session again.
- **Where a new session works.** The Folder box starts at the folder used most recently,
  and its list (Up and Down Arrow on Windows, or open it with Alt+Down Arrow) is every folder
  your sessions have used, The Chat Place's and the desktop app's, newest first: a worktree
  counts as its repository, and Cowork sessions and folders that no longer exist are left out
  (if checking takes more than a moment, as for a network drive that's gone, the list is offered
  unchecked). Delete (Fn+Delete on a Mac) on a folder from the list, with the list open
  or not, takes it off the list, and the next folder takes its place; a folder you've taken off
  comes back once a session works in it again. Delete still edits a folder you're typing.
  Settings (Ctrl+Comma) sets how many recent folders the list offers (20 at first; 0 for none)
  and has Clear Recent Folders (Alt+L), which asks first and then takes them all off.
  Browse (Alt+B) still opens a folder dialog. **From GitHub** (Alt+G) lists your
  repositories with `gh repo list` (the GitHub CLI's own sign-in; The Chat Place keeps no token)
  and says how many there are, or in the list why it couldn't get them. Type to filter, or type
  owner/name or a GitHub address for exactly that repository; Enter uses it. A repository
  already in your GitHub folder (checked by its origin) is used as it is; otherwise
  `gh repo clone` clones it in the background into a temporary folder beside it, renamed only
  when the clone has worked. Cancel ends gh and the git it started, and removes only that
  temporary folder; a folder of the repository's name that isn't the repository is never
  touched. Clones go into the GitHub folder (in your home folder or in Documents), made if
  there isn't one. **Work in** (Alt+I) is The folder as it is (naming its branch) or A new
  worktree, which a folder outside a git repository can't have (choosing it says so). **Branch**
  (Alt+R) lists the local branches no worktree has checked out; choose one, type the name of a
  branch only a remote has (the new branch tracks it), or type a new name to branch from what
  the folder has checked out. Start makes the worktree in the background with `git worktree
  add`, in `<repo>/.claude/worktrees/<branch>` where Claude Code and the desktop app put theirs
  (beside them even when the folder is a worktree itself), and starts the session in the same
  folder inside it as the one chosen. If git doesn't already ignore `.claude/worktrees/`, it's
  added to the repository's own `.git/info/exclude` (never shared), so worktrees don't show as
  new files. The worktree is left in place afterwards. Continue Here keeps its session's own
  folder, so it has none of these. git and gh are run with no shell and never prompt; branch and
  repository names are checked before either sees them. On a Mac without Apple's Command Line
  Tools, `/usr/bin/git` isn't used, since running it asks to install them.
- **Reply box:** for The Chat Place's own sessions, type and press Ctrl+Enter (or Send). You stay in
  the reply box. While a turn is running, Ctrl+Alt+Enter steers instead (see
  [Steering a running turn](#steering-a-running-turn)). For a desktop app or terminal session the same place holds a read-only note
  saying where you reply, with Open in Claude and Continue Here as they apply.
- **Commands and skills** (Ctrl+/, the Commands button after Stop, or File, Insert Command
  or Skill), in The Chat Place's own Claude sessions (not yet for Codex or Copilot chats): a searchable list of your skills and custom commands, then Claude Code's own
  (/compact, /context, /code-review and the rest), each with its description and what it
  takes. Type to filter by name or description, Down to the list, Enter to choose: it goes
  at the start of the reply box ("/compact "), replacing a command already there and keeping
  what you typed, ready to finish and send. The list is Claude Code's own for the session's
  folder, asked for in the background when the session loads (no turn, no cost) and kept
  current by each turn. If it isn't there yet, you hear "Commands are ready" when it is; the list
  never opens by itself.
- **Prompts** (Ctrl+Shift+P, File, Prompts): a library of messages you send often, each with a
  name. The dialog lists them by name, with the selected one's text below to read (Alt+X). **Use**
  (or Enter on a prompt) closes it and puts the prompt in the reply box of the loaded Chat Place
  session: in place of an empty reply, or at the cursor in what you've typed (replacing any text
  you've selected), ready to change and send. **New** and **Edit** ask for a name and the text (Enter starts a new line in the text;
  Ctrl+Enter saves), **Delete** (or Delete on a prompt) asks first, and **Move Up** and **Move
  Down** put them in the order you want. Each change is saved at once and spoken ("Prompt saved:
  Review", "Prompt deleted: Review"); Close or Escape closes. To save something as you send it,
  press **Ctrl+Shift+Enter** in the reply box (File, Send and Save as Prompt): it asks for a name,
  starting with the message's first few words, saves the prompt and then sends the message as
  Ctrl+Enter does. Escape at the name does neither, and leaves your message in the reply box.
  Prompts are kept in `%APPDATA%\TheChatPlace\prompts.json` (on a Mac,
  `~/Library/Application Support/TheChatPlace/prompts.json`). The Chat Place doesn't read Claude
  Code's own history of what you've typed.
- **Attachments**, in The Chat Place's own Claude sessions: **Attach Files** (Alt+T, after Commands;
  Ctrl+Shift+F) adds files and images to your next message, and **Ctrl+V** in the reply box
  attaches a picture on the clipboard, such as a screenshot from Win+Shift+S. They're listed
  under the reply box ("2 attachments: screenshot.png, log.txt"); Delete there removes one.
  Images go to Claude as images, so it can describe what's on screen; other files are named
  in the message as `@"path"`, which Claude Code reads in for Claude. A message can be
  just attachments. Each session keeps its own until they're sent. Pasted pictures are saved
  in `%APPDATA%\TheChatPlace\pasted images` (on a Mac,
  `~/Library/Application Support/TheChatPlace/pasted images`).

### Permissions and questions

- **Claude asks, you answer.** In The Chat Place's own sessions, when Claude needs permission
  for something the permission mode doesn't allow, asks you a question, or has a plan for you
  to approve, the turn waits for you. It's announced ("Build needs you. Claude wants to run
  git push. Ctrl+Shift+A answers."), and the session shows as needing you in the list.
  **Ctrl+Shift+A** opens the answer:
  - **Permission:** the whole request (the command, or the file and what would change) to
    read by line, then **Allow**, **Allow for This Chat** (shown when Claude Code suggests
    something to keep, and named in full, such as "don't ask again in this chat for
    Bash(git push:*)"), or **Deny**, which is the default button and can carry a reason Claude
    reads. For a command that writes, such as `mkdir`, Claude Code also suggests the folder it
    works in, and Allow for This Chat keeps both, as its full name says ("…, and let Claude work
    in the folder …"); without both, Claude Code would ask again next turn. A kept folder is
    given to every later turn as an extra working folder, so Claude can use it as it uses the
    chat's own folder, with any tool. The Chat Place never keeps a whole drive or share. When
    Claude Code suggests no rule, only a switch to accepting edits, Allow for This Chat switches
    the chat's mode instead.
  - **Questions:** a read-only Question box first, where focus starts, with what the chat wrote
    just before asking and each question in full; then each question as a group of options
    with their descriptions, with Other and a box to type your own answer; a Preview box when
    an option has a preview, showing the one for the option you're on (#415); Send Answers, or
    Don't Answer.
  - **Plan:** the plan to read (Read Formatted shows it as a page, by heading), then **Approve**, choosing the mode to carry on in (accept
    edits, auto or manual), or **Keep Planning** with what to change, which is the default.

  Escape in any of them answers later; nothing is approved or refused by waiting. "Allow for
  This Chat" lasts for the chat, not just the turn: The Chat Place keeps the rule with the
  chat and gives it to every later turn. It never writes Claude Code's settings files.

  **File, Chat Permissions** (Ctrl+Shift+H) shows what a chat started here may do without
  asking: its permission mode in words, with **Change...** (the same choices as Change
  Permission Mode), and the rules and folders kept with the chat, one to a row, each read in
  full (a folder reads "Work in the folder …"). Removing a rule leaves a folder kept with it;
  remove the folder's row too to take that back. Each row says what it allows, the rule itself
  and when it was allowed ("Run git push (any arguments).
  Bash(git push:*). Allowed 19 October 2026 at 14:05."). A rule kept before this release says
  "Allowed: time not recorded". **Remove** (or Delete in the list) takes the selected rule back,
  and **Remove All** takes them all, each after one question that names what goes. With nothing
  kept, the list says "Nothing is allowed beyond the permission mode." For a Claude chat a
  removal counts from the next turn: a running turn keeps the rules it started with, and the
  dialog says so. Its About box (Alt+T) also lists, read only, the rules from Claude Code's own
  settings that apply to the chat's folder: your settings, the folder's
  `.claude\settings.local.json`, its shared `.claude\settings.json` (whose allow rules count
  only once the folder is trusted in Claude Code, and are listed as waiting until then) and any
  administrator's policy; a parent folder's settings don't count. Each is named with its file.
  These apply to every Claude session in that folder, The Chat Place never changes them, and
  you change them in Claude Code (for example with `/permissions`). A "never allowed" rule there
  refuses without asking, and an "always asks" rule asks every time, even after Allow for This
  Chat. For a Codex chat, the dialog lists command approvals kept here and when you allowed
  them, with Remove and Remove All. Removal applies before the next approval is sent, including
  during a running turn; work already approved may finish. Changing mode clears the kept
  approvals. Its About box also lists Codex's own saved rules, read only: the commands you've
  told Codex to always allow (which run without asking in every Codex chat), and any it always
  asks about or never allows, with the file each comes from. Desktop app and terminal
  sessions are read-only here, so they have none, and a Continue Here copy starts with nothing
  kept.

### Codex chats

**Codex chats appear automatically** when a native Codex CLI is installed and signed in to
ChatGPT. That includes the copy bundled with the ChatGPT app on Mac, the Codex app's copy on
Windows, and the copy Codex's own installer keeps in your `.codex` folder. Settings offers
**Include Codex chats (restart required)** to turn them off or on explicitly; your choice
is kept. Without Codex installed, no Codex process starts, and Settings says to install
the Codex app or CLI, then restart. As for any session, press Enter on a Codex chat to load its history; messages,
formatted reading, Find and export use Codex. New Session offers a Provider choice: choose Codex to start a chat here.
Model and Effort offer the model and effort choices available in Codex in New Session and Continue Here. Leave
Configured Codex model/effort selected to use its settings. Effort choices follow the selected
model; choices load in the background and are rechecked before the chat starts. If loading fails,
configured defaults remain available. New Codex chats default to editing files in their selected folder with on-request approvals.
Choose read-only in New Session, or change an idle chat started here with File, Change
Permission Mode (Ctrl+Shift+O). Existing chats retain their saved permissions.
Continue Here copies a local Codex chat into a new chat started here. These chats
support text replies, Stop, queued messages and Answer (Ctrl+Shift+A) for
permissions and questions. Externally started Codex chats remain read-only. Turning the
option off and restarting hides Codex without removing its saved preferences.
Quitting The Chat Place requests a stop for its running Codex replies.
Revisiting a chat preserves its running turn. The Chat Place refuses to resume a saved
chat whose latest turn is unfinished or uncertain. If the connection is lost,
use Stop to check the saved turn and reconnect once it has ended. No message
is resent. Selecting the chat again does not automatically reconnect. If
Codex still reports unfinished work, The Chat Place refuses recovery and says why.
Answer shows the command, its folder and Codex's reason. When Codex names extra access
it needs, such as writing to Git's `.git` folder, another folder or the network, a
"This also needs" list says what, in plain words; a network request names the host. When
it names none, the request is usually to run the command outside the chat's folder
sandbox, and the dialog says so: if you allow it, the command can change any file you can
and use the network. Only the one-time answers Codex offers for that request are shown:
Allow Once, Deny (Codex carries on without it) and Deny and Stop Turn, plus Answer Later.
Deny is the default button when it's offered; otherwise Answer Later is. When Codex offers
to keep a native approval (for the session or as a saved rule), The Chat Place leaves that
native choice out and sends only one-time answers. For an eligible Git command,
**Allow for This Chat** remembers its prefix only in
The Chat Place's own data, for this chat and its exact working folder. It survives restarting
the app; a Continue Here copy starts empty. Supported operations are `git status`, `switch`,
`add`, `commit`, `push`, `fetch`, `diff` and `log`, with ordinary supported arguments.
Unknown options, explicit force or delete options in the command, compound commands, shell expansion, a changed
working folder, and requests for separately listed extra access still ask.
Remembering has the same authority as **Allow Once**: the command may run outside the sandbox,
including Git hooks and configured programs, and may access files elsewhere and the network.
The working folder is where it starts, not a confinement boundary. A plain push follows the
repository's configuration, which can itself request forced updates or deletion. The Chat Place sends one-time answers
on your behalf; it never creates a native Codex session approval or saved rule. Automatic
approvals are announced once, shown after the conversation for the current turn and recorded
in Turn Status. File, Chat Permissions (Ctrl+Shift+H) lists and removes them.
Requests The Chat Place cannot present safely, such as typing into a command that's
already running or running a command somewhere other than this computer, are declined
with a notice that says which kind and why; they do not disconnect the chat. Turn Status
keeps that notice available.
Turn Status (Ctrl+Shift+T) reports elapsed time and latest activity, and sent
messages follow the same read-back setting as Claude. Only chats in the local Codex catalog are listed. A status Codex cannot verify
is omitted from default rows and remains available in Properties and the Status column.


Continue Here starts a Codex copy read-only. You can select folder editing for
a project folder, but the home folder, its parents and volume roots cannot be
used as writable folders. If Stop is acknowledged but the turn stays open,
press Stop again to check Codex's saved turn status without resending anything.

**Automatic approval review.** In New Session or Continue Here, the
Permission mode list offers read-only or folder editing with **automatic approval
review**. For an existing owned Codex chat, finish or stop its turn, then use
File, **Change Permission Mode**, or File, **Chat Permissions**, **Change**.
The new mode applies to the next message in the same chat, including when you
change it before sending the first message. The chat keeps its history.
If the change cannot be verified or saved, sending stays disabled. Use **Stop**
to recheck the saved permissions, or try **Change Permission Mode** again.
Recovery sends no message.
Codex's reviewer evaluates eligible requests for you and can allow or deny them;
this reduces interruptions without giving unrestricted access. Human review remains
the default, and existing chats keep their selected mode. This is different from
"never ask", which does not automatically approve requests.

The selected reviewer is verified when the chat starts or resumes and is pinned
on every turn. If your Codex version or administrator's policy cannot confirm
automatic review, no message is sent and the app explains how to select human
review or update Codex. There is no silent fallback. Your global settings are not
changed. Read access remains available; folder editing covers the selected project
folder only. Network access and temporary folders outside that folder require
approval. Your saved Codex command rules still apply. Chat Permissions explains
these boundaries and TCP's overrides. Automatic review decisions are announced
and shown in the chat; the most recent 128 decisions are kept locally for that
chat and can be read again in Chat Permissions, including after restarting.

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

### GitHub Copilot CLI chats

**Settings: GitHub Copilot CLI chats.** Enable **Include GitHub Copilot CLI
chats (preview; restart required)** (Alt+O), save and restart to list GitHub Copilot CLI
sessions alongside Claude, and to start Copilot chats of your own. The option appears once
Copilot CLI is installed or has saved a session on this computer: to decide that, Settings looks
for the Copilot CLI program and at the names of the folders in Copilot's session folder and
whether each has a conversation log, never what's in them, even with the option off; with it off
nothing else of Copilot's is read.
The Chat Place reads the session folders Copilot CLI keeps in
`%USERPROFILE%\.copilot\session-state` on Windows and `~/.copilot/session-state` on a Mac (or
under `COPILOT_HOME`); reading never starts Copilot, so it uses none of your Copilot requests. Selecting a Copilot chat opens its messages;
formatted reading, Find and export work too, and Show Tool Activity shows the commands and file
changes Copilot ran. Chats started in Copilot CLI, or in another app, are read-only here: reply
in Copilot CLI.

**Starting a Copilot chat here.** In New Session, choose **Copilot** as the Provider, then a
folder and your first message. **Model** lists the models Copilot offered your last Copilot chat
here (it lists them only for a chat), each with the premium requests a message costs, such as
"GPT-5 (1x)"; leave it on Copilot's configured model, or choose one, and the chat switches to it
before your first message goes; if Copilot won't, the chat says so and uses Copilot's own.
**File, Change Model** (Alt+F, D) changes a Copilot chat's model later, from its next turn,
choosing from the models Copilot offers that chat; the chat must be open in Copilot first (select
it and wait until it's ready). Once it is, the chat's heading says its model ("on GPT-5 (1x)").
Effort and Copilot's other
settings are its own. Each message uses requests from your Copilot plan. Type and send as in any chat started here; messages sent while
Copilot is answering wait their turn, and Stop (Ctrl+Period) cancels the answer and
puts any waiting messages back in the message box, unsent. Copilot asks before it runs a command
or changes a file: you hear "needs your answer", and Answer (Ctrl+Shift+A) shows the whole
command or change, with **Allow Once** and **Deny**. Deny is the default, and nothing is ever
allowed for later. A command or change too long to show in full can only be denied. The Chat
Place won't send to Copilot:

- in a folder whose Copilot hooks could act without asking you (a project's hooks of any kind,
  or your own or your organisation's hooks that can approve); it names the file, and Copilot CLI
  can still be used there;
- if the answer can't be confirmed as billed to GitHub Copilot;
- after the chat has been used in Copilot CLI in a terminal since it was opened here: open it
  again first;
- with a message starting with `/`, since Copilot's commands include ones that turn off its
  questions.

Continue Here, Change Permission Mode and Chat Permissions aren't available for Copilot chats
yet. A Copilot chat started here can be in an agent conversation, and Send Reply to Another
Chat can send a reply into it. Usage and Context (Ctrl+Shift+U) says how full a Copilot chat's
context is (once Copilot has answered in it here) and how many premium requests the chat has
used, from its log; Copilot doesn't say how many your plan has left, so check that in GitHub's
Copilot settings. A chat started here is kept in The Chat
Place's own list in `provider-sessions-copilot.json`, beside `sessions.json`. A chat that's answering says working, even while Copilot runs a command, and is
announced once when its answer is finished. After 10 minutes with no activity it adds "no
activity from Copilot for 10 minutes", and after 2 hours it's shown as status unavailable (Copilot
may have been closed partway through); one waiting for your permission stays needs you for up to a day, then is shown as status
unavailable ("waiting in Copilot for over a day"). Sessions started in Copilot CLI in a terminal are listed as terminal sessions.

### Passing replies between chats

- **Send a reply to another chat (File, Send Reply to Another Chat, Ctrl+Shift+E).** Pass
  what one agent said to another, for example so Codex can review what Claude built, or so
  Claude can answer Codex's review. It sends the selected reply in the messages list, or the
  latest one if a message of yours is selected. Choose the chat it goes to: any of The Chat
  Place's own chats, Claude, Codex or Copilot, other than this one (desktop app, terminal and
  hidden chats aren't offered), or, at the end of the list, New Claude chat, New Codex chat or
  New Copilot chat (Copilot only with its preview on in Settings). A new
  chat opens New Session with the message as its first message, the folder of the chat the
  reply came from, and the title "Forwarded from" that chat; choose its permission and model
  there, then start it. The message starts with a line saying which agent wrote it and in
  which chat, so the agent reading it knows these are another agent's words, not yours. Read
  it and change or add to it in the Message box (Enter there starts a new line), then press
  Ctrl+Enter, or Enter in the list, to send it. The Chat Place goes to that chat and sends it
  as if you'd typed it: queued if that chat is working, and after Codex or Copilot has got the
  chat ready if it wasn't (going to another chat before then leaves it unsent, in that chat's reply box).
  If it can't be sent, it's left in that chat's reply box. While the chat it comes from is
  still working, the dialog's title says so: the reply may be a step on the way. The chat it
  came from is first in the list next time, so sending the answer back is Ctrl+Shift+E then
  Enter. It's also on a reply's menu in the messages list (Applications key). A forwarded
  message is only ever a message: it can't answer a question or approve anything, which still
  comes to you.
  - **Just send it, or start an agent conversation.** Under Send to, **Just send it**
    (Alt+J) is chosen; **Start an agent conversation with both chats** (Alt+C)
    instead makes the two chats an agent conversation (below), titled in **Agent conversation
    title** (Alt+I, the chat's title to start with). The reply goes as its first hand-off, the
    chat you sent it to answers first, and you approve each hand-off (change that with File,
    Agent Conversation Hand-offs). With a new chat, New Session comes first. It can't be done
    with a chat that isn't one of The Chat Place's own, one already in an agent conversation, or
    while either chat is still working: then the option's name ends with
    "unavailable" and why. If the label line of the message is changed, what's sent is your own
    words rather than a hand-off.

### Agent conversations

- **Agent conversations (File, New Agent Conversation, Ctrl+Shift+G).** Two of The Chat
  Place's own chats, any of Claude, Codex and Copilot (or two of one), talk to each other in
  one conversation, with you in it. Choose each agent's chat: one of yours, keeping its
  history, or a new Claude, Codex or Copilot chat (with its permission, in a folder you
  choose; Copilot only with its preview on in Settings), and give each a role if you like
  ("Build the fix", "Review each change; don't edit files"). Each new Codex chat offers four
  permission choices: read-only or edits in its folder, with human or automatic approval
  review, as in New Session. Templates keep human review unless you explicitly choose
  automatic review. A new Copilot chat has one choice, "Asks you before it changes files or
  runs commands": Copilot asks you about each edit and command, so a reviewer's role ("don't
  edit files") is up to it to keep, and to you when it asks. Kind of conversation fills in the roles, new chats'
  permissions and who goes first for a builder and reviewer, a second opinion, a debate,
  or plan and do. An optional third agent joins in turn: replies go from the first to the
  second, the second to the third, and back, and
  each agent is sent the replies it missed along with the one passed to it. Choose how
  replies go across, who answers first, and write the opening message. The conversation is
  one row in the session list (Provider "Claude + Codex", Kind "agent conversation"); its
  member chats leave the list, and File, Agent Conversation Members goes to any of them.
  Loaded, its messages list has every agent's messages in order, each starting with who said
  it ("Codex: ...", "You, to Claude: ...") and the relay's own lines ("Relay: Claude's reply
  passed to Codex.", with what went if you changed it on the way, and why the relay
  stopped). What you type goes to the agent whose turn it is; start it with @ and a
  name ("@Codex check the tests"), or the name and a comma ("Codex, what's broken?"), to
  choose, or "@all" for every agent. Each agent is told once, at
  the start, who else is in it, that their messages aren't yours, and to end its reply
  with DONE when they all agree the work is finished.
  - **Approve each hand-off** (the default): when an agent answers, you hear its reply, and
    it waits to go to the other agent. Usually you just type your next message: it goes to
    that agent with the waiting reply ahead of it. Ctrl+Shift+E is for passing the reply on
    without adding anything, or reading and changing it first. The row says a reply is
    waiting.
  - **Automatic**, once turned on in Settings: replies go across by themselves, up to a turn
    limit (6 exchanges unless you choose another, here or with File, Agent Conversation
    Hand-offs). Approving what an agent asks doesn't stop it; it pauses, with the last reply
    waiting for Ctrl+Shift+E, when you refused something or a request went unanswered. It
    stops when a turn fails, and you hear why; your next message goes back to that agent.
    Sending a message, or handing a reply on, starts it again with a fresh count.
  - In either mode, the relay finishes when an agent says DONE after the others have replied
    since your last message, or the replies start repeating. The last reply still waits for
    Ctrl+Shift+E (a reviewer's DONE comes with its findings), but the row doesn't need you
    for it.
  - A reply given before background work (a build, say) is handed on when it's given.
  - Writing to the agent a reply is waiting for sends that reply too, ahead of your words,
    and so does "@all". Writing only to the agent that sent it sets it aside (you hear so):
    you've carried on with that agent instead.
  - **Stop** (Ctrl+Period) stops every agent and the relay; a waiting reply isn't passed on,
    a message that hadn't gone comes back to the reply box, and your next message goes to
    the agent you stopped. **Turn Status**
    (Ctrl+Shift+T) says what each agent is doing, the count, and who your next message goes
    to. **Usage and Context** (Ctrl+Shift+U) lists each agent's context and each provider's
    limits (for a Copilot agent, the premium requests its chat has used).
  - The relay also stops when the agents are only thanking each other. File, Agent
    Conversation Hand-offs can make automatic mode say only when it stops or needs you, and
    asks first if Claude or Codex is near a usage limit. Copilot doesn't say how much of its
    allowance is left, so automatic hand-offs with a Copilot agent can't be checked: each of
    its turns uses premium requests from your plan.
  - The relay's own lines are one line each in the list; Enter reads exactly what was sent
    (the setup each agent got, or the reply as it went).
  - A reply passed on ends with the files its agent changed in that turn, so a reviewer
    knows what to look at. For Copilot, these are read from its log when the turn ends.
  - Questions and requests needing human approval come to you, from each agent, through Answer
    (Ctrl+Shift+A). Codex uses its selected approval reviewer; Copilot always asks you. A message from one agent can
    never answer or approve anything for the other. Rename (F2), Properties (Alt+Enter) and
    Delete (Shift+Delete) work on the row; deleting it keeps both chats and their histories
    and puts them back in the list. A member chat can't be deleted while it's in a conversation.

### Speech, announcements and notifications

- **Announcements.** When the open session gets a new reply, or one of The Chat Place's sessions
  finishes a turn, or any listed session stops working, it's announced through your screen reader
  (or a system voice when no screen reader is running) and put on the status bar. Settings (Ctrl+Comma) chooses full (the whole
  reply), summary (the session's name and the first sentence) or silent (status bar only), whether
  every listed session is announced or just the open one, and the speech route; its Reading
  messages group chooses whether full messages open as a formatted page or plain text.
  Ctrl+Shift+R repeats the last announcement.
- **Which voice speaks.** On Windows, Automatic (the default) speaks through JAWS or NVDA,
  whichever is running, in your screen reader's own voice and rate. NVDA needs nothing extra: The
  Chat Place ships NV Access's NVDA Controller Client. A system voice is used only when no screen
  reader is running in your Windows session. If one is running but can't be reached (or takes
  more than 3 seconds to answer), The Chat Place doesn't talk over it in another voice. Instead,
  the status bar shows the announcement with the reason beside it, a Windows notification says
  announcements aren't being spoken (unless notifications are off), and Settings shows a Speech
  problem box after the speaking rate. Each new reason is reported once, until speech works
  again. Nothing is spoken through a screen reader while Windows is locked. Choosing JAWS or NVDA in
  Settings only decides which is tried first when both are running. On a Mac, Automatic speaks
  through VoiceOver when it's running, else a system voice.
- **Which voice speaks on Linux.** Automatic, or Orca, speaks through Orca when it's running, in
  Orca's own voice and rate, on its braille display too. Orca 49 and later take every
  announcement through their D-Bus remote controller (`PresentMessage`), whatever window you're
  in. Orca 45.2 to 48 take them only from the active window, so The Chat Place sends them as
  notifications from its window while you're in it; while you're in another window, they're
  spoken by speech-dispatcher, the speech server Orca uses too. speech-dispatcher says one
  thing at a time, so that voice never talks over Orca, and Orca's next words cut it off. With
  Orca not running, Automatic is a speech-dispatcher voice; Settings lists the voices in your
  language, and the rate. Help, Report a Bug says which way the last announcement went.
- **Answers to what you do are spoken too**, briefly and without cutting off your screen reader:
  "Tool activity shown in Build.", "Message copied.", and so on (unless announcements are set to silent).
- **Your own message is read back when it's sent**, so you hear what actually went to the selected provider,
  and where: "Sent to Hub probe: Fix the build." During a turn it's "Queued for Hub probe: …",
  and when the queued message goes out you hear only "Sent your queued message", not the
  message again. It follows the announcement level: at full, the message up to about 300
  characters, then "… and 412 more words"; at summary, its first sentence; at silent, nothing.
  Markdown is read as words: a code block is "Code block omitted", and headings and list items
  are read as separate sentences. Turn it off in Settings with "Read your own messages back when
  they're sent" (Alt+M), and you hear just "Sent. Hub probe is working."
- **Notifications** while you're in another window: when one of The Chat Place's own
  sessions finishes a turn, fails or needs you (a permission or a question), when a desktop app
  session starts needing you, and when the loaded session finishes. Choosing one brings
  The Chat Place forward with that session loaded. Settings, Windows notifications (on a Mac
  and on Linux, Notifications) chooses every finished turn of its own sessions (the default), only when a
  session needs you, or off. While you're in The Chat Place (or one of its dialogs), the
  announcement is enough and none are shown.
  - On Windows they're ordinary Windows notifications from The Chat Place's icon in the
    notification area: read by your screen reader, and quiet under Do Not Disturb. Choosing the
    icon itself brings The Chat Place forward.
  - On a Mac they're macOS notifications, in Notification Center and under Focus like any other.
    macOS asks once whether The Chat Place may send them (System Settings, Notifications changes
    that). Each one remembers its own session, so choosing an older one still opens the right
    session.
  - On Linux they go to your desktop's notification server (GNOME's, KDE's, or any other that
    follows the freedesktop.org standard), which Orca reads. Choosing one opens its session, as
    on a Mac, where the notification server supports it.
- **The status bar** (F6, or Ctrl+9) has parts, as in QuickMail, and Left and Right move between
  them: what was last said, then the loaded session and what it's doing (read-only text), then
  buttons that appear when there's something to act on: how full the context is (Usage and
  Context), how many sessions need you (goes to the first), and an available update. Tab leaves
  the status bar. Your screen reader's read-status-bar key reads all the parts.

### Usage, Turn Status and Properties

- **Usage and Context** (Ctrl+Shift+U, View menu) opens a list to read with the arrow keys: first
  how full the loaded session's context is ("Quiet one: Context 62% full: 124,000 of 200,000
  tokens"), then each of your plan's usage limits on a line of its own. When something isn't known
  yet, the line says so. Copy (Alt+C), or Ctrl+C in the list, copies the selected line, and Copy
  All (Alt+A) copies every line; Escape or Close (Alt+L) closes it. The context comes from the token counts of Claude's latest
  reply (the percentage only once the window's size is known: Claude Code reports it for a model
  when one of The Chat Place's sessions runs a turn on it, and a session past 200,000 tokens has the
  1,000,000 window), and the limits are as the latest turn reported them ("5-hour limit 8% used,
  resets at 10:00 AM.", then "Weekly limit 34% used, resets on Friday at 9:00 AM.").
  It's said once, unasked, when the loaded session's context passes 80%, when a limit passes
  90% or is reached, and when Claude Code compacts a conversation (the messages list shows
  "Conversation compacted" too). A turn that fails on a usage limit says so in plain words,
  with when it resets.
  For a Codex chat, the same command reads current ChatGPT account limits in the
  background, including used and remaining percentages and reported reset times.
  Limits are shared across your Codex chats. Missing values are shown as not reported.
  Codex chats started here also show the latest reported model request's input, output
  and total tokens, separately from cumulative usage. When Codex reports its
  context window, the latest request's percentage appears here and in the status
  bar; at 80% The Chat Place warns once until the next compaction. After an owned Codex
  turn ends, The Chat Place checks account limits in the background and warns at 80% and
  100%, once per allowance and reset window, without interrupting read-back. These values become
  available only after this connection receives a usage report. Missing windows
  stay not reported; The Chat Place never substitutes Claude's counts or model defaults.
- **Turn Status** (Ctrl+Shift+T) says how long the provider has been working on the current turn and
  what it last did, and how far along the loaded chat's task list is when available. For Claude
  chats, it also reports background work still running. There's no
  time limit on a turn; Stop (Ctrl+Period) ends it, along with anything it started, such as a
  build.
- **Properties** (Alt+Enter, View menu, or the session's and the message's context menus; Option+Return
  on a Mac) opens a list of "Property: value" lines, read and copied like Usage and Context. In the
  session list it's the selected session; in the messages, the selected message; anywhere else,
  the loaded session. A session's lines are its title, provider, kind, folder, status, when it started and
  was last active, the model of its latest reply (for supported chats started here, the model, effort
  and permission mode chosen), how many messages, your messages, the provider's replies, tool calls,
  links, code blocks and changed files it has, how far along the task list is, how full its
  context is, how many times it was
  compacted, its groups, Remote Control, whether it's archived or hidden, its session and desktop
  app IDs where available. Claude chats also show the transcript file and size; Codex history
  comes through its API. Complete history is read for the message counts, so the counts
  don't depend on Show Tool Activity. A message's lines are who it's from, its date and time,
  its position in the list ("12 of 40"), its lines, words and characters, its links and code
  blocks, and its ID.

### Organizing and exporting

- **Rename a session** (F2, File, Rename Session, or the session's menu). One of The Chat
  Place's own sessions is renamed outright. A desktop app session's new name shows only in The
  Chat Place, since its files are never changed: it's kept in
  `%APPDATA%\TheChatPlace\titles.json` (on a Mac,
  `~/Library/Application Support/TheChatPlace/titles.json`), and the desktop app keeps its own
  name for it. Renaming
  it to nothing (clear the box) goes back to the desktop app's name.
- **Hide or delete a session.** **Delete** (File, Hide Session) takes the selected session out of
  the list without changing it; View, Show Sessions, Hidden lists hidden sessions, and File, Bring
  Back Session returns one. **Shift+Delete** (File, Delete Session Permanently) deletes one of The
  Chat Place's own sessions for good, from any view, hidden or not: after you confirm (No is the
  default), its Claude Code transcript is deleted (permanent deletion is currently supported for Claude chats) from the computer and it leaves the list, its
  groups and the hidden list. It waits while the session is working, here or in a terminal.
  Desktop app sessions can only be hidden; delete those in the desktop app.
  On a Mac laptop these are Fn+Delete and Fn+Shift+Delete (the forward-delete key).
- **Groups**, like groups in Claude on the web: **Ctrl+G** (File, Add to Group) puts the
  selected session in a group, or a new one; File, Remove from Group takes it out; File,
  Manage Groups lists your groups with how many sessions each has, to make, rename or delete
  them (deleting a group never touches its sessions). A session can be in several groups, and
  its row says which ("…, group Work"). The Chat Place's groups are kept in
  `%APPDATA%\TheChatPlace\groups.json` (on a Mac,
  `~/Library/Application Support/TheChatPlace/groups.json`); the desktop app's files are never
  changed.
  The **desktop app's own groups** show too: in View, Show Sessions, in each session's row,
  and in Add to Group. They're read from the desktop app's settings each refresh, so a change
  there shows here; they're changed in the desktop app. Adding one of The Chat Place's sessions
  to a desktop app group keeps a Chat Place group of the same name, shown together with it.
- **Export** (Ctrl+E, File, Export Session) saves the selected session's conversation,
  or the loaded one's, to a file: **Markdown**, a **web page** (formatted like the full-message
  view, nothing fetched from the internet) or **plain text**, chosen by the Save as type list
  or the file name's extension. Each message is a heading ("Claude, 10:43") and its text as
  written, with the message's own headings moved down two levels so the file's outline stays
  session, message, section. Tool activity is included when Show Tool Activity is on. It
  starts in Documents, named after the session and the date, works in the background (a long
  session never freezes the window), and says where it saved; the status bar keeps that to read
  back. Headings carry the time, and the date whenever the day changes.

### Links, Remote Control and other computers

- **Copy Session Link** (Ctrl+Shift+L, File menu, or the session's menu) copies a link to the
  selected Claude session (not yet for Codex or Copilot chats), to paste into a note, an email or another session. A list offers the links that
  apply, best first; Enter on the first copies a Markdown link, `[Title](thechatplace://session/…)`,
  that opens the session in The Chat Place. Below it are a desktop app session's Open in Claude link
  (`claude://…`) and, for one of The Chat Place's sessions on Remote Control, its claude.ai address,
  each as a Markdown link and then as the bare link. What was copied is said. Following a
  `thechatplace://` link (from a note, Win+R or Terminal's `open`, a link in a message's formatted
  view) brings The Chat Place forward, starting it if it isn't running, selects the session, loads
  its messages and puts you in them, as choosing it yourself does. If the list's view or search
  leaves the session out, it shows all sessions (or Archived or Hidden) and says so. A link only
  ever finds a session, as Enter on it does: it never sends a message, runs a turn or changes a
  conversation, and anything that isn't exactly a link to a session gets "That link isn't one The
  Chat Place knows." A session's link uses the id that lasts: the Claude Code session id for The
  Chat Place's own sessions, the desktop app's `local_…` id for its Code and Cowork sessions. On
  Windows the installed copy registers `thechatplace://` for your user account
  (`HKEY_CURRENT_USER\Software\Classes\thechatplace`) when it's installed and at start, and
  uninstalling removes it; the portable copy and a source run never register. On a Mac the app
  declares the link type itself. When The Chat Place is already running, the copy a link starts
  hands the link over (as a small file in its own data folder, `links`, taken within a second) and
  closes.
- **Remote Control.** Settings can turn on Remote Control for The Chat Place's sessions, and
  **File, Remote Control** turns it on or off for one session (or back to the Settings
  default). A session on Remote Control can be reached from claude.ai and your other devices
  while one of its turns is running: each turn is its own Claude Code process, so between turns
  it shows as offline. The same Remote Control session is joined every turn, and File, Remote
  Control copies its claude.ai address. Turning it on copies the conversation to claude.ai,
  where it stays (turning Remote Control off, or deleting the session, doesn't remove it;
  archive it on claude.ai). While a turn runs, anyone signed in to your account on claude.ai
  can type into it or answer its questions; a question answered there is cleared here.
  It's also on the session's menu (Applications key). A session on Remote Control says so in
  its row and heading, and View, Show Sessions, Remote Control Sessions lists it (with only the
  Settings default on, a Chat Place session's row says so once it has connected). For a Claude
  desktop app session, File, Remote Control offers to open it in the desktop app, where its
  Remote Control is turned on, or to continue it here as a copy with Remote Control on from the
  first turn; The Chat Place never changes the desktop app's session itself.
- **Other machines (File, Other Machines, Ctrl+Shift+M).** Your Claude Code sessions on
  your other computers, reached through Claude in the loaded Chat Place session, which needs
  Remote Control on (as do they). It lists them from Claude's latest look: their names and
  whether each is idle, busy or offline. "Offline" often only means between turns, and a
  message waits for it. **Refresh the list** asks Claude to look again, as a turn in the
  session, and the list is there next time you press Ctrl+Shift+M. Choose a session, type
  your message, and Claude sends it word for word; its reply comes back to the same session.
  Only Claude can reach other computers, and only what they send back can be shown: their
  own conversations can't be read from here. It works between turns, not during one.
- **Messages from other sessions.** Claude can also send a message to another Claude Code
  session when you ask in your own words, for example "send the session called Mac Hub a
  message asking for its hostname". A message that comes back shows in the messages list as **From** and the
  session's name ("From Mac Hub: ..."), not as something you typed, and it's announced in
  any open session. Other sessions reach a Chat Place session by its title, which stays the
  same every turn, so rename it to something they can find. A Chat Place session only acts on
  a message during one of its turns, so a message that arrives between turns waits for the
  next one. The other computer's own conversation can't be read from here; only what its
  Claude sends back.

### What AI knows about you

- **What AI knows about you** (Ctrl+Shift+K, View, What AI Knows About You). Choose **Claude**
  or **Codex** in the Provider list (Alt+V). This is an inventory of local customization files,
  not an account-wide memory report or a list of everything active in a particular turn. Pick a
  kind, arrow through its items (each with its description), and Enter reads the file in a box
  you can move through by line. **Edit in Your Editor** opens the file in your own editor (on
  Windows, the program set to edit that kind of file, or Notepad); The Chat Place itself never
  writes to these files. Reload shows your changes; Show in Folder and Copy Path (Alt+P) find
  the file. While a provider's files are being read the lists say "Reading…", and if they can't
  be read they say so.
  - **Claude:** Claude Code keeps what it knows about you in plain files, and this lists them by
    kind: your instructions (`CLAUDE.md`, yours and each project's), the memories Claude saved
    for each project, your skills (including ones synced from your account), subagents, slash
    commands, output styles and settings. It includes the project-level ones in the folders your
    sessions work in. Not listed yet: what plugins add, `CLAUDE.md` files in a project's parent
    folders, `.claude/rules`, and an organization's managed settings.
  - **Codex:** `AGENTS.md` and `AGENTS.override.md`, Markdown files under its `memories` folder
    (not the summaries of past chats it keeps in `memories/rollout_summaries`), skills in your
    `.agents/skills` and Codex home's `skills` (including `.system`), saved `prompts`, and
    `config.toml`. It honors `CODEX_HOME` and includes instruction files, `.agents/skills` and
    `.codex/config.toml` in the folders your Codex sessions work in. It never reads Codex's
    sign-in, history or chat files. Parent folders, configured alternate instruction filenames,
    plugins and managed settings are not included; project configuration may require trusting
    the project in Codex.

### Sign-in, the user guide and bug reports

- **Claude Code Sign-in** (Help menu) says whether Claude Code is signed in, to which plan and
  as whom ("Claude Code is signed in to your Claude Max plan as …"). If it isn't, it offers to
  sign in: `claude auth login` opens in its own window (a Terminal window on a Mac) and your
  browser shows Claude's sign-in page; when you've finished, The Chat Place checks again and
  says the result. If Claude Code isn't installed, or only an old npm install is, it says so,
  gives the install command, and offers to run Claude Code's native installer in its own
  window, then checks again once it's done. If Claude Code is there but can't be asked, it says
  why: no answer in 20 seconds, couldn't be started, too old (`claude update` fixes that), or
  what it printed. The Chat Place also checks at start-up and says so only if there's a problem.
  Your sessions are listed whether or not Claude Code is signed in (they're read from disk);
  only sending a message needs a sign-in.
- **Codex Sign-in** (Help menu) says whether Codex is signed in to ChatGPT. If it isn't, or is
  using another credential such as an API key, it offers to run `codex login` in its own window;
  your browser shows ChatGPT's sign-in page, and The Chat Place checks again when it finishes.
  If Codex isn't installed or its status can't be read, the same item explains what is needed.
- **Copilot Sign-in** (Help menu) says whether GitHub Copilot CLI is installed and which GitHub
  account it's signed in to, as Copilot CLI records it in its own settings file; it never starts
  Copilot or sends a message. If it isn't signed in, it offers to run `copilot login` in its own
  window, with the environment the app's own Copilot chats get (no `GH_TOKEN` or other token
  variables). Follow its steps, which may give you a code to enter on GitHub's sign-in page;
  The Chat Place checks again when it finishes. If Copilot CLI isn't installed, it gives the
  install command, and says when the only copy found is an npm install, which isn't used. When
  Copilot refuses because it isn't signed in, a chat says so and points here, instead of
  "Copilot couldn't do that."; the message it refused isn't sent again, and messages still
  waiting come back to the message box, unsent.
- **User Guide** (Help menu, Alt+H, G) shows the user guide as a page, read by heading like the
  keyboard shortcuts, or as plain text (Alt+P). It's part of the app, so it works offline. It's
  edited in `thechatplace/assets/user-guide.md`; `python tools/make_docs.py` makes the copies in
  `docs/`.
- **Report a Bug** (Help menu) asks for a summary, what happened, what you expected and the
  steps, and shows exactly what else the report includes before it goes anywhere: versions
  (The Chat Place, Windows, Python, wxPython, Claude Code), the announcement level and speech
  engine, what happened to the last announcement (which screen reader spoke it, or why none
  did), the list's view and sort, and how many sessions it lists. Never a session's title,
  folder or messages. **Open on GitHub** copies the whole report and opens GitHub's new-issue
  page with it filled in; **Copy Report** only copies it. Filing on GitHub needs access to the
  repository; anyone else can Copy Report and email it to support@theideaplace.net.

## Install

You need Windows 10 or 11, a Mac with Apple silicon, or a Linux PC (x64 or arm64, a preview; see
**Linux** below). For Codex chats, you need a native Codex CLI signed in to ChatGPT. Claude Code is not required for Codex chats.

For GitHub Copilot CLI chats (a preview), you need the native GitHub Copilot CLI
(`winget install GitHub.Copilot` on Windows, `brew install copilot-cli` or GitHub's install
script on a Mac), signed in with `copilot login` to a GitHub account that has Copilot. An npm
install (`npm install -g @github/copilot`) is never used. Then turn the preview on in Settings.

If something isn't working, the user guide's
[Troubleshooting](https://theideaplace.github.io/AIChat/user-guide.html#troubleshooting) section
covers the usual problems with each provider, speech and the session list.

For Claude chats, you need **Claude Code installed with its
native installer and signed in** to a Claude subscription (the `claude` command, the same login
the desktop app uses). The Claude desktop app's own copy of Claude Code isn't on the PATH and
isn't meant for other programs, so install the command as well, from a terminal:

- Mac and Linux: `curl -fsSL https://claude.ai/install.sh | bash`
- Windows PowerShell: `irm https://claude.ai/install.ps1 | iex`
- Windows Command Prompt:
  `curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd`

Then run `claude` once to sign in. Help, Claude Code Sign-in can run the installer and the
sign-in for you. Homebrew (`brew install --cask claude-code`) and WinGet
(`winget install Anthropic.ClaudeCode`) installs work too.

**Windows.** Download `TheChatPlace-windows-Setup.exe` from the newest release on
[the releases page](https://github.com/TheIdeaPlace/AIChat/releases) and run it.
It installs for you only, with no administrator rights, adds The Chat Place to the Start menu, and
starts it. The portable zip from the same release runs without installing, but doesn't update
itself.

The app is built for x64; Arm PCs run it under Windows's x64 emulation.

**Mac.** Download `TheChatPlace-macos-arm64.dmg` from the same release, open it, and drag
The Chat Place onto Applications (or anywhere you like: it updates itself wherever it is). The
disk image is signed and notarized, so it opens without a warning. The first time speech goes through VoiceOver, macOS asks whether The Chat Place may
control VoiceOver; allow it, and turn on "Allow VoiceOver to be controlled with AppleScript" in
VoiceOver Utility, General. Your sessions and settings are in
`~/Library/Application Support/TheChatPlace`.

**Linux** (a preview). Download `TheChatPlace-linux-x64.AppImage` (most PCs) or
`TheChatPlace-linux-arm64.AppImage` (Arm PCs and boards, such as the MNT Reform and Pocket
Reform) from the same release, put it where you keep programs (`~/Applications`,
say), make it executable, and run it:

```
chmod +x ~/Applications/TheChatPlace-linux-arm64.AppImage
~/Applications/TheChatPlace-linux-arm64.AppImage
```

The first start adds The Chat Place to your desktop's menu (a desktop entry in
`~/.local/share/applications`) and makes it the handler for `thechatplace://` links. Your sessions
and settings are in `~/.config/TheChatPlace`.

It needs a Linux at least as new as Ubuntu 24.04, Debian 13 or Fedora 40, with GTK 3 (any
desktop has it), and these, which most desktops have and the rest install in one line:

- **FUSE 2**, which runs AppImages: `sudo apt install libfuse2t64` on Debian 13 and Ubuntu 24.04
  (`libfuse2` on older ones), `sudo dnf install fuse-libs` on Fedora. Without it, an AppImage
  still runs with `--appimage-extract-and-run`, but slower to start.
- **Orca** (45.2 or later; 49 or later reads announcements from the background) and
  **speech-dispatcher**: `sudo apt install orca speech-dispatcher`.
- For formatted full messages (off by default on Linux; see Limitations), **WebKitGTK 4.1**:
  `sudo apt install libwebkit2gtk-4.1-0`.

Anthropic makes no Claude desktop app for Linux, so the Claude sessions listed there are the ones
you started by typing `claude` in a terminal and The Chat Place's own. Codex and GitHub Copilot
CLI chats are listed as on Windows and the Mac.

Claude Code's sign-in (Help, Claude Code Sign-in) opens a web page. Without a web browser, which
a server install often lacks, the terminal shows only the link: open it on another computer or
phone, or install a browser (`sudo apt install firefox-esr` on Debian).

**Uninstalling.** On Windows, uninstall the installed copy from Settings, Apps, Installed apps (Apps & features on Windows 10);
delete the portable copy's folder. On a Mac, drag The Chat Place to the Trash. On Linux, delete
the AppImage and its desktop entry, `~/.local/share/applications/thechatplace.desktop`. Your settings and
records stay in `%APPDATA%\TheChatPlace` (on a Mac, `~/Library/Application Support/TheChatPlace`;
on Linux, `~/.config/TheChatPlace`)
until you delete them, and your Claude, Codex and Copilot chats are never removed. The user
guide's [Uninstalling](https://theideaplace.github.io/AIChat/user-guide.html#uninstalling)
section lists everything that's left behind.

## Updates

The installed app checks for a new version a few seconds after it starts, and whenever you choose
Help, Check for Updates. At start the check only speaks up when there is a new version, and then
only says so: it never asks you anything you didn't ask for. From Help it always says what it found ("up to
date", "no release has been published yet", or an error), even with announcements set to silent.

The Help menu's update item always says which version you're running, as QuickMail's does:
**Check for Updates (running 0.1.4)...** (Alt+H, U). Once a check, at start or from Help, finds a
newer version, the item becomes **Update to 0.1.5 Available...** (still Alt+H, U), and choosing it
offers the update. If a later check finds nothing newer, it goes back to the running version; a
check that fails leaves it as it was.

**After an update.** The first time the updated app starts, a small dialog, **The Chat Place
Update Installed**, says "The Chat Place was updated to 0.1.5." Focus starts on **See what's new
in 0.1.5** (Alt+W), which opens that version's release notes on GitHub
(`https://github.com/TheIdeaPlace/AIChat/releases/tag/v0.1.5`) and closes the dialog; **Close**
(Alt+C) or Escape just closes it. It waits until the main window is in front with no other dialog
open (a warning about a damaged file, say), so it never lands on top of another one; if that
doesn't happen within half an hour, the status bar says it instead. It's shown only by an
installed copy, only when the version is newer than the one that last started (recorded as
`last_run_version` in `speech.json` in the data folder), and never on a first run, after a
downgrade, from the portable zip or from source. Turn it off in Settings with "Tell me when an
update has been installed, with a link to what's new" (Alt+T).

From Help, a new version is offered in a Yes/No dialog where No is the default. If you choose Yes,
it downloads the update, closes, and starts the new version. It won't install while any provider is
working in one of its sessions, and it warns you if a reply box holds text you haven't sent. If a
turn starts, a dialog opens, or you type a reply while it downloads, it asks again or leaves the
update to be installed the next time The Chat Place starts.

The Mac app updates the same way. Velopack's updater is inside the app itself, so it works
wherever you keep it. So does the Linux AppImage: an update replaces the `.AppImage` file where
it is (asking for your password, through pkexec, only if it's in a folder you can't write to).

**Updating never touches your sessions or settings.** On Windows the app lives in
`%LOCALAPPDATA%\TheChatPlace`, which Velopack replaces on update and removes on uninstall, and
your data is in `%APPDATA%\TheChatPlace`. On a Mac, Velopack replaces the app itself, and your
data is in `~/Library/Application Support/TheChatPlace`. On Linux, Velopack replaces the
AppImage, and your data is in `~/.config/TheChatPlace`. The updater refuses to run if the data
were ever inside what it replaces. What the updater did is logged in `update.log` in the data
folder.

The version is in Help, About.

## Keyboard shortcuts

The same list is in the app under Help, Keyboard Shortcuts (F1), as a page with a heading and a
table for each group, so you can move by heading and read each key with what it does. Alt+P
shows it as plain text instead.

| Where | Key | What it does |
|---|---|---|
| Anywhere | Ctrl+Shift+S | Find in All Sessions: search every listed session's messages, then load one on the message found |
| Anywhere | Tab, Shift+Tab | Session list, messages, reply box, and back |
| Anywhere | Ctrl+1, Ctrl+2, Ctrl+3 | Go to the session list, the messages, the reply box |
| Anywhere | F6, Shift+F6 | Next or previous part: session list, messages, reply box, status bar |
| Anywhere | Ctrl+9 | Go to the status bar; Left and Right move between its parts |
| Messages, reply box or status bar | Escape | Back to the session list, on the same session |
| Messages | Backspace | Also back to the session list |
| Session list | Enter | Load that session and move to its messages |
| Session list | Ctrl+O | Open the selected session in the Claude desktop app |
| Session list | Ctrl+Shift+L | Copy Session Link (Claude chats): a Markdown link that opens it in The Chat Place, or the other links |
| Anywhere | Ctrl+N | New Chat Place session |
| Session list | Ctrl+G | Add the selected session to a group |
| Session list | F2 | Rename the selected session |
| Session list | Applications key, Shift+F10 | The session's menu |
| Anywhere | Ctrl+Shift+K | What AI knows about you: choose Claude or Codex; local instructions, memories, skills and more |
| Session list | Ctrl+F | Show only sessions matching some text (Escape shows all) |
| Messages | Ctrl+F, F3, Shift+F3 | Find a message by its text; next; previous |
| Anywhere | Ctrl+E | Export the session to a file |
| Reply box | Ctrl+/ | Insert a slash command or skill |
| Anywhere | Ctrl+Shift+P | Prompts: use, add, edit, reorder or delete your saved prompts |
| Anywhere | Ctrl+Shift+F | Attach files or images to the next message |
| Anywhere | Ctrl+Shift+M | Other machines: list your sessions on other computers and message one |
| Anywhere | Ctrl+Shift+E | Send Reply to Another Chat: pass the selected (or latest) reply to another Chat Place chat, Claude, Codex or Copilot, or a new one; in an agent conversation, hand the waiting reply on |
| Anywhere | Ctrl+Shift+G | New Agent Conversation: two chats that pass replies to each other |
| Anywhere | Ctrl+Shift+I | Lists: code blocks, links, changed files, commands, decisions and tasks, for this message or the whole chat |
| Anywhere | Ctrl+Shift+D | Lists, on Changed Files: each change to read by line |
| Anywhere | Ctrl+Shift+U | Usage and context: a list of how full the context is, and each plan limit |
| Anywhere | Alt+V, H | Show Sessions: choose which sessions are listed |
| Anywhere | Alt+V, E | Session List Columns: choose what each session's line says, and in what order |
| Anywhere | Ctrl+Shift+N | Continue the selected (or loaded) desktop app or terminal session here, as a copy |
| Anywhere | Ctrl+Shift+O | Change Permission Mode for the selected (or loaded) Chat Place session, from its next turn |
| Anywhere | Ctrl+Shift+H | Chat Permissions: the selected (or loaded) Chat Place chat's permission mode and kept rules, to read and remove |
| Session list | F5 | Refresh the list now and put it in order |
| Session list | Delete | Hide the selected session (View, Show Sessions, Hidden lists it; File, Bring Back Session returns it) |
| Session list | Shift+Delete | Delete a Claude chat started here permanently, after you confirm; on an agent conversation, delete the conversation and keep its chats |
| Messages | Enter, or Applications key / Shift+F10 then Read Full Message | Read the whole message; Escape comes back to it |
| Messages | Ctrl+C | Copy the whole message |
| Messages | Ctrl+Shift+B | Lists, on Code Blocks for this message: read and copy each one |
| Messages | Ctrl+L | Lists, on Links: every link in the chat, to open in your browser or copy (also from the session list and the reply box) |
| Messages | Ctrl+Shift+J | Lists, on Tasks: the task list, each step to do, in progress or done |
| Messages | Ctrl+Shift+C | Copy the message's last code block |
| Messages | Ctrl+T | Show or hide tool activity in the loaded session |
| Messages | Ctrl+O | Open this session in the Claude desktop app |
| Reply box | Ctrl+Enter | Send (Chat Place sessions only); you stay in the reply box |
| Reply box | Ctrl+Shift+Enter | Send, and save the message as a prompt (asks for its name first) |
| Reply box | Ctrl+Alt+Enter | Steer: during a turn, send the message into it without stopping it |
| Reply box | Ctrl+Period | Stop the running turn |
| Reply box | Ctrl+Shift+T | Turn status: how long it has been working, on what, what is still running in the background, and how far along the task list is |
| Anywhere | Ctrl+Shift+A | Answer: a provider's permission request, a question or a supported plan |
| Anywhere | Alt+Enter | Properties of the selected session, or in the messages the selected message |
| Anywhere | F1 | Keyboard shortcuts |
| Anywhere | Alt+H, G | The user guide |
| Anywhere | Ctrl+Comma | Settings |
| Anywhere | Ctrl+Shift+R | Repeat the last announcement |
| Anywhere | Alt+F4 | Quit |

Menus are File (Alt+F), View (Alt+V) and Help (Alt+H). Controls have their own Alt letters
(Alt+L the session list, Alt+M the messages, Alt+Y the reply box, Alt+D Send), and none of them
takes a menu's letter.

On a Mac, Ctrl is the Command key, the menus are in the menu bar (VO+M) rather than on Alt, and
Settings is in the app menu.
On Linux, Alt opens the menu bar as on Windows, and so does F10, as in other GTK programs.

## How it works

Everything it reads is on your own computer, so reading costs nothing.

| What | Where |
|---|---|
| The desktop app's sessions | `%APPDATA%\Claude\claude-code-sessions\<id>\<org>\local_<id>.json` (on a Mac, `~/Library/Application Support/Claude/claude-code-sessions/<id>/<org>/local_<id>.json`): title, folder, last activity, archived, and sometimes a summary of the last turn that says whether it needs you. Archived sessions are left out. The Microsoft Store version of the desktop app keeps the same files in `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude-code-sessions`; both places are read, and a session in both is read from the newer copy. |
| The desktop app's Cowork sessions | `%APPDATA%\Claude\local-agent-mode-sessions\<id>\<org>\local_<id>.json` (on a Mac, `~/Library/Application Support/Claude/local-agent-mode-sessions/<id>/<org>/local_<id>.json`), and the same under the Store version's package folder. The `local_<id>` folder beside each is that session's own Claude Code home: its conversation is in `.claude\projects\<folder>\<session>.jsonl` and its busy or idle file in `.claude\sessions`, not in `%USERPROFILE%\.claude` (on a Mac, `~/.claude`). Those paths are longer than Windows' usual 260-character limit, so on Windows they're read with the `\\?\` long-path form. |
| Terminal sessions | Every `%USERPROFILE%\.claude\projects\<folder>\<session>.jsonl` (on a Mac, `~/.claude/projects/<folder>/<session>.jsonl`) that no desktop app or Chat Place session claims and whose records say Claude Code was started interactively (`"entrypoint": "cli"`, where `claude -p` writes `sdk-cli` and the desktop app `claude-desktop`). Its folder and title come from the transcript too. Each is read once, at its start and its last megabyte, and then only what's added to it. |
| Whether a session is working | `%USERPROFILE%\.claude\sessions\<pid>.json` (on a Mac, `~/.claude/sessions/<pid>.json`), which says busy or idle while Claude Code runs it. Files whose process has gone are ignored. |
| The conversation | `%USERPROFILE%\.claude\projects\<folder>\<session>.jsonl` (on a Mac, `~/.claude/projects/<folder>/<session>.jsonl`), where `<folder>` is the session's folder with every character that isn't a letter or digit turned into `-`. If it ever misses, the app searches all the project folders for the session id instead. |
| Saved Codex task lists | `%APPDATA%\TheChatPlace\provider-tasks\codex\<sha256>.json` (on a Mac, `~/Library/Application Support/TheChatPlace/provider-tasks/codex/<sha256>.json`): the latest task list captured for each owned Codex chat, with its turn ID. The Chat Place can't delete Codex chats yet, so these files stay until you delete them. |
| The Chat Place's own sessions | `%APPDATA%\TheChatPlace\sessions.json` (on a Mac, `~/Library/Application Support/TheChatPlace/sessions.json`), and `speech.json` for settings. If `sessions.json` can't be read, it's renamed to `sessions.json.bad-<date>` rather than overwritten, and the app says so. |
| Your saved prompts | `%APPDATA%\TheChatPlace\prompts.json` (on a Mac, `~/Library/Application Support/TheChatPlace/prompts.json`). If it can't be understood, it's renamed to `prompts.json.bad-<date>` rather than overwritten, and the app says so. |
| Errors | `%APPDATA%\TheChatPlace\error.log` (on a Mac, `~/Library/Application Support/TheChatPlace/error.log`): anything that went wrong unexpectedly, with its traceback |

Copilot CLI sessions are read from `%USERPROFILE%\.copilot\session-state\<id>\` (`~/.copilot/session-state/<id>/` on a Mac)
(`workspace.yaml` and `events.jsonl`), and only `thechatplace/providers/copilot.py` knows
their format.

Claude sessions stay in `sessions.json`. Codex sessions and their settings use
`provider-sessions.json`; their groups, hidden state, custom names and Show Tool
Activity preferences use separate `provider-*.json` files. Older versions retain
their Claude data without reading or rewriting Codex preferences.

None of these formats is documented, and Claude Code says the transcript format changes between
versions. So all the knowledge of it is in one small module (`thechatplace/transcript.py`) that
skips record types it doesn't know, never crashes on a line it can't read, and says "couldn't read
N lines" instead. A long transcript is read once, then only its new lines as it grows.

**The Chat Place never writes to the desktop app's files or to any transcript.** Its own settings
and records are in `%APPDATA%\TheChatPlace` (on a Mac, `~/Library/Application Support/TheChatPlace`). It also writes its speech files and `speech.log` in
`%TEMP%\thechatplace-speak` (`$TMPDIR/thechatplace-speak` on a Mac, `$XDG_RUNTIME_DIR/thechatplace-speak` on Linux) and, on Windows, the formatted
view's WebView2 profile in `%LOCALAPPDATA%\TheChatPlace WebView2`; the installed Windows copy
registers `thechatplace://` links for your account each time it starts. Otherwise it writes only
when you ask: exports go where you save them, From GitHub clones into your GitHub folder, a new
worktree goes in the repository's `.claude\worktrees` folder, and Delete Session Permanently
deletes a Claude session's transcript. Providers save the messages you send, as they always do.
Only one copy runs at a time; starting a second brings the first to the front.

### Its own sessions, and why desktop sessions are read-only

The Chat Place drives its own sessions with the `claude` command in print (headless) mode:
`claude -p --input-format stream-json --output-format stream-json --verbose --permission-mode <mode>
--permission-prompts host --permission-prompt-tool stdio`, with `--session-id` and `--name` for the
first message and `--resume <id>` for each reply, plus `--allowedTools` with any rules you chose
"for this chat". The message goes in on standard input as a stream-json message, and standard
input stays open for the turn so The Chat Place can answer what Claude asks; it's closed when the
turn's result arrives. That runs under the same login as the desktop app, never an API key; it
uses the plan's Agent SDK credit, as any `claude -p` does.

- It never uses `--bare`, which needs an API key.
- It needs the native `claude.exe`. The npm install's `claude.cmd` is never run, because Windows
  runs a `.cmd` through `cmd.exe`, which would let characters in a session title run a command.
  A newer npm install's `claude.cmd` only starts the same native `claude.exe` from
  `node_modules`, so that program is run directly instead; an old one that runs `cli.js` through
  Node is refused. Off the PATH, it looks in `~/.local/bin` (the native installer) and WinGet's
  `Links` folder on Windows, and in `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` and
  `~/.npm-global/bin` on a Mac, where an app started from the Finder doesn't get your shell's
  PATH.
- Before starting `claude` it removes two named sets of environment variables, and keeps the rest
  (your `CLAUDE_CODE_GIT_BASH_PATH`, `CLAUDE_CONFIG_DIR`, proxy and timeout settings). The first set
  is what a Claude session puts in the environment of anything started inside it: started from
  there, the app would otherwise pass on variables that point `claude` at the desktop app's local
  proxy and tell it someone else will refresh its sign-in, and the run then waits for a refresh
  that never comes. The second is anything that would move billing off the subscription:
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and the Bedrock, Vertex and
  Foundry switches. Both lists are in `claude_cli.py`.
- As a second check, `claude` reports where its credentials came from before it sends anything.
  If that's an API key rather than the subscription login, The Chat Place stops the run and says so.

Desktop app sessions are read-only because two programs resuming one session run their turns into
the same transcript at once and tangle it. Only the desktop app writes to its sessions, and only
The Chat Place writes to its own. The rule is enforced in code: The Chat Place refuses to build a
`--resume` command for any session it didn't start, any session the desktop app knows about
(archived ones included), or any `local_` id, and there are tests for each. It also won't send
into one of its own sessions while that session is running somewhere else. Continue Here is the
one exception that reads a desktop session through `claude`, and it doesn't write to it:
`--resume <desktop id> --fork-session --session-id <new id>` copies the history into a new
session. Checked with Claude Code 2.1.286: the desktop session's transcript was byte for byte the
same afterwards, and Claude knew the earlier conversation. One turn runs at a time
per session. Send during a turn queues the message: The Chat Place says "Queued", and once the
turn's reply has been announced, it sends the message. More messages sent while one is waiting
join it, and they go together as one message. Each queued message is at the end of the
messages list as "Queued: …": Delete removes it, and its context menu has Send Now (Ctrl+Enter:
Claude stops what it's doing and answers it straight away, in the same turn), Edit Queued Message
(back into the message box, to change and send again) and Remove Queued Message. Turn status
(Ctrl+Shift+T) says when a message is queued. If the turn fails, or you press Stop, the queued text goes back in the message box
instead, ahead of anything typed since, with the cursor left at the end. While one of
The Chat Place's sessions is loaded, Send and Stop are never disabled, so from the message box, Tab
is always Send and the next Tab is always Stop. When a turn is running, Tab doesn't jump past
Send to Stop.

### Steering a running turn

Steering sends a message into the turn that's running, without stopping it. The AI takes it in
at its next step and carries on with it in mind: "also update the README", "use the other test
file", "don't touch that folder". Type it in the reply box and press **Ctrl+Alt+Enter**
(Cmd+Option+Return on a Mac), or choose File, Steer: Send into the Running Turn. On a queued
message in the messages list, Ctrl+Alt+Enter (or Steer on its context menu) steers that message
instead of waiting. With no turn running, Ctrl+Alt+Enter simply sends, as Ctrl+Enter does.

- **Claude chats:** The Chat Place says "Steering" and the chat's name (reading your message back
  as it does for Send), then "has your message" once Claude takes it in. If Claude had already
  finished its reply, that reply is read, then that Claude is answering your message, straight
  after, in the same turn. A steered message that the turn ends (or Stop stops) before Claude
  takes it in comes back to the reply box, never lost or sent twice. Attached files go as their
  paths, as for a queued message.
- **Codex chats:** Codex is asked to take the message into the turn, and The Chat Place says
  "Steered" once Codex accepts it. If the turn has just ended, or Codex is still taking an
  earlier steered message, the message comes back to the reply box (a queued one stays queued)
  with the reason. If Codex doesn't confirm it (an error, or the connection is lost), the message
  comes back to the reply box, even a queued one, with a warning to check the chat's history
  first, since Codex may have it. It is never sent as a new turn on its own.
- **Copilot chats** can't take a message during a turn yet: Ctrl+Enter queues it.

A steered message is at the end of the messages list at once, as "Steering, waiting for Claude:
…" (or Codex), and the row says where it is as that changes ("taken in by Claude", "accepted by
Codex"), until the chat's own history shows it in its place. Turn status (Ctrl+Shift+T) and the
turn status line say when a steered message hasn't been taken in yet. Send Now (Ctrl+Enter on a queued message) is unchanged: it stops what Claude is doing and
answers the message instead.

Every announcement is also written to `speech.log` in `%TEMP%\thechatplace-speak` (on a Mac,
`$TMPDIR/thechatplace-speak`; on Linux, `$XDG_RUNTIME_DIR/thechatplace-speak`), one line each: the time, whether it interrupts or waits its turn,
the speech engine, and its opening words. On Windows, with a screen reader setting, a `route:` line
usually follows it: "spoke through NVDA", "no screen reader running, so a Windows voice", or why a running
screen reader didn't speak. A line means the text was handed to the speech engine,
not that it was heard: a later announcement that interrupts can cut it off. The log shows
whether a reply went to the screen reader at all when you didn't hear it.

A turn runs `claude` in a Windows job object, so Stop, or quitting the app, ends `claude` and every
program it started (a build or test run, say), not just `claude` itself. The same happens when a
turn finishes. Background commands are the exception: while Claude Code reports a command it ran
in the background still running (a build, or `gh pr checks --watch`, say), the turn stays open
even after Claude answers, because Claude Code starts a turn of its own when the command finishes,
and ending the turn would kill it and leave nothing to pick it up (#161). The answer is announced
at once with what is still running ("Still running in the background: Windows build"), the
session's row says "answered, background work running", and the turn ends when Claude goes idle
with nothing left running. While it waits, Send goes straight to Claude as the next message rather
than queuing, and anything already queued goes in then; Turn Status (Ctrl+Shift+T) names the work
still running. (A background agent is different: Claude Code keeps working until the agent is done,
so the turn simply runs until then and its answer is said at the end, as with any turn.)
Something meant to run indefinitely, such as a development server, would keep the turn open until
you press Stop, so ask Claude to start long-running servers in a terminal of your own.
`claude` is started suspended and only let run once it's in the job, so nothing it starts can
slip out first.

### What headless sessions do with questions and permissions

Checked with Claude Code 2.1.286 (issues #187 and #188):

- **Permission prompts, questions and plans come to The Chat Place.** With
  `--permission-prompts host --permission-prompt-tool stdio`, anything that would ask arrives on
  the turn's output as a `can_use_tool` request, the same control protocol the Agent SDK uses,
  and the turn waits for the answer on its input. AskUserQuestion and ExitPlanMode come the same
  way: a question is answered by allowing the tool with the answers added to its input, a plan
  is approved by allowing it with a switch of permission mode, and Keep Planning denies it with
  your note. Each was checked against the real CLI: an allowed Write wrote its file; a denied
  one didn't, and Claude quoted the reason it was given; an approved plan carried on in accept
  edits without asking again.
- **Refusals still happen without asking** where Claude Code's own rules say so (a write outside
  the session's folder, say, or auto mode's safety check). The Chat Place adds those to the
  announcement ("1 tool was refused: ..."), marks the session "needs you", and the chat shows a
  "Permission denied" line.
- Question cards in desktop app sessions are shown as text in the chat ("Claude asked: ...,
  Options: ...", then "You answered: ...") and answered in Claude.

## Limitations

- On a Mac, edit boxes, lists and
  choices have their names under VoiceOver, as under JAWS and NVDA. But the text a screen reader
  reads for each row of the messages list (the whole message) uses MSAA, which wxPython has only
  on Windows, so VoiceOver reads the row's own text instead. On Linux, Orca reads a row's own text
  and then the rest of the message (from the row's accessible description). The OS-specific
  parts are in `thechatplace/platform_paths.py`, `thechatplace/ui/a11y.py`,
  `thechatplace/ui/mac_a11y.py`, `thechatplace/ui/gtk_a11y.py`, `thechatplace/orca.py` and the
  speech scripts.
- On Linux, Orca before 50 doesn't say a list's name ("Session list", "Messages in …") as you move
  into it, only the row you land on; Orca's Where Am I says it. Orca 50 and later say it.
- On Linux, full messages open as plain text unless you turn on the formatted page in Settings.
  Tested with Orca 48 and WebKitGTK 2.50, the page's headings and landmarks could be moved
  between, but Orca didn't read it line by line.
- "Needs you" for desktop sessions depends on the desktop app's turn summary, which it doesn't
  always write. A session without one shows as idle once it stops working.
- A desktop session's transcript can be gone if it's older than Claude Code's retention period
  (`cleanupPeriodDays` in Claude Code's settings). The chat says so.
- The Chat Place's own sessions, and terminal sessions, don't appear in the desktop app, so Open in
  Claude doesn't work for them; the app says so.
- Cowork sessions can't be continued here. Their conversation is in the session's own Claude Code
  folder inside the desktop app's files, where `claude -p` on your PC can't resume it, and its
  tools were run by the desktop app. Open in Claude carries on there.
- Subagent conversations are left out of the chat.
- Turns of The Chat Place's own sessions are also spoken by ClaudeSpeak's Stop hook, if that's
  installed, since `claude -p` runs hooks; so a reply can be heard twice.
- The desktop app's file formats are undocumented and could change with any update.

## Developing

Building, testing and releasing: see [DEVELOPING.md](DEVELOPING.md).
