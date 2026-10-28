"""Failure classification, layout validity, deck-aware DRC, infra retry and analysis exclusion.

Log excerpts are taken from real AES runs (sky130A/gf180mcuD). Artificial fixtures only:
these check bookkeeping, never EDA results.
"""
import contextlib
import csv
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
import analyze  # noqa: E402
import study  # noqa: E402

CONGESTION = """[INFO GRT-0018] Total wirelength: 3715139 um
[09:45:24] ERROR    [GRT-0116] Global routing finished with      openroad.py:312
                    congestion. Check the congestion regions in
Error: grt_seeded.tcl, 23 GRT-0116
proot warning: ptrace(PEEKDATA): Bad address
"""
HEAP = """[INFO GRT-0015] Inserted 1 diodes.
[09:45:13] ERROR    [GRT-0183] Net net3336: heap underflow       openroad.py:312
                    during 3D maze routing.
"""
LEF = """[17:36:28] WARNING  [Odb.CheckDesignAntennaProperties] [ODB-0240]    flow.py:701
                    error: Cannot open LEF file
                    /pdks/sky130A/libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef
"""
SETUP = """[13:04:10] ERROR    The following error was encountered while    __main__.py:189
                    One or more deferred errors were encountered:
                    Setup violations found in the following corners:
                    * nom_ss_125C_4v50
"""


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / "configs", self.root / "configs")
        for module in (study, analyze):
            patcher = mock.patch.object(module, "ROOT", self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dest = self.root / "results/check"
        self.dest.mkdir(parents=True)
        self.manifest = {"phase": "pilot", "jobs": [], "label": "check", "campaign_id": "c", "order_seed": 1}

    def job(self, seed, *, pdk="sky130A", exit_code=0, log="", complete=True, setup_slack=0.2,
            route_drc=0, klayout=True, runset=True):
        job = {"id": f"aes__{pdk}__C0__s{seed:05d}__r1", "design": "aes", "pdk": pdk, "configuration": "C0",
               "seed": seed, "repeat": 1, "stop_after": None, "threads": 1}
        self.manifest["jobs"].append(job)
        directory = self.dest / "jobs" / job["id"]
        run = directory / "runs/run"
        run.mkdir(parents=True)
        corners = json.loads((self.root / "configs/platforms.json").read_text())[pdk]["timing_corners"]
        resolved = {"CLOCK_PERIOD": 25, "RUN_MAGIC_DRC": True, "RUN_KLAYOUT_DRC": True,
                    "KLAYOUT_DRC_RUNSET": "/pdk/drc.lydrc" if runset else None}
        study.save_json(directory / "config.json", resolved)
        study.save_json(run / "config.json", resolved)
        study.save_json(directory / "attempt.json", {"status": "completed" if exit_code == 0 else "flow_error",
                                                     "returncode": exit_code, "wall_time_s": 1,
                                                     "machine_id": "m"})
        (directory / "console.log").write_text(log)
        step = run / "39-study-seededglobalrouting"
        step.mkdir()
        (step / "x.log").write_text(f"STUDY_SEED_APPLIED={seed} STAGE=initial_global_routing CAPACITY_PERTURBATION_PCT=0\n")
        study.save_json(step / "state_out.json", {"metrics": {}})
        if not complete:
            (run / "44-openroad-repairantennas").mkdir()  # step that crashed: no state_out.json
            return job
        setup, hold = corners["setup"][0], corners["hold"][0]
        metrics = {"design__instance__area": 1000 + seed,
                   f"timing__setup__ws__corner:{setup}": setup_slack,
                   f"timing__setup_vio__count__corner:{setup}": 0 if setup_slack >= 0 else 3,
                   f"timing__hold__ws__corner:{hold}": 0.1, f"timing__hold_vio__count__corner:{hold}": 0}
        sta = run / "58-openroad-stapostpnr"
        sta.mkdir()
        study.save_json(sta / "state_out.json", {"metrics": metrics})
        (run / "final/gds").mkdir(parents=True)
        (run / "final/gds/x.gds").write_text("fixture")
        final = dict(metrics, magic__drc_error__count=0, design__lvs_error__count=0, route__drc_errors=route_drc)
        if klayout:
            final["klayout__drc_error__count"] = 0
        study.save_json(run / "final/metrics.json", final)
        return job

    def collect(self):
        study.save_json(self.dest / "manifest.json", self.manifest)
        with contextlib.redirect_stdout(io.StringIO()):
            study.collect(SimpleNamespace(label="check"))
        with (self.dest / "runs.csv").open() as stream:
            return {int(r["seed"]): r for r in csv.DictReader(stream)}

    def test_classes_from_real_log_excerpts(self):
        self.job(1, exit_code=2, log=CONGESTION, complete=False)
        self.job(2, exit_code=2, log=HEAP, complete=False)
        self.job(3, exit_code=2, log=LEF, complete=False)
        self.job(4, exit_code=2, log=SETUP, setup_slack=-0.4)
        self.job(5)
        rows = self.collect()
        got = {s: (r["failure_class"], r["failure_kind"]) for s, r in rows.items()}
        self.assertEqual(got[1], ("congestion", "design"))  # the design error wins over the proot warning
        self.assertEqual(got[2], ("tool_crash", "tool"))
        self.assertEqual(got[3], ("infra", "infra"))
        self.assertEqual(got[4], ("timing", "design"))
        self.assertEqual(got[5], ("", ""))
        self.assertEqual(rows[2]["failed_step"], "44-openroad-repairantennas")

    def test_openroad_signal_11_is_a_tool_failure(self):
        self.job(1, exit_code=1, log='Signal 11 received\nStack trace:\n', complete=False)
        row = self.collect()[1]
        self.assertEqual((row['failure_class'], row['failure_kind']), ('tool_crash', 'tool'))

    def test_timing_failure_keeps_a_valid_layout_but_routing_drc_does_not(self):
        self.job(1, exit_code=2, log=SETUP, setup_slack=-0.4)
        self.job(2, exit_code=2, log="", route_drc=5)
        rows = self.collect()
        self.assertEqual(rows[1]["layout_valid"], "True")
        self.assertEqual(rows[1]["study_checks_pass"], "False")
        self.assertEqual(rows[2]["layout_valid"], "False")
        self.assertEqual(rows[2]["failure_class"], "route_drc")

    def test_klayout_drc_is_only_required_when_a_rule_deck_exists(self):
        self.job(1, pdk="gf180mcuD", klayout=False, runset=False)   # gf180: LibreLane skips KLayout DRC
        self.job(2, pdk="sky130A", klayout=False, runset=True)      # deck exists but no result: unknown
        rows = self.collect()
        self.assertEqual(rows[1]["layout_drc_pass"], "True")
        self.assertEqual(rows[1]["study_checks_pass"], "True")
        self.assertEqual(rows[2]["layout_drc_pass"], "")
        self.assertEqual(rows[2]["study_checks_pass"], "False")

    def test_shipped_gf180_platform_disables_klayout_drc(self):
        platforms = json.loads((SOURCE / "configs/platforms.json").read_text())
        self.assertIs(platforms["gf180mcuD"]["overrides"]["RUN_KLAYOUT_DRC"], False)

    def test_analysis_uses_layout_valid_and_excludes_infra(self):
        for seed in range(1, 6):
            self.job(seed)
        self.job(6, exit_code=2, log=SETUP, setup_slack=-0.4)       # counts for area, fails timing
        self.job(7, exit_code=2, log=LEF, complete=False)             # infra: excluded entirely
        self.collect()
        argv = ["analyze.py", "--fast", "--label", "check"]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            analyze.main()
        out = self.dest / "analysis"
        with (out / "cells.csv").open() as stream:
            area = next(r for r in csv.DictReader(stream) if r["metric"] == "instance_area_um2")
        self.assertEqual(area["n"], "6")          # 5 passing + the timing failure; the infra run is gone
        self.assertEqual(area["attempted"], "6")
        with (out / "failures.csv").open() as stream:
            classes = {r["failure_class"]: r for r in csv.DictReader(stream)}
        self.assertEqual(classes["timing"]["runs"], "1")
        self.assertEqual(classes["infra"]["excluded_from_analysis"], "True")
        report = (out / "report.md").read_text()
        self.assertIn("## Outcomes and failure causes", report)
        self.assertIn("infra (infra): 1", report)


class RetryInfraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / "configs", self.root / "configs")
        settings = json.loads((self.root / "configs/study.json").read_text())
        settings["execution"]["parallel_jobs"] = 1
        (self.root / "configs/study.json").write_text(json.dumps(settings))
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(study, "ROOT", self.root))
        stack.enter_context(mock.patch.object(study, "verify_flow"))
        stack.enter_context(mock.patch.object(study, "capture", return_value={"returncode": 0, "stdout": "x"}))
        stack.enter_context(mock.patch.object(study.shutil, "which", return_value=None))
        (self.root / "scripts").mkdir()
        (self.root / "scripts/flow_entry.py").write_text("raise SystemExit(0)\n")
        self.dest = self.root / "results/r"
        jobs = []
        for seed, log in [(1, LEF), (2, CONGESTION), (3, HEAP)]:
            run_id = f"aes__sky130A__C0__s{seed:05d}__r1"
            directory = self.dest / "jobs" / run_id
            directory.mkdir(parents=True)
            study.save_json(directory / "config.json", {"STUDY_GRT_SEED": seed})
            study.save_json(directory / "attempt.json", {"status": "flow_error", "returncode": 2})
            (directory / "console.log").write_text(log)
            jobs.append({"id": run_id, "design": "aes", "pdk": "sky130A", "configuration": "C0", "seed": seed,
                         "repeat": 1, "threads": 1, "stop_after": None, "manual_pdk_root": None,
                         "config_sha256": study.sha(directory / "config.json")})
        study.save_json(self.dest / "schedule.json", {"schema": 1, "campaign_id": "c", "order_seed": 1,
                                                      "job_ids": [j["id"] for j in jobs]})
        study.save_json(self.dest / "manifest.json", {
            "schema": 4, "campaign_id": "c", "label": "r", "phase": "pilot", "order_seed": 1, "jobs": jobs,
            "schedule_sha256": study.sha(self.dest / "schedule.json"), "input_hashes": {}, "repository": {}})

    def status(self, seed):
        return study.read_json(self.dest / "jobs" / f"aes__sky130A__C0__s{seed:05d}__r1" / "attempt.json")

    def test_only_infra_failures_are_rerun_and_only_when_asked(self):
        with contextlib.redirect_stdout(io.StringIO()):
            study.execute(SimpleNamespace(label="r", timeout=None, machine_id="m"))
        self.assertEqual(self.status(1)["status"], "flow_error")   # not re-run without --retry-infra
        with contextlib.redirect_stdout(io.StringIO()):
            study.execute(SimpleNamespace(label="r", timeout=None, machine_id="m", retry_infra=True))
        self.assertEqual(self.status(1)["status"], "completed")    # infra: re-run, old files archived
        self.assertEqual(len(self.status(1)["previous_attempts"]), 1)
        self.assertEqual(self.status(2)["status"], "flow_error")   # congestion: a result, never re-run
        self.assertEqual(self.status(3)["status"], "flow_error")   # tool crash: a result, never re-run


if __name__ == "__main__":
    unittest.main()
