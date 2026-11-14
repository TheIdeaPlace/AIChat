# Muse Code: owned execution (chat creation, turns, permissions)

Third milestone; no issue number assigned. Builds on onboarding
([muse-code-onboarding.md](muse-code-onboarding.md)) and read-only history
([muse-code-history.md](muse-code-history.md)). Read-only history stays as-is:
externally created chats remain read-only, and `session-resume` is never a
history-recovery path.

## Active implementation plan (revised 2026-11-14)

The next milestone is **reliable execution of one app-created Muse chat**:
create, send, stream, answer requests, stop and reopen after restarting.
Existing terminal conversations remain read-only. This plan supersedes the
original approach and progress claims retained below as historical context.

### Status by stage (2026-11-14, end of day)

Committed on the `Muse_Code` branch (`52843ed`, `fdc5eb8`, `058382e`).

- **Stage 1, contract: done except nested shapes.** The installed 1.4.20
  schema was exported (`msp.schema.json`, experimental, schemaVersion 1).
  Every method in `ExecutionConnection.METHODS` and both server requests
  (`approval/request`, `userInput/request`) exist there, case-sensitively,
  and each request `muse.py` sends carries every required top-level field
  and no unknown one. Native runs (tables below) recorded create, send,
  completion, approval, deny, question, Stop and reopen. Nested shapes
  (`input` items, `answers`, `requirementId`) are confirmed only by those
  runs being accepted, not by a schema comparison.
- **Stage 2, transport and state: done.** `ManagedMuseExecution` uses the
  execution allowlist; `ExecutionConnection` queues events and accepts only
  the two server requests. `MuseOwnedChats` reconciles from `session/read`
  (idle/running/waiting/cancelling/unknown); silence is not completion, and
  the window's monitor outlives switching chats.
- **Stage 3, create/send/stream: done natively.** One app-created chat was
  created, sent, completed and sent again through `MuseOwnedChats`. The
  window's own path (`provider-sessions-muse.json`) is exercised by tests
  with the real `OwnSessionStore`; in the real app a completed turn first
  reported "execution unavailable" because the store refused the saved
  `terminal` field (fixed in `058382e`).
