# Codex release acceptance (#184, #171, #225)

The release must let people talk to Codex and use it to edit files. Unified
history alone is insufficient. New Codex chats default to workspace-write with
on-request approvals; existing chats retain their saved grants. Continue Here
is optional. Steer and the reading pane are outside this release.

## Remaining shipping gates

- **#247 blocks release.** Selecting a known chat must preserve its running
  connection; saved chats must be checked for unfinished turns before resume.
  Pre-send refusals must preserve other chats and give a safe, specific reason.
  Regression checks cover these guards, mid-turn approvals, unsupported requests,
  never-ending turns and Stop/queue recovery through a synthetic stdio server.
  Command output used to fill the bounded event queue despite having no runtime
  consumer; it now stays in completed history. A 256-delta burst regression
  preserves the subsequent approval and completion. Stream failures retain fixed
  safe reasons through diagnostics/speech/list display.
  Unsupported execution requests now get a fixed JSON-RPC error and a safe
  category notice without closing a healthy stream. Unknown-method and unsupported
  command-scope stdio scenarios both complete after the error response. Read-only
  transport and unowned/duplicate identities retain their fail-closed guards.
  Native Mac checks passed for command execution with 54 prepares during one
  turn, genuine stream closure during a command, persisted interrupted status,
  and explicit verified reconnect without replay. Test chats were cleaned using
  official APIs. These do not establish the exact cause of every old abandoned
  turn. Kelly must retest the original chat and sending/switching behavior on
  the pushed build, including Stop recovery; Windows native acceptance remains.
- **Read-back acceptance remains pending.** Working status no longer adds a
  spoken interruption, and reply/error announcements queue behind read-back.
  Full/summary/silent and own-message on/off sequences have regression coverage;
  VoiceOver announcements target the application element. Verify how this
  actually sounds on Mac and Windows; automated tests cannot establish it.
- Kelly reported basic owned Codex chatting working on Mac at bbd1a76. That
  first pass does not cover the later interruption/read-back failures or Windows.

- Audit everyday commands against the goal of doing Codex work from TCP:
  usage/context, model/effort changes, permission changes, file-change review,
  approvals/questions, search/export and recovery. Identify provider-specific
  differences explicitly; do not treat a generic unsupported message as parity.

- Codex Usage and Context is required for daily work in TCP: current account
  allowances/reset times and reported chat context, with accessible warnings.
  Live owned-thread counters/window and a queued 80% context warning are
  implemented, distinguishing latest request from cumulative usage. Unknown or
  disconnected reports are not treated as current zero usage. A native disposable
  command confirmed real counters/window reach TCP. Optional updates coalesce
  outside the control-event queue; compaction clears stale counts and resets the
  warning. Account-limit alerts now check after owned turns and warn at 80%/100% once
  per allowance/reset window, behind read-back. Concurrent completions coalesce
  one follow-up query. Final screen-reader/Windows acceptance remains.

- Validate the implemented New Session permission selection and Change Permission Mode
  (Ctrl+Shift+O). Both must use verified effective policy and persisted canonical
  grants; changing policy must wait for an idle chat and keep sends blocked
  until verification and saving complete.
- Foundations, storage and reading have merged through independent review.
  Finish owned-chat #255 merge and the Provider-default slice; require green
  Windows/Mac tests and packaged smoke checks on the exact candidate.
- Verify inherited Codex execution approval rules before declaring folder-access
  behavior release-ready (#257). External MCP servers, apps and plugins are disabled per chat without changing
  global settings. Thread-specific inventory must confirm disabled servers and
  zero tools before granting runtime ownership and before sending. The native
  Mac folder-edit command passed with two configured servers disabled.
  Decision recorded October 15: keep user/system rules active and disclose their
  effect in New Session, Change Permission Mode and Properties; refuse project
  rules and malformed/unreadable inventories before lifecycle and every turn.
  Verify a synthetic user rule remains usable and disclosed, and project rules
  prevent sending with the rules-folder name and recovery guidance. Do not claim
  upstream per-thread rule isolation: inventory checks are filesystem snapshots.
  Case-insensitive Mac path identity remains a fail-closed follow-up (#256).
- Repeat the passed native Mac file-edit check on Windows: a real Codex turn must edit a harmless file in a disposable selected
  folder. Confirm file scope, approvals/questions, Stop, queued text recovery
  and restarting with the saved grant. Ephemeral lifecycle policy checks alone
  do not establish that file editing works.
- Match README, shipped guide, shortcuts, open release notes and Properties/
  Turn status to the shipped behavior. Keep unimplemented controls out of help.
- Kelly's hands-on acceptance on Windows and Mac, including Claude read-back
  through the new noninterrupting VoiceOver announcement path, screen readers and
  keyboard focus. Windows probe/visual follow-up is tracked in #226; its deferral
  does not count as an accepted visual baseline.
- Merge only after the required reviews/acceptance. Bump/version/tag and release
  publication follow Kelly's release decision; branch pushes do not release.

## Candidate hand-test sequence (Windows and Mac)

Use the same candidate commit/build on both platforms. Use a disposable working
folder and synthetic prompts; existing provider conversations should be read
without being modified.

1. Claude sessions/columns/groups still work. Enable local Codex discovery and
   check a long conversation, Provider versus Kind, provider filtering and
   unavailable-backend/recovery feedback.
2. New Codex chat defaults to editing its folder with on-request approvals.
   Choose model/effort, send, hear the start/reply announcements and inspect Turn
   status. Confirm the displayed provider/model/policy match what was selected.
3. Ask for a harmless file edit inside the test folder and inspect the result.
   Exercise an approval and a question, allowing/declining/cancelling. Confirm
   their names/details and keyboard focus are understandable.
4. Queue two messages during a turn. Stop. Confirm queued text returns with an
   announcement and is not automatically replayed after an uncertain send.
   Revisit the running chat and confirm its turn continues. After a genuine
   connection drop, hear the safe reason and find it in the list/Turn Status.
   Stop explicitly checks/reconnects only after native history confirms the turn
   ended. Unfinished/unknown native work must block resume; no prompt is replayed.
5. Change Permission Mode while idle to read-only, then back to folder editing.
   Active turns/queued dispatch must refuse the change. Restart and confirm the
   selected policy remains. A missing/retargeted writable folder must block a
   write grant rather than silently granting another folder.
6. Switch between providers, use shared Answer/Turn status/Stop, and quit with
   active work or drafts. Check focus restoration, provider pronunciation and
   announcements with screen readers. Follow #226's visual surfaces/checklist.

Record commit, platform, CLI version, result and failed step without private chat
content. Do not report release readiness until the required sequence is passed.
