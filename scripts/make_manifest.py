#!/usr/bin/env python3
"""Build the windstack manifest (wrf/manifest.json) from an R2 object listing.

Usage:
  make_manifest.py --site cloudbreak --listing listing.json \
      --sites-json sites.json --now 2026-08-23T18:00Z --out manifest.json

`listing.json` is the raw output of `aws s3api list-objects-v2` over the
`wrf/{site}/` prefix. Keys look like wrf/{site}/{model}/{cycle}.json; the
manifest's cycle list and per-cycle availability are derived from what
actually exists, so a model that hasn't published simply shows as pending
on the page (the adapter treats a 404 that way by design).
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

# Known models, in display order. Unknown model dirs get a generic entry so a
# new forcing shows up (unstyled) rather than silently disappearing.
MODEL_META = {
    "wrf-gfs":  {"label": "WRF-GFS",  "driver": "gfs",  "kind": "wrf",    "family": "NCEP"},
    "wrf-ifs":  {"label": "WRF-IFS",  "driver": "ifs",  "kind": "wrf",    "family": "ECMWF"},
    "wrf-aifs": {"label": "WRF-AIFS", "driver": "aifs", "kind": "wrf",    "family": "ECMWF"},
    "gfs":      {"label": "GFS",      "driver": "gfs",  "kind": "global", "family": "NCEP"},
    "ifs":      {"label": "IFS",      "driver": "ifs",  "kind": "global", "family": "ECMWF"},
    "aifs":     {"label": "AIFS",     "driver": "aifs", "kind": "global", "family": "ECMWF"},
}
MODEL_ORDER = list(MODEL_META)
FAMILIES = {"gfs": "NCEP", "ifs": "ECMWF", "aifs": "ECMWF"}
FAMILY_LABELS = {"NCEP": "NCEP / GFS", "ECMWF": "ECMWF / IFS"}


def cycle_label(cycle: str) -> str:
    d = datetime.strptime(cycle, "%Y%m%d%H")
    return f"{d.day} {d.strftime('%b')} {d.strftime('%H')}z"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--listing", required=True)
    ap.add_argument("--sites-json", required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    listed = json.loads(Path(args.listing).read_text())
    pat = re.compile(rf"^wrf/{re.escape(args.site)}/([A-Za-z0-9_\-]+)/(\d{{10}})\.json$")
    found: dict[str, set[str]] = {}
    for obj in listed.get("Contents", []):
        m = pat.match(obj.get("Key", ""))
        if m:
            found.setdefault(m.group(1), set()).add(m.group(2))
    if not found:
        raise SystemExit("no wrf/{site}/{model}/{cycle}.json objects in listing")

    model_ids = sorted(found, key=lambda x: MODEL_ORDER.index(x) if x in MODEL_ORDER else 99)
    models = []
    for mid in model_ids:
        meta = MODEL_META.get(mid) or {
            "label": mid.upper(), "driver": mid.removeprefix("wrf-"),
            "kind": "wrf" if mid.startswith("wrf-") else "global",
            "family": FAMILIES.get(mid.removeprefix("wrf-"), mid.upper()),
        }
        models.append({"id": mid, **meta})

    groups = []
    wrf_members = [m["id"] for m in models if m["kind"] == "wrf"]
    glob_members = [m["id"] for m in models if m["kind"] == "global"]
    if wrf_members:
        groups.append({"key": "wrf", "label": "WRF 3 km",
                       "full": "WRF 3 km downscale", "members": wrf_members})
    if glob_members:
        groups.append({"key": "global", "label": "Global / AI",
                       "full": "Global and AI raw", "members": glob_members})

    all_cycles = sorted(set().union(*found.values()), reverse=True)
    cycles = []
    for cyc in all_cycles:
        d = datetime.strptime(cyc, "%Y%m%d%H")
        cycles.append({
            "id": cyc,
            "init": d.strftime("%Y-%m-%dT%H:00Z"),
            "label": cycle_label(cyc),
            "avail": {mid: {"status": "ready"} for mid in model_ids if cyc in found[mid]},
        })

    s = json.loads(Path(args.sites_json).read_text())[args.site]
    sub = s.get("sublabel", "")
    dom = s.get("wrf_domain", {})
    site_block = {
        "id": args.site,
        "city": s["label"],
        "state": sub.split(",")[0].strip() if "," in sub else "",
        "country": sub.split(",")[-1].strip() if sub else "",
        "name": f"{s['label']}, {sub}" if sub else s["label"],
        "lat": s["lat"], "lon": s["lon"],
        "elev_ft": 0,
        "nest": f"{dom.get('d02_km', 3)} km (d02)",
        "tz": s.get("tz_abbr", "UTC"), "tzoff": s.get("tz_offset", 0),
    }

    manifest = {
        "site": site_block,
        "now": args.now,
        "families": FAMILIES,
        "family_labels": FAMILY_LABELS,
        "models": models,
        "groups": groups,
        "cycles": cycles,
    }
    Path(args.out).write_text(json.dumps(manifest))
    print(f"{args.out}: {len(models)} models, {len(cycles)} cycles "
          f"(newest {all_cycles[0]})")


if __name__ == "__main__":
    main()
