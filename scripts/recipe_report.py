#!/usr/bin/env python3
"""Compare recipe campaigns (same jobs, different threads/parallelism/runtime) and pick a recipe.

    python3 scripts/recipe_report.py --prefix recipe-20261024-          # all campaigns with that prefix
    python3 scripts/recipe_report.py --label recipe-a-t1 --label recipe-a-t4 --out report.md

Reads results/<label>/runs.csv (run `study.py collect` first; `run` does it automatically).
For every campaign it reports:

  reliability   runs that finished the whole flow; failures split by cause (design / tool / infra)
  determinism   same seed run twice in the same campaign: identical results? identical failures?
  agreement     same seed across campaigns (e.g. 1 vs 4 threads): identical results?
  cost          wall time, CPU time, CPU efficiency, peak memory per flow, makespan, throughput

A good recipe has no infra or tool failures, is deterministic, and has the highest throughput.
Timing failures at the placeholder clock are design outcomes, not recipe problems.
"""
import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Metrics that must match exactly for two runs to count as "identical".
COMPARE = ["instance_area_um2", "instance_count", "wirelength_um", "vias", "setup_ws_ns", "hold_ws_ns",
           "route_drc_errors", "antenna_violating_nets"]


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load(label):
    path = ROOT / "results" / label / "runs.csv"
    if not path.is_file():
        raise SystemExit(f"{path} not found: run `python3 scripts/study.py collect --label {label}`.")
    with path.open() as stream:
        rows = [r for r in csv.DictReader(stream) if r["status"] not in ("not_run",)]
    manifest = json.loads((ROOT / "results" / label / "manifest.json").read_text())
    return rows, manifest


def outcome(row):
    """A short, comparable description of how a run ended."""
    if row.get("layout_valid") == "True":
        return "layout_valid"
    return f"fail:{row.get('failure_class') or row.get('status')}"


def signature(row):
    return tuple(row.get(m, "") for m in COMPARE)


def fmt(x, digits=1):
    return "-" if x is None else (f"{x:.{digits}f}" if isinstance(x, float) else str(x))


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def campaign_summary(label, rows, manifest):
    threads = sorted({r.get("threads") for r in rows if r.get("threads")})
    parallel = sorted({r.get("parallel_jobs") for r in rows if r.get("parallel_jobs")})
    runtime = sorted({r.get("tool_runtime") for r in rows if r.get("tool_runtime")})
    kinds = Counter(r.get("failure_kind") or ("ok" if r.get("study_checks_pass") == "True" else "?")
                    for r in rows if r["status"] != "running")
    classes = Counter(r.get("failure_class") for r in rows if r.get("failure_class"))
    starts = [datetime.fromisoformat(r["started_utc"]) for r in rows if r.get("started_utc")]
    ends = [datetime.fromisoformat(r["finished_utc"]) for r in rows if r.get("finished_utc")]
    makespan_h = (max(ends) - min(starts)).total_seconds() / 3600 if starts and ends else None
    wall = [num(r.get("wall_time_s")) for r in rows]
    cpu = [num(r.get("cpu_time_s")) for r in rows]
    eff = [c / w for c, w in zip(cpu, wall) if c is not None and w]
    rss = [num(r.get("peak_rss_mb")) for r in rows]
    finished = [r for r in rows if r["status"] not in ("running", "interrupted")]

    # Determinism: repeats of the same seed inside this campaign.
    by_seed = defaultdict(dict)
    for r in rows:
        by_seed[(r["design"], r["pdk"], r["configuration"], r["seed"])][r["repeat"]] = r
    same_outcome = same_result = compared = 0
    differences = []
    for key, reps in sorted(by_seed.items()):
        if len(reps) < 2:
            continue
        a, b = reps[sorted(reps)[0]], reps[sorted(reps)[1]]
        compared += 1
        if outcome(a) == outcome(b):
            same_outcome += 1
            if outcome(a) != "layout_valid" or signature(a) == signature(b):
                same_result += 1
                continue
        differences.append(f"{'/'.join(key[:2])} seed {key[3]}: {outcome(a)} {signature(a)[:3]} vs "
                           f"{outcome(b)} {signature(b)[:3]}")
    return {"label": label, "threads": ",".join(threads), "parallel": ",".join(parallel) or "-",
            "runtime": ",".join(runtime) or "-", "jobs": len(manifest["jobs"]), "finished": len(finished),
            "layout_valid": sum(r.get("layout_valid") == "True" for r in rows),
            "design_fail": kinds.get("design", 0), "tool_fail": kinds.get("tool", 0),
            "infra_fail": kinds.get("infra", 0), "unknown_fail": kinds.get("unknown", 0) + kinds.get("?", 0),
            "classes": dict(classes), "makespan_h": makespan_h,
            "throughput_per_h": len(finished) / makespan_h if makespan_h else None,
            "wall_median_min": median(wall) / 60 if median(wall) else None,
            "wall_max_min": max(w for w in wall if w is not None) / 60 if any(w is not None for w in wall) else None,
            "cpu_eff": median(eff), "rss_median_gb": median(rss) / 1024 if median(rss) else None,
            "rss_max_gb": max(r for r in rss if r is not None) / 1024 if any(r is not None for r in rss) else None,
            "repeat_pairs": compared, "repeat_same_outcome": same_outcome, "repeat_identical": same_result,
            "repeat_differences": differences}


