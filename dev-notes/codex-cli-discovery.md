# Codex CLI discovery (#277)

Finder launches have only the system PATH. Discover native CLIs without running
shell shims or changing the environment: PATH first, then named install locations.
The Mac ChatGPT bundle exists on this development computer at
`/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`.
The same bundle under the user's Applications folder is supported.

Native npm payload locations follow [upstream's launcher](https://github.com/openai/codex/blob/main/codex-cli/bin/codex.js),
including both its current `bin/codex` layout and earlier `codex/codex` layout.
Only the native executable is opened; JavaScript and cmd/bat shims are refused.

The Windows 11 ARM Parallels VM was checked on October 16, 2026 through guest
PowerShell. `Get-AppxPackage -AllUsers` and WindowsApps directory inspection found
no ChatGPT, OpenAI or Codex package. No user's OpenAI/Codex/bin or
Programs/OpenAI/Codex/bin install was found either. This does not establish a
working installed Windows bundle. Candidate paths cover the standalone CLI and
the Store app's relocated native binary, with synthetic tests. The relocation
layout is recorded in [upstream's Windows report](https://github.com/openai/codex/issues/25221).
A real signed-in Windows installation remains a release acceptance check.

Settings persist null/missing as automatic, true as explicit on, false as explicit
off. Effective discovery also requires a native CLI, so a missing installation
never starts an app-server. Saving other settings preserves automatic mode. A
checkbox change records the explicit choice even if it is toggled back.

The changed Settings surface has probes for installed and missing Codex. The Mac
capture attempt returned a blank picture; it is not visual acceptance. Windows
light-100 and High Contrast pictures/comparison/contrast remain under #226.
