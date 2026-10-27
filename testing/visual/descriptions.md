# How The Chat Place looks

Written from the visual probe's pictures (#155), on made-up data, on 2026-10-10.
Updated on 2026-10-16 for 0.1.7 (#283, #226) from light-100 and hc-aquatic-100 pictures of
every screen changed or added since 0.1.6: the Codex screens, Lists, Settings, Session List
Columns, the help pages and the main window.
Regenerate it with `tools/ui_review_prompt.md` whenever screens change.

**Since 0.1.6, in every main-window picture:** each session row now says which AI it is
straight after the title ("Fix the flaky upload test, Claude, AIChat, needs you: ..."), so
less of the rest of the row fits in the 378-wide list. Nothing else in those windows moved.

Every light-100 picture was opened, along with the dark, High Contrast and scaled pictures named below. Sizes come from the probe's JSON and are in pixels at 100% scale. Colours were sampled from the pictures; contrast ratios are WCAG ratios.

## The app at a glance

The Chat Place looks like a plain, older-style Windows program built from standard Windows controls. The window background is light grey (#F0F0F0), lists and text boxes are white with thin grey borders, text is black, and buttons are small, near-white rectangles with a thin grey border and slightly rounded corners. Everything uses the standard Windows interface font (Segoe UI, about 9 point), so the text is small and the layout is dense, with 6 to 10 pixels between controls. There are no icons, toolbars, colours or pictures apart from the small blue speech-bubble icon in the main window's title bar. The selected row in every list is a solid blue bar (#0078D4) with white text (4.5:1). The list that has keyboard focus differs only by a thin dotted outline around its selected row, which shows as orange and blue dots (#FF872B on #0078D4, 1.9:1). The default button in a dialog has a blue outline. The three pages shown in a web view (Keyboard Shortcuts, User Guide and a formatted message) look quite different: larger text (about 12 point), bold black headings, tables with thin grey lines and a pale grey code box, like a simple web page.

The main window in the pictures is 1000 by 720, with a 984 by 630 inside area. A menu bar with File, View and Help is at the top and a status bar is at the bottom. Dialogs range from 272 by 126 (Update Installed) to 860 by 640 (What Claude Knows About You); most are between 620 and 760 wide.

### Themes and scaling

- **Dark mode:** In Windows dark mode for apps (dark-100), the main window and Settings are identical to light mode, pixel for pixel: light grey and white with black text (#177). Only the web view pages turn dark. The formatted message has a near-black page (#1E1E1E) with light grey text (#E8E8E8, 13.6:1), mid-grey table lines, a light blue link (#8AB4F8, 7.9:1) and a code box only slightly lighter than the page (#2B2B2B). The dialog's frame and its two buttons stay light grey around it, so a dark page sits inside a light frame.
- **High Contrast Aquatic:** High Contrast Aquatic (hc-aquatic-100) repaints everything near-black (#202020) with white text (16.3:1). Buttons, text boxes, lists, dropdowns and group frames get thin white outlines, and dropdown arrows and scroll bars turn into classic square boxes. Selected rows are light aqua (#8EE3F0) with near-black text (11.2:1), and the default button (OK in Settings) is filled light aqua. The formatted message follows the theme too: near-black page, white text, white table lines and an aqua link (#75E9FC).
- **High Contrast Desert:** High Contrast Desert (hc-desert-100) is the light version of the same thing: a cream background (#FFFAEF) everywhere with near-black text (#202020, 15.7:1) and thin black outlines. Selected rows and the default button are rust brown (#903909) with cream text (7.3:1). Its formatted message has dark grey text (#3D3D3D), dark grey table lines and a dark teal link (#1C5E75, 6.9:1). In Desert the title bars are mid-grey with pale grey title text (1.2:1), so the window titles almost vanish; the probe pictures windows while they aren't the active window, and this is how Windows draws an inactive title in that theme.
- **150% and 175% scaling:** At 150% and 175% display scaling (light-150, light-175), the app draws itself at the screen's scaling, so the letters are as sharp as at 100% (#185 fixed this: Windows used to stretch a 100% picture, and the text was soft). Sizes grow with the scaling: the main window is 1500 by 968 at 150% and 1750 by 956 at 175%, each as big as the screen allows less a 40-pixel margin, and no window or dialog runs under the taskbar (#186). The gaps between controls are still the 100% ones, so controls sit a little closer together than at 100%.

## main-start: Main window as it opens: session list, no session loaded

The window is split into two columns. On the left, "Session list:" heads a white list box 378 wide that runs the full height (590 tall), about 38% of the width. It holds four one-line sessions, the last with a deliberately long title ("A session whose title goes on and on to show what a very long title doe..."). The first is selected in blue with the dotted focus outline, and long rows are cut off at the list's right edge with no horizontal scroll bar. On the right, after a 16-pixel gap, "Messages:" heads a white list 574 wide and 512 tall. Its only row, "No session loaded. Choose one in the session list and press Enter.", is also selected in blue. Below that list are a "Show tool activity" check box and, side by side, "New Session..." and "Refresh" buttons. The status bar is split into three parts: "4 sessions: 1 need you, 0 working." on the left (464 wide), "No session loaded" in the middle (312 wide), and a button "1 session needs you" on the right (146 wide).

**What looks off:** Both lists show a solid blue selected row, so it is hard to tell which one has focus (#181). Most of the right column is empty white space.

## main-own: An own session loaded: messages and the reply box

This picture has the same two columns as main-start, with the session list 378 wide. At the top right, a plain-text line reads "Visual probe, Scratch, idle. Chat Place session on the default model." It fits on one line. Under it, "Messages in Visual probe (idle, on the default model):" heads the messages list (574 by 323, about half the height). Its two rows are "You: Make the uploader flush before it signals." and the selected "Claude: ## What changed", which has the focus outline. Below the list, "Your message (Ctrl+Enter sends):" heads a white multi-line box 120 tall with a vertical scroll bar. Under that is a row of four buttons, "Send", "Stop", "Commands..." and "Attach Files...", and under the buttons, on a line of its own, the word "Ready." (#179 moved it there from beside the buttons). Then come "Show tool activity", "New Session..." and "Refresh" as before. The status bar reads "Loaded Visual probe, 2 messages." and "Visual probe: idle", with the "1 session needs you" button on the right.

**What looks off:** The message row shows the raw Markdown "## What changed". The session list's selected row is just as blue as the focused messages row (#181). The message area's buttons are all the same small grey size, so Send doesn't stand out.

## main-own-working: An own session mid-turn, one more message queued and a draft typed

The layout matches main-own, and the split stays where it is: the session list is 378 wide and the right column 574, as in main-own. The status line under the buttons is longer, "1 message queued. Claude is working (1 minute 15 seconds).", and fits on its line. The heading line and the label above the messages now say "working": "Visual probe, Scratch, working. Chat Place session on the default model." and "Messages in Visual probe (working, on the default model):". The session's own row in the list says "working" too, and the status bar's left part reads "4 sessions: 1 need you, 1 working.". The messages list gains a third row, "Queued: And then open a PR.". The reply box holds "Draft of a message still being typed" with the text cursor in it. The middle part of the status bar is too short for "Visual probe: 1 message queued. Claude is working (1 minute 15 seconds).", so it ends in "..." before the "1 session needs you" button.

**Fixed:** The column split used to jump 110 pixels when the status text changed length, so the session list lost almost a third of its width while a turn ran; the status bar's middle text was cut mid-word (#179). The split now stays put, and text that doesn't fit ends in "...".

## main-attachments: An own session with two files attached to the next message

This picture is the same as main-own, except that a small white list 48 tall appears under the Send/Stop/Commands/Attach Files row and the "Ready." line below it. It shows "screenshot of the error.png" (selected in blue) and "upload.log". To make room, the messages list drops to 269 tall. The attachment list has no heading of its own, so it looks like a third, unlabelled list sitting under the buttons.

**What looks off:** There is no visible label for the attachments list. The bottom of its border sits 6 pixels from the "Show tool activity" check box, which is tighter than the spacing elsewhere. Three lists now show a blue selected row at once (#181).

## main-desktop: A desktop app session loaded: the read-only panel instead of a reply box

Here the session list is 378 wide and the right column 574 wide, the same split as main-own. The heading at the top right, "Fix the flaky upload test, AIChat, needs you: Pick a name for the release branch. Claude desktop app session, read-only.", is too long for one line, so it wraps onto two (574 by 31), breaking before "session, read-only.". Everything below moves down 15 pixels to make room. "Messages in Fix the flaky upload test (needs you: Pick a name for the release branch, read-only):" heads the messages list and fits on its line in full, so it shows no "...". The messages list is 574 by 328 and shows four rows, with the last one, "You: Thanks. Which branch name should the release use?", selected. Where the reply box would be, "About replying:" heads a read-only box of the same size. It holds a four-line paragraph explaining that the session belongs to the Claude desktop app. Under it are two buttons, "Open in Claude" and "Continue Here...". The rest matches main-own. The status bar's middle part is too short for its text and ends in "..." (#179).

**What looks off:** The read-only box looks just like an editable reply box. The status-bar text doesn't fit, though it now ends in "..." rather than mid-word (#179). The column split matches main-own, so the list doesn't move sideways between kinds of session.

## main-activity: Show tool activity turned on

This picture is the same as main-desktop, but the "Show tool activity" box is ticked (a blue check box), and the messages list has six rows. Two new rows appear between Claude's messages: "Tool: Bash: pytest tests/test_upload.py -q" and "Tool result: Bash returned: 1 failed, 11 passed". They look exactly like the other rows, in the same font and colour with no indent. The status bar's left part reads "Tool activity shown in Fix the flaky upload test." (#162: the setting is per session now).

**What looks off:** Tool rows can't be told apart from messages at a glance, apart from the "Tool:" word at the start. The status bar's middle text ends in "..." as in main-desktop.

## main-last-message: The session list with the Last message column (#146)

This picture is identical to main-start except for the session rows. The second row now ends "...Chat Place session, Claude:", the start of the last-message text, and is cut off at the list's right edge. Nothing else in the picture shows the new column, because the list is too narrow to show more than the start of it.

**What looks off:** The last-message text is cut off after one word, because each row already fills the 378-pixel list (#180).

## main-status-bar: The status bar with every button showing: context, needs you, update

The window matches main-own. The status bar now has five parts: "Loaded Visual probe, 2 messages." (248 wide), "Visual probe: idle" (168), and three buttons 155, 175 and 166 wide: "Context 42% full", "1 session needs you" and "Update 0.2.0 ready". The buttons look like near-white boxes with a thin grey border and centred text, filling the right 51% of the status bar.

**What looks off:** The status bar is 31 tall, so all three buttons show their whole text (#175 fixed this: they were 16 tall and cut the bottoms off letters). The buttons sit close together with a gap of only 5 pixels.

## main-narrow: An own session in a 640 by 480 window

The same layout is squeezed into 640 by 480. The session list is 234 wide and 350 tall, and its rows are cut after about 40 characters ("Fix the flaky upload test, AIChat, needs you:"). The right column is 358 wide; the heading line wraps onto two lines, and "Messages in Visual probe (idle, on the default model):" fits on one. The messages list shrinks to 68 tall, enough for about four rows, and the reply box stays 120 tall. The button row "Send / Stop / Commands... / Attach Files..." fills the column, and "Ready." sits whole on its own line under it (#179). The status bar holds "Loaded Visual probe, 2 messages.", "Visual probe: idle" and the "1 session needs you" button.

**What looks off:** The reply box is almost twice as tall as the messages list, so the conversation gets less room than the draft. #179 made this worse (the list was 103 tall): the status line under the buttons and the wrapped heading take two more lines. "Ready." is no longer cut off.

## main-compacted: Compaction and command output with tool activity off (#249)

The same window as main-own, with two more rows in the messages list after Claude's: "Context: Conversation compacted (you asked), from 968,000 to 9,000 tokens." and "Claude Code: Compacted", which is selected. They look like every other row. In High Contrast Aquatic the window is near-black with white text and the selected rows light aqua.

**What looks off:** Nothing. The status bar still says "Loaded Visual probe, 2 messages." with four rows showing, because the two new rows aren't messages.

## main-codex: A Codex chat read from Codex, in the shared window

The same two columns as main-desktop. The session list has a fifth row, "Codex history preview, Codex, Scratch, active 2 hours ago, external sess...", selected. The heading reads "Codex history preview, Scratch, status unavailable. Codex chat, read-only." and "Messages in Codex history preview (read-only):" heads two rows, "You: Explain the upload test." and "Codex: ## What changed" (selected). Where the reply box would be, "About replying:" heads a read-only box with one line, "This is a Codex chat. The Chat Place currently reads its local history. Reply in Codex.", and there are no Open in Claude or Continue Here buttons. The status bar reads "Loaded Codex history preview, 2 messages." and "Codex history preview: status unavailable". High Contrast Aquatic follows the theme like the other main-window pictures.

**What looks off:** The About replying box is 150 tall for one line. The message row shows raw Markdown, as Claude's do.

## main-codex-owned: A Codex chat started here, mid-turn, with a message queued and a draft

Like main-own. The chat's row has moved to second place in the session list and reads "Codex history preview, Codex, Scratch, working, active 2 hours ago, Ch...", and the status bar's left part reads "5 sessions: 1 need you, 1 working.". The heading is "Codex history preview, Scratch, working. Codex chat." and the label "Messages in Codex history preview (working, on Codex):". The messages are "You: Explain the upload test.", "Codex: ## What changed" (selected) and "Queued: Run the unit tests next.". The reply is selected; the queued message remains immediately below it without selection. The reply box holds "Draft of a message still being typed", with Send, Stop, Commands... and Attach Files... under it and "Codex is working. 1 message queued." below them. The status bar's middle part reads "Codex history preview: working".

**What looks off:** Nothing.

## main-codex-interrupted: A Codex chat whose connection was interrupted

The owned chat after Codex sent a request the app can't answer. The heading wraps onto two lines: "Codex history preview, Scratch, status unavailable: Codex connection interrupted: an unsupported approval or question was received. Use Stop to reconnect. Codex chat.", with one full stop after "reconnect". The session row and the status bar say the same, cut with "...". The line under the buttons shows whole at every size: "Codex connection interrupted: an unsupported approval or question was received. Use Stop to reconnect." The Messages label reads "Messages in Codex history preview (working, on Codex):", because a state change isn't put on the label while the messages list has focus (#372). The messages are the two history rows; the queued message is gone.

**What looks off:** Nothing.

## main-codex-request-declined: A working Codex chat after a request was declined

Like main-codex-owned. As there, the Codex reply "Codex: ## What changed" is the selected message and "Queued: Run the unit tests next." sits unselected below it (#343; the earlier picture had the queued row selected). The status bar's left part reads "Codex history preview: An unsupported Codex request was declined. No permission ..." (cut with "..."), and the middle "Codex history preview: working".

**What looks off:** The notice is visible only in the status bar's left part, and cut off there. A screen reader speaks it when it arrives.

## main-codex-context: A working Codex chat with its context reported

Like main-codex-request-declined, with the reply selected and the queued row below it, and a fourth status-bar part, a button "Context 85% reported", between the middle part and "1 session needs you". The left part reads "Codex history preview: Codex's latest reported model request uses ..." (cut with "..."), and the middle "Codex history preview: working".

**What looks off:** Nothing beyond the cut status text, which is normal for that part.

## main-codex-filter: The session list showing only Codex chats, with a Provider column

The list's label reads "Session list, Codex chats, 1 of 5:", and the list holds one row, "Codex history preview, Codex, Scratch, status unavailable" (selected). The right column is as in main-codex.

**What looks off:** The rest of the list is empty white. Nothing else.

## main-codex-last-message: Codex chats with the Last message column

The same filtered list with the Last message column: "Codex history preview, Codex, Codex: What changed The upload test fa..." cut at the list's edge. The heading's "## " shows as "What changed" here, without the hashes, and runs straight into the next sentence.

**What looks off:** The row is cut off at the list's edge (#180).

## main-codex-unavailable: Codex chats listed from before Codex discovery failed

The same as main-codex, except that the status bar's left part reads "Codex session discovery is unavailable. Its listed chats may be out of date."

**What looks off:** Nothing.

## main-copilot: A GitHub Copilot CLI chat in the shared window

The main window with a synthetic Copilot CLI chat added to the list and opened (#352, #355). The session list's fifth and last row, selected, reads "Copilot history preview, Copilot, Scratch, idle, active just now, external" (cut there) and is cut at the list's edge like the rows above it; the other four are Claude's. The right column's summary reads "Copilot history preview, Scratch, idle. Copilot chat, read-only." The messages list, labelled "Messages in Copilot history preview (idle, read-only):", holds "You: Explain the upload test." and "Copilot: ## What changed" (selected, white on Windows blue, 4.5:1). The heading's hashes show here just as they do for Claude in main-own. In place of a reply box, a read-only "About replying:" box says "This is a Copilot chat. The Chat Place currently reads its local history. Reply in Copilot CLI." Below it are "Show tool activity" (unticked), "New Session..." and "Refresh", with no Send or Cancel. The status bar reads "Loaded Copilot history preview, 2 messages.", "Copilot history preview: idle" and a "1 session needs you" button. In High Contrast Aquatic the selected message is dark blue-grey on light aqua (#263B50 on #8EE3F0, 7.9:1), and in Desert cream on brown (#FFF5E3 on #903909, 7.0:1). The About replying text is 16.3:1 in Aquatic and 10.4:1 in Desert. dark-100 looks the same as light-100 (#177). At 175% everything is in view, larger.

**What looks off:** Nothing beyond the cut session rows, which is normal for that list.

## main-copilot-filter: The session list showing only Copilot CLI chats

View, Show Sessions, GitHub Copilot Chats. The list's label reads "Session list, GitHub Copilot chats, 1 of 5:", and the list holds one row, "Copilot history preview, Copilot, Scratch, idle" (selected), with the Provider column shown. The right column is as in main-copilot.

**What looks off:** The rest of the list is empty, as in main-codex-filter. Nothing else.

## main-copilot-owned: A Copilot chat started here, mid-turn, with a message queued and a draft

Like main-codex-owned, for Copilot (#352, #372). The chat's row is second in the session list and reads "Copilot history preview, Copilot, Scratch, working, active just now, Cha..." (cut at the list's edge). The heading is "Copilot history preview, Scratch, working. Copilot chat." and the label "Messages in Copilot history preview (working, on Copilot):". The messages are "You: Explain the upload test.", "Copilot: ## What changed" (selected, white on Windows blue, #FFFFFF on #0078D4, 4.5:1) and "Queued: Run the unit tests next.". The reply box holds "Draft of a message still being typed", with Send, Stop, Commands... and Attach Files... under it and "Copilot is working. 1 message queued." below them. The status bar reads "5 sessions: 1 need you, 1 working.", "Copilot history preview: working", a "Context 32% reported" button and "1 session needs you". In High Contrast Aquatic the selected message is #263B50 on light aqua #8EE3F0 (7.9:1), with the usual white outlines on near-black.

**What looks off:** Nothing beyond the cut session rows, which is normal for that list.

## shortcuts-page: Help, Keyboard Shortcuts (formatted page)

This is an 820 by 620 dialog. A web page fills almost all of it (796 by 534) inside a thin grey border, with a grey vertical scroll bar on the right. The page has a large bold "Keyboard shortcuts" heading, an intro paragraph (five lines in 0.1.7, now naming the Provider and Kind columns), then a "Moving around" heading and a two-column table, "Keys" and "What it does". Keys are in bold, and the table has thin grey grid lines and generous row padding. The text is about 12 point, noticeably bigger than the rest of the app. At the bottom right are two small buttons, "Read as Plain Text" (115 wide) and "Close".

**What looks off:** The table's last visible row is cut through mid-line at the bottom, which is normal for a page that scrolls. The buttons look tiny next to the large page text. Nothing else.

## shortcuts-plain: Keyboard Shortcuts as plain text

This is a smaller 620 by 520 dialog. "Shortcuts:" heads a white read-only text box 588 by 402 that fills most of the dialog. It holds the same content as plain text. It opens with a five-line paragraph about the window's three parts in Tab order, ending with Provider and Kind as default session-list columns, then section names such as "Moving around:" and "Session list:" on their own lines, and each shortcut indented two spaces, as in "Ctrl+1: Go to the session list". Long lines wrap with no hanging indent, so continuation lines start at the left margin. The text is the small standard font. A single "Close" button sits at the bottom right. The 2026-10-25 run's text adds New Agent Conversation (Ctrl+Shift+G) and the agent-conversation use of Ctrl+Shift+E (#347), What AI Knows About You with Claude or Codex (Ctrl+Shift+K, #371) and Codex's approval reviewer under Change Permission Mode (Ctrl+Shift+O, #374); all of these are below the first screenful, so the light-100 and hc-aquatic-100 pictures look as before. At 175% the first screenful now shows the longer opening paragraph, five lines, so everything below it starts two lines lower. Since the user guide review (#392), the Session list section's Ctrl+Shift+L line reads "Copy Session Link (Claude chats): ..." and wraps onto three lines at 100% (two at 150% and 175%). "Ctrl+N: New Chat Place session" is gone from that section, so Ctrl+Shift+N follows Ctrl+Shift+L directly; Ctrl+N is now listed among the shortcuts that work from anywhere, below the first screenful.

**What looks off:** Wrapped lines aren't indented, so they run into the next shortcut visually. The bottom line is cut through, as the box scrolls. Nothing else.

## user-guide: Help, User Guide (formatted page)

This dialog is laid out exactly like shortcuts-page, 820 by 620, with the web page filling the dialog and "Read as Plain Text" and "Close" at the bottom right. The page starts with a large bold heading, "The Chat Place User Guide", then five paragraphs: Windows and Mac key names; what the app is (Claude, Codex and GitHub Copilot CLI chats in one list, with **Provider** and **Kind** in bold, more providers planned, and a pointer to the "What works for each provider" table); that the app's menus say "session" where the guide says "chat"; how the guide is arranged, ending with its glossary; and the independence line naming Anthropic and OpenAI, whose second line is cut through by the bottom of the view, as the page scrolls. Since the user guide review (#392) no heading after the title is in view at 100%, 150% or 175%; the earlier picture had "Before you start" at the bottom. The scroll thumb is short, showing that the page is long. In dark-100 the page is dark grey (#1e1e1e) with light grey text (#e8e8e8, 13.6:1). In High Contrast Aquatic the page is near-black (#202020) with white text (16.3:1); in Desert, cream (#fffaef) with dark grey text (#3d3d3d, 10.4:1).

**What looks off:** Nothing.

## message-formatted: A message read as a formatted page: heading, list, code, table, link

This uses the same 820 by 620 frame as the other formatted pages, titled "Message: Claude" (it was "Message from Claude"). The page starts with a large bold "Claude" heading, who wrote the message (heading 1, #341). Below it is a smaller bold "What changed" heading, one level down, and a paragraph. In the 2026-10-22 probe only light-100 and hc-aquatic-100 were taken again; the other variants' pictures still show the page without the "Claude" heading. Next is a two-item bulleted list whose file names are in a monospaced font, then a code block of four Python lines on a pale grey (#F3F3F3) rounded box with no border. Then comes a small three-column table (File, Lines added, Lines removed) with bold headers and thin grey grid lines, which takes about half the page width. Last is a paragraph with a blue underlined link, "the issue", followed by many repeated words that wrap across the full width.

**What looks off:** Nothing in light mode, where the link is #0000EE on white (9.4:1); in High Contrast Aquatic it is #75E9FC on #202020 (11.5:1). The code box is very faint against the white page (1.1:1), so the code is set apart mainly by its font. In dark mode the page turns near-black but the dialog around it and its buttons stay light grey (#177).

## message-plain: The same message as plain text

This is a 720 by 520 dialog titled "Message: Claude" (it was "Message from Claude"; #255 named the speaker by provider). "Claude:" (it was "Claude said:") heads a white read-only text box 688 by 402, with a single "Close" button below it on the right. The box shows the raw Markdown in the small standard font: "## What changed", list lines starting with "- " and backticks, the code fenced with three backticks and "python", the table as pipe characters and dashes, and the link written out in full, "[the issue](https://github.com/example/repo/issues/42)". The text wraps at about 80 characters in places and runs wider in others. The code is in the same proportional font as everything else.

**What looks off:** The line lengths are uneven: the first paragraph wraps early, around 430 pixels, while the long paragraph uses the full width. Nothing else.

## settings: Settings

This is a 680 by 651 dialog (550 in 0.1.6; the Codex check box added 40, the task-list check box another 40, #231, and the agent conversations check box 21, #248). At the top, a framed group, "Announcements", runs the full width with three radio buttons: "Full: read the whole reply" (chosen, with the focus outline), "Summary: the session's name and the reply's first sentence" and "Silent: status bar only". Below it are two ticked check boxes, then a second framed group, "Reading messages", with two more ticked boxes. Then come label-and-dropdown rows. "Speech engine" has a dropdown 555 wide, "Automatic (your screen reader, or a system voice when none is running)". "Speaking rate" has a small greyed-out "Default" dropdown, followed on its own line by the note "Voice and rate follow your screen reader's own settings." "Windows notifications" has a dropdown 335 wide, "When your sessions finish a turn, or any session needs you". Under it, "Recent folders New Session offers:" has a small number box showing 20 and a "Clear Recent Folders" button beside it (#215). Five check boxes follow. The Remote Control one (unticked) wraps its label onto two lines (624 by 30): "Turn on Remote Control for The Chat Place's sessions, so you can reach them from claude.ai and other devices." and "Their conversations are copied to claude.ai and kept there. (File, Remote Control changes one session.)". Under it, the ticked "Let Claude keep a task list in The Chat Place's sessions, as it does in the desktop app, so View, Tasks shows how far along it is (from the next message)" wraps onto two lines, with its access key K underlined (#231). Under that, the unticked "Include Codex chats (restart required). Codex isn't installed on this computer; install the Codex app or CLI, then restart." also wraps onto two lines, with "restart." alone on the second. Next, the unticked "Let agent conversations hand replies across automatically" fits one line, with a little less space under it than the others (6 pixels, so Settings still fits a 768-high screen). It lines up at x=10 with the other check boxes; until #368 it sat 4 pixels further left. The last, "Tell me when an update has been installed, with a link to what's new", is ticked. "OK" (blue-outlined, the default) and "Cancel" sit at the bottom right, 8 pixels above the dialog's bottom edge.

**What looks off:** The dropdown widths are uneven (555, 68 and 335), and the Windows notifications dropdown starts at x=140 while the two above it start at x=99. The whole dialog now fits its contents, with no empty band below the buttons.

**At 150% and 175%:** everything is in view at 150% (1020 by 895). At 175% (1190 by 996) the settings are taller than the screen, so they scroll above OK and Cancel (#231): a scroll bar runs down the right of the settings, the wrapped check box labels break short of it, and the last check box is half in view at the bottom until you scroll. OK and Cancel are their full size below it.

## settings-codex: Settings with Codex automatically included

The same dialog, 680 by 636, on a computer where Codex is found. The Codex check box is ticked and its label is one line, "Include Codex chats (restart required)", so the dialog is 15 pixels shorter than settings and everything below the check box moves up. Below it come the unticked agent conversations check box and the ticked update notice, as in settings (the baseline was 615 high before the agent check box; taken again for #368). In High Contrast Aquatic both Settings pictures follow the theme: near-black background (#202020), white text (16.3:1), white group frames and check boxes, and OK filled light aqua.

**What looks off:** Nothing.

## settings-copilot: Settings once Copilot CLI has a session

The same dialog as settings, 680 by 676 (25 taller), on a computer where GitHub Copilot CLI has saved a session (#352, #355). A new unticked check box, "Include GitHub Copilot CLI chats (preview; restart required)" (it said "read-only" before #372), fits on one line (343 wide) between the Codex check box and the agent conversations one, with its access key O underlined. Every check box below the Reading messages group starts at x=10. In High Contrast Aquatic and Desert it follows the theme like the rest of Settings, and dark-100 looks the same as light-100 (#177).

**What looks off:** Nothing at 100% and 150%. At 175% the settings scroll (#231), as in settings, and the Copilot check box is the one cut through at the bottom until you scroll.

## new-session: File, New Session

This is a 680 by 620 dialog laid out as a two-column form. The labels on the left are Folder, Work in, Branch, Title, Model, Effort (#189) and Permission mode. Every field starts at x=241, because the Branch label, "Branch (choose one, or type a new name):", is long. The Folder dropdown is 413 wide, as wide as the other fields, showing a long path highlighted in blue; "Browse..." and "From GitHub..." sit on their own row under it, lined up with the field (#178 moved them there from beside it). Work in shows "The folder as it is (not a git repository, so no worktree)". Branch and its field are greyed out. Effort's dropdown shows "Default (your Claude Code setting)", like Model's, and Permission mode shows "Auto: Claude decides what is safe to run without asking". Below the form, "About permissions:" heads a read-only box 72 tall, which shows its whole five-line explanation, effort included, with a scroll bar. "First message:" heads a large empty box (644 by 179) that takes under a third of the dialog. "Start" (default) and "Cancel" are at the bottom right.

**What looks off:** The label column is about 230 wide for mostly one-word labels, which leaves a wide empty gutter. A long path still shows only its end in the Folder box. The Title box is 2 pixels right of the dropdowns above and below it (243 against 241); that is how Windows draws a text box beside a dropdown (#178 leaves it).

## github-repos: New Session, From GitHub: your repositories

This is a 720 by 520 dialog. "Search, or type owner/name:" heads a full-width search box with a blue underline (it has focus). "Repositories (3 of 3):" heads a white list 688 by 347 that takes about two thirds of the height. It shows "probe/AIChat: The Chat Place: a reader for Claude Code sessions" (selected), "probe/website: The Idea Place website" and "probe/notes, private: Private notes". Under the list, one line of text gives the folder the repository will be cloned into, a long path ending "...Projects\AIChat, if it isn't there yet." "Use" (default) and "Cancel" are at the bottom right.

**What looks off:** The list is mostly empty with three rows, and private repositories are marked only by the word "private" in the row. Nothing else.

## continue-here: Continue Here (a desktop session)

This uses the same 680 by 620 frame and form style as New Session, but with a narrower label column (fields start at x=114). The rows are Folder (a read-only grey box showing a full path), Title ("Fix the flaky upload test (continued)"), Model, Effort (both "Default (your Claude Code setting)") and Permission mode ("Auto: Claude decides what is safe to run without asking"). "About continuing:" heads a 132-tall read-only box that shows both paragraphs of its note whole, the first now ending "The copy starts with nothing allowed beyond its permission mode." (#332), and "First message:" heads a large empty box 181 tall, about a third of the dialog. "Start" (default) and "Cancel" are at the bottom right.

**What looks off:** The Title box now shows the title from its start, and the About box its whole note (#178 fixed both). The fields' left edges and widths differ by 2 to 6 pixels (Folder 116 to 650, Title 116 to 656, the three dropdowns 114 to 654). Its label column also differs from New Session's, although the two dialogs look like a pair (#178).

## update-installed: After an update is installed

This is a tiny 272 by 126 dialog. One line of text reads "The Chat Place was updated to 0.2.0." Below it are two buttons side by side: "See what's new in 0.2.0" (141 wide, the default, outlined in blue) and "Close". The margins are 16 pixels, wider than the 8 to 10 used in the other dialogs.

**What looks off:** Nothing. It is compact and tidy, and it is the only dialog with 16-pixel margins.

## permission: Claude asks permission to run a command

This is a 700 by 540 dialog. "Request:" heads a large read-only box (668 by 367, about 70% of the dialog). It holds "Claude wants to run git push origin release/0.2.", a blank line, "Bash command:" and the command, and the rest of the box is empty. "Reason to give Claude if you deny (optional):" heads a single-line box. At the bottom right are three buttons: "Allow", "Deny" and "Answer Later". Deny is outlined in blue as the default button. A small resize grip sits in the bottom-right corner.

**What looks off:** The request box is mostly empty white space for a four-line request. The default button is Deny, not Allow (this may be intended). The command is in the same proportional font as the prose, so it doesn't stand out as code.

## question: Claude asks questions (one choice and several choices)

This is a 700 by 377 dialog. The first framed group, titled "Branch: Which name should the release branch use?", holds three radio buttons, "release/0.2: Matches the last release" (chosen, with the focus outline), "v0.2-prep: Shorter" and "Other (type below)", with a full-width text box below them. The second framed group, "Checks: Which checks should run first?", holds four unticked check boxes (Unit tests, Smoke test, Lint, Other (type below)) and its own text box. The two groups fill the top 290 pixels, and "Send Answers" (default), "Don't Answer" and "Answer Later" sit 18 pixels below them at the bottom right.

**What looks off:** Nothing. The dialog is now just tall enough for its two groups, with no empty band above the buttons.

## plan: Claude's plan to approve

This is a 760 by 600 dialog. "Plan:" heads a large read-only box (728 by 412, about 70% of the height) showing the plan as raw Markdown: "# Plan" and three numbered steps, with the rest of the box empty. Below it are two form rows. The first is "If approved, carry on in:" with a dropdown 527 wide reading "Auto: Claude decides what is safe to run without asking". The second is "What to change (for Keep Planning):" with a single-line box. Four buttons sit at the bottom right: "Read Formatted...", "Approve", "Keep Planning" (outlined in blue as the default) and "Answer Later".

**What looks off:** The default button is Keep Planning, not Approve. There is a larger gap (18 pixels) between Read Formatted and Approve than between the others (6 pixels). The plan box is mostly empty.

## manage-groups: File, Manage Groups

This is a small 480 by 420 dialog. "Groups:" heads a white list (448 by 302, about three quarters of the dialog). It holds "Releases, 2 sessions" (selected) and "Website, 0 sessions". Along the bottom, "New...", "Rename..." and "Delete..." sit at the left and "Close" at the right, all the standard 75-wide buttons.

**What looks off:** Nothing.

## command-picker: Insert Command or Skill

This is a 720 by 520 dialog laid out like From GitHub. "Search:" heads a full-width focused search box. "Commands and skills (3 of 3):" heads a white list 688 by 371 that fills most of the dialog. Its rows are "/blog-publish: Publish a post to the blog" (selected), "/compact <optional instructions>, Claude Code: Clear history but keep a summary" and "/context, Claude Code: Show what's using the context". "Insert" (default) and "Cancel" are at the bottom right.

**What looks off:** The row format isn't consistent: the first row uses a colon after the name, and the others use a comma and "Claude Code:". Nothing else.

## bug-report: Help, Report a Bug

This is a 660 by 640 dialog. It is a stack of labelled boxes, each the full width (628): "Summary (the issue's title):" with a single line (focused, blue underline), then three empty 95-tall boxes, "What happened", "What you expected (optional)" and "Steps to reproduce (optional)". Below them, "What the report includes besides your words (no session titles, folders or messages):" heads a read-only box of the same height listing the version ("The Chat Place: 0.2.0 (run from source)" in the 2026-10-25 run), Windows, Python, wxPython ("4.3.2 msw (phoenix) wxWidgets 3.3.4"), Claude Code and, on its sixth line, "Announcements: full, speech engine auto", with a scroll bar for the rest. A note in the small standard font follows on two lines, "Open on GitHub needs access to the app's repository on GitHub. Without it, use Copy Report and email the report to" and "support@theideaplace.net.", and then "Open on GitHub" (default), "Copy Report" and "Cancel" at the bottom right.

**What looks off:** Nothing. The note now wraps, so the support address shows in full, and the read-only box shows its six lines whole. The version lines change with every release and every wxPython the test VM installs, so this screen shows as changed after each one.

## find-all: View, Find in All Sessions: the results (#109)

This is an 820 by 520 dialog titled 'The Chat Place: 6 results for "the", Find in All Sessions'. "Results:" heads a white list that takes most of the dialog, with six rows, the first selected in blue. Each row is the session, who wrote the message, when, and the words around the match, such as "Fix the flaky upload test, You, 6 October 07:00: Thanks. Which branch name should the release use?". "Searched:" heads a two-line read-only box: '6 messages in 2 sessions contain "the". Searched 2 sessions. 2 had no transcript to read.' "Go to Message" (the default) and "Close" are at the bottom right. In High Contrast Aquatic it follows the theme: a dark background, outlined controls, the selected row and the default button in the theme's highlight.

**What looks off:** Long rows are cut at the list's right edge, with no horizontal scroll bar, as in the other lists (a screen reader reads the whole row). A heading and the text after it run together ("What changed The upload test…"). Nothing else.

## code-blocks: View, Lists: a message's code blocks (#279)

Code blocks, links and changed files are now kinds in one dialog, View, Lists, 820 by 660 and titled "Lists: " and the session's name ("Lists: Fix the flaky upload test"). From the top: "Show items from:" beside a small dropdown, "This message"; then "Kinds:", a white list the full width (788 by 95) with six rows, each with its count: "Code Blocks (1)" (selected in blue), "Links (1)", "Changed Files (0)", "Commands (1)", "Decisions (0)" and "Tasks (0)" (#231), all six in view. Below that the chosen kind's page, lined up with Kinds at the same left and right edges: "Filter:" over an empty one-line box, "List: Code Blocks (1):" over a white list 788 by 140 holding "Python, 4 lines: def finish(self):", selected, and "Details:" over a box 788 by 165 showing the four lines of code in a monospaced font with their indentation. "Copy" sits at the right under the page, and "Close" directly below it, the same width and right-aligned with it. In High Contrast Aquatic the dialog is near-black (#202020) with white text (16.3:1) and white outlines; the selected rows are light aqua (#8EE3F0) with dark text (#263B50 at the letters' cores, 7.9:1); the code is white.

**What looks off:** Close is 14 pixels further right than the page's buttons above it (#288). Most of the list is empty with one item.

## links: View, Lists: Links (#190, #279)

The same Lists dialog, opened on Links with "Show items from" set to "The whole chat". "Links (1)" is selected in Kinds. "List: Links (1):" holds one row, "the issue, github.com/example/repo/issues/42. Claude, 1 message ago", selected, and Details shows "the issue" and the full address on the next line. Under the page are "Open", "Copy" and "Copy as Markdown", and "Close" below them. High Contrast Aquatic follows the theme as in code-blocks.

**What looks off:** Nothing; the page lines up with Kinds and Close lines up with the buttons above it (#288).

## changes: View, Lists: Changed Files (#279)

The Lists dialog on "Changed Files (1)", for "Visual probe". The list holds "uploader.py, 2 lines added", selected. Details is in a monospaced font: the file's full path followed by ": 2 lines", with "added." wrapped onto the next line, a blank line, "A change:", two "Added:" lines and an "Unchanged:" line. "Copy" is under the page and "Close" below it. High Contrast Aquatic follows the theme as in code-blocks.

**What looks off:** The path's line wraps before "added.". Close now lines up with Copy (#288).

## lists-commands: View, Lists: Commands (#279)

The Lists dialog on "Commands (1)", for "Fix the flaky upload test", over the whole chat. "List: Commands (1):" holds "pytest tests/test_upload.py -q. done", selected, and Details, in a monospaced font, shows "Command:", the command, a blank line, "Output:" and "1 failed, 11 passed". Copy is under the page and Close below it. High Contrast Aquatic follows the theme as in code-blocks.

**What looks off:** Nothing; Close lines up with Copy (#288).

## lists-decisions: View, Lists: Decisions (#279)

The Lists dialog on "Decisions (2)", over the whole chat. The list holds two rows, newest first: "Run the slow tests too? Not answered" (selected) and "Which branch should the release use? You answered: release/0.2". Details shows "Run the slow tests too?", a blank line and "Not answered." in the standard font. Copy is under the page and Close below it. High Contrast Aquatic follows the theme as in code-blocks.

**What looks off:** Nothing; Close lines up with Copy (#288).


## lists-tasks: View, Tasks: Claude's task list (#231)

The Lists dialog opened with View, Tasks, on "Tasks (3)" over the whole chat, for "Fix the flaky upload test"; Tasks is the sixth row of Kinds, selected. "List: Tasks (3):" holds the steps in Claude's order, each its name then its state: "Find why the upload test fails. Done" (selected), "Make the retry wait for the server. In progress" and "Run the whole test suite. To do". Details shows "Find why the upload test fails", a blank line and "Done. Task 1 of 3." in the standard font. Copy is under the page and Close below it. High Contrast Aquatic follows the theme as in code-blocks.

**What looks off:** Nothing.
## usage: View, Usage and Context

This is a 751 by 360 dialog, one of the smallest. "Usage and context:" heads a white list 722 by 258 with two rows. The first, selected, is "Visual probe: Context: 2 tokens used; the window's size isn't known until this session or another on the same model runs a turn here.", and it fits in full, ending a few pixels short of the list's right edge. The second is "Usage limits: not known until a Chat Place session runs a turn." "Copy", "Copy All" and "Close" are at the bottom right.

**What looks off:** Most of the list is empty. The dialog is now wide enough for the first row, but only just.

## about-you: View, What AI Knows About You (Claude)

This is the largest dialog, 860 by 700, titled "What AI Knows About You" (#305). Two lines of black text run across the top: "Local customization files, not an account-wide memory or active-context report." and "Edit opens a file in your editor; Reload reads your changes." Neither reaches the right edge. "Provider:" heads a dropdown the full width of the dialog showing "Claude". "Kind:" heads a list (828 by 120) with seven rows, such as "Instructions, 1 item: What you've told Claude to do in every session (CLAUDE.md), and in each project" (selected, with the focus outline), "Memories, 0 items..." and "Skills, 1 item...". "Items:" heads a second list (80 tall, room for five rows) with one selected row. A "Location:" row has a read-only grey box with a path. "Contents:" heads a text box (233 tall) showing "# How I like to work" and "Test everything.". Five buttons sit at the bottom right: "Edit in Your Editor", "Show in Folder", "Copy Path", "Reload" and "Close". In High Contrast Aquatic the dialog is near-black with white text (16.3:1) and the selected rows dark blue-grey on light aqua (7.9:1); in Desert, near-black on cream (15.7:1) with cream on brown selected rows (7.0:1). dark-100 looks the same as light-100 (#177).

**At 150% and 175%:** all five buttons stay in view. At 175% (1505 by 956, as tall as the screen allows) Contents is 175 tall and shows both lines of the file; before #368 the Provider row had left it 70 tall, two lines, because the Items list kept 140. The Kind list cuts its seventh row, "Settings", at 175% and scrolls, as before #305.

**What looks off:** Both lists show a selected row at once, so it isn't clear which has focus (as in #181). The Location row puts its label beside the box while every other field has its label above. The five buttons have uneven widths (75 to 116).

## about-you-codex: What AI Knows About You with Codex chosen

The same dialog with "Codex" in the Provider dropdown, reading a synthetic Codex folder. The Kind list has five rows, all naming Codex: "Instructions, 1 item: Instructions for Codex (AGENTS.md and AGENTS.override.md)" (selected), "Memories, 0 items: Local Markdown memories saved in Codex's memories folder", "Skills, 1 item: Packaged instructions for Codex tasks", "Prompts, 0 items: Saved prompts in Codex's prompts folder" and "Settings, 0 items: Codex configuration; project settings apply only to trusted projects", leaving two empty rows. Items holds "AGENTS.md — How I like to work" (selected), the Location box ends "codex\AGENTS.md", and Contents shows "# How I like to work" and "Test everything.". No Claude wording is left on screen. The variants differ as in about-you.

**What looks off:** As in about-you.

## properties: View, Properties of a session (#214)

This is a 976 by 360 dialog titled "Properties of Visual probe", laid out like Usage and Context. "Properties:" heads a white list 944 by 258 with one fact per row: "Title: Visual probe" (selected), "Provider: Claude" (#255), "Kind: Chat Place session", the folder's full path, "Status: Idle", "Started" and "Last activity" with a date and "(just now)", the model of the latest reply and the model chosen, effort, permission mode, then counts: messages, your messages, Claude's replies, tool calls, links, code blocks and files changed. More rows than fit, so it has a vertical scroll bar. "Copy", "Copy All" and "Close" are at the bottom right. The probe shows a fixed date and time, so the picture is the same every run.

**What looks off:** Nothing in particular: every row fits the width.

## codex-new: File, New Session with Codex chosen as Provider

The New Chat Place Session dialog, 680 by 635 (15 taller than for Claude, #368). Labels sit in a column on the left and their controls in a column from x=243 to the right edge: "Provider" (a dropdown showing "Codex", with the focus outline), "Folder" (a dropdown with the folder's path, cut at the right edge), "Browse..." and "From GitHub..." under it, "Work in" ("The folder as it is (not a git repository, so no worktree)"), "Branch" (greyed out and empty), "Title" (an empty box), "Model" and "Effort" (greyed out, "Configured Codex model" and "Configured Codex effort"), and "Permission mode" ("Can edit files in this folder; asks before anything else"). Below them "About permissions:" heads a read-only box the full width, 132 tall (it was 102; #374), with an eight-line paragraph: ChatGPT sign-in, "Choose read-only or allow edits in this folder, with human or automatic approval review. Human requests appear in Answer.", what automatic approval review does and doesn't do, that network access and temporary folders outside the folder still need approval, "Saved user/system rule files in effect: 2", the saved always-allow commands, refused project rules and "Model choices load in the background." The text fills the box, which has a scroll bar. "First message:" heads an empty box 72 tall, four lines (it was 87 before #374, and 57 after it until #368 made the dialog grow to keep four lines), and "Start" (default) and "Cancel" are at the bottom right. The greyed text is #6D6D6D on #FAFAFA (5.0:1); in High Contrast Aquatic it is #A6A6A6 on #202020 (6.7:1), and the default Start button is light aqua (#8EE3F0) with dark text (#263B50, 7.9:1).

At 175% the dialog is 1190 by 956, as tall as the screen allows. The About box gives up height so First message keeps four lines (#368): it is 171 tall, shows five lines of the paragraph with the sixth cut through at its bottom edge, and has a scroll bar for the rest. First message is 141 tall, with Start and Cancel below it. Before #368 the About box showed the whole paragraph and left First message one line tall.

**What looks off:** The folder path is cut at the dropdown's edge, as long paths are in any dropdown. At 175% the About box's last visible line is cut through, as in any box that scrolls.

## codex-models: New Codex chat with the models Codex offers

The same dialog after Codex has sent its models: Model is now enabled and shows "Synthetic Codex model", and Effort shows "Extra high". The About permissions paragraph is the codex-new one with "Saved command rules have not been checked." in place of the rule-file count, and ends with "Project-supplied command rules are refused."; its eight lines fill the 132-tall box. The dialog is 680 by 635 and First message 72 tall, and at 175% the About box scrolls and First message is 141 tall, as in codex-new.

**What looks off:** As in codex-new.

## codex-auto-review: New Codex chat with automatic approval review selected (#374)

The same 680 by 620 New Chat Place Session dialog, offered for Codex only, so it has no Provider row and every row sits 31 pixels higher than in codex-new. The Folder dropdown has the focus: its path is selected in blue and scrolled to its end, so it reads "...er\AppData\Local\Temp\tcp-probe\codex-auto-review\Projects\Scratch" with the start cut off. Browse... and From GitHub... are under it, then Work in, Branch (greyed out), Title, Model and Effort (greyed out, "Configured Codex model" and "Configured Codex effort") and "Permission mode" showing "Can edit files in this folder; automatic approval review". "About permissions:" heads a read-only box 117 tall with the codex-new paragraph in seven lines; with no saved rule files it says "Saved user/system rule files in effect: 0." and goes straight on to "Model choices load in the background." (with one space between them since #368; there were two). The seven lines just fit the box. "First message:" heads an empty box 103 tall, and "Start" (default) and "Cancel" are at the bottom right.

Light mode is black on #F0F0F0 and white, the selected folder text white on #0078D4 (4.5:1), the greyed Branch label #A0A0A0 on #F0F0F0 (2.3:1, as for any disabled label) and the greyed dropdown text #6D6D6D on #FAFAFA (5.0:1). In High Contrast Aquatic everything is white on #202020 (16.3:1), the selected folder text near-black on aqua (#202020 on #8EE3F0, 11.2:1), the greyed text #A6A6A6 (6.7:1) and Start light aqua with #263B50 text (7.9:1). In High Contrast Desert it is near-black on cream (#FFFAEF), the selected folder text and Start rust brown (#903909) with cream text (7.0 to 7.3:1) and the greyed text #676767 (5.4:1). At 175% the dialog is 1190 by 956; the About box (201 tall, whole lines) shows the whole paragraph in six lines with a scroll bar, and First message is 157 tall (#368).

**What looks off:** Nothing is cut off or overlapping.

## codex-continue: Continue Here from a Codex chat that may still be working

A 680 by 620 dialog titled "Continue Here: Codex still working". Rows: "Folder" (a read-only grey box with the full path), "Title" ("Codex still working (continued)"), Model and Effort greyed out, and "Permission mode" ("Read-only; asks before allowing changes"). "About continuing:" heads a box that opens with "This chat may still be working in Codex. The copy starts from the last saved point with the permissions selected here. Your saved always-allow commands still apply.", a blank line, and the permissions paragraph of codex-models (with human and automatic approval review, #374), filling the box, which is 177 tall (it was 147). "First message:" heads an empty box 136 tall (it was 166). At 175% the About box is 351 tall and shows the whole text, and First message is 149 tall. "Start" (default) and "Cancel" are at the bottom right.

**What looks off:** Nothing.

## codex-change-mode: Change permissions for a Codex chat

A 596 by 394 dialog (it was 300 tall), "Change Permission Mode" (Windows' own single-choice dialog). Its text, in the dialog's background: "Codex permissions for Review saved command rules for a deliberately long synthetic chat title.", "Folder:", the folder's path, which wraps mid-word ("long-syntheti" / "c-project-folder"), "Finish or Stop active work first.", a blank line, a new four-line paragraph (#374), "Automatic approval review lets Codex review eligible requests instead of asking you. It can deny requests, and does not grant unrestricted access. Human review remains available. Network access and access to temporary folders outside this folder require approval. The Chat Place overrides these boundaries for this chat only; saved Codex rules still apply.", another blank line, then two lines: "Commands you've told Codex to always allow will run without asking and outside this folder's limits," and "including the network. Project-supplied command rules are refused.". Below is a list 64 tall holding exactly its four choices: "Read-only; asks before allowing changes", "Can edit files in this folder; asks before anything else", "Read-only; automatic approval review" and "Can edit files in this folder; automatic approval review" (selected, white on #0078D4, 4.5:1). "OK" (default) and "Cancel" are at the bottom right under a separator line. In High Contrast Aquatic the text is white on #202020 (16.3:1) and the selected row #263B50 on aqua (7.9:1); in Desert the selected row and OK are rust brown with cream text. At 175% (1038 by 739) the path wraps after "long-synthetic-", the paragraphs keep their line breaks, and all four choices fit.

**What looks off:** The path breaks in the middle of a word at 100%, because it has no spaces. The list shows a scroll bar though all four rows fit.

## codex-permission: Codex asks to run a command

A 700 by 540 dialog titled "Codex needs permission: Codex preview". "Request:" heads a read-only box that fills the dialog (about 670 by 420), holding, with blank lines between: "Command:" and "git checkout -b fix"; "Folder:" and the folder's path; "This also needs:" with "Write access to" the folder's `.git` and "Network access to any site"; "Reason:" and "The sandbox blocked writing Git's HEAD.lock."; and "Codex also offered to keep allowing this, for the rest of the session or from now on. The Chat Place leaves that out: your answer covers this request only. Allow for This Chat isn't offered for this request, so approving it keeps nothing in Chat Permissions. The Chat Place can keep only some Git commands, with their usual options: status, switch, add, commit, push, fetch, diff and log.", wrapped onto four lines (#307). Every line fits the box. Four buttons sit at the bottom right: "Allow Once", "Deny" (the default, blue-outlined), "Deny and Stop Turn" and "Answer Later" (#284). In High Contrast Aquatic the box and buttons are white-outlined on near-black, and Deny is filled light aqua.

**What looks off:** The box is mostly empty for a short request. Nothing else.

## codex-files: Codex file changes to review before allowing

The same dialog, showing a file change: "Update: app.py", "@@ -1 +1 @@", "-print('old')" and "+print('new')", in the standard font. Four buttons at the bottom right, with Deny the default: "Allow Once", "Deny", "Deny and Stop Turn" (it was "Stop Turn"; #284, #295) and "Answer Later". The longer third label moved Allow Once and Deny 51 pixels to the left. In High Contrast Aquatic the buttons are white-outlined on near-black and Deny is filled light aqua. At 175% all four labels fit their buttons.

**What looks off:** The diff isn't in a monospaced font, unlike the Lists dialog's Details. The box is mostly empty.

## copilot-new: File, New Session with Copilot chosen as Provider

The New Chat Place Session dialog, 680 by 620, laid out as in codex-new (#352, #372). "Provider" shows "Copilot" with the focus outline. "Model", "Effort" and "Permission mode" are greyed out and read "Copilot's configured model", "Copilot's own setting" and "Asks before running commands or changing files" (#6D6D6D on #FAFAFA, 5.0:1). "About permissions:" heads a read-only box with a four-line paragraph: Copilot uses your GitHub Copilot sign-in and your plan's requests, uses its own model and effort, which can't be chosen here, asks before it runs a command or changes a file (each request appears in Answer, to allow once or deny), and The Chat Place won't send to Copilot in a folder whose Copilot hooks could act without asking. The paragraph fits the box, which shows a scroll bar. "First message:" heads an empty box, and "Start" (default) and "Cancel" are at the bottom right. In High Contrast Aquatic the greyed text is #A6A6A6 on #202020 (6.7:1) and Start is filled light aqua (#263B50 on #8EE3F0, 7.9:1).

**What looks off:** Nothing.

## copilot-permission: Copilot asks to run a command

A 700 by 540 dialog titled "Copilot needs permission: Copilot preview", like codex-permission (#352, #372). "Request:" heads a read-only box that fills the dialog, holding "Run the upload tests on a new branch", a blank line, "Command:" and "git checkout -b fix-upload-test && python -m pytest tests/test_upload.py" (black on white, 21:1). Three buttons sit at the bottom right: "Allow Once", "Deny" (the default, blue-outlined) and "Answer Later". There is no Allow for This Chat and no Deny and Stop Turn, as Copilot's requests offer only these. In High Contrast Aquatic the text is white on near-black (16.3:1) and Deny is filled light aqua (#263B50 on #8EE3F0, 7.9:1).

**What looks off:** The box is mostly empty for a short request, as in codex-permission. Nothing else.

## copilot-files: Copilot asks to create a file, with its diff

The same dialog, showing a file change: "Create notes.txt", "File:" and the file's path, then "Changes:" with "--- a/dev/null", "+++ b/" and the path, "+First synthetic line" and "+Second synthetic line", in the standard font. The same three buttons, with Deny the default. In High Contrast Aquatic it follows the theme as copilot-permission does.

**What looks off:** As in codex-files, the diff isn't in a monospaced font and the box is mostly empty.

## codex-question: Codex asks questions

A 700 by 254 dialog titled "Codex asks: Codex preview". A framed group "Checks: Which checks should run?" holds two radio buttons, "Unit tests" (chosen, focus outline) and "Smoke test". A second group, "Secret: Enter the test secret.", holds one radio button, "Other (type below)", and an empty one-line box under it. "Send Answers" (default), "Decline and Stop Turn" and "Answer Later" are at the bottom right.

**What looks off:** The secret question shows its box under a lone "Other (type below)" choice, which looks like a choice question with one option. Nothing is cut off.

## usage-codex: View, Usage and Context for a Codex chat

A 640 by 360 dialog. "Usage and context:" heads a list that fills it, with seven rows: "Latest reported model request: 75,000 tokens (70,000 input, 5,000 output)." (selected), "Latest request uses 75% of the reported 100,000-token context window.", "Cumulative token usage: 250,000. This is not the current context size.", "Codex account usage is shared across your chats.", "Codex:", "5-hour limit: 25% used, 75% remaining; reset time not reported." and "168-hour limit: 40% used, 60% remaining; reset time not reported.". "Copy", "Copy All" and "Close" are at the bottom right.

**What looks off:** "Codex:" on its own row reads like a heading in a list of facts. Nothing else.

## properties-codex: Properties of a Codex chat read from Codex

A 640 by 360 dialog, "Properties of Codex history preview", laid out like properties. The list's rows: Title, "Provider: Codex", "Kind: External session", the folder, "Status: Unavailable", Started and Last activity ("11 October 2026 at 14:05 (2 hours ago)", a fixed date with a real "ago"), "Saved command rules check: Codex command rules could not be checked.", Messages 2, Your messages 1, "Codex's replies: 1", Tool calls 0, Links 1, Code blocks 1, Groups none, Session ID and "History: read from Codex". There are more rows than fit, so it has a scroll bar.

**What looks off:** Nothing (#290 fixed the repeated "Status").

## properties-codex-owned: Properties of a Codex chat started here

The same dialog for "Codex policy preview", a Codex chat the app started: "Kind: Chat Place session", "Status: Idle", Started and Last activity "not recorded", "Model chosen: Synthetic model", "Effort: High", "Sandbox: can edit files in its folder", "Approval policy: on request", "Approval reviewer: human" (#374), "Writable folder:" and the path, "Saved user/system rule files in effect: 2", Groups, Session ID and "History: unavailable from Codex". Every row fits; the new row pushes "History: unavailable from Codex" to the list's last visible line. At 175% (1120 by 630) the rows to "Groups: none" show, with Session ID cut by the list's bottom edge as it scrolls.

**What looks off:** Nothing.

## session-columns: View, Session List Columns

This is a 700 by 560 dialog. A two-line intro runs across the top (668 by 31): "Each session in the list is read as one line. Choose what it says, and in what order: put Status first to hear what each session is" and "doing before its title.". Below it are two equal lists side by side, each 326 by 294: "Available columns:" with two rows, "Started (when the session began)" (selected) and "Last message", and "Shown columns, in the order they're read:" with eleven rows: Title, "Provider (Claude, Codex or Copilot)" (#355), Folder, "Status (needs you, working or idle)", New reply, Last activity, "Kind (desktop app, Cowork, terminal or The Chat Place)", Remote Control, Archived, Hidden and Groups. Every row fits, the Kind row with its closing bracket. Both have a selected row; the right one has the focus outline. Under them is one row of seven ordinary-sized buttons: "Add", "Remove", "Move Up", "Move Down", "Move to Top", "Move to Bottom" and "Reset to Default", the last ending near the right list's edge. "Preview of the selected session's line:" heads a 60-tall read-only box whose text wraps onto two lines: "Fix the flaky upload test, Claude, AIChat, needs you: Pick a name for the release branch, active 1 minute ago, desktop app" and "session". "OK" (default) and "Cancel" are at the bottom right. At 175% the dialog is 1190 by 956 (the earlier 175% picture was 1120 wide, from before #300): the two lists are 567 wide, all seven buttons fit on one row with Reset to Default at its end, and the preview wraps onto two lines, ending "desktop app session".

**What looks off:** Nothing (#289).

## prompts: File, Prompts

This is a 680 by 560 dialog. "Prompts:" heads a white list 648 by 217 with "Review" (selected) and "Release notes". "Text:" heads a read-only text box of the same size showing "Review this change for bugs, then for accessibility." The two boxes split the dialog about evenly. Along the bottom are seven buttons: "Use" (default), "New...", "Edit...", "Delete...", "Move Up", "Move Down" and, at the far right, "Close".

**What looks off:** The Text box shows "...then for accessibility." while the Edit Prompt dialog for the same prompt shows only "Review this change for bugs.". This is probably different test data, but the two don't match. Both areas are mostly empty.

## prompt-edit: Editing a saved prompt

This is a 620 by 460 dialog. "Name:" heads a single-line box holding "Review". "Text:" heads a large multi-line box (588 by 311, about three quarters of the dialog) holding "Review this change for bugs.". "Save" (default) and "Cancel" are at the bottom right.

**What looks off:** Nothing. It is simple and matches the other dialogs.

## main-agents: An agent conversation loaded, a reply waiting to be handed on (#248)

The main window with the agent conversation "Uploader fix" loaded. Its row in the session list reads "Uploader fix, Claude, Scratch, needs you: Claude 2's reply waiting to go to Claude 1..." and is selected; its two member chats aren't in the list. The heading reads "Uploader fix, Scratch, needs you: Claude 2's reply waiting to go to Claude 1. Agent conversation, Claude." The messages list is labelled "Messages in Uploader fix (needs you: Claude 2's reply waiting to go to Claude 1, agent conversation):" and has five rows: "You, to Claude 1: Make the uploader flush before it signals.", "Claude 1: ## What changed", "Relay: Setup sent to Claude 2: who the other agent is, the roles, and to end with DONE when the work is finished." (cut off at the right edge, as long rows are), "Relay: Claude 1's reply passed to Claude 2, with your changes. Enter reads what went." (the probe's hand-off differs from Claude 1's reply), and, selected, "Claude 2: One problem: close() can run twice when the upload is cancelled. Guard it with a flag." Under Send and Stop the status line reads "Claude 2's reply is waiting to go to Claude 1. Ctrl+Shift+E hands it on." Measured contrast: 4.53:1 for the light selected row (Windows' highlight), 7.89:1 in High Contrast, 16:1 or more for the rest.

**What looks off:** Nothing new: long rows run off the list's right edge, as everywhere in the messages list.

## agent-new: File, New Agent Conversation (#248)

A 720 by 760 dialog titled "New Agent Conversation". From the top: "Kind of conversation:" with a dropdown showing "Your own roles" (focused); "Title:" with an empty box; "First agent's chat:" with a dropdown showing "New Claude chat"; "First agent's role (optional), such as Build the fix:" with an empty box; "First agent's new chat permission:" with "Auto: Claude decides what is safe to run without asking"; "Second agent's chat:" with "New Codex chat"; "Second agent's role (optional), such as Review each change:"; "Second agent's new chat permission:" with "Read-only; asks before allowing changes"; "Third agent's chat (optional):" with "No third agent"; "Folder for new chats:" with an editable dropdown and a "Browse..." button beside it; "Hand-offs:" with "Approve each hand-off: you pass each reply on (type, or Ctrl+Shift+E)"; "Who goes first:" with "First agent"; and "Opening message (Enter starts a new line; Ctrl+Enter starts the conversation):" over a large multi-line box that fills the rest. "Start" (default) and "Cancel" are at the bottom right. Each label's access key is underlined, and none repeats. Measured contrast 18:1 or more in light, 16:1 in High Contrast.

**What looks off:** Nothing. When an existing chat is chosen, its permission row and (with no new chat) the folder row disappear, and the rows below move up.

**At 175%:** the controls are taller than the screen allows, so they scroll above Start and Cancel, which stay in view; the opening message box is below the fold until you scroll, as in Settings.

## agent-new-three: New Agent Conversation with the Builder and reviewer template and a third agent (#248)

The same dialog, filled in by the "Builder and reviewer" kind and with a third agent. "Kind of conversation:" shows "Builder and reviewer: One agent builds; the other reviews each change without editing, and the builder fixes what it finds." The first agent is a new Claude chat with the role "Build what the user asks, then pass your work on for review. Fix what the reviewer finds." and permission "Accept edits: file edits allowed; Claude asks before commands that need approval". The second is a new Codex chat with the role "Review each change the builder makes. List problems by severity. Don't edit files." and "Read-only; asks before allowing changes". "Third agent's chat (optional):" shows "New Claude chat", with "Third agent's role (optional), such as Check the facts:" holding "Check the facts" and "Third agent's new chat permission:" showing "Plan: Claude plans but does not change anything". Below come the folder, "Hand-offs:", and "Who goes first:" with "Everyone (their replies aren't passed on)"; the opening message box is below the fold, and the body scrolls above Start and Cancel. Each label's access key is underlined, and none repeats.

**What looks off:** Nothing. The kind's description runs to the dropdown's right edge at 100%; it's read in full by the screen reader.

## agent-hand-off: An agent conversation's hand-off, Ctrl+Shift+E (#248)

The Send Reply to Another Chat dialog with its own title, "Hand Off: Uploader fix", 700 by 560. "Send to:" lists one row, "Claude 1, Visual probe", selected. "Message:" holds "From Claude 2 (agent conversation "Uploader fix"):", a blank line, and "One problem: close() can run twice when the upload is cancelled. Guard it with a flag." "Send" (default) and "Cancel" are at the bottom right. In High Contrast Aquatic the boxes are dark with white text, and the selected row and Send are cyan with dark text.

**What looks off:** Nothing; with one possible target the list is mostly empty, as in the relay surface.

## relay: Send Reply to Another Chat: a Codex reply going to a Claude chat (#248)

This is a 700 by 560 dialog titled "Send Reply to Another Chat: Codex history preview (still working; this is its latest reply so far)", since the probe's Codex chat is mid-turn. "Send to:" heads a list about 120 high with three rows: "Visual probe, Claude, Scratch, idle", selected, then "New Claude chat" and "New Codex chat" (#378). Under it are two radio buttons, "Just send it" (chosen) and "Start an agent conversation with both chats (unavailable: this chat is still working)", on one line that fits the dialog, then "Agent conversation title:" over a one-line box holding "Codex history preview", both greyed while Just send it is chosen. "Message:" heads a large multi-line box filling the rest of the dialog: its first line is "From Codex, another AI agent, forwarded by the user from their chat "Codex history preview":", then the reply's Markdown as plain text, with a scroll bar. "Send" (default) and "Cancel" are at the bottom right. In High Contrast Aquatic the boxes are dark with white text, and the selected row and Send button are cyan with dark text. Measured contrast: 4.53:1 for the light theme's selected row (Windows' highlight), 7.89:1 for High Contrast's, 16:1 or more for the rest.

**What looks off:** Nothing. The list is mostly empty with one chat, as it would be with few chats.

## rename: Rename Session (wx's own text entry dialog)

This is a small 346 by 153 dialog. The line "New name for Visual probe:" sits above a text box 300 wide holding "Visual probe", which is selected in blue. A thin divider line runs below it, and then "OK" and "Cancel" sit at the bottom right.

**What looks off:** It is the only dialog with a divider line above its buttons, and its text box is inset 17 pixels, while other dialogs use 10. These are minor differences from the app's own dialogs.

## main-empty: Main window with no sessions at all (a first run)

This picture has the same layout as main-start. The session list on the left is completely empty white, apart from a thin dotted focus rectangle across its top row. The messages list on the right says "No sessions yet. Start one with File, New Session (Ctrl+N), in a supported desktop app or in a terminal." (selected in blue; it said "the Claude desktop app" before #255), on one line that fits the list. The status bar shows two parts: "0 sessions: 0 need you, 0 working." and "No session loaded". It has no needs-you button, and its left part is wider (572).

**What looks off:** The session list itself stays empty white, with no words of its own; the line about how to start is in the messages list (#182 put it there).

## High Contrast and scaling, surface by surface

Only surfaces where a variant differs in a way worth knowing are listed. Everywhere else, the dark-100 picture is identical to light-100, and the High Contrast pictures are the same layout in the theme colours described above.

- **message-formatted:** In the 2026-10-16 hc-aquatic-100 picture the code block has a thin white rounded outline round it, on the page's near-black; hc-desert-100 wasn't taken again, and the earlier note follows. In both High Contrast themes the code block had no background and no edge at all. The four lines are only indented and set in a monospaced font, on the same near-black (Aquatic) or cream (Desert) as the page. The table keeps visible lines, white in Aquatic and dark grey in Desert. In dark mode the code box is near-black on near-black (#2B2B2B on #1E1E1E, 1.2:1), so it barely shows either (#177).
- **shortcuts-page, user-guide, message-formatted:** In dark mode only the page turns dark and the frame and buttons stay light (#177). At 175% their "Read as Plain Text" and "Close" buttons are in view (#186 fixed this: they were hidden behind the taskbar).
- **new-session, continue-here, plan, bug-report, about-you:** At 175% these dialogs are made as tall as the screen allows (956) and keep their bottom row in view: Start and Cancel, the Report a Bug note and buttons, the five About You buttons and the Plan dialog's four buttons (#186 fixed this: the taskbar covered them). Their boxes are a little shorter to fit.
- **All main-window pictures:** At 150% the messages list is 398 tall and the reply box 180; at 175%, 302 and 210. The screen is 1080 pixels tall, so at 175% the window can't be as tall as its design (720 at 100%), and the conversation gets less room than at 150%.
- **main-status-bar:** In High Contrast the three status-bar buttons are outlined in white (Aquatic) or black (Desert), with the whole text inside the outline.
- **All surfaces in hc-desert-100:** The title bars show pale grey text on mid grey (1.2:1), so window titles almost disappear. This is Windows' own inactive title bar, not the app's drawing.
- **Lists in every variant:** The focused list is marked only by the thin dotted outline. In High Contrast the selected rows of focused and unfocused lists are the same aqua or rust brown, just as they are the same blue in light mode (#181).


## lists-codex-tasks: View, Tasks: Codex's live plan (#318)

The 820 by 660 Lists dialog, titled "Lists: Codex history preview", uses the same
layout as Claude's Tasks. A scope dropdown is above the six-row Kinds list (788 by
96). Tasks (3) is selected. Below Filter is the 788 by 140 task list with a done
step, an in-progress step and a to-do step. The first step is selected; the Details
box (788 by 165) says "Find why the upload test fails" and "Done. Task 1 of 3."
Copy and Close are at the lower right. All three task names fit on single lines.

Light mode has black text on white (21:1), with white selected text on blue
#0078D4 (4.53:1). High Contrast Aquatic uses white on #202020 (16.29:1), and dark
navy #202074 on aqua #8EE3F0 for selected rows (9.47:1). Colours were sampled from
the screenshots. Both themes keep the same spacing and button positions.

**What looks off:** The existing shared-list focus distinction (#181) remains:
both Kinds and the task list keep selected rows, and the focused row has a thin
dotted outline. In light mode its #FF872B dots against #0078D4 measure 1.89:1.
There are no new clipping, overlap or theme problems in these pictures.

## lists-codex-no-tasks: View, Tasks in an external Codex chat (#318)

The same 820 by 660 layout. Kinds says "Tasks, not available" and the list label
says "Tasks (0)". The single selected row reads "No task list. Task lists are kept
only for Codex chats run in The Chat Place." It fits fully on one line. Details is empty,
Copy is disabled, and Close remains enabled. High Contrast Aquatic changes the
colours as described for lists-codex-tasks and preserves the layout.

**What looks off:** Only the existing list-focus distinction (#181), as above.

## Shortcuts text update for task lists (#318)

The light-100 and hc-aquatic-100 plain-text shortcut pictures retain the previous
620 by 520 layout. The comparison reports changed shortcut text, with no controls
moved or resized; the changed task wording is below the photographed viewport.
The High Contrast diff shows a small scrollbar-thumb change. The formatted
shortcuts and user-guide pictures render their headings, tables or paragraphs and
bottom buttons normally and compare unchanged in the initial viewport. No blank
page was accepted: the initial blank WebView capture was rerun successfully.


## properties-codex-tasks: session Properties with task progress (#318)

A 640 by 360 Properties dialog for "Codex history preview". The 608 by 258
properties list is scrolled to the selected line "Tasks: Task 2 of 3: Make the
retry wait for the server. 1 done." (row 19; "Approval reviewer: human", added by
#374 after "Approval policy: on request", moved it down one). The full line fits.
Status: Working, model, effort, sandbox, approval policy, approval reviewer, the
saved-command-rules check, message counts and Code blocks are visible above it,
with Groups below it. Copy, Copy All and Close are aligned along the lower
right. The probe selects this row to show progress rather than the earlier rows
about title and provider. High Contrast Aquatic preserves the layout and uses the
same near-black, white and aqua colours as the Codex Tasks screenshots.

**What looks off:** No new clipping, overlap, theme or spacing problems in these
pictures. The saved-command-rules check says it could not be checked because this
is the synthetic probe connection, which does not supply that unrelated API.

## lists-codex-owned-no-tasks: View, Tasks in an owned Codex chat without tasks (#323)

The same 820 by 660 dialog uses the shared empty-list wording. Kinds says
"Tasks (0)" without an unavailable warning. The selected row in the 788 by 140
list says "No tasks in this chat." The 788 by 165 Details box is empty. Copy is
disabled and Close is enabled. Both light and High Contrast Aquatic keep the
same layout and colours described for lists-codex-tasks.

**What looks off:** Only the existing list-focus distinction (#181), as above.

## chat-permissions: File, Chat Permissions for a Claude chat (#332)

A 680 by 520 dialog titled "Chat Permissions: Visual probe". At the top, "Permission mode:" labels a grey read-only box showing "Auto: Claude decides what is safe to run without asking", with "Change..." to its right. "Allowed for this chat:" heads a white list that takes about half the dialog. It has three rows: "Run git push (any arguments). Bash(git push:*). Allowed: time not recorded." (selected, white on blue), "Fetch pages from docs.python.org. WebFetch(domain:docs.python.org). Allowed 19 October 2026 at 03:53." and a kept folder, "Work in the folder" and the probe's Scratch folder path with its time (#334). The folder row is wider than the list, so a horizontal scroll bar runs along the list's bottom. Below the list, "Remove..." and "Remove All..." sit at the left and "Close" at the right. "About these permissions:" heads a read-only box that now shares the height with the list (about 169 tall, the list 186; #307): "Removing a rule applies from this chat's next turn." After a blank line it lists Claude Code's own settings for the chat's folder (#336): "From Claude Code's own settings. These apply to every Claude session working in this folder, here, in the desktop app or a terminal. The Chat Place only reads them; change them in Claude Code, for example with /permissions." and, for the probe's made-up folder, "Claude Code's own settings allow nothing extra for this folder." Every line fits; the box scrolls when there are more. The selected row measures 4.53:1 (white on Windows' blue, #0078D4), and unselected rows are black on white. In High Contrast Aquatic the boxes and buttons are white-outlined on near-black (#202020, 16.3:1 for the text), and the selected row is dark text on light aqua (#8EE3F0, 7.9:1).

**What looks off:** The list is mostly empty with two rules. Nothing else.

## chat-permissions-empty: File, Chat Permissions with nothing kept (#332)

The same dialog with one row in the list, "Nothing is allowed beyond the permission mode.", selected. Everything else is as above.

**What looks off:** Nothing.

## chat-permissions-codex: File, Chat Permissions for a Codex chat (#332)

The same 680 by 520 dialog, titled "Chat Permissions: Codex preview". The mode box reads "Can edit files in this folder; asks before anything else", with Change beside it. The selected list entry reads "No command approvals are remembered for this chat." Remove and Remove All now sit below the list on the left, with Close on the right. The About box shares the dialog's height with the list (list 186 tall, About box 169). Since #374 it opens with "Selected reviewer: human review. Can read files and edit the chat folder:" and the folder's path (wrapping after "tcp-"), then a four-line paragraph on automatic approval review and its boundaries, as in codex-change-mode. After a blank line comes the earlier text: when removal counts, that remembered commands may run outside the sandbox, and that only this chat in The Chat Place keeps the approval, ending "No Codex or Claude settings or rules are changed." at the box's bottom edge. The Codex saved-rules paragraph (#307) is now below the first view, so the scroll thumb is shorter. High Contrast Aquatic uses white outlines and text on near-black, and an aqua selected row. Control sizes and positions match the earlier dialog; only the two buttons and the text changed.

**What looks off:** Nothing in these pictures.

## codex-permission-kept: Answer with a TCP remembered approval (#307)

A 700 by 540 dialog. Request heads a 668 by 391 read-only box showing the command, starting folder, execution authority and remembered approval scope. A two-line paragraph explains that Allow for This Chat remembers the approval, while Allow Once keeps none. Allow for This Chat sits just below that box on the right. The bottom row contains Allow Once, Deny, Deny and Stop Turn, and Answer Later. Deny retains the default outline. Light mode uses black text and a white request box on a pale-grey dialog. High Contrast uses white outlines and text on near-black, with aqua Deny. All labels fit, with substantial empty space below the request explanation. Pixel measurements give 21:1 for ordinary light text and 16.29:1 for ordinary High Contrast text; the light default-button outline is 3.97:1 against the dialog background.

**What looks off:** Nothing in these pictures.

## chat-permissions-codex-kept: Review remembered Codex commands (#307)

A 680 by 520 dialog. Permission mode and Change run across the top. A 648 by 186 list takes the middle, with git commit selected above git push. Long rows continue beyond the initial horizontal viewport; a horizontal scrollbar runs below them. Remove and Remove All sit below the list at the left, and Close at the right. A 648 by 169 About box opens with "Selected reviewer: human review. Can read files; changes require approval." and the automatic-review paragraph (#374), then explains removal, mode changes and command authority; the Codex saved-rules paragraph and "Codex's own saved rules allow nothing extra." are now below the first view, reached by scrolling. High Contrast uses white outlines, near-black backgrounds and an aqua selected row. The selected light text measures 4.53:1 (#FFFFFF on #0078D4); High Contrast selected glyph cores measure 7.89:1 (#263B50 on #8EE3F0).

**What looks off:** Nothing in these pictures; long rules use the scrollbar.

## codex-review-history: Chat Permissions for a Codex chat with automatic approval review (#374)

The 680 by 520 Chat Permissions dialog, titled "Chat Permissions: Automatic review preview", laid out exactly like chat-permissions-codex. The mode box reads "Can edit files in this folder; automatic approval review", with Change... beside it. The list holds one selected row, "No command approvals are remembered for this chat.", and Remove..., Remove All... and Close sit below it. The About box (648 by 169) is scrolled to its end, which reads "Recent automatic review decisions (oldest first):", a blank line, "Codex automatic review allowed the request.", "Command: git status", "Reason: Read the project status.", a blank line, "Codex automatic review denied the request." and "Reason: Access was outside the requested task.", leaving about a third of the box empty below. The scroll thumb sits at the bottom of its track.

Text is black on white (21:1) and the selected row white on #0078D4 (4.5:1). In High Contrast Aquatic the text is white on #202020 (16.3:1) and the selected row #263B50 on aqua #8EE3F0 (7.9:1); in Desert the text is dark on cream (#FFFAEF, 10.4:1 or better) and the selected row cream on rust brown (#FFF5E3 on #903909, 7.0:1). At 175% (1190 by 910) the same lines show larger, with more space below them.

**What looks off:** Nothing.
