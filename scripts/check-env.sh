#!/usr/bin/env bash
# Check the pinned tool environment. Run INSIDE it, e.g.:
#   bash scripts/enter-env.sh --run 'bash scripts/check-env.sh'
# Prints versions, which runtime provides the tools, and fails if a required tool is missing
# or the LibreLane checkout is not at the pinned commit.
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

status=0
pinned="$(python3 -c 'import json;print(json.load(open("configs/study.json"))["flow"]["librelane_commit"])')"
actual="$(git -C tools/librelane rev-parse HEAD 2>/dev/null || echo missing)"
printf 'LibreLane commit: %s (pinned %s)\n' "$actual" "$pinned"
[[ "$actual" == "$pinned" ]] || { printf '  MISMATCH: run git submodule update --init\n'; status=1; }
printf 'Runtime: IN_NIX_SHELL=%s NP_RUNTIME=%s\n' "${IN_NIX_SHELL:-}" "${NP_RUNTIME:-}"
printf 'CPUs: %s   Memory available: %s\n' "$(nproc)" "$(awk '/MemAvailable/ {printf "%.0f GiB", $2/1048576}' /proc/meminfo)"

for tool in yosys openroad klayout magic netgen iverilog verilator; do
  path="$(command -v "$tool" || true)"
  printf '%-10s %s\n' "$tool:" "${path:-MISSING}"
  [[ -n "$path" ]] || status=1
done

python3 scripts/flow_entry.py --version || status=1
yosys -V || status=1
openroad -version || status=1

for submodule in designs/aes designs/sha256 designs/chacha designs/prince; do
  [[ -e "$submodule/src/rtl" ]] || { printf 'Missing RTL in %s: git submodule update --init\n' "$submodule"; status=1; }
done

if [[ "$status" == 0 ]]; then
  printf '\nEnvironment OK.\n'
else
  printf '\nEnvironment check FAILED (see above).\n'
fi
exit "$status"
