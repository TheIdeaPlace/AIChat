# Testing guide: The Chat Place on Linux, with Orca

For testing by hand on a real Linux PC (an MNT Pocket Reform, say). The virtual machine tests in
`dev-notes/linux-port.md` covered names, rows, the status bar, dialogs and the build, with Orca
48 on Debian 13. These are the things a VM couldn't show: sound, a real keyboard and desktop,
Wayland, notifications, and turns with a signed-in Claude Code.

Each test says what to do and what you should hear. Note anything else, with the test number.

## Before you start

1. Orca, speech-dispatcher and FUSE 2: `sudo apt install orca speech-dispatcher libfuse2t64`.
   Note your Orca version (Orca, Preferences, or `orca --version`): 49 and later take
   announcements from the background through D-Bus; 48 and earlier don't.
2. Claude Code, installed and signed in (`curl -fsSL https://claude.ai/install.sh | bash`, then
   `claude`), with at least one session you started by typing `claude` in a terminal.
3. The AppImage for your processor (`TheChatPlace-linux-arm64.AppImage` on a Reform), executable
   (`chmod +x`), in `~/Applications`. Or from source: `./linuxsetup.sh`, then
   `.venv/bin/python -m thechatplace`.

## Starting

1. **Start it** from a terminal (`~/Applications/TheChatPlace-linux-arm64.AppImage`). Orca says
   "The Chat Place frame" and the session list's first row. Expected after one start: The Chat
   Place is in your desktop's menu, found by typing its name.
2. **Start it again** while it's running. A message says it's already running. Escape closes it.
3. **Your desktop's session**: say whether you're on Wayland or X11 (`echo $XDG_SESSION_TYPE`),
   and which desktop.

## Reading

4. **Session list.** Arrow through it. Each row reads title, folder, state and age. With Orca
   50 or later, moving into the list (Ctrl+1 from elsewhere) also says "Session list"; with 49
   or earlier it doesn't, and Where Am I (Orca+Enter on the keypad, or Orca+Return) does.
5. **Messages.** Enter on a session. Arrow through the messages: each reads its first line, a
   short pause, then the rest of the message. Turn off Settings, Reading messages, "Read the whole
   message on each item…" and check rows then read only their first line.
6. **Full message.** Enter on a long message: a read-only box, "Claude said", read by line, word
   and character. Escape returns to the same message.
7. **Formatted page (optional).** Turn on Settings, Reading messages, "Open full messages as a
   formatted page". Enter on a message with headings and a list. Note whether Orca reads it line
   by line (Down), moves by heading (H), and whether Escape closes it. In the VM, H worked and
   Down didn't.
8. **Status bar.** F6 until the status bar, or Ctrl+9. Left and Right move between its parts.
   Orca's read-status-bar command (Orca+KP_Divide, or Orca+/ in the laptop layout) reads it.
9. **Menus.** Alt+F, Alt+V, Alt+H, and the Menu key (or Shift+F10) in each list. Every item is
   read with its key.
10. **Every dialog.** Ctrl+Comma (Settings), Ctrl+N (New Session), View, Usage and Context, Help,
    Report a Bug. Tab through each: every control has a name, nothing is read as "panel" or
    "unlabelled".

## Speech

11. **In The Chat Place.** Load a session: "Loaded <title>, N messages" in Orca's own voice and
    rate, and on the braille display if you have one.
12. **From another window.** Start a turn in a terminal session (type a question to `claude` in
    a terminal), then switch to another window before it finishes. When it finishes:
    - Orca 49 and later: the announcement in Orca's voice.
    - Orca 48 and earlier: the announcement in speech-dispatcher's default voice. It should
      never talk at the same time as Orca: press a key while it speaks, and Orca's echo should
      cut it off.

    Help, Report a Bug shows the route ("spoke through Orca (D-Bus)", "(the window)", or "…, so
    speech-dispatcher").
13. **Without Orca.** Quit Orca (Orca+Q), and repeat 11. A speech-dispatcher voice speaks it.
    It's speech-dispatcher's default voice; Settings offers a rate.

## Notifications

14. With a turn running in one of The Chat Place's own sessions (step 17), switch to another
    window. When it finishes, a desktop notification appears; Orca reads it. Choose it (if your
    desktop lets you): The Chat Place comes forward with that session loaded.

## Claude Code

15. **Help, Claude Code Sign-in.** It says whether Claude Code is signed in. If it offers to sign
    you in, a terminal window opens; say which terminal it chose and whether Orca reads it. The
    window waits for Enter before it closes.
16. **New Session** (Ctrl+N). Pick a folder, type a first message, Start. The turn runs; the
    reply is announced and appears in the messages.
17. **Reply** (Ctrl+Enter in the reply box). The reply is announced. Stop (Alt+P) stops a turn.
18. **Permissions.** Ask for something that needs permission (edit a file in default mode). The
    question opens; Allow and Deny are read; Escape answers later.

## Links and files

19. **A link.** On a session, File, Copy Session Link (Ctrl+Shift+L), and choose the link only.
    Open it from another program (`gio open '<link>'` in a terminal, or a link in a browser): The
    Chat Place comes forward with that session.
20. **What Claude Knows About You** (View menu): Edit opens the file in your editor; Show in
    Folder opens your file manager with the file selected.

## Updating

21. Needs two releases with Linux builds. Help, Check for Updates says it's up to date, or offers
    the newer one; Yes downloads it, closes, and starts the new version from the same AppImage
    file.
