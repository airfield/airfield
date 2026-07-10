# airfield

Full Documentation: [airfield.io](https://airfield.io)

Airfield is a package-centric robotics framework for structuring projects, declaring dependencies, and running reproducible package containers.

## Prerequisites

- Linux, or an Apple Silicon Mac (uses Apple's `container` tool). Native Windows is not supported.
- [Docker](https://docs.docker.com/engine/install/) with daemon access for your user (`docker info` should work without sudo)
- `git`
- `tmux` and [`tmuxinator`](https://github.com/tmuxinator/tmuxinator) — only needed for `airfield project up` plan launches (`apt-get install tmux tmuxinator`)

Check your setup any time with `airfield doctor`.

## Install

[Install pipx](https://pipx.pypa.io/stable/how-to/install-pipx/)

Install the airfield tool from the master branch, which is stable:

```bash
pipx install git+https://github.com/airfield/airfield.git
```

Shell completion is managed by your shell startup files, so it cannot be fully auto-enabled by `pipx install` alone.
After installing, run:

```bash
airfield system install-completion bash
```

Optionally, also install an `a` alias for `airfield`:

```bash
airfield system alias --yes
```

## Command model

Airfield uses namespaced commands:

- `airfield package ...` for package operations
- `airfield project ...` for project operations
- `airfield subprojects ...` for subproject source code operations
- `airfield system ...` for system setup and maintenance
- `airfield tools ...` for maintenance tasks
- `airfield status` for context and runtime status
- `airfield doctor` for system dependency checks

Common lifecycle commands:

- `airfield package init`
- `airfield package deinit`
- `airfield package shell`
- `airfield package cmd`
- `airfield project init`
- `airfield project deinit`
- `airfield project run` (run a package's `default` command, or open a shell)
- `airfield subprojects status` (check all subprojects for changes)
- `airfield subprojects commit -m "message"` (commit changes in all subprojects)
- `airfield subprojects push` (push commits in all subprojects)
- `airfield subprojects pull` (pull commits in all subprojects)
- `airfield subprojects stash` (stash changes in all subprojects)
- `airfield subprojects clean` (clean and reset all subprojects to match remote)
- `airfield subprojects undo` (undo the last subprojects operation)
- `airfield system alias` (install `a` shorthand)
- `airfield system install-completion` (set up shell completion)
- `airfield system update` (update the Airfield tool to the latest release)
- `airfield system clean` (remove containers)
- `airfield doctor`

Unique prefixes are accepted when they identify one registered command at that
level. Unknown top-level command fallback is not accepted, so package and
project commands still need a namespace or a unique namespace prefix.

When Airfield detects the current directory is inside an Airfield project or package, it prints the detected context, but it does not route unknown top-level commands into that context.

Top-level legacy commands (`create`, `build`, `up`, `run`, `liftoff`) are intentionally removed.

## Quick start

### 1. Initialize a project

```bash
airfield project init ./my_robot --ros-distro jazzy
```

This creates:

```text
my_robot/
	airfield.yaml
	.gitignore
	.dockerignore
	packages/
	dependencies/
		x86_64/
		arm64/
	plans/
		example.yaml
```

`airfield.yaml` is the project marker used by all `package` and `project` commands.

### 2. Initialize a new Airfield package

From inside an Airfield project:

```bash
airfield package init nav_stack
```

This creates:

```text
packages/nav_stack/
	airfield.yaml
	src/
```

Standalone package initialization is also supported (no project required):

```bash
airfield package init .
```

This creates `airfield.yaml` and `src/` in the current directory.

### 3. Wrap an existing ROS package in place

```bash
airfield package init --path /path/to/existing_ros_package --ros-distro jazzy
```

If `package.xml` exists in that path, Airfield adds:

- `airfield.yaml` with inferred `name` and dependency list
- `ros_distro` to select the ROS workspace base image (`noetic`, `humble`, or `jazzy`)
- `AIRFIELD.md` with migration notes

It does not rewrite existing ROS source files.

### 4. Build a package image

```bash
airfield package build nav_stack --target-device x86_64
```

In a standalone package directory, use `.` for the current package:

```bash
airfield package build . --target-device x86_64
```

To show full Docker build logs for debugging:

```bash
airfield package build . --target-device x86_64 --show-all-output
```

Inside a project, dependency manifests are resolved from the project root:

- `./dependencies/<target-device>/*.yaml`

In a standalone package, dependency manifests are resolved from the package root:

- `./dependencies/<target-device>/*.yaml`

Airfield does not copy package source into image layers. It mounts `source_path` into the container at runtime.

### 5. Run a named package command

Define named commands in `packages/nav_stack/airfield.yaml`:

```yaml
run:
	list-packages: ros2 pkg list
```

```bash
airfield package run nav_stack list-packages
```

In a standalone package directory:

```bash
airfield package run . list-packages
```

### 5b. Open shell in package container

```bash
airfield package shell nav_stack
```

### 5c. Run command in package container

```bash
airfield package cmd nav_stack -- ros2 pkg list
```

To run in the current package:

```bash
airfield package cmd . -- ros2 pkg list
```

### 6. Run a plan

Plans (`plans/*.yaml`) describe tmux sessions: windows and panes, where each pane
can run a command inside a package's container. `project init` scaffolds
`plans/example.yaml` showing the format.

```bash
airfield project up example
```

This compiles the plan to a tmuxinator config and launches it (requires tmux +
tmuxinator). Useful flags: `--no-launch` (only generate the config),
`--inspect` (print it to stdout), `--output <path>`.

There is also `airfield project liftoff <plan>`, which reads a plan's
`packages:` list and runs each package's `default` command *sequentially,
blocking until each exits*. Prefer `project up` for launching robot stacks.

### 8. Remove Airfield config from a package or project

```bash
airfield package deinit
airfield project deinit
```

Both commands require confirmation by default. Use `--yes` to skip confirmation.
Deinit also removes the package image and any containers created from it.

### 9. Inspect current Airfield status

```bash
airfield status
```

This prints relevant project/package metadata, dependency resolution roots, and package container image/container state.

### 10. Clean Airfield containers

```bash
airfield tools system clean
```

This removes all containers created from Airfield package images.

### 11. Check Airfield system dependencies

```bash
airfield doctor
```

This checks required system dependencies (including Docker availability/daemon access)
and shell completion configuration for the current shell.
It also reports GPU accelerator diagnostics:

- detected NVIDIA GPU hardware (if present)
- CUDA toolkit version (if present)
- PyTorch installation/version
- whether PyTorch can allocate and compute on GPU

When run inside a container, `airfield doctor` downgrades engine availability failures
to warnings (instead of hard failures) and reports whether `docker` maps to another
backend such as Podman, Singularity, or Apptainer.

Use `--fix` to auto-apply supported fixes (currently shell completion setup):

```bash
airfield doctor --fix
```

## Package metadata

Example package `airfield.yaml`:

```yaml
name: nav_stack
ros_distro: jazzy
base_image: ghcr.io/example/nav-stack:latest
dependencies:
	- ros_base>=1.3,<2.0
	- cv_bridge==3.2.1
source_path: src
```

- Package dependency definitions should live in `https://github.com/airfield/packages`. When working locally on developing the `airfield` tool, Airfield first looks for a sibling `packages/<target_device>/*.yaml` checkout next to the source tree, and if that is not present it uses the shared clone under Airfield's runtime cache. See the packages repository README for details.
- If working on a new package, you can have a local `dependencies/<target_device>/` folder in the project or standalone package. Airfield will build from it but prints a warning telling you to upstream the manifests with `airfield package dependencies upstream .`.
- `source_path` is relative to the package directory
- `ros_distro` selects the ROS base image and workspace overlay
- `base_image` optionally overrides the generated image's `FROM` line; when omitted, ROS packages use the selected ROS base image and non-ROS packages use `ubuntu:24.04`
- For wrapped ROS packages, `source_path` is usually `.`

Dependency policy:

- Airfield preserves semantic version constraints in `airfield.yaml` (for example `name>=1.2,<2.0`)
- exact pins (`name==x.y.z`) are treated as lock values and are exported into dependency installers
- dependency manifests should support semantic version locking so package builds are reproducible across devices

Host dependency policy for accelerated packages:

- dependency manifests can declare `host_dependencies` separately from in-container install commands
- Airfield auto-detects NVIDIA GPU and NVIDIA driver version before build
- if required host dependencies are missing or outdated, Airfield prints install/upgrade guidance and prompts before continuing
- in non-interactive mode, Airfield auto-selects safe defaults (for example CPU install path)
- CUDA runtime/toolkit dependencies can be declared as normal dependencies and installed inside the container

Local-only runtime options should go in `.air` (gitignored), not `airfield.yaml`.

Example package `.air`:

```yaml
mounts:
	- /robodata/speedway
```

- `mounts` adds host directory mounts at the same in-container path
- both `<project>/.air` and `<package>/.air` are supported; package mounts are appended after project mounts

## Project plan metadata

Example `plans/example.yaml`:

```yaml
name: example
packages:
	- nav_stack
	- perception
```

## Image contents and environment toggles

Generated images are intentionally minimal: the base image plus `python3-pip`,
`git`, the ROS build tool for the selected distro, and the airfield CLI itself
(installed from your running copy, never from PyPI). Everything else — OpenCV,
GUI libraries, extra shells — must be declared as dependencies so each package
only pays for what it uses.

Environment variables that change build/run behavior (each build prints the
effective settings in an `[airfield] build settings:` line):

- `AIRFIELD_NO_PULL=1` — don't `--pull` the base image; required when `base_image` is a locally-built image that exists in no registry
- `AIRFIELD_PACKAGES_REPO` — git URL of the shared dependency-manifest repository (default `https://github.com/airfield/packages.git`); set for forks, mirrors, or air-gapped sites
- `AIRFIELD_REPO` — GitHub `owner/name` slug used for update checks (default `airfield/airfield`)
- `AIRFIELD_FORCE_DOCKER_CACHE_MOUNTS=1` / `AIRFIELD_DISABLE_DOCKER_CACHE_MOUNTS=1` — override BuildKit cache-mount detection
- `AIRFIELD_TORCH_INSTALL_TARGET` (or `TORCH_INSTALL_TARGET`), `AIRFIELD_TORCH_VERSION`, `AIRFIELD_TORCH_GPU_WHL_TAG` — PyTorch build args; `gpu` also enables GPU passthrough at run time on non-Jetson hosts (Jetson hosts always get GPU/camera passthrough)
- `MAKEFLAGS` / `CMAKE_BUILD_PARALLEL_LEVEL` — override the parallelism of the automatic in-container `colcon build` run by `package cmd`/`package run`

## Notes

- Run commands from inside an Airfield project or one of its subdirectories.
- `airfield project run` currently runs the selected package's `default` command when present, otherwise it starts an interactive container shell. `airfield project run --test` runs the package's `test` command and fails if none is defined.


## Development

To install Airfield globally for development (so the `airfield` command is available everywhere but points to this source code) and include testing tools:

```bash
pipx install --force --editable ".[test]"
```

### Running tests

To run the test suite in an isolated environment using your local source:

```bash
PIPX_DEFAULT_BACKEND=pip pipx run --no-cache --editable --spec ".[test]" python3 -m pytest --cov=src/airfield
```

Alternatively, if you are using `uv`:

```bash
uv run --no-env-file --extra test pytest --cov=src/airfield
```

### Release Tagging and Hooks

To ensure update checks work correctly, please tag releases on GitHub after pushing code:
1. Commit and push your changes.
2. Tag the release commit (e.g., `git tag v0.1.1`).
3. Push the tag to GitHub (e.g., `git push origin v0.1.1`).

#### Git Hook

Install the repository's pre-push hook. This hook validates that any version tag being pushed matches the current version declared in the codebase (`src/airfield/__init__.py`), blocking mismatched tag pushes. If pushing a branch, it prints a tagging reminder.

To configure and install the hook:

```bash
# Configure Git to use the repository's hooks folder (recommended):
git config core.hooksPath .githooks

# Alternatively, copy and activate the hook script inside your local .git directory:
cp .githooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

The `airfield doctor` command checks if this hook is configured when run inside the repository and will report a failure if the hook is inactive or missing.
