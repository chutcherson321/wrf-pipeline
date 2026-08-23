#!/usr/bin/env python3
"""Encode WRF d02 10 m winds as directed-fetch-viewer value tiles.

One lossless grayscale PNG per forecast hour, byte-identical in format to the
viewer's global-model wind tiles (build_tiles.py): 12-bit U10/V10, offset
-100 m/s, scale 200/4095, four vertically stacked planes u_hi, v_hi, u_lo,
v_lo. The d02 Mercator grid is rectilinear (XLONG varies along x only, XLAT
along y only) and its grid north is true north, so resampling to a regular
lat/lon raster is two 1-D interpolations and no wind rotation.

Usage:
  make_wind_tiles.py --wrfout-glob 'run_dir/wrf/seg*/wrfout_d02_*' \
      --cycle 2026082312 --out tiles_out
Writes tiles_out/wind_f{FFF}.png + tiles_out/meta.json.
"""
import argparse
import glob
import json
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

WIND_OFFSET = -100.0
WIND_BITS = 12
WIND_LEVELS = (1 << WIND_BITS) - 1
WIND_SCALE = 200.0 / WIND_LEVELS


def write_png_gray(path: Path, planes: np.ndarray) -> int:
    h, w = planes.shape
    raw = b"".join(b"\x00" + planes[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))

    ihdr = (w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0]))
    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(blob)
    return len(blob)


def pack_tile(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = u.shape
    planes = np.empty((4 * h, w), np.uint8)
    for i, a in enumerate((u, v)):
        raw = np.clip(np.round((a - WIND_OFFSET) / WIND_SCALE),
                      0, WIND_LEVELS).astype(np.uint32)
        planes[i * h:(i + 1) * h] = (raw >> 4).astype(np.uint8)
        planes[(2 + i) * h:(3 + i) * h] = (raw & 0xF).astype(np.uint8)
    return planes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wrfout-glob", required=True)
    ap.add_argument("--cycle", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    paths = sorted(glob.glob(args.wrfout_glob))
    if not paths:
        raise SystemExit(f"no wrfout files match {args.wrfout_glob}")
    init = datetime.strptime(args.cycle, "%Y%m%d%H")

    # Source grid from the first file. Unwrap the dateline so lon ascends
    # continuously (Cloudbreak's d02 spans ~173E..181E) — same continuous-lon
    # convention as the viewer's basin frames.
    with Dataset(paths[0]) as nc0:
        lat2d = nc0.variables["XLAT"][0]
        lon2d = nc0.variables["XLONG"][0]
    src_lat = np.asarray(lat2d[:, lat2d.shape[1] // 2], dtype=np.float64)
    src_lon = np.asarray(lon2d[lon2d.shape[0] // 2, :], dtype=np.float64)
    src_lon = np.where(src_lon < src_lon[0], src_lon + 360.0, src_lon)
    rect_err = max(float(np.ptp(lat2d, axis=1).max()),
                   float(np.ptp(np.where(lon2d < lon2d[:, :1], lon2d + 360, lon2d),
                                axis=0).max()))
    if rect_err > 0.02:
        raise SystemExit(f"grid not rectilinear enough ({rect_err:.3f} deg) — "
                         "this encoder assumes a Mercator d02")

    step = round(float(np.mean(np.diff(src_lon))), 4)
    tgt_lon = np.arange(src_lon[0], src_lon[-1] + step / 2, step)
    tgt_lat = np.arange(src_lat[0], src_lat[-1] + step / 2, step)  # ascending S->N

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seen: dict[int, str] = {}
    for p in paths:
        with Dataset(p) as nc:
            times = ["".join(c.decode() for c in row) for row in nc.variables["Times"][:]]
            for ti, ts in enumerate(times):
                valid = datetime.strptime(ts, "%Y-%m-%d_%H:%M:%S")
                fh = round((valid - init).total_seconds() / 3600)
                if fh < 0 or fh in seen:
                    continue
                u = np.asarray(nc.variables["U10"][ti], dtype=np.float64)
                v = np.asarray(nc.variables["V10"][ti], dtype=np.float64)
                # two-pass 1-D linear resample onto the regular raster
                u = np.stack([np.interp(tgt_lon, src_lon, row) for row in u])
                u = np.stack([np.interp(tgt_lat, src_lat, col) for col in u.T]).T
                v = np.stack([np.interp(tgt_lon, src_lon, row) for row in v])
                v = np.stack([np.interp(tgt_lat, src_lat, col) for col in v.T]).T
                # viewer rasters are north-up (row 0 = lat_north)
                planes = pack_tile(u[::-1], v[::-1])
                write_png_gray(out / f"wind_f{fh:03d}.png", planes)
                seen[fh] = ts

    if not seen:
        raise SystemExit("no forecast steps at/after the cycle init")
    meta = {
        "cycle": args.cycle,
        "init": init.strftime("%Y-%m-%dT%H:00Z"),
        "steps": sorted(seen),
        "grid": {
            "lat_south": round(float(tgt_lat[0]), 4),
            "lat_north": round(float(tgt_lat[-1]), 4),
            "lon_left": round(float(tgt_lon[0]), 4),
            "lon_right": round(float(tgt_lon[-1]), 4),
            "width": int(tgt_lon.size), "height": int(tgt_lat.size),
            "dlon": step, "dlat": step,
        },
        "encoding": {"offset": WIND_OFFSET, "scale": WIND_SCALE, "bits": WIND_BITS,
                     "planes": ["u_hi", "v_hi", "u_lo", "v_lo"]},
    }
    (out / "meta.json").write_text(json.dumps(meta))
    print(f"{len(seen)} tiles -> {out} "
          f"({meta['grid']['width']}x{meta['grid']['height']} @ {step} deg)")


if __name__ == "__main__":
    main()
