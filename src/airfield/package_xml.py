"""Read what a ROS package needs straight from its package.xml.

Every ROS package already lists its dependencies in package.xml; colcon and
rosdep both read it. Airfield reads the same file on every command, so a
dependency added there reaches the image without a second copy of the list
in airfield.yaml that someone has to remember to update.
"""
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PACKAGE_XML = "package.xml"

# colcon drops COLCON_IGNORE into its own build/ install/ log/, and honours the
# two older markers for ament and catkin workspaces.
IGNORE_MARKERS = ("COLCON_IGNORE", "AMENT_IGNORE", "CATKIN_IGNORE")

# What a package needs to be built and to run. test_depend and doc_depend are
# left out on purpose: the image exists to build and run the package.
DEPENDENCY_TAGS = (
    "buildtool_depend",
    "buildtool_export_depend",
    "build_depend",
    "build_export_depend",
    "depend",
    "exec_depend",
    "run_depend",  # package format 1
)

_CONDITION_TOKEN = re.compile(
    r"""\s*(==|!=|>=|<=|>|<|\(|\)|\$[A-Za-z0-9_]+|"[^"]*"|'[^']*'|[A-Za-z0-9_.\-]+)"""
)
_COMPARISONS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


@dataclass(frozen=True)
class RosPackage:
    name: str
    path: Path  # the package.xml this was read from
    depends: Tuple[str, ...]


def find_package_xmls(source_root: Path) -> List[Path]:
    """The package.xml files colcon would pick up under ``source_root``.

    Follows colcon's crawl so both agree on what is in the workspace: a
    directory holding package.xml is a package and is not searched further,
    hidden directories are skipped, and so is any directory carrying an
    ignore marker. Symlinked directories are not followed: a link usually
    points outside what the container gets mounted, and this runs on the
    host for every command, where a link into a large tree would stall it.
    """
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(source_root):
        if any(marker in filenames for marker in IGNORE_MARKERS):
            dirnames[:] = []
            continue
        if PACKAGE_XML in filenames:
            found.append(Path(dirpath) / PACKAGE_XML)
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
    return found


def condition_context(ros_distro: Optional[str]) -> Dict[str, str]:
    """Variables a ``condition="..."`` attribute may refer to (REP 149)."""
    distro = (ros_distro or "").strip().lower()
    return {
        "ROS_VERSION": "1" if distro == "noetic" else "2",
        "ROS_DISTRO": distro,
        "ROS_PYTHON_VERSION": "3",
    }


def evaluate_condition(condition: Optional[str], context: Dict[str, str]) -> bool:
    """Evaluate a package.xml ``condition`` attribute.

    The grammar is small (REP 149): comparisons of ``$VARIABLES`` and literals
    joined by ``and`` / ``or``, with parentheses. Values compare as strings and
    an unset variable is the empty string, as in catkin_pkg. Raises ValueError
    on anything outside that grammar.
    """
    if condition is None or not condition.strip():
        return True

    tokens: List[str] = []
    pos = 0
    while pos < len(condition):
        match = _CONDITION_TOKEN.match(condition, pos)
        if match is None:
            if condition[pos:].strip():
                raise ValueError(f"unexpected text in condition: {condition[pos:]!r}")
            break
        tokens.append(match.group(1))
        pos = match.end()

    index = 0

    def peek() -> Optional[str]:
        return tokens[index] if index < len(tokens) else None

    def take() -> str:
        nonlocal index
        if index >= len(tokens):
            raise ValueError(f"condition ends early: {condition!r}")
        token = tokens[index]
        index += 1
        return token

    def operand() -> str:
        token = take()
        if token in _COMPARISONS or token in {"(", ")", "and", "or"}:
            raise ValueError(f"expected a value, got {token!r} in condition {condition!r}")
        if token.startswith("$"):
            return context.get(token[1:], "")
        if token[0] in "\"'":
            return token[1:-1]
        return token

    def comparison() -> bool:
        if peek() == "(":
            take()
            value = either()
            if take() != ")":
                raise ValueError(f"missing ')' in condition {condition!r}")
            return value
        left = operand()
        operator = take()
        if operator not in _COMPARISONS:
            raise ValueError(f"expected a comparison, got {operator!r} in condition {condition!r}")
        return _COMPARISONS[operator](left, operand())

    def both() -> bool:
        value = comparison()
        while peek() == "and":
            take()
            value = comparison() and value
        return value

    def either() -> bool:
        value = both()
        while peek() == "or":
            take()
            value = both() or value
        return value

    result = either()
    if peek() is not None:
        raise ValueError(f"unexpected {peek()!r} in condition {condition!r}")
    return result


def read_package_xml(path: Path, ros_distro: Optional[str] = None) -> RosPackage:
    """Parse one package.xml: its name and its build/run dependencies.

    Raises ValueError when the file is not a readable ROS package manifest.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    name = (root.findtext("name") or "").strip()
    if root.tag != "package" or not name:
        raise ValueError(f"{path} is not a ROS package manifest (no <package><name>)")

    context = condition_context(ros_distro)
    depends: List[str] = []
    for node in root:
        if node.tag not in DEPENDENCY_TAGS:
            continue
        key = (node.text or "").strip()
        if not key or key in depends:
            continue
        try:
            wanted = evaluate_condition(node.get("condition"), context)
        except ValueError:
            # A condition Airfield cannot read: keep the dependency. One
            # package too many in the image beats one missing at build time.
            wanted = True
        if wanted:
            depends.append(key)

    return RosPackage(name=name, path=path, depends=tuple(depends))


def read_ros_packages(
    source_root: Path, ros_distro: Optional[str] = None
) -> Tuple[List[RosPackage], List[str]]:
    """Every ROS package under ``source_root``, plus a problem line per
    package.xml that could not be read (colcon reports those in full when it
    builds; here they must not stop an unrelated command)."""
    packages: List[RosPackage] = []
    problems: List[str] = []
    for path in find_package_xmls(source_root):
        try:
            packages.append(read_package_xml(path, ros_distro))
        except ValueError as exc:
            problems.append(str(exc))
    return packages, problems
