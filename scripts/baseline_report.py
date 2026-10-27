#!/usr/bin/env python3
"""Read existing baseline evidence without running EDA or changing campaign files.

    python3 scripts/baseline_report.py --out results/baseline-review.csv

Includes C0 and candidates with utilization 40 or 50, density 60 and AREA 0
synthesis (for example clock trials). Other settings and provenance may differ: this is an
inventory for review, not a pooled dataset or an automatic clock calibration.
Campaigns with an active runner are skipped. Routing-only diagnostics are not
full-flow successes. No existing runs.csv files are read or overwritten.
"""
import argparse
import fcntl
from pathlib import Path

import study
from project import config_layers, load_settings, load_variables, merge_layers

ROOT = Path(__file__).resolve().parents[1]
DESIGNS = ("aes", "sha256", "chacha", "prince")
PDKS = ("sky130A", "gf180mcuD", "ihp-sg13g2")


def inspect_job(dest, manifest, job, settings, platforms):
    directory = dest / "jobs" / job["id"]
    run = directory / "runs/run"
    config, config_source = study.read_run_config(directory)
    snapshot = study.frozen_or_current(dest, settings["flow"]["variables_snapshot"]) or {}
    defaults = {k: v.get("default") for k, v in snapshot.get("variables", {}).items()}
    value = lambda k: config.get(k, defaults.get(k))
    baseline_values = (value("FP_CORE_UTIL"), value("PL_TARGET_DENSITY_PCT"), value("SYNTH_STRATEGY"))
    if job["configuration"] != "C0" and not (
            baseline_values[0] in (40, 50) and baseline_values[1:] == (60, "AREA 0")):
        return None
    attempt_path = directory / "attempt.json"
    attempt = study.read_json(attempt_path) if attempt_path.is_file() else {}
    states = [p / "state_out.json" for p in study.step_dirs(run)
              if "stapostpnr" in p.name.lower() and (p / "state_out.json").is_file()]
    metrics = study.read_json(states[-1]).get("metrics", {}) if states else {}
    final_path = run / "final/metrics.json"
    final = study.read_json(final_path) if final_path.is_file() else {}
    corners = platforms.get(job["pdk"], {}).get("timing_corners", {})
    capacity = job.get("grt_capacity_perturbation_pct", 0)
    marker = (f"STUDY_SEED_APPLIED={job['seed']} STAGE=initial_global_routing "
              f"CAPACITY_PERTURBATION_PCT={capacity}")
    seed_applied = any(marker in p.read_text(errors="replace")
                       for p in run.glob("*-study-seededglobalrouting/*.log"))
    ctx = {"attempt": attempt, "job": job, "post": metrics, "final": final,
           "post_found": bool(states), "resolved": config, "corners": corners,
           "seed_applied": seed_applied, "gds_paths": list((run / "final").rglob("*.gds"))}
    checks = {name: check(ctx) for name, check in study.CHECKS.items()}
    period = study.number(config.get("CLOCK_PERIOD"))
    analyzed_periods = set()
    if states:
        for clock in states[-1].parent.glob("*/clock.rpt"):
            for line in clock.read_text(errors="replace").splitlines():
                if line.startswith("Period:"):
                    measured = study.number(line.split(":", 1)[1].strip())
                    if measured is not None:
                        analyzed_periods.add(measured)
    setup = study.corner_metric(metrics, "timing__setup__ws", min, corners.get("setup"))
    hold = study.corner_metric(metrics, "timing__hold__ws", min, corners.get("hold"))
    finished = attempt.get("status") in ("completed", "flow_error", "timeout", "launcher_error")
    required = settings["checks"]["required"]
    return {"campaign": dest.name, **{k: job.get(k) for k in
            ("design", "pdk", "configuration", "seed", "repeat", "threads", "stop_after")},
            "run_id": job["id"], "status": attempt.get("status", "not_run"),
            "finished_utc": attempt.get("finished_utc"), "returncode": attempt.get("returncode"),
            "clock_period_ns": period,
            "analyzed_clock_periods_ns": ";".join(str(p) for p in sorted(analyzed_periods)),
            "clock_period_matches_sta": analyzed_periods == {period} if analyzed_periods else None,
            "core_util_pct": baseline_values[0],
            "placement_density_pct": baseline_values[1], "synthesis_strategy": baseline_values[2],
            "setup_ws_ns": setup, "hold_ws_ns": hold,
            "setup_slack_fraction": setup / period if setup is not None and period and period > 0 else None,
            "setup_corners": ";".join(corners.get("setup", [])),
            "hold_corners": ";".join(corners.get("hold", [])),
            "study_checks_pass": finished and all(checks[n] is True for n in required),
            "failed_or_missing_checks": ";".join(n for n in required if checks[n] is not True),
            **{"check__" + k: v for k, v in checks.items()},
            "config_source": str(config_source) if config_source else None,
            "timing_source": str(states[-1]) if states else None,
            "flow_commit": manifest.get("flow_commit"), "parallel_jobs": attempt.get("parallel_jobs"),
            "console_log": str(directory / "console.log")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("results/baseline-review.csv"))
    args = parser.parse_args()
    study.ROOT = ROOT
    settings = load_settings(ROOT)
    variables, aliases = load_variables(ROOT, settings)
    defaults = study.read_json(ROOT / "configs/defaults.json")
    platforms = study.read_json(ROOT / "configs/platforms.json")
    c0 = study.read_json(ROOT / "configs/variants/C0.json")
    print("Current C0 inputs (configured targets, not proof of validation):")
    for design in DESIGNS:
        spec = study.read_json(ROOT / f"configs/designs/{design}/design.json")
        for pdk in PDKS:
            layers, path = config_layers(ROOT, defaults, platforms, design, spec, pdk, "C0", c0)
            merged, _ = merge_layers(layers, pdk, variables, aliases)
            pair = study.read_json(path) if path.is_file() else {}
            status = pair.get("//clock_status", "explicit" if "CLOCK_PERIOD" in pair else "fallback")
            values = [merged.get(k, variables[k].get("default")) for k in
                      ("CLOCK_PERIOD", "FP_CORE_UTIL", "PL_TARGET_DENSITY_PCT", "SYNTH_STRATEGY")]
            print(f"  {design}/{pdk}: T={values[0]} ns, util={values[1]}%, "
                  f"density={values[2]}%, synth={values[3]}, clock_status={status}")
    rows = []
    for manifest_path in sorted((ROOT / "results").glob("*/manifest.json")):
        dest = manifest_path.parent
        lock_path = dest / "runner.lock"
        handle = lock_path.open("r") if lock_path.is_file() else None
        try:
            if handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    print(f"Skipped active campaign: {dest.name}")
                    continue
            manifest = study.read_json(manifest_path)
            frozen = study.settings_for(dest)
            frozen_platforms = study.frozen_or_current(dest, "configs/platforms.json") or {}
            for job in manifest["jobs"]:
                if job["design"] in DESIGNS and job["pdk"] in PDKS:
                    row = inspect_job(dest, manifest, job, frozen, frozen_platforms)
                    if row is not None:
                        rows.append(row)
        finally:
            if handle:
                handle.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    study.write_csv(args.out, rows, list(rows[0]) if rows else ["campaign", "design", "pdk", "status"])
    print(f"Saved {len(rows)} individual job records to {args.out}.")
    print("Rows are not pooled; check configuration and provenance before reusing evidence.")


if __name__ == "__main__":
    main()
