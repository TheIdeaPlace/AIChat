# Developing The Chat Place

How to run The Chat Place from source, test it, build it and release it, and where its code
lives. For what the app does and how to use it, see the [README](README.md) and the
[user guide](thechatplace/assets/user-guide.md). [CLAUDE.md](CLAUDE.md) has the project's
conventions and the architecture in more detail.

## Run from source

You need Python 3.11 to 3.13 (wxPython has wheels for those). The repo's virtual environment is
`.venv` (ignored by git). The setup scripts make it, install everything the app, the tests and
the build need (`requirements-build.txt`), and check it all imports, as Image Description
Toolkit's do. Each replaces an existing `.venv`.

| | Set up | Run | Test |
|---|---|---|---|
| Windows | `winsetup.bat` | `.venv\Scripts\pythonw TheChatPlace.pyw` | `.venv\Scripts\python -m pytest tests` |
| Mac | `./macsetup.sh`, or double-click `macsetup.command` | `.venv/bin/python -m thechatplace` | `.venv/bin/python -m pytest tests` |

`python -m thechatplace` works on Windows too. A copy run from source doesn't update itself; Help, Check
for Updates says so, and names the newest release.

## Build it yourself

The build scripts are in `BuildAndRelease/`, laid out as in Image Description Toolkit:
`MacBuilds/` and `WinBuilds/`. Each does what the release workflow's job for that platform does:
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

`.github/workflows/release-thechatplace.yml` then builds both platforms side by side, and
publishes one GitHub release (a pre-release before 1.0) only if both succeed:

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

Run by hand or for a pull request, it does everything but publish, and keeps the files as
workflow artifacts; tick "sign" on a hand run, or label a pull request `sign-build`, to sign (and
notarize) and check them too.

Releases come from tags named `v<version>`, and
each release carries two Velopack update feeds, `windows` (`releases.windows.json`) and `osx`
(`releases.osx.json`). The updater finds the newest `v*` release in this repository itself
and reads the feed from that release only (`REPO_URL`, `RELEASES_API`, `TAG_PREFIX` and `CHANNEL`
in `thechatplace/updater.py`).

## How it's been tested

Used with JAWS on Kelly's PC; the
installer, uninstaller and update check have been tested in the vmtest VM. Downloading and
installing an update needs two published releases, so it is first tried with 0.1.1. It has not
yet had a pass with NVDA. The Mac build is used with VoiceOver on Kelly's Mac, and since 0.1.2
the release workflow signs and notarizes it.

The rule for finding a session's conversation file (its folder with every character that
isn't a letter or digit turned into `-`) was checked against every transcript on Kelly's PC.

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
| `thechatplace/hub.py` | Gathering the list, noticing finished turns |
| `thechatplace/announce.py` | What gets announced |
| `thechatplace/rendering.py` | A message's markdown as a safe HTML page, for the formatted view |
| `thechatplace/speech.py`, `thechatplace/speech/` | Speech, adapted from Image Description Toolkit (ClaudeSpeak's engine scripts) |
| `thechatplace/screen_readers.py` | Speaking through JAWS and NVDA on Windows |
| `thechatplace/nvda/` | NV Access's NVDA Controller Client (LGPL 2.1, unmodified; see its README and `license.txt`) |
| `thechatplace/platform_paths.py` | Every path and OS call |
| `thechatplace/links.py` | `thechatplace://` links to sessions: making, reading and registering them |
| `thechatplace/ui/` | The wxPython window and dialogs |
| `tests/` | pytest tests, built on made-up records shaped like the real ones |
| `thechatplace/updater.py` | Velopack updates, adapted from GHManage's updater |
| `tools/check_version.py` | Prints the version; checks a release tag against it |
| `tools/make_version_info.py` | The Windows version resource for the built exe |
| `winsetup.bat`, `macsetup.sh` (`.command`) | Set up `.venv` on Windows or a Mac |
| `BuildAndRelease/WinBuilds/build_windows.cmd` | Build on Windows (unsigned) |
| `BuildAndRelease/MacBuilds/` | Build on a Mac (`build_macos.sh`, `.command`), and its signing, notarizing, entitlements and disk image |
| `requirements-build.txt` | Everything a build needs; pins PyInstaller |
| `release-notes/` | One file per release, used as the GitHub release's notes and the update's notes |
