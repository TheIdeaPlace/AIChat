# Approval noise: post-mortem and plans (#358 follow-up)

Agreed by Claude, Codex and Copilot in the agent conversation "fixing codex approvals", 2026-11-07.

## 1. Post-mortem

**What we set out to fix.** Codex chats asked for approval on every command, so Kelly pressed
Ctrl+Shift+A dozens of times per task. #358 / PR #374 added Codex's automatic approval review.

**What we got.** The prompts stopped. Every decision still reached Kelly, now three ways at once:

- spoken: `ui/codex_chats.py` (the `permission_notices` loop) says "title: Codex automatic review
  allowed the request. Command: … Reason: …" for each decision;
- in the message list: `ui/main_frame.py` `_queued_rows` adds a `PERMISSION_NOTICE` row for every
  decision in the current turn, mixed in with the conversation;
- in the status text: `codex_chats.py` adds "N requests allowed automatically. Latest: …" with
  the full command and reason.

Approvals by saved rules ("Allowed git status, as you said for this chat") use the same path.
Copilot chats share it too (`RUNTIME_PROVIDERS`). Claude chats don't use this path.

Key presses turned into speech and rows. The effort moved instead of going away, and it got
worse: now it buries the replies.

**Root causes**

1. **The spec was read too broadly.** #358 said "announce refusals and retain readable
   decisions." The build announced and showed *all* decisions. Nobody asked whether a routine
   approval deserves the attention a refusal does.
2. **It was never tested under real load.** The validation used "a tiny text turn" for each mode,
   which means zero or one approval. No one ran a real 30-command task and listened to it.
   The tests check that a notice exists, never how many there are.
3. **Reviews checked safety, not noise.** The reviews found real ownership and billing defects.
   None asked what a screen reader user hears during a long turn. Our accessibility check asks
   "is it named and announced?", never "is it announced too much?"
4. **Success was measured in the wrong unit.** "No more prompts" passed. "Less effort to follow
   the chat" would have failed.
5. **A channel built for rare events was reused for frequent ones.** Permission notices were made
   for occasional remembered approvals. Automatic review sends far more, and no one estimated
   how many.

## 2. Global changes (every project, every agent)

1. **Volume question in every spec and review.** For anything that speaks, adds rows or
   notifies: how often can it fire in one turn? Per tool call, or unbounded, means it has to be
   quiet by default or summarised. Add this to the `code-reviewer` agent's accessibility
   section and to the global CLAUDE.md.
2. **Event tiers as a written rule.** *Needs you* (blocked, a question, an error): spoken and shown.
   *Worth knowing* (a denial the task worked around): one concise notice that waits for current
   speech. *Routine* (approvals, tool activity): recorded, reachable by keyboard, silent by
   default. Every new event names its tier in the PR.
3. **Heavy-turn acceptance test.** Fakes and the bug-bash harness get a turn with 40+ tool calls
   and approvals, including decisions that arrive while a reply is being spoken. The test
   asserts on the spoken transcript and the rows added. Zero routine announcements or rows by
   default; every blocking problem announced; focus doesn't move; full details reachable by
   keyboard. No universal cap on how many real failures get announced.
4. **Spec the experience, not the feature.** Each issue states "after this, a 30-command turn
   sounds like: …". If we can't write that, the design isn't done.
5. **Name the real goal.** When the fix is "remove friction X," the acceptance test is that total
   effort went down, not that X disappeared.
6. **Prove a shared path is shared before claiming it.** Before saying "one change covers all
   providers," trace it. (Here: Codex and Copilot share it; Claude does not.)

## 3. Fixing approvals in TCP (as built, #564)

Kelly's direction after the plan: approvals aren't unwanted, they're a good way to learn the
commands the AI uses. The problem was having no control over *when* they appear. They're another
category, like tool activity, and deserve a view of their own. So:

1. **Allowed approvals belong with tool activity.** Allowed requests (from automatic review or a
   saved rule) are shown in the chat and spoken only when the chat's **Show Tool Activity**
   (Ctrl+T) is on, like tool calls. That's already per chat, so Kelly chooses when.
2. **Lists, Approvals** (Ctrl+Shift+I, then Ctrl+8): every decision in the chat, newest first.
   For example "Allowed: git log --oneline. Automatic review." The details hold the reviewer's
   reason, and Copy copies the command. It's last in the Kinds list so Ctrl+1 to Ctrl+7 keep the
   kinds they had. It covers the whole chat in either scope, because the history uses Codex's
   turn ids, not the list's turns.
3. **Anything not allowed** (denied, timed out, stopped) is always shown and said once. It waits
   for current speech, never interrupts, never sets Needs You on its own, and never moves focus.
   Codex carries on after a denial, and if it then needs Kelly, it asks, which already sets
   Needs You.
4. **Turn Status** (Ctrl+Shift+T) gives counts only: "12 requests allowed automatically. 1 not
   allowed." It's spoken only on request, so the count changing never causes chatter.
5. **Recent decision history:** the last 128 decisions per chat, now including saved-rule
   approvals (kept as `saved-rule:<id>`, each with its own turn), so Lists and Chat Permissions
   show them after a restart.
6. **Scope:** Codex only. Copilot's state has the field, but nothing fills it. Claude doesn't use
   this path. Kelly would like Claude and Copilot to show approvals this way too: that's a
   follow-up.
7. **Tests:** `tests/test_approvals.py` (parsing, the Lists tab, merging, counts, history format),
   and hidden-window tests for a 40-approval turn with tool activity off and on, Lists, Approvals,
   and the unavailable messages.
