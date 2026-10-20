<!-- Copied from https://airfield.io/docs/quick-start/ by scripts/copy_guide.py. Don't edit it
     here: edit src/pages/docs/quick-start.mdx in the documentation repository,
     then run the script again. -->

# Quick Start

The full loop in one sitting: install, project, package, image, run, plan,
and converting an existing workspace. The other tutorials at
https://airfield.io cover each piece in depth.

> **Using a coding agent?** Start it on this guide: it covers installing
> Airfield, converting a project, and working in one. Once Airfield is
> installed, `airfield guide` prints it for the installed version, and
> `airfield guide reference`, `airfield guide runtime` and
> `airfield guide dependencies` print the CLI Reference, Runtime & Environment
> and Managing Dependencies pages the same way. `airfield project init` leaves
> an `AGENTS.md` that tells agents to run `airfield guide`. Before installing,
> paste the link (`https://airfield.io/docs/quick-start/`) or upload a copy of
> the page. Some agents read a web page only as a summary, which drops exact
> commands; `airfield guide` gives them the full text.

## Rules of thumb

- A directory with an `airfield.yaml` belongs to Airfield. Use the `airfield`
  CLI for it; don't run `docker`, `colcon`, or `rosdep` on the host. The
  toolchain and dependencies live inside the package's image.
- ROS dependencies belong in `package.xml`. Airfield reads it on every build,
  so don't copy its entries into `airfield.yaml`.
- Commands that ask for confirmation take a flag to skip it: `--auto` for
  `subpackages` operations, `--force` for `subpackages clean`, `--yes` for
  `deinit`.
- `package build --push` uploads an image for other machines to use. Only
  pass it on purpose.
- `airfield status`, run inside a package, shows the context, every
  dependency, which file asked for it, and how it resolved.

## 1. Install

Airfield needs Python 3.10 or newer, git, and Docker (or a compatible
engine). tmux and tmuxinator are needed only to launch plans with
`project up`.

```bash
pipx install git+https://github.com/airfield/airfield.git
airfield doctor            # check the host; --fix applies supported fixes
airfield system setup      # only if doctor finds no container backend
airfield system install-completion bash   # optional
airfield guide             # print this guide
```

Install from GitHub as shown: the `airfield` package on PyPI is an unrelated
project.

## 2. Initialize a project

```bash
airfield project init ./my_robot --ros-distro jazzy
```

This creates the project marker (`airfield.yaml`), a `packages/` folder,
dependency roots for `x86_64` and `arm64`, and a `plans/` directory with a
ready-to-run `example.yaml`. Supported distributions are `noetic`, `humble`,
`jazzy`, `kilted`, and `rolling`.

It also writes an `AGENTS.md` that tells coding agents to read
`airfield guide`. If the project already has an `AGENTS.md`, a short section
is added at its end and nothing else in it changes.

## 3. Initialize a package

From inside an Airfield project:

```bash
cd my_robot
airfield package init nav_stack
```

A package can also live on its own, outside any project. Run the init from
inside the package's directory:

```bash
mkdir nav_stack && cd nav_stack
airfield package init .
```

## 4. Wrap an existing ROS package

```bash
airfield package init --path /path/to/existing_ros_package --ros-distro jazzy
```

Inside a project, move the package to `packages/<name>/` first and pass that
path: that is where a package named on the command line or in a plan is looked
up (see step 9).

If `package.xml` exists, Airfield infers the ROS package name, then writes
`airfield.yaml` (with `source_path: .`) without rewriting the ROS source
files. The package's dependencies stay in `package.xml`: Airfield reads it on
every build, so a `<depend>` you add later is installed on the next build with
no change to `airfield.yaml`. A name with no dependency manifest is
translated the way rosdep translates it (`nav2_msgs` to `ros-<distro>-nav2-msgs`,
`eigen` to `libeigen3-dev`); the command lists what each one becomes, and
points out any it could only guess at.

