# Chat Permissions: Codex backend and Claude integration

Parent: #307. Claude follow-up: #329. Keep the parent open until both parts ship. This replaces #298's
in-memory Codex session-approval approach.

## Work split

Codex implements the provider backend: immutable proposed prefixes, conservative
matching, TCP-only persistence, immediate revocation, one-time wire answers,
automatic-answer notices, and synthetic protocol/runtime tests. The implementation
is on branch `codex/chat-permission-backend`.

Claude implements the shared user experience for **both** providers: Chat Permissions,
the approval buttons, Claude rule metadata/removal, accessible announcements and
history/status presentation, visual evidence, and user documentation. The backend
alone does not expose a new approval button or complete #307.

## Codex backend contract

Call through `SessionController.owned_action(action, record, *args)` on a worker,
returning to wx with `wx.CallAfter`. Each action requires an owned Codex record.

| Action | Arguments after record | Result |
|---|---|---|
| `chat_rule_candidate` | immutable pending request | `CodexChatRule` or `None`; only offer the button for a candidate |
| `allow_for_chat` | same pending request | saves the rule, then sends only `accept`; errors grant nothing or report an uncertain answer |
| `chat_rules` | none | tuple of saved rules, also available before reopening a chat |
| `remove_chat_rule` | `rule_id`, or `None` for all | remaining rules; revocation is effective when the call returns |

`CodexChatRule` has `rule_id`, `prefix` (tuple), `description`, canonical `folder`,
`allowed_at_ms`, `access` (plain words), and an opaque `scope` comparison value.
Display the description, actual prefix, date/time, and access. Do not interpret
or change `scope` in the UI. Include the full outside-sandbox warning when present.

`CodexChatState.permission_notices` is a bounded tuple of
`(turn_id, request_id, text)`. Deduplicate with **chat ID + turn ID + request ID**
(IDs can be strings or integers). Announce each new entry quietly once and add it
to that chat and Turn Status. Repeated snapshots are not new notices. Keep entries
associated with their original chat/turn, including updates received while another
chat is open. These notices are runtime data, not writes to Codex transcripts.

Rules live under TCP's app-data folder in
`provider-permissions/codex/<sha256-native-id>.json`, a sibling provider store.
They survive restart. Continue Here uses a new native ID and inherits no rules.
Permission-mode preparation clears kept rules before verifying the new mode;
even a failed change cannot retain an earlier grant. Removal does not recall a
command whose one-time approval was already sent.

Matching is deliberately narrower than a shell parser: printable ASCII, one
simple command, exact leading words, no shell expansion/control syntax, no
backslashes, no executable paths. The initial reviewed executable set is `git`,
`gh`, `rg`, `curl`, `wget`, `ls`, `cat`, `cp`, `mv`, `mkdir`, `rm`, `touch`, `head`,
`tail`, `wc`, `diff`, `tar`, `zip`, `unzip` (also `.exe` forms). Other executables,
including all interpreters/wrappers, retain Allow Once. Do not broaden this set
or the grammar in UI integration. Extra permission profiles, hosts and protocols
must be identical, including hidden profile fields; even a narrower but different
profile asks again. Literal extra-access paths must be absolute, local and resolvable
and are bound to their resolved targets; relative, glob and special paths have no
chat-rule offer. Both folders must resolve to existing local directories and
the request folder must be within the chat folder. Unreadable rules fail closed.

The official [app-server approval documentation](https://learn.chatgpt.com/docs/app-server)
describes the one-time `accept` decision separately from session and saved-rule
decisions. Existing transport validation continues to reject lasting decisions.

## Claude implementation checklist

- Add **File, Chat Permissions** for chats started here, working or idle; exclude
  desktop/terminal sessions. Choose a shortcut only after checking `SHORTCUTS`.
- Show permission mode in words and reuse the existing Change Permission Mode
  dialog; disable Change while working. Refresh the rules after a mode change.
- Show one fully readable row per rule, its actual syntax/prefix and when allowed.
  Add Remove, Delete and Remove All with a confirmation naming the selected rule
  (or all rules). Preserve sensible selection/focus. Show the empty-state sentence
  from #307. Keep persistence failures visible; never claim removal succeeded early.
- For Codex, use the worker actions above. Show the existing native rule-file
  count from Properties, read-only, with the explanation in #307. Keep that count
  separate from TCP's per-chat rules. Removal applies to the next request, including
  during the running turn.
- In Codex Answer, obtain a candidate before offering **Allow for This Chat**.
  Give it the full accessible name from #307, including the prefix and any arguments.
  Route it to `allow_for_chat`, not `request.approval_answer` with a new decision.
  Leave ordinary Allow Once/Deny/Stop paths intact. Replace the old generic
  "TCP leaves lasting approvals out" note when a TCP chat-rule option is offered;
  still explain that Codex's own saved/session rules are not enabled.
- Consume `permission_notices` as described above for speech, chat and Turn Status.
- For Claude, retain `allowed_tools` and the existing `--allowedTools` path.
  Add backward-compatible timestamps in TCP storage. Existing rules have an unknown
  allowed time; do not invent one. Rename **Allow for this session** to **Allow for
  This Chat**, preserving the complete accessible rule name. Removing a rule affects
  the next turn; explain that a running turn keeps its starting rules.
- Ensure Continue Here starts with no kept rules for either provider and says so
  in its note. Add Claude fake-CLI tests for listing/removal and next-turn behavior,
  timestamp migration, restart, and copy isolation. Use synthetic content only.
- Add hidden UI tests for both providers, owned/read-only availability, mode-change
  enablement, keyboard Delete/confirm/cancel, focus after removal, empty state,
  worker failures, candidate refusal, and deduplicated automatic-answer notices.
- Add a Chat Permissions probe surface and update Answer. Run light-100 and one
  High Contrast variant in the test VM, compare and review screenshots, measure
  contrast, accept reviewed baselines, and update visual descriptions. Follow
  CLAUDE.md's visual review requirements. Leave keyboard/speech hand checks for Kelly.
- Update README/user guide, `ui_text.SHORTCUTS` if adding a key, regenerate `docs/`,
  and add a user-facing release-note entry referencing the implementation PR.
- Run the full test suite. Close #307 and superseded #298 only after the integrated
  feature and required checks are complete.

## Backend validation

Backend tests include real stdio fake-server coverage: explicit first grant,
automatic second commit with a notice, compound
command refusal, immediate removal, and inspection of the exact one-time wire
answers. Tests also cover restart, fork isolation, permission-mode clearing,
access/host changes, interpreter/wrapper refusals, invalid storage, failed saves,
and no retry of uncertain approval responses. Validation: `pytest -q tests` passed
2,464 tests with 7 skipped; the latest `test_codex_permissions.py` run passed all
57 tests, including the final path-scope and uncertain-response checks. No UI
appearance changes are made by this part, so UI probes belong to Claude's
integration PR.
