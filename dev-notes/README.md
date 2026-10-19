# Development notes

Working documents for developing The Chat Place: research, investigations and design notes
written while working on an issue, before or alongside the code. They record what was found and
the choices weighed, so the reasoning isn't lost once the issue is closed.

These are for people working on the app, not for its users. The user guide and the published
pages are in `docs/`, which GitHub Pages serves; this folder isn't published there. Guides for
testing a change by hand are in `testing/`.

Name each file for its subject, and start it with the issue it was written for. A note may go out
of date once the work is done: the code, the README and the release notes are the record of what
shipped.

| Note | Issue |
|---|---|
| [speech-routing.md](speech-routing.md): how The Chat Place chooses what speaks, why NVDA users hear a Windows voice, and the options for fixing it | #98 |

- [Provider integration review slices](provider-integration-slices.md): release scope, layer boundaries and review gates (#184, #171, #225).

- [Owned Codex chats](codex-owned-chats.md): execution, policy and review evidence (#184, #171, #225).

- [Codex saved command rules](codex-command-rules.md): native boundary reproduction, conservative refusal candidate and release decision (#257).

- [Codex CLI discovery](codex-cli-discovery.md): Finder launches, native npm/bundle lookup, three-state settings and Windows evidence (#277).

- [Codex live task lists](codex-live-tasks.md): installed history checks, event routing, saved snapshots and visual review (#318).

- [Chat Permissions](chat-permissions.md): Codex backend contract and Claude's shared UI integration checklist (#307).
