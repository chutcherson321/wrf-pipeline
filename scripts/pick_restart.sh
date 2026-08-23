#!/usr/bin/env bash
# Pick the newest COMPLETE restart pair in a wrf run dir, prune the rest, and
# print its timestamp. "Complete" = wrfrst_d01 + wrfrst_d02 both present and
# byte-identical in size to an earlier pair (restart files for a given domain
# are constant-size, so a mismatch means wrf.exe was killed mid-write).
# Usage: pick_restart.sh RUN_WRF_DIR
set -euo pipefail
DIR="$1"

mapfile -t TS < <(ls "$DIR"/wrfrst_d01_* 2>/dev/null | sed 's/.*wrfrst_d01_//' | sort)
[ "${#TS[@]}" -gt 0 ] || { echo "no restart files in $DIR" >&2; exit 1; }

# Keep only timestamps with both domains present.
PAIRS=()
for t in "${TS[@]}"; do
  [ -f "$DIR/wrfrst_d02_$t" ] && PAIRS+=("$t")
done
[ "${#PAIRS[@]}" -gt 0 ] || { echo "no complete d01+d02 restart pair" >&2; exit 1; }

PICK="${PAIRS[-1]}"
if [ "${#PAIRS[@]}" -ge 2 ]; then
  PREV="${PAIRS[-2]}"
  for d in d01 d02; do
    a=$(stat -c%s "$DIR/wrfrst_${d}_$PICK" 2>/dev/null || stat -f%z "$DIR/wrfrst_${d}_$PICK")
    b=$(stat -c%s "$DIR/wrfrst_${d}_$PREV" 2>/dev/null || stat -f%z "$DIR/wrfrst_${d}_$PREV")
    if [ "$a" != "$b" ]; then
      echo "newest restart $PICK looks partial ($d: $a vs $b bytes) — using $PREV" >&2
      PICK="$PREV"
      break
    fi
  done
fi

for f in "$DIR"/wrfrst_d0*_*; do
  case "$f" in *"$PICK") ;; *) rm -f "$f" ;; esac
done
echo "$PICK"
