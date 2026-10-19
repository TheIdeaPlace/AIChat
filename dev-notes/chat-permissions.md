# Chat Permissions: Codex backend and Claude integration

Native execution follow-up: [scoped execution investigation](codex-scoped-execution-investigation.md).
Real Codex 0.162.1 executes a `.git`-only grant on macOS while blocking outside
writes. Windows elevated/unelevated still fail `.git` writes, and managed networking
is unsupported in unelevated mode. Scoped tools are disabled development features
by default. The production backend does not enable them; #307 remains incomplete.

Parent: #307. Claude follow-up: #329. Keep the parent open until its success criteria are met. This replaces #298's
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

## Current eligibility limit

Full-escalation commands never offer or match a kept rule. Git configuration and
hooks can run other programs, including from repositories the agent creates, so
reusing an unsandboxed approval is unsafe even for `git status`.

Reuse requires an explicit `additionalPermissions` profile with literal filesystem
grants inside the chat folder. Unbounded network profiles, network-only requests,
empty/deny-only profiles and grants outside the chat are ineligible. Network-wide
`network.enabled` profiles are never reused, even if a host is also named. Managed
host context, when present on a filesystem grant, remains part of the scope.

**The native approvals captured on Windows and macOS have no such profile and
therefore remain manual.** This PR and #329 do not yet fulfill #307's goal of
avoiding repeated approvals for those common full-escalation requests. Keep #307
open for that remaining goal. Do not present this as a completed solution to it.

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
The request cwd must equal that root. The scope binds the ordinary `.git` metadata
location and configuration fingerprint, so configuration changes require a new grant.
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
an error without rewriting the file. The file has a format version; ordinary edits
refuse newer formats, unrecognized fields and invalid JSON. During a mode change,
unreadable or unsupported files are moved to `<hash>.preserved-<uuid>.json` beside
the original, then replaced with an empty rule set. A failed replacement restores
the original when possible. This preserves the bytes while allowing mode changes.

App-server serializes exec argv using shlex quoting. Matching first unwraps exactly
one recognized shell invocation: the Windows system PowerShell executable or
Program Files PowerShell 7 `pwsh.exe`, optional `-NoProfile`, then `-Command` and
one script; or `/bin/bash`, `/bin/zsh`, `/bin/sh` with `-c` or `-lc` and one script.
Other locations, switches, extra argv and nested shells remain manual. Shell
identity and switches are saved in the opaque scope, so grants cannot cross them.

The inner command must be printable ASCII, one simple command, exact leading
words, and literal whole words or quoted literals. Expansion/control syntax,
backslashes, executable paths and mixed/adjacent quotes remain manual. Empty words,
commas, `@` (including inside messages) and words beginning with `=` are refused
because PowerShell/zsh can rewrite them. Only
`git`/`git.exe` with a prefix of at least two words is eligible, for `status`,
`switch`, `add`, `commit`, `push`, `fetch`, `diff`, and `log`. Both candidacy and
matching validate **every** argument against the operation-specific `GIT_FLAGS`
and `GIT_VALUES` allowlists. Unknown options, explicit transports, absolute/drive
paths, parent traversal and symlink escapes remain manual. Commit message values
are literal data, so path confinement is not applied to their text. In particular,
`switch -c` means create a branch; leading global `git -c` is never eligible.

Destructive options such as `--force`, `-f`, `--force-with-lease`, `--delete`, `-d`
and `--amend` are never kept. Push/fetch require an explicit remote name from the
root `.git/config`, with a supported network URL; local paths and local-path URLs
are not eligible. Gitfiles, bare repositories, redirected metadata, alternate
worktrees, config includes and URL rewrite sections are refused. Configured
push URLs and process overrides are also checked conservatively. No Git process
is run to inspect this configuration.

There are no rules for `gh`, `rg`, archive/file tools, downloaders, interpreters or
PowerShell aliases. Git's ordinary configured behavior, including hooks, still
applies to a scoped request; this is why full escalations are never reused. Keep
their existing outside-sandbox warning visible in manual Answer. Do not broaden
this grammar in UI integration.
Extra permission profiles, hosts and protocols
must be identical, including hidden profile fields; even a narrower but different
profile asks again. Literal extra-access paths must be absolute, local and resolvable
and are bound to their resolved targets; relative, glob and special paths have no
chat-rule offer. Both folders must resolve to the same existing local directory.
Unreadable rules fail closed.

