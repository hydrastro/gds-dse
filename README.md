# GDS-DSE

**How reliable is flow-parameter design space exploration across open PDKs?**

GDS-DSE explores flow parameters with LibreLane and evaluates how reliably
configurations improve implementation quality. Each configuration is repeated
with different routing seeds, then compared across PDKs using baseline-relative
metrics and configuration rankings. RTL and tool revisions stay fixed; the
selected flow settings, including constraints when chosen as knobs, define
the design space. Every attempt is recorded, including failures.

The project compares unchanged cryptographic cores across **SKY130, GF180MCU
and IHP SG13G2**. AES is the first design; SHA-256, ChaCha and PRINCE form the
planned wider suite. All three PDKs are part of the study's scope.

[Setup](docs/setup.md) · [Study plan](docs/study-plan.md) ·
[Run guide](docs/running-study.md) · [Configuration](docs/configuration.md) ·
[Analysis](docs/analysis-guide.md)

## What is being measured?

The study asks whether the advantages of a flow configuration persist across
routing seeds, and whether its relative performance and ranking transfer to
another PDK. The design space includes clock target, core utilisation, placement
density, synthesis strategy, hierarchy, and selected CTS and routing settings.
We start with a small parameter set and expand it as the study develops.

Repeated seeds measure variability within each configuration. The implemented
seed intervention changes initial global-routing net order; it does not
randomize every stage of the flow.

All four benchmarks use unchanged upstream RTL and its existing top-level
interface. This repository adds experiment definitions and measurement tools.

## Baseline check before DSE

The C0-only commands below check the baseline across three PDKs. They are a
preliminary diagnostic, not the full DSE experiment. DSE repeats this process
for a selected set of flow configurations, including C0.

Complete [setup](docs/setup.md), then enter the pinned environment from the
repository root:

```bash
nix-shell tools/librelane/shell.nix
```

Bring-up can proceed one platform at a time, starting with SKY130. Select and
document each platform's clock and constraints before freezing the dataset.
The supplied **25 ns clock is an unvalidated starting value**.

**Run one seed on each PDK first:**

```bash
python3 scripts/study.py prepare \
  --experiment experiments/suite/pilot-baseline.json \
  --designs aes --pdks sky130A gf180mcuD ihp-sg13g2 \
  --seeds 1 --label aes-baseline-first

python3 scripts/study.py run --label aes-baseline-first --machine-id desktop
```

This schedules three full-flow attempts. Inspect `results/aes-baseline-first/runs.csv`
and the logs for each platform. Check the intended design, seed application,
GDS, timing, DRC and LVS. The [run guide](docs/running-study.md) explains how to
handle bring-up changes without mixing different setups.

**If the setups are sound, keep them fixed and run the other nine seeds per PDK:**

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

That is **30 full-flow attempts: one design × three PDKs × one configuration
× ten seeds**. Keep the same machine and resource policy for both batches.
The analyser checks compatibility before combining them. Once the setups are
validated, all thirty can instead be prepared as
[one campaign](docs/running-study.md#run-all-thirty-together).

The default is one flow at a time with two requested threads. For a hard CPU
limit, see [resource controls](docs/running-study.md#limit-cpu-use).

## Where things live

| Path | Purpose |
| --- | --- |
| `designs/` | Upstream RTL submodules |
| `tools/librelane/` | Pinned flow submodule |
| `configs/designs/` | Source lists, top modules and per-PDK clock targets |
| `configs/variants/` | Shared flow settings such as placement density |
| `experiments/` | Designs, PDKs, configurations and seeds to run |
| `scripts/` | Preparation, execution, collection and analysis |
| `results/<label>/` | Frozen inputs, logs, layouts and automatically collected tables |
| `reports/` | Selected results and their interpretation |
| `docs/` | Protocol, reference material and version information |

Results and PDK downloads are excluded from Git. Archive the complete campaign
before publishing a result; a CSV alone is not enough to reproduce it.

## Read the results

`run` writes `runs.csv`, `metrics_long.csv` and `summary.csv` automatically.
Seeds, execution order, machine details and failures are recorded without manual
transcription. No database server is needed.

Start with pass counts, failure reasons and the spread of each metric. Ten seeds
give a pilot estimate; they do not establish a universal flow ranking.
[The analysis guide](docs/analysis-guide.md) explains the populations, comparisons
and limits of the generated tables.

## Project status

The runner has automated software checks. End-to-end RTL simulation and
physical implementation must be validated in the intended tool environment;
this source package contains no measured benchmark results.

For exact source pins and update procedures, see [versioning](docs/versioning.md).
For the full documentation map, see [docs/](docs/README.md).
