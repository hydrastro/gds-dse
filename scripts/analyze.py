#!/usr/bin/env python3
"""Turn runs.csv (design × PDK × configuration × seed) into the study's answers.

    python3 scripts/analyze.py --label ascon-core-001
    python3 scripts/analyze.py --label camp-a --label camp-b --out reports/pooled   # disjoint campaigns only

Pure standard library. Settings: configs/study.json → "analysis" (frozen copy in the
campaign when available). Outputs (CSV + report.md) go to results/<label>/analysis/.

The questions, and the table that answers each:

  Q1  How large is the flow's seed noise?                       noise.csv, noise_floor.csv
  Q2  Which configurations differ from baseline beyond noise?   effects.csv
  Q3  How much of the variation is configuration vs seed?       variance.csv
  Q4  Which knobs matter (screening)?                           knob_effects.csv, knob_importance.csv
  Q5  Is the ranking of configurations stable across PDKs?      ranking.csv, transfer.csv
  Q6  Within-sample selection stability (exploratory only)  dse_reliability.csv
  (all) one tidy row per design×PDK×config×metric               cells.csv

Every statistic is computed per design (never pooled across designs) and per
metric, using the population declared in study.json (passing runs or every
attempted run with a value). Failures are kept: pass fractions sit next to every
median, and ranking puts configurations below the feasibility threshold last.
"""
import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from expr import Unresolved, evaluate
from project import load_settings, strip_comments

ROOT = Path(__file__).resolve().parents[1]
SKIP_STATUS = {"not_run", "running"}


# ---------------------------------------------------------------- statistics

def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def quantile(values, q):
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def mad(values):
    m = statistics.median(values)
    return statistics.median([abs(v - m) for v in values])


def bootstrap_diff(a, b, stat, iterations, rng, alpha):
    """Percentile CI of stat(a*) - stat(b*) resampling each group independently."""
    draws = []
    for _ in range(iterations):
        draws.append(stat(rng.choices(a, k=len(a))) - stat(rng.choices(b, k=len(b))))
    return quantile(draws, alpha / 2), quantile(draws, 1 - alpha / 2)


def bootstrap_ratio(a, b, iterations, rng, alpha):
    """Percentile CI of 100*(median(a*)/median(b*) - 1)."""
    draws = []
    for _ in range(iterations):
        mb = statistics.median(rng.choices(b, k=len(b)))
        if mb == 0:
            continue
        draws.append(100 * (statistics.median(rng.choices(a, k=len(a))) / mb - 1))
    if not draws:
        return None, None
    return quantile(draws, alpha / 2), quantile(draws, 1 - alpha / 2)


def permutation_p(a, b, iterations, rng):
    """Two-sided permutation p-value for a difference in medians (with +1 correction)."""
    observed = abs(statistics.median(a) - statistics.median(b))
    pooled = list(a) + list(b)
    hits = 0
    for _ in range(iterations):
        rng.shuffle(pooled)
        if abs(statistics.median(pooled[:len(a)]) - statistics.median(pooled[len(a):])) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (iterations + 1)


def cliffs_delta(a, b):
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    return (gt - lt) / (len(a) * len(b))


def bh(pvalues):
    """Benjamini-Hochberg q-values (same order as input; None passes through)."""
    indexed = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    q = [None] * len(pvalues)
    running = 1.0
    m = len(indexed)
    for rank in range(m, 0, -1):
        p, i = indexed[rank - 1]
        running = min(running, p * m / rank)
        q[i] = running
    return q


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def pearson(x, y):
    if len(x) < 3:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def spearman(x, y):
    return pearson(ranks(x), ranks(y))


def kendall_tau_b(x, y):
    n = len(x)
    if n < 3:
        return None
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = x[i] - x[j], y[i] - y[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx * dy > 0:
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + ties_x) * (concordant + discordant + ties_y))
    return None if denom == 0 else (concordant - discordant) / denom


# ---------------------------------------------------------------- data loading

def campaign_dir(label):
    return ROOT / "results" / label


def analysis_settings(labels):
    frozen = campaign_dir(labels[0]) / "inputs/configs/study.json"
    settings = strip_comments(json.loads(frozen.read_text())) if frozen.is_file() else load_settings(ROOT)
    return settings["analysis"]


def load_rows(labels):
    rows, seen = [], {}
    for label in labels:
        path = campaign_dir(label) / "runs.csv"
        if not path.is_file():
            raise SystemExit(f"{path} not found; run `study.py collect --label {label}` first.")
        with path.open() as stream:
            for row in csv.DictReader(stream):
                if row["status"] in SKIP_STATUS or row["repeat"] != "1":
                    continue  # unattempted, or an identical-seed repeat (not an independent sample)
                key = (row["design"], row["pdk"], row["configuration"], row["seed"])
                if key in seen:
                    raise SystemExit(f"{key} appears in both {seen[key]} and {label}. Pooling campaigns "
                                     "is only allowed when they do not share design/PDK/config/seed cells.")
                seen[key] = label
                row['_campaign'] = label
                rows.append(row)
    validate_pooling(labels, rows)
    return rows


def add_derived(rows, derived):
    for row in rows:
        def lookup(name):
            value = num(row.get(name))
            if value is None:
                raise Unresolved(name)
            return value
        for name, expression in derived.items():
            try:
                value = evaluate(expression, lookup)
                row[name] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
            except (Unresolved, ZeroDivisionError, ValueError, OverflowError):
                row[name] = None


