#!/usr/bin/env bash
# Download ECMWF IFS 0.25° open-data files for one cycle (full per-step GRIB2,
# same approach as fetch_gfs.sh — ungrib skips fields not in the Vtable).
# Primary: data.ecmwf.int (publishes ~7h35m after cycle time); fallback: the
# AWS mirror bucket, which lags the origin by a few hours.
# Only 00z/12z cycles carry the long 'oper' stream (06z/18z is short 'scda').
# Usage: fetch_ifs.sh CYCLE(YYYYMMDDHH) HOURS DEST_DIR
set -euo pipefail

CYCLE="$1"; HOURS="$2"; DEST="$3"
DATE="${CYCLE:0:8}"; HH="${CYCLE:8:2}"
mkdir -p "$DEST"

case "$HH" in
  00|12) ;;
  *) echo "IFS open data long runs exist only for 00z/12z (got ${HH}z)" >&2; exit 1 ;;
esac
if [ "$HOURS" -gt 240 ]; then
  echo "IFS open data ends at f240 (requested ${HOURS}h)" >&2; exit 1
fi

for ((f=0; f<=HOURS; f+=6)); do
  NAME="${DATE}${HH}0000-${f}h-oper-fc.grib2"
  OUT="$DEST/ifs.${NAME}"
  if [ -s "$OUT" ]; then echo "have $OUT"; continue; fi
  URL="https://data.ecmwf.int/forecasts/${DATE}/${HH}z/ifs/0p25/oper/${NAME}"
  echo "fetching $URL"
  if ! curl -sfL --retry 4 --retry-delay 10 --max-time 600 "$URL" -o "$OUT"; then
    echo "  origin failed, trying AWS mirror"
    aws s3 cp --no-sign-request --region eu-central-1 --only-show-errors \
      "s3://ecmwf-forecasts/${DATE}/${HH}z/ifs/0p25/oper/${NAME}" "$OUT"
  fi
done
echo "IFS download complete: $(ls "$DEST"/ifs.* | wc -l) files, $(du -sh "$DEST" | cut -f1)"
