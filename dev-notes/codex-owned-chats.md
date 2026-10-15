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