Only the listener auto-answers, including approvals received before turn startup
returns. `CodexApprovalNotSent` means the approval payload was never queued: the
request is not retried automatically and manual Answer remains usable. Other
response failures are treated as uncertain and block further sends/answers until
reconciliation. A failed automatic approval never makes a successful Send report
that the turn failed to start. Connection failures retain their own diagnostic;
encoding failures do not blame sign-in.

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
  Full escalations have no candidate; leave their ordinary Answer controls intact.
  The first native probe's eligible results preceded this safety restriction.
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
- Run the full test suite. Keep #307 open until its repeated-approval goal for
  common native requests is actually achieved; this scoped subset does not finish it.
- Claude-only listing/removal, timestamps and the button rename can proceed
  independently. Codex integration needs PR #330. Native Mac/Windows approval-shape
  validation is complete, as described below; the integrated UI checks remain here.

## Backend validation

The live Windows probe used installed **codex-cli 0.162.1**, a temporary empty
Git repository and a synthetic prompt. No approval was accepted. It captured:

```
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command 'git switch -c tcp-permission-probe'
proposedExecpolicyAmendment: ["git", "switch"]
environmentId: "local"
additionalPermissions: null
```

Claude subsequently supplied a credential-free native probe using real Codex
0.162.1 with a local fake Responses API in an isolated temporary `CODEX_HOME`.
The workflow runs on GitHub's macOS and Windows runners and declines every approval.
Its [second attempt](https://github.com/TheIdeaPlace/AIChat/actions/runs/39253925878/attempts/2)
checked the pushed backend commit `d2130327cccd096279a8f216d6b8cc2f5205348b` on both
platforms. The `probe-macOS` and `probe-Windows` artifacts' `out-pr330-*.json` files
show successful candidacy for both `git commit -m probe` and `git push origin main`,
with completed turns and no probe error. The Mac-capture evidence gap is closed.
Those results predate the full-escalation refusal. Current replay tests still
decode these exact requests but require **no candidate** because their profiles
are absent. They do not establish reusable scoped grants against native Codex.

The macOS runner used `/bin/bash -lc '...'`; Windows used the double-quoted Program
Files PowerShell 7 `pwsh.exe` path with doubled backslashes and `-Command`.
The synthetic stdio tests now exercise bash and zsh on POSIX hosts, and system
PowerShell and Program Files PowerShell 7 on Windows. The full captured request
shape is also replayed with synthetic IDs/paths, including `commandActions` and
the native `acceptWithExecpolicyAmendment` offer, which remains unavailable to TCP.
Python `shlex.join` alone produces a different Windows serialization, so the fixture
preserves the captured double quoting/backslashes explicitly.

These native probes establish the real protocol shape. Repeated automatic approval
and removal use synthetic stdio requests with explicit `.git` filesystem grants;
full UI, keyboard
and speech integration still belongs to #329.

Real stdio fake-server tests cover a scoped wrapped first grant, automatic second commit,
compound-command refusal, immediate removal and exact one-time wire answers.
Regression tests cover every reported process-launch option, full-escalation path
escapes, unsupported executables, ambiguous quotes, wrapper identity, startup races,
pre-send refusals, listener uncertainty with no retry, failed mode-change revocation,
restart, a copied chat's first approval, and preservation of unknown storage formats.
No UI appearance changes are made by this part, so UI probes belong to Claude's
integration PR. The PR records current test results.

Current review regression coverage also checks full-escalation refusal, agent-created
bare/nested repositories, external gitfiles, local remotes, rewritten shell words,
destructive options, preservation during mode changes, and accurate pre-send errors.
Validation: the full repository run passed 2,596 tests with 7 skipped. The latest
focused Codex/runtime/transport/storage run passed all 437 tests, including the
final profile restrictions and connection-error diagnostics.
