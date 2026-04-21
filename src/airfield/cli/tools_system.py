import typer
from rich.console import Console

from airfield.cli.docker_cleanup import cleanup_all_airfield_containers

console = Console()


def run():
    """Remove all containers created from Airfield package images."""
    try:
        removed = cleanup_all_airfield_containers()
    except FileNotFoundError:
        console.print("[yellow]Docker not found; skipping container cleanup.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold green]Removed {removed} Airfield container(s).[/bold green]")