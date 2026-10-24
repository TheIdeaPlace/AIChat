#!/bin/bash
# ============================================================================
# Build The Chat Place on this Mac: tests, TheChatPlace.app, Velopack's update
# packages, and the disk image. The same script the release workflow's macOS
# job runs; BuildAndRelease/WinBuilds/build_windows.cmd is the Windows one.
#
#   BuildAndRelease/MacBuilds/build_macos.sh                   into dist/ and releases/
#   BuildAndRelease/MacBuilds/build_macos.sh ~/Desktop/builds  and copy the .dmg there
#
# Or double-click build_macos.command in Finder.
#
# Signing, as in Image Description Toolkit: a local build signs with your
# Developer ID Application certificate when the keychain has one.
#   TCP_SIGN_CODE=0        don't sign (the .dmg's README then says how to open it)
#   TCP_SIGN_CODE=1        sign, and fail if there's no certificate (CI)
#   TCP_NOTARIZE=1         also notarize the app and the .dmg; credentials in
#                          notarize.sh (NOTARY_PROFILE, or the API key variables)
#   TCP_SIGNING_IDENTITY   which certificate, if there's more than one
#   TCP_KEYCHAIN           the keychain that holds it (CI uses a throwaway one);
#                          codesign, notarytool and vpk all use it
#   TCP_KEEP_RELEASES=1    keep releases/ (CI puts the previous release's
#                          packages there, for a delta update)
#
# How it fits together, following GHManage (docs/INSTALLER.md there):
#   1. PyInstaller makes dist/TheChatPlace.app, smoke-tested.
#   2. sign.sh signs it inside-out (never codesign --deep), smoke-tested again.
#   3. vpk pack adds Velopack's updater (Contents/MacOS/UpdateMac), signs that
#      and the bundle (--signDisableDeep keeps step 2's signatures), notarizes,
#      and writes the osx update feed and TheChatPlace-osx-Portable.zip.
#   4. The disk image is made from the app in that zip, never from dist/: an
#      app without UpdateMac runs perfectly and never updates again.
#
# Needs .venv (macsetup.sh makes it) and vpk (dotnet tool install -g vpk
# --version 1.2.161, which needs the .NET SDK: brew install dotnet).
# Apple silicon only.
# ============================================================================
set -euo pipefail

