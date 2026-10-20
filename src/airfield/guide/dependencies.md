<!-- Copied from https://airfield.io/docs/manage-dependencies/ by scripts/copy_guide.py. Don't edit it
     here: edit src/pages/docs/manage-dependencies.mdx in the documentation repository,
     then run the script again. -->

# Managing Dependencies

A ROS package's dependencies come from its `package.xml`. Airfield reads
every `package.xml` under the package's `source_path` each time it builds or
runs the package, so that file is the only list to maintain. Add
`<depend>nav2_msgs</depend>` and the next build installs it. Nothing has to be
repeated in `airfield.yaml`.

`dependencies:` in `airfield.yaml` is for what `package.xml` does not say: a
tool that a plan runs in this container, a driver chosen at launch time, a
library an upstream `package.xml` leaves out. A package with no `package.xml`,
such as a containerized tool, lists everything there.

## How a name is resolved

Each name, from either file, resolves to the first of these:

1. A ROS package in the package's own source tree. Nothing is installed.
2. A manifest named `<name>.yaml` in `dependencies/<target-device>/` (or
   `dependencies/xplatform/` for architecture-independent recipes), found
   locally or in the shared Airfield packages repository. The manifest is the
   recipe for installing the dependency.
3. A peer package under the project's `packages/` folder, matched by its
   folder name or by a ROS package inside it. Its source is mounted and built
   from source alongside the dependent package. Peer packages are covered in
   [Create a Package](https://airfield.io/docs/create-package/).
4. rosdep's lookup table: what the name is called on the image's Ubuntu
   release. `nav2_msgs` becomes `ros-jazzy-nav2-msgs`, `eigen` becomes
   `libeigen3-dev`, `python3-numpy` stays `python3-numpy`.
5. For a name the table does not have, the apt package the name
   conventionally maps to: `ros-<distro>-<name>` for a ROS package name, or
   the name itself for a Debian-style name such as `python3-serial`.

So a released ROS package needs no manifest, whichever file names it, and
neither does a system library rosdep knows. You write a manifest for what
rosdep cannot say:

- The dependency does not come from a package manager: a source build pinned
  to a commit, a vendor `.deb`. Or it is a pip package rosdep has no key for.
- It installs differently per machine: a GPU and a CPU variant, or separate
  x86_64 and arm64 downloads.
- rosdep does not know the name (`opencv2` standing for `libopencv-dev`), or
  you deliberately want something other than rosdep's choice.

A manifest named after the dependency is picked up with no other change,
because step 2 comes before steps 4 and 5.

### The lookup table

Step 4 uses rosdep's own data, from
[ros/rosdistro](https://github.com/ros/rosdistro): its rule files and the
list of packages released for the ROS distribution. rosdep itself is never
installed or run, in the image or on the host. Airfield reads the files and
keeps the result in `~/.cache/airfield/rosdep/`.

- It is fetched the first time a name needs it (about 1 MB, once). After
  that, translating a name needs no network.
- It never refreshes by itself, so an image does not change from one day to
  the next. `airfield package dependencies pull` brings it up to date.
- If it cannot be fetched, the build goes on with step 5 alone and says so.
- Every build lists the names it installed without a manifest. Names that
  fell through to step 5 are printed on their own line ("installed from apt
  by name"); a misspelled name shows up there. If apt has no such package the
  build stops and says which file listed the name.

A few more rules:

- Steps 4 and 5 describe an image built for a ROS distribution, so they apply
  only to packages that set `ros_distro`. In a package without it every name
  needs a manifest, and a miss is an error. So is a name that is in no table
  and fits neither naming pattern.
- A manifest for a `python3-*` name, or for a library ROS's own binaries are
  built against, should install the apt package and not a pip one. A pip
  OpenCV or numpy lands in front of Ubuntu's, and `cv_bridge` (compiled
  against Ubuntu's) then crashes.
- `skip_dependencies:` in `airfield.yaml` lists `package.xml` entries to leave
  out of the image, like rosdep's `--skip-keys`. Use it for an upstream
  `package.xml` that names something the image does not need.
- An entry that cannot be a package name (`OpenCV 3.4.12`) is ignored with a
  warning.
- Build and run tags are read (`depend`, `build_depend`, `exec_depend`, and the
  export and buildtool variants), and `condition="..."` attributes are
  honored. `test_depend` and `doc_depend` are not read.
- A package without `ros_distro` has no ROS in its image, so its `package.xml`
  is not read.

To see what a package's image will contain, run `airfield status` inside the
package. It lists every dependency, the file that asked for it, and how it
resolved. It also names the `airfield.yaml` entries that only repeat
`package.xml` and can be deleted.

The rest of this page is about manifests. Use local dependency manifests while
developing a package, then contribute them to the shared repository when
they're ready for other packages to reuse.

## Why a shared repository?

The shared Airfield repository
([github.com/airfield/packages](https://github.com/airfield/packages)) does
**not** hold ROS source code. Your packages stay in their own repositories.
What it holds is the dependency *manifests*: the small recipes that describe
how to install each dependency inside a container. Think of it as a package
manager's index, closer to a Homebrew tap or an apt sources list than to a
source monorepo. It is a shared cookbook of "how to install dependency X in a
ROS container."

Centralizing the recipes instead of the code has practical benefits:

- The first person to work out the exact apt/pip commands and version pins
  for a tricky driver writes its manifest once. Every other package lists that
  dependency by name and inherits the working recipe, so nobody re-derives
  install commands across projects.
- When a dependency's install changes (a new apt repo, a renamed package),
  you fix the manifest in one place, and everyone picks up the change on
  their next `dependencies pull` instead of chasing down stale copies.
- A dependency can carry separate `x86_64` and `arm64` manifests, or a single
  shared `xplatform` recipe, so the "installs differently on the Jetson than
  on a laptop" case lives in the shared repo rather than in every consumer.
- You keep working locally without waiting on anyone: hold a manifest locally
  while developing and promote it upstream only when it is ready, using the
  workflow below.

## 1. Add a local manifest

For a project package, place manifests at the project root:

```text
my_robot/
  dependencies/
    x86_64/
      my_driver.yaml
```

For a standalone package, place manifests at the package root:

```text
nav_stack/
  dependencies/
    x86_64/
      my_driver.yaml
```

Example manifest:

```yaml
name: my_driver
version: 1.0.0
ros_versions:
  - jazzy
apt:
  - libmy-driver-dev
```

Apt entries may use shell variables, which is how one manifest serves several
distros:

```yaml
apt:
  - ros-$ROS_DISTRO-nav2-bringup
```

Then name it where the package declares its dependencies. If the package's
code needs it, that is `package.xml`:

```xml
<depend>my_driver</depend>
```

If only the container needs it, or the package has no `package.xml`, that is
`airfield.yaml`:

```yaml
dependencies:
  - my_driver
```

A pip-based manifest declares requirements under `pip:` — package names, not
commands:

```yaml
name: tqdm
version: 1.0.0
pip:
  - tqdm
```

Names rather than commands matters more than it looks: see
[Why `pip:` is a list of names](#why-pip-is-a-list-of-names-not-commands)
below for what that buys you and when you still need a raw command.

## 2. Check for shared-repository conflicts

Run the check from the package or project directory:

```bash
airfield package dependencies check . --target-device x86_64
```

Or pass the directory explicitly:

```bash
airfield package dependencies check ./packages/nav_stack --target-device x86_64
```

Airfield prints the local dependency root, the shared repository root, and any
manifest names that already exist upstream. If a conflict is reported, rename
the local manifest or use the existing shared dependency.

## 3. Copy manifests into the shared package repository

```bash
airfield package dependencies upstream . --target-device x86_64
```

Airfield asks before copying local manifest files into the shared packages
repository. After copying, create a feature branch, commit, push, and open a
pull request in `https://github.com/airfield/packages`.

## 4. Repeat for each target device

Dependency manifests are target-specific. If the dependency needs different
install commands per architecture, check and upstream each target:

```bash
airfield package dependencies check . --target-device x86_64
airfield package dependencies upstream . --target-device x86_64

airfield package dependencies check . --target-device arm64
airfield package dependencies upstream . --target-device arm64
```

If the same install commands work on both architectures, place a single
manifest in `dependencies/xplatform/` instead; it resolves for every target
device.

## 5. Pull the latest shared manifests

To update your machine's copy of the shared packages repository:

```bash
airfield package dependencies pull
```

## Why `pip:` is a list of names, not commands

Every `pip:` entry across all of a package's dependencies is collected into a
**single** `pip install`. That is the whole point of the field.

Run separately, pip solves each install in isolation and has no idea the others
are coming. A later install will happily uninstall a version an earlier one
needs, print `ERROR:` about it, and **still exit 0** — so the image builds
green and fails at runtime instead:

```text
pip install opencv-python-headless    # pulls numpy 2.x
pip install "some-package"            # needs numpy <2, rips out 2.x
  ERROR: opencv ... requires numpy>=2, but you have 1.26.4
  Successfully installed ...          # exit 0. Build passes.
```

Handed to pip together, the resolver searches for a set that satisfies
everyone. Usually it finds one and you never hear about it; when no such set
exists it fails the build with the actual conflict named. You do not pin
versions to get this — declaring the plain name is what makes it work.

Airfield does not need a version from you, but it accepts one when you have a
real constraint:

```yaml
pip:
  - numpy>=1.24
  - flask
```

## `apt:` is batched for the same reason

Apt does not have pip's version problem: an Ubuntu release ships exactly one
version of each package, curated so everything in the archive installs
together. There is no version to pick, so there is no wrong version to pick.
Apt also never leaves broken dependencies — it either finds a workable plan or
refuses.

What it will do, given `-y`, is resolve a genuine conflict by **removing** the
other package and reporting success:

```text
manifest A:  apt-get install -y exim4-daemon-light    -> installed
manifest B:  apt-get install -y postfix
             The following packages will be REMOVED:
               exim4-base exim4-config exim4-daemon-light
             exit 0
```

Manifest A's package is gone and the build passes. No checker catches it,
because nothing is *broken* — the remaining packages all have what they need.
Something is merely missing. Batched into one `apt-get install`, the same pair
fails loudly with conflicting requests instead.

That case is rare, since the archive is curated to be co-installable. The
everyday win is speed: one index refresh for the whole package instead of one
per dependency.

## When you still need a raw command

`pip:` and `apt:` only cover what the package manager can express as a name.
Anything else — a custom index, an install that branches on GPU vs CPU, a
downloaded `.deb`, adding a third-party apt repository — stays a command under
`user:` or `system:`. Put a pip command in `user:`: those run *after* the
batched install, so a deliberate choice gets the last word over the generic
resolve. (`system:` commands run earlier, as root, after the batched
`apt-get install` but before the batched `pip install`. A manifest that adds its
own apt repository therefore needs the repo setup *and* its install together in
`system:`, since the batch has already run by then.) On Ubuntu
24.04 base images (ROS `jazzy` and newer) pip refuses to install into the system
Python unless told otherwise, so hand-written pip commands need
`--break-system-packages`:

```yaml
user:
  - python3 -m pip install --break-system-packages torch --index-url https://download.pytorch.org/whl/cu121
```

The tradeoff is that these installs sit outside the shared resolve, which is
what the build-time check below is for.

## The build-time consistency check

Batching protects what went through the batch. Three things sit outside it and
can still collide in the one Python environment the container has: packages
installed by `apt` manifests, manifests running their own pip command, and
whatever the base image shipped.

So every build runs `pip check` twice — once before any dependency installs to
record what the base image already had broken, once at the end. Only newly
broken packages fail the build. A conflict that arrived with a vendor base
image is not yours to fix, so Airfield does not blame you for it.

```text
[airfield] Dependency conflict introduced by this package's installs:
    requests 2.25.1 has requirement urllib3<1.27, but you have urllib3 2.7.0.
```

Control it with `AIRFIELD_PIP_CHECK`:

| Value | Behavior |
| --- | --- |
| unset (default) | Fail the build on a newly introduced conflict |
| `warn` | Print the conflict, build anyway |
| `off` | Skip the check entirely |

## Manifest fields

- `name` must match the YAML filename without `.yaml`.
- `version` versions the dependency manifest.
- `ros_versions` is optional and limits the manifest to specific ROS distributions.
- `apt` lists apt packages by name (not commands). Entries from every dependency
  are batched into one `apt-get install`, and may use shell variables such as
  `ros-$ROS_DISTRO-nav2-util`.
- `pip` lists Python requirements by name (not commands). Entries from every
  dependency are batched into one `pip install` so the resolver solves them
  together; prefer this over a hand-written pip command.
- `system` commands run as root during image build, after the batched
  `apt-get install` and before the batched `pip install`.
- `user` commands run as the package user, after both.
- `host_dependencies` can declare host-side requirements such as GPU drivers.

## Shared package definitions

Besides dependency manifests, the shared repository can hold whole *package
definitions*: a manifest that declares `kind: package` and describes a
complete Airfield package, optionally with a `source: {url, ref}` pointing at
a git repository. These are for tools a project runs as-is but does not
develop, such as a visualization bridge or a fiducial detector.

When a command or plan pane names a package that does not exist under
`packages/` but has a shared definition, Airfield materializes it: it clones
the declared source (or scaffolds an empty source folder for config-only
tools), writes the definition as the package's `airfield.yaml`, and adds the
directory to the project's `.gitignore`. From then on it behaves like any
local package.

The materialized directory is disposable by design. It is reproducible from
the shared definition, so a fresh checkout of the project regains it on first
use, and deleting the directory forces a re-materialization with the latest
definition. Packages a team actively develops belong in the project itself,
not in the shared repository.
