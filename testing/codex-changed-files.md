# Codex Changed Files

Use a disposable folder and a Codex chat with permission to edit it. These checks cover
keyboard focus and speech, which the screenshot probe cannot verify.

1. Ask Codex to create a text file, change an existing file, delete another, and rename one
   using its patch tool. After the turn ends, at Full announcements, its reply should be
   followed by a summary of the files changed. Reopening the chat must not repeat old changes.
2. With Show Tool Activity off, press Ctrl+Shift+D (Cmd+Shift+D on a Mac), or use View,
   Changed Files. Lists opens on Changed Files, scoped to the selected message's turn.
   Arrow through the files: hear the name, line counts, and created, deleted or renamed status.
3. Press Enter to reach Details. Read by line: the location, then Added, Removed and Unchanged
   lines. A rename includes the old path. Ctrl+C on the file's list row copies its path;
   a renamed file copies the new path. Escape closes Lists and returns focus.
4. Run another turn that changes different files. Select an earlier message and press
   Ctrl+Shift+D: only that turn's changes appear. Alt+S, The whole chat shows both turns.
   A message whose turn changed no files opens Changed Files for the whole chat.
5. Open a Codex chat started outside TCP with patch changes. Changed Files works there too;
   viewing changes does not resume it or send a message. A chat with no recorded file changes
   says "No files in this chat."
6. Decline a requested patch. It must not appear as a completed change. A successful patch
   remains listed if a later action fails or the turn is stopped. File changes made through
   shell commands are absent unless Codex records them as file changes.

Changes are grouped by path. Deleting and recreating the same path keeps both
changes in one row, with their combined line counts and no separate "recreated"
label. If a file was edited before being renamed, the earlier edits remain under
the old path; the new path has a separate row naming where it was renamed from.
The two paths' histories are not joined.

The synthetic visual surface is `lists-codex-files` in `tools/ui_probe.py`. It uses the
real Codex history reader and shared Lists dialog, with an update, addition, deletion and
rename. `lists-codex-deleted-file` selects the deletion and shows its old line
range. Run them with `changes` as a shared-viewer regression, in `light-100` and
`hc-aquatic-100` at minimum.

Protocol reference: [OpenAI's app-server documentation](https://learn.chatgpt.com/docs/app-server),
especially fileChange items and their completed, failed and declined statuses. A read-only
check of installed app-server history on 2026-10-27 confirmed that updates have unified
diffs and additions/deletions contain plain file content. No prompts were sent by that check.
