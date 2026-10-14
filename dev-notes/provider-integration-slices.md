# Provider integration review slices (#184, #171, #225)

The release requires owned Codex conversations and file-editing permission
choices. Read-only history alone does not satisfy the release. Continue Here
is optional; Steer is planned for later. Kelly tests Windows and Mac before
release, and chooses when to merge and release.

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

Each branch is based on the preceding slice, passes independently and receives
review and Windows/Mac CI. #225 stays a draft integration reference until the
stack lands. Slice 1 is based on remote main 067a76d and preserves its shipping
fixes. Its primary risks are Claude ownership/state parity, stale reader
callbacks, foreign IDs reaching Claude paths and worker/UI thread boundaries.
