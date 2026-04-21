import os
import pwd
import re
import glob
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import typer
import yaml

from airfield.builder import Builder
from airfield.config import AIRFIELD_CONFIG, AIRFIELD_LOCAL_CONFIG, LEGACY_PACKAGE_MARKER, dependencies_dir, find_project_root, packages_dir, require_package_root
from airfield.host_check import detect_host_facts, evaluate_host_dependencies
from airfield.models import Dependency, Package, SUPPORTED_ROS_DISTROS


def _load_ros_distro(project_root: Optional[Path]) -> Optional[str]:
    if project_root is None:
        return None

    project_manifest = project_root / AIRFIELD_CONFIG
    if not project_manifest.exists():
        return None

    try:
        data = yaml.safe_load(project_manifest.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    ros_distro = data.get("ros_distro")
    if isinstance(ros_distro, str):
        ros_distro = ros_distro.strip().lower()
        if ros_distro:
            return ros_distro
    return None


def _resolve_package_ros_distro(pkg: Package, project_root: Optional[Path]) -> str:
    ros_distro = pkg.ros_distro or _load_ros_distro(project_root) or "jazzy"
    ros_distro = ros_distro.strip().lower()
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
            if candidate.exists():
                pkg_dir = candidate.resolve()
            else:
                pkg_dir = (packages_dir(root) / package_name).resolve()
        dep_root = dependencies_dir(root, target_device)
    else:
        if package_name is not None:
            pkg_dir = Path(package_name).expanduser().resolve()
        else:
            pkg_dir = require_package_root()
        dep_root = pkg_dir / "dependencies" / target_device

    pkg_yaml = pkg_dir / AIRFIELD_CONFIG
    if not pkg_yaml.exists():
        legacy_pkg_yaml = pkg_dir / LEGACY_PACKAGE_MARKER
        if legacy_pkg_yaml.exists():
            pkg_yaml = legacy_pkg_yaml
        else:
            raise typer.BadParameter(
                f"Package config not found at {pkg_dir / AIRFIELD_CONFIG}"
            )

    pkg = Package.load(pkg_yaml)
    _resolve_package_ros_distro(pkg, root)
    source_root = (pkg_dir / pkg.source_path).resolve()
    if not source_root.exists():
        raise typer.BadParameter(f"source_path '{pkg.source_path}' does not exist in {pkg_dir}")

    deps: List[Dependency] = []
    for dep_name in pkg.dependencies:
        dep_path = dep_root / f"{dep_name}.yaml"
        if dep_path.exists():
            deps.append(Dependency.load(dep_path))
        else:
            print(f"Warning: Dependency {dep_name} not found at {dep_path}")

    return pkg_dir, pkg, deps, source_root


def build_package_image(
    pkg_dir: Path,
    pkg: Package,
    deps: List[Dependency],
    target_device: str = "x86_64",
    show_all_output: bool = False,
) -> str:
    _apply_locked_dependency_versions(pkg)
    _validate_and_configure_host_dependencies(pkg, deps)

    print(f"Building container for {pkg.name} ({target_device})...")
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


def docker_mount_args(pkg_dir: Path, pkg: Package, source_root: Path) -> List[str]:
    """Build docker -v mount arguments from package source and config mounts."""
    mount_args: List[str] = []

    container_src = container_source_mount_path(pkg.name)
    mount_args.extend(["-v", f"{source_root}:{container_src}"])

    seen_mounts = {str(source_root)}
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
            raise typer.BadParameter(
                f"Configured mount path '{mount}' does not exist (resolved to {mount_path})"
            )
        if not mount_path.is_dir():
            raise typer.BadParameter(
                f"Configured mount path '{mount}' is not a directory (resolved to {mount_path})"
            )

        mount_args.extend(["-v", f"{mount_path}:{mount_path}"])
        seen_mounts.add(mount_str)

    return mount_args


def _container_engine_alias() -> str:
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


def gpu_runtime_args() -> List[str]:
    install_target = (os.environ.get("AIRFIELD_TORCH_INSTALL_TARGET") or os.environ.get("TORCH_INSTALL_TARGET") or "").strip().lower()
    if install_target != "gpu":
        return []

    args: List[str] = [
        "-e", "NVIDIA_VISIBLE_DEVICES=all",
        "-e", "NVIDIA_DRIVER_CAPABILITIES=compute,utility",
    ]

    engine = _container_engine_alias()
    if engine == "docker":
        args.extend(["--gpus", "all"])
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
    for dev in sorted(device_candidates):
        if Path(dev).exists():
            args.extend(["--device", dev])

    return args
