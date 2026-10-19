# Chat Permissions: Codex backend and Claude integration

Parent: #307. Claude follow-up: #329. Keep the parent open until both parts ship. This replaces #298's
in-memory Codex session-approval approach.

## Work split

Codex implements the provider backend: immutable proposed prefixes, conservative
matching, TCP-only persistence, immediate revocation, one-time wire answers,
automatic-answer notices, and synthetic protocol/runtime tests. The implementation
is on branch `codex/chat-permission-backend`.

Claude implements the shared user experience for **both** providers: Chat Permissions,
the approval buttons, Claude rule metadata/removal, accessible announcements and
history/status presentation, visual evidence, and user documentation. The backend
alone does not expose a new approval button or complete #307.

## Codex backend contract

Call through `SessionController.owned_action(action, record, *args)` on a worker,
returning to wx with `wx.CallAfter`. Each action requires an owned Codex record.

| Action | Arguments after record | Result |
|---|---|---|
| `chat_rule_candidate` | immutable pending request | `CodexChatRule` or `None`; only offer the button for a candidate |
| `allow_for_chat` | same pending request | saves the rule, then sends only `accept`; errors grant nothing or report an uncertain answer |
| `chat_rules` | none | tuple of saved rules, also available before reopening a chat |
| `remove_chat_rule` | `rule_id`, or `None` for all | remaining rules; revocation is effective when the call returns |

`CodexChatRule` has `rule_id`, `prefix` (tuple), `description`, canonical chat-root `folder`,
`allowed_at_ms`, `access` (plain words), and an opaque `scope` comparison value.
Display the description, actual prefix, date/time, and access. Do not interpret
or change `scope` in the UI. Include the full outside-sandbox warning when present.

`CodexChatState.permission_notices` is a bounded tuple of
`(turn_id, request_id, text)`. Deduplicate with **chat ID + turn ID + request ID**
(IDs can be strings or integers). Announce each new entry quietly once and add it
to that chat and Turn Status. Repeated snapshots are not new notices. Keep entries
associated with their original chat/turn, including updates received while another
chat is open. These notices are runtime data, not writes to Codex transcripts.

Rules live under TCP's app-data folder in
`provider-permissions/codex/<sha256-native-id>.json`, a sibling provider store.
They survive restart. Continue Here uses a new native ID and inherits no rules.
Permission-mode preparation clears kept rules before verifying the new mode;
even a failed change cannot retain an earlier grant. Removal does not recall a
command whose one-time approval was already sent. An unknown removal ID raises
an error without rewriting the file. The file has a format version; newer formats,
unrecognized fields and invalid JSON are preserved, and edits fail visibly.

App-server serializes exec argv using shlex quoting. Matching first unwraps exactly
one recognized shell invocation: the Windows system PowerShell executable or
Program Files PowerShell 7 `pwsh.exe`, optional `-NoProfile`, then `-Command` and
one script; or `/bin/bash`, `/bin/zsh`, `/bin/sh` with `-c` or `-lc` and one script.
Other locations, switches, extra argv and nested shells remain manual. Shell
identity and switches are saved in the opaque scope, so grants cannot cross them.

The inner command must be printable ASCII, one simple command, exact leading
words, and literal whole words or quoted literals. Expansion/control syntax,
backslashes, executable paths and mixed/adjacent quotes remain manual. Only
`git`/`git.exe` with a prefix of at least two words is eligible, for `status`,
`switch`, `add`, `commit`, `push`, `fetch`, `diff`, and `log`. Both candidacy and
matching validate **every** argument against the operation-specific `GIT_FLAGS`
and `GIT_VALUES` allowlists. Unknown options, explicit transports, absolute/drive
paths, parent traversal and symlink escapes remain manual. Commit message values
are literal data, so path confinement is not applied to their text. In particular,
`switch -c` means create a branch; leading global `git -c` is never eligible.

There are no rules for `gh`, `rg`, archive/file tools, downloaders, interpreters or
PowerShell aliases. Git's ordinary configured behavior, including hooks and
remotes, still applies when a Git command is approved. Keep the outside-sandbox
warning visible for full escalations. Do not broaden this grammar in UI integration.
Extra permission profiles, hosts and protocols
must be identical, including hidden profile fields; even a narrower but different
profile asks again. Literal extra-access paths must be absolute, local and resolvable
and are bound to their resolved targets; relative, glob and special paths have no
chat-rule offer. Both folders must resolve to existing local directories and
the request folder must be within the chat folder. Unreadable rules fail closed.

Only the listener auto-answers, including approvals received before turn startup
returns. `CodexApprovalNotSent` means the approval payload was never queued: the
request is not retried automatically and manual Answer remains usable. Other
response failures are treated as uncertain and block further sends/answers until
reconciliation. A failed automatic approval never makes a successful Send report
that the turn failed to start.

