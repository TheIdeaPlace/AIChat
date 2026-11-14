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
