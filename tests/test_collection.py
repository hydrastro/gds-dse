"""Artificial fixtures test data integrity, never substitute for EDA validation."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import study


class CollectionTests(unittest.TestCase):
    def test_wilson_endpoints_are_safe_for_all_pass_or_all_fail_plot(self):
        self.assertEqual(study.wilson(0, 3)[0], 0.0)
        self.assertEqual(study.wilson(10, 10)[1], 1.0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = study.ROOT
        study.ROOT = Path(self.temp.name)
        shutil.copytree(Path(__file__).resolve().parents[1] / "configs", study.ROOT / "configs")
        self.dest = study.ROOT / "results/check"
        self.dest.mkdir(parents=True)
        self.manifest = {"phase": "pilot", "jobs": []}
        self.args = SimpleNamespace(label="check")

    def tearDown(self):
        study.ROOT = self.previous_root
        self.temp.cleanup()

    def job(self, seed=1, repeat=1, *, post=True, exit_code=0, marker=True,
            missing_drc=False, attempted=True, negative_slack=False, design="ascon"):
        job = {"id": f"{design}__sky130A__C0__s{seed:05d}__r{repeat}", "design": design, "pdk": "sky130A",
               "configuration": "C0", "seed": seed, "repeat": repeat, "stop_after": None}
        self.manifest["jobs"].append(job)
        directory = self.dest / "jobs" / job["id"]
        run = directory / "runs/run"
        run.mkdir(parents=True)
        config = {"CLOCK_PERIOD": 50, "PL_TARGET_DENSITY_PCT": 60,
                  "STD_CELL_LIBRARY": "fixture", "RUN_MAGIC_DRC": True, "RUN_KLAYOUT_DRC": True}
        study.save_json(directory / "config.json", config)
        if not attempted:
            return
        study.save_json(directory / "attempt.json", {
            "status": "completed" if exit_code == 0 else "flow_error",
            "returncode": exit_code, "wall_time_s": 100})
        metrics = {"design__instance__area": 123,
                   "timing__setup__ws__corner:nom_ss_100C_1v60": -.05 if negative_slack else .2,
                   "timing__setup__ws__corner:nom_ff_n40C_1v95": .8,
                   "timing__hold__ws__corner:nom_ss_100C_1v60": .1,
                   "timing__hold__ws__corner:nom_ff_n40C_1v95": .05,
                   "timing__setup__tns__corner:nom_ss_100C_1v60": -.05 if negative_slack else 0,
                   "timing__hold__tns__corner:nom_ff_n40C_1v95": 0,
                   "timing__setup_vio__count__corner:nom_ss_100C_1v60": 1 if negative_slack else 0,
                   "timing__setup_vio__count__corner:nom_ff_n40C_1v95": 0,
                   "timing__hold_vio__count__corner:nom_ss_100C_1v60": 0,
                   "timing__hold_vio__count__corner:nom_ff_n40C_1v95": 0}
        state_dir = run / ("58-openroad-stapostpnr" if post else "12-openroad-staprepnr")
        state_dir.mkdir()
        study.save_json(state_dir / "state_out.json", {"metrics": metrics})
        step = run / "39-study-seededglobalrouting"
        step.mkdir()
        (step / "study-seededglobalrouting.log").write_text(
            f"STUDY_SEED_APPLIED={seed} STAGE=initial_global_routing CAPACITY_PERTURBATION_PCT=0\n" if marker else "")
        (run / "final/gds").mkdir(parents=True)
        (run / "final/gds/fixture.gds").write_text("artificial fixture, not a GDS")
        final = dict(metrics, magic__drc_error__count=0, klayout__drc_error__count=0,
                     design__lvs_error__count=0, route__drc_errors=0)
        if missing_drc:
            final.pop("klayout__drc_error__count")
        study.save_json(run / "final/metrics.json", final)

    def collect(self):
        study.save_json(self.dest / "manifest.json", self.manifest)
        with contextlib.redirect_stdout(io.StringIO()):
            study.collect(self.args)
        with (self.dest / "runs.csv").open() as stream:
            return list(csv.DictReader(stream))

    def test_complete_result_and_corner_selection(self):
        self.job()
        row = self.collect()[0]
        self.assertEqual(row["study_checks_pass"], "True")
        self.assertEqual(float(row["setup_ws_ns"]), .2)
        self.assertEqual(float(row["hold_ws_ns"]), .05)
        self.assertIn("stapostpnr", row["post_route_metrics_source"])

    def test_resolved_json_overrides_submitted_and_legacy_config(self):
        self.job()
        directory = self.dest / "jobs" / self.manifest["jobs"][0]["id"]
        config = study.read_json(directory / "config.json")
        study.save_json(directory / "runs/run/config.json", dict(config, CLOCK_PERIOD=30))
        resolved = directory / "runs/run/resolved.json"
        study.save_json(resolved, dict(config, CLOCK_PERIOD=75, SYNTH_STRATEGY="DELAY 1"))
        row = self.collect()[0]
        self.assertEqual(float(row["clock_period_ns"]), 75)
        self.assertEqual(row["synthesis_strategy"], "DELAY 1")
        self.assertEqual(row["resolved_config_source"], str(resolved))

    def test_legacy_run_config_remains_supported(self):
        self.job()
        directory = self.dest / "jobs" / self.manifest["jobs"][0]["id"]
        config = study.read_json(directory / "config.json")
        study.save_json(directory / "runs/run/config.json", dict(config, CLOCK_PERIOD=60))
        self.assertEqual(float(self.collect()[0]["clock_period_ns"]), 60)

    def test_baseline_report_keeps_timing_failure_and_rejects_partial_success(self):
        import baseline_report
        self.job(negative_slack=True, exit_code=2)
        job = self.manifest["jobs"][0]
        directory = self.dest / "jobs" / job["id"]
        config = study.read_json(directory / "config.json")
        study.save_json(directory / "runs/run/resolved.json",
                        dict(config, FP_CORE_UTIL=50, SYNTH_STRATEGY="AREA 0"))
        clock_report = directory / "runs/run/58-openroad-stapostpnr/nom_ss_100C_1v60/clock.rpt"
        clock_report.parent.mkdir()
        clock_report.write_text("Clock: clk\nPeriod: 50.000000\n")
        settings = study.load_settings(study.ROOT)
        platforms = study.read_json(study.ROOT / "configs/platforms.json")
        row = baseline_report.inspect_job(self.dest, self.manifest, job, settings, platforms)
        self.assertEqual(row["setup_ws_ns"], -.05)
        self.assertFalse(row["study_checks_pass"])
        self.assertIn("setup_timing", row["failed_or_missing_checks"])
        self.assertTrue(row["check__gds"])
        self.assertTrue(row["clock_period_matches_sta"])

        self.job(seed=2)
        partial = self.manifest["jobs"][-1]
        partial["stop_after"] = "OpenROAD.STAPostPNR"
        row = baseline_report.inspect_job(self.dest, self.manifest, partial, settings, platforms)
        self.assertFalse(row["study_checks_pass"])
        self.assertIn("full_flow", row["failed_or_missing_checks"])

    def test_pre_route_metrics_never_fill_final_timing(self):
        self.job(post=False)
        row = self.collect()[0]
        self.assertEqual(row["gds_available"], "True")
        self.assertEqual(row["setup_ws_ns"], "")
        self.assertEqual(row["post_route_sta_available"], "False")
        self.assertEqual(row["study_checks_pass"], "False")

    def test_gds_with_failed_exit_is_retained_and_does_not_pass(self):
        self.job(exit_code=2)
        row = self.collect()[0]
        self.assertEqual(row["status"], "flow_error")
        self.assertEqual(row["gds_available"], "True")
        self.assertEqual(row["study_checks_pass"], "False")

    def test_missing_required_drc_is_unknown_not_zero(self):
        self.job(missing_drc=True)
        row = self.collect()[0]
        self.assertEqual(row["layout_drc_pass"], "")
        self.assertEqual(row["study_checks_pass"], "False")

    def test_seed_field_without_execution_evidence_does_not_pass(self):
        self.job(marker=False)
        row = self.collect()[0]
        self.assertEqual(row["seed_applied"], "False")
        self.assertEqual(row["study_checks_pass"], "False")

    def test_negative_slack_does_not_pass_even_with_zero_exit(self):
        self.job(negative_slack=True)
        row = self.collect()[0]
        self.assertEqual(row["timing_pass"], "False")
        self.assertEqual(row["study_checks_pass"], "False")

    def test_summary_keeps_failures_but_does_not_count_repeat_as_new_seed(self):
        self.job(seed=1)
        self.job(seed=1, repeat=2)
        self.job(seed=2, exit_code=2)
        self.job(seed=3, attempted=False)
        self.assertEqual(len(self.collect()), 4)
        with contextlib.redirect_stdout(io.StringIO()):
            study.summarize(self.args)
        with (self.dest / "summary.csv").open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["attempts"], "2")
        self.assertEqual(row["study_checks_pass_count"], "1")
        self.assertEqual(float(row["study_pass_fraction"]), .5)
        self.assertLess(float(row["study_pass_wilson95_low"]), .5)
        self.assertGreater(float(row["study_pass_wilson95_high"]), .5)

    def test_summary_never_pools_different_designs(self):
        self.job(design="ascon")
        self.job(design="aes", exit_code=2)
        self.collect()
        with contextlib.redirect_stdout(io.StringIO()):
            study.summarize(self.args)
        with (self.dest / "summary.csv").open() as stream:
            rows = {row["design"]: row for row in csv.DictReader(stream)}
        self.assertEqual(set(rows), {"ascon", "aes"})
        self.assertEqual(float(rows["ascon"]["study_pass_fraction"]), 1)
        self.assertEqual(float(rows["aes"]["study_pass_fraction"]), 0)

    def test_exported_campaign_and_machine_provenance(self):
        self.job()
        self.manifest.update(label="check", campaign_id="fixture-campaign", order_seed=42)
        job = self.manifest["jobs"][0]
        job["threads"] = 4
        directory = self.dest / "jobs" / job["id"]
        attempt = study.read_json(directory / "attempt.json")
        attempt.update(machine_id="desktop", execution_id="fixture-execution", execution_index=1,
                       environment_file="environment-fixture-execution.json",
                       machine={"cpu_model": "fixture CPU", "host_ram_bytes": 16 * 1024**3})
        study.save_json(directory / "attempt.json", attempt)
        config = study.read_json(directory / "config.json")
        config["SYNTH_STRATEGY"] = "AREA 0"
        study.save_json(directory / "config.json", config)
        row = self.collect()[0]
        self.assertEqual(row["campaign_id"], "fixture-campaign")
        self.assertEqual(row["campaign_label"], "check")
        self.assertEqual(row["order_seed"], "42")
        self.assertEqual(row["machine_id"], "desktop")
        self.assertEqual(row["cpu_model"], "fixture CPU")
        self.assertEqual(row["host_ram_bytes"], str(16 * 1024**3))
        self.assertEqual(row["synthesis_strategy"], "AREA 0")
        self.assertEqual(row["schedule_index"], "")  # Historical position is unknown.
        with (self.dest / "metrics_long.csv").open() as stream:
            long_row = next(csv.DictReader(stream))
        for field in ["campaign_id", "campaign_label", "order_seed", "seed", "machine_id", "execution_id"]:
            self.assertEqual(long_row[field], row[field])

    def test_summary_separates_hosts_and_thread_counts(self):
        for seed, machine_id, threads in [(1, "desktop", 4), (2, "server", 4), (3, "server", 8)]:
            self.job(seed=seed)
            job = self.manifest["jobs"][-1]
            job["threads"] = threads
            path = self.dest / "jobs" / job["id"] / "attempt.json"
            attempt = study.read_json(path)
            attempt["machine_id"] = machine_id
            study.save_json(path, attempt)
        self.collect()
        with contextlib.redirect_stdout(io.StringIO()):
            study.summarize(self.args)
        with (self.dest / "summary.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({(r["machine_id"], r["threads"]) for r in rows},
                         {("desktop", "4"), ("server", "4"), ("server", "8")})
        self.assertTrue(all(r["attempts"] == "1" for r in rows))


if __name__ == "__main__":
    unittest.main()
