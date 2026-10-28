#!/usr/bin/env python3
"""Choose a pilot reference clock from a complete, audited clock sweep.

All planned jobs need valid post-route timing evidence with an applied seed, or
a recognized design failure. Negative setup slack is a valid measurement.
The chosen observed passing period is divided by --fraction (default .85)
as a headroom policy. --min-setup-margin also requires measured slack / period
to meet that threshold; --fraction 1 keeps the tested period unchanged.
This is NOT a measured Fmax or a guaranteed timing margin.
Validate the resulting reference with full flows before confirmation experiments.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def true(value):
    return value is True or value == 'True'


def calibrate(rows, fraction, expected_jobs, min_setup_margin=0.0):
    """Require the frozen planned jobs, each with clock_period_ns from its config."""
    if not 0 < fraction <= 1:
        raise ValueError('fraction must be in (0, 1].')
    if not math.isfinite(min_setup_margin) or not 0 <= min_setup_margin < 1:
        raise ValueError('min_setup_margin must be in [0, 1).')
    if not expected_jobs:
        raise ValueError('A nonempty frozen manifest is required for calibration.')
    planned = {job['id']: job for job in expected_jobs}
    if len(planned) != len(expected_jobs):
        raise ValueError('Duplicate planned job IDs.')
    observed = {}
    for row in rows:
        run_id = row.get('run_id')
        if run_id not in planned or run_id in observed:
            raise ValueError(f'Unexpected or duplicate calibration row: {run_id}')
        observed[run_id] = row
    groups = defaultdict(list)
    for job in expected_jobs:
        period = num(job.get('clock_period_ns'))
        if period is None or period <= 0 or str(job.get('repeat')) != '1':
            raise ValueError('Calibration requires positive planned periods and one run per distinct seed.')
        if job.get('stop_after') not in (None, 'OpenROAD.STAPostPNR'):
            raise ValueError('Calibration must request a full flow or stop at OpenROAD.STAPostPNR.')
        groups[(job['design'], job['pdk'])].append(job)
    results = {}
    for key, jobs in sorted(groups.items()):
        by_period, errors = defaultdict(list), []
        planned_counts = defaultdict(int)
        for job in jobs:
            period = float(job['clock_period_ns'])
            planned_counts[period] += 1
            row = observed.get(job['id'])
            if row is None:
                errors.append(f"{job['id']}: missing row")
                continue
            identity = ('design', 'pdk', 'configuration', 'seed', 'repeat')
            if any(str(row.get(k)) != str(job.get(k)) for k in identity):
                errors.append(f"{job['id']}: identity differs from manifest")
                continue
            ws = num(row.get('setup_ws_ns'))
            evidence_ok = (row.get('status') == 'completed' and num(row.get('returncode')) == 0
                           and true(row.get('seed_applied')) and true(row.get('post_route_sta_available'))
                           and num(row.get('clock_period_ns')) == period and ws is not None
                           and str(row.get('check__setup_timing')) in ('True', 'False'))
            design_failure = (row.get('status') == 'flow_error'
                              and num(row.get('returncode')) not in (None, 0)
                              and row.get('failure_kind') == 'design'
                              and row.get('failure_class') in {
                                  'congestion', 'placement', 'detailed_routing', 'timing',
                                  'setup_timing', 'hold_timing', 'layout_drc', 'lvs', 'route_drc'})
            if not evidence_ok and design_failure and num(row.get('clock_period_ns')) == period:
                # A recognized design failure can precede the seeded routing step (e.g. placement).
                # Missing seed/STA/full-flow checks alone do not establish a design failure.
                # Tool crashes, infrastructure failures and unexplained exits block selection.
                by_period[period].append((float('-inf'), False))
                continue
            if not evidence_ok:
                errors.append(f"{job['id']}: incomplete, failed, or missing/mismatched STA/seed evidence"
                              f" ({row.get('failure_class') or row.get('status')})")
                continue
            by_period[period].append((ws, true(row['check__setup_timing'])))
        periods = sorted(planned_counts)
        meets = {p: len(by_period[p]) == planned_counts[p]
                 and all(ws >= min_setup_margin * p and check for ws, check in by_period[p])
                 for p in periods}
        result = {'swept_periods_ns': periods,
                  'meets_timing': {str(p): len(by_period[p]) == planned_counts[p]
                                   and all(ws >= 0 and check for ws, check in by_period[p]) for p in periods},
                  'meets_reference_criterion': {str(p): meets[p] for p in periods},
                  'planned_runs_per_period': {str(p): planned_counts[p] for p in periods},
                  'runs_per_period': {str(p): len(by_period[p]) for p in periods},
                  'worst_setup_slack_ns': {str(p): num(min((v[0] for v in by_period[p]), default=float('nan')))
                                           for p in periods},
                  'design_failure_runs_per_period': {str(p): sum(not math.isfinite(ws) for ws, _ in by_period[p])
                                                     for p in periods},
                  'reference_frequency_fraction': fraction, 'errors': errors,
                  'minimum_setup_slack_fraction': min_setup_margin,
                  'selection_rule': 'Fastest qualifying period in the contiguous passing tail ending at the slowest tested period.',
                  'interpretation': 'Observed setup-passing reference plus chosen headroom; not Fmax or signoff.'}
        results[key] = result
        if errors:
            result['status'] = 'incomplete_or_failed_sweep'
            continue
        selected = None
        for p in reversed(periods):
            if not meets[p]:
                break
            selected = p
        if selected is None:
            result['status'] = 'no_period_meets_margin' if min_setup_margin else 'no_period_meets_timing'
        else:
            result.update(status='fastest_swept_period_met' if selected == periods[0] else 'ok',
                          fastest_observed_passing_period_ns=selected,
                          reference_period_ns=math.ceil(selected / fraction * 10) / 10)
    return results


def planned_jobs(dest, manifest):
    """Read the frozen configurations, checking hashes and a clock-only sweep."""
    jobs, signatures = [], {}
    for job in manifest['jobs']:
        path = dest / 'jobs' / job['id'] / 'config.json'
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != job['config_sha256']:
            raise ValueError(f'Prepared configuration changed: {job["id"]}')
        config = json.loads(raw)
        comparison = {k: v for k, v in config.items() if k not in {'CLOCK_PERIOD', 'STUDY_GRT_SEED'}}
        key = (job['design'], job['pdk'])
        if key in signatures and signatures[key] != comparison:
            raise ValueError(f'{key}: calibration must vary only CLOCK_PERIOD and routing seed.')
        signatures[key] = comparison
        jobs.append({**job, 'clock_period_ns': config['CLOCK_PERIOD']})
    return jobs


def write_reference(design, pdk, result, label):
    if result.get('status') not in ('ok', 'fastest_swept_period_met') or result.get('errors'):
        raise ValueError('Refusing to write a reference from an invalid sweep.')
    path = ROOT / 'configs/designs' / design / 'pdks' / f'{pdk}.json'
    data = json.loads(path.read_text()) if path.is_file() else {}
    data['CLOCK_PERIOD'] = result['reference_period_ns']
    data['//clock_status'] = 'calibrated'
    data.pop('//provisional_estimate', None)
    data['//calibration'] = {'campaign': label,
                            'written_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), **result}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--fraction', type=float, default=.85, help='Headroom policy relative to observed passing frequency.')
    parser.add_argument('--min-setup-margin', type=float, default=0.0,
                        help='Minimum observed setup slack / clock period (e.g. 0.10). '
                             'Use --fraction 1 to retain the measured candidate period unchanged.')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    from study import campaign
    dest = campaign(args.label)
    with (dest / 'runs.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    manifest = json.loads((dest / 'manifest.json').read_text())
    results = calibrate(rows, args.fraction, planned_jobs(dest, manifest), args.min_setup_margin)
    provenance = {name + '_sha256': hashlib.sha256((dest / name).read_bytes()).hexdigest()
                  for name in ['manifest.json', 'runs.csv']}
    for key, result in results.items():
        result['evidence'] = provenance
        print(f'{key[0]}/{key[1]}: {result["status"]}; reference={result.get("reference_period_ns")} ns')
        for error in result['errors']:
            print('  ' + error)
    (dest / 'calibration.json').write_text(json.dumps({f'{d}/{p}': r for (d, p), r in results.items()}, indent=2) + '\n')
    if any('reference_period_ns' not in r for r in results.values()):
        raise SystemExit('No clock files written: every planned design/PDK sweep must have complete evidence and a passing period.')
    if args.write:
        for (design, pdk), result in results.items():
            print(f'Wrote {write_reference(design, pdk, result, args.label)}')
        print('Validate these pilot reference clocks with full flows, then commit the clock files and evidence.')


if __name__ == '__main__':
    main()
