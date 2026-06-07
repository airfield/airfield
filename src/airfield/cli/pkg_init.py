from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Optional

import typer
import yaml
from rich.console import Console

from airfield.config import AIRFIELD_CONFIG, find_project_root, packages_dir
from airfield.models import SUPPORTED_ROS_DISTROS
from airfield.docker_cache import generate_dockerignore

console = Console()


def _parse_ros2_package_xml(package_xml: Path):
    tree = ET.parse(package_xml)
    root = tree.getroot()

    name_node = root.find("name")
    if name_node is None or not name_node.text:
        raise typer.BadParameter(f"Invalid ROS package.xml at {package_xml}: missing <name>")

    dep_tags = ["depend", "exec_depend", "build_depend", "buildtool_depend", "run_depend"]
    deps = []
    for tag in dep_tags:
        for node in root.findall(tag):
            if node.text:
                deps.append(node.text.strip())

    # Preserve order while deduplicating.
    dep_set = []
    for dep in deps:
        if dep and dep not in dep_set:
            dep_set.append(dep)

    return name_node.text.strip(), dep_set


def _write_airfield_yaml(path: Path, data: dict, force: bool) -> None:
    if path.exists() and not force:
        raise typer.BadParameter(f"{path} already exists. Use --force to overwrite.")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _ensure_gitignore_entry(root: Path, entry: str) -> None:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(f"{entry}\n", encoding="utf-8")
        return

    content = gitignore.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    if entry in lines:
        return

    suffix = "" if content.endswith("\n") or not content else "\n"
    gitignore.write_text(f"{content}{suffix}{entry}\n", encoding="utf-8")


def _project_ros_distro(project_root: Optional[Path]) -> str:
    if project_root is None:
        return "jazzy"

    project_yaml = project_root / AIRFIELD_CONFIG
    if not project_yaml.exists():
        return "jazzy"

    try:
        data = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
    except Exception:
        return "jazzy"

    if isinstance(data, dict):
        ros_distro = data.get("ros_distro")
        if isinstance(ros_distro, str) and ros_distro.strip():
            return ros_distro.strip().lower()

    return "jazzy"


def _is_path_like(value: str) -> bool:
    if value in {".", ".."}:
        return True
    if "/" in value or "\\" in value:
        return True
    return Path(value).exists()


def _init_new_package(name: str, force: bool, ros_distro: str) -> None:
    project_root = find_project_root()

    if project_root is not None and not _is_path_like(name):
        pkg_dir = packages_dir(project_root) / name
        package_name = name
    else:
        pkg_dir = Path(name).expanduser().resolve()
        package_name = pkg_dir.name

    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "src").mkdir(parents=True, exist_ok=True)

    package_yaml = {
        "kind": "package",
        "name": package_name,
        "dependencies": [],
        "source_path": "src",
        "ros_distro": ros_distro,
    }

    _write_airfield_yaml(pkg_dir / AIRFIELD_CONFIG, package_yaml, force=force)

    readme_path = pkg_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            f"# {package_name}\n\nAirfield package scaffold. Place ROS code under ./src\n",
            encoding="utf-8",
        )

    _ensure_gitignore_entry(pkg_dir, ".air")
    _ensure_gitignore_entry(pkg_dir, ".airfield/")
    _ensure_gitignore_entry(pkg_dir, "build/")
    _ensure_gitignore_entry(pkg_dir, "log/")
    _ensure_gitignore_entry(pkg_dir, "install/")

    # Generate .dockerignore for optimized container builds
    generate_dockerignore(pkg_dir)

    console.print(f"[bold green]Initialized Airfield package {package_name} at {pkg_dir}[/bold green]")


def _wrap_existing_ros_package(path: Path, force: bool, ros_distro: str) -> None:
    pkg_dir = path.resolve()
    package_xml = pkg_dir / "package.xml"
    if not package_xml.exists():
        raise typer.BadParameter(f"Expected package.xml at {package_xml}")

    pkg_name, ros_deps = _parse_ros2_package_xml(package_xml)

    package_yaml = {
        "kind": "package",
        "name": pkg_name,
        "dependencies": ros_deps,
        "source_path": ".",
        "ros_distro": ros_distro,
    }

    _write_airfield_yaml(pkg_dir / AIRFIELD_CONFIG, package_yaml, force=force)

    airfield_note = pkg_dir / "AIRFIELD.md"
    if not airfield_note.exists():
        airfield_note.write_text(
            "# Airfield Notes\n\n"
            "This ROS package has been wrapped for Airfield.\n"
            "- airfield.yaml defines Airfield package metadata\n"
            "- source_path is '.' so Airfield builds from this package root\n",
            encoding="utf-8",
        )

    _ensure_gitignore_entry(pkg_dir, ".air")
    _ensure_gitignore_entry(pkg_dir, ".airfield/")
    _ensure_gitignore_entry(pkg_dir, "build/")
    _ensure_gitignore_entry(pkg_dir, "log/")
    _ensure_gitignore_entry(pkg_dir, "install/")

    # Generate .dockerignore for optimized container builds
    generate_dockerignore(pkg_dir)

    console.print(f"[bold green]Wrapped existing ROS package at {pkg_dir}[/bold green]")


def run(
    name: Optional[str] = typer.Argument(None, help="New package name (omit when using --path for existing ROS package)"),
    path: Optional[Path] = typer.Option(None, "--path", help="Path to existing ROS package to wrap in place"),
    ros_distro: Optional[str] = typer.Option(None, "--ros-distro", help="ROS distribution for the package workspace (noetic, humble, or jazzy)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing airfield.yaml if present"),
):
    """Initialize a new Airfield package or wrap an existing ROS package."""
    project_root = find_project_root()
    resolved_ros_distro = (ros_distro or _project_ros_distro(project_root)).strip().lower()
    if resolved_ros_distro not in SUPPORTED_ROS_DISTROS:
        raise typer.BadParameter(
            f"Unsupported ROS distribution '{resolved_ros_distro}'. Supported values: {', '.join(sorted(SUPPORTED_ROS_DISTROS))}"
        )

    if path is not None:
        if name is not None:
            raise typer.BadParameter("Do not pass a package name when using --path.")
        _wrap_existing_ros_package(path, force=force, ros_distro=resolved_ros_distro)
        return

    if name is None:
        raise typer.BadParameter("Package name is required unless --path is provided.")

    _init_new_package(name, force=force, ros_distro=resolved_ros_distro)
