#!/usr/bin/env python3
"""Tests for grid_point: the verification grid-point rule (numpy only).

Run: python3 scripts/grid_point.test.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_point import nearest_matching, wanted_surface  # noqa: E402

FAILS = 0


def check(name, got, want):
    global FAILS
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  (got {got}, want {want})"))
    FAILS += 0 if ok else 1


# 20 x 20 grid, ~1 km spacing; land on the east half (i >= 10), sea on the west.
lat = np.repeat(np.linspace(37.70, 37.88, 20)[:, None], 20, axis=1)
lon = np.repeat(np.linspace(-122.56, -122.36, 20)[None, :], 20, axis=0)
land = np.zeros((20, 20)); land[:, 10:] = 1
pier = (lat[9, 10] + 0.001, lon[9, 10] + 0.001)        # sits just on the land side of the coast

check("surface: explicit wins", wanted_surface({"surface": "sea", "platform": "airport"}), "sea")
check("surface: pier -> sea", wanted_surface({"platform": "pier"}), "sea")
check("surface: buoy -> sea", wanted_surface({"platform": "buoy"}), "sea")
check("surface: airport -> land", wanted_surface({"platform": "airport"}), "land")
check("surface: unknown -> no rule", wanted_surface({}), None)

j, i, km, ok = nearest_matching(lat, lon, land, *pier, None)
check("no rule: geometric nearest is the land cell", (j, i, ok), (9, 10, None))
j, i, km, ok = nearest_matching(lat, lon, land, *pier, "sea")
check("pier: nearest water cell, one column west", (j, i, ok), (9, 9, True))
j, i, km, ok = nearest_matching(lat, lon, land, *pier, "land")
check("land station: stays on land", (j, i, ok), (9, 10, True))
allland = np.ones((20, 20))
j, i, km, ok = nearest_matching(lat, lon, allland, *pier, "sea")
check("no water within 5 cells: geometric nearest, mask_ok False", (j, i, ok), (9, 10, False))
nanmask = np.full((20, 20), np.nan)
check("no LANDMASK in the file: geometric nearest, mask_ok None", nearest_matching(lat, lon, nanmask, *pier, "sea")[3], None)

print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)
