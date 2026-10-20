# Tool sources

`librelane/` is the flow submodule. Its source commit, Nix lock and PDK revision
list define the supported tool environment.

Use [setup](../docs/setup.md) to initialise it and
[versioning](../docs/versioning.md) when considering an update.

The study's seed adapter lives in `scripts/flow_entry.py`. It writes an
instrumented routing script inside each run, leaving the upstream checkout
unchanged. The supported commit and routing-script hash are recorded in
`configs/study.json`.
