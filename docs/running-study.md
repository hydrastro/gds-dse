# Running the study

Complete [setup](setup.md) and use the pinned Nix shell. The target experiment
includes `sky130A`, `gf180mcuD` and `ihp-sg13g2`. AES comes first; the other
three designs follow the same protocol. Flow-parameter DSE is the main
experiment; the C0-only walkthrough below is a baseline diagnostic.

## Before preparing a campaign

Bring up each platform and choose its clock/constraints. SKY130 can be the first
installation check, followed by GF180MCU and IHP SG13G2. Select the targets for
all three before preparing the batches that will form one pooled dataset.
The baseline command below selects all three explicitly; the preset itself has
a small SKY130 default for restricted bring-up.

Select requested threads and CPU policy before preparation. The defaults in
`configs/study.json` request two threads and one simultaneous flow. Use the
same resource policy throughout the comparison.

A campaign label identifies a saved batch. `prepare` expands the selected
designs × PDKs × configurations × seeds × repeats, checks the configuration,
freezes inputs and saves a shuffled schedule. It does not run the EDA tools.
It prints the job count and refuses an existing label.

CLI selections replace the corresponding list in the experiment file. The
selections and original definition are both recorded. To bring up one platform
alone, select only its ID with `--pdks` and use a separate diagnostic label.

## Baseline diagnostic: one seed per PDK, then the remaining nine

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A gf180mcuD ihp-sg13g2 \
  --seeds 1 --label aes-baseline-first

python3 scripts/study.py run --label aes-baseline-first --machine-id desktop
```

This schedules three full-flow attempts. Inspect `results/aes-baseline-first/runs.csv`,
each job's `console.log`, and the reports beneath `jobs/<job-id>/runs/run/`.
Confirm the intended top and sources, actual seed application, final STA, GDS
availability, setup/hold, DRC and LVS. Check for unexpected black boxes, latches
or unconstrained endpoints. GDS availability alone does not establish a pass.

If a setup needs repair, preserve the results as bring-up, make the change and
use a new label. If the setups are appropriate, leave the inputs untouched:

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A gf180mcuD ihp-sg13g2 \
  --seeds 2 3 4 5 6 7 8 9 10 --label aes-baseline-rest

python3 scripts/study.py run --label aes-baseline-rest --machine-id desktop

python3 scripts/analyze.py \
  --label aes-baseline-first --label aes-baseline-rest \
  --out reports/aes/baseline-all-pdks
```

This is thirty attempts, not thirty guaranteed successes. Record failures under
the frozen setup. Do not keep trying new seeds until ten per platform happen
to pass. The analyser groups by PDK; combining campaigns does not merge the
three technologies into one metric distribution.

Combining batches requires unchanged shared inputs, compatible tool evidence,
the same machine/resource context within each design/PDK and disjoint seed cells.
Even comment-only edits to hashed configs can prevent pooling. Leave all
comparison inputs fixed from the first batch through the second.

## Run all thirty together

Use this alternative when the three baseline setups have already been validated:

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A gf180mcuD ihp-sg13g2 \
  --label aes-baseline-all-pdks

python3 scripts/study.py run --label aes-baseline-all-pdks --machine-id desktop
python3 scripts/analyze.py --label aes-baseline-all-pdks
```

Choose either this route or the three-plus-twenty-seven route. Running both
would duplicate the same observations.

## Choose a fixed clock

Each design currently has a 25 ns fallback; the supplied per-PDK files are
empty. This is a starting value, not a timing result.

Put a chosen target in
`configs/designs/<design>/pdks/<pdk>.json` as a numeric `CLOCK_PERIOD` in
nanoseconds. Record the requirement or bring-up evidence supporting it.
Changing this file requires a new campaign. A slower clock may reduce setup
pressure; it does not repair hold, DRC or LVS errors.

Keep the target fixed across seeds within each configuration. It can vary
between configurations when clock target is a declared DSE knob. A baseline
reference supports relative clock variants; it does not establish maximum
frequency. More slack under a looser target does not establish better achieved
performance, so define the evaluation criterion before ranking clock variants.

The field `require_calibrated_clock_for_study` has a historical name. Its actual
gate is the presence of an explicit `CLOCK_PERIOD` in the per-PDK file.
It does not require an automated sweep or certify that the value was measured.
Document the evidence yourself.

### Optional calibration helper

Use this only if a clock sweep is needed. The selection below costs **24
partial-flow runs**: eight periods × three seeds, through post-route STA.

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/calibrate.json \
  --designs aes --pdks sky130A --label aes-sky130-clock

python3 scripts/study.py run --label aes-sky130-clock --machine-id desktop
python3 scripts/calibrate_clock.py --label aes-sky130-clock
```

