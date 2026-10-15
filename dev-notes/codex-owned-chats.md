# Owned Codex chats (#184, #171, #225)

This slice builds on the reviewed reading layer. It adds owned-chat lifecycle,
turns and queues, approvals/questions, creation-time model/effort choices,
workspace/read-only policy controls, shared Answer/Stop/Turn Status, account and
context reports, and recovery diagnostics. The default write grant is the
selected canonical folder with on-request approval outside its scope. Fresh
ChatGPT sign-in and the configured builtin route are checked before every turn.
Only proven runtime ownership can send. Selection and readiness never resume
over an unfinished or uncertain native turn. Explicit Stop recovery checks
native history and never replays a prompt.

Provider-default migration remains the following slice. Kind describes origin;
Provider identifies the AI. Claude adapters retain their compatibility data.
No raw Codex rollout is parsed or written. GUI callbacks return through CallAfter,
and stdout/token updates cannot fill the approval/completion control queue.
Unsupported executable requests get an error and notice without permission.

Independent backend/UI reviews checked ownership, policy, transport, runtime,
model choices and provider routing. Review found a rejected queued preflight
could advance later text: recovery now drains its queue atomically with releasing
the reservation, recovers text FIFO and prevents automatic advancement. Tests
also cover a new send during the recovery notification. The guide scopes later
model/effort changes to Claude; Codex chooses them at creation/fork.

Validation: full native Mac 1,957 passed, 87 skipped; final runtime/policy/UI/docs
118 passed. Native file-edit, command/readiness and real stream-drop checks from
the integration branch used disposable chats/folders and official cleanup APIs.
Windows screenshots and keyboard/speech acceptance remain required by #226 and
testing/codex-release-acceptance.md. This commit is not release authorization.

## PR #255 review corrections

Claude review #27 reproduced lifecycle failures. Reply deltas now coalesce per
item; oversized presentation is truncated rather than disconnecting every chat.
Repeated Stop reconciles the same terminal native turn without resume or replay,
and retires its late approvals. Queue recovery rechecks the current turn under
the runtime lock so a stale interrupted snapshot cannot recover newly queued text.
Ordinary RPC timeouts abandon only that request and ignore late responses; an
uncertain inference timeout still closes the connection.

Runtime-announced Codex chats no longer receive duplicate discovery speech.
Approvals/questions and completions produce desktop notifications, and requests
include a bounded command, file-change or question summary. Continue Here starts
read-only; home folders, their parents and volume roots cannot receive write
access. The deleted provider-label regression tests are restored. User text,
Answer priority wording and release notes now describe the actual behavior.

External MCP tools are disabled per chat, including app/plugin sources, without
changing global configuration. A thread-specific inventory must prove all servers
disabled with no tools before runtime ownership and before every turn. A native
ephemeral probe reported both configured servers disabled and zero tools. The
real command regression then completed a disposable in-folder file edit through
53 readiness checks during its turn, with token usage and context window reported.
Its disposable chat was removed through official archive/delete methods.

Independent runtime and UI reviews found no remaining blockers after the queue
snapshot race was fixed. A separate policy review approved external-tool isolation
and its 189 focused tests. Mac case canonicalization is tracked in #256; inherited
execution approval rules remain a release gate in #257. Windows visual comparisons
and manual focus/VoiceOver checks remain in #226, including Message dialog labels
and non-interrupting Claude read-back. No new screenshot baseline is claimed here.
