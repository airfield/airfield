<!-- Copied from https://airfield.io/docs/reference/ by scripts/copy_guide.py. Don't edit it
     here: edit src/pages/docs/reference.mdx in the documentation repository,
     then run the script again. -->

# CLI Reference

## Command namespaces

Airfield uses namespaced commands:

- `airfield project ...` for project operations
- `airfield package ...` for package operations
- `airfield subpackages ...` for git operations across nested source repositories
- `airfield system ...` for system setup and maintenance (also reachable as `airfield tools system ...`)
- `airfield docker cache ...` for build-cache management
- `airfield status` for context and runtime status
- `airfield doctor` for host dependency checks
- `airfield guide [topic]` to print the [Quick Start](https://airfield.io/docs/quick-start/), or
  this page, Runtime & Environment or Managing Dependencies, as plain Markdown
  matching the installed version; this is what to give a coding agent

Unique prefixes are accepted at every level, so `airfield pa b .` resolves to
`airfield package build .`. Ambiguous prefixes are rejected with the list of
matches. When the current directory is inside an Airfield project or package,
the CLI prints the detected context; package commands can then omit the
package name (for `build`, pass `.`).

## Project commands

| Command | Description |
| --- | --- |
| `project init [path] --ros-distro <distro>` | Scaffold a project: `airfield.yaml`, `packages/`, `dependencies/`, `plans/example.yaml`. `--force` overwrites an existing marker. |
| `project run <pkg> [--test]` | Run the package's `default` run command (`test` with `--test`). With no `default` defined, opens an interactive shell instead. |
| `project up [plan]` | Render a plan to `.airfield/<plan>.tmuxinator.yml` and launch it as a tmux session. With no plan name, lists available plans. `--inspect` prints the config, `--no-launch` only writes it, `--output <path>` changes the destination. |
| `project liftoff <plan>` | Run each package in the plan's `packages:` list sequentially (no tmux); each blocks until it exits. |
| `project down [plan] [--prune]` | Kill the plan's tmux session; each pane's container stops with it. With no name, tears down every session matching a plan. `--prune` force-removes all `airfield-run-*` containers (crash cleanup). |
| `project deinit` | Remove Airfield config from the project. |

## Package commands

| Command | Description |
| --- | --- |
| `package init <name>` | Scaffold a new package (`airfield.yaml`, `src/`, `README.md`). Inherits the project's `ros_distro` unless `--ros-distro` is given. |
| `package init --path <dir>` | Wrap an existing ROS package in place: reads the name from `package.xml`, writes `airfield.yaml` with `source_path: .`, and reports how each `package.xml` dependency will be installed. Dependencies are not copied; `package.xml` is read on every build. |
| `package build <pkg\|.> [--target-device <arch>] [--push] [--rebuild]` | Make sure the package's environment image `airfield-pkg-<name>:<fingerprint>` exists: reuse the one on this machine, else pull it from the project's `image_registry`, else build it. Docker output streams to the terminal, including the generated Dockerfile. `--push` uploads the image for other machines; `--rebuild` builds from scratch whatever exists. |
| `package run [pkg] [cmd-name]` | Run a named command from the package's `run:` map. With no command name, lists available run commands. `--args "<extra>"` appends arguments to the configured command; `--execution auto\|container\|host` selects where it runs. |
| `package shell [pkg]` | Open an interactive shell in the package container with source mounted. |
| `package cmd [pkg] -- <command>` | Run a one-off command in the package container. |
| `package dependencies check [dir]` | Compare local dependency manifests against the shared packages repository. |
| `package dependencies upstream [dir]` | Copy local manifests into the shared packages repository checkout (asks first). |
| `package dependencies pull` | Update the shared packages repository checkout (`git pull --ff-only`) and fetch rosdep's lookup table again. |
| `package deinit` | Remove Airfield config from the package, its image, and its containers. |

`build`, `check`, and `upstream` take `--target-device x86_64|arm64`
(defaulting to the host architecture).

## Subpackages

The `airfield subpackages` namespace performs batch git operations across the
nested repositories in an Airfield project's `src/` and `packages/`
directories:

- `subpackages status`: clean/dirty/ahead/behind state of every subpackage
- `subpackages commit -m "message"`: commit changes in dirty subpackages
- `subpackages push` / `subpackages pull`: sync subpackages that are ahead/behind
- `subpackages stash`: stash changes in dirty subpackages
- `subpackages clean [--force]`: log, stash, and clean all changes in dirty subpackages
- `subpackages diff [--staged] [--head]`: view diffs across dirty subpackages
- `subpackages switch [branch]`: switch every subpackage to a branch
- `subpackages track`: record all subpackages in `airfield.yaml`
- `subpackages checkout`: clone subpackages listed in `airfield.yaml` that are missing
- `subpackages find <name>` / `subpackages cd <name>`: locate a subpackage
- `subpackages undo`: revert the last batch operation (commit, push, pull, stash, or switch)

Modifying operations prompt per subpackage unless `--auto` is given (`clean`
uses `--force`).

## System commands

| Command | Description |
| --- | --- |
| `system clean [--cache] [--until <dur>] [--aggressive]` | Remove all containers created from Airfield package images; `--cache` also prunes the BuildKit cache. |
| `system setup` | Install the container backend if the host lacks one. |
| `system update [--dry-run] [--force]` | Update Airfield itself to the latest release. Refuses to overwrite an editable (development) install. |
| `system alias [--shells bash,zsh,fish]` | Install an `a` alias for `airfield` into shell RC files. Prints the proposed changes by default; apply with `--no-dry-run`. |
| `system install-completion <shell>` | Install shell completion. |
| `docker cache status` | Show Docker BuildKit cache usage. |
| `docker cache prune [--aggressive] [--until <dur>]` | Prune the BuildKit cache. |
| `status [--path <dir>] [--target-device <arch>]` | Print project/package context, dependency roots, plans, and image state. |
| `doctor [--fix]` | Check host requirements; `--fix` auto-applies supported fixes. |
| `guide [topic]` | Print a page as plain Markdown, for the installed version: the Quick Start by default, or `reference`, `runtime` or `dependencies`. |

`airfield doctor` reports on: a container backend (Docker or compatible), git,
tmux + tmuxinator (needed only for `project up`), detected NVIDIA GPU hardware
and CUDA toolkit version, GPU-enabled PyTorch, shell completion, and available
Airfield updates.

## Package metadata (`airfield.yaml`)

```yaml
kind: package
name: base_driver
dependencies:           # extras only: package.xml files under source_path are read on every build
  - urg_node>=1.0
skip_dependencies:      # optional: package.xml entries to leave out of the image
  - some_unreleased_pkg
source_path: src        # default: src. Use "." when the package root is the ROS package
ros_distro: jazzy       # noetic | humble | jazzy | kilted | rolling
base_image: osrf/ros:jazzy-desktop   # optional override
colcon_args: --cmake-args -DCMAKE_BUILD_TYPE=Release   # optional
default_workdir: .      # optional, relative to the mounted source
devices:                # optional host devices
  - /dev/input
group_add:              # optional supplementary groups
  - "20"
run:                    # named launch targets
  default: ros2 run motor_driver motor_node
  joystick: ros2 run joystick joystick_node
```

Base image precedence: package `base_image`, then project `base_image`, then
the ROS distro's default image for the target architecture (a desktop image
on `x86_64`, the official `ros-base` image on `arm64`). A package with no
`ros_distro` builds from plain `ubuntu:24.04` and skips the ROS toolchain and
the first-run colcon build; this is the shape of config-only tool packages.

