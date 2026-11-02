# What the ACP-first rebuild found (#489, #466)

The question: if The Chat Place had started with what we know now, what would it look like, and is that better? This branch built it from nothing to find out.

## The short answer

**The design works, and it would have been better to start this way. But don't replace `main` with this branch.** It has a fraction of `main`'s features. Bring the ideas across to `main` one at a time instead. Which of them, and when, is a decision for #489.

## What held up, with evidence

**A new ACP agent really can be one row.** Hermes was added as a 9-line definition in `integrations/agents.py`, with no other code change. Real Hermes then ran a reply, a permission request that was denied, a reconnect and its model list, through the same window and dialogs as everything else. `main` has about 3,850 lines of Copilot code; here the shared backend and both agent definitions are about 640.

That doesn't mean a new agent is free. Being listed gets it running, not trusted. Hermes shows "This version hasn't been checked" and carries a caution on Allow until someone exercises its approvals, cancel and reload. Its turns can also go to a paid API key, which this app can't check, so it stays off until you turn it on. The first real run also found that Hermes refuses a handshake without `clientInfo.version`, which `main`'s client doesn't send. A generic backend still has to meet each agent.

**The window can know nothing about providers.** `ui/main_window.py`, `ui/dialogs.py` and `service.py` contain no provider's name at all, and a test fails if one appears. `main`'s `ui/` has 1,045 lines that mention one.

**Claude and Codex fit without going through ACP.** They keep their own routes and their guards, and reach the window through the same contracts. Nothing in the contracts had to bend for them. The ACP adapters for Claude and Codex weren't tried, because their billing is the first thing to check (#489 step 3).

**One test suite does work across all of them.** `tests/test_contract.py` runs the same checks on ACP, Claude, Codex and plain chat. It found a real bug straight away: pressing Stop before a turn had finished starting did nothing, on some backends. Fixing that in the service fixed it for all of them.

**The drift counters are worth having at once (#466).** The first real run named things no one had noticed: Codex sending `thread/goal/cleared`, `turn/moderationMetadata` and `deprecationNotice`. Then Claude Code updated itself from 2.1.296 to 2.1.316 *while this was being written*. The next run said "this version hasn't been checked" and counted four new transcript record types (`api-request`, `api-request-blob`, `api-request-shape`, `tools-used`). That is #466's whole problem, noticed by the app without anyone reading a changelog.

## Sizes

These aren't comparable as they stand, because `main` does far more. They show roughly where the lines go.

| | `main` | This branch |
|---|---|---|
| App code | 47,124 lines | 4,666 lines, plus 5 accessibility and speech files carried over |
| Window and dialogs | 17,000 | 981 |
| Provider integrations | 11,158 | 2,148 |
| Tests | 50,293 | 2,180 |

## What it cost, and what it would still cost

The architecture was the easy part. Most of `main`'s size is years of behaviour: fixes for specific bugs, each with a test. A rewrite loses those unless each is found again. The review of this branch shows how: an independent review found 35 problems in about 4,700 new lines, including closing a stuck program hanging forever, a tightened permission mode being undone when a turn ended, and a permission request going unasked behind another dialog. Walking it by keyboard found more, such as Alt keys landing on the wrong control. `main` has already paid for that learning.

That's the main reason to port rather than switch.

## What isn't here

Not carried over at all: agent conversations, the formatted message view, attachments, backups and restore, export, search and filters, groups, hiding and deleting, sorting and columns, session links, Remote Control, Rewind, Branch and Continue Here, subagents, steering, prompts and slash-command insertion, the desktop app's sessions and live status, system voices and speech settings, sign-in help, updates and installers, the visual probe, and Orca's speech checks.

Copilot's check of each turn's log for billing to another provider isn't carried over either, so real Copilot's status here claims less than `main` can.

## What wasn't tested

- **How it sounds.** Everything here was checked by role, name and focus. Nobody has heard it with JAWS, NVDA, VoiceOver or Orca.
- **A real Mac or Linux desktop.** CI runs the window tests on both, but no one has used it there with a screen reader.
- **Real approvals being allowed.** The real AIs were asked and denied. Allowing was only exercised against fakes, on purpose.
- **The real AIs' question requests.** Only fakes have asked one.

## Recommended order for `main`

Each of these is useful alone, and none needs the others first:

1. **Drift counters** (#466). Small, and they start paying back immediately.
2. **The shared contract tests**, run against `main`'s existing adapters, which would show where they differ today.
3. **Neutral contracts for one dialog**, moving provider checks out of `ui/`. Permission requests first, because TCP's own labels and one-time choices are the safety rule that matters most.
4. **Pull the generic ACP backend out of the Copilot code**, then add Hermes as the second agent, with `main`'s Copilot tests as the gate.
5. **The local-only OpenAI-compatible adapter** for Ollama (#224). Paid hosted services stay a separate decision.

## Decisions this leaves for Kelly

- Whether to go ahead with this order.
- Whether to add any agent to `main` whose billing the app can't check, such as Hermes, and how it should be turned on.
- Paid API-key services: still outside the subscription-only scope of #184.
- Whether to keep this branch once the ideas have moved, or delete it.
