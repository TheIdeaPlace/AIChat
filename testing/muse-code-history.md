# Muse Code history validation

Use NVDA 2026.3.1 (2026.3.1.58288) and JAWS 2027.2610.428
(Script Revision: 0), unless the user specifies updated versions. Use synthetic
conversation content and record manual results separately from automated tests.

## Installed Muse

1. Start The Chat Place with Muse Code 1.4.18 installed. No sign-in offer should
   appear during discovery. An empty Muse catalog must not hide other providers.
   Choose View, Show Sessions, Muse Code Chats with no stored Muse conversations.
   Confirm the guidance says none were found and explains how to refresh after
   starting one in Muse Code. Enter must repeat that guidance. Pending or failed
   discovery must have different wording, without claiming the catalog is empty.
2. With an existing synthetic Muse conversation, press F5 and locate its row.
   Confirm the title, Muse Code provider, folder and status unavailable are
   readable. View, Show Sessions, Muse Code Chats filters only Muse rows.
3. Press Enter. Read user and Muse replies, copy a message, search and export.
   Enable tool activity to read recorded tool calls and output. No Muse turn,
   tool, workspace-trust prompt or new conversation should be started.
4. Check the read-only explanation. Send, Stop, Continue Here, Answer and
   permanent deletion must not become available for Muse. Return to the same
   session-list row using the normal keyboard workflow.
5. Restart The Chat Place and open the conversation again. Its identity and
   readable history should be stable; it must not become a Chat Place session.
6. Test a catalog with multiple pages. All sessions should appear once.
7. Make history unavailable while a chat is open. The loaded messages and focus
   must remain; the status must explain the failure without raw protocol data.
   Repeated timer retries must not keep announcing the same failure. Restore
   availability, press F5 and confirm recovery.
8. Test unsupported items and truncated history. The warning must be readable
   even with tool activity hidden. Complete-history-unavailable cases must not
   be described as an empty conversation.
9. Start loading a chat, select another, then close the window during a slow
   request. Stale callbacks must not replace the new chat or show late dialogs;
   the Muse child host must exit.

## Visual and platform checks

Run the Muse history and unavailable-history probe surfaces in a Windows test
VM at light 100% and one High Contrast variant. Review clipping, overlap and
measured contrast. Walk real Enter, Tab, Shift+Tab, F6 and Escape; verify focus,
names and announcements separately with both screen readers. Record Mac and
Linux Orca CI results separately. Do not change the user's desktop theme to
simulate a VM run.

## Evidence

On 2026-11-13 the installed Windows Muse 1.4.18 host accepted the verified stable
and experimental schema fingerprints. A signed-out session/list request returned
an empty catalog. A read-only request for a nonexistent valid synthetic UUID
returned sessionNotFound (-32020), confirming that session/read is recognized.
No conversation was created and no prompt or sign-in request was sent.

The completed MuseProvider also passed a native read-only smoke check: it
returned the empty catalog, reported chat creation unavailable, and closed its
host cleanly after bounded executable/version and schema verification.

Automated verification on 2026-11-13:

- Full regression suite: 6,805 passed, 61 skipped, one non-failing warning from
  the existing Claude test double's StuckStdin cleanup; 495.57 seconds.
- Final focused Muse UI, probe/layout, Orca-step and generated-document checks:
  21 passed after the final tooling expectation and cross-platform hidden-focus
  test adjustments.
- Synthetic transport/provider tests cover real subprocess pipes, handshake
  rejection, pagination, history parsing, stale data, reconnect, shutdown races
  and refusal of write operations. Initial-load recovery is checked to announce
  a history load rather than an old reply as newly arrived.
- git diff --check passed. No visual baseline was accepted.

The original empty-catalog check did not establish a populated native read.
The isolated native echo test below now covers that path. Pagination and failure
cases are tested with synthetic responses. Manual reader and visual results for
this milestone must be recorded when performed; earlier onboarding results do
not establish history-flow passes.

## Follow-up: Enter in an empty Muse view

A native count-only check returned zero stored Muse conversations. The empty
Muse filter previously retained generic instructions to select a session, and
Enter reported only "No session selected." The view, its persistent empty-state
text and Enter feedback now explain the catalog/discovery state. Pending or
failed discovery is not described as an empty catalog. An installed provider
with a confirmed empty catalog points users to start a conversation in Muse
Code, then refresh here.

Synthetic tests now cover discovery through provider filtering and the actual
Enter/Numpad Enter handler for a populated row. Both pass. Five empty-state
regressions failed before the fix and pass afterward. The focused UI/controller
run passed 37 tests, the broader affected Enter/view/empty/probe/layout run passed
107, and documentation/Orca-tooling/command checks passed 32. These are focused
runs for this follow-up; the full-suite result above predates it. The
main-muse-empty probe surface and empty-list Enter Orca step are included;
live screen-reader and VM visual validation of that state remain pending.

## Discovery clarification and native populated-history test (2026-11-14)

The user clarified that "Muse Code chats, 0 of 77" describes an empty list and
then confirmed they had only signed in to Muse, without starting a conversation.
Signing in does not create a saved conversation. No named-row activation defect
was established. The temporary selection-failure diagnostic and its tests were
removed; the empty-view guidance and actual Enter-path regression tests remain.

A new native test used installed Muse 1.4.18 with a disposable profile and the
local echo provider. HOME, USERPROFILE, XDG directories and the authentication
file path were isolated. Before creating the fixture, initialize.museHome was
checked to resolve inside that temporary profile. All child processes were owned
and closed, and the temporary directory was removed afterward.

Results:

- Initial native session/list: zero conversations.
- Native `muse exec --provider echo --preset empty`: exit code zero.
- A new read-only MuseProvider connection discovered one saved conversation.
- Its reader returned two messages: user and assistant, including the synthetic
  test text, with zero unreadable items.

This verifies native persistence, discovery and parsing without the user's Meta
account or history. It does not establish interactive screen-reader behavior or
real Meta-provider execution. Creating/sending Muse chats from The Chat Place
remains a later milestone.

After removing the temporary selection diagnostic and updating the guides, the
focused discovery/reader/connection/controller/UI/documentation run passed all
87 tests. The native fixture above was a separate runtime check.
