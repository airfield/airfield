import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
import yaml

AIRFIELD_CONFIG = "airfield.yaml"
AIRFIELD_LOCAL_CONFIG = ".air"
PACKAGES_GITHUB_REPO = "https://github.com/airfield/packages.git"


def _xdg_home(env_name: str, fallback_suffix: str) -> Path:
    value = os.environ.get(env_name)
    if value:
        return Path(value).expanduser()
    return Path.home() / fallback_suffix


def xdg_config_root() -> Path:
    root = _xdg_home("XDG_CONFIG_HOME", ".config") / "airfield"
    root.mkdir(parents=True, exist_ok=True)
    return root


def xdg_cache_root() -> Path:
    root = _xdg_home("XDG_CACHE_HOME", ".cache") / "airfield"
    root.mkdir(parents=True, exist_ok=True)
    return root


def xdg_data_root() -> Path:
    root = _xdg_home("XDG_DATA_HOME", ".local/share") / "airfield"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_airfield_runtime_dirs() -> None:
    xdg_config_root()
    xdg_cache_root()
    xdg_data_root()


def _airfield_source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _local_packages_root() -> Optional[Path]:
    candidate = _airfield_source_root().parent / "packages"
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def _cached_packages_root() -> Path:
    cache_root = xdg_cache_root() / "packages"
    if cache_root.exists():
        return cache_root

    cache_root.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth=1", PACKAGES_GITHUB_REPO, str(cache_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(
            f"Unable to locate a local packages repository at {_airfield_source_root().parent / 'packages'} "
            f"and cloning {PACKAGES_GITHUB_REPO} failed: {details}"
        )
    return cache_root


def packages_repo_root() -> Path:
    local = _local_packages_root()
    if local is not None:
        return local
    return _cached_packages_root()


def _load_yaml(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _save_yaml(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


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


def load_project_config(root: Path) -> dict:
    config_path = root / AIRFIELD_CONFIG
    data = _load_yaml(config_path)
    return data if isinstance(data, dict) else {}


def save_project_config(root: Path, data: dict):
    config_path = root / AIRFIELD_CONFIG
    _save_yaml(config_path, data)


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
    local_root = root / "dependencies" / target_device
    if local_root.exists() and any(local_root.glob("*.yaml")):
        return local_root
    return packages_repo_root() / target_device


def plans_dir(root: Path) -> Path:
    return root / "plans"
