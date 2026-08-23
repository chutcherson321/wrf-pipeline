#!/usr/bin/env bash
# Download GFS 0.25° pgrb2 files for one cycle from NOAA's public S3 bucket.
# Usage: fetch_gfs.sh CYCLE(YYYYMMDDHH) HOURS DEST_DIR
# Files: f000..fHHH at 6-hour interval, per the coworker's input spec.
set -euo pipefail

CYCLE="$1"; HOURS="$2"; DEST="$3"
DATE="${CYCLE:0:8}"; HH="${CYCLE:8:2}"
mkdir -p "$DEST"

for ((f=0; f<=HOURS; f+=6)); do
  FFF=$(printf "%03d" "$f")
  KEY="gfs.${DATE}/${HH}/atmos/gfs.t${HH}z.pgrb2.0p25.f${FFF}"
  OUT="$DEST/gfs.t${HH}z.pgrb2.0p25.f${FFF}"
  if [ -s "$OUT" ]; then echo "have $OUT"; continue; fi
  echo "fetching s3://noaa-gfs-bdp-pds/$KEY"
  aws s3 cp --no-sign-request --region us-east-1 --only-show-errors \
    "s3://noaa-gfs-bdp-pds/$KEY" "$OUT"
done
echo "GFS download complete: $(ls "$DEST" | wc -l) files, $(du -sh "$DEST" | cut -f1)"
