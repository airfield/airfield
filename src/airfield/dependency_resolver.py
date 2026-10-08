"""Work out what a package's image has to contain, and where each part comes from.

Two files can ask for a dependency:

* ``package.xml``, which every ROS package already has and which colcon and
  rosdep read. Airfield reads it on every command, so a dependency added
  there reaches the image with nothing to repeat anywhere else.
* ``airfield.yaml`` ``dependencies:``, for what package.xml does not say: a
  tool a plan runs in this container, a driver chosen at launch time, a
  library an upstream package.xml forgot to list.

Each name is then resolved, and the first match wins:

1. a ROS package in this package's own source tree needs nothing installed;
2. a dependency manifest in the search paths: the recipe for installing it,
   and the way to override the rule in step 4;
3. a peer package in the project, whose source is mounted and built alongside;
4. rosdep's lookup table (see ``rosdep_table``): what the name is called on
   the image's Ubuntu release, for the names rosdep knows;
5. the apt package the name conventionally maps to (see
   ``conventional_apt_package``), for a name rosdep does not know or when its
   table could not be fetched.

Steps 4 and 5 are what keep manifests for the cases that need one. Most names
are plain ROS packages, and for those a manifest could only repeat the rule
(``nav2_util`` -> ``ros-<distro>-nav2-util``). The table adds the system
libraries whose key is not their apt name (``eigen`` -> ``libeigen3-dev``,
``cmake`` -> ``cmake`` and not a ROS package of that name). A manifest is for
what neither can say: a source build pinned to a commit, an install that
differs per machine, a deliberate choice other than rosdep's.

Both point at the ROS apt repository and at what a ROS distribution was
released for, so they apply only in a ROS image. In a package without
``ros_distro`` every name still needs a manifest. A name from airfield.yaml
that no step covers is an error. A package.xml entry that cannot be a package
name at all is reported and ignored, because upstream manifests are not always
clean and the package built without that entry before.
"""
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from airfield import rosdep_table
from airfield.config import AIRFIELD_CONFIG, packages_dir
from airfield.models import Dependency, Package
from airfield.package_xml import read_ros_packages


RECIPE = "recipe"        # a dependency manifest says how to install it
PEER = "peer"            # another package in the project, built from source
WORKSPACE = "workspace"  # source this package already brings into the workspace
INFERRED = "inferred"    # no recipe; translated by rosdep's table or the naming rule
SKIPPED = "skipped"      # listed under skip_dependencies
INVALID = "invalid"      # a package.xml entry that cannot be a package name
MISSING = "missing"      # named in airfield.yaml, and no step above covers it

# How an INFERRED name was translated (Dependency.inferred_via).
VIA_ROSDEP = rosdep_table.VIA_ROSDEP        # a rule in rosdep's table
VIA_ROS_INDEX = rosdep_table.VIA_ROS_INDEX  # a package released for the ROS distribution
VIA_RULE = "rule"                           # neither: a guess from the shape of the name

_ROS_PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_DEBIAN_PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.\-]+$")


@dataclass
class Resolved:
    name: str
    origin: Path  # the airfield.yaml or package.xml that asked for it first
    kind: str
    dependency: Optional[Dependency] = None  # RECIPE and INFERRED
    location: Optional[Path] = None  # the manifest (RECIPE) or the peer's directory (PEER)
    # Every file that asked for it, `origin` included.
    requested_by: List[Path] = field(default_factory=list)


@dataclass
class DependencyPlan:
    entries: List[Resolved] = field(default_factory=list)
    # (package name, source directory) of each peer, in the order found.
    peers: List[Tuple[str, Path]] = field(default_factory=list)
    # package.xml files that could not be read.
    problems: List[str] = field(default_factory=list)

    @property
    def dependencies(self) -> List[Dependency]:
        return [entry.dependency for entry in self.entries if entry.dependency is not None]

    def of_kind(self, kind: str) -> List[Resolved]:
        return [entry for entry in self.entries if entry.kind == kind]

    def warnings(self) -> List[str]:
        lines = list(self.problems)
        for entry in self.of_kind(INVALID):
            lines.append(
                f"{entry.origin} lists '{entry.name}', which is not a valid package name. "
                "Ignoring it; fix that line, or add what it meant to airfield.yaml."
            )
        return lines


