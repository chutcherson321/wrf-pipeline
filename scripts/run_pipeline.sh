#!/usr/bin/env bash
# Run the full WPS -> real -> wrf pipeline in the dtcenter/wps_wrf container.
# geogrid is SKIPPED: the site's prebuilt geo_em.d0*.nc are used instead,
# which removes the 30 GB WPS_GEOG dependency entirely.
#
# Usage: run_pipeline.sh SITE RUN_DIR N_CORES
#   RUN_DIR must contain: namelist.wps, namelist.input, gfs.* GRIB files,
#   geo_em.d01.nc, geo_em.d02.nc
set -euo pipefail

SITE="$1"; RUN_DIR="$(cd "$2" && pwd)"; N_CORES="${3:-$(nproc)}"
IMAGE="dtcenter/wps_wrf"
WPS_DIR="/comsoftware/wrf/WPS-4.3"
WRF_RUN_DIR="/comsoftware/wrf/WRF-4.3/run"

echo "=== pipeline: site=$SITE run_dir=$RUN_DIR cores=$N_CORES ==="

# The dtcenter image runs as UID 9999; on a Linux host it can't write into a
# bind mount owned by the invoking user unless the tree is world-writable.
chmod -R a+rwX "$RUN_DIR"

docker run --rm --name "wps_${SITE}" -v "$RUN_DIR:/run_dir" "$IMAGE" bash -c "
set -e
cd /run_dir

echo '=== UNGRIB ==='
rm -f GRIBFILE.* FILE:*
$WPS_DIR/link_grib.csh /run_dir/gfs.*
ln -sf $WPS_DIR/ungrib/Variable_Tables/Vtable.GFS Vtable
$WPS_DIR/ungrib.exe

echo '=== METGRID (geogrid skipped: prebuilt geo_em) ==='
ln -sf $WPS_DIR/metgrid .
$WPS_DIR/metgrid.exe
echo \"met_em files: \$(ls met_em.d0* | wc -l)\"
"

docker run --rm --name "wrf_${SITE}" -v "$RUN_DIR:/run_dir" "$IMAGE" bash -c "
set -e
mkdir -p /run_dir/wrf && cd /run_dir/wrf
for f in /run_dir/met_em.d0*; do ln -sf \$f .; done
for f in $WRF_RUN_DIR/*;     do ln -sf \$f . 2>/dev/null || true; done
rm -f namelist.input && cp /run_dir/namelist.input .

echo '=== REAL.EXE ==='
mpirun --allow-run-as-root -np 1 $WRF_RUN_DIR/real.exe
tail -4 rsl.error.0000

echo \"=== WRF.EXE ($N_CORES cores) ===\"
mpirun --allow-run-as-root --oversubscribe -np $N_CORES $WRF_RUN_DIR/wrf.exe
tail -4 rsl.error.0000

echo 'WRF COMPLETE'
ls -lh wrfout_d0*
"
