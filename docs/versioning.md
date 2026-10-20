# Versioning and provenance

A seed comparison needs the same implementation inputs. If the RTL, tools or
PDK change between runs, the observed difference is no longer attributable to
the seed alone.

Git records the chosen sources. Prepared campaigns record the configuration and
execution evidence. The README does not need to repeat every hash.

## Source revisions

These are the pins used by this source package. The parent repository's
submodule entries are authoritative after initialisation.

| Path | Upstream repository | Commit |
| --- | --- | --- |
| `tools/librelane` | [librelane/librelane](https://github.com/librelane/librelane) | `01ddb25e4424f6f5aaf6b951b14a2b6fdcf737c0` |
| `designs/aes` | [secworks/aes](https://github.com/secworks/aes) | `80dc4718e1dcbbdb4b0dd1bdb393d8f7b98981dc` |
| `designs/sha256` | [secworks/sha256](https://github.com/secworks/sha256) | `837c5cc396f001d18f2c765721c585716eb439ae` |
| `designs/chacha` | [secworks/chacha](https://github.com/secworks/chacha) | `7eaba360df9fed9fc2db98d5f3df81cf01e5b604` |
| `designs/prince` | [secworks/prince](https://github.com/secworks/prince) | `f40631db7a3fe2eb7a09d63696ad610b133db163` |

`.gitmodules` stores paths and URLs. The Git entries in the parent commit
store the submodule commits. Copying `.gitmodules` alone cannot recreate those
entries in a fresh repository; use the [initialisation commands](setup.md).

## Toolchain and PDKs

Three layers matter:

1. The LibreLane submodule pins the flow source.
2. `tools/librelane/flake.lock` pins the Nix dependencies.
3. `tools/librelane/librelane/pdk_hashes.yaml` selects compatible Ciel PDK builds.

The current PDK revision entries are:

| Ciel family | Revision |
| --- | --- |
| `sky130` | `8afc8346a57fe1ab7934ba5a6056ea8b43078e71` |
| `gf180mcu` | `54435919abffb937387ec956209f9cf5fd2dfbee` |
| `ihp-sg13g2` | `c4b8b4e5e7a05f375cca3815d51b3a37721fbf5c` |

The experiment selects `sky130A`, `gf180mcuD` or `ihp-sg13g2`.
PDK downloads live outside Git under `pdks/`. A manual PDK installation
overrides automatic management and needs an accurate revision record.

The runner also records available executable versions and hashes. Those are
useful evidence that the intended binaries actually ran; a source pin alone
does not establish the execution environment.

## Routing-seed adapter

`scripts/flow_entry.py` substitutes a study step for the initial
`OpenROAD.GlobalRouting` step. It generates an instrumented Tcl file inside
each run; it does not edit the upstream checkout.

The supported LibreLane commit and original `grt.tcl` hash are in
`configs/study.json`. A mismatch stops the run. The tool log must contain the
seed marker before collection marks `seed_applied` as true. Later routing
passes retain their normal settings.

## What needs to be reported?

| Evidence | Why keep it? |
| --- | --- |
| Study commit, RTL/top/parameters, flow and PDK revisions | Identify the implementation being compared |
| Resolved config, SDC and source hashes | Establish which inputs were held fixed |
| Routing seed, scheduling seed and saved order | Identify the intervention and run schedule |
| Machine, thread count, concurrency and CPU policy | Explain execution differences and runtime |
| Commands, status, logs and stage metrics | Audit successes, failures and missing data |
| Timestamps, free disk and host details | Diagnose execution problems; usually appendix material |

Do not transcribe these fields manually into the README. Preparation and
execution save them with the campaign. In a paper, report the essential
experimental settings and link the complete archive.

The protocol still needs human decisions: the research question, clock rationale,
choice of configurations, failure classification and limits of the conclusion.

## Updating a dependency

Make updates between campaigns, on a separate revision:

1. Choose the intended upstream commit and check it out inside the submodule.
2. For a LibreLane update, review the seed adapter, PDK support and Nix lock.
   Update `configs/study.json` only after that review.
3. Regenerate the variable snapshot in the new pinned shell with
   `python3 scripts/librelane_vars.py`; check the knob catalogue and tests.
4. Validate the affected RTL/flow, stage the submodule pointer and study changes,
   then commit.
5. Prepare new campaign labels. Retain the old revision and its results.

Do not use `git submodule update --remote` during a campaign. Updating to whatever
is newest would change the experiment.

## Archive a result

Preserve the complete `results/<label>/` directory, its source revision and a
checksum for the archive. Commit selected small tables, figures and interpretation
under `reports/`, with links to their raw evidence. Excluding results from Git
does not back them up.

The runner verifies current input hashes when resuming. Resume an interrupted
campaign from its recorded checkout and working location. An archive supports
audit and reconstruction; it is not a promise that an in-progress run can be
moved to another path and resumed without adjustment.
