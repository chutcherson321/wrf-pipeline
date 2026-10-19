#!/usr/bin/env python3
"""WRF point series at every verification station inside the grid -> points.json.

extract_stations.py writes ONE point (the site, e.g. Supertubos). The verify scorer
(wavetrak-fetch verify/score.py) grades models at every obs station around the site, so with
one point WRF could only ever be scored at the single station within 12 km of it while WOOF was
scored at all of them. This writes the same per-step series for every station in the config
repo's verify/stations.json that sits inside the grid, using the same rules as the site point:

  - nearest grid cell, per domain (indices are per-grid);
  - the 1 km nest where the station is in its interior (clear of the 5-cell relaxation zone)
    up to the site's per-cycle d03 bound, the 3 km grid after it (domain_merge.merge_domains);
  - a station outside d02's interior is skipped (no forecast to grade).

Fields per step match <site>.json's series rows (time, wspd_kts, wdir, gust_kts, temp_f, grid),
so the scorer reads them with the same parser.

Usage:
  extract_points.py --site supertubos --stations verify_stations.json \\
      --wrfout-glob 'run/wrf/seg*/wrfout_d02_*' --inner-glob 'run/wrf/seg*/wrfout_d03_*' \\
      --cycle 2026101812 --forcing gfs --out wrf_store/points.json
"""
import argparse
import glob
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import wrf
from netCDF4 import Dataset

# Sibling modules, whatever the caller's cwd or interpreter flags.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_config                                          # noqa: E402
from domain_merge import inside_interior, merge_domains     # noqa: E402

KT = 1.943844


def load_domain(paths):
    """All frames of one domain, read ONCE: wind speed/dir (earth-rotated), T2, grid geometry.
    Segment boundaries can repeat a frame, so times are de-duplicated keeping the first."""
    ncs = [Dataset(p) for p in paths]
    times = pd.to_datetime(wrf.extract_times(ncs, wrf.ALL_TIMES)).round("h")
    wsd = wrf.getvar(ncs, "wspd_wdir10", timeidx=wrf.ALL_TIMES, method="cat").values  # (2, T, ny, nx)
    try:
        t2 = wrf.getvar(ncs, "T2", timeidx=wrf.ALL_TIMES, method="cat").values           # (T, ny, nx)
    except Exception:
        t2 = np.full(wsd.shape[1:], np.nan)
    first = ncs[0]
    lat = np.asarray(first.variables["XLAT"][0], dtype=float)
    lon = np.asarray(first.variables["XLONG"][0], dtype=float)
    land = (np.asarray(first.variables["LANDMASK"][0]) if "LANDMASK" in first.variables
            else np.full(lat.shape, np.nan))
    keep = ~pd.Index(times).duplicated(keep="first")
    order = np.argsort(np.asarray(times)[keep], kind="stable")
    idx = np.flatnonzero(keep)[order]
    for nc in ncs:
        nc.close()
    return dict(times=pd.DatetimeIndex(np.asarray(times)[idx]), ws=wsd[0][idx], wd=wsd[1][idx],
                t2=t2[idx], lat=lat, lon=lon, land=land)


def nearest(dom, la, lo):
    dist = np.hypot(dom["lat"] - la, (dom["lon"] - lo) * math.cos(math.radians(la)))
    j, i = np.unravel_index(dist.argmin(), dist.shape)
    km = math.hypot((dom["lat"][j, i] - la) * 111.2,
                    (dom["lon"][j, i] - lo) * 111.2 * math.cos(math.radians(la)))
    return int(j), int(i), km


def frame(dom, j, i):
    ws = dom["ws"][:, j, i] * KT
    df = pd.DataFrame({"wspd_kts": ws, "wdir": dom["wd"][:, j, i] % 360,
                       "gust_kts": ws * 1.1,                 # same approximation as extract_stations
                       "temp_f": (dom["t2"][:, j, i] - 273.15) * 9 / 5 + 32}, index=dom["times"])
    df.index.name = "time"
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="site key in sites.json (for the per-cycle nest bound)")
    ap.add_argument("--stations", required=True, help="verify/stations.json from the config repo")
    ap.add_argument("--station-site", default=None,
                    help="only stations tagged with this site (default: the site's domain_dir, then all "
                         "stations whose cell is inside the grid)")
    ap.add_argument("--wrfout-glob", required=True)
    ap.add_argument("--inner-glob", default=None)
    ap.add_argument("--cycle", required=True)
    ap.add_argument("--forcing", default="gfs")
    ap.add_argument("--sites-json", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    sites = site_config.load_sites(a.sites_json)
    tag = a.station_site or site_config.domain_dir(sites, a.site)
    stations = json.loads(Path(a.stations).read_text())
    stations = stations.get("stations", stations) if isinstance(stations, dict) else stations
    cand = [s for s in stations if s.get("site") == tag] or stations
    print(f"{len(cand)} candidate station(s) (site tag {tag!r})")

    outer = sorted(glob.glob(a.wrfout_glob))
    if not outer:
        raise SystemExit(f"no wrfout files match {a.wrfout_glob}")
    d02 = load_domain(outer)
    inner_paths = sorted(glob.glob(a.inner_glob)) if a.inner_glob else []
    d03 = load_domain(inner_paths) if inner_paths else None
    init = datetime.strptime(a.cycle, "%Y%m%d%H")
    nh = site_config.nest_hours(sites, a.site, a.cycle)
    inner_end = init + timedelta(hours=nh) if (d03 is not None and nh > 0) else None

    out = {}
    for s in cand:
        j2, i2, km2 = nearest(d02, s["lat"], s["lon"])
        if not inside_interior(j2, i2, d02["lat"].shape):
            print(f"  {s['id']:>8}  skip: outside the d02 interior")
            continue
        grids = {"d02": dict(ji=[j2, i2], km=round(km2, 2),
                             land=None if np.isnan(d02["land"][j2, i2]) else int(d02["land"][j2, i2]))}
        inner = None
        if d03 is not None:
            j3, i3, km3 = nearest(d03, s["lat"], s["lon"])
            if inside_interior(j3, i3, d03["lat"].shape):
                inner = frame(d03, j3, i3)
                grids["d03"] = dict(ji=[j3, i3], km=round(km3, 2),
                                    land=None if np.isnan(d03["land"][j3, i3]) else int(d03["land"][j3, i3]))
        df = merge_domains(inner, frame(d02, j2, i2), inner_end).round(3)
        rows = json.loads(df.reset_index().to_json(orient="records", date_format="iso"))
        out[s["id"]] = dict(name=s.get("name"), lat=s["lat"], lon=s["lon"], grids=grids, series=rows)
        n3 = int((df["grid"] == "d03").sum())
        print(f"  {s['id']:>8}  d02 {km2:.1f} km{' land' if grids['d02']['land'] else ''}"
              + (f", d03 {grids['d03']['km']:.1f} km ({n3} steps)" if "d03" in grids else ", d02 only")
              + f", {len(rows)} steps")

    doc = dict(site=a.site, forcing=a.forcing, cycle=a.cycle,
               note="per-station WRF series for verification; rows match <site>.json series rows",
               stations=out)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc))
    print(f"{a.out}: {len(out)} station(s)")


if __name__ == "__main__":
    main()
