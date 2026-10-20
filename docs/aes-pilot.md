# AES pilot

AES uses the same flow settings and runner as the other designs. Its source
submodule stays unchanged.

Start with [the three-PDK baseline](running-study.md): ten seeds per PDK,
thirty attempts for AES. The
[study plan](study-plan.md#what-were-the-extra-aes-settings) explains the optional
2 × 2 preset and its overlap with existing variants. It is not required for the
first dataset.

The files in `experiments/aes/` are convenience selections.
`experiments/suite/pilot-baseline.json --designs aes --pdks sky130A` selects the
same baseline scope as `experiments/aes/baseline-10.json`.
Choose one route; running both duplicates seeds.

The SKY130-only convenience file is for a restricted selection. Pass
`--pdks sky130A gf180mcuD ihp-sg13g2` for the complete platform comparison.
