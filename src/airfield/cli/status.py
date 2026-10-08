import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.console import Console

from airfield.config import (
    AIRFIELD_CONFIG,
    dependency_search_paths,
    find_package_root,
    find_project_root,
    plans_dir,
    is_arm_mac,
    is_arm64,
)
from airfield.dependency_resolver import (
    INFERRED,
    INVALID,
    PEER,
    RECIPE,
    SKIPPED,
    VIA_ROS_INDEX,
    VIA_ROSDEP,
    WORKSPACE,
    resolve_dependencies,
)
from airfield.models import Package
from airfield.package_xml import PACKAGE_XML

console = Console()


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _package_manifest_path(package_root: Path) -> Optional[Path]:
    primary = package_root / AIRFIELD_CONFIG
    if primary.exists():
        return primary
    return None


def _project_manifest_path(project_root: Path) -> Optional[Path]:
    primary = project_root / AIRFIELD_CONFIG
    if primary.exists():
        return primary
    return None


def _docker_summary(image_name: str) -> Dict[str, Any]:
    engine = "container" if is_arm_mac() else "docker"
    try:
        image_result = subprocess.run(
            [engine, "image", "inspect", image_name],
            capture_output=True,
            text=True,
            check=False,
        )
        image_exists = image_result.returncode == 0

        container_result = subprocess.run(
            [engine, "ps", "-aq", "--filter", f"ancestor={image_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        container_count = 0
        if container_result.returncode == 0:
            container_count = len([line for line in container_result.stdout.splitlines() if line.strip()])

        return {
            "docker_available": True,
            "image_exists": image_exists,
            "container_count": container_count,
        }
    except FileNotFoundError:
        return {
            "docker_available": False,
            "image_exists": False,
            "container_count": 0,
        }


def _print_project_status(project_root: Path) -> None:
    console.print("[bold]Project status[/bold]")
    console.print(f"root: {project_root}")

    manifest_path = _project_manifest_path(project_root)
    if manifest_path is None:
        console.print("manifest: missing")
        return

    project = _load_yaml(manifest_path)
    console.print(f"manifest: {manifest_path.name}")
    console.print(f"name: {project.get('name', project_root.name)}")
    console.print(f"kind: {project.get('kind', 'project')}")
    console.print(f"version: {project.get('version', 'unknown')}")
    console.print(f"ros_distro: {project.get('ros_distro', 'unknown')}")

    project_packages = project_root / "packages"
    package_count = 0
    package_names: List[str] = []
    if project_packages.exists():
        for child in sorted(project_packages.iterdir()):
            if not child.is_dir():
                continue
            if (child / AIRFIELD_CONFIG).exists():
                package_count += 1
                package_names.append(child.name)

    console.print(f"packages_dir: {project_packages}")
    console.print(f"packages_in_packages_dir: {package_count}")
    if package_names:
        console.print(f"package_names: {', '.join(package_names)}")

    dep_root = project_root / "dependencies"
    if dep_root.exists():
        targets = sorted([d for d in dep_root.iterdir() if d.is_dir()])
        if targets:
            for target in targets:
                dep_files = sorted(target.glob("*.yaml"))
                console.print(f"dependencies_{target.name}: {len(dep_files)} manifests")
        else:
            console.print("dependencies: no target folders")
    else:
        console.print("dependencies: missing")

    plan_root = plans_dir(project_root)
    plan_files = sorted(plan_root.glob("*.yaml")) if plan_root.exists() else []
    console.print(f"plans: {len(plan_files)}")
    if plan_files:
        console.print(f"plan_names: {', '.join(p.stem for p in plan_files)}")


def _print_resolved_dependencies(
    manifest_path: Path,
    package_root: Path,
    source_root: Path,
    project_root: Optional[Path],
    search_paths: List[Path],
) -> None:
    """List everything the image will contain and which file asked for it.

    Dependencies come from two places (airfield.yaml and the package.xml files
    in the source tree) and resolve in several ways, so this is where to look
    when the image has something unexpected or lacks something expected.
    """
    try:
        pkg = Package.load(manifest_path)
        plan = resolve_dependencies(pkg, package_root, source_root, project_root, search_paths)
    except Exception as exc:
        console.print(f"dependencies: could not be resolved ({exc})", markup=False)
        return

    def shown(path: Path) -> str:
        # Shortest unambiguous form: inside this package, else inside the
        # project, else (a shared manifest) its folder and file name.
        for base in (package_root.resolve(), project_root):
            if base is not None and base in path.parents:
                return str(path.relative_to(base))
        return f"{path.parent.name}/{path.name}"

    console.print(f"dependencies: {len(plan.entries)}")
    for entry in plan.entries:
        if entry.kind == RECIPE:
            how = f"manifest {shown(entry.location)}"
        elif entry.kind == PEER:
            how = f"peer package {shown(entry.location)}, built from source"
        elif entry.kind == WORKSPACE:
            how = "source in this package"
        elif entry.kind == INFERRED:
            dep = entry.dependency
            apt_names = " ".join(dep.apt).replace("$ROS_DISTRO", pkg.ros_distro or "")
            if dep.pip:
                how = f"no manifest, installed with pip as {' '.join(dep.pip)} (rosdep's table)"
            elif not dep.apt:
                how = "no manifest, nothing to install (rosdep's table)"
            elif dep.inferred_via == VIA_ROSDEP:
                how = f"no manifest, installed from apt as {apt_names} (rosdep's table)"
            elif dep.inferred_via == VIA_ROS_INDEX:
                how = f"no manifest, installed from apt as {apt_names} (released ROS package)"
            else:
                how = f"no manifest, installed from apt as {apt_names}"
        elif entry.kind == SKIPPED:
            how = "skipped (skip_dependencies)"
        elif entry.kind == INVALID:
            how = "ignored, not a valid package name"
        else:
            how = "missing: no manifest and no peer package"
        asked = ", ".join(shown(path) for path in entry.requested_by)
        console.print(f" - {entry.name}: {how} (from {asked})", markup=False)

    for problem in plan.problems:
        console.print(f"warning: {problem}", markup=False)

    # An airfield.yaml entry that this package's own package.xml also lists is
    # a second copy of the same fact; the package.xml one is enough.
    own_yaml = package_root.resolve() / AIRFIELD_CONFIG
    repeated = [
        entry.name
        for entry in plan.entries
        if own_yaml in entry.requested_by
        and any(path.name == PACKAGE_XML and source_root in path.parents for path in entry.requested_by)
    ]
    if repeated:
        console.print(
            f"also_in_package_xml: {', '.join(repeated)}  "
            "(these airfield.yaml entries repeat package.xml and can be removed)",
            markup=False,
        )


def _print_package_status(package_root: Path, project_root: Optional[Path], target_device: str) -> None:
    console.print("[bold]Package status[/bold]")
    console.print(f"root: {package_root}")

    manifest_path = _package_manifest_path(package_root)
    if manifest_path is None:
        console.print("manifest: missing")
        return

    package = _load_yaml(manifest_path)
    package_name = str(package.get("name", package_root.name))
    source_path = str(package.get("source_path", "src"))
    source_root = (package_root / source_path).resolve()
    dependencies = package.get("dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = []

    console.print(f"manifest: {manifest_path.name}")
    console.print(f"name: {package_name}")
    console.print(f"kind: {package.get('kind', 'package')}")
    console.print(f"ros_distro: {package.get('ros_distro') or 'none'}")
    console.print(f"base_image: {package.get('base_image') or 'default'}")
    console.print(f"source_path: {source_path}")
    console.print(f"source_exists: {'yes' if source_root.exists() else 'no'}")
    if project_root is not None:
        console.print(f"project_root: {project_root}")
    else:
        console.print("project_root: standalone")

    if project_root is not None:
        search_paths = dependency_search_paths(project_root, target_device)
    else:
        search_paths = dependency_search_paths(package_root, target_device)

    console.print(f"target_device: {target_device}")
    console.print(f"dependency_search_paths:")
    for sp in search_paths:
        console.print(f"  - {sp}")
    console.print(f"declared_dependencies: {len(dependencies)}")
    _print_resolved_dependencies(manifest_path, package_root, source_root, project_root, search_paths)

    image_name = f"airfield-pkg-{package_name}:latest"
    docker = _docker_summary(image_name)
    console.print(f"image: {image_name}")
    try:
        from airfield.cli.package_exec import image_registry

        registry = image_registry(package_root)
    except Exception as exc:
        registry = f"invalid ({exc})"
    console.print(
        f"image_registry: {registry or 'none (images are built on this machine)'}",
        markup=False,
    )
    if docker["docker_available"]:
        console.print(f"image_exists: {'yes' if docker['image_exists'] else 'no'}")
        console.print(f"containers_from_image: {docker['container_count']}")
    else:
        engine_name = "container" if is_arm_mac() else "docker"
        console.print(f"{engine_name}: unavailable")


def run(
    path: Optional[Path] = typer.Option(None, "--path", help="Path to inspect (defaults to current directory)"),
    target_device: Optional[str] = typer.Option(None, "--target-device", help="Target device used for dependency resolution"),
):
    """Print status for the current Airfield package or project context."""
    start = path.resolve() if path is not None else Path.cwd()
    project_root = find_project_root(start)
    package_root = find_package_root(start)

    if package_root is None and project_root is None:
        console.print(f"[yellow]No Airfield project or package found at {start}.[/yellow]")
        raise typer.Exit(1)

    # Same rule as every build/run command: host arch unless --target-device
    # is passed explicitly.
    resolved_target = target_device
    if resolved_target is None:
        resolved_target = "arm64" if is_arm64() else "x86_64"

    if project_root is not None:
        _print_project_status(project_root)
        if package_root is not None:
            console.print("")

    if package_root is not None:
        _print_package_status(package_root, project_root, resolved_target)
