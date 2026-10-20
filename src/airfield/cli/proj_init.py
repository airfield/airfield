import os
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from airfield.config import AIRFIELD_CONFIG
from airfield.models import SUPPORTED_ROS_DISTROS
from airfield.docker_cache import generate_dockerignore

console = Console()

AGENTS_FILE = "AGENTS.md"

# A pointer to `airfield guide`, not a copy of the guide: the guide changes
# with each Airfield version, while AGENTS.md belongs to the project. So this
# is written once, and nothing here ever needs rewriting.
AGENTS_SECTION = """## Airfield

This project is built and run with Airfield. Before you build, run, or change
dependencies here, run `airfield guide` and read all of it: it prints the guide
for the installed version of Airfield, and ends with the other pages it prints,
such as `airfield guide reference`; read the ones your task touches. In a
directory with an `airfield.yaml`, use the `airfield` CLI; don't run `docker`,
`colcon`, or `rosdep` on the host.

If `airfield` is not installed, install it from GitHub with
`pipx install git+https://github.com/airfield/airfield.git` (the `airfield`
package on PyPI is an unrelated project). The same guide is online at
https://airfield.io/docs/quick-start/.
"""


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


def _point_agents_at_guide(root: Path) -> Optional[str]:
    """Make AGENTS.md send coding agents to `airfield guide`.

    An existing AGENTS.md gets the section appended once; nothing already in
    it is changed. Returns a note for the user, or None.
    """
    agents = root / AGENTS_FILE
    note = None
    if not agents.exists():
        agents.write_text(f"# Notes for coding agents\n\n{AGENTS_SECTION}", encoding="utf-8")
    else:
        content = agents.read_text(encoding="utf-8")
        if "airfield guide" not in content:
            if content and not content.endswith("\n"):
                content += "\n"
            agents.write_text(f"{content}\n{AGENTS_SECTION}" if content else AGENTS_SECTION, encoding="utf-8")
            note = f"Added an Airfield section to the end of {AGENTS_FILE}."

    # Claude Code reads AGENTS.md only in a project without a CLAUDE.md.
    for claude_md in (root / "CLAUDE.md", root / ".claude" / "CLAUDE.md"):
        if not claude_md.exists():
            continue
        text = claude_md.read_text(encoding="utf-8", errors="replace")
        if AGENTS_FILE not in text and "airfield guide" not in text:
            import_path = os.path.relpath(agents, claude_md.parent)
            hint = (
                f"{claude_md.relative_to(root)} exists, so Claude Code will not read {AGENTS_FILE}. "
                f"Add the line @{import_path} to it so Claude Code sees the Airfield section."
            )
            note = f"{note}\n{hint}" if note else hint
        break
    return note


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

    agents_note = _point_agents_at_guide(project_root)

    _ensure_gitignore_entry(project_root, ".air")
    _ensure_gitignore_entry(project_root, ".airfield/")
    _ensure_gitignore_entry(project_root, "build/")
    _ensure_gitignore_entry(project_root, "log/")
    _ensure_gitignore_entry(project_root, "install/")

    # Generate .dockerignore for optimized container builds
    generate_dockerignore(project_root)

    console.print(f"[bold green]Initialized Airfield project at {project_root}[/bold green]")
    if agents_note:
        console.print(agents_note, style="yellow", markup=False)
