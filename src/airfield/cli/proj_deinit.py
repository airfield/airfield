import shutil
from pathlib import Path

import typer
from rich.console import Console

from airfield.cli.docker_cleanup import cleanup_package_container_artifacts
from airfield.config import AIRFIELD_CONFIG, require_project_root

console = Console()


def _remove_if_exists(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _cleanup_empty_dir(path: Path) -> None:
    if path.exists() and path.is_dir() and not any(path.iterdir()):
        path.rmdir()


def run(
    path: Path = typer.Option(None, "--path", help="Project root to deinitialize (defaults to current project)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirmation"),
):
    """Remove Airfield project config from a project."""
    project_root = path.resolve() if path is not None else require_project_root()

    targets = [
        project_root / AIRFIELD_CONFIG,
        project_root / ".airfield",
        project_root / "plans" / "example.yaml",
        project_root / "dependencies" / "x86_64" / "README.md",
        project_root / "dependencies" / "arm64" / "README.md",
    ]
    existing_targets = [t for t in targets if t.exists()]

    if not existing_targets:
        console.print(f"[yellow]No Airfield project config found in {project_root}.[/yellow]")
        raise typer.Exit(1)

    if not yes:
        console.print("The following Airfield config paths will be removed:")
        for target in existing_targets:
            console.print(f" - {target}")
        confirmed = typer.confirm("Proceed with project deinit?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    packages_root = project_root / "packages"
    if packages_root.exists():
        for package_dir in packages_root.iterdir():
            if package_dir.is_dir():
                try:
                    cleanup_package_container_artifacts(package_dir)
                except FileNotFoundError:
                    console.print("[yellow]Docker not found; skipping container cleanup.[/yellow]")
                    break

    for target in existing_targets:
        _remove_if_exists(target)

    # Prune scaffold folders if they became empty.
    _cleanup_empty_dir(project_root / "dependencies" / "x86_64")
    _cleanup_empty_dir(project_root / "dependencies" / "arm64")
    _cleanup_empty_dir(project_root / "dependencies")
    _cleanup_empty_dir(project_root / "plans")

    console.print(f"[bold green]Deinitialized Airfield project config in {project_root}[/bold green]")
