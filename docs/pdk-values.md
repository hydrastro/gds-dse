# PDK assumptions

The three platforms use different standard cells, electrical conditions and
physical rules. Their configuration effects can be compared, but they are not
three electrically equivalent implementations.

The source of truth for the study's explicit settings is
`configs/platforms.json`. Exact source revisions are in
[versioning](versioning.md). The tables here describe the supplied configuration;
they are not measured implementation results.

## Libraries and timing checks

| Setting | sky130A | gf180mcuD | ihp-sg13g2 |
| --- | --- | --- | --- |
| Standard-cell library | `sky130_fd_sc_hd` | `gf180mcu_fd_sc_mcu7t5v0` | `sg13g2_stdcell` |
| Nominal library supply | 1.8 V | 5.0 V | 1.2 V |
| Setup check | `nom_ss_100C_1v60` | `nom_ss_125C_4v50` | `nom_slow_1p08V_125C` |
| Hold check | `nom_ff_n40C_1v95` | `nom_ff_n40C_5v50` | `nom_fast_1p32V_m40C` |
| Output load | 33.442 fF | 72.91 fF | 6.0 fF |

The declared timing policy checks setup at a slow PVT corner and hold at a fast
PVT corner, using nominal RC. These are analogous corner roles, not equal
voltages, temperatures, delays or signoff coverage.

The selected names are used both by the flow's violation checks and by the
collector. Missing a required selected corner leaves the aggregated metric
unknown. Preserve all collected detailed metrics and the resolved flow settings
when reporting results.

## Shared explicit settings

| Setting | Value | Role |
| --- | ---: | --- |
| `FP_CORE_UTIL` | 50% | Floorplan utilisation |
| `PL_TARGET_DENSITY_PCT` | 60% | Baseline placement target |
| `MAX_FANOUT_CONSTRAINT` | 10 | Fanout constraint |
| `CLOCK_UNCERTAINTY_CONSTRAINT` | 0.25 ns | Clock uncertainty |
| `CLOCK_TRANSITION_CONSTRAINT` | 0.15 ns | Clock transition |
| `TIME_DERATING_CONSTRAINT` | 5% | Early/late timing derating |
| `IO_DELAY_CONSTRAINT` | 20% of clock period | Input/output delay budget |
| `GPL_CELL_PADDING`, `DPL_CELL_PADDING` | 0, 0 | Cell padding |

The pinned synthesis default is `AREA 0`. The explicit placement target keeps
the density knob independent of automatic density changes when utilisation is
modified.

These values define this study's baseline. C0 is **not an untouched stock run**:
it includes the study's selected timing checks, shared SDC and initial-routing
seed adapter, as well as the ordinary Classic flow.

For SKY130 and GF180, the baseline's documented source includes LibreLane's
`librelane/config/pdk_compat.py` and the open_pdks flow configuration.
For IHP, it includes the PDK's
`ihp-sg13g2/libs.tech/librelane/` configuration. The manifest also stores
reference information such as row dimensions; reference fields do not themselves
override the flow.

## Clock and interface

A PDK does not specify this benchmark's clock target. The current design
manifests provide a 25 ns fallback, with empty per-PDK override files.
Choose and document a target using the [run guide](running-study.md#choose-a-fixed-clock).

The SDC follows the pinned flow's basic single-clock conventions, requires a
real unambiguous clock port and times all other inputs. Review reset semantics,
interface timing, loads and unconstrained paths for the actual design.
The template is an experimental assumption, not a complete system specification.

If clock targets differ by PDK, report them. A slack difference then includes
the changed target. Start with within-PDK configuration effects before making
cross-PDK claims.

## What a pass means

`study_checks_pass` combines the required checks in `configs/study.json`:
successful execution, seed evidence, a full flow, GDS, post-route STA,
setup/hold, layout DRC, LVS and routing DRC.

Other checks, such as antenna, power-grid and electrical violations, are reported
but are not all required by that flag. Read them too. A pass means the declared
study checks passed; it is not a fabrication certificate.

Configuration preparation and software tests do not demonstrate physical-flow
success on any PDK. Validate each platform with actual runs before interpreting
its results.
