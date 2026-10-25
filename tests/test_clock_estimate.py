"""Two-step clock calibration: provisional estimate, relative sweep, calibrated reference."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
sys.path.insert(0, str(SOURCE / "tests"))
import calibrate_clock  # noqa: E402
import estimate_clock  # noqa: E402
import project  # noqa: E402


def smoke_row(pdk, ws, design="aes", seed=1, sta=True):
    return {"run_id": f"{design}__{pdk}__C0__s{seed:05d}__r1", "design": design, "pdk": pdk, "seed": str(seed),
            "repeat": "1", "clock_period_ns": "25.0", "setup_ws_ns": str(ws) if ws is not None else "",
            "post_route_sta_available": "True" if sta else "False", "layout_valid": "True", "failure_class": "timing"}


class EstimateTests(unittest.TestCase):
    def test_estimates_from_the_real_smoke_numbers(self):
        rows = [smoke_row("sky130A", -5.711850709637924), smoke_row("gf180mcuD", -12.791281285114273),
                smoke_row("ihp-sg13g2", 8.320067537088944), smoke_row("sky130A", -9.0, design="sha256", sta=False)]
        found = estimate_clock.estimates(rows)
        self.assertEqual(found[("aes", "sky130A")][0], 30.8)       # ceil(30.71, 0.1 ns)
        self.assertEqual(found[("aes", "gf180mcuD")][0], 37.8)
        self.assertEqual(found[("aes", "ihp-sg13g2")][0], 16.7)
        self.assertNotIn(("sha256", "sky130A"), found)             # no post-route STA: no estimate

    def test_write_marks_provisional_and_keeps_calibrated_clocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(SOURCE / "configs", root / "configs")
            (root / "results/smoke").mkdir(parents=True)
            import csv
            rows = [smoke_row("sky130A", -5.7), smoke_row("gf180mcuD", -12.8)]
            with (root / "results/smoke/runs.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            calibrated = root / "configs/designs/aes/pdks/gf180mcuD.json"
            calibrated.write_text(json.dumps({"CLOCK_PERIOD": 44.5, "//clock_status": "calibrated"}))
            argv = ["estimate_clock.py", "--label", "smoke", "--write"]
            with mock.patch.object(estimate_clock, "ROOT", root), mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(io.StringIO()):
                estimate_clock.main()
            sky = json.loads((root / "configs/designs/aes/pdks/sky130A.json").read_text())
            self.assertEqual((sky["CLOCK_PERIOD"], sky["//clock_status"]), (30.7, "provisional"))
            self.assertEqual(json.loads(calibrated.read_text())["CLOCK_PERIOD"], 44.5)   # not overwritten


class RelativeSweepTests(unittest.TestCase):
    def test_shipped_relative_plan_scales_the_provisional_clock(self):
        plan = json.loads((SOURCE / "configs/plans/calibrate-relative.json").read_text())
        experiment = json.loads((SOURCE / "experiments/suite/calibrate-relative.json").read_text())
        self.assertEqual(experiment["configs"][0], "C0")             # x1.0
        self.assertEqual(len(experiment["configs"]), 8)
        self.assertEqual(experiment["stop_after"], "OpenROAD.STAPostPNR")
        variables, aliases = project.load_variables(SOURCE, project.load_settings(SOURCE))
        layer = json.loads((SOURCE / "configs/variants/calrel__x0p8.json").read_text())
        merged = project.merge_layers([("design-pdk", {"CLOCK_PERIOD": 30.7}), ("variant", layer)],
                                      "sky130A", variables, aliases)[0]
        self.assertAlmostEqual(merged["CLOCK_PERIOD"], 24.56)
        self.assertEqual(plan["name"], "calrel")


class CalibrationWithDesignFailuresTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [dict(id=f"j{p}_{s}", design="aes", pdk="sky130A", configuration=f"c{p}", seed=s, repeat=1,
                          clock_period_ns=p, stop_after="OpenROAD.STAPostPNR") for p in (24, 30, 36) for s in (1, 2)]
        self.rows = [{**job, "run_id": job["id"], "status": "completed", "returncode": "0", "seed_applied": "True",
                      "post_route_sta_available": "True", "check__setup_timing": "True", "setup_ws_ns": "0.5"}
                     for job in self.jobs]

    def result(self):
        return calibrate_clock.calibrate(self.rows, .85, self.jobs)[("aes", "sky130A")]

    def test_congestion_at_the_tightest_period_counts_as_not_meeting(self):
        self.rows[0].update(status="flow_error", returncode="2", post_route_sta_available="False", setup_ws_ns="",
                            check__setup_timing="", failure_class="congestion", failure_kind="design")
        result = self.result()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fastest_observed_passing_period_ns"], 30)
        self.assertEqual(result["reference_period_ns"], 35.3)

    def test_tool_crash_still_blocks_the_sweep(self):
        self.rows[0].update(status="flow_error", returncode="2", post_route_sta_available="False", setup_ws_ns="",
                            failure_class="tool_crash", failure_kind="tool")
        self.assertEqual(self.result()["status"], "incomplete_or_failed_sweep")


class ProvisionalGuardTests(unittest.TestCase):
    def test_study_campaign_refuses_a_provisional_clock(self):
        # Reuse the git fixture from test_project.
        from test_project import ProjectTests
        case = ProjectTests("test_job_limit")
        case.setUp()
        try:
            case.write(case.root / "configs/designs/ascon/pdks/sky130A.json",
                       {"CLOCK_PERIOD": 30.7, "//clock_status": "provisional"})
            case.spec["phase"] = "study"
            case.write(case.spec_path, case.spec)
            case.commit(case.root)
            with self.assertRaisesRegex(ValueError, "no calibrated CLOCK_PERIOD"):
                case.prepare()
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
