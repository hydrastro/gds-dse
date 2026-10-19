# AES selection of the shared pilot

The previous AES-only experiment additions changed no RTL. The shared workflow
now covers AES, SHA-256, ChaCha and PRINCE without duplicate design-specific
experiment presets. See [running-study.md](running-study.md) and select AES with
`--designs aes`. Existing experiments/aes files remain optional legacy selections.

The calibration, ranking and transfer bugs from the initial review have been
corrected. See [project-review.md](project-review.md) and the updated analysis guide.
