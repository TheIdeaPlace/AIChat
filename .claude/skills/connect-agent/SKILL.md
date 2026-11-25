---
name: connect-agent
description: Connect a new third-party AI coding agent (a "provider", like Claude, Codex, Copilot or Muse Code) to The Chat Place, end to end: detection and sign-in, read-only history, chats of the app's own with native permissions, then every menu command. Use this whenever someone wants to add, import, integrate, support or "hook up" another agent, CLI or AI coding tool in The Chat Place, starts a new provider adapter under thechatplace/providers/, asks how Muse Code was integrated, or wants to enable another provider's menu commands, even if they don't say "provider".
---

# Connect Agent

How to bring another AI agent into The Chat Place, distilled from integrating
Muse Code (branch `Muse_Code`, notes in `dev-notes/muse-code-*.md`). Muse is
the worked example throughout: when unsure how to do a step, read what Muse
did, then check this provider's own protocol before copying it.

Read `CLAUDE.md` (or `AGENTS.md`, which points to it) first. Everything there still applies: accessibility is the
point of the app, OS calls go in `platform_paths.py`, tests come first, the
README is the user guide, never write to another app's files.

## The one rule that matters most

**Check the installed agent before you trust its documentation.** The Muse
schema documented features the installed build refused (extra workspace
folders), accepted values it then failed on (any model id, any effort), named
choices differently than expected (Reject was decision `abort`, not
`denied`), and needed capabilities that only Unix hosts grant (admissions,
the session registry). None of that showed in the schema. Every one surfaced
the first time a real request was sent. So each feature goes:

1. **Read the protocol** (schema or docs) for the methods and shapes.
2. **Probe the real agent** with a small script outside the repo, synthetic
   content only, in a throwaway folder (`scripts/probe_template.py`).
3. **Write failing tests** whose fake host behaves like the probe showed,
   including the surprises.
4. **Implement**, then **probe again through the app's own code**.
5. **Document** what was verified and what wasn't, in the dev note.

When a probe can't be run (the agent can't do the thing on this machine),
build from the protocol, keep the action safe by default, and record it as
unverified with what blocks it. Never claim a pass you didn't see.

## Before starting: ask, don't assume

Ask the user (AskUserQuestion) when the answer is a product or safety call:

- Which permission modes/profiles to offer, especially any that let the agent
  act without a person approving (Muse: all four, warning before the two
  without human review).
- Anything that leaves traces in the agent's own data (Muse's side chats are
  durable; the user chose "build it, hide them here").
- Whether a refusal is better than a partial feature.

Decide yourself, and say so, when it's purely technical.

## Phases

Each phase is a milestone with its own dev note
(`dev-notes/<agent>-<phase>.md`, listed in `dev-notes/README.md`) and its own
commits. Commit only when the user asks.

### Phase 1: Detection and sign-in

Goal: the app finds the agent, says why it can't if it can't, and helps sign
in. Nothing runs, nothing is created.

- Find the real executable in `platform_paths.py` (PATH plus verified install
  locations; never a shell wrapper; a bounded `--version` check off the UI
  thread). Muse: `find_muse`, `.muse-version` file to the versioned binary.
- Strip credential environment variables that would bill a different account
  (Muse: `muse_child_environment`; Claude: API keys).
- An account-only connection with its own method allowlist
  (Muse: `muse_account.AccountConnection`).
- Tools, Sign In, *Agent*: a command in `commands.py` with its handler in
  `MainFrame._command_handlers`, native OK dialogs, focus back where it was.
- Install instructions are shown, never run.

### Phase 2: Read-only history

Goal: the agent's existing conversations appear in the list and read like any
other chat. Opening one never starts a turn, resumes, or asks for trust.

- A history connection with its own allowlist (list and read only), bounded
  frames, pages and time, and a version/fingerprint check
  (Muse: `HistoryConnection`, `VERIFIED_SCHEMAS`).
- A provider in `thechatplace/providers/<agent>.py`: `collect()`,
  `open_reader()`, `capabilities()` all read-only, keys `<agent>:<id>`.
- A transcript mapper that never crashes on an unknown item: count it, show
  its fallback text, and warn only when something truly can't be read
  (see references/lessons.md, "History display").
- Register in `SessionController`; add a view filter; say "reply in *Agent*"
  in the read-only reply area.
