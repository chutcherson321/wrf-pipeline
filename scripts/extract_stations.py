#!/usr/bin/env python3
"""Extract a per-site timeseries JSON from wrfout files.

Ported from the forecast notebook's extract_site_timeseries() (WSL_WRF_Forecast
POC v4) — same variables, same units, same gust approximation — reshaped as a
CLI that emits the wavetrak-fetch station-JSON pattern.

Usage:
  extract_stations.py --site teahupoo --wrfout-glob 'RUN/wrf/wrfout_d02_*' \
      --out wrf_store [--cycle 2026082200 --forcing gfs]
"""
import argparse
import glob
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import wrf
from netCDF4 import Dataset

REPO = Path(__file__).resolve().parent.parent
SITES = json.loads((REPO / "sites.json").read_text())


def nearest_ij(nc, lat, lon):
    lat2d = wrf.getvar(nc, "XLAT", timeidx=0).values
    lon2d = wrf.getvar(nc, "XLONG", timeidx=0).values
    dist = np.hypot(lat2d - lat, lon2d - lon)
    return np.unravel_index(dist.argmin(), dist.shape)


def extract_site_timeseries(ncs, site_key):
    site = SITES[site_key]
    j, i = nearest_ij(ncs[0], site["lat"], site["lon"])
    times = pd.to_datetime(wrf.extract_times(ncs, wrf.ALL_TIMES)).round("h")

    wspd_wdir = wrf.getvar(ncs, "wspd_wdir10", timeidx=wrf.ALL_TIMES, method="cat")
    wspd_ms = wspd_wdir[0, :, j, i].values
    wdir_deg = wspd_wdir[1, :, j, i].values
    u10 = wrf.getvar(ncs, "U10", timeidx=wrf.ALL_TIMES, method="cat")[:, j, i].values
    v10 = wrf.getvar(ncs, "V10", timeidx=wrf.ALL_TIMES, method="cat")[:, j, i].values

    def grab(name, index, default):
        try:
            var = wrf.getvar(ncs, name, timeidx=wrf.ALL_TIMES, method="cat")
            v = var.values
            if index is not None:  # stacked diagnostics: (K, Time, ny, nx) or (Time, K, ny, nx)
                if v.shape[0] in (3, 4) and v.ndim == 4:
                    out = v[index, :, j, i]
                else:
                    out = v[:, index, j, i]
            else:
                out = v[:, j, i]
            # cape_2d (and friends) come back masked where the diagnostic is
            # undefined; through .values that is NaN, which would serialize to
            # null. Fill with the same default the notebook used on exception.
            return np.ma.filled(np.ma.masked_invalid(out), default)
        except Exception:
            return np.full(len(times), default)

    cape = grab("cape_2d", 0, 0.0)
    cin = grab("cape_2d", 1, 0.0)
    mdbz = grab("mdbz", None, 0.0)
    pw = grab("pw", None, 0.0)
    dewp_c = grab("td2", None, np.nan)   # degC
    slp = grab("slp", None, np.nan)      # hPa
    t2 = grab("T2", None, np.nan)
    temp_f = (t2 - 273.15) * 9 / 5 + 32

    rainc = grab("RAINC", None, 0.0)
    rainnc = grab("RAINNC", None, 0.0)

    try:
        cv = wrf.getvar(ncs, "cloudfrac", timeidx=wrf.ALL_TIMES, method="cat").values
        if cv.shape[0] == 3 and cv.ndim == 4:
            lvls = cv[:, :, j, i]        # (3, Time): low/mid/high
        else:
            lvls = cv[:, :, j, i].T      # (Time, 3) -> (3, Time)
        cloud = (1 - np.prod(1 - lvls, axis=0)) * 100
        cl, cm, ch = (l * 100 for l in lvls)
    except Exception:
        cloud = np.full(len(times), np.nan)
        cl = cm = ch = np.full(len(times), np.nan)

    df = pd.DataFrame({
        "wspd_kts": wspd_ms * 1.944,
        "wdir": wdir_deg,
        "u10_kts": u10 * 1.944,
        "v10_kts": v10 * 1.944,
        "gust_kts": wspd_ms * 1.944 * 1.1,  # same approximation as the notebook
        "temp_f": temp_f,
        "rain_acc_mm": rainc + rainnc,
        "cloud_pct": cloud,
        "cape": cape,
        "dewp_f": dewp_c * 9 / 5 + 32,
        "mslp_hpa": slp,
        "cl_pct": cl, "cm_pct": cm, "ch_pct": ch,
        "cin": cin,
        "mdbz": mdbz,
        "pw_mm": pw,
    }, index=times)
    df.index.name = "time"

    # Segmented runs concatenate wrfouts from several restart segments: the
    # restart-time frame can appear in two files, so dedupe and sort before
    # differencing the accumulated rain.
    df = df[~df.index.duplicated(keep="first")].sort_index()
    rain_acc = df.pop("rain_acc_mm")
    df["rain_in"] = rain_acc.diff().fillna(rain_acc.iloc[0]) / 25.4
    return df, (int(j), int(i))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--wrfout-glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--forcing", default="gfs")
    ap.add_argument("--model-id", default=None, help="windstack model id; default wrf-<forcing>")
    args = ap.parse_args()

    paths = sorted(glob.glob(args.wrfout_glob))
    if not paths:
        raise SystemExit(f"no wrfout files match {args.wrfout_glob}")
    ncs = [Dataset(p) for p in paths]

    df, (j, i) = extract_site_timeseries(ncs, args.site)
    df = df.round(3)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    WINDSTACK_ONLY = ["dewp_f", "mslp_hpa", "cl_pct", "cm_pct", "ch_pct"]
    station_df = df.drop(columns=WINDSTACK_ONLY)
    payload = {
        "site": args.site,
        "label": SITES[args.site]["label"],
        "lat": SITES[args.site]["lat"],
        "lon": SITES[args.site]["lon"],
        "forcing": args.forcing,
        "cycle": args.cycle,
        "grid_ji": [j, i],
        "source_files": [Path(p).name for p in paths],
        "series": json.loads(station_df.reset_index().to_json(orient="records", date_format="iso")),
    }
    out_file = out_dir / f"{args.site}.json"
    out_file.write_text(json.dumps(payload))
    print(f"{out_file}: {len(df)} steps, {df.index[0]} -> {df.index[-1]}")

    # Windstack model file (DISPLAY_SPEC contract: kt / degF / hPa / in-per-step,
    # cl-cm-ch split, null = missing). Written alongside the station JSON.
    if args.cycle:
        init = datetime.strptime(args.cycle, "%Y%m%d%H")
        model_id = args.model_id or f"wrf-{args.forcing}"

        def num(x, nd=1):
            return None if pd.isna(x) else round(float(x), nd)

        def intv(x):
            return None if pd.isna(x) else int(round(float(x)))

        series = []
        for ts, row in df.iterrows():
            fh = int(round((ts.to_pydatetime() - init).total_seconds() / 3600))
            if fh < 0:
                continue
            series.append({
                "fh": fh,
                "wspd": num(row["wspd_kts"]), "gust": num(row["gust_kts"]),
                "wdir": None if pd.isna(row["wdir"]) else int(round(row["wdir"])) % 360,
                "t2m": num(row["temp_f"]), "dewp": num(row["dewp_f"]),
                "mslp": num(row["mslp_hpa"]),
                "qpf": num(row["rain_in"], 2),
                "cl": intv(row["cl_pct"]), "cm": intv(row["cm_pct"]), "ch": intv(row["ch_pct"]),
                "vis": None,  # no clean vis diagnostic in this WRF build
            })
        ws = {"model": model_id, "cycle": args.cycle,
              "init": init.strftime("%Y-%m-%dT%H:00Z"), "series": series}
        ws_file = out_dir / f"windstack-{model_id}.json"
        ws_file.write_text(json.dumps(ws))
        print(f"{ws_file}: {len(series)} steps (windstack contract)")


if __name__ == "__main__":
    main()
