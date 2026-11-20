# Local file links (#253)

No application, wx app/window tests, visual probe or keyboard walk was run on
Kelly's PC for this change. Only the named pure test modules were run. CI owns
the GUI regression tests; the following checks remain before landing.

## Visual and keyboard checks in the test VM

Run the probe on `links`, `code-blocks`, `local-file-links`, `local-file-code`
and `local-file-reading`, at `light-100` and `hc-aquatic-100`. Compare with
`tools/ui_compare.py`, review changed pictures with `tools/ui_review_prompt.md`,
and accept only reviewed baselines. No claim about appearance has been made.

With JAWS or NVDA enabled, open the `local-file-links` probe surface. It
starts on the first file in the Links list. Check these steps with real keys:

1. Listen for the document name, **file**, who wrote it, and **in code**.
   Tab reaches Details, Open, Open containing folder, Copy, Copy as Markdown,
   and Close. Shift+Tab visits them in reverse. Each button's native name,
   role and enabled state should be spoken once, without a separate echo.
2. Arrow to the script. The row explains **Executable, script or shortcut
   type**. Open is disabled; Tab reaches the folder action and Copy.
3. Arrow to the folder. The row says **Folder. Use Open folder or Copy.**
   The folder action is named **Open folder**. Open remains disabled.
4. Arrow to the missing file. Hear **File not found. Copy only.** Tab skips
   disabled actions and reaches Copy. Enter explains why it cannot open.
5. Escape returns to the same message. No file or folder opens from merely
   selecting an item, filtering or moving between kinds.

The `local-file-code` surface checks the same Open and folder actions on a
single-path code block. Enter must focus Details; Copy must keep the original
code text. Opening the probe surfaces alone never activates their file actions.

## Actual file actions and formatted reading

Use a test chat containing paths to a disposable text file, a directory, a
missing file, and an empty `.ps1` file. Include a quoted or inline-code path
with spaces, a Markdown link target, a file: URL, and a path ending in `:12`.
Use a relative path such as `src/notes.txt` with a known session folder.

1. Ctrl+L lists every reference as a file. Open on the text file closes Lists
   and opens its default application. Open containing folder selects it in
   Explorer. A folder's Open folder displays its contents.
2. Delete the text file after opening Lists. Choosing Open or the folder
   action must report that it was not found and launch nothing.
3. The script never offers an enabled Open. Its folder action reveals it
   without executing it; Copy copies the path.
4. Open the message in formatted reading. With JAWS/NVDA browse mode, Tab or
   the usual link-navigation key reaches the local link; Enter on the text
   file opens its default app. A missing file or script explains the
   restriction and opens nothing. Dismiss that message, close formatted
   reading, and use Ctrl+L for folder access or Copy.

On macOS, repeat link activation with VoiceOver's link rotor and VO+Space;
the folder action must reveal in Finder. A `.app` directory's Open folder
must show a Finder folder window without starting the application. This
Finder behavior still needs an actual macOS check. Linux file-manager and
Orca checks run in CI; no manual Linux run is required.

Offline UNC paths have not been tested against a real network share. Each
listed file uses one status snapshot, but filesystem discovery can still
wait for the operating system's network timeout. Activation always checks
again, including symlink targets and executable permissions on Unix.
