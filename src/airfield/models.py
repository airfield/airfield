import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator


# Single source of truth for supported ROS distributions. Adding a distro is
# one entry here: base images per arch (osrf desktop images are amd64-only,
# so arm64 uses the official ros-base tags) and the in-image build tool.
ROS_DISTROS = {
    "noetic": {
        "base_image": "ros:noetic-ros-base",
        "arm64_base_image": "ros:noetic-ros-base",
        "core_packages": ["python3-catkin-tools"],
    },
    "humble": {
        "base_image": "osrf/ros:humble-desktop",
        "arm64_base_image": "ros:humble-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
    "jazzy": {
        "base_image": "osrf/ros:jazzy-desktop",
        "arm64_base_image": "ros:jazzy-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
    "kilted": {
        "base_image": "osrf/ros:kilted-desktop",
        "arm64_base_image": "ros:kilted-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
    "rolling": {
        "base_image": "osrf/ros:rolling-desktop",
        "arm64_base_image": "ros:rolling-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
}

SUPPORTED_ROS_DISTROS = set(ROS_DISTROS)


_DEP_SPEC_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*(.*)$")

# An `apt:` entry is a package name, optionally pinned (`foo=1.2-3`) or targeted
# at a suite (`foo/noble-backports`). Shell variables are allowed and expanded at
# build time: `ros-$ROS_DISTRO-nav2-bringup` is how a manifest stays usable
# across distros. Spaces are not, which is what keeps a shell command out.
_APT_VAR = r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"
_APT_NAME = rf"(?:[a-z0-9+.\-]|{_APT_VAR})+"
_APT_SPEC_PATTERN = re.compile(rf"^{_APT_NAME}(?:=[A-Za-z0-9+.:~\-]+|/[A-Za-z0-9.\-]+)?$")


class DependencySpec(BaseModel):
    raw: str
    name: str
    constraint: Optional[str] = None


def parse_dependency_spec(raw: str) -> DependencySpec:
    spec = raw.strip()
    if not spec:
        raise ValueError("Dependency entries must be non-empty strings")

    match = _DEP_SPEC_PATTERN.match(spec)
    if match is None:
        raise ValueError(f"Invalid dependency spec '{raw}'")

    name = match.group(1).strip()
    constraint = match.group(2).strip() or None
    if constraint and not constraint.startswith(("==", ">=", "<=", "~=", ">", "<", "!=")):
        raise ValueError(
            f"Invalid constraint in dependency spec '{raw}'. "
            "Use semantic version operators like ==, >=, <=, >, <, ~=, !="
        )

    return DependencySpec(raw=spec, name=name, constraint=constraint)


class HostDependency(BaseModel):
    name: str
    min_version: Optional[str] = None
    max_version: Optional[str] = None
    install_hint: Optional[str] = None
    required: bool = True
    mode: str = "any"


class Dependency(BaseModel):
    name: str
    version: str = "1.0.0"
    ros_versions: List[str] = Field(default_factory=list)
    # Requirement specs (e.g. "numpy", "flask>=3"), NOT commands. Every package's
    # pip entries are collected into a single `pip install` so the resolver sees
    # them together; separate installs each solve in isolation and silently
    # overwrite each other's versions (pip exits 0 on that, so the build goes
    # green with a broken image). Anything pip cannot express as a plain
    # requirement -- a custom index, a conditional install -- still belongs in
    # `user`/`system`, at the cost of being outside the shared resolve.
    pip: List[str] = Field(default_factory=list)
    # Apt package names, likewise collected across every dependency into a single
    # `apt-get install`. Two wins over a command per manifest: apt refuses a set
    # it cannot satisfy instead of resolving a conflict by quietly REMOVING a
    # package an earlier manifest installed (it exits 0 either way, and the
    # result passes `apt-get check` because nothing is broken -- something is
    # just missing), and one index refresh replaces one per dependency.
    apt: List[str] = Field(default_factory=list)
    system: List[str] = Field(default_factory=list)
    user: List[str] = Field(default_factory=list)
    host_dependencies: List[HostDependency] = Field(default_factory=list)

    @field_validator("apt", mode="after")
    @classmethod
    def _validate_apt_specs(cls, specs: List[str]) -> List[str]:
        cleaned: List[str] = []
        for raw in specs:
            spec = str(raw).strip()
            if not spec:
                continue
            if not _APT_SPEC_PATTERN.match(spec):
                raise ValueError(
                    f"'apt' entries are package names, not shell commands (got '{spec}'). "
                    "Use e.g. 'libfoo-dev' or 'ros-$ROS_DISTRO-nav2-bringup'; "
                    "put real commands under 'system' or 'user'."
                )
            cleaned.append(spec)
        return cleaned

    @field_validator("pip", mode="after")
    @classmethod
    def _validate_pip_specs(cls, specs: List[str]) -> List[str]:
        cleaned: List[str] = []
        for raw in specs:
            spec = str(raw).strip()
            if not spec:
                continue
            # The common migration mistake is pasting the old command in here.
            # Catch it at load time rather than emitting a Dockerfile that tries
            # to `pip install python3 -m pip install foo`.
            if re.search(r"(^|\s)(pip|python3?|apt-get|sudo)(\s|$)", spec):
                raise ValueError(
                    f"'pip' entries are requirement specs, not shell commands (got '{spec}'). "
                    "Use e.g. 'numpy' or 'flask>=3'; put real commands under 'user' or 'system'."
                )
            cleaned.append(spec)
        return cleaned

    @classmethod
    def load(cls, path: Path) -> "Dependency":
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(**data)

class Package(BaseModel):
    name: str
    dependencies: List[str] = Field(default_factory=list)
    dependency_constraints: Dict[str, str] = Field(default_factory=dict)
    source_path: str = "src"
    ros_distro: Optional[str] = None
    base_image: Optional[str] = None
    # Extra args appended to the auto `colcon build` run by the container entry
    # wrapper, e.g. "--cmake-args -DCMAKE_BUILD_MODE=Hardware".
    colcon_args: Optional[str] = None
    default_workdir: Optional[str] = None
    devices: List[str] = Field(default_factory=list)
    group_add: List[str] = Field(default_factory=list)
    run: Dict[str, str] = Field(default_factory=dict)
    
    @classmethod
    def load(cls, path: Path) -> "Package":
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        raw_deps = data.get("dependencies", [])
        cleaned_deps: List[str] = []
        constraints: Dict[str, str] = {}
        for d in raw_deps:
            dep_spec = parse_dependency_spec(str(d))
            cleaned_deps.append(dep_spec.name)
            if dep_spec.constraint:
                constraints[dep_spec.name] = dep_spec.constraint

        data["dependencies"] = cleaned_deps
        data["dependency_constraints"] = constraints

        ros_distro = data.get("ros_distro")
        if isinstance(ros_distro, str):
            data["ros_distro"] = ros_distro.strip().lower() or None

        base_image = data.get("base_image")
        if isinstance(base_image, str):
            data["base_image"] = base_image.strip() or None

        colcon_args = data.get("colcon_args")
        if isinstance(colcon_args, str):
            data["colcon_args"] = colcon_args.strip() or None

        # Devices (e.g. /dev/ttyACM0) and supplementary group ids/names are
        # normalized to clean string lists (YAML may parse GIDs as ints).
        data["devices"] = [str(d).strip() for d in (data.get("devices") or []) if str(d).strip()]
        data["group_add"] = [str(g).strip() for g in (data.get("group_add") or []) if str(g).strip()]

        raw_run = data.get("run", {})
        if raw_run is None:
            raw_run = {}
        if not isinstance(raw_run, dict):
            raise ValueError("'run' must be a mapping of run-name to command string")

        cleaned_run: Dict[str, str] = {}
        for raw_name, raw_command in raw_run.items():
            name = str(raw_name).strip()
            command = str(raw_command).strip()
            if not name:
                raise ValueError("Run command names must be non-empty strings")
            if not command:
                raise ValueError(f"Run command '{name}' must have a non-empty command string")
            cleaned_run[name] = command
        data["run"] = cleaned_run

        raw_default_workdir = data.get("default_workdir")
        if isinstance(raw_default_workdir, str):
            data["default_workdir"] = raw_default_workdir.strip() or None

        return cls(**data)

class Pane(BaseModel):
    package: Optional[str] = None
    cmd: Optional[str] = None


class Window(BaseModel):
    name: str
    layout: str = "main-vertical"
    panes: List[Union[str, Pane, None]] = Field(default_factory=list)
    pre_window: Optional[str] = None


class Plan(BaseModel):
    name: str
    packages: List[str] = Field(default_factory=list)
    windows: List[Window] = Field(default_factory=list)
    pre_window: Optional[str] = None
    
    @classmethod
    def load(cls, path: Path) -> "Plan":
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(**data)
