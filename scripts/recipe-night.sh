#!/usr/bin/env bash
# Run the recipe diagnostic: one campaign per "threads:parallel_jobs" setting, one after the other,
# each using the whole machine. Launch from an ordinary host shell (NOT inside the Nix environment):
#
#   nohup setsid bash scripts/recipe-night.sh > results/recipe-night.log 2>&1 < /dev/null &
#
# Settings (default "1:48 4:16 2:32 8:8", most informative first) and a label tag can be given:
#
#   bash scripts/recipe-night.sh "1:48 4:16" r2
#
# Each setting's 48 jobs = 4 designs x 3 PDKs x C0 x seeds 1-2 x 2 repeats (experiments/recipe/recipe.json).
# Afterwards (or any time):  python3 scripts/recipe_report.py --prefix recipe-<tag>
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

settings="${1:-1:48 4:16 2:32 8:8}"
tag="${2:-$(date +%Y%m%d)}"
machine="${GDS_MACHINE_ID:-$(hostname -s)}"
mkdir -p results

for setting in $settings; do
  threads="${setting%%:*}"
  parallel="${setting##*:}"
  label="recipe-${tag}-t${threads}"
  printf '\n=== %s  threads=%s parallel_jobs=%s  start %s\n' "$label" "$threads" "$parallel" "$(date -Is)"
  if [[ ! -e "results/$label/manifest.json" ]]; then
    python3 scripts/study.py prepare --experiment experiments/recipe/recipe.json \
      --label "$label" --threads "$threads" || exit 1
  fi
  GDS_LABEL="$label" GDS_PARALLEL="$parallel" GDS_MACHINE="$machine" \
    bash scripts/enter-env.sh --run \
    'python3 scripts/study.py run --label "$GDS_LABEL" --parallel-jobs "$GDS_PARALLEL" --machine-id "$GDS_MACHINE"' \
    || printf 'Runner for %s exited with status %s (failed flows are recorded; continuing)\n' "$label" "$?"
  printf '=== %s  done %s\n' "$label" "$(date -Is)"
done

python3 scripts/recipe_report.py --prefix "recipe-${tag}-" --out "results/recipe-${tag}-report.md"
