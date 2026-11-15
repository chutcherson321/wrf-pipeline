"""The verification grid-point rule, for extract_points.py (numpy only, so it tests without wrf-python).

Wind verification redesign spec, locked by CH: buoys and exposed piers take the nearest WATER cell,
land stations the nearest LAND cell, and every model uses the same rule. wavetrak-fetch applies it to
WOOF, HRRR and RRFS (render.Grid.nearest_matching): the nearest cell whose land mask matches the
station's surface within 5 cells of the geometric nearest, else the geometric nearest flagged
mask_ok=False. This is the same rule for the CPU WRF lanes, so a station is scored at the same kind of
point in every model (2026-11-15: Fort Point, a pier, sat on a land cell 1.6 km away in WRF).
"""
from __future__ import annotations

import math

import numpy as np

SEA_PLATFORMS = ("buoy", "pier")


def wanted_surface(station: dict) -> str | None:
    """'sea' / 'land' for a station: its explicit `surface`, else from the platform (buoys and piers
    are sea, everything else land). None = no rule (plain nearest)."""
    s = station.get("surface")
    if s in ("sea", "land"):
        return s
    p = (station.get("platform") or station.get("kind") or "").lower()
    if not p:
        return None
    return "sea" if p in SEA_PLATFORMS else "land"


def _km(lat2d, lon2d, j, i, la, lo):
    return math.hypot((lat2d[j, i] - la) * 111.2, (lon2d[j, i] - lo) * 111.2 * math.cos(math.radians(la)))


def nearest_matching(lat2d, lon2d, land, la, lo, want: str | None, max_cells: int = 5):
    """(j, i, km, mask_ok): the nearest cell whose LANDMASK matches `want` ('sea' -> 0, 'land' -> 1)
    within `max_cells` of the geometric nearest; else the geometric nearest with mask_ok=False.
    No mask, or no rule, -> the geometric nearest with mask_ok=None."""
    dist = np.hypot(lat2d - la, (lon2d - lo) * math.cos(math.radians(la)))
    j0, i0 = np.unravel_index(int(np.argmin(dist)), dist.shape)
    if want not in ("sea", "land") or land is None or np.all(np.isnan(land)):
        return int(j0), int(i0), _km(lat2d, lon2d, j0, i0, la, lo), None
    target = 0 if want == "sea" else 1
    ny, nx = lat2d.shape
    js, je = max(0, j0 - max_cells), min(ny, j0 + max_cells + 1)
    is_, ie = max(0, i0 - max_cells), min(nx, i0 + max_cells + 1)
    win = dist[js:je, is_:ie].copy()
    mask = land[js:je, is_:ie]
    win[~(np.isfinite(mask) & (np.rint(mask) == target))] = np.inf
    if not np.isfinite(win).any():
        return int(j0), int(i0), _km(lat2d, lon2d, j0, i0, la, lo), False
    dj, di = np.unravel_index(int(np.argmin(win)), win.shape)
    j, i = js + dj, is_ + di
    return int(j), int(i), _km(lat2d, lon2d, j, i, la, lo), True
