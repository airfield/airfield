# Airfield overview

> This document consolidates the former `airfield-explainer.md` and
> `command-tree.md` into a single overview. For visual diagrams of the command
> tree, namespace hierarchy, file structure, and build/launch flow, see
> [`airfield-architecture-diagrams.md`](airfield-architecture-diagrams.md) in
> this directory.

## Introduction

Airfield is an opinionated robotics framework centered on packages. It defines a
consistent project layout, package metadata schema, and command surface so teams
can build and run ROS 2 code reproducibly. Each component is treated as a package
with explicit metadata — what source tree to build, what dependencies to install,
and how packages are grouped into runnable plans — which keeps package ownership
clear while still enabling full-project orchestration.

## Why package-centric

Robotics teams usually share runtime infrastructure but own different software
components. Airfield treats each component as a package with explicit metadata:

- what source tree to build
- what dependencies to install
- how packages are grouped into runnable plans

This keeps package ownership clear while still enabling full-project orchestration.

## Core principles

1. **Containerized package execution** — every package builds to its own
   container image and runs in it.
2. **Convention-first project layout.**
3. **Explicit dependency declarations by target architecture.**
4. **Project-level orchestration from package plans.**

## Project layout

`airfield project init` creates:

```text
<project>/
   airfield.yaml          project marker (kind: project) — used for project discovery
   packages/              one buildable ROS package → one container image
   dependencies/
      x86_64/             per-target-device dependency manifests
      arm64/
   plans/                 named groups of packages (launch targets)
```

`airfield.yaml` at the project root is the root marker used for project discovery
by all runtime commands. A package can also live standalone (no enclosing
project), which is why package commands accept `.`.

## Package model

Each package has an `airfield.yaml` (`kind: package`):

```yaml
kind: package
name: my_pkg
dependencies:
   - ros_base>=1.3,<2.0
   - cv_bridge==3.2.1
source_path: src
ros_distro: jazzy          # optional
base_image: ...           # optional; overrides the project default
colcon_args: ...          # optional; extra args appended to the auto colcon build
devices: [/dev/ttyACM0]   # optional; host devices passed through
group_add: ["20"]         # optional; supplementary groups
run:                       # optional; named commands → ros2 run/launch
   default: ros2 run my_pkg my_node
```

- `name`: package identifier.
- `dependencies`: names resolved from `dependencies/<target_device>/<name>.yaml`.
- `source_path`: source folder relative to the package directory.
- `run`: a map of named commands; `package run <pkg> <name>` runs one, and
  `project run <pkg>` runs the `default` entry (or opens a shell if none).

**Dependency constraints and locking:**

- semantic version constraints in `airfield.yaml` are preserved;
- exact pins (`==`) are used as lock values during build-time dependency
  installation;
- dependency manifests are expected to support lock-aware installation behavior.

**Local-only runtime config** belongs in `.air` (gitignored), for example:

```yaml
mounts:
   - /robodata/speedway
```

- `mounts`: extra host directories mounted into the container at the same
  absolute path.

## Existing ROS package wrapping

`airfield package init --path /path/to/ros_pkg` supports in-place migration:

1. Detect `package.xml`.
2. Extract the ROS package name.
3. Extract dependency tags (`depend`, `exec_depend`, `build_depend`,
   `buildtool_depend`, `run_depend`).
4. Write `airfield.yaml` with `source_path: .`.

The command does not rewrite ROS Python/C++ sources. An airfield package can wrap
**one or more** ROS packages — a single airfield package may ship several
`ros2 run` / `ros2 launch` targets (the `run:` map names them).

## Dependency model

Dependency manifests are architecture-specific YAML files in the separate
packages repository:

- local checkout next to the Airfield source tree:
  `../packages/x86_64/*.yaml` and `../packages/arm64/*.yaml`;
- fallback source: `https://github.com/airfield/packages`;
- search order: local `dependencies/<device>/` → local `dependencies/xplatform/`
  → global `<device>/` → global `xplatform/`.

If a package keeps local dependency manifests in its source tree, Airfield still
reads them, but prints a build warning telling you to upstream them with
`airfield package dependencies upstream`.

Each dependency may define:

- `system`: root-level install commands;
- `user`: user-level install commands;
- `ros_versions`: compatible ROS distributions;
- `host_dependencies`: host-level requirements checked before container build
  (for example `nvidia_driver`).

For host dependency checks, Airfield can auto-detect GPU presence and driver
versions, then prompt for remediation when required host requirements are missing
or outdated. CUDA runtime requirements should be expressed as normal dependencies
and installed inside the container.

## Plan model

Plans live in `plans/*.yaml` and group packages into a launch target:

```yaml
name: navstack
packages:
   - localization
   - perception
   - planner
```

A plan can also define tmux `windows` / `panes`, where each pane names a package
and a command — this is what `airfield project up` renders into a tmuxinator
session. `airfield project liftoff <plan>` loads the file and launches the listed
packages in order; `airfield project up <plan>` renders the plan to a tmuxinator
session and launches it.

