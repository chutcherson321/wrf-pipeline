#!/usr/bin/env python3
"""Tests for wrfout_ready: which wrfout files the partial publisher may read.

Real netCDF files, real HDF5 locks: a writer process holds a file open exactly
as wrf.exe holds the nest's last wrfout, and the probe runs in a separate
process, as it does on the runner. Needs netCDF4 + numpy.

Run: python3 scripts/wrfout_ready.test.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

PROBE = str(Path(__file__).resolve().parent / "wrfout_ready.py")
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    FAILS += 0 if cond else 1


# A minimal wrfout: Times plus the fields the probe sanity-checks. `frames`
# frames are fully written; `stub` more get a Times stamp only, which is what
# a reader sees when the record dimension has grown but the data has not
# landed yet. `t2` overrides the written value (0 = a NOFILL frame of zeros).
WRITER = r'''
import sys, time, numpy as np
from netCDF4 import Dataset
path, fmt, frames, stub, t2, hold = sys.argv[1], sys.argv[2], *map(float, sys.argv[3:7])
nc = Dataset(path, "w", format=fmt)
nc.createDimension("Time", None); nc.createDimension("DateStrLen", 19)
nc.createDimension("south_north", 4); nc.createDimension("west_east", 5)
times = nc.createVariable("Times", "S1", ("Time", "DateStrLen"))
dims = ("Time", "south_north", "west_east")
T2, PSFC, U10 = (nc.createVariable(n, "f4", dims) for n in ("T2", "PSFC", "U10"))
nc.createVariable("XLAT", "f4", ("south_north", "west_east"))[:] = 39.0  # no Time dim
for k in range(int(frames + stub)):
    times[k] = np.array(list("2026-10-17_%02d:00:00" % k), "S1")
    if k < frames:
        T2[k] = t2; PSFC[k] = 101000.0; U10[k] = 5.0
nc.sync()
open(path + ".ready", "w").close()
time.sleep(hold)
nc.close()
'''


def write(path, fmt="NETCDF4", frames=2, stub=0, t2=290.0, hold=0.0):
    """Write a wrfout. hold > 0 returns the writer still holding it open."""
    # A marker left by an earlier write of the same path would let us return
    # before this writer has the file open, and the probe would read the OLD,
    # complete copy (CI caught exactly that race; locally the writer won it).
    Path(f"{path}.ready").unlink(missing_ok=True)
    p = subprocess.Popen([sys.executable, "-c", WRITER, str(path), fmt,
                          str(frames), str(stub), str(t2), str(hold)])
    if hold <= 0:
        p.wait()
        return None
    for _ in range(100):
        if os.path.exists(f"{path}.ready"):
            return p
        time.sleep(0.05)
    raise RuntimeError("writer never got going")


def probe(*paths, locking=True):
    env = dict(os.environ)
    env.pop("HDF5_USE_FILE_LOCKING", None)
    if not locking:
        env["HDF5_USE_FILE_LOCKING"] = "FALSE"
    r = subprocess.run([sys.executable, PROBE, *map(str, paths)],
                       capture_output=True, text=True, env=env)
    return r.returncode, [Path(l).name for l in r.stdout.split()], r.stderr


with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    a, b, c = (d / f"wrfout_d03_2026-10-1{n}_00:00:00" for n in (6, 7, 8))

    write(a); write(b); write(c)
    rc, out, _ = probe(a, b, c)
    check("closed, complete files are all ready, in order", out == [a.name, b.name, c.name], out)
    check("exits 0", rc == 0, rc)

    # THE 2026-10-16 STALL: wrf.exe holding the nest's last file open. With
    # HDF5's lock, Dataset() raises `NetCDF: HDF error`; that used to kill the
    # whole publish. Now the file is held back and everything before it stays.
    w = write(c, frames=1, hold=4)
    try:
        rc, out, err = probe(a, b, c)
        check("held-open file is held back, not fatal", rc == 0 and out == [a.name, b.name], (rc, out))
        check("...and the reason is logged", "holding back" in err and c.name in err, err)
    finally:
        w.wait()
    rc, out, _ = probe(a, b, c)
    check("...and is ready once the writer closes it", out == [a.name, b.name, c.name], out)

    # Readable but INCOMPLETE. Without the lock (or on a classic-format build)
    # a file being written opens, and its last frame reads back as fill.
    for fmt, locking in (("NETCDF4", False), ("NETCDF3_64BIT_OFFSET", True)):
        w = write(c, fmt=fmt, frames=1, stub=1, hold=4)
        try:
            rc, out, err = probe(a, b, c, locking=locking)
            check(f"{fmt} (lock {'on' if locking else 'off'}): opens but last frame unwritten "
                  "-> held back", out == [a.name, b.name] and "unwritten" in err, (out, err))
        finally:
            w.wait()

    write(c, frames=1, t2=0.0)
    rc, out, err = probe(c)
    check("a zero-filled frame (NOFILL) is not data", out == [] and "T2" in err, (out, err))

    # A gap is worse than a shorter series: stop at the first miss.
    w = write(b, frames=1, hold=4)
    try:
        rc, out, _ = probe(a, b, c)
        check("stops at the first file that is not ready", out == [a.name], out)
    finally:
        w.wait()

    bad = d / "wrfout_d02_truncated"
    bad.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\0" * 64)
    rc, out, _ = probe(bad)
    check("a truncated file is held back", rc == 0 and out == [], (rc, out))
    rc, out, _ = probe()
    check("no files -> nothing, exit 0", rc == 0 and out == [], (rc, out))

print(f"\n{'all passed' if not FAILS else f'{FAILS} failed'}")
sys.exit(1 if FAILS else 0)
