# Running unchanged crypto designs with shared flow settings

The four upstream RTL submodules are unchanged: secworks AES, SHA-256, ChaCha
and PRINCE. No added wrapper, crypto feature, architecture variant or RTL
optimization is part of this update. Each manifest uses the upstream repository's
own register-interface top. That top is part of the benchmark; switching to an
internal core would be a different benchmark, so keep the chosen top fixed.

General flow variants belong in configs/variants/ and can be shared by every
design. Design manifests and per-design/per-PDK clock targets describe necessary
integration and constraints. They do not modify the RTL. The previous AES-only
experiment files were just selections; the shared presets below supersede that
workflow and avoid maintaining equivalent experiment files for each design.

## Setup

Apply the update files to your existing project, preserving your own changes.
Do not apply them in the middle of a prepared/running campaign: scripts and
settings are hashed inputs. Finish or archive that campaign first, retain its
original checkout for resumption, and prepare new campaign labels for this version.

From the project root:

```bash
git submodule update --init
git submodule status
```

Install Nix and the FOSSi binary cache following the
[official LibreLane Linux instructions](https://librelane.readthedocs.io/en/stable/installation/nix_installation/installation_linux.html).
Then use the existing pinned checkout:

```bash
nix-shell tools/librelane/shell.nix
librelane --smoke-test
python3 -m unittest discover -s tests -v
python3 scripts/variants.py check
```

Leave machine.local.json absent to use Ciel-managed PDKs at the pinned revisions.
Use it only for intentional manual PDK installations, recording their real
revisions. The runner caches the automatic installations under pdks/.

Run each upstream design's functional tests and retain their summaries. For AES:

```bash
make -C designs/aes/toolruns sim-top sim-core sim-keymem sim-encipher sim-decipher
```

Inspect the other submodules' own Makefiles/FuseSoC targets rather than assuming
AES's target names apply. Some testbenches print failures but exit zero. No FPGA
board is required for the ASIC experiment. Upstream provenance and RTL validation
are separate from successful physical implementation.

Review `git status`, then commit the intended scaffold, settings and submodule
pointers. The uploaded parent repository had no commit. Formal study campaigns
still require a clean committed repository; this protection has not been removed.
Do not commit results/, pdks/, machine-local paths or downloaded update archives.

## CPU control

The updated configs/study.json defaults are:

```json
"execution": {
  "threads": 2,
  "parallel_jobs": 1
}
```

These are fields within the existing execution object; retain its other fields.
`threads` is passed to LibreLane, detailed routing, STA and OMP_NUM_THREADS.
`parallel_jobs` controls simultaneous flows. Their product is a planning estimate,
not an OS-enforced percentage limit; some tool subprocesses can use other threads.
Change these settings before preparing a campaign and keep them fixed throughout.

On Linux with a systemd user manager and an available delegated CPU controller,
run the whole campaign in a quota-limited scope from inside the pinned Nix shell:

```bash
systemd-run --user --scope -p CPUQuota=200% \
  python3 scripts/study.py run --label aes-pilot-001 --machine-id desktop
```

200% allows at most two CPUs' worth of aggregate CPU time over each quota period,
shared across the runner and its descendants. It is not 200% of the whole machine
or a reservation of two physical cores. Use 100% for one CPU's worth or 50% for
half of one. A university scheduler's assigned resources take precedence; use
its allocation controls and ask the administrator if user scopes are unavailable.
The command must succeed; do not assume a rejected quota is active.

For responsiveness without a hard cap, `nice -n 10 python3 scripts/study.py run ...`
lowers scheduling priority but may still use all available CPU when the system is
idle. `taskset` limits eligible logical CPUs, not a percentage; it also matters
which logical CPUs share a physical core. cpulimit uses stop/continue throttling;
a cgroup quota is a cleaner choice for this multi-process toolchain.

The runner records affinity, nice level, parallel job count and visible cgroup-v2
quota/cpuset information in environment and attempt records and runs.csv. A parent
quota may be shared with other processes. This does not record every source of
contention; keep comparable machine conditions and limit policies, especially for
runtime claims. Throttling can cause wall-clock timeouts.

References: [systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html),
[systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html),
[cpulimit](https://github.com/opsengine/cpulimit).

## Shared presets and job counts

All four presets use phase=pilot and default to SKY130 and all four designs.

| Preset under experiments/suite/ | Default jobs | With --designs aes |
| --- | ---: | ---: |
| smoke.json | 4 | 1 |
| pilot-seed-check.json | 16 | 4 |
| pilot-baseline.json | 40 | 10 |
| pilot-2x2.json | 160 | 40 |

The 2x2 preset crosses placement density 60/70 with synthesis AREA 0/DELAY 1.
The four configs are C0, pilot_d70_area0, pilot_d60_delay1 and pilot_d70_delay1.
Core utilization remains 50 and the clock stays fixed within each design/PDK.
These candidate levels are not claims of best performance. You can instead keep
only C0 for the initial ten-seed baseline, or use Cminus/C0/Cplus for a 30-flow
single-design density pilot.

CLI selections replace the corresponding list in the preset. They are recorded
in manifest.json alongside the original experiment, so there is no hidden manual
edit. --designs, --pdks, --configs and --seeds accept space-separated values.

## Bring up one design and PDK

```bash
python3 scripts/study.py prepare --experiment experiments/suite/smoke.json \
  --designs aes --pdks sky130A --label aes-smoke-001
python3 scripts/study.py run --label aes-smoke-001 --machine-id desktop
```

Use the systemd-run prefix above on run commands when you want a hard quota.
A complete successful run establishes tool/PDK integration and gives a resource
estimate. Inspect the logs, GDS availability, timing and required checks.

The original 25 ns design fallback is unvalidated. If unsuitable, choose a new
candidate in configs/designs/<design>/pdks/<pdk>.json, document the evidence, and
prepare a new campaign. A slower period can address setup pressure but cannot
repair hold, LVS or DRC problems. Keep the selected reference fixed across the
configuration comparison. Record targets before preparing campaigns you will pool.

An audited clock sweep is also available using the existing calibration preset:

```bash
python3 scripts/study.py prepare --experiment experiments/suite/calibrate.json \
  --designs aes --pdks sky130A --label aes-cal-001
python3 scripts/study.py run --label aes-cal-001 --machine-id desktop
python3 scripts/calibrate_clock.py --label aes-cal-001
python3 scripts/calibrate_clock.py --label aes-cal-001 --write
```

This selection is 8 periods x 3 seeds = 24 jobs through post-route STA. The script
now requires every manifest job, verified frozen config, successful execution,
seed evidence and finite setup measurements at the intended period. Missing or
failed execution blocks writing; completed negative-slack measurements remain
valid observations of a failing period. Only the clock and routing seed may vary.

The selected observed passing period is divided by .85 as a chosen headroom
policy. It is not a measured Fmax or a guarantee that the reference will meet
all checks. Validate the written reference with full flows before confirmation.
If every sampled period passes, extend toward faster periods only if frequency
characterization is part of your question. If none passes, inspect failures and
extend toward slower periods as appropriate. Commit clocks and evidence.

## Repeatability and the pilot

```bash
python3 scripts/study.py prepare --experiment experiments/suite/pilot-seed-check.json \
  --designs aes --label aes-seedcheck-001
python3 scripts/study.py run --label aes-seedcheck-001 --machine-id desktop
```

Compare repeat 1 versus repeat 2 for each seed in runs.csv: statuses/checks,
setup/hold slack, area and wirelength. Runtime naturally varies; GDS metadata
can differ without a geometry change. Investigate other differences before
attributing measured spread to the routing seed. Repeats are diagnostic samples.

For ten seeds and two knobs on one design:

```bash
python3 scripts/study.py prepare --experiment experiments/suite/pilot-2x2.json \
  --designs aes --label aes-pilot-001
python3 scripts/study.py run --label aes-pilot-001 --machine-id desktop
```

For all four unchanged designs after their baselines work:

```bash
python3 scripts/study.py prepare --experiment experiments/suite/pilot-2x2.json \
  --label crypto-pilot-001
python3 scripts/study.py run --label crypto-pilot-001 --machine-id desktop
```

Add `--pdks sky130A gf180mcuD ihp-sg13g2` to preparation when all three platform
baselines work; four designs x three PDKs x four configurations x ten seeds is
480 full flows. Start smaller to measure feasibility and cost. The original
large suite presets remain available; they are not required first steps.

Changing order_seed is unnecessary; it only determines the saved schedule. You
may reuse the same seed list and order_seed across designs and PDKs. Identical
numeric routing seeds do not imply identical physical perturbations in different
netlists. Reusing identical design/PDK/configuration/seed cells does not create
new independent seed observations.

## Collect, inspect and preserve

Run automatically collects and summarizes on completion or a handled interruption.
The same run command resumes unattempted jobs and preserves interrupted attempts;
recorded flow errors/timeouts remain outcomes. After intentional input changes,
prepare a new label. Do not modify inputs while a campaign is running.

```bash
python3 scripts/study.py collect --label crypto-pilot-001
python3 scripts/study.py summarize --label crypto-pilot-001
python3 scripts/analyze.py --label crypto-pilot-001
```

This produces JSON/CSV records, not a database server. Seeds, schedule, commands,
resources, failures and results are collected automatically. Review raw data and
missing outcomes. Back up the entire results/<label>/ directory, including raw
logs, frozen inputs, environment records and GDS outputs. Read analysis-guide.md
for tie handling, transfer, pooling and the limits of exploratory resampling.
