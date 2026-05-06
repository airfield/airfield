import subprocess
from typing import Optional

import typer
from rich.console import Console

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    gpu_runtime_args,
    in_airfield_container,
    resolve_package_context,
)

console = Console()


def run(
    package_name: Optional[str] = typer.Argument(None, help="Package name/path (or use '.' for current package)"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency resolution"),
):
    """Open an interactive shell in the package container with source mounted."""
    if in_airfield_container():
        raise typer.BadParameter(
            "Already inside an Airfield container. "
            "You are already in the container environment; use a nested shell or exit to return to the host."
        )

    console.print(f"[dim]Loading package {package_name or '(auto)'}...[/dim]")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    console.print(f"Build successful. Opening shell in [cyan]{image_name}[/cyan]...")
    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    run_cmd = [
        "docker", "run", "-it", "--rm",
        "--group-add", "0",
        "--ipc=host", "--network=host",
        *mount_args,
        "-w", container_workdir(pkg),
        *runtime_gpu_args,
        image_name,
        "/bin/zsh", "-l",
    ]
    result = subprocess.run(run_cmd)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
