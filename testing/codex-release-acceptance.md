# Codex release acceptance (#184, #171, #225)

The release must let people talk to Codex and use it to edit files. Unified
history alone is insufficient. New Codex chats default to workspace-write with
on-request approvals; existing chats retain their saved grants. Continue Here
is optional. Steer and the reading pane are outside this release.

## Current review queue (October 16)

Owned chats (#255) and Provider defaults (#271) are merged, following foundations,
storage and reading. Saved-rule disclosure/project refusal (#272), Mac shortcut
names (#274), and Claude command/compaction feedback (#275) are also merged.
Automatic Codex discovery and bundled CLI lookup (#277) are the current release blocker.
Require green Windows/Mac tests and packaging on the final merged candidate.

## Kelly's remaining hands-on checks

Use the committed preview, with the installed app closed, and a disposable work
folder. Run these on Mac and Windows with the screen reader normally used there.

1. Launch the installed Mac app from Finder with the ChatGPT-bundled CLI and
   no shell PATH additions: Codex should appear automatically. Turning Include
   Codex chats off and restarting must keep it off; turning it on must restore it.
   Also test a real Windows Codex installation; the available Windows ARM VM has
   no ChatGPT/Codex package, so its bundle could not be validated here.
2. Start a Codex folder-edit chat and make a harmless file edit/command. Verify
   the file appears in the chosen folder and the saved access survives restart.
   With a command not covered by your always-allow rules, verify an additional
   access request can be read, answered or denied without silently changing the
   chat's permissions. New Session/Change Permission Mode must disclose saved-rule
   behavior; Properties must show a count, a pending check, or the explained refusal.
3. During a long turn, switch chats and return, then queue a message. Confirm the
   original turn continues. Stop must recover queued text; repeating Stop must
   reconcile a completed native turn if its completion event was missed. A genuine
   dropped connection must say interrupted and recover only through explicit action,
   without replaying a prompt. Retest Kelly's original #247 chat.
4. Listen to sent-message read-back and later replies/status in full, summary and
   silent modes, with own-message reading on/off. Replies, compaction and warnings
   must queue behind read-back. Verify focus remains where expected while answering
   questions/permissions; automated tests cannot establish what this sounds like.
5. Read Usage and Context, model/effort and permission choices, account resets and
   reported context. Unknown telemetry must remain unknown. After `/compact` in a
   Claude chat, hear confirmation and see its row with tool activity off; `/context`
   or `/cost` output must appear as Claude Code messages. When post-compaction token
   counts are reported, verify the counter updates. Repeat compaction in a later
   turn to check that a new event is announced again.
6. On Mac, follow the displayed/spoken Cmd, Option and Return hints, including
   Answer, Properties, find and commands. Help must use Mac key names; menus are
   reached through the menu bar (VO+M). Verify the corresponding Windows hints and
   keyboard commands still work.
7. Complete #226: capture affected surfaces in the Windows test VM at light-100
   and a High Contrast variant, compare against the baseline, review every changed
   picture and measure contrast. Mac capture attempts in this session were blank
   or distorted; they do not establish final rendering. Keep final visual review
   and baseline acceptance open until usable captures exist.

No release tag or publication is authorized by CI results or peer review alone.

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
  Owned-chat #255 and Provider-default #271 are merged; require green
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
  Verify native Windows rule inventories for ordinary drive paths and local
  verbatim (`\\?\`) paths during #226; do not mistake a refused inventory for
  an unchecked or empty one. Case-insensitive Mac path identity remains a
  fail-closed follow-up (#256).
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

## Inherited approval rules and settings: Windows results (#257, October 16)

Windows 11 ARM64, codex-cli 0.162.1 (npm native binary), branch
`codex-inherited-rules`. Every native check used a disposable `CODEX_HOME`
deleted afterwards; the real `~/.codex` was not read or changed. No sign-in
was used and no inference was requested; the probe faked only TCP's own
`account/read` gate and drove TCP's real `CodexOwnedThreads.start` against
the real app-server with ephemeral threads.

Hostile user config in the disposable home: `approval_policy = "never"`,
`sandbox_mode = "danger-full-access"`, `approvals_reviewer = "auto_review"`,
`[sandbox_workspace_write]` with an extra writable root outside the folder,
`network_access = true` and both tmp exclusions off, plus an allow rule in
`rules/default.rules` and a trusted project carrying `.codex/rules/p.rules`.

| Check | Result |
| --- | --- |
| Merged config/read | Reports the hostile values (never / danger-full-access / auto_review): the settings really are inherited |
| Read-only thread/start | Codex returned readOnly, network off, on-request, reviewer user; TCP granted the chat |
| Workspace-write, `[windows] sandbox = "unelevated"` added | Codex returned workspaceWrite, writableRoots `[]`, network off, both exclusions on, on-request, reviewer user; TCP granted the chat. The extra root, network and approval settings from config were all overridden |
| Workspace-write, no `[windows] sandbox` (hostile or clean config) | Codex downgraded to readOnly; TCP refused. It now says the Codex Agent sandbox must be set up (new `sandbox_unavailable` reason) instead of a generic failure |
| Project with `.codex/rules` | Refused with the rules folder (project layer reported by config/read) |
| User-layer rule inventory | Counted 1 file from an ordinary `C:\` user layer path; no verbatim `\\?\` path was reported |
| `codex execpolicy check` on the allow rule | `decision: allow` for the matching command; no match for another command |
| Rule file after all runs | Unchanged; TCP sends only accept/decline/cancel, never `acceptWithExecpolicyAmendment` or `acceptForSession` |

Findings, from upstream source at tag `rust-v0.162.1`:
- An inherited `allow` rule does more than skip the prompt. When every parsed
  segment of a command matches an allow rule, `exec_policy.rs` returns
  `Skip { bypass_sandbox: true }` and `tools/sandboxing.rs` runs the first
  attempt outside the sandbox: writes outside the folder and network access.
  This matches the Mac reproduction in `dev-notes/codex-command-rules.md`.
  Commands not fully matched by an allow rule stay inside the sandbox and
  prompt as before. This is the disclosed exception Kelly chose; the guide now
  says it includes the network.
- There is no client-side way to ignore user rules per thread. The only switch,
  `LoaderOverrides::ignore_user_and_project_exec_policy_rules`, is set by
  `codex exec --ignore-rules` (and the TUI); `app-server` does not expose it,
  and `thread/start`/`turn/start` have no rules parameter in the generated
  schema. `requirements.toml` `auto_review.ignore_rules` is an admin-managed
  model list, not a client option. No key was invented.
- Config approval settings (`approval_policy`, `sandbox_mode`,
  `sandbox_workspace_write`, `approvals_reviewer`) do not broaden TCP chats:
  TCP pins them in `thread/start` and every `turn/start`, and refuses unless
  the response reports exactly the requested policy.
- On Windows, workspace-write needs Codex's Windows sandbox
  (`effective_sandbox_mode` in `config_toml.rs` downgrades to read-only when it
  is disabled). Folder editing on Windows therefore requires the Agent sandbox
  setup in the Codex app; TCP fails closed and now explains it.

Still for Kelly on Windows: a signed-in folder-edit turn after the Agent
sandbox is set up (set it up first, then do hands-on check 2), and hearing the new refusal on a copy
without the sandbox. A real allow-rule escape run on Windows needs a signed-in
account and was not done; the behaviour is established from source and the
Mac reproduction.

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
