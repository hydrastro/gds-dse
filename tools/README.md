# Tool sources

Add LibreLane as the `tools/librelane` submodule using the root README.
The generated archive intentionally excludes cloned repositories and Git metadata.
The study's custom routing step stays in `scripts/flow_entry.py`; the upstream
LibreLane checkout remains unmodified. Its supported commit is checked in
`scripts/study.py`, and the upstream routing script hash in `flow_entry.py`.
