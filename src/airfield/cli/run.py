import typer
from rich.console import Console

from airfield.config import is_arm_mac, is_arm64
from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    entry_wrap_args,
    gpu_runtime_args,
    resolve_package_context,
    run_container_foreground,
)

console = Console()

def run(
    package_name: str = typer.Argument(..., help="Package name/path (use '.' for current package)"),
    target_device: str = typer.Option("arm64" if is_arm64() else "x86_64", "--target-device", help="Target architecture for dependency resolution"),
    test: bool = typer.Option(False, "--test", help="Run the package's 'test' run command instead of 'default'"),
):
    console.print(f"[dim]Loading package {package_name or '(auto)'}...[/dim]")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    mount_args = docker_mount_args(pkg_dir, pkg, source_root, target_device)
    runtime_gpu_args = gpu_runtime_args()

    run_name = "test" if test else "default"
    entrypoint_cmd = pkg.run.get(run_name)
    if test and not entrypoint_cmd:
        console.print(
            f"[red]No 'test' run command defined in {pkg.name}'s airfield.yaml; nothing was tested.[/red]"
        )
        raise typer.Exit(1)

    if entrypoint_cmd:
        console.print(f"Build successful. Running [bold]{run_name}[/bold] in [cyan]{image_name}[/cyan]...")
        entry_env_args, container_cmd = entry_wrap_args(pkg, entrypoint_cmd)
    else:
        console.print("[yellow]Warning: No 'default' run command defined in airfield.yaml. Dropping into interactive shell.[/yellow]")
        entry_env_args, container_cmd = [], ["/bin/bash", "-l"]

    if is_arm_mac():
        run_cmd = [
            "container", "run", "-it", "--rm",
            *mount_args,
            *entry_env_args,
            "-w", container_workdir(pkg),
            *runtime_gpu_args,
            image_name,
            *container_cmd,
        ]
    else:
        run_cmd = [
            "docker", "run", "-it", "--rm",
            "--group-add", "0",
            "--ipc=host", "--network=host",
            *mount_args,
            *entry_env_args,
            "-w", container_workdir(pkg),
            *runtime_gpu_args,
            image_name,
            *container_cmd,
        ]

    returncode = run_container_foreground(run_cmd)
    if returncode != 0:
        raise typer.Exit(returncode)

if __name__ == "__main__":
    typer.run(run)
