#!/bin/bash
# ============================================================================
# The Chat Place's disk image: TheChatPlace.app, a link to Applications to
# drag it onto, and a short README.txt. From Image Description Toolkit's
# create_macos_dmg.sh, without its Finder window styling. The app must be the
# one vpk packed (build_macos.sh passes it), which carries Velopack's updater.
# ============================================================================
#   bash BuildAndRelease/MacBuilds/create_dmg.sh <packed TheChatPlace.app> releases/TheChatPlace-macos-arm64.dmg <version> [signed]
#
# With "signed" the README leaves out the unsigned-build note.
# ============================================================================
set -euo pipefail

APP="${1:?Usage: $0 <app> <dmg> <version> [signed]}"
DMG="${2:?Usage: $0 <app> <dmg> <version> [signed]}"
VERSION="${3:?Usage: $0 <app> <dmg> <version> [signed]}"
SIGNED="${4:-}"
VOLUME="The Chat Place"

[ -d "$APP" ] || { echo "ERROR: not found: $APP"; exit 1; }
mkdir -p "$(dirname "$DMG")"

STAGING=$(mktemp -d)
WORK=$(mktemp -d)
MOUNT="$WORK/mount"
cleanup() {
    hdiutil detach "$MOUNT" -force >/dev/null 2>&1 || true
    rm -rf "$STAGING" "$WORK"
}
trap cleanup EXIT

# ditto keeps signatures and extended attributes, which cp can lose.
APP_NAME="$(basename "$APP")"
ditto "$APP" "$STAGING/$APP_NAME"

cat > "$STAGING/README.txt" <<README
The Chat Place $VERSION
=======================

A keyboard and screen reader friendly home for your AI agent chats.

To install, drag $APP_NAME onto Applications, then open it from
Applications (or with Spotlight: Cmd+Space, "The Chat Place").

You need Claude Code installed and signed in to a Claude subscription
(https://claude.com/claude-code). The Chat Place never uses an API key.

The Chat Place updates itself wherever you keep it, once it's out of this
disk image: it says when a new version is out, and Help, Check for Updates
installs it. Your sessions and
settings are in ~/Library/Application Support/TheChatPlace and are kept.
README
if [ "$SIGNED" != "signed" ]; then
    cat >> "$STAGING/README.txt" <<'README'

This build isn't signed by Apple, so the first time you open it macOS says it
can't check it. Open System Settings, Privacy & Security, scroll to Security,
and choose Open Anyway next to The Chat Place; then open it again.
README
fi

# The Applications link is made on the mounted image, not in the staging
# folder: IDT found that hdiutil's -srcfolder can follow it and try to copy
# all of /Applications.
echo "Creating the disk image..."
hdiutil create -srcfolder "$STAGING" -volname "$VOLUME" -fs APFS -format UDRW \
    -ov "$WORK/rw.dmg" >/dev/null
mkdir -p "$MOUNT"
hdiutil attach "$WORK/rw.dmg" -readwrite -noverify -noautoopen -nobrowse -mountpoint "$MOUNT" >/dev/null
ln -s /Applications "$MOUNT/Applications"
sync
# Spotlight or fseventsd can still hold a just-mounted image ("Resource busy",
# seen on a CI runner), so try a few times, then force it as IDT and GHManage do.
for attempt in 1 2 3 4 5; do
    hdiutil detach "$MOUNT" >/dev/null 2>&1 && break
    if [ "$attempt" = 5 ]; then hdiutil detach "$MOUNT" -force >/dev/null; break; fi
    sleep 2
done

rm -f "$DMG"
hdiutil convert "$WORK/rw.dmg" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
echo "Disk image: $DMG"
