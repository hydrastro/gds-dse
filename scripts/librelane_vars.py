#!/usr/bin/env python3
"""Snapshot every configuration variable of LibreLane's Classic flow at the pinned commit.

Run inside the pinned Nix shell (or any environment where LibreLane imports):

    python3 scripts/librelane_vars.py            # rewrite configs/librelane_variables.json
    python3 scripts/librelane_vars.py --check    # fail if the snapshot is stale

`prepare` validates every flow key, type and choice against this snapshot, so a
typo such as PL_TIME_DRIVEN (instead of PL_TIMING_DRIVEN) fails before any run.
Regenerate and commit it whenever the LibreLane submodule pointer moves.
"""
import argparse
import decimal
import json
import subprocess
import sys
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "tools/librelane"
OUTPUT = ROOT / "configs/librelane_variables.json"


def ser(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def describe(kind):
    origin, args = typing.get_origin(kind), typing.get_args(kind)
    optional = False
    if origin is typing.Union and type(None) in args:
        optional = True
        rest = [a for a in args if a is not type(None)]
        if len(rest) == 1:
            kind = rest[0]
            origin, args = typing.get_origin(kind), typing.get_args(kind)
    if origin is typing.Literal:
        return {"kind": "choice", "choices": [ser(a) for a in args], "optional": optional}
    name = getattr(kind, "__name__", None) or repr(kind)
    simple = {"bool": "bool", "int": "int", "Decimal": "number", "float": "number", "str": "str"}
    return {"kind": simple.get(name, "other"), "python_type": name if name in simple else repr(kind),
            "optional": optional}


def snapshot():
    sys.path.insert(0, str(FLOW_ROOT))
    from librelane.flows import Flow
    import librelane.flows.classic  # noqa: F401  (registers the flow)
    from librelane.config.flow import option_variables, pdk_variables, scl_variables
    classic = Flow.factory.get("Classic")
    variables = {}

    def add(var, owner):
        entry = variables.setdefault(var.name, {
            **describe(var.type), "default": ser(var.default), "units": getattr(var, "units", None),
            "description": var.description, "pdk": bool(getattr(var, "pdk", False)), "owners": [],
            "deprecated_names": [n if isinstance(n, str) else n[0] for n in (var.deprecated_names or [])]})
        if owner not in entry["owners"]:
            entry["owners"].append(owner)

    for var in classic.config_vars:
        add(var, "flow:Classic")
    for step in classic.Steps:
        for var in step.config_vars:
            add(var, step.id)
    for group, values in [("common:option", option_variables), ("common:pdk", pdk_variables),
                          ("common:scl", scl_variables)]:
        for var in values:
            add(var, group)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=FLOW_ROOT, capture_output=True,
                            text=True).stdout.strip()
    return {"schema": 1, "librelane_commit": commit, "flow": "Classic",
            "steps": [step.id for step in classic.Steps], "variables": variables}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Compare instead of writing.")
    args = parser.parse_args()
    data = json.dumps(snapshot(), indent=1, sort_keys=True) + "\n"
    # Absolute default paths (e.g. FALLBACK_SDC) differ per checkout; keep them relative.
    data = data.replace(str(FLOW_ROOT.resolve()) + "/", "<librelane>/")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != data:
            raise SystemExit(f"{OUTPUT} is stale. Regenerate it with scripts/librelane_vars.py and commit it.")
        print("LibreLane variable snapshot is current.")
        return
    OUTPUT.write_text(data)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
