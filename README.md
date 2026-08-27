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

## Short-range inner nest

A third domain can run at higher resolution over the first part of the
forecast only. Cost scales roughly 27x per 3:1 nest ratio (9x the grid
points, 3x the timesteps to hold CFL), so a 1 km d03 for the full 168 h is
not affordable on a runner — but 1 km out to +48 h, with the 3 km d02
carrying on to +168 h, is, and that is where nearly all of the extra detail
is worth having.

Set the repo variable `INNER_NEST_HOURS` (Settings -> Secrets and variables
-> Actions -> Variables) to the lead time the inner nest should stop at;
`0` or unset keeps every domain running the full length.

The mechanism is the existing segmentation. `segment_namelist.py` already
rewrites the namelist at each restart; with `--cycle-start` and
`--nest-hours` it also drops `max_dom` to `--keep-doms` (default 2) once the
restart being resumed from is at or past the cutoff. Reducing the domain
count at a restart is safe — WRF reads restart files for domains
`1..max_dom` and ignores the rest. Adding a nest mid-run is not supported.

Two supporting pieces follow from that:

- `pick_restart.sh` treats `d03`+ as optional. It still requires `d01` and
  `d02` (those run the whole way, so a missing one means a truncated write)
  and size-checks only the domains present in both of the last two restart
  sets, so a retired nest is not mistaken for a partial write.
- The segment state tarball carries `wrfrst_d0?_$TS`, so whichever domains
  are still active are handed to the next segment.

### Building the domain file

`geo_em.d03.nc` has to exist before any of this runs, and the forecast
pipeline skips geogrid on purpose so it never carries the WPS_GEOG terrain
database. The **Build geo_em domains** workflow is the one-off counterpart:
run it only when a site's domains change.

    Actions -> Build geo_em domains -> Run workflow
      site                 teahupoo
      add_nest_span_cells  60          # blank to use the site namelist as-is
      nest_ratio           3
      geog                 high_res_mandatory
      publish              true

With `add_nest_span_cells` set it runs `scripts/add_nest.py` first, which is
the part that is annoying by hand: WRF wants `(e_we - 1) % parent_grid_ratio
== 0`, the child must sit wholly inside its parent with a buffer, and every
per-domain list in both namelists has to grow by one entry in step. The nest
is centred on its parent and covers `--span-cells` of it, so a 3 km d02 with
`--span-cells 60 --ratio 3` gives a 1 km d03 of 181x181 points, about 180 km
across. Other per-domain lists are extended by repeating the last value, and
anything with an unexpected number of entries is left alone and warned about.

Run it with `--dry-run` locally first to see the geometry:

    python3 scripts/add_nest.py --wps sites/teahupoo/namelist.wps \
        --input sites/teahupoo/namelist.input --span-cells 60 --dry-run

The workflow downloads WPS_GEOG (`high_res_mandatory`, 2.8 GB, cached
between runs), runs `geogrid.exe` in the same `dtcenter/wps_wrf` container
the forecast uses, prints each domain's dimensions and `DX`, and publishes
`geo_em.d0*.nc` plus both namelists to a `geo-em-<site>` release on the
private config repo. It also uploads them as an encrypted artifact, since
artifacts on a public repo are world-downloadable.

Then adopt them in the private config repo:

    gh release download geo-em-teahupoo -R <config-repo> -D sites/teahupoo --clobber

Take all three `geo_em` files as a set, not just the new one — they are
built together from one WPS_GEOG version, and mixing vintages across domains
is a subtle way to get inconsistent terrain.

Products (`make_wind_tiles.py`, `extract_stations.py`) still read `d02`.
That is deliberate: d02 covers the full forecast, so charts and timeseries
stay continuous while d03 output is evaluated on its own.

## Using it for your own site

Fork it, point `CONFIG_REPO` at your own repo containing
`sites/<key>/{namelist.wps,namelist.input,geo_em.d01.nc,geo_em.d02.nc}` and a
`sites.json` with extraction points, and set two secrets: `CONFIG_TOKEN`
(fine-grained token, contents read/write on that repo) and `ARTIFACT_KEY`
(any long random string, used to seal inter-job artifacts).
