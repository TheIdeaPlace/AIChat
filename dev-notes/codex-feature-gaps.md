# Codex feature gaps (#467)

This implementation builds on `claude-gaps-integration` at a3b1bc3 and preserves
Claude's five additional accessibility fixes in c399adf. The shared dialogs,
keyboard actions and transcript presentation stay shared. Codex operations live
in its provider and runtime; no foreign thread is passed to a Claude runner or
read from a raw Codex rollout file.

The protocol was checked against the installed **Codex 0.163.0** generated JSON
schema, with `--experimental`, on November 1, 2026. Relevant official reference:
[app-server](https://learn.chatgpt.com/docs/app-server). Older runtimes can refuse
new endpoints; unsupported methods do not trigger a shell fallback or replay.

## Coverage of the parent issue

Numbers refer to the original #467 table. “Implemented” describes this branch;
it does not imply an unperformed live-provider or screen-reader check passed.

| Gap | Result and remaining boundary |
|---|---|
| 1. Connected tools | Configured MCP servers can be explicitly enabled in a new read-only copy through Claude's shared Connected Tools dialog. Selection persists and is verified before turns. Apps/plugins, OAuth inside TCP and MCP elicitation forms remain follow-ups. |
| 2. Attachments | Native local images, clipboard images, and file references work in owned Codex chats, including queued messages. Recovery to a plain draft retains file names; reattach images for native image input. |
| 3. Model/effort | Existing idle chats use the current catalog and the existing prepare/save/finish barrier. Effective effort or the catalog default is pinned, including resetting from an earlier explicit effort. |
| 4. Goals | Read status/usage, pause, clear. Autonomous creation/resume remains open. Goal tools are disabled because native continuations would bypass TCP's fresh per-turn account, route and permission checks. |
| 5. Scheduled work | Open. Needs a provider-neutral scheduler, missed-run behavior, notifications and approval handling. Backup scheduling is not an agent scheduler. |
| 6. Interactive command input | Open. Need a typed `writeStdin` approval showing exactly what is sent, rather than treating it as a command execution. |
| 7. Folder/network scopes | Open. Current canonical workspace grants and one-time approvals remain. Multiple roots and destination scopes require independently verified policy serialization and recovery. |
| 8. Remembered approvals | Existing limited Git approvals remain. Broader rule/session approvals require explicit scope, inspect/remove controls and replay-safe persistence. |
| 9. Project rule files | Open. Trust must bind the approved rule content and applicable folders; removing the inventory check would silently broaden access. |
| 10. External takeover | Open. Continue Here and branches create owned copies. In-place takeover needs confirmation of native idle status and coordination with other clients. |
| 11. Earlier-turn branching | Implemented using native `lastTurnId`; reads and paginates native history to validate the selected boundary. |
| 12. Revert/retry | Shared Rewind makes a preserved-source copy using `beforeTurnId`, with the chosen user message restored as a draft. Destructive in-place reversion remains unexposed; no filesystem rollback is claimed. |
| 13. Temporary side questions | Shared Ask Aside dialog, separate process, ephemeral read-only fork. Disables shell, external tools, hooks, memories, Goals and delegation. Tool questions/approvals are declined rather than hanging. |
| 14. Compaction | `/compact`, native events and normal turn status. After a policy/model/mode change, a normal pinned turn must apply the settings before compaction can use the loaded native thread. |
| 15. Native Plan | `/plan` chooses advertised Plan/Default presets. Mode is persisted and pinned alongside the model, effort and policy on each turn. It is separate from the permission grant. |
| 16. Dedicated review | Open. Existing prompted reviews and agent conversations remain. A dedicated review must start on an independently verified read-only fork, pin settings and present native review results. |
| 17. Explicit skills | Current enabled folder inventory in the shared picker; sends a native `skill` item as well as `$name`. |
| 18. Subagent history | Open. Claude's shared reader can be reused after a Codex child-thread adapter and ownership-aware approval routing are built. Never use a child ID as an owned parent proof. |
| 19. Background terminals | Shared Stop Background Task action and `/background`; paginated native list and confirmed single-terminal termination. |
| 20. Native lifecycle | Open. Local names/hiding remain. Native archive/delete/rename need separate ownership, confirmation, discovery and recovery semantics. |
| 21. Worktree handoff | Open. Needs canonical path rebinding, native cwd verification and recovery when paths move or disappear. Existing worktree creation remains. |
| 22. Memory controls | Native thread memory-mode on/off, including before the first message. |
| 23. Speed tiers | Open. Model catalogs advertise tiers; selection needs explicit usage/cost wording and per-turn verification, with configured defaults preserved. |
| 24. Browser | Open. A configured MCP browser server can now be enabled; Codex desktop's integrated browser is not supplied by that. |
| 25. Computer use | Open. Requires a supported host/service API, accessible consent and platform isolation. |
| 26. Remote hosts/mobile | Open. Needs an authenticated transport and remote paths/approval identity; the local stdio runtime cannot stand in for it. |
| 27. Cloud | Open. Needs a supported authenticated cloud task API and environment/result lifecycle. Do not guess private desktop endpoints. |

## Decisions and invariants

- A Codex input is a string with immutable native image/skill parts. Existing
  queues and draft recovery remain usable, without dropping native parts while
  queued. Missing queued images are a known preflight refusal and do not close
  unrelated chats' streams.
- A settings change never takes effect before persistence succeeds. A failed
  store write restores the previous mutable record as well as rejecting the
  new proof. Every normal turn pins the verified settings again.
- Tool configuration is loaded with a native thread. Changing a saved option
  alone cannot safely enable or disable a live tool. Therefore changing the
  tool set makes a copy, with explicit consent to external service access.
  Empty unsent chats use a new thread rather than forking a nonexistent rollout.
- MCP tools are outside filesystem sandbox scope. All unselected configured
  servers, plus apps/plugins, are disabled; unexpected active tools refuse the
  operation. Consent to one server does not authorize other servers.
- Native controls use verified owned proofs, refresh the ChatGPT account and
  recheck the route/rules/tools. A timed-out mutation is not automatically
  retried. A normal message cannot race a settings/control reservation.
- A side question's ephemeral proof is executable only in its dedicated
  connection. Normal ephemeral permission probes remain non-executable.
- Branch boundaries come from Codex `ChatMessage.key`, never Claude's chain
  `uuid`. A branch after a message includes its entire native turn.

## Verification

Focused tests cover image/skill preservation and recovery, missing queued files,
branch boundaries from converted history, settings persistence failure, effort
reset, Plan/Default transitions, rejected autonomous Goals, external tool
selection/isolation, terminal pagination, unsupported inputs, and native control
ownership/account checks. GUI tests use synthetic data and run only in the VM.

A no-prompt live check on Codex 0.163.0 verified persistent thread creation,
`thread/memoryMode/set`, `thread/goal/get` and
`thread/backgroundTerminals/list` before any normal turn. It verified endpoint
availability, not model output or real third-party MCP credentials.

Visual probes cover Codex attachments, command/skill picker, Ask Aside,
Connected Tools and conversation rewind in Light and Aquatic High Contrast.
The final validation record belongs in the PR, with any failures stated.

## Human listening checks

With JAWS or NVDA, open a local Codex chat and:

1. Press Ctrl+Shift+F, attach an image, then Delete it in the attachment list.
   Hear its name/count and focus return to the reply box when the list empties.
2. Press Ctrl+/, choose an enabled `$skill`, and hear its name and description.
   Inspect the reply box before sending; select `/plan` and verify the mode
   picker reads both choices and the heading reports Plan after saving.
3. Select a saved user message and press Ctrl+Shift+Z. Hear Conversation Only
   selected, other choices unavailable, and that files remain unchanged.
   Escape must return to the selected message once.
4. Press Ctrl+Shift+Y, type a short question and Ctrl+Enter. Hear “Asking Codex,”
   then the answer according to the configured announcement level. Escape
   cancels the temporary question and returns to the prior control.
5. Open View, Connected Tools. Arrow through servers; hear each name/status,
   and Tab through Sign In, Read Status, Copy with Tool On/Off, Refresh,
   Close and Details. Decline the copy confirmation and verify no new chat.
6. With two background terminals running in a disposable chat, use Stop
   Background Task, select one and confirm. Hear the stopped result; the
   other terminal and the turn should continue.
