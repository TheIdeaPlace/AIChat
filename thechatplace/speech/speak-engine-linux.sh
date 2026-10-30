#!/usr/bin/env bash
# The Chat Place speaker (Linux). Speaks a text file in a system voice.
#
# Orca is reached from the app's own process (orca.py); this script is the
# system voice, used when Orca isn't running or can't take the text.
#
# Routes:
#   speechd    - speech-dispatcher (spd-say), the speech server Orca uses too.
#                It speaks one message at a time, so this never talks over
#                Orca; at the "text" priority, Orca's next words cut it off.
#   espeak-ng  - when there's no speech-dispatcher.
#
# Config (JSON, written by speech.py): engine, voice (a speech-dispatcher
# synthesis voice name, "" for the default), rate (-100 to 100, or null).
#
# Never throws: a speech failure must not break the app.
set -uo pipefail

path=""
config=""
while [ $# -gt 0 ]; do
    case "$1" in
        --path|-p)   path="${2:-}"; shift 2 ;;
        --config|-c) config="${2:-}"; shift 2 ;;
        *) [ -z "$path" ] && path="$1"; shift ;;
    esac
done

[ -n "$path" ] && [ -s "$path" ] || exit 0

# One value from the config. jq when there is one; otherwise sed, which is
# enough for the flat object speech.py writes.
value() {
    [ -n "$config" ] && [ -f "$config" ] || return 0
    if command -v jq >/dev/null 2>&1; then
        jq -r ".$1 // empty" "$config" 2>/dev/null
    else
        sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" "$config" | head -1 \
            | sed 's/^null$//'
    fi
}

voice=$(value voice)
rate=$(value rate)

WORKDIR="$(dirname "$path")"
log_route() {
    printf '%s  used=%s  voice=%s  rate=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" \
        "$1" "${voice:-<system default>}" "${rate:-<default>}" \
        > "$WORKDIR/last-route.log" 2>/dev/null || true
}

# -w waits until it has been spoken (or cut off), so this process lasts as
# long as the speech, as the other platforms' engines do.
if command -v spd-say >/dev/null 2>&1; then
    args=(-w -P text -e)
    [ -n "$voice" ] && args+=(-y "$voice")
    if [ -n "$rate" ]; then args+=(-r "${rate%.*}"); fi
    log_route speechd
    # No cancel here: speech-dispatcher's cancels every client's speech,
    # Orca's too. The app makes it, only when its own voice was cut off and
    # Orca wasn't running.
    # -e (read stdin) sends a line starting with !-! to speech-dispatcher as
    # a command, not text: a reply could otherwise silence or change every
    # voice, Orca's too. A space in front makes it text again.
    sed 's/^!-!/ !-!/' "$path" | spd-say "${args[@]}" && exit 0
fi

for program in espeak-ng espeak; do
    if command -v "$program" >/dev/null 2>&1; then
        log_route "$program"
        exec "$program" -f "$path"
    fi
done
log_route none
exit 0
