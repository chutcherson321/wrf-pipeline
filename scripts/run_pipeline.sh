#!/usr/bin/env bash
# Run the WPS/WRF pipeline in the dtcenter/wps_wrf container, in stages so
# long forecasts can be split across CI jobs (the 6h job cap applies to
# public runners too — unlimited minutes, not unlimited job length).
#
# Usage: run_pipeline.sh SITE RUN_DIR N_CORES [STAGE]
#   STAGE:
#     all (default) — wps + wrf in one go (short runs / local use)
#     wps           — ungrib + metgrid + real: produces wrfinput_d0*/wrfbdy_d01
#     wrf           — run (or resume) wrf.exe under a wall-clock budget
#
# Env knobs:
#   FORCING=gfs|ifs (default gfs) — ifs ungribs ECMWF open data with
#     vtables/Vtable.IFS plus a GFS soil subset under the SOIL prefix
#   WRF_BUDGET_MIN (stage wrf; default 0 = no budget) — stop wrf.exe after
#     this many wall minutes; a restart file lets the next segment resume.
#     Exit code 0 with run_dir/wrf/WRF_DONE present = forecast complete;
#     exit 0 without WRF_DONE = budget expired, resumable.
#
# geogrid is SKIPPED: the site's prebuilt geo_em.d0*.nc are used instead,
# which removes the 30 GB WPS_GEOG dependency entirely.
set -euo pipefail

SITE="$1"; RUN_DIR="$(cd "$2" && pwd)"; N_CORES="${3:-$(nproc)}"
STAGE="${4:-all}"
FORCING="${FORCING:-gfs}"
WRF_BUDGET_MIN="${WRF_BUDGET_MIN:-0}"
IMAGE="dtcenter/wps_wrf"
WPS_DIR="/comsoftware/wrf/WPS-4.3"
WRF_RUN_DIR="/comsoftware/wrf/WRF-4.3/run"

echo "=== pipeline: site=$SITE run_dir=$RUN_DIR cores=$N_CORES stage=$STAGE forcing=$FORCING ==="

# The dtcenter image runs as UID 9999; on a Linux host it can't write into a
# bind mount owned by the invoking user unless the tree is world-writable.
chmod -R a+rwX "$RUN_DIR"

