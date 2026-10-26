# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The Chat Place: a wxPython desktop app for Windows, macOS and Linux (Windows came first) that is a keyboard- and screen-reader-friendly
home for AI sessions in one list, with Claude and Codex now and more providers planned. It shows
arrowable conversations, announces replies and drives sessions of its own through provider
adapters. Claude uses `claude -p`; Codex uses app-server with ChatGPT sign-in. Neither uses an
API key. Shared UI and guide text must be neutral or name the provider. Provider is which AI;
Kind is where the session started. Preserve legacy Claude storage/defaults only at its adapter
and compatibility boundaries. Accessibility with
JAWS and NVDA (VoiceOver on a Mac, Orca on Linux) is the point of the app, so every UI change has
to work by keyboard and be named and announced for a screen reader.

The README is the user-facing spec. It is long and detailed, and it lists the shortcuts, file
locations and behaviour. Keep it in step with behaviour changes.

## Commands

```
./macsetup.sh  |  winsetup.bat  |  ./linuxsetup.sh   # make .venv from requirements-build.txt (replaces an existing one)
.venv/bin/python -m thechatplace                   # run from source (Windows: .venv\Scripts\pythonw TheChatPlace.pyw)
.venv/bin/python -m pytest -q tests                # what CI runs, on Windows and macOS
.venv/bin/python -m pytest tests/test_claude_cli.py -k resume   # one file / one test
BuildAndRelease/MacBuilds/build_macos.sh           # tests, app, Velopack osx feed, .dmg; signed if the keychain has a Developer ID
BuildAndRelease\WinBuilds\build_windows.cmd         # Windows: tests, app, Velopack installer and feed (always unsigned)
BuildAndRelease/LinuxBuilds/build_linux.sh         # Linux: tests, app, Velopack AppImage and linux-<arch> feed (unsigned)
python tools/check_version.py [vTAG]               # print version / check a tag against it
powershell -File tools\ui_probe_vm.ps1 [-Variant light-100] [-Surface settings]  # visual probe, in the test VM (vmtest begin first)
.venv/bin/python tools/ui_compare.py RUN_FOLDER [--accept]   # what changed against testing/visual/baseline/windows
```

