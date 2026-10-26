# Codex automatic approval review (#358, #374)

The selected reviewer is independent of sandbox access. Human review remains
the default; automatic review is an explicit choice for an owned chat. Creation,
fork and resume verify the returned reviewer, and every turn pins it alongside
the existing ownership, model-provider, billing and filesystem checks. Unknown
or policy-rejected reviewer settings do not silently fall back. Changes wait for
an idle chat and successful persistence before the new runtime becomes usable.

The installed codex-cli 0.164.0-alpha.11.1 generated the experimental protocol
schema used for the autoApprovalReview notifications. A live ephemeral lifecycle
check accepted auto_review with the read-only sandbox; no message was sent.
Decision notifications are display data, never local permission answers. The
latest 128 decisions are retained in a separate TCP companion file and remain
readable in Chat Permissions when Codex cannot list its own saved rules.

An independent reviewer found three defects, all corrected with regression tests:
missing probe class mappings; retaining the first rather than latest 128 reviews;
and hiding local review history when native-rule inventory failed. The reviewer
found no ownership or billing bypass in the new choice and turn verification.

Validation on the reconciled code, including main's About You work (#371):

- Full local suite: 2,878 passed, 7 skipped. After adding the bundled guide,
  CI caught a bold span split across lines in its plain-text rendering. Keeping
  that span on one line fixed it; 24 rendering, generated-doc and Help tests pass.
- Local screenshot checks cover all nine affected states at the current 200%
  desktop scaling. No layout flags. Properties needed one retry because no
  dialog appeared in the first run. The review-history surface scrolls to show
  both an allowed and denied decision, rather than only the introductory text.
- Screenshots are under the local temporary tcp-ui-probe folder in
  issue-358-local-fixed, issue-358-local-final and issue-358-history-final.
  Comparison reports label these as new because the local-default tag has no
  baseline. No baseline was accepted and no High Contrast result is claimed.
- The required VM light/High Contrast probe could not start: Get-VM is absent.
  Native screen-reader speech and High Contrast still need a suitable test host.
- The first macOS packaging CI attempt failed during .NET setup with HTTP 409
  downloading the SDK/runtime, before application tests/build. This is separate
  from the Windows/macOS pytest workflows; their latest results are on PR #374.

The release-note conflict with #371 was resolved by retaining both entries.
Generated documentation includes both changes. Other open Copilot work was not
included or modified.

Permission-change regression, verified with installed codex-cli 0.163.0:
`thread/resume` on an already loaded thread returns its existing sandbox and
reviewer, ignoring the requested overrides. An empty persistent thread cannot
be resumed before its first turn has saved history, either. Permission changes
now keep the owned thread loaded, verify the requested policy in an ephemeral
thread without inference, and pin that policy on the owned chat's next
`turn/start`. The ephemeral check never receives an executable ownership proof.
The existing persistence barrier still prevents sends before the change is saved.

Live regression validation switched one synthetic chat through folder editing
with automatic review, read-only with human review, folder editing with human
review, and read-only with automatic review. Each mode completed a tiny text
turn; the first change happened before any message. Reloading the saved record
into a fresh runtime preserved automatic review and completed another turn.
The synthetic chat was archived afterward. The window's creation helper also
forwards the reviewer again, fixing the missing-argument error in New Session
and Continue Here. No user's chat content was used in regression fixtures.

The full local suite passed (3,134 passed, 7 skipped). A final recovery case
also verifies that an unloaded chat can select read-only when its saved editing
mode is no longer available; all 189 ownership, runtime, execution, approval and
automatic-review tests pass with that case included. Hidden-window tests cover
all four creation/fork permission choices and the permission-change command.

The VM visual run completed at `%TEMP%/tcp-ui-probe/20261025-225332` for New
Session, Continue Here, Change Permission Mode and User Guide, in light-100 and
hc-aquatic-100. All eight screenshots were opened and reviewed, with no layout
flags. Comparison against the baselines reports 0 changed, 8 unchanged and no
new, missing or failed surfaces. No baseline or visual description changed.
