# Study plan

The research question is whether an implementation choice remains convincing
when a flow is repeated with different seeds, and whether that choice transfers
to another PDK.

The first task is deliberately smaller: measure one unchanged design, on one
PDK, under one baseline configuration.

## The first dataset

Use AES, `sky130A`, `C0` and routing seeds 1–10. Keep the source revision, top
module, clock target, SDC, tools, PDK, machine and execution settings fixed.
Request the complete flow, including GDSII and the configured checks.

Run seed 1 first. If the setup is appropriate, it can count toward the ten runs;
then execute seeds 2–10 without changing inputs. If bring-up requires a change,
retain the first result as diagnostic evidence and start the dataset under the
new fixed setup. Do not silently replace unsuccessful seeds with favourable ones.

Report:

- how many attempts reached each relevant stage and passed the declared checks;
- setup and hold slack, area and wirelength for the stated data population;
- spread across seeds, including identical outcomes if there is no observed spread;
- runtime and resource context, with download/setup costs identified.

This measures sensitivity to the **initial global-routing net order**.
Capacity perturbation is set to zero. Synthesis, placement and later tool seeds
are not independently swept. A small spread does not establish that the entire
flow is insensitive to randomness.

## What comes after the baseline?

Choose the next experiment from the question, rather than running every preset.

| Question | One design, one PDK | Purpose |
| --- | ---: | --- |
| Does the baseline vary across seeds? | 1 config × 10 seeds = **10** | First dataset |
| Does placement density affect the result? | 3 configs × 10 seeds = **30** | `Cminus`, `C0`, `Cplus` |
| Do density and synthesis strategy interact? | 4 configs × 10 seeds = **40** | Optional 2 × 2 comparison |
| Does the preferred configuration transfer? | Same candidates × 10 seeds on a second PDK | Evaluate the source choice on the destination |

The second-PDK baseline alone costs another ten runs and checks whether baseline
seed sensitivity differs. It cannot establish transfer of a *winning
configuration* when only one candidate was tested.

A three-density study over two PDKs is 60 runs for one design. Four designs,
three PDKs, three densities and ten seeds would be 360 runs. Neither expansion
is part of the first ten-run task.

A ten-seed sample is a pilot budget, not a promise of sufficient statistical
precision. Decide whether more seeds or designs are useful after seeing the
spread, failures, effect sizes and runtime. Keep conclusions specific to the
tested candidates, designs, operating targets and PDK versions.

## Why three densities?

The existing settings are:

| Configuration | Placement density | Core utilisation | Synthesis strategy |
| --- | ---: | ---: | --- |
| `Cminus` | 55% | 50% | `AREA 0` |
| `C0` | 60% | 50% | `AREA 0` |
| `Cplus` | 65% | 50% | `AREA 0` |

This is a local sensitivity experiment around the baseline. It can show whether
a modest density change has an effect larger than the observed seed spread.
It cannot locate a global optimum or describe every interaction.

More density points are useful only when they resolve a specific uncertainty:
for example, an apparent trend or a failure boundary. Adding many knobs at once
would answer a broader and more expensive question.

Core utilisation and placement density are different settings. Utilisation
helps determine the floorplan size; density guides cell placement within that
floorplan. Neither changes the RTL.

## What were the extra AES settings?

The additional `experiments/aes/` files select AES and general flow variants.
They do not add an AES feature, S-box architecture or custom wrapper.

The proposed 2 × 2 experiment contains these shared variants:

| Variant | Density | Strategy | Relationship to existing settings |
| --- | ---: | --- | --- |
| `C0` | 60% | `AREA 0` | Baseline |
| `pilot_d70_area0` | 70% | `AREA 0` | Same effective settings as `s1__PL_TARGET_DENSITY_PCT__70` |
| `pilot_d60_delay1` | 60% | `DELAY 1` | Same effective settings as `s1__SYNTH_STRATEGY__DELAY1` |
| `pilot_d70_delay1` | 70% | `DELAY 1` | Both changes together |

The equivalences hold for the supplied baseline; inspect the merged config
after changing defaults. Do not run the duplicate names as different candidates.

The fourth cell exists to answer a particular question: does changing synthesis
strategy alter the effect of density? It is useful for that interaction study.
It is unnecessary for the initial baseline or a density-only experiment.
The 2 × 2 preset is an alternative later study, not another mandatory layer on
top of the existing knob catalogue.

## Keep the design fixed

The benchmarks are the upstream `aes`, `sha256`, `chacha` and `prince` top
modules, including the register interface already present in each repository.
The manifests list their existing source files. `project_sources` is empty for
these four designs.

A clock, source list and SDC are necessary implementation inputs. Record and
freeze them; they are not experimental factors unless the question explicitly
calls for varying them. A new wrapper, different internal top or RTL optimisation
would define another benchmark and needs a separate rationale.

Study each design's configuration effects separately. Comparing AES area with
SHA-256 area does not compare implementations of the same cryptographic task.
Adding more designs tests how broadly a pattern holds.

## Seeds, repeats and scheduling

The routing seed is the intentional intervention. `order_seed` only shuffles
the execution schedule, which is saved once at preparation. Leave the default
`order_seed` alone; no manual shuffling is needed.

Seed values 1–10 and the same `order_seed` may be reused across designs,
configurations and PDKs. Equal seed numbers do not imply equal physical changes
in different netlists. Duplicate seed entries in a single seed list are rejected.

Repeating the same design/PDK/configuration/seed is a repeatability diagnostic,
not another independent seed. If unexpected variability needs investigation,
repeat a selected seed and compare physical metrics; wall time can vary even
when the layout is unchanged. This diagnostic is separate from the ten distinct
seed observations.

For a later multi-configuration comparison, prepare the candidates together so
their runs share a shuffled schedule. An earlier baseline is not automatically
a substitute for concurrent controls: consider time, machine and environment
changes before reuse. The analyser's compatibility checks help, but cannot
detect every experimental confound.

## Choosing a clock is not a required clock sweep

The supplied 25 ns fallback has no measured validation attached. Bring up the
design, inspect timing and select a defensible fixed target. An application
requirement or a documented feasible engineering target can serve that purpose.
A per-PDK target belongs in `configs/designs/<design>/pdks/<pdk>.json`.

The optional calibration sweep is available when selecting a target actually
needs a sweep. It is not required to characterise seed variation, and its
headroom rule does not measure Fmax. See [clock selection](running-study.md#choose-a-fixed-clock).

Across PDKs, record differing clocks, libraries, corners and loads as part of
the operating conditions. Compare configuration effects within each PDK first;
the platforms are not electrically equivalent merely because their corner
roles have similar names.