## CLI surface / command tree

Every invocation is `airfield <namespace> <command> [args]`, e.g.
`airfield project up navstack` = `airfield` → `project` → `up` → arg `navstack`.
The only two leaves that skip a namespace are `status` and `doctor`.

```text
airfield
│
├─ package ……………… package operations  (acts on ONE package)
│    ├─ init                 create / wrap a package
│    ├─ deinit               remove airfield config from package
│    ├─ build                build a package image
│    ├─ shell                open a shell in the package container
│    ├─ cmd                  run an arbitrary command in the container (cmd <pkg> -- …)
│    ├─ run                  run a named command from the package's `run:` map
│    └─ dependencies ……… dependency-repo operations
│         ├─ check
│         └─ upstream
│
├─ project ……………… project operations  (acts on the WORKSPACE)
│    ├─ init                 scaffold a new project
│    ├─ deinit               remove airfield config from project
│    ├─ run                  run a package's `default` command (or shell)
│    ├─ liftoff              run a plan (each package's default, in sequence; no tmux)
│    └─ up                   render a plan to a tmuxinator session and launch it
│                           (--no-launch = generate the config only)
│
├─ tools ………………… system tools
│    └─ system ………… system maintenance
│         ├─ clean
│         ├─ setup
│         ├─ update
│         ├─ alias
│         └─ install-completion
│
├─ system ………………  ⟵ same group as `tools system`, exposed at top level
│    ├─ clean / setup / update / alias / install-completion
│
├─ docker ………………  docker build optimization
│    └─ cache
│
├─ subpackages ………  multi-repo source-code ops (module is `subprojects.py`)
│    ├─ status / commit / push / pull / stash / clean / track / checkout / undo / diff
│
├─ status …………………  (leaf) print project/package context & runtime status
└─ doctor …………………  (leaf) check system dependencies   [--fix]
```

`airfield doctor` reports required system dependencies and accelerator diagnostics
(Docker, shell completion, NVIDIA GPU/CUDA presence, and PyTorch GPU runtime
checks).

### Prefix matching

The `PrefixGroup` class in `main.py` accepts any unambiguous prefix at each
level, so `airfield proj up navstack` or `airfield package dep check` work. A
prefix that matches two commands at the same level errors out as ambiguous.
Unknown top-level commands are not accepted as fallback. The old top-level
commands (`create`, `build`, `up`, `run`, `liftoff`) are removed.

## Namespace = which level it acts on

This is the key insight: the command's **namespace** tells you which level of the
hierarchy it touches.

```text
COMMAND NAMESPACE              ACTS ON
────────────────────────────────────────────────────────────
airfield package ...    ──►    ONE package        (build it, run it, shell in)
airfield project ...    ──►    the WORKSPACE       (init it, or orchestrate
                                                    across packages via a plan)
airfield subpackages... ──►    the source repos inside packages
airfield system/doctor  ──►    your machine / context
```

## run vs liftoff vs up — one vs many

```text
ONE package                              MANY packages
─────────────────────────────           ──────────────────────────────
package run <pkg> <cmd-name>             project liftoff <plan>
  runs one NAMED command from the          runs a whole PLAN — every
  package's run: map, in its               package listed in
  container                                 plans/<plan>.yaml, in sequence

project run
  convenience: runs the selected
  package's `default` command,
  else opens a shell (still ONE)

project up <plan>
  renders the plan to a tmuxinator session and LAUNCHES it (one tmux pane
  per plan entry, each an `airfield package cmd <pkg> -- <cmd>`).
  --no-launch = generate the config only.
```

Mnemonic: **run = launch one. liftoff = launch the whole stack (the rocket).
up = the whole stack, laid out as a tmux session.**

## Runtime workflow

1. `airfield project init` to create the project scaffold.
2. `airfield package init <name>` to create a new package, or
   `airfield package init --path ...` to wrap an existing ROS package.
3. Fill package source and dependency manifests.
4. `airfield package build <name>` to build the package container image.
5. `airfield package run <name> <run-command>` to run a named package command.
6. `airfield package shell <name>` to open an interactive shell in the package
   container.
7. `airfield package cmd <name> -- <command...>` to run one command in the
   package container.
8. `airfield project liftoff <plan>` to run all packages listed in a plan
   (in sequence).
9. `airfield project up <plan>` to render the plan to a tmuxinator session and
   launch it.
10. `airfield status` to inspect package/project metadata and container runtime
    state.
11. `airfield tools system clean` to remove all containers created from Airfield
    package images.

## Current scope and limitations

- Dependency version constraints are normalized to names for resolution in the
  current implementation.
- Full dependency graph solving and loop detection are not yet implemented.
- `airfield project run` currently runs a package's `default` command when
  present, otherwise it opens an interactive shell.
- `airfield package deinit` and `airfield project deinit` remove the package
  image and any containers created from it.
