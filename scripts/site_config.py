#!/usr/bin/env python3
"""Per-site settings read from the private config repo's sites.json.

Stdlib only, on purpose: make_namelists.py and the workflow's inline steps run
under the runner's bare python3, which has no pandas or wrf-python.

One place answers the questions every stage used to answer for itself:

  domain-dir SITE         folder under sites/ holding namelists + geo_em.
                          `domain_dir` in the entry, else the site key. Lets a
                          station (supertubos) run on a domain (peniche) that a
                          later station (nazare) can reuse after a d03 move.
  hours SITE              forecast length when nobody asked for one: the
                          entry's `hours`, else 168 (the Cloudbreak default).
  nest-hours SITE CYCLE   how far the innermost nest runs for THIS cycle.
                          `wrf_domain.d03_hours` is either an int (every cycle)
                          or a map keyed by cycle hour, {"00": 48, "12": 60},
                          so the 1 km -> 3 km seam can always fall at 00Z.
                          0 = no inner nest.

Usage from a shell step: python3 scripts/site_config.py nest-hours supertubos 2026101612
Pass --sites PATH when sites.json is not at the repo root.
"""
import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_HOURS = 168


def load_sites(path=None):
    return json.loads(Path(path or REPO / "sites.json").read_text())


def entry(sites, site):
    return sites.get(site) or {}


def domain_dir(sites, site):
    return entry(sites, site).get("domain_dir") or site


def run_hours(sites, site):
    return int(entry(sites, site).get("hours") or DEFAULT_HOURS)


def nest_hours(sites, site, cycle=None):
    """Innermost-nest length in forecast hours for `cycle` (YYYYMMDDHH)."""
    raw = (entry(sites, site).get("wrf_domain") or {}).get("d03_hours")
    if not raw:
        return 0
    if isinstance(raw, dict):
        if not cycle:
            # No cycle to key on: the shortest declared span is the only
            # value that is safe for every cycle.
            return int(min(raw.values()))
        hh = str(cycle)[8:10]
        if hh not in raw:
            raise SystemExit(f"{site}: no d03_hours for {hh}Z cycles in {sorted(raw)}")
        return int(raw[hh])
    return int(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["domain-dir", "hours", "nest-hours"])
    ap.add_argument("site")
    ap.add_argument("cycle", nargs="?")
    ap.add_argument("--sites", default=None)
    a = ap.parse_args()
    sites = load_sites(a.sites)
    if a.what == "domain-dir":
        print(domain_dir(sites, a.site))
    elif a.what == "hours":
        print(run_hours(sites, a.site))
    else:
        print(nest_hours(sites, a.site, a.cycle))


if __name__ == "__main__":
    main()
