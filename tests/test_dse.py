"""Config validation, relative operators, SDC rendering, variant generation, analysis and parallel runs.

Artificial fixtures only: these check bookkeeping and statistics, never EDA results.
"""
import contextlib
import csv
import io
import json
import random
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
import calibrate_clock  # noqa: E402
import project  # noqa: E402
import study  # noqa: E402
import variants  # noqa: E402
from expr import evaluate  # noqa: E402


def snapshot():
    settings = project.load_settings(SOURCE)
    return project.load_variables(SOURCE, settings)


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.variables, self.aliases = snapshot()

    def merge(self, *layers, pdk="sky130A"):
        return project.merge_layers([(f"layer{i}", l) for i, l in enumerate(layers)], pdk,
                                    self.variables, self.aliases)[0]

    def test_typo_is_rejected_with_suggestion(self):
        with self.assertRaisesRegex(ValueError, "PL_TIMING_DRIVEN"):
            self.merge({"PL_TIME_DRIVEN": True})

    def test_deprecated_name_points_to_canonical_name(self):
        with self.assertRaisesRegex(ValueError, "deprecated.*SYNTH_HIERARCHY_MODE"):
            self.merge({"SYNTH_NO_FLAT": True})

    def test_wrong_type_and_choice(self):
        with self.assertRaisesRegex(ValueError, "JSON bool"):
            self.merge({"PL_TIMING_DRIVEN": "yes"})
        with self.assertRaisesRegex(ValueError, "one of"):
            self.merge({"SYNTH_STRATEGY": "AREA 9"})

    def test_relative_operators(self):
        merged = self.merge({"CLOCK_PERIOD": 20}, {"CLOCK_PERIOD": {"$scale": 0.8}, "FP_CORE_UTIL": {"$add": 5}})
        self.assertEqual(merged["CLOCK_PERIOD"], 16.0)
        self.assertEqual(merged["FP_CORE_UTIL"], 55)  # LibreLane default 50 + 5
        merged = self.merge({"GRT_OVERFLOW_ITERS": {"$scale": 1.5}})
        self.assertEqual(merged["GRT_OVERFLOW_ITERS"], 75)
        self.assertIsInstance(merged["GRT_OVERFLOW_ITERS"], int)

    def test_per_pdk_values(self):
        layer = {"RT_MAX_LAYER": {"$per_pdk": {"sky130A": "met4", "gf180mcuD": "Metal4"}}}
        self.assertEqual(self.merge(layer, pdk="gf180mcuD")["RT_MAX_LAYER"], "Metal4")
        with self.assertRaisesRegex(ValueError, "no \\$per_pdk entry for ihp-sg13g2"):
            self.merge(layer, pdk="ihp-sg13g2")

    def test_scale_without_base_value_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "no lower layer"):
            self.merge({"PL_TARGET_DENSITY_PCT": {"$scale": 1.1}})

    def test_protected_fields(self):
        with self.assertRaisesRegex(ValueError, "identity/path/thread/seed"):
            self.merge({"STUDY_GRT_SEED": 3})

    def test_constraints(self):
        problems = project.constraint_violations({"FP_CORE_UTIL": 60, "PL_TARGET_DENSITY_PCT": 60},
                                                 self.variables, ["PL_TARGET_DENSITY_PCT >= FP_CORE_UTIL + 5"])
        self.assertEqual(len(problems), 1)
        problems = project.constraint_violations({}, self.variables, ["PL_TARGET_DENSITY_PCT > 0"])
        self.assertIn("has no value", problems[0])

    def test_expression_evaluator_is_restricted(self):
        self.assertTrue(evaluate("1 < x <= 3 and not y", {"x": 2, "y": False}.get))
        with self.assertRaises(ValueError):
            evaluate("__import__('os')", {}.get)

    def test_sdc_rendering(self):
        template = "set a @STUDY_SDC_A@\nset b @STUDY_SDC_B@\n"
        self.assertEqual(project.render_sdc(template, {"STUDY_SDC_A": 0.1, "STUDY_SDC_B": "x"}, "t"),
                         "set a 0.1\nset b x\n")
        with self.assertRaisesRegex(ValueError, "no value"):
            project.render_sdc(template, {"STUDY_SDC_A": 1}, "t")
        with self.assertRaisesRegex(ValueError, "not used"):
            project.render_sdc(template, {"STUDY_SDC_A": 1, "STUDY_SDC_B": 2, "STUDY_SDC_C": 3}, "t")

    def test_shipped_defaults_render_the_shipped_sdc(self):
        defaults = json.loads((SOURCE / "configs/defaults.json").read_text())["flow_overrides"]
        merged = self.merge(defaults)
        _, sdc = project.split_sdc(merged)
        text = project.render_sdc((SOURCE / "configs/constraints/single_clock.sdc").read_text(), sdc, "shipped")
        self.assertNotIn("@STUDY_SDC", text)


class CatalogueAndGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / "configs", self.root / "configs",
                        ignore=shutil.ignore_patterns("*.generated.json"))
        for path in (self.root / "configs/variants").glob("*__*.json"):
            path.unlink()

    def plan(self, **fields):
        body = {"name": "t", "experiment": {"designs": ["ascon"], "pdks": ["sky130A", "ihp-sg13g2"], "seeds": [1, 2]},
                "experiment_path": "experiments/t.json", **fields}
        path = self.root / "configs/plans/t.json"
        path.write_text(json.dumps(body))
        return path

    def test_shipped_catalogue_matches_librelane_snapshot(self):
        self.assertEqual(variants.check_catalog(SOURCE), [])

    def test_shipped_plans_generate(self):
        for plan in sorted((SOURCE / "configs/plans").glob("*.json")):
            if plan.name.endswith(".generated.json"):
                continue
            result = variants.generate(plan, root=self.root, write=False)
            self.assertGreater(result["variants"], 0, plan.name)

    def test_ofat_adds_requirements_and_skips_baseline_duplicates(self):
        result = variants.generate(self.plan(strategy="ofat", knobs=["SYNTH_STRATEGY", "PL_RESIZER_SETUP_SLACK_MARGIN"]),
                                   root=self.root)
        self.assertEqual(result["variants"], 8 + 3)  # AREA 0 equals the baseline and is skipped
        layer = json.loads((self.root / "configs/variants/t__PL_RESIZER_SETUP_SLACK_MARGIN__ofclock0p02.json").read_text())
        self.assertTrue(layer["RUN_POST_CTS_RESIZER_TIMING"])
        experiment = json.loads((self.root / "experiments/t.json").read_text())
        self.assertEqual(experiment["configs"][0], "C0")
        self.assertEqual(len(experiment["configs"]), 12)
        self.assertEqual(result["jobs"], 12 * 2 * 2)

    def test_factorial_counts_and_constraint_skips(self):
        result = variants.generate(self.plan(strategy="factorial", knobs=["FP_CORE_UTIL", "PL_TARGET_DENSITY_PCT"],
                                             levels={"FP_CORE_UTIL": [58], "PL_TARGET_DENSITY_PCT": [62, 70]}),
                                   root=self.root)
        # (base, 58) x (base 60, 62, 70) minus baseline = 5 combos; util 58 with density 60/62 violates +5.
        self.assertEqual(result["variants"], 3)
        self.assertEqual(result["skipped"], 2)

    def test_lhs_is_reproducible_and_relative_clock(self):
        plan = self.plan(strategy="lhs", samples=12, rng_seed=3, knobs=["CLOCK_PERIOD", "GRT_ADJUSTMENT"])
        first = variants.generate(plan, root=self.root, write=False)["index"]["variants"]
        second = variants.generate(plan, root=self.root, write=False)["index"]["variants"]
        self.assertEqual(first, second)
        scales = sorted(v["CLOCK_PERIOD"]["$scale"] for v in first.values())
        self.assertEqual(len(scales), 12)
        # One sample per stratum of [0.7, 1.3].
        for i, s in enumerate(scales):
            self.assertTrue(0.7 + 0.05 * i - 1e-9 <= s <= 0.7 + 0.05 * (i + 1) + 1e-9)

    def test_uncontrolled_randomness_is_blocked_unless_allowed(self):
        with self.assertRaisesRegex(ValueError, "uncontrolled_randomness"):
            variants.generate(self.plan(strategy="ofat", knobs=["RUN_HEURISTIC_DIODE_INSERTION"]), root=self.root)
        result = variants.generate(self.plan(strategy="ofat", knobs=["RUN_HEURISTIC_DIODE_INSERTION"],
                                             allow_opt_in=True), root=self.root, write=False)
        self.assertEqual(result["variants"], 1)

    def test_regeneration_replaces_only_its_own_files(self):
        manual = self.root / "configs/variants/t__manual.json"
        manual.write_text("{}")
        variants.generate(self.plan(strategy="ofat", knobs=["SYNTH_SIZING"]), root=self.root)
        variants.generate(self.plan(strategy="ofat", knobs=["SYNTH_ABC_DFF"]), root=self.root)
        names = sorted(p.name for p in (self.root / "configs/variants").glob("t__*.json"))
        self.assertEqual(names, ["t__SYNTH_ABC_DFF__on.json", "t__manual.json"])  # off == default


