# wrf-pipeline

A generic GitHub Actions pipeline that runs a nested WRF forecast in the
prebuilt [`dtcenter/wps_wrf`](https://hub.docker.com/r/dtcenter/wps_wrf)
container (WRF/WPS 4.3) and extracts a per-site timeseries JSON with
wrf-python. Two forcings are supported:

- **GFS 0.25°** from NOAA's public
  [`noaa-gfs-bdp-pds`](https://registry.opendata.aws/noaa-gfs-bdp-pds/) bucket
- **ECMWF IFS 0.25°** from [open data](https://data.ecmwf.int) (00z/12z, to
  f240), with soil/landmask from a small same-cycle GFS byte-range subset —
  IFS open data encodes soil on GRIB2 level type 151, which WPS 4.3 ungrib
  cannot read

Long forecasts are **segmented across jobs**: GitHub caps any job at 6 hours
(public repos lift the minutes bill, not the job cap), so `prep` runs WPS +
real once for the full span, then up to four chained `wrf` jobs each run
wrf.exe under a wall-clock budget and hand the newest restart file to the
next job. All inter-job artifacts are encrypted (`ARTIFACT_KEY` secret) since
artifacts on a public repo are world-downloadable.

This repo is intentionally generic: it contains **no site configurations and
no forecast data**. Site namelists and prebuilt `geo_em` domain files live in
a private repo, fetched at run time with a fine-grained token
(`CONFIG_TOKEN`), and results are published back to that private repo as
release assets — nothing produced by a run is stored publicly.

## Pipeline

1. `scripts/latest_cycle.sh` / `scripts/latest_ifs_cycle.sh` — probe for the
   newest cycle whose files reach the requested forecast length
2. `scripts/fetch_gfs.sh` or `scripts/fetch_ifs.sh` + `scripts/fetch_gfs_soil.sh`
   — download the 6-hourly boundary files
3. `scripts/make_namelists.py` — template the site's namelists for the cycle
   and forcing (metgrid levels, restart cadence, `fg_name`)
4. `scripts/run_pipeline.sh` — staged: `wps` = ungrib → metgrid → real
   (geogrid is skipped: prebuilt `geo_em.d0*.nc` remove the WPS_GEOG
   dependency; IFS ungribs twice, atmosphere + GFS soil, with
   `vtables/Vtable.IFS`); `wrf` = run or resume wrf.exe under a budget
   (`scripts/segment_namelist.py`, `scripts/pick_restart.sh`)
5. `scripts/extract_stations.py` — nearest-gridpoint timeseries (wind, temp,
   rain, cloud, CAPE/CIN, reflectivity, precipitable water) → JSON; merges
   segment outputs and dedupes restart-boundary frames

## Using it for your own site

Fork it, point `CONFIG_REPO` at your own repo containing
`sites/<key>/{namelist.wps,namelist.input,geo_em.d01.nc,geo_em.d02.nc}` and a
`sites.json` with extraction points, and set two secrets: `CONFIG_TOKEN`
(fine-grained token, contents read/write on that repo) and `ARTIFACT_KEY`
(any long random string, used to seal inter-job artifacts).
