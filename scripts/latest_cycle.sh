#!/usr/bin/env bash
# Print the newest GFS cycle (YYYYMMDDHH) whose forecast files reach HOURS.
# Walks back through 00/06/12/18Z cycles until the final file exists.
# Usage: latest_cycle.sh HOURS
set -euo pipefail
HOURS="${1:-48}"
FFF=$(printf "%03d" "$HOURS")

for back in 0 6 12 18 24 30 36; do
  TS=$(date -u -d "-${back} hours" +%Y%m%d%H 2>/dev/null || date -u -v-"${back}"H +%Y%m%d%H)
  DATE="${TS:0:8}"
  HH=$(( (10#${TS:8:2} / 6) * 6 ))
  HH=$(printf "%02d" "$HH")
  KEY="gfs.${DATE}/${HH}/atmos/gfs.t${HH}z.pgrb2.0p25.f${FFF}"
  if aws s3api head-object --no-sign-request --region us-east-1 \
       --bucket noaa-gfs-bdp-pds --key "$KEY" >/dev/null 2>&1; then
    echo "${DATE}${HH}"
    exit 0
  fi
done
echo "no complete GFS cycle found reaching f${FFF}" >&2
exit 1
