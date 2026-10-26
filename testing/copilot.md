# Hand-testing GitHub Copilot CLI support (#352)

Kelly doesn't use Copilot, so each Copilot pull request is tried by hand against the real
Copilot CLI before it's reviewed, and the PR says what was tried. These checks are written for
the keyboard and a screen reader. Each message sent to Copilot uses a premium request
(Copilot Free has a monthly allowance), so the checks keep messages short.

## Before you start

1. Install Copilot CLI: `winget install --id GitHub.Copilot -e` (Windows) or
   `brew install copilot-cli` (Mac). Open a new terminal and run `copilot login`.
2. Make a scratch folder for Copilot to work in, away from real files, for example
   `C:\Users\<you>\code\copilot-scratch`.
3. Optional: personal instructions in `~/.copilot/copilot-instructions.md` (Markdown answers,
   lists one item per line), so replies read well.
4. Run The Chat Place from the branch being tested: `.venv\Scripts\pythonw TheChatPlace.pyw`
   from the repository folder (Mac: `.venv/bin/python -m thechatplace`). Only one copy runs at
   a time, so close an installed copy first.

To have something to read, send Copilot a request from a terminal in the scratch folder:

```
copilot -C <scratch folder> --allow-tool shell --allow-tool write -p "List the files in this folder, then create notes.txt saying hello, and tell me what you did."
copilot -C <scratch folder> --continue --allow-tool shell -p "Read notes.txt back to me."
```

## Reading Copilot chats (#355)

1. **Settings.** Open Settings. "Include GitHub Copilot CLI chats (read-only; restart required)"
   is there, on Alt+O, only because Copilot has saved a session. Check it, save, and hear
   "Settings saved. Restart The Chat Place to change which chats are listed." Restart.
2. **The list.** Alt+V, H, then G: GitHub Copilot Chats. The list's name says so and how many.
   A Copilot CLI chat started in a terminal also shows in Terminal Sessions (Alt+V, H, T).
3. **Opening a chat.** Its lines read "You: …" and "Copilot: …". The messages heading says
   read-only, and the reply area says "Reply in Copilot CLI".
4. **Tool activity.** Turn on Show Tool Activity. Each command reads as the command itself
   first ("Running command (powershell): Get-ChildItem …"), a file change names the file
   ("Applying patch (apply_patch): Add File: notes.txt"), and each result follows.
5. **A full message.** Enter on a Copilot reply: the page starts with "Copilot" as heading 1.
   Escape returns to the same line.
6. **Find and export** work on a Copilot chat as on any other.
7. **The finished announcement.** With the app open, send another `copilot -p` request that runs
   a command. The row says working the whole time, through every step, and you hear it finish
   once, not after each step.
8. **Turning it off.** Uncheck the setting, save and restart: Copilot chats are gone, and come
   back when it's turned on again.

## The back end (#364), before the window uses it

`tools/probe_copilot.py` runs one chat through the same code the window will use, in plain
text. From the repository folder:

```
.venv\Scripts\python tools\probe_copilot.py --folder <scratch folder>
```

It says "Safe to send (agent mode, allow-all off): yes", then asks "You (empty line to quit)".

1. **A plain answer.** Type `Reply with exactly the word: ready. Do not use any tools.` You hear
   "Copilot: ready" and "[turn completed]".
2. **A refused command.** Type `Run this shell command: echo test > probe.txt`. Copilot asks
   permission, and the request names the command. Press Enter without typing y: "Denied." Then
   check that probe.txt wasn't created.
3. **An allowed command.** Ask the same again and type y: "Allowed once." This time probe.txt
   is created. It asks again next time: nothing is remembered as "always".
4. **Slash commands.** Type `/allow-all on`: "Not sent: Copilot commands starting with / can't be
   sent from The Chat Place". Copilot gets nothing.
5. **Stopping.** Ask for something long (`Write the numbers from 1 to 400, one per line`) and
   press Ctrl+C while it streams: "Stopping this turn." then "[turn interrupted]".
6. **Reopening.** Quit with an empty line. The tool prints a `--resume` line; run it. "Reopened
   chat …", and the old history isn't read out as new replies. Send one more short message.