def passing(row):
    return row.get("study_checks_pass") == "True"


def layout_valid(row):
    """Complete, clean layout whatever its timing. Older runs.csv files without the column fall back to passing."""
    if "layout_valid" not in row or row.get("layout_valid") in ("", None):
        return passing(row)
    return row.get("layout_valid") == "True"


POPULATIONS = {"passing": passing, "layout_valid": layout_valid, "attempted": lambda row: True}


def in_population(row, metric):
    pop = metric.get("population", "passing")
    if pop not in POPULATIONS:
        raise SystemExit(f"Unknown population {pop!r}; use one of {sorted(POPULATIONS)}.")
    return POPULATIONS[pop](row)


def split_excluded(rows, kinds):
    """Drop runs whose failure_kind is excluded (e.g. infrastructure failures): they are not outcomes."""
    kept, dropped = [], []
    for row in rows:
        (dropped if row.get("failure_kind") in kinds else kept).append(row)
    return kept, dropped


def failure_table(rows, dropped):
    """Counts per design x PDK x configuration x failure class (excluded runs included, flagged)."""
    counts = defaultdict(int)
    totals = defaultdict(int)
    for row in rows + dropped:
        key = (row["design"], row["pdk"], row["configuration"])
        totals[key] += 1
        if row.get("study_checks_pass") != "True":
            counts[key + (row.get("failure_class") or row.get("failure_reason") or "unknown",
                          row.get("failure_kind") or "unknown")] += 1
    out = []
    for (d, p, c, cls, kind), n in sorted(counts.items()):
        out.append({"design": d, "pdk": p, "configuration": c, "failure_class": cls, "failure_kind": kind,
                    "runs": n, "attempted": totals[(d, p, c)], "fraction": n / totals[(d, p, c)],
                    "excluded_from_analysis": any(r.get("failure_kind") == kind for r in dropped)})
    return out


def values_of(group, metric):
    out = []
    for row in group:
        if not in_population(row, metric):
            continue
        value = num(row.get(metric["column"]))
        if value is not None:
            out.append(value)
    return out


def better_sign(metric):
    return -1 if metric["direction"] == "min" else 1


def best_names(values, metric):
    """All candidates within the predeclared tolerance of the best value."""
    if not values:
        return []
    absolute = metric.get('tie_tolerance_abs', 0.0)
    relative = metric.get('tie_tolerance_rel', 0.0)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in (absolute, relative)):
        raise ValueError('Tie tolerances must be finite nonnegative numbers.')
    sign = better_sign(metric)
    best = max(values.values(), key=lambda v: sign * v)
    tolerance = max(absolute, relative * abs(best))
    return sorted(c for c, value in values.items() if sign * (best - value) <= tolerance)


def regret_value(chosen, ideal, metric):
    gap = max(0.0, better_sign(metric) * (ideal - chosen))
    return (100 * gap / abs(ideal) if ideal else None) if metric['effect'] == 'relative' else gap


def validate_context(rows):
    """Refuse confounded cells instead of silently pooling hosts or settings."""
    hosts, cells = {}, {}
    for row in rows:
        group = (row['design'], row['pdk'])
        context = tuple(row.get(k, '') for k in ('machine_id', 'threads', 'parallel_jobs',
                        'cpu_model', 'os', 'cpu_affinity', 'cpu_limits', 'process_nice'))
        if group in hosts and hosts[group] != context:
            raise ValueError(f'{group}: mixed machine/thread/CPU-limit contexts; analyze separate campaigns.')
        hosts[group] = context
        cell = (*group, row['configuration'])
        settings = {k: v for k, v in row.items() if k.startswith('knob__') or k in
                    ('clock_period_ns', 'sdc_sha256', 'scl')}
        # Failed launches may lack resolved values; unknown does not overwrite evidence.
        settings = {k: v for k, v in settings.items() if v not in ('', None)}
        previous = cells.setdefault(cell, {})
        if any(k in previous and previous[k] != v for k, v in settings.items()):
            raise ValueError(f'{cell}: configuration changed across samples.')
        previous.update(settings)


