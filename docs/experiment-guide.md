# Additional design-integration notes (including optional ASCON)

Use [running-study.md](running-study.md) for the current four-design workflow. This guide retains optional ASCON integration examples. Complete the repository/submodule setup in the root README first. Run the flow
commands from the project root inside `nix-shell tools/librelane/shell.nix`.
The first study uses one ASCON architecture, one configuration and three PDKs.
Its question is how sensitive final implementation results are to a controlled
initial-global-routing seed. Three configurations are a later extension.

## 1. Validate the existing design

Record the exact ASCON algorithm/version, datapath width, rounds per cycle,
parameters and interface. Use the matching known-answer vectors and your existing
functional testbench. Check reset, handshakes, boundary lengths and repeated
operations as appropriate to the core. Save the test command, simulator version
and logs. A generic simulation command cannot be supplied without seeing your
repository's test setup.

The design manifest lists synthesis sources only, with packages before users.
Exclude testbenches, simulation-only code and FPGA vendor primitives. An FPGA
memory primitive needs a suitable ASIC mapping or synthesizable replacement.
Hard macros need explicit timing/physical models and configuration beyond this
standard-cell, single-clock starter. Run functional checks again after RTL or
wrapper changes. Where available, also use RTL-to-netlist equivalence or
gate-level functional checks; LVS alone does not establish that equivalence.

## 2. Choose and review the baseline constraints

The shared SDC (`configs/constraints/single_clock.sdc`) has one real clock and times
all non-clock inputs, including reset. It follows LibreLane's `base.sdc`, and every
number comes from each PDK's own LibreLane defaults in `configs/platforms.json`:
I/O delay 20% of the period, clock uncertainty 0.25 ns, clock transition 0.15 ns,
5% derating, and the PDK's driving cell and output load (see
[pdk-values.md](pdk-values.md)). These are the flow's out-of-the-box assumptions,
not ASCON or AES specifications. The design manifest's clock is only a fallback:
the calibration campaign chooses a pilot reference from a complete observed setup-passing sweep, with a documented headroom policy. This is not measured Fmax or a guaranteed timing margin; validate the chosen reference with full flows. Variants then use
`{"CLOCK_PERIOD": {"$scale": x}}` relative to that reference.

Replace the assumptions with your interface requirements. Review asynchronous
reset recovery/removal, generated clocks, exceptions and clock domains explicitly;
the single-clock template does not solve those cases automatically. Check final
reports for unconstrained endpoints and unpropagated clocks. Avoid adding false
paths simply to hide timing violations.

The shared floorplan utilization is 50%, and C0 placement density is 60%. These
are separate knobs: floorplan utilization influences core size, while placement
density constrains the placement target. Check feasibility for your core and its
PDN/pin needs. Make necessary changes during the pilot, then freeze the settings.

Use the same clock/constraints for all variants and seeds within each PDK. A
common target across PDKs is helpful when meaningful. If per-PDK targets differ,
record them in the design–PDK overrides and treat the results as different
operating conditions. Raw slack values then do not represent equal timing tasks.

## 3. Run synthesis first

```bash
python3 scripts/study.py prepare --experiment experiments/ascon/ascon-synthesis.json --label ascon-synth-001
python3 scripts/study.py run --label ascon-synth-001
```

Read `results/ascon-synth-001/jobs/ascon__sky130A__C0__s00001__r1/console.log`
and the numbered synthesis reports beneath its `runs/run/` directory. Verify
the intended top/parameters, nonconstant outputs, expected registers/logic,
and absence of unexpected latches or black boxes. This short run stops at
`Yosys.Synthesis`; the downstream unmapped-cell and synthesis checkers still
have to pass in the full flow.

Resolve source-order/include/frontend issues now. If the default frontend
cannot parse your SystemVerilog, set the design manifest's `use_slang` to true
and revalidate. Use a new campaign label after changing inputs.

## 4. Complete one baseline on each PDK

```bash
python3 scripts/study.py prepare --experiment experiments/ascon/ascon-pilot.json --label ascon-pilot-001
python3 scripts/study.py run --label ascon-pilot-001
python3 scripts/study.py collect --label ascon-pilot-001
```

This performs three complete Classic flow attempts, targeting `sky130A`,
`gf180mcuD` and `ihp-sg13g2`. The sequence includes synthesis, floorplanning/PDN,
placement, clock-tree synthesis, global/detailed routing, extraction, final STA,
GDS stream-out and enabled physical verification.

Each job's artifacts are under `results/<label>/jobs/<run_id>/runs/run/`.
The `final/` directory holds final views; `runs.csv` records exact GDS paths.
Inspect the GDS in KLayout and verify pins, dimensions, clock/power connectivity
and expected structure. Check setup AND hold timing at the selected corners,
routing errors, layout DRC, LVS and other flow checker failures.

