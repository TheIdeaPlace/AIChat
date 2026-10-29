#!/bin/bash
# ============================================================================
# Build The Chat Place on this Linux PC: tests, the app, and Velopack's
# AppImage and update packages, for this PC's processor (x64 or arm64). The
# same script the release workflow's Linux jobs run; build_macos.sh and
# build_windows.cmd are the others.
#
#   BuildAndRelease/LinuxBuilds/build_linux.sh                  into dist/ and releases/
#   BuildAndRelease/LinuxBuilds/build_linux.sh ~/Desktop/builds  and copy the AppImage there
#
#   TCP_KEEP_RELEASES=1   keep releases/ (CI puts the previous release's
#                         packages there, for a delta update)
#
# How it fits together:
#   1. PyInstaller makes dist/TheChatPlace/, smoke-tested.
#   2. The desktop's own libraries are taken back out of it (see below), and
#      it's smoke-tested again.
#   3. vpk pack makes TheChatPlace-linux-<arch>.AppImage, with Velopack's
#      updater in it, and the linux-<arch> update feed.
#   4. The AppImage is smoke-tested: it must start, and Velopack in it must
#      know its version, or it could never update.
#
# The app runs on the desktop's own GTK, accessibility bridge, WebKitGTK and
# speech-dispatcher, never copies built in: those are what Orca talks to and
# what the desktop's theme and settings are for, and a copied WebKitGTK can't
# find its helper programs on another system. What stays in the app is
# Python, wxWidgets and the image libraries distributions name differently
# (libjpeg.so.62 on Debian, .8 on Ubuntu). So the PC it's built on sets the
# oldest Linux it runs on: CI builds on Ubuntu 24.04 (glibc 2.39). Libraries
# every distribution names alike (OpenSSL, zlib) are newer on any later one,
# and the copy in the app would be loaded first: Ubuntu 24.04's OpenSSL
# stopped Debian 13's libcurl from loading, and wxWidgets with it.
#
# Needs: .venv (linuxsetup.sh makes it), objdump (binutils), the Python
# shared library (libpython3.X), mksquashfs (squashfs-tools), and vpk
# (dotnet tool install -g vpk --version 1.2.161, which needs the .NET SDK).
# With xvfb-run and xdotool it also checks the AppImage opens its window.
# ============================================================================
set -euo pipefail

OUTPUT_DIR="${1:-}"
if [ -n "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR" || { echo "Couldn't make $OUTPUT_DIR"; exit 1; }
    OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

PY=.venv/bin/python
APP=dist/TheChatPlace
VPK_VERSION=1.2.161              # the velopack library's version in requirements.txt
RELEASES=releases

fail() { echo ""; echo "BUILD FAILED: $*"; exit 1; }

case "$(uname -m)" in
    x86_64|amd64)  ARCH=x64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) fail "this builds for x64 and arm64, and this PC is $(uname -m)." ;;
esac
CHANNEL="linux-$ARCH"            # updater.CHANNEL on this processor
APPIMAGE="$RELEASES/TheChatPlace-$CHANNEL.AppImage"

# The built app imports everything and checks its data files, without opening
# a window. The AppImage is run unpacked (APPIMAGE_EXTRACT_AND_RUN), so the
# build doesn't need FUSE.
smoke_test() {
    local program="$1" stage="$2"
    rm -f smoke-local.json
    APPIMAGE_EXTRACT_AND_RUN=1 "$program" --smoke-test "$PWD/smoke-local.json" \
        || { cat smoke-local.json 2>/dev/null; fail "the $stage app's smoke test."; }
    echo "  $stage app: $(tr -d '\n' < smoke-local.json | sed 's/.*"problems": //')"
}

echo "========================================================================"
echo "Building The Chat Place for Linux ($ARCH)"
echo "========================================================================"

