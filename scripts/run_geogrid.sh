#!/usr/bin/env bash
# Build geo_em.d0*.nc with WPS geogrid.exe.
#
# The forecast pipeline deliberately skips geogrid so it never needs the
# WPS_GEOG terrain database (tens of GB). This is the other half of that
# trade: a one-off job that does need WPS_GEOG, run only when a site's
# domains change -- adding an inner nest, moving a domain, resizing one.
#
# Usage: run_geogrid.sh SITE RUN_DIR GEOG_DIR
#   RUN_DIR  holds namelist.wps; geo_em files are written back into it
#   GEOG_DIR is an extracted WPS_GEOG tree
set -euo pipefail
SITE="$1"; RUN_DIR="$(cd "$2" && pwd)"; GEOG_DIR="$(cd "$3" && pwd)"
IMAGE="dtcenter/wps_wrf"
WPS_DIR="/comsoftware/wrf/WPS-4.3"

test -f "$RUN_DIR/namelist.wps" || { echo "no namelist.wps in $RUN_DIR" >&2; exit 1; }
# The dtcenter image runs as UID 9999 and cannot write into a bind mount
# owned by the invoking user unless the tree is world-writable.
chmod -R a+rwX "$RUN_DIR"

echo "=== geogrid: site=$SITE geog=$GEOG_DIR ==="
docker run --rm --name "geogrid_${SITE}" \
  -v "$RUN_DIR:/run_dir" -v "$GEOG_DIR:/geog:ro" "$IMAGE" bash -c "
set -e
cd /run_dir
rm -f geo_em.d0*.nc geogrid.log*

# Point at the mounted terrain database, adding the key if the site's
# namelist omits it (the forecast path never reads it).
if grep -q 'geog_data_path' namelist.wps; then
  sed -i \"s|geog_data_path.*=.*|geog_data_path = '/geog',|\" namelist.wps
else
  sed -i \"/&geogrid/a\\\\ geog_data_path = '/geog',\" namelist.wps
fi
grep -E 'max_dom|geog_data_path|e_we|e_sn|parent' namelist.wps

ln -sf $WPS_DIR/geogrid .
$WPS_DIR/geogrid.exe
" || { echo "--- geogrid.log ---"; tail -40 "$RUN_DIR"/geogrid.log* 2>/dev/null; exit 1; }

echo "=== geo_em files ==="
ls -lh "$RUN_DIR"/geo_em.d0*.nc
