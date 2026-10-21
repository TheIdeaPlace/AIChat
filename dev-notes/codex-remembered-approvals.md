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

The permission lock covers the final wire enqueue and durable removal. Sign-in
checks run on approval workers, leaving the event listener free to drain other
chats. A rule is rechecked after sign-in, so removal during that check cancels
dispatch. Each request is evaluated once, even if reading the rules fails; only
an explicit Allow for This Chat also reconsiders siblings already waiting.
Requests under automatic consideration stay out of Answer until that decision
finishes; unmatched siblings become available before sign-in begins.
Removal cannot recall
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

## Review follow-up, 21 October 2026

Claude's nine-item review on #307 is addressed on this test branch:

1. Automatic answers run on workers; sign-in never holds the dispatch lock.
   Removal, ownership and pending-request identity are checked again at enqueue.
2. Opening a chat selects its latest conversation row before notice/queue tails.
3. An unreadable or unmatched request stays manual. An explicit grant reconsiders
   only matching siblings already waiting. Concurrent explicit grants serialize
   per chat so an earlier rollback cannot erase a later grant.
4. Known pre-send failures remove newly saved consent; uncertain sends report
   whether consent remains and require reconciliation. Failed rollback is visible.
   Lost ownership is classified as not sent.
5. Permanent owned-store removal cleans that chat's approval and preserved files.
6. Documentation distinguishes explicit force/delete options from push configuration.
7. The native-choice note is conditional on Allow for This Chat; user text spells
   out The Chat Place.
8. Turn Status pluralizes request counts without repeating the consent phrase.
9. Version remains 0.1.9. The open 0.1.10 notes file is preparation, not a release.

Validation: ClaudeTesting ran the complete suite: **2,528 passed, 7 skipped** in
172 seconds. The portable PyInstaller build's VM smoke report has no problems,
including Codex permissions, speech/client imports and WebView2. The real Codex
0.163.0 probe passed ten scenarios again. The four-job native probe matrix passed
on Windows/macOS with Codex 0.162.1/0.163.0 on the first branch commit.
JAWS/NVDA listening and authenticated HTTPS pushes still need Kelly's hand test.
Visual probes in light-100 and High Contrast Aquatic flagged no layout problems.
The six changed pictures were inspected, compared and reviewed. The native-choice
fixture was then extended to show the conditional note and captured again. Text
contrast remains 21:1 in light mode and 16.293:1 in High Contrast; selected text
is 4.529:1 and 7.893:1 respectively. The default-button outline is 3.974:1.

VM setup encountered two infrastructure problems, both worked around without
opening anything on Kelly's PC: winget's Git source could not resolve its host;
`vmtest deploy 'C:\Program Files\Git' -Name Git` failed with
`Cannot retrieve the dynamic parameters for the cmdlet. The specified wildcard
character pattern is not valid: [.exe`. Transferring a ZIP of Git and extracting
it in the VM worked. No vmtest files were changed.