def validate_pooling(labels, rows):
    """Conservative provenance checks before combining disjoint campaigns."""
    if len(labels) < 2:
        return
    files, designs, flow, analysis_cfg, tool_identity = {}, {}, None, None, None
    for label in labels:
        dest = campaign_dir(label)
        manifest_path = dest / 'manifest.json'
        frozen_path = dest / 'inputs/configs/study.json'
        if not manifest_path.is_file() or not frozen_path.is_file():
            raise SystemExit('Pooling requires frozen manifests and settings for every campaign.')
        manifest = json.loads(manifest_path.read_text())
        settings = strip_comments(json.loads(frozen_path.read_text()))
        if not manifest.get('flow_commit') or not manifest.get('input_hashes'):
            raise SystemExit('Pooling requires recorded flow and input hashes.')
        if flow is not None and flow != manifest['flow_commit']:
            raise SystemExit('Pooling rejected: different flow commits.')
        flow = manifest['flow_commit']
        if analysis_cfg is not None and analysis_cfg != settings['analysis']:
            raise SystemExit('Pooling rejected: different frozen analysis policies.')
        analysis_cfg = settings['analysis']
        for name, revision in manifest['design_revisions'].items():
            head = revision['head'].get('stdout')
            if not head or (name in designs and designs[name] != head):
                raise SystemExit(f'Pooling rejected: missing/different RTL revision for {name}.')
            designs[name] = head
        root = Path(manifest['repository']['toplevel']['stdout'])
        for path, digest in manifest['input_hashes'].items():
            try:
                relative = Path(path).relative_to(root)
            except ValueError:
                raise SystemExit('Pooling requires portable, repository-local input provenance.')
            if relative.parts[0] in ('experiments', 'results'):
                continue
            key = str(relative)
            if key in files and files[key] != digest:
                raise SystemExit(f'Pooling rejected: changed input {key}.')
            files[key] = digest
        for name in ('flake.lock', 'pdk_hashes.yaml'):
            path = dest / 'inputs/toolchain' / name
            if not path.is_file():
                raise SystemExit('Pooling requires frozen toolchain and PDK pins.')
            contents = path.read_bytes()
            key = 'toolchain/' + name
            if key in files and files[key] != contents:
                raise SystemExit(f'Pooling rejected: different {key}.')
            files[key] = contents
        for env_file in {r.get('environment_file') for r in rows if r.get('_campaign') == label}:
            if not env_file or not (dest / env_file).is_file():
                raise SystemExit('Pooling requires each execution environment record.')
            env = json.loads((dest / env_file).read_text())
            hashes = {name: item.get('sha256') for name, item in env.get('executable_provenance', {}).items()}
            versions = {name: {k: item.get(k) for k in ('returncode', 'stdout', 'stderr')}
                        for name, item in env.items() if isinstance(item, dict) and 'returncode' in item}
            identity = (hashes, versions, env.get('python'))
            if not hashes or (tool_identity is not None and tool_identity != identity):
                raise SystemExit('Pooling rejected: missing/different tool executable provenance.')
            tool_identity = identity


def effect_of(a_median, b_median, metric):
    if metric["effect"] == "relative":
        return None if b_median == 0 else 100 * (a_median / b_median - 1)
    return a_median - b_median


def normalize(value, base_median, metric):
    if metric["effect"] == "relative":
        return None if base_median in (0, None) else value / base_median
    return value - base_median


def fmt(value, digits=4):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return value


def write_table(path, rows, columns=None):
    columns = columns or list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: fmt(v) if isinstance(v, float) else ("" if v is None else v)
                             for k, v in row.items()})


# ---------------------------------------------------------------- analyses

