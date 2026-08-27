#!/usr/bin/env python3
"""Add an inner nest to a site's namelist.wps and namelist.input.

Computing i_parent_start / e_we by hand is the fiddly part of adding a nest:
WRF wants (e_we - 1) % parent_grid_ratio == 0, the child must sit wholly
inside the parent with a buffer, and every per-domain list in the namelist
has to grow by one entry in step. This does all of that and leaves the
result for you to eyeball -- geogrid.exe and real.exe are the real
validators.

The new nest is centred on its parent and covers --span-cells of it, so a
3 km d02 with --span-cells 60 --ratio 3 gives a 1 km d03 roughly 180 km
across.

Every other per-domain list (physics options, history_interval, ...) is
extended by repeating its last value, which is what you want for a nest
that should behave like its parent. Check anything where it isn't.

Usage: add_nest.py --wps sites/X/namelist.wps --input sites/X/namelist.input \
    --span-cells 60 [--ratio 3] [--dry-run]
"""
import argparse
import re
from pathlib import Path

# Geometry we compute rather than inherit.
COMPUTED = {"grid_id", "parent_id", "i_parent_start", "j_parent_start",
            "parent_grid_ratio", "parent_time_step_ratio", "e_we", "e_sn",
            "dx", "dy"}
# Lists that must not simply repeat the parent's value.
SKIP_REPEAT = COMPUTED | {"max_dom", "start_date", "end_date"}


def get_int(text, key):
    m = re.search(rf"^\s*{key}\s*=\s*(-?\d+)", text, flags=re.M)
    if not m:
        raise SystemExit(f"namelist: no '{key}' line")
    return int(m.group(1))


def get_list(text, key):
    """Values of a per-domain list, as written (strings, trailing comma gone)."""
    m = re.search(rf"^\s*{key}\s*=(.*)$", text, flags=re.M)
    if not m:
        return None
    return [v.strip() for v in m.group(1).split(",") if v.strip()]


def set_list(text, key, values, width=22):
    line = f" {key:<{width}} = " + " ".join(f"{v}," for v in values)
    text, n = re.subn(rf"^\s*{key}\s*=.*$", lambda _: line, text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"namelist: expected one '{key}' line, found {n}")
    return text


def extend(text, ndom, computed):
    """Grow every per-domain list from ndom to ndom+1 entries."""
    out, grown, odd = text, [], []
    for m in re.finditer(r"^\s*([a-z_0-9]+)\s*=(.*)$", text, flags=re.M):
        key = m.group(1)
        if key in SKIP_REPEAT:
            continue
        vals = get_list(text, key)
        if vals is None or len(vals) != ndom:
            if vals is not None and 1 < len(vals) != ndom:
                odd.append(f"{key} ({len(vals)} values, max_dom={ndom})")
            continue
        out = set_list(out, key, vals + [vals[-1]])
        grown.append(key)
    for key, vals in computed.items():
        out = set_list(out, key, vals)
    return out, grown, odd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wps", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--span-cells", type=int, required=True,
                    help="how many parent cells across the nest should cover")
    ap.add_argument("--ratio", type=int, default=3, help="parent_grid_ratio")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wps_p, inp_p = Path(args.wps), Path(args.input)
    wps, inp = wps_p.read_text(), inp_p.read_text()

    ndom = get_int(wps, "max_dom")
    if ndom != get_int(inp, "max_dom"):
        raise SystemExit("max_dom differs between namelist.wps and namelist.input")
    new = ndom + 1

    # Parent geometry comes from namelist.wps, the authority geogrid reads.
    p_we = int(get_list(wps, "e_we")[ndom - 1])
    p_sn = int(get_list(wps, "e_sn")[ndom - 1])
    span = args.span_cells
    # (e_we - 1) must be a whole number of parent cells.
    c_we = span * args.ratio + 1
    c_sn = span * args.ratio + 1
    # Centre on the parent, leaving an equal margin each side.
    i0 = (p_we - span) // 2
    j0 = (p_sn - span) // 2
    if i0 < 5 or j0 < 5 or i0 + span > p_we - 5 or j0 + span > p_sn - 5:
        raise SystemExit(
            f"nest of {span} parent cells does not fit inside d{ndom:02d} "
            f"({p_we}x{p_sn}) with a 5-cell buffer -- reduce --span-cells")

    def grow(vals, extra):
        return vals + [str(extra)]

    wps_comp = {
        "parent_id": grow(get_list(wps, "parent_id"), ndom),
        "parent_grid_ratio": grow(get_list(wps, "parent_grid_ratio"), args.ratio),
        "i_parent_start": grow(get_list(wps, "i_parent_start"), i0),
        "j_parent_start": grow(get_list(wps, "j_parent_start"), j0),
        "e_we": grow(get_list(wps, "e_we"), c_we),
        "e_sn": grow(get_list(wps, "e_sn"), c_sn),
    }
    inp_comp = {
        "grid_id": grow(get_list(inp, "grid_id"), new),
        "parent_id": grow(get_list(inp, "parent_id"), ndom),
        "parent_grid_ratio": grow(get_list(inp, "parent_grid_ratio"), args.ratio),
        "parent_time_step_ratio": grow(get_list(inp, "parent_time_step_ratio"), args.ratio),
        "i_parent_start": grow(get_list(inp, "i_parent_start"), i0),
        "j_parent_start": grow(get_list(inp, "j_parent_start"), j0),
        "e_we": grow(get_list(inp, "e_we"), c_we),
        "e_sn": grow(get_list(inp, "e_sn"), c_sn),
    }
    for k in ("dx", "dy"):
        vals = get_list(inp, k)
        if vals and len(vals) == ndom:
            inp_comp[k] = grow(vals, int(round(int(float(vals[-1])) / args.ratio)))

    wps_out, wps_grown, wps_odd = extend(wps, ndom, wps_comp)
    inp_out, inp_grown, inp_odd = extend(inp, ndom, inp_comp)
    wps_out = set_list(wps_out, "max_dom", [new], width=16)
    inp_out = set_list(inp_out, "max_dom", [new])

    dx_note = ""
    if "dx" in inp_comp:
        dx_note = f", dx {inp_comp['dx'][-2]} -> {inp_comp['dx'][-1]} m"
    print(f"d{new:02d}: {c_we}x{c_sn} points, ratio {args.ratio}, "
          f"i_parent_start={i0} j_parent_start={j0}{dx_note}")
    print(f"  wps   extended {len(wps_grown)} per-domain lists")
    print(f"  input extended {len(inp_grown)} per-domain lists")
    for label, odd in (("wps", wps_odd), ("input", inp_odd)):
        for o in odd:
            print(f"  WARNING {label}: {o} -- left alone, check it by hand")

    if args.dry_run:
        print("(dry run, nothing written)")
        return
    wps_p.write_text(wps_out)
    inp_p.write_text(inp_out)
    print(f"wrote {wps_p} and {inp_p}")


if __name__ == "__main__":
    main()