A GDS file can be written even when a deferred checker fails. GDS availability
and passing the study checks are separate columns. The result is a hardened
block, not a padframe/package or proof of complete foundry signoff. PDKs can use
different available verification tools/decks; preserve their resolved settings.

Fix infeasible floorplan/clock/configuration choices during bring-up. Do not tune
individual study seeds separately. Measure actual memory, runtime and disk use
here before choosing a batch budget; initial PDK downloads should happen before
formal runtime comparisons.

## 5. Audit seed application and same-seed repeatability

```bash
python3 scripts/study.py prepare --experiment experiments/ascon/ascon-seed-check.json --label ascon-seed-check-001
python3 scripts/study.py run --label ascon-seed-check-001
python3 scripts/study.py collect --label ascon-seed-check-001
```

This runs seeds 1 and 2 twice each on SKY130. Extend the pilot experiment to
other PDKs before making repeatability claims about all three.

The custom step in `scripts/flow_entry.py` inserts:

```tcl
set_global_routing_random -seed $::env(STUDY_GRT_SEED) -capacities_perturbation_percentage 0
```

It randomizes net ordering at the initial global-routing call while leaving
edge-capacity perturbation disabled. Nonzero seeds enable randomization. This
does not vary synthesis, placement, detailed-routing seeds or later rerouting
defaults. Describe the intervention with that exact scope.

The upstream Tcl file must match its reviewed hash. The generated copy,
`seed_intent.json`, a log execution marker and `after_grt.guide` are saved in the
numbered `study-seededglobalrouting` step. Inspect them to confirm the intended
seed was actually applied. Compare initial guides and final outcomes.

Same-seed repetitions measure residual repeatability; different seeds measure
sensitivity to this intervention. Investigate same-seed differences before
attributing all spread to seed changes. Identical-seed repeats are not extra
independent seed samples. File hashes alone can change because of ordering or
timestamps, so inspect meaningful route/metric differences.

Different seeds may lead to identical final results. Later stages can erase an
initial-routing difference, or the core may be insensitive. Area may barely
change because synthesis and placement occur before this intervention. Audit
the implementation before interpreting zero variation as a general conclusion.

## 6. Freeze the baseline and run 30 study jobs

Commit the reviewed settings and source/submodule changes before preparing the
formal campaign. If you changed the RTL submodule, its changes need their own
commit, and the parent needs the updated pointer. Publish needed design commits
to their remote before sharing the study. Keep the recorded parent revision
throughout the run.

```bash
git add .
git commit -m "Freeze ASCON baseline experiment"
python3 scripts/study.py prepare --experiment experiments/ascon/ascon-baseline.json --label ascon-baseline-001
python3 scripts/study.py run --label ascon-baseline-001
python3 scripts/study.py collect --label ascon-baseline-001
python3 scripts/study.py summarize --label ascon-baseline-001
```

Skip the commit command if there are no changes to commit. This campaign uses
one design × three PDKs × C0 × ten distinct seeds = 30 jobs. Ten is an exploratory
starting point, not an automatic publication-quality sample size. Use observed
spread, failure rates and uncertainty to plan additional seeds with your professor.

The runner saves the exact shuffled order in `schedule.json` at `prepare` and
checks its checksum before execution. Keep `order_seed` fixed within a campaign;
it is reusable across campaigns and designs. The runner assigns every routing
seed from the experiment automatically. Keep the host,
thread count and background load consistent for runtime comparisons. Wall time
measures the launched flow process, including tool setup inside that process;
it excludes campaign preparation, environment probing and CSV generation. It is
not CPU time. You can add a fixed
`--timeout SECONDS` budget to `run` after selecting it from the pilot.

Use `run --label LABEL --machine-id desktop-i7` to supply an optional stable host
name; otherwise the hostname is used. CPU model, logical CPUs, CPU affinity when
available, host-visible RAM, OS and starting free disk space are captured once
per invocation. Each attempt records this machine snapshot, an `execution_id`
and its environment filename. Record scheduler/container CPU and RAM limits in
the protocol: these can differ from host-visible resources. `threads` is the
configured per-flow thread count, not the number of parallel flow jobs.

`run` refreshes `runs.csv`, `metrics_long.csv` and `summary.csv` automatically
after completion, ordinary errors or Ctrl-C. The explicit `collect` and
`summarize` commands shown in this guide remain valid and may be run again to
refresh derived tables. A power loss or forced kill cannot run that cleanup;
collect manually after reviewing the interrupted attempt. Plot generation is
still an explicit analysis command.

