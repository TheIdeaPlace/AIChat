# Testing guide: your sessions on other computers (#123)

For Kelly, by hand, across Clark (the Lenovo), the Surface and the Mac. It covers the two changes
merged on 2026-10-08:

- **#124**: messages from other sessions show as "From <session>" and are announced, and a Chat
  Place session keeps its name every turn.
- **#126**: File, Other Machines (Ctrl+Shift+M).

Each test says what to do and what you should hear. Note anything else as a comment on #123, with
the test number.

## Before you start

**1. Get the new code on the computer you test from (Clark).** It isn't released yet: the
installed copy is 0.1.2 and doesn't have it. Either:

- run from source: in `C:\Users\kelly\GitHub\AIChat`, run `git pull`, then
  `.venv\Scripts\pythonw TheChatPlace.pyw`; or
- release 0.1.3 first, and let the installed copy update itself.

To check you have it: the File menu should have **Other Machines... Ctrl+Shift+M**. (From
source, Help, About still says 0.1.2 until the release.)

**2. Pick a session to send from on Clark.** Make or choose one of The Chat Place's own sessions.
Rename it (F2) to **Clark Hub**. Turn Remote Control on for it (File, Remote Control, "On for this
session"), and send it one ordinary message so it has connected once.

**3. Have a session to send to on another computer.** On the Surface, a Claude Code session
with Remote Control on: in the Claude desktop app (turn Remote Control on for the session), or in
The Chat Place there (File, Remote Control). A desktop app session is the better first test: it
stays running, so it can answer without you going to the Surface. Note its exact title, for example **Surface Hub**. Keep the Surface
awake.

**4. Optional, on the Mac:** the same, called **Mac Hub**, for the tests with two machines.

## Tests

### 1. Other Machines needs the right session

1. Select a desktop app session (not one of The Chat Place's own), load it, press Ctrl+Shift+M.
   - **Expect:** a message box saying other machines are reached through one of The Chat Place's
     own sessions with Remote Control on. Nothing is sent.
2. Load one of The Chat Place's own sessions with Remote Control **off**, press Ctrl+Shift+M.
   - **Expect:** "Remote Control is off for <title>…", telling you to turn it on with File,
     Remote Control. Nothing is sent.

### 2. The first list

1. Load **Clark Hub**. Press Ctrl+Shift+M.
   - **Expect:** a list with one choice, "Refresh the list (asks Claude)", and a prompt saying
     Clark Hub hasn't listed your other computers' sessions yet.
2. Choose Refresh.
   - **Expect:** you hear that Claude in Clark Hub is being asked, and to press Ctrl+Shift+M again
     once it has answered. A turn starts. Your own read-back should *not* be a long instruction
     about ListAgents.
3. Wait for the reply.
   - **Expect:** Claude names your sessions on other computers, including Surface Hub.

### 3. The list from Claude's answer

1. Press Ctrl+Shift+M again.
   - **Expect:** each session on your other computers on its own line, with how it is: "idle",
     "working", "needs you" or "offline". "needs you" should be spoken as words, never as
     "requires underscore action". Refresh is last.
2. Arrow through it with the screen reader. Escape closes it without doing anything.

### 4. Send a message and hear the reply

1. Ctrl+Shift+M, choose **Surface Hub**.
   - **Expect:** a box titled "Other Machines" asking "Message to Surface Hub (Enter sends):".
2. Type `Reply with your computer's name and nothing else.` and press Enter.
   - **Expect:** you hear that Claude in Clark Hub is sending your message to Surface Hub, and that
     a reply will show as from Surface Hub. Your read-back is not Claude's instructions.
3. Claude's reply in Clark Hub should say it was sent.
4. **If Surface Hub is a desktop app or terminal session**, wait a minute or two. **If it's a Chat
   Place session**, go to the Surface and send it anything, since it only acts during a turn.
5. Back on Clark, with Clark Hub loaded:
   - **Expect:** a new line "From Surface Hub: SURFACEPRO7" (or whatever it answered), and an
     announcement "Clark Hub: message from Surface Hub. …". Enter on the line reads it in full.
   - It must **not** show as "You: <cross-session-message …>".

   The reply arrives in Clark Hub's transcript when Clark Hub next runs a turn, or straight away
   if Clark Hub is mid-turn. If nothing shows, send Clark Hub a short message such as "any
   replies?" and look again.

### 5. A reply while Claude is working

1. In Clark Hub, ask for something slow ("count to 30 slowly, one number per line").
2. While it works, from the Surface session, send a message to **Clark Hub** by name ("send
   Clark Hub the message: hello from the Surface").
   - **Expect:** "From Surface Hub: hello from the Surface" appears and is announced while the
     turn is still running, even though Clark Hub is The Chat Place's own session.

### 6. The name stays the same

1. Send Clark Hub two or three ordinary messages.
2. From the Surface, ask its Claude to "list sessions with ListAgents".
   - **Expect:** Clark Hub is listed as **Clark Hub**, not as a made-up name like
     `the-idea-place-projects-37` that changes every turn.
3. From the Surface, send a message to "Clark Hub" by name, then check test 4's step 5 on Clark.

### 7. Not during a turn

1. Start a slow turn in Clark Hub (test 5, step 1). While it runs, press Ctrl+Shift+M and choose
   anything.
   - **Expect:** "Clark Hub is working. Use Other Machines again when the turn ends." Nothing is
     queued; the messages list has no queued message.

### 8. Empty message, Cancel

1. Ctrl+Shift+M, choose a session, press Enter without typing.
   - **Expect:** "Nothing sent: the message was empty."
2. Ctrl+Shift+M, choose a session, press Escape in the message box.
   - **Expect:** nothing happens and nothing is said.

### 9. Two sessions with the same name (optional)

1. Give the Surface and the Mac sessions the same title, say **Hub**. Refresh the list.
2. Send a message to one of them.
   - **Expect:** it reaches only the one you chose. Claude's reply should name it with its
     bracketed code, such as `Hub [66e038]`.

### 10. Helpers aren't messages from other sessions

1. In Clark Hub, ask Claude to do something with a background helper agent ("use an agent to
   count the files in this folder").
   - **Expect:** the helper's report is not shown or announced as "From general-purpose" or
     "From a1b2c3…". Messages from other sessions only.

### 11. The user guide

1. Help, User Guide. Move by heading (H) to **Your chats on other computers**.
   - **Expect:** it matches what you just did. Report anything wrong or unclear.
2. In the Remote Control section, "What you can't do yet" now says you can send to a session on
   another computer but can't read it from here.

## On the Mac

Run tests 2 to 4 again from the Mac with Cmd instead of Ctrl (Cmd+Shift+M), and with VoiceOver.
Check that the list and the message box can be reached and used with the keyboard alone, and
that Return sends.

## What's known not to work yet

- A Chat Place session doesn't act on a message by itself between turns. Someone has to send it
  something first. That's the rest of #123, built on #118.
- Other Machines can't read another computer's conversation. That's on claude.ai and in the
  Claude app only.
- The list is Claude's latest look, not live. Refresh it after a while.
