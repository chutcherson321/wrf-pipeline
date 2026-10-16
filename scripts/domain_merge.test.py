#!/usr/bin/env python3
"""Tests for domain_merge: the d03-then-d02 splice. Needs pandas only.

Run: python3 scripts/domain_merge.test.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain_merge import inside_interior, merge_domains  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    FAILS += 0 if cond else 1


def frame(init, hours, step, wspd, rain_step):
    idx = [pd.Timestamp(init) + pd.Timedelta(hours=h) for h in range(0, hours + 1, step)]
    return pd.DataFrame({"wspd_kts": [wspd] * len(idx),
                         "rain_in": [rain_step] * len(idx)},
                        index=pd.DatetimeIndex(idx, name="time"))


def fhs(df, init):
    return [int((t - pd.Timestamp(init)).total_seconds() // 3600) for t in df.index]


for init, d03h, n_in, n_out in (("2026-10-16T00", 48, 49, 24),   # 00Z: seam +48
                                ("2026-10-16T12", 60, 61, 20)):  # 12Z: seam +60
    hh = init[-2:]
    d02 = frame(init, 120, 3, 20.0, 0.3)
    # The nest's files may run PAST the config bound (the bound is a floor at
    # a restart): give it 12 extra hourly frames and expect them trimmed.
    d03 = frame(init, d03h + 12, 1, 12.0, 0.1)
    end = pd.Timestamp(init) + pd.Timedelta(hours=d03h)
    m = merge_domains(d03, d02, end)
    f = fhs(m, init)
    seam_local = (pd.Timestamp(init) + pd.Timedelta(hours=d03h)).hour
    check(f"{hh}Z: {n_in + n_out} steps", len(m) == n_in + n_out, len(m))
    check(f"{hh}Z: hourly d03 0..{d03h}", f[:n_in] == list(range(0, d03h + 1))
          and set(m["grid"][:n_in]) == {"d03"})
    check(f"{hh}Z: 3-hourly d02 {d03h + 3}..120, no padding",
          f[n_in:] == list(range(d03h + 3, 121, 3)) and set(m["grid"][n_in:]) == {"d02"},
          f[n_in:n_in + 3])
    check(f"{hh}Z: seam valid time is 00Z", seam_local == 0, seam_local)
    check(f"{hh}Z: d03 wins inside the overlap", (m.loc[m.grid == "d03", "wspd_kts"] == 12.0).all())
    check(f"{hh}Z: per-domain rain kept, never re-differenced across the seam",
          (m.loc[m.grid == "d02", "rain_in"] == 0.3).all()
          and (m.loc[m.grid == "d03", "rain_in"] == 0.1).all())

# A nest that died early: files are the truth, d02 takes over at its last frame.
init = "2026-10-16T00"
m = merge_domains(frame(init, 30, 1, 12.0, 0.1), frame(init, 120, 3, 20.0, 0.3),
                  pd.Timestamp(init) + pd.Timedelta(hours=48))
f = fhs(m, init)
check("short nest: d03 to +30 then d02 from +33",
      f[:31] == list(range(31)) and f[31] == 33 and m["grid"].iloc[31] == "d02", f[29:33])

# No nest (Cloudbreak): d02 untouched apart from the grid label.
d02 = frame(init, 168, 1, 20.0, 0.3)
m = merge_domains(None, d02)
check("no nest: identical series, all d02",
      m.drop(columns="grid").equals(d02) and set(m["grid"]) == {"d02"})

check("interior: centre of a 73x73 nest", inside_interior(36, 36, (73, 73)))
check("interior: 5th cell from the edge is in", inside_interior(5, 67, (73, 73)))
check("interior: relaxation zone is out", not inside_interior(4, 36, (73, 73))
      and not inside_interior(36, 68, (73, 73)))

print(f"\n{FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
