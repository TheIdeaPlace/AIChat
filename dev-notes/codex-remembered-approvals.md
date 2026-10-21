# TCP-owned remembered approvals (#307)

Kelly requested the terminal's approve-once experience, kept inside TCP. This
branch deliberately revises #330's execution contract: it remembers human consent
to native Allow Once authority, rather than promising a .git-only sandbox grant.
Git hooks/configured subprocesses retain that authority. The UI states this.
There is no automatic reviewer, no native retained approval and no native rule-file
write. This is a separate feature branch for testing, not a release.

Rules bind native chat identity, exact canonical starting folder, captured native
shell wrapper, Git prefix and timestamp. Matching accepts a small literal shell
grammar and reviewed Git options; compound/expanded scripts and destructive or
unknown options ask again. Resource scope is not inferred from command text.
Requests with separately listed extra access remain manual. The baseline native
project/system rule checks, sign-in checks, tool disabling and owned-thread
verification remain in place.

The permission lock covers dispatch and durable removal. Removal cannot recall
already dispatched work; afterward the next request cannot use the removed rule.
Mode changes revoke before native resume, including failure. New native chat IDs
start empty. Unknown/newer/corrupt permission files never authorize a request and
ordinary edits preserve their contents; clearing an unknown format preserves it
under a unique sibling name. Uncertain answers are never retried. Pre-send errors
leave manual Answer available.

Local native Codex 0.163.0 on Windows passed ten credential-free scenarios:
branch/add/commit, repeated add and commit, local push twice, copy isolation,
restart and removal. The output is in the task's temporary folder. The workflow
also runs Windows/macOS against 0.162.1 and 0.163.0. Authenticated HTTPS push and
screen-reader listening remain explicit hand tests; the local remote probe does
not establish those outcomes. VM screenshots and complete-suite results are
recorded with the candidate's validation report.
