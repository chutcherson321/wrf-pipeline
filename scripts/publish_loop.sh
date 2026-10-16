#!/usr/bin/env bash
# Publish partial results while wrf.exe is still running.
#
# wrf.exe writes a wrfout file every `frames_per_outfile` history steps (12,
# i.e. one file per 12 forecast hours). A file is complete once the NEXT one
# appears, so we stage every file except the newest and publish those. The run
# therefore reaches the page ~30 min after it starts and grows in 12-hour
# blocks instead of arriving 6.5 h later in one lump.
#
# "Every file but the newest" is NOT enough on its own. An inner nest stops
# early (Supertubos d03: +24h on 00Z, +36h on 12Z) and wrf.exe keeps its LAST
# file open until the whole run exits, long after the count below has called
# it closed. Every candidate therefore also has
# to pass $PROBE (scripts/wrfout_ready.py): it must open, and its last frame
# must be whole. Only the leading run that passes is staged, so a held-open
# d03 file is skipped and d02's later hours still publish. See wrfout_ready.py
# for why "opens" alone is not enough.
#
# Runs as a background process alongside the wrf stage and exits when STOP_FILE
# appears. Deliberately `nice`d — wrf.exe owns the cores that matter. Kept to
# POSIX-ish shell (no mapfile) so it runs the same on a runner and on a Mac.
# Usage: publish_loop.sh SITE FORCING CYCLE RUN_DIR STOP_FILE EXPECT_HOURS
set -uo pipefail

SITE="$1"; FORCING="$2"; CYCLE="$3"; RUN_DIR="$4"; STOP="$5"; EXPECT="${6:-0}"
INTERVAL="${PUBLISH_POLL_SEC:-120}"
PUBLISHER="${PUBLISHER:-scripts/publish_r2.sh}"
PROBE="${PROBE:-python scripts/wrfout_ready.py}"
STAGE="partial_frames"
# Integration started when this loop did; publish_r2.sh derives the rate from it.
SINCE=$(date +%s)
LIST=".publish_loop_files"
LIST3=".publish_loop_files_d03"
# Files that passed the probe once. A closed file never changes again, so each
# is read once rather than on every tick.
READY=".publish_loop_ready"
: > "$READY"
last_count=0

# stdin: candidate paths in order. stdout: the leading run of them that is
# ready -- cached passes first, then ONE probe call for the rest. A file that
# fails is not cached, so it is asked about again next tick.
ready_prefix(){
  local f; local todo=()
  while read -r f; do
    if [ ${#todo[@]} -eq 0 ] && grep -qxF -- "$f" "$READY"; then
      echo "$f"; continue
    fi
    todo+=("$f")
  done
  [ ${#todo[@]} -eq 0 ] && return 0
  # $PROBE is word-split on purpose: it is a command line, e.g. "python x.py".
  $PROBE "${todo[@]}" 2>&1 >"$READY.new" | sed 's/^/publish_loop: /' >&2
  cat "$READY.new"; cat "$READY.new" >> "$READY"; rm -f "$READY.new"
}

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
  # ...and anything that will not read cleanly yet (normally nothing on d02).
  head -n "$closed" "$LIST" | ready_prefix > "$LIST.ok"
  closed=$(wc -l < "$LIST.ok" | tr -d ' ')
  [ "$closed" -lt 1 ] && continue
  [ "$closed" -le "$last_count" ] && continue

  rm -rf "$STAGE"; mkdir -p "$STAGE"
  while read -r f; do
    [ -f "$f" ] && ln -sf "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" "$STAGE/"
  done < "$LIST.ok"
  # Inner nest (1 km d03), when the site runs one. Its files open on the same
  # 12-hour boundaries as d02's from the same start, so the first `closed`
  # of them are closed exactly when d02's are. Staging the same count keeps
  # the two domains in step without parsing timestamps. The exception is the
  # nest's LAST file (one frame, at its end hour): wrf.exe holds it open until
  # the run exits, so the probe drops it, the series takes d03 to the hour
  # before and d02 from there (domain_merge ends the nest at its last frame
  # present), and the final publish after wrf.exe exits puts that hour back.
  ls -1 "$RUN_DIR"/wrf/wrfout_d03_* 2>/dev/null | sort > "$LIST3" || true
  head -n "$closed" "$LIST3" | ready_prefix | while read -r f; do
    [ -f "$f" ] && ln -sf "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" "$STAGE/"
  done

  echo "publish_loop: $closed closed file(s) — publishing"
  # Cap a single publish: one stalled upload otherwise wedges the loop, and a
  # wedged loop used to take the whole job down with it.
  #
  # Both wrappers are BEST EFFORT. `timeout` is GNU coreutils -- present on the
  # runners, absent on macOS -- and `nice` cannot always lower priority in a
  # sandbox. Hard-requiring either made this script unrunnable outside CI, which
  # is the same portability trap as `mapfile`. Missing timeout means no cap, and
  # the workflow's bounded shutdown is the backstop.
  RUN=()
  command -v timeout >/dev/null 2>&1 && RUN+=(timeout "${PUBLISH_MAX_SEC:-900}")
  command -v nice    >/dev/null 2>&1 && RUN+=(nice -n 10)
  if INNER_GLOB="$STAGE/wrfout_d03_*" ${RUN[@]+"${RUN[@]}"} "$PUBLISHER" \
       "$SITE" "$FORCING" "$CYCLE" "$STAGE/wrfout_d02_*" "$EXPECT" "$SINCE"; then
    last_count="$closed"
  else
    echo "publish_loop: publish failed, will retry next tick" >&2
  fi
done
rm -f "$LIST" "$LIST.ok" "$LIST3" "$READY"
echo "publish_loop: stop file seen, exiting"
