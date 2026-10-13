#!/bin/bash
# ============================================================================
# Linux setup for The Chat Place
# ============================================================================
# Makes the repo's .venv and installs everything the app, its tests and the
# Linux build need (requirements-build.txt), then checks that it all imports.
# The Linux counterpart of macsetup.sh and winsetup.bat.
#
#   ./linuxsetup.sh          set up (an existing .venv is replaced)
#   ./linuxsetup.sh --yes    the same, without the two "press Enter" prompts
#                            (the Linux build uses this when there's no .venv)
#
# Then:
#   .venv/bin/python -m thechatplace                  run the app from source
#   BuildAndRelease/LinuxBuilds/build_linux.sh        build the AppImage and update feed
#
# wxPython publishes no Linux wheels on PyPI, and building it takes an hour
# or more (longer on an ARM board). So .venv uses your distribution's
# wxPython (python3-wxgtk4.0 on Debian and Ubuntu), through
# --system-site-packages, with the system's Python. TCP_PYTHON chooses
# another Python, which must then have wxPython of its own.
# ============================================================================

cd "$(dirname "$0")" || exit 1

ASK=1
[ "${1:-}" = "--yes" ] && ASK=0

echo ""
echo "========================================================================"
echo "Linux setup for The Chat Place"
echo "========================================================================"
echo ""
echo "This makes .venv in $(pwd)"
echo "with your distribution's wxPython, and installs PyInstaller and the rest."
echo ""
[ $ASK -eq 1 ] && read -r -p "Press Enter to continue or Ctrl+C to cancel..."

# What to install for wxPython, and the rest the app uses, on each family.
. /etc/os-release 2>/dev/null
case " ${ID:-} ${ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*)
        PACKAGES="python3-venv python3-wxgtk4.0 python3-wxgtk-webview4.0 speech-dispatcher orca xdg-utils libfuse2t64 (libfuse2 before Debian 13 and Ubuntu 24.04)"
        BUILD_PACKAGES="binutils squashfs-tools libpython3-dev xvfb" ;;
    *" fedora "*|*" rhel "*)
        PACKAGES="python3-wxpython4 speech-dispatcher orca xdg-utils fuse-libs"
        BUILD_PACKAGES="binutils squashfs-tools python3-devel xorg-x11-server-Xvfb" ;;
    *" arch "*)
        PACKAGES="python-wxpython speech-dispatcher orca xdg-utils fuse2"
        BUILD_PACKAGES="binutils squashfs-tools xorg-server-xvfb" ;;
    *)
        PACKAGES="your distribution's wxPython 4.2 for Python 3, speech-dispatcher, Orca, xdg-utils, FUSE 2"
        BUILD_PACKAGES="binutils, squashfs-tools, the Python shared library, Xvfb" ;;
esac

PYTHON="${TCP_PYTHON:-/usr/bin/python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "ERROR: The Chat Place needs Python 3.11 or newer ($PYTHON isn't)."
    exit 1
fi
if ! "$PYTHON" -c 'import wx' 2>/dev/null; then
    echo "ERROR: $PYTHON has no wxPython. Install your distribution's:"
    echo "       $PACKAGES"
    exit 1
fi
echo "Python:   $PYTHON ($("$PYTHON" --version 2>&1))"
echo "wxPython: $("$PYTHON" -c 'import wx; print(wx.version())')"
echo ""

if [ -d .venv ]; then
    echo "Removing the old .venv..."
    rm -rf .venv
fi

echo "Creating .venv..."
if ! "$PYTHON" -m venv --system-site-packages .venv; then
    echo "ERROR: couldn't create .venv (on Debian and Ubuntu: sudo apt install python3-venv)"
    exit 1
fi

echo "Installing (requirements-build.txt)..."
.venv/bin/python -m pip install --upgrade pip
if ! .venv/bin/python -m pip install -r requirements-build.txt; then
    echo ""
    echo "ERROR: pip couldn't install everything. Scroll up for the reason."
    exit 1
fi

# pip's exit code isn't proof the environment works: check the imports the
# app and the build need.
PROBLEMS=0
for module in wx markdown velopack truststore PyInstaller pytest; do
    if .venv/bin/python -c "import $module" 2>/dev/null; then
        echo "  ok       $module"
    else
        echo "  MISSING  $module"
        PROBLEMS=$((PROBLEMS + 1))
    fi
done
if .venv/bin/python -c "import wx.html2" 2>/dev/null; then
    echo "  ok       wx.html2 (formatted messages, if you turn them on in Settings)"
else
    echo "  missing  wx.html2: full messages open as plain text (python3-wxgtk-webview4.0 adds it)"
fi

echo ""
echo "Other things The Chat Place and its build use:"
check() {  # check COMMAND DESCRIPTION WHAT-IF-MISSING
    if command -v "$1" >/dev/null 2>&1; then
        echo "  ok       $2"
    else
        echo "  missing  $2: $3"
    fi
}
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
    echo "  ok       claude (Claude Code)"
else
    echo "  missing  claude: install Claude Code and sign in, or the app can only read sessions"
fi
check orca "Orca" "the screen reader the app is made for"
check spd-say "speech-dispatcher" "announcements need it when Orca can't take them"
check busctl "busctl (Orca 49 and later take announcements through it)" "gdbus or dbus-send do too"
check xdg-open "xdg-utils" "links and files open in other programs through it"
check dotnet ".NET SDK (the build installs vpk, Velopack's packer, with it)" "needed only to build the AppImage"
check objdump "binutils" "needed only to build"
check mksquashfs "squashfs-tools" "needed only to build"

echo ""
echo "========================================================================"
if [ $PROBLEMS -eq 0 ]; then
    echo "SETUP COMPLETE"
    echo "========================================================================"
    echo "  Run from source:  .venv/bin/python -m thechatplace"
    echo "  Run the tests:    .venv/bin/python -m pytest -q tests"
    echo "  Build the app:    BuildAndRelease/LinuxBuilds/build_linux.sh"
    echo "  Packages to build with: $BUILD_PACKAGES"
else
    echo "SETUP FAILED: $PROBLEMS module(s) won't import (see above)"
    echo "========================================================================"
fi
echo ""
[ $ASK -eq 1 ] && read -r -p "Press Enter to close..."
exit $PROBLEMS
