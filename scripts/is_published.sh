#!/usr/bin/env bash
# Has this site/forcing/cycle already been published?
# Exit 0 = yes (a release exists on the private repo), 1 = no.
# Usage: is_published.sh CONFIG_REPO SITE FORCING CYCLE     (needs $GH_TOKEN)
set -uo pipefail
REPO="$1"; SITE="$2"; FORCING="$3"; CYCLE="$4"
TAG="wrf-${SITE}-${FORCING}-${CYCLE}"
code=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO}/releases/tags/${TAG}")
[ "$code" = "200" ]
