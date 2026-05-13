"""Docker cache management commands for Airfield."""

import subprocess

import typer
from rich.console import Console

console = Console()


def cache_status():
    """Show Docker BuildKit cache status."""
    try:
        # Try to get cache status from docker buildx
        result = subprocess.run(
            ["docker", "buildx", "du"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("[bold]Docker BuildKit Cache Status:[/bold]")
            console.print(result.stdout)
        else:
            console.print("[yellow]Docker BuildKit cache not available or not configured[/yellow]")
    except FileNotFoundError:
        console.print("[red]Docker not found[/red]")
        raise typer.Exit(1)


def cache_prune(aggressive: bool = False):
    """Prune Docker BuildKit cache."""
    try:
        if aggressive:
            console.print("[yellow]Pruning all BuildKit cache (aggressive)...[/yellow]")
            cmd = ["docker", "buildx", "prune", "-a", "-f"]
        else:
            console.print("[yellow]Pruning unused BuildKit cache...[/yellow]")
            cmd = ["docker", "buildx", "prune", "-f"]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            console.print("[green]✓ Cache pruned successfully[/green]")
            console.print(result.stdout)
        else:
            console.print("[red]Failed to prune cache[/red]")
            console.print(result.stderr)
            raise typer.Exit(1)
    except FileNotFoundError:
        console.print("[red]Docker not found[/red]")
        raise typer.Exit(1)


def run(
    action: str = typer.Argument(..., help="Action to perform: status, prune"),
    aggressive: bool = typer.Option(False, "--aggressive", help="For prune: remove all cache including in-use layers"),
):
    """Manage Docker BuildKit cache for optimized builds.
    
    Examples:
        airfield docker cache status    - Show cache usage
        airfield docker cache prune     - Remove unused cache
        airfield docker cache prune --aggressive  - Remove all cache
    """
    if action == "status":
        cache_status()
    elif action == "prune":
        cache_prune(aggressive=aggressive)
    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print("Valid actions: status, prune")
        raise typer.Exit(1)
