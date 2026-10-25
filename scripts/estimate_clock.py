#!/usr/bin/env python3
"""Step 1 of clock calibration: a provisional clock per design x PDK from one smoke build.

    python3 scripts/estimate_clock.py --label recipe-smoke --label smoke-rest            # report
    python3 scripts/estimate_clock.py --label recipe-smoke --label smoke-rest --write    # write

For every design x PDK with post-route STA, the estimate is

    achievable period ~ clock period - worst setup slack     (at the PDK's matched setup corner)

e.g. 25 ns with -5.7 ns slack -> ~30.7 ns. It is written to configs/designs/<d>/pdks/<pdk>.json as
CLOCK_PERIOD with "//clock_status": "provisional". Study campaigns refuse provisional clocks.

Step 2 sweeps relative to that estimate for every design at once
(experiments/suite/calibrate-relative.json: x0.8 ... x1.7, 3 seeds, stopped after post-route STA),
and step 3, scripts/calibrate_clock.py --write, replaces the estimate with the calibrated reference
(fastest period that every seed meets, divided by 0.85).
"""
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def estimates(rows):
    """{(design, pdk): (estimate_ns, evidence)} from runs with post-route STA; first seed/repeat wins."""
    out = {}
    for row in sorted(rows, key=lambda r: (r["design"], r["pdk"], int(r["seed"]), int(r["repeat"]))):
        key = (row["design"], row["pdk"])
        period, ws = num(row.get("clock_period_ns")), num(row.get("setup_ws_ns"))
        if key in out or period is None or ws is None or row.get("post_route_sta_available") != "True":
            continue
        out[key] = (math.ceil((period - ws) * 10) / 10,
                    {"clock_period_ns": period, "worst_setup_slack_ns": ws, "run_id": row["run_id"],
                     "layout_valid": row.get("layout_valid"), "failure_class": row.get("failure_class") or None})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", action="append", required=True, help="Smoke campaign(s) with full or STA-stopped flows.")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--force", action="store_true", help="Also overwrite clocks that are already calibrated.")
    args = parser.parse_args()
    rows, seen = [], set()
    for label in args.label:
        path = ROOT / "results" / label / "runs.csv"
        if not path.is_file():
            raise SystemExit(f"{path} not found: run `python3 scripts/study.py collect --label {label}` first.")
        with path.open() as stream:
            for row in csv.DictReader(stream):
                row["_label"] = label
                rows.append(row)
                seen.add((row["design"], row["pdk"]))
    found = estimates(rows)
    for key in sorted(seen - set(found)):
        print(f"{key[0]}/{key[1]}: no post-route STA in these campaigns; the build did not get that far. "
              "Look at its failure_class and console.log before calibrating.")
    for (design, pdk), (estimate, evidence) in sorted(found.items()):
        print(f"{design}/{pdk}: clock {evidence['clock_period_ns']} ns, slack {evidence['worst_setup_slack_ns']:+.2f} ns "
              f"-> provisional {estimate} ns")
        if not args.write:
            continue
        path = ROOT / "configs/designs" / design / "pdks" / f"{pdk}.json"
        data = json.loads(path.read_text()) if path.is_file() else {}
        if data.get("//clock_status") == "calibrated" and not args.force:
            print(f"  kept the calibrated clock in {path.relative_to(ROOT)} (use --force to replace it)")
            continue
        data["CLOCK_PERIOD"] = estimate
        data["//clock_status"] = "provisional"
        data["//provisional_estimate"] = {"rule": "clock period - worst setup slack at the matched setup corner",
                                          "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          **evidence}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"  wrote {path.relative_to(ROOT)}")
    if args.write:
        print("Next: commit these files, then run experiments/suite/calibrate-relative.json (see docs/recipe.md).")


if __name__ == "__main__":
    main()
