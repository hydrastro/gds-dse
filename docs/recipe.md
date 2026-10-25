# Clean start and the build "recipe"

Before any study campaign, every build must work. That means no infrastructure
failures, no tool crashes, and the same result every time the same seed runs. This page
covers a clean start and the overnight diagnostic that picks the execution **recipe**
(threads per flow, flows in parallel, tool runtime) that all experiments then use.

## 1. Stop everything and delete all results

From an ordinary login shell on the server (not inside the Nix environment):

```bash
cd ~/gds-dse

# Ask every runner to stop: each one stops its own flows and records them as interrupted.
pkill -INT -f 'scripts/study.py run'
sleep 60

# Anything left? (flows, tools, diagnostic step runs)
ps -u "$USER" -o pid,etime,args | grep -E 'flow_entry|librelane|openroad|yosys|magic|netgen|klayout|study.py' | grep -v grep

# If something is still listed, stop it, then force it after a grace period:
pkill -TERM -f 'flow_entry.py|librelane.steps|scripts/study.py run'
sleep 30
pkill -KILL -f 'flow_entry.py|librelane.steps|scripts/study.py run'

# Delete ALL results, diagnostics and logs. results/ is not tracked by Git.
rm -rf results
```

Keep `pdks/` (the downloaded, pinned PDK builds) and the Nix store under
`~/.local/share/gds-dse` (the pinned tools). They are inputs, not results, and
re-downloading them changes nothing.

Then make sure the repository itself is exactly a committed revision:

```bash
git status --short                 # should print nothing
git submodule update --init
git submodule status               # no leading '+' or '-'
git clean -nd                      # preview untracked files; delete with -fd only if they are junk
```

## 2. Check the environment

```bash
bash scripts/enter-env.sh --run 'bash scripts/check-env.sh'
```

`enter-env.sh` now chooses nix-portable's **bwrap** runtime automatically when the
kernel allows unprivileged user namespaces, and falls back to **proot** otherwise. It
prints which one it uses. An explicit `NP_RUNTIME=…` still wins. Every campaign records
the runtime (`tool_runtime` in `runs.csv`).

Why it matters: proot runs every system call of every tool through `ptrace`. The
earlier `Cannot open LEF file` failures (the file exists) came with
`proot warning: ptrace(PEEKDATA): Bad address`. bwrap has no such layer.

## 3. One quick sanity build (about half an hour)

Before committing the night, build the smallest design once:

```bash
python3 scripts/study.py prepare --experiment experiments/recipe/recipe.json --label recipe-smoke \
  --designs prince --pdks ihp-sg13g2 --seeds 1 --repeat 1 --threads 1
bash scripts/enter-env.sh --run 'python3 scripts/study.py run --label recipe-smoke --machine-id olympus'
python3 -c "import csv; r = next(csv.DictReader(open('results/recipe-smoke/runs.csv'))); \
print({k: r[k] for k in ['status', 'layout_valid', 'failure_class', 'wall_time_s', 'peak_rss_mb', 'tool_runtime']})"
```

`layout_valid=True` and a recorded `peak_rss_mb` mean the pipeline works end to end.

## 4. The overnight recipe diagnostic

```bash
nohup setsid bash scripts/recipe-night.sh > results/recipe-night.log 2>&1 < /dev/null &
```

The same 48 jobs (4 designs × 3 PDKs × C0 × seeds 1–2, **each run twice**) run
once per setting, one setting after the other, each filling the machine:

| Campaign | Threads per flow | Flows at once | Cores used |
|---|---|---|---|
| `recipe-<date>-t1` | 1 | 48 | 48 |
| `recipe-<date>-t4` | 4 | 16 | 64 |
| `recipe-<date>-t2` | 2 | 32 | 64 |
| `recipe-<date>-t8` | 8 | 8 | 64 |

The order puts the most informative comparison (1 vs 4 threads) first, so even a
partial night answers the main question. To run only some settings:
`bash scripts/recipe-night.sh "1:48 4:16"`. To compare runtimes too, run the same
settings again with `NP_RUNTIME=proot bash scripts/recipe-night.sh "1:48" proot`.

### What it measures

Each flow now records its **peak memory** (largest process of the flow) and **CPU
time**, next to wall time, in `attempt.json` and `runs.csv`.

| Question | How |
|---|---|
| Do tool crashes or infra failures depend on threads? | Same jobs, same seeds, same load, same runtime; only threads change |
| Is the flow deterministic at that thread count? | Every seed runs twice in the same campaign: are the results identical? |
| Do thread counts change results? | The same seed across campaigns: identical? |
| Is a failure tied to the seed or random? | A crash that happens in both repeats (and in every campaign) is deterministic: a property of that design/PDK/seed, not of the recipe |
| What is the fastest safe setting? | Makespan and runs/hour; CPU efficiency (CPU time / wall time) shows whether extra threads are actually used |
| Is memory a limit? | Peak memory per flow × flows at once vs the 256 GB |

### Why threads *could* matter

