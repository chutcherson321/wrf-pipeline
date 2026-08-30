#!/usr/bin/env bash
# Download ECMWF IFS 0.25° open-data files for one cycle and re-encode them
# for WPS.
#
# Two transforms are needed before ungrib will read this data:
#   1. Open data is CCSDS/AEC-packed (GRIB2 DRS template 42), which the g2
#      library bundled with WPS 4.3 cannot decode ("getdrstemplate: DRS
#      Template 42 not defined") -- so everything is repacked to grid_simple.
#   2. Only the fields Vtable.IFS maps are wanted; the rest is dead weight.
#
# We take (2) at the WIRE rather than after the fact. A step file is ~144 MB of
# 184 messages, of which the 77 we map are ~44 MB -- so downloading whole files
# and running grib_copy over them moved 3x the bytes needed. The .index sidecar
# gives a byte offset and length per message, and adjacent keepers merge into
# ~25 contiguous ranges, so one step costs 25 range requests instead of one
# 144 MB transfer. Same trick fetch_gfs_soil.sh already uses for the soil
# fields. Falls back to the whole-file path if the index is unusable, because a
# slow run beats a failed one.
#
# Primary: data.ecmwf.int (publishes ~7h35m after cycle time); fallback: the
# AWS mirror bucket, which lags the origin by a few hours.
# Usage: fetch_ifs.sh CYCLE(YYYYMMDDHH) HOURS DEST_DIR
set -euo pipefail
. "$(dirname "$0")/lib_wait.sh"

command -v grib_set >/dev/null || {
  echo "eccodes tools required (apt-get install -y libeccodes-tools)" >&2; exit 1; }

# Everything Vtable.IFS maps, and nothing else.
WPS_FIELDS="gh/t/u/v/r/10u/10v/2t/2d/msl/sp/skt"

CYCLE="$1"; HOURS="$2"; DEST="$3"
DATE="${CYCLE:0:8}"; HH="${CYCLE:8:2}"
mkdir -p "$DEST"

# WRF runs 00/12z only (CH's decision; make_manifest.py's WRF_RUNS = [0, 12]
# encodes the same choice). This is a POLICY guard, not an availability one:
# since IFS cycle 50r1 (2026-05-12) ECMWF publishes `oper` at 06/18z too, so
# the old comment here -- "long runs exist only for 00z/12z" -- was stale and
# is exactly the kind of wrong-reason-in-an-error-message that propagates.
# Verified 2026-08-30: .../20260829/18z/.../oper/...18h-oper-fc.index -> 200,
# the same path under scda/ -> 404.
case "$HH" in
  00|12) ;;
  *) echo "WRF runs 00z/12z only; refusing ${HH}z (upstream may well have it)" >&2
     exit 1 ;;
esac
if [ "$HOURS" -gt 240 ]; then
  echo "IFS open data ends at f240 (requested ${HOURS}h)" >&2; exit 1
fi

# Print merged "start-end" byte ranges covering the wanted messages.
ranges_from_index() {
  WPS_FIELDS="$WPS_FIELDS" python3 -c '
import json, os, sys
keep = set(os.environ["WPS_FIELDS"].split("/"))
sel = []
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    if r.get("param") in keep:
        sel.append((int(r["_offset"]), int(r["_offset"]) + int(r["_length"])))
if not sel:
    sys.exit(1)
sel.sort()
merged = [list(sel[0])]
for a, b in sel[1:]:
    if a <= merged[-1][1]:                 # touching or overlapping
        merged[-1][1] = max(merged[-1][1], b)
    else:
        merged.append([a, b])
print(len(sel), sum(b - a for a, b in merged), file=sys.stderr)
for a, b in merged:
    print(f"{a}-{b-1}")
' "$1"
}

for ((f=0; f<=HOURS; f+=6)); do
  NAME="${DATE}${HH}0000-${f}h-oper-fc.grib2"
  OUT="$DEST/ifs.${NAME}"
  [ -s "$OUT" ] && { echo "have $OUT"; continue; }
  BASE="https://data.ecmwf.int/forecasts/${DATE}/${HH}z/ifs/0p25/oper"
  URL="$BASE/$NAME"
  IDXURL="${URL%.grib2}.index"
  PACKED="$DEST/packed.${NAME}"
  rm -f "$PACKED"

  # The index publishes with its data file, so waiting on it waits on both.
  echo "fetching $NAME"
  wait_for_http "$IDXURL" || true

  IDX="$DEST/idx.${NAME}.json"
  got_ranges=0
  if curl -sfL --retry 4 --retry-delay 5 --max-time 120 "$IDXURL" -o "$IDX" \
     && RANGES=$(ranges_from_index "$IDX" 2>"$DEST/.idxstat"); then
    read -r NMSG NBYTES < "$DEST/.idxstat" || true
    NR=$(printf '%s\n' "$RANGES" | grep -c .)
    echo "  index: ${NMSG} messages, $(( NBYTES / 1000000 )) MB in ${NR} ranges"
    got_ranges=1
    for r in $RANGES; do
      if ! curl -sfL --retry 4 --retry-delay 5 --max-time 300 -r "$r" "$URL" >> "$PACKED"; then
        echo "  range $r failed — falling back to the whole file" >&2
        got_ranges=0; rm -f "$PACKED"; break
      fi
    done
  else
    echo "  no usable index — falling back to the whole file" >&2
  fi
  rm -f "$IDX" "$DEST/.idxstat"

  if [ "$got_ranges" -ne 1 ]; then
    RAW="$DEST/raw.${NAME}"
    if ! curl -sfL --retry 4 --retry-delay 10 --max-time 600 "$URL" -o "$RAW"; then
      echo "  origin failed, trying AWS mirror (lags the origin by hours)"
      aws s3 cp --no-sign-request --region eu-central-1 --only-show-errors \
        "s3://ecmwf-forecasts/${DATE}/${HH}z/ifs/0p25/oper/${NAME}" "$RAW"
    fi
    grib_copy -w shortName="$WPS_FIELDS" "$RAW" "$PACKED"
    rm -f "$RAW"
  fi

  grib_set -r -s packingType=grid_simple "$PACKED" "$OUT"
  rm -f "$PACKED"
  # A truncated range fetch yields a file eccodes still opens, so count what
  # actually landed rather than trusting the transfer.
  N=$(grib_count "$OUT" 2>/dev/null || echo 0)
  [ "$N" -ge 70 ] || { echo "only $N messages in $OUT — refusing a short step" >&2; exit 1; }
done
echo "IFS download complete: $(ls "$DEST"/ifs.* | wc -l) files, $(du -sh "$DEST" | cut -f1)"
