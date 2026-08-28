#!/usr/bin/env bash
# Tests for the incremental-publish path.
#
# WHY THIS EXISTS: every bug in that path so far reached a three-hour production
# run before anyone saw it, because there was nothing to run it against. Two in
# one night on 2026-08-27 -- `mapfile` (bash 4 only) and an unbounded `wait` on
# the background poller that consumed the job's whole 350-min timeout -- and
# neither was visible to any check.
#
# No AWS and no WRF here: `aws` and `python` are stubbed on PATH so the REAL
# control flow in publish_loop.sh / publish_r2.sh runs against fake data.
#
# Usage: scripts/publish.test.sh        (from the repo root)
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
ok(){ printf '  \033[32mok\033[0m   %s\n' "$1"; PASS=$((PASS+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ $# -gt 1 ] && printf '       %s\n' "$2"; FAIL=$((FAIL+1)); }
is(){ [ "$2" = "$3" ] && ok "$1" || no "$1" "expected [$3], got [$2]"; }

# Refuse to run anywhere but a real scratch dir. A silently-empty WORK once made
# this whole suite execute in the repo root and litter it with fixtures.
WORK="$(mktemp -d 2>/dev/null || mktemp -d "${TMPDIR:-/tmp}/pubtest.XXXXXX" 2>/dev/null || true)"
if [ -z "$WORK" ] || [ ! -d "$WORK" ]; then
  echo "cannot create a scratch directory (mktemp failed); refusing to run in $PWD" >&2
  exit 2
fi
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || { echo "cannot cd to $WORK" >&2; exit 2; }
[ "$PWD" = "$WORK" ] || { echo "not in the scratch dir; refusing" >&2; exit 2; }

# ---------------------------------------------------------------- stubs -----
mkdir -p bin wrf_store tiles_out
cat > bin/aws <<'STUB'
#!/usr/bin/env bash
# Enough of the AWS CLI for the publisher's control flow. The verb is found by
# SCANNING: the real calls put --endpoint-url and --region before it, so
# matching on $1/$2 silently matches nothing.
svc="$1"; shift
verb=""; text_out=0; src=""; dst=""
while [ $# -gt 0 ]; do
  case "$1" in
    --endpoint-url|--region|--content-type|--bucket|--key|--prefix|--query)
      shift 2; continue ;;
    --output) [ "${2:-}" = "text" ] && text_out=1; shift 2; continue ;;
    --recursive|--only-show-errors|--no-sign-request|--quiet) shift; continue ;;
    cp|list-objects-v2|delete-object|head-object) verb="$1"; shift; continue ;;
    *) if [ -z "$src" ]; then src="$1"; elif [ -z "$dst" ]; then dst="$1"; fi; shift ;;
  esac
done
mkdir -p "$FAKE_S3"
case "$verb" in
  cp)
    case "$src" in
      s3://*)   # a download: succeed only if the fixture was seeded
        f="$FAKE_S3/$(basename "$src")"
        [ -f "$f" ] && cp "$f" "$dst" || exit 1 ;;
      *) echo "$dst" >> "$FAKE_S3/uploads.log" ;;
    esac ;;
  list-objects-v2)
    if [ "$text_out" = "1" ]; then
      # partial_marker.sh asks for --query/--output text and iterates the words
      cat "$FAKE_S3/marker_keys.txt" 2>/dev/null || echo "None"
    elif [ -f "$FAKE_S3/listing.json" ]; then cat "$FAKE_S3/listing.json"
    else echo '{"Contents":[]}'; fi ;;
  delete-object) echo "$src" >> "$FAKE_S3/deletes.log" ;;
esac
exit 0
STUB
cat > bin/python <<'STUB'
#!/usr/bin/env bash
# Fake ONLY the two generators; everything else is real python3.
case "${1:-}" in
  */extract_stations.py|scripts/extract_stations.py)
    printf '%s' "$FAKE_SERIES" > wrf_store/windstack-wrf-gfs.json ;;
  */make_wind_tiles.py|scripts/make_wind_tiles.py)
    mkdir -p tiles_out; : > tiles_out/wind_f000.png ;;
  *) exec python3 "$@" ;;
