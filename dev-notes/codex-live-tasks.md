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


## Visual verification

The effective final set is 14 screenshots: Tasks, Codex Tasks, Codex without a saved
list, Codex session Properties, plain/formatted shortcuts and the user guide, each
in light-100 and hc-aquatic-100. All screenshots were opened. The main comparison
reported two changed plain-shortcut text descriptions, six unchanged surfaces and
six new ones; the final Properties-only run replaced its initial unscrolled preview.
Both final runs reported zero layout flags and no failed captures. New and changed
baselines are committed for Claude's review. The initial blank WebView capture was
rerun and was not accepted.

Per-picture verdicts are in `codex-live-tasks-visual-review.json`: eleven PASS and
three FAIL for the existing native-list focus contrast issue #181, with no new
defects. Measured colours: black/white text 21:1; white/#0078D4 selected text 4.53:1;
High Contrast white/#202020 text 16.29:1 and #202074/#8EE3F0 selected text 9.47:1.
The existing light-mode #FF872B focus dots against #0078D4 are 1.89:1. Keyboard
focus transitions and actual speech remain Kelly's checks in `testing/codex-tasks.md`.


## Test verification

The full suite ran in the vmtest VM with Python 3.12, the project's development
requirements, the Visual C++ runtime and Git available: **2,393 passed, 7 skipped**
in 176.67 seconds. This includes `test_ui_hidden.py`, `test_layout.py` and
`test_ui_probe.py`; no window-building tests ran on Kelly's PC. The first full run
had one existing fake-GitHub-clone test failure because the clean VM lacked Git;
installing Git and rerunning the suite resolved it. The provider-neutral refactor
was committed separately and its 78 focused checks passed before Codex work began.
