#!/bin/bash
# A desktop of its own for The Chat Place's Linux accessibility checks: a
# session bus, a virtual screen (Xvfb), a window manager and an accessibility
# bus, then the command given, run from the repo. Nothing touches your own
# desktop or Orca, so it runs in CI and over ssh alike (#230).
#
#   tools/linux_a11y_session.sh COMMAND [ARGS...]
#
# tools/orca_speech.sh runs Orca in it; CI also runs the visual probe's Orca
# audit (TCP_PROBE_ORCA=1 tools/ui_probe.py) in it.
set -uo pipefail

if [ -z "${TCP_A11Y_SESSION:-}" ]; then
    export TCP_A11Y_SESSION=1
    exec dbus-run-session -- xvfb-run -a -s "-screen 0 1920x1080x24" "$0" "$@"
fi
cd "$(dirname "$0")/.." || exit 1

# GTK on the virtual screen, not a Wayland desktop this user may also be on.
export GDK_BACKEND=x11
unset WAYLAND_DISPLAY
# Orca's settings in folders of this run's. Orca won't start without a
# runtime folder, which sudo and a CI runner don't give.
export XDG_CONFIG_HOME="$(mktemp -d)" XDG_DATA_HOME="$(mktemp -d)"
if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -w "$XDG_RUNTIME_DIR" ]; then
    XDG_RUNTIME_DIR="$(mktemp -d)"; chmod 700 "$XDG_RUNTIME_DIR"; export XDG_RUNTIME_DIR
fi
# Orca buffers its debug log, so a check reading it while Orca runs saw
# nothing; Orca runs orca-customizations.py at start, with the log open.
mkdir -p "$XDG_DATA_HOME/orca"
cat > "$XDG_DATA_HOME/orca/orca-customizations.py" <<'PY'
from orca import debug
if debug.debugFile is not None:
    debug.debugFile.reconfigure(line_buffering=True)
PY
for launcher in /usr/libexec/at-spi-bus-launcher /usr/lib/at-spi2-core/at-spi-bus-launcher; do
    if [ -x "$launcher" ]; then "$launcher" --launch-immediately >/dev/null 2>&1 & break; fi
done
# A window manager, as every desktop has: without one, closing a dialog
# gives the keyboard focus to nothing, and Orca says nothing.
if command -v openbox >/dev/null 2>&1; then openbox --sm-disable >/dev/null 2>&1 & fi
sleep 1
# No gsettings here: with a fresh settings folder they'd only reach a store
# that forgets them, and nothing needs them. Orca is started directly, and
# GTK 3 loads its accessibility bridge whenever the bus is there.
"$@"
