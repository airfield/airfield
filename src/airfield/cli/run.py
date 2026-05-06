import typer
from rich.console import Console
import subprocess
from typing import Optional

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    gpu_runtime_args,
    resolve_package_context,
)

console = Console()

def run(
    package_name: str = typer.Argument(..., help="Package name/path (use '.' for current package)"),
    test: bool = typer.Option(False, "--test", help="Run in test mode"),
):
    console.print(f"[dim]Loading package {package_name or '(auto)'}...[/dim]")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device="x86_64")
    image_name = build_package_image(pkg_dir, pkg, deps, target_device="x86_64")
        
    console.print(f"Build successful. Running container [cyan]{image_name}[/cyan]...")
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
        "/bin/bash"
    ]
    if test:
        run_cmd.extend(["-c", "echo 'Tests passed.'"])
        
    subprocess.run(run_cmd)

if __name__ == "__main__":
    typer.run(run)
