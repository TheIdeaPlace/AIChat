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
Empty plans clear the list. A later turn without a task update preserves the last
list and its announcement baseline. Completed, failed and interrupted turns preserve their
last reported states, and late events cannot rewrite them.

Live snapshots follow the bounded presentation-event path, so bursts cannot crowd
out approvals or completion. Each owned runtime saves changed snapshots atomically
under the app data folder, `provider-tasks/codex/<hash of native ID>.json`, including
provider, native ID and turn ID. Disk errors do not cancel inference. History readers
attach saved lists only to owned chats whose read-only history contains that turn;
no plan prose is interpreted. The latest captured snapshot is retained, not a full
history of every plan update. The Claude-only task-list setting is unchanged.


## Visual verification

The effective final set is 16 screenshots, each opened: Claude Tasks, Codex Tasks,
external Codex without a task list, owned Codex without tasks, Codex session
Properties, plain/formatted shortcuts and the user guide, in light-100 and
hc-aquatic-100. The review-fix run `20261018-214806` refreshed twelve pictures:
two changed external-empty descriptions, eight unchanged pictures and two new
owned-empty pictures. Baselines and diffs for changed pictures were opened too.
The four earlier shortcut pictures remain applicable. No capture failed and no
layout flags were reported. The initial blank WebView capture from the original
implementation was rerun and was not accepted.

Per-picture verdicts are in `codex-live-tasks-visual-review.json`: twelve PASS and
four FAIL for the existing native-list focus contrast issue #181, with no new
defects. Measured colours: black/white text 21:1; white/#0078D4 selected text 4.53:1;
High Contrast white/#202020 text 16.29:1 and #202074/#8EE3F0 selected text 9.47:1.
The existing light-mode #FF872B focus dots against #0078D4 are 1.89:1. Keyboard
focus transitions and actual speech remain Kelly's checks in `testing/codex-tasks.md`.


## Test verification

The full suite ran in the vmtest VM with Python 3.12, the project's development
requirements, the Visual C++ runtime and Git available: **2,411 passed, 7 skipped**
in 183.44 seconds after the PR review fixes. The windowless storage, event,
provider, Properties and documentation checks also passed on the host: 172 tests,
including the Windows sharing-violation reproduction. This includes `test_ui_hidden.py`, `test_layout.py` and
`test_ui_probe.py`; no window-building tests ran on Kelly's PC. The first full run
had one existing fake-GitHub-clone test failure because the clean VM lacked Git;
installing Git and rerunning the suite resolved it. The provider-neutral refactor
was committed separately and its 78 focused checks passed before Codex work began.

## PR #323 review resolutions

All ten points in the review comment have regression coverage:

1. Callbacks, chat reloads and Properties retain the previous list until a turn
   actually supplies a plan; an explicit empty plan still clears it.
2. The real Codex history converter gives every message, tool output and error
   its native turn's one-based number. Lists scope is tested using that output,
   including a restored list from the previous turn.
3. Idle Turn Status only includes unfinished tasks belonging to the latest turn.
4. Task announcements compare across turn boundaries; only the initial load is
   silent.
5. Task completion is announced before the final reply, with duplicate suppression.
6. Owned chats use the shared empty Tasks text; only external chats explain
   unavailable task lists.
7. Properties treats NEEDS_YOU as active for both Claude and Codex; the test is
   parameterized over both providers.
8. Saved-task reads/writes share a process lock, and sharing violations get brief
   bounded retries. A Windows test holds the real destination open until the
   first replace fails, then confirms that retry saves the completed snapshot.
   Concurrent reader/writer stress and transient-failure tests cover the same path.
9. The README lists the hashed saved-task file. Removing an owned Codex record
   removes its saved tasks; failed record saves retain both, and removal can retry
   cleanup. This adds no permanent-delete UI for Codex.
10. Unknown string statuses become pending; malformed non-string statuses still
    reject the update.

The guide and release notes now describe the shared task-list feature together
and state the historical/external limitation directly. The Claude-only Settings
control remains explicit. Keyboard and speech checks are in `testing/codex-tasks.md`.
