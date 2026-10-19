"""Experiment expansion, explicit config layering, validation and Git provenance."""
import difflib
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from expr import Unresolved, evaluate

STUDY_SETTINGS = 'configs/study.json'
SDC_PREFIX = 'STUDY_SDC_'
SDC_PLACEHOLDER = re.compile(r'@(STUDY_SDC_[A-Z0-9_]+)@')
OPERATORS = {'$scale', '$add', '$per_pdk', '$of'}
# Identity/path/thread/seed fields come from manifests and study.json, never from an override layer.
PROTECTED = {'DESIGN_NAME', 'PDK', 'STD_CELL_LIBRARY', 'VERILOG_FILES', 'VERILOG_INCLUDE_DIRS',
             'VERILOG_DEFINES', 'SYNTH_PARAMETERS', 'USE_SLANG', 'CLOCK_PORT',
             'PNR_SDC_FILE', 'SIGNOFF_SDC_FILE', 'DRT_THREADS', 'STA_THREADS',
             'STUDY_GRT_SEED', 'STUDY_GRT_CAPACITY_PCT', 'meta'}


def git(root, *args):
    try:
        p = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=30)
        return {'returncode': p.returncode, 'stdout': p.stdout.strip(), 'stderr': p.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'error': str(exc)}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Names must contain only letters, digits, underscores or hyphens.')
    return value


def strip_comments(obj):
    """Drop '//'-prefixed documentation keys from a JSON object (recursively for dicts)."""
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not str(k).startswith('//')}
    return obj


def load_settings(root):
    return strip_comments(json.loads((Path(root) / STUDY_SETTINGS).read_text()))


def load_variables(root, settings):
    path = Path(root) / settings['flow']['variables_snapshot']
    snapshot = json.loads(path.read_text())
    if snapshot.get('librelane_commit') != settings['flow']['librelane_commit']:
        raise ValueError(f'{path} was generated for LibreLane {snapshot.get("librelane_commit")}, but '
                         f'study.json pins {settings["flow"]["librelane_commit"]}. Regenerate it with '
                         'scripts/librelane_vars.py inside the pinned Nix shell.')
    variables = snapshot['variables']
    aliases = {old: name for name, var in variables.items() for old in var.get('deprecated_names', [])}
    return variables, aliases


def variable(variables, aliases, key, source):
    if key in variables:
        return variables[key]
    if key in aliases:
        raise ValueError(f'{source}: {key} is a deprecated LibreLane name; use {aliases[key]} so every '
                         'campaign records one canonical name.')
    close = difflib.get_close_matches(key, list(variables), n=3)
    hint = f' Did you mean {", ".join(close)}?' if close else ''
    raise ValueError(f'{source}: {key} is not a LibreLane Classic-flow variable at the pinned commit.{hint}')


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_value(key, value, var, source):
    kind = var['kind']
    if value is None:
        return
    ok = {'bool': isinstance(value, bool),
          'int': isinstance(value, int) and not isinstance(value, bool),
          'number': is_number(value),
          'str': isinstance(value, str),
          'choice': value in var.get('choices', [])}.get(kind, True)
    if not ok:
        expected = f'one of {var["choices"]}' if kind == 'choice' else f'a JSON {kind}'
        raise ValueError(f'{source}: {key}={value!r} must be {expected}.')


def is_operator(value):
    return isinstance(value, dict) and len(value) == 1 and next(iter(value)) in OPERATORS


class Deferred:
    """{"$of": [OTHER_KEY, ratio]}: resolved after all layers, as ratio x final OTHER_KEY."""
    def __init__(self, ref, ratio, source):
        self.ref, self.ratio, self.source = ref, ratio, source

    def __eq__(self, other):
        return isinstance(other, Deferred) and (self.ref, self.ratio) == (other.ref, other.ratio)


