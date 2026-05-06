import shlex
import subprocess
from typing import List, Optional

import typer

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    gpu_runtime_args,
    in_airfield_container,
    resolve_package_context,
)


def run(
    package_name: str = typer.Argument(..., help="Package name/path (use '.' for current package)"),
    command: List[str] = typer.Argument(..., help="Command to execute inside the package container"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency resolution"),
):
    """Run a command directly in the package container with source mounted."""
    if in_airfield_container():
        raise typer.BadParameter(
            "Already inside an Airfield container. "
            "Use the command directly on the host shell instead."
        )

    print(f"Loading package {package_name or '(auto)'}...")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    command_text = shlex.join(command)
    print(f"Build successful. Running command in {image_name}: {command_text}")

    run_cmd = [
        "docker", "run", "--rm",
        "--group-add", "0",
        "--ipc=host", "--network=host",
        *mount_args,
        "-w", container_workdir(pkg),
        *runtime_gpu_args,
        image_name,
        "/bin/bash", "-lc", command_text,
    ]

    result = subprocess.run(run_cmd)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
