#!/usr/bin/env bash
# Shared "wait for the upstream file" helper.
#
# Why this exists: on 2026-08-26 the IFS 00z run died because f066 answered 404
# while f168 already existed — a fresh cycle publishing out of order, or a brief
# CDN inconsistency. `curl -f` does NOT retry a 404 (only transient 5xx and
# timeouts), and the AWS mirror lags the origin by hours, so it cannot cover a
# just-published cycle. A whole 6.5 h scheduled run was lost to a file that
# appeared minutes later. Waiting is nearly free on a public repo; failing is
# not.
#
# wait_for_http URL [MAX_MIN]   — poll HEAD until 2xx, or give up
# wait_for_s3   S3URI [MAX_MIN] — same for a public S3 object

WAIT_MAX_MIN_DEFAULT="${WAIT_MAX_MIN:-45}"
WAIT_POLL_SEC="${WAIT_POLL_SEC:-60}"

_wait_loop() {           # $1 label  $2 max_min  $3.. probe command
  local label="$1" max_min="$2"; shift 2
  local waited=0 budget=$(( max_min * 60 ))
  if "$@" >/dev/null 2>&1; then return 0; fi
  echo "  not published yet: $label — waiting up to ${max_min}m"
  while [ "$waited" -lt "$budget" ]; do
    sleep "$WAIT_POLL_SEC"
    waited=$(( waited + WAIT_POLL_SEC ))
    if "$@" >/dev/null 2>&1; then
      echo "  appeared after ${waited}s: $label"
      return 0
    fi
    [ $(( waited % 300 )) -eq 0 ] && echo "  still waiting (${waited}s): $label"
  done
  echo "  gave up after ${max_min}m: $label" >&2
  return 1
}

wait_for_http() {
  _wait_loop "$(basename "$1")" "${2:-$WAIT_MAX_MIN_DEFAULT}" \
    curl -sfI --max-time 30 "$1"
}

wait_for_s3() {
  local uri="${1#s3://}" bucket key
  bucket="${uri%%/*}"; key="${uri#*/}"
  _wait_loop "$(basename "$1")" "${2:-$WAIT_MAX_MIN_DEFAULT}" \
    aws s3api head-object --no-sign-request --bucket "$bucket" --key "$key"
}
