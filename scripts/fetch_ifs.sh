#!/usr/bin/env bash
# Download ECMWF IFS 0.25° open-data files for one cycle and re-encode them
# for WPS: open data is CCSDS/AEC-packed (GRIB2 DRS template 42), which the
# g2 library bundled with WPS 4.3 cannot decode ("getdrstemplate: DRS
# Template 42 not defined"), so the fields ungrib needs are filtered out with
# grib_copy and repacked to grid_simple with grib_set (eccodes tools).
# Primary: data.ecmwf.int (publishes ~7h35m after cycle time); fallback: the
# AWS mirror bucket, which lags the origin by a few hours.
# Only 00z/12z cycles carry the long 'oper' stream (06z/18z is short 'scda').
# Usage: fetch_ifs.sh CYCLE(YYYYMMDDHH) HOURS DEST_DIR
set -euo pipefail

command -v grib_copy >/dev/null || {
  echo "eccodes tools required (apt-get install -y libeccodes-tools)" >&2; exit 1; }

# Everything Vtable.IFS maps, plus nothing else — keeps the repacked files small.
WPS_FIELDS="gh/t/u/v/r/10u/10v/2t/2d/msl/sp/skt"

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
  RAW="$DEST/raw.${NAME}"
  URL="https://data.ecmwf.int/forecasts/${DATE}/${HH}z/ifs/0p25/oper/${NAME}"
  echo "fetching $URL"
  if ! curl -sfL --retry 4 --retry-delay 10 --max-time 600 "$URL" -o "$RAW"; then
    echo "  origin failed, trying AWS mirror"
    aws s3 cp --no-sign-request --region eu-central-1 --only-show-errors \
      "s3://ecmwf-forecasts/${DATE}/${HH}z/ifs/0p25/oper/${NAME}" "$RAW"
  fi
  grib_copy -w shortName="$WPS_FIELDS" "$RAW" "$OUT.ccsds"
  grib_set -r -s packingType=grid_simple "$OUT.ccsds" "$OUT"
  rm -f "$RAW" "$OUT.ccsds"
done
echo "IFS download complete: $(ls "$DEST"/ifs.* | wc -l) files, $(du -sh "$DEST" | cut -f1)"
