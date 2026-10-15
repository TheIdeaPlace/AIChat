# Codex in The Chat Place: project review and implementation plan

Issues: #184 (additional providers) and #171 (shared session architecture).

Reviewed October 11, 2026 against `origin/main` at
`08473e38a6860d73447a4a0a3b3fcbe75a8f2d73`.
Requirements: [issue #184](https://github.com/TheIdeaPlace/AIChat/issues/184).
Related architecture work: [issue #171](https://github.com/TheIdeaPlace/AIChat/issues/171).

## Recommended outcome

Keep one session list containing Claude and Codex chats. Selecting a chat
automatically selects its backend for reading, replying, stopping and answering
requests. New Session adds a provider choice, remembered for the next new chat.
Provider filters are optional conveniences, not separate windows.

Implementation stays on the separate feature branch. Keep changes small and
independently reviewable; leave the shipping branch unchanged.

## What the current code tells us

| Area | Finding | Required change |
| --- | --- | --- |
| `hub.py` | Already merges Claude desktop, terminal and owned sessions and sorts one snapshot. | Retain merging and sorting; gather through a provider registry. Isolate backend failures. |
| `sessions.py` | `SessionInfo` mixes presentation with Claude transcript paths and desktop links. | Separate common session metadata from backend operations and capabilities. |
| `own_store.py` | Stores are keyed by raw CLI ID, validate Claude options, and assume Claude ownership. | Versioned provider-aware persistence and validation. |
| `transcript.py` | Useful message and transcript models, but assistant labels and parsers assume Claude. | Common message types with provider-aware assistant labels; separate parsers. |
| `ui/main_frame.py` | 5,303 lines; directly imports `TurnRunner`, response builders and provider-specific features. Runners, drafts and other state use raw session IDs. | Introduce a controller between the window and providers, including all per-session state. |
| `groups.py`, `hidden.py`, `titles.py` | Persist session keys. | Preserve existing memberships and overrides during identity changes. |
| `claude_cli.py`, `signin.py`, `usage.py`, `changes.py`, `about_you.py` | Claude-specific behavior and safeguards. | Keep as Claude adapter internals; expose only common results to the controller. |

Adding a Codex subprocess directly to the window would leave these assumptions
in place. The provider boundary is the prerequisite for reliable mixed chats.

## Codex integration choice

Use a managed local `codex app-server` process over stdio. Its documented
protocol supports initialization, `thread/list`, `thread/read`, start/resume/fork,
turn streaming, interruption and approval requests. Generate protocol schemas
from the supported CLI version and record that version in fixtures.

Use explicit top-level source filters: listing defaults omit `appServer` and
`exec`. Fetch every page and archived chats separately. Read history without
resuming. Runtime `notLoaded` must not imply idle in another client.

Use Codex-managed ChatGPT sign-in; verify account/provider configuration before
inference and reject API-key or other billing routes. Rate limits are available
through account methods. Do not copy tokens into TCP's settings or logs.

These protocol findings come from the
[official app-server documentation](https://learn.chatgpt.com/docs/app-server).
The exact installed-version behavior still needs a compatibility probe.

## Proposed boundary

Add `providers/base.py`, `providers/claude.py`, `providers/codex.py` and a
session controller. Start with the small contract needed by the list and reader;
extend it for turns rather than extracting every feature at once.

- Identity: `(provider, native_session_id)`; source and ownership are separate.
  The protocol's model provider is not TCP's agent-provider identity.
- Discovery: session summaries, paging, recoverable errors and refresh signals.
- Reading: neutral messages with stable item IDs, assistant names, attachments,
  activity, errors, questions and file changes.
- Execution: start, continue owned chat, fork external chat, send, interrupt,
  and typed events keyed by session, turn, item and request IDs.
- Requests: typed command/file approvals and questions; provider-specific
  choices and conversion of answers stay in the adapter.
- Options: models, effort, sandbox and approval policies with labels and
  backend validation. Never translate Codex settings into Claude mode names.
- Capabilities: read, send, fork, delete, attachments, live state, approvals,
  usage, instructions, desktop links and remote access, per session where needed.

The controller owns routing, queues, drafts, request ordering and lifecycle.
The UI consumes common data and asks the controller to act. Preserve a host
identity slot for #171 without implementing another-machine transport here.

## Ownership and reliability rules

Externally discovered Codex chats start read-only. Continue Here forks into a
TCP-owned chat, paralleling the Claude safeguard. Do not resume an external chat
to display it. Only offer direct sends to chats TCP owns, with one active turn
per chat. Verify external-client concurrency before relaxing that restriction.

Do not infer another app's active work or waiting state from a timestamp alone.
Show an honest unknown/unavailable state where live information cannot be verified.
Local hide/title/group changes stay TCP-owned. Permanent delete must be a
capability with its own backend implementation, never Claude's path deletion.

One unavailable backend must leave the other's list and replies usable. Cache
the last successful discovery result, mark it stale, and report the error once.
Never replay an interrupted or uncertain send automatically after reconnecting.
Reconcile history and turn IDs first to avoid duplicated prompts and messages.

Pending approvals are session-scoped and use the existing Answer workflow; a
background chat must not steal focus. Unknown requests fail closed with a clear
message. Do not offer approval scopes the installed backend cannot implement.
Interrupt a Codex turn without terminating other chats sharing its process.
On shutdown, resolve/cancel requests and stop TCP-owned work deliberately.

## Persistence and migration

Default old records to Claude. Add a versioned provider identity and a separate
provider-options payload; preserve the original data on failed migration.
Back up and migrate atomically. Either retain legacy Claude keys behind a
resolver or migrate every reference together: own sessions, groups, hidden,
titles, unread state and saved per-session preferences. Choose one approach
before coding; never partially rename keys.

Key runner, draft, queue, permission and cache state by the same canonical
identity. Test equal native IDs from different providers. Disabling Codex hides
its rows without deleting its preferences or group memberships.

## Delivery sequence and acceptance gates

1. **Compatibility probe.** On a development profile, check CLI discovery on
   Windows/macOS, protocol version, subscription sign-in, source coverage,
   history fidelity, external live status and fork behavior. Use synthetic
   fixtures and a disposable real chat, never a user's existing chat for writes.
   Document desktop/cloud coverage explicitly; do not promise chats absent from
   the local app-server catalog. No inference is needed for initial discovery.
2. **Claude boundary.** Introduce common types, controller and Claude adapter.
   Preserve existing list wording, commands, ownership guards and execution.
   Gate: existing suite and parity fixtures pass; no visible Claude regression.
3. **Mixed read-only list.** Add opt-in Codex discovery and history conversion,
   provider column/filter, loading/errors and provider-aware announcements.
   Gate: mixed sorting, groups, hidden, archived, Find, export, formatted reading,
   code blocks and last-message column work; one backend failure is isolated.
4. **Writable Codex chats.** Add New Session, fork/Continue Here, owned resume,
   send, stop, queue handling, approvals/questions and model/policy selection.
   Gate: missing auth, denied requests, process crashes, interrupted turns and
   concurrent chats have correct outcomes and accessible feedback.
5. **Extras and release readiness.** Add supported changes/usage/instructions,
   adapt help and product wording, and disable unsupported Claude commands on
   Codex chats. Run platform packaging and hands-on screen-reader checks before
   enabling by default. Keep Claude Remote Control and Cowork provider-specific.

Each step should be its own reviewable PR. Ship no provider refactor together
with an unverified Codex execution path.

## Validation

Use a fake app-server with recorded protocol fixtures for partial lines,
out-of-order responses, interleaved chats, unknown items, paging, errors,
approvals, disconnects and terminal outcomes. Test item deduplication across
live events and history refresh. Redact all private paths and credentials.

Add migration tests with existing groups/titles/hidden data, colliding IDs and
corrupt stores; parity tests for Claude; and UI routing/capability tests for a
mixed list. Real smoke checks must cover subscription billing, resumed context,
fork source preservation and two chats where stopping one leaves the other
running. JAWS, NVDA and VoiceOver checks cover provider names, focus, request
answers, queued prompts and announcements from background chats.

## Decisions to verify during the probe

- Which local desktop, CLI and IDE chats the installed server actually exposes;
  cloud-only chats require a separate supported discovery route if absent.
- What live status can be trusted across app-server processes.
- Supported CLI minimum/version policy, safe executable discovery and packaging.
- Whether every needed approval/question scope and attachment type is supported.
- Long-history performance without experimental pagination dependencies.

Proceed with the unified-list design. These verification items constrain
capabilities and coverage; they do not require a separate provider window.

## Review validation results

No application code changed. `git diff --check` passed. The initial baseline
suite produced 831 passes, 69 skips and 502 setup errors, beginning with
wxPython being unable to access the Mac display. A retry outside the sandbox
also failed and aborted with exit 134 and a pthread diagnostic. A clean full
baseline was initially unverified.

Resolved on October 11: GUI tests run with desktop access outside the sandbox;
all GUI modules now use one session-scoped `wx.App`, and the DPI test respects
macOS logical window coordinates on Retina screens. No application code needed
changing for these fixes. The full suite subsequently completed with 1,337
passes, 69 platform-specific skips and exit code 0, without a native crash.

## Implementation progress

The first foundation change introduces a discovery protocol and shared snapshot
in `providers/base.py`, moves existing Claude discovery to `providers/claude.py`,
and routes `hub.collect` through it. Claude keys, sorting, cache hooks and
ownership metadata remain compatible. `SessionInfo` now carries a provider and
backend-aware identity; foreign sessions cannot resolve Claude transcript paths
or desktop links.

Focused validation: 207 tests passed across provider routing, sessions, terminal
and Cowork discovery, last messages, activity, stores, groups, hidden sessions
and titles. The subsequent full-suite run passed as recorded above.
This is the beginning of phase 2's Claude boundary, not completion of that phase:
reader and turn routing, the controller, persistence migration and Codex support
remain to be implemented. No Codex UI option is enabled yet.

October 12: added a window-independent `SessionController` and conversation
provider/reader contracts. The main window now routes chat loading, unloaded
session export, history for Properties, and background reply lookup through
the controller. Claude's reader and bounded tail parsing live in its adapter.
Unknown providers are rejected rather than falling back to Claude. Existing
window layout, Claude wording and session keys are retained.

Six new tests cover backend routing with colliding IDs, unavailable and duplicate
providers, missing history, read-only Claude history access and rejection of
foreign chats by the Claude adapter. Full-suite validation: 1,343 passed,
69 skipped, exit code 0.
Remaining reader work includes cross-session Find, backend-neutral message
labels and history availability checks; turn routing and Codex execution are
still pending. The installed local CLI reports `0.162.0-alpha.17.2`; its
app-server has stdio and schema-generation commands. Compatibility with a
released CLI remains to be verified before selecting a supported minimum.

## Reviewed checkpoints on October 12

The reader boundary now covers cross-session Find, backend-specific assistant
labels, properties and unloaded exports without requiring Claude files.
Reader construction runs on a worker thread. Generation checks preserve the
current session when an older factory finishes; tests cover both successful
and missing stale readers. Two older GUI tests now wait for the asynchronous
load announcement rather than depending on callback timing.

Independent review found no Claude regression in the foundation or reader
changes. It suggested the stale-reader tests, which were added. This is still
a checkpoint within the Claude-boundary phase: turn routing, capabilities,
provider-aware persistence and last-message cache routing remain outstanding.

A read-only `CodexProvider` now converts protocol session summaries and history
through an injected, initialized RPC connection. It lists all pages and archived
chats, excludes child chats, distinguishes unavailable runtime status from idle,
and reads history without resuming. Synthetic fixtures follow the generated
`0.162.0-alpha.17.2` schema. The adapter is not yet registered in the window.

Independent review identified partial-history acceptance and omitted MCP
outputs. Both were fixed and re-reviewed: partial/paginated history fails with
an explicit error until pagination is supported; tool activity preserves MCP
text/errors and file diffs. The follow-up review reported no blocking findings.

The development-only `tools/probe_codex.py` verified the local stdio handshake,
ChatGPT account type and listing of five active chats, zero archived chats.
It prints aggregate counts only, sends no prompts and reads no message bodies.
The reviewer checked its cleanup/privacy logic but did not repeat the real probe.
Windows executable discovery, cross-client status fidelity, paginated history,
forking and subscription execution still need compatibility checks.

Validation for these checkpoints: 1,361 tests passed, 69 platform-specific tests
skipped, process exited successfully. The independent reviewer also ran the
33 focused adapter/search/controller tests successfully. No new dialogs or
Codex controls are enabled. The Windows visual-probe gate remains part of the
later UI phase; no screenshot comparison was performed for this checkpoint.

The next discovery checkpoint adds a background-only `DiscoveryRegistry` that
merges and sorts provider snapshots, preserves Claude ownership metadata, and
retains private copies of the last successful results during an outage. Results
identify stale providers and first failures so callers can report each outage
once. Raw backend exception text is omitted from UI diagnostics.

Independent review caught malformed metadata being cached before merge/sort
validation. Validation now precedes cache replacement, with regression cases
for every sort order and malformed merge fields. Focused validation: 139 tests
passed across discovery, provider routing and Claude/Codex session adapters.
The follow-up independent review found no remaining blockers and passed all
57 discovery tests. Successful results and fallback results use the same
validated private cache copy, as suggested in that review.
The registry is not wired into the window yet. Before activation, stale results
must be excluded from live completion announcements and execution decisions;
production RPC transport, capabilities and opt-in settings remain outstanding.

## Managed transport checkpoint

Added a managed read-only stdio connection for the background provider workers.
The connection initializes once, serializes writes, matches concurrent responses
by request ID, bounds frames/requests and closes all pending requests on timeout
or protocol failure. Notifications are ignored in this read-only phase;
unexpected server requests close the connection without approving any action.
No start, resume, fork, inference or automatic replay methods are exposed.

Native executable discovery and process lifecycle live in `platform_paths.py`.
The child uses the existing process-tree owner, with no shell and no stderr
payloads in public diagnostics. Close waits for cleanup already running on
another worker. Discovery checks PATH and Mac Homebrew locations; desktop
bundled copies are not automatically selected. Resolving npm installations and
choosing a supported released minimum remain compatibility work.

The development probe now uses this same transport. Its live Mac check passed
initialization, reported ChatGPT account type and found five active chats and
zero archived chats. It printed no conversation bodies, IDs or tokens and
started no turn. This does not establish subscription execution or Windows
compatibility.

Independent review caught a deeply nested JSON frame that could terminate the
reader before cleanup. It now fails closed, with a regression test. Follow-up
review passed all 24 transport/launch tests with no blocking findings. The
broader focused suite passed 142 tests. Production connection ownership and
reconnect integration, subscription guards, capability routing and UI activation
remain next steps. The feature branch also includes the latest upstream sign-in
fix; the shipping branch itself was not modified.

Final full-suite validation for this checkpoint: 1,443 passed, 69 skipped,
process exited successfully. No UI or layout changes are part of this checkpoint.

## Connection ownership checkpoint

Codex providers can now create a managed connection lazily, with no process
startup in provider construction or reader creation. Concurrent discovery and
history requests share one initialized server. Deferred initialization lets the
owner cancel a blocked handshake during shutdown. A later read can reconnect
after a failed process is cleaned up; the failed operation is never replayed.
The session controller closes every registered backend, even if another
backend's cleanup fails, and rejects operations after shutdown begins.

Independent review caught cleanup failures losing ownership in late startup
and in reconnects. Both now retain the connection and report a redacted failure;
replacement startup is prevented until cleanup succeeds. Regression tests
cover those failures, eventual recovery, shutdown during initialization and
concurrent controller shutdown. Follow-up review passed all 113 focused
lifecycle/discovery/provider/controller tests with no remaining blockers.
Window shutdown wiring, capabilities, authentication guards and UI activation
are still pending.

Kelly authorized the best checks available on this Mac and requested a Windows
handoff issue. [Issue #226](https://github.com/TheIdeaPlace/AIChat/issues/226)
records CLI discovery, process cleanup, packaging, visual baseline and keyboard/
screen-reader validation for an agent on Windows. Windows validation remains
outstanding; it does not stop independent backend or Mac validation work.

Final full-suite validation for connection ownership: 1,457 passed, 69 skipped,
process exited successfully. No UI controls or layout changes are enabled by
this checkpoint.

## Stale discovery and recovery checkpoint

Finished-turn detection now excludes stale provider snapshots. The window also
drops stale or unknown Codex states from its comparison baseline, so recovery
cannot announce a chat as finished from a state remembered before the outage.
Codex must explicitly become idle; waiting for approval or unavailable status
does not count as a finished turn. Existing Claude behavior is preserved.

Independent review found no actionable defects and passed all 62 discovery
tests. Broader validation passed 562 hidden-window/session/view tests with six
platform skips; the additional end-to-end refresh/recovery regression passed
separately. No mixed-list option is enabled by this checkpoint.

The Mac visual probe was attempted for main-start, main-own, main-own-working
and main-desktop, but native screen capture failed for all four windows. No
screenshots or visual-parity verdict are available. Issue #226 records the
remaining Windows probe and screenshot review. The prior macOS packaging
failure at disk-image detach passed on retry; both platform test jobs and
Windows packaging passed for the published lifecycle checkpoint.

## Window shutdown checkpoint

Accepted window close now closes registered conversation backends on a dedicated
worker. The worker survives wx exit and cannot be cancelled with queued history
jobs; shutdown never blocks the UI thread. A vetoed close leaves the controller
usable. Cleanup still attempts every backend after a failure.

Independent review found no actionable defects. Validation passed both new
hidden-window cleanup tests and 104 controller, transport and discovery tests.
The published stale-state checkpoint passed both platform test and packaging
jobs. No Codex option or layout change is enabled yet; Windows visual and
hands-on accessibility checks remain tracked in issue #226.

## Billing safety review checkpoint

The managed native Codex process now receives a copied environment with OpenAI,
Azure OpenAI and named Codex credential/endpoint overrides removed. Login lookup
variables remain intact. Parent environment variables are never modified.
The wire boundary checks refreshed `account/read` on the same connection before
any future turn/start or turn/steer request, requiring a ChatGPT account and an
OpenAI-authenticated provider. Production methods remain read-only.

Independent review passed all 47 transport tests with no actionable findings;
the broader provider/controller/transport suite passed 70 tests. Before enabling
execution, validate the target thread's provider and endpoint configuration as
well: account metadata alone does not establish its billing route. This remains
an execution acceptance gate, alongside ownership and subscription smoke checks.

The earlier macOS disk-image failure passed on attempt 2 without build changes;
PR #225 records the retry. Issue #226 explicitly blocks turning Codex on in the
UI until every added/changed actual probe surface has light-100 and High Contrast
captures, baseline comparison and independent review. It separately lists
Kelly's focus, provider-name and screen-reader announcement hand checks.

## Provider-aware ownership storage

Ownership records now have a provider identity and separate provider options.
Equal native IDs from Claude and Codex remain distinct. Legacy store calls see
Claude only, preventing foreign records from reaching existing runners and
ownership guards. Existing Claude `own:<id>` keys are retained; Codex uses the
same `codex:<id>` key whether externally discovered or owned, preserving local
hide/title/group references when ownership changes.

The 0.1.6 compatibility audit supersedes the initial mixed version-2 format.
`sessions.json` remains version 1 and Claude-only, without provider fields.
Codex ownership, unread state and per-session runner settings live in
`provider-sessions.json`. The same separation applies to `provider-groups.json`,
`provider-hidden.json`, `provider-titles.json` and `provider-tool_activity.json`.
Legacy keys and files stay unchanged. Other reserved backend key prefixes also
stay out of the legacy metadata files.

A valid experimental mixed ownership store is separated on load, after an
exact backup, so an older release cannot subsequently see its foreign records.
Foreign data is written before replacing the legacy file. Invalid, conflicting
or unreadable provider files block saving; they are never overwritten as empty.
Provider-only changes do not rewrite unchanged Claude partitions, preserving
newer edits made by an already-running 0.1.6 instance. This does not introduce
cross-process merging for simultaneous edits to the same Claude record.

Tests use copies of the actual origin/main 0.1.6 store implementations, pinned
to commit 067a76da0209462f5b9bea9886309bea07a5abdf (relative imports adjusted).
They exercise legacy load/save, edits and deletes with matching native IDs;
foreign settings and identity survive, and the older app sees no foreign records.
A shared group's foreign membership survives a legacy group delete or rename;
it appears under its saved name on the branch. Branch group operations update
both partitions intentionally. Shared `speech.json` settings and
`recent_folders.json` are application/folder preferences, not session-keyed;
prompts are templates, not ownership records. Drafts and reply caches are
in-memory only. Codex options still need runtime capability/model validation
before execution.

## Runnable read-only history preview

`tools/read_codex.py` lists the local Codex catalog or exports a selected chat
as text, Markdown or inert HTML through the production adapter and managed
connection. It starts no turn and rejects unknown/child chats and partial
history. Terminal controls are stripped from stdout; read/cleanup errors remain
redacted, and cleanup failure returns nonzero. Independent review found no
blocking issues. Six synthetic preview tests cover conversion, unavailable and
partial history, terminal controls and cleanup errors.

Exact Mac launch instructions and limitations are in
[the preview hand-testing guide](../testing/codex-read-only-preview.md). This is
a development reader tool, not UI activation: the unified window, sending and
cloud-only coverage are still incomplete. Issue #226 remains the Codex UI gate.

## Shared discovery and capability checkpoint

The controller now merges current Claude discovery inputs with registered
foreign adapters through its persistent discovery registry. Discovery and
history share the same Codex adapter/connection. Overlapping refresh callbacks
are serialized, outage caches survive and shutdown rejects late results.
The window still uses its existing Claude collection path: mixed discovery
activation and provider controls remain gated by #226.

Explicit per-session capabilities default to read-only. Codex ownership cannot
enable send, fork, delete, attachments, approvals, usage, links or Claude-only
options. Malformed capability flags fail closed. Window shortcuts enforce the
boundary before using raw native IDs; queue send/remove/edit, drafts, local
owned renames, attachments and Claude turn callbacks are isolated from a foreign
chat with the same ID. Claude discovery ignores foreign ownership records.
Existing Claude ownership/context safeguards still apply.

Independent reviews found no actionable defects in the final changes and
verified overlapping refreshes, capabilities and collision guards. Focused
validation passed 106 backend/preview/store tests, 440 existing hidden-window
tests with six skips, 120 affected Claude command tests and 55 final collision/
queue/rename/background tests. Further audit of provider-specific presentation
and owned-record lookups remains necessary before foreign owned rows are
activated. Runtime drafts/queues remain Claude-specific until Codex execution
is implemented; no Codex turns or UI controls are enabled.

The Mac probe attempted main-own and main-desktop. It produced two 224x250
pixel compressed/skewed captures for nominal 1000x720 windows, then crashed
in native teardown. Both images were opened and independently reviewed as
UNSURE; contrast, clipping and focus cannot be assessed from these captures.
Comparison found two new Mac variants without a baseline. No baseline was
accepted and no visual-parity claim is made. Issue #226 records the exact
surfaces requiring Windows light-100 and High Contrast checks.

Final full-suite validation for the discovery/capability/collision checkpoint:
1,519 passed, 69 skipped, process exited successfully. CI tests and packaging
also passed on both platforms for the preceding published preview checkpoint.

## Provider presentation and synthetic probe checkpoint

Codex history uses the existing read-only panel, including owned Codex records
while execution is unavailable. Provider-specific labels, Properties, context
limits and unsupported links no longer imply Claude behavior. Owned metadata
lookups include the provider, and Codex selection cannot consume Claude drafts,
clipboard attachments or unread state with the same native ID. New replies in
read-only Codex history announce once; Claude-owned replies remain runner-driven.

The probe adds actual `main-codex` and `properties-codex` synthetic surfaces,
without activating production discovery. Focused validation passed 111 tests
with one skip. Independent code review found no actionable defects. The Mac
main image was distorted and unreadable (UNSURE); Properties was blank (FAIL
for capture validity). These results cannot establish a product layout defect
or visual parity. No baseline was accepted. Issue #226 lists both new surfaces
and `main-own`/`main-desktop`, requiring light-100 and High Contrast comparison
against the accepted Windows baselines plus independent review and manual
focus/screen-reader checks before any Codex UI activation.

Tests and packaging passed on both platforms for the preceding published
capability checkpoint. The fresh isolated Windows VM is not installed yet;
Windows visual validation remains outstanding. Older-app persistence
compatibility is being audited separately before Codex-owned chats are enabled.

## Window discovery routing checkpoint

The window now gathers snapshots through the shared controller, preserving
current Claude ownership/runtime inputs and reusing registered foreign adapters
for discovery and history. The default controller still registers only Claude;
no Codex option or live Codex discovery is enabled. Synthetic window tests cover
mixed rows, backend outage caches, recovery and collection off the UI thread.

Independent review caught silent Claude collection failures during routing.
The window now reports generic, redacted notices once per outage; forced refresh
totals warn when listed chats may be stale. A regression exercises the actual
Claude callback failure/recovery path. The initial window/controller suite passed
477 tests with six skips; the final outage-focused checks passed three tests.

The actual probe inventory adds `main-codex-unavailable`. Its Mac capture is
again distorted and unreadable, so visual parity remains unverified and no
baseline is accepted. Windows light-100 and High Contrast comparison, independent
image review and focus/screen-reader checks remain required by #226.

## Preliminary execution route validation

Future turn/start and turn/steer requests now verify the target thread identity,
built-in OpenAI provider and working directory, then read effective configuration
for that directory. Explicit OpenAI/ChatGPT endpoint overrides, alternate default
providers/login methods, older-CLI built-in provider overrides and malformed
configuration fail closed before enqueueing a turn. Account and configuration
checks are repeated on the same connection before each synthetic send.

The production allowlist adds only read-only config/read; inference remains
disabled. Independent review found no actionable defects in this preliminary
guard. Initial RPC checks passed 72 tests; additional requested-directory and
invalid-directory regressions are included in the final focused suite.

Execution readiness still requires pinning and validating the actual effective
thread start/resume configuration. Config/read describes resolved configuration
on disk, not proof of a previously loaded thread's active route, and these reads
do not provide an atomic lock against external account/config edits. Do not
enable writes based on these guards alone. The reference is the official
[app-server documentation](https://learn.chatgpt.com/docs/app-server) and
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## Saved foreign ownership in discovery

Controller discovery now overlays saved ownership, local title and unread state
using provider/native identity. Catalog runtime status, archive flag and working
directory remain authoritative. Saved foreign chats absent from the catalog
are read-only entries with unknown status; persisted idle state cannot prove
that another client is idle. Disabled providers stay hidden even when their
companion store retains records. Claude inputs remain filtered separately, and
provider metadata is copied before worker collection. No foreign records enter
legacy storage and no write capability is granted.

Independent review found no blocking findings and independently passed 83
controller/discovery/provider tests. Local controller/persistence checks passed
45 tests; affected synthetic window checks passed 25. Production Codex discovery
and turns remain disabled pending #226 and execution validation.

Final focused validation of the preceding route-guard phase: 167 tests passed.

Combined validation after shared window discovery, preliminary route guards and
saved ownership overlay: 1,580 passed, 72 skipped; the full synthetic suite
exited successfully. The feature branch remains separate from shipping main.

## Typed streaming foundation

The read-only transport now optionally collects typed events on a bounded
nonblocking queue. Immutable events carry separate thread, turn and item IDs,
agent text deltas, authoritative completed agent text, command output deltas
and started/completed/failed/interrupted turn outcomes. Terminal error bodies
are omitted from displayable events. Unknown notification types are ignored;
malformed known events and queue overflow disconnect for reconciliation.
Pop refuses residual events from a closed stream rather than presenting an
incomplete stream as trustworthy. Each queue entry bounds text to 256K Unicode
characters and identities to 256 characters; the queue holds at most 32 events.

No UI callbacks run on the RPC reader. Event collection defaults off and the
production method allowlist still excludes inference. Approval/server requests
remain fail-closed; tool/question conversion and execution consumers are later
work. Initial events/transport checks passed 96 tests and independent review
found no actionable defects. Fixtures are synthetic, based on the installed
0.162.0-alpha.17.2 schema shapes and the official app-server event documentation.
No real chat or account was used for streaming tests.

Local tracking was refreshed to match the already-pushed feature commits; the
earlier ahead count represented stale tracking metadata, not unpublished work.

Final streaming/provider/controller/discovery validation: 188 tests passed.

## Per-turn streamed reply state

A worker-owned buffer now accepts events only for its explicit thread/turn
pair. Equal item IDs in another chat cannot affect it. Completed agent text
replaces deltas; duplicate completed items, post-completion deltas and duplicate
terminal outcomes do not produce another transition. Disconnects, malformed
matching events and capacity limits require history reconciliation. Successful
completion with missing final agent text also fails closed. Reply snapshots are
immutable and each buffer limits item count and aggregate text.

Independent review found a validation-error path that originally left the
buffer active; it now disconnects before re-raising, with regressions preventing
a later false success. This buffer is not authorization: execution must supply
IDs from a verified owned turn response. It sends no prompt and implements no
automatic retry. Approval/question routing and queues remain separate work.

Final buffer/stream/provider/controller/discovery checks: 198 passed.

## Functional UI milestone after Windows deferral

Kelly explicitly deferred Windows verification and requested functionality plus
a testable feature-branch build. The earlier Windows-before-UI activation gate
is superseded for development preview implementation; Windows parity and manual
accessibility checks remain unverified. Do not treat that deferral as approval
to change shipping main or release the feature.

Settings now offers an opt-in read-only local Codex catalog, applied on restart.
Startup registers a lazy managed Codex adapter beside Claude only when enabled.
The shared controller routes discovery/history, and Codex rows identify the
provider in Kind. Default startup makes no Codex calls. Existing capabilities
keep foreign chats read-only. Packaging names the lazy provider in all three
commands and smoke imports it without starting an agent. Synthetic startup and
settings checks passed 51 focused tests.

Mac best-effort probe again failed to capture Settings and produced an unreadable
distorted Codex image. No visual parity or baseline acceptance is claimed.
User launch and hand checks are in testing/codex-read-only-preview.md. Sending,
approvals, queues and last-message population for unopened Codex chats remain
unfinished; the feature is not release-ready.

Full functional opt-in regression suite: 1,617 passed, 72 skipped; clean exit.
Independent review found no blocking code findings and passed 132 focused checks.
An isolated Mac preview bundle builds successfully; its packaged smoke result is
recorded separately on the PR. Mac probe native teardown failed after capture.

## Provider filters and optional row column

View, Show Sessions now offers Claude Chats and Codex Chats. Hidden and archived
rules remain consistent with other filters. Session List Columns offers Provider,
optional so existing default/saved Claude rows remain unchanged. A Codex chat
with unavailable status is excluded from Idle, rather than guessed idle. The
actual synthetic startup test exercises filters and explicit provider rows.
User-facing help, README, release notes and generated guides are kept in step.

Focused validation passed 113 data/settings/group tests, 31 column/docs checks
and 56 affected synthetic window tests with one skip. The actual probe surface
is main-codex-filter; its Mac picture is distorted/unreadable and has no accepted
baseline. Session Columns capture hung and the specific agent-created probe was
stopped. No product layout defect or parity pass is inferred from capture limits.
Windows verification remains deferred by Kelly's functionality-first instruction.

Independent provider-filter review found no blockers and passed 68 focused tests.
The full run passed 1,620 tests with 73 skips but found two plain-text Help
failures from a multiline bold label in the new guide. The label now stays on
one line. Final targeted rendering/docs/columns checks passed 44 tests, and
affected window/guide checks passed 59 with one skip. The preview was rebuilt
with the corrected guide. No unrelated code changed to address these failures.

## Codex Last message column

The optional column now reads Codex through its adapter only when shown. A
round-robin budget limits each pass to eight histories; activity invalidation
and a thirty-second refresh deadline keep short display lines current. The
cache holds at most 256 lines, never full transcripts. Outages reuse known
lines without history requests, and a read failure stops the current pass.
Hiding the column or closing the provider clears the cache.

Independent review caught late reads repopulating a cleared cache. Closed-state
and generation checks now reject publication from interrupted reads; blocked
read regressions cover both close and forget. Review found no further findings.
Final checks passed 232 backend/data tests and 92 affected UI/probe tests.

The actual new surface is main-codex-last-message. The Mac probe captured it
without geometry flags, but the pixels are again skewed/compressed to 224x250
for a nominal 1000x720 frame. Both direct and independent image review are
UNSURE. Comparison found one new Mac surface with no accepted baseline; none
is accepted and no visual-parity claim is made. Windows remains deferred.

The first probe attempt did not execute because automatic approval review hit
a usage limit. After Kelly requested resumption, the normal approval path
succeeded and checks resumed without bypassing review.

Final combined regression suite: 1,629 passed, 74 skipped; clean process exit.
The rebuilt Mac preview's packaged smoke has no problems. A real local read-only
probe confirmed managed ChatGPT account type and catalog access (aggregate counts
only); no existing history was read and no prompt was sent.

## Owned Codex lifecycle foundation

A separate lifecycle connection permits start, fork and owned resume, while
keeping turn execution disabled. Each operation checks fresh ChatGPT sign-in
and the default route, then pins the built-in provider, ChatGPT login/endpoint,
read-only sandbox and user approval reviewer. The returned runtime policy,
identity, model, idle state and persistence mode must agree before producing
an immutable ownership proof. External fork must return a different identity;
resume requires a started TCP-owned Codex record. Ephemeral proofs cannot be
persisted. Unsupported owned options fail before mutation.

Independent review caught unverified returned ephemeral state; strict boolean
validation now rejects missing, malformed and mismatched responses. All 32
lifecycle tests pass independently. Combined lifecycle/event/transport/provider
checks pass 158 tests. A native ephemeral no-inference preflight accepted the
runtime overrides with ChatGPT sign-in, the built-in provider, read-only sandbox
and user reviewer. No prompt or existing history was read. This foundation is
not yet connected to writable UI actions.

Current feature CI passed both macOS jobs but failed Windows test setup at the
oversized event cases. Their generated parameter IDs included enormous input
strings, exceeding safe fixture-name lengths on Windows. Explicit short IDs
preserve the oversized inputs and assertions while keeping node names bounded.
The next CI run must confirm the Windows diagnosis; no product workaround or
release-packaging change is made.

## Long-chat reading and quieter unknown status

The installed app-server schema exposes experimental thread/turns/list and
thread/items/list. The official app-server guide documents both; its older
paginated-history limitation is superseded for the tested installed CLI by an
actual native no-inference read: 24 turns and 504 messages loaded completely,
with a last reply available. No private bodies or identifiers were logged.

The reader requests ascending turn metadata and ascending cross-thread item
pages, using opaque nextCursor values. It merges items across mid-turn page
boundaries and deduplicates by turn plus item identity. Invalid/repeated cursors,
malformed pages or failed requests never publish partial history. All callers
receive a plain safe loading error, including Find, export and Properties.
Unchanged metadata avoids reloading every page for thirty seconds; updates to
metadata invalidate the cache immediately. Loading remains on worker threads.

Unknown Codex status remains available in Properties, selected State columns
and status filters. Default rows and the spoken message title omit it; known
idle and read-only indications remain. Synthetic coverage checks page ordering,
overlap, same item IDs across turns, failure atomicity, cache deadlines, actual
controller Find/export/Last message paths and UI labels/error privacy.
Independent review found no remaining blocking findings.

The earlier Windows test diagnosis is corrected: oversized automatic pytest
IDs exceeded the Windows PYTEST_CURRENT_TEST environment-variable limit. Short
explicit IDs resolved it. Both Windows tests and packaging, and both macOS jobs,
passed on 9406639; full collection has no comparable oversized node IDs.

Validation: full Mac regression suite passed 1,672 tests with 74 skips and a
clean process exit. The subsequent controller Find/export/Last message test
also passed. The isolated Mac preview was rebuilt; packaged smoke reported no
problems. Native VoiceOver behavior still needs Kelly's hands-on confirmation;
synthetic accessible-name checks are not a screen-reader hand-test pass.

## Owned text-turn execution foundation

A separate execution connection accepts only persistent runtime proofs produced
by the verified start/fork/owned-resume lifecycle on that same connection.
Fresh account and route checks remain at the wire boundary for every turn.
Text input is bounded and the model, working directory, approval reviewer,
policy and read-only sandbox are pinned; extra overrides are rejected. A
metadata read must confirm idle before sending. This adapter is not UI-enabled.

The executor reserves one turn per chat, routes interleaved events by thread
and returned turn identity, and interrupts only the selected chat/turn. An
interrupt acknowledgement does not fabricate completion. Uncertain sends stay
reserved; disconnects require reconciliation and never replay a prompt.

Independent review caught early events arriving before the start response.
Pending-start events now have a bounded stash, are applied only after matching
the verified turn ID, and preserve replies and terminal outcomes. A serialized
drain prevents overlapping worker calls from reordering final text/completion.
Startup overflow/disconnect, failed/interrupted outcomes, wrong-turn events,
forged/other-connection proofs and pinned-option violations have regressions.
Focused execution/lifecycle/event/transport checks passed 155 tests. A process
cleanup failure in the sandboxed first run passed on retry with normal native
permissions; no transport code was changed to conceal it.

Approvals, questions, queued-send/controller integration and writable UI remain
next work. Tool requests still fail closed; this text-turn foundation grants no
production capabilities or default activation. Both Windows and both macOS CI
jobs passed on the previous reading milestone b25ea64.

The native execution smoke initially stopped before sending: the earlier wire
route guard rejected the exact canonical ChatGPT URL pinned by the lifecycle.
The guard now accepts that exact URL or its implicit default, while rejecting
alternate endpoints and explicit OpenAI API base overrides. A synthetic wire
regression and independent billing-guard review passed. The corrected native
probe completed one literal text reply, with no reconciliation required. It
used a disposable read-only working directory and deleted only the newly
created test chat; existing chats were not modified. Final execution/owned/RPC
checks passed 131 tests. This verifies text execution, not approvals or UI.

## Typed approvals and questions

The execution connection can receive supported command/file approvals and tool
questions as immutable, bounded events. Ordinary read-only connections continue
to reject server requests. Only registered owned threads can receive requests;
unknown methods, contexts, fields, duplicate IDs and overflow fail closed.
Requests are keyed by their RPC identity, independently of item identity and
outgoing client request IDs, so callbacks sharing an item remain distinct.

Answer conversion remains in the Codex adapter. Only one-time allow, deny and
stop choices are supported; persistent/session grants and unverified file-root
grants are unavailable. Questions preserve their own IDs, labels, optional
Other and secret flags, with bounded answers and aggregate display text.
Accept requires fresh ChatGPT account authentication at the wire boundary.
Answers are validated again before serialization and can be sent only once.
Terminal notifications immediately invalidate stale pending answers.

The executor ties requests to the current chat and turn. Review caught a
startup-processing failure leaving requests behind; all startup/stream failures
now share fail-closed cleanup, clearing waiting requests, marking active turns
unknown and closing the stream. Regressions cover mismatched requests arriving
before a start response. A synthetic stdio server verifies callback/answer
framing and independent client/server IDs. Focused checks passed 156 tests;
legacy 0.1.6 round-trip compatibility also remains covered.

This phase adds no writable UI capability. File changes need readable details,
secret questions need masked controls, and command requests need the dedicated
Answer flow before sending becomes available in the application. Windows VM
checks remain deferred; native text execution was tested separately above.

Final request-phase review found no blocking findings. Backend
serverRequest/resolved notifications now immediately invalidate wire answers
and clear matching waiting UI state without completing a turn. The full Mac
suite passed 1,716 tests with 74 skips and clean exit; subsequent resolution
checks passed in the 158-test focused set. Independent focused review passed
126 tests. Both platform CI pairs were green on 0934401 before this phase.

## Owned Codex UI preview and peer review follow-up

Kelly confirmed the read-only long-history preview works with VoiceOver on
October 13, and requested new chats, send, stop, approvals and questions next.
Windows manual/visual validation remains explicitly deferred; main is unchanged.

The opt-in provider now creates and forks owned chats on workers, prepares
owned resume only after successful history loading, and routes text sends,
Stop and Answer through verified runtime proofs. Queues are per chat and
advance only after successful completion. Failed/interrupted/disconnected turns
return unsent queued text to the correct draft, preserving newer typing;
uncertain prompts are never retried automatically. UI send-result callbacks
hold and coalesce progressive updates without dropping terminal or unsent data.
Closed-window results are discarded.

Dedicated command/file dialogs offer one-time decisions only. A file grant
requires readable changes for the same item; unsupported session write grants
stay disabled. Question controls preserve IDs even when question text repeats,
honor offered choices/Other, and mask secret answers. Model/effort/policy
controls describe the pinned configuration; overrides, attachments, individual
queue editing and Send Now remain unavailable in this preview.

Claude's mailbox review points 1–5 are addressed: SessionInfo identity uses only
native CLI/desktop IDs; Properties resolves a Claude path once and uses that
reader for content and facts, retaining file details on read errors; unavailable
status is a named constant with explicit sorting/filter/announcement behavior;
tail-reader tests target the Claude adapter; architecture guidance covers
providers/controller/companion stores and AGENTS.md points to CLAUDE.md.
For #228, imported identity must additionally include source computer and must
not confer local ownership. The generic reader and capability boundary permits
TCP-owned history without tools for #224. Those features remain future work.
Splitting #225 into independently mergeable phases remains Kelly's decision;
this checkpoint does not merge or release it.

New actual probe surfaces: main-codex-owned, codex-new, codex-permission,
codex-files and codex-question. Native fixture tests pass. The best-effort Mac
capture of codex-new was blank; the next capture hung and the specific synthetic
probe process was stopped. The blank image was inspected; no visual verdict or
baseline was accepted. Windows light-100/High Contrast and screen-reader checks
for these surfaces remain in #226. Source smoke imports the runtime successfully;
all three packaging commands explicitly include its lazy import.

Checkpoint validation: full native Mac suite passed 1,749 tests with 79
platform skips and exit code 0. The owned Codex/Properties subset passed 42;
visual-probe fixture suite passed 65. Source smoke reports problems=[] and
codex_execution=true. All 1,828 collected IDs remain bounded (maximum 375
characters), preserving the earlier Windows environment-limit fix. The new UI
code awaits Claude's committed-code review; these checks are not a peer-review
or visual-parity claim. Previous feature HEAD 3b1fd7c has green Windows/Mac
pytest and packaging; a pushed checkpoint starts fresh CI.

Startup follow-up: newly created/forked chats already hold a verified runtime
proof and can send before their first history becomes readable. Opening such a
chat now clears the preparation marker immediately. Persisted/unbound chats
still require successful history reconciliation before owned resume. A native
window regression simulates unavailable empty history and verifies exactly one
explicit first send. The 34-test Codex UI subset passes; both platform pytest
jobs passed on ee4796d, with packaging still running when this follow-up started.

Queue-error follow-up: Stop now publishes removed queued text even if the
interrupt RPC raises, while retaining the real in-progress outcome. Failed
automatic dispatch also returns the text it removed from the queue, since it
has no explicit Send UI callback to restore it. Both paths require deliberate
history reconciliation and never replay a prompt. New regressions verify
one-time recovery of dispatched and remaining messages and no false completion;
the runtime/UI callback set passes 15 tests.

Terminal-history follow-up: the first authoritative terminal event for the
loaded owned chat now invalidates its reader/generation. This bypasses the
30-second paginated-history cache even if second-resolution catalog metadata
has not changed, and discards any older in-flight read. Repeated terminal
snapshots do not invalidate or announce twice. Callback regressions verify
one-time generation advancement and reader invalidation.

## Resumed TCP chat: read-back and Claude review fixes

Kelly asked to continue here after the TCP-started Codex chat stalled on its
pinned read-only permissions. Its attempted writes had not landed. The new
working context resumed the outstanding message read-back request and Claude's
mailbox entry #4. The Status-column concern was withdrawn; no column redesign
is included. The previously reviewed follow-ups through 2400482 are pushed,
and Windows/Mac pytest and packaging passed on that head.

Sent and queued Codex messages now use the same announcement formatter and
announce_own setting as Claude. A recorded send problem makes the runtime not
ready and blocks sends/answers until deliberate history reconciliation and
owned reopen clears it. The reservation rechecks that problem under its lock.
Stop remains available for a verified active turn even when a problem exists.
Regressions cover failure on a live connection, explicit recovery, renewed queue
dispatch, a problem arriving during reservation, and no automatic replay.

Actual confirmed starts announce Codex is working once per turn. Typed item
activity and monotonic start time feed Turn Status's latest command/item and
elapsed time. Unconfirmed startup and unavailable state are distinguished.
Returned unsent messages announce their count and destination without reading
contents; that announcement queues after interrupting error/reply speech.

The question button explicitly says Decline and Stop Turn because the supported
answer contract has no verified decline-only response. Continue Here names the
last saved point, possible source activity, read-only sandbox and on-request
approval policy. Exceptions in Codex workers write bounded diagnostics containing
only the exception type and a fixed redacted summary; bug reports can include
that validated summary, never exception bodies, commands, paths or chat text.
The guide documents that quitting requests a stop for owned Codex replies.

Validation: full native Mac suite passed 1,763 tests / 80 skips with exit 0;
subsequent focused runtime/UI/event/diagnostic checks passed 57, and the native
Turn Status/continuation/queue subset passed three. The earlier native dialog/UI
set passed 80. Source smoke reports problems=[]. The actual new visual surface
is codex-continue; its bounded Mac probe produced a blank image (inspected), then
hung during teardown and was stopped as one isolated synthetic process. No
visual verdict or baseline was accepted; Windows visual/hand checks stay deferred.
This checkpoint awaits Claude's committed-code review before push. TCP's pinned
sandbox has not been relaxed as part of the handoff.

Claude's PR review approved ef5bc4d for push. The remaining queued-count wording
nit is corrected in both Turn Status and the reply-area status: one message,
otherwise messages. Focused callback tests and the native Mac Turn Status check
pass. This active integration chat and the feature branch are the authoritative
continuation; the older resume chats are idle. Shipping main remains unchanged.

The approved checkpoint and queued wording correction are pushed. Windows tests
and both packaging jobs passed; Mac tests exposed a startup/shutdown race rather
than a disk-image failure. A deterministic synthetic subprocess reproduction
showed that killing the child during a buffered handshake makes stdin's final
flush raise BrokenPipeError, skipping stdout cleanup. Cleanup now attempts both
pipes, tolerates that expected stdin flush failure, and retains genuine tree or
stream cleanup failures. The reproduction now succeeds and all 89 RPC tests
pass. Independent review approved the fix and passed three focused cleanup
checks. No inference, policy change, shipping-branch edit or release is involved.
The full native Mac suite passed 1,768 tests with 80 platform skips.

### Model catalog foundation

The next options phase now has a worker-only provider method for Codex's
advertised text models and reasoning efforts. It reads model/list through the
existing read-only connection, follows opaque pagination, bounds pages and
choices, and returns immutable choices only after the complete catalog validates.
Hidden and non-text models are excluded. Future advertised effort strings are
preserved; duplicate or malformed choices fail without a partial picker result.
This does not imply account entitlement or relax the independent billing gate.
There is no picker or lifecycle policy change in this checkpoint. Multiple
advertised defaults must be handled explicitly when the picker is wired.
Independent review approved the parser and provider wiring; 14 catalog tests,
42 catalog/provider tests and 102 catalog/RPC tests passed.
Read-only discovery against the installed native CLI returned seven text models
and one advertised default, without creating or resuming a chat or sending a
prompt. The next step is wiring selected model/effort into a verified owned
runtime; workspace-write remains a separate explicit user choice to implement.

The model-catalog checkpoint passed Mac CI, but Windows failed the deterministic
buffered-handshake shutdown regression. CPython documents a Windows closed-pipe
flush reporting EINVAL instead of BrokenPipeError (bpo-35754). Cleanup now handles
that Windows-specific error only after stdin is confirmed closed and the child
has exited; other errors, open pipes and live children remain failures. Six
synthetic cases cover those boundaries and still require stdout to close. The
native buffered-handshake regression remains enabled on both platforms.

### Verified model and reasoning-effort selection

Owned lifecycle APIs now accept an explicit model and reasoning effort. New/fork
choices are checked against the current execution connection's catalog; an
explicit effort requires a selected advertised model. The returned model/effort
must agree before a runtime proof is granted. Saved owned records retain the
selected effort; resume revalidates it. Each turn pins the proof's effort, and
the wire guard rejects omitted or changed selections. Existing default chats
retain their prior behavior, subscription checks and read-only/on-request policy.
The provider/runtime facade accepts the options; UI controls are not wired yet.

Independent review approved this backend checkpoint and passed 71 focused tests;
the final owned/execution/runtime/provider set passed 100. Native lifecycle
validation used an ephemeral chat and an empty temporary workspace: the returned
model and effort matched the selected catalog default, with no prompt sent,
no tool execution and no persisted chat. This verifies the config key and
response contract before enabling user choices in the New Session dialog.
Full native Mac validation passed 1,794 tests with 80 platform skips. Windows and
Mac pytest CI passed on the preceding Windows-cleanup checkpoint, confirming
the actual closed-pipe regression now succeeds on both platforms.

### Provider-neutral UI and default columns

Kelly's direction is one home for every AI's sessions, now Claude and Codex,
with Ollama (#224) and other adapters later. Shared UI must be neutral or name
its provider. Legacy Claude defaults remain only where they identify old data
or an actual Claude-only feature, not as the product's description.

Changes in this phase:

- Provider is a regular default column for everyone; Kind remains a default.
  An older saved column list gains Provider once at the end without moving,
  dropping or re-enabling other columns. A saved migration marker respects a
  subsequent removal. Loading settings does not overwrite the original file.
- Kind names the originating source (desktop app, Cowork, terminal, The Chat
  Place or another external client), independently of Provider. Codex's known
  CLI, editor and command-line origins are mapped; an app-server origin is an
  other-app session, not guessed to be a desktop session. Unknown/custom origins
  remain external. Owned chats of any provider are Chat Place sessions.
- Properties separates Provider and Kind. Shared Answer, no-waiting feedback,
  folder chooser, first-message validation, empty-list guidance, message views
  and About use neutral text. External rename names its actual provider, and
  Ctrl+O refuses foreign chats before giving Claude-specific desktop guidance.
- New Session uses the available Codex choice when Claude's CLI is absent,
  while remembering the user's provider choice when both are available.
- Quit and update guards count Claude and Codex work, including permission
  waits and pending sends, without counting the same Codex chat twice. Update
  draft warnings include both providers. Quit describes running work neutrally.
- README, shipped user guide, help/shortcuts and open 0.1.7 release notes explain
  Provider versus Kind, migration/removal, common behavior and provider-specific
  limitations. Published guide/shortcuts are regenerated, not hand-edited.
  Open in Claude, Claude Code Sign-in, What Claude Knows About You, Claude's
  permissions/plans and Remote Control retain their explicit provider names.

Independent audit found and prompted fixes for external rename, Ctrl+O and
remaining guide assumptions. Native visual probes cover columns, the owned
Codex list and plain messages: two captures are blank; the list is distorted.
All pictures were inspected. Comparison cannot establish parity; no visual
baseline is accepted. Windows visual/hand checks remain deferred by Kelly.

### Shared steering contract (#238, #239)

Both providers will use one command, **Steer**, and one planned shortcut,
**Ctrl+Alt+Enter** (Cmd+Option+Return on Mac), in the reply box and queued-message
menu. The current shortcut catalog has no command assigned to this chord;
implementation must route it before existing Ctrl+Enter handling and test that
it cannot accidentally become Send. Do not publish the shortcut as available
until its backend behavior is verified.

The behavior is sending text into the current owned turn without stopping it.
It must target the exact runtime turn, use fresh subscription/route checks,
read back according to announce_own, and announce acceptance only when known.
One steer is submitted at a time; later explicit steers wait in bounded order.
If a turn ends, a submission fails or acceptance is uncertain, recover the text
with an explanation; never start a new turn or silently turn it into a normal
queue item. Uncertain acceptance requires history reconciliation before retry.
Turn Status reports a pending steer. Desktop/external chats remain read-only.

For Codex, verify turn/steer's expected-turn contract and test accepted/rejected,
completion races, disconnects and repeated submissions with a fake server. For
Claude, verify the installed headless streaming interface actually consumes a
mid-turn user message without interrupting. If it only consumes the message
later, keep #238 open and do not present ordinary queueing as steering. Both
issues share this contract; a provider exposes Steer only after real semantics
are established. Codex sandbox/approval choices remain a separate planned phase.

Validation: the final full native Mac suite passed 1,827 tests with 80 platform
skips. Independent review approved the phase after its naming/guide findings
were fixed and inspected all three unsuccessful visual captures. Focused units
passed 128; native shared-control checks passed 37 before the final full run.
The steering contract is also posted on both #238 and #239, and #226 lists the
expanded eventual Windows surfaces and manual focus/speech checks.

### Model and effort controls

New Session and Continue Here now load the validated advertised Codex catalog
through the controller on a worker. Model and Effort retain configured-default
choices. Selecting a model offers its supported efforts with readable labels;
changing model clears the previous effort. Multiple advertised defaults do not
silently select one. Claude/Codex control selections are remembered separately
when switching provider. Creation/fork forwards only explicit nonempty choices;
existing lifecycle/turn proof and fresh ChatGPT checks revalidate them.

Late callbacks ignore a destroyed dialog, change no focus and never submit a
prompt. Availability/failure is announced when the Codex dialog is shown;
configured defaults remain usable on discovery failure. The new codex-models
probe surface uses only synthetic choices. Guide, README and open release notes
explain the controls; sandbox/approval choices remain the next phase.

Independent review found no actionable issue. Direct native tests cover both
window action bindings, choice preservation, effort reset, failure/dead-dialog
callbacks and controller create/fork routing. Native UI/probe/controller/catalog
and docs tests passed 594 with six platform skips. The earlier restricted-process
probe-fixture attempt could not initialize wx; its native rerun passed.
The native codex-models capture is blank and was inspected; comparison reports
capture failure. No visual baseline/parity verdict is claimed. Windows checks
remain deferred and need this new surface at light-100 and High Contrast.
The final full native Mac suite passed 1,834 tests with 81 platform skips.
Final review cleared the readable labels, announcement feedback wiring and
direct New/Continue binding tests; no actionable findings remain.

### Verified workspace and approval backend

Owned start, fork and resume now accept only read-only/workspace-write and
on-request/never. Workspace-write pins the canonical working folder, no extra
writable roots, no network and neither temporary-directory exemption. Runtime
proofs require the returned effective policy to match; each turn checks the
same scope again before metadata or inference requests. Never-ask refuses
unexpected permission callbacks while retaining questions.

The installed CLI verified ephemeral workspace-write creation with both
approval choices without sending inference or saving a chat. Its returned
empty writableRoots means cwd is implicitly writable; configuration roots are
additional roots. This agrees with the official configuration reference:
https://learn.chatgpt.com/docs/config-file/config-reference

Independent review identified a saved-path alias risk. Write proofs now save
the canonical folder and a separate immutable grant root. Resume rejects a
missing or changed grant before RPC; turns reject unavailable or retargeted
folders. Regression tests cover alias persistence, changed grants, removed
folders and failure before RPC. The focused policy/lifecycle/execution/runtime/
provider suite passes 132 tests. Permission controls remain on existing UI
defaults until the backend review and the next UI phase are complete.

### Release scope confirmed by Kelly

Owned Codex chats are the core of this release: New Session, send, queue, Stop,
approvals, questions, model/effort choices, announcements and Turn status must
work. Sandbox/approval controls are also required, including workspace-write
with on-request approvals, both at creation and later through Change Permission
Mode (Ctrl+Shift+O). Continue Here is optional; Steer and the reading pane are
outside this release. Kelly will test on Windows and Mac before release.
Kelly selected workspace-write with on-request approvals as the default for
new Codex chats: can edit files in its folder, asks before anything else.
Existing chats keep their saved grants; no automatic permission escalation.

Prepare a stack from current remote main, split by final layer: (1) foundations
with Claude parity and no visible change, (2) provider-aware storage with frozen
0.1.6 round trips, then finish permission controls and cut (3) opt-in Codex
reading, (4) owned Codex chats, (5) provider-neutral defaults. Each slice needs
standalone tests, independent review, green CI and its own reviewable PR. Keep
#225 as the draft integration reference. Merges and release remain Kelly's
choice; the shipping branch is not changed by preparing this stack.

### Claude's slice reviews and follow-up

Claude reviewed #244 and #245 on October 14. Foundations now defer unused
DiscoveryRegistry/stale-state/capability machinery to the slice that connects
it; Properties resolves its transcript once, failed readers are retained and
individual last-reply failures cannot discard discovery. The duplicate unused
status constants and Codex-only completion rules were removed from slice 1.
Its corrected full Mac suite passed 1,352 tests with 69 platform skips.

Storage now loads valid Claude records despite a corrupt provider companion,
blocks saves and identifies the damaged companion. Normal partitioned edits
restore the exact previous companion if the legacy write fails; migration
retains its copied data alongside the untouched original for retry. Writes
flush and fsync before replacement. Companion metadata preserves group order
and case variants merge with the existing group's name. Validation errors are
caught by the UI store-write boundary. Tests cover corruption, failed mixed
group deletion, partition rollback, order, case variants and conflicting titles.
Slice 2 passed 1,391 tests with 69 skips; 118 focused storage checks passed.
Integration propagation passed 141 focused checks and 16 native shared/Codex
GUI checks with one skip. Re-review and fresh CI are pending after these pushes.

Remaining review follow-ups: older 0.1.6 renames of mixed groups need explicit
handling/tests; unchanged companion rewrites and backup naming are nits.
Before slice 3 connects discovery isolation, malformed Claude metadata must
not make every Claude row stale. These are tracked, not silently marked fixed.

### Permission-change transaction and remaining release gates

The runtime now prepares an idle owned chat's policy through fresh ChatGPT/
route checks and verified lifecycle resume, reserving it against sends/queues.
Only the exact pending change can be confirmed after persistence. A failed save
keeps sends blocked until explicit reload restores the saved policy. Tests
include send-context and queued-dispatch races, active/queued refusal, missing
sign-in and failed-save recovery. Independent review found no blockers and
passed 99 focused checks; final local policy/storage checks passed 143.
The New Session and Change Permission Mode UI wiring is still outstanding.

The 0.1.6 mixed-group rename follow-up is now covered: saved legacy membership
anchors follow only a unique unchanged Claude group. Ambiguous matches block
saving and preserve both files. Case variants and order remain covered.
This closes that previously listed follow-up; backup naming and unchanged
companion rewrites remain nonblocking nits.

[Release acceptance](../testing/codex-release-acceptance.md) records remaining
gates and Kelly's Windows/Mac sequence. The major gaps are permission UI,
actual native file-editing validation, independent review of slices 3–5 and
final packaged cross-platform acceptance. No layout redesign is required.

### Codex usage is a release requirement

Kelly clarified that users should be able to do their daily Codex work from
TCP, as with Claude. Usage/context and an audit of everyday commands are now
shipping gates, not optional parity work. Account limits are implemented in
the existing Usage and Context command: a worker validates ChatGPT sign-in,
reads account/rateLimits/read and shows used/remaining percentages and reported
reset times, preferring named multiple buckets over the legacy single view.
Missing/malformed values never become zero usage. No inference, new thread,
credit purchase or reset consumption is involved.

Official reference: https://learn.chatgpt.com/docs/app-server documents account
rate-limit reads and thread/tokenUsage/updated notifications. The installed
native CLI read succeeded with two reported windows and no inference. Independent
review found no blockers and passed 61 focused checks; its huge-integer handling
suggestion was fixed with a regression. Provider/protocol/controller/docs checks
passed 159; native usage/Codex/probe checks passed 83. Full native Mac suite passed
1,892 with 82 skips; final help/guide/reporting checks passed 14.

The usage-codex probe surface is synthetic. Its Mac capture was blank and viewed;
comparison reports capture failure. No visual baseline or parity is claimed.
Windows light-100/High Contrast and focus/speech checks remain in #226.
Live thread context and automatic usage/context warnings still need event wiring
before release. The displayed context line clearly identifies the unfinished TCP
feature; it does not blame Codex or infer values from Claude/model defaults.

### Merged foundations, rebased storage and permission controls

Claude merged #244 as ba06abd. Storage was rebased onto that main commit,
dropping the foundation history; 8b897f8 has three storage-only commits. Its tree
is identical to reviewed e22acb8. #245 is main-based, mergeable and ready; both
Windows/Mac test and packaging workflows passed, and mailbox #20 notified Claude.
Integration was rebuilt as remaining feature code on ba06abd (83f9266), preserving
merged guards/tests and local recovery history. Its Windows/Mac CI also passed.
Later PRs 3, 4, 5 open against main only after the preceding slice merges. Codex
never pushes or merges main; Claude owns the merge workflow with Kelly's permission.

New Session now defaults Codex to workspace-write/on-request, with a read-only
choice. Each provider's selections survive switching and asynchronous model
catalog updates. Continue Here uses the selected policy too. Ctrl+Shift+O on an
owned Codex chat routes through prepare/save/confirm, refusing active/queued work
and external chats. Failed persistence restores the prior in-memory cwd/options
before leaving sends blocked; a store-identity guard prevents confirmation after
a record disappears or is replaced. Properties shows chosen model/effort and
saved sandbox/approval/root. Guide, shortcuts and open release notes match.

A real native Mac ChatGPT-backed turn wrote the exact synthetic marker file in a
disposable selected folder with workspace-write/on-request. The newly created
test chat was archived/deleted through official APIs; existing chats were not
modified. This verifies actual file editing, not just lifecycle settings.
Independent final review found no blockers and passed 141 focused checks. The
native affected UI/probe run passed 83; final full Mac suite passed 1,899 tests
with 84 platform skips. The initial full run exposed a missing SingleChoiceDialog
ShowModal interception in the probe fixture; it was corrected before the full
passing rerun. No user-facing permission code was weakened for tests.

Affected probes: codex-new, codex-models, codex-continue, codex-change-mode and
properties-codex-owned. New/owned-Properties Mac images were blank and viewed;
comparisons reject capture. The native mode probe hung and only its identified
synthetic process was terminated. Independent review inspected the black New
image. No visual verdict or baseline accepted; Windows/focus/speech checks are
still required under the deferred #226 handoff.

### Command interruption and explicit recovery (#247)

Windows CI stopped at the new permission-mode layout surface because its native
SingleChoiceDialog.ShowModal escaped the layout fixture's Dialog patch. Adding
the explicit interception restored green Windows/Mac tests and builds at 2bcca54.
Verbose test names now make a future stall identifiable.

The runtime also queued command stdout deltas even though it never consumed
those events. More than 32 queued notifications could close the execution stream,
losing the turn. Stdout remains available in completed history; it no longer
occupies the runtime's control/reply queue. A deterministic 256-delta regression
and real-stdio approval/completion scenario exercise this failure path. This is a
reproduced interruption mechanism, not a claim that every historical abandoned
turn had this cause.

Connection failures now retain fixed, app-authored reason codes (stream closed,
timeout, write failure, invalid protocol, unsupported request). Raw RPC payloads,
commands, paths and exception bodies stay out of diagnostic logs. Interruption
reasons appear in speech, Turn Status and default session rows rather than being
hidden with the ordinary unknown-status label.

Stop is the explicit recovery action. A failed Stop can check and reconnect an
owned chat only after existing local reservations and native latest-turn history
permit resume. Selection/readiness never reconnects. Recovery never resends a
message. A failed interrupt releases its stopping reservation on confirmed
connection loss; it must not permanently bar later verified recovery.

Native Mac evidence used new disposable chats and official APIs only. A shell
command wrote an exact marker in the selected folder while 54 prepare calls
preserved the same turn through completion. Closing another test app-server
during a sleep command left native history in interrupted state; explicit resume
then restored a verified ready runtime. New test chats were archived/deleted.
Kelly's original-chat retest and Windows native/speech acceptance remain required.

### Unsupported requests do not tear down a healthy execution stream

Claude's next review identified that an unsupported request still closed the
entire runtime. Execution connections now send a fixed JSON-RPC error for an
unknown method or unrepresented permission scope. The response grants nothing
and echoes no request parameters. Valid pending request identities cannot be
reused to dismiss an existing approval. Read-only transport still refuses
executable server requests, and known unowned requests remain fail-closed.

A fixed category notice is attached only to active or starting owned turns,
spoken once without interrupting read-back, and retained in Turn Status. Both
unknown-method and unsupported-command-scope fake app-server tests complete the
turn after receiving the error response, with the connection healthy. The global
Answer command also chooses the actual waiting chat while Codex is open, instead
of rejecting the shortcut based on the currently viewed provider.

### Live token reporting

Owned Codex chats now consume thread/tokenUsage/updated without occupying the
control-event queue. The display separates the latest reported model request
from cumulative usage and account limits. A percentage is shown only when Codex
reports a context window; missing telemetry is not zero. Compaction clears the
previous sample and resets the noninterrupting 80% warning. These counters do not
claim to measure remaining context capacity. The official app-server event list
and the generated notification schema establish the event and fields.

Independent review found no blocker. The full native Mac suite passed with
1,946 tests and 87 skips. A disposable native command turn reported both token
counters and a context window while repeated readiness checks preserved the turn.
Windows visual and screen-reader acceptance remain on the release checklist.

### Reading review carried into owned-chat integration

Claude's #254 findings now apply here too: one bounded foreign discovery worker
includes optional Last message work; pending refreshes retain completion tracking
without false outage announcements. Actual outages still reset comparison state.
Foreign reply history no longer blocks the shared refresh. Claude discovery
exceptions keep the previous list/groups through the original failure callback.
Both Codex history modes read metadata then bounded turns/items pages, with
unchanged-history caching and failed-page/column backoff until metadata changes.
Independent review found no remaining blocker. The native Mac full pass exposed
one copied startup fixture missing execution keyword support; after correcting
it, the affected provider/native UI set passed (191 passed, one skipped).

### Account-limit warnings

After a fresh owned turn ends, one background query checks ChatGPT account limits
and warns at 80% and 100%, deduplicated per allowance/window/reset/threshold.
Expired, missing and invalid reports do not warn or imply zero. Named allowance
buckets take precedence over the legacy duplicate. Fresh account/read still
requires ChatGPT before querying limits. Speech queues behind read-back.
Concurrent completions coalesce one follow-up query rather than overlapping
requests or losing a threshold crossing after the first report was sampled.
Independent review and 52 focused usage/UI/docs tests passed; full native Mac
suite passed with 1,971 tests and 87 skips. Guide placement/internal wording
from the reading review is also corrected here.

### Rejected queued preflights preserve order

Owned-slice independent review found that a proven-unsent rejection could retain
a completed old buffer and advance later queued text. Rejection now drains its
remaining queue atomically with releasing the sending reservation, recovers
automatic text FIFO, and never advances past that prompt. A fresh deliberate
retry remains possible without resume. Regressions cover both rejected queued
dispatch and a new send arriving during its recovery notification. Focused
runtime/UI/docs verification passed with 53 tests.

### Aggregate branch rebuilt from reviewed slices

After Claude merged reading #254 as 583f9e7, the aggregate branch was rebuilt
from that main commit, owned-chat #255 and the independently reviewed Provider
default slice. The previous aggregate history remains on the local
codex/integration-before-reading-slices backup. Function/class AST comparison
confirmed platform helper definitions are identical; the reviewed source order
and restored main notification fixture are retained. No implementation is lost,
and published guide/release references use the slice versions.

The final defaults slice passed 1,973 native Mac tests with 87 skips. Its PR
will target main only after owned #255 merges. Claude retains merge authority;
Windows/Mac manual acceptance and the #226 screenshot matrix remain release gates.