def conventional_apt_package(key: str) -> Optional[str]:
    """The apt package a name stands for when no recipe says otherwise.

    These are the two naming rules rosdep's database is built on: a ROS
    package ``foo_bar`` is released as ``ros-<distro>-foo-bar``, and a system
    dependency is keyed by its Debian package name (``python3-numpy``,
    ``libopencv-dev``). ROS names carry no hyphen and Debian names no
    underscore, which is what tells the two apart. Returns None for a name
    that fits neither.

    The rules cannot know rosdep's exceptions: a system key with no hyphen
    (``boost``, ``eigen``, ``cmake``) reads as a ROS name. That is what
    rosdep's table is consulted for first; this is the fallback for a name the
    table does not have, and for when there is no table.
    """
    if _ROS_PACKAGE_NAME.match(key):
        return rosdep_table.ros_apt_package(key)
    if _DEBIAN_PACKAGE_NAME.match(key):
        return key
    return None


def find_manifest(name: str, search_paths: List[Path]) -> Optional[Path]:
    for search_path in search_paths:
        candidate = search_path / f"{name}.yaml"
        if candidate.exists():
            return candidate
    return None


def _ros_package_index(project_root: Path, ros_distro: str) -> Dict[str, Path]:
    """ROS package name -> the project package whose source tree holds it.

    A project package is not always named after the ROS packages inside it
    (one repository often carries ``foo`` and ``foo_msgs``), so a package.xml
    entry has to be matched against what the source trees actually contain.
    """
    index: Dict[str, Path] = {}
    root = packages_dir(project_root)
    if not root.is_dir():
        return index
    for child in sorted(root.iterdir()):
        config = child / AIRFIELD_CONFIG
        if not config.exists():
            continue
        try:
            peer = Package.load(config)
        except Exception:
            # A package whose airfield.yaml does not load cannot be offered as
            # a provider. The error surfaces when that package is used itself.
            continue
        ros_packages, _ = read_ros_packages(child / peer.source_path, ros_distro)
        for ros_package in ros_packages:
            index.setdefault(ros_package.name, child)
    return index


