#!/usr/bin/env python3
"""Rewrite the site's namelist.wps and namelist.input for a given forcing cycle.

Usage: make_namelists.py --site teahupoo --cycle 2026082200 --hours 48 \
    --forcing gfs|ifs --out RUN_DIR

Reads sites/<site>/namelist.{wps,input}, replaces every date field with the
cycle start/end, and writes the results into RUN_DIR. Forcing-dependent
settings (num_metgrid_levels) and the restart cadence for segmented runs are
rewritten; all other settings (domains, physics, interval_seconds) pass
through untouched.
"""
import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def rewrite_wps(text: str, start: datetime, end: datetime, forcing: str) -> str:
    fmt = "%Y-%m-%d_%H:%M:%S"
    text = re.sub(r"start_date\s*=.*",
                  f" start_date       = '{start:{fmt}}','{start:{fmt}}',", text)
    text = re.sub(r"end_date\s*=.*",
                  f" end_date         = '{end:{fmt}}','{end:{fmt}}',", text)
    if forcing == "ifs":
        # IFS atmosphere + GFS soil/landmask (see vtables/Vtable.IFS).
        text = re.sub(r"fg_name\s*=.*", " fg_name = 'FILE','SOIL',", text)
    return text


# met_em vertical levels per forcing: isobaric levels in the source + 1 sfc.
# GFS pgrb2 0p25 -> 34; IFS 0p25 open data -> 14 pl + sfc = 15.
METGRID_LEVELS = {"gfs": 34, "ifs": 15}

# Restart files every 12 forecast hours so segmented runs can resume near
# where the previous job's wall-clock budget expired (minutes of model time).
RESTART_INTERVAL_MIN = 720


def rewrite_input(text: str, start: datetime, end: datetime, hours: int,
                  forcing: str) -> str:
    days, rem = divmod(hours, 24)
    subs = {
        "num_metgrid_levels": f" num_metgrid_levels     = {METGRID_LEVELS[forcing]},",
        "restart_interval": f" restart_interval       = {RESTART_INTERVAL_MIN},",
        "run_days": f" run_days               = {days},",
        "run_hours": f" run_hours              = {rem},",
        "start_year": f" start_year             = {start.year}, {start.year},",
        "start_month": f" start_month            = {start.month:02d},   {start.month:02d},",
        "start_day": f" start_day              = {start.day:02d},   {start.day:02d},",
        "start_hour": f" start_hour             = {start.hour:02d},   {start.hour:02d},",
        "end_year": f" end_year               = {end.year}, {end.year},",
        "end_month": f" end_month              = {end.month:02d},   {end.month:02d},",
        "end_day": f" end_day                = {end.day:02d},   {end.day:02d},",
        "end_hour": f" end_hour               = {end.hour:02d},   {end.hour:02d},",
    }
    for key, line in subs.items():
        text, n = re.subn(rf"^\s*{key}\s*=.*$", line, text, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"namelist.input: expected exactly one '{key}' line, found {n}")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--cycle", required=True, help="YYYYMMDDHH")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--forcing", choices=sorted(METGRID_LEVELS), default="gfs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start = datetime.strptime(args.cycle, "%Y%m%d%H")
    end = start + timedelta(hours=args.hours)
    site_dir = REPO / "sites" / args.site
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    (out / "namelist.wps").write_text(
        rewrite_wps((site_dir / "namelist.wps").read_text(), start, end,
                    args.forcing))
    (out / "namelist.input").write_text(
        rewrite_input((site_dir / "namelist.input").read_text(), start, end,
                      args.hours, args.forcing))
    print(f"namelists written to {out} for {args.site} {args.cycle} "
          f"+{args.hours}h forcing={args.forcing}")


if __name__ == "__main__":
    main()