class Study:
    def __init__(self, rows, cfg):
        validate_context(rows)
        self.cfg = cfg
        self.rng = random.Random(cfg["rng_seed"])
        self.alpha = 1 - cfg["confidence"]
        self.baseline = cfg["baseline_config"]
        self.metrics = cfg["metrics"]
        self.cells = defaultdict(list)
        for row in rows:
            self.cells[(row["design"], row["pdk"], row["configuration"])].append(row)
        self.designs = sorted({k[0] for k in self.cells})
        self.pdks = {d: sorted({k[1] for k in self.cells if k[0] == d}) for d in self.designs}
        self.configs = {d: sorted({k[2] for k in self.cells if k[0] == d}) for d in self.designs}
        self.min_n = cfg["min_seeds_per_group"]
        self.warnings = []
        for d in self.designs:
            for p in self.pdks[d]:
                if (d, p, self.baseline) not in self.cells:
                    self.warnings.append(f"{d}/{p}: no baseline configuration {self.baseline}; "
                                         "effects/normalisation are skipped there.")

    def pass_fraction(self, key):
        group = self.cells.get(key, [])
        return sum(passing(r) for r in group) / len(group) if group else None

    # Q1 ------------------------------------------------------------------
    def noise(self):
        rows, floors = [], []
        for d in self.designs:
            for p in self.pdks[d]:
                for m in self.metrics:
                    pooled_ss, pooled_df, rel_terms = 0.0, 0, []
                    for c in self.configs[d]:
                        key = (d, p, c)
                        if key not in self.cells:
                            continue
                        vals = values_of(self.cells[key], m)
                        row = {"design": d, "pdk": p, "configuration": c, "metric": m["column"],
                               "attempted": len(self.cells[key]), "n": len(vals),
                               "pass_fraction": self.pass_fraction(key)}
                        if vals:
                            med = statistics.median(vals)
                            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
                            mean = statistics.fmean(vals)
                            row.update(median=med, mean=mean, sd=sd, iqr=quantile(vals, .75) - quantile(vals, .25),
                                       mad=mad(vals), min=min(vals), max=max(vals),
                                       distinct_values=len(set(vals)),
                                       cv_pct=100 * sd / abs(mean) if mean else None,
                                       robust_cv_pct=100 * 1.4826 * mad(vals) / abs(med) if med else None,
                                       range_pct_of_median=100 * (max(vals) - min(vals)) / abs(med) if med else None)
                            if len(vals) > 1:
                                pooled_ss += sd ** 2 * (len(vals) - 1)
                                pooled_df += len(vals) - 1
                                if mean:
                                    rel_terms.append(((sd / abs(mean)) ** 2, len(vals) - 1))
                        rows.append(row)
                    base = self.cells.get((d, p, self.baseline), [])
                    base_vals = values_of(base, m)
                    floors.append({
                        "design": d, "pdk": p, "metric": m["column"],
                        "baseline_n": len(base_vals),
                        "baseline_sd": statistics.stdev(base_vals) if len(base_vals) > 1 else None,
                        "baseline_cv_pct": (100 * statistics.stdev(base_vals) / abs(statistics.fmean(base_vals))
                                            if len(base_vals) > 1 and statistics.fmean(base_vals) else None),
                        "pooled_within_config_sd": math.sqrt(pooled_ss / pooled_df) if pooled_df else None,
                        "pooled_within_config_cv_pct": (100 * math.sqrt(sum(v * w for v, w in rel_terms) /
                                                                         sum(w for _, w in rel_terms))
                                                        if rel_terms else None),
                        "pooled_df": pooled_df,
                        "seed_insensitive": (len(base_vals) > 1 and len(set(base_vals)) == 1) or None})
        self.floor = {(f["design"], f["pdk"], f["metric"]): f for f in floors}
        return rows, floors

    def noise_unit(self, d, p, m):
        """Seed noise in the metric's effect units (percent for relative, raw for absolute)."""
        f = self.floor.get((d, p, m["column"]), {})
        return f.get("pooled_within_config_cv_pct") if m["effect"] == "relative" else f.get("pooled_within_config_sd")

    # Q2 ------------------------------------------------------------------
    def effects(self):
        out = []
        iters, perms = self.cfg["bootstrap_iterations"], self.cfg["permutation_iterations"]
        for d in self.designs:
            for m in self.metrics:
                family = []
                for p in self.pdks[d]:
                    base_key = (d, p, self.baseline)
                    if base_key not in self.cells:
                        continue
                    b = values_of(self.cells[base_key], m)
                    for c in self.configs[d]:
                        if c == self.baseline or (d, p, c) not in self.cells:
                            continue
                        a = values_of(self.cells[(d, p, c)], m)
                        row = {"design": d, "pdk": p, "configuration": c, "metric": m["column"],
                               "effect_kind": "percent" if m["effect"] == "relative" else "units",
                               "n": len(a), "n_baseline": len(b),
                               "pass_fraction": self.pass_fraction((d, p, c)),
                               "pass_fraction_baseline": self.pass_fraction(base_key)}
                        if row["pass_fraction"] is not None and row["pass_fraction_baseline"] is not None:
                            row["pass_diff_pp"] = 100 * (row["pass_fraction"] - row["pass_fraction_baseline"])
                        if len(a) < self.min_n or len(b) < self.min_n:
                            row["verdict"] = "insufficient_data"
                            family.append(row)
                            continue
                        ma, mb = statistics.median(a), statistics.median(b)
                        eff = effect_of(ma, mb, m)
                        if m["effect"] == "relative":
                            lo, hi = bootstrap_ratio(a, b, iters, self.rng, self.alpha)
                        else:
                            lo, hi = bootstrap_diff(a, b, statistics.median, iters, self.rng, self.alpha)
                        noise = self.noise_unit(d, p, m)
                        row.update(median=ma, median_baseline=mb, effect=eff, ci_low=lo, ci_high=hi,
                                   improvement=None if eff is None else better_sign(m) * eff,
                                   cliffs_delta=cliffs_delta(a, b),
                                   p_value=permutation_p(a, b, perms, self.rng),
                                   noise_sd=noise,
                                   effect_in_noise_sd=(abs(eff) / noise) if eff is not None and noise else None)
                        family.append(row)
                qs = bh([r.get("p_value") for r in family])
                for row, q in zip(family, qs):
                    row["q_value"] = q
                    if row.get("verdict") == "insufficient_data":
                        continue
                    excludes_zero = (row.get("ci_low") is not None and
                                     (row["ci_low"] > 0 or row["ci_high"] < 0))
                    if q is not None and q <= self.cfg["fdr_q"] and excludes_zero:
                        row["verdict"] = "better" if row["improvement"] > 0 else "worse"
                    else:
                        row["verdict"] = "no_clear_difference"
                out += family
        self.effect_rows = out
        return out

    # Q3 ------------------------------------------------------------------
    def variance(self):
        out = []
        for d in self.designs:
            for m in self.metrics:
                # (a) per PDK: one-way decomposition configuration vs seed.
                for p in self.pdks[d]:
                    groups = [values_of(self.cells[(d, p, c)], m) for c in self.configs[d] if (d, p, c) in self.cells]
                    groups = [g for g in groups if len(g) >= 2]
                    out.append(self._oneway(d, p, m, groups))
                # (b) across PDKs on baseline-normalised values: config, PDK×config interaction, seed.
                out.append(self._twoway(d, m))
        return out

    def _oneway(self, d, p, m, groups):
        row = {"design": d, "pdk": p, "metric": m["column"], "scope": "per_pdk",
               "configs": len(groups), "observations": sum(len(g) for g in groups)}
        values = [v for g in groups for v in g]
        if len(groups) < 2 or len(values) < 4:
            return row
        grand = statistics.fmean(values)
        ss_between = sum(len(g) * (statistics.fmean(g) - grand) ** 2 for g in groups)
        ss_within = sum((v - statistics.fmean(g)) ** 2 for g in groups for v in g)
        total = ss_between + ss_within
        df_b, df_w = len(groups) - 1, len(values) - len(groups)
        ms_w = ss_within / df_w if df_w else 0
        row.update(share_configuration_pct=100 * ss_between / total if total else None,
                   share_seed_pct=100 * ss_within / total if total else None,
                   omega_squared=((ss_between - df_b * ms_w) / (total + ms_w)) if total else None,
                   f_statistic=(ss_between / df_b) / ms_w if ms_w else None)
        return row

    def _twoway(self, d, m):
        """Unweighted-cell decomposition of normalised values into configuration, PDK×configuration and seed.

        Main PDK effects are removed by normalising to each PDK's baseline median, so a
        large interaction share means configuration effects do NOT transfer across PDKs.
        """
        row = {"design": d, "pdk": "ALL", "metric": m["column"], "scope": "across_pdks_normalised"}
        cells = {}
        for p in self.pdks[d]:
            base = values_of(self.cells.get((d, p, self.baseline), []), m)
            if len(base) < 1:
                continue
            bm = statistics.median(base)
            for c in self.configs[d]:
                vals = [normalize(v, bm, m) for v in values_of(self.cells.get((d, p, c), []), m)]
                vals = [v for v in vals if v is not None]
                if len(vals) >= 2:
                    cells[(p, c)] = vals
        pdks = sorted({p for p, _ in cells})
        configs = [c for c in self.configs[d] if all((p, c) in cells for p in pdks)]
        if len(pdks) < 2 or len(configs) < 2:
            return row
        means = {(p, c): statistics.fmean(cells[(p, c)]) for p in pdks for c in configs}
        grand = statistics.fmean(means.values())
        cfg_mean = {c: statistics.fmean(means[(p, c)] for p in pdks) for c in configs}
        pdk_mean = {p: statistics.fmean(means[(p, c)] for c in configs) for p in pdks}
        n_h = statistics.harmonic_mean([len(cells[(p, c)]) for p in pdks for c in configs])
        ss_cfg = n_h * len(pdks) * sum((cfg_mean[c] - grand) ** 2 for c in configs)
        ss_pdk = n_h * len(configs) * sum((pdk_mean[p] - grand) ** 2 for p in pdks)
        ss_int = n_h * sum((means[(p, c)] - cfg_mean[c] - pdk_mean[p] + grand) ** 2 for p in pdks for c in configs)
        ss_seed = sum((v - means[(p, c)]) ** 2 for p in pdks for c in configs for v in cells[(p, c)])
        total = ss_cfg + ss_pdk + ss_int + ss_seed
        if total:
            row.update(configs=len(configs), pdks=len(pdks),
                       observations=sum(len(cells[(p, c)]) for p in pdks for c in configs),
                       share_configuration_pct=100 * ss_cfg / total,
                       share_pdk_residual_pct=100 * ss_pdk / total,
                       share_pdk_x_configuration_pct=100 * ss_int / total,
                       share_seed_pct=100 * ss_seed / total)
        return row

    # Q4 ------------------------------------------------------------------
    def knob_effects(self):
        out, importance = [], []
        knob_cols = sorted({k for rows in self.cells.values() for r in rows for k in r if k.startswith("knob__")})
        for d in self.designs:
            for p in self.pdks[d]:
                base_rows = self.cells.get((d, p, self.baseline), [])
                if not base_rows:
                    continue
                rows = [r for c in self.configs[d] for r in self.cells.get((d, p, c), [])]
                for m in self.metrics:
                    bm_vals = values_of(base_rows, m)
                    if not bm_vals:
                        continue
                    bm = statistics.median(bm_vals)
                    for col in knob_cols:
                        levels = {r.get(col, "") for r in rows}
                        if len(levels) < 2:
                            continue
                        base_level = base_rows[0].get(col, "")
                        numeric = [num(level) for level in levels]
                        knob = col[len("knob__"):]
                        if all(v is not None for v in numeric) and len(levels) > 6:
                            pairs = [(num(r[col]), normalize(num(r.get(m["column"])), bm, m)) for r in rows
                                     if num(r.get(m["column"])) is not None and in_population(r, m)]
                            pairs = [(x, y) for x, y in pairs if y is not None]
                            if len(pairs) < 5:
                                continue
                            rho = spearman([x for x, _ in pairs], [y for _, y in pairs])
                            out.append({"design": d, "pdk": p, "metric": m["column"], "knob": knob,
                                        "kind": "continuous", "n": len(pairs), "spearman_rho": rho,
                                        "improves_with_increase": None if rho is None else (rho * better_sign(m) > 0)})
                            importance.append({"design": d, "pdk": p, "metric": m["column"], "knob": knob,
                                               "score": abs(rho) if rho is not None else None,
                                               "score_kind": "|spearman_rho|"})
                            continue
                        best = None
                        for level in sorted(levels):
                            subset = [r for r in rows if r.get(col, "") == level]
                            vals = values_of(subset, m)
                            entry = {"design": d, "pdk": p, "metric": m["column"], "knob": knob, "kind": "levels",
                                     "level": level, "is_baseline_level": level == base_level,
                                     "runs": len(subset), "n": len(vals),
                                     "pass_fraction": sum(passing(r) for r in subset) / len(subset) if subset else None}
                            if vals:
                                eff = effect_of(statistics.median(vals), bm, m)
                                entry.update(median=statistics.median(vals), effect_vs_baseline=eff,
                                             improvement=None if eff is None else better_sign(m) * eff)
                                if level != base_level and eff is not None and (best is None or abs(eff) > abs(best[1])):
                                    best = (level, eff)
                            out.append(entry)
                        if best:
                            noise = self.noise_unit(d, p, m)
                            importance.append({"design": d, "pdk": p, "metric": m["column"], "knob": knob,
                                               "score": abs(best[1]), "score_kind": "max |effect| over levels",
                                               "strongest_level": best[0], "strongest_effect": best[1],
                                               "in_noise_sd": abs(best[1]) / noise if noise else None})
        groups = defaultdict(list)
        for row in importance:
            groups[(row["design"], row["pdk"], row["metric"], row["score_kind"])].append(row)
        for rows in groups.values():
            rows.sort(key=lambda r: -(r["score"] or 0))
            for i, row in enumerate(rows, 1):
                row["rank"] = i
        return out, importance

    # Q5 ------------------------------------------------------------------
    def rankings(self):
        out = []
        self.rank_table = {}
        iters = self.cfg["bootstrap_iterations"]
        threshold = self.cfg["min_pass_fraction_for_ranking"]
        for d in self.designs:
            for p in self.pdks[d]:
                for m in self.metrics:
                    entries = []
                    for c in self.configs[d]:
                        key = (d, p, c)
                        if key not in self.cells:
                            continue
                        vals = values_of(self.cells[key], m)
                        pf = self.pass_fraction(key)
                        entries.append({"configuration": c, "values": vals, "pass_fraction": pf,
                                        "feasible": pf is not None and pf >= threshold and len(vals) >= self.min_n,
                                        "median": statistics.median(vals) if vals else None})
                    sign = better_sign(m)
                    feasible = sorted((e for e in entries if e['feasible']), key=lambda e: -sign * e['median'])
                    rest = sorted((e for e in entries if not e['feasible']),
                                  key=lambda e: (-(e['pass_fraction'] or 0), e['configuration']))
                    observed = {e['configuration']: e['median'] for e in feasible}
                    co_best = best_names(observed, m)
                    wins = defaultdict(float)
                    if feasible:
                        for _ in range(iters):
                            draws = {e['configuration']: statistics.median(self.rng.choices(e['values'], k=len(e['values'])))
                                     for e in feasible}
                            winners = best_names(draws, m)
                            for c in winners:
                                wins[c] += 1 / len(winners)
                    base_median = next((e['median'] for e in entries if e['configuration'] == self.baseline), None)
                    for position, e in enumerate(feasible + rest, 1):
                        # Equal observed medians share a rank; practical co-best membership is separate.
                        rank = (1 + sum(sign * other['median'] > sign * e['median'] for other in feasible)
                                if e['feasible'] else position)
                        out.append({'design': d, 'pdk': p, 'metric': m['column'], 'configuration': e['configuration'],
                                    'rank': rank, 'feasible': e['feasible'], 'pass_fraction': e['pass_fraction'],
                                    'n': len(e['values']), 'median': e['median'],
                                    'normalised_median': normalize(e['median'], base_median, m)
                                    if e['median'] is not None and base_median is not None else None,
                                    'observed_co_best': e['configuration'] in co_best,
                                    'p_best': wins[e['configuration']] / iters if e['feasible'] else None,
                                    'p_best_definition': 'bootstrap selection share; tied winners split credit'})
                    self.rank_table[(d, p, m['column'])] = {e['configuration']: e for e in entries}
        return out

    def transfer(self):
        out = []
        iters = min(self.cfg['bootstrap_iterations'], 500)
        for d in self.designs:
            for m in self.metrics:
                sign = better_sign(m)
                pdks = self.pdks[d]
                for i, pa in enumerate(pdks):
                    for pb in pdks[i + 1:]:
                        ta = self.rank_table.get((d, pa, m['column']), {})
                        tb = self.rank_table.get((d, pb, m['column']), {})
                        va = {c: e['median'] for c, e in ta.items() if e['feasible']}
                        vb = {c: e['median'] for c, e in tb.items() if e['feasible']}
                        ba, bb = best_names(va, m), best_names(vb, m)
                        common = sorted(set(va) & set(vb))
                        row = {'design': d, 'metric': m['column'], 'pdk_a': pa, 'pdk_b': pb,
                               'common_feasible_configs': len(common),
                               'best_a': ';'.join(ba), 'best_b': ';'.join(bb),
                               'same_best': set(ba) == set(bb) if ba and bb else None,
                               'best_sets_overlap': bool(set(ba) & set(bb)) if ba and bb else None,
                               'regret_kind': 'percent' if m['effect'] == 'relative' else 'units',
                               'transfer_tie_policy': 'uniform choice among source co-best; regret undefined if any fails'}

                        def evaluate_transfer(winners, target, target_values):
                            if not winners:
                                return 'no_source_winner', None, ''
                            missing = [c for c in winners if c not in target]
                            failed = [c for c in winners if c in target and not target[c]['feasible']]
                            if missing:
                                return 'missing_destination', None, ';'.join(missing)
                            if failed:
                                status = 'infeasible' if len(failed) == len(winners) else 'partly_infeasible'
                                return status, None, ';'.join(failed)
                            ideal = max(target_values.values(), key=lambda v: sign * v)
                            regrets = [regret_value(target[c]['median'], ideal, m) for c in winners]
                            return 'feasible', (statistics.fmean(regrets) if all(r is not None for r in regrets) else None), ''

                        status, regret, bad = evaluate_transfer(ba, tb, vb)
                        row.update(transfer_a_to_b_status=status, regret_on_b_using_best_of_a=regret,
                                   unavailable_winners_on_b=bad)
                        status, regret, bad = evaluate_transfer(bb, ta, va)
                        row.update(transfer_b_to_a_status=status, regret_on_a_using_best_of_b=regret,
                                   unavailable_winners_on_a=bad)
                        xa, xb = [sign * va[c] for c in common], [sign * vb[c] for c in common]
                        tau = kendall_tau_b(xa, xb)
                        draws = []
                        if len(common) >= 3:
                            for _ in range(iters):
                                ra = [sign * statistics.median(self.rng.choices(ta[c]['values'], k=len(ta[c]['values']))) for c in common]
                                rb = [sign * statistics.median(self.rng.choices(tb[c]['values'], k=len(tb[c]['values']))) for c in common]
                                t = kendall_tau_b(ra, rb)
                                if t is not None:
                                    draws.append(t)
                        row.update(kendall_tau=tau, spearman_rho=spearman(xa, xb),
                                   tau_ci_low=quantile(draws, self.alpha / 2) if draws else None,
                                   tau_ci_high=quantile(draws, 1 - self.alpha / 2) if draws else None,
                                   correlation_population='common feasible configurations only')
                        out.append(row)
        return out

    # Q6 ------------------------------------------------------------------
    def dse_reliability(self):
        """Descriptive within-sample stability, NOT out-of-sample DSE accuracy."""
        out = []
        trials = self.cfg['single_run_trials']
        for d in self.designs:
            for p in self.pdks[d]:
                for m in self.metrics:
                    table = self.rank_table.get((d, p, m['column']), {})
                    sign = better_sign(m)
                    for k in self.cfg['single_run_budgets']:
                        pool = {c: e for c, e in table.items() if e['feasible'] and len(e['values']) >= k}
                        if len(pool) < 2:
                            continue
                        reference = {c: e['median'] for c, e in pool.items()}
                        reference_best = best_names(reference, m)
                        ideal = max(reference.values(), key=lambda v: sign * v)
                        correct, regrets = 0, []
                        for _ in range(trials):
                            draws = {c: statistics.median(self.rng.sample(e['values'], k)) for c, e in pool.items()}
                            pick = self.rng.choice(best_names(draws, m))
                            correct += pick in reference_best
                            regret = regret_value(reference[pick], ideal, m)
                            if regret is not None:
                                regrets.append(regret)
                        out.append({'design': d, 'pdk': p, 'metric': m['column'], 'seeds_per_config': k,
                                    'candidate_configs': len(pool), 'observed_best_set': ';'.join(reference_best),
                                    'p_pick_observed_best': correct / trials,
                                    'chance_level': len(reference_best) / len(pool),
                                    'mean_regret': statistics.fmean(regrets) if regrets else None,
                                    'p90_regret': quantile(regrets, .9) if regrets else None,
                                    'regret_kind': 'percent' if m['effect'] == 'relative' else 'units',
                                    'evaluation': 'within-sample; conditional on observed feasibility; not held-out validation'})
        return out

    # tidy --------------------------------------------------------------
    def cells_table(self):
        out = []
        for (d, p, c), group in sorted(self.cells.items()):
            for m in self.metrics:
                vals = values_of(group, m)
                base = values_of(self.cells.get((d, p, self.baseline), []), m)
                bm = statistics.median(base) if base else None
                med = statistics.median(vals) if vals else None
                reasons = defaultdict(int)
                for r in group:
                    if r.get("failure_reason"):
                        reasons[r["failure_reason"]] += 1
                out.append({"design": d, "pdk": p, "configuration": c, "metric": m["column"],
                            "attempted": len(group), "pass_fraction": self.pass_fraction((d, p, c)),
                            "n": len(vals), "median": med,
                            "q25": quantile(vals, .25) if vals else None, "q75": quantile(vals, .75) if vals else None,
                            "normalised_median": normalize(med, bm, m) if med is not None and bm is not None else None,
                            "failure_reasons": ";".join(f"{k}:{v}" for k, v in sorted(reasons.items()))})
        return out


