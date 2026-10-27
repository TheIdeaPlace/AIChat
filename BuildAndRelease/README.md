# Building and releasing The Chat Place

Local builds for each platform, laid out as in Image Description Toolkit. Set up `.venv` first
with `macsetup.sh` or `winsetup.bat` at the top of the repo (the build scripts run them if there's
no `.venv`). Both builds need the .NET SDK for Velopack's `vpk`, which the scripts install at
1.2.161, the version of the `velopack` library the app uses.

| | Build | Signed locally? |
|---|---|---|
| Mac | `MacBuilds/build_macos.sh` (or double-click `build_macos.command`) | Yes, when the keychain has a Developer ID Application certificate. `TCP_SIGN_CODE=0` to skip, `TCP_NOTARIZE=1` to notarize too |
| Windows | `WinBuilds\build_windows.cmd` | No: Windows signing is Azure Artifact Signing, which only the release workflow does |

Results: `dist/` (the app) and `releases/` (Mac: `TheChatPlace-macos-arm64.dmg` and the `osx`
update feed; Windows: Setup, the portable zip and the `windows` update feed).

`MacBuilds/` also holds the Mac build's parts: `sign.sh` (inside-out Developer ID signing),
`notarize.sh`, `create_dmg.sh` and `TheChatPlace.entitlements`. To make the disk image again from
the last build without rebuilding, double-click `create_dmg.command`: it uses the app in
`releases/TheChatPlace-osx-Portable.zip` and signs the image if that app is signed.

## Releasing

Set `__version__` in `thechatplace/__init__.py`, write `release-notes/v<version>.md`, commit, and
push the tag `v<version>`. `.github/workflows/release-thechatplace.yml` builds and signs both
platforms (the Mac job runs `MacBuilds/build_macos.sh`) and publishes one release only if both
succeed. The signing secrets come from `The-Idea-Place-Projects/signing` (`sync-secrets.py`).
The "Build it yourself" and "Releasing" sections of [DEVELOPING.md](../DEVELOPING.md) have the details.
