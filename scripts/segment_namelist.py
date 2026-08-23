#!/usr/bin/env python3
"""Rewrite a run's namelist.input to resume from a WRF restart file.

Used by segmented runs: sets restart = .true. and moves start_* to the
restart timestamp, leaving end_* and everything else untouched. Needs no
site config — it operates on the namelist already templated for the run.

Usage: segment_namelist.py --namelist run_dir/namelist.input \
    --restart-time 2026-08-24_12:00:00 [--out run_dir/namelist.input]
"""
import argparse
import re
from datetime import datetime
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--namelist", required=True)
    ap.add_argument("--restart-time", required=True, help="YYYY-MM-DD_HH:MM:SS")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    t = datetime.strptime(args.restart_time, "%Y-%m-%d_%H:%M:%S")
    text = Path(args.namelist).read_text()

    # run_days/run_hours override end_* in WRF, so they must shrink to the
    # remaining span or the restarted segment runs past the intended end.
    def grab(key):
        m = re.search(rf"^\s*{key}\s*=\s*(\d+)", text, flags=re.M)
        if not m:
            raise SystemExit(f"namelist: no '{key}' line")
        return int(m.group(1))

    end = datetime(grab("end_year"), grab("end_month"), grab("end_day"),
                   grab("end_hour"))
    remaining = end - t
    if remaining.total_seconds() <= 0:
        raise SystemExit(f"restart time {t} is not before end time {end}")
    days, rem_h = divmod(int(remaining.total_seconds()) // 3600, 24)

    subs = {
        "run_days": f" run_days               = {days},",
        "run_hours": f" run_hours              = {rem_h},",
        "start_year": f" start_year             = {t.year}, {t.year},",
        "start_month": f" start_month            = {t.month:02d},   {t.month:02d},",
        "start_day": f" start_day              = {t.day:02d},   {t.day:02d},",
        "start_hour": f" start_hour             = {t.hour:02d},   {t.hour:02d},",
        "restart": " restart                = .true.,",
    }
    for key, line in subs.items():
        # \b keeps "restart" from also matching restart_interval / io_form_restart
        text, n = re.subn(rf"^\s*{key}\b\s*=.*$", line, text, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"namelist: expected exactly one '{key}' line, found {n}")

    Path(args.out or args.namelist).write_text(text)
    print(f"namelist set to restart from {args.restart_time}")


if __name__ == "__main__":
    main()
