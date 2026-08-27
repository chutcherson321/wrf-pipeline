#!/usr/bin/env bash
# Publish whatever forecast hours exist right now to R2: the windstack model
# file, the wind value-tiles, and a refreshed manifest. Safe to call repeatedly
# while a run is still integrating — that is the point.
#
# Usage: publish_r2.sh SITE FORCING CYCLE WRFOUT_GLOB [EXPECT_HOURS]
# Needs: R2_ACCOUNT_ID, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and an
#        activated python env carrying wrf-python.
#
# MONOTONICITY: a shorter series must never replace a longer one for the same
# cycle. A retried segment, or a poll that raced a file close, would otherwise
# truncate what the page is already showing. We compare the new max forecast
# hour against what is already published and bail out if it is not an advance.
set -euo pipefail

SITE="$1"; FORCING="$2"; CYCLE="$3"; GLOB="$4"; EXPECT="${5:-0}"
MODEL="wrf-$FORCING"
EP="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
BUCKET="wavetrak-frames"
DEST="s3://$BUCKET/wrf/$SITE/$MODEL"
S3="aws s3 --endpoint-url $EP --region auto"
S3API="aws s3api --endpoint-url $EP --region auto"

python scripts/extract_stations.py --site "$SITE" --wrfout-glob "$GLOB" \
  --out wrf_store --cycle "$CYCLE" --forcing "$FORCING" >/dev/null
python scripts/make_wind_tiles.py --wrfout-glob "$GLOB" \
  --cycle "$CYCLE" --out tiles_out >/dev/null

NEW_MAX=$(python -c "
import json
d = json.load(open('wrf_store/windstack-$MODEL.json'))
print(max((r['fh'] for r in d['series']), default=-1))")

OLD_MAX=-1
if $S3 cp "$DEST/$CYCLE.json" existing.json --only-show-errors 2>/dev/null; then
  OLD_MAX=$(python -c "
import json
try:
    d = json.load(open('existing.json'))
    print(max((r['fh'] for r in d.get('series', [])), default=-1))
except Exception:
    print(-1)")
  rm -f existing.json
fi

if [ "$NEW_MAX" -le "$OLD_MAX" ]; then
  echo "publish_r2: +${NEW_MAX}h is not an advance on the published +${OLD_MAX}h — skipping"
  exit 0
fi
echo "publish_r2: publishing +${NEW_MAX}h (was +${OLD_MAX}h)"

$S3 cp "wrf_store/windstack-$MODEL.json" "$DEST/$CYCLE.json" \
  --content-type application/json --only-show-errors
$S3 cp "wrf_store/windstack-$MODEL.json" "$DEST/latest.json" \
  --content-type application/json --only-show-errors
$S3 cp tiles_out "s3://$BUCKET/wrf/$SITE/tiles/$MODEL/$CYCLE" \
  --recursive --only-show-errors

# Mark this member partial while it is still short of the target, so the page
# shows "+NNh · rest ETA" instead of implying a finished run. The mark is an
# object in R2, not an argument, so the other forcing's lane can rebuild this
# same manifest without erasing it.
if [ "$EXPECT" -gt 0 ] && [ "$NEW_MAX" -lt "$EXPECT" ]; then
  # ~26 min of wall clock per 12 forecast hours on a 4-vCPU runner.
  ETA=$(( (EXPECT - NEW_MAX) * 26 / 12 ))
  echo "publish_r2: partial, ~${ETA} min of forecast left to run"
  scripts/partial_marker.sh set "$SITE" "$MODEL" "$CYCLE" "$NEW_MAX" "$ETA"
else
  scripts/partial_marker.sh clear "$SITE" "$MODEL" "$CYCLE"
fi

$S3API list-objects-v2 --bucket "$BUCKET" --prefix "wrf/$SITE/" > listing.json
python scripts/make_manifest.py --site "$SITE" --listing listing.json \
  --sites-json sites.json --now "$(date -u +%Y-%m-%dT%H:%MZ)" \
  --out manifest.json
$S3 cp manifest.json "s3://$BUCKET/wrf/manifest.json" \
  --content-type application/json --only-show-errors
echo "publish_r2: done (+${NEW_MAX}h)"
