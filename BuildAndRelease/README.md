# Building and releasing The Chat Place

Local builds for each platform, laid out as in Image Description Toolkit. Set up `.venv` first
with `macsetup.sh`, `winsetup.bat` or `linuxsetup.sh` at the top of the repo (the build scripts run
them if there's no `.venv`). Every build needs the .NET SDK for Velopack's `vpk`, which the
scripts install at 1.2.161, the version of the `velopack` library the app uses.

| | Build | Signed locally? |
|---|---|---|
| Mac | `MacBuilds/build_macos.sh` (or double-click `build_macos.command`) | Yes, when the keychain has a Developer ID Application certificate. `TCP_SIGN_CODE=0` to skip, `TCP_NOTARIZE=1` to notarize too |
| Windows | `WinBuilds\build_windows.cmd` | No: Windows signing is Azure Artifact Signing, which only the release workflow does |
| Linux | `LinuxBuilds/build_linux.sh`, for the PC's processor (x64 or arm64) | Not signed anywhere |

Results: `dist/` (the app) and `releases/` (Mac: `TheChatPlace-macos-arm64.dmg` and the `osx`
update feed; Windows: Setup, the portable zip and the `windows` update feed; Linux:
`TheChatPlace-linux-<arch>.AppImage` and the `linux-<arch>` update feed).

The Linux build leaves GTK, its accessibility bridge, WebKitGTK and the rest of the system's
libraries out of the app, so it runs on the desktop's own (what Orca reads), and keeps only
Python, wxWidgets and the image libraries distributions name differently. The PC it's built on
is then the oldest Linux it runs on; the workflow builds on Ubuntu 24.04. `build_linux.sh`
explains why.

`MacBuilds/` also holds the Mac build's parts: `sign.sh` (inside-out Developer ID signing),
`notarize.sh`, `create_dmg.sh` and `TheChatPlace.entitlements`. To make the disk image again from
the last build without rebuilding, double-click `create_dmg.command`: it uses the app in
`releases/TheChatPlace-osx-Portable.zip` and signs the image if that app is signed.

## Releasing

Set `__version__` in `thechatplace/__init__.py`, write `release-notes/v<version>.md`, commit, and
push the tag `v<version>`. `.github/workflows/release-thechatplace.yml` builds every platform,
signing Windows and the Mac (the Mac job runs `MacBuilds/build_macos.sh`, the Linux jobs
`LinuxBuilds/build_linux.sh` on x64 and arm64). It publishes one release once Windows and the
Mac succeed, with the AppImage and feed of each Linux build that succeeded: a Linux failure
doesn't hold up the others. The signing secrets come from `The-Idea-Place-Projects/signing` (`sync-secrets.py`).
The "Build it yourself" and "Releasing" sections of [DEVELOPING.md](../DEVELOPING.md) have the details.
