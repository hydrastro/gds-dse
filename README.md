# GDS-DSE

**How much can a routing seed change an RTL-to-GDSII result?**

GDS-DSE runs controlled implementation experiments with LibreLane. It keeps the
RTL, constraints and tool versions fixed, changes selected flow settings, and
records every attempt—including failures.

The project compares unchanged cryptographic cores across **SKY130, GF180MCU
and IHP SG13G2**. AES is the first design; SHA-256, ChaCha and PRINCE form the
planned wider suite. All three PDKs are part of the study's scope.

[Setup](docs/setup.md) · [Study plan](docs/study-plan.md) ·
[Run guide](docs/running-study.md) · [Configuration](docs/configuration.md) ·
[Analysis](docs/analysis-guide.md)

## What is being measured?

There are two questions:

1. **Seed sensitivity:** how much do timing, area, wirelength and feasibility
   change when only the initial global-routing seed changes?
2. **Configuration transfer:** when several flow settings are compared, does
   the preferred setting stay preferred on another PDK?

The baseline uses ten seeds on each of the three PDKs: thirty runs for AES.
Comparing several configurations across those PDKs addresses the second
question. The seed intervention changes initial global-routing net order;
it does not randomize placement, synthesis or the entire flow.

All four benchmarks use unchanged upstream RTL and its existing top-level
interface. This repository adds experiment definitions and measurement tools.

## Start with the three-PDK baseline

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