class AnalysisTests(unittest.TestCase):
    """Synthetic runs with planted effects: the analysis must find them and nothing else."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / "configs", self.root / "configs")
        patcher = mock.patch.object(analyze, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def campaign(self, label, effects, seeds=10, noise=0.01, fail=()):
        rng = random.Random(1)
        rows = []
        for pdk, base in {"sky130A": 9000.0, "ihp-sg13g2": 7000.0}.items():
            for cfg, eff in effects.items():
                eff = eff[pdk] if isinstance(eff, dict) else eff
                for seed in range(1, seeds + 1):
                    ok = (pdk, cfg) not in fail
                    rows.append({"design": "ascon", "pdk": pdk, "configuration": cfg, "seed": seed, "repeat": 1,
                                 "status": "completed", "study_checks_pass": ok, "clock_period_ns": 20,
                                 "setup_ws_ns": 1 + rng.gauss(0, 0.1),
                                 "instance_area_um2": base * (1 + eff / 100) * (1 + rng.gauss(0, noise)),
                                 "knob__X": cfg})
        dest = self.root / "results" / label
        dest.mkdir(parents=True)
        with (dest / "runs.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def run_analysis(self, *labels):
        argv = ["analyze.py", "--fast"] + [x for label in labels for x in ("--label", label)]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            analyze.main()
        out = self.root / "results" / labels[0] / "analysis"
        tables = {}
        for name in ["effects", "transfer", "variance", "dse_reliability", "ranking", "noise_floor"]:
            with (out / f"{name}.csv").open() as stream:
                tables[name] = list(csv.DictReader(stream))
        return tables

    def test_planted_effects_are_found_and_null_ones_are_not(self):
        self.campaign("a", {"C0": 0, "big": -8, "null": 0, "worse": 5})
        tables = self.run_analysis("a")
        verdict = {(r["pdk"], r["configuration"]): r["verdict"] for r in tables["effects"]
                   if r["metric"] == "instance_area_um2"}
        for pdk in ["sky130A", "ihp-sg13g2"]:
            self.assertEqual(verdict[(pdk, "big")], "better")
            self.assertEqual(verdict[(pdk, "worse")], "worse")
            self.assertEqual(verdict[(pdk, "null")], "no_clear_difference")
        floor = next(r for r in tables["noise_floor"] if r["metric"] == "instance_area_um2" and r["pdk"] == "sky130A")
        self.assertAlmostEqual(float(floor["pooled_within_config_cv_pct"]), 1.0, delta=0.35)

    def test_transfer_and_interaction(self):
        self.campaign("b", {"C0": 0, "a": -8, "b": -4, "c": 4, "d": 8})
        same = next(r for r in self.run_analysis("b")["transfer"] if r["metric"] == "instance_area_um2")
        self.assertGreater(float(same["kendall_tau"]), 0.9)
        self.assertEqual(same["same_best"], "True")
        self.campaign("c", {"C0": 0, "a": {"sky130A": -8, "ihp-sg13g2": 8}, "b": {"sky130A": 8, "ihp-sg13g2": -8},
                            "c": {"sky130A": -4, "ihp-sg13g2": 4}, "d": {"sky130A": 4, "ihp-sg13g2": -4}})
        tables = self.run_analysis("c")
        flipped = next(r for r in tables["transfer"] if r["metric"] == "instance_area_um2")
        self.assertLess(float(flipped["kendall_tau"]), -0.5)
        self.assertEqual(flipped["same_best"], "False")
        across = next(r for r in tables["variance"] if r["metric"] == "instance_area_um2" and r["pdk"] == "ALL")
        self.assertGreater(float(across["share_pdk_x_configuration_pct"]), 50)

    def test_infeasible_configuration_is_ranked_last(self):
        self.campaign("d", {"C0": 0, "tiny_but_failing": -20}, fail={("sky130A", "tiny_but_failing")})
        ranking = [r for r in self.run_analysis("d")["ranking"]
                   if r["metric"] == "instance_area_um2" and r["pdk"] == "sky130A"]
        self.assertEqual(ranking[0]["configuration"], "C0")
        self.assertEqual(ranking[-1]["feasible"], "False")

    def test_noise_makes_single_seed_dse_unreliable(self):
        self.campaign("e", {"C0": 0, **{f"v{i}": 0 for i in range(6)}}, noise=0.02)
        rel = [r for r in self.run_analysis("e")["dse_reliability"]
               if r["metric"] == "instance_area_um2" and r["seeds_per_config"] == "1"]
        for r in rel:  # no real differences: picking the 'best' is close to chance
            self.assertLess(float(r["p_pick_observed_best"]), 0.5)

    def test_overlapping_campaigns_are_not_pooled(self):
        self.campaign("f", {"C0": 0, "x": 1})
        self.campaign("g", {"C0": 0})
        with self.assertRaises(SystemExit):
            self.run_analysis("f", "g")


class ParallelAndFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / "configs", self.root / "configs")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [("ROOT", self.root), ("verify_flow", mock.Mock()),
                            ("capture", mock.Mock(return_value={"returncode": 0, "stdout": "fixture"}))]:
            self.stack.enter_context(mock.patch.object(study, name, value))
        self.stack.enter_context(mock.patch.object(study.shutil, "which", return_value=None))
        (self.root / "scripts").mkdir()
        (self.root / "scripts/flow_entry.py").write_text(
            "import json, sys, time\nfrom pathlib import Path\n"
            "config = json.loads(Path(sys.argv[-1]).read_text())\n"
            "run = Path(sys.argv[sys.argv.index('--design-dir') + 1]) / 'runs/run'\n"
            "(run / '1-yosys-synthesis').mkdir(parents=True)\n"
            "(run / '1-yosys-synthesis/state_out.json').write_text('{\"metrics\": {}}')\n"
            "(run / '2-openroad-floorplan').mkdir()\n"
            "time.sleep(0.2)\n"
            "raise SystemExit(config.get('fixture_exit', 0))\n")
        self.dest = self.root / "results/par"
        jobs = []
        for seed in range(1, 7):
            run_id = f"ascon__sky130A__C0__s{seed:05d}__r1"
            directory = self.dest / "jobs" / run_id
            directory.mkdir(parents=True)
            study.save_json(directory / "config.json", {"STUDY_GRT_SEED": seed, "SYNTH_STRATEGY": "AREA 0",
                                                         "fixture_exit": 3 if seed == 4 else 0})
            jobs.append({"id": run_id, "design": "ascon", "pdk": "sky130A", "configuration": "C0", "seed": seed,
                         "repeat": 1, "threads": 1, "stop_after": None, "manual_pdk_root": None,
                         "config_sha256": study.sha(directory / "config.json")})
        ids = [j["id"] for j in jobs]
        study.save_json(self.dest / "schedule.json", {"schema": 1, "campaign_id": "c", "order_seed": 1, "job_ids": ids})
        study.save_json(self.dest / "manifest.json", {
            "schema": 4, "campaign_id": "c", "label": "par", "phase": "pilot", "order_seed": 1, "jobs": jobs,
            "schedule_sha256": study.sha(self.dest / "schedule.json"), "input_hashes": {}, "repository": {},
            "variant_layers": {"C0": {"SYNTH_STRATEGY": "AREA 0"}}})
        settings = json.loads((self.root / "configs/study.json").read_text())
        settings["execution"]["parallel_jobs"] = 3
        (self.root / "configs/study.json").write_text(json.dumps(settings))

    def test_parallel_run_records_every_job_and_failure_location(self):
        with contextlib.redirect_stdout(io.StringIO()):
            study.execute(SimpleNamespace(label="par", timeout=None, machine_id="host"))
        with (self.dest / "runs.csv").open() as stream:
            rows = {r["seed"]: r for r in csv.DictReader(stream)}
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["status"] for s, r in rows.items() if s != "4"}, {"completed"})
        self.assertEqual(rows["4"]["status"], "flow_error")
        self.assertEqual(rows["4"]["failed_step"], "2-openroad-floorplan")
        self.assertEqual(rows["4"]["failure_reason"], "exit_ok=fail")
        self.assertEqual(rows["1"]["failure_reason"], "seed_applied=fail")  # fixture never routes
        self.assertEqual(rows["1"]["knob__SYNTH_STRATEGY"], "AREA 0")
        self.assertIn("knob__PL_TARGET_DENSITY_PCT", rows["1"])
        indices = sorted(int(study.read_json(self.dest / "jobs" / r["run_id"] / "attempt.json")["execution_index"])
                         for r in rows.values())
        self.assertEqual(indices, list(range(1, 7)))
        attempt = study.read_json(self.dest / "jobs" / rows["1"]["run_id"] / "attempt.json")
        self.assertEqual(attempt["parallel_jobs"], 3)


class CalibrationTests(unittest.TestCase):
    def rows(self, slacks):
        return [{"run_id": f"p{period}-s{seed}", "design": "aes", "pdk": "sky130A",
                 "configuration": f"p{period}", "seed": str(seed), "status": "completed", "repeat": "1",
                 "returncode": "0", "seed_applied": "True", "post_route_sta_available": str(ws is not None),
                 "check__setup_timing": str(ws is not None and ws >= 0),
                 "clock_period_ns": str(period), "setup_ws_ns": "" if ws is None else str(ws)}
                for period, values in slacks.items() for seed, ws in enumerate(values, 1)]

    def evaluate(self, slacks):
        rows = self.rows(slacks)
        jobs = [{**r, 'id': r['run_id'], 'stop_after': 'OpenROAD.STAPostPNR'} for r in rows]
        return calibrate_clock.calibrate(rows, .85, jobs)[('aes', 'sky130A')]

    def test_fastest_observed_period_ignores_lucky_points_below_a_failure(self):
        result = self.evaluate({4: [.01, .02, .03], 5.5: [.1, -.05, .2], 8: [.5, .6, .4], 11: [2, 2.1, 1.9]})
        self.assertEqual(result['fastest_observed_passing_period_ns'], 8)
        self.assertEqual(result['reference_period_ns'], 9.5)
        self.assertEqual(result['status'], 'ok')

    def test_missing_sta_blocks_the_sweep(self):
        result = self.evaluate({4: [-1], 8: [None, .2]})
        self.assertEqual(result['status'], 'incomplete_or_failed_sweep')
        self.assertNotIn('reference_period_ns', result)

    def test_fastest_swept_point_met_is_flagged(self):
        self.assertEqual(self.evaluate({4: [.3], 8: [1]})['status'], 'fastest_swept_period_met')


if __name__ == "__main__":
    unittest.main()
