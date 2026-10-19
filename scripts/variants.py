#!/usr/bin/env python3
"""Generate configuration variants and an experiment file from a plan.

    python3 scripts/variants.py list                         # show the knob catalogue
    python3 scripts/variants.py generate configs/plans/ascon-screen.json
    python3 scripts/variants.py check                        # validate catalogue vs LibreLane snapshot

Strategies
  ofat       one factor at a time: baseline + each level of each knob, others at baseline
  factorial  full grid over the listed knobs (each knob: baseline value + its levels)
  lhs        latin-hypercube sample of `samples` points over knob ranges (categoricals balanced)
  list       hand-written variants in the plan ("variants": {name: {...}})

Every generated variant is merged with the real config layers for each design × PDK
the plan targets and checked against the LibreLane variable snapshot and the
study's config_constraints. Infeasible combinations are skipped and listed in
configs/plans/<plan>.generated.json. Generated files are ordinary variant JSON:
commit them like hand-written ones. Re-running a plan replaces its own files only.
"""
import argparse
import itertools
import json
import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import (config_layers, constraint_violations, label, load_settings, load_variables,
                     merge_layers, strip_comments)

ROOT = Path(__file__).resolve().parents[1]
BASE = object()  # sentinel: leave the knob at its baseline value
GENERATED_KEY = "//generated_by"


def read(path):
    return json.loads(Path(path).read_text())


def catalog(root, settings):
    return strip_comments(read(root / settings["flow"]["knob_catalog"]))


def pdk_units(root):
    """Per-PDK physical units from configs/platforms.json 'reference' (e.g. row_height_um)."""
    platforms = strip_comments(read(Path(root) / "configs/platforms.json"))
    units = {}
    for pdk, entry in platforms.items():
        for key, value in (entry.get("reference") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                units.setdefault(key, {})[pdk] = value
    return units


def expand(level, units):
    """Catalogue shorthands -> config operators.

    {"$rows": n}            n standard-cell row heights, as a per-PDK length in µm ($per_pdk)
    {"$value": v, "$with": {...}}  a level that also sets companion keys (expanded recursively)
    """
    if isinstance(level, dict) and "$value" in level:
        return {"$value": expand(level["$value"], units),
                "$with": {k: expand(v, units) for k, v in level.get("$with", {}).items()}}
    if isinstance(level, dict) and set(level) == {"$rows"}:
        heights = units.get("row_height_um")
        if not heights:
            raise ValueError("$rows needs reference.row_height_um for every PDK in configs/platforms.json.")
        return {"$per_pdk": {pdk: round_sig(level["$rows"] * h) for pdk, h in sorted(heights.items())}}
    return level


def split_level(level):
    """(value, companion settings) for a possibly composite level."""
    if isinstance(level, dict) and "$value" in level:
        return level["$value"], dict(level.get("$with", {}))
    return level, {}


def check_catalog(root=ROOT):
    """Return a list of problems with the knob catalogue (empty = valid)."""
    settings = load_settings(root)
    variables, aliases = load_variables(root, settings)
    problems = []
    cat = catalog(root, settings)
    for name, spec in cat["knobs"].items():
        if name not in variables:
            problems.append(f"{name}: not a LibreLane variable at the pinned commit")
            continue
        var = variables[name]
        if spec.get("group") not in cat["groups"]:
            problems.append(f"{name}: unknown group {spec.get('group')}")
        if spec.get("status") not in {"default", "opt_in"}:
            problems.append(f"{name}: status must be default or opt_in")
        units = pdk_units(root)
        base = {"CLOCK_PERIOD": 10.0, "FP_CORE_UTIL": 50, "PL_TARGET_DENSITY_PCT": 60,
                "CLOCK_UNCERTAINTY_CONSTRAINT": 0.25}
        for level in spec.get("levels", []):
            try:
                value, extra = split_level(expand(level, units))
                for pdk in units.get("row_height_um", {"any": None}):
                    merge_layers([("base", base), ("catalog", {name: value, **extra})], pdk, variables, aliases)
            except ValueError as exc:
                problems.append(f"{name}: level {level!r}: {exc}")
        for pdk in spec.get("effective_pdks", []):
            if pdk not in units.get("row_height_um", {}):
                problems.append(f"{name}: effective_pdks lists unknown PDK {pdk}")
        rng = spec.get("range")
        if rng:
            if var["kind"] not in {"int", "number"}:
                problems.append(f"{name}: range given for non-numeric {var['kind']}")
            if not rng["min"] < rng["max"]:
                problems.append(f"{name}: range min must be < max")
            if rng.get("scale") == "log" and rng["min"] <= 0:
                problems.append(f"{name}: log range needs min > 0")
        for req in spec.get("requires", {}):
            if req not in variables:
                problems.append(f"{name}: requires unknown variable {req}")
    return problems


def tag(value):
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, dict) and "$value" in value:
        return tag(value["$value"])
    if isinstance(value, dict) and set(value) == {"$rows"}:
        return f"rows{tag(value['$rows'])}"
    if isinstance(value, dict) and "$per_pdk" in value:
        return "pdk" + "_".join(tag(v) for _, v in sorted(value["$per_pdk"].items()))
    if isinstance(value, dict) and "$of" in value:
        return f"of{value['$of'][0].split('_')[0].lower()}{tag(value['$of'][1])}"
    if isinstance(value, dict):
        op, arg = next(iter(value.items()))
        prefix = {"$scale": "x", "$add": "plus"}.get(op, op.strip("$"))
        return prefix + tag(arg)
    if isinstance(value, list):
        return "_".join(tag(v) for v in value)
    text = repr(value) if isinstance(value, float) else str(value)
    text = text.replace("-", "m").replace(".", "p").replace(" ", "")
    return re.sub(r"[^A-Za-z0-9]", "", text) or "v"


