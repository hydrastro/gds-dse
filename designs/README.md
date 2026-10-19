# Design sources

Each directory is a Git submodule containing one design's source repository, pinned
to an exact commit by the parent repository:

| Directory | Repository | Commit |
|---|---|---|
| `aes/` | https://github.com/secworks/aes | `80dc4718` |
| `sha256/` | https://github.com/secworks/sha256 | `837c5cc3` |
| `chacha/` | https://github.com/secworks/chacha | `7eaba360` |
| `prince/` | https://github.com/secworks/prince | `f40631db` |
| `ascon/` | your ASCON repository (add it as a submodule) | — |

The study manifest of each design is in `configs/designs/<design-id>/`. Do not edit
files inside a submodule from here: commit changes in the design's own repository
and advance the pointer. After cloning, run `git submodule update --init` (non-recursive is enough:
LibreLane's own nested test-data submodules are not needed and not checked).
