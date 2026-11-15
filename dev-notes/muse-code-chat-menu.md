# Muse Code: every Chat menu action handled

Plan written 2026-11-14; no issue number assigned. Follows the execution
milestone ([muse-code-execution.md](muse-code-execution.md)).

## What "handled" means

For every Chat menu command, a Muse Code chat either gets the action, done
through Muse itself, or is told plainly why not, naming Muse Code and where to
do it instead when there is a place. A command never does the wrong thing
quietly, and never reaches Claude's runner or Codex's backend. Four cases are
checked for each command: a Muse chat started here that is idle, one with a
running turn, one waiting for an answer, and a Muse chat started elsewhere.

## Where it stood when planned (from reading the code)

| Command | Muse chat started here | Notes |
| --- | --- | --- |
| Answer | Works | Checked natively |
| Stop Running Turn | Works | Checked natively |
| Chat Settings, Chat Permissions | Works | Explains Muse's own rules |
| Chat Settings, Change Permission Mode | Says why not | Muse's own mode |
| Turn Status | Bug | Reads `_codex_states`, so a running Muse turn is "not running" |
| Stop Background Task | Wrong message | Names Claude |
| Insert Command or Skill | Says why not | "isn't available for Muse Code chats yet" |
| Attach Files, Ask Aside, Chat Options, Other Machines | Says why not | Capability refusals; wording to check |
| Change Model, Change Effort | Says why not | `options` capability refusal |
| Retry from Here, Branch from Here | To check | Only Codex has a branch; may reach Claude's rewind |
| Steer | To check | With no Muse state it falls through to Send |
| Prompts, Send and Save as Prompt | To check | Probably provider-neutral |
| Send Reply to Another Chat | To check | Muse as destination or new chat |

## Steps

1. **A sweep test of the whole menu.** A hidden-window test runs every Chat
   menu command against the four Muse cases with the fake environment and a
   fake Muse provider. It checks that nothing raises, nothing reaches the
   Claude runner or a Codex backend, what is said names Muse Code and not
   Claude or Codex, and the enabled state agrees with the result. A guard test
   fails when a new Chat menu command has no Muse expectation, as
   `tests/test_ui_probe.py` does for dialogs. Its results replace the "to
   check" rows above.
2. **Fix what is wrong.** Turn Status reads `_muse_runtime` and
   `_muse_approvals`; Stop Background Task gets neutral wording; Retry and
   Branch refuse a Muse chat before any Claude path; plus whatever the sweep
   finds. Each fix starts from a failing test.
