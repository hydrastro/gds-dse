#!/usr/bin/env bash
# Shrink a FINISHED campaign, keeping everything the analysis and a re-collect need:
# records, metrics, reports, logs and the final GDS files. Deletes intermediate layouts and
# databases (ODB/DEF/SPEF/netlists of every step) and aborted attempts. Afterwards it re-collects
# and checks that runs.csv is unchanged; if not, it restores the original tables and says so.
#
#   bash scripts/slim.sh results/<label> --dry-run     # show what would be kept and deleted
#   bash scripts/slim.sh results/<label>
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: bash scripts/slim.sh results/<label> [--dry-run]"; exit 2; }
dir=$(cd "$1" && pwd); mode=${2:-}; label=$(basename "$dir")
cd "$(dirname "$0")/.."
[ -f "$dir/manifest.json" ] && [ -f "$dir/runs.csv" ] || { echo "$dir: not a collected campaign"; exit 1; }
if pgrep -f "study.py run --label $label( |\$)" > /dev/null; then
  echo "$label is still running: slim only finished campaigns"; exit 1
fi

candidates() {   # large files inside run trees, except data files and the final GDS
  find "$dir" -path '*/runs/run/*' -type f -size +1M \
    ! -name '*.json' ! -name '*.rpt' ! -name '*.log' ! -name '*.csv' ! -path '*/runs/run/final/*.gds' -print0
}
gds() { find "$dir" -path '*/runs/run/final/*.gds' -type f -print0; }
size() { du -ch --files0-from=- 2>/dev/null | tail -1 | cut -f1; }

echo "$label: $(du -sh "$dir" | cut -f1) now"
echo "  keep   final GDS: $(gds | tr -cd '\0' | wc -c) files, $(gds | size)"
echo "  delete aborted attempts: $(find "$dir" -type d -name aborted | wc -l) folders"
echo "  delete intermediate layouts/databases: $(candidates | tr -cd '\0' | wc -c) files, $(candidates | size)"
[ "$mode" = "--dry-run" ] && exit 0

cp "$dir/runs.csv" "$dir/runs.csv.before-slim"
[ -f "$dir/summary.csv" ] && cp "$dir/summary.csv" "$dir/summary.csv.before-slim"
find "$dir" -type d -name aborted -prune -exec rm -rf {} +
candidates | xargs -0 -r rm -f

python3 scripts/study.py collect --label "$label" > /dev/null
if cmp -s "$dir/runs.csv" "$dir/runs.csv.before-slim"; then
  rm -f "$dir/runs.csv.before-slim" "$dir/summary.csv.before-slim"
  echo "  re-collect check: identical runs.csv"
else
  diff "$dir/runs.csv.before-slim" "$dir/runs.csv" > "$dir/slim-collect-diff.txt" || true
  mv "$dir/runs.csv.before-slim" "$dir/runs.csv"
  [ -f "$dir/summary.csv.before-slim" ] && mv "$dir/summary.csv.before-slim" "$dir/summary.csv"
  echo "  WARNING: a re-collect would differ (see slim-collect-diff.txt); the original tables were restored."
  echo "           Do not run 'collect' on this campaign again."
fi
echo "$label: $(du -sh "$dir" | cut -f1) after slimming"
