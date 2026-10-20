# Running the study

Complete [setup](setup.md) and use the pinned Nix shell. Start with one unchanged
design and one PDK. The commands below select AES and SKY130 explicitly.

## Before preparing a campaign

Select the clock/constraints, requested threads and CPU policy. The defaults in
`configs/study.json` request two threads and one simultaneous flow. Use the
same policy throughout the comparison.

A campaign label identifies a saved batch. `prepare` expands the selected
designs × PDKs × configurations × seeds × repeats, checks the configuration,
freezes inputs and saves a shuffled schedule. It does not run the EDA tools.
It prints the job count and refuses an existing label.

CLI selections such as `--designs aes` replace the corresponding list in the
experiment file. The selections and original definition are both recorded.

## Run one, then the remaining nine

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A --seeds 1 --label aes-sky130-first

python3 scripts/study.py run --label aes-sky130-first --machine-id desktop
```

Inspect `results/aes-sky130-first/runs.csv`, the job's `console.log`, and the
reports beneath `jobs/<job-id>/runs/run/`. Confirm the intended top and source
list, actual seed application, final STA, GDS availability, setup/hold, DRC and
LVS. Check for unexpected black boxes, latches or unconstrained endpoints.
A GDS file by itself does not establish a passing implementation.

If the setup needs repair, preserve this campaign as bring-up, make the change
and use a new label. If the setup is appropriate, leave the inputs untouched:

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A --seeds 2 3 4 5 6 7 8 9 10 \
  --label aes-sky130-rest

python3 scripts/study.py run --label aes-sky130-rest --machine-id desktop

python3 scripts/analyze.py \
  --label aes-sky130-first --label aes-sky130-rest \
  --out reports/aes/sky130-baseline
```

This is ten attempts, not ten guaranteed successes. Record failures under the
frozen setup. Do not keep trying new seeds until ten happen to pass.

Combining batches requires unchanged shared inputs, compatible tool evidence,
the same machine/resource context and disjoint seed cells. Even comment-only
edits to hashed configs can prevent pooling. Decide the setup before the first
batch and leave it fixed through the second.

## Run all ten together

Use this alternative when the baseline setup has already been validated:

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A --label aes-sky130-baseline

python3 scripts/study.py run --label aes-sky130-baseline --machine-id desktop
python3 scripts/analyze.py --label aes-sky130-baseline
```

Choose either this route or the one-plus-nine route. Running both would
duplicate the same observations.

## Choose a fixed clock

Each design currently has a 25 ns fallback; the supplied per-PDK files are
empty. This is a starting value, not a timing result.

Put a chosen target in
`configs/designs/<design>/pdks/<pdk>.json` as a numeric `CLOCK_PERIOD` in
nanoseconds. Record the requirement or bring-up evidence supporting it.
Changing this file requires a new campaign. A slower clock may reduce setup
pressure; it does not repair hold, DRC or LVS errors.

Keep the target fixed for all configurations compared within a design/PDK.
Selecting a comfortable target is valid for a seed-sensitivity pilot; finding
maximum frequency is a separate question.

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
`configs/study.json` before preparation. The default values are 2 and 1.
They limit requested tool parallelism, not an enforced percentage of the host.

On Linux with a systemd user manager and a delegated CPU controller, replace
each `run` command with a scope like this, from inside the pinned shell:

```bash
systemd-run --user --scope -p CPUQuota=200% \
  python3 scripts/study.py run --label aes-sky130-first --machine-id desktop
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
python3 scripts/study.py collect --label aes-sky130-first
python3 scripts/study.py summarize --label aes-sky130-first
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

## Expand only when the question is clear

For a second-PDK baseline, select `--pdks gf180mcuD` or `--pdks ihp-sg13g2`,
choose an appropriate documented clock and use a new label. Bring up that
platform first. For another design, replace `--designs aes` with its ID after
validating its RTL.

For a density study, use `--configs Cminus C0 Cplus` with the baseline preset.
That schedules 30 runs per design/PDK at ten seeds. Prepare the candidates in
one campaign so their execution order is shuffled together.

The [study plan](study-plan.md) explains when the optional 2 × 2 comparison
helps. The [experiment index](../experiments/README.md) lists the much larger
presets; none is a prerequisite for the baseline.

## Pilot and formal campaigns

`phase: pilot` permits bring-up with uncommitted study files and records their
state. The LibreLane pin and frozen-input checks still apply.

`phase: study` requires a clean committed parent repository, submodules at their
recorded revisions, an explicit per-design/per-PDK clock, distinct seeds and a
complete flow. Prepare a tracked experiment with that phase after the protocol
and inputs are fixed. Phase labels do not establish statistical validity.
