## Introduction

Airfield is an opinionated robotics framework centered on packages. It defines a consistent project layout, package metadata schema, and command surface so teams can build and run ROS2 code reproducibly.

## Why package-centric

Robotics teams usually share runtime infrastructure but own different software components. Airfield treats each component as a package with explicit metadata:

- what source tree to build
- what dependencies to install
- how packages are grouped into runnable plans

This keeps package ownership clear while still enabling full-project orchestration.

## Core principles

1. Containerized package execution
2. Convention-first project layout
3. Explicit dependency declarations by target architecture
4. Project-level orchestration from package plans

## CLI surface

Airfield uses namespaced commands:

- `airfield package init`
- `airfield package build`
- `airfield package shell`
- `airfield package cmd`
- `airfield package up`
- `airfield project init`
- `airfield project run`
- `airfield project liftoff`
- `airfield tools system clean`
- `airfield status`
- `airfield doctor`

`airfield doctor` reports required system dependencies and accelerator diagnostics
(Docker, shell completion, NVIDIA GPU/CUDA presence, and PyTorch GPU runtime checks).

Any unambiguous unique prefix is accepted by the CLI.

The old top-level commands (`create`, `build`, `up`, `run`, `liftoff`) are removed.

## Project layout

`airfield project init` creates:

```text
<project>/
   airfield.yaml
   packages/
   dependencies/
      x86_64/
      arm64/
   plans/
```

`airfield.yaml` is the root marker used for project discovery by all runtime commands.

## Package model

Each package has an `airfield.yaml`:

```yaml
name: my_pkg
dependencies:
   - ros_base>=1.3,<2.0
   - cv_bridge==3.2.1
source_path: src
```

- `name`: package identifier
- `dependencies`: names resolved from `dependencies/<target_device>/<name>.yaml`
- `source_path`: source folder relative to the package directory

Dependency constraints and locking:

- semantic version constraints in `airfield.yaml` are preserved
- exact pins (`==`) are used as lock values during build-time dependency installation
- dependency manifests are expected to support lock-aware installation behavior

Local-only runtime config belongs in `.air` (gitignored), for example:

```yaml
mounts:
   - /robodata/speedway
```

- `mounts`: extra host directories mounted into container at the same absolute path

## Existing ROS package wrapping

`airfield package init --path /path/to/ros_pkg` supports in-place migration:

1. Detect `package.xml`
2. Extract ROS package name
3. Extract dependency tags (`depend`, `exec_depend`, `build_depend`, `buildtool_depend`, `run_depend`)
4. Write `airfield.yaml` with `source_path: .`

The command does not rewrite ROS Python/C++ sources.

## Dependency model

Dependency manifests are architecture-specific YAML files in:

- `dependencies/x86_64/*.yaml`
- `dependencies/arm64/*.yaml`

Each dependency may define:

- `system`: root-level install commands
- `user`: user-level install commands
- `ros_versions`: compatible ROS distributions
- `host_dependencies`: host-level requirements checked before container build (for example `nvidia_driver`)

For host dependency checks, Airfield can auto-detect GPU presence and driver versions, then prompt for remediation when required host requirements are missing or outdated. CUDA runtime requirements should be expressed as normal dependencies and installed inside the container.

## Runtime workflow

1. `airfield project init` to create project scaffold
2. `airfield package init <name>` to create a new package, or `airfield package init --path ...` to wrap an existing ROS package
3. Fill package source and dependency manifests
4. `airfield package build <name>` to build package container image
5. `airfield project run <name>` to run one package container
6. `airfield package shell <name>` to open an interactive shell in the package container
7. `airfield package cmd --package <name> -- <command...>` to run one command in the package container
8. `airfield project liftoff <plan>` to run all packages listed in a plan
9. `airfield package up <plan>` to render tmuxinator session config (and optionally launch)
10. `airfield status` to inspect package/project metadata and container runtime state
11. `airfield tools system clean` to remove all containers created from Airfield package images

## Plan model

Plans live in `plans/*.yaml`:

```yaml
name: navstack
packages:
   - localization
   - perception
   - planner
```

`airfield project liftoff navstack` loads this file and launches package runs in order.

## Current scope and limitations

- Dependency version constraints are normalized to names for resolution in the current implementation.
- Full dependency graph solving and loop detection are not yet implemented.
- `airfield project run` currently runs package images with an interactive shell entrypoint by default.
- `airfield package deinit` and `airfield project deinit` remove the package image and any containers created from it.