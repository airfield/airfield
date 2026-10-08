"""rosdep's lookup table, read straight from its data files.

A package.xml names what the package needs by rosdep key: ``rclcpp``,
``eigen``, ``python3-numpy``. rosdep turns each key into what the system
package manager calls it. Everything it knows for that is data: three YAML
files of rules, plus the list of packages released for each ROS distribution.
Airfield reads that data itself, on the host. Nothing is installed or run
inside the image for it, and once the files are cached, translating a name
needs no network.

The cache is one file per ROS distribution under
``$XDG_CACHE_HOME/airfield/rosdep``. It is fetched the first time a name
needs it and after that only on request
(``airfield package dependencies pull``): a table that changed by itself
would change what a project's images contain from one day to the next.
"""
import fcntl
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

import yaml

from airfield.config import xdg_cache_root


# Overridable so a mirror, a fork, or an air-gapped site (``file:///...``) can
# serve the same layout as github.com/ros/rosdistro.
DEFAULT_ROSDISTRO_URL = "https://raw.githubusercontent.com/ros/rosdistro/master"
RULE_FILES = ("rosdep/base.yaml", "rosdep/python.yaml", "rosdep/ruby.yaml")

# Airfield images are Ubuntu images, and the release of Ubuntu is whichever
# one the ROS distribution is released for (its distribution.yaml says so).
OS_NAME = "ubuntu"

# What a dependency can be installed with here. rosdep knows more installers
# (gem, snap, source, npm); a rule that needs one of those is left to a
# dependency manifest.
SUPPORTED_INSTALLERS = ("apt", "pip")
_INSTALLER_KEYS = ("apt", "pip", "gem", "snap", "source", "npm")

VIA_ROSDEP = "rosdep"        # a rule in rosdep's YAML files
VIA_ROS_INDEX = "ros-index"  # a package released for the ROS distribution

_FORMAT = 1
_DOWNLOAD_TIMEOUT = 8  # seconds per file
_RETRY_AFTER = 600     # seconds to leave the network alone after a failed fetch


@dataclass(frozen=True)
class Translation:
    installer: str              # "apt" or "pip"
    packages: Tuple[str, ...]   # may be empty: the name needs nothing installed
    via: str                    # VIA_ROSDEP or VIA_ROS_INDEX


def ros_apt_package(name: str) -> str:
    """The apt package a ROS package is released as. ``$ROS_DISTRO`` is left
    for the image build to expand, like in a dependency manifest."""
    return "ros-$ROS_DISTRO-" + name.lower().replace("_", "-")


class NameTable:
    def __init__(
        self,
        ros_distro: str,
        codename: str,
        rules: Dict[str, List],
        released: FrozenSet[str],
        fetched: float,
    ):
        self.ros_distro = ros_distro
        self.codename = codename
        self.rules = rules
        self.released = released
        self.fetched = fetched

    def lookup(self, name: str) -> Optional[Translation]:
        # Same precedence as rosdep: a rule in the YAML files first, then the
        # packages released for the distribution.
        rule = self.rules.get(name)
        if rule is not None:
            return Translation(rule[0], tuple(rule[1]), VIA_ROSDEP)
        if name in self.released:
            return Translation("apt", (ros_apt_package(name),), VIA_ROS_INDEX)
        return None

    def describe(self) -> str:
        day = time.strftime("%Y-%m-%d", time.localtime(self.fetched))
        return f"rosdep data for {self.ros_distro} on Ubuntu {self.codename}, fetched {day}"


def _select_rule(data, codename: str) -> Optional[Tuple[str, List[str]]]:
    """Pick the rule for Ubuntu ``codename`` out of one key's entry.

    Mirrors rosdep's own selection: the OS (or ``*``), then an installer if
    the entry names one, else the OS release (or ``*``) and then an installer
    again. What is left is a package list, a string, or ``{packages: [...]}``.
    """
    if not isinstance(data, dict):
        return None
    if OS_NAME in data:
        data = data[OS_NAME]
    elif "*" in data:
        data = data["*"]
    else:
        return None

    installer = "apt"

    def pick_installer(entry: dict):
        for key in _INSTALLER_KEYS:
            if key in entry:
                return key, entry[key]
        return None

    if isinstance(data, dict):
        picked = pick_installer(data)
        if picked is not None:
            installer, data = picked
        else:
            if codename in data:
                data = data[codename]
            elif "*" in data:
                data = data["*"]
            else:
                return None
            if isinstance(data, dict):
                picked = pick_installer(data)
                if picked is not None:
                    installer, data = picked

    if data is None:
        # An explicit null: the key exists but not on this release.
        return None
    if isinstance(data, dict):
        data = data.get("packages", [])
    if isinstance(data, str):
        data = data.split()
    if not isinstance(data, list) or installer not in SUPPORTED_INSTALLERS:
        return None
    return installer, [str(item) for item in data]


def compile_rules(tables: List[dict], codename: str) -> Dict[str, List]:
    """name -> [installer, [packages]] for every key that has a usable rule."""
    rules: Dict[str, List] = {}
    for table in tables:
        for key, entry in (table or {}).items():
            if key in rules:
                continue  # first file wins, as in rosdep's sources list
            selected = _select_rule(entry, codename)
            if selected is not None:
                rules[str(key)] = [selected[0], selected[1]]
    return rules


