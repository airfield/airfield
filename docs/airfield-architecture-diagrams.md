# Airfield Architecture — Diagrams (ICRA paper)

ASCII wireframes / mappings to be redrawn in Canva. Two figures:

- **Diagram #1** — command tree + namespace hierarchy + runtime relationships.
- **Diagram #2** — file structure, build-time / launch-time flow, and the `plans/` format.

Audience: robotics researchers (not DevOps). Scope favors the build/run/launch
lifecycle; peripheral commands (`subpackages`, `system`, `docker cache`,
`status`, `doctor`) are summarized to one line each. Package/project names below
are a **generic example** (`my_robot/`), not any specific deployment.

> Two factual notes carried into the figures:
> 1. `project up` **generates the tmuxinator config AND launches it** by default
>    (`--no-launch` is generate-only). `liftoff` is the simpler, no-tmux path
>    (runs each package's `default` command in sequence).
> 2. An airfield **package** wraps **≥1 ROS package** (e.g. a `base_driver`
>    package may ship `motor_driver`, `joystick`, `gui`, `lidar_launch` as
>    separate `ros2 run/launch` targets). `package init --path` wraps an existing
>    ROS package by reading its `package.xml`.

---

## Diagram #1 — Command tree & namespace hierarchy

```
═══════════════════════════════════════════════════════════════════════════════
 THE airfield CLI COMMAND TREE
 "the command's namespace tells you which level of the stack it acts on"
═══════════════════════════════════════════════════════════════════════════════

 NAMESPACE            SCOPE  ("the space it covers")        KEY FILE
 ─────────            ──────────────────────────────       ────────
 airfield project     the WORKSPACE  (project root)        airfield.yaml   (kind: project)
 airfield package     ONE package     (packages/<name>/)    airfield.yaml   (kind: package)
 ─ ros2 run/launch    ROS nodes INSIDE one airfield pkg     package.xml / launch/*.py
                      (an airfield pkg wraps ≥1 ROS package)


                                 airfield
                                    │
          ┌─────────────────────────┼───────────────────────────┐
          │                         │                           │
   ╔═════════════════════╗   ╔═════════════════════╗   ╔════════════════════════╗
   ║  project            ║   ║  package            ║   ║  peripheral (summarized)║
   ║  ▸ the workspace    ║   ║  ▸ one package      ║   ║  subpackages  → git ops ║
   ╚═════════════════════╝   ╚═════════════════════╝   ║    across src/ &        ║
          │                         │                  ║    packages/ repos      ║
          │                         │                  ║    (status/commit/push/ ║
          │                         │                  ║     pull/…/checkout)    ║
          │                         │                  ║  system / tools system  ║
          │                         │                  ║    → host maintenance   ║
          │                         │                  ║    (clean/setup/update/ ║
          │                         │                  ║     alias/install-comp) ║
          │                         │                  ║  docker cache           ║
          │                         │                  ║    → build-cache optim. ║
          │                         │                  ║  status / doctor (leaf) ║
          │                         │                  ║    → context & dep check║
          │                         │                  ╚════════════════════════╝
          │                         │
   ┌──────┴──────────────┐   ┌──────┴────────────────────────────────┐
   │ init     deinit     │   │ init      deinit                       │
   │  scaffold a project │   │  new pkg, OR wrap an existing ROS pkg  │
   │  (airfield.yaml,    │   │  (--path reads its package.xml → deps) │
   │   kind: project)    │   │                                        │
   │                     │   │ build <pkg>                            │
   │ run <pkg>           │   │  build the container IMAGE             │
   │  run ONE package's  │   │  (airfield-pkg-<name>:latest)          │
   │  'default' command  │   │                                        │
   │  (or shell)         │   │ shell <pkg>                            │
   │                     │   │  interactive shell in the container    │
   │ liftoff <plan>      │   │                                        │
   │  launch a plan by   │   │ cmd <pkg> -- <cmd>                     │
   │  running each pkg's │   │  run an ARBITRARY command in the       │
   │  default cmd in     │   │  container (generic runner; used by    │
   │  SEQUENCE (no tmux) │   │  every plan pane)                      │
   │                     │   │                                        │
   │ up <plan>           │   │ run <pkg> [cmd]                        │
   │  render tmux config │   │  run a NAMED command from the pkg's    │
   │  from the plan &    │   │  run: map (lists them if none given)   │
   │  LAUNCH the tmux    │   │                                        │
   │  session            │   │ dependencies                           │
   │  (--no-launch =     │   │  check    upstream                     │
   │   generate only)    │   │  (manifest name-clashes vs shared       │
   │                     │   │   packages repo)                       │
   │ down [plan]         │   │                                        │
   │  kill the tmux      │   │   ┌──────────────────────────────────┐ │
   │  session; panes'    │   │   │  ROS level  (nested inside an    │ │
   │  containers stop on │   │   │  airfield package)              │ │
   │  SIGHUP             │   │   │  base_driver ▸ motor_driver,     │ │
   │  (--prune = sweep   │   │   │    joystick, gui, lidar_launch   │ │
   │   crash orphans)    │   │   │  camera_driver ▸ image_processor,│ │
   │                     │   │   │    stereo_processor              │ │
   │                     │   │   │  run: maps name → ros2 run/launch │ │
   │                     │   │   └──────────────────────────────────┘ │
   └─────────────────────┘   └────────────────────────────────────────┘
```

### How the commands relate at runtime (the "wire" view)

```
   airfield package build ─────► image  airfield-pkg-<name>
                                         │
                                         │ reused by every run/cmd/shell & by every plan pane
                                         ▼
   ┌─ airfield project up <plan> ──────────────────────────────────────────┐
   │   plans/<plan>.yaml ──render──► .airfield/<plan>.tmuxinator.yml        │
   │   ──tmuxinator start──► tmux session:  one PANE per plan entry         │
   │        each pane  =  airfield package cmd <pkg> -- bash -lc "<cmd>"    │
   │                        └─► docker run <image>  +  /opt/airfield-entry  │
   └────────────────────────────────────────────────────────────────────────┘

   airfield project liftoff <plan> ──►  for pkg in plan.packages:
                                            airfield project run <pkg>
                                              └─► runs  pkg.run["default"]

   airfield project down ──► tmux kill-session ──SIGHUP──► each pane's airfield
        process stops its OWN container  (no orphans;  --prune sweeps crash leftovers)
```

**Mapping for Canva:** three vertical "lanes" = the three namespace levels
(project / package / ros-package-nested). Core lifecycle commands live in the
project & package lanes; the summarized peripheral group sits in a third lane.
The runtime-wire block is a separate sub-figure showing the
build → image → (up → panes → package cmd → entry) chain and the
liftoff → project run → default chain.

---

## Diagram #2 — File structure, build time & plans

```
═══════════════════════════════════════════════════════════════════════════════
 FILE STRUCTURE   (example project: my_robot/)
═══════════════════════════════════════════════════════════════════════════════

my_robot/                             ◄── PROJECT  (airfield.yaml, kind: project)
├── airfield.yaml                     ros_distro · default_target_device ·
│                                     base_image (single source of truth) ·
│                                     subprojects: {name: {url, version}}   (git repos)
│
├── packages/                         ◄── one buildable ROS pkg → one IMAGE
│   │
│   │  ┌─────────────────────────────────────────────────────────────────┐
│   │  │  PACKAGE-LEVEL airfield.yaml   (kind: package)                  │
│   │  │   dependencies: [rclcpp, cv_bridge, my_msgs, urg_node, …]       │
│   │  │   ros_distro · base_image (override) · colcon_args              │
│   │  │   devices · group_add ·  run: {name → ros2 run/launch cmd}      │
│   │  └─────────────────────────────────────────────────────────────────┘
│   │
│   ├── base_driver/            ◄── hardware/drivers (motor, joystick, IMU,
│   │   ├── airfield.yaml            lidar launch, gui); devices + colcon_args
│   │   ├── src/  ▸ motor_driver,    ── one airfield pkg, MANY ROS nodes
│   │   │        joystick, gui, …
│   │   ├── launch/  config/
│   │   └── .air   (extra mounts, e.g. /tmp/.X11-unix for GUI panes)
│   ├── camera_driver/          ◄── CSI camera; has a `run:` map
│   │   └── airfield.yaml + src/      ▸ image_processor, stereo_processor
│   ├── nav_stack/              ◄── navigation (nav2-style)
│   ├── vnc/                    ┐
│   ├── foxglove_bridge/        ├── infra / visualization / bridge
│   ├── rviz2/                  ┘    (vnc, foxglove, rviz2 = config-only,
│   └── … + N more                   no colcon source of their own)
│        teleop, imu_driver, perception, simulator, description,
│        my_msgs, my_maps
│
├── dependencies/                     ◄── per-dependency .yaml manifests
│   ├── xplatform/        (cross-arch: rclcpp, urg_node, image_transport, …)
│   └── arm64/            (target-specific;  optionally a local base image)
│        └── <base>/ {Dockerfile, build.sh}   (local-only base image, if used)
│
├── plans/                            ◄── a launch target: windows → panes → tmux
│   ├── navstack.yaml       (main stack: camera, nav, lidar, motor, joystick,
│   │                        description, foxglove, gui, vnc, rviz2)
│   ├── teleop.yaml         (drive-by-joystick)
│   └── … (record_map, sim_teleop, calibration, …)
│
└── scripts/   build (build-once, serial, OOM-safe) · up (crash-recover+launch)
               · down (clean teardown)   [optional host-side convenience wrappers]


~/workspace/{build,install,log}       ◄── SHARED colcon workspace, mounted
                                      same-path into EVERY container (via .air).
                                      Built ONCE → panes only source + launch.
                                      This is "build once, launch many."
```

### Build time  ( `airfield package build <pkg>` — or implicit on first run )

```
   package airfield.yaml ─┐
   dependencies/*.yaml ───┴──► resolve deps  (search order: local <device> →
                                  local xplatform → global <device> → global xplatform;
                                  peer pkgs with no manifest, e.g. my_msgs,
                                  are mounted as source & built by colcon)
                                         │
                                         ▼
                        generated Dockerfile ──► docker build ──► IMAGE
                        FROM <base_image>                       airfield-pkg-<name>
                        (pkg base_image → else project base_image → else ROS default)
                        + apt + colcon, install the airfield CLI,
                          run dep `system:` (apt) then `user:` (pip) commands,
                          matching host user + ~/workspace/src,
                          source ROS + workspace install in shell rc,
                          COPY /opt/airfield-entry.sh
```

### Launch time  ( `airfield project up <plan>` )

```
   plan.yaml ──render──► tmuxinator.yml ──► tmux session  (one pane per plan entry)

        each pane:   airfield package cmd <pkg> -- bash -lc "<cmd>"
            └─► docker run <image>  +  mounts: src, peer-src, shared ~/workspace,
                                            devices, group_add, GPU/Jetson runtime
                └─► /opt/airfield-entry.sh:
                       if <pkg> not yet in ~/workspace/install:
                           flock-serialized  colcon build --packages-up-to <pkg>
                           (one build at a time, capped parallelism → OOM-safe)
                       exec bash -lc "<cmd>"   (profile auto-sources ROS + install)
                                         │
                                         ▼
                       shared  ~/workspace/install   ──►  "build once, launch many"
```

### Inside a plan  ( `plans/navstack.yaml` )

```
   name: navstack
   pre_window: export AIRFIELD_NO_PULL=1
   windows:
     - name: main
       layout: main-vertical
       panes:
         - null                              ◄── bare shell
         - package: camera_driver            ◄── which IMAGE to run in
           cmd: ros2 run camera_driver image_processor --ros-args -p display_mode:=none
         - package: base_driver              ◄── one airfield pkg → many panes
           cmd: ros2 run base_driver motor_driver
         - package: base_driver
           cmd: ros2 run base_driver joystick
         - package: nav_stack
           cmd: ros2 launch nav_stack navigation.launch.py map:=${MAP:-default}
         … (lidar, description, foxglove, gui, vnc, rviz2)
```

**Mapping for Canva:** left column = the directory tree (project `airfield.yaml`
called out separately from each package `airfield.yaml`; the nested
"▸ ROS nodes" callout under `base_driver` makes the airfield-pkg→ROS-pkg
relationship explicit). Right/lower block = the build→image→launch pipeline as a
left-to-right flow, with the shared `~/workspace/install` as the convergence
point that every pane sources. A small inset shows the plan-yaml
`windows → panes → {package, cmd}` shape.

---

## Notes (design rationale, not part of the figures)

- **`base_image` inheritance** is intentional: one `base_image:` line in the
  project `airfield.yaml` pins the base for every package unless a package
  overrides it. This is how a whole project targets a specific board/ROS image
  from a single source of truth.
- **`AIRFIELD_NO_PULL=1`** makes `docker build` use a local-only base image
  (e.g. a custom board image not in any registry) instead of trying to pull it.
  It can be exported in a plan's `pre_window` or a wrapper script.
- **Why the shared workspace + serialized build exist:** launching a plan spins
  up one container per pane, and if each pane built its own `colcon` package
  concurrently the host can run out of memory. The entry wrapper's
  `flock`-serialized, parallelism-capped build into a single shared
  `~/workspace/install` restores the "build once, launch many" model — every
  pane sources the same prebuilt install and just launches.
- **An airfield package ≠ a ROS package.** One airfield package can wrap several
  ROS packages and appear in multiple plan panes (each running a different
  `ros2 run`/`ros2 launch` target). The `run:` map in `airfield.yaml` names them.