def resolve_dependencies(
    pkg: Package,
    pkg_dir: Path,
    source_root: Path,
    project_root: Optional[Path],
    search_paths: List[Path],
) -> DependencyPlan:
    plan = DependencyPlan()
    pkg_dir = pkg_dir.resolve()
    # package.xml only means something in a ROS image.
    ros_distro = pkg.ros_distro

    # (name, the file that asked for it, whether that file is a package.xml)
    queue: Deque[Tuple[str, Path, bool]] = deque(
        (name, pkg_dir / AIRFIELD_CONFIG, False) for name in pkg.dependencies
    )

    own_names: Set[str] = set()
    if ros_distro:
        own_packages, problems = read_ros_packages(source_root, ros_distro)
        plan.problems.extend(problems)
        own_names = {ros_package.name for ros_package in own_packages}
        for ros_package in own_packages:
            queue.extend((key, ros_package.path, True) for key in ros_package.depends)

    ros_index: Optional[Dict[str, Path]] = None

    def peer_dir_for(name: str) -> Optional[Path]:
        nonlocal ros_index
        if project_root is None:
            return None
        candidate = packages_dir(project_root) / name
        if (candidate / AIRFIELD_CONFIG).exists():
            return candidate
        if not ros_distro:
            return None
        if ros_index is None:
            ros_index = _ros_package_index(project_root, ros_distro)
        return ros_index.get(name)

    resolved: Dict[str, Resolved] = {}
    visited_peers: Set[Path] = set()

    def record(name: str, origin: Path, kind: str, **details) -> None:
        resolved[name] = Resolved(name, origin, kind, requested_by=[origin], **details)
        plan.entries.append(resolved[name])

    table: Optional[rosdep_table.NameTable] = None
    table_asked = False

    def translate(name: str, origin: Path) -> Optional[Dependency]:
        """What to install for a name nothing in the project provides."""
        nonlocal table, table_asked
        if not table_asked:
            # Only now, so a package whose names are all manifests and peers
            # never waits for the table to be fetched.
            table_asked = True
            table, problem = rosdep_table.load(ros_distro)
            if problem:
                plan.problems.append(problem)
        hit = table.lookup(name) if table is not None else None
        if hit is not None:
            try:
                return Dependency(
                    name=name,
                    inferred_from=str(origin),
                    inferred_via=hit.via,
                    **{hit.installer: list(hit.packages)},
                )
            except ValidationError:
                # A rule Airfield cannot express as plain package names; treat
                # the name as one the table does not have.
                pass
        apt_package = conventional_apt_package(name)
        if apt_package is None:
            return None
        return Dependency(name=name, apt=[apt_package], inferred_from=str(origin), inferred_via=VIA_RULE)

    while queue:
        name, origin, from_package_xml = queue.popleft()
        if name in resolved:
            if origin not in resolved[name].requested_by:
                resolved[name].requested_by.append(origin)
            continue

        if from_package_xml:
            # The name is about to be used as a file name (a manifest) and as a
            # folder name (a peer package, whose source gets mounted into the
            # container). package.xml may come from someone else's repository,
            # so a name that is really a path must never get that far.
            # airfield.yaml names cannot contain a separator (see
            # parse_dependency_spec).
            if "/" in name or "\\" in name or name in {".", ".."}:
                record(name, origin, INVALID)
                continue
            if name in pkg.skip_dependencies:
                record(name, origin, SKIPPED)
                continue
            if name in own_names:
                record(name, origin, WORKSPACE)
                continue

        manifest = find_manifest(name, search_paths)
        if manifest is not None:
            record(name, origin, RECIPE, dependency=Dependency.load(manifest), location=manifest)
            continue

        peer_dir = peer_dir_for(name)
        names_this_folder = peer_dir is not None and peer_dir.resolve() == pkg_dir
        if peer_dir is not None and not names_this_folder:
            peer_dir = peer_dir.resolve()
            record(name, origin, PEER, location=peer_dir)
            if peer_dir in visited_peers:
                continue
            visited_peers.add(peer_dir)

            peer = Package.load(peer_dir / AIRFIELD_CONFIG)
            peer_source = (peer_dir / peer.source_path).resolve()
            if all(peer.name != known for known, _ in plan.peers):
                plan.peers.append((peer.name, peer_source))
            # The peer is compiled in this image, so the image needs what the
            # peer needs: its airfield.yaml list and its own package.xml.
            queue.extend((dep, peer_dir / AIRFIELD_CONFIG, False) for dep in peer.dependencies)
            if ros_distro:
                peer_packages, problems = read_ros_packages(peer_source, ros_distro)
                plan.problems.extend(problems)
                peer_names = {ros_package.name for ros_package in peer_packages}
                for ros_package in peer_packages:
                    queue.extend(
                        (key, ros_package.path, True)
                        for key in ros_package.depends
                        if key not in peer_names and key not in peer.skip_dependencies
                    )
            continue

        # Source this package brings itself needs nothing installed: a ROS
        # package in its tree, or its own folder name when the folder holds
        # ROS source. A tool package named after the one thing it installs
        # (rviz2, foxglove_bridge) holds none, so its own name goes on to the
        # naming rule like any other. Outside a ROS image there is no rule to
        # go on to, and a package naming itself stays the no-op it always was.
        if name in own_names or (names_this_folder and (own_names or not ros_distro)):
            record(name, origin, WORKSPACE)
            continue

        # Nothing in the project provides it and no recipe says how to install
        # it, so translate the name: rosdep's table, then the naming rule,
        # whichever file asked. Both describe a ROS image, so neither applies
        # to a package without ros_distro.
        translated = translate(name, origin) if ros_distro else None
        if translated is not None:
            record(name, origin, INFERRED, dependency=translated)
        elif from_package_xml:
            record(name, origin, INVALID)
        else:
            record(name, origin, MISSING)

    return plan
