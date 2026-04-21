import subprocess
from typing import Optional

import typer

from airfield.cli.package_exec import (
    build_package_image,
    docker_mount_args,
    gpu_runtime_args,
    resolve_package_context,
)


def run(
    package_name: Optional[str] = typer.Argument(None, help="Package name/path (optional in standalone package roots)"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency resolution"),
):
    """Open an interactive shell in the package container with source mounted."""
    print(f"Loading package {package_name or '(auto)'}...")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    print(f"Build successful. Opening shell in {image_name}...")
    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    run_cmd = [
        "docker", "run", "-it", "--rm",
        "--ipc=host", "--network=host",
        *mount_args,
        *runtime_gpu_args,
        image_name,
        "/bin/zsh", "-l",
    ]
    result = subprocess.run(run_cmd)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