Review its output before using `--write` to update the per-PDK file. The helper
requires complete planned observations, frozen-config checks, seed evidence and
finite setup measurements. Missing or failed executions block writing; completed
negative-slack measurements are valid observations of failing periods.

The default headroom rule divides a selected observed passing period by 0.85.
It is a target-selection policy, not measured Fmax or guaranteed timing closure.
Validate the selected target with a full flow. Keep the sweep separate from the
seed dataset.

## Limit CPU use

Set `execution.threads` and `execution.parallel_jobs` in
`configs/study.json` before preparation. The current server settings are 4 and 6.
They limit requested tool parallelism, not an enforced percentage of the host.

On Linux with a systemd user manager and a delegated CPU controller, replace
each `run` command with a scope like this, from inside the pinned shell:

```bash
systemd-run --user --scope -p CPUQuota=200% \
  python3 scripts/study.py run --label aes-baseline-first --machine-id desktop
```

Use the same prefix for the remaining batch. A 200% quota caps the scope and
its descendants at two CPUs' worth of aggregate CPU time per quota period.
100% means one CPU's worth; 50% means half of one. It does not reserve physical
cores. Check that the command succeeds.

`nice -n 10` lowers scheduling priority without imposing a hard cap.
`taskset` limits eligible logical CPUs. `cpulimit` uses stop/continue signals;
a cgroup quota fits this multi-process flow more directly.

On a university server, use its scheduler's resource allocation. Record the
allocation, wall-time limit and resource policy. The runner captures machine
identity, requested threads, affinity, nice level and visible cgroup-v2 limits;
it cannot observe every competing workload or scheduler restriction.

Keep resource settings fixed for comparisons. A throttling-induced timeout is
an execution outcome, not evidence that the physical design cannot route.

References: [systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html),
[systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html),
[cpulimit](https://github.com/opsengine/cpulimit).

## Resume and collect

Repeat the same `run --label ...` command after an interruption. The runner
continues unattempted jobs and restarts incomplete attempts, preserving their
partial files under the job's `aborted/` directory. Recorded flow errors,
timeouts and launcher errors remain recorded; they are not silently retried.

Do not edit frozen inputs while resuming. After intentional input changes,
prepare a new label. A runner lock prevents two processes from running the
same campaign concurrently.

Collection and summary happen automatically after a normal finish or handled
interruption. After a power loss or forced kill, refresh them explicitly:

```bash
python3 scripts/study.py collect --label aes-baseline-first
python3 scripts/study.py summarize --label aes-baseline-first
```

| Output under `results/<label>/` | Contents |
| --- | --- |
| `manifest.json`, `schedule.json` | Selected jobs, input hashes and execution order |
| `inputs/` | Frozen configuration, constraints and toolchain pin records |
| `environment-*.json` | Execution environment and tool evidence |
| `jobs/` | Per-attempt commands, status, logs and flow outputs |
| `runs.csv` | One row per planned job, including failures and missing outcomes |
| `metrics_long.csv` | Collected detailed metrics |
| `summary.csv` | Grouped counts, pass fractions and descriptive statistics |
| `analysis/` | Analysis tables and report, unless `--out` selects another path |

Keep the entire campaign when archiving results. The tables are automatically
collected JSON/CSV files; no database setup or manual seed log is needed.

## Run DSE across configurations and PDKs

Select the flow parameters and candidate configurations before the main DSE
campaign. The existing stage1 plan covers clock, utilisation, density, synthesis
and hierarchy; CTS/routing plans support later expansion. A density-only
comparison is one example. For that example, add `--configs Cminus C0 Cplus` to the preparation
command that selects all three PDKs. That schedules **90 runs for AES**:
three PDKs × three configurations × ten seeds. Prepare the candidates together
so their execution order is shuffled within one campaign.

Select that comparison instead of a baseline-only campaign if it is already
the agreed question. If baseline results exist, consider provenance and blocking
before reuse; do not automatically rerun or pool the same C0 seed cells.

After validating the other cores and their platform targets, select
`--designs aes sha256 chacha prince`. The same three-density experiment then
contains **360 runs** across the full suite.

The [study plan](study-plan.md) explains when an optional 2 × 2 knob comparison
helps. Its extra synthesis dimension is optional; inclusion of the three PDKs
is part of the planned study. The [experiment index](../experiments/README.md)
lists available templates and their default counts.

## Pilot and formal campaigns

`phase: pilot` permits bring-up with uncommitted study files and records their
state. The LibreLane pin and frozen-input checks still apply.

`phase: study` requires a clean committed parent repository, submodules at their
recorded revisions, an explicit per-design/per-PDK clock, distinct seeds and a
complete flow. Prepare a tracked experiment with that phase after the protocol
and inputs are fixed. Phase labels do not establish statistical validity.
