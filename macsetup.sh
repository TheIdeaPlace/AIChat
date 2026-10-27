#!/bin/bash
# ============================================================================
# macOS setup for The Chat Place
# ============================================================================
# Makes the repo's .venv and installs everything the app, its tests and the
# Mac build need (requirements-build.txt), then checks that it all imports.
# Modelled on Image Description Toolkit's macsetup.sh.
#
#   ./macsetup.sh          set up (an existing .venv is replaced)
#   ./macsetup.sh --yes    the same, without the two "press Enter" prompts
#                          (the Mac build uses this when there's no .venv)
#
# Or double-click macsetup.command in Finder.
#
# Then:
#   .venv/bin/python -m thechatplace     run the app from source
#   BuildAndRelease/MacBuilds/build_macos.sh   build the app, update feed and .dmg
# ============================================================================

cd "$(dirname "$0")" || exit 1

ASK=1
[ "${1:-}" = "--yes" ] && ASK=0

echo ""
echo "========================================================================"
echo "macOS setup for The Chat Place"
echo "========================================================================"
echo ""
echo "This makes .venv in $(pwd)"
echo "and installs wxPython, PyInstaller and the rest into it."
echo ""
[ $ASK -eq 1 ] && read -r -p "Press Enter to continue or Ctrl+C to cancel..."

# wxPython publishes Mac wheels for these; a newer Python can mean building
# wxPython from source, which takes most of an hour and often fails.
# TCP_PYTHON chooses one (the release workflow passes the one it set up).
PYTHON="${TCP_PYTHON:-}"
for candidate in python3.13 python3.12 python3.11; do
    [ -n "$PYTHON" ] && break
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        PYTHON="$(command -v python3)"
    fi
fi
if [ -z "$PYTHON" ]; then
    echo "ERROR: The Chat Place needs Python 3.11, 3.12 or 3.13."
    echo "       Install one from python.org, or: brew install python@3.13"
    exit 1
fi
echo "Python: $PYTHON ($("$PYTHON" --version 2>&1))"
echo ""

if [ -d .venv ]; then
    echo "Removing the old .venv..."
    rm -rf .venv
fi

echo "Creating .venv..."
if ! "$PYTHON" -m venv .venv; then
    echo "ERROR: couldn't create .venv"
    exit 1
fi

echo "Installing (requirements-build.txt)..."
.venv/bin/python -m pip install --upgrade pip
if ! .venv/bin/python -m pip install -r requirements-build.txt; then
    echo ""
    echo "ERROR: pip couldn't install everything. Scroll up for the reason."
    echo "       Common causes: no internet, or no wxPython wheel for this Python."
    exit 1
fi

# pip's exit code isn't proof the environment works (IDT learnt this the hard
# way): check the imports the app and the build need.
PROBLEMS=0
for module in wx wx.html2 markdown theideaplace_appkit PyInstaller pytest; do
    if .venv/bin/python -c "import $module" 2>/dev/null; then
        echo "  ok       $module"
    else
        echo "  MISSING  $module"
        PROBLEMS=$((PROBLEMS + 1))
    fi
done

echo ""
echo "Other things The Chat Place and its build use:"
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
    echo "  ok       claude (Claude Code)"
else
    echo "  missing  claude: install Claude Code and sign in, or the app can only read sessions"
fi
if command -v dotnet >/dev/null 2>&1 || [ -x /opt/homebrew/opt/dotnet/bin/dotnet ]; then
    echo "  ok       .NET SDK (the build installs vpk, Velopack's packer, with it)"
else
    echo "  missing  .NET SDK: brew install dotnet (needed only to build the app)"
fi
if xcode-select -p >/dev/null 2>&1; then
    echo "  ok       Xcode command line tools (codesign, notarytool)"
else
    echo "  missing  Xcode command line tools: xcode-select --install"
    echo "           (needed only to sign and notarize a build)"
fi

echo ""
echo "========================================================================"
if [ $PROBLEMS -eq 0 ]; then
    echo "SETUP COMPLETE"
    echo "========================================================================"
    echo "  Run from source:  .venv/bin/python -m thechatplace"
    echo "  Run the tests:    .venv/bin/python -m pytest -q tests"
    echo "  Build the app:    BuildAndRelease/MacBuilds/build_macos.sh (or double-click build_macos.command)"
else
    echo "SETUP FAILED: $PROBLEMS module(s) won't import (see above)"
    echo "========================================================================"
fi
echo ""
[ $ASK -eq 1 ] && read -r -p "Press Enter to close..."
exit $PROBLEMS
