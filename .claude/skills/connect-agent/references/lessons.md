# Lessons from connecting Muse Code

Each entry: what happened, how it showed up, what fixed it, and the general
rule. Muse Code 1.4.20 on Windows, November 2026. Expect a new agent to
surprise you in the same places.

## Contents

1. Protocol versus the installed build
2. Permissions and safety
3. State, completion and restarts
4. Window and menus
5. History display and status
6. Process, docs and release

## 1. Protocol versus the installed build

- **A capability the client never asked for.** `admission/listPending` was in
  the schema, but every call failed with "capability required" (-32010): it
  belongs to peer messaging, granted only on Unix hosts. Because the status
  poll asked for it, every running turn looked "unknown". Fix: don't call it.
  Rule: check which features need a capability granted at connect time, and
  don't let an optional call fail a whole poll.
- **Documented but refused.** `workspaceRoots` (extra folders) is on three
  methods in the schema; the installed host refused it everywhere (-32602,
  -32030), whatever the path spelling. Rule: a schema field is a promise
  about the protocol, not about this build. Probe it.
- **Accepts anything, fails later.** Muse took a made-up model id and kept it
  (the chat then had no usable model), and took an effort the model doesn't
  offer (the next turn failed). Rule: send only values from a list the agent
  gave you just now, and read the result back.
- **Listed but unusable.** The model catalog listed a model the account
  couldn't use; the turn failed with `error.kind` `modelError`. Rule: map
  known error kinds to your own words; never show the host's raw message
  (it carried a request id).
- **Different names for the same idea.** Reject was decision `abort` (it
  also ends the turn), not `denied`. Rule: act on the decision field, not the
  choice id (the schema said ids are open-ended), and prefer the gentlest
  matching decision.
- **Plain text isn't a command.** `/skill args` in a text part reached the
  model as words; only a structured `skill` part ran it. Rule: convert at
  send time, checking the name against a fresh list.
- **Interdependent settings.** A model and its effort: switching to a model
  without the chat's effort made every turn fail. Rule: when one setting
  constrains another, fix the other in the same change and say so.

## 2. Permissions and safety

- **Asking for a policy isn't getting it.** Requesting "prompt for unmatched"
  still left the session on Auto-review; only choosing the Ask me profile
  gave a human reviewer. Rule: read the effective policy back and refuse to
  send unless it matches what's saved for the chat.
- **New sessions the agent makes start differently.** Forks and side chats
  began in approval mode `onRequest`, which the app's check refused. Rule:
  set and confirm mode and profile on every new session before use.
- **Restarts undo unsaved choices.** After a host restart a session reported
  no profile until reopened, and reopening applied whatever the app passed.
  Rule: save each user choice with the chat and pass it when reopening.
- **Profiles without a human.** Auto-review (an AI approves) and Unrestricted
  (no prompts, no sandbox) were offered only after the user decided, with a
  Yes/No warning and No the default.
- **A sandbox problem looks like an app problem.** Allowed commands never ran:
  "windows elevated sandbox setup has not run", even after setup reported
  ready. `muse exec` worked; `muse serve` didn't. Rule: before debugging the
  app, reproduce with the agent's own CLI; report upstream (with the user's
  permission) and record the issue in the dev note.

## 3. State, completion and restarts

- **Silence isn't completion.** Never mark a turn done because polls went
  quiet; wait for the agent's own terminal state (`lastTurn.terminal`).
- **Closing the host ends the turn.** A stdio host's turns die with it.
- **A steer becomes its own turn** (id = the steer's commandId), so the chat's
  last turn may be the steer's. Rule: save the ids of everything you sent into
  a turn, and accept any of them as its end.
- **A shared send path can drop a provider's result.** `stop_or_recover`
  reshaped every provider's Stop result into Codex's tuple, losing Muse's
  "nothing was running". Rule: give the new provider its own branch through
  shared plumbing when its result means something different.
- **The store validates every saved key.** A new field the provider saved
  (`terminal`) was refused by `own_store`, and the catch-all turned that into
  "execution unavailable". Rule: change the validator with the field, test
  through the real store, and give save failures their own error message.

## 4. Window and menus

- **Handlers aren't the whole story.** Stop Background Task worked when called
  but its menu item was greyed out by a separate enabling rule. Test the menu
  bar too.
- **Branch order in shared handlers.** Granting a capability (`aside`) would
  have sent the provider into Claude's code below; put its branch first.
- **Wrong provider's words.** "Load one of your Claude chats..." for a Muse
  chat. The sweep checks that what's said doesn't name another provider.
- **Generic refusals mislead.** "Not available yet" on a chat started
  elsewhere suggested it might be one day. Use the read-only wording.
- **Spoken keys follow the user's keys** (`key_name(your_key(...))`); a
  hard-coded "Ctrl+Shift+A" fails `tests/test_key_names.py`.
- **Errors are hidden by default.** `safe_reason` let only Codex and Copilot
  messages through; every Muse refusal became generic until it was added.
- **Double announcements.** Once rows can be Working/Needs you, the list
  announces finished turns too; add the provider to `_runtime_announces`.

## 5. History display and status

- **Unknown item kinds** (`skillRead`) and **shortened items** (`truncated`)
  each triggered a chat-wide "Error" warning. Fix: show the known kind as an
  activity line, mark a shortened item where it is, warn only for items that
  truly can't be read. Diagnose with counts of kinds, never content.
- **Status is per host.** A session's status was its load state in the host
  asked; the app's own host knows chats started here, not ones running in a
  terminal. The machine-wide registry (`muse sessions --json`) was empty on
  Windows. So: real status for chats started here, "status unavailable" for
  the rest.
- **Durable side effects.** Each side chat was a permanent chat in the
  agent's list; the user chose to build Ask Aside and hide them here.

## 6. Process, docs and release

- **Edits through shell heredocs mangle escapes and quotes.** Prefer a small
  Python edit script written with the Write tool, or the Edit tool; check for
  syntax errors right after.
- **A probe's check can match ordinary text.** Searching every message for
  "couldn't be" hit normal conversation text; check by the warning's own key.
- **Release notes move when a release tags them.** The open notes file is the
  highest version without a tag; when upstream released 0.6.1, the Muse
  entries moved to 0.6.2.
- **Merging main:** expect conflicts where two features add a branch at the
  same spot; keep both, never just one side.
- **CRLF warnings aren't CRLF commits.** Check `git show HEAD:<file>` bytes
  before worrying; a shell's `$'\r'` may not reach grep as a carriage return.
