#!/usr/bin/env bash
# Publish partial results while wrf.exe is still running.
#
# wrf.exe writes a wrfout file every `frames_per_outfile` history steps (12,
# i.e. one file per 12 forecast hours). A file is complete once the NEXT one
# appears, so we stage every file except the newest and publish those. The run
# therefore reaches the page ~30 min after it starts and grows in 12-hour
# blocks instead of arriving 6.5 h later in one lump.
#
# Runs as a background process alongside the wrf stage and exits when STOP_FILE
# appears. Deliberately `nice`d — wrf.exe owns the cores that matter. Kept to
# POSIX-ish shell (no mapfile) so it runs the same on a runner and on a Mac.
# Usage: publish_loop.sh SITE FORCING CYCLE RUN_DIR STOP_FILE EXPECT_HOURS
set -uo pipefail

SITE="$1"; FORCING="$2"; CYCLE="$3"; RUN_DIR="$4"; STOP="$5"; EXPECT="${6:-0}"
INTERVAL="${PUBLISH_POLL_SEC:-120}"
PUBLISHER="${PUBLISHER:-scripts/publish_r2.sh}"
STAGE="partial_frames"
LIST=".publish_loop_files"
last_count=0

while [ ! -f "$STOP" ]; do
  sleep "$INTERVAL"
  ls -1 "$RUN_DIR"/wrf/wrfout_d02_* "$RUN_DIR"/wrf/seg*/wrfout_d02_* 2>/dev/null \
    | sort > "$LIST" || true
  n=$(wc -l < "$LIST" 2>/dev/null | tr -d ' ')
  [ -z "$n" ] && n=0
  # Exclude the newest: wrf.exe is still appending to it.
  closed=$((n - 1))
  [ "$closed" -lt 1 ] && continue
  [ "$closed" -le "$last_count" ] && continue

  rm -rf "$STAGE"; mkdir -p "$STAGE"
  head -n "$closed" "$LIST" | while read -r f; do
    [ -f "$f" ] && ln -sf "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" "$STAGE/"
  done

  echo "publish_loop: $closed closed file(s) — publishing"
  if nice -n 10 "$PUBLISHER" "$SITE" "$FORCING" "$CYCLE" \
       "$STAGE/wrfout_d02_*" "$EXPECT"; then
    last_count="$closed"
  else
    echo "publish_loop: publish failed, will retry next tick" >&2
  fi
done
rm -f "$LIST"
echo "publish_loop: stop file seen, exiting"
