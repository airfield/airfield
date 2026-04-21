from pathlib import Path
from typing import Optional

import typer
import yaml

AIRFIELD_CONFIG = "airfield.yaml"
AIRFIELD_LOCAL_CONFIG = ".air"
LEGACY_PROJECT_MARKER = "project.yaml"
LEGACY_PACKAGE_MARKER = "package.yaml"


def _load_yaml(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _airfield_kind_in_dir(path: Path) -> Optional[str]:
    cfg = path / AIRFIELD_CONFIG
    if cfg.exists():
        data = _load_yaml(cfg)
        if isinstance(data, dict):
            kind = data.get("kind")
            if kind in {"project", "package"}:
                return kind

            # Heuristic fallback for configs without explicit kind.
            if "source_path" in data:
                return "package"
            return "project"

    if (path / LEGACY_PACKAGE_MARKER).exists():
        return "package"
    if (path / LEGACY_PROJECT_MARKER).exists():
        return "project"

    return None


def find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Find the nearest Airfield project root by walking parent directories."""
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for candidate in [current, *current.parents]:
        if _airfield_kind_in_dir(candidate) == "project":
            return candidate
    return None


def find_package_root(start: Optional[Path] = None) -> Optional[Path]:
    """Find the nearest Airfield package root by walking parent directories."""
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for candidate in [current, *current.parents]:
        if _airfield_kind_in_dir(candidate) == "package":
            return candidate
    return None


def require_project_root(start: Optional[Path] = None) -> Path:
    root = find_project_root(start)
    if root is None:
        raise typer.BadParameter(
            "Not inside an Airfield project. Run 'airfield project init' first or cd into a project root."
        )
    return root


def require_package_root(start: Optional[Path] = None) -> Path:
    root = find_package_root(start)
    if root is None:
        raise typer.BadParameter(
            "Not inside an Airfield package. Run 'airfield package init' first or cd into a package root."
        )
    return root


def packages_dir(root: Path) -> Path:
    return root / "packages"


def dependencies_dir(root: Path, target_device: str) -> Path:
    return root / "dependencies" / target_device


def plans_dir(root: Path) -> Path:
    return root / "plans"
