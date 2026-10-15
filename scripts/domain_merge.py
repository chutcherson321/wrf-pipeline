"""Splice an inner nest's point series onto its parent's: d03 first, d02 after.

pandas only (no wrf-python), so the rule can be tested without wrfout files —
see domain_merge.test.py.

The rule, per output step:
  1. valid time <= the nest's end AND the point is inside the nest (outside its
     relaxation zone) -> the nest row, grid "d03";
  2. otherwise the parent row, grid "d02".

The nest's end is the EARLIER of (a) the per-cycle bound from the site config
and (b) the last nest frame that actually exists. (b) matters because the
bound is a floor -- segment_namelist.py drops the nest at the first restart at
or after it -- and because a nest can die early; either way the files are the
truth and the config is a cap, never a promise.

Inputs are the per-domain frames extract_stations builds, each with a
`rain_in` column ALREADY differenced within its own domain. RAINC/RAINNC are
per-domain running totals: differencing across the seam would subtract d03's
accumulator from d02's and produce nonsense (possibly negative rain). So the
parent's first row after the seam is a parent-minus-parent increment over the
parent's own step (3 h on a 3-hourly d02), which is what "in per step" means.

No padding: a 3-hourly parent tail stays 3-hourly. The display handles uneven
steps; interpolated hours would be invented data.
"""
import pandas as pd


def merge_domains(inner, outer, inner_end=None):
    """Return `outer` with `inner` spliced in up to `inner_end` (inclusive).

    inner      DataFrame indexed by valid time, or None when there is no nest
               (or the point is not usefully inside it).
    outer      DataFrame indexed by valid time: the full-length parent.
    inner_end  Timestamp cap from the site config, or None for "every nest
               frame present".
    Adds a `grid` column ("d03" / "d02").
    """
    outer = outer.copy()
    outer["grid"] = "d02"
    if inner is None or inner.empty:
        return outer
    end = inner.index.max()
    if inner_end is not None:
        end = min(end, pd.Timestamp(inner_end))
    head = inner[inner.index <= end].copy()
    head["grid"] = "d03"
    tail = outer[outer.index > end]
    merged = pd.concat([head, tail[head.columns]])
    # Explicit, not concat-order luck: inside the overlap the nest wins.
    return merged[~merged.index.duplicated(keep="first")].sort_index()


def inside_interior(j, i, shape, relax=5):
    """True when grid cell (j, i) is clear of the `relax`-cell boundary zone.

    The outermost rows/columns of a nest are relaxed toward the parent, so a
    point there is not really a 1 km answer. A nearest-cell search for a point
    OUTSIDE the nest also lands on the edge, so this doubles as the
    "is the point in the nest at all" test.
    """
    ny, nx = shape
    return relax <= j < ny - relax and relax <= i < nx - relax
