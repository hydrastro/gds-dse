"""Exercise real local Git submodules and config expansion; no EDA tools needed."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts'))
from project import prepare_campaign, repo_snapshot, verify_repository
from study import verify_inputs


class ProjectTests(unittest.TestCase):
    def command(self, root, *args):
        return subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                               *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, root):
        self.command(root, 'add', '.')
        self.command(root, 'commit', '-m', 'fixture')

    def write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / 'study'
        self.root.mkdir()
        for directory in ['configs', 'experiments', 'scripts']:
            shutil.copytree(SOURCE / directory, self.root / directory, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(SOURCE / '.gitignore', self.root / '.gitignore')
        self.command(self.root, 'init')
        for name, target in [('flow', 'tools/librelane'), ('rtl', 'designs/ascon')]:
            remote = base / name
            remote.mkdir()
            if name == 'flow':
                self.write(remote / 'flake.lock', {})
                (remote / 'librelane').mkdir()
                (remote / 'librelane/pdk_hashes.yaml').write_text('fixture: fixture\n')
            else:
                (remote / 'core.sv').write_text('module core(input clk, output q); assign q = clk; endmodule\n')
            self.command(remote, 'init')
            self.commit(remote)
            self.command(self.root, '-c', 'protocol.file.allow=always', 'submodule', 'add', str(remote), target)
        self.flow = self.root / 'tools/librelane'
        self.flow_commit = self.command(self.flow, 'rev-parse', 'HEAD')
        path = self.root / 'configs/designs/ascon/design.json'
        design = json.loads(path.read_text())
        design.update(top='core', clock_port='clk', sources=['core.sv'])
        self.write(path, design)
        self.spec = {'phase': 'pilot', 'designs': ['ascon'], 'pdks': ['sky130A'],
                     'configs': ['C0'], 'seeds': [1], 'repeat': 1, 'stop_after': None, 'order_seed': 7}
        self.spec_path = self.root / 'experiments/fixture.json'
        self.write(self.spec_path, self.spec)
        self.write(self.root / 'configs/designs/ascon/pdks/sky130A.json', {'CLOCK_PERIOD': 50})  # 'calibrated'
        self.commit(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, name='fixture'):
        prepare_campaign(self.root, self.flow, self.flow_commit, self.spec_path, name)
        return json.loads((self.root / 'results' / name / 'manifest.json').read_text())

    def test_precedence_and_multiple_design_cartesian_product(self):
        second = self.root / 'configs/designs/second'
        shutil.copytree(self.root / 'configs/designs/ascon', second)
        self.spec.update(designs=['ascon', 'second'], configs=['C0', 'Cminus'], seeds=[1, 2])
        self.write(self.spec_path, self.spec)
        self.write(self.root / 'configs/designs/ascon/pdks/sky130A.json', {'FP_CORE_UTIL': 45, 'CLOCK_PERIOD': 60})
        self.write(self.root / 'configs/variants/C0.json', {'FP_CORE_UTIL': 50, 'PL_TARGET_DENSITY_PCT': 60})
        manifest = self.prepare()
        self.assertEqual(len(manifest['jobs']), 8)
        self.assertEqual(len({job['id'] for job in manifest['jobs']}), 8)
        job = next(j for j in manifest['jobs'] if j['design'] == 'ascon' and j['configuration'] == 'C0')
        config_path = self.root / 'results/fixture/jobs' / job['id'] / 'config.json'
        config = json.loads(config_path.read_text())
        self.assertEqual(config['FP_CORE_UTIL'], 50)
        self.assertEqual(config['CLOCK_PERIOD'], 60)
        self.assertRegex(config['PNR_SDC_FILE'], r'/inputs/constraints/ascon-[0-9a-f]{16}\.sdc$')
        origins = json.loads(config_path.with_name('config_origins.json').read_text())
        self.assertEqual(origins['FP_CORE_UTIL'], 'configs/variants/C0.json')

    def test_prepare_selections_are_frozen_without_editing_the_preset(self):
        original = self.spec_path.read_bytes()
        prepare_campaign(self.root, self.flow, self.flow_commit, self.spec_path, 'selected',
                         {'configs': ['C0', 'Cminus'], 'seeds': [7, 9]})
        dest = self.root / 'results/selected'
        manifest = json.loads((dest / 'manifest.json').read_text())
        self.assertEqual(len(manifest['jobs']), 4)
        self.assertEqual(manifest['experiment']['seeds'], [7, 9])
        self.assertEqual(manifest['source_experiment']['seeds'], [1])
        self.assertEqual(manifest['prepare_selections']['configs'], ['C0', 'Cminus'])
        self.assertEqual(self.spec_path.read_bytes(), original)
        self.assertEqual(json.loads((dest / 'inputs/experiments/fixture.json').read_text()), self.spec)

    def test_formal_campaign_uses_committed_submodules_and_rejects_edits(self):
        self.spec['phase'] = 'study'
        self.write(self.spec_path, self.spec)
        self.commit(self.root)
        manifest = self.prepare()
        verify_repository(self.root, manifest['repository'], True)
        rtl = self.root / 'designs/ascon/core.sv'
        rtl.write_text(rtl.read_text() + '// changed\n')
        with self.assertRaisesRegex(ValueError, 'Commit the study files'):
            verify_repository(self.root, manifest['repository'], True)

    def test_changed_configuration_cannot_silently_change_a_prepared_campaign(self):
        manifest = self.prepare()
        verify_inputs(manifest)
        self.write(self.root / 'configs/variants/C0.json', {'PL_TARGET_DENSITY_PCT': 70})
        with self.assertRaisesRegex(ValueError, 'Frozen input changed'):
            verify_inputs(manifest)

    def test_identity_fields_cannot_be_overridden_by_a_variant(self):
        self.write(self.root / 'configs/variants/C0.json', {'PDK': 'wrong', 'PL_TARGET_DENSITY_PCT': 60})
        with self.assertRaisesRegex(ValueError, 'identity/path/thread/seed'):
            self.prepare()
        self.assertFalse((self.root / 'results/fixture').exists())

    def test_saved_schedule_and_reusing_order_seed_with_nested_experiment(self):
        self.spec.update(configs=['C0', 'Cminus'], seeds=[1, 2, 3])
        self.spec_path = self.root / 'experiments/ascon/schedule.json'
        self.write(self.spec_path, self.spec)
        first = self.prepare('first')
        second = self.prepare('second')
        schedules = [json.loads((self.root / 'results' / name / 'schedule.json').read_text())
                     for name in ['first', 'second']]
        self.assertNotEqual(first['campaign_id'], second['campaign_id'])
        self.assertEqual(schedules[0]['job_ids'], schedules[1]['job_ids'])
        self.assertEqual(len(schedules[0]['job_ids']), 6)
        self.assertEqual(set(schedules[0]['job_ids']), {job['id'] for job in first['jobs']})
        self.assertEqual(schedules[0]['campaign_id'], first['campaign_id'])
        self.assertEqual(schedules[0]['order_seed'], 7)


    def test_relative_variant_and_rendered_sdc_per_configuration(self):
        sdc = self.root / 'configs/constraints/single_clock.sdc'
        sdc.write_text(sdc.read_text() + 'set_clock_uncertainty -hold @STUDY_SDC_HOLD_UNCERTAINTY_NS@ [all_clocks]\n')
        defaults = json.loads((self.root / 'configs/defaults.json').read_text())
        defaults['flow_overrides']['STUDY_SDC_HOLD_UNCERTAINTY_NS'] = 0.1
        self.write(self.root / 'configs/defaults.json', defaults)
        self.write(self.root / 'configs/designs/ascon/pdks/sky130A.json', {'CLOCK_PERIOD': 40})
        self.write(self.root / 'configs/variants/fast.json', {'CLOCK_PERIOD': {'$scale': 0.5},
                                                              'STUDY_SDC_HOLD_UNCERTAINTY_NS': 0.3})
        self.spec.update(configs=['C0', 'fast'])
        self.write(self.spec_path, self.spec)
        manifest = self.prepare()
        jobs = {j['configuration']: j for j in manifest['jobs']}
        configs = {c: json.loads((self.root / 'results/fixture/jobs' / j['id'] / 'config.json').read_text())
                   for c, j in jobs.items()}
        self.assertEqual(configs['C0']['CLOCK_PERIOD'], 40)
        self.assertEqual(configs['fast']['CLOCK_PERIOD'], 20.0)
        self.assertNotIn('STUDY_SDC_HOLD_UNCERTAINTY_NS', configs['fast'])  # never passed to LibreLane
        self.assertEqual(configs['fast']['STUDY_GRT_CAPACITY_PCT'], 0)
        sdc = {c: Path(cfg['PNR_SDC_FILE']).read_text() for c, cfg in configs.items()}
        self.assertIn('set_clock_uncertainty -hold 0.3 ', sdc['fast'])
        self.assertIn('set_clock_uncertainty -hold 0.1 ', sdc['C0'])
        self.assertNotEqual(jobs['C0']['sdc_sha256'], jobs['fast']['sdc_sha256'])
        verify_inputs(manifest)
        Path(configs['fast']['PNR_SDC_FILE']).write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'Frozen input changed'):
            verify_inputs(manifest)

    def test_infeasible_merged_configuration_stops_prepare(self):
        self.write(self.root / 'configs/variants/C0.json', {'FP_CORE_UTIL': 70})
        with self.assertRaisesRegex(ValueError, 'PL_TARGET_DENSITY_PCT >= FP_CORE_UTIL'):
            self.prepare()
        self.assertFalse((self.root / 'results/fixture').exists())

    def test_job_limit(self):
        settings = json.loads((self.root / 'configs/study.json').read_text())
        settings['execution']['max_jobs_per_campaign'] = 1
        self.write(self.root / 'configs/study.json', settings)
        self.spec.update(seeds=[1, 2])
        self.write(self.spec_path, self.spec)
        with self.assertRaisesRegex(ValueError, 'max_jobs_per_campaign'):
            self.prepare()


    def test_study_campaign_requires_calibrated_clock(self):
        self.write(self.root / 'configs/designs/ascon/pdks/sky130A.json', {})
        self.spec['phase'] = 'study'
        self.write(self.spec_path, self.spec)
        self.commit(self.root)
        with self.assertRaisesRegex(ValueError, 'no calibrated CLOCK_PERIOD'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