def read_distribution(distribution: dict) -> Tuple[Optional[str], List[str]]:
    """(Ubuntu release, released package names) from a distribution.yaml."""
    platforms = (distribution.get("release_platforms") or {}).get(OS_NAME) or []
    codename = str(platforms[0]) if platforms else None
    released = set()
    for repository, info in (distribution.get("repositories") or {}).items():
        release = (info or {}).get("release")
        if release:
            # A repository that lists no packages releases one, named after it.
            released.update(str(name) for name in (release.get("packages") or [repository]))
    return codename, sorted(released)


def _cache_dir() -> Path:
    path = xdg_cache_root() / "rosdep"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_file(ros_distro: str) -> Path:
    return _cache_dir() / f"{ros_distro}.json"


def _source_url() -> str:
    return (os.environ.get("AIRFIELD_ROSDISTRO_URL") or DEFAULT_ROSDISTRO_URL).rstrip("/")


def _fetch_yaml(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "airfield"})
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:
        text = response.read().decode("utf-8")
    data = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    if not isinstance(data, dict):
        raise ValueError(f"{url} is not a YAML mapping")
    return data


def _download(ros_distro: str) -> dict:
    base = _source_url()
    codename, released = read_distribution(_fetch_yaml(f"{base}/{ros_distro}/distribution.yaml"))
    if codename is None:
        raise ValueError(f"{ros_distro}/distribution.yaml names no Ubuntu release")
    tables = [_fetch_yaml(f"{base}/{name}") for name in RULE_FILES]
    return {
        "format": _FORMAT,
        "ros_distro": ros_distro,
        "codename": codename,
        "fetched": time.time(),
        "source": base,
        "rules": compile_rules(tables, codename),
        "released": released,
    }


def _read_cache(ros_distro: str) -> Optional[NameTable]:
    path = _cache_file(ros_distro)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != _FORMAT:
        return None
    try:
        return NameTable(
            ros_distro=ros_distro,
            codename=str(data["codename"]),
            rules=dict(data["rules"]),
            released=frozenset(data["released"]),
            fetched=float(data.get("fetched", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_cache(ros_distro: str, data: dict) -> None:
    path = _cache_file(ros_distro)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees half a file


def enabled() -> bool:
    value = (os.environ.get("AIRFIELD_ROSDEP_TABLE") or "").strip().lower()
    return value not in {"off", "0", "false", "no", "none"}


def _may_download() -> bool:
    # Shell completion resolves packages on every <TAB>; it must never wait on
    # the network. Click sets _<PROG>_COMPLETE while completing.
    return not any(key.startswith("_") and key.endswith("_COMPLETE") for key in os.environ)


def _fetch(ros_distro: str, force: bool) -> Tuple[Optional[NameTable], Optional[str]]:
    """Download and cache the table. Returns (table, problem)."""
    failed_marker = _cache_dir() / f"{ros_distro}.failed"
    # Panes of one plan start together; let one of them fetch and the rest
    # read its result instead of all downloading at once.
    with open(_cache_dir() / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not force:
            cached = _read_cache(ros_distro)
            if cached is not None:
                return cached, None
            try:
                if time.time() - failed_marker.stat().st_mtime < _RETRY_AFTER:
                    return None, failed_marker.read_text(encoding="utf-8").strip() or None
            except OSError:
                pass
        try:
            _write_cache(ros_distro, _download(ros_distro))
        except Exception as exc:  # network, YAML, disk: all mean "no table today"
            problem = (
                f"rosdep's table for '{ros_distro}' could not be fetched from {_source_url()} "
                f"({type(exc).__name__}: {exc}). Names without a manifest are translated by the "
                "naming rule alone until it can be: airfield package dependencies pull"
            )
            try:
                failed_marker.write_text(problem, encoding="utf-8")
            except OSError:
                pass
            return _read_cache(ros_distro), problem
        try:
            failed_marker.unlink()
        except OSError:
            pass
        return _read_cache(ros_distro), None


_loaded: Dict[str, Tuple[Optional[NameTable], Optional[str]]] = {}


def load(ros_distro: str) -> Tuple[Optional[NameTable], Optional[str]]:
    """The table for a ROS distribution, fetched once if it is not cached.

    Returns (table, problem). The table is None when it is switched off
    (``AIRFIELD_ROSDEP_TABLE=off``) or could not be fetched; ``problem`` then
    says why, if there is something worth telling the user.
    """
    if not enabled():
        return None, None
    if ros_distro in _loaded:
        return _loaded[ros_distro]
    try:
        table = _read_cache(ros_distro)
        problem = None
        if table is None and _may_download():
            table, problem = _fetch(ros_distro, force=False)
    except OSError as exc:
        # No usable cache directory (read-only home, full disk). Translating
        # by rule still works, so this must not stop the command.
        table, problem = None, _unusable_cache(exc)
    if table is not None or _may_download():
        # Not remembered while completing, so the next real command still tries.
        _loaded[ros_distro] = (table, problem)
    return table, problem


def _unusable_cache(exc: OSError) -> str:
    return (
        f"rosdep's table cannot be kept on this machine ({exc}). Names without a manifest "
        "are translated by the naming rule alone."
    )


def cached_distros() -> List[str]:
    try:
        return sorted(path.stem for path in _cache_dir().glob("*.json"))
    except OSError:
        return []


def refresh(ros_distro: str) -> Tuple[Optional[NameTable], Optional[str]]:
    """Fetch the table again, replacing the cached copy."""
    _loaded.pop(ros_distro, None)
    try:
        result = _fetch(ros_distro, force=True)
    except OSError as exc:
        result = (None, _unusable_cache(exc))
    _loaded[ros_distro] = result
    return result
