# Adding another design

Keep upstream RTL in `designs/<id>/` and integration settings in
`configs/designs/<id>/`. Shared variants stay in `configs/variants/`.
This avoids maintaining a separate knob catalogue for every core.

## Register the sources

Add the design repository as a submodule and check out an intentional commit.
Use the same pattern as [setup](setup.md), replacing the repository URL, path and
commit. Then stage the new submodule pointer in the parent repository.

The optional ASCON manifest already has a directory under
`configs/designs/ascon/`, but its top, clock and source list are placeholders.
There is no ASCON source submodule in this package. Complete those fields from
the actual repository before selecting ASCON.

## Describe the benchmark

Create or fill `configs/designs/<id>/design.json` with:

- the upstream RTL root and existing top module;
- clock port and an initial, explicitly unvalidated clock target;
- ordered synthesizable sources, include directories, defines and parameters;
- an SDC suitable for the actual clock and interface;
- any other files that must be included in input hashing.

Use [configuration](configuration.md) for path and merge rules. Reuse the
upstream top when suitable. A custom wrapper is optional and changes the
benchmark boundary; record it as part of the benchmark if one is needed.
The current four designs use no custom project wrapper.

## Validate before collecting a dataset

Run the design's existing functional tests with appropriate known-answer
vectors. Record its algorithm/version and parameter choices. Exclude
testbench-only files and FPGA-specific primitives from synthesis inputs;
any required ASIC adaptation needs its own review and functional validation.

Start with one seed on one PDK using the shared baseline preset and
`--designs <id>`. Check synthesis structure, expected state elements,
nonconstant outputs, timing constraints, routing and physical checks.
Fix integration problems before freezing a seed comparison.

A synthesis-only campaign can help diagnose elaboration problems. It does not
count as a full RTL-to-GDSII observation. Functional tests, synthesis checks and
LVS establish different properties.

## Organise experiments

Use `experiments/<id>/` for design-specific campaign definitions, and
`experiments/suite/` for shared selections. Passing `--designs <id>` to a
shared preset is sufficient; copying every preset is unnecessary.

Add the design to a multi-design campaign only when its own baseline and fixed
clock are ready. Additional designs improve coverage, but their raw area and
timing values should not be pooled as if they implement the same workload.

Keep the first campaign small. Follow [the run guide](running-study.md) for
the one-plus-nine baseline pattern.