def round_sig(value, digits=4):
    if value == 0:
        return 0.0
    return round(value, -int(math.floor(math.log10(abs(value)))) + digits - 1)


def select_knobs(plan, cat):
    knobs = cat["knobs"]
    chosen = []
    named = plan.get("knobs", [])
    if named == "all":
        named = []
        groups = list(cat["groups"])
    else:
        groups = plan.get("groups", [])
    for group in groups:
        if group not in cat["groups"]:
            raise ValueError(f"Unknown group {group}; choose from {sorted(cat['groups'])}")
        chosen += [k for k, s in knobs.items() if s["group"] == group and s["status"] == "default"]
    for name in named:
        if name not in knobs:
            raise ValueError(f"{name} is not in the knob catalogue ({cat and 'configs/knobs.json'}).")
        chosen.append(name)
    chosen = [k for k in dict.fromkeys(chosen) if k not in set(plan.get("exclude", []))]
    for name in chosen:
        caution = knobs[name].get("caution", "")
        if knobs[name]["status"] == "opt_in" and caution and not plan.get("allow_opt_in"):
            raise ValueError(f"{name} is opt-in ({caution.split(':')[0]}). Set \"allow_opt_in\": true in the "
                             "plan if you really want it; it breaks the 'initial GRT seed only' noise model.")
    if not chosen and plan["strategy"] != "list":
        raise ValueError("The plan selects no knobs.")
    return chosen


def levels_for(name, plan, cat, units=None):
    levels = plan.get("levels", {}).get(name, cat["knobs"][name].get("levels", []))
    return [expand(level, units or {}) for level in levels]


def apply_requires(variant, cat):
    """Add prerequisite settings; drop a dependent knob whose prerequisite is explicitly disabled."""
    out = dict(variant)
    for name in list(out):
        requires = cat["knobs"].get(name, {}).get("requires", {})
        for key, needed in requires.items():
            if key in out and out[key] != needed:
                out.pop(name, None)  # dependent setting has no effect: treat as baseline
                break
        else:
            for key, needed in requires.items():
                out.setdefault(key, needed)
    return out


def compose(base, settings_):
    variant = dict(base)
    extras = {}
    for key, level in settings_.items():
        if level is BASE:
            continue
        value, extra = split_level(level)
        variant[key] = value
        extras.update(extra)
    for key, value in extras.items():
        variant.setdefault(key, value)  # an explicitly varied knob wins over a companion setting
    return variant


def ofat(plan, cat, knobs, base, units):
    for name in knobs:
        written = plan.get("levels", {}).get(name, cat["knobs"][name].get("levels", []))
        for raw, level in zip(written, levels_for(name, plan, cat, units)):
            yield f"{name}__{tag(raw)}", compose(base, {name: level}), {name: level}


def factorial(plan, cat, knobs, base, units):
    grids = [[BASE] + [lv for lv in levels_for(n, plan, cat, units)] for n in knobs]
    for index, combo in enumerate(itertools.product(*grids), 1):
        factors = dict(zip(knobs, combo))
        if all(v is BASE for v in combo):
            continue  # that is the baseline itself
        yield f"{index:04d}", compose(base, factors), {k: v for k, v in factors.items() if v is not BASE}


