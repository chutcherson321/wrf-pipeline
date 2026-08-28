#!/usr/bin/env bash
# Partial-run markers as zero-byte R2 keys: wrf/{site}/{model}/{cycle}.partial-{maxfh}-{eta}
#
# WHY NOT A CLI FLAG: GFS and IFS now integrate in parallel lanes, and every
# publish in either lane regenerates wrf/manifest.json from an object listing.
# A --partial flag passed by one lane would be silently erased by whichever
# lane wrote the manifest last. Keeping the fact in R2 means any lane, at any
# time, derives the same answer.
#
# Usage: partial_marker.sh set   SITE MODEL CYCLE MAXFH ETA_MIN
#        partial_marker.sh clear SITE MODEL CYCLE
set -euo pipefail

ACTION="$1"; SITE="$2"; MODEL="$3"; CYCLE="$4"
EP="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
BUCKET="wavetrak-frames"
PREFIX="wrf/$SITE/$MODEL/$CYCLE.partial"
S3API="aws s3api --endpoint-url $EP --region auto"

# Clear unconditionally first: maxfh and eta are part of the key, so each
# advance is a NEW key and the old ones would otherwise accumulate.
KEYS=$($S3API list-objects-v2 --bucket "$BUCKET" --prefix "$PREFIX" \
  --query 'Contents[].Key' --output text 2>/dev/null || true)
for k in $KEYS; do
  [ "$k" = "None" ] && continue
  $S3API delete-object --bucket "$BUCKET" --key "$k" >/dev/null
done

if [ "$ACTION" = "set" ]; then
  MAXFH="$5"; ETA="$6"
  : > partial.marker
  aws s3 cp --endpoint-url "$EP" --region auto partial.marker \
    "s3://$BUCKET/$PREFIX-$MAXFH-$ETA" --only-show-errors
  rm -f partial.marker
  echo "partial_marker: set $MODEL $CYCLE +${MAXFH}h eta ${ETA}min"
else
  echo "partial_marker: cleared $MODEL $CYCLE"
fi