# ---------------------------------------------------------------- report

def report(study, floors, effects, variance, importance, transfer, reliability, labels, out):
    lines = [f"# Exploratory flow study: {', '.join(labels)}", '',
             'Initial-global-routing seed variation only. Read sample counts, failures and uncertainty together.', '',
             'Fmax and area-delay inferred from worst slack are disabled. Passing means the declared study checks, not full signoff.', '',
             'Bootstrap selection shares are descriptive. A nonsignificant difference is not evidence of equivalence.']
    lines += [''] + [f'- {w}' for w in study.warnings]
    for d in study.designs:
        lines += ['', f'## {d}', '', f"PDKs: {', '.join(study.pdks[d])}. See cells.csv/noise.csv for distributions, "
                  'effects.csv for baseline comparisons, and ranking.csv for tie-aware bootstrap selection shares.', '',
                  'Variance and knob summaries are descriptive; a PDK-by-configuration interaction alone does not prove rank reversal.', '',
                  '### Transfer of source-selected winners', '',
                  '| Metric | PDK pair | Source co-best | Destination status | Regret |',
                  '| --- | --- | --- | --- | --- |']
        for r in transfer:
            if r['design'] != d:
                continue
            for source, target, best, status, regret in [
                (r['pdk_a'], r['pdk_b'], r['best_a'], r['transfer_a_to_b_status'], r['regret_on_b_using_best_of_a']),
                (r['pdk_b'], r['pdk_a'], r['best_b'], r['transfer_b_to_a_status'], r['regret_on_a_using_best_of_b'])]:
                units = '%' if r['regret_kind'] == 'percent' else ' units'
                lines.append(f"| {r['metric']} | {source} → {target} | {best or 'none'} | {status} | "
                             f"{str(fmt(regret)) + units if regret is not None else 'undefined'} |")
        lines += ['', 'Rank correlations use common feasible candidates only. Transfer outcomes above retain source winners that fail on the destination.', '',
                  '### Within-sample selection stability', '',
                  'dse_reliability.csv retains its historical filename, but measures agreement with the observed median ranking, '
                  'conditional on observed feasibility. It is not a probability of selecting the true optimum. '
                  'Use independent confirmation seeds to assess decisions after tuning.', '',
                  '| Metric | PDK | Seeds/config | Agreement with observed best set | Chance |',
                  '| --- | --- | --- | --- | --- |']
        for r in reliability:
            if r['design'] == d:
                lines.append(f"| {r['metric']} | {r['pdk']} | {r['seeds_per_config']} | "
                             f"{r['p_pick_observed_best']:.3f} | {r['chance_level']:.3f} |")
    (out / 'report.md').write_text('\n'.join(lines) + '\n')


