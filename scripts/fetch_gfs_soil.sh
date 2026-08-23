#!/usr/bin/env bash
# Byte-range download of the GFS fields that IFS open data cannot provide:
# soil temperature/moisture (4 layers), land mask, and surface height.
# Uses the .idx sidecar files to pull ~10 messages (~2-3 MB) per step instead
# of the full ~500 MB pgrb2. Output: one concatenated GRIB2 per step.
# Usage: fetch_gfs_soil.sh CYCLE(YYYYMMDDHH) HOURS DEST_DIR
set -euo pipefail

CYCLE="$1"; HOURS="$2"; DEST="$3"
DATE="${CYCLE:0:8}"; HH="${CYCLE:8:2}"
BASE="https://noaa-gfs-bdp-pds.s3.amazonaws.com"
mkdir -p "$DEST"

PATTERN=':(TSOIL|SOILW):(0-0\.1|0\.1-0\.4|0\.4-1|1-2) m below ground:|:LAND:surface:|:HGT:surface:'

for ((f=0; f<=HOURS; f+=6)); do
  FFF=$(printf "%03d" "$f")
  KEY="gfs.${DATE}/${HH}/atmos/gfs.t${HH}z.pgrb2.0p25.f${FFF}"
  OUT="$DEST/gfssoil.t${HH}z.f${FFF}.grib2"
  if [ -s "$OUT" ]; then echo "have $OUT"; continue; fi

  IDX=$(curl -sfL --retry 4 --retry-delay 5 "$BASE/$KEY.idx")
  # idx lines: msgnum:offset:date:VAR:LEVEL:fcst... — the next line's offset
  # bounds each message.
  RANGES=$(echo "$IDX" | awk -F: -v pat="$PATTERN" '
    { off[NR]=$2; line[NR]=$0 }
    END {
      for (i=1; i<=NR; i++) if (line[i] ~ pat) {
        end = (i<NR) ? off[i+1]-1 : ""
        print off[i]"-"end
      }
    }')
  N=$(echo "$RANGES" | grep -c . || true)
  [ "$N" -ge 10 ] || { echo "only $N soil messages matched in $KEY.idx" >&2; exit 1; }

  : > "$OUT"
  for r in $RANGES; do
    curl -sfL --retry 4 --retry-delay 5 -r "$r" "$BASE/$KEY" >> "$OUT"
  done
  echo "fetched $OUT ($N messages, $(du -h "$OUT" | cut -f1))"
done
echo "GFS soil download complete: $(ls "$DEST"/gfssoil.* | wc -l) files"
