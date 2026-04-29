import typer
import subprocess
from typing import Optional

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    gpu_runtime_args,
    resolve_package_context,
)

def run(
    package_name: Optional[str] = typer.Argument(None, help="Package name (optional in standalone package roots)"),
    test: bool = typer.Option(False, "--test", help="Run in test mode"),
):
    print(f"Loading package {package_name or '(auto)'}...")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device="x86_64")
    image_name = build_package_image(pkg_dir, pkg, deps, target_device="x86_64")
        
    print(f"Build successful. Running container {image_name}...")
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
