# How The Chat Place looks

Written from the visual probe's pictures (#155), on made-up data, on 2026-10-10.
Regenerate it with `tools/ui_review_prompt.md` whenever screens change.

Every light-100 picture was opened, along with the dark, High Contrast and scaled pictures named below. Sizes come from the probe's JSON and are in pixels at 100% scale. Colours were sampled from the pictures; contrast ratios are WCAG ratios.

## The app at a glance

The Chat Place looks like a plain, older-style Windows program built from standard Windows controls. The window background is light grey (#F0F0F0), lists and text boxes are white with thin grey borders, text is black, and buttons are small, near-white rectangles with a thin grey border and slightly rounded corners. Everything uses the standard Windows interface font (Segoe UI, about 9 point), so the text is small and the layout is dense, with 6 to 10 pixels between controls. There are no icons, toolbars, colours or pictures apart from the small blue speech-bubble icon in the main window's title bar. The selected row in every list is a solid blue bar (#0078D4) with white text (4.5:1). The list that has keyboard focus differs only by a thin dotted outline around its selected row, which shows as orange and blue dots (#FF872B on #0078D4, 1.9:1). The default button in a dialog has a blue outline. The three pages shown in a web view (Keyboard Shortcuts, User Guide and a formatted message) look quite different: larger text (about 12 point), bold black headings, tables with thin grey lines and a pale grey code box, like a simple web page.

The main window in the pictures is 1000 by 720, with a 984 by 630 inside area. A menu bar with File, View and Help is at the top and a status bar is at the bottom. Dialogs range from 272 by 126 (Update Installed) to 860 by 640 (What Claude Knows About You); most are between 620 and 760 wide.

### Themes and scaling

- **Dark mode:** In Windows dark mode for apps (dark-100), the main window and Settings are identical to light mode, pixel for pixel: light grey and white with black text (#177). Only the web view pages turn dark. The formatted message has a near-black page (#1E1E1E) with light grey text (#E8E8E8, 13.6:1), mid-grey table lines, a light blue link (#8AB4F8, 7.9:1) and a code box only slightly lighter than the page (#2B2B2B). The dialog's frame and its two buttons stay light grey around it, so a dark page sits inside a light frame.
- **High Contrast Aquatic:** High Contrast Aquatic (hc-aquatic-100) repaints everything near-black (#202020) with white text (16.3:1). Buttons, text boxes, lists, dropdowns and group frames get thin white outlines, and dropdown arrows and scroll bars turn into classic square boxes. Selected rows are light aqua (#8EE3F0) with near-black text (11.2:1), and the default button (OK in Settings) is filled light aqua. The formatted message follows the theme too: near-black page, white text, white table lines and an aqua link (#75E9FC).
- **High Contrast Desert:** High Contrast Desert (hc-desert-100) is the light version of the same thing: a cream background (#FFFAEF) everywhere with near-black text (#202020, 15.7:1) and thin black outlines. Selected rows and the default button are rust brown (#903909) with cream text (7.3:1). Its formatted message has dark grey text (#3D3D3D), dark grey table lines and a dark teal link (#1C5E75, 6.9:1). In Desert the title bars are mid-grey with pale grey title text (1.2:1), so the window titles almost vanish; the probe pictures windows while they aren't the active window, and this is how Windows draws an inactive title in that theme.
- **150% and 175% scaling:** At 150% and 175% display scaling (light-150, light-175), Windows enlarges the whole app like a picture: same layout, everything 1.5 or 1.75 times bigger, and the letters slightly soft and blurred (#185). The main window comes out shorter, 632 tall at 150% and 529 tall at 175% instead of 720, so the messages list shrinks to 255 and then 152 tall while the reply box stays 120.

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

This picture is the same as main-own, except that a small white list 48 tall appears under the Send/Stop/Commands/Attach Files row. It shows "screenshot of the error.png" (selected in blue) and "upload.log". To make room, the messages list drops to 269 tall. The attachment list has no heading of its own, so it looks like a third, unlabelled list sitting under the buttons.

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

## shortcuts-page: Help, Keyboard Shortcuts (formatted page)

This is an 820 by 620 dialog. A web page fills almost all of it (796 by 534) inside a thin grey border, with a grey vertical scroll bar on the right. The page has a large bold "Keyboard shortcuts" heading, an intro paragraph, then a "Moving around" heading and a two-column table, "Keys" and "What it does". Keys are in bold, and the table has thin grey grid lines and generous row padding. The text is about 12 point, noticeably bigger than the rest of the app. At the bottom right are two small buttons, "Read as Plain Text" (115 wide) and "Close".

**What looks off:** The table's last visible row is cut through mid-line at the bottom, which is normal for a page that scrolls. The buttons look tiny next to the large page text. Nothing else.

## shortcuts-plain: Keyboard Shortcuts as plain text

This is a smaller 620 by 520 dialog. "Shortcuts:" heads a white read-only text box 588 by 402 that fills most of the dialog. It holds the same content as plain text. It opens with a four-line paragraph about the window's three parts in Tab order, then section names such as "Moving around:" and "Session list:" on their own lines, and each shortcut indented two spaces, as in "Ctrl+1: Go to the session list". Long lines wrap with no hanging indent, so continuation lines start at the left margin. The text is the small standard font. A single "Close" button sits at the bottom right.

**What looks off:** Wrapped lines aren't indented, so they run into the next shortcut visually. The bottom line is cut through, as the box scrolls. Nothing else.

## user-guide: Help, User Guide (formatted page)

This dialog is laid out exactly like shortcuts-page, 820 by 620, with the web page filling the dialog and "Read as Plain Text" and "Close" at the bottom right. The page starts with a large bold heading, "The Chat Place User Guide", then three paragraphs. A bold "Before you start" heading follows, then "You need:" and a bulleted list. The word "claude" is set in a monospaced font. The scroll thumb is short, showing that the page is long.

**What looks off:** Nothing.

## message-formatted: A message read as a formatted page: heading, list, code, table, link

This uses the same 820 by 620 frame as the other formatted pages. The page shows a bold "What changed" heading and a paragraph. Next is a two-item bulleted list whose file names are in a monospaced font, then a code block of four Python lines on a pale grey (#F3F3F3) rounded box with no border. Then comes a small three-column table (File, Lines added, Lines removed) with bold headers and thin grey grid lines, which takes about half the page width. Last is a paragraph with a blue underlined link, "the issue", followed by many repeated words that wrap across the full width.

**What looks off:** Nothing in light mode. The code box is very faint against the white page (1.1:1), so the code is set apart mainly by its font. In dark mode the page turns near-black but the dialog around it and its buttons stay light grey (#177).

## message-plain: The same message as plain text

This is a 720 by 520 dialog. "Claude said:" heads a white read-only text box 688 by 402, with a single "Close" button below it on the right. The box shows the raw Markdown in the small standard font: "## What changed", list lines starting with "- " and backticks, the code fenced with three backticks and "python", the table as pipe characters and dashes, and the link written out in full, "[the issue](https://github.com/example/repo/issues/42)". The text wraps at about 80 characters in places and runs wider in others. The code is in the same proportional font as everything else.

**What looks off:** The line lengths are uneven: the first paragraph wraps early, around 430 pixels, while the long paragraph uses the full width. Nothing else.

## settings: Settings

This is a 680 by 517 dialog. At the top, a framed group, "Announcements", runs the full width with three radio buttons: "Full: read the whole reply" (chosen, with the focus outline), "Summary: the session's name and the reply's first sentence" and "Silent: status bar only". Below it are two ticked check boxes, then a second framed group, "Reading messages", with two more ticked boxes. Then come label-and-dropdown rows. "Speech engine" has a dropdown 555 wide, "Automatic (your screen reader, or a system voice when none is running)". "Speaking rate" has a small greyed-out "Default" dropdown, followed on its own line by the note "Voice and rate follow your screen reader's own settings." "Windows notifications" has a dropdown 335 wide, "When your sessions finish a turn, or any session needs you". Two final check boxes follow. The Remote Control one (unticked) wraps its label onto two lines (624 by 30): "Turn on Remote Control for The Chat Place's sessions, so you can reach them from claude.ai and other devices." and "Their conversations are copied to claude.ai and kept there. (File, Remote Control changes one session.)". The last, "Tell me when an update has been installed, with a link to what's new", is ticked. "OK" (blue-outlined, the default) and "Cancel" sit at the bottom right, 8 pixels above the dialog's bottom edge.

**What looks off:** The dropdown widths are uneven (555, 68 and 335), and the Windows notifications dropdown starts at x=140 while the two above it start at x=99. The whole dialog now fits its contents, with no empty band below the buttons.

## new-session: File, New Session

This is a 680 by 620 dialog laid out as a two-column form. The labels on the left are Folder, Work in, Branch, Title, Model, Effort (#189) and Permission mode. Every field starts at x=241, because the Branch label, "Branch (choose one, or type a new name):", is long. The Folder dropdown is 413 wide, as wide as the other fields, showing a long path highlighted in blue; "Browse..." and "From GitHub..." sit on their own row under it, lined up with the field (#178 moved them there from beside it). Work in shows "The folder as it is (not a git repository, so no worktree)". Branch and its field are greyed out. Effort's dropdown shows "Default (your Claude Code setting)", like Model's, and Permission mode shows "Auto: Claude decides what is safe to run without asking". Below the form, "About permissions:" heads a read-only box 72 tall, which shows its whole five-line explanation, effort included, with a scroll bar. "First message:" heads a large empty box (644 by 179) that takes under a third of the dialog. "Start" (default) and "Cancel" are at the bottom right.

**What looks off:** The label column is about 230 wide for mostly one-word labels, which leaves a wide empty gutter. A long path still shows only its end in the Folder box. The Title box is 2 pixels right of the dropdowns above and below it (243 against 241); that is how Windows draws a text box beside a dropdown (#178 leaves it).

## github-repos: New Session, From GitHub: your repositories

This is a 720 by 520 dialog. "Search, or type owner/name:" heads a full-width search box with a blue underline (it has focus). "Repositories (3 of 3):" heads a white list 688 by 347 that takes about two thirds of the height. It shows "probe/AIChat: The Chat Place: a reader for Claude Code sessions" (selected), "probe/website: The Idea Place website" and "probe/notes, private: Private notes". Under the list, one line of text gives the folder the repository will be cloned into, a long path ending "...Projects\AIChat, if it isn't there yet." "Use" (default) and "Cancel" are at the bottom right.

**What looks off:** The list is mostly empty with three rows, and private repositories are marked only by the word "private" in the row. Nothing else.

## continue-here: Continue Here (a desktop session)

This uses the same 680 by 620 frame and form style as New Session, but with a narrower label column (fields start at x=114). The rows are Folder (a read-only grey box showing a full path), Title ("Fix the flaky upload test (continued)"), Model, Effort (both "Default (your Claude Code setting)") and Permission mode ("Auto: Claude decides what is safe to run without asking"). "About continuing:" heads a 117-tall read-only box that shows both paragraphs of its note whole, and "First message:" heads a large empty box 196 tall, about a third of the dialog. "Start" (default) and "Cancel" are at the bottom right.

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

This is a 660 by 640 dialog. It is a stack of labelled boxes, each the full width (628): "Summary (the issue's title):" with a single line (focused, blue underline), then three empty 95-tall boxes, "What happened", "What you expected (optional)" and "Steps to reproduce (optional)". Below them, "What the report includes besides your words (no session titles, folders or messages):" heads a read-only box of the same height listing the version, Windows, Python, wxPython, Claude Code and, on its sixth line, "Announcements: full, speech engine auto", with a scroll bar for the rest. A note in the small standard font follows on two lines, "Open on GitHub needs access to the app's repository on GitHub. Without it, use Copy Report and email the report to" and "support@theideaplace.net.", and then "Open on GitHub" (default), "Copy Report" and "Cancel" at the bottom right.

**What looks off:** Nothing. The note now wraps, so the support address shows in full, and the read-only box shows its six lines whole.

## links: View, Links (#190)

This is a 760 by 480 dialog titled "Links in Fix the flaky upload test: 1". "Filter:" heads a full-width empty text box. "Links (1):" heads a white list that fills most of the dialog, with one row selected in blue: "the issue, github.com/example/repo/issues/42. Claude, 1 message ago". "Open" (the default), "Copy", "Copy as Markdown" and "Close" are in a row at the bottom right. In High Contrast Aquatic the dialog is dark, with the list, the box and the buttons outlined in the theme's text colour, the selected row in the theme's highlight, and the default button filled.

**What looks off:** With one link, the list is mostly empty. Nothing else.

## find-all: View, Find in All Sessions: the results (#109)

This is an 820 by 520 dialog titled 'The Chat Place: 6 results for "the", Find in All Sessions'. "Results:" heads a white list that takes most of the dialog, with six rows, the first selected in blue. Each row is the session, who wrote the message, when, and the words around the match, such as "Fix the flaky upload test, You, 6 October 07:00: Thanks. Which branch name should the release use?". "Searched:" heads a two-line read-only box: '6 messages in 2 sessions contain "the". Searched 2 sessions. 2 had no transcript to read.' "Go to Message" (the default) and "Close" are at the bottom right. In High Contrast Aquatic it follows the theme: a dark background, outlined controls, the selected row and the default button in the theme's highlight.

**What looks off:** Long rows are cut at the list's right edge, with no horizontal scroll bar, as in the other lists (a screen reader reads the whole row). A heading and the text after it run together ("What changed The upload test…"). Nothing else.

## code-blocks: A message's code blocks

This is a 760 by 560 dialog. "Code blocks:" heads a white list 728 by 110 with one row, "Python, 4 lines: def finish(self):", selected. "Code:" heads a large text box (728 by 324, about 60% of the dialog) showing the four lines in a monospaced font with their indentation. "Copy" and "Close" are at the bottom right.

**What looks off:** The code box is empty below the fourth line. Neither button is drawn as the default. Nothing else.

## changes: View, Changed Files

This is an 800 by 580 dialog. At the top left, "Show changes from:" sits beside a small dropdown, "Your latest message". "Files:" heads a white list 768 by 130 with one row, "uploader.py, 2 lines added", selected in blue with the dotted focus outline. "Changes:" heads a large text box (768 by 293) in a monospaced font. It shows the full file path followed by ": 2", with "lines added." wrapped onto a second line, then "A change:", two "Added:" lines and an "Unchanged:" line. "Close" is alone at the bottom right.

**What looks off:** The first line wraps between "2" and "lines added". The files list is mostly empty. (An earlier draft called the row's outline an orange outline unique to this list; it is the same focus outline every focused list shows.)

## usage: View, Usage and Context

This is a 754 by 360 dialog, one of the smallest. "Usage and context:" heads a white list 722 by 258 with two rows. The first, selected, is "Visual probe: Context: 2 tokens used; the window's size isn't known until this session or another on the same model runs a turn here.", and it fits in full, ending a few pixels short of the list's right edge. The second is "Usage limits: not known until a Chat Place session runs a turn." "Copy", "Copy All" and "Close" are at the bottom right.

**What looks off:** Most of the list is empty. The dialog is now wide enough for the first row, but only just.

## about-you: View, What Claude Knows About You

This is the largest dialog, 860 by 640. A one-line note runs across the top. "Kind:" heads a list (828 by 120) with seven rows, such as "Instructions, 1 item: What you've told Claude to do in every session (CLAUDE.md), and in each project" (selected, with the focus outline), "Memories, 0 items..." and "Skills, 1 item...". "Items:" heads a second list (140 tall) with one selected row. A "Location:" row has a read-only grey box with a path. "Contents:" heads a text box (175 tall) showing "# How I like to work" and "Test everything.". Five buttons sit at the bottom right: "Edit in Your Editor", "Show in Folder", "Copy Path", "Reload" and "Close".

**What looks off:** Both lists show a blue selected row at once, so it isn't clear which has focus (as in #181). The Location row puts its label beside the box while every other field has its label above. The five buttons have uneven widths (75 to 116).

## session-columns: View, Session List Columns

This is a 640 by 560 dialog. A two-line intro runs across the top (608 by 31): "Each session in the list is read as one line. Choose what it says, and in what order: put Status first to hear what" and "each session is doing before its title.". Below it are two equal lists side by side, each 296 by 265: "Available columns:" with two rows, "Started (when the session began)" (selected) and "Last message", and "Shown columns, in the order they're read:" with ten rows (Title, Folder, Status..., Groups). Both have a selected row; the right one has the focus outline. Under them is a row of six buttons, "Add", "Remove", "Move Up", "Move Down", "Move to Top" and "Move to Bottom", then a very wide "Reset to Default" button (537 wide). "Preview of the selected session's line:" heads a 60-tall read-only box. "OK" (default) and "Cancel" are at the bottom right.

**What looks off:** The button row and Reset button stop at x=545 while the lists reach 618, so they look misaligned. The 537-wide Reset button is unlike any other button in the app.

## prompts: File, Prompts

This is a 680 by 560 dialog. "Prompts:" heads a white list 648 by 217 with "Review" (selected) and "Release notes". "Text:" heads a read-only text box of the same size showing "Review this change for bugs, then for accessibility." The two boxes split the dialog about evenly. Along the bottom are seven buttons: "Use" (default), "New...", "Edit...", "Delete...", "Move Up", "Move Down" and, at the far right, "Close".

**What looks off:** The Text box shows "...then for accessibility." while the Edit Prompt dialog for the same prompt shows only "Review this change for bugs.". This is probably different test data, but the two don't match. Both areas are mostly empty.

## prompt-edit: Editing a saved prompt

This is a 620 by 460 dialog. "Name:" heads a single-line box holding "Review". "Text:" heads a large multi-line box (588 by 311, about three quarters of the dialog) holding "Review this change for bugs.". "Save" (default) and "Cancel" are at the bottom right.

**What looks off:** Nothing. It is simple and matches the other dialogs.

## rename: Rename Session (wx's own text entry dialog)

This is a small 346 by 153 dialog. The line "New name for Visual probe:" sits above a text box 300 wide holding "Visual probe", which is selected in blue. A thin divider line runs below it, and then "OK" and "Cancel" sit at the bottom right.

**What looks off:** It is the only dialog with a divider line above its buttons, and its text box is inset 17 pixels, while other dialogs use 10. These are minor differences from the app's own dialogs.

## main-empty: Main window with no sessions at all (a first run)

This picture has the same layout as main-start. The session list on the left is completely empty white, apart from a thin dotted focus rectangle across its top row. The messages list on the right says "No sessions yet. Start one with File, New Session (Ctrl+N), in the Claude desktop app or in a terminal." (selected in blue), on one line that fits the list. The status bar shows two parts: "0 sessions: 0 need you, 0 working." and "No session loaded". It has no needs-you button, and its left part is wider (572).

**What looks off:** The session list itself stays empty white, with no words of its own; the line about how to start is in the messages list (#182 put it there).

## High Contrast and scaling, surface by surface

Only surfaces where a variant differs in a way worth knowing are listed. Everywhere else, the dark-100 picture is identical to light-100, and the High Contrast pictures are the same layout in the theme colours described above.

- **message-formatted:** In both High Contrast themes the code block has no background and no edge at all. The four lines are only indented and set in a monospaced font, on the same near-black (Aquatic) or cream (Desert) as the page. The table keeps visible lines, white in Aquatic and dark grey in Desert. In dark mode the code box is near-black on near-black (#2B2B2B on #1E1E1E, 1.2:1), so it barely shows either (#177).
- **shortcuts-page, user-guide, message-formatted:** In dark mode only the page turns dark and the frame and buttons stay light (#177). At 175% their "Read as Plain Text" and "Close" buttons are hidden behind the Windows taskbar.
- **new-session, continue-here, plan, bug-report, about-you:** At 175% these dialogs are taller than the screen's working area, and the taskbar covers their bottom row. Start and Cancel, the Report a Bug note and buttons, and the five About You buttons can't be seen; only the tops of the Plan dialog's four buttons show. Settings is as tall but keeps OK and Cancel in view; only its empty space is covered. At 150% every dialog fits.
- **All main-window pictures:** At 150% and 175% the window is shorter (632 and 529 tall), so the messages list shrinks to 255 and 152 tall while the reply box stays 120. At 175% the conversation gets barely more room than the draft.
- **main-status-bar:** In High Contrast the three status-bar buttons are outlined in white (Aquatic) or black (Desert), with the whole text inside the outline.
- **All surfaces in hc-desert-100:** The title bars show pale grey text on mid grey (1.2:1), so window titles almost disappear. This is Windows' own inactive title bar, not the app's drawing.
- **Lists in every variant:** The focused list is marked only by the thin dotted outline. In High Contrast the selected rows of focused and unfocused lists are the same aqua or rust brown, just as they are the same blue in light mode (#181).