def lhs(plan, cat, knobs, base, variables, units):
    n = plan["samples"]
    rng = random.Random(plan.get("rng_seed", 1))
    columns = {}
    for name in knobs:
        spec = dict(cat["knobs"][name].get("range") or {}, **plan.get("ranges", {}).get(name, {}))
        if spec:
            strata = list(range(n))
            rng.shuffle(strata)
            values = []
            for s in strata:
                u = (s + rng.random()) / n
                lo, hi = spec["min"], spec["max"]
                x = math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo))) if spec.get("scale") == "log" \
                    else lo + u * (hi - lo)
                integer = spec.get("integer") or variables[name]["kind"] == "int"
                x = int(round(x)) if integer else round_sig(x)
                if spec.get("relative"):
                    x = {"$scale": x}
                elif spec.get("of"):
                    x = {"$of": [spec["of"], x]}
                elif spec.get("unit") == "row_height_um":
                    x = expand({"$rows": x}, units)
                values.append(x)
            columns[name] = values
        else:
            options = [BASE] + list(levels_for(name, plan, cat, units))
            values = [options[i % len(options)] for i in range(n)]
            rng.shuffle(values)
            columns[name] = values
    for i in range(n):
        factors = {name: columns[name][i] for name in knobs}
        yield f"{i + 1:04d}", compose(base, factors), {k: v for k, v in factors.items() if v is not BASE}


