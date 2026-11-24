# Codex browser, computer use and Cloud (#524)

Checked November 23, 2026, on Windows arm64 with installed **codex-cli 0.163.0**.
Compared Claude's integration in `git show c0a0c901` (PR #706).

## Finding

**No supported way was found to attach the user's real browser, the desktop
browser extension or the built-in desktop browser to a third-party Codex
app-server session.** This is a conclusion from the documented interfaces and
installed protocol, not a claim that Codex desktop lacks these features. Do not
reverse engineer desktop sockets, plugin internals or private Cloud endpoints.
Recheck this conclusion against future documented releases before implementing it.

The Chat Place therefore adds no Codex browser switch or fictitious Connected
Tools server. The Claude-only setting keeps its name. Explicitly configured and
selected MCP servers remain a different integration, with existing consent and
copy behavior. Nothing here enables a server or changes browser permissions.

## Primary sources

- [Browser](https://learn.chatgpt.com/docs/browser?surface=app): the built-in
  browser belongs to the desktop app, uses a separate profile, and is unavailable
  in CLI and the IDE extension. Cloud-task browser access runs in that task,
  rather than giving a local session access to the user's browser.
- [Browser extension](https://learn.chatgpt.com/docs/chrome-extension): setup
  starts in desktop Settings, Computer Use, installs the extension into the
  chosen browser profile, then selects the browser in a desktop chat. Site
  requests identify the host and offer one-time, site and global grants or
  decline. These are desktop controls; the page documents no app-server bridge.
  The page now uses ChatGPT desktop branding and includes Codex chats.
- [Computer Use](https://learn.chatgpt.com/docs/computer-use): the native GUI
  integration is documented for the desktop app on macOS and Windows, subject to
  region/workspace availability. It requires app consent; macOS also requires
  Screen Recording and Accessibility access. Windows uses the active desktop.
  This isn't authorization to operate Kelly's desktop during a test.
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference):
  `features.browser_use`, `browser_use_external`, `browser_use_full_cdp_access`,
  `computer_use` and `in_app_browser` include managed restrictions. A true flag
  or a browser policy table does not document a host connection mechanism or
  grant product/account availability.
- [Codex app-server](https://learn.chatgpt.com/docs/app-server): documents local
  JSON-RPC lifecycle, authentication, streamed events and approvals, and explains
  how to generate version-matched schemas. No browser attachment workflow is
  documented. Authentication supports both ChatGPT and API keys; neither is a
  browser host interface. `thread/resume` restores a local thread, not desktop
  UI services.
- [Changelog](https://learn.chatgpt.com/docs/changelog): the 26.820 browser entry
  directs extension setup to desktop Settings, Computer Use. CLI 0.163.0
  (October 15) records resume permission fixes and other CLI changes, but no
  third-party browser bridge. Desktop browser announcements are not evidence
  that the stdio integration can use them.
- [OpenAI's versioned source tree](https://github.com/openai/codex/tree/rust-v0.163.0/codex-rs):
  the browser/computer-use files exposed in the public protocol/config crates
  describe policy. The tag's public tree was inspected using GitHub's API;
  no private endpoint was contacted.

### Sign-in, plan and API-credit boundary

The documented browser setup requires the desktop product and its browser
installation/setup, with account/workspace rollout restrictions. These sources
do not establish a subscription tier that enables the same feature in a
third-party app-server client. No API key, ChatGPT plan or existing desktop
sign-in can be recommended as a way to enable it here. We did not purchase a
plan, change sign-in, install an extension or test API-funded browser use.
An API-credit Codex chat receives the same accurate host limitation as a
subscription chat. This differs from Claude's supported `--chrome` switch and
its explicit subscription-only extension integration.

### Permissions and resumed threads

Desktop site approval UI is documented, but no supported browser-site approval
RPC or desktop attachment/resume contract was found for app-server. Existing
command, file, permission and MCP approval RPCs must not be repurposed as a
guessed browser protocol. A separately enabled MCP tool can use the existing
MCP approval/elicitation paths; that doesn't give it the desktop browser.

Accordingly, desktop browser operation on a resumed third-party thread is
**unverified and unsupported by the interfaces found**, not promised. The new
capability note was verified on a real resumed thread, as described below.

## Exact inspection commands

Run in PowerShell; keep generated artifacts outside the checkout:

```powershell
git show c0a0c901
codex --version
codex --help
codex app-server --help
codex features list
codex app-server generate-json-schema --help
codex app-server generate-json-schema --experimental --out "$env:TEMP/codex-browser-524/schema"
gh api 'repos/openai/codex/git/trees/rust-v0.163.0?recursive=1'
```

The npm PowerShell launcher intermittently failed with a denied Windows pipe;
the schema generation succeeded by invoking the installed native executable:

```powershell
$codexBinary = 'C:/Users/kelly/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-arm64/vendor/aarch64-pc-windows-msvc/bin/codex.exe'
& $codexBinary --version
& $codexBinary app-server generate-json-schema --experimental --out "$env:TEMP/codex-browser-524/schema"
```

Observed:

- Version was `codex-cli 0.163.0`. CLI/app-server help has no counterpart to
  Claude's `--chrome`; `--search` describes web search, not browser control.
- `features list` reports `browser_use`, `browser_use_external`,
  `browser_use_full_cdp_access`, `computer_use` and `in_app_browser` as stable
  and true. Their presence is not sufficient evidence of a usable browser.
- Generated `ClientRequest.json` and `ServerRequest.json` contain no browser
  attachment or browser-site approval method. The browser occurrence in client
  initialization describes gateway OAuth authorization, not page automation.
- `ThreadStartParams`, `ThreadResumeParams` and `TurnStartParams` expose no
  browser connection parameter. Generic config, environments and dynamic tool
  fields do not document a connection to the installed desktop browser.
- The combined v2 schema has `BrowserUseConfig`, `BrowserUseRequirements`,
  origin-policy types, `InAppBrowserRequirements` and `ComputerUseConfig`.
  These describe policy, not an extension transport or executable browser tool.
  Inspection included experimental fields; absence wasn't inferred just from
  the default stable-only schema.

## Implementation and verification

`codex_execution.instruction_context` adds a distinct application context entry,
`tcp_capabilities`, on **every normal `turn/start`**. Codex's
`AdditionalContextStore` inserts only changed entries into model context, so the
unchanged note reaches the model once per app-server session per thread,
including reopened chats and copies. It explains the desktop
browser/extension, native computer use and Cloud limits, says no local Codex
setting enables them, and distinguishes Claude's Chrome setting and explicitly
enabled MCP tools. Editable chat instructions keep their separate entry. The
wire guard requires the exact context before dispatch, so callers cannot omit
or alter the host note. The note scopes these limits to The Chat Place running
the chat and says they may not apply to later messages from another app, since
the developer message stays in thread history. The ephemeral OpenAI key test
omits the context, and its wire guard requires that omission; Ask Aside retains
the note. This works without migrating old saved chat records;
new chats, copies, resumed chats, Plan and API-credit turns use the same path.
Steering continues the already instructed active turn and doesn't start another.
This is model guidance, not a guarantee against an incorrect model reply.

### Live observation

Using the repository's actual `CodexExecutionConnection`, `CodexOwnedThreads`
and `CodexTurnExecutor`, a disposable owned chat was created in
`C:/Users/kelly/OneDrive/testing/scratch/524/browser-live`, read-only with approval
policy `never`. No tools or windows were requested. Existing ChatGPT sign-in
was confirmed by account **type only**, without recording email or tokens.
Codex selected `gpt-6.1-sol`.

The synthetic prompt asked whether the chat could control existing Chrome
tabs, use the desktop browser/computer or start Cloud work, whether a setting
enabled this, and how available web search/MCP tools differ. The first turn
completed and correctly explained all four limits and the lack of a setting.
After closing the connection, starting a fresh app-server and calling
`thread/resume` with that chat's record, the second turn also completed with
the same correct explanation. Both identified web search as separate. No
permission request arrived. This proves the note reaches new/resumed real
model turns; it does **not** prove actual browser operation or permissions.

Windowless regressions cover missing/altered host context, absent/empty/custom
chat instructions, consecutive turns, subscription/API routes and resumed/copied
lifecycle records. Local pytest initially couldn't access its temporary folders
inside the sandbox; windowless runs were then allowed outside that boundary.
The guide is regenerated with `python tools/make_docs.py`. CI runs the full
platform and accessibility suites; its result belongs in the PR.

The final focused local run passed **285 tests**:

```powershell
C:/Users/kelly/GitHub/AIChat/.venv/Scripts/python -m pytest -q tests/test_codex_browser.py tests/test_codex_execution.py tests/test_codex_parity.py tests/test_codex_copies.py tests/test_codex_api.py tests/test_codex_auto_review.py tests/test_codex_runtime.py tests/test_docs.py
```

A separate reviewer agent checked the implementation, tests and documentation
and reported no actionable findings. No wx window tests ran on this PC.

The PR #708 review fixes add real `CodexApiConnection` wire coverage for the
normal API turn's `tcp_capabilities` and the key test's absent
`additionalContext`, including rejection if context is added to a key test.
An ephemeral aside regression keeps its capability note. The focused windowless
run after these fixes passed **133 tests** (`test_codex_browser.py`,
`test_codex_api.py`, `test_codex_execution.py` and `test_docs.py`). Only
`docs/user-guide.html` and `docs/user-guide.md` changed among generated docs.

### Remaining listening check

With JAWS or NVDA (VoiceOver on macOS, Orca on Linux), send "Can you use my
Chrome tabs, and which setting enables it?" in an owned Codex chat, then close
and reopen it and repeat. Expect a plain explanation that the desktop browser
is unavailable here and no setting enables it for Codex. Listen for the normal
reply announcement; there is no new dialog, focus step or browser permission.
An enabled MCP browser may be offered separately if one is actually available.
Human screen-reader listening and real desktop browser permission handling were
not performed in this investigation.
