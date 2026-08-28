#!/usr/bin/env bash
# Publish whatever forecast hours exist right now to R2: the windstack model
# file, the wind value-tiles, and a refreshed manifest. Safe to call repeatedly
# while a run is still integrating — that is the point.
#
# Usage: publish_r2.sh SITE FORCING CYCLE WRFOUT_GLOB [EXPECT_HOURS] [SINCE_EPOCH]
# Needs: R2_ACCOUNT_ID, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and an
#        activated python env carrying wrf-python.
#
# MONOTONICITY: a shorter series must never replace a longer one for the same
# cycle. A retried segment, or a poll that raced a file close, would otherwise
# truncate what the page is already showing. We compare the new max forecast
# hour against what is already published and bail out if it is not an advance.
set -euo pipefail

SITE="$1"; FORCING="$2"; CYCLE="$3"; GLOB="$4"; EXPECT="${5:-0}"
SINCE="${6:-0}"          # epoch seconds when integration began, for the ETA
NOW=$(date +%s)
# One line, "<epoch> <maxfh>", from the previous successful publish in this job.
# Lets the ETA use a windowed rate instead of a since-start average.
RATE_STATE=".publish_rate_state"
PREV_T=0; PREV_FH=0
if [ -f "$RATE_STATE" ]; then read -r PREV_T PREV_FH < "$RATE_STATE" || true; fi
# Trust nothing from that file. A truncated or empty field would make the
# arithmetic comparisons below fail, and under `set -e` that aborts the publish
# -- i.e. one bad state file would silently stop publishing for the rest of the
# run. Anything not a plain integer pair resets to "no previous point".
case "${PREV_T}_${PREV_FH}" in
  *[!0-9_]*|_*|*_) PREV_T=0; PREV_FH=0 ;;
esac
BUDGET="${WRF_BUDGET_MIN:-280}"
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
# NOTE: d03 is deliberately NOT tiled here -- only in the final publish. This
# poller re-reads every closed file each tick rather than incrementally, so its
# per-poll cost already grows with elapsed run time, and d03 exists only over
# the first ~24 h: exactly the window where wrf.exe is doing the 1 km
# integration and is most starved for cores. At 109x109 against d02's 73x73 it
# is ~2.2x the grid points per frame. Block publishing exists so the windstack
# METEOGRAM stops waiting 6.5 h, and that is stations, not tiles -- nobody has
# asked for the 1 km overlay in 30-minute increments. Add it here only if that
# changes, and behind the same `timeout` wrapper the publish already uses.

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
  # MEASURE the integration rate rather than assume one. A hardcoded 26 min per
  # 12 forecast hours was ~2x out on the first live run (the runner did 36 h in
  # ~40 min), which the page showed as "rest ETA 288 min" against a real ~145.
  #
  # Measured over the LAST INTERVAL, not since the start, because the rate is
  # not constant within a run. With a short-range inner nest (INNER_NEST_HOURS)
  # the early hours carry a ~27x-per-3:1-ratio domain and integrate far slower;
  # once d03 stops, the run speeds up. An average since start stays poisoned by
  # those slow hours long after they end, so the ETA would read pessimistic for
  # most of the run. A windowed rate re-converges within one block.
  REMAIN=$(( EXPECT - NEW_MAX ))
  RATE_MIN=0; RATE_FH=0
  if [ "$PREV_T" -gt 0 ] && [ "$NEW_MAX" -gt "$PREV_FH" ]; then
    RATE_MIN=$(( (NOW - PREV_T) / 60 )); RATE_FH=$(( NEW_MAX - PREV_FH ))
  elif [ "$SINCE" -gt 0 ] && [ "$NEW_MAX" -gt 0 ]; then
    RATE_MIN=$(( (NOW - SINCE) / 60 )); RATE_FH=$NEW_MAX   # only one point so far
  fi
  if [ "$RATE_MIN" -gt 0 ] && [ "$RATE_FH" -gt 0 ]; then
    INTEG=$(( REMAIN * RATE_MIN / RATE_FH ))
  else
    INTEG=$(( REMAIN * 26 / 12 ))   # nothing to measure yet on the first tick
  fi
  # Integration time is not the whole wait: each remaining segment costs a
  # fresh runner (queue, docker pull, artifact download, restart) before it
  # integrates anything. Measured around 12 min and charged separately.
  SEGS=$(( INTEG / BUDGET ))
  ETA=$(( INTEG + SEGS * 12 ))
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
echo "$NOW $NEW_MAX" > "$RATE_STATE"
echo "publish_r2: done (+${NEW_MAX}h)"
