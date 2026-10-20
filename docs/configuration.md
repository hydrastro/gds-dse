# Configuration reference

Use one shared definition for each flow variant. Select designs in the
experiment file or on the command line; there is no need to copy flow knobs
into every design's directory.

## Which file should change?

| Change | File |
| --- | --- |
| Top module, clock port, synthesis sources or parameters | `configs/designs/<design>/design.json` |
| Fixed clock target for one design/PDK | `configs/designs/<design>/pdks/<pdk>.json` |
| Shared baseline flow setting | `configs/defaults.json` |
| Standard-cell library and PDK-specific assumptions | `configs/platforms.json` |
| A candidate setting to compare with the baseline | `configs/variants/<name>.json` |
| Which designs, PDKs, variants and seeds to run | `experiments/<group>/<name>.json` |
| Threads, concurrency, checks, exported metrics and analysis policy | `configs/study.json` |
| Machine-specific manual PDK paths | `machine.local.json` (ignored by Git) |

A variant file is a flat object of overrides. For example,
`configs/variants/Cplus.json` contains a placement density of 65.
`C0.json` has no overrides: it uses the merged baseline.

The experiment file chooses combinations; it does not contain the RTL or
define the general flow knobs. Its job count is:

**designs × PDKs × configurations × seeds × repeats**

`--designs`, `--pdks`, `--configs` and `--seeds` on `prepare` replace the
respective lists. Their resolved values are frozen into the campaign.

## Merge order

Later layers override earlier values with the same key:

1. `configs/defaults.json` → `flow_overrides`
2. Selected PDK in `configs/platforms.json` → `overrides`
3. Design manifest → `clock_period_ns` and `flow_overrides`
4. `configs/designs/<design>/pdks/<pdk>.json`
5. Selected file in `configs/variants/`

Arrays are replaced. Documentation keys beginning with `//` are ignored as
settings. Other unspecified values come from the pinned LibreLane/PDK defaults.

Each job saves `config.json` and `config_origins.json`. The latter identifies
the winning layer for ordinary flow settings. LibreLane also saves its own
resolved configuration during execution. Inspect these when two candidate
names appear to describe the same settings.

## Source manifest paths

- `rtl_root` and `sdc` are relative to the study root.
- `sources` and `include_dirs` are relative to `rtl_root`.
- `project_sources` and `project_include_dirs` are relative to the study root.
  Project sources are appended after the upstream sources.
- `additional_input_files` lists other dependencies to hash, such as macro
  models or Tcl files. Paths are study-root-relative or explicitly absolute.

List synthesis sources in dependency order, with packages before users.
Testbenches do not belong in the source list. The supplied four-core suite
uses no additional project RTL.

Design identity, PDK/library selection, source paths, SDC paths, threads and
routing seed are managed separately. A flow variant cannot replace those
protected fields.

## Relative and platform-specific values

The runner supports these operators in override layers:

| Expression | Meaning |
| --- | --- |
| `{"$scale": 0.9}` | Multiply the value inherited from lower layers by 0.9 |
| `{"$add": 5}` | Add 5 to the inherited value |
| `{"$per_pdk": {"sky130A": "met4", "gf180mcuD": "Metal4", "ihp-sg13g2": "Metal4"}}` | Select a PDK-specific value |
| `{"$of": ["CLOCK_PERIOD", 0.02]}` | Use 2% of the final merged clock period |

These describe candidate settings; their existence does not require a clock
or routing-layer sweep. Keep the first baseline simple.

## Constraints and validation

`prepare` checks names, types and allowed choices against
`configs/librelane_variables.json`, a snapshot from the pinned Classic flow.
Deprecated names are rejected. It also checks the study's configuration
constraints, including the declared relationship between floorplan utilisation,
padding and placement density.

This catches configuration mistakes before an EDA run. It does not prove that
a design will place, route or meet timing.

The shared `configs/constraints/single_clock.sdc` creates one real clock and
takes electrical/timing values from the merged config. It times non-clock
inputs, including reset. Review suitability for a different interface; do not
add timing exceptions just to hide violations.

Study-only template values can use `STUDY_SDC_*` keys. Corresponding
`@STUDY_SDC_...@` placeholders are substituted at preparation and the rendered
SDC is hashed. These keys are not sent to LibreLane. Missing values or unused
study-only keys cause an error.

## Optional variant generation

The catalogue in `configs/knobs.json` and plans in `configs/plans/` support
later exploration. They are reference material, not the initial study scope.

```bash
python3 scripts/variants.py list
python3 scripts/variants.py check
python3 scripts/variants.py generate configs/plans/stage1-core-knobs.json --dry-run
```

A generation plan can describe one-at-a-time, factorial, Latin-hypercube or
explicit-list variants. Inspect its output and job count before generating
files without `--dry-run`. Generation creates files; execution still requires
an explicit `prepare` and `run`.

Some catalogue choices have companion changes or platform restrictions.
Consequently, a named knob is not always an isolated single-factor change.
Inspect the generated settings. Opt-in choices that introduce other randomness
or routing passes require a separate interpretation of the seed experiment.

Do not regenerate or edit inputs during a prepared campaign.