def resolve_value(value, base, pdk, key, source, var):
    """Apply {"$scale": x}, {"$add": x}, {"$per_pdk": {...}} and {"$of": [KEY, r]} relative to lower layers."""
    if isinstance(value, dict) and len(value) == 1 and str(next(iter(value))).startswith('$') \
            and not is_operator(value):
        raise ValueError(f'{source}: {key} uses unknown operator {next(iter(value))}; '
                         f'supported: {sorted(OPERATORS)}.')
    if not is_operator(value):
        return value
    op, arg = next(iter(value.items()))
    if op == '$per_pdk':
        if not isinstance(arg, dict) or pdk not in arg:
            raise ValueError(f'{source}: {key} has no $per_pdk entry for {pdk}.')
        return resolve_value(arg[pdk], base, pdk, key, source, var)
    if op == '$of':
        if not (isinstance(arg, list) and len(arg) == 2 and isinstance(arg[0], str) and is_number(arg[1])):
            raise ValueError(f'{source}: {key} $of needs [OTHER_KEY, ratio].')
        return Deferred(arg[0], arg[1], source)
    if isinstance(base, Deferred):
        raise ValueError(f'{source}: {key} cannot apply {op} to a $of value.')
    if not is_number(arg):
        raise ValueError(f'{source}: {key} {op} needs a number.')
    if not is_number(base):
        raise ValueError(f'{source}: {key} uses {op}, but no lower layer (or LibreLane default) gives it a '
                         'numeric value. Set an absolute value in defaults/platforms/design first.')
    result = base * arg if op == '$scale' else base + arg
    if var is not None and var['kind'] == 'int':
        return int(round(result))
    return round(result, 9)


def merge_layers(layers, pdk, variables, aliases):
    """Merge (source, dict) layers in order. Returns (config, origins)."""
    config, origins = {}, {}
    for source, layer in layers:
        layer = strip_comments(layer)
        if not isinstance(layer, dict):
            raise ValueError(f'{source}: overrides must be a JSON object.')
        bad = PROTECTED.intersection(layer)
        if bad:
            raise ValueError(f'{source}: overrides must be an object without identity/path/thread/seed fields '
                             f'({", ".join(sorted(bad))}).')
        for key, value in layer.items():
            var = None if key.startswith(SDC_PREFIX) else variable(variables, aliases, key, source)
            base = config.get(key, var['default'] if var else None)
            resolved = resolve_value(value, base, pdk, key, source, var)
            if var is not None and not isinstance(resolved, Deferred):
                check_value(key, resolved, var, source)
            elif var is None and not isinstance(resolved, Deferred) and not (is_number(resolved) or isinstance(resolved, str)):
                raise ValueError(f'{source}: {key} must be a number or string for SDC substitution.')
            config[key] = resolved
            origins[key] = source
    for key, value in list(config.items()):
        if isinstance(value, Deferred):
            ref = config.get(value.ref, variables.get(value.ref, {}).get('default'))
            if not is_number(ref):
                raise ValueError(f'{value.source}: {key} is $of {value.ref}, which has no numeric value.')
            var = variables.get(key)
            result = ref * value.ratio
            config[key] = int(round(result)) if var is not None and var['kind'] == 'int' else round(result, 9)
            if var is not None:
                check_value(key, config[key], var, value.source)
    return config, origins


def lookup_in(config, variables):
    def lookup(name):
        if name in config:
            return config[name]
        if name in variables:
            return variables[name]['default']
        raise Unresolved(name)
    return lookup


def constraint_violations(config, variables, constraints):
    """Return a list of human-readable violated/unresolvable constraints."""
    problems = []
    for expression in constraints:
        try:
            if not evaluate(expression, lookup_in(config, variables)):
                problems.append(f'violates "{expression}"')
        except Unresolved as exc:
            problems.append(f'cannot check "{expression}": {exc.args[0]} has no value (set it explicitly)')
    return problems


def render_sdc(template, values, source):
    used = set(SDC_PLACEHOLDER.findall(template))
    missing = sorted(used - set(values))
    unused = sorted(set(values) - used)
    if missing:
        raise ValueError(f'{source}: no value for SDC placeholder(s) {", ".join(missing)}; set them in a config layer.')
    if unused:
        raise ValueError(f'{source}: {", ".join(unused)} set but not used by the SDC template.')
    return SDC_PLACEHOLDER.sub(lambda m: repr(values[m.group(1)]) if is_number(values[m.group(1)])
                               else str(values[m.group(1)]), template)


def config_layers(root, defaults, platforms, design_name, design, pdk, variant_name, variant):
    """The ordered precedence layers for one design × PDK × variant."""
    root = Path(root)
    override_path = root / f'configs/designs/{design_name}/pdks/{pdk}.json'
    design_pdk = json.loads(override_path.read_text()) if override_path.exists() else {}
    return [('configs/defaults.json', defaults.get('flow_overrides', {})),
            (f'configs/platforms.json:{pdk}', platforms[pdk].get('overrides', {})),
            (f'configs/designs/{design_name}/design.json',
             {'CLOCK_PERIOD': design['clock_period_ns'], **design.get('flow_overrides', {})}),
            (f'configs/designs/{design_name}/pdks/{pdk}.json', design_pdk),
            (f'configs/variants/{variant_name}.json', variant)], override_path


def split_sdc(config):
    flow = {k: v for k, v in config.items() if not k.startswith(SDC_PREFIX)}
    sdc = {k: v for k, v in config.items() if k.startswith(SDC_PREFIX)}
    return flow, sdc


