#!/usr/bin/env python3
"""Stop wrf.exe at a restart boundary, not at a fixed minute.

WHY: a segment used to end with `timeout 280m`, a blind kill. Everything a
segment simulates past its newest COMPLETE restart is thrown away, so a blind
kill wastes up to a whole restart interval (12 model hours, ~20-100 min of
runtime depending on the nest), and if it lands inside a restart write it
throws away that restart too. The job allows 350 min, so there is headroom to
run on to the next restart instead. (Run 38901516183 looked like a mid-write
kill, but the log showed the model 4.4 h past the "partial" restart: that was
pick_restart.sh's size test misfiring on compressed files, fixed alongside.)

The budget is therefore a SOFT deadline. Once it passes, this watcher:
  - keeps going while a restart is being written (never cuts one off);
  - stops as soon as a restart that finished after the deadline is on disk;
  - stops straight away if, at the measured sim-rate, the next restart would
    not land before the HARD cap -- the work past the newest restart is lost
    either way, so there is no point burning runner time on it;
  - stops at the hard cap no matter what (pick_restart.sh discards a
    partial restart, so the worst case is exactly the old behaviour).
The hard cap must leave room under the job's timeout-minutes for the steps
before and after the WRF step; run_pipeline.sh also keeps `timeout` on
mpirun just past the hard cap as a backstop in case this watcher dies.

Completeness comes from the model's own log, not file sizes: domain N's
restart at T is written before domain N steps past T, so once rsl.error.0000
shows "Timing for main: time >T on domain N" for every domain that is due a
restart at T, the set is complete. A nest past its end time writes none.

Usage (run_pipeline.sh starts it in the background next to `docker run`):
  wrf_budget.py --wrf-dir RUN_DIR/wrf --soft-min 280 --hard-min 320 \
      (--container wrf_SITE | --pid PID) [--minute-sec 60] [--poll-sec 15]
On a stop it writes WRF_DIR/BUDGET_STOP (the reason) and then stops wrf.exe.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

TS = "%Y-%m-%d_%H:%M:%S"
TIMING = re.compile(
    r"Timing for main: time (\d{4}-\d\d-\d\d_\d\d:\d\d:\d\d) on domain\s+(\d+):")
STOP_FILE = "BUDGET_STOP"
# Trailing window for the sim-rate. Long enough to smooth over history
# writes, short enough to see the speed-up when an inner nest stops.
RATE_WINDOW_S = 20 * 60
# Predicted next-restart time is padded by this factor before comparing it
# with the hard cap: waiting on a restart that then misses only costs runner
# minutes, stopping early on one that would have fit costs a restart interval.
RATE_SAFETY = 1.15


def namelist_info(text):
    """(max_dom, restart_interval_min, d01 start, [end per domain])."""
    def ints(key):
        m = re.search(rf"^\s*{key}\s*=\s*([^\n]*)", text, flags=re.M)
        if not m:
            raise SystemExit(f"namelist.input: no '{key}' line")
        vals = [int(v) for v in re.findall(r"\d+", m.group(1))]
        if not vals:
            raise SystemExit(f"namelist.input: empty '{key}'")
        return vals

    def nth(vals, i):
        return vals[min(i, len(vals) - 1)]

    max_dom = ints("max_dom")[0]
    interval = ints("restart_interval")[0]
    keys = ("year", "month", "day", "hour")
    start = datetime(*(ints(f"start_{k}")[0] for k in keys))
    ends = [datetime(*(nth(ints(f"end_{k}"), i) for k in keys))
            for i in range(max_dom)]
    return max_dom, interval, start, ends


class Rsl:
    """Incremental reader: newest model time reached per domain."""

    def __init__(self, path):
        self.path, self.pos, self.latest = Path(path), 0, {}

    def update(self):
        try:
            with open(self.path, "rb") as f:
                if os.fstat(f.fileno()).st_size < self.pos:  # log restarted
                    self.pos, self.latest = 0, {}
                f.seek(self.pos)
                chunk = f.read()
        except FileNotFoundError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.pos += end + 1
        for m in TIMING.finditer(chunk[:end + 1].decode("ascii", "replace")):
            t, d = datetime.strptime(m.group(1), TS), int(m.group(2))
            if t > self.latest.get(d, datetime.min):
                self.latest[d] = t


class Budget:
    """The decision, separated from the clock and from how wrf.exe is stopped."""

    def __init__(self, wrf_dir, soft_s, hard_s):
        self.dir = Path(wrf_dir)
        self.soft_s, self.hard_s = soft_s, max(hard_s, soft_s)
        self.rsl = Rsl(self.dir / "rsl.error.0000")
        self.samples = []      # (wall seconds, d01 model time)
        self.baseline = None   # newest complete restart when the soft deadline passed
        self.armed = False
        self._nl = None

    def namelist(self):
        if self._nl is None:
            self._nl = namelist_info((self.dir / "namelist.input").read_text())
        return self._nl

    def restart_times(self):
        out = []
        for p in self.dir.glob("wrfrst_d01_*"):
            try:
                out.append(datetime.strptime(p.name[len("wrfrst_d01_"):], TS))
            except ValueError:
                pass
        return sorted(out)

    def complete(self, t):
        max_dom, _, _, ends = self.namelist()
        for n in range(1, max_dom + 1):
            if n > 2 and ends[n - 1] <= t:
                continue  # the nest has stopped: it is due no restart at t
            if not (self.dir / f"wrfrst_d{n:02d}_{t.strftime(TS)}").exists():
                return False  # not started yet (or a nest not reached yet)
            if self.rsl.latest.get(n, datetime.min) <= t:
                return False  # domain n has not stepped past t: still writing
        return True

    def sec_per_sim_sec(self, now):
        recent = [s for s in self.samples if s[0] >= now - RATE_WINDOW_S]
        if len(recent) < 2:
            recent = self.samples[-2:]
        if len(recent) < 2:
            return None
        (w0, t0), (w1, t1) = recent[0], recent[-1]
        sim = (t1 - t0).total_seconds()
        return (w1 - w0) / sim if sim > 0 else None

    def decide(self, now):
        """('run'|'stop', reason) at `now` seconds since wrf.exe started."""
        self.rsl.update()
        sim = self.rsl.latest.get(1)
        if sim and (not self.samples or sim > self.samples[-1][1]):
            self.samples.append((now, sim))
        if now < self.soft_s:
            return "run", "inside budget"
        if now >= self.hard_s:
            return "stop", "hard cap reached"

        rts = self.restart_times()
        done = [t for t in rts if self.complete(t)]
        newest = done[-1] if done else None
        if not self.armed:
            self.armed, self.baseline = True, newest
        writing = [t for t in rts if t not in done and (newest is None or t > newest)]
        if writing:
            return "run", f"restart {writing[0].strftime(TS)} is being written"
        if newest is not None and (self.baseline is None or newest > self.baseline):
            return "stop", f"restart {newest.strftime(TS)} complete after the budget"

        # Nothing in flight. Is the next restart (or the end of the forecast,
        # which is even better) reachable before the hard cap?
        if sim is None:
            return "run", "no model time in rsl.error.0000 yet"
        _, interval, start, ends = self.namelist()
        step = timedelta(minutes=interval)
        nxt = start + step * ((sim - start) // step + 1)
        target = min(nxt, ends[0])
        rate = self.sec_per_sim_sec(now)
        if rate is None:
            return "run", "no sim-rate yet"
        eta = now + (target - sim).total_seconds() * rate * RATE_SAFETY
        label = "end of forecast" if target == ends[0] else f"restart {target.strftime(TS)}"
        if eta <= self.hard_s:
            return "run", (f"waiting for {label}, due ~{(eta - self.soft_s) / 60:.0f} min "
                           f"past the budget")
        keep = newest.strftime(TS) if newest else "none"
        return "stop", (f"{label} due ~{(eta - self.soft_s) / 60:.0f} min past the budget, "
                        f"beyond the hard cap; newest complete restart {keep}")


def stop(args):
    if args.container:
        # TERM to the container's init, which tears the whole container down;
        # KILL after 30 s. Every file wrf.exe writes at a restart time is
        # closed by now (history is written before the restart at each step).
        subprocess.run(["docker", "stop", "-t", "30", args.container], check=False,
                       stdout=subprocess.DEVNULL)
    else:
        try:
            os.kill(args.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wrf-dir", required=True)
    ap.add_argument("--soft-min", type=float, required=True)
    ap.add_argument("--hard-min", type=float, required=True)
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--container", help="docker container running wrf.exe")
    who.add_argument("--pid", type=int, help="process to SIGTERM (tests)")
    # Tests compress time; the env spelling reaches the watcher through
    # run_pipeline.sh without a test-only flag there.
    ap.add_argument("--minute-sec", type=float,
                    default=float(os.environ.get("WRF_BUDGET_MINUTE_SEC", 60)),
                    help="seconds per budget minute (default 60)")
    ap.add_argument("--poll-sec", type=float, default=None,
                    help="default: a quarter of a budget minute")
    ap.add_argument("--started-at", type=float, default=None,
                    help="epoch seconds wrf.exe started (default: now)")
    args = ap.parse_args()

    m = args.minute_sec
    poll = args.poll_sec if args.poll_sec is not None else m / 4
    b = Budget(args.wrf_dir, args.soft_min * 60, args.hard_min * 60)
    t0 = args.started_at if args.started_at is not None else time.time()
    last = None
    print(f"budget watcher: soft {args.soft_min:g} min, hard {args.hard_min:g} min",
          flush=True)
    while True:
        # Decide in "model-budget seconds" so --minute-sec scales everything.
        now = (time.time() - t0) / m * 60
        try:
            action, why = b.decide(now)
        except Exception as e:  # never let a parse error end the forecast early
            action = "stop" if now >= b.hard_s else "run"
            why = f"watcher error: {e!r}"
        # Log each state once, not every poll as the ETA wobbles.
        state = re.sub(r"~\d+ min", "", why)
        if now >= b.soft_s and state != last:
            print(f"budget watcher [{now / 60:.1f} min]: {why}", flush=True)
            last = state
        if action == "stop":
            (b.dir / STOP_FILE).write_text(why + "\n")
            stop(args)
            return 0
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
