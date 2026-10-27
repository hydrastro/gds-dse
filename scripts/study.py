#!/usr/bin/env python3
"""Prepare immutable run descriptions; execute sequentially; collect all attempts.

No third-party Python packages are required. Actual EDA runs require the pinned
LibreLane Nix shell. Input RTL remains in the user's own repository. Runner,
check, metric and summary settings live in configs/study.json and are frozen
into each campaign at prepare.
"""
import argparse
import concurrent.futures
import csv
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from project import load_settings, prepare_campaign, strip_comments, verify_repository

ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "tools/librelane"


def read_json(path):
    return json.loads(Path(path).read_text())


def read_run_config(job_dir):
    """Prefer LibreLane's effective configuration over the submitted overrides."""
    job_dir = Path(job_dir)
    for path in (job_dir / "runs/run/resolved.json",
                 job_dir / "runs/run/config.json", job_dir / "config.json"):
        if path.is_file():
            return read_json(path), path
    return {}, None


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def capture(argv, cwd=None):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=30)
        return {"returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}


def safe_label(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Use letters, digits, underscores and hyphens in labels.")
    return value


def campaign(label):
    return ROOT / "results" / safe_label(label)


def resolve(path, parent=None):
    p = Path(path).expanduser()
    return ((parent or ROOT) / p).resolve() if not p.is_absolute() else p.resolve()


def settings_for(dest=None):
    """Frozen campaign settings when available, otherwise the repository's current ones."""
    frozen = dest / "inputs/configs/study.json" if dest is not None else None
    if frozen is not None and frozen.is_file():
        return strip_comments(read_json(frozen))
    return load_settings(ROOT)


def frozen_or_current(dest, relative):
    frozen = dest / "inputs" / relative
    path = frozen if frozen.is_file() else ROOT / relative
    return read_json(path) if path.is_file() else None


def verify_flow():
    commit = load_settings(ROOT)["flow"]["librelane_commit"]
    actual = capture(["git", "rev-parse", "HEAD"], FLOW_ROOT)
    if actual.get("stdout") != commit:
        raise ValueError(f"Expected LibreLane commit {commit}. Clone/check out it under tools/librelane first.")
    dirty = capture(["git", "diff", "--name-only", "HEAD"], FLOW_ROOT)
    if dirty.get("returncode") != 0 or dirty.get("stdout"):
        raise ValueError("The pinned flow has tracked modifications. Preserve/review them separately; this starter expects the reviewed checkout.")
    return commit


def verify_inputs(manifest):
    for name, digest in manifest["input_hashes"].items():
        if not Path(name).is_file() or sha(name) != digest:
            raise ValueError(f"Frozen input changed or disappeared: {name}. Make a new campaign after intentional edits.")


def prepare(args):
    commit = verify_flow()
    selections = {key: getattr(args, key) for key in ('designs', 'pdks', 'configs', 'seeds', 'threads', 'repeat')
                  if getattr(args, key, None) is not None}
    print(prepare_campaign(ROOT, FLOW_ROOT, commit, resolve(args.experiment), args.label, selections))


def read_schedule(dest, manifest):
    """Read the frozen order, never reconstruct historical scheduling by guessing."""
    if "schedule_sha256" not in manifest:
        if manifest.get("schema", 0) >= 3:
            raise ValueError("Prepared campaign is missing its schedule checksum.")
        return None  # Older campaigns can still be collected, with unknown positions.
    path = dest / "schedule.json"
    if not path.is_file() or sha(path) != manifest["schedule_sha256"]:
        raise ValueError("Prepared schedule changed or disappeared. Restore it or prepare a new campaign.")
    schedule = read_json(path)
    ids = schedule["job_ids"]
    expected = [job["id"] for job in manifest["jobs"]]
    if (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
            or len(ids) != len(expected) or len(set(ids)) != len(ids)
            or set(ids) != set(expected)
            or schedule.get("order_seed") != manifest["order_seed"]
            or schedule.get("campaign_id") != manifest.get("campaign_id")):
        raise ValueError("Prepared schedule does not match the campaign jobs, identity or order_seed.")
    return ids


def cpu_limit_snapshot(root=Path('/sys/fs/cgroup'), membership=Path('/proc/self/cgroup')):
    """Observe cgroup-v2 CPU ceilings, including visible ancestors; never change them."""
    try:
        member = next(line[3:] for line in membership.read_text().splitlines() if line.startswith('0::'))
        root = root.resolve()
        current = (root / member.lstrip('/')).resolve()
        if not current.is_relative_to(root) or not current.is_dir():
            current = root  # cgroup namespace exposes its root rather than the host path
        quotas = []
        cpuset = (current / 'cpuset.cpus.effective').read_text().strip() if (current / 'cpuset.cpus.effective').is_file() else None
        while True:
            path = current / 'cpu.max'
            if path.is_file():
                quota, period = path.read_text().split()
                if quota != 'max':
                    quotas.append(int(quota) / int(period))
            if current == root:
                break
            current = current.parent
        return {'controller': 'cgroup_v2', 'quota_cpu_equivalents': min(quotas) if quotas else None,
                'effective_cpuset': cpuset, 'scope': 'visible hierarchy; parent quota may be shared'}
    except (OSError, StopIteration, ValueError, ZeroDivisionError):
        return {'controller': 'unavailable', 'quota_cpu_equivalents': None}


def machine_snapshot(dest, machine_id=None):
    """Record host resources; physical RAM is not a cluster/container allocation."""
    hostname = platform.node()
    cpu_model = platform.processor() or None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() in {"model name", "Hardware"}:
                cpu_model = value.strip()
                break
    except OSError:
        pass
    try:
        host_ram_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if host_ram_bytes <= 0:
            host_ram_bytes = None
    except (AttributeError, OSError, ValueError):
        host_ram_bytes = None
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    try:
        os_name = platform.freedesktop_os_release().get("PRETTY_NAME", platform.platform())
    except (AttributeError, OSError):
        os_name = platform.platform()
    return {"machine_id": safe_label(machine_id) if machine_id else hostname or "unknown-host",
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "hostname": hostname, "cpu_model": cpu_model,
            "architecture": platform.machine(), "logical_cpus": os.cpu_count(),
            "cpu_affinity": affinity,
            "cpu_limits": cpu_limit_snapshot(),
            "process_nice": os.getpriority(os.PRIO_PROCESS, 0) if hasattr(os, 'getpriority') else None,
            "available_logical_cpus": len(affinity) if affinity is not None else None,
            "host_ram_bytes": host_ram_bytes, "os": os_name,
            "platform": platform.platform(),
            "disk_free_bytes_at_invocation": shutil.disk_usage(dest).free,
            "resource_scope": "Host-visible resources; not guaranteed scheduler/container limits.",
            "memory_available_bytes_at_invocation": meminfo("MemAvailable"),
            "load_average_at_invocation": os.getloadavg() if hasattr(os, "getloadavg") else None,
            # How the tools were provided: real Nix, or nix-portable with the proot/bwrap runtime.
            "tool_runtime": {key: os.environ.get(key) for key in
                             ("IN_NIX_SHELL", "NP_RUNTIME", "NP_LOCATION", "GDS_DSE_RUNTIME")}}


def meminfo(field):
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


INCOMPLETE = {"running", "interrupted"}  # never finished: not an outcome of the configuration


def acquire_lock(dest):
    """One runner per campaign. The OS releases the lock if the runner or host dies."""
    handle = (dest / "runner.lock").open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError(f"Another runner is already executing campaign {dest.name}. "
                         "Wait for it to finish or stop it before running again.")
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} host={platform.node()} since={datetime.now(timezone.utc).isoformat()}\n")
    handle.flush()
    return handle


def archive_incomplete(job_dir):
    """Move an unfinished attempt (crash, power loss, Ctrl-C) aside so the job can run again.

    Nothing is deleted: attempt.json, console.log and the partial runs/ tree go to
    jobs/<id>/aborted/<n>/, and the new attempt lists them in previous_attempts.
    """
    archive_root = job_dir / "aborted"
    number = 1 + max([int(p.name) for p in archive_root.glob("[0-9]*") if p.name.isdigit()] or [0])
    target = archive_root / str(number)
    target.mkdir(parents=True)
    for name in ["attempt.json", "console.log", "runs"]:
        if (job_dir / name).exists():
            shutil.move(str(job_dir / name), str(target / name))
    return str(target)


def execute_jobs(args):
    verify_flow()
    lock = acquire_lock(campaign(args.label))
    try:
        _execute_jobs(args)
    finally:
        lock.close()  # closing the file releases the lock


def _execute_jobs(args):
    dest = campaign(args.label)
    manifest = read_json(dest / "manifest.json")
    settings = settings_for(dest)
    execution = settings["execution"]
    timeout = execution.get("timeout_s", 0) if args.timeout is None else args.timeout
    if timeout < 0:
        raise ValueError("Timeout must be nonnegative.")
    verify_inputs(manifest)
    verify_repository(ROOT, manifest["repository"], manifest["phase"] == "study")
    ordered_ids = read_schedule(dest, manifest)
    if ordered_ids is None:
        raise ValueError("This runner requires a newly prepared campaign with a saved schedule. Old results can still be collected.")
    execution_id = str(uuid.uuid4())
    machine = machine_snapshot(dest, getattr(args, "machine_id", None))
    commands = {"librelane": [sys.executable, str(ROOT / "scripts/flow_entry.py"), "--version"]}
    commands.update(execution.get("tool_versions", {}))
    versions = {name: capture(argv) for name, argv in commands.items()}
    versions.update({"python": sys.version, "platform": platform.platform(),
                     "execution_id": execution_id, "campaign_id": manifest["campaign_id"],
                     "campaign_label": args.label, "machine": machine})
    versions["executable_provenance"] = {}
    for name in execution.get("hash_executables", []):
        executable = shutil.which(name)
        if executable:
            path = str(Path(executable).resolve())
            versions["executable_provenance"][name] = {"path": path, "sha256": sha(path)}
    environment_file = f"environment-{execution_id}.json"
    save_json(dest / environment_file, versions)
    if versions["librelane"].get("returncode") != 0:
        raise ValueError("LibreLane Python environment is unavailable. Enter the pinned Nix shell before running.")
    jobs = {job["id"]: job for job in manifest["jobs"]}
    grace = execution.get("kill_grace_s", 10)
    parallel = execution.get("parallel_jobs", 1) if getattr(args, "parallel_jobs", None) is None else args.parallel_jobs
    if type(parallel) is not int or parallel < 1:
        raise ValueError("execution.parallel_jobs must be a positive integer.")
    live = {}            # pid -> Popen, for Ctrl-C cleanup in parallel mode
    previous = {}        # job id -> archived unfinished attempts
    stopping = threading.Event()

    def kill(proc):
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()

    def prepare_job(schedule_index, job, execution_index):
        """Validate and write the 'running' record; return (record, cmd, job_dir)."""
        job_dir = dest / "jobs" / job["id"]
        verify_inputs(manifest)
        verify_repository(ROOT, manifest["repository"], manifest["phase"] == "study")
        if sha(job_dir / "config.json") != job["config_sha256"]:
            raise ValueError(f"Prepared configuration was edited: {job['id']}")
        if (job_dir / "runs/run").exists():
            raise ValueError(f"Untracked existing run in {job_dir}. Preserve it and use a new campaign label.")
        cmd = [sys.executable, str(ROOT / "scripts/flow_entry.py"),
               "--pdk", job["pdk"], "--jobs", str(job["threads"]),
               "--design-dir", str(job_dir), "--run-tag", "run", *execution.get("extra_flow_args", [])]
        if job["manual_pdk_root"]:
            cmd += ["--manual-pdk", "--pdk-root", job["manual_pdk_root"]]
        else:
            cmd += ["--pdk-root", str(resolve(execution.get("pdk_cache_dir", "pdks")))]
        if job["stop_after"]:
            cmd += ["--to", job["stop_after"]]
        cmd += [str(job_dir / "config.json")]
        record = {"schema": 2, "status": "running", "command": cmd,
                  "campaign_id": manifest["campaign_id"], "campaign_label": args.label,
                  "run_id": job["id"], "design": job["design"], "pdk": job["pdk"],
                  "configuration": job["configuration"], "seed": job["seed"],
                  "repeat": job["repeat"], "order_seed": manifest["order_seed"],
                  "schedule_index": schedule_index, "execution_index": execution_index,
                  "execution_id": execution_id, "environment_file": environment_file,
                  "machine_id": machine["machine_id"], "machine": machine,
                  "threads": job["threads"], "parallel_jobs": parallel,
                  "started_utc": datetime.now(timezone.utc).isoformat(),
                  "timeout_s": timeout, "returncode": None}
        archived = sorted(str(p) for p in (job_dir / "aborted").glob("*")) if (job_dir / "aborted").exists() else []
        if archived:
            record["previous_attempts"] = archived
        save_json(job_dir / "attempt.json", record)
        return record, cmd, job_dir

    def run_job(record, cmd, job_dir, job):
        started = time.monotonic()
        proc = None
        print(f"RUN {job['id']} — log: {job_dir / 'console.log'}", flush=True)
        try:
            with (job_dir / "console.log").open("w") as log:
                env = dict(os.environ)
                if execution.get("set_omp_num_threads", True):
                    env["OMP_NUM_THREADS"] = str(job["threads"])
                proc = subprocess.Popen(cmd, cwd=job_dir, stdout=log, stderr=subprocess.STDOUT,
                                        env=env, start_new_session=True)
                live[proc.pid] = proc
                record["returncode"], usage = wait_with_usage(proc, timeout)
                if usage:
                    record.update(usage)
                if stopping.is_set():
                    record["status"] = "interrupted"
                else:
                    record["status"] = "completed" if proc.returncode == 0 else "flow_error"
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            kill(proc)
            record["status"] = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
            if isinstance(exc, KeyboardInterrupt):
                raise
        except OSError as exc:
            record.update(status="launcher_error", error=str(exc))
        finally:
            if proc is not None:
                live.pop(proc.pid, None)
            record["wall_time_s"] = time.monotonic() - started
            record["finished_utc"] = datetime.now(timezone.utc).isoformat()
            save_json(job_dir / "attempt.json", record)
        print(f"{record['status']}: {job['id']}", flush=True)

    pending = []
    retry = getattr(args, "retry_incomplete", True)
    retry_infra = getattr(args, "retry_infra", False)
    classes = settings.get("failure_classes", {})
    for schedule_index, job_id in enumerate(ordered_ids, 1):
        job_dir = dest / "jobs" / job_id
        if (job_dir / "attempt.json").exists():
            attempt = read_json(job_dir / "attempt.json")
            status = attempt.get("status")
            infra = None
            if retry_infra and status not in INCOMPLETE and status != "completed":
                rule = classes.get("status_rules", {}).get(status)
                if rule and rule["kind"] == "infra":
                    infra = f"status={status}"
                elif attempt.get("returncode") not in (0, None):
                    found = match_log_rule(log_text(job_dir), classes.get("log_rules", []))
                    infra = found[2] if found and found[1] == "infra" else None
            if not ((retry and status in INCOMPLETE) or infra):
                print(f"SKIP existing attempt ({status}): {job_id}")
                continue
            if infra:
                print(f"RETRY infrastructure failure ({infra}): {job_id}")
            # We hold the campaign lock, so a 'running' record is left over from a dead runner.
            archived = archive_incomplete(job_dir)
            previous[job_id] = previous.get(job_id, []) + [archived]
            print(f"RETRY unfinished attempt ({status}), previous files kept in {archived}: {job_id}")
        pending.append((schedule_index, jobs[job_id]))
    if parallel == 1:
        for execution_index, (schedule_index, job) in enumerate(pending, 1):
            run_job(*prepare_job(schedule_index, job, execution_index), job)
        return
    # Parallel mode: launch in saved schedule order; at most `parallel` flows at once.
    # Runtime from parallel campaigns is not comparable to sequential runtime (recorded per attempt).
    queue = iter(enumerate(pending, 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        active = set()
        try:
            while True:
                while len(active) < parallel and not stopping.is_set():
                    item = next(queue, None)
                    if item is None:
                        break
                    execution_index, (schedule_index, job) = item
                    prepared = prepare_job(schedule_index, job, execution_index)
                    active.add(pool.submit(run_job, *prepared, job))
                if not active:
                    break
                done, active = concurrent.futures.wait(active, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    future.result()
        except BaseException:
            stopping.set()
            for proc in list(live.values()):
                kill(proc)
            concurrent.futures.wait(active)
            raise


def wait_with_usage(proc, timeout):
    """Wait like Popen.wait(timeout) but also return the flow's resource usage via wait4:
    peak resident memory of the largest process in the flow and total CPU time of the flow."""
    if not isinstance(getattr(proc, "pid", None), int):
        return proc.wait(timeout=timeout or None), None  # test doubles
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        try:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        except ChildProcessError:
            return proc.wait(timeout=timeout or None), None
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            return proc.returncode, {"peak_rss_mb": round(usage.ru_maxrss / 1024, 1),
                                     "cpu_user_s": round(usage.ru_utime, 1), "cpu_system_s": round(usage.ru_stime, 1)}
        if deadline is not None and time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(0.5)


def refresh_tables(args):
    collect(args)
    summarize(args)


def execute(args):
    """Refresh tables on normal completion and ordinary errors/Ctrl-C."""
    try:
        execute_jobs(args)
    except BaseException:
        # Preserve the original exception even if partial output cannot be parsed.
        if (campaign(args.label) / "manifest.json").exists():
            try:
                refresh_tables(args)
            except Exception as exc:
                print(f"Could not refresh result tables: {exc}. Run collect/summarize after review.", file=sys.stderr)
        raise
    else:
        refresh_tables(args)


def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def metric(metrics, name):
    return number(metrics.get(name))


AGGREGATES = {"min": min, "max": max, "sum": sum}


def corner_metric(metrics, name, aggregate, corners=None):
    """Combine '<name>__corner:<c>' values. With `corners`, only those corners count and a
    missing corner gives None (never a silent fallback to other corners)."""
    if aggregate is None:
        return metric(metrics, name)
    prefix = name + "__corner:"
    if corners:
        values = [number(metrics.get(prefix + corner)) for corner in corners]
        return None if any(value is None for value in values) else aggregate(values)
    values = [number(v) for k, v in metrics.items()
              if k.startswith(prefix) and (not corners or k[len(prefix):] in corners)]
    values = [v for v in values if v is not None]
    if values:
        return aggregate(values)
    return None if corners else metric(metrics, name)


def zero(value):
    return None if value is None else value == 0


def _drc(ctx):
    """Layout DRC from every DRC tool that actually ran. KLayout DRC counts only when the resolved config has
    a rule deck: without KLAYOUT_DRC_RUNSET (e.g. gf180mcuD at the pinned PDK) LibreLane skips the step."""
    resolved = ctx["resolved"]
    tools = [("RUN_MAGIC_DRC", "magic__drc_error__count", True),
             ("RUN_KLAYOUT_DRC", "klayout__drc_error__count",
              "KLAYOUT_DRC_RUNSET" not in resolved or resolved.get("KLAYOUT_DRC_RUNSET") not in (None, "", []))]
    required = [metric(ctx["final"], key) for flag, key, available in tools if resolved.get(flag, True) and available]
    return None if not required or any(v is None for v in required) else all(v == 0 for v in required)


def _timing(ctx, kind):
    corners = ctx.get("corners", {}).get(kind)
    ws = corner_metric(ctx["post"], f"timing__{kind}__ws", min, corners)
    vios = corner_metric(ctx["post"], f"timing__{kind}_vio__count", sum, corners)
    return None if ws is None or vios is None else (ws >= 0 and vios == 0)


# Each check returns True (pass), False (fail) or None (unknown/missing evidence).
CHECKS = {
    "exit_ok": lambda c: None if not c["attempt"] else c["attempt"].get("returncode") == 0,
    "seed_applied": lambda c: c["seed_applied"],
    "full_flow": lambda c: not c["job"].get("stop_after"),
    "gds": lambda c: bool(c["gds_paths"]),
    "post_route_sta": lambda c: bool(c["post_found"]),
    "setup_timing": lambda c: _timing(c, "setup"),
    "hold_timing": lambda c: _timing(c, "hold"),
    "layout_drc": _drc,
    "lvs": lambda c: zero(metric(c["final"], "design__lvs_error__count")),
    "route_drc": lambda c: zero(metric(c["final"], "route__drc_errors")),
    "max_slew": lambda c: zero(corner_metric(c["post"], "design__max_slew_violation__count", sum)),
    "max_cap": lambda c: zero(corner_metric(c["post"], "design__max_cap_violation__count", sum)),
    "max_fanout": lambda c: zero(corner_metric(c["post"], "design__max_fanout_violation__count", sum)),
    "antenna": lambda c: zero(metric(c["final"], "antenna__violating__nets")),
    "power_grid": lambda c: zero(metric(c["final"], "design__power_grid_violation__count")),
}


DEFAULT_LAYOUT_VALID = ["seed_applied", "full_flow", "gds", "post_route_sta", "route_drc", "layout_drc", "lvs"]


def log_text(job_dir, limit=4_000_000):
    """The tail of a job's console log (bounded so huge logs stay cheap)."""
    path = Path(job_dir) / "console.log"
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - limit))
        return stream.read().decode(errors="replace")


def match_log_rule(text, rules, kinds=None):
    """First rule (in file order) whose pattern occurs in the log: (class, kind, matched pattern)."""
    for rule in rules:
        if kinds and rule["kind"] not in kinds:
            continue
        for pattern in rule["patterns"]:
            if re.search(pattern, text):
                return rule["class"], rule["kind"], pattern
    return None


def classify_failure(attempt, checks, layout_valid, required, text, settings):
    """Why a finished attempt did not pass: (failure_class, failure_kind, evidence).

    kind is 'design' (an outcome of the configuration: congestion, timing, DRC...),
    'tool' (the EDA tool crashed: a property of the flow) or 'infra' (the machine or
    environment failed: not an outcome; excluded from analysis, retryable with --retry-infra).
    """
    spec = settings.get("failure_classes", {})
    status = attempt.get("status")
    if status in spec.get("status_rules", {}):
        rule = spec["status_rules"][status]
        return rule["class"], rule["kind"], f"status={status}"
    if attempt.get("returncode") not in (0, None):
        found = match_log_rule(text, spec.get("log_rules", []))
        if found:
            return found[0], found[1], found[2]
    if layout_valid:
        timing = [name for name in ("setup_timing", "hold_timing") if checks.get(name) is False]
        if timing:
            return "timing", "design", "+".join(timing)
    # The exit code is a symptom; name the check that explains it when there is one.
    failing = [name for name in required if name != "exit_ok" and checks.get(name) is not True]
    if failing:
        name = failing[0]
        return name, "design" if checks.get(name) is False else "unknown", f"{name}={checks.get(name)}"
    if attempt.get("returncode") not in (0, None):
        return "nonzero_exit", "unknown", "non-zero exit with every check passing and no matching log rule"
    return "unknown", "unknown", "no failing check identified"


def write_csv(path, rows, columns=None):
    if columns is None:
        columns = list(dict.fromkeys(k for row in rows for k in row))
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def step_dirs(run_dir):
    dirs = [p for p in run_dir.glob("[0-9]*-*") if p.is_dir() and p.name.split("-", 1)[0].isdigit()]
    return sorted(dirs, key=lambda p: int(p.name.split("-", 1)[0]))


def knob_names(dest, manifest, settings):
    if settings.get("knob_columns", "catalog_and_variants") == "none":
        return []
    catalog = frozen_or_current(dest, settings["flow"].get("knob_catalog", "configs/knobs.json")) or {}
    names = set(catalog.get("knobs", {}))
    for layer in manifest.get("variant_layers", {}).values():
        names.update(k for k in layer if not k.startswith("//"))
    return sorted(names)


def scalar(value):
    return json.dumps(value) if isinstance(value, (list, dict)) else value


def collect(args):
    dest = campaign(args.label)
    manifest = read_json(dest / "manifest.json")
    settings = settings_for(dest)
    current = load_settings(ROOT)
    # Failure classification and the layout_valid definition are derived from logs and metrics, not flow
    # inputs: campaigns frozen before they existed use the repository's current rules.
    settings.setdefault("failure_classes", current.get("failure_classes", {}))
    settings["checks"].setdefault("layout_valid", current["checks"].get("layout_valid", DEFAULT_LAYOUT_VALID))
    order = read_schedule(dest, manifest)
    positions = {run_id: index for index, run_id in enumerate(order or [], 1)}
    snapshot = frozen_or_current(dest, settings["flow"]["variables_snapshot"]) or {"variables": {}}
    defaults = {k: v.get("default") for k, v in snapshot["variables"].items()}
    knobs = knob_names(dest, manifest, settings)
    platforms = frozen_or_current(dest, "configs/platforms.json") or {}
    required = settings["checks"]["required"]
    reported = settings["checks"].get("reported", [])
    unknown = [c for c in required + reported if c not in CHECKS]
    if unknown:
        raise ValueError(f"Unknown check name(s) in study.json: {unknown}. Available: {sorted(CHECKS)}")
    rows, long_rows = [], []
    context_columns = ["campaign_id", "campaign_label", "order_seed", "run_id", "design", "pdk",
                       "configuration", "seed", "repeat", "schedule_index", "execution_id",
                       "execution_index", "machine_id", "environment_file", "threads", "parallel_jobs",
                       "cpu_affinity", "cpu_limits", "process_nice",
                       "hostname", "cpu_model", "logical_cpus", "available_logical_cpus",
                       "host_ram_bytes", "os"]
    for job in manifest["jobs"]:
        job_dir = dest / "jobs" / job["id"]
        run_dir = job_dir / "runs/run"
        attempt = read_json(job_dir / "attempt.json") if (job_dir / "attempt.json").exists() else {}
        machine = attempt.get("machine", {})
        context = {"campaign_id": manifest.get("campaign_id"),
                   "campaign_label": manifest.get("label", args.label),
                   "order_seed": manifest.get("order_seed"),
                   "run_id": job["id"], "design": job["design"], "pdk": job["pdk"],
                   "configuration": job["configuration"], "seed": job["seed"], "repeat": job["repeat"],
                   "schedule_index": positions.get(job["id"]),
                   "execution_id": attempt.get("execution_id"),
                   "execution_index": attempt.get("execution_index"),
                   "machine_id": attempt.get("machine_id"),
                   "environment_file": attempt.get("environment_file"), "threads": job.get("threads"),
                   "parallel_jobs": attempt.get('parallel_jobs'),
                   "cpu_affinity": scalar(machine.get('cpu_affinity')),
                   "cpu_limits": scalar(machine.get('cpu_limits')),
                   "process_nice": machine.get('process_nice'),
                   **{key: machine.get(key) for key in ["hostname", "cpu_model", "logical_cpus",
                                                       "available_logical_cpus", "host_ram_bytes", "os"]}}
        steps = step_dirs(run_dir) if run_dir.exists() else []
        states = [p / "state_out.json" for p in steps if (p / "state_out.json").is_file()]
        post = [(p, read_json(p).get("metrics", {})) for p in states if "stapostpnr" in p.parent.name.lower()]
        post_metrics = post[-1][1] if post else {}
        final_metrics_file = run_dir / "final/metrics.json"
        final_metrics = read_json(final_metrics_file) if final_metrics_file.exists() else {}
        for path in states:
            for key, value in read_json(path).get("metrics", {}).items():
                long_rows.append({**context, "stage": path.parent.name,
                                  "source": str(path), "metric": key, "value": scalar(value)})
        for key, value in final_metrics.items():
            long_rows.append({**context, "stage": "final", "source": str(final_metrics_file),
                              "metric": key, "value": scalar(value)})
        job_config = read_json(job_dir / "config.json") if (job_dir / "config.json").exists() else {}
        resolved, resolved_path = read_run_config(job_dir)
        gds_paths = sorted(str(p) for p in (run_dir / "final").rglob("*.gds")) if (run_dir / "final").exists() else []
        capacity = job.get("grt_capacity_perturbation_pct", 0)
        marker = f"STUDY_SEED_APPLIED={job['seed']} STAGE=initial_global_routing CAPACITY_PERTURBATION_PCT={capacity}"
        seed_applied = any(marker in p.read_text(errors="replace") for p in run_dir.glob("*-study-seededglobalrouting/*.log"))
        ctx = {"attempt": attempt, "job": job, "post": post_metrics, "post_found": bool(post),
               "final": final_metrics, "resolved": resolved, "gds_paths": gds_paths,
               "seed_applied": seed_applied,
               "corners": (platforms.get(job["pdk"]) or {}).get("timing_corners", {})}
        checks = {name: CHECKS[name](ctx) for name in dict.fromkeys(required + reported)}
        eligible = bool(attempt) and all(checks[name] is True for name in required)
        setup_ok, hold_ok = _timing(ctx, "setup"), _timing(ctx, "hold")
        timing_pass = None if setup_ok is None or hold_ok is None else (setup_ok and hold_ok)
        failure_reason = None
        if attempt and not eligible:
            failure_reason = next(f"{name}={'unknown' if checks[name] is None else 'fail'}"
                                  for name in required if checks[name] is not True)
        failed_step = None
        if attempt and attempt.get("returncode") not in (0, None):
            failed_step = next((p.name for p in steps if not (p / "state_out.json").is_file()), None)
        layout_checks = settings["checks"].get("layout_valid", DEFAULT_LAYOUT_VALID)
        layout_valid = bool(attempt) and all(CHECKS[name](ctx) is True for name in layout_checks)
        failure_class = failure_kind = failure_evidence = None
        if attempt and not eligible:
            failure_class, failure_kind, failure_evidence = classify_failure(
                attempt, {**checks, **{n: CHECKS[n](ctx) for n in layout_checks}}, layout_valid,
                required, log_text(job_dir), settings)
        values = {}
        for spec in settings["metrics"]:
            source = post_metrics if spec["source"] == "post_route_sta" else final_metrics
            corner_set = ctx["corners"].get(spec["corner_set"]) if spec.get("corner_set") else None
            values[spec["column"]] = corner_metric(source, spec["key"], AGGREGATES.get(spec.get("corners", "none")),
                                                   corner_set)
        knob_values = {}
        for name in knobs:
            value = resolved.get(name, job_config.get(name, defaults.get(name)))
            knob_values[f"knob__{name}"] = scalar(value)
        row = {
            **context, "phase": manifest["phase"],
            "status": attempt.get("status", "not_run"), "returncode": attempt.get("returncode"),
            "previous_unfinished_attempts": len(attempt.get("previous_attempts", [])),
            "started_utc": attempt.get("started_utc"), "finished_utc": attempt.get("finished_utc"),
            "timeout_s": attempt.get("timeout_s"),
            "wall_time_s": attempt.get("wall_time_s"),
            "cpu_time_s": (attempt["cpu_user_s"] + attempt["cpu_system_s"]) if "cpu_user_s" in attempt else None,
            "peak_rss_mb": attempt.get("peak_rss_mb"),
            "tool_runtime": (machine.get("tool_runtime") or {}).get("NP_RUNTIME")
            or ("nix" if (machine.get("tool_runtime") or {}).get("IN_NIX_SHELL") else None),
            "clock_period_ns": resolved.get("CLOCK_PERIOD"),
            "synthesis_strategy": resolved.get("SYNTH_STRATEGY"),
            "resolved_config_source": str(resolved_path) if resolved_path else None,
            "density_pct": resolved.get("PL_TARGET_DENSITY_PCT"), "scl": resolved.get("STD_CELL_LIBRARY"),
            "resolved_pdk_root": resolved.get("PDK_ROOT"),
            "seed_applied": seed_applied, "post_route_sta_available": bool(post),
            "gds_available": bool(gds_paths),
            "timing_pass": timing_pass,
            "layout_drc_pass": _drc(ctx),
            **{f"check__{name}": value for name, value in checks.items()},
            "study_checks_pass": eligible, "layout_valid": layout_valid,
            "failure_reason": failure_reason, "failure_class": failure_class, "failure_kind": failure_kind,
            "failure_evidence": failure_evidence, "failed_step": failed_step,
            **values,
            **knob_values,
            "sdc_sha256": job.get("sdc_sha256"),
            "post_route_metrics_source": str(post[-1][0]) if post else None,
            "final_metrics_source": str(final_metrics_file) if final_metrics_file.exists() else None,
            "last_completed_step": states[-1].parent.name if states else None,
            "gds_paths": ";".join(gds_paths), "run_directory": str(run_dir)
        }
        rows.append(row)
    write_csv(dest / "runs.csv", rows)
    write_csv(dest / "metrics_long.csv", long_rows, context_columns + ["stage", "source", "metric", "value"])
    print(f"Collected {len(rows)} planned runs, including missing/failed attempts, into {dest / 'runs.csv'}")


def quantile(values, q):
    values = sorted(values)
    position = (len(values) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def wilson(successes, n):
    if n == 0:
        return None, None
    z = 1.959963984540054
    p = successes / n
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n))/denominator
    radius = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denominator
    # Exact endpoints avoid tiny negative error-bar lengths from rounding.
    return (0.0 if successes == 0 else max(0, center-radius),
            1.0 if successes == n else min(1, center+radius))


def summarize(args):
    dest = campaign(args.label)
    settings = settings_for(dest)
    spec = settings.get("summary", {})
    group_by = spec.get("group_by", ["design", "pdk", "configuration", "machine_id", "threads"])
    with (dest / "runs.csv").open() as f:
        rows = list(csv.DictReader(f))
    groups = {}
    for row in rows:
        # Identical-seed repeat checks are not independent seed samples.
        if row["repeat"] != "1" or row["status"] in {"not_run", "running"}:
            continue
        groups.setdefault(tuple(row.get(k, "") for k in group_by), []).append(row)
    summary = []
    for key, group in sorted(groups.items()):
        good = [r for r in group if r["study_checks_pass"] == "True"]
        lower, upper = wilson(len(good), len(group))
        reasons = {}
        for r in group:
            if r.get("failure_reason"):
                reasons[r["failure_reason"]] = reasons.get(r["failure_reason"], 0) + 1
        output = {"campaign_id": group[0].get("campaign_id"),
                  "campaign_label": group[0].get("campaign_label", args.label),
                  "order_seed": group[0].get("order_seed"),
                  **dict(zip(group_by, key)), "attempts": len(group),
                  "study_checks_pass_count": len(good), "study_pass_fraction": len(good)/len(group),
                  "study_pass_wilson95_low": lower, "study_pass_wilson95_high": upper,
                  "post_route_sta_count": sum(r["post_route_sta_available"] == "True" for r in group),
                  "failure_reasons": ";".join(f"{k}:{v}" for k, v in sorted(reasons.items()))}
        fields = [(f, "all_available", group) for f in spec.get("fields_all_attempts", [])]
        for f in spec.get("fields_passing_only", []):
            fields += [(f, "all_available", group), (f, "passing_only", good)]
        for field, population, records in fields:
            values = [number(r.get(field)) for r in records]
            values = [v for v in values if v is not None]
            prefix = f"{field}__{population}"
            output[prefix + "__n"] = len(values)
            if values:
                output[prefix + "__median"] = statistics.median(values)
                output[prefix + "__iqr"] = quantile(values, .75)-quantile(values, .25)
                output[prefix + "__min"] = min(values)
                output[prefix + "__max"] = max(values)
        summary.append(output)
    write_csv(dest / "summary.csv", summary, None if summary else [
        "campaign_id", "campaign_label", "order_seed", *group_by, "attempts",
        "study_checks_pass_count", "study_pass_fraction"])
    print(f"Saved {dest / 'summary.csv'}. Read the sample counts and missing outcomes alongside each median.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--label", required=True)
    p.add_argument("--experiment", required=True, help="Tracked experiment JSON file.")
    for name in ('designs', 'pdks', 'configs', 'seeds'):
        p.add_argument('--' + name, nargs='+', type=int if name == 'seeds' else str,
                       help='Override this selection; the effective experiment is frozen in manifest.json.')
    p.add_argument('--threads', type=int, help='Threads per flow for this campaign (overrides study.json execution.threads).')
    p.add_argument('--repeat', type=int, help='Runs per seed (same-seed repeats measure determinism; pilot phase only).')
    p.set_defaults(function=prepare)
    p = sub.add_parser("run")
    p.add_argument("--label", required=True)
    p.add_argument("--parallel-jobs", type=int, default=None,
                   help="Flows at once for this invocation (overrides study.json execution.parallel_jobs; recorded per attempt).")
    p.add_argument("--timeout", type=int, default=None,
                   help="Per-run wall-clock limit in seconds; 0 means no limit. Default: execution.timeout_s in study.json.")
    p.add_argument("--machine-id", type=safe_label,
                   help="Optional stable machine label, e.g. desktop-i7. Defaults to hostname; hardware is captured automatically.")
    p.add_argument("--no-retry-incomplete", dest="retry_incomplete", action="store_false",
                   help="Do not re-run jobs left 'running' (crash/power loss) or 'interrupted' (Ctrl-C). "
                        "By default they are archived under jobs/<id>/aborted/ and run again; finished "
                        "attempts (completed, flow_error, timeout, launcher_error) are never re-run.")
    p.add_argument("--retry-infra", action="store_true",
                   help="Also re-run finished attempts whose failure is classified as 'infra' (study.json "
                        "failure_classes: e.g. a LEF file that could not be opened, out of memory, launcher error). "
                        "Their files are archived like unfinished attempts. Design and tool failures are never re-run.")
    p.set_defaults(function=execute)
    for name, function in [("collect", collect), ("summarize", summarize)]:
        p = sub.add_parser(name)
        p.add_argument("--label", required=True)
        p.set_defaults(function=function)
    args = parser.parse_args()
    try:
        args.function(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
