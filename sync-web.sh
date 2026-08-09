#!/usr/bin/env bash
# Refresh voice-service/web/ from the scoreboard sources one directory up.
#
# The image serves the web app from this folder, so editing CABO_vX.Y.Z.1.html
# without re-running this ships a stale page — a silent failure that looks like
# "my fix did not work". Run this before every build.
#
#   bash sync-web.sh
#
# Asset names are read out of the HTML rather than derived from the version
# string: the manifest is called manifest-711.webmanifest for v0.7.1.1, and
# guessing that mapping breaks the moment the numbering changes.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
WEB="$HERE/web"

# Newest cloud build of the scoreboard: CABO_v<major>.<minor>.<patch>.1.html
APP="$(ls -1 "$SRC"/CABO_v*.1.html 2>/dev/null | sort -V | tail -1)"
[ -n "$APP" ] || { echo "ERROR: no CABO_v*.1.html found in $SRC" >&2; exit 1; }
APP_NAME="$(basename "$APP")"

# Start clean so files from older versions cannot linger in the image.
rm -rf "$WEB"
mkdir -p "$WEB/icons" "$WEB/assets"

# index.html is what the platform serves at /; the versioned copy keeps the
# service worker's precache list resolvable.
cp "$APP" "$WEB/index.html"
cp "$APP" "$WEB/$APP_NAME"

# Exactly the manifest / service worker / scripts this build references.
REFS="$(grep -oE '(manifest-[0-9]+\.webmanifest|sw-v[0-9.]+\.js|[A-Za-z0-9_.-]+\.min\.js)' "$APP" | sort -u)"
for f in $REFS; do
  if [ -f "$SRC/$f" ]; then
    cp "$SRC/$f" "$WEB/"
  else
    echo "  warn: referenced but missing in $SRC: $f" >&2
  fi
done

cp "$SRC"/icons/icon-192.png "$SRC"/icons/icon-512.png "$WEB/icons/" 2>/dev/null || true
cp "$SRC"/assets/*.jpg "$WEB/assets/" 2>/dev/null || true

echo "synced $APP_NAME -> web/ ($(du -sh "$WEB" | cut -f1), $(find "$WEB" -type f | wc -l) files)"
find "$WEB" -type f -printf '  %P\n' 2>/dev/null || find "$WEB" -type f
