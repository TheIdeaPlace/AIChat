# Checking kept Claude history (#438)

Implemented in PR #440. Automated tests use invented conversations and never alter a
real provider profile. The Windows checks ran in ClaudeTesting, which is saved for
`AIChat / keep-438` when not in use.

Completed checks:

- Copies refresh on discovery and reads/turns; deleting an invented original leaves
  its copy and metadata readable, including after restarting the keeper.
- Rewrites preserve preceding bytes. Explicit permanent deletion removes the copy.
- No execution, Continue Here, provider links or ownership from a kept copy. A stale
  busy row cannot authorize resuming a deleted original. An actual owned running
  turn remains stoppable during a temporary transcript rewrite.
- 240 final VM preservation/probe/layout checks and 84 selected send/stop/continuation/
  deletion/Settings/clone checks pass. Four focused Settings checks also pass after
  correcting the access key, including a duplicate-mnemonic regression.
- Ten pictures reviewed: four Settings variants plus the kept-chat window, in light
  and High Contrast Aquatic at 100%. No layout flags; new text is 21:1 light and
  16.29:1 High Contrast. Accepted baselines and descriptions are in this PR.
- The Settings keyboard walk reaches History retention with Alt+Y, then the editor
  button, OK and Cancel by Tab; Shift+Tab reverses, and Escape returns to the chat.
  On the kept chat, the reply-focus key reaches its read-only explanation.

Kelly's listening check (JAWS and NVDA):

1. Open Settings with Ctrl+Comma and press Alt+Y. Expect a named read-only History
   retention and kept copies report. Read it by line; it should distinguish global
   settings from project or administrator overrides and explain local copies/backups.
2. Tab to Open Claude's retention settings. Its name should be complete. Opening an
   existing settings file uses your editor; The Chat Place does not modify it.
3. On a kept chat, expect the list, heading and messages label to identify read-only
   history. The reply-focus key should read the explanation instead of offering a
   send box. Continue Here and Open in Claude should not be offered.
4. Confirm the copy's messages can be read and searched normally. A same-disk kept
   copy still needs Back Up Now to another disk.

vmtest reports focus, roles and names; it does not verify what JAWS or NVDA speaks.
Higher display scaling was not recaptured for this PR. Short-screen scrolling and
button bounds have automated layout coverage.
