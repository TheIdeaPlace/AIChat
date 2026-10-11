# The Chat Place keyboard shortcuts

One window, three parts, in Tab order: the session list; the messages of the loaded session; and the reply box with Send and Stop (for a desktop app or terminal session, a read-only note saying where you reply, with Open in Claude and Continue Here as they apply, in the same place). Shift+Tab goes back the same way.

## Moving around

| Keys | What it does |
| --- | --- |
| Tab, Shift+Tab | Session list, messages, reply box, and back |
| Ctrl+1 | Go to the session list |
| Ctrl+2 | Go to the messages |
| Ctrl+3 | Go to the reply box (or the note, for a read-only session) |
| F6, Shift+F6 | Next or previous part of the window: session list, messages, reply box (when a session is loaded), status bar, and round again |
| Ctrl+9 | Go to the status bar: what was last said, the loaded session, and buttons for how full the context is, sessions that need you and an update; Left and Right move between them, Tab leaves |
| Escape in the messages or the reply box | Back to the session list, on the same session |
| Backspace in the messages | Also back to the session list |

## Session list

| Keys | What it does |
| --- | --- |
| Enter | Load that session and move to its messages |
| Ctrl+O | Open the selected session in the Claude desktop app |
| Ctrl+Shift+L | Copy Session Link: Enter copies a Markdown link that opens the session in The Chat Place; the list also has the bare link, and where they apply its Open in Claude link and claude.ai address |
| Ctrl+N | New Chat Place session |
| Ctrl+Shift+N | Continue the selected desktop app or terminal session here, as a copy you can reply to (the original isn't changed) |
| Ctrl+Shift+O | Change Permission Mode: auto, accept edits, manual or plan for the selected (or loaded) Chat Place session, from its next turn (File, Change Effort and Change Model are beside it) |
| F5 | Refresh the list now and put it back in order (it also refreshes itself every few seconds, without moving rows while you're in it) |
| Alt+V, O | Sort Sessions: by status, newest first, oldest first, by title or by folder; the choice is remembered |
| Alt+V, H | Show Sessions: all, needs you or working, needs you, working, with a new reply, idle, desktop app, Cowork, The Chat Place, terminal, Remote Control, ungrouped (in no group), archived, or one of your groups |
| Alt+V, E | Session List Columns: choose what each session's line says and in what order, such as status first; in the dialog, Enter adds, Delete removes, and Alt+Up, Alt+Down, Alt+Home and Alt+End move |
| Ctrl+E | Export the selected (or loaded) session as Markdown, a web page or plain text |
| F2 | Rename the selected session (a desktop app or terminal session's new name shows only in The Chat Place) |
| Applications key / Shift+F10 | The session's menu: load, open in Claude, copy its link, rename, Remote Control, groups, export, hide or delete |
| Alt+F, O | Remote Control: reach the selected session from claude.ai and your phone (a desktop app session can be continued here with it on) |
| Ctrl+G | Add the selected session to a group (or a new one); File, Remove from Group and Manage Groups are on the File menu |
| Delete | Hide the selected session; View, Show Sessions, Hidden lists hidden sessions, and File, Bring Back Session returns one |
| Shift+Delete | Delete one of The Chat Place's own sessions permanently, transcript and all, after you confirm (desktop app and terminal sessions can only be hidden) |
| Ctrl+F | Show only sessions whose title, folder or what they need contains some text; Escape in the list shows them all again |

## Messages

| Keys | What it does |
| --- | --- |
| Enter, or Applications key / Shift+F10 then Read Full Message | Read the whole message as a formatted page (move by heading, table, list and code block); Alt+P reads it as plain text instead; Escape comes back to it |
| Ctrl+C | Copy the whole message |
| Ctrl+Shift+C | Copy the message's last code block |
| Ctrl+Enter on a queued message | Send it now: Claude stops what it's doing and answers it |
| Ctrl+Shift+B, or Applications key / Shift+F10 then Code Blocks | List, read and copy each code block |
| Ctrl+F, then F3 and Shift+F3 | Find a message containing some text (its whole text, not just the first line), then the next or previous one |
| End | Newest message |
| Ctrl+L | Links: every link in the loaded session, newest first; Enter opens one in your browser, Ctrl+C copies its address, typing narrows the list (also from the session list and the reply box) |
| Ctrl+T | Show or hide tool activity in the loaded session |
| Ctrl+O | Open this session in the Claude desktop app |

## Reply box

| Keys | What it does |
| --- | --- |
| Ctrl+Enter | Send (Chat Place sessions only); you stay in the reply box. During a turn it queues the message and sends it when the turn ends |
| Ctrl+Shift+Enter | Send and save as a prompt: asks for the prompt's name (its first words to start with), saves it, then sends as Ctrl+Enter does; Escape at the name neither saves nor sends |
| Ctrl+Period | Stop the running turn; a queued message goes back in the reply box |
| Ctrl+/ | Insert a slash command or one of your skills (type to search); it goes at the start of the reply box |
| Ctrl+Shift+F | Attach files or images to your next message (Attach Files button, Alt+T); Delete in the attachments list removes one |
| Ctrl+V with a picture copied | Attach the picture (a screenshot from Win+Shift+S, say) |
| Ctrl+Shift+M | Other machines: list your sessions on other computers and send one a message, through Claude in the loaded Chat Place session (it needs Remote Control on) |
| Ctrl+Shift+D | Changed files: what Claude changed since your latest message or in the whole session; Enter on a file reads its changes line by line |
| Ctrl+Shift+U | Usage and context: a list of how full the loaded session's context is, and how much of each of your plan's limits is used; Ctrl+C copies a line |
| Ctrl+Shift+T | Turn status: how long Claude has been working, on what, what is still running in the background, and whether a message is queued |

## Anywhere

| Keys | What it does |
| --- | --- |
| Ctrl+Shift+S | Find in All Sessions: search the messages of every session in the list; Enter on a result loads that session on that message. Pressed again while it searches, it stops |
| Ctrl+Shift+A | Answer Claude: approve or deny a tool, answer its questions, or approve its plan (the loaded session first, then the one that has waited longest). Escape in the dialog answers later |
| Ctrl+Shift+K | What Claude knows about you: your instructions, memories, skills, subagents, commands and settings, to read or open in your editor |
| Ctrl+Shift+P | Prompts: your saved prompts, to use (Enter puts one in the reply box), add, edit, reorder or delete |
| Alt+Enter | Properties: what can be known about the selected session (or, in the messages, the selected message), one line each; Ctrl+C copies a line. Option+Return on a Mac |
| F1 | This list of shortcuts |
| Alt+H, G | The user guide, read by heading |
| Ctrl+Comma | Settings (announcements and speech) |
| Ctrl+Shift+R | Repeat the last announcement |
| Alt+F4 | Quit |
