# Muse Code: detection and sign-in plan

Design and implementation note for Muse Code support; no issue number has been
assigned in this conversation. The first milestone is implemented in this
checkout; it has not been released. Native detection and read-only account status
have been validated against Muse 1.4.18; browser sign-in remains unverified.

## First milestone

A user can find out whether Muse Code is available, obtain installation
instructions if it is missing, and start and recheck sign-in if it is installed.
This milestone does not enable Muse conversations or run an installer from the
app. Session discovery, history, owned turns and permissions follow separately.

Implement each step by writing its tests first, observing the expected failure,
and then writing the code those tests exercise.

## 1. Verify the executable and authentication interface

Use an installed native Muse binary in a disposable workspace outside the repo.
Record its version, supported platforms, actual installation locations and
protocol/schema version. Check the documented account status and login APIs
against that build before choosing the implementation. Do not infer a
`muse login` command from the other providers.

The official user documentation describes starting `muse` for first-run browser
sign-in and using `/login` inside its interactive session. The protocol reference
also lists account methods; verify whether they provide browser sign-in and
status without starting a conversation. Prefer that path when supported, since
it can keep status and feedback in the accessible app interface.

Confirm how to distinguish missing credentials, an expired sign-in, a browser
session, a stored API key and an environment override. The documentation says
`META_API_KEY` takes precedence over a stored key, which takes precedence over a
browser session. Follow the existing app's subscription-based intent: never
report browser sign-in as ready when a different credential would be used, and
do not clear or rewrite the user's stored credentials. Document any status the
installed build cannot establish; unknown must not become signed out or ready.

Exit criterion: a recorded, repeatable detection/status/login/recheck sequence
that sends no model prompt and exposes no credentials.

## 2. Executable detection

Add discovery in `thechatplace/platform_paths.py`, following the existing native
Claude, Codex and Copilot finders. Use PATH and only installation locations
verified in step 1. Launch the resolved native executable without a shell.
Keep process launch, environment preparation and OS-specific behavior here.

Write tests for a usable PATH binary, a verified fallback location, no binary,
an unsupported wrapper, an unrelated executable named muse and a disappeared
file. Perform a bounded identity/version check off the UI thread before
trusting a PATH match. Distinguish missing, unusable and incompatible results.
Rechecking must detect a newly installed binary without restarting where the
current process can discover it; explain when a restart is needed for PATH.

Exit criterion: absence starts no Muse process, and an unrelated or unsupported
program is never launched as a Muse session host.

## 3. Missing-agent message and installation instructions

Add provider-specific status and text in a proposed `thechatplace/muse_signin.py`,
using the existing sign-in modules as the pattern. Suggested missing message:

> Muse Code wasn't found. Install Muse Code, then choose Muse Code Sign-in
> again. If it still isn't found, restart The Chat Place.

Show installation instructions with the official link in the same native OK
warning used by Copilot Sign-in, as requested after the first implementation.
The commands documented on 2026-11-11 are:

- Windows PowerShell: `irm https://dev.meta.ai/install.ps1 | iex`
- macOS and Linux: `curl -fsSL https://dev.meta.ai/install.sh | sh`

Tell users to verify installation with `muse --version`. Instructions are shown
to the user; displaying them executes nothing. An unusable or incompatible
installation gets an explanation appropriate to that state rather than the
unqualified claim that Muse is not installed.

Write tests for platform-specific instructions, missing/unusable/incompatible
messages, and the absence of installer or login launches when showing status.

## 4. Sign-in assistance

Add Tools, Sign In, Muse Code, available even when Muse is absent. Check status on
a worker and return through `wx.CallAfter`. Show the account/credential method
when the supported API can establish it. If sign-in is needed, offer a confirmed
browser sign-in through the verified interface from step 1. If only interactive
terminal sign-in works, explain the steps and validate that fallback with a
screen reader before relying on it.

Recheck when sign-in finishes, fails, is cancelled or times out. Keep the app
responsive, bound waits and stop callbacks reaching a closed frame. Return
focus to the invoking control and announce the outcome once. Missing binaries
show installation help rather than attempting sign-in. No background status
check should prompt for workspace trust or start inference.

Write tests for signed-in, signed-out, unknown, incompatible and overridden
credential states; accepting/declining sign-in; cancellation; launch failure;
timeout; successful recheck; duplicate launches; and closing during a check.
Use synthetic account responses and stub browser/terminal/process operations.
Never access a real user's credentials in tests.

## Validation and delivery

Start with focused platform/sign-in tests, then hidden-window tests using the
existing fake environment. Run the full suite before delivery. Add a visual
probe surface for any new dialog, run the affected surfaces in light and High
Contrast, and review the comparison. Walk the flow with real keyboard input
and validate names, status announcements and focus with NVDA/JAWS on Windows;
report automated and live screen-reader evidence separately. Apply the
repository's Mac validation and Linux Orca CI requirements to changed paths.

Update README behavior, any affected bundled guide text and the open release
notes when the feature is implemented. Keep controls for creating Muse chats
unavailable until a later milestone implements and verifies owned execution.

## Sources and local precedents

