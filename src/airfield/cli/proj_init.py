from pathlib import Path

import typer
import yaml
from rich.console import Console

from airfield.config import AIRFIELD_CONFIG
from airfield.models import SUPPORTED_ROS_DISTROS
from airfield.docker_cache import generate_dockerignore

console = Console()


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")


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


def run(
    path: Path = typer.Argument(Path("."), exists=False, help="Project root directory to initialize"),
    ros_distro: str = typer.Option("jazzy", "--ros-distro", help="Default ROS distribution"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing airfield.yaml if present"),
):
    """Initialize a new Airfield project."""
    ros_distro = ros_distro.strip().lower()
    if ros_distro not in SUPPORTED_ROS_DISTROS:
        raise typer.BadParameter(
            f"Unsupported ROS distribution '{ros_distro}'. Supported values: {', '.join(sorted(SUPPORTED_ROS_DISTROS))}"
        )

    project_root = path.resolve()
    project_root.mkdir(parents=True, exist_ok=True)

    marker_path = project_root / AIRFIELD_CONFIG
    if marker_path.exists() and not force:
        console.print(f"[yellow]{marker_path.name} already exists at {marker_path}. Use --force to overwrite.[/yellow]")
        raise typer.Exit(1)

    (project_root / "packages").mkdir(parents=True, exist_ok=True)
    (project_root / "dependencies" / "x86_64").mkdir(parents=True, exist_ok=True)
    (project_root / "dependencies" / "arm64").mkdir(parents=True, exist_ok=True)
    (project_root / "plans").mkdir(parents=True, exist_ok=True)

    # No default_target_device field: commands detect the host arch, which is
    # correct when the same project checkout is used on both a dev laptop and
    # the robot. Cross-builds use the explicit --target-device flag.
    marker_data = {
        "kind": "project",
        "name": project_root.name,
        "version": "0.1.0",
        "ros_distro": ros_distro,
    }
    marker_path.write_text(yaml.safe_dump(marker_data, sort_keys=False), encoding="utf-8")


    _write_if_missing(
        project_root / "plans" / "example.yaml",
        """# Airfield plan: `airfield project up example` compiles this into a
# tmuxinator session. Each window is a tmux window; each pane runs a command.
# A pane with a `package:` key runs its `cmd` inside that package's container.
name: example
windows:
  - name: hello
    layout: main-vertical
    panes:
      - echo "Hello from plan 'example'. Replace these panes with launch commands."
      # Run a command inside a package's container:
      # - package: my_package
      #   cmd: ros2 launch my_package bringup.launch.py
""",
    )

    _write_if_missing(
        project_root / "dependencies" / "x86_64" / "README.md",
        "# x86_64 dependencies\n\nPlace dependency YAML files here.\n",
    )
    _write_if_missing(
        project_root / "dependencies" / "arm64" / "README.md",
        "# arm64 dependencies\n\nPlace dependency YAML files here.\n",
    )

    _ensure_gitignore_entry(project_root, ".air")
    _ensure_gitignore_entry(project_root, ".airfield/")
    _ensure_gitignore_entry(project_root, "build/")
    _ensure_gitignore_entry(project_root, "log/")
    _ensure_gitignore_entry(project_root, "install/")

    # Generate .dockerignore for optimized container builds
    generate_dockerignore(project_root)

    console.print(f"[bold green]Initialized Airfield project at {project_root}[/bold green]")
