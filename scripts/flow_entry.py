#!/usr/bin/env python3
"""Classic LibreLane with a controlled INITIAL-global-routing seed.

Run inside the pinned LibreLane Nix environment. The upstream checkout is
read-only. A generated copy of its grt.tcl is saved in each affected step.
This is the study's ONLY intentional random intervention: later re-routing
(antenna repair, optional post-GRT resizer steps) and other tool seeds keep
their ordinary defaults. The reviewed script hash and the capacity-perturbation
setting come from configs/study.json (flow.seeded_step).
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "tools/librelane"
sys.path.insert(0, str(FLOW_ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from project import load_settings  # noqa: E402

SEEDED = load_settings(ROOT)["flow"]["seeded_step"]
EXPECTED_GRT_SHA256 = SEEDED["grt_script_sha256"]
ANCHOR = "source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl"
INJECTION = '''# Study intervention: random net ordering at the initial global route only.
set_global_routing_random -seed $::env(STUDY_GRT_SEED) -capacities_perturbation_percentage $::env(STUDY_GRT_CAPACITY_PCT)
puts "STUDY_SEED_APPLIED=$::env(STUDY_GRT_SEED) STAGE=initial_global_routing CAPACITY_PERTURBATION_PCT=$::env(STUDY_GRT_CAPACITY_PCT)"
'''


def marker(seed, capacity_pct):
    """The exact log line collect() requires as evidence that the seed was applied."""
    return f"STUDY_SEED_APPLIED={seed} STAGE=initial_global_routing CAPACITY_PERTURBATION_PCT={capacity_pct}"


def instrument(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != EXPECTED_GRT_SHA256 or source.count(ANCHOR) != 1:
        raise ValueError("Upstream grt.tcl differs from the reviewed source. Review the adapter before using another flow revision.")
    return source.replace(ANCHOR, INJECTION + ANCHOR)


def main():
    from librelane.config import Variable
    from librelane.steps import OpenROAD, Step, StepException
    from librelane.__main__ import cli

    @Step.factory.register()
    class SeededGlobalRouting(OpenROAD.GlobalRouting):
        id = SEEDED["id"]
        name = "Initial Global Routing with Recorded Seed"
        config_vars = OpenROAD.GlobalRouting.config_vars + [
            Variable("STUDY_GRT_SEED", int, "Nonzero seed for initial global-route net ordering.", default=1),
            Variable("STUDY_GRT_CAPACITY_PCT", int, "Edge-capacity perturbation percentage (0 = off).", default=0),
        ]

        def get_script_path(self):
            seed = self.config["STUDY_GRT_SEED"]
            capacity = self.config["STUDY_GRT_CAPACITY_PCT"]
            if not 1 <= seed <= 2147483647:
                raise StepException("STUDY_GRT_SEED must be in 1..2147483647.")
            if not 0 <= capacity <= 100:
                raise StepException("STUDY_GRT_CAPACITY_PCT must be in 0..100.")
            original = Path(super().get_script_path())
            try:
                generated = instrument(original.read_text())
            except ValueError as exc:
                raise StepException(str(exc)) from exc
            output = Path(self.step_dir) / "grt_seeded.tcl"
            output.write_text(generated)
            audit = {
                "stage": "initial_global_routing", "seed": seed,
                "original_script": str(original),
                "original_sha256": EXPECTED_GRT_SHA256,
                "generated_sha256": hashlib.sha256(generated.encode()).hexdigest(),
                "capacity_perturbation_pct": capacity,
                "note": "This file records intent; the tool log's STUDY_SEED_APPLIED marker confirms execution."
            }
            (Path(self.step_dir) / "seed_intent.json").write_text(json.dumps(audit, indent=2) + "\n")
            return str(output)

    cli()


if __name__ == "__main__":
    main()