def append_failure_section(path, rows, dropped):
    """Per design x PDK x configuration: passes, valid layouts and the cause of every failure."""
    cells = defaultdict(lambda: {"n": 0, "pass": 0, "layout": 0, "causes": defaultdict(int)})
    for row in rows + dropped:
        cell = cells[(row["design"], row["pdk"], row["configuration"])]
        cell["n"] += 1
        cell["pass"] += row.get("study_checks_pass") == "True"
        cell["layout"] += layout_valid(row)
        if row.get("study_checks_pass") != "True":
            cls = row.get("failure_class") or row.get("failure_reason") or "unknown"
            cell["causes"][f"{cls} ({row.get('failure_kind') or '?'})"] += 1
    lines = ["", "## Outcomes and failure causes", "",
             "Pass = every required check including timing at the matched corner. Layout valid = complete, clean layout "
             "(GDS, routing DRC, layout DRC, LVS) whatever its timing; area/wirelength use these runs. "
             "Kinds: design = outcome of the configuration; tool = EDA tool crash; infra = environment failure "
             "(excluded from all statistics).", "",
             "| design | pdk | configuration | runs | pass | layout valid | failure causes |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for (d, p, c), cell in sorted(cells.items()):
        causes = ", ".join(f"{k}: {v}" for k, v in sorted(cell["causes"].items(), key=lambda kv: -kv[1])) or "-"
        lines.append(f"| {d} | {p} | {c} | {cell['n']} | {cell['pass']} | {cell['layout']} | {causes} |")
    with path.open("a") as stream:
        stream.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", action="append", required=True, help="Campaign label (repeatable).")
    parser.add_argument("--out", help="Output directory (default: results/<first label>/analysis).")
    parser.add_argument("--fast", action="store_true", help="Use 200 resamples (for quick looks; not for reporting).")
    parser.add_argument("--population", choices=sorted(POPULATIONS),
                        help="Use this population for every metric (e.g. layout_valid for campaigns frozen before it "
                             "became the default). Recorded in analysis_settings.json.")
    args = parser.parse_args()
    cfg = dict(analysis_settings(args.label))
    cfg.setdefault("exclude_failure_kinds", ["infra"])
    if args.population:
        cfg["metrics"] = [dict(m, population=args.population) for m in cfg["metrics"]]
        cfg["population_override"] = args.population
    if args.fast:
        cfg.update(bootstrap_iterations=200, permutation_iterations=200, single_run_trials=200)
    unsafe = {'achieved_period_ns', 'fmax_est_mhz', 'area_delay_um2ns'}
    removed = sorted(unsafe & (set(cfg.get('derived', {})) | {m['column'] for m in cfg['metrics']}))
    cfg['derived'] = {k: v for k, v in cfg.get('derived', {}).items() if k not in unsafe}
    cfg['metrics'] = [m for m in cfg['metrics'] if m['column'] not in unsafe]
    cfg['disabled_legacy_metrics'] = removed
    rows = load_rows(args.label)
    rows, dropped = split_excluded(rows, set(cfg.get("exclude_failure_kinds", ["infra"])))
    if not rows:
        raise SystemExit("No attempted runs to analyse.")
    add_derived(rows, cfg.get("derived", {}))
    out = Path(args.out) if args.out else campaign_dir(args.label[0]) / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    study = Study(rows, cfg)
    if dropped:
        study.warnings.append(f"{len(dropped)} run(s) excluded as {sorted({r.get('failure_kind') for r in dropped})} "
                              "failures (not outcomes of the configuration); see failures.csv. "
                              "Re-run them with `study.py run --label ... --retry-infra`.")
    if removed:
        study.warnings.append('Disabled unsupported legacy slack-derived metrics: ' + ', '.join(removed))
    if any(not row.get('machine_id') or not row.get('threads') for row in rows):
        study.warnings.append('Some host/thread provenance is missing; cross-campaign pooling needs full evidence.')
    noise_rows, floors = study.noise()
    effects = study.effects()
    variance = study.variance()
    knob_rows, importance = study.knob_effects()
    ranking = study.rankings()
    transfer = study.transfer()
    reliability = study.dse_reliability()
    for name, table in [("failures", failure_table(rows, dropped)), ("cells", study.cells_table()), ("noise", noise_rows), ("noise_floor", floors),
                        ("effects", effects), ("variance", variance), ("knob_effects", knob_rows),
                        ("knob_importance", importance), ("ranking", ranking), ("transfer", transfer),
                        ("dse_reliability", reliability)]:
        write_table(out / f"{name}.csv", table, None if table else ["design"])
    (out / "analysis_settings.json").write_text(json.dumps({"labels": args.label, **cfg}, indent=2) + "\n")
    report(study, floors, effects, variance, importance, transfer, reliability, args.label, out)
    append_failure_section(out / "report.md", rows, dropped)
    for w in study.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"Wrote analysis for {len(rows)} attempted runs to {out} (start with report.md).")


if __name__ == "__main__":
    main()
