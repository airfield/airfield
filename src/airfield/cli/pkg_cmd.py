import shlex
import subprocess
from typing import List, Optional

import typer

from airfield.cli.package_exec import (
    build_package_image,
    docker_mount_args,
    gpu_runtime_args,
    resolve_package_context,
)


def run(
    command: List[str] = typer.Argument(..., help="Command to execute inside the package container"),
    package_name: Optional[str] = typer.Option(
        None,
        "--package",
        "-p",
        help="Package name/path (optional in standalone package roots)",
    ),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency resolution"),
):
    """Run a command directly in the package container with source mounted."""
    print(f"Loading package {package_name or '(auto)'}...")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    command_text = shlex.join(command)
    print(f"Build successful. Running command in {image_name}: {command_text}")

    run_cmd = [
        "docker", "run", "--rm",
        "--ipc=host", "--network=host",
        *mount_args,
        *runtime_gpu_args,
        image_name,
        "/bin/bash", "-lc", command_text,
    ]

    result = subprocess.run(run_cmd)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
