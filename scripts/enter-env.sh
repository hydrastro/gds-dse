#!/usr/bin/env bash
# Enter the pinned tool environment from an ordinary host shell.
set -euo pipefail

if [[ -n "${IN_NIX_SHELL:-}" ]]; then
  printf 'Already inside a Nix shell. Open a fresh SSH login to launch a batch.\n' >&2
  printf 'For interactive work here, run the study commands directly.\n' >&2
  exit 2
fi

# IN_NIX_SHELL alone is insufficient: a portable runtime can trace this
# process even when that environment variable is missing.
env_tracer_pid=0
if [[ -r /proc/self/status ]]; then
  while read -r env_status_key env_status_value env_status_rest; do
    if [[ "$env_status_key" == 'TracerPid:' ]]; then
      env_tracer_pid="$env_status_value"
      break
    fi
  done < /proc/self/status
fi
if [[ "$env_tracer_pid" != 0 ]]; then
  printf 'This shell is already traced by PID %s. Refusing to nest the tool environment.\n' "$env_tracer_pid" >&2
  printf 'Open a fresh SSH login outside Nix/PRoot and run this script there.\n' >&2
  exit 2
fi

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
# nix-portable runtime: bwrap (Linux user namespaces, no syscall tracing) is preferred; proot traces
# every system call with ptrace and has shown spurious file errors under heavy parallel load.
# An explicit NP_RUNTIME always wins.
if [[ -z "${NP_RUNTIME:-}" ]]; then
  if command -v unshare >/dev/null 2>&1 && unshare --user --map-root-user true >/dev/null 2>&1; then
    export NP_RUNTIME=bwrap
  else
    export NP_RUNTIME=proot
    printf 'Note: unprivileged user namespaces are unavailable; using the slower proot runtime.\n' >&2
  fi
fi
printf 'nix-portable runtime: %s\n' "$NP_RUNTIME" >&2
portable="$GDS_DSE_RUNTIME/bin/nix-portable-v012"
if [[ ! -x "$portable" ]]; then
  printf 'Cannot find nix-shell or %s. Restore the installed Nix environment first.\n' "$portable" >&2
  exit 1
fi

exec "$portable" nix-shell "${shell_args[@]}" "$@"
