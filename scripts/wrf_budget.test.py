#!/usr/bin/env python3
"""Tests for the segment handoff: wrf_budget.py (stop wrf.exe at a restart
boundary, never mid-write) and pick_restart.sh (hand off the newest COMPLETE
restart, judged by rsl.error.0000, not by file size).

Three parts:
  1. The watcher's decision, against hand-built run dirs (no clock, no processes).
  2. pick_restart.sh, including the shape of run 38901516183 (2026-10-16): a
     complete newest restart that is SMALLER than the previous one, because
     the files are compressed. The old size test threw it away.
  3. A compressed-time replay: a fake wrf.exe writes rsl.error.0000 lines and
     restart files on a schedule, and the REAL watcher (or, for comparison, a
     blind `timeout` at the budget) stops it; then the REAL pick_restart.sh
     chooses the handoff. The run_pipeline.sh wiring runs too, with a stub
     `docker` on PATH.

Stdlib only. Run: python3 scripts/wrf_budget.test.py
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from wrf_budget import STOP_FILE, TS, Budget  # noqa: E402

FAILS = 0
START = datetime(2026, 10, 16)
END = datetime(2026, 10, 21)


def check(name, cond, detail=""):
    global FAILS
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    FAILS += 0 if cond else 1


def namelist(max_dom=2, nest_end=None, interval=720):
    ends = [END] * max_dom
    if nest_end:
        ends[-1] = nest_end

    def row(key, vals):
        return f" {key:<22} = " + " ".join(f"{v}," for v in vals)

    lines = [row("max_dom", [max_dom]), row("restart_interval", [interval])]
    for k in ("year", "month", "day", "hour"):
        lines.append(row(f"start_{k}", [getattr(START, k)] * max_dom))
        lines.append(row(f"end_{k}", [getattr(e, k) for e in ends]))
    return "&time_control\n" + "\n".join(lines) + "\n/\n"


def timing(t, dom):
    return (f"Timing for main: time {t.strftime(TS)} on domain {dom:>3}:"
            f"    0.71280 elapsed seconds\n")


def rst(d, dom, t, size=100):
    (Path(d) / f"wrfrst_d{dom:02d}_{t.strftime(TS)}").write_bytes(b"x" * size)


# ---------------------------------------------------------------- 1. decision
def h(n):
    return START + timedelta(hours=n)


def run_dir(max_dom=2, nest_end=None):
    d = tempfile.mkdtemp(prefix="wrfbudget-")
    Path(d, "namelist.input").write_text(namelist(max_dom, nest_end))
    Path(d, "rsl.error.0000").write_text("")
    return d


def log(d, *pairs):
    with open(Path(d, "rsl.error.0000"), "a") as f:
        for t, dom in pairs:
            f.write(timing(t, dom))


def decision_tests():
    M = 60  # seconds per minute
    # Restart +60 h done, model now at +71 h; the +72 h write starts.
    d = run_dir()
    rst(d, 1, h(60)); rst(d, 2, h(60))
    log(d, (h(60.5), 1), (h(60.5), 2))
    b = Budget(d, 280 * M, 320 * M)
    log(d, (h(70), 1))
    check("inside the budget: run", b.decide(270 * M)[0] == "run")
    log(d, (h(71.9), 1), (h(71.9), 2))
    rst(d, 1, h(72))  # d01 file exists, d01 not past +72 yet: mid-write
    a, why = b.decide(280 * M)
    check("past budget, restart mid-write: keep running", a == "run" and "being written" in why, why)
    rst(d, 2, h(72)); log(d, (h(72.1), 1))
    a, why = b.decide(280.5 * M)
    check("d01 done, d02 still writing: keep running", a == "run" and "being written" in why, why)
    log(d, (h(72.05), 2))
    a, why = b.decide(281 * M)
    check("both domains past +72 h: stop", a == "stop" and "2026-10-19_00:00:00 complete" in why, why)

    # A restart that was already complete when the budget passed is not a
    # reason to stop -- unless the next one cannot make the hard cap.
    d = run_dir()
    rst(d, 1, h(72)); rst(d, 2, h(72))
    b = Budget(d, 280 * M, 320 * M)
    log(d, (h(72.1), 1), (h(72.1), 2))
    b.decide(250 * M)
    log(d, (h(80), 1), (h(80), 2))  # 8 sim-h in 30 min: next +84 h in ~15 min
    a, why = b.decide(280 * M)
    check("next restart fits before the hard cap: wait", a == "run" and "waiting for restart" in why, why)

    d = run_dir()
    rst(d, 1, h(72)); rst(d, 2, h(72))
    b = Budget(d, 280 * M, 320 * M)
    log(d, (h(72.1), 1), (h(72.1), 2))
    b.decide(200 * M)
    log(d, (h(73), 1), (h(73), 2))  # ~0.9 sim-h in 80 min: +84 h is hours off
    a, why = b.decide(280 * M)
    check("next restart cannot make the hard cap: stop now", a == "stop" and "beyond the hard cap" in why, why)

    d = run_dir()
    rst(d, 1, h(72))  # a write that never finishes
    b = Budget(d, 280 * M, 320 * M)
    log(d, (h(71.9), 1), (h(71.9), 2))
    check("hard cap always wins", b.decide(320 * M) == ("stop", "hard cap reached"))

    # Inner nest: still running at +24 h, so the restart needs d03 too.
    d = run_dir(max_dom=3, nest_end=h(48))
    for dom in (1, 2):
        rst(d, dom, h(24))
    log(d, (h(24.1), 1), (h(24.1), 2), (h(24.05), 3))
    b = Budget(d, 0, 320 * M)
    b.rsl.update()
    check("nest still running: restart without d03 is not complete", not b.complete(h(24)))
    rst(d, 3, h(24))
    check("nest still running: d01-d03 all past +24 h = complete", b.complete(h(24)))
    # Past the nest's end, d03 is due no restart.
    rst(d, 1, h(60)); rst(d, 2, h(60))
    log(d, (h(60.1), 1), (h(60.1), 2))
    b.rsl.update()
    check("nest finished: d01+d02 alone are complete", b.complete(h(60)))

    # Forecast end inside reach counts like a restart (finishing is better).
    d = run_dir()
    rst(d, 1, h(108)); rst(d, 2, h(108))
    b = Budget(d, 280 * M, 320 * M)
    log(d, (h(108.1), 1), (h(108.1), 2))
    b.decide(250 * M)
    log(d, (h(118), 1), (h(118), 2))
    a, why = b.decide(280 * M)
    check("end of forecast within reach: wait for it", a == "run" and "end of forecast" in why, why)


# ---------------------------------------------------------- 2. pick_restart.sh
def pick(d):
    p = subprocess.run(["bash", str(HERE / "pick_restart.sh"), d], capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def pick_tests():
    # Run 38901516183: +60 h and +72 h restarts both on disk, +72 h smaller
    # (67587355 vs 73358701 bytes for d01), model at +76.4 h when stopped.
    d = run_dir()
    for t, s1, s2 in ((h(60), 73358701 // 1000, 150), (h(72), 67587355 // 1000, 140)):
        rst(d, 1, t, s1); rst(d, 2, t, s2)
    log(d, (h(76.3), 1), (h(76.4), 2))
    rc, got, err = pick(d)
    check("smaller-but-complete newest restart is handed off (run 38901516183)",
          rc == 0 and got == "2026-10-19_00:00:00", f"{rc} {got!r} {err}")
    check("the older set is pruned", not Path(d, f"wrfrst_d01_{h(60).strftime(TS)}").exists())

    # A real truncation: d02 has not stepped past +72 h, so its write is open.
    d = run_dir()
    for t in (h(60), h(72)):
        rst(d, 1, t); rst(d, 2, t)
    log(d, (h(72.1), 1), (h(71.9), 2))
    rc, got, err = pick(d)
    check("restart still being written is skipped", got == "2026-10-18_12:00:00" and "incomplete" in err,
          f"{got!r} {err}")

    # Inner nest still running at +24 h and its d03 write cut off.
    d = run_dir(max_dom=3, nest_end=h(48))
    for t in (h(12), h(24)):
        for dom in (1, 2, 3):
            rst(d, dom, t)
    log(d, (h(24.2), 1), (h(24.1), 2), (h(23.9), 3))
    rc, got, err = pick(d)
    check("nest restart cut off: falls back", got == "2026-10-16_12:00:00", f"{got!r} {err}")

    # Nest finished: later sets have no d03, and the log's d03 time is old.
    d = run_dir(max_dom=3, nest_end=h(24))
    rst(d, 1, h(12)); rst(d, 2, h(12)); rst(d, 3, h(12))
    rst(d, 1, h(36)); rst(d, 2, h(36))
    log(d, (h(24), 3), (h(36.5), 1), (h(36.5), 2))
    rc, got, err = pick(d)
    check("finished nest does not block a d01+d02 set", got == "2026-10-17_12:00:00", f"{got!r} {err}")

    # No log at all: the old size check, conservative.
    d = run_dir()
    Path(d, "rsl.error.0000").unlink()
    rst(d, 1, h(60), 100); rst(d, 2, h(60))
    rst(d, 1, h(72), 90); rst(d, 2, h(72))
    rc, got, err = pick(d)
    check("no rsl: size fallback still rejects a size mismatch",
          got == "2026-10-18_12:00:00" and "falling back" in err, f"{got!r} {err}")

    # Log present but nothing past any restart: fail loudly, hand off nothing.
    d = run_dir()
    rst(d, 1, h(12)); rst(d, 2, h(12))
    log(d, (h(11), 1), (h(11), 2))
    rc, got, err = pick(d)
    check("no complete set at all: exit 1", rc == 1 and got == "", f"{rc} {got!r} {err}")


# ------------------------------------------------------------ 3. fake wrf.exe
def fake_wrf(d, min_per_sim_h, write_min, minute_sec, started_at):
    """Integrate START->END in 30-min model steps at min_per_sim_h wall minutes
    per model hour; at every 12 h boundary write d01 then d02 restarts in ten
    chunks, `write_min` wall minutes each. Restart sizes: 1000 / 2000 bytes."""
    d = Path(d)
    wall = 0.0  # wall minutes since start

    def at(minute):
        delay = started_at + minute * minute_sec - time.time()
        if delay > 0:
            time.sleep(delay)

    rsl = open(d / "rsl.error.0000", "a")
    t = START
    while t < END:
        if t > START and (t - START) % timedelta(hours=12) == timedelta(0):
            for dom, size in ((1, 1000), (2, 2000)):
                f = open(d / f"wrfrst_d{dom:02d}_{t.strftime(TS)}", "wb")
                for _ in range(10):
                    wall += write_min / 10
                    at(wall)
                    f.write(b"x" * (size // 10)); f.flush()
                f.close()
        t += timedelta(minutes=30)
        wall += min_per_sim_h / 2
        at(wall)
        rsl.write(timing(t, 1) + timing(t, 2)); rsl.flush()
    rsl.write("d01 2026-10-21_00:00:00 wrf: SUCCESS COMPLETE WRF\n"); rsl.flush()


MINUTE_SEC = 0.03  # one model-budget minute = 30 ms: a 320-min segment in ~10 s


def replay(name, min_per_sim_h, mode, expect_pick, max_stop_min=None):
    d = run_dir()
    started = time.time() + 0.5
    fake = subprocess.Popen([sys.executable, __file__, "--fake-wrf", d, str(min_per_sim_h),
                             str(MINUTE_SEC), str(started)])
    watcher = None
    if mode == "timeout":  # the old behaviour: a blind kill at 280 min
        delay = started + 280 * MINUTE_SEC - time.time()
        try:
            fake.wait(timeout=max(delay, 0))
        except subprocess.TimeoutExpired:
            fake.send_signal(signal.SIGTERM)
    else:
        watcher = subprocess.Popen(
            [sys.executable, str(HERE / "wrf_budget.py"), "--wrf-dir", d,
             "--soft-min", "280", "--hard-min", "320", "--pid", str(fake.pid),
             "--minute-sec", str(MINUTE_SEC), "--poll-sec", "0.005",
             "--started-at", str(started)],
            stdout=subprocess.PIPE, text=True)
    fake.wait()
    stopped_min = (time.time() - started) / MINUTE_SEC
    out = ""
    if watcher:
        try:
            out, _ = watcher.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            watcher.kill(); out, _ = watcher.communicate()
    sizes = {p.name: p.stat().st_size for p in Path(d).glob("wrfrst_d01_*")}
    pick = subprocess.run(["bash", str(HERE / "pick_restart.sh"), d],
                          capture_output=True, text=True)
    got = pick.stdout.strip()
    print(f"  [{name} / {mode}] stopped at ~{stopped_min:.0f} min; d01 restart sizes "
          f"{ {k[11:24]: v for k, v in sorted(sizes.items())} }")
    if pick.stderr.strip():
        print(f"  [{name} / {mode}] pick_restart: {pick.stderr.strip()}")
    for line in out.splitlines():
        print(f"  [{name} / {mode}] {line}")
    if mode == "watcher":
        reason = Path(d, STOP_FILE).read_text().strip() if Path(d, STOP_FILE).exists() else ""
        check(f"{name}: watcher wrote {STOP_FILE}", bool(reason))
    check(f"{name} / {mode}: hands off {expect_pick}", got == expect_pick, f"got {got!r}")
    if max_stop_min:
        check(f"{name} / {mode}: stopped by {max_stop_min} min", stopped_min <= max_stop_min,
              f"{stopped_min:.0f}")


DOCKER_STUB = """#!/usr/bin/env bash
# `docker` for the run_pipeline.sh test: `run` becomes the fake wrf.exe in the
# -v HOST:/run_dir mount, `stop` TERMs it. Exit codes pass through as docker's do.
case "$1" in
  run)
    while [ $# -gt 0 ]; do
      case "$1" in -v) host="${2%%:*}"; shift ;; esac; shift
    done
    echo $$ > "$host/wrf/.fake_pid"
    exec "$PY" "$TEST" --fake-wrf "$host/wrf" "$FAKE_RATE" "$WRF_BUDGET_MINUTE_SEC" now ;;
  stop) kill -TERM "$(cat "$RUN_DIR_FOR_STUB/wrf/.fake_pid")" ;;
