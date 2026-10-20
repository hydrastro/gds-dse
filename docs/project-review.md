# Validation

Software checks validate the experiment machinery. RTL simulation and real
implementation runs validate different parts of the research setup.

## Runner and analysis checks

From the study root:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/variants.py check
```

The tests cover configuration merging, immutable inputs and scheduling,
collection, interruption handling, and analysis edge cases. Regression fixtures
include incomplete clock sweeps, tied ranks, failed transfers, missing timing
corners and incompatible campaign pooling.

These checks do not invoke a complete EDA flow. Passing them does not establish
functional RTL correctness, routability or timing closure.

## Experimental evidence still needed

For each selected design and platform, retain:

1. Upstream functional-test commands and results.
2. The chosen top, parameters, clock, SDC and their rationale.
3. A real full-flow attempt and inspection of its synthesis, routing, timing,
   DRC, LVS and GDS outputs.
4. A fixed-input seed dataset, including failures.
5. Analysis that states its sample size, populations and limits.

The source package includes no measured timing or physical implementation
dataset. Per-PDK clock files are empty until a target is selected and documented.

## Interpretation

The initial routing seed is the declared intervention. Same-seed differences
may reveal additional nondeterminism or environmental effects, and require
investigation before attributing all spread to that seed.

The analysis preserves unsuccessful transfers, splits credit for tied rankings
and checks provenance before pooling. These safeguards prevent some misleading
summaries; they do not replace a sound experiment.

See [the study plan](study-plan.md) for the scope of the first ten runs and
[the analysis guide](analysis-guide.md) for the statistical definitions.
