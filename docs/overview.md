# Airfield overview

> For visual diagrams of the command tree, namespace hierarchy, file
> structure, and build/launch flow, see
> [`airfield-architecture-diagrams.md`](airfield-architecture-diagrams.md) and
> [`diagrams/README.md`](diagrams/README.md) in this directory.

## Introduction

Airfield is an opinionated robotics framework centered on packages. It defines
a consistent project layout, package metadata schema, and command surface so
teams can build and run ROS 2 code reproducibly. Each component is a package
with explicit metadata: what source tree to build, what dependencies to
install, and how packages group into runnable plans.

## Why package-centric

Robotics teams usually share runtime infrastructure but own different software
components. Treating each component as a package with explicit metadata keeps
package ownership clear while still enabling full-project orchestration: one
person's package can be built, run, and debugged in isolation, and the whole
stack still comes up from a single plan.

## Core principles

1. Containerized package execution: every package builds to its own container
   image and runs in it.
2. Convention-first project layout.
3. Explicit dependency declarations by target architecture.
4. Project-level orchestration from package plans.

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

`airfield.yaml` at the project root is the root marker used for project
discovery by all runtime commands. Besides `kind`, `name`, `version`, and
`ros_distro`, it can carry an optional `base_image` (inherited by every
package that does not set its own), `pull_base_image: false` for a base image
that is built locally and exists in no registry (inherited along with the
project's `base_image`), and a `subprojects` map recording the
source repositories that make up the project (restored by
`airfield subpackages checkout`). A package can also live standalone with no
enclosing project, which is why package commands accept `.`.

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
pull_base_image: false    # optional; default true (docker build --pull)
colcon_args: ...          # optional; extra args appended to the auto colcon build
default_workdir: ...      # optional; working dir for run/shell/cmd
devices: [/dev/ttyACM0]   # optional; host devices passed through
group_add: ["20"]         # optional; supplementary groups
run:                       # optional; named commands → ros2 run/launch
   default: ros2 run my_pkg my_node
```

- `name` is the package identifier and determines the image name:
  `airfield-pkg-<name>:<fingerprint>`, where the fingerprint is taken from
  everything that decides the image's contents, plus `:latest` for the most
  recent one. An image holds nothing about who built it, so machines of the
  same kind arrive at the same name and can share one build through the
  project's `image_registry:` (see the README, "Building once for several
  machines").
- `dependencies` lists what the image needs beyond the package's own
  `package.xml` files, which are read on every command (see
  [Dependency model](#dependency-model)). A name resolves to a dependency
  manifest if one exists, else to a peer package under `packages/`, whose
  source is mounted and built from source alongside this one, else to the
  apt package rosdep's lookup table gives for it.
- `skip_dependencies` (optional) lists `package.xml` entries to leave out of
  the image.
- `source_path` is the source folder relative to the package directory.
- `run` is a map of named commands. `package run <pkg> <name>` runs one
  (listing them when no name is given), and `project run <pkg>` runs the
  `default` entry, or opens a shell if none exists. `project run --test` runs
  the `test` entry.

Dependency constraints and locking:

- semantic version constraints in `airfield.yaml` are parsed and preserved;
- exact pins (`==`) become environment values
  (`AIRFIELD_DEP_<NAME>_VERSION`) that lock-aware manifests read during
  build-time installation;
- other constraint forms are accepted but not enforced at install time.

Local-only runtime config belongs in `.air` (gitignored). Both a project-level
and a package-level `.air` are read, project first:

```yaml
mounts:
   - /robodata/speedway
```

- `mounts` lists extra host paths mounted into the container at the same
  absolute path. Missing paths are skipped with a warning.

## Existing ROS package wrapping

`airfield package init --path /path/to/ros_pkg` supports in-place migration:

1. Detect `package.xml` and extract the ROS package name.
2. Write `airfield.yaml` with `source_path: .` and an empty `dependencies:`
   list. Nothing is copied out of `package.xml`: it is read again on every
   build, so a copy could only go stale.
3. Print how each `package.xml` entry will be installed, so a name with no
   manifest that is not really an apt package is noticed here.

The command does not rewrite ROS Python/C++ sources. An airfield package can
wrap one or more ROS packages; a single airfield package may ship several
`ros2 run` / `ros2 launch` targets, named by the `run:` map.

## Dependency model

A package's dependencies come from two files:

- every `package.xml` under `source_path`, read on each build and run, so a
  dependency added there reaches the image with nothing else to edit. Build
  and run tags are read (`depend`, `build_depend`, `build_export_depend`,
  `buildtool_depend`, `buildtool_export_depend`, `exec_depend`, `run_depend`),
  `condition` attributes are honored, and `test_depend` / `doc_depend` are
  left out. Files are found the way colcon finds them: a directory holding
  `package.xml` is not searched further, and hidden or `COLCON_IGNORE`d
  directories are skipped;
- `dependencies:` in `airfield.yaml`, for what `package.xml` does not say.

Each name resolves to the first of:

1. a ROS package in the package's own source tree (nothing to install);
2. a dependency manifest: the recipe, which also overrides steps 4 and 5;
3. a peer package in the project, matched by folder name or by a ROS package
   inside it, mounted and built from source. The peer's own `airfield.yaml`
   and `package.xml` dependencies are installed in this image too, because
   the peer is compiled here;
4. rosdep's lookup table: what the name is called on the image's Ubuntu
   release (`nav2_msgs` -> `ros-jazzy-nav2-msgs`, `eigen` -> `libeigen3-dev`);
5. for a name the table does not have, the conventional apt package:
   `ros-<distro>-<name>` for a ROS package name, the name itself for a
   Debian-style name (`python3-serial`).

Steps 4 and 5 apply to names from either file, and are why most names need no
manifest. Manifests are for what rosdep cannot say: installs done by
commands, installs that differ per machine, names rosdep does not know, and
deliberate choices other than rosdep's.

Step 4 reads rosdep's data files (its rules, and the packages released for
the ROS distribution) on the host; rosdep itself is never installed or run.
The table is fetched once into `~/.cache/airfield/rosdep/` and refreshed only
by `airfield package dependencies pull`, so translating a name needs no
network during a build and does not change from one day to the next. If it
cannot be fetched, step 5 is used alone and the build says so.

Steps 4 and 5 describe an image built for a ROS distribution, so they apply
only to packages that set `ros_distro`; without it every name needs a manifest
and a miss is an error. If apt rejects a translated name, the failed build
names the file that listed it and the fixes: correct the name, add a manifest,
or drop it (for a `package.xml` entry, through `skip_dependencies:`). A
`package.xml` entry that cannot be a package name is ignored with a warning.
`airfield status` shows, per dependency, which file asked for it and how it
resolved.

Dependency manifests are architecture-specific YAML files in the separate
packages repository:

- local checkout next to the Airfield source tree:
  `../packages/x86_64/*.yaml` and `../packages/arm64/*.yaml`;
- fallback: a cached clone of `https://github.com/airfield/packages`
  (overridable via `AIRFIELD_PACKAGES_REPO`);
- search order: local `dependencies/<device>/` → local `dependencies/xplatform/`
  → global `<device>/` → global `xplatform/`.

If a package keeps local dependency manifests in its source tree, Airfield
still reads them, but prints a build warning telling you to upstream them with
`airfield package dependencies upstream`.

Each dependency may define:

- `system`: root-level install commands;
- `user`: user-level install commands;
- `ros_versions`: compatible ROS distributions;
- `host_dependencies`: host-level requirements checked before container build
  (for example `nvidia_driver`).

For host dependency checks, Airfield detects GPU presence and driver versions,
then prompts for remediation when required host requirements are missing or
outdated (non-interactive runs fall back to CPU installs). CUDA runtime
requirements should be expressed as normal dependencies and installed inside
the container.

The search paths can also hold shared *package definitions* (`kind: package`
manifests, optionally with a `source: {url, ref}`). Naming one in a command or
plan materializes it into `packages/<name>/` as a gitignored, reproducible
use-only package. This is for tools a project runs but does not develop.

## Plan model

Plans live in `plans/*.yaml` and group packages into a launch target:

```yaml
name: navstack
packages:
   - localization
   - perception
   - planner
```

A plan can also define tmux `windows` / `panes`, where each pane names a
package and a command. `airfield project up <plan>` renders the plan to a
tmuxinator config and launches it: package panes each become an
`airfield package cmd <pkg> -- bash -lc "<cmd>"`, and a packages-only plan
gets one window per package running `airfield project run <pkg>`.
`airfield project liftoff <plan>` reads only the `packages:` list and runs
each package's `default` command in sequence, blocking on each.

## CLI surface / command tree

Every invocation is `airfield <namespace> <command> [args]`, e.g.
`airfield project up navstack` = `airfield` → `project` → `up` → arg `navstack`.
The only two leaves that skip a namespace are `status` and `doctor`.

```text
airfield
│
├─ package ……………… package operations  (acts on ONE package)
│    ├─ init                 create a package / wrap an existing ROS package (--path)
│    ├─ deinit               remove airfield config, image, and containers
│    ├─ build                build a package image
│    ├─ shell                open a shell in the package container
│    ├─ cmd                  run an arbitrary command in the container (cmd <pkg> -- …)
│    ├─ run                  run a named command from the package's `run:` map
│    └─ dependencies ……… dependency-repo operations
│         ├─ check
│         ├─ upstream
│         └─ pull
│
├─ project ……………… project operations  (acts on the WORKSPACE)
│    ├─ init                 scaffold a new project
│    ├─ deinit               remove airfield config from project
│    ├─ run                  run a package's `default` command (or shell)
│    ├─ liftoff              run a plan's packages list (defaults, in sequence; no tmux)
│    ├─ up                   render a plan to a tmuxinator session and launch it
│    │                       (no plan = list plans; --no-launch = generate only;
│    │                        --inspect = print only)
│    └─ down                 kill a plan's tmux session; panes stop their own
│                            containers (--prune sweeps crash orphans)
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
│    ├─ status / commit / push / pull / stash / clean / track / checkout
│    ├─ undo / diff / switch / find / cd
│
├─ status …………………  (leaf) print project/package context & runtime status
└─ doctor …………………  (leaf) check system dependencies   [--fix]
```

`airfield doctor` reports required system dependencies and accelerator
diagnostics: a container backend, git, tmux + tmuxinator (warn-only; needed
for `project up`), shell completion, NVIDIA GPU/CUDA presence, PyTorch GPU
runtime (inside containers), and available Airfield updates.

### Prefix matching

The `PrefixGroup` class in `main.py` accepts any unambiguous prefix at each
level, so `airfield proj up navstack` or `airfield package dep check` work. A
prefix that matches two commands at the same level errors out as ambiguous.
Unknown top-level commands are not accepted as fallback.

## Namespace = which level it acts on

The command's namespace tells you which level of the hierarchy it touches:

```text
COMMAND NAMESPACE              ACTS ON
────────────────────────────────────────────────────────────
airfield package ...    ──►    ONE package        (build it, run it, shell in)
airfield project ...    ──►    the WORKSPACE       (init it, or orchestrate
                                                    across packages via a plan)
airfield subpackages... ──►    the source repos inside packages
airfield system/doctor  ──►    your machine / context
```

## run vs liftoff vs up: one vs many

```text
ONE package                              MANY packages
─────────────────────────────           ──────────────────────────────
package run <pkg> <cmd-name>             project liftoff <plan>
  runs one NAMED command from the          runs the plan's packages: list,
  package's run: map, in its               each package's default command,
  container                                in sequence (no tmux)

project run
  convenience: runs the selected
  package's `default` command,
  else opens a shell (still ONE)

project up <plan>
  renders the plan to a tmuxinator session and LAUNCHES it (one tmux pane
  per plan entry, each an `airfield package cmd <pkg> -- <cmd>`).
  --no-launch = generate the config only; --inspect = print it.
```

Mnemonic: **run = launch one. liftoff = launch the whole stack (the rocket).
up = the whole stack, laid out as a tmux session.**

## Runtime workflow

1. `airfield project init` to create the project scaffold.
2. `airfield package init <name>` to create a new package, or
   `airfield package init --path ...` to wrap an existing ROS package.
3. Fill package source; declare dependencies in `package.xml`.
4. `airfield package build <name>` to build the package container image.
5. `airfield package run <name> <run-command>` to run a named package command.
6. `airfield package shell <name>` to open an interactive shell in the package
   container.
7. `airfield package cmd <name> -- <command...>` to run one command in the
   package container.
8. `airfield project up <plan>` to render the plan to a tmuxinator session and
   launch it; `airfield project down` to tear it down.
9. `airfield project liftoff <plan>` to run a plan's packages sequentially
   without tmux.
10. `airfield status` to inspect package/project metadata and container runtime
    state.
11. `airfield tools system clean` to remove all containers created from
    Airfield package images.

## Current scope and limitations

- Dependency resolution is by name; version constraints other than exact pins
  are parsed but not enforced at install time, and there is no full dependency
  graph solver.
- Names with no manifest are translated with rosdep's data, read on the host.
  rosdep's installers other than apt and pip (source, snap, gem) are not
  supported; a name that needs one needs a manifest.
- `airfield project run` runs a package's `default` command when present,
  otherwise it opens an interactive shell.
- `airfield package deinit` and `airfield project deinit` remove the affected
  package images and any containers created from them.
