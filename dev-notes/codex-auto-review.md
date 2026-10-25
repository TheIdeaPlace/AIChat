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
