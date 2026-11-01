#!/usr/bin/env bash
# Screen -> calibrate -> validate, on all 64 logical CPUs. Stops if calibration is blocked.
cd "$(dirname "$0")/.."
P=64
run() { bash scripts/enter-env.sh --run "python3 scripts/study.py run --label $1 --machine-id olympus --parallel-jobs $P"; }
stamp() { echo "=== $(date '+%F %T') $*"; }

stamp "1/3 clock screen (120 jobs)"
python3 scripts/study.py prepare --experiment experiments/suite/clock-screen-u40.json --label clock-screen-u40 || exit 1
run clock-screen-u40
python3 scripts/study.py collect --label clock-screen-u40

stamp "2/3 calibration"
if ! python3 scripts/calibrate_clock.py --label clock-screen-u40 --fraction 1 --min-setup-margin 0.10 --write; then
  stamp "STOPPED: calibration blocked. See results/clock-screen-u40/calibration.json"; exit 1
fi

stamp "3/3 reference validation (36 full flows)"
python3 scripts/study.py prepare --experiment experiments/suite/reference-validation.json --label reference-validation-u40 || exit 1
run reference-validation-u40
python3 scripts/study.py collect --label reference-validation-u40
python3 -c "
import csv
rows = list(csv.DictReader(open('results/reference-validation-u40/runs.csv')))
ok = [r for r in rows if r['study_checks_pass'] == 'True']
print(f'{len(ok)}/{len(rows)} validation flows pass every study check')
for r in rows:
    if r['study_checks_pass'] != 'True':
        print('  FAIL', r['run_id'], r['failure_class'], r['failed_step'])
"
stamp "done"