def generate(plan_path, root=ROOT, write=True):
    plan_path = Path(plan_path)
    plan = strip_comments(read(plan_path))
    settings = load_settings(root)
    variables, aliases = load_variables(root, settings)
    cat = catalog(root, settings)
    units = pdk_units(root)
    name = label(plan["name"])
    if "__" in name:
        raise ValueError("Plan names must not contain '__' (used as the variant separator).")
    strategy = plan["strategy"]
    base = dict(plan.get("base", {}))
    knobs = select_knobs(plan, cat) if strategy != "list" else []
    if strategy == "ofat":
        candidates = ofat(plan, cat, knobs, base, units)
    elif strategy == "factorial":
        candidates = factorial(plan, cat, knobs, base, units)
    elif strategy == "lhs":
        candidates = lhs(plan, cat, knobs, base, variables, units)
    elif strategy == "list":
        candidates = ((label(k), compose(base, v), v) for k, v in plan["variants"].items())
    else:
        raise ValueError("strategy must be ofat, factorial, lhs or list")

    experiment = dict(plan.get("experiment", {}))
    targets = plan.get("check_against", {})
    designs = targets.get("designs", experiment.get("designs", []))
    pdks = targets.get("pdks", experiment.get("pdks", []))
    if not designs or not pdks:
        raise ValueError("The plan needs experiment.designs/pdks (or check_against) to validate variants.")
    defaults = read(root / "configs/defaults.json")
    platforms = read(root / "configs/platforms.json")
    manifests = {d: read(root / f"configs/designs/{d}/design.json") for d in designs}
    baseline_name = settings["analysis"]["baseline_config"]
    baseline_layer = read(root / f"configs/variants/{baseline_name}.json")
    reference = {}
    for d in designs:
        for p in pdks:
            layers, _ = config_layers(root, defaults, platforms, d, manifests[d], p, baseline_name, baseline_layer)
            reference[(d, p)] = merge_layers(layers, p, variables, aliases)[0]

    accepted, skipped, seen = {}, [], {}
    limit = plan.get("max_variants", 500)
    for suffix, variant, factors in candidates:
        variant = apply_requires(variant, cat)
        vname = f"{name}__{suffix}"
        label(vname)
        canonical = json.dumps(variant, sort_keys=True)
        if canonical in seen:
            skipped.append({"variant": vname, "reason": f"identical to {seen[canonical]}"})
            continue
        problems, same_as_baseline = [], True
        for (d, p), ref in reference.items():
            layers, _ = config_layers(root, defaults, platforms, d, manifests[d], p, vname, variant)
            try:
                merged = merge_layers(layers, p, variables, aliases)[0]
            except ValueError as exc:
                problems.append(f"{d}/{p}: {exc}")
                continue
            problems += [f"{d}/{p}: {msg}" for msg in
                         constraint_violations(merged, variables, settings.get("config_constraints", []))]
            keys = set(merged) | set(ref)
            effective = lambda cfg, k: cfg.get(k, variables[k]["default"] if k in variables else None)
            same_as_baseline &= all(effective(merged, k) == effective(ref, k) for k in keys)
        if problems:
            skipped.append({"variant": vname, "reason": "; ".join(problems)})
            continue
        if same_as_baseline:
            skipped.append({"variant": vname, "reason": f"identical to baseline {baseline_name} on every target"})
            continue
        seen[canonical] = vname
        accepted[vname] = {"layer": variant, "factors": {k: v for k, v in factors.items()}}
        if len(accepted) > limit:
            raise ValueError(f"More than max_variants={limit} variants; narrow the plan or raise the limit.")

    configs = ([baseline_name] if plan.get("include_baseline", True) else []) + list(accepted)
    experiment.setdefault("phase", "pilot")
    experiment.setdefault("repeat", 1)
    experiment.setdefault("stop_after", None)
    experiment.setdefault("order_seed", settings["execution"]["default_order_seed"])
    experiment["configs"] = configs
    experiment_path = root / plan.get("experiment_path", f"experiments/{name}.json")
    notes = []
    for knob in knobs:
        effective = cat["knobs"][knob].get("effective_pdks")
        if effective and set(pdks) - set(effective):
            notes.append(f"{knob} only takes effect on {', '.join(effective)}; on "
                         f"{', '.join(sorted(set(pdks) - set(effective)))} its variants equal the baseline "
                         "(expect within-noise results there; do not read them as 'no effect of the knob').")
    index = {"notes": notes, "plan": str(plan_path.resolve().relative_to(root.resolve())) if plan_path.resolve().is_relative_to(root.resolve()) else str(plan_path),
             "strategy": strategy, "knobs": knobs, "baseline": baseline_name,
             "variants": {k: v["factors"] for k, v in accepted.items()}, "skipped": skipped}
    jobs = len(configs) * len(experiment.get("designs", [])) * len(experiment.get("pdks", [])) \
        * len(experiment.get("seeds", [])) * experiment["repeat"]
    if write:
        variants_dir = root / "configs/variants"
        for old in variants_dir.glob(f"{name}__*.json"):
            if read(old).get(GENERATED_KEY) == name:
                old.unlink()
        for vname, item in accepted.items():
            body = {GENERATED_KEY: name, **{f"//factor:{k}": v for k, v in item["factors"].items()},
                    **item["layer"]}
            (variants_dir / f"{vname}.json").write_text(json.dumps(body, indent=2) + "\n")
        index_path = root / "configs/plans" / f"{name}.generated.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2, default=str) + "\n")
        experiment_path.parent.mkdir(parents=True, exist_ok=True)
        experiment_path.write_text(json.dumps(experiment, indent=2) + "\n")
    return {"variants": len(accepted), "skipped": len(skipped), "configs": configs,
            "jobs": jobs, "experiment": str(experiment_path), "index": index}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("plan")
    g.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    lst = sub.add_parser("list")
    lst.add_argument("--all", action="store_true", help="Include opt-in knobs.")
    args = parser.parse_args()
    try:
        if args.command == "check":
            problems = check_catalog()
            print("\n".join(problems) if problems else "Knob catalogue is consistent with the LibreLane snapshot.")
            raise SystemExit(1 if problems else 0)
        if args.command == "list":
            settings = load_settings(ROOT)
            cat = catalog(ROOT, settings)
            variables, _ = load_variables(ROOT, settings)
            for group in cat["groups"]:
                print(f"\n[{group}] {cat['groups'][group]}")
                for name, spec in cat["knobs"].items():
                    if spec["group"] != group or (spec["status"] == "opt_in" and not args.all):
                        continue
                    flag = "" if spec["status"] == "default" else f"  (opt-in{': ' + spec['caution'].split(':')[0] if spec.get('caution') else ''})"
                    print(f"  {name:34} default={variables[name]['default']!r:<10} levels={spec.get('levels')}{flag}")
            return
        result = generate(args.plan, write=not args.dry_run)
        print(f"{result['variants']} variants ({result['skipped']} skipped; see the .generated.json index), "
              f"{len(result['configs'])} configurations, {result['jobs']} planned jobs.")
        for note in result["index"]["notes"]:
            print(f"  NOTE {note}")
        for item in result["index"]["skipped"][:10]:
            print(f"  skipped {item['variant']}: {item['reason']}")
        print(("Would write " if args.dry_run else "Wrote ") + result["experiment"])
    except ValueError as exc:
        parser.exit(2, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
