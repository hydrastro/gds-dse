# Experiment definitions

A definition selects designs, PDKs, shared flow variants, seeds and repetitions.
Nothing runs because a file exists here.

The main study compares selected flow configurations across three PDKs, with
repeated seeds for each configuration. The baseline-only diagnostic uses `suite/pilot-baseline.json` with
`--designs aes --pdks sky130A gf180mcuD ihp-sg13g2`:
**ten seeds per PDK, thirty baseline attempts in total**.
The [run guide](../docs/running-study.md) starts with one seed on each PDK and
then the remaining nine. All three PDKs are part of the project scope.

The file's SKY130-only default remains available for a restricted bring-up
check. Always include the three-PDK selection for the intended comparison.

## Shared presets

Counts below are for the files as supplied, before CLI selections.

| File under `suite/` | Default jobs | Scope |
| --- | ---: | --- |
| `smoke.json` | 4 | One seed, four designs, SKY130 |
| `pilot-baseline.json` | 40 | Ten seeds, C0, four designs, SKY130 |
| `pilot-seed-check.json` | 16 | Two seeds repeated twice, four designs, SKY130 |
| `pilot-2x2.json` | 160 | Four configs, ten seeds, four designs, SKY130 |
| `baseline.json` | 120 | C0, ten seeds, four designs, all three PDKs |
| `seed-check.json` | 48 | Repeatability diagnostic across the full suite |
| `calibrate.json` | 288 | Eight clock targets, three seeds, full suite; stops after STA |
| `stage1.json` | 660 | Eleven configs, five seeds, full suite |

The last four are broader templates, not setup steps. `baseline.json` and
`stage1.json` use the stricter `study` phase.

CLI `--designs`, `--pdks`, `--configs` and `--seeds` replace corresponding
lists and are frozen into the prepared manifest. For example, selecting AES
alone reduces the SKY130 pilot counts by four.

## Design-specific presets

`aes/` retains convenience definitions for synthesis bring-up, full-flow
bring-up, repeatability, a baseline and the optional 2 × 2 comparison.
They overlap with the shared presets. Choose a single definition for a dataset;
do not run both names as independent samples.

`ascon/` contains templates for an optional ASCON integration. Its source
manifest still needs a real top, clock and file list before any run.

Use a new campaign label for each prepared batch. Labels identify saved runs;
variant names identify settings. Neither name changes the RTL.
