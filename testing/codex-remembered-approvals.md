# Test remembered Codex approvals (#307)

Released in 0.2.0. To try a build from source, close the installed copy first: The Chat Place deliberately
allows one instance at a time. It uses The Chat Place's normal session store;
only its own new `provider-permissions/codex` files hold remembered approvals.
No Codex/Claude settings, native rules or transcript files are edited by The Chat Place.

Use a disposable repository and a Codex chat started in The Chat Place. Suggested sequence:

1. Ask to create a branch, add a file and commit. Open Answer with Ctrl+Shift+A.
   Read the request and its authority; choose Allow for This Chat for each offered
   Git operation. The screen-reader name should include the operation, starting
   folder and the fact it can run outside the sandbox. Enter should still choose
   Deny, or Answer Later when Deny is unavailable.
2. Ask for another edit and commit. Matching add/commit requests should continue
   automatically, each with one quiet announcement. Ctrl+Shift+T reports automatic
   approvals and the current turn's messages have an approval notice after the conversation.
   Opening the chat should select the latest conversation message rather than that notice.
3. Ctrl+Shift+H lists the rules and dates. Remove commit with Delete (Backspace on
   Mac). Hear the named confirmation. After removal, the row taking its place
   should be selected. The next commit should ask, including during a running turn.
4. Restart The Chat Place: remaining approvals should still work. Continue Here should create
   a copy with no approvals. Changing permission mode should clear this chat's rules.
5. In a disposable remote you control, try two ordinary HTTPS pushes. Approve push
   for this chat once; the next matching push should not interrupt. The credential-
   free automated probe uses a local bare remote, so this authenticated check still
   needs your signed-in environment. Explicit force or delete options, unknown options and compound
   commands should still ask. Plain push follows the repository's configuration. Native saved
   Codex rules can independently skip prompts.

These are human consent records with Allow Once authority, including Git hooks
and configured programs. They do not enforce confinement to the starting folder,
limit the network to a host, or undo work already dispatched before removal.
The initial supported command families are git status/switch/add/commit/push/
fetch/diff/log. Other commands remain manual.

The native probe is `tools/probe_codex_remembered_approvals.py --out <temp>/result.json`.
It uses real Codex with a local synthetic model, disposable Git trees and no
account credentials, and tests repeated operations, restart, copies and removal.
The synthetic stdio test separately exercises The Chat Place's actual runtime event loop.
