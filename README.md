# wrf-pipeline

A generic GitHub Actions pipeline that runs a nested WRF forecast in the
prebuilt [`dtcenter/wps_wrf`](https://hub.docker.com/r/dtcenter/wps_wrf)
container (WRF/WPS 4.3), forced by GFS 0.25° from NOAA's public
[`noaa-gfs-bdp-pds`](https://registry.opendata.aws/noaa-gfs-bdp-pds/) bucket,
and extracts a per-site timeseries JSON with wrf-python.

This repo is intentionally generic: it contains **no site configurations and
no forecast data**. Site namelists and prebuilt `geo_em` domain files live in
a private repo, fetched at run time with a fine-grained token
(`CONFIG_TOKEN`), and results are published back to that private repo as
release assets — nothing produced by a run is stored publicly.

## Pipeline

1. `scripts/latest_cycle.sh` — probe S3 for the newest GFS cycle whose files
   reach the requested forecast length
2. `scripts/fetch_gfs.sh` — download the 6-hourly pgrb2 boundary files
3. `scripts/make_namelists.py` — template the site's namelists for the cycle
4. `scripts/run_pipeline.sh` — ungrib → metgrid → real → wrf (geogrid is
   skipped: prebuilt `geo_em.d0*.nc` remove the WPS_GEOG dependency)
5. `scripts/extract_stations.py` — nearest-gridpoint timeseries (wind, temp,
   rain, cloud, CAPE/CIN, reflectivity, precipitable water) → JSON

## Using it for your own site

Fork it, point `CONFIG_REPO` at your own repo containing
`sites/<key>/{namelist.wps,namelist.input,geo_em.d01.nc,geo_em.d02.nc}` and a
`sites.json` with extraction points, and set a `CONFIG_TOKEN` secret with
contents read/write on that repo.
