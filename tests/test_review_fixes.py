"""Regression tests for review findings. Synthetic observations, not EDA validation."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import analyze
import calibrate_clock
import project
import study


class CalibrationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [dict(id=f'j{i}', design='aes', pdk='sky130A', configuration='c', seed=i,
                         repeat=1, clock_period_ns=25, stop_after='OpenROAD.STAPostPNR') for i in [1, 2]]
        self.rows = [{**job, 'run_id': job['id'], 'status': 'completed', 'returncode': '0',
                      'seed_applied': 'True', 'post_route_sta_available': 'True',
                      'check__setup_timing': 'True', 'setup_ws_ns': '.5'} for job in self.jobs]

    def result(self, rows=None):
        return calibrate_clock.calibrate(self.rows if rows is None else rows, .85, self.jobs)[('aes', 'sky130A')]

    def test_missing_and_unattempted_rows_cannot_create_a_reference(self):
        for rows in (self.rows[:1], [self.rows[0], {**self.rows[1], 'status': 'not_run'}], []):
            with self.subTest(rows=rows):
                self.assertNotIn('reference_period_ns', self.result(rows))

    def test_failed_run_with_positive_slack_does_not_pass(self):
        self.rows[0].update(status='flow_error', returncode='1')
        self.assertEqual(self.result()['status'], 'incomplete_or_failed_sweep')

    def test_seed_endpoint_period_and_identity_evidence_are_required(self):
        for update in ({'seed_applied': 'False'}, {'post_route_sta_available': 'False'},
                       {'clock_period_ns': '30'}, {'seed': 99}, {'setup_ws_ns': 'nan'}):
            with self.subTest(update=update):
                rows = [{**self.rows[0], **update}, self.rows[1]]
                self.assertNotIn('reference_period_ns', self.result(rows))

    def test_negative_slack_is_an_observed_failure_not_missing_data(self):
        self.rows[0].update(setup_ws_ns='-1', check__setup_timing='False')
        self.assertEqual(self.result()['status'], 'no_period_meets_timing')

    def test_duplicate_and_unplanned_rows_rejected(self):
        for rows in (self.rows + self.rows[:1], self.rows + [{**self.rows[0], 'run_id': 'unplanned'}]):
            with self.assertRaises(ValueError):
                self.result(rows)

    def test_invalid_sweep_cannot_be_written(self):
        with self.assertRaises(ValueError):
            calibrate_clock.write_reference('aes', 'sky130A', self.result([]), 'bad')

    def test_prepared_configs_must_be_unmodified_and_clock_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            jobs = []
            for i, period in enumerate([10, 20]):
                path = dest / 'jobs' / f'j{i}' / 'config.json'
                path.parent.mkdir(parents=True)
                study.save_json(path, {'CLOCK_PERIOD': period, 'STUDY_GRT_SEED': i + 1, 'SYNTH_STRATEGY': 'AREA 0'})
                jobs.append({'id': f'j{i}', 'design': 'aes', 'pdk': 'sky130A', 'config_sha256': study.sha(path)})
            self.assertEqual(len(calibrate_clock.planned_jobs(dest, {'jobs': jobs})), 2)
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'changed'):
                calibrate_clock.planned_jobs(dest, {'jobs': jobs})
            study.save_json(path, {'CLOCK_PERIOD': 20, 'STUDY_GRT_SEED': 2, 'SYNTH_STRATEGY': 'DELAY 1'})
            jobs[1]['config_sha256'] = study.sha(path)
            with self.assertRaisesRegex(ValueError, 'only CLOCK_PERIOD'):
                calibrate_clock.planned_jobs(dest, {'jobs': jobs})


class StatisticalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(project.load_settings(ROOT)['analysis'])
        self.metric = {'column': 'area', 'direction': 'min', 'effect': 'relative', 'population': 'passing'}
        self.cfg.update(metrics=[self.metric], bootstrap_iterations=100, single_run_trials=100)

    def rows(self, config, pdk='a', value=100, passed=True, n=10):
        return [dict(design='aes', pdk=pdk, configuration=config, seed=str(i), repeat='1',
                     status='completed', study_checks_pass=str(passed), area=value,
                     machine_id='host', threads='2', clock_period_ns='25') for i in range(1, n+1)]

    def make_study(self, rows):
        result = analyze.Study(rows, self.cfg)
        result.rankings()
        return result

    def test_identical_candidates_split_credit_and_share_rank(self):
        ranks = self.make_study(self.rows('C0') + self.rows('C1')).rankings()
        self.assertEqual([r['p_best'] for r in ranks], [.5, .5])
        self.assertEqual([r['rank'] for r in ranks], [1, 1])

    def test_single_eligible_candidate_gets_full_credit(self):
        ranks = self.make_study(self.rows('C0') + self.rows('bad', value=10, passed=False)).rankings()
        self.assertEqual(ranks[0]['p_best'], 1)
        self.assertIsNone(ranks[1]['p_best'])

    def test_predeclared_practical_tolerance(self):
        self.metric['tie_tolerance_abs'] = 1
        ranks = self.make_study(self.rows('C0') + self.rows('C1', value=100.5)).rankings()
        self.assertEqual([r['p_best'] for r in ranks], [.5, .5])
        self.assertTrue(all(r['observed_co_best'] for r in ranks))

    def test_transfer_retains_source_winner_that_fails_on_destination(self):
        rows = []
        for pdk in ['a', 'b']:
            rows += self.rows('winner', pdk, 50, passed=pdk == 'a')
            for i in range(3):
                rows += self.rows(f'C{i}', pdk, 100+i*10)
        result = self.make_study(rows).transfer()[0]
        self.assertEqual(result['best_a'], 'winner')
        self.assertEqual(result['transfer_a_to_b_status'], 'infeasible')
        self.assertIsNone(result['regret_on_b_using_best_of_a'])
        self.assertFalse(result['same_best'])
        self.assertEqual(result['kendall_tau'], 1)

    def test_transfer_with_no_common_feasible_configurations_still_reports_failure(self):
        rows = self.rows('C0', 'a') + self.rows('C0', 'b', passed=False)
        rows += self.rows('C1', 'a', passed=False) + self.rows('C1', 'b')
        result = self.make_study(rows).transfer()[0]
        self.assertEqual(result['common_feasible_configs'], 0)
        self.assertEqual(result['transfer_a_to_b_status'], 'infeasible')

    def test_tied_source_winners_do_not_hide_partial_infeasibility(self):
        rows = self.rows('C0', 'a') + self.rows('C1', 'a')
        rows += self.rows('C0', 'b') + self.rows('C1', 'b', passed=False)
        result = self.make_study(rows).transfer()[0]
        self.assertEqual(result['best_a'], 'C0;C1')
        self.assertEqual(result['transfer_a_to_b_status'], 'partly_infeasible')
        self.assertIsNone(result['regret_on_b_using_best_of_a'])

    def test_resampling_has_tie_aware_reference_and_no_true_best_claim(self):
        rows = self.make_study(self.rows('C0') + self.rows('C1')).dse_reliability()
        self.assertTrue(all(r['chance_level'] == 1 and r['p_pick_observed_best'] == 1 for r in rows))
        self.assertTrue(all('p_pick_true_best' not in r for r in rows))

    def test_budget_reference_uses_only_candidates_in_that_budget(self):
        rows = self.rows('too_few', value=1, n=3) + self.rows('C0', value=100) + self.rows('C1', value=110)
        result = next(r for r in self.make_study(rows).dse_reliability() if r['seeds_per_config'] == 5)
        self.assertEqual(result['observed_best_set'], 'C0')
        self.assertEqual(result['mean_regret'], 0)

    def test_mixed_machine_clock_and_limits_are_rejected(self):
        for change in ({'machine_id': 'other'}, {'threads': '4'}, {'cpu_limits': 'different'},
                       {'clock_period_ns': '30'}, {'parallel_jobs': '2'}):
            with self.subTest(change=change):
                rows = self.rows('C0')
                rows[-1].update(change)
                with self.assertRaises(ValueError):
                    self.make_study(rows)

    def test_defaults_do_not_infer_fmax_or_workload_power(self):
        cfg = project.load_settings(ROOT)['analysis']
        self.assertTrue({'fmax_est_mhz', 'area_delay_um2ns', 'achieved_period_ns'}.isdisjoint(cfg['derived']))
        self.assertNotIn('power_total_w', {m['column'] for m in cfg['metrics']})


class PoolingProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(analyze, 'ROOT', self.root)
        patch.start(); self.addCleanup(patch.stop)
        self.rows = []
        for label in ['one', 'two']:
            dest = self.root / 'results' / label
            (dest / 'inputs/configs').mkdir(parents=True)
            (dest / 'inputs/toolchain').mkdir()
            study.save_json(dest / 'manifest.json', {'flow_commit': 'flow-pin',
                'input_hashes': {'/repo/scripts/study.py': 'runner-hash'},
                'repository': {'toplevel': {'stdout': '/repo'}},
                'design_revisions': {'aes': {'head': {'stdout': 'rtl-pin'}}}})
            study.save_json(dest / 'inputs/configs/study.json', {'analysis': {'baseline_config': 'C0'}})
            for name in ['flake.lock', 'pdk_hashes.yaml']:
                (dest / 'inputs/toolchain' / name).write_text('pinned')
            study.save_json(dest / 'environment.json', {'python': '3.fixture',
                'executable_provenance': {'openroad': {'sha256': 'executable-hash'}},
                'openroad': {'returncode': 0, 'stdout': 'version', 'stderr': ''}})
            self.rows.append({'_campaign': label, 'environment_file': 'environment.json'})

    def validate(self):
        analyze.validate_pooling(['one', 'two'], self.rows)

    def test_matching_frozen_provenance_is_accepted(self):
        self.validate()

    def test_input_change_rejected_despite_disjoint_seeds(self):
        path = self.root / 'results/two/manifest.json'
        data = json.loads(path.read_text()); data['input_hashes']['/repo/scripts/study.py'] = 'changed'
        study.save_json(path, data)
        with self.assertRaisesRegex(SystemExit, 'changed input'):
            self.validate()

    def test_different_executable_rejected(self):
        path = self.root / 'results/two/environment.json'
        data = json.loads(path.read_text()); data['executable_provenance']['openroad']['sha256'] = 'different'
        study.save_json(path, data)
        with self.assertRaisesRegex(SystemExit, 'tool executable'):
            self.validate()

    def test_missing_manifest_rejected(self):
        (self.root / 'results/two/manifest.json').unlink()
        with self.assertRaises(SystemExit):
            self.validate()


class ResourceAndCornerTests(unittest.TestCase):
    def test_missing_selected_corner_is_unknown(self):
        self.assertIsNone(study.corner_metric({'ws__corner:slow': 1}, 'ws', min, ['slow', 'fast']))
        self.assertEqual(study.corner_metric({'ws__corner:slow': 1, 'ws__corner:fast': -2}, 'ws', min, ['slow', 'fast']), -2)

    def test_cpu_quota_includes_visible_ancestors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'cgroup'; child = root / 'user/job'; child.mkdir(parents=True)
            (root / 'cpu.max').write_text('300000 100000')
            (child.parent / 'cpu.max').write_text('150000 100000')
            (child / 'cpu.max').write_text('200000 100000')
            (child / 'cpuset.cpus.effective').write_text('0-3')
            membership = Path(tmp) / 'membership'; membership.write_text('0::/user/job\n')
            result = study.cpu_limit_snapshot(root, membership)
            self.assertEqual(result['quota_cpu_equivalents'], 1.5)
            self.assertEqual(result['effective_cpuset'], '0-3')


if __name__ == '__main__':
    unittest.main()
