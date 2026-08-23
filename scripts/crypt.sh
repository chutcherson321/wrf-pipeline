#!/usr/bin/env bash
# Symmetric encrypt/decrypt for inter-job artifacts. This repo is PUBLIC and
# its Actions artifacts are publicly downloadable, but the run state carries
# private site config and forecast data — so every artifact is sealed with
# the ARTIFACT_KEY repo secret before upload.
# Usage: crypt.sh enc IN OUT   |   crypt.sh dec IN OUT
set -euo pipefail
MODE="$1"; IN="$2"; OUT="$3"
: "${ARTIFACT_KEY:?ARTIFACT_KEY secret is not set on this repo}"

case "$MODE" in
  enc) openssl enc -aes-256-cbc -pbkdf2 -pass env:ARTIFACT_KEY -in "$IN" -out "$OUT" ;;
  dec) openssl enc -d -aes-256-cbc -pbkdf2 -pass env:ARTIFACT_KEY -in "$IN" -out "$OUT" ;;
  *) echo "usage: crypt.sh enc|dec IN OUT" >&2; exit 2 ;;
esac
