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

## Revised rule-source policy

Kelly's decision, relayed in Claude mailbox #30 on October 15: retain user and
system rule files, disclose their effect, and refuse project-supplied rules.
The revised inventory counts direct user/system `.rules` entries without reading
or publishing their contents. Project entries are refused with their folder and
recovery guidance before lifecycle, after lifecycle and before every turn.
Unknown/malformed/unreadable inventories, network paths, symlinked rule directories
and bounded-scan overflow remain fail-closed. Disabled known layers are skipped.
The scan permits at most 64 layers and 4,096 directory entries.

New Session reads the selected folder's inventory on a worker and ignores stale
callbacks. Change Permission Mode always displays the saved-rule exception.
Properties reads a current count on a worker; unavailable inventory stays unknown.
No authentication, configuration or rule file is modified. No persistent approval
amendment is granted. Network, write-root and ChatGPT billing checks are retained.

This is a filesystem snapshot check, not upstream runtime isolation. Rule changes
during an active turn can race the snapshot. The policy deliberately respects
user/system rules, including always-allow commands outside the selected folder.
Project rules remain forbidden. Native Windows/Mac acceptance and the #226 visual
matrix remain release gates; automated checks alone do not close #257.