The official [app-server approval documentation](https://learn.chatgpt.com/docs/app-server)
describes the one-time `accept` decision separately from session and saved-rule
decisions. Existing transport validation continues to reject lasting decisions.

## Claude implementation checklist

- Add **File, Chat Permissions** for chats started here, working or idle; exclude
  desktop/terminal sessions. Choose a shortcut only after checking `SHORTCUTS`.
- Show permission mode in words and reuse the existing Change Permission Mode
  dialog; disable Change while working. Refresh the rules after a mode change.
- Show one fully readable row per rule, its actual syntax/prefix and when allowed.
  Add Remove, Delete and Remove All with a confirmation naming the selected rule
  (or all rules). Preserve sensible selection/focus. Show the empty-state sentence
  from #307. Keep persistence failures visible; never claim removal succeeded early.
- For Codex, use the worker actions above. Show the existing native rule-file
  count from Properties, read-only, with the explanation in #307. Keep that count
  separate from TCP's per-chat rules. Removal applies to the next request, including
  during the running turn.
- In Codex Answer, obtain a candidate before offering **Allow for This Chat**.
  Give it a full accessible name including the prefix, reviewed simple arguments,
  and rest-of-chat duration. Explain that other options still ask; do not promise
  unrestricted "any arguments". The rule's description reflects this restriction.
  Route it to `allow_for_chat`, not `request.approval_answer` with a new decision.
  Leave ordinary Allow Once/Deny/Stop paths intact. Replace the old generic
  "TCP leaves lasting approvals out" note when a TCP chat-rule option is offered;
  still explain that Codex's own saved/session rules are not enabled.
- Consume `permission_notices` as described above for speech, chat and Turn Status.
- For Claude, retain `allowed_tools` and the existing `--allowedTools` path.
  Add backward-compatible timestamps in TCP storage. Existing rules have an unknown
  allowed time; do not invent one. Rename **Allow for this session** to **Allow for
  This Chat**, preserving the complete accessible rule name. Removing a rule affects
  the next turn; explain that a running turn keeps its starting rules.
- Ensure Continue Here starts with no kept rules for either provider and says so
  in its note. Add Claude fake-CLI tests for listing/removal and next-turn behavior,
  timestamp migration, restart, and copy isolation. Use synthetic content only.
- Add hidden UI tests for both providers, owned/read-only availability, mode-change
  enablement, keyboard Delete/confirm/cancel, focus after removal, empty state,
  worker failures, candidate refusal, and deduplicated automatic-answer notices.
- Add a Chat Permissions probe surface and update Answer. Run light-100 and one
  High Contrast variant in the test VM, compare and review screenshots, measure
  contrast, accept reviewed baselines, and update visual descriptions. Follow
  CLAUDE.md's visual review requirements. Leave keyboard/speech hand checks for Kelly.
- Update README/user guide, `ui_text.SHORTCUTS` if adding a key, regenerate `docs/`,
  and add a user-facing release-note entry referencing the implementation PR.
- Run the full test suite. Close #307 and superseded #298 only after the integrated
  feature and required checks are complete.
- Claude-only listing/removal, timestamps and the button rename can proceed
  independently. Codex end-to-end integration needs PR #330 and live macOS approval
  validation; a Mac capture is outstanding, as described below.

## Backend validation

The live Windows probe used installed **codex-cli 0.162.1**, a temporary empty
Git repository and a synthetic prompt. No approval was accepted. It captured:

```
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command 'git switch -c tcp-permission-probe'
proposedExecpolicyAmendment: ["git", "switch"]
environmentId: "local"
additionalPermissions: null
```

The synthetic Windows fixture reproduces this Rust shlex serialization, including
its doubled backslashes inside double quotes. Python `shlex.join` alone produces a
different Windows representation. The macOS fixture uses `/bin/zsh -lc` with one
quoted script, and unit tests cover bash/sh and `-c` too. **No live Mac capture
was available in this session.** Run that check before claiming live macOS support.

Real stdio fake-server tests cover the wrapped first grant, automatic second commit,
compound-command refusal, immediate removal and exact one-time wire answers.
Regression tests cover every reported process-launch option, full-escalation path
escapes, unsupported executables, ambiguous quotes, wrapper identity, startup races,
pre-send refusals, listener uncertainty with no retry, failed mode-change revocation,
restart, a copied chat's first approval, and preservation of unknown storage formats.
No UI appearance changes are made by this part, so UI probes belong to Claude's
integration PR. The PR records current test results.

Review-fix validation: the full repository run passed 2,542 tests with 7 skipped.
The latest focused Codex/runtime/transport/storage run passed 376 tests, including
the final storage guards and copied-chat first-approval check.