- [Muse Code installation and first run](https://dev.meta.ai/docs/muse-code)
- [Muse authentication and credential precedence](https://dev.meta.ai/docs/muse-code/auth)
- [Muse SDK and session protocol](https://meta-models.github.io/muse-code-sdk/1.4/)
- `thechatplace/platform_paths.py`: native discovery and terminal launching.
- `thechatplace/codex_signin.py` and `thechatplace/copilot_signin.py`: status,
  descriptions and login helpers.
- `thechatplace/ui/main_frame.py`: Help sign-in handlers and asynchronous rechecks.
- `tests/test_ui_hidden.py`: missing-Claude installer consent and recheck coverage.

Official documentation was first checked on 2026-11-11. Native detection and
read-only account status were subsequently verified with Muse Code 1.4.18
(1.4.18-R8587.1) on Windows on 2026-11-13. Browser authentication remains unverified.

## Implementation in this checkout

The branch incorporates upstream main at `99e2c50` (v0.6.0). Muse is registered
as `muse_sign_in` in the shared command list, under Tools, Sign In, Muse Code,
with its matching window handler. The command remains available without Muse
installed and participates in the upstream keyboard-customization system.
All status/error notices now use native OK dialogs with information or warning
icons and focus restoration, following the user's sign-in consistency request.
The interactive device-code dialog retains its browser and cancellation actions.
Earlier Help-menu screenshots are historical. After the merge, the user
completed manual testing and confirmed the updated Tools submenu flow works as
expected with NVDA 2026.3.1 (2026.3.1.58288) and JAWS 2027.2610.428
(Script Revision: 0). This is a user-confirmed runtime pass, separate from the
earlier screenshot measurements and automated tests.

Upstream integration verification: 185 focused Muse, menu, shortcut,
documentation and Orca-tooling tests passed. The full merged suite passed
with 6,726 passed, 61 skipped and one non-failing warning in a Claude test
double's cleanup. A read-only native check still recognizes Muse 1.4.18 and
reports `loggedOut`. The pre-merge work is retained in a recovery stash;
the restored feature changes remain uncommitted.

`platform_paths` discovers a native binary on PATH and the documented Unix
`~/.local/bin/muse` fallback. On Windows it follows the installed launcher's
`.muse-version` file to `muse-bin-<version>.exe`, without running CMD or
PowerShell wrappers. It also checks the verified `%LOCALAPPDATA%\Programs\muse`
installation directory when the desktop process has an older PATH. A bounded
`--version` check establishes Muse identity before opening the account host.
Instructions show the official installer commands; the app executes neither.

`muse_account.AccountConnection` owns an account-only `muse serve` process with
bounded NDJSON frames, queues and request waits. Its method allowlist excludes
session creation, prompts, tools and credential-writing API-key requests from
the UI flow. The handshake requires MSP envelope schema 1 and experimental API
support, followed by the required client `initialized` notification before
account requests. `muse_signin` validates account states and keeps one host alive during
device-code sign-in. The UI displays the verification URL and code before the
user explicitly opens a browser. Unknown account states stay unknown.

The account surface is experimental in the official reference. Native detection
and account status work with 1.4.18; no minimum Muse release is claimed.
Unsupported hosts produce update/recheck guidance. References used for this
small wire implementation include:

- [MSP framing](https://meta-models.github.io/muse-code-sdk/next/guides/msp-wire/framing-and-caps/)
- [Account status](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/accountstate/)
- [Device-code result](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/accountloginstartresult/)
- [Login outcomes](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/accountloginoutcome/)
- [Unix launcher location](https://meta-models.github.io/muse-code-sdk/next/guides/quickstart/)

Tests use synthetic account messages and a synthetic subprocess; they never use
a real account. Packaged smoke testing imports both new backend modules without
starting Muse. The user subsequently confirmed that JAWS 2027.2610.428
(Script Revision: 0) and NVDA 2026.3.1 (2026.3.1.58288) testing passes for the
Help-menu and missing-Muse dialog flow, alongside the supplied
light-theme screenshots. Live authentication, Windows VM keyboard walkthrough,
High Contrast pictures, Mac testing and Linux Orca CI remain separate validation work. See
[the validation guide](../testing/muse-code-onboarding.md).

Automated verification on 2026-11-12:

- Full regression suite: 6,535 passed, 61 skipped. One non-failing warning came
  from a Claude test double's file cleanup.
- Final focused backend, hidden-window UI, documentation, smoke-test and Orca
  tooling checks: 108 passed.
- Final Muse dialog, probe registration, hidden layout and trigger checks:
  21 passed.
- Accessibility source review: no remaining blockers. This is not live
  screen-reader evidence.

Verification on 2026-11-13 after installing Muse Code:

- 50 focused Muse detection, transport, status and UI tests passed.
- The installed Windows launcher uses `.muse-version` to select the active
  native binary. Detection now follows that metadata, rejects unsafe version
  names and never selects another version by filename sorting.
- The real host rejected `account/read` before `initialized` with error -32600.
  The corrected handshake succeeds and reports `loggedOut` with a credential
  required. No sign-in flow, session or model prompt was started.
