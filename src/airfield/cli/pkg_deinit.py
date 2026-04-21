from pathlib import Path

import typer
from rich.console import Console

from airfield.cli.docker_cleanup import cleanup_package_container_artifacts
from airfield.config import AIRFIELD_CONFIG, LEGACY_PACKAGE_MARKER, require_package_root

console = Console()


def run(
    path: Path = typer.Option(None, "--path", help="Package root to deinitialize (defaults to current package)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirmation"),
):
    """Remove Airfield package config from a package."""
    package_root = path.resolve() if path is not None else require_package_root()
    package_yaml = package_root / AIRFIELD_CONFIG
    legacy_package_yaml = package_root / LEGACY_PACKAGE_MARKER
    airfield_note = package_root / "AIRFIELD.md"

    if not package_yaml.exists() and not legacy_package_yaml.exists() and not airfield_note.exists():
        console.print(f"[yellow]No Airfield package config found in {package_root}.[/yellow]")
        raise typer.Exit(1)

    targets = [p for p in [package_yaml, legacy_package_yaml, airfield_note] if p.exists()]

    if not yes:
        console.print("The following files will be removed:")
        for target in targets:
            console.print(f" - {target}")
        confirmed = typer.confirm("Proceed with package deinit?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    try:
        cleanup_package_container_artifacts(package_root)
    except FileNotFoundError:
        console.print("[yellow]Docker not found; skipping container cleanup.[/yellow]")

    for target in targets:
        target.unlink()

    console.print(f"[bold green]Deinitialized Airfield package config in {package_root}[/bold green]")
