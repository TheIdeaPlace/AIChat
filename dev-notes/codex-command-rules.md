# Codex saved command rules (#257)

The selected-folder grant cannot promise approval for additional access while
inherited allow rules remain active. Official documentation says allow rules
can run commands outside the sandbox without prompting. A native Mac test on
CLI 0.162.0-alpha.17.2 reproduced this with a disposable trusted project and a
rule for one harmless script: it wrote a marker in the disposable parent folder
with zero approval requests. The matching control without an allow rule did
not create the marker. Both chats were officially archived/deleted and synthetic
trees removed. No existing rule or global configuration was modified.

Sources:
- [Official rules behavior](https://learn.chatgpt.com/docs/agent-configuration/rules)
- [CLI reference: exec-only ignore-rules](https://learn.chatgpt.com/docs/cli/reference)
- [Rule loading](https://github.com/openai/codex/blob/main/codex-rs/core/src/exec_policy.rs)
- [Layer folder mapping](https://github.com/openai/codex/blob/main/codex-rs/config/src/state.rs)
- [Managed restrictive rules](https://github.com/openai/codex/blob/main/codex-rs/config/src/requirements_exec_policy.rs)

The installed app-server rejects --ignore-rules; no supported equivalent was
found in current official app-server arguments or session configuration. Do not
invent a config key, change CODEX_HOME, copy authentication, or alter user rules
to make the test pass. Upstream main corroborates this version's behavior but
does not establish compatibility across CLI versions.

## Separate refusal candidate

The candidate uses config/read with includeLayers and the exact selected cwd.
It checks active system/user file parents and project dotCodexFolder/rules,
matching upstream loading. Disabled known layers are skipped. Unknown sources,
missing/malformed inventories, nonabsolute/network paths, unreadable directories,
symlinked rules directories and bounded-scan overflow refuse the action. Any
.rules entry is refused, without parsing Starlark; even prompt-only/deny-only
files block new chats. The scan permits at most 64 layers and 4,096 directory entries.
Checks run before lifecycle, after lifecycle before granting a runtime proof,
and before each turn. All OS/filesystem work remains in platform_paths.

Independent source/code review found no blocking implementation defect and
passed 234 focused tests. Added requested coverage for a symlinked rules folder,
all known sources without rule folders, and rules appearing after ownership
before an actual inference request can consume an ID; 134 focused tests pass.

The guarded native reproduction is refused before a prompt is sent. The current
user's active global layer contains one rule file, so even a clean temporary
folder's lifecycle is refused. That compatibility cost must be explicit; no
claim of a successful native rule-free lifecycle is made on this machine.

This is a filesystem snapshot refusal, not upstream runtime isolation. It cannot
retroactively bless old ungated proofs or remove all races involving changes
during a turn. Keep #257 open pending review of the product tradeoff, supported
CLI-version behavior and Windows/native acceptance. The separate branch does
not change PR #255's reviewed head or its CI candidate.

Full native Mac validation: **2,021 passed, 88 skipped**. The separate branch
will receive Windows/Mac workflow checks before any merge request is opened.