esac
"""


def pipeline(name, rate, expect):
    """The real run_pipeline.sh wrf stage, with `docker` stubbed on PATH, so the
    background launch, the watcher, `wait`, and the exit-code mapping all run.

    The stub starts the fake a variable number of compressed "minutes" after
    the watcher (process start-up at 30 ms/min), so this asserts the
    timing-free property -- the newest restart on disk is whole, and that is
    what gets handed off -- and leaves exact timing to replay()."""
    root = Path(tempfile.mkdtemp(prefix="wrfpipe-"))
    (root / "bin").mkdir()
    stub = root / "bin" / "docker"
    stub.write_text(DOCKER_STUB); stub.chmod(0o755)
    run = root / "run_dir"
    (run / "wrf").mkdir(parents=True)
    (run / "wrf" / "namelist.input").write_text(namelist())
    env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}", PY=sys.executable,
               TEST=__file__, FAKE_RATE=str(rate), RUN_DIR_FOR_STUB=str(run),
               WRF_BUDGET_MIN="280", WRF_HARD_MIN="320",
               WRF_BUDGET_MINUTE_SEC=str(MINUTE_SEC))
    p = subprocess.run(["bash", str(HERE / "run_pipeline.sh"), "test", str(run), "2", "wrf"],
                       env=env, capture_output=True, text=True, timeout=60)
    for line in p.stdout.splitlines():
        if "budget" in line.lower() or "WRF COMPLETE" in line or "finished ok" in line:
            print(f"  [{name} / run_pipeline] {line}")
    check(f"{name} / run_pipeline: exits 0", p.returncode == 0,
          f"rc={p.returncode} {p.stderr.strip()[-300:]}")
    done = (run / "wrf" / "WRF_DONE").exists()
    if expect == "DONE":
        check(f"{name} / run_pipeline: WRF_DONE written", done)
        return
    check(f"{name} / run_pipeline: no WRF_DONE (resumable)", not done)
    check(f"{name} / run_pipeline: reports the budget stop",
          "WRF budget expired" in p.stdout and (run / "wrf" / STOP_FILE).exists())
    newest = sorted(q.name[len("wrfrst_d01_"):] for q in (run / "wrf").glob("wrfrst_d01_*"))[-1]
    pick = subprocess.run(["bash", str(HERE / "pick_restart.sh"), str(run / "wrf")],
                          capture_output=True, text=True)
    check(f"{name} / run_pipeline: hands off the newest restart ({newest}), nothing partial",
          pick.stdout.strip() == newest and "partial" not in pick.stderr,
          f"got {pick.stdout.strip()!r} {pick.stderr.strip()}")


def replay_tests():
    # A blind kill inside a restart write: five 2-min restart writes before
    # +72 h, so the +72 h d01 write spans 279.4-280.4 min and `timeout 280m`
    # lands in it (the run 38901516183 timeline, had the kill been unlucky).
    r = (279.4 - 10) / 72
    replay("kill inside a write", r, "timeout", "2026-10-18_12:00:00")
    replay("kill inside a write", r, "watcher", "2026-10-19_00:00:00", max_stop_min=285)
    # Next restart is ~20 min past the budget: worth waiting for.
    replay("restart at ~300 min", 3.4, "watcher", "2026-10-19_12:00:00", max_stop_min=305)
    # Slow (inner nest) segment: the next restart is ~2 h off, beyond the hard
    # cap, so stop at the budget instead of idling to the cap for nothing.
    replay("next restart beyond cap", 10.6, "watcher", "2026-10-17_00:00:00", max_stop_min=286)
    pipeline("stop past the budget", 2.8, "RESTART")
    pipeline("finishes inside the budget", 0.5, "DONE")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fake-wrf":
        _, _, d, rate, msec, started = sys.argv
        fake_wrf(d, float(rate), 1.0, float(msec),
                 time.time() if started == "now" else float(started))
        sys.exit(0)
    decision_tests()
    pick_tests()
    replay_tests()
    print(f"\n{'FAILED' if FAILS else 'passed'}: {FAILS} failure(s)")
    sys.exit(1 if FAILS else 0)