run_wps() {
  # --init for the same reason as the wrf stage below: real.exe runs under
  # mpirun here too, and prep has never shown the phantom exit only because it
  # is short -- not because it is structurally different.
  docker run --init --rm --name "wps_${SITE}" -v "$RUN_DIR:/run_dir" "$IMAGE" bash -c "
set -e
cd /run_dir
rm -f GRIBFILE.* FILE:* SOIL:*

if [ '$FORCING' = 'ifs' ]; then
  echo '=== UNGRIB pass 1: IFS atmosphere ==='
  $WPS_DIR/link_grib.csh /run_dir/ifs.*
  ln -sf /run_dir/Vtable.IFS Vtable
  sed -i \"s/prefix.*=.*/prefix = 'FILE',/\" namelist.wps
  $WPS_DIR/ungrib.exe
  echo '=== UNGRIB pass 2: GFS soil ==='
  rm -f GRIBFILE.*
  $WPS_DIR/link_grib.csh /run_dir/gfssoil.*
  ln -sf $WPS_DIR/ungrib/Variable_Tables/Vtable.GFS Vtable
  sed -i \"s/prefix.*=.*/prefix = 'SOIL',/\" namelist.wps
  $WPS_DIR/ungrib.exe
else
  echo '=== UNGRIB: GFS ==='
  $WPS_DIR/link_grib.csh /run_dir/gfs.*
  ln -sf $WPS_DIR/ungrib/Variable_Tables/Vtable.GFS Vtable
  $WPS_DIR/ungrib.exe
fi

echo '=== METGRID (geogrid skipped: prebuilt geo_em) ==='
ln -sf $WPS_DIR/metgrid .
$WPS_DIR/metgrid.exe
echo \"met_em files: \$(ls met_em.d0* | wc -l)\"

echo '=== REAL.EXE ==='
mkdir -p /run_dir/wrf && cd /run_dir/wrf
for f in /run_dir/met_em.d0*; do ln -sf \$f .; done
for f in $WRF_RUN_DIR/*;     do ln -sf \$f . 2>/dev/null || true; done
rm -f namelist.input && cp /run_dir/namelist.input .
mpirun --allow-run-as-root -np 1 $WRF_RUN_DIR/real.exe
tail -4 rsl.error.0000
ls -lh wrfinput_d0* wrfbdy_d01
"
}

run_wrf() {
  local budget_prefix=""
  if [ "$WRF_BUDGET_MIN" -gt 0 ]; then
    budget_prefix="timeout ${WRF_BUDGET_MIN}m"
  fi
  local rc=0
  # --init runs a real PID 1 inside the container to reap children. Without it,
  # mpirun's ranks can outlive wrf.exe and keep the step's stdout pipe open;
  # the runner then force-kills at step end and marks the step FAILED even
  # though the script exited 0. That is the "phantom exit" seen since
  # 2026-08-28: the log prints `run_pipeline RC=0` immediately before
  # `##[error]Process completed with exit code 1`. It correlates exactly with
  # running a container -- segments that only pass the DONE marker through
  # (no docker run) are always green, and segments that run wrf.exe go red on
  # BOTH the timeout path and the SUCCESS path, which rules out anything in
  # this script's own logic.
  docker run --init --rm --name "wrf_${SITE}" -v "$RUN_DIR:/run_dir" "$IMAGE" bash -c "
set -e
cd /run_dir/wrf
rm -f WRF_DONE
# Fresh runners: relink the model's static run files around the carried state.
for f in $WRF_RUN_DIR/*; do [ -e \$(basename \$f) ] || ln -sf \$f . 2>/dev/null || true; done
rm -f namelist.input && cp /run_dir/namelist.input .

echo \"=== WRF.EXE ($N_CORES cores, budget=${WRF_BUDGET_MIN}m) ===\"
$budget_prefix mpirun --allow-run-as-root --oversubscribe -np $N_CORES $WRF_RUN_DIR/wrf.exe
" || rc=$?

  if [ "$rc" -eq 0 ] && grep -q "SUCCESS COMPLETE WRF" "$RUN_DIR"/wrf/rsl.error.0000 2>/dev/null; then
    touch "$RUN_DIR/wrf/WRF_DONE"
    echo "WRF COMPLETE"
    tail -3 "$RUN_DIR"/wrf/rsl.error.0000
  elif [ "$rc" -eq 124 ]; then
    echo "WRF budget expired after ${WRF_BUDGET_MIN}m — resumable from newest restart"
    # Show what the guard is actually looking at. On 2026-08-28 both lanes took
    # this path correctly at +60 h and the step still exited 1, and the guard
    # being silent made it impossible to tell from the log whether it had fired.
    ls -1 "$RUN_DIR"/wrf/wrfrst_d01_* 2>/dev/null | tail -3 || {
      echo "ERROR: budget expired before the first restart file was written" >&2
      exit 1
    }
  else
    echo "wrf.exe failed (rc=$rc)" >&2
    tail -20 "$RUN_DIR"/wrf/rsl.error.0000 2>/dev/null || true
    exit "$rc"
  fi
  ls -lh "$RUN_DIR"/wrf/wrfout_d02_* 2>/dev/null || true
  # EXPLICIT success. A budget expiry that leaves restart files is a NORMAL
  # outcome -- the next segment resumes from them -- not an error. Relying on
  # the last command's status to convey that was too fragile: on 2026-08-28 both
  # lanes reached this line at +60 h and the step still exited 1, which
  # fail-fast then turned into a cancelled 6.5 h forecast. Say 0 and mean it.
  return 0
}

case "$STAGE" in
  wps) run_wps ;;
  wrf) run_wrf ;;
  all) run_wps; run_wrf ;;
  *) echo "unknown stage: $STAGE" >&2; exit 2 ;;
esac
# Do not leave the script's status to whatever the case happened to end on.
echo "run_pipeline: stage=$STAGE finished ok"
exit 0
