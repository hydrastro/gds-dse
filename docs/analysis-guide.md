# Analysing the flow study

The experimental unit is a run of one upstream design, PDK, flow configuration
and routing seed. Analyse each design separately. Keep the RTL and flow inputs
fixed while comparing general flow settings. The injected seed randomizes only
the initial global-routing net order; it does not sample all flow randomness.

## Start with the observations

`runs.csv` retains planned jobs, failures, metrics, seeds, machine identity and
resource settings. `summary.csv` reports attempted counts, pass fractions with
Wilson intervals, and descriptive statistics. Compare raw distributions before
interpreting rankings. Do not count a same-seed repeat as another independent
seed observation. Unattempted jobs are not failures, but must remain visible.

Area and wirelength comparisons use passing runs by default; slack and runtime
use available observations from attempted runs. Always show the denominator and
failure rate alongside each result. These are conditional populations, so a
small median on passing runs does not excuse a high failure rate. Infrastructure
failures and design-space failures need separate interpretation.

Ten seeds per configuration is a reasonable pilot. It does not guarantee precise
confidence intervals, tail probabilities or a publication-ready conclusion.
Use the observed spread and a scientifically meaningful effect size to plan
confirmation. New configurations selected after viewing pilot results need fresh
confirmation seeds. Avoid pooling reused baseline seeds as independent data.

## Output tables

| Table | Meaning and limits |
| --- | --- |
| cells.csv | Observed counts, medians, quartiles, pass fractions and failure reasons |
| noise.csv | Per-cell spread under the chosen initial-routing intervention |
| noise_floor.csv | Baseline and pooled within-configuration spread; not all sources of flow variation |
| effects.csv | Baseline comparisons with bootstrap intervals, permutation p-values and BH q-values |
| variance.csv | Descriptive configuration/PDK-interaction/within-cell variance decomposition |
| knob_effects.csv, knob_importance.csv | Descriptive associations between knob levels and outcomes |
| ranking.csv | Feasibility, observed median ranks and tie-aware bootstrap selection shares |
| transfer.csv | Source-selected winners evaluated on the destination, plus conditional rank correlations |
| dse_reliability.csv | Historical filename for within-sample selection stability; not held-out reliability |

Effects use `better` or `worse` only when both the bootstrap interval excludes
zero and the adjusted q-value passes the configured threshold. Otherwise the
verdict is `no_clear_difference` or `insufficient_data`. A nonsignificant test
is not evidence of equivalence. Bootstrap/permutation assumptions and small-sample
uncertainty still need scrutiny; the scripts do not automate scientific validity.

Knob summaries pool observations at each level. They are not automatically
isolated causal main effects when other knobs differ. In a 2x2 factorial, inspect
both density contrasts (one at each synthesis strategy), both strategy contrasts,
and their difference. A large PDK-by-configuration interaction can reflect a
change in effect magnitude without any ranking reversal.

## Ranking and ties

A configuration is eligible for ranking when its observed pass fraction is at
least `min_pass_fraction_for_ranking` and its available metric count meets
`min_seeds_per_group`. The defaults remain 0.8 and 3, respectively. These are
exploratory screening rules, not proofs of reliability. Inspect the pass-fraction
interval, especially with ten seeds; 8/10 passing is not equivalent to a demonstrated
80% long-run success probability.

`p_best` is a bootstrap selection share conditional on these observed candidates
and the data population. Each tied winner receives an equal share of that draw's
credit. One eligible candidate receives 1, without implying certainty about the
underlying optimum. Equal observed medians share a rank. `observed_co_best`
additionally respects any predeclared practical tolerance.

Per-metric `tie_tolerance_abs` and `tie_tolerance_rel` default to zero. A candidate
is co-best when its distance from the best is at most the larger of the absolute
tolerance and `tie_tolerance_rel * abs(best)`. Relative tolerance is a fraction
(0.01 means 1%). Set these before looking at results; they are not an equivalence
test. The exact policy is saved in the analysis settings.

## Transfer between PDKs