- **Stage 4, permissions and questions: done, except an allowed command
  running.** `prepare()` refuses to send unless the session reports
  `promptUnmatched` with a human reviewer attached on `:ask-me`. Decisions
  act on a choice's `decision`, never its id: allow is the once-only
  `approved` choice; deny prefers once-only `denied`, else `abort` (what
  1.4.20 offers as Reject; it ends the turn). Saved-rule and session choices
  are never sent. Questions submit `freeText`/`selectedLabel(s)` checked
  against the question's options. Admissions are out of scope: they are
  `peerMessaging` (another chat asking to message this one), which the app
  never requests. An allowed command can't run yet because of the `serve`
  sandbox problem (meta-models/muse-code-sdk#149).
- **Stage 5, Stop and recovery: partly.** A UUIDv7 `commandId` is saved
  before `turn/start`; a transport failure marks the turn unknown, not
  idle; a failed Stop keeps the turn. Stop waits for `lastTurn.terminal`,
  which is saved, so the window says "Muse Code stopped." rather than
  "finished". A save failure says so (`MuseSaveError`) instead of blaming
  Muse. Reopen after a host restart works natively. Not yet run natively:
  a host dying mid-turn or mid-approval, and a lost acknowledgement.
- **Stage 6, validation: automated only.** Full suite with wx: 6908
  passed, 61 skipped. Not yet done: VM light-100 and High Contrast pictures
  for the Muse dialogs, a real keyboard walk, and NVDA/JAWS passes on the
  execution flow.

### Native run, installed 1.4.20 (2026-11-14)

Driven through `MuseOwnedChats` + `ManagedMuseExecution` from a script
outside the repo, with a throwaway workspace under the temp folder, the
signed-in profile and synthetic prompts only.

| Step | Result |
| --- | --- |
| `permissionProfile/list` | `:ask-me` available, `reviewerAuthority: human` |
| `session/start` (`promptUnmatched`, `:ask-me`) | idle; `permissionState` axes: filesystem sandboxed, network off, approval onRequest, reviewer human, `reviewerAttached: true` |
| `turn/start` | `accepted`, `disposition: started`, `startedNewTurn: true`; `turnId` equals the `commandId` |
| Polling | `running` until `lastTurn.terminal: completed`; reply read by `transcript_from_muse` |
| `admission/listPending` | **-32010 capabilityRequired**: admission/* is `peerMessaging` (cross-session messages; served only on Unix hosts). Was making every poll "unknown". Removed: never listed, decided or accepted as a server request. |
| Write outside workspace | No approval: `write_file` refuses ("absolute path is outside the workspace"). A sandbox boundary, not an approvable action. |
| Network shell command | `approval/listPending` returned a `shell` approval with choices Allow once (`approved`/once), Always allow in this workspace (`approvedPolicyAmendment`/localPersistent), Reject (`abort`/once). |
| Deny | Was impossible: the generic deny looked only for `denied`. Now prefers `denied`, else `abort`. Accepted; tool call `rejected`, "approval aborted"; nothing ran. |
| Allow once | Accepted, but Muse refused to start the command: "sandbox enforcement unavailable: windows_elevated setup_required". It looked at first as if the sandbox setup hadn't run; the next row shows setup doesn't fix it. |
| After `muse sandbox windows setup` (elevated; `status=ready`, every part true) | Same error. Also the same with the workspace outside `C:\Users`, with the host outside the app's job object, with plain `Popen` flags, and with `serve --sandbox-network restricted`. Under `serve`, even `echo` asks for approval and then fails; `muse exec --permission-profile :ask-me` runs the same `echo` in the sandbox without asking. **Looks like a Muse 1.4.20 `serve` problem**, not the app's launch; reported as [meta-models/muse-code-sdk#149](https://github.com/meta-models/muse-code-sdk/issues/149). Not worked around: `--disable-sandbox` also lifts file-tool workspace confinement. |
| Closing the host | Ends a running turn; the session reads `notLoaded` afterwards (matters for Stage 5). |

Also seen: each turn adds "Reminder child session" event items, which the
conversation view shows as event lines. Not yet decided whether to hide them.

Run C (same day, workspaces under `C:\dev`, deleted afterwards):

| Step | Result |
| --- | --- |
| Question | `request_user_input` arrived in `approval/listPending` `userInputs` (single-choice Red/Blue); `userInput/answer` with `selectedLabel: Blue` accepted; the model replied "You chose Blue."; turn `completed`. |
| Stop | `turn/cancel` accepted; state held `cancelling` until `lastTurn.terminal: cancelled`, then idle. The window said "finished" for this; it now saves the terminal and says "Muse Code stopped." (or "turn failed" for `failed`). |
| Reopen | After the first host closed, a fresh host's `prepare()` read `notLoaded`, called `session/resume` with `:ask-me`, re-confirmed human review, and a second turn completed in the same chat. |

Still to verify natively: an allowed command that actually runs (blocked by
the `serve` sandbox problem above), and Stop/recovery across a host that dies
mid-turn or mid-approval.

### Blockers found at the 2026-11-14 review, and how each was resolved

- The execution host inherited the history-only request gate. Resolved:
  `ManagedMuseExecution.METHODS` is the execution allowlist, with a
  regression through the managed connection.
- Wrong method names and fields (`prompt`, `idempotencyKey`). Resolved:
  `input`, `commandId` and the schema's case-sensitive names.
- Three quiet polls counted as completion; switching chats stopped
  monitoring. Resolved: completion comes from `lastTurn.terminal`, and the
  monitor is per chat, not per open view.
- Questions were Allow/Deny and sent their own text. Resolved: the dialog
  collects real answers. Admissions were dropped rather than routed (see
  Stage 4 status).
- Failed sends and cancellations cleared running state. Resolved: they stay
  unknown or running until Muse's state says otherwise.
- `RUNTIME_PROVIDERS` included Muse ahead of an audit. Kept: Muse is a
  runtime provider, and `MuseProvider.capabilities()` still allows send and
  approvals only for chats started here. Guide wording now follows the
  verified behaviour, except the sandbox limitation, which no guide states.

### Stage 1: Verify the installed execution contract

Record the installed build and fingerprint pair; export its exact schema.
Compare case-sensitive method names, required fields, responses, events and
errors with the official references. A compatible handshake does not prove
that the execution implementation is correct.

Verify only the operations needed to create and reopen an owned chat, submit
and cancel a turn, observe history and terminal state, handle permissions
and questions, and establish effective native permission policy. (Admissions
were first in this list; they turned out to be `peerMessaging`, out of scope.)
Use synthetic content in a disposable profile/workspace, checking the host's
resolved profile before creating fixtures. Do not alter global configuration
or copy real conversation content into tests. Begin with the native echo
provider where supported, followed by a bounded tool-permission fixture.

Acceptance: record a contract table and synthetic fixtures for installed
request/response/event shapes, rejection and completion. Demonstrate permission
enforcement before enabling tool-capable sends. Determine the lifecycle needed
to reopen owned chats; foreign-session resume remains refused. Record any
authentication or trust blocker rather than assuming a compatible operation.

### Stage 2: Correct transport and provider-owned execution state

Fix the managed execution request gate while preserving separate account,
history and execution allowlists. Allow only operations verified in stage 1.
Distinguish normal protocol rejection from transport failure. Add bounded
event handling and provider-owned state for idle, submitting, running,
waiting, cancelling, completed and disconnected/unknown.

Native events or verified state reads drive transitions; silence is not
completion. Monitoring lives for the entire owned turn, independently of
the selected conversation. UI callbacks use `wx.CallAfter`; presentation
generation checks still reject stale updates to the open view.

Acceptance: a regression exercises the first execution request through
`ManagedMuseExecution`, and every connection rejects forbidden methods.
Cover duplicate/out-of-order events, malformed frames, request timeout and
host exit. Switching chats preserves monitoring; shutdown terminates hosts
and prevents late UI updates.

### Stage 3: Complete create, send and streaming

Persist provider-qualified owned identity before sending the first message.
Preserve the draft on creation, storage or send failure. Drive Send and Stop
from confirmed state, preserve reading position/focus during incremental
updates, and announce completion once. Audit the current runtime-provider
integration for assumptions specific to Codex or Copilot.

Acceptance: one synthetic owned chat creates, sends, streams, completes and
sends again through the integration used by the window. A slow or quiet turn
remains running; switching chats does not abandon it. Foreign chats cannot
send, stop, answer or reach another provider's runner. Missing Muse leaves
other providers usable.

### Stage 4: Complete permissions and questions

Establish the verified native policy before tool-capable sends; if it cannot
be established, explain the limitation and keep sending unavailable. Do not
claim every tool needs individual approval unless the effective policy
demonstrates that behavior.

Keep approvals and questions distinct (admissions: see Stage 4 status;
the app never requests `peerMessaging`, so it never lists them). Provide the native
decision choices for permissions and labeled text/choice controls for actual
question answers. Submit only the person's response. Escape/Not Yet leaves
a request pending; explicit denial or question cancellation uses the verified
native operation. Parsing errors, timeout and host death never grant consent.

Pending requests belong to provider state. Announce each once with its chat
title, without stealing focus. Begin dialog review on the readable request,
followed by details/answer controls and decision buttons; restore focus on exit.

Acceptance: native allow/deny fixtures demonstrate permitted and blocked
effects, and question fixtures receive the selected/entered answer. Cover
multiple requests, pagination if supported, stale/duplicate requests, another
chat's ID, malformed payloads and host death. Background-chat requests remain
discoverable and answerable. Lists Approvals records confirmed decisions;
uncertain decisions are not reported as successes.

### Stage 5: Verify Stop, disconnect and restart recovery

Persist command identity before submission, using the verified native format.
Retain enough state to reconcile an uncertain outcome after disconnect or
restart. Retry the same logical command only as the protocol permits; do not
automatically resend with a fresh identity. Stop remains cancelling/unknown
until native confirmation; a failed Stop must not mark the chat idle.
Reopen owned chats through the verified lifecycle and reconcile pending
requests and running state before another send.

Acceptance: cover disconnect before acknowledgement, lost acknowledgement
after acceptance, host death mid-turn/approval, failed cancellation, duplicate
replay and restart. Each logical message starts at most one turn. Uncertain
state has persistent readable feedback and a verified recovery path. Old
content is not announced as a new reply.

### Stage 6: Complete validation and documentation

Run focused failing-first regression checks per stage and the full suite
after integration. Synthetic hosts validate the verified protocol rather
than repeat implementation assumptions. Perform native create/send/stream,
permission allow/deny, question, Stop and restart checks with synthetic content
and confirmed process cleanup.

Bring README, bundled/generated guides, open release notes and the execution
testing guide into agreement with supported behavior. Remove contradictory
availability claims. Update probe surfaces and Orca steps for changed controls.

Acceptance: focused and full tests pass with skips/limitations recorded.
Review Windows VM light-100 and one High Contrast run, compare pictures,
measure contrast, and accept baselines only after review. Update descriptions
from actual pictures. Walk real Tab, Shift+Tab, arrows, Enter and Escape,
checking every stop and focus return. Validate with NVDA 2026.3.1
(2026.3.1.58288) and JAWS 2027.2610.428 (Script Revision: 0).
Record Mac runtime and Linux Orca CI separately. Prior onboarding passes do
not establish an execution pass; no screenshot means no visual-pass claim.

### Completion and deferred work

Complete means an app-created chat can create, send, stream, answer, stop
and reopen against the installed build, including native permission
enforcement, recovery without duplicates and accessibility validation.
Terminal chats remain readable throughout. Defer steering, attachments,
branching, deletion, model switching, subagents, background tasks, computer
use and plugin/MCP management until this path works.

Primary files: `providers/muse_connection.py` (transport/gates/events),
`providers/muse.py` (lifecycle and permissions), `own_store.py` and provider
storage (durable identity/recovery), `session_controller.py` and `sessions.py`
(routing/capabilities), UI execution mixins/dialogs/permission hosts, and the
existing Muse, store and hidden-window tests.

Protocol references, to compare against the installed schema:

- [Session creation](https://meta-models.github.io/muse-code-sdk/next/generated/msp/methods/session-start/).
- [Turn submission](https://meta-models.github.io/muse-code-sdk/next/generated/msp/methods/turn-start/).
- [Pending requests](https://meta-models.github.io/muse-code-sdk/next/generated/msp/methods/approval-listpending/).
- [Question answers](https://meta-models.github.io/muse-code-sdk/next/generated/msp/methods/userinput-answer/).

## Original plan and historical progress (superseded)

The material below records the earlier design and draft implementation.
Its completion claims and original decisions do not override the active plan.
In particular, admissions were later removed, the approval dialog shows
Muse's own once-only choices rather than Allow Once/Deny, and quiet polls no
longer settle a turn.

### Original goal

Start Muse chats from inside The Chat Place, stream their replies into the
existing conversation view, and enforce Muse-native permissions before anything
runs — with the same keyboard/screen-reader bar as the Codex execution flow.

## Success criteria

- A new Muse Code chat can be created from the app, streams replies,
  and can be stopped mid-turn.
- Every tool run, shell command, and agent question in an owned chat goes
  through a native Muse permission/approval decision first; nothing executes
  on a default-allow.
- Foreign (externally created) chats stay read-only: no send, stop, fork, or
  permission path can reach them.
- Recovery (restart, outage, host death) never double-submits a turn and
  never announces a stale reply as new.
- NVDA/JAWS pass on the new send/permission/stop flow; light/High Contrast
  VM pictures reviewed; Linux Orca CI recorded separately.

## Approach and key decisions

- **Verify the execution interface first, as history did.** Candidate MSP
  methods (from the official method index): `session-start`, `turn-start`,
  `turn-cancel`, `turn-interrupt`, `turn-steer`, `turn-unqueue`,
  `approval-decide`, `approval-listpending`, `admission-decide`,
  `admission-listpending`, `permissionprofile-list`,
  `session-setpermissionprofile`, `view-subscribe`, `view-page`,
  `userinput-answer`, `model-list`, `usage-read`. Confirm each one's exact
  request/response shape against the installed Muse build with synthetic
  fixtures before wiring it into the app. `session-resume` stays off-limits
  for foreign chats; decide explicitly whether owned chats need it at all.
- **Separate execution connection from history.** Follow the history
  precedent: the account connection stays sign-in-only, the history
  connection stays `session/list` + `session/read` only. Execution gets its
  own connection with its own verified method allowlist (turn + approval +
  view-subscription methods only), its own lifecycle and shutdown. No
  settings-write, plugin, hook, or credential method enters any allowlist.
- **Mirror the Codex execution shape.** `MuseProvider` gains
  `create_owned` / `send_owned` / `stop_owned` alongside `open_reader`,
  `capabilities()` returns `send=True` (plus `stop`, `approvals`) only for
  owned sessions, and runtime updates return through `wx.CallAfter` like the
  Codex `on_update` path. Reuse `SessionInfo` ownership marking and the
  existing send/stop/permission controls; do not add Muse to
  `RUNTIME_PROVIDERS` as a shortcut.
- **Permissions default-deny.** Model on `codex_permissions.py` /
  `codex_requests.py`: every approval/admission/user-input request surfaces
  in the app's permission UI and blocks the turn until decided. A denied or
  unanswered request cancels safely, never silently proceeds. Covered by
  synthetic tests including timeout and host-death mid-approval.
- **Idempotent turns.** Follow the cookbook's retry guidance
  (`retry-without-double-submitting`): client-generated idempotency per turn,
  `turn-unqueue` on recovery paths, and no resend after an ambiguous
  disconnect without reconciling state first.

## Work plan

1. Verify execution methods natively (synthetic fixtures, disposable
   profile): `session-start`, `turn-start`, view subscription/page streaming,
   `approval-decide` + `userinput-answer`, `turn-cancel`/`turn-interrupt`,
   permission-profile listing/selection. Record evidence; block on any
   method whose side effects can't be bounded.
2. Execution connection: new allowlisted connection beside history/account,
   with frame, timeout, and shutdown bounds copied from the history
   connection's proven shape.
3. Owned lifecycle: create/send/stop/steer, owned identity records, and
   capability gating (foreign chats unchanged and still read-only).
4. Permissions UI: approval/admission/question surfacing, default-deny,
   timeout and host-death behavior, announcement text.
5. Streaming into the conversation view: incremental transcript updates that
   preserve focus, selection, and the no-false-new-reply rule from history.
6. Failure/recovery: outage mid-turn, restart with pending turns (no double
   submit), stale-row handling consistent with discovery.
7. Docs and guides: README + generated guides, open release notes entry,
   manual execution testing guide under `testing/`, probe surfaces for new
   dialogs/states.

Each slice lands with focused failing-first tests; the full suite runs once
at the end.

## Validation plan

- Focused synthetic tests per slice (pagination of approvals, malformed
  approval payloads, duplicate/regressing revisions, shutdown during turn,
  reconnect, refused writes to foreign chats).
- Full suite (`python -m pytest -q tests`) green at the end.
- Native smoke against installed Muse (owned create/stream/stop + one
  approval round-trip, synthetic content only) with clean host shutdown.
- Manual: NVDA/JAWS pass on send/permission/stop; VM light + High Contrast
  pictures reviewed; Mac runtime and Linux Orca CI recorded separately.
  No screenshot means no visual-pass claim.

## Progress (2026-11-14)

Provider slice landed and tested: `ExecutionConnection` (owned-only
allowlist), `MuseOwnedChats` (create/send/stop/settle, pending approvals,
answer), capability gating (foreign chats stay read-only), and
`tests/test_muse_execution.py` (synthetic host only).

Window slice landed, unverified live: owned `muse` records persist in
`provider-sessions-muse.json` (older releases never see them);
`ManagedMuseExecution` registers lazily with the window;
`RUNTIME_PROVIDERS` includes `muse` (every runtime-gated site audited:
codex-state lookups are None-safe, usage/model/settings paths stay
capability-gated); New Session offers Muse Code with its own model/effort
note; owned chats send, stop, and answer through Allow Once/Deny/Not Yet
(`MuseApprovalDialog`) with a background poller that announces each new
request once and settles quiet turns; `MusePermissions` backs Chat
Permissions; probe surfaces `muse-new`, `muse-approval`, and
`chat-permissions-muse` exist. Release-notes entry, README and asset guide
updates (generated docs rewritten), and `testing/muse-code-execution.md`
are in.

Still open: native verification of the assumed `session/start`,
`turn/start`, `approval/listpending`, `approval/decide`, and
`userinput/answer` shapes against the installed build (plan step 1);
live NVDA/JAWS passes; VM light/High Contrast pictures and
`descriptions.md` entries written from them; full-suite and Orca CI runs.

Follow-up (2026-11-14, path 2): Lists Approvals now lists Muse requests
answered here (`approvals.parse` reads `muse:<id>` rows as "You decided";
allowed ones are routine, denied ones always shown). Orca steps added for
the three new dialogs (`muse-new`, `muse-approval`, `muse-permissions`
triggers; expectations unconfirmed until the VM run). Sandbox checks:
188 passed, 2 skipped across approvals, all Muse modules, provider store,
docs, and Orca tools. The full wx suite, VM visuals, and native execution
still need Kelly's machine.

## Risks and non-goals

- If Muse's execution methods require workspace trust or sign-in flows the
  app can't broker safely, that blocks owned creation — record it rather
  than working around it.
- Non-goals: Rewind/Branch, Ask Aside, attachments, model switching
  mid-session, subagents, background tasks, computer use, MCP servers,
  skills/plugins. Fork/stop/delete of foreign chats stays refused.
- Fingerprint risk stands: a Muse update can invalidate `VERIFIED_SCHEMAS`
  again; the plan's first step re-verifies against the installed build.
