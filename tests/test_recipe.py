"""Recipe comparison on synthetic campaigns (bookkeeping only, no EDA)."""
import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
import recipe_report  # noqa: E402

FIELDS = ["design", "pdk", "configuration", "seed", "repeat", "status", "threads", "parallel_jobs", "tool_runtime",
          "layout_valid", "study_checks_pass", "failure_class", "failure_kind", "started_utc", "finished_utc",
          "wall_time_s", "cpu_time_s", "peak_rss_mb"] + recipe_report.COMPARE


class RecipeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(recipe_report, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def campaign(self, label, threads, hours, crash_seed=None, noisy=False):
        rows = []
        for seed in (1, 2):
            for rep in (1, 2):
                crashed = seed == crash_seed and rep == 2
                area = 1000 + seed + (rep * 0.5 if noisy else 0)
                rows.append({"design": "aes", "pdk": "sky130A", "configuration": "C0", "seed": seed, "repeat": rep,
                             "status": "flow_error" if crashed else "completed", "threads": threads,
                             "parallel_jobs": 64 // threads, "tool_runtime": "bwrap",
                             "layout_valid": not crashed, "study_checks_pass": not crashed,
                             "failure_class": "tool_crash" if crashed else "", "failure_kind": "tool" if crashed else "",
                             "started_utc": "2026-10-24T10:00:00+00:00",
                             "finished_utc": f"2026-10-24T{10 + hours:02d}:00:00+00:00",
                             "wall_time_s": 3600 * hours, "cpu_time_s": 3600 * hours * threads * 0.8,
                             "peak_rss_mb": 3000, "instance_area_um2": area, "instance_count": 9000,
                             "wirelength_um": 5e5 + seed, "vias": 1e5, "setup_ws_ns": 0.5, "hold_ws_ns": 0.1,
                             "route_drc_errors": 0, "antenna_violating_nets": 0})
        dest = self.root / "results" / label
        dest.mkdir(parents=True)
        (dest / "manifest.json").write_text(json.dumps({"jobs": rows}))
        with (dest / "runs.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def report(self):
        out = self.root / "r.md"
        with mock.patch.object(sys, "argv", ["recipe_report.py", "--prefix", "recipe-x-", "--out", str(out)]), \
                contextlib.redirect_stdout(io.StringIO()):
            recipe_report.main()
        return out.read_text()

    def test_picks_the_fastest_clean_deterministic_campaign(self):
        self.campaign("recipe-x-t1", 1, 4)
        self.campaign("recipe-x-t4", 4, 2, crash_seed=2)        # faster but a tool crash
        self.campaign("recipe-x-t2", 2, 3, noisy=True)          # clean but not deterministic
        text = self.report()
        self.assertIn("Highest throughput: **recipe-x-t1**", text)
        self.assertIn("| recipe-x-t2 | 2 | 0 |", text)          # 2 pairs, 0 same... (outcome same, results differ)
        self.assertIn("tool_crash", text)

    def test_no_candidate_is_reported_honestly(self):
        self.campaign("recipe-x-t4", 4, 2, crash_seed=1)
        self.assertIn("No campaign is free of tool/infra failures", self.report())


if __name__ == "__main__":
    unittest.main()
