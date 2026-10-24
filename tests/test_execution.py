"""Exercise scheduling and bookkeeping using a tiny Python process, never EDA."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import study


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(study, "ROOT", self.root))
        self.stack.enter_context(mock.patch.object(study, "verify_flow"))
        self.stack.enter_context(mock.patch.object(study, "capture", return_value={"returncode": 0, "stdout": "fixture"}))
        self.stack.enter_context(mock.patch.object(study.shutil, "which", return_value=None))
        shutil.copytree(Path(__file__).resolve().parents[1] / "configs", self.root / "configs")
        settings_path = self.root / "configs/study.json"
        settings = json.loads(settings_path.read_text())
        settings["execution"]["parallel_jobs"] = 1  # these tests exercise the sequential path
        settings_path.write_text(json.dumps(settings))
        (self.root / "scripts").mkdir()
        (self.root / "scripts/flow_entry.py").write_text(
            'import json, sys\nfrom pathlib import Path\n'
            'config = json.loads(Path(sys.argv[-1]).read_text())\n'
            'print("Bookkeeping fixture: no EDA was performed")\n'
            'raise SystemExit(config.get("fixture_exit", 0))\n')
        self.dest = self.root / "results/check"
        self.dest.mkdir(parents=True)
        jobs = []
        for seed in [1, 2, 3]:
            run_id = f"ascon__sky130A__C0__s{seed:05d}__r1"
            directory = self.dest / "jobs" / run_id
            directory.mkdir(parents=True)
            study.save_json(directory / "config.json", {"STUDY_GRT_SEED": seed,
                            "CLOCK_PERIOD": 50, "PL_TARGET_DENSITY_PCT": 60,
                            "fixture_exit": 2 if seed == 2 else 0})
            jobs.append({"id": run_id, "design": "ascon", "pdk": "sky130A", "configuration": "C0",
                         "seed": seed, "repeat": 1, "threads": 4, "stop_after": None,
                         "manual_pdk_root": None, "config_sha256": study.sha(directory / "config.json")})
        self.ids = [job["id"] for job in jobs]
        self.schedule = {"schema": 1, "campaign_id": "fixture-campaign", "order_seed": 42,
                         "job_ids": [self.ids[2], self.ids[0], self.ids[1]]}
        study.save_json(self.dest / "schedule.json", self.schedule)
        self.manifest = {"schema": 3, "campaign_id": "fixture-campaign", "label": "check",
                         "phase": "pilot", "order_seed": 42, "jobs": jobs,
                         "schedule_sha256": study.sha(self.dest / "schedule.json"),
                         "input_hashes": {}, "repository": {}}
        study.save_json(self.dest / "manifest.json", self.manifest)
        self.args = SimpleNamespace(label="check", timeout=0, machine_id="fixture-host")

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()):
            study.execute(self.args)

    def rows(self):
        with (self.dest / "runs.csv").open() as stream:
            return {row["run_id"]: row for row in csv.DictReader(stream)}

    def test_saved_order_host_records_and_automatic_collection_with_failure(self):
        self.execute()
        rows = self.rows()
        for position, run_id in enumerate(self.schedule["job_ids"], 1):
            row = rows[run_id]
            attempt = study.read_json(self.dest / "jobs" / run_id / "attempt.json")
            self.assertEqual(row["schedule_index"], str(position))
            self.assertEqual(attempt["execution_index"], position)
            self.assertEqual(row["order_seed"], "42")
            self.assertEqual(row["campaign_id"], "fixture-campaign")
            self.assertEqual(row["machine_id"], "fixture-host")
            environment = study.read_json(self.dest / attempt["environment_file"])
            self.assertEqual(environment["machine"], attempt["machine"])
            self.assertEqual(environment["execution_id"], attempt["execution_id"])
            self.assertIn("cpu_model", attempt["machine"])
            self.assertIn("host_ram_bytes", attempt["machine"])
            self.assertEqual(json.loads(row['cpu_limits']), attempt['machine']['cpu_limits'])
            self.assertEqual(row['parallel_jobs'], str(attempt['parallel_jobs']))
            self.assertEqual(json.loads(row['cpu_affinity']), attempt['machine']['cpu_affinity'])
            self.assertEqual(row["study_checks_pass"], "False")  # A fake success is not a valid layout.
        self.assertEqual(rows[self.ids[1]]["status"], "flow_error")
        self.assertEqual(rows[self.ids[0]]["status"], "completed")
        self.assertTrue((self.dest / "summary.csv").is_file())
        self.assertTrue((self.dest / "metrics_long.csv").is_file())

    def test_resume_preserves_attempts_and_uses_remaining_saved_positions(self):
        prior_id = self.ids[2]
        path = self.dest / "jobs" / prior_id / "attempt.json"
        study.save_json(path, {"status": "flow_error", "returncode": 2,
                              "machine_id": "prior-host", "execution_id": "earlier"})
        original = path.read_bytes()
        self.execute()
        self.assertEqual(path.read_bytes(), original)
        first_new = study.read_json(self.dest / "jobs" / self.ids[0] / "attempt.json")
        self.assertEqual(first_new["schedule_index"], 2)
        self.assertEqual(first_new["execution_index"], 1)
        before = {p: p.read_bytes() for p in (self.dest / "jobs").glob("*/attempt.json")}
        self.execute()
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))

    def test_ctrl_c_records_interruption_and_collects_unattempted_jobs(self):
        process = mock.Mock()
        process.wait.side_effect = KeyboardInterrupt
        process.poll.return_value = 0
        machine = study.machine_snapshot(self.dest, self.args.machine_id)
        with mock.patch.object(study, "machine_snapshot", return_value=machine), \
                mock.patch.object(study.subprocess, "Popen", return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        rows = self.rows()
        self.assertEqual(rows[self.ids[2]]["status"], "interrupted")
        self.assertEqual(rows[self.ids[0]]["status"], "not_run")
        self.assertEqual(rows[self.ids[1]]["status"], "not_run")
        self.assertEqual(rows[self.ids[0]]["machine_id"], "")
        self.assertTrue((self.dest / "summary.csv").is_file())

    def test_edited_schedule_is_rejected(self):
        self.schedule["job_ids"].reverse()
        study.save_json(self.dest / "schedule.json", self.schedule)
        with self.assertRaisesRegex(ValueError, "schedule changed"):
            study.read_schedule(self.dest, self.manifest)
        self.assertEqual(list((self.dest / "jobs").glob("*/attempt.json")), [])

    def test_duplicate_schedule_positions_are_rejected_even_with_matching_checksum(self):
        self.schedule["job_ids"] = [self.ids[0]] * 3
        study.save_json(self.dest / "schedule.json", self.schedule)
        self.manifest["schedule_sha256"] = study.sha(self.dest / "schedule.json")
        with self.assertRaisesRegex(ValueError, "does not match"):
            study.read_schedule(self.dest, self.manifest)

    def test_unstarted_campaign_has_collectable_tables_and_summary_headers(self):
        with contextlib.redirect_stdout(io.StringIO()):
            study.refresh_tables(self.args)
        self.assertTrue(all(row["status"] == "not_run" for row in self.rows().values()))
        with (self.dest / "summary.csv").open() as stream:
            table = csv.DictReader(stream)
            self.assertIn("campaign_id", table.fieldnames)
            self.assertEqual(list(table), [])


    def test_crash_resume_reruns_only_unfinished_jobs_and_keeps_their_files(self):
        done, crashed, interrupted = self.ids[2], self.ids[0], self.ids[1]
        study.save_json(self.dest / "jobs" / done / "attempt.json", {"status": "completed", "returncode": 0})
        finished = (self.dest / "jobs" / done / "attempt.json").read_bytes()
        crash_dir = self.dest / "jobs" / crashed
        study.save_json(crash_dir / "attempt.json", {"status": "running", "returncode": None})
        (crash_dir / "runs/run/12-openroad-floorplan").mkdir(parents=True)
        (crash_dir / "console.log").write_text("partial log before power loss")
        study.save_json(self.dest / "jobs" / interrupted / "attempt.json", {"status": "interrupted"})
        self.execute()
        self.assertEqual((self.dest / "jobs" / done / "attempt.json").read_bytes(), finished)  # never recomputed
        attempt = study.read_json(crash_dir / "attempt.json")
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(len(attempt["previous_attempts"]), 1)
        archived = Path(attempt["previous_attempts"][0])
        self.assertEqual(study.read_json(archived / "attempt.json")["status"], "running")
        self.assertEqual((archived / "console.log").read_text(), "partial log before power loss")
        self.assertTrue((archived / "runs/run/12-openroad-floorplan").is_dir())
        self.assertEqual(study.read_json(self.dest / "jobs" / interrupted / "attempt.json")["status"], "flow_error")
        self.assertEqual(self.rows()[crashed]["previous_unfinished_attempts"], "1")

    def test_retry_can_be_disabled(self):
        study.save_json(self.dest / "jobs" / self.ids[0] / "attempt.json", {"status": "running"})
        self.args.retry_incomplete = False
        self.execute()
        self.assertEqual(study.read_json(self.dest / "jobs" / self.ids[0] / "attempt.json")["status"], "running")

    def test_second_runner_on_the_same_campaign_is_refused(self):
        lock = study.acquire_lock(self.dest)
        try:
            with self.assertRaisesRegex(ValueError, "Another runner"):
                self.execute()
        finally:
            lock.close()
        self.assertEqual(list((self.dest / "jobs").glob("*/attempt.json")), [])


if __name__ == "__main__":
    unittest.main()
