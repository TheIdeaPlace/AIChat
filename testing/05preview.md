# The Chat Place 0.5 preview: testing guide

This guide covers everything new in the 0.5 preview since 0.4.0: every item in
`release-notes/v0.5.0.md`, area by area. Each step says what to do and what you should hear or
see. It includes the Codex parity checks; `testing/codex-parity.md` has the background on how
that work was built and checked.

Test a build made from the latest commit on `05preview`. The app still says **0.4.0** in Help,
About until the release commit changes the version, so About doesn't tell you which preview you
have.

Keys are written for Windows. On a Mac, use Command for Ctrl. The Alt keys are for Windows and
Linux; on a Mac, Tab to the control instead.

## Before you start

1. Close any running copy of The Chat Place.
2. Run the portable build, **The Chat Place.exe**, after extracting the whole zip. It uses your
   normal chats and settings and leaves your installed copy alone. Only one copy can run at a
   time. A local build is unsigned, so Windows may ask: choose More info, then Run anyway. Don't
   use the Setup installer for these tests: it installs over your installed copy.
3. If you want a backup first, use File, **Back Up Now**, and save it outside the build's folder.
4. Make a test folder you don't mind changing, with two short text files: `preview-notes.txt`,
   with three topics listed, and `rewind-example.txt`. Keep the Rewind file tests away from work
   you care about: putting files back can lose edits.
5. Check Help, **Claude Code Sign-in** and **Codex Sign-in**. Codex must be turned on in Settings
   (Ctrl+Comma), which needs a restart. The work was checked against Codex 0.163.0 and Claude Code
   2.1.296 and 2.1.316; older versions may say something isn't available.

Each question or message uses your plan, as usual. Nothing below has been marked as passed for
you. The user guide (Help, **User Guide**) has the details of every feature.

## 1. A quick first pass, Claude then Codex

Do this in a new Claude chat, then a new Codex chat, in your test folder.

1. Press **Ctrl+N**, choose the provider and the test folder, and start. Send: "Read
   preview-notes.txt and summarize its three topics. Do not change files." Expect: a reply about
   the file, announced as usual.
