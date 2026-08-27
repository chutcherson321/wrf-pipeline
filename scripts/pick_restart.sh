#!/usr/bin/env bash
# Pick the newest COMPLETE restart set in a wrf run dir, prune the rest, and
# print its timestamp. "Complete" = d01 and d02 both present, plus any inner
# nests that are still running, and byte-identical in size to an earlier set
# (restart files for a given domain are constant-size, so a mismatch means
# wrf.exe was killed mid-write).
#
# d03+ is treated as optional on purpose: a short-range inner nest stops
# partway through the forecast (see segment_namelist.py --nest-hours), so
# later timestamps legitimately carry fewer domains. d01+d02 run the whole
# way, so requiring them still catches a truncated write.
# Usage: pick_restart.sh RUN_WRF_DIR
set -euo pipefail
DIR="$1"

TS=()
while IFS= read -r line; do TS+=("$line"); done < <(
  ls "$DIR"/wrfrst_d01_* 2>/dev/null | sed 's/.*wrfrst_d01_//' | sort)
[ "${#TS[@]}" -gt 0 ] || { echo "no restart files in $DIR" >&2; exit 1; }

# doms_at TIMESTAMP -> prints the domains present, contiguous from d01.
doms_at() {
  local t="$1" n
  for n in 01 02 03 04; do
    [ -f "$DIR/wrfrst_d${n}_$t" ] || break
    echo "d$n"
  done
}

# Keep only timestamps with at least the two always-on domains.
PAIRS=()
for t in "${TS[@]}"; do
  [ -f "$DIR/wrfrst_d02_$t" ] && PAIRS+=("$t")
done
[ "${#PAIRS[@]}" -gt 0 ] || { echo "no complete d01+d02 restart set" >&2; exit 1; }

PICK="${PAIRS[$((${#PAIRS[@]}-1))]}"
if [ "${#PAIRS[@]}" -ge 2 ]; then
  PREV="${PAIRS[$((${#PAIRS[@]}-2))]}"
  # Compare only domains present in both: an inner nest that has finished is
  # absent from the newer set, which is not a partial write.
  CHECK=()
  while IFS= read -r line; do CHECK+=("$line"); done < <(
    comm -12 <(doms_at "$PICK") <(doms_at "$PREV"))
  for d in "${CHECK[@]}"; do
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
