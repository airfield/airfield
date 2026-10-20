<!-- Copied from https://airfield.io/docs/runtime/ by scripts/copy_guide.py. Don't edit it
     here: edit src/pages/docs/runtime.mdx in the documentation repository,
     then run the script again. -->

# Runtime & Environment

This page describes what happens when an Airfield container runs: which
container engine is used, how the container is wired to the host, what gets
mounted where, and the environment variables that change any of it. The
[CLI Reference](https://airfield.io/docs/reference/) covers commands and file schemas; this page
covers behavior.

## Container engines

Airfield targets Docker and works with engines that present a Docker-compatible
CLI:

- **Docker** is the default on Linux.
- **Podman, Apptainer, and Singularity** are detected when the `docker` command
  on `PATH` resolves to one of them. Podman and Buildah do not support BuildKit
  cache mounts, so builds on those engines use a compatibility-mode Dockerfile
  automatically.
- **Apple Silicon Macs** use Apple's `container` tool instead of Docker.
  `airfield system setup` installs and starts it.

If a build fails with `invalid mount type "cache"`, Airfield regenerates the
Dockerfile without cache mounts and retries on its own.

## Networking and IPC

On Docker, every `run`, `cmd`, and `shell` container starts with
`--network=host` and `--ipc=host`. This is deliberate: ROS 2 nodes in different
containers, and nodes running directly on the host, discover each other over
DDS exactly as if they were plain processes on one machine. You do not need to
configure discovery servers, port mappings, or a shared Docker network, and
tools on the host (`ros2 topic echo`, `rviz2`) see topics published from any
container.

The trade-off is standard for host networking: containers share the host's
network namespace, so two containers cannot bind the same port.

## Who the container runs as

An image holds no account for the person using it. Every command starts, as
root, in a small script at `/opt/airfield-init.sh`, which creates the caller's
account inside the container (same login name, user and group ids, and home
path as on the host) and then runs the command as that user. So:

- files written to the mounted source and workspace belong to you on the
  host, on any machine and under any login;
- `$HOME` in a plan command means the same path inside the container as on
  the host;
- groups added with `group_add` (for serial ports, I2C, joysticks) still
  apply to the command;
- `docker exec` into a running container lands you as root. Use
  `docker exec -u $(id -u):$(id -g) ...` to be yourself.

Nothing in the home directory that is mounted from the host is touched.

## Images built once, used by many machines

Because an image holds nothing about who built it or where, it can be named
after its recipe: `airfield-pkg-<name>:<fingerprint>`. The fingerprint is taken
from everything that decides the image's contents (the base image's name,
every dependency, the architecture, GPU or CPU where a dependency cares, and
Airfield's own code). Machines of the same kind arrive at the same name for
the same package; a machine that differs arrives at another and builds its
own.

To let machines share, name a repository in the project's `airfield.yaml`:

```yaml
image_registry: ghcr.io/my-org/my-robot
```

Each time a package's image is needed, Airfield then:

1. uses the image for this recipe if the machine already has it, without
   running `docker build` at all;
2. else pulls it, if another machine has pushed it;
3. else builds it here.

```bash
airfield package build nav_stack --push      # build if needed, then upload
airfield package build nav_stack --rebuild   # build from scratch, whatever exists
```

- Every package goes to that one repository, as the tag
  `<name>-<fingerprint>`, so the base image's layers are uploaded once.
- Log in with `docker login` on each machine. Use a private repository if the
  base image may not be redistributed.
- The fingerprint covers the base image's name, not its contents. A shared
  image stays in use on a machine whose own copy of the base image differs,
  and Airfield says so. To move a fleet to a new base image, give the base a
  new tag, or run `--rebuild --push` on one machine.
- Without `image_registry`, step 1 still applies to a package that does not
  refresh its base image (`pull_base_image: false`): its image is rebuilt only
  when the recipe or the local base image changes. A package that refreshes
  its base image builds on every command, as before.
- An update to Airfield gives every image a new tag, because Airfield's own
  code is part of the image. That code is the last thing put into an image,
  so the rebuild redoes that one step and keeps every dependency install: it
  takes seconds. This relies on the earlier build's layers being on the
  machine. A machine that pulled its image and never built it has none, so
  let a machine that did build `--push` the new image first.
- Reuse and sharing are implemented for Docker. With Apple's `container`
  engine every command builds, as before.

## What gets mounted

Each container receives:

- **The package source**, mounted at
  `/home/<user>/workspace/src/<package-name>`. Edits on the host appear
  in the container immediately; nothing is copied into the image.
- **The shared colcon workspace** — `build/`, `install/`, and `log/` from
  `<project>/.airfield/workspace` on the host, mounted at `~/workspace` in the
  container. Containers run with `--rm`, so without this the workspace would be
  discarded every time a pane exits. Sharing it is what makes *build once,
  launch many* work, and it is also what gives the build lock below a file that
  every container can see. The workspace is per project, so two projects that
  each contain a package of the same name never share one `install/`. This is
  automatic and needs no configuration; see
  [Shared colcon workspace](https://airfield.io/docs/reference/#shared-colcon-workspace) for the
  host path, the fallback for standalone packages, and how to relocate or
  disable it.
- **Peer package sources.** A dependency with no manifest that exists as a
  package under `packages/` is treated as a source dependency: its source is
  mounted alongside yours so `colcon build --packages-up-to` can compile it
  first. Message packages shared between components typically resolve this way.
- **`.air` mounts.** Extra host paths from the project's `.air` file, then the
  package's, each mounted at the same absolute path inside the container.
  Missing paths are skipped with a warning.
- **Devices and groups** declared in `airfield.yaml` (`devices`, `group_add`).
  A declared device that is absent on the host is skipped with a warning
  rather than failing the run, so the same config works on machines with and
  without the hardware attached.

The working directory defaults to the source mount; `default_workdir` in
`airfield.yaml` overrides it.

Containers created by one image are independent. Two panes running the same
package get two containers from the same image, with the same mounts.

## The first-run build

Images built for a ROS package carry a small entry script at
`/opt/airfield-entry.sh`. Before running your command, it checks whether the
package has been built into the workspace `install/` directory:

- If yes (or the package has no colcon source, as with tool-only packages), it
  goes straight to your command.
- If not, it runs `colcon build --packages-up-to <name>` first, with any
  `colcon_args` from `airfield.yaml` appended.

Because `install/` lives on the host, that check sees work done by earlier
containers in the same project: the first pane to need a package compiles it,
and every later pane finds it built and goes straight to launching.

A build that failed or was cut short does not count as built, even though
colcon leaves the package's folder behind in `install/`. The next run tries
the build again, so a package that never compiled is never launched as if it
had. A container that starts while another is still compiling the same package
waits for that build and then goes straight to its command. Packages you built
yourself, with colcon in a shell or in an earlier version of Airfield, count as
built.

The automatic build uses colcon. A ROS 1 package gets its image and its
containers like any other, but its catkin workspace is built by hand, in
`airfield package shell`.

Two protections make this safe on small boards:

- Concurrent containers serialize on a file lock in the shared `log/`
  directory, so only one colcon build runs at a time even when a plan starts
  many panes at once. This works because every container locks the *same* file;
  point `AIRFIELD_WORKSPACE` at `none` and each container gets a private copy
  instead, so no pane ever waits for another and a plan's panes all compile
  simultaneously.
- Compile parallelism defaults to one job per core, capped at roughly one job
  per 4 GB of RAM. A Jetson gets fewer parallel jobs than a workstation, which
  prevents out-of-memory failures during first launch. Set `MAKEFLAGS` or
  `CMAKE_BUILD_PARALLEL_LEVEL` to override the defaults.

Packages without a `ros_distro` have no entry script; their commands run in a
plain login shell.

## GPU access

GPU passthrough depends on the platform:

- **NVIDIA Jetson boards** (detected via `/etc/nv_tegra_release`) always get
  the NVIDIA runtime, full driver capabilities, the Argus camera socket, and
  the `/dev/video*` nodes. CSI cameras, EGL, and CUDA work inside containers
  without configuration, on every checkout of the project.
- **Other hosts** opt in by setting `AIRFIELD_TORCH_INSTALL_TARGET=gpu` (or
  `TORCH_INSTALL_TARGET=gpu`). On Docker this adds `--gpus all`; on Podman it
  wires up the OCI hooks instead. When Airfield detects an NVIDIA GPU on the
  host, it selects GPU mode by itself and suggests a matching CUDA wheel tag
  for PyTorch installs.

Dependency manifests can declare `host_dependencies` (an NVIDIA driver with a
minimum version, for example). Airfield evaluates them before the image build:
interactive sessions are prompted when a required host dependency is missing,
and non-interactive runs (CI) fall back to CPU installs rather than failing.

## Teardown

Airfield names each container it starts (`airfield-run-<pid>-<id>`) and runs
it with `--rm`. When the airfield process receives Ctrl-C, SIGTERM, or SIGHUP
(a closed terminal, or `tmux kill-session`), it stops its own container
explicitly instead of trusting the in-container process tree to forward
signals. This is why `airfield project down` leaves no orphaned containers:
killing the tmux session signals each pane, and each pane stops its container
on the way out.

Processes started under `nohup` keep their SIGHUP immunity; Airfield does not
override it.

After a hard crash (SIGKILL, power loss) where no handler could run, sweep the
leftovers:

```bash
airfield project down --prune
```

## Environment variables

| Variable | Effect |
| --- | --- |
| `AIRFIELD_NO_PULL` | Skip `docker build --pull`, so a package can build FROM a locally built base image that exists in no registry (a custom L4T base, for example). Often exported in a plan's `pre_window` or a wrapper script. |
| `AIRFIELD_IMAGE_REGISTRY` | Repository that images are shared through, overriding the project's `image_registry` on this machine. `none` switches sharing off. |
| `AIRFIELD_ROSDISTRO_URL` | Where rosdep's lookup table is fetched from. Point it at a mirror or a local copy (`file:///...`) with the same layout as `github.com/ros/rosdistro`. |
| `AIRFIELD_ROSDEP_TABLE` | `off` translates names by the naming convention alone, without rosdep's table. |
| `AIRFIELD_PACKAGES_REPO` | URL of the shared packages repository. Lets forks, mirrors, and air-gapped sites point at their own manifests. Default: `https://github.com/airfield/packages.git`. |
| `AIRFIELD_REPO` | GitHub `owner/name` slug used by `system update` and the update check. Set it when running a fork. |
| `AIRFIELD_WORKSPACE` | Host location of the shared colcon workspace, overriding the default `<project>/.airfield/workspace`. Point several projects at one path to share a build tree; set it to `none` to opt out entirely, giving every container a throwaway workspace and removing the lock that serializes concurrent builds. |
| `AIRFIELD_TORCH_INSTALL_TARGET` | `gpu` or `cpu`. Selects GPU passthrough at run time and the wheel flavor for manifests that install PyTorch. `TORCH_INSTALL_TARGET` is accepted as a fallback name. |
| `AIRFIELD_TORCH_VERSION`, `AIRFIELD_TORCH_GPU_WHL_TAG` | Version and CUDA wheel tag passed to the image build for PyTorch manifests. Airfield sets them itself from a `torch==<version>` pin and the detected driver; set them manually to override. |
| `AIRFIELD_DEP_<NAME>_VERSION` | Set automatically for every exact pin (`name==1.2.3`) in `airfield.yaml`, for manifests that read a version at install time. |
| `AIRFIELD_FORCE_DOCKER_CACHE_MOUNTS`, `AIRFIELD_DISABLE_DOCKER_CACHE_MOUNTS` | Force BuildKit cache mounts on or off, overriding engine detection. Useful in CI and when troubleshooting builds. |
| `MAKEFLAGS`, `CMAKE_BUILD_PARALLEL_LEVEL` | Override the first-run build's computed compile parallelism. |
| `IN_AIRFIELD_CONTAINER` | Set to `1` inside every Airfield image. The CLI checks it to refuse container-in-container operations. |

Airfield's own cache and config live under the XDG base directories
(`~/.cache/airfield`, `~/.config/airfield` by default) and honor
`XDG_CACHE_HOME` and `XDG_CONFIG_HOME`.
