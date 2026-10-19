# Native Codex scoped execution investigation

Issue #307; backend PR #330; Claude UI handoff #329. Investigated 2026-10-19
against installed and npm-pinned **Codex 0.162.1**.

The user goal is fewer repeated approvals in real folder-editing chats. Recognizing
a synthetic approval in tests does not establish that native Codex can execute it.
This investigation adds no production feature flags or changes to user settings.

## Reproducible evidence

`tools/probe_codex_scoped_permissions.py` drives real app-server against a local
synthetic Responses server. Each scenario has an isolated `CODEX_HOME`, synthetic
Git repository, and no credentials. Full escalations are canceled. Only bounded
experimental per-command requests are accepted with a one-time `accept`.
Named profiles are defined only inside that temporary home. HTTP network controls
use the public documentation domains example.com and example.org, without data
or account credentials. No real repository is committed or pushed by the probe.

The workflow `.github/workflows/probe-codex-scoped-permissions.yml` runs macOS and
Windows elevated/unelevated comparisons. A green job means the evidence was
collected; inspect command exit status, the actual `.git/HEAD`, and sentinel files
to decide viability. Git can print "Switched to a new branch" even when updating
HEAD failed, so that message alone is insufficient.

The initial [native enforcement run](https://github.com/TheIdeaPlace/AIChat/actions/runs/39306638883)
at `8e1ccf8` establishes:

| Platform | Default Git write | Accepted `.git`-only write | Outside write with that grant | New-session Git write |
|---|---|---|---|---|
| macOS Seatbelt | Blocked | Succeeded; HEAD changed | Blocked; no sentinel | Blocked |
| Windows unelevated | Blocked | Still blocked at HEAD.lock | Blocked; no sentinel | Blocked |
| Windows elevated | Directory-access error | Directory-access error | Blocked; no sentinel | Blocked |

Windows Python temporary directories initially had an owner-only ACL that prevented
restricted-token traversal and caused PowerShell to start in its installation
directory. Giving **only the synthetic tree** Everyone read/traverse access fixed
the local unelevated control. It did not make the approved `.git` write work.
Both local Windows PowerShell 5 and hosted PowerShell 7 reproduced the write denial.
The elevated result needs explicit-path controls before attributing it to Git protection.

The [profile/network run](https://github.com/TheIdeaPlace/AIChat/actions/runs/39312523006)
at `95bde2e` further establishes:

- Mac named `.git`-write profile: Git succeeds; outside writes remain blocked.
  Selecting the baseline in a new native session restores the Git write denial.
- Windows elevated **and** unelevated: selecting the named `.git`-write profile
  still fails with `HEAD.lock: Permission denied`; HEAD remains unchanged.
- Mac managed networking: example.com succeeds, explicitly denied example.org
  is rejected by policy. An attempted direct connection with curl's proxy bypass
  fails DNS resolution. This is a useful negative control, not exhaustive proof
  against direct-IP, SSH, or other transport bypasses.
- Windows unelevated: every managed-network control fails before launch with
  `managed networking requires the elevated Windows sandbox backend`.
- Windows elevated: the explicit host deny works and a direct-proxy-bypass
  attempt cannot connect. The allowed HTTPS request reaches the proxy but fails
  with Schannel `SEC_E_NO_CREDENTIALS`; HTTPS Git/authentication remains unproven.

These tests establish a native Mac path worth implementing, and a reproducible
Windows compatibility blocker. They do not establish production automatic reuse,
same-session revocation, or the full authenticated Git workflow on either platform.

The [final control run](https://github.com/TheIdeaPlace/AIChat/actions/runs/39313197496)
at `b7bc325` closes the directory-error ambiguity: even `git -C <exact-test-repo>`
fails at `HEAD.lock` in **both** Windows modes, with either an inline grant or the
explicit named-profile override. A scoped commit fails at `index.lock`. These
diagnostic commands change no matcher policy. On Mac, the scoped commit succeeds
while its pre-commit hook's attempted write to the sibling sentinel is denied;
the sentinel does not exist afterward. This directly tests the hook risk raised
in review without granting an unsandboxed command.

## Native mechanisms and limits

`codex features list` marks `exec_permission_approvals` and
`request_permissions_tool` **under development**, false by default. The probe
enables them only in its temporary configuration. Without the first flag, the
ordinary command tool does not expose the scoped execution argument being tested.

With it enabled, the real `exec_command` tool offers
`sandbox_permissions: "with_additional_permissions"` and
`additional_permissions.file_system.write: [absolute_path]`. The real app-server
then sends `item/commandExecution/requestApproval` with a camel-case
`additionalPermissions.fileSystem` profile. PR #330 recognizes the simple Git
request. Approval preserves the sandbox on the verified Mac path.

`request_permissions` is a different API: its granted filesystem/network access
applies automatically to later shell commands for the turn or session. It is not
an executable-prefix rule. Its response schema offers only `turn` and `session`
scope. Do not substitute a native session grant for TCP's removable command rule:
stopping TCP automatic answers alone would not retract that native grant. A fresh
native session/profile reset and enforcement proof would be needed.

The [official app-server contract](https://learn.chatgpt.com/docs/app-server#approvals)
documents both APIs. The generated schema from the installed binary additionally
exposes `thread/start.permissions`, `turn/start.permissions`, and
`thread/settings/update.permissions` for named profiles. Selection cannot be
combined with the corresponding legacy `sandbox`/`sandboxPolicy` field.

[Permission profiles](https://learn.chatgpt.com/docs/permissions) can extend
`:workspace` and explicitly override its `.git` read-only entry. They describe
filesystem capabilities for all commands while selected, rather than permission
to execute one Git verb. The follow-up probe compares an explicit `.git` override
with the baseline profile, and checks an explicit Git working-directory argument
solely as a diagnostic. That argument remains ineligible for PR #330 matching.

The inline additional network schema exposes a boolean, not a destination
allowlist. A host in UI metadata does not make `network.enabled: true` a
host-restricted capability. The official profile documentation requires an active
`features.network_proxy` as well as profile domain rules; without the proxy,
network-enabled commands can connect directly. The follow-up includes allowed-host,
denied-host, and direct-proxy-bypass controls. Do not approve broad inline network
profiles automatically. Native Windows unelevated networking has documented
limitations; it cannot be treated as equivalent to Seatbelt or elevated isolation.

## Implementation decision and work split

1. **Codex owns native execution and capability detection.** Establish a supported
   minimum version/mode, enable scoped tooling through per-process or per-thread
   overrides, and verify effective settings without writing user configuration.
   Prefer per-command bounded grants and TCP-owned one-time answers. Never
   silently fall back to remembered unsandboxed execution.
2. **Codex owns the Windows blocker.** Find a native sandbox mode that proves the
   positive Git operation and negative outside-write/hook controls, or produce a
   minimal upstream reproduction. An API accepting a profile is insufficient.
   A separate execution broker would require a new design and review; prefix
   parsing alone does not safely sandbox Git hooks or repository configuration.
3. **Codex owns managed networking.** Prove the Git transport uses an enforced
   per-host proxy, denial cannot be bypassed, host/port/protocol changes ask again,
   and removal takes effect. HTTPS Git and SSH must be evaluated separately.
   The existing `git push` allowlist does not establish any of these properties.
4. **Claude owns #329's UI and accessible lifecycle.** Retain the backend contract,
   offer Allow for This Chat only for a real candidate, and explain unsupported
   full escalations without promising that common Git requests are remembered.
   Any new capability/profile UI must describe the actual scope and duration.
   New backend shapes require a contract update before UI implementation.
5. **Completion requires the real workflow.** On Windows and Mac, create a branch,
   stage and commit twice, and push to the intended remote; approve an eligible
   class once and observe no repeated prompt within that same scope. Then remove
   the rule and observe the next request, including in a running turn, ask again.
   Restart, Continue Here, permission-mode change, hostile hook/configuration,
   different repository/host and unsupported-mode controls must retain their
   stated boundaries. Include screen-reader announcements and persisted history.

PR #330 and #329 remain partial contributions. Keep #307 open until these native
acceptance checks establish the user's repeated-approval goal.
