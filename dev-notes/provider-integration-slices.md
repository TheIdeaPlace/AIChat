# Provider integration review slices (#184, #171, #225)

The release requires owned Codex conversations and file-editing permission
choices. Read-only history alone does not satisfy the release. Continue Here
is optional; Steer is planned for later. Kelly tests Windows and Mac before
release, and chooses when to merge and release. New Codex chats default to
workspace-write with on-request approvals; existing chats retain saved grants.

1. Foundations: provider contracts, Claude discovery/history routing, worker
   callbacks and native GUI test infrastructure. No visible behavior change.
2. Storage: partition owned sessions, groups, hidden rows and titles into
   legacy Claude files and provider companion files. Frozen 0.1.6 round trips
   and preservation on write/migration failure are the review gates.
3. Reading: off-by-default local Codex discovery, managed app-server and
   history, filters/cache and associated guide entries.
4. Owned conversations: creation, send/queue/Stop, approvals/questions,
   model/effort, announcements, Turn status and sandbox/approval choices at
   creation and through Change Permission Mode. ChatGPT billing guards apply
   before every turn. Existing saved grants must never silently broaden.
5. Neutral defaults: Provider and Kind columns, saved-column migration and
   neutral shared labels, with its own user-visible release entry.

Foundations (#244) and storage (#245) are merged. Slice 3 starts from main
815c864 and retains its native-identity/store-preservation fixes. Its history
provider cannot start/resume a chat or send inference; owned execution remains
a required later slice, not an optional release feature.

Each branch is based on the preceding slice, passes independently and receives
review and Windows/Mac CI. #225 stays a draft integration reference until the
stack lands. Slice 1 is based on remote main 067a76d and preserves its shipping
fixes. Its primary risks are Claude ownership/state parity, stale reader
callbacks, foreign IDs reaching Claude paths and worker/UI thread boundaries.

## Reading review follow-up

Claude's #254 review identified serial foreign RPCs in discovery and optional
Last message filling. Each foreign provider now has one background worker and a
short bounded wait; slow work merges its last private snapshot. Pending work is
separate from failure, retaining working-to-idle comparisons without announcing
cached completions. Actual outages still reset comparison state. The shared
refresh never reads foreign reply history synchronously. Open-chat readers
continue to announce new messages independently. Claude discovery exceptions
reach the original snapshot-failed path, preserving rows and desktop groups.

All Codex history modes use metadata first, then bounded turns/items pages.
Unchanged metadata reuses complete history for 30 seconds. A failed page read
backs off until metadata changes; failed optional-column reads likewise wait
for new activity. Partial histories never replace complete ones. A local native
chat with 3,248 messages loaded through these page APIs and reused its unchanged
history on refresh. No turn was started and no conversation content was logged.

The guide now places settings, filters, columns and companion storage with their
respective features. Rename and read-only notices name the actual provider.
Provider filters and the optional Provider chooser deliberately remain available
with the preview off; slice 5 applies Kelly's regular Provider default/migration.
Independent review found no remaining blocker after two pending-state corrections.
Full native Mac suite: 1,674 passed, 74 skipped; final focused regression pass:
160 passed, one skipped. Windows screenshots/speech remain tracked by #226.

## Provider defaults

Slice 5 enables Provider as a regular default column for everyone, independent
of the Codex setting. Kind remains on. Older saved column choices insert Provider
once immediately after Title (or first without Title) without dropping or reordering other choices; the upgrade flag is persisted
with normal settings saves so removing Provider afterwards remains effective.
The guide, layout help and session-list regressions describe the same behavior.
