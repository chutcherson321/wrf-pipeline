#!/usr/bin/env bash
# Pick the newest COMPLETE restart set in a wrf run dir, prune the rest, and
# print its timestamp. "Complete" = d01 and d02 both present, plus any inner
# nests that are still running, and every one of those domains has stepped
# PAST the timestamp in rsl.error.0000 ("Timing for main: time ... on domain
# N"). WRF writes domain N's restart at T before it steps beyond T, so a
# later model time in the log proves the write finished.
#
# It used to compare file sizes with the previous set ("restart files are
# constant-size"). They are not: the output is netCDF4 with compression, so
# size follows content (wrfout_d02 12 h blocks: 26/28/28/28/29 MB). Every
# segmented run checked (5 of 5 lanes, 2026-10-14..16) called its newest
# restart "partial" -- run 38901516183 rejected wrfrst_d01_2026-10-19_00 at
# 67.6 vs 73.4 MB while the model was already at 10-19_04:23 -- so each
# handoff silently threw away 12 simulated hours. The size test survives only
# as a fallback when there is no log to read.
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

RSL="$DIR/rsl.error.0000"
PICK=""
if [ -s "$RSL" ]; then
  # Newest model time per domain, as "dNN YYYY-MM-DD_HH:MM:SS" lines. These
  # timestamps sort as strings, so plain comparisons are enough.
  LATEST=$(grep -o 'Timing for main: time [0-9_:-]* on domain *[0-9]*' "$RSL" \
    | awk '{d = $NF + 0; if ($5 > m[d]) m[d] = $5}
           END {for (d in m) printf "d%02d %s\n", d, m[d]}' || true)
  i=$((${#PAIRS[@]} - 1))
  while [ "$i" -ge 0 ]; do
    t="${PAIRS[$i]}"; ok=1
    for d in $(doms_at "$t"); do
      reached=$(printf '%s\n' "$LATEST" | awk -v d="$d" '$1 == d {print $2}')
      if [ -z "$reached" ] || ! [[ "$reached" > "$t" ]]; then
        echo "restart $t is incomplete ($d reached ${reached:-nothing} in rsl.error.0000) — skipping" >&2
        ok=0; break
      fi
    done
    if [ "$ok" = 1 ]; then PICK="$t"; break; fi
    i=$((i - 1))
  done
  [ -n "$PICK" ] || { echo "no restart set is complete per rsl.error.0000" >&2; exit 1; }
else
  # No log (a copied-out dir, a local test): fall back to the size heuristic.
  # It is conservative -- compressed sizes vary, so it can reject a good set,
  # costing one restart interval, but it never accepts a truncated one.
  echo "no rsl.error.0000 in $DIR — falling back to the restart-size check" >&2
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
fi

for f in "$DIR"/wrfrst_d0*_*; do
  case "$f" in *"$PICK") ;; *) rm -f "$f" ;; esac
done
echo "$PICK"
