import shlex
import subprocess
from typing import List, Optional

import typer
from airfield.config import is_arm_mac, is_arm64

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    gpu_runtime_args,
    in_airfield_container,
    resolve_package_context,
)


def run(
    package_name: Optional[str] = typer.Argument(None, help="Package name/path (or first word of command if inside a package)"),
    command: Optional[List[str]] = typer.Argument(None, help="Command to execute inside the package container"),
    target_device: str = typer.Option("arm64" if is_arm64() else "x86_64", "--target-device", help="Target architecture for dependency resolution"),
):
    """Run a command directly in the package container with source mounted."""
    if in_airfield_container():
        raise typer.BadParameter(
            "Already inside an Airfield container. "
            "Use the command directly on the host shell instead."
        )

    actual_package_name = package_name
    actual_command = command or []

    if package_name is not None:
        from airfield.config import find_package_root, AIRFIELD_CONFIG
        # Check if we are inside a package
        local_pkg_dir = find_package_root()
        if local_pkg_dir is not None:
            # We are inside a package. Is package_name actually a package or just part of the command?
            # If package_name is not a directory or a known package in a project, we assume it's part of the command.
            from pathlib import Path
            candidate = Path(package_name).expanduser()
            
            is_valid_package = False
            if candidate.exists() and (candidate / AIRFIELD_CONFIG).exists():
                is_valid_package = True
            else:
                from airfield.config import find_project_root, packages_dir, dependency_search_paths
                root = find_project_root()
                if root:
                    pkg_dir_candidate = packages_dir(root) / package_name
                    if pkg_dir_candidate.exists():
                        is_valid_package = True
                    else:
                        for sp in dependency_search_paths(root, target_device):
                            if (sp / f"{package_name}.yaml").exists():
                                is_valid_package = True
                                break

            if not is_valid_package:
                actual_package_name = None
                actual_command = [package_name] + actual_command

    if not actual_command:
        raise typer.BadParameter("Missing command to execute.")

    package_name = actual_package_name
    command = actual_command

    print(f"Loading package {package_name or '(auto)'}...")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    command_text = shlex.join(command)
    if pkg is not None and pkg.ros_distro and command and not command[0].startswith("colcon"):
        ros_prefix = f"source /opt/ros/{pkg.ros_distro}/setup.bash && (if [ ! -f install/setup.bash ]; then colcon build --symlink-install >/dev/null 2>&1; fi) && if [ -f install/setup.bash ]; then source install/setup.bash; fi"
        command_text = f"{ros_prefix} && {command_text}"
    print(f"Build successful. Running command in {image_name}: {command_text}")

    if is_arm_mac():
        run_cmd = [
            "container", "run", "--rm",
            *mount_args,
            "-w", container_workdir(pkg),
            *runtime_gpu_args,
            image_name,
            "/bin/bash", "-lc", command_text,
        ]
    else:
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
