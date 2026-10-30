#!/bin/bash
# What Orca says as you use The Chat Place, checked a key at a time (Linux).
# See tools/orca_speech.py. It runs in a desktop of its own
# (tools/linux_a11y_session.sh), so it leaves yours alone.
#
#   tools/orca_speech.sh [--record] [--json out.json] [--orca-log orca.log]
cd "$(dirname "$0")/.." || exit 1
exec tools/linux_a11y_session.sh "${TCP_PYTHON:-.venv/bin/python}" tools/orca_speech.py check "$@"
