import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.console import Console

from airfield.config import (
    AIRFIELD_CONFIG,
    LEGACY_PACKAGE_MARKER,
    LEGACY_PROJECT_MARKER,
    dependencies_dir,
    find_package_root,
    find_project_root,
    plans_dir,
)

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
    legacy = package_root / LEGACY_PACKAGE_MARKER
    if legacy.exists():
        return legacy
    return None


def _project_manifest_path(project_root: Path) -> Optional[Path]:
    primary = project_root / AIRFIELD_CONFIG
    if primary.exists():
        return primary
    legacy = project_root / LEGACY_PROJECT_MARKER
    if legacy.exists():
        return legacy
    return None


def _docker_summary(image_name: str) -> Dict[str, Any]:
    try:
        image_result = subprocess.run(
            ["docker", "image", "inspect", image_name],
            capture_output=True,
            text=True,
            check=False,
        )
        image_exists = image_result.returncode == 0

        container_result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"ancestor={image_name}"],
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
    console.print(f"default_target_device: {project.get('default_target_device', 'x86_64')}")

    project_packages = project_root / "packages"
    package_count = 0
    package_names: List[str] = []
    if project_packages.exists():
        for child in sorted(project_packages.iterdir()):
            if not child.is_dir():
                continue
            if (child / AIRFIELD_CONFIG).exists() or (child / LEGACY_PACKAGE_MARKER).exists():
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
    console.print(f"ros_distro: {package.get('ros_distro', 'jazzy')}")
    console.print(f"source_path: {source_path}")
    console.print(f"source_exists: {'yes' if source_root.exists() else 'no'}")
    if project_root is not None:
        console.print(f"project_root: {project_root}")
    else:
        console.print("project_root: standalone")

    if project_root is not None:
        dep_root = dependencies_dir(project_root, target_device)
    else:
        dep_root = package_root / "dependencies" / target_device

    console.print(f"target_device: {target_device}")
    console.print(f"dependency_root: {dep_root}")
    console.print(f"declared_dependencies: {len(dependencies)}")

    if dependencies:
        for dep in dependencies:
            dep_file = dep_root / f"{dep}.yaml"
            status = "ok" if dep_file.exists() else "missing"
            console.print(f" - {dep}: {status}")

    image_name = f"airfield-pkg-{package_name}:latest"
    docker = _docker_summary(image_name)
    console.print(f"image: {image_name}")
    if docker["docker_available"]:
        console.print(f"image_exists: {'yes' if docker['image_exists'] else 'no'}")
        console.print(f"containers_from_image: {docker['container_count']}")
    else:
        console.print("docker: unavailable")


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

    resolved_target = target_device
    if resolved_target is None:
        resolved_target = "x86_64"
        if project_root is not None:
            project_manifest = _project_manifest_path(project_root)
            if project_manifest is not None:
                project_data = _load_yaml(project_manifest)
                default_target = project_data.get("default_target_device")
                if isinstance(default_target, str) and default_target.strip():
                    resolved_target = default_target.strip()

    if project_root is not None:
        _print_project_status(project_root)
        if package_root is not None:
            console.print("")

    if package_root is not None:
        _print_package_status(package_root, project_root, resolved_target)