- Tests of Windows-only behaviour (made-up `C:\` paths, MSAA via `wx.Accessible`) are marked with
  `windows_paths` / `msaa` / `windows_layout` from `tests/markers.py` and skip elsewhere; ATK names
  (what Orca reads) with `atk`, which runs on Linux only. Use them for new tests of that kind
  rather than letting another platform's CI job fail.
- On Linux, `.venv` uses the distribution's wxPython (`--system-site-packages`; PyPI has no Linux
  wheels), and the window tests need a display: `xvfb-run -a -s "-screen 0 1920x1080x24"` in CI.
- PyInstaller is pinned in `requirements-build.txt`, and `vpk` must match the `velopack` version in
  `requirements.txt` (1.2.161) everywhere it's installed. The PyInstaller command lines live in
  four places that must agree: `build_windows.cmd`, `build_macos.sh`, `build_linux.sh`, and the
  Windows job of `release-thechatplace.yml` (the Mac and Linux jobs call their scripts).
- The built app supports `--smoke-test out.json`. It imports everything and checks the data files
  (each platform's speech scripts, `speech.engine_script_names()`; `WebView2Loader.dll` on Windows;
  ATK on Linux). A new lazily imported module or data file needs adding to those command lines.
- Linux build: after PyInstaller, `build_linux.sh` deletes every bundled library except Python's,
  wxWidgets' and the image ones distributions name differently, so the app runs on the system's
  GTK, accessibility bridge and WebKitGTK (what Orca reads). The build machine is then the oldest
  Linux it runs on (Ubuntu 24.04 in CI). Never keep a library every distribution names alike
  (OpenSSL broke Debian 13's libcurl). The AppImage needs FUSE 2 to run; the build smoke-tests it
  with `APPIMAGE_EXTRACT_AND_RUN=1` and requires `"updater": "<version>"`.
- Mac build (follows GHManage): sign inside-out with `sign.sh` (never `codesign --deep`), then
  `vpk pack --signDisableDeep` adds `Contents/MacOS/UpdateMac` and signs it and the bundle; the
  `.dmg` is made from the app in `TheChatPlace-osx-Portable.zip` (named "The Chat Place.app"),
  never from `dist/`, and the build fails unless the packed app's smoke test reports
  `"updater": "<version>"`. `TCP_SIGN_CODE=0|1` (default: sign if a certificate exists),
  `TCP_NOTARIZE=1`. `TheChatPlace.entitlements` (vpk insists on the extension) includes Apple
  Events, which VoiceOver speech (`osascript`) needs under the hardened runtime.
- Signing secrets come from `~/Documents/GitHub/The-Idea-Place-Projects/signing` (`repos.json`,
  `sync-secrets.py`), not by hand.

## Releasing

The version lives in one place: `__version__` in `thechatplace/__init__.py`. To release, bump it and
add `release-notes/v<version>.md` in the same commit, then push the tag `v<version>`. The release
workflow fails if the tag and `__version__` disagree, or if the notes file is missing. It builds
Windows, macOS and Linux (x64 and arm64) in parallel jobs, and a `publish` job makes the release only
if all succeed (the Windows installer, portable zip and `windows` feed; the notarized `.dmg` and
`osx` feed; the two AppImages and `linux-x64` / `linux-arm64` feeds).
Versions below 1.0 publish as pre-releases. Both platforms update through Velopack
(`updater.CHANNEL`). Velopack calls every Mac app portable, so the portable check is Windows-only
(`updater.PORTABLE_COPIES_EXIST`); applying it on a Mac silently stops all Mac updates.

Only Kelly can release or sign: tags are admin-only by ruleset, releases are immutable, and every
signing run (a tag, a hand run with "sign", a `sign-build` pull request) waits for his approval of
the `azure-signing` environment, where the signing secrets belong. After his "go", approve only the
tag run he pushed, never a run on someone else's branch (it runs that branch's workflow with the
secrets), then watch it. The run takes a few seconds to appear after the push; wait until
`gh run list` shows it waiting, or `id` comes back empty:

```
id=$(gh run list -R TheIdeaPlace/AIChat -w "Release The Chat Place" -b v<version> --json databaseId -q '.[0].databaseId')
[ -n "$id" ] && gh api -X POST repos/TheIdeaPlace/AIChat/actions/runs/$id/pending_deployments -F "environment_ids[]=23643397700" -f state=approved -f comment=release
```

Code-owner review covers every file (`.github/CODEOWNERS`): for now Kelly reviews whatever ships,
from anyone. He can't approve his own PRs, so his PRs merge with an admin bypass. That bypass
skips the code-owner check, so before an admin merge confirm with `gh pr view N --json commits`
that every commit is Kelly's or his agent's; a collaborator can push to his branches.

**Release notes and the user guide, with every user-visible change** (the QuickMail process):
- The **open** notes file is the highest `release-notes/vX.Y.Z.md` with no matching tag. Add an
  entry there with each PR, citing it (`(#90)`); if none is open, start the next patch version's
  file. Internal changes (tests, CI, refactors) need no entry. `__version__` is bumped only in
  the release commit, so the notes file can wait on main ahead of it.
- The order: a short intro (what it updates, pre-release or not, that it offers itself as an
  update), then **Added**, **Changed** and **Fixed** as needed (what's new is what most people
  came to read), then the footers, copied from the last release: **Reporting Issues**, **Downloads**,
  **Requirements**, and the independence line.
- Write for users: say what they'll notice, in plain words, with the menu path and the key. Don't
  name a screen reader unless the change is specific to one; say "screen readers".
- The **README is the user guide**: update it in the same PR whenever behaviour changes, along
  with `ui_text.py` (`SHORTCUTS`) and `tools/make_docs.py`'s output in `docs/` when a key changes.

## Architecture

**Data flow.** The app reads files that belong to the Claude desktop app and Claude Code. Their
formats are undocumented, and it never writes to them:
- `sessions.py`: the desktop app's session metadata (`claude-code-sessions/**/local_*.json`, both
  the regular and the Microsoft Store locations) and live busy/idle state (`~/.claude/sessions/<pid>.json`).
- `transcript.py`: the module that knows Claude's `.jsonl` transcript format. It must skip unknown
  record types and never crash on a bad line (it counts them instead), and it reads incrementally.
  Keep Claude transcript-format knowledge here; other backend formats belong in their adapters.
- `providers/`: backend contracts, Claude discovery/history, and Codex's managed stdio app-server.
  Codex history is read through app-server, never parsed from raw rollout files. Owned Codex
  lifecycle, execution, requests and queues stay in the backend; only verified owned chats can send.
- `session_controller.py`: routes discovery, readers, capabilities and owned actions by backend.
  The window must never route foreign IDs through Claude runners or transcript paths.
- `provider_storage.py`: atomic companion stores keep foreign records out of the legacy Claude
  files understood by older releases. Preserve originals on migration failures; provider-only edits
  must not overwrite another release's newer Claude data.
  Native identity is backend plus native ID, separate from presentation keys and ownership.
  Imported chats (#228) will additionally need source-computer identity; do not collapse them
  into local ownership. Provider readers need not read external files: a tool-free backend such
  as Ollama (#224) can keep TCP-owned history and leave agent capabilities disabled.
- `hub.py`: gathers a whole `Snapshot` on a background thread and notices finished turns. It returns
  plain data.
- `own_store.py`, `groups.py`: the app's own state in `%APPDATA%\TheChatPlace` (`~/.config/TheChatPlace`
  on Linux; the only place it writes, apart from the AppImage's desktop entry).
  `desktop_groups.py` reads the desktop app's groups without changing them.
- `platform_paths.py`: **every** path and OS call (`win32`, `darwin` and Linux branches; Linux is
  `sys.platform.startswith("linux")`) (Windows job objects, pid checks, opening URLs,
  finding `claude.exe`). OS-specific code goes here, so a port changes only this file and the speech
  scripts. Tests monkeypatch its functions (`desktop_sessions_dirs`, `projects_dir`,
  `app_data_dir`, …) to point at `tmp_path`.

**Running turns (`claude_cli.py`).** Builds the `claude -p` command lines, runs a turn in a
kill-on-close Windows job (started suspended, then assigned to the job), parses stream-json into
`TurnEvent`s, and answers `can_use_tool` control requests (permissions, AskUserQuestion,
ExitPlanMode) over stdin. Safety invariants that have tests; keep them:
- **Desktop sessions are read-only.** Refuse to build `--resume` for any session the app didn't
  start, any id the desktop app knows (archived included), or any `local_` id. The only exception is
  Continue Here, which uses `--fork-session` into a new id.
- Never use `--bare`. Refuse the npm `claude.cmd`, because a command run through cmd.exe could be
  injected from a session title.
- Strip the environment variables that are inherited from a parent Claude session, and the ones
  that would move billing off the subscription (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`,
  Bedrock/Vertex/Foundry …). Abort the run if `claude` reports an API-key credential source.

**UI (`ui/`).** `main_frame.py` is the large `MainFrame`. Background work (snapshots, chat
loading, turns, update checks, sign-in, slash-command fetches) runs on threads and returns to the
UI only through `wx.CallAfter`. Timers drive list and chat refreshes. `a11y.py` gives controls
MSAA names, and through `mac_a11y.py` (ctypes, no PyObjC) VoiceOver names too, because wx's
`SetName` reaches neither. `mac_a11y.py` is the one exception to "OS calls go in
`platform_paths.py`": it needs a control's native view, and it also holds `activate_app`, which
gives the app the menu bar (VO+M) when the window is raised. `gtk_a11y.py` is its Linux
counterpart, for the same reason: names, roles and row descriptions for Orca through ATK on the
native GTK widget (`GetGtkWidget()`), and announcements as ATK notifications from the window. `announce.py` decides what is spoken, and `speech.py` plus `speech/*.ps1|*.sh` speak it
through the screen reader or a system voice. On Windows `screen_readers.py` reaches JAWS and NVDA
from the app's process; on Linux `orca.py` reaches Orca (D-Bus `PresentMessage` on 49+, else the
active window's ATK notification), and `speak-engine-linux.sh` is speech-dispatcher. See
`dev-notes/linux-port.md`. `ui_text.py` holds UI text that can be tested without wx
(the `LAYOUT` and `SHORTCUTS` lists, which the Help dialog and the README both show).

**Other modules:** `updater.py` (Velopack updates for installed copies only, which refuse to run
if the data folder is inside the install folder), `rendering.py` (markdown to safe HTML for the
WebView2 formatted view), `usage.py`, `changes.py`, `codeblocks.py`, `attachments.py`, `export.py`,
`bugreport.py`, `signin.py`.

## Tests

- `tests/records.py` builds **synthetic** transcript records shaped like Claude Code's. Never copy
  real conversation content into tests.
- `tests/fake_claude.py` stands in for `claude -p` stream-json (`FAKE_CLAUDE_MODE=hang|ask`,
  `FAKE_CLAUDE_LOG`). Use it for turn, cancel and permission tests.
- `test_ui_hidden.py` builds `MainFrame` hidden, with fixtures that monkeypatch paths, speech
  (`spoken`/`feedback` recorders), clipboard, sign-in, `fetch_commands`, `open_url` and the
  formatted view. New UI tests should use its `env` fixture (from `tests/fake_env.py`) so no test
  touches real `%APPDATA%`, real `claude`, or speaks aloud.

## Visual check (#155)

Kelly can't see the screen, so how the app looks is checked by the visual probe, never assumed.
`testing/visual-probe.md` says how it works.
- **A new dialog or main-window state needs a surface in `tools/ui_probe.py`.**
  `tests/test_ui_probe.py` fails on a dialog class without one. `tests/test_layout.py` then measures
  it in CI for cut-off text, overlaps and controls outside the window. Its `KNOWN` list holds only
  problems with a filed issue.
- **Any change that can alter how something looks runs the probe** on the affected surfaces in the
  test VM, at least `light-100` and one High Contrast variant. On Linux the probe draws windows
  through GTK under Xvfb, and `TCP_PROBE_ORCA=1` adds what Orca can reach on each surface
  (`tools/atspi_audit.py`), flagging any control with no name. That's a change to `ui/`,
  `rendering.py`, `statusbar.py`, dialog sizes or labels, or a string a dialog shows.
- **Then compare and review.** Run `tools/ui_compare.py` on the run. Review each changed picture
  with `tools/ui_review_prompt.md`, and say in the PR what changed and its verdict.
  - **No screenshot, no claim:** don't describe a look you haven't seen in a picture.
  - **Contrast is measured** from the pixels, not judged.
  - Give the pictures to the `code-reviewer` too.
- **Accept with care.** Once the change is right, `--accept` the changed pictures into
  `testing/visual/baseline/windows/` in the same PR. Update `testing/visual/descriptions.md` for any
  screen whose look changed.
- **Hand the rest to Kelly.** Anything the probe can't see (animation, focus moving, how it sounds)
  goes in the step-4 list for Kelly.

## Conventions

- Comments and docstrings explain *why* in plain prose and often cite GitHub issue numbers (`#190`).
  Commit messages are one plain-English sentence about the user-visible change, ending in issue/PR
  refs, e.g. `Code blocks: list, read and copy each one (#17) (#43)`.
- Never write to the desktop app's files or to any transcript.
- Research and design notes written while working on an issue go in `dev-notes/` (listed in its
  README), not in `docs/`: GitHub Pages publishes everything in `docs/`. Hand-testing guides go in
  `testing/`.
