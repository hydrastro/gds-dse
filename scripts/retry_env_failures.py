#!/usr/bin/env python3
"""Find failed runs whose log shows an environment (infra) failure and set them up for a rerun.

    python3 scripts/retry_env_failures.py --label dse1-prince            # list only
    python3 scripts/retry_env_failures.py --label dse1-prince --apply    # set aside, then rerun the label

It classifies every failed attempt with the repository's *current* failure rules
(configs/study.json), also for campaigns frozen with older rules, and prints the old and the
current class side by side. With --apply, each environment failure's attempt.json, console.log
and runs/ tree move to jobs/<id>/retried/<n>/ (with a reason.json), so the next
`study.py run --label <label>` runs exactly that job again: same configuration, same seed.
Nothing is deleted, and design outcomes and tool crashes are never touched.
"""
import argparse
import csv
import json
import shutil
from datetime import datetime, timezone

from study import campaign, load_settings, log_text, match_failure, read_json, ROOT


def candidates(dest, rules):
    old = {}
    if (dest / "runs.csv").is_file():
        with (dest / "runs.csv").open() as stream:
            old = {r["run_id"]: r for r in csv.DictReader(stream)}
    out = []
    for job_dir in sorted((dest / "jobs").iterdir()):
        path = job_dir / "attempt.json"
        if not path.is_file():
            continue
        attempt = read_json(path)
        if attempt.get("status") == "completed" or attempt.get("returncode") in (0, None):
            continue
        found = match_failure(log_text(job_dir), rules)
        before = old.get(job_dir.name, {})
        out.append({"id": job_dir.name, "dir": job_dir, "status": attempt.get("status"),
                    "old": f'{before.get("failure_kind") or "-"}/{before.get("failure_class") or "-"}',
                    "kind": found[1] if found else "-", "class": found[0] if found else "-",
                    "evidence": found[2] if found else ""})
    return out


def set_aside(item):
    root = item["dir"] / "retried"
    number = 1 + max([int(p.name) for p in root.glob("[0-9]*") if p.name.isdigit()] or [0])
    target = root / str(number)
    target.mkdir(parents=True)
    for name in ("attempt.json", "console.log", "runs"):
        if (item["dir"] / name).exists():
            shutil.move(str(item["dir"] / name), str(target / name))
    (target / "reason.json").write_text(json.dumps(
        {"failure_kind": item["kind"], "failure_class": item["class"], "evidence": item["evidence"],
         "previous_classification": item["old"], "set_aside_utc": datetime.now(timezone.utc).isoformat()},
        indent=2) + "\n")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    parser.add_argument("--apply", action="store_true", help="Set environment failures aside for a rerun.")
    args = parser.parse_args()
    dest = campaign(args.label)
    rules = load_settings(ROOT).get("failure_classes", {}).get("log_rules", [])
    items = candidates(dest, rules)
    print(f"{len(items)} failed attempts in {args.label}")
    for item in items:
        print(f'  {item["id"]:52} old {item["old"]:22} now {item["kind"]}/{item["class"]:16} {item["evidence"]}')
    infra = [item for item in items if item["kind"] == "infra"]
    print(f"{len(infra)} environment (infra) failures by the current rules")
    if not args.apply or not infra:
        return
    for item in infra:
        print(f"  set aside -> {set_aside(item).relative_to(ROOT)}")
    print(f"Now rerun them: python3 scripts/study.py run --label {args.label} ... (finished jobs are skipped).")


if __name__ == "__main__":
    main()
