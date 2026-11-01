#!/usr/bin/env bash
# Re-enter the pinned tool environment; forward arguments such as --run.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"

shell_args=(
  tools/librelane/shell.nix
  --cores 4
  --max-jobs 2
  --option extra-substituters https://nix-cache.fossi-foundation.org
  --option extra-trusted-public-keys
  'nix-cache.fossi-foundation.org:3+K59iFwXqKsL7BNu6Guy0v+uTlwsxYQxjspXzqLYQs='
)

if command -v nix-shell >/dev/null 2>&1; then
  exec nix-shell "${shell_args[@]}" "$@"
fi

export GDS_DSE_RUNTIME="${GDS_DSE_RUNTIME:-$HOME/.local/share/gds-dse}"
export NP_LOCATION="$GDS_DSE_RUNTIME"
export NP_GIT="${NP_GIT:-$(command -v git)}"
export NP_RUNTIME="${NP_RUNTIME:-proot}"
portable="$GDS_DSE_RUNTIME/bin/nix-portable-v012"
if [[ ! -x "$portable" ]]; then
  printf 'Cannot find nix-shell or %s. Restore the installed Nix environment first.\n' "$portable" >&2
  exit 1
fi

exec "$portable" nix-shell "${shell_args[@]}" "$@"
