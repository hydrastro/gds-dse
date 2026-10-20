# Optional integration RTL

The supplied AES, SHA-256, ChaCha and PRINCE benchmarks use their upstream top
modules. No custom wrapper is required or supplied.

If a future benchmark needs an adaptation, place it under
`wrappers/<design>/` and list it in the manifest's `project_sources`.
Paths are relative to the study root; project sources follow upstream sources
in the synthesis list.

Treat the wrapper as part of the benchmark, validate its behaviour and keep it
fixed across the comparison. Adding a wrapper is a separate design decision,
not a prerequisite for varying flow settings.
