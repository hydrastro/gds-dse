# Fresh study

Question: how stable are configuration rankings across routing seeds and PDKs?
Old campaigns are setup evidence only. Keep their files, but do not combine their
observations with the fresh study.

## Reference policy

Use the same baseline recipe for every core/PDK pair:

| Setting | Value |
| --- | --- |
| Floorplan sizing | Relative, with no fixed die/core rectangle |
| Core utilization | 50% |
| Placement target density | 60% |
| Synthesis | AREA 0 |
| Clock period | Selected separately for each core/PDK pair |

Relative sizing derives the floorplan from mapped cell area and utilization.
It does not impose equal physical areas across PDKs. Keep the RTL and the
existing interface constraints fixed. Changing the clock also scales the
existing percentage-based I/O delays; document that constraint policy.

## 1. Screen reference clocks

`experiments/suite/clock-screen.json` schedules 96 jobs: four cores, three PDKs,
eight periods (10, 15, 22, 33, 50, 75, 110 and 165 ns), seed 101. Each job starts
from RTL and stops after post-route STA. This includes placement and routing;
it is not a short synthesis test or a completed signoff run.

Start with one thread per flow and six simultaneous flows. This is an initial
execution setting, not a measured throughput optimum. Use one guarded environment
launched from an ordinary SSH shell. Do not nest Nix/PRoot environments.

Collect the campaign, then inspect candidates with:

```bash
python3 scripts/calibrate_clock.py --label fresh-clock-screen-v1 \
  --fraction 1 --min-setup-margin 0.10
```

The helper selects the fastest qualifying period in the contiguous qualifying
tail ending at the slowest tested period. Qualifying requires observed setup
slack / period >= 0.10. `--fraction 1` keeps the tested period unchanged.
This is a reproducible reference-selection rule, not an Fmax measurement or a
claim that every PDK has exactly the same timing difficulty.

Incomplete jobs, missing evidence and unresolved tool/infrastructure failures
block selection. Classified design failures count as unsuccessful settings.
If no candidate qualifies, inspect the failure before extending the clock
range. Congestion, hold violations and crashes are not automatically fixed by
a slower clock. The helper writes no pair files unless all planned pairs have
candidates; read `calibration.json` for individual outcomes.

## 2. Validate the references

After reviewing the screen, the same calibration command with `--write` stores
candidate clocks in `configs/designs/<core>/pdks/<pdk>.json`.
Do not change source files while a campaign is running.

`experiments/suite/reference-validation.json` then schedules 36 full flows:
one C0 per pair, seeds 201, 202 and 203. Each must pass the study's required
checks and have setup slack / period >= 0.10. Inspect all reported electrical
and antenna checks too; the repository's pass flag is not fabrication signoff.
This three-seed check qualifies a reference for the next stage, not its true
success probability. If validation fails, investigate it and document any
reference change before validating again. Do not silently remove a failing seed.

## 3. Freeze and run the DSE

Freeze the validated references, tool/PDK/RTL versions, constraints, execution
settings, failure policy and analysis choices in Git before formal runs.
Check at least one same-seed repeat per pair for residual execution variability.
Repeated runs of a seed are not additional independent seeds.

The initial DSE is the full 2 x 2 x 2 grid: utilization 40/50%, density 60/70%,
and synthesis AREA 0/DELAY 1. Check the merged configurations contain all eight
distinct combinations. Hold each pair's reference clock fixed throughout this
grid; clock is not a fourth DSE factor in this stage.

Use seeds 1001 through 1020: 480 full flows per core, 1,920 for the four-core
suite. Treat 20 as the initial sample size, not a guarantee of precise rankings.
Report success rates and uncertainty alongside area/timing distributions and
cross-PDK rankings. Normalize against C0 within each core/PDK, retain failures,
and distinguish design outcomes, tool failures and infrastructure failures.
Use separate seeds for any later confirmation of selected winners.