Running `run` again continues the campaign. It never re-runs finished attempts,
including failures and timeouts: keep those and their causes in the dataset.
Jobs left `running` by a crashed runner or `interrupted` by Ctrl-C produced no
result, so they run again automatically. Their partial files are archived
under `jobs/<id>/aborted/` (see the README section on resuming). Use a fresh
campaign for deliberate retries of finished jobs. Do not count the same seed
again as a new independent sample.

## 7. Inspect the dataset and plot it

| File under `results/<label>/` | Purpose |
|---|---|
| `manifest.json` | Unique campaign ID, label, `order_seed`, planned jobs, Git revisions, hashes and experiment |
| `schedule.json` | Exact planned job order, `order_seed`, campaign ID and shuffle Python version |
| `inputs/` | Experiment/configuration snapshots, per-design constraints, flow/PDK locks |
| `environment-*.json` | Per-invocation execution ID, tool provenance, CPU/RAM/OS details and Python version |
| `jobs/<id>/config.json` | Expanded input configuration |
| `jobs/<id>/config_origins.json` | Winning source layer for ordinary flow settings |
| `jobs/<id>/attempt.json`, `console.log` | Seeds, schedule/execution positions, machine snapshot, command, status, timestamps and runtime |
| `jobs/<id>/runs/run/` | Resolved config, per-step states/reports and final views |
| `runs.csv` | One row per planned run, with campaign/seeds/machine context, including failures and unattempted jobs |
| `metrics_long.csv` | Raw metrics with campaign/run/machine context, stage and source path |
| `summary.csv` | Separate design × PDK × configuration × machine ID × thread-count summaries |

These are structured files, not a SQL database. Campaign identity and
`order_seed` are repeated in exported tables, so they remain identifiable when
copied. Use `(campaign_id, run_id)` as the run key across campaigns; a job name
alone can recur in another campaign. For old campaigns without `campaign_id`,
retain their archive origin and label explicitly rather than inventing an ID.
`schedule_index` is the one-based position in the full frozen order;
`execution_index` is the one-based launch position within one invocation, after
skipping existing attempts. Timestamps and `execution_id` describe actual
execution. `synthesis_strategy` is exported alongside placement density in
`runs.csv` for later configuration comparisons.

Unattempted jobs have planned identities and schedule positions but no invented
machine or runtime. Legacy campaigns can still be collected; historical fields
that were never recorded remain empty. New execution requires a newly prepared
campaign after installing and committing the updated runner. Do not rewrite old
manifests or attempts to make an updated runner appear to be the original one.

The runner supports one invocation per campaign at a time. Prepare and execute
on the chosen host; do not have two machines or runner processes write into the
same campaign. Area/slack plots compare design/PDK/configuration and do not
stratify by machine; success-rate plots use the separate machine/thread summary
groups and label them. Use one host for a main campaign, or explicitly account
for host differences in subsequent analysis.

LibreLane's `final/metrics.csv` is a metric/value table, not a row-per-run dataset.
The collector reads final timing only from `OpenROAD.STAPostPNR`, after extraction;
missing final STA stays missing. It never substitutes synthesis timing. Raw
per-step metrics are cumulative, so do not sum areas/counts across stages.

The principal fields are worst signed setup/hold slack, worst-corner setup/hold
TNS, violation counts, reported instance area, core/die area, route wire length,
runtime and check outcomes. Slack is in ns; area is in µm². Violation counts are
summed across reported corners and are not counts of unique endpoints across
those corners. Instance area is the flow's reported value, not a normalized
transistor/process comparison. Fixed floorplan area may not vary across seeds.

`study_checks_pass` requires full successful exit, seed execution evidence, GDS,
final timing, enabled layout DRC, LVS and routing DRC. The flag does not establish
that the SDC has no missing constraints or that the PDK decks cover every foundry
rule; review those separately in the baseline.

Plotting requires Matplotlib in a separate analysis environment; collection and
summarization use only Python's standard library:

```bash
python3 -m venv .venv-analysis
.venv-analysis/bin/python -m pip install matplotlib
.venv-analysis/bin/python -m pip freeze > analysis-requirements-lock.txt
.venv-analysis/bin/python scripts/plot.py --label ascon-baseline-001
```

Use your normal Linux Python terminal for the analysis environment if necessary.
Commit the analysis dependency lock before preparing the next formal campaign.
Each design gets its own `plots/<design>/` directory with PDF/PNG distributions
of setup slack and instance area, plus pass rates with 95% Wilson intervals.
Plots display available/attempted counts. Summary area includes a separate
passing-only population so success rates can be considered alongside it.

