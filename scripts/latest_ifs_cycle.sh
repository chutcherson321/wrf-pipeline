#!/usr/bin/env bash
# Print the newest IFS 00z/12z cycle (YYYYMMDDHH) whose open-data files reach
# HOURS, probing data.ecmwf.int for the final step file.
# Usage: latest_ifs_cycle.sh HOURS
set -euo pipefail
HOURS="${1:-168}"

for back in 0 6 12 18 24 30 36 42 48; do
  TS=$(date -u -d "-${back} hours" +%Y%m%d%H 2>/dev/null || date -u -v-"${back}"H +%Y%m%d%H)
  DATE="${TS:0:8}"
  HH=$(( (10#${TS:8:2} / 12) * 12 ))
  HH=$(printf "%02d" "$HH")
  URL="https://data.ecmwf.int/forecasts/${DATE}/${HH}z/ifs/0p25/oper/${DATE}${HH}0000-${HOURS}h-oper-fc.grib2"
  if curl -sfI --max-time 30 "$URL" >/dev/null 2>&1; then
    echo "${DATE}${HH}"
    exit 0
  fi
done
echo "no complete IFS cycle found reaching f${HOURS}" >&2
exit 1