2. Press **Ctrl+/** (File, Insert Command or Skill). Type part of a name, press Down, and arrow.
   Expect: each name and its description. Enter puts it in the reply box. Escape goes back to
   the reply box. Tab and Shift+Tab reach the search box, the list and the buttons. Codex lists
   its controls, such as `/plan` and `/compact`, and your skills; Claude lists its own commands.
3. Press **Ctrl+Shift+F**, attach `preview-notes.txt`, and Tab to the attachment list. Expect: its
   name and how many are attached. Press Delete. Expect: it's removed, and with none left you're
   back in the reply box.
4. Attach it again and send "Summarize the attached file." Expect: it's read, and the list
   empties after sending. A message with only an attachment and no text sends too.
5. **Paste files.** In File Explorer (the Finder on a Mac), select `preview-notes.txt` and copy
   it. In the reply box press **Ctrl+V**. Expect: it's attached, as with Attach Files. Copy two
   files and paste: both attach. Then copy a screenshot (Win+Shift+S) and press Ctrl+V. Expect:
   the picture attaches. Ask what the picture shows. Expect: it's described, so the AI got the
   image, not just a file name.
6. While a long reply is running, type a follow-up and press **Ctrl+Enter**. Expect: it waits and
   sends when the reply ends. Do it once with a picture attached: the picture goes with it.
7. During another running turn, type "Keep the answer brief" and press **Ctrl+Alt+Enter**.
   Expect: it's taken into the turn without stopping it, said once.
8. Press **Ctrl+Shift+U** (Usage and Context) and **Ctrl+Shift+T** (Turn Status). Expect: readable
   details, or a plain statement that the provider hasn't reported something. Escape takes you
   back where you were.

## 2. Sounds

Sounds are off until you turn them on. The set is new: The Chat Place's own sound for each of 20
events, made by the rules in `dev-notes/sound-design.md`. None is QuickMail's any more. Nobody
has heard the new set yet, so what's being tested is whether each sound fits its event, whether
the families make sense, and how they sit with your screen reader.

### Listen to all 20

1. Choose File, **Sounds**. Tab through it both ways. Expect: every control is named. Turn on
   **Play sounds**.
2. Tab to **Sound events**. Expect: the list's name says how many events have a sound, and each
   row says the event and its sound, such as "Reply finished: Rise".
3. Arrow through all 20 and press **Space** on each. Expect: each one plays, with nothing said
   over it, on Windows and on a Mac. Try **Preview** (Alt+R) once too: the same sound.

What each should sound like, top to bottom of the list:

| Event | Sound | Listen for |
| --- | --- | --- |
| Tool ran | Tick | One very soft, very short high tick |
| File changed | Double tick | Two soft high ticks, rising |
| Still working | Hum | One faint low note, the softest |
| Message sent | Send | A quick leap up an octave |
| Message queued | Hold | One note three times, each softer |
| Message steered into the turn | Bend | The send, with a step on the way up |
| Steered message taken | Taken | A soft, quick climb of four notes |
| Queued message sent | Release | The held note leaping up into a send |
| You stopped a turn | Stop | A quick, damped drop, low |
| Reply finished | Rise | Three notes rising |
| Another chat finished | Low rise | The same rise, lower and softer |
| Ask Aside answered | Aside | Three quiet notes, down and back |
| Needs you | Call | One note three times fast, then a leap up; the loudest |
| Turn failed | Fall | Four notes falling |
| Message not sent | Bump | A note slipping down, then dropping |
| Ask Aside not answered | Aside fall | The aside's start, bending down |
| Service trouble | Waver | Two low notes wavering |
| Usage or context warning | Alert | High notes, alternating |
| Conversation compacted | Settle | A chord settling into one low note |
| Message from another session | Knock | Two knocks, then a lower note answering |

Note any that are hard to tell apart, too loud or quiet, harsh, or that don't suit their event.

4. Choose each **Sound scheme** (Alt+H): Glass, Soft bell, Wood. Space on a few events in each.
   Expect: the same notes in a different tone. Lower the **Volume** (Alt+V) and play one. Expect:
   it plays at the volume showing.
5. Choose OK.

### Sounds in use

1. In a test chat, with **Show Tool Activity** off (Ctrl+T), ask for something that runs a few
   commands and edits a test file. Expect: a Tick for each tool, a Double tick when the edit
   works, and Rise when the reply finishes. Note whether the ticks get in the way of speech.
2. Ask for something that needs your permission. Expect: Call, along with the announcement, not
   instead of it. Press **Ctrl+Period** to stop a turn. Expect: Stop.
3. Let a turn run in another chat while you stay in this one. Expect: Low rise when it finishes.
4. **Steering.** While a Claude turn runs, type a message and press **Ctrl+Alt+Enter**. Expect:
   Bend, and then Taken when Claude takes it in. In Codex, only Bend plays. Queue a message with
   Ctrl+Enter instead and let the turn finish. Expect: Hold when you queue it, and Release when it
   goes on its own.
5. Ask Aside a question (Ctrl+Shift+Y). Expect: Aside when the answer comes.
6. Press **Ctrl+Shift+X** (View, Mute All Sounds). Expect: "Sounds muted", and silence, even a
   sound already playing. Press it again. Expect: "Sounds on".
7. On Windows, in File, Sounds, turn on **No Windows sound for The Chat Place's notifications**.
   Let a chat finish while you're in another program, then choose its notification. Expect: only
   The Chat Place's sound, and the chat opens.
8. Try one Codex chat as well. A Codex subagent's own tools make no sound yet (#570).

### Sound schemes

1. In File, Sounds, press **Alt+A** (Scheme Actions). Expect: a menu with New, Copy, Rename,
   Delete, Reset, Export and Import Scheme. On Glass, Rename and Delete are read as turned off.
2. Choose **New Scheme** and give it a name. Expect: the events list says "0 of 20 events have a
   sound", and **Sound** (Alt+S) offers sounds in each tone, such as "Rise (Soft bell)".
3. Give two events a sound. Choose **Export Scheme** and save it. Then **Import Scheme** that file.
   Expect: "Added the sound scheme" and its name, and the copy has the same two sounds.
4. Choose **Reset Scheme** on Glass after changing one of its events. Expect: it's back to how it
   came.
5. Press **Alt+B** (Browse Gallery). The gallery isn't open yet. Expect: "Loading the sound scheme
   gallery", then "The gallery could not be reached", with focus on **Reload**. Escape closes it
   and you're back on Browse Gallery.
6. Choose Cancel in Sounds. Expect: the new scheme isn't kept.

## 3. Read a whole chat as one page

In a Claude chat, then a Codex chat, with several messages.

1. Select a message in the middle and press **Ctrl+Shift+W** (View, Read Full Chat as a Web Page,
   or Alt+V, W). Expect: "Preparing the full chat", then the page opens on that message's heading.
2. Press **2**. Expect: the next message, saying who wrote it and when. **H** also stops at
   headings inside a reply.
3. Press **Escape**. Expect: back on the message you started from.
4. Turn on Show Tool Activity (Ctrl+T) and open a long chat. Expect: it opens, and **3** reaches
   tool rows saying what each did, while **2** stays on messages.
5. On the page, press **Alt+P** (Option+P on a Mac). Expect: the same chat in a plain text box, at
   the same message.

## 4. Ask Aside, Claude and Codex

1. Load a chat with a few replies. Press **Ctrl+Shift+Y** (File, Ask Aside).
2. Type "What did I ask you to do first?" and press **Ctrl+Enter**. Expect: "Asking Claude" or
   "Asking Codex". Focus moves to **Answer**, the answer is read out, and you can read it by line.
   **Copy Answer** copies it.
3. Ask a follow-up that needs the last answer, such as "what did you mean by that?". Expect: it
   understands.
4. Tab to **Questions and answers** (Alt+N) and arrow. Expect: each answer shows in the Answer box,
   and Enter takes you there.
5. Close the dialog and press Ctrl+Shift+Y again. Expect: you hear how many earlier questions
   there are, focus is on **Your question**, and the newest answer is in Answer. Open Ask Aside
   on another chat. Expect: its own list.
6. Back on the first chat, press **Clear List** (Alt+R). Expect: "Cleared" with the count, and "No
   questions yet". Quit and restart the app. Expect: no earlier questions anywhere.
7. While an answer is coming, press Escape. Expect: the question stops. Ask another to check the
   dialog still works; once it's answered, Escape closes the dialog.
8. Read the chat. Expect: none of the questions or answers were added, and no files changed.
9. Ask while the chat is working. Expect: the chat's turn keeps running, and the answer covers the
   chat up to its last finished reply, not the turn that's running.
10. **After /compact.** In a Claude chat with a few finished replies, send `/compact`, wait for it,
    then Ask Aside about something from before. Expect: an answer. If the chat compacted during a
    turn that didn't finish, Ask Aside says to ask again after Claude's next reply, rather than
    asking.

Ask Aside works for chats started here, Claude desktop and terminal chats, and local Codex chats
started elsewhere. It isn't offered for Cowork chats, kept copies or Copilot.

## 5. Rewind to Here and Branch from Here

Make a short chat with at least two of your messages and replies.

1. In the messages, select a reply. Press **Shift+F10** and choose **Branch from Here**. Expect: a
   new chat with "(branch)" in its title, and the original still listed. In Codex the copy
   includes the whole turn that reply is in.
2. Go back to the original and select your second message. Press **Ctrl+Shift+Z** (on a Mac,
   Command+Shift+Z in the messages). Expect: **Rewind to Here** opens on **Conversation only**.
3. Read **What happens** (Alt+H) and press Enter there. Expect: "Tab to Rewind, or to Cancel",
   and nothing starts. Press Escape. Expect: back on the message, and no copy made.
4. Open it again and choose Rewind. Expect: a new "(rewound)" chat ending just before that
   message, with your message in the reply box ready to change. The original and your files are
   unchanged.
5. **Codex.** Expect: only Conversation only; the file choices say they aren't available. The copy
   starts read-only, on Codex's own model and effort, with connected tools off.
6. **Claude, putting files back.** Ask Claude to change `rewind-example.txt` with its Edit tool,
   not a command or a subagent (check with Ctrl+T). Note the file before and after. Open Rewind on
   the message that asked for it and arrow to **Files only**. Expect: What happens names the file,
   and warns that hand edits since Claude last changed it are lost and it can't be undone. Choose
   Rewind. Expect: the file is back as it was, the conversation stays, and you hear "Files in" the
   chat's name "restored to the versions saved before your message". Make another edit and try
   **Conversation and files**.
7. **Hand edits are lost.** Have Claude edit the file, then edit it yourself and save. Send a
   message that changes nothing, then another. Rewind to the last one with **Files only**. Expect:
   What happens says Claude changed the file earlier, and it goes back too. After Rewind, the file
   has Claude's version and your edit is gone.
8. With a Codex chat in the same folder waiting on an approval, open Rewind in a Claude chat
   there. Expect: the file choices say "(unavailable)", and What happens says another chat is
   working in this folder. Answer the approval and let it finish; they're offered again.

## 6. Subagents, Claude and Codex

1. **Claude.** Ask Claude to hand a read-only look at the two test files to a subagent, then sum
   up its report. Open View, **Subagents** (Alt+V, G), or Ctrl+7 in Lists. Expect: its prompt,
   how it's going and its report. You hear when it finishes.
2. Press Enter on it. Expect: its conversation opens. Enter reads a message, **Ctrl+C** copies it,
   **Ctrl+T** shows tool activity. Escape goes back. While it works, new messages arrive without
   moving your focus.
3. On a long conversation, press Enter and then move to another subagent before it opens. Expect:
   it doesn't open over you, and you hear so.
4. On the turn's message menu (Shift+F10), try **Subagent Conversations**. If a subagent edits a
   test file, Changed Files (Ctrl+Shift+D) names the subagent that made the change.
5. **Codex.** Ask a Codex chat started here to hand a small task to a subagent. Open View,
   Subagents. Expect: each subagent, and in its conversation, who said what. If it asks for an
   approval, the request names the subagent. Once the subagent has finished, nothing from it is
   left waiting to answer.

## 7. Connected tools and their questions

Open **View, Connected Tools (MCP)**. Arrow through the list; Tab and Shift+Tab reach every button
and **Details**. Escape closes it. If you have no servers or connectors, the list is empty: skip
the steps that need an account.

### Claude

1. Check a server or claude.ai connector's name and status in the list and Details. One that
   failed or needs sign-in says why.
2. **Sign In** (Alt+S), finish in the browser, then **Refresh** (Alt+F). **Reconnect** (Alt+R)
   tries a failed one again.
3. In a chat started here, choose **Turn Off for This Chat** (Alt+O), then send something. Expect:
   the tool isn't used in the next turn. Turn it on again, and check another chat wasn't changed.
   In a chat started elsewhere, turning one off says it's only for chats started here.

### Codex

1. In a Codex chat started here, open the list. Expect: servers for the folder, the Codex apps you
   can use, and The Chat Place's session tools. Tools start off.
2. On a server, choose **Copy with Tool On** (Alt+O). Expect: a question warning that its tools can
   reach outside the folder. Say No. Expect: no new chat, nothing changed.
3. Do it again and say Yes, for a server you trust. Expect: a new read-only "(tools copy)" chat with
   that server on, and the original unchanged. Ask the copy to use the server for something
   harmless. Expect: it works, and servers you didn't choose stay off.
4. Do the same with an app. Expect: only the chosen app can be used, and the original keeps its
   choice.
5. In the copy, choose **Sign In** on the server. Expect: a dialog reading you the sign-in page's
   real address first. Cancel once: no browser opens. Do it again and open it, finish signing in,
   and expect to hear the result, or press Refresh if Codex doesn't report one.
6. Open the list on a Codex chat started elsewhere. Expect: the folder's servers, read-only, with
   no copying.
7. In the tools copy, remove its server from Codex's configuration, then send. Expect: the message
   is refused and says why. The list shows that server first, saying it's been removed, and
   **Copy with Tool Off** makes a copy without it. Put the server back afterwards.

### When a connected tool asks you something

These need a server that really asks. Try with Claude, then with Codex.

1. When a tool asks for a form, you hear the chat needs you. Press **Ctrl+Shift+A**. Expect: it opens
   on the read-only **Message** box. Press Enter there. Expect: nothing is sent, and you hear "Tab to
   the form, or to Send".
2. Tab through the form. Expect: each question, choice and field is named, a number says its limits,
   and each field's Alt key goes to it. A check box's Alt key moves to it without ticking it.
3. Leave a required answer empty and press Send. Expect: it says what's missing. Enter 0 in a number
   field and send. Expect: 0 is kept, not dropped.
4. Press Escape on another request. Expect: it's left waiting, to answer later. Choose **Decline** on
   one. Expect: nothing you typed is sent.
5. With nothing to fill in, Enter on the message says "Tab to Send, or to Decline".
6. When a tool asks you to open a web page, Ctrl+Shift+A opens on an **Address** box, with "Opens"
   and the host first, then the whole address. Enter there sends nothing and says "Tab to Open in
   Browser, or to Decline". **Open in Browser** opens it.
7. A form too complex to show opens on a **Note** box saying why, with only **Decline** and **Answer
   Later**.

## 8. Chat Options, Claude and Codex

1. In New Session (Ctrl+N), choose Claude and press **Alt+H** (Chat Options). Type instructions
   such as "End each reply with the word previewcheck", choose OK, start the chat and ask
   something. Expect: the reply ends with that word. Properties (Alt+Enter) lists the instructions.
2. Back in Chat Options, check **Agent** and **Output style**. If you have none, Default is fine. An
   agent with its own model reads that way, such as "code-reviewer (uses its own model, opus)". One
   whose model could bill usage credits is refused when you choose OK, saying why.
3. Use File, **Chat Options** on that chat and change the instructions. Expect: they apply from the
   next message; a running turn carries on as it started.
4. **More folders.** Add a separate test folder (**Add Folder**), choose OK, and check it's listed in
   Chat Permissions (Ctrl+Shift+H). Remove it and check it's gone.
5. **Codex.** Open File, Chat Options on a Codex chat started here. Expect: it starts on
   **Instructions**, and Tab goes to More folders, Add Folder, Remove, About these options, OK and
   Cancel, then round again. Shift+Tab goes back. Escape closes without saving; Ctrl+Enter saves.
   Try instructions and a folder as in steps 1 and 4. A folder lets Codex write there only in a
   mode that edits files.

## 9. Live changes, thinking and retries

1. **Claude, during a turn.** Use File, **Change Model** or **Change Permission Mode**
   (Ctrl+Shift+O). Expect: "now and for later turns", or a plain reason it applies from the next
   turn. Change the mode twice quickly. Expect: the heading stays on your last choice.
2. When Claude switches itself to plan mode, you hear "Claude Code switched to plan mode", without
   it cutting off what's being read, and the next turn stays in plan mode until you approve the plan.
3. **Thinking.** Turn on Show Tool Activity (Ctrl+T). In Claude, where it kept its thinking, it comes
   before the reply. In Codex, reasoning summaries appear, live and in history. Expect: a summary is
   never read out as if it were the reply.
4. **Context.** After a Claude turn, open Usage and Context (Ctrl+Shift+U). Expect: readable
   figures for tools, memory files, skills, messages and free space.
5. **Retries.** If Claude or Codex retries on its own, you hear it once for each change, and Turn
   Status (Ctrl+Shift+T) has the details. You don't need to disconnect to force this.

## 10. Codex controls

Use a Codex chat started here.

1. **Model and effort.** While it's idle, use File, **Change Model**, then **Change Effort**. Expect:
   the choices come from Codex, and the change applies from the next message. Cancel a picker once:
   the old choice stays. Try during a turn. Expect: you're told to wait or stop first.
2. **Plan mode.** Press Ctrl+/, choose `/plan`, and send it. Choose **Plan**. Expect: the heading
   says Plan, and asking for a change gets a plan, not the change. Choose `/plan` again and
   **Default**, then send a message to carry it out.
3. **A control with a draft.** Put `/model` alone on the first line and some text below it. Send.
   Expect: the model picker opens, and your text stays in the reply box.
4. **Moved on.** Choose Change Model and straight away type in the reply box. Expect: no picker over
   your typing, and you hear that you've moved on. This is hard to catch when Codex is quick.
5. **Skills.** If you have a Codex skill for the folder, choose it with Ctrl+/. Expect: `$name` in
   the reply box; finish the request and send.
6. **Compact.** In an idle chat with some history, send `/compact`. Expect: "context compacted". If
   you've just changed its settings, expect to be told to send a normal message first.
7. **Memories.** Send `/memories off`, then `/memories on`. Expect: each is confirmed. Your saved
   memories aren't deleted.
8. **Goals.** Send `/goal`. Expect: its Goal and usage, or "No goal is set for this chat".
9. **Session tools.** Ask the chat to list your sessions, look up itself, and search for a word in
   a test chat. Expect: it can, and it leaves itself out of the list. Ask it to hide an idle test
   chat. Expect: it's hidden (File, Bring Back Session returns it). Ask it to hide itself, an open
   chat, or one that's working. Expect: each is refused.
10. **Approvals.** Turn on automatic approval review (Ctrl+Shift+O) and ask for something that needs
    a few commands. With Show Tool Activity off, expect allowed requests not to be spoken or shown;
    one that isn't allowed is. Turn Show Tool Activity on: allowed ones are shown and spoken. Open
    Lists (Ctrl+Shift+I) and press **Ctrl+8** (or choose **Approvals** in Kinds). Expect: each
    request, newest first, as "Allowed:" or not, the command and who allowed it; Details has the
    reason, and **Copy** copies the command.

## 11. Stop one background task

In a test chat, ask the AI to start two background commands that each wait about 90 seconds, and to
say when both are running without waiting for them.

1. Choose File, **Stop Background Task** (in Codex you can also send `/background`). Expect: a list of
   both. Cancel once: both keep running.
2. Open it again and stop one. Expect: only that one ends; the turn and the other keep going.
3. Wait for the other to finish with the list open, then choose it. Expect: you hear it had already
   finished.

## 12. New Session and Continue Here

1. **Where a worktree's branch starts.** Press Ctrl+N on a folder that's a git repository. Set
   **Work in** (Alt+I) to A new worktree and type a new branch name in **Branch** (Alt+R). Expect:
   **Start from** (Alt+A) turns on, offering the remote's main branch or what the folder has checked
   out. Start the chat. Expect: "Making the worktree", then what was made, such as "Made worktree
   fix-login on new branch fix/login, from origin/main." Properties (Alt+Enter) lists the branch,
   the worktree and what the branch started from. Choose an existing branch instead: Start from
   turns off.
2. **A chat moving into a worktree.** Ask Claude to make a worktree and work in it. Expect: you hear
   where the chat went, and the chat keeps showing its messages.
3. **Turned-off settings.** In New Session choose Copilot, then press Alt+D and Alt+E. Expect: focus
   stays where it was, and Model and Effort report no Alt key. Choose Claude: both have their keys
   back.
4. **Continue Here.** On a desktop app chat, press Ctrl+Shift+N and Tab to **Folder**. Expect: Tab
   stops there, the whole path is shown, and Enter says "Tab to Title, or on to Start". Alt+B and
   Alt+G do nothing there.

## 13. Fixes to check

1. **Desktop app and Cowork sessions.** If you use the Microsoft Store version of the Claude desktop
   app, check that its Code and Cowork sessions are listed (View, Show Sessions, Desktop app), each
   only once, in their groups. Run File, Back Up Now and Check Backup. Expect: they're included.
2. **Quitting during a turn.** Start a long turn in a Claude chat, with a message queued behind it.
   Press Alt+F4. Expect: a question saying the turn will be stopped and the waiting message won't be
   sent. Say Yes. Start the app again. Expect: that chat keeps its place in the list and says it was
   stopped, and the queued message wasn't sent on the way out. Try once with an agent conversation mid-turn: it comes
   back stopped, and carries on with the agent that was stopped.

## 14. The user guide

Open Help, **User Guide** and read the new parts "Getting more out of Claude", "Getting more out of
Codex" and "Getting more out of GitHub Copilot CLI", and "Branches and worktrees". Check that headings move as expected, and
that the menu paths and keys it gives work.

## What isn't in this release

Starting or resuming autonomous Codex Goals, Codex's own review command, installing plugins, cloud or
remote Codex runs, and connected-tool forms that are specific to one provider. A Codex subagent's
tools make no sound (#570). Issue #181, about the focus outline in lists, is still open.

## Reporting what you find

For each problem, note:

- the section and step number;
- Claude or Codex, and the chat type (started here, desktop app, terminal, Codex elsewhere);
- whether the chat was idle or working;
- what you did, what you expected, and what happened;
- what your screen reader said, and which one.

Help, **Report a Bug** fills in the versions and recent errors, and shows you everything before it
goes anywhere. Leave out private messages and passwords.
