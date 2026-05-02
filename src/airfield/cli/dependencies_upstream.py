from pathlib import Path
from typing import List, Optional

import typer

from airfield.config import packages_repo_root


def _dependency_names(dep_root: Path) -> List[str]:
    if not dep_root.exists():
        return []
    return sorted(path.stem for path in dep_root.glob("*.yaml") if path.is_file())


def _local_dependency_root(pkg_dir: Path, target_device: str) -> Path:
    return pkg_dir / "dependencies" / target_device


def check(
    package: Optional[Path] = typer.Option(None, "--package", help="Package directory to inspect"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency manifests"),
):
    """Check local dependency manifests against the shared packages repository."""
    pkg_dir = (package or Path.cwd()).expanduser().resolve()
    local_dep_root = _local_dependency_root(pkg_dir, target_device)
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
    package: Optional[Path] = typer.Option(None, "--package", help="Package directory to upstream from"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency manifests"),
):
    """Prepare local dependency manifests for upstreaming into the shared packages repository."""
    pkg_dir = (package or Path.cwd()).expanduser().resolve()
    local_dep_root = _local_dependency_root(pkg_dir, target_device)
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
    typer.echo("Command: airfield package dependencies upstream")

    if not typer.confirm("Copy local dependency manifests into the packages repository?", default=False):
        raise typer.Exit(1)

    for src in local_files:
        dst = repo_root / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    typer.echo(f"Copied {len(local_files)} dependency manifest(s) into {repo_root}")
    typer.echo("Next: create a feature branch, commit, push, and open a pull request in the packages repository.")