## 5. Build a package image

```bash
airfield package build nav_stack --target-device x86_64
```

The build streams full Docker output and prints the generated Dockerfile, so
there is nothing hidden to debug. The first build downloads the declared apt
and pip dependencies; later builds reuse BuildKit cache mounts, which makes
dependency-only rebuilds substantially faster. A generated `.dockerignore`
keeps source artifacts and data files out of the build context.

The image only needs rebuilding when `package.xml`, `dependencies:`, or a
dependency manifest changes. Source edits don't need a new image; see
[Edit, rebuild, run](#10-edit-rebuild-run).

## 6. Run the package

Define a named run command in `packages/nav_stack/airfield.yaml`:

```yaml
run:
  default: ros2 launch nav_stack bringup.launch.py   # what project run / liftoff use
  list-packages: ros2 pkg list
devices: [/dev/ttyACM0]   # optional host devices; missing ones are skipped
group_add: ["20"]         # optional supplementary groups (dialout for serial)
```

```bash
airfield package run nav_stack list-packages
```

Running `airfield package run nav_stack` with no command name lists the
available run commands. You can also open a package container shell:

```bash
airfield package shell nav_stack
```

Or run one command in the package container:

```bash
airfield package cmd nav_stack -- ros2 pkg list
```

Containers run as your own user, so files they write on mounted folders are
yours. Don't add a `--user` flag or bake a user into a base image.

## 7. Launch a plan

```bash
airfield project up example
```

This renders `plans/example.yaml` into a tmuxinator config at
`.airfield/example.tmuxinator.yml` and launches it as a tmux session, one
pane per plan entry. Use `--no-launch` to only generate the config, or
`--inspect` to print it without writing anything. Tear the session down (and
its containers) with:

```bash
airfield project down example
```

A plan pane that names a package runs its command in that package's
container:

```yaml
name: robot
windows:
  - name: main
    panes:
      - package: nav_stack
        cmd: ros2 launch nav_stack bringup.launch.py
      - htop                   # plain host command
```

`airfield project liftoff <plan>` is the alternative for plans that define a
simple `packages:` list instead of `windows:`; it runs each package's default
command sequentially, without tmux, blocking until each exits. The scaffolded
`example.yaml` uses `windows:`, so launch it with `project up`.

## 8. Manage subpackages

If your project contains multiple nested git repositories (under `src/` or
`packages/`), the `subpackages` commands perform batch git operations across
all of them:

```bash
# Check the git status of all subpackages
airfield subpackages status

# Commit changes in all dirty subpackages
airfield subpackages commit -m "Update parameters"

# Clean and reset all subpackages to discard local changes
airfield subpackages clean --force
```

To record every present repository in `airfield.yaml` so a fresh checkout can
restore them with `airfield subpackages checkout`:

```bash
airfield subpackages track
```

## 9. Convert an existing workspace

To bring an existing ROS workspace under Airfield, aim for this layout. Inside
a project, a package named on the command line or in a plan is looked up at
`packages/<name>/`.

```text
my_robot/
  airfield.yaml                  # kind: project
  packages/
    nav_stack/                   # wraps one ROS package
      airfield.yaml              #   source_path: .
      package.xml
    base_driver/                 # bundles several ROS packages
      airfield.yaml              #   source_path: src
      src/
        motor_driver/
        joystick/
        base_driver/             #   metapackage named after the Airfield package
  dependencies/                  # manifests, only for what rosdep can't install
  plans/
```

1. At the root of the workspace, run
   `airfield project init . --ros-distro jazzy`.
2. Decide how to group the ROS packages. Bundle ROS packages whose
   dependencies agree into one Airfield package, and split where they diverge
   (GPU vs CPU builds, conflicting pip pins). Each plan pane still gets its own
   container either way.
3. **One ROS package:** move it to `packages/<name>/`, then run
   `airfield package init --path packages/<name>`.
4. **Several ROS packages:** run `airfield package init <name>`, move them
   into `packages/<name>/src/`, and add a metapackage at
   `packages/<name>/src/<name>/` that depends on all of them. On first start
   Airfield runs `colcon build --packages-up-to <name>`; if no colcon package
   has that name, nothing is built. The metapackage needs two files, a
   `package.xml`:

   ```xml
   <?xml version="1.0"?>
   <package format="3">
     <name>base_driver</name>
     <version>0.0.0</version>
     <description>Builds the base_driver bundle.</description>
     <maintainer email="dev@example.com">dev</maintainer>
     <license>MIT</license>
     <buildtool_depend>ament_cmake</buildtool_depend>
     <depend>motor_driver</depend>
     <depend>joystick</depend>
     <export><build_type>ament_cmake</build_type></export>
   </package>
   ```

   and a `CMakeLists.txt`:

   ```cmake
   cmake_minimum_required(VERSION 3.8)
   project(base_driver)
   find_package(ament_cmake REQUIRED)
   ament_package()
   ```

5. Add `run:` commands to each package (step 6) and write a plan (step 7).
6. Check that everything resolves, builds, and runs:

   ```bash
   cd packages/base_driver && airfield status   # every dependency resolves
   airfield package build base_driver
   airfield package cmd base_driver -- ros2 pkg list
   airfield project up robot --inspect          # render the plan without launching
   ```

Most dependencies need no work: a name in `package.xml` is installed as the
apt package rosdep maps it to. Add to `dependencies:` in `airfield.yaml` only
what the container needs that `package.xml` doesn't say, and use
`skip_dependencies:` for a `package.xml` entry the image must not install. For
a name rosdep doesn't know, a pip-only package, or a custom install, write a
manifest whose filename matches its `name`:

```yaml
# dependencies/xplatform/my_dep.yaml  (x86_64/ or arm64/ when it differs by machine)
name: my_dep
version: 1.0.0
apt: [libfoo-dev, ros-$ROS_DISTRO-nav2-util]   # package names, not commands
pip: [tqdm, numpy>=1.24]                       # requirement specs, not commands
system: []   # raw root commands, only for what a name can't express
user: []     # raw user commands, run last
```

Don't put `apt-get install` or `pip install` lines in `system:` or `user:`;
listed names are installed together in one resolve, and raw commands sit
outside it. A `python3-*` name must install the apt package, because pip's
numpy or OpenCV breaks `cv_bridge`. See
[Manage Dependencies](https://airfield.io/docs/manage-dependencies/) for the details.

## 10. Edit, rebuild, run

The package source is mounted live into its container, but `ros2 run` and
`ros2 launch` read the built copy in the workspace's `install/` folder. That
copy is built automatically only the first time, so after **any** source edit
(Python and launch files included) rebuild it:

```bash
airfield package cmd nav_stack -- bash -lc "cd ~/workspace && colcon build --packages-up-to nav_stack"
```

To have Python edits apply without rebuilding, add
`colcon_args: --symlink-install` to the package's `airfield.yaml`. Machine-only
settings, such as extra data mounts, go in a gitignored `.air` file
(`mounts: [/data/bags]`), never in `airfield.yaml`.

## 11. When something fails

- **`Dependency 'x' manifest not found`**: rosdep doesn't know `x`. Add a
  manifest at `dependencies/xplatform/x.yaml`.
- **Executable not found when running**: no colcon package matches the
  Airfield package's name (add the metapackage from step 9), or the code
  hasn't been built.
- **An edit has no effect**: the colcon rebuild in step 10 was skipped.
- **The build fails at `pip check`**: two dependencies pin conflicting
  versions. Fix the manifests; `AIRFIELD_PIP_CHECK=warn` relaxes the check as
  a stopgap.
- **Leftover containers after a crash**: `airfield project down --prune`, or
  `airfield system clean`.
