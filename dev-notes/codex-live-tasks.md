# Codex live task lists (#318)

On 2026-10-18 the installed `codex-cli 0.162.1` was queried using the app's
`ReadOnlyCodexConnection`: `thread/list` (limit 10), then `thread/turns/list`
(limit 10, itemsView full) and `thread/items/list` (limit 100) for each returned
thread. Five threads and eleven turns were sampled. Item types were userMessage
(10), agentMessage (19), commandExecution (67), webSearch (7), reasoning (39), and
fileChange (12). No structured plan arrays were returned. Only aggregate counts
were printed; no conversation content is recorded here or copied into tests.
No session was resumed, no inference requested, and no rollout files read.

This sample does not establish that every historical plan is absent. The official
[app-server documentation](https://learn.chatgpt.com/docs/app-server), checked the
same day, documents structured state through `turn/plan/updated`, whereas the
`plan` history item is proposed plan text. We therefore support captured live
snapshots and make no promise of recovering checklists from older or foreign chats.

`TaskStep` and its statuses live in `tasklist.py`; Claude parsing remains in
`transcript.py`. Codex validates optional events in its adapter, ignores malformed
snapshots, and applies the entire latest plan only to the matching verified owned
thread and active turn. Step text plus occurrence identifies steps across reorders;
a renamed step is a new identity because Codex supplies no native step IDs.
Empty plans clear the list. Completed, failed and interrupted turns preserve their
last reported states, and late events cannot rewrite them.

Live snapshots follow the bounded presentation-event path, so bursts cannot crowd
out approvals or completion. Each owned runtime saves changed snapshots atomically
under the app data folder, `provider-tasks/codex/<hash of native ID>.json`, including
provider, native ID and turn ID. Disk errors do not cancel inference. History readers
attach saved lists only to owned chats whose read-only history contains that turn;
no plan prose is interpreted. The latest captured snapshot is retained, not a full
history of every plan update. The Claude-only task-list setting is unchanged.