Threads should not make a correct program fail. But three mechanisms can link them to
the failures seen so far:

1. **Multi-threaded detailed routing is not deterministic.** TritonRoute splits the die
   into batches and threads finish them in an order that depends on scheduling. The same
   seed can then give a different routing, which can take a different (and sometimes
   buggy) code path. With one thread, the order is fixed.
2. **Oversubscription.** Flows × threads larger than the cores (e.g. 60 × 4 = 240 on 64)
   slows every flow down and multiplies memory use, leading to timeouts or out-of-memory
   failures.
3. **proot.** Its ptrace layer intercepts each thread's system calls; heavily threaded
   processes stress it most.

Your one-thread re-run of the failed IHP detailed-routing step changed **two** things at
once: the thread count, and running alone instead of among 59 other flows. Its success
does not tell which one mattered. Tonight's campaigns change only the thread count.

## 5. Read the result in the morning

```bash
python3 scripts/recipe_report.py --prefix recipe-$(date +%Y%m%d)-     # or the date it started
```

The night script also writes `results/recipe-<date>-report.md`. Choose the recipe like this:

1. Discard settings with **tool** or **infra** failures, or **non-identical repeats**.
2. Among the rest, take the highest **runs/hour**, provided peak memory × flows at once
   stays well below 256 GB.
3. Look at **Where failures happen**. A design/PDK that fails the same way in every
   campaign is not a recipe problem:
   * `timing`: expected at the 25 ns placeholder clock. Calibration (step 6) fixes it.
   * `congestion` at C0: the default utilisation is too dense for that design on that
     PDK. Choose a C0 utilisation that routes, and document it in
     `configs/designs/<d>/pdks/<pdk>.json`.
   * `tool_crash` on the same seed every time: a reproducible OpenROAD bug for that
     input. Keep the log; it is worth an upstream report.

Then write the recipe into `configs/study.json` (`execution.threads`,
`execution.parallel_jobs`) and commit it, so every later campaign uses it.

## 6. A clean base case for every design × PDK

The study only starts once **all 12 design × PDK cells** (4 designs × 3 PDKs) build
cleanly at C0: complete flow, no DRC/LVS/antenna problems, and timing met. Timing at the
25 ns placeholder clock is meaningless (AES: −5.7 ns on sky130, −12.8 ns on gf180, +8.3 ns
on IHP), so every cell first needs its own clock. This takes three steps, for all designs
at once.

**6a. One smoke build per cell.** AES already has one (`recipe-smoke`). Build the others:

```bash
python3 scripts/study.py prepare --experiment experiments/recipe/recipe.json --label smoke-rest \
  --designs sha256 chacha prince --seeds 1 --repeat 1 --threads 1
bash scripts/enter-env.sh --run 'python3 scripts/study.py run --label smoke-rest --machine-id olympus'
```

**6b. Provisional clock = clock − worst setup slack.**

```bash
python3 scripts/estimate_clock.py --label recipe-smoke --label smoke-rest          # look first
python3 scripts/estimate_clock.py --label recipe-smoke --label smoke-rest --write
git add configs/designs && git commit -m "Provisional clocks from smoke builds"
```

It writes `CLOCK_PERIOD` into `configs/designs/<d>/pdks/<pdk>.json`, marked
`"//clock_status": "provisional"`. Study campaigns refuse provisional clocks. A cell
without post-route STA (its build failed earlier) is reported: fix that first.

**6c. Relative sweep around the estimate, then the calibrated reference.**

```bash
python3 scripts/study.py prepare --experiment experiments/suite/calibrate-relative.json --label calrel-001
bash scripts/enter-env.sh --run 'python3 scripts/study.py run --label calrel-001 --machine-id olympus'
python3 scripts/calibrate_clock.py --label calrel-001            # look first
python3 scripts/calibrate_clock.py --label calrel-001 --write
git add configs/designs && git commit -m "Calibrated reference clocks"
```

The sweep runs ×0.8 … ×1.7 the provisional clock (C0 is ×1.0), 3 seeds, stopped after
post-route STA: 4 × 3 × 8 × 3 = 288 short runs. The same plan works for every design,
because the periods are relative. `calibrate_clock.py` takes the fastest period met by
**every** seed and divides by 0.85 (15 % margin), then marks the file `calibrated`. A
period where a seed fails for a *design* reason (e.g. congestion) counts as "not met";
tool or infra failures still stop the calibration (`study.py run --retry-infra`). Use
`--designs`/`--pdks` at prepare to redo only some cells.

**6d. Base check.** Full builds of every cell at the calibrated clocks:

```bash
python3 scripts/study.py prepare --experiment experiments/recipe/recipe.json --label base-check \
  --seeds 1 2 3 --repeat 1
bash scripts/enter-env.sh --run 'python3 scripts/study.py run --label base-check --machine-id olympus'
python3 scripts/analyze.py --label base-check --population attempted
```

All 36 runs should pass every study check. Anything else (congestion, a crash, a DRC
error) is fixed at C0 for that cell before the study starts.
