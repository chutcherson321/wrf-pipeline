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
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def max_dom(text: str) -> int:
    """Domain count declared by the namelist, so per-domain lines are rebuilt
    at the right width. A site running a short-range inner nest has three."""
    m = re.search(r"^\s*max_dom\s*=\s*(\d+)", text, flags=re.M)
    if not m:
        raise SystemExit("namelist: no 'max_dom' line")
    return int(m.group(1))


def per_dom(key, value, n):
    return f" {key:<22} = " + " ".join([f"{value}," for _ in range(n)])


def per_dom_each(key, values):
    """Per-domain line where the domains DIFFER -- a short-range inner nest
    stops before its parents, expressed as an earlier end_* on the innermost
    domain only."""
    return f" {key:<22} = " + " ".join([f"{v}," for v in values])


def rewrite_wps(text: str, start: datetime, end: datetime, forcing: str) -> str:
    fmt = "%Y-%m-%d_%H:%M:%S"
    n = max_dom(text)
    text = re.sub(r"^\s*start_date\s*=.*$",
                  per_dom("start_date", f"'{start:{fmt}}'", n), text, flags=re.M)
    text = re.sub(r"^\s*end_date\s*=.*$",
                  per_dom("end_date", f"'{end:{fmt}}'", n), text, flags=re.M)
    if forcing == "ifs":
        # IFS atmosphere + GFS soil/landmask (see vtables/Vtable.IFS).
        text = re.sub(r"fg_name\s*=.*", " fg_name = 'FILE','SOIL',", text)
    return text


# met_em vertical levels per forcing: isobaric levels in the source + 1 sfc.
# GFS pgrb2 0p25 -> 34; IFS 0p25 open data -> 14 pl + sfc = 15.
METGRID_LEVELS = {"gfs": 34, "ifs": 15}

# Restart files every 12 forecast hours (default) so segmented runs can
# resume near where the previous job's wall-clock budget expired (minutes of
# model time).
RESTART_INTERVAL_MIN = 720


def rewrite_input(text: str, start: datetime, end: datetime, hours: int,
                  forcing: str, restart_interval: int,
                  nest_hours: int = 0) -> str:
    """`nest_hours` > 0 stops the INNERMOST domain that early.

    This has to happen in the INITIAL namelist, not only at a restart.
    segment_namelist.py bounds the nest from segment 2 onward, but segment 1 is
    the only segment a run ever reaches if the nest makes it too slow to
    finish -- exactly what happened on 2026-08-28. A 1 km d03 was declared in
    the site config, ran the full 168 h in segment 1 at roughly 27x the cost per
    3:1 ratio, and both lanes died in the WRF step with their logs lost."""
    days, rem = divmod(hours, 24)
    n = max_dom(text)
    ends = [end] * n
    if nest_hours > 0 and n >= 2 and nest_hours < hours:
        ends[-1] = start + timedelta(hours=nest_hours)
    subs = {
        "num_metgrid_levels": f" num_metgrid_levels     = {METGRID_LEVELS[forcing]},",
        "restart_interval": f" restart_interval       = {restart_interval},",
        # One output file per 12 forecast hours (history_interval is 60 min).
        # A file is complete once the next one opens, which is what lets the
        # run publish in 12-hour blocks while it is still integrating.
        "frames_per_outfile": per_dom("frames_per_outfile", 12, n),
        "run_days": f" run_days               = {days},",
        "run_hours": f" run_hours              = {rem},",
        "start_year": per_dom("start_year", start.year, n),
        "start_month": per_dom("start_month", f"{start.month:02d}", n),
        "start_day": per_dom("start_day", f"{start.day:02d}", n),
        "start_hour": per_dom("start_hour", f"{start.hour:02d}", n),
        "end_year": per_dom_each("end_year", [e.year for e in ends]),
        "end_month": per_dom_each("end_month", [f"{e.month:02d}" for e in ends]),
        "end_day": per_dom_each("end_day", [f"{e.day:02d}" for e in ends]),
        "end_hour": per_dom_each("end_hour", [f"{e.hour:02d}" for e in ends]),
    }
    for key, line in subs.items():
        text, hits = re.subn(rf"^\s*{key}\s*=.*$", line, text, count=1, flags=re.M)
        if hits != 1:
            raise SystemExit(f"namelist.input: expected exactly one '{key}' line, found {hits}")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--cycle", required=True, help="YYYYMMDDHH")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--forcing", choices=sorted(METGRID_LEVELS), default="gfs")
    ap.add_argument("--restart-interval", type=int, default=RESTART_INTERVAL_MIN,
                    help="restart file cadence, minutes of model time")
    ap.add_argument("--out", required=True)
    ap.add_argument("--nest-hours", type=int, default=0,
                    help="stop the innermost domain this many forecast hours "
                         "in; 0 falls back to the site's wrf_domain.d03_hours")
    args = ap.parse_args()

    start = datetime.strptime(args.cycle, "%Y%m%d%H")
    end = start + timedelta(hours=args.hours)
    site_dir = REPO / "sites" / args.site

    # Precedence: an explicit --nest-hours wins, otherwise the site config that
    # DECLARES the nest also bounds it. Defaulting from sites.json means a site
    # cannot declare a 1 km nest and silently get an UNBOUNDED one because a
    # separate repo variable was never set -- which is how 2026-08-28 broke.
    nest_hours = args.nest_hours
    if nest_hours <= 0:
        try:
            site = json.loads((REPO / "sites.json").read_text())[args.site]
            nest_hours = int((site.get("wrf_domain") or {}).get("d03_hours") or 0)
        except Exception:
            nest_hours = 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    (out / "namelist.wps").write_text(
        rewrite_wps((site_dir / "namelist.wps").read_text(), start, end,
                    args.forcing))
    (out / "namelist.input").write_text(
        rewrite_input((site_dir / "namelist.input").read_text(), start, end,
                      args.hours, args.forcing, args.restart_interval,
                      nest_hours))
    print(f"namelists written to {out} for {args.site} {args.cycle} "
          f"+{args.hours}h forcing={args.forcing}"
          + (f" inner nest +{nest_hours}h" if nest_hours > 0 else ""))


if __name__ == "__main__":
    main()
