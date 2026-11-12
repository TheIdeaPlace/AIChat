# Developing The Chat Place

How to run The Chat Place from source, test it, build it and release it, and where its code
lives. For what the app does and how to use it, see the [README](README.md) and the
[user guide](thechatplace/assets/user-guide.md). [CLAUDE.md](CLAUDE.md) has the project's
conventions and the architecture in more detail.

## Run from source

You need Python 3.11 to 3.13 (wxPython has wheels for those on Windows and the Mac; on Linux,
see below). The repo's virtual environment is
`.venv` (ignored by git). The setup scripts make it, install everything the app, the tests and
the build need (`requirements-build.txt`), and check it all imports, as Image Description
Toolkit's do. Each replaces an existing `.venv`.

| | Set up | Run | Test |
|---|---|---|---|
| Windows | `winsetup.bat` | `.venv\Scripts\pythonw TheChatPlace.pyw` | `.venv\Scripts\python -m pytest tests` |
| Mac | `./macsetup.sh`, or double-click `macsetup.command` | `.venv/bin/python -m thechatplace` | `.venv/bin/python -m pytest tests` |
| Linux | `./linuxsetup.sh` | `.venv/bin/python -m thechatplace` | `.venv/bin/python -m pytest tests` |

wxPython publishes no Linux wheels, and building it takes an hour or more (longer on an Arm
board), so on Linux `.venv` uses your distribution's: `linuxsetup.sh` makes it with
`--system-site-packages` from the system's Python, and says what to install first if it's missing
(`sudo apt install python3-venv python3-wxgtk4.0 python3-wxgtk-webview4.0` on Debian and Ubuntu).
The window tests need a display; without one, run them under `GDK_BACKEND=x11 xvfb-run -a` (the
`GDK_BACKEND` keeps GTK off a Wayland desktop you're logged in to elsewhere).

`python -m thechatplace` works on Windows too. A copy run from source doesn't update itself; Help, Check
for Updates says so, and names the newest release.

One package comes from The Idea Place's own [app-kit](https://github.com/TheIdeaPlace/app-kit):
`theideaplace-appkit`, which Changed Files and GHManage share (#174). `requirements.txt` pins
it to an archive of one app-kit commit, so installing it needs no git. To take a newer app-kit,
change that commit, then run the setup script again or `pip install -r requirements.txt`. An
existing `.venv` made before #174 needs that too.

## Build it yourself

The build scripts are in `BuildAndRelease/`, laid out as in Image Description Toolkit:
`MacBuilds/`, `WinBuilds/` and `LinuxBuilds/`. Each does what the release workflow's job for that platform does:
makes `.venv` if it isn't there, runs the tests, builds the app with PyInstaller, smoke-tests it,
and packs it with Velopack, whose tool `vpk` needs the .NET SDK (the scripts install `vpk` at the
version the workflow uses). Results go in `releases\` and `dist\`; give a folder to copy the
installer or disk image there too, relative to where you ran the script.

**Windows.** `BuildAndRelease\WinBuilds\build_windows.cmd`, from a command prompt or by
double-clicking (the window then stays open to read the result). It makes the installer and
portable zip, **unsigned**: Windows signing is Azure Artifact Signing, which only the workflow
does, so SmartScreen may warn when you run Setup (More info, then Run anyway). Without the .NET SDK
it builds just the app, says why, and exits with an error.

```
BuildAndRelease\WinBuilds\build_windows.cmd
BuildAndRelease\WinBuilds\build_windows.cmd C:\Users\kelly\OneDrive\thehub
```

**Mac.** `BuildAndRelease/MacBuilds/build_macos.sh`, or double-click `build_macos.command`. It
makes `releases/TheChatPlace-macos-arm64.dmg` and the `osx` update feed, for Apple silicon. Like
IDT's, a local Mac build is **signed** with your Developer ID Application certificate when the
keychain has one; notarizing is up to you. The .NET SDK comes from `brew install dotnet`.

```
BuildAndRelease/MacBuilds/build_macos.sh                    signed if you have the certificate
BuildAndRelease/MacBuilds/build_macos.sh ~/Desktop/builds   and copy the .dmg there
TCP_SIGN_CODE=0 BuildAndRelease/MacBuilds/build_macos.sh    unsigned
TCP_NOTARIZE=1 BuildAndRelease/MacBuilds/build_macos.sh     signed and notarized (credentials: notarize.sh)
```

**Linux.** `BuildAndRelease/LinuxBuilds/build_linux.sh` makes
`releases/TheChatPlace-linux-<arch>.AppImage` and the `linux-<arch>` update feed, for the
processor it runs on (x64 or arm64). It also needs objdump, the Python shared library and
mksquashfs (`sudo apt install binutils libpython3-dev squashfs-tools`), and the .NET SDK
(Microsoft's `dotnet-install.sh`, or your distribution's `dotnet-sdk` package).

```
BuildAndRelease/LinuxBuilds/build_linux.sh
BuildAndRelease/LinuxBuilds/build_linux.sh ~/Desktop/builds   and copy the AppImage there
```

The app uses the desktop's own GTK, accessibility bridge, WebKitGTK and speech-dispatcher, never
copies of them: those are what Orca reads, and what the desktop's theme and settings apply to. So
after PyInstaller, the build takes back out every library it copied except Python's,
wxWidgets' and a few small ones each distribution names differently. That makes the PC it's built
on set the oldest Linux it runs on; the workflow builds on Ubuntu 24.04. Linux builds aren't
signed.

The Mac build follows GHManage's: the app is signed inside-out (`sign.sh`), `vpk pack` adds
Velopack's updater (`UpdateMac`) and signs that and the bundle, and the disk image is made from
the packed app, never from `dist/`. An app without `UpdateMac` runs perfectly and never updates
again, so the build checks that Velopack inside the packed app finds its updater. An unsigned
disk image's README.txt says how to open it (System Settings, Privacy & Security, Open Anyway).

## Releasing

The version lives in one place, `__version__` in `thechatplace/__init__.py`. To release:

1. Set `__version__`, and write `release-notes/v<version>.md` (what it is, what's new, downloads,
   requirements), in one commit on main.
2. Tag it `v<version>` and push the tag.

`.github/workflows/release-thechatplace.yml` then builds every platform side by side, and
publishes one GitHub release (a pre-release before 1.0) only if Windows and macOS succeed. For
now a Linux build that fails doesn't hold the release up: it goes out without that AppImage and
feed, and Linux copies stay on the release before.

- **Windows:** runs the tests, fails if the tag and `__version__` disagree, builds the app with
  PyInstaller, smoke-tests the built exe, signs it with Azure Artifact Signing, packs the Velopack
  installer, portable zip and update feed (signing Setup, the updater and the launcher too), and
  checks every signature.
- **Mac:** runs `build_macos.sh` with the Developer ID certificate in a throwaway keychain, so the
  app is signed, packed with its updater and notarized, the disk image notarized and stapled, and
  Gatekeeper's verdict checked. It needs the repository secrets `MACOS_CERTIFICATE_P12`,
  `MACOS_CERTIFICATE_PASSWORD`, `NOTARY_KEY_P8`, `NOTARY_KEY_ID` and `NOTARY_ISSUER_ID`, which
  `The-Idea-Place-Projects/signing/sync-secrets.py` sets from the signing kit; a tag fails without
  them rather than publish an unsigned app.
- **Linux:** runs `build_linux.sh` twice, on GitHub's Ubuntu 24.04 x64 and arm64 runners, with
  Ubuntu's own wxPython, so each makes its AppImage and update feed. The AppImage must start and
  know its version before it's kept.

Run by hand or for a pull request, it does everything but publish, and keeps the files as
workflow artifacts; tick "sign" on a hand run, or label a pull request `sign-build`, to sign (and
notarize) and check them too.

Releases come from tags named `v<version>`, and
each release carries four Velopack update feeds, `windows` (`releases.windows.json`), `osx`
(`releases.osx.json`), `linux-x64` and `linux-arm64` (one for each processor, since an AppImage
holds programs for one). The updater finds the newest `v*` release in this repository itself
and reads the feed from that release only (`REPO_URL`, `RELEASES_API`, `TAG_PREFIX` and `CHANNEL`
in `thechatplace/updater.py`).

## How it's been tested

The Chat Place is used with JAWS on Kelly's PC; the
installer, uninstaller and update check have been tested in the vmtest VM. Downloading and
installing an update needs two published releases, so it is first tried with 0.1.1. It has not
yet had a pass with NVDA. The Mac build is used with VoiceOver on Kelly's Mac, and since 0.1.2
the release workflow signs and notarizes it. The Linux build (AppImages for x64 and arm64) is a
preview. It has been tested with Orca 51 on an MNT Pocket Reform, with Orca 48 on Debian 13
(arm64) in a virtual machine, and on Ubuntu 26.04 (arm64) in a virtual machine.

Linux isn't tested by hand for each release. CI's **Orca (Linux)** job runs real Orca in a
desktop of its own (`tools/linux_a11y_session.sh`: a session bus, Xvfb, a window manager and an
accessibility bus), with no person, sound or VM:

- `tools/orca_speech.sh` starts the app on the visual probe's made-up sessions, presses keys
  through the session list, a chat's messages, Settings, the menu bar (F10 and Alt on its own)
  and Keyboard Shortcuts, and checks what Orca said for each from its debug log. `--record`
  says what each step spoke without checking; add a step to `STEPS` in `tools/orca_speech.py`
  when a keyboard path changes. A step waits, up to 15 seconds, for everything it expects and
  then for a moment's quiet, so it doesn't depend on how fast the runner is. `--keep DIR`
  writes Orca's debug log and the app's output there from the start, so a run that fails or
  runs out of time keeps them (CI uploads them), and each step's line gives the time it
  started, to find it in the log.
- The visual probe's Orca audit, run and checked by `tools/orca_names.py --run DIR`, fails on
  any control on any surface that Orca reaches without a name.
- The ATK tests in `tests/test_linux.py` run with an accessibility bus.

The job used to fail on a different step from run to run on the same code (#579). Orca 46,
which Ubuntu 24.04 and so CI has, remembers the objects it has found dead by their address in
its memory. When a dead one is freed, a new object (a dialog just opened, a row just arrowed
to) can get the same address, and Orca then takes it for dead and says nothing about it until
it clears what it remembers, a minute later: a dialog read without its title or not at all, a
row arrowed to and not read. The debug logs of the failed runs showed it every time, apart from
the Copilot chat's step (the last point below).
`tools/orca-customizations.py`, which `linux_a11y_session.sh` gives the test Orca
only, has Orca ask such an object again before believing it dead, as newer Orca does
(`revalidate_if_known_dead`). It asks the app itself, not AT-SPI's cache, and an object the app
says is defunct stays dead, so an object that really is gone still fails the question. That
puts Orca right about 20 times a run, nearly always as a dialog opens. This fixes Orca 46's mistake in CI only: people using
Orca 46 still meet it, and #606 asks whether the app can replace fewer rows and windows to make
it rarer for them. The speech report ends with how often Orca was corrected, step by step (also
`corrected` in the JSON), with a warning for a step above `CORRECTIONS_CEILING`, so a change
that makes the app replace many more objects shows. So:

- No step is run again after a failure. A failed step is a real difference in what Orca said,
  or a new kind of flakiness to investigate, not to retry.
- The one exception is the names audit's probe crashing (a segfault in GTK or AT-SPI, seen
  twice in 2026): `orca_names.py --run` starts it again on the surface it crashed on and those
  after it, with a warning in the log. That happens once a run: a second crash anywhere fails,
  as does a crash after the last surface (the app's windows closing), and a control without a
  name is never run again. What the probe printed, GTK's thousands of CRITICAL warnings
  included, is in `probe-linux/probe.log`.
- The audit used to take about 2.4 seconds a surface, waiting on the probe's 20 ms sleeps to
  answer each of AT-SPI's questions; the probe now answers at once, and the audit takes about
  0.2 seconds a surface, the whole step about 5 minutes for 131 surfaces.
- A trigger that sets up a lot at once (the Copilot chat) lets the window settle before it
  moves the focus, and what happens next (Copilot asking) is a step of its own. That setup
  opened the chat twice, which no one does; an update and a refresh with the focus in the
  messages, which do happen, have a step of their own that checks the arrows still read the
  rows around it. A step with `after_trigger` presses its keys only once its trigger has
  finished.

`build_linux.sh`, and so the release workflow's Linux jobs, also start the packed AppImage on a
virtual screen and wait for its window. Orca runs one copy per user, so to run these on a PC
where you use Orca, run them as another user (the Ubuntu test VM has `tcptest`). They need
`sudo apt install orca xdotool xvfb at-spi2-core dbus-x11 openbox x11-xserver-utils python3-gi
gir1.2-atspi-2.0`.

The rule for finding a session's conversation file (its folder with every character that
isn't a letter or digit turned into `-`) was checked against every transcript on Kelly's PC.

## Test coverage

The **Coverage** workflow runs every Monday (or by hand: Actions, Coverage, Run workflow). It
measures how much of `thechatplace` the tests run on Windows, macOS and Linux, lines and
branches, and combines the three (#628). `tools/coverage_check.py` compares the result with
`testing/coverage/baseline.json` and flags:

- the whole app falling more than 1 point;
- a file of 50 statements or more falling more than 5 points;
- any file, a new one included, with 25 or more statements more that no test runs.

A drop files an issue labelled `coverage-regression`, or comments on the one already open. Each
run's summary page shows the report, and its `coverage-report` download has the per-line HTML.

The baseline changes only when it's accepted, so a slow slide is still caught. When a drop is
expected (code moved, tests removed on purpose), or coverage rose and the bar should rise with it,
download `coverage.json` from a run's `coverage-report` and accept it in a pull request:

```
gh run download RUN_ID -n coverage-report -D cov
.venv/bin/python tools/coverage_check.py cov/coverage.json --accept --measured "main at SHA, DATE, run RUN_ID"
```

To measure locally: `pip install pytest-cov`, then `python -m pytest -q tests --cov
--cov-report=term-missing` (`.coveragerc` has the settings). One platform alone reads lower than
the combined figure, because each skips the others' tests.

## Files

| Path | What |
|---|---|
| `TheChatPlace.pyw` | Double-click launcher |
| `thechatplace/transcript.py` | Transcript parser |
| `thechatplace/sessions.py` | Desktop metadata, terminal sessions, live state, sorting, the list wording |
| `thechatplace/own_store.py` | The Chat Place's own sessions |
| `thechatplace/titles.py` | Names you've given desktop app sessions |
| `thechatplace/prompts.py` | Your saved prompts |
| `thechatplace/about_you.py` | Finding Claude and Codex local customization files |
| `thechatplace/claude_cli.py` | `claude` commands, the `--resume` guard, the environment, stream-json events, running a turn |
| `thechatplace/rewind.py`, `thechatplace/ui/rewind_chats.py` | Retry from Here (named Rewind to Here until #596) and Branch from Here (#468): where to cut a Claude chat's history, what each choice does, putting files back with `--rewind-files` |
| `thechatplace/hub.py` | Gathering the list, noticing finished turns |
| `thechatplace/announce.py` | What gets announced |
| `thechatplace/commands.py` | Every menu command once (#575): its id, label, access key, key, and which menu it's in. `MainFrame._build_menu` builds the menu bar from it, and `_command_handlers` says what each id does. Ids are kept once released: customizable keys (#635) store them |
| `thechatplace/keymap.py` | Your keyboard shortcuts (#635): `keys.json` (only the keys you changed, by command id, in Windows names; ids it doesn't know are kept), the one name for a key, which keys can't be given and why (`FIXED`, `refusal`), and which command a key runs. `MainFrame.apply_keys` shows a change at once |
| `thechatplace/sound_events.py`, `thechatplace/ui/sound_host.py`, `thechatplace/ui/sounds_dialog.py` | Sounds: each event's sound, sound schemes, what the window plays, and Tools, Sounds. `thechatplace/assets/sounds` holds copies of app-kit's WAVs; app-kit plays them. Schemes, scheme files and the gallery follow app-kit's `sounds/SCHEMES.md`, through its `soundschemes` and `soundgallery`; app-kit's `apps.json` is bundled with `--collect-data theideaplace_appkit` |
| `thechatplace/sound_gallery.py`, `thechatplace/ui/sound_gallery_dialog.py` | Tools, Sounds, Browse Gallery: the signed gallery of sound schemes. `--sound-gallery <folder or address>` points it at a gallery of your own for testing |
| `thechatplace/rendering.py` | A message's markdown as a safe HTML page, for the formatted view |
| `thechatplace/speech.py`, `thechatplace/speech/` | Speech, adapted from Image Description Toolkit (ClaudeSpeak's engine scripts) |
| `thechatplace/screen_readers.py` | Speaking through JAWS and NVDA on Windows |
| `thechatplace/orca.py` | Speaking through Orca on Linux |
| `thechatplace/nvda/` | NV Access's NVDA Controller Client (LGPL 2.1, unmodified; see its README and `license.txt`) |
| `thechatplace/platform_paths.py` | Every path and OS call |
| `thechatplace/links.py` | `thechatplace://` links to sessions: making, reading and registering them |
| `thechatplace/session_tools.py`, `thechatplace/session_tools_mcp.py` | The session tools Claude turns get (`/wrap` and the like, #165): the tools' answers, the connection to the window, and the MCP server (`--session-tools-mcp`), whose stdio the built app's smoke test checks |
| `thechatplace/ui/` | The wxPython window and dialogs |
| `tests/` | pytest tests, built on made-up records shaped like the real ones |
| `thechatplace/updater.py` | Velopack updates, adapted from GHManage's updater |
| `tools/check_version.py` | Prints the version; checks a release tag against it |
| `tools/make_version_info.py` | The Windows version resource for the built exe |
| `tools/coverage_check.py`, `testing/coverage/baseline.json`, `.coveragerc` | The weekly coverage check, its baseline and its settings |
| `winsetup.bat`, `macsetup.sh` (`.command`), `linuxsetup.sh` | Set up `.venv` on Windows, a Mac or Linux |
| `BuildAndRelease/WinBuilds/build_windows.cmd` | Build on Windows (unsigned) |
| `BuildAndRelease/MacBuilds/` | Build on a Mac (`build_macos.sh`, `.command`), and its signing, notarizing, entitlements and disk image |
| `BuildAndRelease/LinuxBuilds/build_linux.sh` | Build on Linux: the AppImage and update feed for the PC's processor |
| `requirements-build.txt` | Everything a build needs; pins PyInstaller |
| `release-notes/` | One file per release, used as the GitHub release's notes and the update's notes |
