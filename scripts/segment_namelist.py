#!/usr/bin/env python3
"""Rewrite a run's namelist.input to resume from a WRF restart file.

Used by segmented runs: sets restart = .true. and moves start_* to the
restart timestamp, leaving end_* and everything else untouched. Needs no
site config — it operates on the namelist already templated for the run.

Per-domain lines are rebuilt for however many domains max_dom declares, so
a site running a third (inner) nest needs no change here.

Short-range inner nest: pass --cycle-start and --nest-min-hours to stop the
innermost domain(s) partway through a long forecast — e.g. a 1 km d03 out
to +48 h while the 3 km d02 carries on to +168 h. Once the restart being
resumed from is at or past that lead time, max_dom is reduced to
--keep-doms for the remaining segments. Dropping a nest at a restart is
safe: WRF reads restart files for domains 1..max_dom and ignores the rest.
Going the other way (adding a nest mid-run) is not supported.

Usage: segment_namelist.py --namelist run_dir/namelist.input \
    --restart-time 2026-08-24_12:00:00 [--out run_dir/namelist.input] \
    [--cycle-start 2026-08-24_00:00:00 --nest-min-hours 24 [--keep-doms 2]]
"""
import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path

TS = "%Y-%m-%d_%H:%M:%S"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--namelist", required=True)
    ap.add_argument("--restart-time", required=True, help="YYYY-MM-DD_HH:MM:SS")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cycle-start", default=None,
                    help="forecast hour 0, needed with --nest-min-hours")
    # MIN, not an exact cutoff: the drop happens at the first RESTART at or
    # after this lead time, so the nest runs at least this long and possibly
    # into the next restart interval. The old --nest-hours spelling is kept so
    # an older caller does not silently lose its nest.
    ap.add_argument("--nest-min-hours", "--nest-hours", dest="nest_min_hours",
                    type=int, default=0,
                    help="drop to --keep-doms domains at the first restart "
                         "at or after this lead time")
    ap.add_argument("--keep-doms", type=int, default=2,
                    help="domains to keep once --nest-min-hours has passed")
    args = ap.parse_args()

    if args.nest_min_hours and not args.cycle_start:
        raise SystemExit("--nest-min-hours needs --cycle-start")

    t = datetime.strptime(args.restart_time, TS)
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

    ndom = grab("max_dom")
    drop_to = 0
    if args.nest_min_hours:
        cutoff = datetime.strptime(args.cycle_start, TS) + timedelta(hours=args.nest_min_hours)
        if t >= cutoff and ndom > args.keep_doms:
            drop_to = args.keep_doms
            print(f"inner nest done at +{args.nest_min_hours}h (min) "
                  f"({cutoff:{TS}}) — max_dom {ndom} -> {drop_to}")
            ndom = drop_to

    def per_dom(key, value):
        # One value per active domain; nests inherit the parent's restart time.
        return f" {key:<22} = " + " ".join([f"{value}," for _ in range(ndom)])

    subs = {
        "run_days": f" run_days               = {days},",
        "run_hours": f" run_hours              = {rem_h},",
        "start_year": per_dom("start_year", t.year),
        "start_month": per_dom("start_month", f"{t.month:02d}"),
        "start_day": per_dom("start_day", f"{t.day:02d}"),
        "start_hour": per_dom("start_hour", f"{t.hour:02d}"),
        "restart": " restart                = .true.,",
    }
    if drop_to:
        subs["max_dom"] = f" max_dom                = {drop_to},"

    for key, line in subs.items():
        # \b keeps "restart" from also matching restart_interval / io_form_restart
        text, n = re.subn(rf"^\s*{key}\b\s*=.*$", line, text, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"namelist: expected exactly one '{key}' line, found {n}")

    Path(args.out or args.namelist).write_text(text)
    print(f"namelist set to restart from {args.restart_time} (max_dom={ndom})")


if __name__ == "__main__":
    main()