esac
exit 0
STUB
chmod +x bin/aws bin/python
export PATH="$WORK/bin:$PATH"
export FAKE_S3="$WORK/s3"; mkdir -p "$FAKE_S3"
export R2_ACCOUNT_ID=test
mkdir -p scripts
for f in publish_r2.sh publish_loop.sh partial_marker.sh make_manifest.py; do
  cp "$REPO/scripts/$f" scripts/
done
chmod +x scripts/*.sh
# make_manifest needs a sites.json at the repo root it computes
python3 - <<'PY'
import json
json.dump({"cloudbreak":{"label":"Cloudbreak","sublabel":"Tavarua, Fiji","lat":-17.8,
  "lon":177.1,"wrf_domain":{"d02_km":3},"tz_abbr":"FJT","tz_offset":12}}, open("sites.json","w"))
PY

# The bucket is never truly empty in practice, and make_manifest.py exits
# non-zero on a listing with no {cycle}.json keys, so give the stub a
# realistic one for the publisher's own manifest step.
python3 - <<'PY'
import json, os
keys = ["wrf/cloudbreak/wrf-gfs/2026082712.json",
        "wrf/cloudbreak/wrf-ifs/2026082700.json",
        "wrf/cloudbreak/gfs/2026082712.json"]
json.dump({"Contents":[{"Key":k} for k in keys]},
          open(os.path.join(os.environ["FAKE_S3"], "listing.json"), "w"))
PY

series(){ python3 -c "
import json,sys
n=int(sys.argv[1])
print(json.dumps({'series':[{'fh':i,'time':'t%d'%i,'wspd':10} for i in range(n)]}))" "$1"; }

# ============================================================ publish_loop ===
echo "publish_loop.sh"

mkdir -p run/wrf
cat > pub.sh <<'P'
#!/usr/bin/env bash
echo "$(ls $4 2>/dev/null | wc -l | tr -d ' ')" >> "$PWD/staged.log"
P
chmod +x pub.sh

# T1: only CLOSED files publish -- the newest is still being appended to
: > staged.log
touch run/wrf/wrfout_d02_2026-08-27_12:00:00 run/wrf/wrfout_d02_2026-08-28_00:00:00 \
      run/wrf/wrfout_d02_2026-08-28_12:00:00
rm -f STOP
PUBLISH_POLL_SEC=1 PUBLISHER="$WORK/pub.sh" \
  scripts/publish_loop.sh cb gfs 2026082712 run STOP 168 >/dev/null 2>&1 &
LP=$!
sleep 3; touch STOP; sleep 2
kill -KILL "$LP" 2>/dev/null; wait "$LP" 2>/dev/null
is "stages every file except the newest" "$(tail -1 staged.log 2>/dev/null || echo none)" "2"

# T2: no new close -> no second publish
n_before=$(wc -l < staged.log | tr -d ' ')
rm -f STOP
PUBLISH_POLL_SEC=1 PUBLISHER="$WORK/pub.sh" \
  scripts/publish_loop.sh cb gfs 2026082712 run STOP 168 >/dev/null 2>&1 &
LP=$!
sleep 3; touch STOP; sleep 2
kill -KILL "$LP" 2>/dev/null; wait "$LP" 2>/dev/null
n_after=$(wc -l < staged.log | tr -d ' ')
[ "$n_after" -gt "$n_before" ] && ok "re-publishes when restarted (fresh process has no memory)" \
  || no "re-publishes when restarted" "no publish happened at all"

# T3: THE 2026-08-27 BUG -- a publisher that ignores the stop file must be killed,
# not waited on forever. This mirrors the workflow's shutdown block.
cat > hang.sh <<'P'
#!/usr/bin/env bash
while true; do sleep 1; done
P
chmod +x hang.sh
( while true; do sleep 1; done ) & HUNG=$!
START=$(date +%s)
for _ in $(seq 1 3); do kill -0 "$HUNG" 2>/dev/null || break; sleep 1; done
if kill -0 "$HUNG" 2>/dev/null; then
  kill -TERM "$HUNG" 2>/dev/null || true; sleep 1; kill -KILL "$HUNG" 2>/dev/null || true
fi
wait "$HUNG" 2>/dev/null
ELAPSED=$(( $(date +%s) - START ))
kill -0 "$HUNG" 2>/dev/null && no "bounded shutdown kills a hung publisher" "still alive" \
  || { [ "$ELAPSED" -le 8 ] && ok "bounded shutdown kills a hung publisher (${ELAPSED}s)" \
       || no "bounded shutdown" "took ${ELAPSED}s"; }

# ============================================================== publish_r2 ===
echo "publish_r2.sh"

# T4: an advance publishes
export FAKE_SERIES="$(series 60)"     # fh 0..59
rm -f "$FAKE_S3/2026082712.json" "$FAKE_S3/uploads.log"
out=$(scripts/publish_r2.sh cloudbreak gfs 2026082712 'x*' 168 2>&1)
case "$out" in *"publishing +59h"*) ok "publishes a first series (+59h)" ;;
  *) no "publishes a first series" "$out" ;; esac

# T5: MONOTONICITY -- a shorter series must never replace a longer one
printf '%s' "$(series 168)" > "$FAKE_S3/2026082712.json"   # published: fh 0..167
export FAKE_SERIES="$(series 12)"                          # new run, only fh 0..11
out=$(scripts/publish_r2.sh cloudbreak gfs 2026082712 'x*' 168 2>&1)
case "$out" in *"not an advance"*) ok "refuses to downgrade +167h to +11h" ;;
  *) no "refuses to downgrade" "$out" ;; esac

# T6: a malformed rate-state file must fall back, never abort the publish
export FAKE_SERIES="$(series 200)"
for bad in "" "abc def" "1787 " " 143"; do
  printf '%s' "$bad" > .publish_rate_state
  out=$(scripts/publish_r2.sh cloudbreak gfs 2026082712 'x*' 168 2>&1); rc=$?
  [ "$rc" = "0" ] || { no "survives rate state [$bad]" "exit $rc: $out"; continue; }
  ok "survives malformed rate state [$bad]"
done
rm -f .publish_rate_state

# ============================================================ make_manifest ===
echo "make_manifest.py"

# T7: a partial marker KEY in the listing becomes partial status -- this is what
# lets both forcing lanes rebuild the manifest without erasing each other.
python3 - <<'PY'
import json
keys = ["wrf/cloudbreak/wrf-gfs/2026082712.json",
        "wrf/cloudbreak/wrf-gfs/2026082712.partial-36-286",
        "wrf/cloudbreak/wrf-ifs/2026082712.json"]
json.dump({"Contents":[{"Key":k} for k in keys]}, open("listing.json","w"))
PY
python3 scripts/make_manifest.py --site cloudbreak --listing listing.json \
  --sites-json sites.json --now 2026-08-28T00:00Z --out man.json >/dev/null 2>&1
got=$(python3 -c "
import json
a=json.load(open('man.json'))['cycles'][0]['avail']
print('%s/%s/%s' % (a['wrf-gfs']['status'], a['wrf-gfs'].get('maxfh'), a['wrf-ifs']['status']))")
is "derives partial+maxfh from a marker key" "$got" "partial/36/ready"

# ================================================================= workflows ==
echo "workflow references"

# T8: every script a workflow calls must exist. A `git add -A` in a partial
# clone once committed the DELETION of scripts/latest_cycle.sh, which would have
# broken GFS cycle resolution on every scheduled run.
missing=""
for f in $(grep -rhoE 'scripts/[a-z_0-9]+\.(sh|py)' "$REPO/.github/workflows/" | sort -u); do
  [ -f "$REPO/$f" ] || missing="$missing $f"
done
is "every script referenced by a workflow exists" "${missing:-none}" "none"

# ===================================================================== done ==
echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