OUTPUT_DIR="${1:-}"
# Resolved before the cd, so a relative folder means relative to where you ran it.
if [ -n "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR" || { echo "Couldn't make $OUTPUT_DIR"; exit 1; }
    OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

PY=.venv/bin/python
APP=dist/TheChatPlace.app
BUNDLE_ID=net.theideaplace.thechatplace
CHANNEL=osx                      # updater.CHANNEL on a Mac
VPK_VERSION=1.2.161              # the velopack library's version in requirements.txt
RELEASES=releases
PORTABLE_ZIP="$RELEASES/TheChatPlace-$CHANNEL-Portable.zip"
DMG="$RELEASES/TheChatPlace-macos-arm64.dmg"
NOTARIZE="${TCP_NOTARIZE:-0}"
SIGN="${TCP_SIGN_CODE:-auto}"
[ "$NOTARIZE" = "1" ] && SIGN=1

fail() { echo ""; echo "BUILD FAILED: $*"; exit 1; }

# CI names its throwaway keychain whether or not this run signs, but only makes
# it when it does; vpk refuses a --keychain that doesn't exist.
if [ -n "${TCP_KEYCHAIN:-}" ] && [ ! -f "$TCP_KEYCHAIN" ]; then
    unset TCP_KEYCHAIN
fi

# The built app imports everything and checks its data files, without opening
# a window. Run at each stage: the hardened runtime can refuse what ran fine
# ad-hoc signed, and vpk changes the bundle.
smoke_test() {
    local app="$1" stage="$2"
    rm -f smoke-local.json
    "$app/Contents/MacOS/TheChatPlace" --smoke-test smoke-local.json \
        || { cat smoke-local.json 2>/dev/null; fail "the $stage app's smoke test."; }
    echo "  $stage app: $(tr -d '\n' < smoke-local.json | sed 's/.*"problems": //')"
}

echo "========================================================================"
echo "Building The Chat Place for macOS"
echo "========================================================================"

[ "$(uname -m)" = "arm64" ] || fail "this builds for Apple silicon, and this Mac is $(uname -m)."

# ---------------------------------------------------------------- tools ----
if [ ! -x "$PY" ]; then
    echo ""
    echo "No .venv yet: running macsetup.sh..."
    ./macsetup.sh --yes || fail "macsetup.sh couldn't set up .venv."
fi
"$PY" -m pip install --quiet -r requirements-build.txt || fail "pip install"

# vpk can't start without knowing where .NET is: Homebrew's, or Microsoft's installer's.
if [ -z "${DOTNET_ROOT:-}" ]; then
    for root in /opt/homebrew/opt/dotnet/libexec /usr/local/share/dotnet; do
        if [ -d "$root" ]; then export DOTNET_ROOT="$root"; break; fi
    done
fi
VPK="$(command -v vpk || echo "$HOME/.dotnet/tools/vpk")"
if [ ! -x "$VPK" ] || ! "$VPK" -h 2>/dev/null | grep -q "$VPK_VERSION"; then
    command -v dotnet >/dev/null 2>&1 \
        || fail "vpk needs the .NET SDK: brew install dotnet, then run this again."
    echo "Installing vpk $VPK_VERSION..."
    dotnet tool update -g vpk --version "$VPK_VERSION" >/dev/null || fail "installing vpk"
    VPK="$HOME/.dotnet/tools/vpk"
    "$VPK" -h >/dev/null 2>&1 || fail "vpk is installed but won't start: set DOTNET_ROOT to the .NET folder."
fi

# Signing: on by default when there's a certificate, as IDT's local builds do.
# One identity for the app, vpk and the .dmg: with two Developer ID
# certificates (after a renewal, say) a bare "Developer ID Application" is
# ambiguous.
if [ "$SIGN" != "0" ] && [ -z "${TCP_SIGNING_IDENTITY:-}" ]; then
    TCP_SIGNING_IDENTITY=$(security find-identity -v -p codesigning ${TCP_KEYCHAIN:+"$TCP_KEYCHAIN"} \
        | grep "Developer ID Application" | head -1 | sed 's/.*"\(.*\)"/\1/') || true
fi
if [ "$SIGN" = "auto" ]; then
    if [ -n "${TCP_SIGNING_IDENTITY:-}" ]; then SIGN=1; else SIGN=0; fi
fi
if [ "$SIGN" = "1" ]; then
    [ -n "${TCP_SIGNING_IDENTITY:-}" ] || fail "no Developer ID Application certificate (see sign.sh)."
    export TCP_SIGNING_IDENTITY
fi

# vpk accepts notarization credentials only as a notarytool keychain profile,
# so make one from the API key when there isn't one (GHManage does the same).
if [ "$NOTARIZE" = "1" ] && [ -z "${NOTARY_PROFILE:-}" ]; then
    [ -n "${NOTARY_KEY_PATH:-}" ] && [ -n "${NOTARY_KEY_ID:-}" ] && [ -n "${NOTARY_ISSUER_ID:-}" ] \
        || fail "TCP_NOTARIZE=1 needs NOTARY_PROFILE, or NOTARY_KEY_PATH, NOTARY_KEY_ID and NOTARY_ISSUER_ID."
    NOTARY_PROFILE=thechatplace-notary
    xcrun notarytool store-credentials "$NOTARY_PROFILE" --key "$NOTARY_KEY_PATH" \
        --key-id "$NOTARY_KEY_ID" --issuer "$NOTARY_ISSUER_ID" \
        ${TCP_KEYCHAIN:+--keychain "$TCP_KEYCHAIN"} >/dev/null \
        || fail "storing the notarytool profile"
    export NOTARY_PROFILE
fi

# ------------------------------------------------------------------ tests ----
echo ""
echo "Running the tests..."
"$PY" -m pytest -q tests || fail "the tests."

VERSION=$("$PY" tools/check_version.py) || fail "tools/check_version.py"
echo ""
echo "The Chat Place $VERSION ($([ "$SIGN" = "1" ] && echo "signed: $TCP_SIGNING_IDENTITY" || echo unsigned)$([ "$NOTARIZE" = "1" ] && echo ", notarized"))"

# -------------------------------------------------------------------- app ----
# --onedir --windowed makes an .app whose libraries can each be signed (a
# onefile app unpacks unsigned copies at every start). velopack is imported
# lazily, so it's named; the speech scripts are data files.
echo ""
echo "Building the app (PyInstaller)..."
rm -rf "$APP"
"$PY" -m PyInstaller --noconfirm --clean --windowed --onedir --name TheChatPlace \
    --osx-bundle-identifier "$BUNDLE_ID" --hidden-import velopack --hidden-import thechatplace.providers.codex --hidden-import thechatplace.providers.codex_runtime --hidden-import thechatplace.providers.copilot \
    --add-data "thechatplace/assets:thechatplace/assets" \
    --add-data "thechatplace/speech:thechatplace/speech" \
    TheChatPlace.pyw || fail "PyInstaller"
[ -x "$APP/Contents/MacOS/TheChatPlace" ] || fail "no $APP/Contents/MacOS/TheChatPlace"

# What PyInstaller can't set from the command line. NSAppleEventsUsageDescription
# is what macOS shows when speech first asks VoiceOver to speak.
PLIST="$APP/Contents/Info.plist"
plist_set() {
    /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
plist_set CFBundleShortVersionString "$VERSION"
plist_set CFBundleVersion "$VERSION"
plist_set CFBundleDisplayName "The Chat Place"
plist_set CFBundleName "The Chat Place"
plist_set NSHumanReadableCopyright "Copyright Kelly Ford. MIT License."
plist_set NSAppleEventsUsageDescription \
    "The Chat Place asks VoiceOver to speak announcements, such as when Claude replies."
# thechatplace:// links to sessions (#144): macOS sends them to the app, which
# opens them in MacOpenURL. Rewritten whole, so a rebuild never doubles it.
/usr/libexec/PlistBuddy -c "Delete :CFBundleURLTypes" "$PLIST" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy \
    -c "Add :CFBundleURLTypes array" \
    -c "Add :CFBundleURLTypes:0 dict" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLName string $BUNDLE_ID.session-link" \
    -c "Add :CFBundleURLTypes:0:CFBundleTypeRole string Viewer" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string thechatplace" \
    "$PLIST" || fail "adding the thechatplace:// link type to Info.plist"
[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleURLTypes:0:CFBundleURLSchemes:0' "$PLIST")" \
    = "thechatplace" ] || fail "Info.plist has no thechatplace:// link type"
# Editing Info.plist breaks PyInstaller's ad-hoc signature; make a new one.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || fail "ad-hoc signing"

echo ""
echo "Smoke tests:"
smoke_test "$APP" built

if [ "$SIGN" = "1" ]; then
    echo ""
    echo "Signing with Developer ID (inside-out)..."
    bash "$HERE/sign.sh" "$APP" || fail "signing"
    smoke_test "$APP" signed
fi

# ---------------------------------------------------------------- Velopack ----
echo ""
echo "Packing the update feed (vpk, channel $CHANNEL)..."
# vpk refuses a version its output folder already has, so a local rebuild
# starts clean; CI keeps the previous release's packages it put there.
if [ "${TCP_KEEP_RELEASES:-0}" != "1" ]; then
    rm -rf "$RELEASES"
fi
mkdir -p "$RELEASES"
VPK_ARGS=(pack --packId TheChatPlace --packVersion "$VERSION" --packDir "$APP"
          --mainExe TheChatPlace --packTitle "The Chat Place" --packAuthors "Kelly Ford"
          --bundleId "$BUNDLE_ID" --channel "$CHANNEL" --outputDir "$RELEASES"
          # No Setup.pkg: signing one needs a Developer ID Installer certificate,
          # and the .dmg holds the same self-updating app (as GHManage ships).
          --noInst)
[ -f "release-notes/v$VERSION.md" ] && VPK_ARGS+=(--releaseNotes "release-notes/v$VERSION.md")
if [ "$SIGN" = "1" ]; then
    # vpk insists on the .entitlements extension.
    VPK_ARGS+=(--signDisableDeep --signEntitlements "$HERE/TheChatPlace.entitlements"
               --signAppIdentity "$TCP_SIGNING_IDENTITY")
fi
[ "$NOTARIZE" = "1" ] && VPK_ARGS+=(--notaryProfile "$NOTARY_PROFILE")
[ -n "${TCP_KEYCHAIN:-}" ] && VPK_ARGS+=(--keychain "$TCP_KEYCHAIN")
"$VPK" "${VPK_ARGS[@]}" || fail "vpk pack"
[ -f "$PORTABLE_ZIP" ] || fail "vpk made no $PORTABLE_ZIP"

# ------------------------------------------------------------- disk image ----
# From the packed app, which carries Velopack's updater. ditto, not unzip,
# keeps the symlinks and attributes the signature covers.
PACKED=$(mktemp -d)
trap 'rm -rf "$PACKED"' EXIT
ditto -x -k "$PORTABLE_ZIP" "$PACKED"
# vpk names the bundle after --packTitle: "The Chat Place.app".
PACKED_APP=$(find "$PACKED" -maxdepth 1 -name "*.app" | head -1)
[ -n "$PACKED_APP" ] || fail "no .app in $PORTABLE_ZIP"
[ -f "$PACKED_APP/Contents/MacOS/UpdateMac" ] \
    || fail "the packed app has no Contents/MacOS/UpdateMac, so it could never update."
smoke_test "$PACKED_APP" packed
# vpk rewrites parts of Info.plist; the link type (#144) must survive it.
[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleURLTypes:0:CFBundleURLSchemes:0' \
    "$PACKED_APP/Contents/Info.plist" 2>/dev/null)" = "thechatplace" ] \
    || fail "the packed app's Info.plist lost the thechatplace:// link type"
# And Velopack, inside the packed app, finds its updater and knows its version.
grep -q "\"updater\": \"$VERSION\"" smoke-local.json \
    || { cat smoke-local.json; fail "Velopack doesn't see the packed app as updatable."; }
if [ "$SIGN" = "1" ]; then
    codesign --verify --strict --deep "$PACKED_APP" || fail "the packed app's signature"
fi

echo ""
bash "$HERE/create_dmg.sh" "$PACKED_APP" "$DMG" "$VERSION" "$([ "$SIGN" = "1" ] && echo signed)" \
    || fail "the disk image."
if [ "$SIGN" = "1" ]; then
    codesign --force --timestamp ${TCP_KEYCHAIN:+--keychain "$TCP_KEYCHAIN"} \
        --sign "$TCP_SIGNING_IDENTITY" "$DMG" || fail "signing the .dmg"
fi
if [ "$NOTARIZE" = "1" ]; then
    bash "$HERE/notarize.sh" "$DMG" || fail "notarizing the .dmg"
fi

if [ -n "$OUTPUT_DIR" ]; then
    cp "$DMG" "$OUTPUT_DIR/" || fail "couldn't copy into $OUTPUT_DIR"
fi

echo ""
echo "========================================================================"
echo "BUILD SUCCESSFUL: The Chat Place $VERSION ($([ "$SIGN" = "1" ] && echo signed || echo unsigned)$([ "$NOTARIZE" = "1" ] && echo ", notarized"))"
echo "========================================================================"
echo "  Disk image:  $DMG"
echo "  Update feed: $RELEASES/releases.$CHANNEL.json and the packages beside it"
echo "  App:         $APP (before Velopack; the .dmg holds the packed one)"
[ -n "$OUTPUT_DIR" ] && echo "  Copied to:   $OUTPUT_DIR"
exit 0
