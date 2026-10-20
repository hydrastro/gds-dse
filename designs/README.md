# Design sources

The four benchmark repositories are Git submodules. Their existing
register-interface top modules define the benchmark boundary.

| Design ID | Upstream | Top | Manifest |
| --- | --- | --- | --- |
| `aes` | [secworks/aes](https://github.com/secworks/aes) | `aes` | [design.json](../configs/designs/aes/design.json) |
| `sha256` | [secworks/sha256](https://github.com/secworks/sha256) | `sha256` | [design.json](../configs/designs/sha256/design.json) |
| `chacha` | [secworks/chacha](https://github.com/secworks/chacha) | `chacha` | [design.json](../configs/designs/chacha/design.json) |
| `prince` | [secworks/prince](https://github.com/secworks/prince) | `prince` | [design.json](../configs/designs/prince/design.json) |

The study adds no RTL to these designs. Flow variants are shared in
`configs/variants/`; clocks and integration settings live in each design's
manifest directory.

See [setup](../docs/setup.md) for submodule commands and
[versioning](../docs/versioning.md) for pins. Submodule contents retain their
upstream licenses.

The optional ASCON manifest is a template awaiting real source information.
Use [adding a design](../docs/experiment-guide.md) to complete it.