Winners are selected from all eligible source-PDK configurations first. Their
outcome is then checked on the destination. `best_a` and `best_b` contain all
observed co-best names, separated by semicolons. Both transfer directions are
reported even if there are fewer than three common feasible configurations.

`transfer_a_to_b_status` (and the reverse field) distinguishes feasible, infeasible,
partly infeasible, missing destination data, and no source winner. Regret is
undefined when any source co-best candidate is missing or ineligible at the
destination; the names remain visible. When all transfer, regret averages over
an explicitly uniform choice among source co-best candidates and compares with
the best eligible destination median. Relative regret uses percent; absolute
metrics retain their units.

Kendall/Spearman correlations describe only configurations feasible on both PDKs.
They do not replace the transfer outcome. Identical best sets and overlapping
best sets are reported separately. Compare relative changes within each PDK and
state its library, timing corner, load and clock choices. These jointly define
the platform; differences are not attributable solely to process geometry.

## Selection stability is exploratory

`dse_reliability.csv` now reports `observed_best_set` and `p_pick_observed_best`.
For each sampling budget, only candidates with enough observations enter both
the resampling pool and the reference ranking. Tied sample winners are selected
uniformly. Chance agreement is the size of the observed best set divided by the
candidate count, so an all-tie case does not manufacture a unique winner.

The reference and resamples still use the same observations and eligibility
filter. This is deliberately labeled within-sample, conditional selection
stability. It is not an estimate of picking a true optimum, and it is not held-out
validation. To assess decisions made after tuning, predefine the selection rule
and evaluate its selected candidates on independent confirmation seeds, including
failures. No synthetic resampling curve substitutes for those new runs.

## Timing, power and signoff scope

The default analysis no longer computes Fmax, achieved period or area-delay from
`clock_period - worst_setup_slack`. The SDC includes I/O paths with delays that
scale with the clock; the formula is not a general frequency bound. The analyser
also disables these named legacy derived metrics when reading old frozen settings,
records the change and emits a warning. Use fixed timing constraints and signed
setup/hold slack until a separate path-aware frequency characterization exists.

Power remains collected when available, but is not a default ranking metric.
Workload-level power claims require an explicit switching-activity methodology.

`study_checks_pass` refers to the declared checks and selected nominal-RC timing
corners. Some violation classes are reported rather than required. It is not a
fabrication/signoff certificate. Missing any explicitly selected corner now makes
that aggregated metric unknown rather than silently using the available subset.

## Combining campaigns and comparing runtime

Overlapping design/PDK/configuration/seed cells cannot be pooled. Disjoint samples
also need compatible provenance: frozen flow and RTL revisions, shared input
hashes, toolchain/PDK pins, executable evidence and analysis settings. The analyser
rejects missing or conflicting evidence rather than guessing. Even comment-only
changes to hashed shared files can require separate analysis under this conservative
policy. Configure all desired PDK baselines before preparing campaigns to pool.

Within each design/PDK comparison, machine, threads, parallel jobs, affinity, CPU
quota context and nice level must agree. Within a configuration, the available
clock, library, SDC and knob values must agree. Unknown historical provenance
cannot certify comparability. The cgroup recorder observes visible cgroup-v2
limits, not all possible scheduler constraints or competing workloads.

Keep resource limits fixed across a campaign. Quotas and contention affect wall
time and may cause timeouts. A timeout during deliberate throttling is not proof
that a configuration cannot route. Preserve that distinction in reporting.

## Commands

```bash
python3 scripts/study.py collect --label pilot-001
python3 scripts/study.py summarize --label pilot-001
python3 scripts/analyze.py --label pilot-001
# Optional, in your separate plotting environment with matplotlib:
python3 scripts/plot.py --label pilot-001
python3 scripts/plot_analysis.py --label pilot-001
```

Use repeated `--label` options only for compatible disjoint campaigns. `--fast`
reduces resampling for inspection. The analysis outputs its effective settings
and explanatory report beside the CSV tables. Old plotting environments need
the updated plot_analysis.py for the renamed selection-stability column.
