import subprocess
import shutil
from pathlib import Path
from typing import Optional

import typer

from airfield.cli.package_exec import (
    build_package_image,
    container_workdir,
    docker_mount_args,
    gpu_runtime_args,
    in_airfield_container,
    resolve_package_context,
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
    print(f"Available run commands for package '{pkg.name}':")
    if not pkg.run:
        print("  (none defined in airfield.yaml under 'run')")
        return
    for name in sorted(pkg.run.keys()):
        print(f"  - {name}")


def _host_workdir(pkg_dir: Path, pkg) -> str:
    raw = (pkg.default_workdir or ".").strip()
    if raw in {"", ".", "./"}:
        return str(pkg_dir.resolve())

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((pkg_dir / candidate).resolve())


def run(
    run_name: Optional[str] = typer.Argument(None, help="Run command name from package airfield.yaml", autocompletion=_run_name_autocomplete),
    package_name: Optional[str] = typer.Option(
        None,
        "--package",
        "-p",
        help="Package name/path (optional in standalone package roots)",
    ),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency resolution"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="Extra arguments appended to the configured run command"),
    execution: str = typer.Option("auto", "--execution", "-x", help="Execution mode: auto, container, or host"),
):
    """Run a named package command defined in airfield.yaml."""
    print(f"Loading package {package_name or '(auto)'}...")
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
        mode = "container" if shutil.which("docker") is not None else "host"
        print(f"Execution mode auto-selected: {mode}")

    if mode == "host":
        workdir = _host_workdir(pkg_dir, pkg)
        print(f"Running '{run_name}' on host in {workdir}: {command_text}")
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

        if shutil.which("docker") is None:
            raise typer.BadParameter("Container execution requested but 'docker' was not found. Use --execution host.")

    image_name = build_package_image(pkg_dir, pkg, deps, target_device=target_device)

    mount_args = docker_mount_args(pkg_dir, pkg, source_root)
    runtime_gpu_args = gpu_runtime_args()
    print(f"Build successful. Running '{run_name}' in {image_name}: {command_text}")

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
