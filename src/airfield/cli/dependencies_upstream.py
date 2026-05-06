from pathlib import Path
from typing import List, Optional

import typer

from airfield.config import find_package_root, find_project_root, packages_repo_root


def _dependency_names(dep_root: Path) -> List[str]:
    if not dep_root.exists():
        return []
    return sorted(path.stem for path in dep_root.glob("*.yaml") if path.is_file())


def _dependency_root_for(root: Path, target_device: str) -> Path:
    return root / "dependencies" / target_device


def _resolve_local_dependency_root(start: Path, target_device: str) -> Path:
    """Resolve local dependency manifests from package or project context."""
    start = start.expanduser().resolve()
    if start.is_file():
        start = start.parent

    direct_root = _dependency_root_for(start, target_device)
    if direct_root.exists():
        return direct_root

    package_root = find_package_root(start)
    if package_root is not None:
        package_dep_root = _dependency_root_for(package_root, target_device)
        if package_dep_root.exists():
            return package_dep_root

    project_root = find_project_root(start)
    if project_root is not None:
        return _dependency_root_for(project_root, target_device)

    return direct_root


def check(
    target: Path = typer.Argument(..., help="Package or project directory to inspect (use '.' for current)"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency manifests"),
):
    """Check local dependency manifests against the shared packages repository."""
    local_dep_root = _resolve_local_dependency_root(target, target_device)
    if not local_dep_root.exists():
        typer.echo(f"No local dependencies found at {local_dep_root}")
        raise typer.Exit(0)

    repo_root = packages_repo_root() / target_device
    local_names = _dependency_names(local_dep_root)
    repo_names = set(_dependency_names(repo_root))
    conflicts = [name for name in local_names if name in repo_names]

    typer.echo(f"Local dependency root: {local_dep_root}")
    typer.echo(f"Repository dependency root: {repo_root}")
    typer.echo(f"Local dependency manifests: {', '.join(local_names) if local_names else '(none)'}")
    if conflicts:
        typer.echo(f"Conflicts: {', '.join(conflicts)}")
        typer.echo("Rename your package or switch to the existing package.")
        raise typer.Exit(1)

    typer.echo("No name conflicts found.")


def upstream(
    target: Path = typer.Argument(..., help="Package or project directory to upstream from (use '.' for current)"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency manifests"),
):
    """Prepare local dependency manifests for upstreaming into the shared packages repository."""
    local_dep_root = _resolve_local_dependency_root(target, target_device)
    if not local_dep_root.exists():
        typer.echo(f"No local dependencies found at {local_dep_root}")
        raise typer.Exit(1)

    repo_root = packages_repo_root() / target_device
    repo_root.mkdir(parents=True, exist_ok=True)

    local_files = sorted(local_dep_root.glob("*.yaml"))
    existing_names = {path.stem for path in repo_root.glob("*.yaml")}
    collisions = [path.stem for path in local_files if path.stem in existing_names]
    if collisions:
        typer.echo(f"Existing packages found: {', '.join(collisions)}")
        typer.echo("Rename your package or switch to the existing package.")
        raise typer.Exit(1)

    typer.echo("Repository: https://github.com/airfield/packages")
    typer.echo("README: https://github.com/airfield/packages#readme")
    typer.echo("Command: airfield package dependencies upstream .")

    if not typer.confirm("Copy local dependency manifests into the packages repository?", default=False):
        raise typer.Exit(1)

    for src in local_files:
        dst = repo_root / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    typer.echo(f"Copied {len(local_files)} dependency manifest(s) into {repo_root}")
    typer.echo("Next: create a feature branch, commit, push, and open a pull request in the packages repository.")