3. **Decide which commands Muse supports rather than refuses.** Suggested
   order, each with a native check of the protocol before any code:
   - Done or nearly: Answer, Stop, Turn Status, Chat Permissions, Change
     Permission Mode, Prompts, Send and Save, Send Reply to Another Chat.
   - Next: Steer (`turn/steer`), Change Model (`model/list` is allowlisted;
     the setter isn't verified), Insert Command or Skill (Muse skills), Ask
     Aside (Muse side chats, `sideFrom`), Attach Files (what `turn/start`
     input accepts).
   - Refused for now, as in the execution plan's deferred list: Retry and
     Branch (rewind and fork), Stop Background Task, Chat Options, Other
     Machines, and Change Effort unless Muse lets a session change it.
4. **Docs and accessibility with each change.** README and the built-in
   guide say per provider what works (regenerate `docs/`), the open release
   notes get an entry, the Muse testing guide gets a step per newly supported
   command, Orca steps cover changed keyboard paths, and new dialogs get probe
   surfaces, VM pictures, a real keyboard walk and NVDA/JAWS passes.

## Progress

### Step 1 done (2026-11-14): `tests/test_muse_chat_menu.py`

The sweep runs all 19 Chat menu commands (including the Chat Settings
submenu) against the four cases: 76 pairs, plus Steer with text typed. It uses
the real `MuseProvider` and `SessionController` over the fake history and
execution hosts, with dialogs that close themselves unanswered.

- **Invariants hold everywhere:** nothing raises, no Claude turn starts, and a
  chat started in Muse Code never reaches the execution host.
- `EXPECTED` pins each pair's result. The known-wrong pairs are strict
  xfails, so fixing one fails the test until its expectation is updated.
- `test_every_chat_menu_command_has_a_muse_expectation` fails when a Chat
  menu command is added without a row.

What the sweep found (replacing the "to check" rows above):

| Command | Result |
| --- | --- |
| Answer | Works: opens the Muse dialog when a request waits; otherwise "No session is waiting for an answer." |
| Stop Running Turn | Works while running or waiting (`turn/cancel`). **Wrong when idle:** says "Stop requested… Send again once it has stopped." |
| Stop Background Task | **Wrong in all cases:** "Load one of your Claude chats…" |
| Turn Status | **Wrong while running or waiting:** "No Muse Code turn is running." |
| Steer | Idle with text: sends as a normal message. **Wrong while running:** says "could not confirm this send" though nothing was sent. |
| Chat Permissions | Works for a chat started here. **Wrong for a chat started elsewhere:** "is an other app session… Its desktop app decides". |
| Retry from Here, Branch from Here | Refused cleanly; no Claude rewind path is reached. |
| Prompts, Send and Save as Prompt | Work (provider-neutral); Send and Save refuses a chat started elsewhere. |
| Send Reply to Another Chat | Works in all four cases (relay dialog). |
| Change Permission Mode | Says Muse uses its own mode (also for a chat started elsewhere, where "answer each request" doesn't apply). |
| Ask Aside, Attach Files, Insert Command or Skill, Change Model, Change Effort, Chat Options, Other Machines | Refused: "This action isn't available for Muse Code chats yet." |

Worth deciding in step 2 as well as the bugs: the generic refusal says "yet"
even for a chat started elsewhere, where Stop and Steer will never be
available here; "It's read-only here; do that in Muse Code" would be truer.

### Step 2 done (2026-11-14)

Each fix started as a failing row in `EXPECTED`; the sweep now has no xfails.

- **Turn Status** says Muse's own state (`_muse_state_text`, shared with the
  turn-status line): "Muse Code is working.", "Muse Code is waiting for you.
  Ctrl+Shift+A answers.", "Ready."
- **Stop with nothing running:** `MuseOwnedChats.stop()` returns "idle" or
  "cancelling", passed through `stop_or_recover` unchanged (it used to be
  folded into Codex's tuple), and the window says "Nothing is running in
  *title*."
- **Stop Background Task:** "Muse Code chats have no background tasks to stop.
  Stop Running Turn stops the whole turn."
- **Steer during a turn:** reads `_muse_runtime`, and says "Muse Code can't
  take a message during its turn. Wait for it to finish, or press Ctrl+. to
  stop it." Nothing is sent. Idle Steer still sends.
- **Chats started in Muse Code:** every capability refusal (and Insert
  Command, Change Permission Mode and Stop Background Task) says "*title* was
  started in Muse Code, so it's read-only here. Do that in Muse Code." Chat
  Permissions says "Muse Code decides what it may do." Limited to Muse; the
  same wording could serve Codex and Copilot chats started elsewhere
  (`HOME_APPS`), which `tests/test_ui_rewind.py` pins today.

### Step 3, Steer done (2026-11-14)

Native check first (installed 1.4.20): `turn/steer` takes `sessionId`,
`commandId` (UUIDv7), `expectedTurnId` and `input`; accepted while the named
turn runs (the ack names it), refused with -32030 `commandRejected` once it
has ended. Muse records the steer as **its own turn**, id = the steer's
`commandId`, after the steered one, so the chat's last turn may be the steer's.

- `turn/steer` is allowlisted; `MuseProvider.capabilities()` grants `steer`
  to chats started here only.
- `MuseOwnedChats.steer()` saves the steer id with the turn (`steers`, at most
  16, UUIDv7, allowed by `own_store`) before sending, forgets it if Muse
  refuses, and `_reconcile` accepts the turn's end from the steered turn or
  any saved steer. Checked natively with no polling during the steer's turn.
- The window (`_steer_muse_owned`) steers a running turn, asks you to answer
  first while a request waits, and puts refused or unconfirmed text back in
  the reply box with Muse's reason.
- Found on the way: `codex_diagnostics.safe_reason` passed no Muse message, so
  every Muse refusal reached the window as a generic one. Muse errors are all
  the app's own words, so they now pass (same length and control-character
  guard as Copilot's).

### Step 3, Change Model done (2026-11-14)

Native check first (installed 1.4.20): `model/list` with `sessionId` lists
the catalog (four models on the checking account) and marks the session's
model `isActive`; `session/setModel` takes `{modelId, providerId,
profileId}`, applies from the next model call (also mid-turn), and lasts
across a host restart. Two surprises:

- Muse **accepts any `modelId`**, even "not-a-model", and keeps it, leaving
  the chat with no usable model. `MuseOwnedChats.set_model()` sends only an id
  from the catalog it has just read, and reads the session back to confirm.
- The catalog lists models an account **can't use**: a turn on muse-ember-1.0
  failed with `lastTurn.error.kind` `modelError`. `poll()` now returns that
  kind (only a short identifier; Muse's message has a request id and is never
  shown), and the window says the chat's model may not be available to you
  and to choose another with Change Model.

`session/setModel` is allowlisted; Change Model uses the existing
`prepare_settings` / `set_model` owned actions and the shared single-choice
dialog. Muse also has `session/setReasoningEffort`, so Change Effort could
follow the same path.

### Step 3, Change Effort done (2026-11-14)

Native check first (installed 1.4.20): `session/setReasoningEffort` takes
`{sessionId, commandId, reasoningEffort}` from the closed eight-tier list.
`session/read` never carries the effort; the view does, as
`session/reasoningEffortChanged` events, and `view/page` with `direction:
backward` returns the newest page first (each page in order, `nextCursor`
towards the start). No event means none was set: the model's
`defaultReasoningEffort` applies. Each catalog model lists its tiers
(`variants`). Two surprises, as with models:

- Muse **accepts a tier the model doesn't offer** ("max" on muse-spark-1.2),
  and the next turn fails with `modelError`. `set_effort()` sends only one of
  the chat's model's tiers and reads the view back to confirm.
- So does a model switch: a chat set to "max" switched to a model without it
  fails every turn. `set_model()` now sets the new model's default in that
  case and says so ("Its effort is now high, since muse-spark-1.2 doesn't
  offer maximum."). Checked natively: the turn after the switch completed.

The current effort is read from at most 10 backward pages of 200 events;
beyond that it's "unknown" rather than a guess. `session/setReasoningEffort`
is allowlisted, and the owned actions `prepare_effort` / `set_effort` are
added to `SessionController.owned_action`.

### Step 3, Insert Command or Skill done (2026-11-14)

Native check first (installed 1.4.20): `skill/list` with `sessionId` lists
the chat's user-invocable skills (81 on the checking account: 67 user, 12
bundled, 2 plugin), each with `selector`, `source`, `description` and an
optional `argumentHint`. A skill runs only as a `skill` input part,
`{type, selector, source, arguments?}`: "/name" in a text part reaches the
model as words, and an unknown selector is refused with -32032
`skillNotFound` before any turn starts. History records the message as
"/name arguments" followed by a `skillRead` item.

- `skill/list` is allowlisted; the `skills` owned action lists rows.
- The window reuses `CommandPickerDialog` (with its search box): the chosen
  skill goes before your draft as "/name ".
- `MuseOwnedChats.send()` reads the skill list only for a message starting
  with "/", and sends a listed name as a skill part with the rest as its
  arguments; anything else is sent as typed. A skill removed in Muse since it
  was listed says so and leaves the command rejected, not unknown.

Checked natively through `send()`: "/plan ..." loaded the plan skill and the
reply followed it; "/not-a-skill ..." was answered as plain text.


### Step 3, Ask Aside: checked natively, waiting on a decision (2026-11-14)

`session/sideChat` (from a loaded main session, `{sessionId, commandId}`)
returns a new session that sees the main chat's history (asked which word to
remember, it answered from the main chat) and leaves the main chat
untouched. It also opens while the main chat has a turn running. Its policy
is `:ask-me` with a human reviewer but approval mode `onRequest`, and
`session/setPermissionProfile` to `:read-only` works on it (filesystem
sandboxed, network restricted). `session/leave` is accepted.

The catch: a side chat is **durable**. `session/list` lists it as an
ordinary `kind: root` session, and a later `session/read` shows no
`sideFrom`, so each Ask Aside would leave a new chat (a copy of the
conversation) in Muse Code's list and in The Chat Place's Muse list.
Codex's Ask Aside is ephemeral and leaves nothing; the app can't delete Muse
chats. Not implemented until it's decided whether that's acceptable.

### Step 3, Ask Aside done (2026-11-14): build it, hide the side chats

Decided: build it and hide each side chat here (it still appears in Muse
Code's own list). `MuseAsideRunner` (interface as `CodexAsideRunner`):

- refuses a chat with no finished turn (`NOTHING_FINISHED`);
- opens `session/sideChat` from the main chat, checks `sideFrom` names it,
  and gives the side id to the window at once (`_hide_muse_aside` hides
  `muse:<id>` in `HiddenStore`);
- sets `:read-only` with `session/setPermissionProfile` and asks nothing
  unless the reply confirms it (with a human reviewer);
- sends the question to the side chat only, streams the side turn's replies,
  and cancels the turn on any pending approval or question: nothing is ever
  decided for you;
- always cancels an unfinished side turn and leaves the side chat.

`session/sideChat` and `session/setPermissionProfile` are allowlisted, the
latter only ever sent for the side chat just made; `aside` is granted to
chats started here only, and Ask Aside's Muse branch comes before its
capability check so a Muse chat can never reach the Claude path.

Checked natively: idle and mid-turn asides both answered from the main
chat's history (mid-turn, only the question, not the running turn's
request); a question needing a shell command made no request (the read-only
side chat couldn't run it, the #149 sandbox problem) and the model said so;
no decision was sent and the main chat got only its own messages.

### Step 3, Attach Files done (2026-11-14)

Native check first (installed 1.4.20): an `image` input part
(`base64Data`, `mediaType`, optional `clientRef`) reaches the model (128 px
red and blue squares named correctly; an 8 px one was called grey, so tiny
images say little); bad base64 is -32602 before any turn starts. A file goes
as `@"path"` in the text: Muse reads it with `read_file`, inside the chat's
folder and outside it (only writes are confined to the workspace).

- `providers/muse_input.py`: `MuseInput` (a `str`, as `CodexInput` is, so
  the send path, announcements and recovery keep working) carries image
  parts; `build_input()` makes images that pass the app's checks (signature,
  `fits_inline`) image parts named in the text, and every other file an
  `Attached: @"path"` line. At most 20; a file gone is refused.
- `MuseOwnedChats.send()` appends the images after the text (or skill) part,
  and refuses more than `MAX_INPUT_BYTES` (8 MB, under the 10 MB frame)
  before saving or sending anything. A skill message keeps its attachments:
  the lines join its arguments.
- `attachments` is granted to chats started here only; the window builds the
  `MuseInput` on Send and clears the attachments once it has gone.
- The new module is in all four PyInstaller command lines and the smoke
  test, as AGENTS.md requires for a lazily imported one.

Checked natively through `build_input()` and `send()`: a green picture and
a text file from outside the folder in one message; the reply was "green
otter".

### Change Permission Mode done (2026-11-15)

Decided: offer all four Muse Code profiles, warning before the two you
don't review. Native check first (installed 1.4.20): `session/setPermissionProfile`
switches an owned chat between `:read-only`, `:ask-me`, `:auto-review` and
`:unrestricted`; approval mode stays `promptUnmatched`; the reviewer is
human, human, `autoReview` and `none`, attached in each, and Unrestricted
reports filesystem unrestricted, network enabled, approval `allowAll`. After
a host restart a chat reports no profile until reopened, and
`session/resume`'s `permissionProfile` sets it, so the app passing Ask me
would have silently undone a change.

- The chosen profile is saved (`provider_options["muse_profile"]`, one of the
  four, allowed by `own_store`), passed when the chat is reopened, and
  checked before every send: `_policy()` now wants the saved profile with its
  own reviewer, so an Ask me chat whose reviewer turns to `autoReview` is
  still refused.
- `set_mode()` sends only a profile Muse lists with the expected reviewer,
  confirms it from the reply and a read-back, and saves it only then.
- The window offers the four through the shared choice dialog, and asks
  first (Yes/No, No default) before Auto-review or Unrestricted;
  `MusePermissions` names the chat's profile and its Change goes to the same
  flow. The New Session note now says a chat starts on Ask me.

Checked natively through `set_mode()`/`send()`: Read only (sent), kept after
a host restart (sent), Auto-review (sent), Unrestricted (no turn), back to
Ask me. The changed dialog text (New Session note, Chat Permissions) still
needs the VM visual probe.


Orca (2026-11-15): `tools/orca_speech.py` has steps for both warnings
(triggers `muse-mode-auto-review` and `muse-mode-unrestricted`, which open
the app's own `_confirm_muse_mode`): the warning is read with its title,
who decides and No; Enter (No) returns to the messages and, with Orca 49+,
says the chat keeps Ask me. `tests/test_orca_tools.py` keeps the steps'
words in the warning's text. The Muse Chat Permissions step now expects
"Muse Code: Ask me". Not yet run against real Orca (Linux CI job or the
Ubuntu VM as `tcptest`).
