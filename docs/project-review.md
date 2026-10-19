# Review fixes and remaining experimental work

Implemented fixes:

- Calibration uses the frozen manifest and config hashes, requires all planned
  samples and execution/seed/STA evidence, rejects non-clock changes and duplicate
  rows, and does not write any clock files if any sweep is incomplete or lacks a
  passing period. Reference periods are labeled pilot choices rather than Fmax.
- Ranking splits bootstrap credit among co-best candidates, handles a sole
  eligible candidate, and supports predeclared practical tie tolerances.
- Cross-PDK transfer selects source winners before testing destination feasibility.
  Missing or failing transfers remain visible; regret is undefined where needed.
- Unsupported slack-derived Fmax and area-delay metrics are disabled, including
  those requested by old frozen settings. Workload power is not a default ranking.
- Nonsignificant effects are labeled no_clear_difference. Resampling is labeled
  within-sample selection stability, with a consistent candidate set and tie policy.
- Pooling requires compatible frozen provenance. Machine/resource mixtures within
  a design/PDK comparison and changing configuration values are rejected.
- Missing any requested timing corner now yields unknown evidence.
- CPU affinity, visible cgroup-v2 limits, nice level and flow concurrency are
  recorded in per-run tables and summary groups. Defaults are two threads, one flow.
- Shared presets and frozen CLI selections cover all four unchanged RTL designs.

The raw design submodules and the pinned LibreLane source were not modified.
The AES manifest's architecture comment was corrected; no synthesis sources or
architecture were changed. The optional pilot varies only shared flow knobs.

Validation uses the existing unit tests plus regression fixtures for the review
findings and prepared configurations. It does not substitute for RTL simulation
or an actual GDS run. No new timing results or calibrated clock values were
invented. The parent repository still needs your initial commit, and each
reference clock needs measured evidence before a formal phase=study campaign.

The remaining work is experimental: validate upstream RTL, bring up each
platform, choose and document fixed comparison targets, measure the pilot,
then plan independent confirmation. Same-seed repeatability, selected corner
coverage, failure classification and resource comparability remain part of the
protocol. Four designs broaden evidence but do not prove universal flow behavior.

See running-study.md for commands and analysis-guide.md for statistical definitions.
