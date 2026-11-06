# Codex parity validation (#558)

Branch: `codex/05preview-parity`, based on preview commit `5c21f9b74b819108829ce93e281b09e7744a19d4`.
The preview checkout is not modified. `f859738` is an implementation checkpoint, not a release candidate.

## Bounded completion passes

1. Runtime and permission boundaries: targeted backend tests and independent review.
2. Dialogs: VM tests, light and High Contrast pictures, keyboard traversal.
3. Documentation and final integration: generated guide, focused review of corrections, final regression run.

## Automated and VM results

- Focused backend regression: 415 passed.
- Focused VM GUI suite after integrating preview `c88f8be`: 318 passed.
- Visual probe: 18 pictures across light and High Contrast, zero layout flags.
  Eight changed/new pictures reviewed; existing light-theme Connected Tools
  focus contrast remains tracked in #181; native border contrast is tracked in #532.
- Real VM keyboard traversal: Chat Options starts on Instructions; Tab reaches
  More folders, Add Folder, Remove, About, OK, Cancel and wraps. Shift+Tab reverses
  and Escape closes without saving. Connected Tools supports arrow selection;
  Tab reaches Sign In, Reconnect, Copy with Tool Off, Refresh, Close and Details.
  Shift+Tab returns to Close; Escape closes. Speech needs the checks below.

## Checks needing Kelly's accounts or screen reader

- In an owned Codex chat, open Chat Options. JAWS/NVDA should start on Instructions, then Tab
  through More folders, Add Folder, Remove, About these options, OK and Cancel. Escape returns
  without saving; Ctrl+Enter saves valid options. Check the additional scope in Chat Permissions.
- Enable a configured test MCP server in a copy. Sign In should first read the actual web address.
  Complete OAuth in the browser; listen for the result, or use Refresh if this Codex version supplies
  no login identity. Check that canceling the address dialog opens no browser.
- Ask a test server for a form. Answer should expose named controls, validate missing required answers,
  preserve numeric zero, and leave the request waiting on Escape. Decline sends no form content.
- Ask Codex to delegate a small test task. View, Subagents should expose each child and its speaker
  names. Child approvals identify the subagent; a finished child must not leave an answerable request.
- With Show Tool Activity on, read a reasoning summary. It must not be spoken as a final reply.
  If Codex reports a retry, listen for one notice per changed status and check Turn Status.
- Enable a test app in a copy. Confirm the original selection is unchanged and only selected apps
  can be used. Provider-specific verification forms are not supported and must be declined.
- Ask an owned Codex chat to list sessions, get itself, search a synthetic chat, and hide an idle
  test chat. It must exclude itself from the list and refuse to hide itself, an open chat, or running work.

No live OAuth/account-dependent connector calls have been claimed as verified by automated tests.

Draft integration PR: https://github.com/TheIdeaPlace/AIChat/pull/563.
