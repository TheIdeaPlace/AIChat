# The Chat Place 0.5 preview: testing guide

The 0.5 preview is the combined Claude (#468) and Codex (#467) feature work on the branch
`05preview`, for the planned 0.5.0 release. It also has Sounds (#250) and the Codex parity work
(#558), whose checks are in `testing/codex-parity.md`. Test a build made from the latest commit on that
branch. The app reports **0.4.0** until the release commit bumps the version, so Help, About
doesn't tell you which preview you have.

## Start here

1. Close any running copy of The Chat Place.
2. Run the portable build, **The Chat Place.exe**, after extracting the whole zip if it came as
   one. A portable copy leaves your installed program in place. Both use your normal chats and
   settings, and only one copy can run at a time. A local Windows build is unsigned, so Windows
   may offer More info, then Run anyway. The Setup installer installs over your installed app;
   it isn't a separate preview installation, so use the portable copy for these tests.
3. If you want to keep a backup first, use File, **Back Up Now**, and save it outside the
   preview's folder. The guide's file tests change only the test files you choose.
4. Make a disposable folder for new test chats, with two short text files in it:
   `preview-notes.txt`, with three topics listed, and `rewind-example.txt`. Keep the
   file-restoration test away from work you want to keep: it can lose edits (see Branch and Rewind).
5. Check Help, **Claude Sign-in** and **Codex Sign-in**. New replies need the appropriate
   signed-in provider. Codex must be enabled in Settings; enabling it requires a restart.
   The implementation was checked against Codex 0.163.0 and Claude Code 2.1.296 and 2.1.316.
   Older provider versions may report that an operation is unavailable.

The full user guide (Help, **User Guide**) and the planned notes in `release-notes/v0.5.0.md` go
with this guide. None of the listening checks below has been marked as passed on your behalf.
Each question or message uses the selected provider's plan.

## A quick first pass with each provider

Repeat this section in a new Claude chat, then a new Codex chat, in your test folder.

1. Press **Ctrl+N**, choose the provider and folder, and start the chat. Send:
   "Read preview-notes.txt and summarize its three test topics. Do not change files."
   Expect: a reply about the file. Listen for the correct provider and the reply announcement.
2. Press **Ctrl+/**. Type part of a command or skill name, Down into the results, then move with
   the arrows. Expect: names and descriptions, and Enter puts the choice in the reply box.
   Escape closes the picker and returns to the reply box. Listen for the selected result and
   make sure Tab and Shift+Tab reach the search box, list and buttons.
3. Press **Ctrl+Shift+F**, attach `preview-notes.txt`, and Tab into the attachment list.
   Expect: its file name and attachment count. Press Delete. Expect: the attachment is removed;
   when none remain, focus returns to the reply box. Listen for the removal announcement.
4. Attach the file again and send "Summarize the attached file." Expect: it is read, and the
   attachment list clears after sending. Attach a picture of your choice, or copy a screenshot
   and press **Ctrl+V** in the reply box. Ask what it shows. Expect: the provider receives an
   image, rather than only its file name. A message with attachments and no typed text can send.
5. While a longer reply is running, type a follow-up and press **Ctrl+Enter**. Expect: it queues
   and sends after the reply. Repeat once with an image attached. Expect: its image input stays
   with the queued message. Press **Ctrl+Period** to stop a separate queued attempt; check the
   returned draft before sending again. In Codex, a recovered text draft names its files;
   reattach images to send them as images again.
6. During another running turn, type "Keep the answer brief" and press **Ctrl+Alt+Enter**.
   Expect: steering is accepted without stopping the turn. Listen for the acceptance once.
7. Press **Ctrl+Shift+U** for Usage and Context, and **Ctrl+Shift+T** for Turn Status. Expect:
   readable provider-specific information, including an explicit absence of information where
   the provider hasn't reported it. Escape should return to the control you came from.

If the picker is empty of personal skills, that is normal when none are installed. Claude and
Codex offer different built-in commands; they aren't expected to have identical lists.

## Read a full chat as a page

In a Claude chat, then a Codex chat, with a few messages. Select a message in the middle and press
**Ctrl+Shift+W**. Expect: you hear "Preparing the full chat.", then the page opens with the reading
position on that message's heading. Press **2**. Expect: the next message, saying who wrote it and
when. **H** also stops at headings inside a reply. Press **Escape**. Expect: back on the message you
started from. Turn on tool activity (**Ctrl+T**) and try a long chat. Expect: it still opens, and
**3** reaches tool rows that say what each did, while **2** stays on messages. It may take a moment for
your screen reader to take in a very long page. Press **Alt+P** in the page. Expect: the same chat in
a plain text box, at that message, with no "Exported" line.

## Ask Aside: both Claude and Codex

1. Load a chat with a few replies. Press **Ctrl+Shift+Y** (File, Ask Aside).
2. Type "What did I ask you to do first?" and press **Ctrl+Enter**.
   Listen for **Asking Claude** or **Asking Codex**, matching the source chat.
3. Expect: focus moves to **Answer**, the answer is announced according to your announcement
   setting, and you can read it by line. **Copy Answer** copies the selected answer.
4. Ask a second question, then the first one again. Tab to **Questions and answers**, move through
   the entries, and press Enter. Expect: the corresponding answer opens for reading, and the
   repeated question's row finishes with its own answer. A question that starts with a slash is
   just a question here, not a command.
   Ask a follow-up that needs the last answer, such as "what did you mean by that?". Expect: it
   understands. Close the dialog and press **Ctrl+Shift+Y** again. Expect: you hear how many earlier
   questions are in the list, focus is on **Your question**, and the newest answer is in **Answer**.
   Open Ask Aside on another chat. Expect: its own list, not this one's. Back on the first, press
   **Clear List** (**Alt+R**). Expect: "Cleared" with the count, focus on **Your question**, and
   "No questions yet" in the list. Quit and restart the app. Expect: no earlier questions.
5. While an answer is still coming, press Escape. Expect: it stops the side question. Ask another
   question to check the dialog is usable again; after it finishes, Escape closes the dialog.
6. Read the source chat. Expect: none of the side questions or answers were added. Its files
   are unchanged, and focus returns to the original control. Repeat while the source chat is
   working; its main turn should keep running.

Ask Aside also works for local Codex chats started elsewhere, and Claude desktop and terminal
chats. Cowork chats, kept copies and Copilot aren't covered. Claude's side question can only read
files; Codex uses a temporary read-only copy with commands, connected tools, hooks, memories,
Goals and delegation off. Side questions still use your provider's plan.

## Branch and Rewind: check the provider differences

Create a short chat with at least two user messages and replies before testing.

1. In Messages, select a saved reply. Open its menu with **Shift+F10** and choose **Branch from
   Here**. Expect: a new chat with "(branch)" in its title; the original remains in the list.
   Claude's copy ends at the selected message. Codex's copy includes the whole native turn
   containing that message, so later messages in the same turn can be included too.
2. Return to the original and select your second user message. Press **Ctrl+Shift+Z**.
   In **Rewind to Here**, read **What happens** (**Alt+H**). Press Enter there. Expect: you hear
   "Tab to Rewind, or to Cancel", and nothing starts. Press Escape. Expect: focus returns to the
   selected message and no copy is made. Press Ctrl+Shift+Z again, choose **Conversation only**,
   then Rewind.
3. Expect: a new "(rewound)" chat ending before that user message, with the message in its reply
   box ready to edit and send. The original conversation and files are kept.
4. For Codex, listen for **Conversation only** selected, and file choices unavailable. Its new
   copy starts read-only, with Codex's configured model and effort and connected tools off.
   A source without a saved turn can't be branched yet; branching after a turn waits for it to
   finish. Check that the explanation is readable, rather than treating refusal as success.
5. For Claude, select a user message that led to an edit of `rewind-example.txt`. For this test,
   ask Claude explicitly to use its Edit or Write tool, without a command or subagent. Confirm
   that in Show Tool Activity (**Ctrl+T**), and note the file's before and after contents.
6. Open Rewind on that user message. Expect: **Conversation only** chosen to begin with, though
   Claude changed a file since that message. Arrow to **Files only**. Expect: What happens names
   the files and warns that each goes back to the last version Claude Code saved, that hand edits
   since Claude last changed it are lost, and that it can't be undone. Press Rewind. Expect: the file returns to its earlier
   contents, the conversation stays, and you hear "Files in" the chat's name "restored to the
   versions saved before your message". Make a fresh edit and try **Conversation and files** as well.
7. Hand edits are lost even from before the message. Have Claude edit `rewind-example.txt`, then
   edit it yourself and save. Send a message that changes no files, then another. Rewind to that
   last message, and choose **Files only**. Expect: What happens says Claude changed the file
   earlier in the chat, which goes back too. Press Rewind. Expect: the file has Claude's contents and your hand edit is gone. A file
   created since the message is removed, and a file Claude never touched stays.
8. With a Codex chat in the same folder waiting on an approval, open Rewind in a Claude chat there.
   Expect: the file choices say "(unavailable)", and What happens says another chat is working in
   this folder. Answer the approval and let it finish, and they're offered again.

Claude file restoration applies only to chats started here and messages sent with this preview
or later. It doesn't restore changes made by commands, or files only a subagent changed; What
happens names those. A subagent's changes in a file Claude changed too do go back. Older messages
or chats started elsewhere may offer conversation rewind without file restoration. While files go
back, nothing in that folder sends. Claude's New Session and Continue Here make no chat and put
your message on the clipboard, saying so; a new Codex or Copilot chat keeps it in its reply box.
Codex Rewind never rolls files back.

## Codex: model, effort and native Plan mode

1. With an idle Codex chat started here, use File, **Change Model**, then **Change Effort**.
   Expect: choices come from Codex, and the saved change applies to the next message. Read the
   heading and send a short message. Try cancelling a picker too: the old choice should stay.
2. Press **Ctrl+/**, choose `/plan`, then send that control with **Ctrl+Enter**. Choose **Plan**
   in the mode picker. Expect: the heading reports Plan. Ask for a plan to change the sample file;
   it should plan instead of implementing the change.
3. Choose `/plan` again and choose **Default**. Send a normal message to carry out the plan.
   Expect: the chat's existing permissions still govern edits and approval requests.
4. Try a model or effort change while the chat is working. Expect: a clear instruction to wait
   or stop first. Codex settings don't change a running turn as Claude's model/mode changes can.
5. To test draft preservation, put `/model` alone on the first line and a short unsent draft
   below it. Send. Expect: the model picker opens, and the text below stays in the reply box.
   A recognized control shouldn't appear as an ordinary prompt in the conversation.
6. Lists from Codex take a moment, and open only if you haven't moved on. Choose **Change Model**,
   and straight away type in the reply box or move to another control before it opens. Expect: no
   picker over your typing, and you hear that the list is ready but you've moved on, so nothing
   opened. Choose it again and it opens. This is hard to catch when Codex answers quickly.

A model, effort or mode that can't be verified is said and changes nothing; the chat can go on
sending. A chat on a model Codex no longer lists still opens, sends, and changes its permissions
and mode; only choosing a model needs it to be listed. If Codex has no Plan mode, a chat saved in
Plan says so and runs in Default. Codex Plan mode and its read-only/editing permission mode are
separate controls. File, **Change Permission Mode** (**Ctrl+Shift+O**) still changes the latter
when the chat is idle.

## Codex: skills, compaction, memories and Goals

1. If you have an enabled Codex skill for this folder, select it with **Ctrl+/**. Expect: `$name`
   appears in the reply box. Complete the request and send. The skill accompanies the message.
   If you queue or steer a skill request, its skill and any image inputs should stay with it.
2. In an idle chat with some history, choose and send `/compact`. Expect: native compaction and
   readable status. If you just changed settings and haven't sent a normal message yet, expect
   the instruction to send one first. Do that, wait for the turn to finish, then compact.
3. Send `/memories off`, then `/memories on`. Listen for confirmation of each accepted change.
   These choose whether this conversation uses and contributes to Codex memories; they don't
   delete your saved memories. Leave the test chat in the state you want.
4. Send `/goal`. Expect: a readable Goal and usage, or **No goal is set for this chat**.
   `/goal pause` and `/goal clear` are for an existing Goal. If none exists, a clear refusal or
   empty result is expected. Starting or resuming autonomous Goals isn't available here.

If your installed Codex doesn't support an endpoint, it should say so and keep the draft/chat
usable. A timed-out operation isn't resent automatically; check the result before retrying.

## Connected tools: both providers

Open **View, Connected Tools (MCP)**. Use the arrows to read servers and Tab/Shift+Tab to reach
every button and Details. Escape closes it and returns to the chat. An empty list is expected
when no servers or connectors are configured; skip the account-dependent steps in that case.

For Claude:

1. Check a configured server or claude.ai connector's name, source and status in the list and
   Details. A failed or signed-out tool should say why, rather than disappear.
2. Use **Sign In** if needed, finish in the browser and return to **Refresh**. **Reconnect**
   retries a failed connection. These steps need your real account.
3. In a chat started here, choose **Turn Off for This Chat**, then send a request. Expect: it is
   left out from the next turn, and it isn't started by reopening this list either. Turn it on again
   and confirm another chat's choice was unchanged. In a chat started elsewhere the list reads the
   same, but turning a tool off says it's only for chats The Chat Place started.
4. If a connected tool asks for a form while Claude is working, press **Ctrl+Shift+A**. Listen for
   the needs-you announcement. Expect: it opens on the read-only **Message** box. Press Enter there.
   Expect: nothing is sent, and you hear "Tab to the form, or to Send". Tab into the form and check
   that the questions, choices and focused field are named, that a number says its limits, and that
   each field's Alt key goes to it. A check box's Alt key moves to it and doesn't tick it. Fill it
   in and press Enter, or Send.
   With nothing to fill in, Enter on the message sends nothing and you hear "Tab to Send, or to
   Decline"; Send is one Tab away.
5. If it asks you to open a web page, **Ctrl+Shift+A** opens on an **Address** box above the message,
   with "Opens" and the host on its first line and then the whole address. Press Enter there and in
   the message. Expect: nothing opens, and you hear "Tab to Open in Browser, or to Decline". **Open
   in Browser** opens it.
6. A form with fields inside fields, or too many fields or choices, opens on a **Note** box saying
   why it can't be shown, with only **Decline** and **Answer Later**. Press Enter there and in the
   message. Expect: you hear "Tab to Decline". A field that may be left empty isn't one of these:
   it's an ordinary box you can leave blank.

Steps 4 to 6 need a server that actually asks; a tool that doesn't ask can't test them.

For Codex:

1. Start with a configured server off. **Copy with Tool On** must ask before creating a chat,
   explaining that the server can access external services beyond the folder sandbox.
2. Decline once. Expect: no new chat and no change to the source.
3. Accept for a trusted test server. Expect: a new read-only "(tools copy)" chat with the chosen
   server enabled. The original keeps its tool selection. Reopen Connected Tools to inspect it.
   **Copy with Tool Off** makes another copy; it doesn't change the tool set in the existing chat.
4. Expect **Read Status** (**Alt+A**) and **Refresh** to reread status. **Sign In** explains that
   Codex CLI handles sign-in and server setup. It doesn't offer Claude's sign-in workflow.
5. Ask the copy to use the enabled test server for a harmless read. Expect: the chosen server
   works, while unselected servers, apps and plugins remain off. Codex connected-tool questions
   that can't be displayed are declined with an explanation; Claude's forms aren't offered.
6. Open the list on a Codex chat started elsewhere. Expect: which servers are configured, read-only,
   with no copying or turning off.
7. In the tools copy, remove its server from Codex's configuration, then send. Expect: the turn is
   refused and says why. The list shows the removed server first, saying so; **Sign In** and
   **Reconnect** on it say the same, not that it's off for this chat. **Copy with Tool Off** makes
   a copy without it. Add the server back when you've finished.

## Stop a single background task

In a disposable chat, ask the provider to start two independent background commands that each
wait for about 90 seconds, and report when both are running without waiting for them to finish.

1. Choose File, **Stop Background Task**. In Codex you can also send `/background`. It's on for any
   Codex chat started here, and says if none are running.
2. Expect: a list identifying the work. Cancel once to leave both running.
3. Open it again, select one and confirm. Expect: only that task ends; the main turn and the
   other task continue. Listen for the result and check Turn Status or the task list afterward.
4. Wait for the other to finish while its list is open, then choose it. Expect: you hear it had
   already finished, not that it was stopped.
5. Stop the remaining test work when finished. If no native background tasks were started,
   report that rather than assuming this action stopped the main turn.

## Claude: subagents and chat options

1. Ask Claude to delegate a read-only inspection of the two sample files to a subagent, then
   summarize its report. Open View, **Subagents** (**Alt+V, G**), or **Ctrl+7** within Lists.
   Expect: the subagent's prompt, state and report. Listen for its completion announcement.
2. Press Enter on it. Expect: its conversation opens; Enter reads a message, **Ctrl+C** copies
   it and **Ctrl+T** shows tool activity. Escape returns to the subagent list or source chat.
   While it is working, new messages should arrive without forcing focus away from your reading.
   On a long conversation, Enter again while it's read says "Still reading that subagent's
   conversation". If you move away during the read, to another subagent in the list as much as
   out of Lists, it doesn't open over you, and you hear so. Tabbing to Details on the same subagent
   doesn't count: it still opens.
3. On the source turn's message menu, try **Subagent Conversations**. If you separately ask a
   subagent to edit a disposable file, Changed Files should include the change and its author.
   Codex subagents are listed too: `testing/codex-parity.md` has their checks.
4. In New Session or Continue Here, press **Alt+H** for **Chat Options**. Try instructions such
   as "End each reply with the word previewcheck", then start a chat and send a short question.
   Expect: the instructions take effect. An agent or output style can be chosen if configured;
   None/default is valid when you haven't added any. An agent with a model of its own is listed
   that way ("code-reviewer (uses its own model, opus)"), and OK refuses, saying why, one whose
   model could bill usage credits. Check Properties (**Alt+Enter**) afterward.
5. Use File, **Chat Options** to change instructions for an existing Claude chat. Expect: they
   apply from its next message; a running turn carries on with the options it started with.
   If testing More folders, add a separate disposable folder, inspect it in Chat Permissions
   (**Ctrl+Shift+H**), then remove it and check that the grant is removed.
6. In Continue Here, Tab to **Folder**. Expect: Tab stops there, with no scroll arrows and a box
   filled like a button, and a long path shown in full. Press Enter there. Expect: you hear "Tab
   to Title, or on to Start".
7. In New Session, choose Copilot, then press **Alt+D** and **Alt+E** with Model and Effort turned
   off. Expect: focus stays where it was, and the turned-off boxes report no Alt key. Choosing
   Claude gives both their keys back.

## Claude: live changes, thinking and context

1. While a Claude turn is working, use File, **Change Model** or **Change Permission Mode**.
   Listen for **now and for later turns** when the provider accepts the live change, or an
   explicit next-turn explanation if it can't change the active turn. Check the chat heading.
   Change the mode twice in quick succession. Expect: the heading stays on your last choice, with
   no "Claude Code switched" announcement for either.
2. When Claude switches itself to Plan mode, you hear "Claude Code switched to plan mode", without
   it cutting off what's being read. Expect the next turn to remain in Plan until you approve the
   plan or change mode. Check that approving it uses the appropriate previous mode. A wider mode
   set elsewhere, such as over Remote Control, is said as "for this turn. Later turns start in"
   the mode you chose, and later turns do.
3. Turn on **Show Tool Activity** (**Ctrl+T**). Where Claude recorded its thinking, expect it
   before the reply. No recorded thinking isn't itself an error.
4. After a completed turn, open **Usage and Context** (**Ctrl+Shift+U**). Expect the reported
   context categories, such as tools, memories, skills, messages and free space, to be readable.
5. If a retry, model fallback or memory save happens naturally, check Turn Status and the
   announcements. Retries should include their attempt and delay; memory-save announcements
   need the Full announcement level. You don't need to disconnect your PC to force these.

## Sounds

Sounds are off until turned on. Nobody has heard them yet: their levels and timing were measured
in a VM through a virtual sound card, so what's left is whether they're pleasant and how they sit
with a screen reader. User Guide, **Sounds** lists every sound.

1. Choose File, **Sounds**. Tab through it both ways. Expect: each control is named, and the
   **Sound events** list reads each row as the event and its sound. Turn on **Play sounds** and
   choose OK.
2. In **Sound events**, arrow to a few events and press **Space** on each (on a Mac too), then
   try **Preview** once. Expect: the sound plays, and nothing is said over it. Try each **Sound scheme**
   (Glass, Soft bell, Wood) and a lower **Volume**. Expect: each plays at the volume showing, and
   none is harsh or clipped.
3. In a disposable chat, with **Show Tool Activity** off (**Ctrl+T**), ask for something that
   runs a few commands and edits a test file. Expect: a soft tap per tool, Changed when the edit
   works, and Arrived when the reply finishes. Listen for whether the taps get in the way of
   speech or are hard to hear under it.
4. Ask for something that needs your permission. Expect: the Attention sound with the
   announcement, not instead of it. Stop a turn (**Ctrl+Period**). Expect: Stopped.
5. While a turn runs in another chat, stay in this one. Expect: the softer Elsewhere when it
   finishes.
6. Press **Ctrl+Shift+X**. Expect: "Sounds muted", and silence, including a sound already
   playing. Press it again for "Sounds on".
7. With **No Windows sound for The Chat Place's notifications** turned on, let a chat finish
   while you're in another program, then select the notification. Expect: only The Chat Place's
   sound, and its chat opens. Nobody has checked that the chat still opens with this setting on.
8. Try one Codex chat as well. A Codex subagent's own tools make no sound yet (#570).
9. **Sound schemes.** Press **Alt+A** (Scheme Actions). Expect: a menu of New, Copy, Rename,
   Delete, Reset, Export and Import Scheme, with Rename and Delete turned off on Glass (on Windows
   and a Mac, read as turned off). Choose **New Scheme**, name it, and check that the events list says "0 of
   15 events have a sound" and that **Sound** offers tones such as "Arrived (Soft bell)". Give
   two events a sound, then **Export Scheme** and **Import Scheme** the file. Expect: "Added the
   sound scheme ..." is said, and the copy has the same sounds.
10. **Browse Gallery** (**Alt+B**). The gallery isn't open yet. Expect: "Loading the sound
    scheme gallery…", then "The gallery could not be reached", with focus on **Reload**. Escape
    closes it and you're back on Browse Gallery.

## Remaining listening checks and reporting

With JAWS, and with NVDA if available, pay particular attention to the command picker, attachment
list and Ask Aside. These still need human listening. Each result, file name, provider name,
request and changed state should be spoken once at the appropriate announcement level. Check
both Tab directions and Escape, and note any focus that lands somewhere unexpected.

CI runs the full test suite on the branch's pull request (#468). Real model responses, third-party
authentication and these listening checks still need verification by a person.

Still outside this preview: starting/resuming autonomous Codex Goals, native dedicated review,
plugin installation, and cloud/remote execution. Codex subagents, app connectors and extra
folders for a chat came with the parity work; see `testing/codex-parity.md`.
Existing appearance issue #181 about native list focus indicators is still open.

For each finding, record the provider, section and step, chat type, what you did, expected
behavior, what actually happened, and what JAWS or NVDA said. Note your provider version and
whether the turn was idle or working. Help, **Report a Bug** shows diagnostic details before
anything is sent. Don't include private messages or account credentials in a public report.