def cross_agreement(data):
    """Same design/PDK/seed (first repeat) across campaigns: identical outcome and metrics?"""
    out = []
    labels = list(data)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            ra = {(r["design"], r["pdk"], r["configuration"], r["seed"]): r for r in data[a][0] if r["repeat"] == "1"}
            rb = {(r["design"], r["pdk"], r["configuration"], r["seed"]): r for r in data[b][0] if r["repeat"] == "1"}
            common = sorted(set(ra) & set(rb))
            same = sum(outcome(ra[k]) == outcome(rb[k]) and
                       (outcome(ra[k]) != "layout_valid" or signature(ra[k]) == signature(rb[k])) for k in common)
            out.append((a, b, len(common), same))
    return out


def per_cell_failures(data):
    """Which design x PDK cells fail, in which campaigns: a failure in every campaign is not the recipe's fault."""
    cells = defaultdict(lambda: defaultdict(Counter))
    for label, (rows, _) in data.items():
        for r in rows:
            if r.get("layout_valid") != "True" and r["status"] not in ("running",):
                cells[(r["design"], r["pdk"])][label][r.get("failure_class") or r["status"]] += 1
    return cells


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--prefix", help="Every campaign under results/ whose label starts with this.")
    parser.add_argument("--out", help="Also write the report to this Markdown file.")
    args = parser.parse_args()
    labels = list(args.label)
    if args.prefix:
        labels += sorted(p.name for p in (ROOT / "results").glob(args.prefix + "*")
                         if (p / "manifest.json").is_file() and p.name not in labels)
    if not labels:
        raise SystemExit("No campaigns selected (use --label or --prefix).")
    data = {label: load(label) for label in labels}
    summaries = [campaign_summary(label, *data[label]) for label in labels]

    lines = ["# Recipe comparison", "",
             "Same jobs in every campaign. A good recipe: no tool/infra failures, deterministic repeats, "
             "highest throughput. Design failures (e.g. timing at the placeholder clock) are not recipe problems.", "",
             "## Reliability", "",
             "| campaign | threads | parallel | runtime | jobs | finished | layout valid | design fail | tool fail | infra fail | unknown |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in summaries:
        lines.append(f"| {s['label']} | {s['threads']} | {s['parallel']} | {s['runtime']} | {s['jobs']} | {s['finished']} | "
                     f"{s['layout_valid']} | {s['design_fail']} | {s['tool_fail']} | {s['infra_fail']} | {s['unknown_fail']} |")
    lines += ["", "Failure classes: " + "; ".join(f"{s['label']}: {s['classes'] or 'none'}" for s in summaries), "",
              "## Determinism (same seed run twice within a campaign)", "",
              "| campaign | pairs | same outcome | identical results |", "| --- | --- | --- | --- |"]
    for s in summaries:
        lines.append(f"| {s['label']} | {s['repeat_pairs']} | {s['repeat_same_outcome']} | {s['repeat_identical']} |")
    for s in summaries:
        for d in s["repeat_differences"][:10]:
            lines.append(f"- {s['label']}: {d}")
    lines += ["", "## Agreement across campaigns (same seed, first repeat)", "",
              "| campaign A | campaign B | common runs | identical |", "| --- | --- | --- | --- |"]
    for a, b, n, same in cross_agreement(data):
        lines.append(f"| {a} | {b} | {n} | {same} |")
    lines += ["", "## Cost", "",
              "| campaign | makespan (h) | runs/hour | wall median (min) | wall max (min) | CPU efficiency | peak RSS median (GB) | peak RSS max (GB) |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in summaries:
        lines.append(f"| {s['label']} | {fmt(s['makespan_h'], 2)} | {fmt(s['throughput_per_h'])} | {fmt(s['wall_median_min'])} | "
                     f"{fmt(s['wall_max_min'])} | {fmt(s['cpu_eff'], 2)} | {fmt(s['rss_median_gb'], 2)} | {fmt(s['rss_max_gb'], 2)} |")
    lines += ["", "CPU efficiency = CPU time / wall time per flow (≈ cores actually used). "
              "Peak RSS = largest single process of the flow.", "",
              "## Where failures happen", "", "| design / PDK | " + " | ".join(labels) + " |",
              "| --- |" + " --- |" * len(labels)]
    for (design, pdk), by_label in sorted(per_cell_failures(data).items()):
        cells = [", ".join(f"{k}×{v}" for k, v in by_label[label].items()) or "-" for label in labels]
        lines.append(f"| {design} / {pdk} | " + " | ".join(cells) + " |")
    lines += ["", "A cell failing the same way in every campaign is a property of the design/PDK/clock, not of the recipe."]

    good = [s for s in summaries if s["finished"] and s["layout_valid"] > 0 and s["tool_fail"] == 0 and s["infra_fail"] == 0
            and s["unknown_fail"] == 0 and s["repeat_pairs"] and s["repeat_identical"] == s["repeat_pairs"]]
    lines += ["", "## Verdict", ""]
    if good:
        best = max(good, key=lambda s: s["throughput_per_h"] or 0)
        lines.append(f"Recipe candidates (no tool/infra failures, fully deterministic): "
                     f"{', '.join(s['label'] for s in good)}. Highest throughput: **{best['label']}** "
                     f"(threads {best['threads']}, parallel {best['parallel']}, runtime {best['runtime']}).")
    else:
        lines.append("No campaign is free of tool/infra failures and fully deterministic yet. "
                     "Read 'Where failures happen' and the repeat differences above before choosing.")
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text)


if __name__ == "__main__":
    main()
