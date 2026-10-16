#!/usr/bin/env python3
"""Print the leading run of wrfout files that are safe to read right now.

The partial publisher (publish_loop.sh) asks this before it stages anything.
It used to infer "closed" from file order alone, and that is wrong for the
LAST file of an inner nest: wrf.exe keeps it open until the whole run exits.
On a netCDF4/HDF5 wrfout, HDF5's file lock then makes every open fail with
`OSError: [Errno -101] NetCDF: HDF error`, the extractor died on it, and the
whole publish failed every tick. Run 38901516183 (Supertubos 2026101600)
sat at +23h for ~90 min that way, with d02 hours ready to go.

A file is ready when it opens AND its last frame is whole. The second test is
not paranoia: without the lock (HDF5_USE_FILE_LOCKING=FALSE, a filesystem that
cannot lock, or a classic-format build) a file being written opens fine, and
its record dimension grows BEFORE the frame's variables are written -- the
last frame reads back as fill. That frame would be published as data.

Prints ready paths one per line, in the order given, and stops at the first
file that is not ready: a gap in the middle of a series is worse than a
shorter series. Always exits 0; "nothing ready" is an empty answer.

Usage: wrfout_ready.py FILE [FILE ...]
Needs netCDF4 + numpy only (no wrf-python).
"""
import sys

import numpy as np
from netCDF4 import Dataset

# Physical bounds on fields every wrfout carries. Catches a last frame that is
# zeros (NOFILL) rather than masked fill.
SANE = {"T2": (150.0, 350.0), "PSFC": (2.0e4, 1.2e5)}


def why_not_ready(path):
    """None when `path` is readable with a complete last frame, else a reason."""
    try:
        nc = Dataset(path)
    except OSError as e:          # held open for write (HDF5 lock), truncated, ...
        return f"does not open ({e})"
    try:
        nc.set_auto_mask(True)
        if "Time" not in nc.dimensions or len(nc.dimensions["Time"]) == 0:
            return "no frames yet"
        last = len(nc.dimensions["Time"]) - 1
        times = nc.variables.get("Times")
        if times is not None:
            stamp = b"".join(np.ma.filled(times[last], b"")).decode(errors="replace").strip("\x00 ")
            if not stamp:
                return f"frame {last} has no Times stamp"
        for name, var in nc.variables.items():
            if not var.dimensions or var.dimensions[0] != "Time" or var.dtype.kind != "f":
                continue
            v = np.ma.masked_invalid(var[last])
            if np.ma.count(v) == 0:
                return f"frame {last} of {name} is unwritten"
            if name in SANE:
                lo, hi = SANE[name]
                if v.min() < lo or v.max() > hi:
                    return f"frame {last} of {name} is outside [{lo}, {hi}]"
        return None
    except Exception as e:        # a half-written header can fail anywhere
        return f"unreadable ({e})"
    finally:
        nc.close()


def main(paths):
    for p in paths:
        reason = why_not_ready(p)
        if reason:
            print(f"wrfout_ready: holding back {p}: {reason}", file=sys.stderr)
            break
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