# The smoke tests import everything but never open a window. This starts the
# AppImage as a user would, on a virtual screen with a home folder of its own
# (no sessions, no settings), and waits for its window (#230). It needs
# xvfb-run and xdotool; without them it says so and goes on.
window_test() {
    if ! command -v xvfb-run >/dev/null 2>&1 || ! command -v xdotool >/dev/null 2>&1; then
        echo "  window: not checked (needs xvfb-run and xdotool)"
        return
    fi
    local home status=0
    home="$(mktemp -d)"
    HOME="$home" XDG_CONFIG_HOME="$home/.config" XDG_DATA_HOME="$home/.local/share" \
        APPIMAGE_EXTRACT_AND_RUN=1 GDK_BACKEND=x11 \
        timeout 180 xvfb-run -a -s "-screen 0 1920x1080x24" bash -c '
            "$1" > "$2/app.log" 2>&1 &
            app=$!
            for _ in $(seq 90); do
                if xdotool search --name "The Chat Place" > /dev/null 2>&1; then
                    kill "$app"; exit 0
                fi
                kill -0 "$app" 2> /dev/null || exit 2
                sleep 1
            done
            kill "$app"; exit 1' _ "$1" "$home" || status=$?
    case $status in
        0) echo "  window: opened" ;;
        2) tail -40 "$home/app.log"; fail "the AppImage quit before its window opened." ;;
        *) tail -40 "$home/app.log"; fail "the AppImage's window didn't open within 90 seconds." ;;
    esac
}

# ---------------------------------------------------------------- tools ----
if [ ! -x "$PY" ]; then
    echo ""
    echo "No .venv yet: running linuxsetup.sh..."
    ./linuxsetup.sh --yes || fail "linuxsetup.sh couldn't set up .venv."
fi
"$PY" -m pip install --quiet -r requirements-build.txt || fail "pip install"
command -v objdump >/dev/null 2>&1 || fail "PyInstaller needs objdump: sudo apt install binutils"
command -v mksquashfs >/dev/null 2>&1 || fail "vpk needs mksquashfs: sudo apt install squashfs-tools"

# The folder .NET itself is in, which vpk needs. Not just any folder by that
# name: ~/.dotnet also exists once `dotnet tool` has installed vpk there, and
# with the distribution's .NET in /usr/lib/dotnet it holds only the tools, so
# a second build failed (#230).
if [ -z "${DOTNET_ROOT:-}" ]; then
    if command -v dotnet >/dev/null 2>&1; then
        export DOTNET_ROOT="$(dirname "$(readlink -f "$(command -v dotnet)")")"
    else
        for root in "$HOME/.dotnet" /usr/share/dotnet /usr/lib/dotnet; do
            if [ -x "$root/dotnet" ]; then export DOTNET_ROOT="$root"; break; fi
        done
    fi
fi
VPK="$(command -v vpk || echo "$HOME/.dotnet/tools/vpk")"
if [ ! -x "$VPK" ] || ! "$VPK" -h 2>/dev/null | grep -q "$VPK_VERSION"; then
    command -v dotnet >/dev/null 2>&1 || [ -x "${DOTNET_ROOT:-/nonexistent}/dotnet" ] \
        || fail "vpk needs the .NET SDK (https://learn.microsoft.com/dotnet/core/install/linux), then run this again."
    DOTNET="$(command -v dotnet || echo "$DOTNET_ROOT/dotnet")"
    echo "Installing vpk $VPK_VERSION..."
    "$DOTNET" tool update -g vpk --version "$VPK_VERSION" >/dev/null || fail "installing vpk"
    VPK="$HOME/.dotnet/tools/vpk"
    "$VPK" -h >/dev/null 2>&1 || fail "vpk is installed but won't start: set DOTNET_ROOT to the .NET folder."
fi

# ------------------------------------------------------------------ tests ----
# The window tests need a display; a build without one gets a virtual one.
# GDK_BACKEND=x11 keeps GTK on it: a build over ssh while the same user is
# logged in to a Wayland desktop otherwise finds that desktop's wayland-0,
# and wx sees no screen at all (#230).
echo ""
echo "Running the tests..."
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
    GDK_BACKEND=x11 xvfb-run -a -s "-screen 0 1920x1080x24" "$PY" -m pytest -q tests \
        || fail "the tests."