def repo_snapshot(root):
    return {'head': git(root, 'rev-parse', 'HEAD'),
            'toplevel': git(root, 'rev-parse', '--show-toplevel'),
            'status': git(root, 'status', '--porcelain', '--untracked-files=normal'),
            # This repository's own submodules (LibreLane, designs) must be at their recorded commits.
            # Nested submodules inside them (e.g. LibreLane's test data) are recorded for provenance only.
            'submodules': git(root, 'submodule', 'status'),
            'submodules_recursive': git(root, 'submodule', 'status', '--recursive')}


def require_clean_repository(root, snapshot):
    if snapshot['head'].get('returncode') != 0 or snapshot['toplevel'].get('stdout') != str(root.resolve()):
        raise ValueError('Study campaigns require this project to be its own committed Git repository.')
    if snapshot['status'].get('returncode') != 0 or snapshot['status'].get('stdout'):
        raise ValueError('Commit the study files and submodule pointers before preparing/running a study campaign. Use phase pilot during bring-up.')
    result = snapshot['submodules']
    if result.get('returncode') != 0 or any(line.startswith(('-', '+', 'U')) for line in result.get('stdout', '').splitlines()):
        raise ValueError('Initialize submodules recursively and check out their committed versions.')


def verify_repository(root, expected, strict):
    if not strict:
        return
    current = repo_snapshot(root)
    require_clean_repository(root, current)
    if current['head'].get('stdout') != expected['head'].get('stdout') or current['submodules'].get('stdout') != expected['submodules'].get('stdout'):
        raise ValueError('The study repository/submodule revisions changed after prepare. Restore the recorded revision or prepare a new campaign.')


