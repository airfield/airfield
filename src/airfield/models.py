import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field


SUPPORTED_ROS_DISTROS = {"noetic", "humble", "jazzy"}


_DEP_SPEC_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*(.*)$")


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
    system: List[str] = Field(default_factory=list)
    user: List[str] = Field(default_factory=list)
    host_dependencies: List[HostDependency] = Field(default_factory=list)

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