else
    "$PY" -m pytest -q tests || fail "the tests."
fi

VERSION=$("$PY" tools/check_version.py) || fail "tools/check_version.py"
echo ""
echo "The Chat Place $VERSION ($CHANNEL)"

# -------------------------------------------------------------------- app ----
# --onedir: Velopack swaps the app's folder whole. velopack is imported
# lazily, so it's named; the speech scripts are data files.
echo ""
echo "Building the app (PyInstaller)..."
rm -rf "$APP"
"$PY" -m PyInstaller --noconfirm --clean --windowed --onedir --name TheChatPlace \
    --hidden-import velopack --hidden-import thechatplace.providers.codex \
    --hidden-import thechatplace.providers.codex_runtime --hidden-import thechatplace.providers.copilot \
    --hidden-import thechatplace.providers.copilot_runtime \
    --add-data "thechatplace/assets:thechatplace/assets" \
    --add-data "thechatplace/speech:thechatplace/speech" \
    TheChatPlace.pyw || fail "PyInstaller"
[ -x "$APP/TheChatPlace" ] || fail "no $APP/TheChatPlace"

# The system's own libraries out (see the top): everything PyInstaller
# copied except Python's, wxWidgets' and the image ones named per distribution.
KEEP='^lib(python3|wx_|mpdec|jpeg|png|tiff|Lerc|jbig|deflate|webp|sharpyuv)'
removed=0
for library in "$APP"/_internal/lib*.so*; do
    if ! [[ "$(basename "$library")" =~ $KEEP ]]; then
        rm -f "$library"
        removed=$((removed + 1))
    fi
done
echo "  Left $removed libraries to the system's own copies."

echo ""
echo "Smoke tests:"
smoke_test "$APP/TheChatPlace" built

# ---------------------------------------------------------------- Velopack ----
echo ""
echo "Packing the AppImage and update feed (vpk, channel $CHANNEL)..."
if [ "${TCP_KEEP_RELEASES:-0}" != "1" ]; then
    rm -rf "$RELEASES"
fi
mkdir -p "$RELEASES"
VPK_ARGS=(pack --packId TheChatPlace --packVersion "$VERSION" --packDir "$APP"
          --mainExe TheChatPlace --packTitle "The Chat Place" --packAuthors "Kelly Ford"
          --icon thechatplace/assets/app.png --categories "Utility;Development"
          --channel "$CHANNEL" --runtime "linux-$ARCH" --outputDir "$RELEASES")
[ -f "release-notes/v$VERSION.md" ] && VPK_ARGS+=(--releaseNotes "release-notes/v$VERSION.md")
"$VPK" "${VPK_ARGS[@]}" || fail "vpk pack"
[ -f "$APPIMAGE" ] || fail "vpk made no $APPIMAGE"
chmod +x "$APPIMAGE"

smoke_test "$PWD/$APPIMAGE" packed
grep -q "\"updater\": \"$VERSION\"" smoke-local.json \
    || { cat smoke-local.json; fail "Velopack doesn't see the AppImage as updatable."; }
window_test "$PWD/$APPIMAGE"

if [ -n "$OUTPUT_DIR" ]; then
    cp "$APPIMAGE" "$OUTPUT_DIR/" || fail "couldn't copy into $OUTPUT_DIR"
fi

echo ""
echo "========================================================================"
echo "BUILD SUCCESSFUL: The Chat Place $VERSION ($CHANNEL)"
echo "========================================================================"
echo "  AppImage:    $APPIMAGE"
echo "  Update feed: $RELEASES/releases.$CHANNEL.json and the packages beside it"
echo "  App:         $APP (before Velopack; the AppImage holds the packed one)"
[ -n "$OUTPUT_DIR" ] && echo "  Copied to:   $OUTPUT_DIR"
exit 0