## 8. Add configuration comparisons when useful

For more than a handful of variants, generate them from the knob catalogue
(`scripts/variants.py`, README section 6) and analyse campaigns with
`scripts/analyze.py`; the [analysis guide](analysis-guide.md) explains every
output and the recommended screen → confirm → explore sequence. The three density
variants below remain a minimal, hand-written example.

The three supplied variants change placement density only: Cminus 55%, C0 60%
(from `configs/defaults.json`) and Cplus 65%. Confirm these values are feasible in pilots. Other design/PDK
settings stay fixed across variants.

```bash
python3 scripts/study.py prepare --experiment experiments/ascon/ascon-density-pilot.json --label ascon-density-pilot-001
python3 scripts/study.py run --label ascon-density-pilot-001
python3 scripts/study.py collect --label ascon-density-pilot-001
```

After reviewing all nine pilot outcomes and committing any deliberate changes:

```bash
python3 scripts/study.py prepare --experiment experiments/ascon/ascon-density.json --label ascon-density-001
python3 scripts/study.py run --label ascon-density-001
python3 scripts/study.py collect --label ascon-density-001
python3 scripts/study.py summarize --label ascon-density-001
.venv-analysis/bin/python scripts/plot.py --label ascon-density-001
```

This is 90 jobs, including a new interleaved C0 baseline. Do not pool its repeated
C0 seeds with the earlier campaign as additional independent samples. If you
need to reuse baseline jobs to save compute, handle duplicates/provenance
explicitly rather than concatenating the CSVs.

Analyze each design × PDK × configuration separately. Show individual points,
counts, medians, IQRs and pass-rate uncertainty. Keep failures visible and inspect
their causes; a missing dependency or machine crash is not evidence of seed
sensitivity. For each design/PDK, compare C against C0 with:

* Area change: `100 × (median_area_C / median_area_C0 − 1)` percent.
* Slack change: `median_setup_slack_C − median_setup_slack_C0` ns.
* Pass-rate change: `100 × (pass_fraction_C − pass_fraction_C0)` percentage points.

Use the same area population and report its counts on both sides. Prefer signed
worst slack to WNS, which can be clipped at zero. Percent slack changes near zero
are misleading. Target frequency is not measured Fmax; converting a single slack
to Fmax requires checking the path type and constraints.

A configuration ranking should first reflect the intended validity/timing
requirement, then area and other tradeoffs among feasible settings. Small median
differences relative to the spread do not establish a robust winner. Ten chosen
seeds and Wilson intervals provide exploratory evidence, not an exact universal
probability. Formal superiority claims need a predeclared uncertainty analysis
and enough data. The same seed list improves auditing but does not automatically
justify a paired statistical test when placement/configuration changes.

Across PDKs, compare relative effects and whether configuration ordering
transfers, with library/voltage/corner/constraint differences stated. Absolute
area or timing differences combine process, library and flow effects. Across
designs, keep their architectures and throughput requirements explicit.

Power is a later extension: fix the operation, activity annotation, frequency,
voltage and corner first. Raw power metrics are retained where available, but
do not sum corner power estimates or treat differing default/vectorless
assumptions as an equal-workload measurement.

## 9. Preserve the result and decide the next experiment

Archive the complete campaign, including failures, and preserve the exact source
commits and functional-test evidence. Hashes/pointers do not copy the design
repository, and ignoring `results/` does not create a backup. Preserve the PDK
build identity too. Keep a lab log of pilot changes and the reason for each.

Put selected small tables/plots and conclusions under `reports/` with their
campaign/commit and raw-archive reference. A tag on the parent repository can
identify the experiment definition and submodule pins used for a reported result.
Keep large GDS/SPEF/log archives outside ordinary Git history.

Use this first ASCON dataset to decide whether seed sensitivity is material,
whether configuration changes are robust, and whether adding AES/SHA is likely
to answer a useful broader question. A large run count alone does not establish
the novelty of an ODE4EC contribution.

## Sources and validation scope

* [Pinned LibreLane source](https://github.com/librelane/librelane/tree/01ddb25e4424f6f5aaf6b951b14a2b6fdcf737c0)
* [PDK integration](https://librelane.readthedocs.io/en/stable/usage/about_pdks.html)
* [IHP setup](https://ihp-open-pdk-docs.readthedocs.io/en/latest/digital/librelane_setup.html)
* [OpenROAD routing randomness](https://openroad.readthedocs.io/en/latest/main/src/grt/README.html)

The source pin is authoritative when rolling documentation differs. This starter
has been checked with artificial data fixtures and temporary local submodules;
no ASCON synthesis or physical implementation has been run in this workspace.
