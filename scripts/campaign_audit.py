#!/usr/bin/env python3
"""Read campaign status and optionally package evidence; never collect, retry, or delete.

Run this file from outside the study checkout so it does not dirty a frozen revision:
  python3 ~/campaign_audit.py --repo ~/gds-dse dse1-prince dse2-prince dse3-prince --bundle
  python3 ~/campaign_audit.py --repo ~/gds-dse dse3-prince

Requires only Python's standard library. The ZIP contains records, configs, analysis
tables and log tails, not GDS/ODB/PDKs/RTL. Existing bundle files are never replaced.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

TERMINAL = {"completed", "flow_error", "timeout", "launcher_error"}
COMPONENT = re.compile(r"[A-Za-z0-9_-]+\Z")
EFFECTIVE_KEYS = (
    "PDK", "STD_CELL_LIBRARY", "CLOCK_PERIOD", "FP_SIZING", "FP_CORE_UTIL",
    "PL_TARGET_DENSITY_PCT", "SYNTH_STRATEGY", "SYNTH_CORNER", "DEFAULT_CORNER",
    "SYNTH_HIERARCHY_MODE", "CTS_SINK_CLUSTERING_ENABLE", "CTS_SINK_CLUSTERING_SIZE",
    "GRT_ADJUSTMENT", "DRT_THREADS", "STA_THREADS", "IO_DELAY_CONSTRAINT",
    "SETUP_VIOLATION_CORNERS", "HOLD_VIOLATION_CORNERS",
)


def read_json(path):
    return json.loads(path.read_text())


def stamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.is_file() else None


def git(root, *args):
    try:
        result = subprocess.run(["git", "--no-optional-locks", *args], cwd=root,
                                capture_output=True, text=True, timeout=30)
        return {"returncode": result.returncode, "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}


def inspect(root, label):
    dest = root / "results" / label
    manifest = read_json(dest / "manifest.json")
    jobs = manifest["jobs"]
    csv_path = dest / "runs.csv"
    with csv_path.open(newline="") if csv_path.is_file() else io.StringIO("") as stream:
        rows = list(csv.DictReader(stream))
    row_ids = Counter(row.get("run_id") for row in rows)
    by_id = {row.get("run_id"): row for row in rows}
    statuses, histories = Counter(), Counter()
    disagreements, missing_configs, missing_gds, latest = [], [], [], []
    files, effective, issues = set(), {}, []
    for job in jobs:
        ident = job["id"]
        if not COMPONENT.fullmatch(ident):
            raise ValueError(f"Unsafe job identifier in manifest: {ident!r}")
        directory = dest / "jobs" / ident
        path = directory / "attempt.json"
        attempt = read_json(path) if path.is_file() else {}
        status = attempt.get("status", "not_run")
        statuses[status] += 1
        row = by_id.get(ident)
        if row is None or row.get("status") != status:
            disagreements.append({"id": ident, "latest": status,
                                  "csv": row.get("status") if row else "missing row"})
        elif row.get("returncode", "") != ("" if attempt.get("returncode") is None else str(attempt["returncode"])):
            disagreements.append({"id": ident, "reason": "returncode differs"})
        if path.is_file():
            latest.append(path.stat().st_mtime)
        config = directory / "config.json"
        if not config.is_file():
            missing_configs.append(ident)
        elif job.get("config_sha256") and hashlib.sha256(config.read_bytes()).hexdigest() != job["config_sha256"]:
            issues.append(f"Prepared config checksum differs: {ident}")
        if row and row.get("gds_available") == "True" and not any((directory / "runs/run/final").rglob("*.gds")):
            missing_gds.append(ident)
        for name in ("attempt.json", "config.json", "config_origins.json"):
            files.add(directory / name)
        # Capture defaults resolved by LibreLane without copying large library/path lists.
        key = f"{job['design']}/{job['pdk']}/{job['configuration']}"
        if key not in effective:
            for name in ("runs/run/resolved.json", "runs/run/config.json"):
                resolved = directory / name
                if resolved.is_file():
                    value = read_json(resolved)
                    effective[key] = {"source": str(resolved.relative_to(root)),
                                      "values": {k: value.get(k) for k in EFFECTIVE_KEYS}}
                    break
        for history in ("retried", "aborted"):
            for previous in sorted((directory / history).glob("[0-9]*")):
                if previous.is_dir():
                    histories[history] += 1
                    files.update(previous / name for name in ("attempt.json", "reason.json"))
    planned_ids = {job["id"] for job in jobs}
    failures = Counter(f"{r.get('failure_kind') or '?'}/{r.get('failure_class') or r.get('failure_reason') or '?'}"
                       for r in rows if r.get("status") in TERMINAL and r.get("study_checks_pass") != "True")
    for name in ("manifest.json", "schedule.json", "runs.csv", "summary.csv", "runs.csv.before-slim",
                 "summary.csv.before-slim", "slim-collect-diff.txt"):
        files.add(dest / name)
    for name in ("analysis", "inputs/configs", "inputs/experiments"):
        files.update(p for p in (dest / name).rglob("*") if p.suffix in {".json", ".csv", ".md"})
    report = dest / "analysis/report.md"
    info = {
        "label": label, "campaign_id": manifest.get("campaign_id"), "planned": len(jobs),
        "prepared_head": manifest.get("repository", {}).get("head", {}).get("stdout"),
        "prepared_submodules": manifest.get("repository", {}).get("submodules", {}).get("stdout"),
        "experiment": manifest.get("experiment"), "latest_statuses": dict(statuses),
        "terminal": sum(statuses[s] for s in TERMINAL), "csv_rows": len(rows),
        "csv_passes": sum(r.get("study_checks_pass") == "True" for r in rows),
        "csv_failures": dict(failures), "csv_status_disagreements": disagreements,
        "csv_duplicate_ids": [k for k, v in row_ids.items() if v > 1],
        "csv_unexpected_ids": sorted(str(k) for k in set(by_id) - planned_ids),
        "csv_campaign_id_mismatches": [r.get("run_id") for r in rows if r.get("campaign_id")
                                       and r["campaign_id"] != manifest.get("campaign_id")],
        "saved_attempt_directories": dict(histories), "missing_job_configs": missing_configs,
        "csv_claims_gds_but_file_missing": missing_gds, "issues": issues,
        "csv_modified_utc": stamp(csv_path), "report_modified_utc": stamp(report),
        "csv_older_than_latest_attempt": bool(csv_path.is_file() and latest and csv_path.stat().st_mtime < max(latest)),
        "report_older_than_csv": bool(csv_path.is_file() and report.is_file() and report.stat().st_mtime < csv_path.stat().st_mtime),
        "report_heading": report.read_text(errors="replace").splitlines()[:1] if report.is_file() else [],
        "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest() if report.is_file() else None,
        "resolved_settings": effective,
    }
    return info, files


def log_info(path, limit=65536):
    counts = Counter()
    with path.open(errors="replace") as stream:
        for line in stream:
            match = re.match(r"^(completed|flow_error|timeout|interrupted|launcher_error):\s", line)
            if match:
                counts[match[1]] += 1
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        tail = stream.read(limit)
    return dict(counts), tail


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("labels", nargs="+", help="Campaign labels; e.g. dse1-prince dse2-prince dse3-prince")
    parser.add_argument("--repo", type=Path, default=Path.home() / "gds-dse")
    parser.add_argument("--bundle", nargs="?", const="auto", help="Also save an evidence ZIP (default: home directory).")
    args = parser.parse_args()
    if any(not COMPONENT.fullmatch(label) for label in args.labels) or len(set(args.labels)) != len(args.labels):
        parser.error("Use distinct campaign labels containing letters, numbers, underscores or hyphens.")
    root = args.repo.expanduser().resolve()
    snapshot = {"captured_utc": datetime.now(timezone.utc).isoformat(), "repo": str(root),
                "head": git(root, "rev-parse", "HEAD"), "branch": git(root, "branch", "--show-current"),
                "status": git(root, "status", "--porcelain"), "submodules": git(root, "submodule", "status"),
                "campaigns": []}
    head = snapshot["head"].get("stdout", "unknown")
    print(f"Current HEAD: {head}")
    files, tails, active = set(), {}, []
    with ExitStack() as stack:
        for label in args.labels:
            lock = root / "results" / label / "runner.lock"
            if lock.is_file():
                handle = stack.enter_context(lock.open("r"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    active.append(label)
        if active and args.bundle:
            parser.error("Bundle after these runners finish: " + ", ".join(active))
        for label in args.labels:
            info, selected = inspect(root, label)
            files.update(selected)
            info["runner_active"] = label in active
            print(f"\n{label}: {info['terminal']}/{info['planned']} terminal; latest={info['latest_statuses']}")
            print(f"  prepared HEAD: {info['prepared_head']} (matches current: {info['prepared_head'] == head})")
            print(f"  saved CSV: {info['csv_rows']} rows, {info['csv_passes']} pass; failures={info['csv_failures']}")
            print(f"  CSV/attempt differences: {len(info['csv_status_disagreements'])}; "
                  f"CSV older than latest attempt: {info['csv_older_than_latest_attempt']}; "
                  f"report older than CSV: {info['report_older_than_csv']}")
            print(f"  claimed GDS now missing: {len(info['csv_claims_gds_but_file_missing'])}; "
                  f"missing job configs: {len(info['missing_job_configs'])}; histories={info['saved_attempt_directories']}")
            info["runner_logs"] = {}
            if args.bundle:
                paths = set((root / "results/logs").glob(label + "*.log")) | set((root / "results").glob(label + "*.log"))
                for path in sorted(paths):
                    if path.is_file() and not path.is_symlink():
                        counts, tail = log_info(path)
                        name = str(path.relative_to(root))
                        info["runner_logs"][name] = {"outcome_lines": counts, "modified_utc": stamp(path)}
                        tails[name + ".tail.txt"] = tail
                dest = root / "results" / label
                for job in read_json(dest / "manifest.json")["jobs"]:
                    directory = dest / "jobs" / job["id"]
                    # Keep console tails for all jobs: a zero exit need not mean all study checks passed.
                    paths = [directory / "console.log"]
                    for history in ("retried", "aborted"):
                        paths.extend((directory / history).glob("[0-9]*/console.log"))
                    for path in paths:
                        if path.is_file() and not path.is_symlink():
                            with path.open("rb") as stream:
                                stream.seek(max(0, path.stat().st_size - 32768))
                                tails[str(path.relative_to(root)) + ".tail.txt"] = stream.read(32768)
            snapshot["campaigns"].append(info)
        if not args.bundle:
            if active:
                print("Live snapshot; active runners: " + ", ".join(active))
            return
        out = (Path.home() / ("gds-dse-audit-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".zip")
               if args.bundle == "auto" else Path(args.bundle).expanduser()).resolve()
        if out == root or root in out.parents:
            parser.error("Save the bundle outside the repository, e.g. ~/gds-dse-audit.zip.")
        with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("audit.json", json.dumps(snapshot, indent=2) + "\n")
            for path in sorted(files):
                if path.is_file() and path.resolve().is_relative_to(root) and not path.is_symlink():
                    archive.write(path, str(path.relative_to(root)))
            for name, content in sorted(tails.items()):
                archive.writestr(name, content)
        print(f"\nSaved {out} ({out.stat().st_size / 1048576:.1f} MiB). Campaign files were not changed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
