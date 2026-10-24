# Failed runs: what they mean and what to do

A failed run is usually a **result**, not a bug to fix. The study measures how often
each configuration fails and why, alongside the area and timing of the runs that worked.
Since `collect` classifies every unsuccessful run, `runs.csv` has these columns:

| Column | Meaning |
|---|---|
| `study_checks_pass` | Every required check passed, including setup/hold at the matched corner |
| `layout_valid` | The layout is complete and clean (GDS, routing DRC, layout DRC, LVS) **whatever its timing** |
| `failure_class` | The cause: `congestion`, `timing`, `tool_crash`, `infra`, `route_drc`, `lvs`, … |
| `failure_kind` | `design` (an outcome of the configuration), `tool` (the EDA tool crashed) or `infra` (the machine/environment failed) |
| `failure_evidence` | The log pattern or check that decided the class |
| `failed_step` | The LibreLane step that stopped the flow, if any |

The rules are in `configs/study.json` → `failure_classes`. `analyze.py` writes
`failures.csv` and an *Outcomes and failure causes* table at the end of `report.md`.

## The causes seen in the first AES batch

| What the log shows | Class / kind | Is the run valid? | What to do |
|---|---|---|---|
| `[GRT-0116] Global routing finished with congestion` (metal 1–2 above 100%) | `congestion` / design | No layout. A real outcome: this configuration does not route on this seed | Keep. It is part of the configuration's failure rate. It is seed-dependent, which is itself a finding |
| `Setup violations found … nom_ss_…` **after** the GDS was written | `timing` / design | **Layout valid** (`layout_valid=True`); it misses timing at the slow corner | Keep. Area/wirelength count (population `layout_valid`); slack is the measurement. If C0 does this often, the clock target is too tight (see below) |
| `[GRT-0183] Net …: heap underflow during 3D maze routing` in antenna repair | `tool_crash` / tool | No. OpenROAD crashed inside FastRoute's maze router | Keep, counted separately. It is a fact about the flow's reliability. Report its rate; do not change knobs to hide it |
| `Cannot open LEF file …` (file exists) with `proot warning: ptrace(…)` | `infra` / infra | No, and **not an outcome** | Excluded from every statistic. Re-run with `study.py run --label … --retry-infra` |
| `Max Slew / Max Cap violations` in all corners | reported only | Warnings, not failures | Not a required check (`checks.reported`). They mostly come from the PDK's output load / max-transition limits. Look at them, but they do not decide pass/fail |
| `KLayout.DRC … KLAYOUT_DRC_RUNSET is unset … skipped` (gf180mcuD) | — | — | The pinned gf180mcu PDK has no KLayout DRC deck, so only Magic DRC applies. This used to make **every** GF180 run fail the study's DRC check. It is fixed: the platform sets `RUN_KLAYOUT_DRC=false`, and the check only requires KLayout when a deck exists. Re-run `collect` on old campaigns to update them |
| A single residual antenna violation | `antenna` (reported) | Layout valid | Reported only. LibreLane does not stop for it |

## Why not "fix" failures with config changes

Suggested overrides such as `PL_RESIZE_HOLD_ITERATIONS`, `GLB_RESIZER_TIMING_OPTIMIZATIONS`
and `GLB_RESIZER_MAX_*_MARGIN` are **OpenLane 1** names. The first does not exist in
LibreLane at the pinned commit. The others are deprecated aliases for
`RUN_POST_GRT_RESIZER_TIMING` and `GRT_DESIGN_REPAIR_MAX_*_PCT`; the first of these
re-runs global routing without the study's seed (an opt-in knob in the catalogue).
`prepare` rejects all of them with that explanation.

More importantly, adding such settings to C0 changes the baseline after you have seen
its failures, and those failures are data. Settings like these belong in the study as
**variants** (e.g. `MAX_FANOUT_CONSTRAINT` is already a catalogue knob), so their effect
is measured against an unchanged C0.

## When C0 itself fails often: fix the operating point, not the flow

C0 should pass on most seeds. If it does not, the clock target or the floorplan is wrong
for that design × PDK, and every comparison is then made at the edge of feasibility:

* **Setup failures at C0** (sky130A and gf180mcuD at the 25 ns placeholder): set the
  real per-PDK clock. Run the calibration campaign (`experiments/suite/calibrate.json`,
  then `scripts/calibrate_clock.py --label … --write`); it picks 85% of the fastest
  reliably met frequency for each design × PDK. Until then, every PDK runs at the same
  25 ns placeholder: too tight for gf180mcuD's 5 V cells at the slow corner, very loose
  for IHP.
* **Congestion at C0:** the default utilisation/density are too dense for that design on
  that PDK. Choose a C0 utilisation that routes on (nearly) every seed, document it once
  in `configs/designs/<d>/pdks/<pdk>.json`, then freeze it.

Make these choices in a pilot, write them down with the evidence, and start the real
campaigns afterwards. Do not re-tune C0 after the study has started.

## Running environment

The `infra` failures came with `proot warning: ptrace(PEEKDATA): Bad address`. PRoot
(used by nix-portable when Nix is not installed) intercepts every system call with
ptrace. With 60 flows in parallel it occasionally fails file operations, so a file that
exists cannot be opened. Options, best first:

1. Install real Nix (multi-user or single-user) on the server: no ptrace layer.
2. Use nix-portable's `bwrap` runtime if the kernel allows unprivileged user namespaces
   (`NP_RUNTIME=bwrap`): no ptrace either.
3. Keep PRoot but lower `execution.parallel_jobs`, and re-run infra failures with
   `--retry-infra`.

Also check memory: each AES flow can use several GB, so 60 at once needs a matching
amount of RAM. Out-of-memory kills show up as `infra` (`Cannot allocate memory`,
`std::bad_alloc`) or as a `timeout`.

## Threads and determinism

Single-threaded detailed routing (`threads: 1` → `DRT_THREADS=1`) is the right choice for
this study: TritonRoute with several threads can give different results for the same
seed, which would add noise the study does not control. Your one-thread re-runs of the
failed IHP detailed-routing step all succeeded, which fits this. Keep one thread, and
confirm with the same-seed check (`experiments/suite/seed-check.json`) that repeats are
identical.