7. **Chats started elsewhere stay read-only.** Run the tool with `--resume` and the id of a chat
   started in a terminal (its folder name under `~/.copilot/session-state`): "This Copilot chat
   was started outside The Chat Place, so it can only be read here."
8. **Hooks.** In the scratch folder, make `.github\hooks\allow.json` holding
   `{"version": 1, "hooks": {"preToolUse": [{"bash": "echo"}]}}`, and start the tool there:
   "Couldn't use Copilot: Copilot is set up with a hook that could approve its actions without
   asking you (…allow.json) …". Copilot isn't started. Delete the file afterwards.
9. **Used in a terminal meanwhile.** Start a chat with the tool and send one message. In another
   terminal, in the same folder, run `copilot --resume <its id> -p "Say hi"`. Back in the tool,
   send another message: "Not sent: This Copilot chat was used outside The Chat Place since it
   was opened here. Open it again to carry on."

## Running Copilot chats in the window

Run from source with the Copilot setting on (Settings, Alt+O, then restart). Use a scratch folder
that isn't a git repository with hooks. Each message uses Copilot requests.

1. **Settings.** The check box reads "Include GitHub Copilot CLI chats (preview; restart
   required)".
2. **New Session (Ctrl+N).** Provider offers Claude, Codex (if set up) and Copilot. Choose
   Copilot: Model, Effort and Permission mode say "Copilot's configured model", "Copilot's own
   setting" and "Asks before running commands or changing files", and can't be changed; the note
   says Copilot asks before acting and uses your plan's requests. Choose the scratch folder, type
   `Reply with just the word: ready` and start.
3. **The new chat.** It opens with "No messages yet.", you hear "Sent to …", then "… replied.
   ready". The row is a Chat Place session with Provider Copilot, the message box is there, and
   the heading ends ", on Copilot".
4. **A second message.** Send `Reply with just the word: again`: you hear only "again", not the
   first reply again.
5. **Asking.** Send `Run this shell command: echo test > probe.txt`. You hear "Copilot needs your
   answer. Command: echo test > probe.txt" and Ctrl+Shift+A answers. Ctrl+Shift+A opens "Copilot
   needs permission: <title>"; the request names the whole command; the buttons are Allow Once,
   Deny and Answer Later; Enter denies. Deny: "Answer sent to Copilot.", and probe.txt isn't
   created. Ask again and choose Allow Once: probe.txt is created.
6. **Waiting and Stop.** Send `Without using any tools, write a 1500-word story about a
   lighthouse`. Straight away, while it's still answering, type and send `And then stop`: it
   waits its turn (Copilot would run a command for a list of numbers, and finish too soon).
   Press Ctrl+Period: "Stop requested…", "1 waiting message is back in the message box,
   unsent.", and "And then stop" is back in the message box.
6a. **Both replies, when one waited.** Send `Reply with just the word: one`, and straight away,
   while it's answering, send `Reply with just the word: two`. Let both finish: you hear "one"
   and then "two", each once, and nothing is put back in the message box.
7. **Refusals in Copilot's words.** Send `/allow-all on`: it's refused with "Copilot commands
   starting with / can't be sent…", and the text stays in the message box.
8. **Context.** The status bar shows "Context N% reported" once Copilot has reported it.
9. **After a restart.** Quit and start again: the chat is still listed as a Chat Place session,
   opens, and a message sends (the app reopens it in Copilot first).
10. **Used in a terminal meanwhile.** With the chat open here, run
    `copilot --resume <its id> -p "Say hi"` in a terminal in the same folder (the id is in the
    chat's Properties). Sending here then says the chat was used outside The Chat Place; select
    another chat and come back to open it again, then send.
11. **A hook.** Put `.github\hooks\allow.json` holding
    `{"version": 1, "hooks": {"preToolUse": [{"bash": "echo"}]}}` in the scratch folder: a
    message is refused, naming the file. Delete the file afterwards.
12. **Other chats stay read-only.** A chat started in Copilot CLI in a terminal still says
    "Reply in Copilot CLI", with no message box.
13. **Chat Permissions (Ctrl+Shift+H)** on a Copilot chat started here says it keeps no
    permissions and Copilot asks each time.

## What to report in the PR

Copilot CLI version (`copilot --version`), the operating system, which checks passed, and
anything heard that was wrong, missing or confusing. Never paste conversation content.
