# Study plan

The study explores flow parameters and asks how reliable the resulting
configuration comparisons are under seed variation and across **SKY130,
GF180MCU and IHP SG13G2**. It compares baseline-relative metrics and
configuration rankings rather than absolute QoR values between technologies.

The knobs include clock target, core utilisation, placement density, synthesis
strategy, hierarchy, and selected CTS and routing options. Begin with a small
selection and expand it later. Seeds provide repeated observations of each
configuration.

All three PDKs are part of the project. Bringing up SKY130 first is an execution
order, not a reduction of the scientific scope. AES is the first benchmark;
SHA-256, ChaCha and PRINCE are the planned wider suite.

## Preliminary baseline check

The C0-only example below is a diagnostic subset of the study. The DSE
experiment uses several selected configurations, including C0.

For the baseline alone, use AES, all three PDKs, `C0` and routing seeds 1–10:
**one design × three PDKs × one configuration × ten seeds = 30 runs**.

For each design/PDK/configuration, keep its clock and other settings fixed
across seeds. Across configurations, vary the declared knobs, including the
clock target when it is selected. Keep the RTL revision, top, flow revision
and resource policy fixed. The PDK is the intentional platform
factor; its library and physical rules cannot be identical across platforms.

Bring up the platforms one at a time if useful. The documented campaign starts
with seed 1 on each PDK, then adds seeds 2–10. The first three runs can count
toward the thirty if the frozen inputs remain unchanged. If integration requires
changes, retain those attempts as diagnostics and establish the dataset under
the corrected setup. Do not replace unsuccessful seeds with favourable ones.

Report separately for each platform:

- how many attempts reached each relevant stage and passed the declared checks;
- setup and hold slack, area and wirelength for the stated data population;
- spread across seeds, including identical outcomes if there is no observed spread;
- runtime and resource context, with download/setup costs identified.

This measures sensitivity to the **initial global-routing net order**.
Capacity perturbation is set to zero. Synthesis, placement and later tool seeds
are not independently swept. A small spread does not establish that the entire
flow is insensitive to randomness.

## Configuration comparison across all three PDKs

The baseline measures seed sensitivity on each platform. To answer whether the
preferred configuration transfers, compare the same candidate configurations on
all three PDKs. A single candidate cannot establish a configuration ranking.

| Candidate set | AES on all three PDKs | Purpose |
| --- | ---: | --- |
| `C0` | 1 × 3 × 10 = **30** | Baseline seed sensitivity |
| `Cminus`, `C0`, `Cplus` | 3 × 3 × 10 = **90** | Local density effects and cross-PDK ranking |
| Two densities × two synthesis strategies | 4 × 3 × 10 = **120** | Optional interaction study |

These are total sizes of alternative campaign definitions, not an instruction
to run all three campaigns. Choose the candidate set before the comparison.
If C0 was already measured, reuse needs compatible inputs and a defensible
execution/blocking strategy; the old baseline is not automatically concurrent
control data. Never count repeated seed cells as independent observations.

The three-density comparison across the full four-design suite would contain
**4 × 3 × 3 × 10 = 360 runs**. Start execution with AES, then extend the same
protocol to the other designs after their integration is ready.

Ten seeds per configuration/PDK is a pilot budget, not a guarantee of precision.
Use spread, failures, effect sizes and runtime to decide whether more seeds or
candidate settings resolve a useful uncertainty. All three PDKs remain in scope.
Keep conclusions specific to the tested candidates, designs and operating targets.

## A density-only example

The existing settings are:

| Configuration | Placement density | Core utilisation | Synthesis strategy |
| --- | ---: | ---: | --- |
| `Cminus` | 55% | 50% | `AREA 0` |
| `C0` | 60% | 50% | `AREA 0` |
| `Cplus` | 65% | 50% | `AREA 0` |

This is one possible local sensitivity experiment within the broader DSE. It
does not define the full parameter set. It can show whether
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
The 2 × 2 preset is an example of a focused interaction study within the broader
DSE. It is not an additional mandatory campaign.

## Keep the design fixed

The benchmarks are the upstream `aes`, `sha256`, `chacha` and `prince` top
modules, including the register interface already present in each repository.
The manifests list their existing source files. `project_sources` is empty for
these four designs.

A source list, clock and SDC define the implementation inputs. Freeze them
within each configuration; clock target and other declared constraint knobs
may vary deliberately between configurations. A new wrapper, different internal top or RTL optimisation
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
design, inspect timing and select a defensible baseline reference target. An application
requirement or a documented feasible engineering target can serve that purpose.
A per-PDK target belongs in `configs/designs/<design>/pdks/<pdk>.json`.

The optional calibration sweep is available when selecting a target actually
needs a sweep. It is not required to characterise seed variation, and its
headroom rule does not measure Fmax. See [clock selection](running-study.md#choose-a-fixed-clock).

Across PDKs, record differing clocks, libraries, corners and loads as part of
the operating conditions. Compare configuration effects within each PDK first;
the platforms are not electrically equivalent merely because their corner
roles have similar names.
