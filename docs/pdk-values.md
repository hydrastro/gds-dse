# Where every baseline value comes from

C0, the baseline every result is normalised against, is **LibreLane's out-of-the-box
Classic flow for each PDK**, written out explicitly. Nothing in C0 is tuned. This page
lists every value, its source and the few deliberate deviations.

## Sources (exact versions)

| What | Version | File(s) |
|---|---|---|
| LibreLane | `01ddb25e` (3.0.16) | `librelane/config/pdk_compat.py`, `flows/classic.py`, `scripts/base.sdc` |
| sky130A | open_pdks `8afc8346` (pinned in `pdk_hashes.yaml`) | `sky130/openlane/config.tcl`, `sky130/openlane/sky130_fd_sc_hd/config.tcl` |
| gf180mcuD | open_pdks `54435919` | `gf180mcu/openlane/config.tcl`, `gf180mcu/openlane/gf180mcu_fd_sc_mcu7t5v0/config.tcl` |
| ihp-sg13g2 | IHP-Open-PDK `c4b8b4e5` | `ihp-sg13g2/libs.tech/librelane/config.tcl`, `…/sg13g2_stdcell/config.tcl` |

For sky130 and gf180mcu, LibreLane's `pdk_compat.py` fills in the values those older
configs omit: fanout 10, clock uncertainty 0.25 ns, clock transition 0.15 ns,
derating 5%, I/O delay 20%. IHP sets the same five values itself.

## Per-PDK values (`configs/platforms.json`)

| Setting | sky130A | gf180mcuD | ihp-sg13g2 | Source |
|---|---|---|---|---|
| Standard-cell library | `sky130_fd_sc_hd` | `gf180mcu_fd_sc_mcu7t5v0` | `sg13g2_stdcell` | PDK default |
| Supply (typical) | 1.8 V | 5.0 V | 1.2 V | PDK |
| Site / row height | unithd 0.46 × 2.72 µm | 0.56 × 3.92 µm | 0.48 × 3.78 µm | PDK |
| `MAX_FANOUT_CONSTRAINT` | 10 | 10 | 10 | PDK / pdk_compat |
| `CLOCK_UNCERTAINTY_CONSTRAINT` | 0.25 ns | 0.25 ns | 0.25 ns | pdk_compat / PDK |
| `CLOCK_TRANSITION_CONSTRAINT` | 0.15 ns | 0.15 ns | 0.15 ns | pdk_compat / PDK |
| `TIME_DERATING_CONSTRAINT` | 5% | 5% | 5% | pdk_compat / PDK |
| `IO_DELAY_CONSTRAINT` | 20% | 20% | 20% | pdk_compat / PDK |
| `OUTPUT_CAP_LOAD` | 33.442 fF | 72.91 fF | 6.0 fF | PDK |
| Input driving cell | `inv_2/Y` | `inv_1/ZN` (clock `inv_4/ZN`) | `buf_4/X` | PDK |
| `MAX_TRANSITION_CONSTRAINT` | 0.75 ns | 3 ns | not set | PDK |
| `MAX_CAPACITANCE_CONSTRAINT` | 0.2 pF | 0.2 pF | not set | PDK |
| `GPL_CELL_PADDING`, `DPL_CELL_PADDING` | 0, 0 | 0, 0 | 0, 0 | PDK |
| Routing layers | met1–met5 | Metal2–Metal5 | Metal2–TopMetal2 | PDK |
| STA corners | 9 (nom/min/max RC × tt/ss/ff) | 9 (same structure) | 3 (nominal RC × typ/slow/fast) | pdk_compat / PDK |
| Adder maps (FA/RCA/CSA) | available | none | none | PDK / pdk_compat |