- Status: an agent's "status" may be per process. Show "status unavailable"
  for chats the app can't see rather than a guess (references/lessons.md).

### Phase 3: Chats of the app's own, with native permissions

Goal: create, send, stream, stop, answer requests, reopen after restart :
for chats started here only. Chats started elsewhere stay read-only.

- A separate execution connection and allowlist; add each method only when a
  feature needs it and the probe verified it.
- Owned records in a companion store (`provider_storage.py`,
  `own_store._valid_<agent>_options`): validate every saved key, so an
  unexpected field is refused, not stored. **When you add a saved field,
  update the validator in the same change**: Muse's `terminal` field was
  refused by the store and made every finished turn say "unavailable".
- Command identity saved before sending (UUIDv7 `commandId` for Muse); state
  only from the agent's own reads (silence is not completion); a failed Stop
  keeps the turn.
- Permissions: confirm the agent's *effective* policy before every send
  (profile, reviewer, approval mode), not just what you asked for. Default
  deny: parsing errors, timeouts and crashes never grant anything. Offer only
  one-time choices unless the user decides otherwise.
- New chats the agent makes for you (forks, side chats) may start with a
  different policy: set and confirm it before anything can be sent there.
- Errors shown to the user go through `codex_diagnostics.safe_reason`; add the
  agent's error type there, or every refusal reaches the window as a generic
  message.

### Phase 4: Every menu command

Goal: each command either works for the agent's chats or says plainly why
not, and never reaches another provider's code.

1. **Sweep first.** Copy `scripts/sweep_test_template.py` to
   `tests/test_<agent>_chat_menu.py` and fill in the fixture. It runs every
   Chat menu command in four cases (started here: idle, running, waiting;
   started elsewhere) and pins each result. Its invariants: nothing raises, no
   Claude turn starts, a chat started elsewhere never reaches execution.
2. **Also check the menu bar** (`frame.menu_items[id].IsEnabled()`): the sweep
   calls handlers directly and missed a greyed-out item once.
3. **For a temporary all-menus survey**, run the same harness over
   `commands.commands()`, print, and delete the file: don't commit it.
4. Work through commands one at a time, in the order the user picks. Put the
   agent's branch **before** any shared Claude/Codex path in the handler.
5. A chat started elsewhere: "*title* was started in *Agent*, so it's
   read-only here. Do that in *Agent*." (`_require_capability`).
6. Something the agent can't do: say why specifically, not "yet".

### Phase 5: Validation and docs (every phase)

- Full suite: `.venv/Scripts/python -m pytest -q -p no:cacheprovider tests`
  (use a `.venv` that has wxPython; a worktree's may not).
- README, `thechatplace/assets/user-guide.md` (then `python tools/make_docs.py`),
  the open release notes (highest `release-notes/vX.Y.Z.md` without a tag :
  if a release tags it meanwhile, move the entries to the next one), the
  testing guide under `testing/`, and the dev note.
- A new lazily imported module goes in all four PyInstaller command lines and
  the `--smoke-test` imports in `app.py`; update DEVELOPING.md's file table.
- Key names in spoken text go through `key_name(your_key(...))`
  (`tests/test_key_names.py` enforces it).
- New dialogs: a probe surface in `tools/ui_probe.py`; new keyboard paths: an
  Orca step in `tools/orca_speech.py` (open the app's own dialog, keep the
  expected words in sync with a test). VM pictures, keyboard walks and
  NVDA/JAWS are for the user: list them, don't claim them.

## Privacy while probing

Probes run against the user's real account. Use synthetic prompts and
throwaway folders, delete them afterwards, and when looking at the user's
real conversations print only kinds and counts, never content or titles.
Ask before anything outward-facing (filing an issue upstream, posting).

## References

- `references/lessons.md`: every surprise from the Muse work, how it showed
  up, and the fix. Read before Phase 3 and whenever a probe disagrees with
  the docs.
- `references/codebase-map.md`: the files and functions each phase touches,
  with the Muse equivalents to copy from.
- `scripts/schema_check.py`: compare the methods and fields you send with a
  JSON schema export.
- `scripts/probe_template.py`: a native probe harness to copy outside the
  repo.
- `scripts/sweep_test_template.py`: the Chat-menu sweep test to copy into
  `tests/`.
