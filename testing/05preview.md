# The Chat Place 0.5 preview: testing guide

This Windows preview contains the combined Claude (#468) and Codex (#467) feature work on
`codex/feature-gaps`, built from commit `cec6d7be09993b30afbe0e7289ba3e805cfe90ee` on
November 1, 2026. The app still calls itself **0.3.3**: the version has not been bumped for the
planned 0.5.0 release. Your v04 folder remains the preview of the release being prepared on main.

## Start here

1. Close any running copy of The Chat Place.
2. Open `TheChatPlace-windows-Portable`, then run **The Chat Place.exe**. The folder is already
   extracted for you. If using the zip elsewhere, extract the whole zip before running it.
   The portable copy leaves your installed program in place. Both use your normal chats and
   settings, and only one copy can run at a time.
3. If you want to keep a backup first, use File, **Back Up Now**, and save it outside this
   preview's folder. The guide's file tests change only the test files you choose.
4. Use the supplied **Test files** folder as the working folder for new test chats. It includes
   `preview-notes.txt` and `rewind-example.txt`. Use your own disposable folder if you prefer.
   Keep the file-restoration test separate from work you want to keep.
5. Check Help, **Claude Sign-in** and **Codex Sign-in**. New replies need the appropriate
   signed-in provider. Codex must be enabled in Settings; enabling it requires a restart.
   The implementation was checked against Codex 0.163.0 and Claude Code 2.1.296. Older provider
   versions may report that an operation is unavailable.

`TheChatPlace-windows-Setup.exe` is also included. It installs over the installed app; it isn't
a separate preview installation. The build is unsigned, as local Windows builds normally are.
Windows may offer More info, then Run anyway. Use the portable copy for these tests.

The full user guide and planned 0.5.0 release notes are beside this guide. **Build information.txt**
records this build's source and checks. None of the listening checks below has been marked as
passed on your behalf. Each question or message uses the selected provider's plan.

## A quick first pass with each provider

Repeat this section in a new Claude chat, then a new Codex chat, in the Test files folder.

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

## Ask Aside: both Claude and Codex

1. Load a chat with a few replies. Press **Ctrl+Shift+Y** (File, Ask Aside).
2. Type "What did I ask you to do first?" and press **Ctrl+Enter**.
   Listen for **Asking Claude** or **Asking Codex**, matching the source chat.
3. Expect: focus moves to **Answer**, the answer is announced according to your announcement
   setting, and you can read it by line. **Copy Answer** copies the selected answer.
4. Ask a second question. Tab to **Questions and answers**, move through both entries, and press
   Enter. Expect: the corresponding answer opens for reading.
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
   In **Rewind to Here**, read **What happens**. First press Escape. Expect: focus returns to the
   selected message and no copy is made. Reopen it and choose **Conversation only**, then Rewind.
3. Expect: a new "(rewound)" chat ending before that user message, with the message in its reply
   box ready to edit and send. The original conversation and files are kept.
4. For Codex, listen for **Conversation only** selected, and file choices unavailable. Its new
   copy starts read-only, with Codex's configured model and effort and connected tools off.
   A source without a saved turn can't be branched yet; branching after a turn waits for it to
   finish. Check that the explanation is readable, rather than treating refusal as success.
5. For Claude, select a user message that led to an edit of `rewind-example.txt`. For this test,
   ask Claude explicitly to use its Edit or Write tool, without a command or subagent. Confirm
   that in Show Tool Activity (**Ctrl+T**), and note the file's before and after contents.
6. Open Rewind on that user message. Read which files it will restore. Try **Files only**.
   Expect: the file returns to its earlier contents, the conversation stays, and the result is
   announced. Make a fresh edit and try **Conversation and files** as well.

Claude file restoration applies only to chats started here and messages sent with this preview
or later. It doesn't restore changes made by commands or subagents. Those files should be
identified in What happens. Older messages or chats started elsewhere may offer conversation
rewind without file restoration. Codex Rewind never rolls files back.

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

Codex Plan mode and its read-only/editing permission mode are separate controls. File,
**Change Permission Mode** (**Ctrl+Shift+O**) still changes the latter when the chat is idle.

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
   left out from the next turn. Turn it on again and confirm another chat's choice was unchanged.
4. If a connected tool asks for a form or a web page while Claude is working, press
   **Ctrl+Shift+A**. Read the request, fill in its fields or open its page, and answer. Listen for
   the needs-you announcement and check that the questions, choices and focused field are named.
   This requires a server that actually asks questions; a tool that doesn't ask can't test it.

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

## Stop a single background task

In a disposable chat, ask the provider to start two independent background commands that each
wait for about 90 seconds, and report when both are running without waiting for them to finish.

1. Choose File, **Stop Background Task**. In Codex you can also send `/background`.
2. Expect: a list identifying the work. Cancel once to leave both running.
3. Open it again, select one and confirm. Expect: only that task ends; the main turn and the
   other task continue. Listen for the result and check Turn Status or the task list afterward.
4. Stop the remaining test work when finished. If no native background tasks were started,
   report that rather than assuming this action stopped the main turn.

## Claude: subagents and chat options

1. Ask Claude to delegate a read-only inspection of the two sample files to a subagent, then
   summarize its report. Open View, **Subagents** (**Alt+V, G**), or **Ctrl+7** within Lists.
   Expect: the subagent's prompt, state and report. Listen for its completion announcement.
2. Press Enter on it. Expect: its conversation opens; Enter reads a message, **Ctrl+C** copies
   it and **Ctrl+T** shows tool activity. Escape returns to the subagent list or source chat.
   While it is working, new messages should arrive without forcing focus away from your reading.
3. On the source turn's message menu, try **Subagent Conversations**. If you separately ask a
   subagent to edit a disposable file, Changed Files should include the change and its author.
   Codex subagent conversations aren't supplied by this preview.
4. In New Session or Continue Here, press **Alt+H** for **Chat Options**. Try instructions such
   as "End each reply with the word previewcheck", then start a chat and send a short question.
   Expect: the instructions take effect. An agent or output style can be chosen if configured;
   None/default is valid when you haven't added any. Check Properties (**Alt+Enter**) afterward.
5. Use File, **Chat Options** to change instructions for an existing Claude chat. Expect: they
   apply from its next message; a running turn carries on with the options it started with.
   If testing More folders, add a separate disposable folder, inspect it in Chat Permissions
   (**Ctrl+Shift+H**), then remove it and check that the grant is removed.

## Claude: live changes, thinking and context

1. While a Claude turn is working, use File, **Change Model** or **Change Permission Mode**.
   Listen for **now and for later turns** when the provider accepts the live change, or an
   explicit next-turn explanation if it can't change the active turn. Check the chat heading.
2. When Claude switches itself to Plan mode, expect the next turn to remain in Plan until you
   approve the plan or change mode. Check that approving it uses the appropriate previous mode.
3. Turn on **Show Tool Activity** (**Ctrl+T**). Where Claude recorded its thinking, expect it
   before the reply. No recorded thinking isn't itself an error.
4. After a completed turn, open **Usage and Context** (**Ctrl+Shift+U**). Expect the reported
   context categories, such as tools, memories, skills, messages and free space, to be readable.
5. If a retry, model fallback or memory save happens naturally, check Turn Status and the
   announcements. Retries should include their attempt and delay; memory-save announcements
   need the Full announcement level. You don't need to disconnect your PC to force these.

## Remaining listening checks and reporting

With JAWS, and with NVDA if available, pay particular attention to the command picker, attachment
list and Ask Aside. These still need human listening. Each result, file name, provider name,
request and changed state should be spoken once at the appropriate announcement level. Check
both Tab directions and Escape, and note any focus that lands somewhere unexpected.

The earlier automated and visual results are recorded in **Validation notes.md**. They include
passing focused tests and reviewed Light and High Contrast captures. The complete suite hasn't
had a clean final rerun; real model responses, third-party authentication and these listening
checks still need verification. Build-specific results belong in **Build information.txt**.

Still outside this preview: starting/resuming autonomous Codex Goals, Codex subagent history,
native dedicated review, apps/plugins, cloud/remote execution and multi-folder scope controls.
Existing appearance issue #181 about native list focus indicators is still open.

For each finding, record the provider, section and step, chat type, what you did, expected
behavior, what actually happened, and what JAWS or NVDA said. Note your provider version and
whether the turn was idle or working. Help, **Report a Bug** shows diagnostic details before
anything is sent. Don't include private messages or account credentials in a public report.