A base image has to be Debian or Ubuntu based, because the build installs with
apt. The `airfield` command inside an image needs Python 3.10 or newer: on a
base with an older Python (ROS Noetic and many vendor board images are Ubuntu
20.04) the image is built without that command, and the build says so. Nothing
else about the image, or about how Airfield runs it, changes.

On first container start, Airfield runs
`colcon build --packages-up-to <name>`, so `name` must match a colcon package
in the source tree (a metapackage when wrapping several ROS packages; see
the [Overview](https://airfield.io/docs/overview/)).

## Plan files (`plans/*.yaml`)

```yaml
name: navstack
pre_window: export MAP=speedway   # optional; also valid per window
windows:
  - name: main
    layout: main-vertical         # any tmux layout; default main-vertical
    panes:
      - package: base_driver      # run cmd inside this package's container
        cmd: ros2 run motor_driver motor_node
      - htop                      # plain host command
      -                           # null: bare shell pane
packages: []                      # alternative simple form used by liftoff
```

When a plan defines only `packages:`, `project up` gives each package its own
window running `airfield project run <pkg>`. When both forms are present,
`up` renders `windows:` and `liftoff` reads `packages:`. See
[Create a Plan](https://airfield.io/docs/create-plan/) for the walkthrough.

## Dependency manifests

Dependency manifests are architecture-specific YAML files:

```text
dependencies/x86_64/*.yaml
dependencies/arm64/*.yaml
dependencies/xplatform/*.yaml
```

For each dependency name, Airfield searches in order: the local
`dependencies/<target-device>/`, the local `dependencies/xplatform/`, then the
same two folders in the shared packages repository ("local" meaning the
project root, or the package root for standalone packages). The shared
repository is a sibling `packages/` checkout when present, otherwise a cached
clone of `github.com/airfield/packages` (overridable with the
`AIRFIELD_PACKAGES_REPO` environment variable).

Each manifest may define:

- `name`: must match the filename without `.yaml`
- `version`: versions the manifest itself
- `ros_versions`: optional list of compatible ROS distributions
- `system`: root-level install commands run during image build
- `user`: user-level install commands run during image build
- `host_dependencies`: host-side requirements checked before container build

Semantic version constraints in `airfield.yaml` are preserved. Exact pins such
as `name==1.2.3` are treated as lock values during build-time dependency
installation.

A name with no manifest can resolve as a peer package under `packages/`
(built from source), or as a shared *package definition* that Airfield
materializes into the project; see
[Manage Dependencies](https://airfield.io/docs/manage-dependencies/).

## Local runtime config (`.air`)

Local-only runtime options belong in `.air`, which is gitignored by the
generated `.gitignore`:

```yaml
mounts:
  - /robodata/speedway
```

Both project-level and package-level `.air` files are supported. Package
mounts are appended after project mounts; relative paths resolve against the
package directory, and paths that don't exist are skipped with a warning.

`.air` is for paths that genuinely differ between machines. The colcon
workspace is not one of them and does not belong here — Airfield mounts it on
its own, as described next.

## Shared colcon workspace

Every container mounts the project's `build/`, `install/`, and `log/` so the
compiled workspace outlives the `--rm` container that produced it. This is what
makes the [first-run build](https://airfield.io/docs/runtime/#the-first-run-build) a one-time cost
per package rather than a cost per pane, and it gives the build lock a file
that all containers share. Airfield creates the directories if they are
missing, owned by you rather than by root.

One directory has two paths, and the distinction matters:

| | Path |
| --- | --- |
| On the host | `<project>/.airfield/workspace/{build,install,log}` |
| In the container | `~/workspace/{build,install,log}` |

The container path never varies, on any project or any machine, so anything you
run inside a container uses `~/workspace` unchanged. Only the host location is
project-specific.

The workspace is scoped to the project on purpose. A single machine-wide
workspace would let unrelated projects share build output by package name: two
projects that each define a `base_driver` would resolve to one
`install/base_driver`, so the second project's containers would find the name
already built, skip the build, and source the first project's binaries with no
warning. Scoping the root makes that impossible rather than merely documented.

`.airfield/` is the project's scratch directory, where `project up` also writes
its tmuxinator configs, and `project init` adds it to `.gitignore`. Build output
therefore stays out of version control without any extra setup.

A package that lives outside any project has no root to scope to and falls back
to `~/workspace` on the host.

Set `AIRFIELD_WORKSPACE` to override the host side:

```bash
# Point several projects at one build tree, deliberately
AIRFIELD_WORKSPACE=~/shared-ws airfield project up navstack

# Opt out: every container gets a throwaway workspace and rebuilds from
# scratch. Concurrent panes then build in parallel with no lock between them.
AIRFIELD_WORKSPACE=none airfield package cmd base_driver -- ros2 run ...
```

## Container build cache

Airfield enables Docker BuildKit (`DOCKER_BUILDKIT=1`) and generates
Dockerfiles with persistent cache mounts for apt lists (`/var/lib/apt`),
downloaded packages (`/var/cache/apt`), and pip wheels (`~/.cache/pip`), so a
rebuild only re-downloads what changed. The generated `.dockerignore`
excludes `.git/`, `__pycache__/`, IDE files, and large data files (`*.h5`,
`*.pt`) from the build context. On engines without BuildKit support the
Dockerfile falls back to a compatibility mode automatically.

Inspect and reclaim cache space with `airfield docker cache status` and
`airfield docker cache prune`, or use standard Docker Buildx commands:

```bash
# View build cache usage
docker buildx du

# Prune unused cache
docker buildx prune
```

## Cleanup

Remove Airfield config from a package or project:

```bash
airfield package deinit
airfield project deinit
```

Both remove the affected packages' images and containers as well; `deinit`
prompts before touching anything (`--yes` skips the prompt).

Remove all containers created from Airfield package images:

```bash
airfield system clean
```

Runtime behavior (networking, mounts, GPU access, teardown) and the full
environment-variable list live on the
[Runtime & Environment](https://airfield.io/docs/runtime/) page.
