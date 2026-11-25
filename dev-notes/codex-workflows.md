# Codex workflow follow-up

Based on main at `224deece` (0.6.3), in `codex/workflows-20261125`.
Current main, including the separately developed Claude Fable work, is merged for integration verification.

## Queue controls (#526)

The runtime owns the queue, including its native image/skill input. Each queued
occurrence is a distinct CodexInput, so equal text cannot stand in for a row
that was already dispatched. Take and indexed steer compare object identity
under the same lock as dispatch, Stop and queue advancement. Taking a message
refuses while another steer is in flight, so restoration cannot reorder edits.

Edit returns files, native images and skills to removable attachments,
merging with a draft typed meanwhile. A switched chat gets its own draft, never
the newly open chat's box. Stop and uncertain-send recovery preserve those parts
too. Live long inputs retain their full original independently of the bounded
history cache; editing never deletes the only full copy. No transcript changes.

Send Now on a Codex queue row uses native turn/steer, as Steer already does.
It does not interrupt a running command. Invalid-request refusals put the
message back in its queue; uncertain delivery returns it with a history warning
and never replays it. Claude's interrupt-and-send and Copilot's queue stay as before.

## Subagent feedback (#569, #570)

The connection captures active owned parent turns when each child turn starts.
A bounded context table survives child/parent completion for late reviews;
duplicate start notifications cannot rebind the child to a later parent turn.
Bounded background workers, never the reader or shared event listener, verify native parentThreadId
ancestry (OpenAI provider, matching IDs, bounded depth and cycle rejection).
The connection must still hold the same owned proof. Child metadata never
becomes execution ownership.

Only tool-start counters, successful-edit counters and automatic-review notices
are attributed. Child replies, tokens, lifecycle and diffs stay with the child.
Per-child/turn/item identities prevent duplicates and review-ID collisions.
Routine allowed reviews retain the existing quiet/history behavior; denials
retain the existing announcement. Existing sound definitions are reused.

A real ChatGPT-subscription check on Codex 0.163.0, through TCP's actual
CodexChatRuntime and CodexExecutionConnection, started a disposable read-only
parent with automatic review. Its child ran only a PowerShell WriteLine marker
with explicit escalation. The trace showed child turn/started, child
item/autoApprovalReview/started, child review completed approved, child completed,
then parent completed. The parent's final state contained one tool start and
one `auto-review:subagent:...` notice saying the child request was allowed.
No manual approval or file change occurred. Parent and child chats were deleted
through the runtime after the check. Synthetic traces cover denied, timed-out,
late, wrong-provider and later-parent cases; the real check exercised approved.

## Connected tools (#597, #598, #536)

Official reference: https://learn.chatgpt.com/docs/app-server
Installed experimental schema: codex-cli 0.163.0, generated November 25, 2026.

A real app-server with a local synthetic model and stdio MCP tool established:

- A thread created with the server disabled still reports it disabled after
  thread/resume with enabled=true followed by config/mcpServer/reload. A fresh
  enabled thread connects and lists the tool. Keep Copy with Tool On/Off (#597).
- mcpServer/oauth/login accepts no threadId. A local synthetic authorization
  server completed the login and emitted threadId=null and the matching loginId.
  TCP's production standalone connection completed the same flow and closed its
  process. There were no real credentials, external accounts, browser windows or
  paid model calls in this probe.
- A harmless tool marked destructiveHint=true, under on-request, triggers
  mcpServer/elicitation/request. Its form has no fields; its metadata identifies
  mcp_tool_call and contains the arguments. TCP now copies those arguments into
  the immutable Message text, previewed at 4096 characters with an omitted count. Replies use the existing one-call answer;
  metadata offers to persist the grant are never sent back. This does not promise
  every configured tool asks: native tool/configuration policy decides.

Standalone sign-in starts an OAuth-only connection in the chat's canonical
folder. It reads that process's effective configuration, checks the server is
still present, and never starts/resumes a thread, enables a server, writes
configuration or acquires chat ownership. Results match folder, server and
native loginId on that exact connection; failed polling never reconnects.
There are at most eight pending processes, with expiry timers, completion
cleanup and shutdown coordination with any cleanup already in progress.
Failed cleanup retains the manager for shutdown retry. API-credit providers
cannot use the subscription sign-in path.

Reproduce the free native probe from the repository (output outside it):

```powershell
python tools/probe_codex_workflows.py --out "$env:TEMP/codex-workflows-result.json"
```

It uses a disposable CODEX_HOME and localhost services. No real chat history,
account, API key or third-party server is needed. Generated data stays in temp.

## Verification and review

Windowless regressions cover stale equal-text queue occurrences, concurrent
queue operations, native image/skill recovery, live long-message cache eviction,
child provenance and delayed reviews, OAuth identity, expiry and shutdown races,
and immutable MCP tool arguments. Windowed handler/focus tests run in VM and CI.
The visual probe includes main-codex-queue-edited and codex-tool-approval; Orca
steps walk the tool approval's Message, Send, Decline and Answer Later controls.

An independent hostile reviewer found queue identity, late child attribution,
long-message eviction/native recovery and OAuth cleanup races. Each was fixed
and covered by a regression; the final code review had no pending findings.
CI and VM results are recorded in the PR, not assumed here.


Final review additions: native probe sets both CODEX_HOME and CODEX_SQLITE_HOME
to disposable storage, restores process environment even on failure, and closes
a partially initialized RPC before shutting down the local HTTP server. Late
child counters can update only their same successfully completed parent turn;
parent activity/status and completion announcements stay unchanged. Windowed
Codex feature tests passed in the Windows VM (50 tests). Eight visual surfaces
were independently inspected; inherited light native-border contrast remains
documented in testing/visual/descriptions.md.


PR comment follow-up: merged current main including Fable notes and visual surfaces.
Child presentation ancestry now runs on at most four daemon workers, with 128
positive/negative cached turn bindings, 32 pending events per lookup and 128
ready events. Saturation skips optional display data without poisoning the
cache; later events can retry. The shared listener never waits for ancestry.
MCP arguments are previewed at 4096 characters with an exact omitted count.
Editable draft text and explicit attachment paths travel separately from native
wire text through queueing, long-message handoff and context prepending, so
user-written text is never stripped heuristically. Recovered skills are visible
in Attachments and retain native skill type when resent. Invalid login IDs are
rejected, and API providers create no subscription OAuth owner. Shutdown cleanup
is already on the non-daemon provider-cleanup worker in main_frame.py.
The local OAuth probe rejects external and CR/LF redirect addresses.
