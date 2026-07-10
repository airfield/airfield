import os
import posixpath
import pwd
import re
import glob
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import typer
import yaml

from airfield.builder import Builder
from airfield.config import AIRFIELD_CONFIG, AIRFIELD_LOCAL_CONFIG, dependencies_dir, dependency_search_paths, find_project_root, packages_dir, require_package_root, is_arm_mac, is_arm64
from airfield.host_check import detect_host_facts, evaluate_host_dependencies
from airfield.models import Dependency, Package, SUPPORTED_ROS_DISTROS


def run_container_foreground(run_cmd: List[str]) -> int:
    """Run a container in the foreground, tearing it down if we're interrupted.

    `docker run` without a TTY proxies our signal to PID 1 (``bash -lc ...``), but a
    non-interactive bash does not forward it to the workload, so on Ctrl-C, SIGTERM,
    or SIGHUP (tmux kill-server / terminal close)
    the container survives as an orphan that keeps holding host resources (e.g. the
    CSI camera's single Argus capture session, which then makes the next run fail
    with ``Failed to create CaptureSession``). To make interruption reliable
    regardless of how the in-container process tree handles signals, we name the
    container and stop it explicitly: ``docker stop`` SIGTERMs PID 1 and SIGKILLs the
    whole container after a short grace period, and ``--rm`` reaps it.

    Only the ``docker`` engine is handled specially; any other engine (e.g. the
    arm64 macOS ``container`` runtime) falls through to an unchanged blocking run.
    Returns the child's exit code.
    """
    if not run_cmd or run_cmd[0] != "docker":
        return subprocess.run(run_cmd).returncode

    name = f"airfield-run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    # insert `--name <name>` immediately after the `run` subcommand
    named_cmd = [run_cmd[0], run_cmd[1], "--name", name, *run_cmd[2:]]

    def _stop() -> None:
        subprocess.run(
            ["docker", "stop", "-t", "2", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    proc = subprocess.Popen(named_cmd)

    def _on_term(_signum, _frame) -> None:
        # `timeout` sends SIGTERM to us (not the container); stop it ourselves.
        _stop()

    previous_term = signal.signal(signal.SIGTERM, _on_term)
    # `tmux kill-server` / closing the terminal sends SIGHUP. Python's default
    # action terminates us without running handlers or `finally`, so the docker
    # client dies but the container survives as an orphan (still holding e.g. a
    # display socket or camera session). Trap it like SIGTERM — unless SIGHUP is
    # already ignored (`nohup`), which we must not override.
    previous_hup = signal.getsignal(signal.SIGHUP)
    if previous_hup is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _on_term)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Ctrl-C reached the docker client but not the in-container workload.
        _stop()
        return proc.wait()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if previous_hup is not signal.SIG_IGN:
            signal.signal(signal.SIGHUP, previous_hup)


def entry_wrap_args(pkg: Optional[Package], command_text: str) -> Tuple[List[str], List[str]]:
    """Container env args + command vector for `package cmd` / `package run`.

    ROS packages route through /opt/airfield-entry.sh (baked into their image),
    which builds the target colcon package into the shared workspace if it is
    not built yet — a no-op for apt-only tool packages and already-built ones —
    then execs a login shell running the command. Non-ROS packages run the
    login shell directly (their image has no entry script).
    """
    if pkg is not None and pkg.ros_distro:
        env_args = ["-e", f"AIRFIELD_BUILD_PKG={pkg.name}"]
        if pkg.colcon_args:
            env_args.extend(["-e", f"AIRFIELD_COLCON_ARGS={pkg.colcon_args}"])
        return env_args, ["/opt/airfield-entry.sh", command_text]
    return [], ["/bin/bash", "-lc", command_text]


def _resolve_package_ros_distro(pkg: Package, project_root: Optional[Path]) -> Optional[str]:
    del project_root

    if pkg.ros_distro is None:
        return None

    ros_distro = pkg.ros_distro.strip().lower()
    if not ros_distro:
        pkg.ros_distro = None
        return None

    if ros_distro not in SUPPORTED_ROS_DISTROS:
        raise typer.BadParameter(
            f"Unsupported ROS distribution '{ros_distro}'. Supported values: {', '.join(sorted(SUPPORTED_ROS_DISTROS))}"
        )
    pkg.ros_distro = ros_distro
    return ros_distro


def resolve_package_context(
    package_name: Optional[str],
    target_device: str = "x86_64",
) -> Tuple[Path, Package, List[Dependency], Path]:
    root = find_project_root()

    if root is not None:
        if package_name is None:
            pkg_dir = require_package_root()
        else:
            candidate = Path(package_name).expanduser()
            # Only treat a CWD-relative path as the package if it's actually an
            # airfield package (has airfield.yaml). Otherwise a same-named non-
            # package dir at the project root (e.g. `rviz2/` configs or the
            # `simulator/` Unity build) would shadow `packages/<name>`.
            if candidate.exists() and (candidate / AIRFIELD_CONFIG).exists():
                pkg_dir = candidate.resolve()
            else:
                pkg_dir = (packages_dir(root) / package_name).resolve()
        search_paths = dependency_search_paths(root, target_device)
    else:
        if package_name is not None:
            pkg_dir = Path(package_name).expanduser().resolve()
        else:
            pkg_dir = require_package_root()
        search_paths = dependency_search_paths(pkg_dir, target_device)

    pkg_yaml = pkg_dir / AIRFIELD_CONFIG
    if not pkg_yaml.exists():
        raise typer.BadParameter(
            f"Package config not found at {pkg_dir / AIRFIELD_CONFIG}"
        )

    pkg = Package.load(pkg_yaml)
    _resolve_package_ros_distro(pkg, root)
    source_root = (pkg_dir / pkg.source_path).resolve()
    if not source_root.exists():
        raise typer.BadParameter(f"source_path '{pkg.source_path}' does not exist in {pkg_dir}")

    deps: List[Dependency] = []
    seen_deps = set()
    queue = list(pkg.dependencies)

    while queue:
        dep_name = queue.pop(0)
        if dep_name in seen_deps:
            continue
        seen_deps.add(dep_name)

        dep_path = None
        for search_path in search_paths:
            candidate = search_path / f"{dep_name}.yaml"
            if candidate.exists():
                dep_path = candidate
                break
                
        if dep_path is not None:
            deps.append(Dependency.load(dep_path))
        else:
            peer_pkg_dir = None
            if root is not None:
                candidate_peer = packages_dir(root) / dep_name
                if (candidate_peer / AIRFIELD_CONFIG).exists():
                    peer_pkg_dir = candidate_peer

            if peer_pkg_dir is not None:
                peer_pkg = Package.load(peer_pkg_dir / AIRFIELD_CONFIG)
                queue.extend(peer_pkg.dependencies)
            else:
                print(f"Error: Dependency '{dep_name}' manifest not found in search paths:")
                for sp in search_paths:
                    print(f"  - {sp}")
                if root is not None:
                    print(f"  - {packages_dir(root)} (peer packages)")
                print(f"Each dependency listed in airfield.yaml must have a corresponding .yaml manifest.")
                raise typer.Exit(1)

    return pkg_dir, pkg, deps, source_root


def _apply_project_default_base_image(pkg: Package, pkg_dir: Path) -> None:
    """Inherit a project-level default ``base_image`` when the package doesn't set one.

    Lets a whole project pin one base image (e.g. a custom L4T image) in a single
    place — the project's ``airfield.yaml`` ``base_image:`` field — instead of
    repeating it in every package's ``airfield.yaml``. An explicit per-package
    ``base_image`` still wins; a standalone package (no enclosing project) is
    unaffected and falls back to the ROS/ubuntu default as before.
    """
    if pkg.base_image:
        return
    root = find_project_root(pkg_dir)
    if root is None:
        return
    proj_cfg = root / AIRFIELD_CONFIG
    if not proj_cfg.exists():
        return
    try:
        data = yaml.safe_load(proj_cfg.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return
    default_base = data.get("base_image")
    if isinstance(default_base, str) and default_base.strip():
        pkg.base_image = default_base.strip()


def build_package_image(
    pkg_dir: Path,
    pkg: Package,
    deps: List[Dependency],
    target_device: str = "x86_64",
    show_all_output: bool = False,
) -> str:
    _apply_locked_dependency_versions(pkg)
    _apply_project_default_base_image(pkg, pkg_dir)
    _validate_and_configure_host_dependencies(pkg, deps)

    builder = Builder(package=pkg, dependencies=deps, target_device=target_device)
    success, image_name = builder.build(context_dir=pkg_dir, show_all_output=show_all_output)
    if not success:
        raise typer.Exit(1)
    return image_name


def _is_non_interactive() -> bool:
    if os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return not sys.stdin.isatty()


def _normalize_dep_env_name(dep_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", dep_name).upper().strip("_")


def _set_env_default(env_name: str, value: str) -> None:
    if not os.environ.get(env_name):
        os.environ[env_name] = value


def _apply_locked_dependency_versions(pkg: Package) -> None:
    for dep_name, constraint in pkg.dependency_constraints.items():
        dep_key = _normalize_dep_env_name(dep_name)
        exact_version_match = re.match(r"^==\s*([0-9]+(?:\.[0-9]+){0,2})$", constraint.strip())
        if exact_version_match is None:
            continue

        exact_version = exact_version_match.group(1)
        _set_env_default(f"AIRFIELD_DEP_{dep_key}_VERSION", exact_version)

        # Backward-compatible convenience alias for existing torch installer hooks.
        if dep_name.strip().lower() == "torch":
            _set_env_default("AIRFIELD_TORCH_VERSION", exact_version)


def _resolve_torch_install_target() -> str:
    explicit = os.environ.get("AIRFIELD_TORCH_INSTALL_TARGET") or os.environ.get("TORCH_INSTALL_TARGET")
    if explicit:
        target = explicit.strip().lower()
        return "gpu" if target == "gpu" else "cpu"

    facts = detect_host_facts()
    if facts.has_nvidia_gpu:
        os.environ["AIRFIELD_TORCH_INSTALL_TARGET"] = "gpu"
        if facts.suggested_torch_cuda_tag:
            _set_env_default("AIRFIELD_TORCH_GPU_WHL_TAG", facts.suggested_torch_cuda_tag)
        return "gpu"

    os.environ["AIRFIELD_TORCH_INSTALL_TARGET"] = "cpu"
    return "cpu"


def _print_host_issues(issues) -> None:
    print("Host dependency checks found issues:")
    for issue in issues:
        level = "ERROR" if issue.required else "WARN"
        print(f" - [{level}] {issue.dependency_name}:{issue.requirement_name} -> {issue.message}")
        if issue.install_hint:
            print(f"   hint: {issue.install_hint}")


def _validate_and_configure_host_dependencies(pkg: Package, deps: List[Dependency]) -> None:
    install_target = _resolve_torch_install_target()
    facts, issues = evaluate_host_dependencies(deps, install_target=install_target)

    if install_target == "gpu" and facts.suggested_torch_cuda_tag:
        _set_env_default("AIRFIELD_TORCH_GPU_WHL_TAG", facts.suggested_torch_cuda_tag)

    if not issues:
        return

    required_issues = [issue for issue in issues if issue.required]
    if not required_issues:
        _print_host_issues(issues)
        return

    if _is_non_interactive():
        # Safe default in CI/non-interactive mode: use CPU wheels when host GPU deps fail.
        if install_target == "gpu":
            print("Required GPU host dependencies are not satisfied. Falling back to CPU install mode.")
            os.environ["AIRFIELD_TORCH_INSTALL_TARGET"] = "cpu"
            _, cpu_issues = evaluate_host_dependencies(deps, install_target="cpu")
            blocking_cpu_issues = [issue for issue in cpu_issues if issue.required]
            if blocking_cpu_issues:
                _print_host_issues(blocking_cpu_issues)
                raise typer.Exit(1)
            if cpu_issues:
                _print_host_issues(cpu_issues)
            return

        _print_host_issues(required_issues)
        raise typer.Exit(1)

    _print_host_issues(required_issues)
    print("Please install or upgrade missing host dependencies before building.")
    confirmed = typer.confirm("Continue build anyway?", default=False)
    if not confirmed:
        raise typer.Exit(1)


def container_source_mount_path(package_name: str) -> str:
    username = pwd.getpwuid(os.getuid()).pw_name
    return f"/home/{username}/workspace/src/{package_name}"


def container_workdir(pkg: Package) -> str:
    """Resolve the default in-container working directory for a package run/shell."""
    source_mount = container_source_mount_path(pkg.name)
    raw = (pkg.default_workdir or ".").strip()

    if raw in {"", ".", "./"}:
        return source_mount
    if raw.startswith("/"):
        return raw
    return posixpath.normpath(f"{source_mount}/{raw}")


def _read_local_mounts(config_path: Path) -> List[str]:
    if not config_path.exists():
        return []

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise typer.BadParameter(f"Failed to parse local config at {config_path}: {exc}")

    if data is None:
        return []
    if not isinstance(data, dict):
        raise typer.BadParameter(f"Local config at {config_path} must be a YAML mapping")

    mounts = data.get("mounts", [])
    if mounts is None:
        return []
    if not isinstance(mounts, list):
        raise typer.BadParameter(f"'mounts' in {config_path} must be a list")

    cleaned: List[str] = []
    for mount in mounts:
        if not isinstance(mount, str):
            raise typer.BadParameter(f"Each mount in {config_path} must be a string path")
        mount_path = mount.strip()
        if mount_path:
            cleaned.append(mount_path)
    return cleaned


def _configured_mounts(pkg_dir: Path) -> List[str]:
    mounts: List[str] = []
    project_root = find_project_root(pkg_dir)
    if project_root is not None:
        mounts.extend(_read_local_mounts(project_root / AIRFIELD_LOCAL_CONFIG))
    mounts.extend(_read_local_mounts(pkg_dir / AIRFIELD_LOCAL_CONFIG))
    return mounts


def in_airfield_container() -> bool:
    """Check if currently running inside an Airfield-built container."""
    return os.environ.get("IN_AIRFIELD_CONTAINER") == "1"


def _collect_peer_source_mounts(
    pkg: Package, root: Path, search_paths: List[Path]
) -> List[Tuple[str, Path]]:
    """Recursively find peer-package dependencies: custom packages living in
    ``packages/<name>/`` that have no dependency manifest (so they are built
    from source rather than apt-installed). Their source must be mounted into
    the workspace alongside the dependent package so colcon can build them
    together — e.g. ``ut_automata`` does ``find_package(amrl_msgs)`` and needs
    ``amrl_msgs``/``amrl_maps`` present. Build with
    ``colcon build --packages-up-to <pkg>`` so peers compile first."""
    peers = {}
    seen = set()
    queue = list(pkg.dependencies)
    while queue:
        dep = queue.pop(0)
        if dep in seen:
            continue
        seen.add(dep)
        # A dep resolved by a manifest is apt-installed — there's no source to mount.
        if any((sp / f"{dep}.yaml").exists() for sp in search_paths):
            continue
        peer_dir = packages_dir(root) / dep
        if not (peer_dir / AIRFIELD_CONFIG).exists():
            continue
        peer_pkg = Package.load(peer_dir / AIRFIELD_CONFIG)
        peer_src = (peer_dir / peer_pkg.source_path).resolve()
        if peer_pkg.name not in peers:
            peers[peer_pkg.name] = peer_src
        queue.extend(peer_pkg.dependencies)
    return list(peers.items())


def docker_mount_args(pkg_dir: Path, pkg: Package, source_root: Path) -> List[str]:
    """Build docker -v mount arguments from package source and config mounts."""
    mount_args: List[str] = []

    container_src = container_source_mount_path(pkg.name)
    mount_args.extend(["-v", f"{source_root}:{container_src}"])

    seen_mounts = {str(source_root)}

    # Mount peer-package sources (custom deps built from source, e.g. amrl_msgs)
    # so colcon can build them alongside this package via --packages-up-to.
    root = find_project_root()
    if root is not None:
        target = "arm64" if is_arm64() else "x86_64"
        for peer_name, peer_src in _collect_peer_source_mounts(
            pkg, root, dependency_search_paths(root, target)
        ):
            if str(peer_src) in seen_mounts or not peer_src.exists():
                continue
            mount_args.extend(["-v", f"{peer_src}:{container_source_mount_path(peer_name)}"])
            seen_mounts.add(str(peer_src))

    for mount in _configured_mounts(pkg_dir):
        mount_path = Path(mount).expanduser()
        if not mount_path.is_absolute():
            mount_path = (pkg_dir / mount_path).resolve()
        else:
            mount_path = mount_path.resolve()

        mount_str = str(mount_path)
        if mount_str in seen_mounts:
            continue

        if not mount_path.exists():
            print(f"[WARN] Skipping mount '{mount}': path does not exist (resolved to {mount_path})")
            continue

        # Files mount fine with docker -v (e.g. ~/.bash_history or a single
        # calibration file); only nonexistent paths are skipped.
        mount_args.extend(["-v", f"{mount_path}:{mount_path}"])
        seen_mounts.add(mount_str)

    # Pass through declared host devices (e.g. VESC serial /dev/ttyACM0, joystick
    # /dev/input/js0) and supplementary groups (e.g. dialout) so nodes can access
    # hardware. Devices that aren't present are skipped so the container still
    # starts and the node reports the missing device itself.
    for dev in pkg.devices:
        if Path(dev).exists():
            mount_args.extend(["--device", dev])
        else:
            print(f"[WARN] Skipping device '{dev}': not present on host")
    for grp in pkg.group_add:
        mount_args.extend(["--group-add", grp])

    return mount_args


def _container_engine_alias() -> str:
    if is_arm_mac():
        return "container"
    docker_path = shutil.which("docker")
    if docker_path is None:
        return "docker"
    resolved = str(Path(docker_path).resolve()).lower()
    if "podman" in resolved:
        return "podman"
    if "singularity" in resolved:
        return "singularity"
    if "apptainer" in resolved:
        return "apptainer"
    return "docker"


def _is_jetson() -> bool:
    """Detect NVIDIA Jetson platforms (Tegra-based arm64 boards)."""
    return Path("/etc/nv_tegra_release").exists()


def gpu_runtime_args() -> List[str]:
    install_target = (os.environ.get("AIRFIELD_TORCH_INSTALL_TARGET") or os.environ.get("TORCH_INSTALL_TARGET") or "").strip().lower()
    # On Jetson the nvidia runtime is required for BASIC hardware access —
    # CSI cameras (nvarguscamerasrc), EGL, CUDA — not just for torch, so it
    # must not depend on a torch env var being set on the machine: plans have
    # to run identically on a fresh checkout. Elsewhere, GPU passthrough
    # remains opt-in via TORCH_INSTALL_TARGET=gpu.
    if install_target != "gpu" and not _is_jetson():
        return []

    # On Jetson/L4T, EGL and the Tegra GStreamer plugins (e.g. nvarguscamerasrc)
    # are only mounted into the container when the 'graphics'/'video'/'display'
    # driver capabilities are requested; 'compute,utility' alone leaves
    # libEGL.so.1 missing and CSI camera capture fails.
    driver_caps = "all" if _is_jetson() else "compute,utility"
    args: List[str] = [
        "-e", "NVIDIA_VISIBLE_DEVICES=all",
        "-e", f"NVIDIA_DRIVER_CAPABILITIES={driver_caps}",
    ]

    engine = _container_engine_alias()
    if engine == "docker":
        if _is_jetson():
            args.extend(["--runtime", "nvidia"])
        else:
            args.extend(["--gpus", "all"])

    # Mount the Argus camera daemon socket so CSI cameras work inside the container.
    if _is_jetson() and Path("/tmp/argus_socket").exists():
        args.extend(["-v", "/tmp/argus_socket:/tmp/argus_socket"])
    elif engine == "podman":
        for hook_dir in ("/usr/share/containers/oci/hooks.d", "/etc/containers/oci/hooks.d"):
            if Path(hook_dir).exists():
                args.extend(["--hooks-dir", hook_dir])
                break
        args.extend(["--security-opt", "label=disable"])

    device_candidates = {
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
        "/dev/nvidia-uvm-tools",
        "/dev/nvidia-modeset",
        *glob.glob("/dev/nvidia[0-9]*"),
    }
    # On Jetson, pass through V4L2 video nodes so CSI/USB cameras are visible
    # inside the container (e.g. nvarguscamerasrc / OpenCV capture).
    if _is_jetson():
        device_candidates.update(glob.glob("/dev/video[0-9]*"))
    for dev in sorted(device_candidates):
        if Path(dev).exists():
            args.extend(["--device", dev])

    return args