def prepare_campaign(root, flow_root, flow_commit, experiment_file, campaign_label, selections=None):
    root = root.resolve()
    dest = root / 'results' / label(campaign_label)
    if dest.exists():
        raise ValueError(f'{dest} already exists. Choose a new label.')
    files = {}

    def resolve(value, parent=root):
        path = Path(value).expanduser()
        return (parent / path).resolve() if not path.is_absolute() else path.resolve()

    def load(path):
        path = resolve(path)
        obj = json.loads(path.read_text())
        files[path] = obj
        return obj

    settings = strip_comments(load(STUDY_SETTINGS))
    variables, aliases = load_variables(root, settings)
    files[resolve(settings['flow']['variables_snapshot'])] = None  # hashed, not re-written
    execution = settings['execution']
    experiment = load(experiment_file)
    source_experiment = dict(experiment)
    selections = selections or {}
    if set(selections) - {'designs', 'pdks', 'configs', 'seeds'}:
        raise ValueError('Only designs, pdks, configs and seeds may be selected at prepare.')
    experiment = {**experiment, **selections}
    defaults = load('configs/defaults.json')
    platforms = load('configs/platforms.json')
    machine_path = root / 'machine.local.json'
    machine = load(machine_path) if machine_path.exists() else {'manual_pdks': {}}
    phase = experiment.get('phase', 'pilot')
    repeat = experiment.get('repeat', 1)
    stop_after = experiment.get('stop_after')
    threads = execution['threads']
    if 'threads' in defaults:
        raise ValueError('configs/defaults.json: "threads" moved to configs/study.json execution.threads.')
    if phase not in {'pilot', 'study'} or type(repeat) is not int or repeat < 1 or type(threads) is not int or threads < 1:
        raise ValueError('Check phase, repeat and the positive integer thread count.')
    if phase == 'study' and (repeat != 1 or stop_after):
        raise ValueError('Study campaigns require full flows and one run per distinct seed.')
    for key in ['designs', 'pdks', 'configs', 'seeds']:
        values = experiment[key]
        if not isinstance(values, list) or not values or len(set(values)) != len(values):
            raise ValueError(f'{key} must be a nonempty list without duplicates.')
    seeds = experiment['seeds']
    if any(type(seed) is not int or not 1 <= seed <= 2147483647 for seed in seeds):
        raise ValueError('Use distinct nonzero integer seeds in 1..2147483647.')
    order_seed = experiment.get('order_seed', execution['default_order_seed'])
    if type(order_seed) is not int:
        raise ValueError('order_seed must be an integer.')
    planned = len(experiment['designs']) * len(experiment['pdks']) * len(experiment['configs']) * len(seeds) * repeat
    if planned > execution['max_jobs_per_campaign']:
        raise ValueError(f'{planned} jobs exceed execution.max_jobs_per_campaign '
                         f'({execution["max_jobs_per_campaign"]}) in configs/study.json.')
    capacity_pct = settings['flow']['seeded_step']['capacity_perturbation_pct']
    if type(capacity_pct) is not int or not 0 <= capacity_pct <= 100:
        raise ValueError('flow.seeded_step.capacity_perturbation_pct must be an integer 0..100.')
    repository = repo_snapshot(root)
    if phase == 'study':
        require_clean_repository(root, repository)

    variants = {name: load(f'configs/variants/{label(name)}.json') for name in experiment['configs']}
    designs = {}
    input_hashes = {}
    submodule_roots = {flow_root.resolve()}
    extensions = {e.lower() for e in settings['inputs']['rtl_hash_extensions']}
    for name in experiment['designs']:
        label(name)
        design = load(f'configs/designs/{name}/design.json')
        for key in ['top', 'clock_port']:
            if not design.get(key) or 'REPLACE' in design[key]:
                raise ValueError(f'Fill {key} in configs/designs/{name}/design.json using your RTL.')
        rtl_root = resolve(design['rtl_root'])
        submodule_roots.add(rtl_root)
        sources = [resolve(path, rtl_root) for path in design['sources']]
        sources += [resolve(path) for path in design.get('project_sources', [])]
        includes = [resolve(path, rtl_root) for path in design.get('include_dirs', [])]
        includes += [resolve(path) for path in design.get('project_include_dirs', [])]
        sdc = resolve(design['sdc'])
        if not sources or any(not path.is_file() for path in sources) or not sdc.is_file():
            raise ValueError(f'{name}: verify the ordered sources and SDC file paths.')
        if any(not path.is_dir() for path in includes):
            raise ValueError(f'{name}: an include directory does not exist.')
        if design['clock_period_ns'] <= 0:
            raise ValueError(f'{name}: clock_period_ns must be positive.')
        paths = set(sources + [sdc])
        for directory in [rtl_root] + includes:
            paths.update(p.resolve() for p in directory.rglob('*')
                         if p.is_file() and '.git' not in p.parts and p.suffix.lower() in extensions)
        # Extra dependencies, unlike sources, use project-root-relative paths.
        paths.update(resolve(p) for p in design.get('additional_input_files', []))
        input_hashes.update({str(p): digest(p) for p in paths})
        designs[name] = {'manifest': design, 'rtl_root': rtl_root, 'sources': sources,
                         'includes': includes, 'sdc': sdc, 'sdc_template': sdc.read_text(),
                         'git': {'head': git(rtl_root, 'rev-parse', 'HEAD'),
                                 'status': git(rtl_root, 'status', '--porcelain')}}
    if phase == 'study':
        for path in sorted(submodule_roots):
            try:
                relative = path.relative_to(root)
            except ValueError as exc:
                raise ValueError('Study source/flow roots must be submodules inside this repository.') from exc
            entry = git(root, 'ls-tree', 'HEAD', '--', str(relative)).get('stdout', '').split()
            actual = git(path, 'rev-parse', 'HEAD').get('stdout')
            if len(entry) < 3 or entry[0] != '160000' or entry[2] != actual:
                raise ValueError(f'{relative} must be a submodule at the commit recorded by the study repository.')

    jobs = []
    configs_to_write = []
    rendered_sdcs = {}
    for name, item in designs.items():
        design = item['manifest']
        for pdk in experiment['pdks']:
            label(pdk)
            if pdk not in platforms:
                raise ValueError(f'Unknown PDK {pdk}.')
            platform = platforms[pdk]
            local = machine.get('manual_pdks', {}).get(pdk)
            if local and (not local.get('root') or not local.get('revision')):
                raise ValueError(f'Record both root and revision for the manual PDK {pdk}.')
            for cfg, variant in variants.items():
                layers, override_path = config_layers(root, defaults, platforms, name, design, pdk, cfg, variant)
                if override_path.exists():
                    files[override_path] = json.loads(override_path.read_text())
                if (phase == 'study' and settings['inputs'].get('require_calibrated_clock_for_study')
                        and 'CLOCK_PERIOD' not in strip_comments(files.get(override_path, {}))):
                    raise ValueError(f'{name}/{pdk}: no calibrated CLOCK_PERIOD in {override_path.relative_to(root)}. '
                                     'Run the clock calibration (scripts/calibrate_clock.py --write) before study campaigns.')
                merged, origins = merge_layers(layers, pdk, variables, aliases)
                problems = constraint_violations(merged, variables, settings.get('config_constraints', []))
                if problems:
                    raise ValueError(f'{name}/{pdk}/{cfg}: merged configuration ' + '; '.join(problems) + '.')
                config, sdc_values = split_sdc(merged)
                text = render_sdc(item['sdc_template'], sdc_values, f'{name}/{pdk}/{cfg}')
                sdc_hash = hashlib.sha256(text.encode()).hexdigest()
                snapshot_sdc = dest / 'inputs' / 'constraints' / f'{name}-{sdc_hash[:16]}.sdc'
                rendered_sdcs[snapshot_sdc] = text
                config.update({'DESIGN_NAME': design['top'], 'PDK': pdk,
                    'VERILOG_FILES': [str(p) for p in item['sources']],
                    'VERILOG_INCLUDE_DIRS': [str(p) for p in item['includes']],
                    'VERILOG_DEFINES': design.get('defines', []), 'SYNTH_PARAMETERS': design.get('parameters', []),
                    'USE_SLANG': design.get('use_slang', False), 'CLOCK_PORT': design['clock_port'],
                    'PNR_SDC_FILE': str(snapshot_sdc), 'SIGNOFF_SDC_FILE': str(snapshot_sdc),
                    'DRT_THREADS': threads, 'STA_THREADS': threads,
                    'STUDY_GRT_CAPACITY_PCT': capacity_pct,
                    'meta': {'flow': settings['flow']['flow'],
                             'substituting_steps': {settings['flow']['seeded_step']['replaces']:
                                                    settings['flow']['seeded_step']['id']}}})
                if platform.get('scl'):
                    config['STD_CELL_LIBRARY'] = platform['scl']
                for seed in seeds:
                    for rep in range(1, repeat + 1):
                        job_id = f'{name}__{pdk}__{cfg}__s{seed:05d}__r{rep}'
                        job_dir = dest / 'jobs' / job_id
                        expanded = dict(config, STUDY_GRT_SEED=seed)
                        configs_to_write.append((job_dir, expanded, origins))
                        jobs.append({'id': job_id, 'design': name, 'pdk': pdk, 'configuration': cfg,
                                     'seed': seed, 'repeat': rep, 'threads': threads,
                                     'grt_capacity_perturbation_pct': capacity_pct,
                                     'study_sdc_values': sdc_values, 'sdc_sha256': sdc_hash,
                                     'manual_pdk_root': str(resolve(local['root'])) if local else None,
                                     'manual_pdk_revision': local['revision'] if local else None,
                                     'stop_after': stop_after})
    # Validation finishes before creating the campaign directory.
    for path in root.glob('scripts/*.py'):
        input_hashes[str(path)] = digest(path)
    for path in files:
        input_hashes[str(path)] = digest(path)
    dest.mkdir(parents=True)
    for path, data in files.items():
        relative = path.relative_to(root) if path.is_relative_to(root) else Path('external') / path.name
        if data is None:
            (dest / 'inputs' / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest / 'inputs' / relative)
        else:
            write(dest / 'inputs' / relative, data)
    for name, item in designs.items():
        target = dest / 'inputs/constraints/templates' / f'{name}.sdc'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item['sdc'], target)
    for path, text in rendered_sdcs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        input_hashes[str(path)] = digest(path)
    for source, target in [('flake.lock', 'inputs/toolchain/flake.lock'), ('librelane/pdk_hashes.yaml', 'inputs/toolchain/pdk_hashes.yaml')]:
        output = dest / target
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(flow_root / source, output)
    for job, (directory, config, origins) in zip(jobs, configs_to_write):
        write(directory / 'config.json', config)
        write(directory / 'config_origins.json', origins)
        job['config_sha256'] = digest(directory / 'config.json')
    campaign_id = str(uuid.uuid4())
    ordered_ids = [job['id'] for job in jobs]
    random.Random(order_seed).shuffle(ordered_ids)
    schedule_path = dest / 'schedule.json'
    write(schedule_path, {'schema': 1, 'campaign_id': campaign_id,
                         'order_seed': order_seed, 'job_ids': ordered_ids,
                         'generator': 'random.Random(order_seed).shuffle',
                         'python': sys.version})
    manifest = {'schema': 4, 'campaign_id': campaign_id,
                'label': campaign_label, 'phase': phase,
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'flow_commit': flow_commit, 'repository': repository,
                'design_revisions': {name: item['git'] for name, item in designs.items()},
                'experiment': experiment, 'source_experiment': source_experiment,
                'prepare_selections': selections, 'order_seed': order_seed,
                'variant_layers': {name: strip_comments(v) for name, v in variants.items()},
                'schedule_sha256': digest(schedule_path),
                'input_hashes': input_hashes, 'jobs': jobs}
    write(dest / 'manifest.json', manifest)
    return f'Prepared {len(jobs)} jobs in {dest}'
