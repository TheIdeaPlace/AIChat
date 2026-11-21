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
| [codex-more-models-review.md](codex-more-models-review.md): review fixes, model sources and native service-tier blocker | #685 |
| [codex-feature-gaps.md](codex-feature-gaps.md): shared workflows, native boundaries and remaining work | #467, #468 |
| [codex-auto-review.md](codex-auto-review.md): per-chat reviewer verification, independent review fixes and validation limits | #358, #374 |
| [steering.md](steering.md): what `claude -p` does with a message mid-turn, how `--replay-user-messages` tells the cases apart, and Codex's `turn/steer` | #238, #239 |
| [sound-design.md](sound-design.md): the rules for The Chat Place's sounds, the families, and every event's sound and notes | #250 |
| [speech-routing.md](speech-routing.md): how The Chat Place chooses what speaks, why NVDA users hear a Windows voice, and the options for fixing it | #98 |
| [linux-port.md](linux-port.md): Linux and Orca: naming controls through ATK, reading message rows, which way announcements reach Orca, the formatted page, and what the AppImage keeps of the system's libraries | Linux port |

- [Provider integration review slices](provider-integration-slices.md): release scope, layer boundaries and review gates (#184, #171, #225).

- [Owned Codex chats](codex-owned-chats.md): execution, policy and review evidence (#184, #171, #225).

- [Codex saved command rules](codex-command-rules.md): native boundary reproduction, conservative refusal candidate and release decision (#257).

- [Codex CLI discovery](codex-cli-discovery.md): Finder launches, native npm/bundle lookup, three-state settings and Windows evidence (#277).

- [Codex live task lists](codex-live-tasks.md): installed history checks, event routing, saved snapshots and visual review (#318).

- [TCP-owned remembered approvals](codex-remembered-approvals.md): consent authority, matching and revocation for #307.

- [GitHub Copilot CLI probe](copilot-cli-probe.md): what Copilot CLI's JSON output, saved sessions and ACP permission requests look like, and how the app could use them (#352).

- [Muse Code onboarding plan](muse-code-onboarding.md): first milestone for executable detection, missing-agent guidance, installation instructions and sign-in assistance (issue not yet assigned).

- [Muse Code history plan and implementation](muse-code-history.md): second milestone for session discovery and read-only conversation history, with validation limits (issue not yet assigned).

- [Muse Code execution plan](muse-code-execution.md): third milestone for owned chat creation, turn streaming and native permissions (issue not yet assigned).

- [Muse Code Chat menu plan](muse-code-chat-menu.md): making every Chat menu command either work for a Muse Code chat or say why not, starting with a sweep test (issue not yet assigned).

- [The connect-agent skill](../.claude/skills/connect-agent/SKILL.md): the four Muse Code notes above, turned into a reusable process for connecting the next AI agent (issue not yet assigned). It's a Claude Code skill, so an assistant working in this repository picks it up when asked to add an agent, and people can follow it directly. It covers the same milestones in order (detection and sign-in, read-only history, chats of the app's own with native permissions, then every menu command) and the rule behind all of them: check each feature against the installed agent before building it, because the Muse Code schema promised things the installed build refused. Alongside the guide are what went wrong with Muse Code and how each was fixed ([lessons](../.claude/skills/connect-agent/references/lessons.md)), which files each milestone touches ([codebase map](../.claude/skills/connect-agent/references/codebase-map.md)), and three scripts to copy: a check of the methods and fields the app sends against the agent's schema, a harness for checking the real agent with made-up content, and the Chat menu sweep test.

- [User guide review](user-guide-review.md): agreed corrections and improvements to the user guide, and decisions for Kelly, from a Claude and Codex review against 0.2.0.

- [Backup history reader](backup-reader-436.md): the #436 UI contract and isolated Codex recovery.
- [Approval noise post-mortem](approval-noise-postmortem.md): how automatic approval review buried the chat, the cross-project changes (#566), and the fix (#564).
