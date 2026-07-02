import subprocess
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

from airfield.config import is_arm_mac, is_arm64

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    entry_wrap_args,
    gpu_runtime_args,
    in_airfield_container,
    resolve_package_context,
    run_container_foreground,
)


def _run_name_autocomplete(ctx: typer.Context, incomplete: str):
    package_name = None
    if hasattr(ctx, "params") and isinstance(ctx.params, dict):
        package_name = ctx.params.get("package_name")

    try:
        _, pkg, _, _ = resolve_package_context(package_name, target_device="x86_64")
    except Exception:
        return []

    return [name for name in sorted(pkg.run.keys()) if name.startswith(incomplete)]


def _print_available_run_commands(pkg) -> None:
    if not pkg.run:
        console.print(f"[yellow]No run commands defined in airfield.yaml for package '{pkg.name}'[/yellow]")
        return

    table = Table(show_header=False, box=None, padding=(0, 1))
    for name in sorted(pkg.run.keys()):
        table.add_row(f"[bold cyan]{name}[/bold cyan]")

    console.print("")
    console.print(Panel(
        table,
        title="Available run commands",
        title_align="left",
        expand=False,
    ))
    console.print("")


def _host_workdir(pkg_dir: Path, pkg) -> str:
    raw = (pkg.default_workdir or ".").strip()
    if raw in {"", ".", "./"}:
        return str(pkg_dir.resolve())

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((pkg_dir / candidate).resolve())


def run(
    package_name: Optional[str] = typer.Argument(None, help="Package name/path (or run command if inside a package)"),
    run_name: Optional[str] = typer.Argument(None, help="Run command name from package airfield.yaml", autocompletion=_run_name_autocomplete),
    target_device: str = typer.Option("arm64" if is_arm64() else "x86_64", "--target-device", help="Target architecture for dependency resolution"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="Extra arguments appended to the configured run command"),
    execution: str = typer.Option("auto", "--execution", "-x", help="Execution mode: auto, container, or host"),
):
    """Run a named package command defined in airfield.yaml."""
    actual_package_name = package_name
    actual_run_name = run_name

    if package_name is not None and run_name is None:
        from airfield.config import find_package_root, AIRFIELD_CONFIG
        from airfield.models import Package
        local_pkg_dir = find_package_root()
        if local_pkg_dir is not None:
            local_pkg_yaml = local_pkg_dir / AIRFIELD_CONFIG
            if local_pkg_yaml.exists():
                try:
                    local_pkg = Package.load(local_pkg_yaml)
                    if local_pkg.run and package_name in local_pkg.run:
                        actual_package_name = None
                        actual_run_name = package_name
                except Exception:
                    pass

    package_name = actual_package_name
    run_name = actual_run_name

    console.print(f"[dim]Loading package {package_name or '(auto)'}...[/dim]")
    pkg_dir, pkg, deps, source_root = resolve_package_context(package_name, target_device=target_device)

    if run_name is None:
        _print_available_run_commands(pkg)
        return

    command_template = pkg.run.get(run_name)
    if command_template is None:
        available = ", ".join(sorted(pkg.run.keys())) if pkg.run else "(none)"
        raise typer.BadParameter(
            f"Unknown run command '{run_name}' for package '{pkg.name}'. Available run commands: {available}"
        )

    command_text = command_template
    if args and args.strip():
        command_text = f"{command_text} {args.strip()}"

    mode = execution.strip().lower()
    if mode not in {"auto", "container", "host"}:
        raise typer.BadParameter("--execution must be one of: auto, container, host")

    if mode == "auto":
        engine = "container" if is_arm_mac() else "docker"
        mode = "container" if shutil.which(engine) is not None else "host"
        console.print(f"[dim]Execution mode auto-selected: {mode}[/dim]")

    if mode == "host":
        workdir = _host_workdir(pkg_dir, pkg)
        console.print(f"Running [bold]{run_name}[/bold] on host in [dim]{workdir}[/dim]:")
        console.print(f"  [blue]> {command_text}[/blue]")
        result = subprocess.run(["/bin/bash", "-lc", command_text], cwd=workdir)
        if result.returncode != 0:
            raise typer.Exit(result.returncode)
        return

    if mode == "container":
        if in_airfield_container():
            raise typer.BadParameter(
                "Already inside an Airfield container. "
                "Use --execution host to run the command on the host instead."
            )

        engine = "container" if is_arm_mac() else "docker"
        if shutil.which(engine) is None:
            raise typer.BadParameter(f"Container execution requested but '{engine}' was not found. Use --execution host.")

    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    entry_env_args, container_cmd = entry_wrap_args(pkg, command_text)
    console.print(f"Build successful. Running [bold]{run_name}[/bold] in [cyan]{image_name}[/cyan]:")
    console.print(f"  [blue]> {command_text}[/blue]")

    if is_arm_mac():
        run_cmd = [
            "container", "run", "--rm",
            *mount_args,
            *entry_env_args,
            "-w", container_workdir(pkg),
            *runtime_gpu_args,
            image_name,
            *container_cmd,
        ]
    else:
        run_cmd = [
            "docker", "run", "--rm",
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