Shared flow settings (`configs/defaults.json`): `FP_CORE_UTIL` 50, which is LibreLane's
default. `PL_TARGET_DENSITY_PCT` is 60, which is LibreLane's automatic value
`util + 5·padding + 10` with padding 0 on all three PDKs. It is written explicitly so
that the density knob and the utilisation knob are independent. Every other setting is
LibreLane's default.

## Deliberate deviations from the PDK defaults

1. **Timing is judged at matched corners.** The PDK defaults only check *typical*
   corners (`*tt*` / `*typ*`), and the three PDKs have different corner sets (9 vs 3).
   This study checks **setup at the slow PVT corner and hold at the fast PVT corner,
   both with nominal RC**, a corner every PDK has:

   | | sky130A | gf180mcuD | ihp-sg13g2 |
   |---|---|---|---|
   | setup | `nom_ss_100C_1v60` | `nom_ss_125C_4v50` | `nom_slow_1p08V_125C` |
   | hold | `nom_ff_n40C_1v95` | `nom_ff_n40C_5v50` | `nom_fast_1p32V_m40C` |

   This is set both for LibreLane (`SETUP/HOLD_VIOLATION_CORNERS`) and for the study's
   own checks and metrics (`timing_corners`, used by `collect`), so a "timing pass"
   means the same thing on every PDK. All corners are still analysed and kept in
   `metrics_long.csv`.

2. **The SDC errors out instead of guessing.** `configs/constraints/single_clock.sdc`
   follows LibreLane's `base.sdc` value for value (I/O delay as a fraction of the period,
   driving cell, output load, uncertainty, transition, derating). Two differences: a
   missing clock port is an error rather than a silent virtual clock, and reset is timed
   like any other input.

3. **The clock period is measured, not looked up.** No PDK defines a clock target; it
   depends on the design. `scripts/calibrate_clock.py` sweeps 8 periods × 3 seeds for
   each design × PDK. It takes the fastest period at and above which every run meets
   setup at the matched slow corner, and sets the reference to 85% of that frequency.
   That puts timing under real pressure, so timing knobs matter, while C0 still passes.
   `phase: study` campaigns refuse to start until every design × PDK has been calibrated.

## How the knob levels respect the PDK differences

| Problem | How the catalogue handles it |
|---|---|
| Clock periods differ ~3× across PDKs | `CLOCK_PERIOD` levels are `$scale` of each calibrated reference |
| Slack margins in ns mean different things at different periods | `PL_RESIZER_SETUP_SLACK_MARGIN` levels are `$of` the clock (0, 2%, 5%) |
| Cells and rows have different sizes | CTS lengths and diameters are in **row heights** (`$rows`), converted to µm per PDK |
| Padding without more density makes placement infeasible | `GPL_CELL_PADDING` levels raise density by 5 per site, as LibreLane's automatic formula does |
| Adder maps exist only in sky130A | `SYNTH_ADDER_TYPE` has `effective_pdks: [sky130A]`; the generator prints a note, since on the other PDKs those variants equal C0 |
| A higher utilisation needs more density | The `FP_CORE_UTIL` 65 level also sets density 75 |

## What has been validated, and how

* Every catalogue knob name, type and choice exists at the pinned LibreLane commit
  (`scripts/variants.py check`, tests).
* Every job config of every suite campaign, loaded for ihp-sg13g2 through LibreLane's
  own `Config` and `Flow` code with the seeded routing step substituted exactly as the
  CLI does: 180 of 180 configs load. sky130A and gf180mcuD could not be loaded the same
  way in the authoring environment, because their libraries have to be built and the
  pre-built PDK download was blocked. Their corner names were checked against
  LibreLane's hard-coded lists in `pdk_compat.py`.
* The SDC was executed in a Tcl interpreter with each PDK's values and stub STA commands.
* The four suite designs elaborate and synthesise cleanly with Yosys from the exact
  source lists in their manifests.

Not validated here: an actual LibreLane run (OpenROAD, Magic, Netgen and KLayout need
the Nix environment). The first pilot on your machine is that check.
