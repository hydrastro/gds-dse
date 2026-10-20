# Setup

The supported workflow uses Linux, Git, Python 3 and LibreLane's pinned Nix
environment. Run commands from the study repository root unless shown otherwise.

## Existing Git checkout

The repository's Git metadata records its submodules. Initialise them with:

```bash
git submodule update --init
git submodule status
```

Use this path if the five submodules are already registered. Do not run
`git submodule add` again. LibreLane's nested test-data submodules are not needed
for this workflow.

## Starting from the source archive

The clean source archive contains the study files, without Git history or
upstream checkouts. Extract it into an empty working directory, enter
`gds-dse/`, then register and pin the dependencies:

```bash
git init

git submodule add https://github.com/librelane/librelane.git tools/librelane
git -C tools/librelane checkout --detach 01ddb25e4424f6f5aaf6b951b14a2b6fdcf737c0

git submodule add https://github.com/secworks/aes.git designs/aes
git -C designs/aes checkout --detach 80dc4718e1dcbbdb4b0dd1bdb393d8f7b98981dc

git submodule add https://github.com/secworks/sha256.git designs/sha256
git -C designs/sha256 checkout --detach 837c5cc396f001d18f2c765721c585716eb439ae

git submodule add https://github.com/secworks/chacha.git designs/chacha
git -C designs/chacha checkout --detach 7eaba360df9fed9fc2db98d5f3df81cf01e5b604

git submodule add https://github.com/secworks/prince.git designs/prince
git -C designs/prince checkout --detach f40631db7a3fe2eb7a09d63696ad610b133db163

git add .gitmodules tools/librelane designs/aes designs/sha256 designs/chacha designs/prince
git submodule status
```

These are all five source dependencies for the current suite. For AES alone,
only LibreLane and AES are needed initially; the other three can be added later.
Adding a design submodule does not schedule any experiments.

Do not add OpenROAD, Yosys or each PDK as more submodules. The pinned LibreLane
checkout supplies the Nix dependency lock and the Ciel PDK revision list.
The [versioning guide](versioning.md) explains those layers.

## Install the tools

Follow the official [LibreLane installation instructions for Linux](https://librelane.readthedocs.io/en/stable/installation/nix_installation/installation_linux.html)
to install Nix and configure the FOSSi binary cache. Then use this checkout,
rather than a separate installation:

```bash
nix-shell tools/librelane/shell.nix
```

Stay in that shell for the flow commands. The runner invokes the LibreLane source
under `tools/librelane`. The first environment build and PDK download may take
time and disk space; do them before interpreting runtime comparisons.

The runner uses Ciel-managed PDKs unless `machine.local.json` specifies otherwise.
There is no need to create that file for the default setup. PDKs are cached in
`pdks/`.

For an existing flow-ready PDK installation, copy `machine.example.json` to
`machine.local.json` and give each selected manual PDK both its root and a real
revision description. The root contains the named PDK directory. Manual
installations are not hashed in full, so preserve the exact installation and
record local changes.

An optional `librelane --smoke-test` diagnoses the tool installation. It runs
LibreLane's own test design, not this study's AES benchmark; it is not an
additional required experiment.

## Validate the selected RTL

Use the upstream tests for each design before measuring its implementation.
For the pinned AES checkout:

```bash
make -C designs/aes/toolruns sim-top sim-core sim-keymem sim-encipher sim-decipher
```

This uses Icarus Verilog (`iverilog` and `vvp`). Check that they are available in
the environment. Read the testbench's final pass/fail summary as well as the
command exit status; some Verilog testbenches print an error without returning
a failing process status.

When adding the other designs to the experiment, their existing targets include:

```bash
make -C designs/sha256/toolruns sim-top sim-core sim-wmem
make -C designs/chacha/toolruns sim-top sim-core
make -C designs/prince/toolruns sim-top sim-core
```

Retain the command, simulator version and test summary with the research notes.
No FPGA board is needed for these ASIC implementation experiments. Functional
simulation and physical verification answer different questions.

## Save the starting revision

Before running, review the intended files:

```bash
git status --short
git add README.md docs configs experiments scripts tests designs tools wrappers reports machine.example.json .gitignore .gitmodules
git diff --cached --stat
git commit -m "Set up controlled RTL-to-GDSII experiments"
```

Review any untracked files inside a submodule separately. Commit submodule
changes in their own repository if they are intentional; the parent repository
only records the chosen commit.

The source archive does not impose a license on the study's own code. Choose
that with the project owner before inviting reuse; upstream submodules retain
their own license files.

Continue with [the first run](running-study.md). Once the study has a GitHub
remote, other users can clone that URL and run `git submodule update --init`.

Reference: [Git's submodule guide](https://git-scm.com/book/en/v2/Git-Tools-Submodules).
