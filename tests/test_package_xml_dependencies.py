"""package.xml is read on every command, so a dependency declared there
reaches the image without being repeated in airfield.yaml."""
from pathlib import Path

import pytest
import yaml

from airfield.builder import Builder
from airfield.dependency_resolver import (
    INFERRED,
    INVALID,
    PEER,
    RECIPE,
    SKIPPED,
    VIA_ROS_INDEX,
    VIA_ROSDEP,
    VIA_RULE,
    WORKSPACE,
    conventional_apt_package,
    resolve_dependencies,
)
from airfield.main import app
from airfield.models import Dependency, Package
from airfield.package_xml import (
    condition_context,
    evaluate_condition,
    find_package_xmls,
    read_package_xml,
    read_ros_packages,
)


def _package_xml(directory: Path, name: str, body: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "package.xml"
    path.write_text(
        f"""<?xml version="1.0"?>
<package format="3">
  <name>{name}</name>
  <version>0.0.1</version>
  <description>test</description>
  <maintainer email="t@t.io">t</maintainer>
  <license>MIT</license>
{body}
</package>
""",
        encoding="utf-8",
    )
    return path


def _depends(*names: str) -> str:
    return "\n".join(f"  <depend>{name}</depend>" for name in names)


def _manifest(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(f"name: {name}\nversion: 1.0.0\n{body}", encoding="utf-8")


@pytest.fixture
def project(tmp_path, mocker, monkeypatch):
    """A project on disk with one ROS package, `main`, and a shared manifest
    repository holding a recipe for nav2_msgs. Commands run from outside it."""
    shared = tmp_path / "shared"
    _manifest(shared / "xplatform", "nav2_msgs", "apt:\n  - ros-$ROS_DISTRO-nav2-msgs\n")
    mocker.patch("airfield.config.packages_repo_root", return_value=shared)

    proj = tmp_path / "proj"
    main = proj / "packages" / "main"
    main.mkdir(parents=True)
    (proj / "airfield.yaml").write_text("kind: project\nname: proj\n", encoding="utf-8")
    (main / "airfield.yaml").write_text(
        "kind: package\nname: main\nsource_path: .\nros_distro: jazzy\n", encoding="utf-8"
    )
    _package_xml(main, "main")

    monkeypatch.chdir(tmp_path)
    return proj


def _resolve(project: Path, name: str = "main"):
    from airfield.cli.package_exec import resolve_package_context

    return resolve_package_context(str(project / "packages" / name), target_device="x86_64")


def _plan(project: Path, name: str = "main"):
    from airfield.config import dependency_search_paths

    pkg_dir = project / "packages" / name
    pkg = Package.load(pkg_dir / "airfield.yaml")
    return resolve_dependencies(
        pkg, pkg_dir, (pkg_dir / pkg.source_path).resolve(), project, dependency_search_paths(project, "x86_64")
    )


# --- reading package.xml -------------------------------------------------------

def test_crawl_matches_colcon(tmp_path):
    """One package per directory holding package.xml, nothing below it, and no
    hidden or ignore-marked directories."""
    _package_xml(tmp_path / "a", "a")
    _package_xml(tmp_path / "a" / "nested", "never_seen")
    _package_xml(tmp_path / "group" / "b", "b")
    _package_xml(tmp_path / ".hidden" / "c", "c")
    _package_xml(tmp_path / "build" / "d", "d")
    (tmp_path / "build" / "COLCON_IGNORE").write_text("", encoding="utf-8")

    found = [p.parent.name for p in find_package_xmls(tmp_path)]
    assert found == ["a", "b"]


def test_crawl_stops_at_a_top_level_package(tmp_path):
    _package_xml(tmp_path, "top")
    _package_xml(tmp_path / "sub", "sub")
    assert find_package_xmls(tmp_path) == [tmp_path / "package.xml"]


def test_crawl_does_not_follow_symlinked_directories(tmp_path):
    outside = tmp_path / "outside"
    _package_xml(outside / "linked", "linked")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link").symlink_to(outside, target_is_directory=True)
    assert find_package_xmls(tree) == []


def test_reads_build_and_run_tags_but_not_test_or_doc(tmp_path):
    path = _package_xml(
        tmp_path,
        "p",
        """  <buildtool_depend>ament_cmake</buildtool_depend>
  <build_depend>a</build_depend>
  <build_export_depend>b</build_export_depend>
  <buildtool_export_depend>c</buildtool_export_depend>
  <depend>d</depend>
  <exec_depend>e</exec_depend>
  <run_depend>f</run_depend>
  <depend>d</depend>
  <test_depend>ament_lint_auto</test_depend>
  <doc_depend>doxygen</doc_depend>
  <member_of_group>rosidl_interface_packages</member_of_group>""",
    )
    ros_package = read_package_xml(path, "jazzy")
    assert ros_package.name == "p"
    assert ros_package.depends == ("ament_cmake", "a", "b", "c", "d", "e", "f")


def test_conditional_dependencies_follow_the_ros_distro(tmp_path):
    path = _package_xml(
        tmp_path,
        "p",
        """  <depend condition="$ROS_VERSION == 1">roscpp</depend>
  <depend condition="$ROS_VERSION == 2">rclcpp</depend>
  <depend condition="$ROS_DISTRO == humble or $ROS_DISTRO == jazzy">newer_only</depend>""",
    )
    assert read_package_xml(path, "jazzy").depends == ("rclcpp", "newer_only")
    assert read_package_xml(path, "noetic").depends == ("roscpp",)


@pytest.mark.parametrize(
    "condition, expected",
    [
        (None, True),
        ("", True),
        ("$ROS_VERSION == 2", True),
        ("$ROS_VERSION != 2", False),
        ("$ROS_DISTRO == 'jazzy'", True),
        ('$ROS_DISTRO == "humble"', False),
        ("$ROS_VERSION == 1 or $ROS_DISTRO == jazzy", True),
        ("$ROS_VERSION == 1 and $ROS_DISTRO == jazzy", False),
        ("$ROS_VERSION == 1 or $ROS_VERSION == 2 and $ROS_DISTRO == humble", False),
        ("($ROS_VERSION == 1 or $ROS_VERSION == 2) and $ROS_DISTRO == jazzy", True),
        ("$ROS_DISTRO >= humble", True),
        ("$UNSET_VARIABLE == ''", True),
    ],
)
def test_evaluate_condition(condition, expected):
    assert evaluate_condition(condition, condition_context("jazzy")) is expected


@pytest.mark.parametrize("condition", ["$ROS_VERSION ==", "== 2", "$ROS_VERSION 2", "($ROS_VERSION == 2", "$A == 1 %"])
def test_evaluate_condition_rejects_what_it_cannot_read(condition):
    with pytest.raises(ValueError):
        evaluate_condition(condition, condition_context("jazzy"))


def test_unreadable_condition_keeps_the_dependency(tmp_path):
    path = _package_xml(tmp_path, "p", '  <depend condition="$ROS_VERSION ===">kept</depend>')
    assert read_package_xml(path, "jazzy").depends == ("kept",)


def test_broken_package_xml_is_reported_not_fatal(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "package.xml").write_text("<package><name>bad</name>", encoding="utf-8")
    _package_xml(tmp_path / "good", "good", _depends("std_msgs"))

    packages, problems = read_ros_packages(tmp_path, "jazzy")
    assert [p.name for p in packages] == ["good"]
    assert len(problems) == 1 and "bad/package.xml" in problems[0]


# --- names with no manifest ----------------------------------------------------

@pytest.mark.parametrize(
    "key, apt_package",
    [
        ("nav2_msgs", "ros-$ROS_DISTRO-nav2-msgs"),
        ("rclcpp", "ros-$ROS_DISTRO-rclcpp"),
        ("python3-numpy", "python3-numpy"),
        ("libusb-1.0-0-dev", "libusb-1.0-0-dev"),
        ("OpenCV 3.4.12", None),
        ("mixed_under-score", None),
        ("", None),
    ],
)
def test_conventional_apt_package(key, apt_package):
    assert conventional_apt_package(key) == apt_package


def test_conventional_names_pass_the_apt_field_validation():
    """Whatever the convention produces has to be a legal `apt:` entry."""
    for key in ("nav2_msgs", "Mixed_Case", "python3-numpy", "g++-13", "libfoo1.2"):
        Dependency(name=key, apt=[conventional_apt_package(key)])


# --- the reported scenario -----------------------------------------------------

def test_dependency_added_only_to_package_xml_reaches_the_image(project):
    """A developer adds <depend>nav2_msgs</depend> and does not touch
    airfield.yaml. The next build installs it."""
    main = project / "packages" / "main"
    assert "nav2-msgs" not in Builder(*_builder_args(project)).generate_dockerfile()

    _package_xml(main, "main", _depends("nav2_msgs"))

    assert yaml.safe_load((main / "airfield.yaml").read_text()).get("dependencies") is None
    dockerfile = Builder(*_builder_args(project)).generate_dockerfile()
    assert "apt-get install -y ros-$ROS_DISTRO-nav2-msgs" in dockerfile


def _builder_args(project: Path):
    _, pkg, deps, _ = _resolve(project)
    return pkg, deps, "x86_64"


def test_name_with_no_manifest_is_installed_by_its_conventional_name(project):
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("foo_msgs", "python3-requests"))

    _, pkg, deps, _ = _resolve(project)

    by_name = {dep.name: dep for dep in deps}
    assert by_name["foo_msgs"].apt == ["ros-$ROS_DISTRO-foo-msgs"]
    assert by_name["python3-requests"].apt == ["python3-requests"]
    assert by_name["foo_msgs"].inferred_from == str(main / "package.xml")
    dockerfile = Builder(pkg, deps, "x86_64").generate_dockerfile()
    assert "apt-get install -y ros-$ROS_DISTRO-foo-msgs python3-requests" in dockerfile


def test_manifest_overrides_the_convention(project):
    """A recipe always wins: here rosbag2_py brings the whole rosbag2 stack,
    not the one package the name alone would give."""
    main = project / "packages" / "main"
    _manifest(project / "dependencies" / "xplatform", "rosbag2_py", "apt:\n  - ros-$ROS_DISTRO-rosbag2\n")
    _package_xml(main, "main", _depends("rosbag2_py"))

    _, _, deps, _ = _resolve(project)

    assert [(d.name, d.apt, d.pip, d.inferred_from) for d in deps] == [
        ("rosbag2_py", ["ros-$ROS_DISTRO-rosbag2"], [], None)
    ]


def test_airfield_yaml_entries_come_first_and_are_not_duplicated(project):
    """Names listed in both files resolve once, in airfield.yaml's order, so an
    existing package's Dockerfile (and its layer cache) does not change."""
    main = project / "packages" / "main"
    _manifest(project / "dependencies" / "xplatform", "std_msgs", "apt:\n  - ros-$ROS_DISTRO-std-msgs\n")
    (main / "airfield.yaml").write_text(
        "kind: package\nname: main\nsource_path: .\nros_distro: jazzy\ndependencies:\n  - std_msgs\n  - nav2_msgs\n",
        encoding="utf-8",
    )
    _package_xml(main, "main", _depends("nav2_msgs", "std_msgs"))

    _, _, deps, _ = _resolve(project)

    assert [dep.name for dep in deps] == ["std_msgs", "nav2_msgs"]
    entry = _plan(project).entries[0]
    assert entry.requested_by == [main / "airfield.yaml", main / "package.xml"]


def test_ros_packages_in_the_same_source_tree_are_not_installed(project):
    """leg_detector depends on leg_detector_msgs, which sits next to it."""
    main = project / "packages" / "main"
    (main / "package.xml").unlink()
    _package_xml(main / "src" / "detector", "detector", _depends("detector_msgs", "nav2_msgs"))
    _package_xml(main / "src" / "detector_msgs", "detector_msgs")

    _, _, deps, _ = _resolve(project)

    assert [dep.name for dep in deps] == ["nav2_msgs"]
    assert [e.name for e in _plan(project).of_kind(WORKSPACE)] == ["detector_msgs"]


def test_peer_named_only_in_package_xml_is_built_from_source(project):
    """The peer's source is mounted, and what the peer's own package.xml needs
    is installed in this image, where the peer gets compiled."""
    from airfield.cli.package_exec import docker_mount_args

    main = project / "packages" / "main"
    peer = project / "packages" / "peer"
    peer.mkdir()
    (peer / "airfield.yaml").write_text(
        "kind: package\nname: peer\nsource_path: .\nros_distro: jazzy\n", encoding="utf-8"
    )
    _package_xml(peer, "peer", _depends("nav2_msgs"))
    _package_xml(main, "main", _depends("peer"))

    pkg_dir, pkg, deps, source_root = _resolve(project)

    assert [dep.name for dep in deps] == ["nav2_msgs"]
    mounts = " ".join(docker_mount_args(pkg_dir, pkg, source_root, "x86_64"))
    assert f"{peer}:" in mounts and "/workspace/src/peer" in mounts


def test_peer_is_found_by_the_ros_package_name_inside_it(project):
    """Project package `drivers` carries the ROS package `lidar_driver`."""
    main = project / "packages" / "main"
    drivers = project / "packages" / "drivers"
    drivers.mkdir()
    (drivers / "airfield.yaml").write_text(
        "kind: package\nname: drivers\nsource_path: .\nros_distro: jazzy\n", encoding="utf-8"
    )
    _package_xml(drivers / "lidar_driver", "lidar_driver")
    _package_xml(main, "main", _depends("lidar_driver"))

    plan = _plan(project)

    assert [(e.name, e.kind, e.location) for e in plan.entries] == [("lidar_driver", PEER, drivers)]
    assert plan.peers == [("drivers", drivers)]


def test_manifest_still_beats_a_peer_of_the_same_name(project):
    main = project / "packages" / "main"
    peer = project / "packages" / "nav2_msgs"
    peer.mkdir()
    (peer / "airfield.yaml").write_text("kind: package\nname: nav2_msgs\nsource_path: .\n", encoding="utf-8")
    _package_xml(main, "main", _depends("nav2_msgs"))

    plan = _plan(project)

    assert [e.kind for e in plan.entries] == [RECIPE]
    assert plan.peers == []


def test_skip_dependencies_leaves_a_package_xml_entry_out(project):
    main = project / "packages" / "main"
    (main / "airfield.yaml").write_text(
        "kind: package\nname: main\nsource_path: .\nros_distro: jazzy\nskip_dependencies:\n  - not_released\n",
        encoding="utf-8",
    )
    _package_xml(main, "main", _depends("not_released", "nav2_msgs"))

    _, _, deps, _ = _resolve(project)

    assert [dep.name for dep in deps] == ["nav2_msgs"]
    assert [e.name for e in _plan(project).of_kind(SKIPPED)] == ["not_released"]


def test_entry_that_cannot_be_a_package_name_is_ignored_with_a_warning(project, mocker, capsys):
    """A real upstream manifest lists <depend>OpenCV 3.4.12</depend>. It built
    before package.xml was read, so it must keep building."""
    from airfield.cli.package_exec import build_package_image

    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("OpenCV 3.4.12", "nav2_msgs"))

    pkg_dir, pkg, deps, _ = _resolve(project)
    assert capsys.readouterr().out == "", "resolution itself stays silent (shell completion runs it)"
    assert [dep.name for dep in deps] == ["nav2_msgs"]
    assert [e.name for e in _plan(project).of_kind(INVALID)] == ["OpenCV 3.4.12"]

    mocker.patch("airfield.cli.package_exec._validate_and_configure_host_dependencies")
    mocker.patch("airfield.cli.package_exec.Builder.build", return_value=(True, "image"))
    build_package_image(pkg_dir, pkg, deps, target_device="x86_64")

    out = capsys.readouterr().out
    assert "[WARN]" in out and "'OpenCV 3.4.12'" in out and str(main / "package.xml") in out


def test_entry_that_is_a_path_never_reaches_the_filesystem(project, tmp_path):
    """A peer package's source is mounted into the container and a manifest's
    commands run in the image build, so a package.xml entry must not be able
    to point either lookup outside the project."""
    from airfield.cli.package_exec import docker_mount_args

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "airfield.yaml").write_text("kind: package\nname: elsewhere\nsource_path: .\n", encoding="utf-8")
    _manifest(tmp_path, "planted", "system:\n  - echo planted\n")

    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("../../elsewhere", "../../../planted", ".."))

    pkg_dir, pkg, deps, source_root = _resolve(project)

    assert deps == []
    assert [e.kind for e in _plan(project).entries] == [INVALID, INVALID, INVALID]
    assert str(outside) not in " ".join(docker_mount_args(pkg_dir, pkg, source_root, "x86_64"))


def test_package_without_ros_distro_does_not_read_package_xml(project):
    main = project / "packages" / "main"
    (main / "airfield.yaml").write_text("kind: package\nname: main\nsource_path: .\n", encoding="utf-8")
    _package_xml(main, "main", _depends("nav2_msgs"))

    _, _, deps, _ = _resolve(project)

    assert deps == []


def test_dependency_for_another_ros_version_is_not_installed(project):
    main = project / "packages" / "main"
    _package_xml(
        main,
        "main",
        '  <depend condition="$ROS_VERSION == 1">roscpp</depend>\n'
        '  <depend condition="$ROS_VERSION == 2">rclcpp</depend>',
    )

    _, _, deps, _ = _resolve(project)

    assert [dep.name for dep in deps] == ["rclcpp"]


# --- names listed in airfield.yaml get the same rule -----------------------------
#
# A manifest that only says "install ros-$ROS_DISTRO-<name>" says nothing the
# name does not already say. Manifests are for what the rule cannot express.

def _write_airfield_yaml(pkg_dir: Path, name: str, dependencies, ros_distro="jazzy") -> None:
    lines = ["kind: package", f"name: {name}", "source_path: ."]
    if ros_distro:
        lines.append(f"ros_distro: {ros_distro}")
    lines.append("dependencies:")
    lines.extend(f"  - {dep}" for dep in dependencies)
    (pkg_dir / "airfield.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_airfield_yaml_name_with_no_manifest_is_installed_by_the_rule(project):
    """Adding `angles` to airfield.yaml used to stop with "manifest not found"
    until someone wrote angles.yaml containing only the name the rule gives."""
    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["angles", "python3-requests", "nav2_msgs"])

    _, pkg, deps, _ = _resolve(project)

    assert [(d.name, d.apt, d.inferred_from) for d in deps] == [
        ("angles", ["ros-$ROS_DISTRO-angles"], str(main / "airfield.yaml")),
        ("python3-requests", ["python3-requests"], str(main / "airfield.yaml")),
        ("nav2_msgs", ["ros-$ROS_DISTRO-nav2-msgs"], None),  # has a manifest; it wins
    ]
    dockerfile = Builder(pkg, deps, "x86_64").generate_dockerfile()
    assert "apt-get install -y ros-$ROS_DISTRO-angles python3-requests ros-$ROS_DISTRO-nav2-msgs" in dockerfile


def test_rule_gives_what_a_rule_only_manifest_gave(project):
    """Deleting a manifest that merely repeats the rule changes nothing."""
    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["nav2_msgs", "tf2_ros"])
    _manifest(project / "dependencies" / "xplatform", "tf2_ros", "apt:\n  - ros-$ROS_DISTRO-tf2-ros\n")

    _, pkg, deps, _ = _resolve(project)
    with_manifests = Builder(pkg, deps, "x86_64").generate_dockerfile()

    (project / "dependencies" / "xplatform" / "tf2_ros.yaml").unlink()
    (project.parent / "shared" / "xplatform" / "nav2_msgs.yaml").unlink()
    _, pkg, deps, _ = _resolve(project)

    assert Builder(pkg, deps, "x86_64").generate_dockerfile() == with_manifests


def test_tool_package_named_after_what_it_installs(project):
    """`rviz2` is a package folder holding only airfield.yaml, which lists
    `rviz2`. That is the apt package, not a dependency on its own folder."""
    tool = project / "packages" / "rviz2"
    tool.mkdir()
    _write_airfield_yaml(tool, "rviz2", ["rviz2"])

    _, _, deps, _ = _resolve(project, "rviz2")

    assert [(d.name, d.apt) for d in deps] == [("rviz2", ["ros-$ROS_DISTRO-rviz2"])]


def test_source_package_naming_itself_installs_nothing(project):
    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["main"])  # the fixture gave it a package.xml named main

    _, _, deps, _ = _resolve(project)

    assert deps == []
    assert [e.kind for e in _plan(project).entries] == [WORKSPACE]


def test_package_without_ros_distro_still_needs_a_manifest_for_every_name(project, capsys):
    """The rule points at the ROS apt repository. A plain Ubuntu image has
    none, so there a name with no manifest stays the error it was."""
    import typer

    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["angles"], ros_distro=None)

    with pytest.raises(typer.Exit):
        _resolve(project)

    out = capsys.readouterr().out
    assert "Dependency 'angles' manifest not found" in out
    assert str(main / "airfield.yaml") in out
    assert "airfield package dependencies pull" in out


def test_package_without_ros_distro_naming_itself_is_still_a_no_op(project):
    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["main"], ros_distro=None)

    _, _, deps, _ = _resolve(project)

    assert deps == []


def test_airfield_yaml_name_that_fits_no_rule_is_an_error(project, capsys):
    import typer

    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["mixed_under-score"])

    with pytest.raises(typer.Exit):
        _resolve(project)

    assert "Dependency 'mixed_under-score' manifest not found" in capsys.readouterr().out


# --- names rosdep knows ---------------------------------------------------------
#
# These run with rosdep's table switched on (the `name_table` fixture serves a
# small local copy). Everything above runs with it off, which is also what a
# machine that cannot fetch the table gets.

def test_system_library_is_installed_under_the_name_rosdep_gives_it(project, name_table):
    """cmake and eigen carry no hyphen, so the naming rule alone reads them as
    ROS packages and asks apt for ros-jazzy-cmake. A quarter of the packages
    released for Jazzy list a name like that."""
    main = project / "packages" / "main"
    _package_xml(main, "main", "  <buildtool_depend>cmake</buildtool_depend>\n" + _depends("eigen", "curl"))

    _, pkg, deps, _ = _resolve(project)

    assert [(d.name, d.apt, d.inferred_via) for d in deps] == [
        ("cmake", ["cmake"], VIA_ROSDEP),
        ("eigen", ["libeigen3-dev"], VIA_ROSDEP),
        ("curl", ["libcurl4-openssl-dev", "curl"], VIA_ROSDEP),
    ]
    dockerfile = Builder(pkg, deps, "x86_64").generate_dockerfile()
    assert "apt-get install -y cmake libeigen3-dev libcurl4-openssl-dev curl" in dockerfile
    assert "ros-$ROS_DISTRO-cmake" not in dockerfile


def test_ros_package_resolves_the_same_with_and_without_the_table(project, name_table, monkeypatch):
    """The table and the rule agree on every ROS package, so switching the
    table on changes no image that built before."""
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("rclcpp", "angles", "python3-numpy"))

    _, pkg, with_table, _ = _resolve(project)
    monkeypatch.setenv("AIRFIELD_ROSDEP_TABLE", "off")
    _, _, without_table, _ = _resolve(project)

    assert [d.inferred_via for d in with_table] == [VIA_ROS_INDEX, VIA_ROS_INDEX, VIA_ROSDEP]
    assert [d.inferred_via for d in without_table] == [VIA_RULE] * 3
    assert [d.apt for d in with_table] == [d.apt for d in without_table] == [
        ["ros-$ROS_DISTRO-rclcpp"], ["ros-$ROS_DISTRO-angles"], ["python3-numpy"],
    ]
    assert (
        Builder(pkg, with_table, "x86_64").generate_dockerfile()
        == Builder(pkg, without_table, "x86_64").generate_dockerfile()
    )


def test_name_rosdep_does_not_know_falls_back_on_the_rule(project, name_table):
    """A package from another apt source, or one released after the table was
    fetched: the name is still tried the way it is written."""
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("foo_msgs", "python3-requests", "not_released_yet"))

    _, _, deps, _ = _resolve(project)

    assert [(d.apt, d.inferred_via) for d in deps] == [
        (["ros-$ROS_DISTRO-foo-msgs"], VIA_RULE),
        (["python3-requests"], VIA_RULE),
        (["ros-$ROS_DISTRO-not-released-yet"], VIA_RULE),
    ]


def test_manifest_and_peer_still_come_before_rosdep(project, name_table):
    """The table only fills in for names nothing else covers."""
    main = project / "packages" / "main"
    _manifest(project / "dependencies" / "xplatform", "eigen", "apt:\n  - libeigen3-dev=3.4.0-4\n")
    angles = project / "packages" / "angles"
    angles.mkdir()
    (angles / "airfield.yaml").write_text(
        "kind: package\nname: angles\nsource_path: .\nros_distro: jazzy\n", encoding="utf-8"
    )
    _package_xml(angles, "angles")
    _package_xml(main, "main", _depends("eigen", "angles", "nav2_msgs"))

    plan = _plan(project)

    assert [(e.name, e.kind) for e in plan.entries] == [("eigen", RECIPE), ("angles", PEER), ("nav2_msgs", RECIPE)]
    assert plan.entries[0].dependency.apt == ["libeigen3-dev=3.4.0-4"]


def test_rosdep_pip_rule_becomes_a_pip_install(project, name_table):
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("pykalman-pip"))

    _, pkg, deps, _ = _resolve(project)

    assert [(d.name, d.apt, d.pip, d.inferred_via) for d in deps] == [
        ("pykalman-pip", [], ["pykalman"], VIA_ROSDEP)
    ]
    assert "pip install --break-system-packages pykalman" in Builder(pkg, deps, "x86_64").generate_dockerfile()


def test_rosdep_rule_with_nothing_to_install(project, name_table):
    """Some keys stand for something Ubuntu already ships (python-argparse)."""
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("python-argparse"))
    baseline = Builder(Package(name="main", source_path=".", ros_distro="jazzy"), [], "x86_64").generate_dockerfile()

    _, pkg, deps, _ = _resolve(project)

    assert [(d.name, d.apt, d.pip) for d in deps] == [("python-argparse", [], [])]
    assert Builder(pkg, deps, "x86_64").generate_dockerfile() == baseline


def test_names_in_airfield_yaml_go_through_rosdep_too(project, name_table):
    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["eigen", "rclcpp_action"])

    _, _, deps, _ = _resolve(project)

    assert [(d.apt, d.inferred_from) for d in deps] == [
        (["libeigen3-dev"], str(main / "airfield.yaml")),
        (["ros-$ROS_DISTRO-rclcpp-action"], str(main / "airfield.yaml")),
    ]


def test_without_the_table_names_are_translated_by_rule_and_the_build_says_so(project, name_table, capsys):
    """Offline, or github is down: the build goes on the way it did before the
    table existed, and tells the user why `cmake` may now fail."""
    (name_table / "jazzy" / "distribution.yaml").unlink()
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("rclcpp", "cmake"))

    _, pkg, deps, _ = _resolve(project)

    assert [d.apt for d in deps] == [["ros-$ROS_DISTRO-rclcpp"], ["ros-$ROS_DISTRO-cmake"]]
    assert len(pkg._resolution_notes) == 1
    assert "rosdep's table for 'jazzy' could not be fetched" in pkg._resolution_notes[0]


def test_package_whose_names_all_have_manifests_never_fetches_the_table(project, name_table, mocker):
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("nav2_msgs"))
    load = mocker.patch("airfield.rosdep_table.load")

    _resolve(project)

    load.assert_not_called()


def test_package_without_ros_distro_does_not_consult_rosdep(project, name_table, mocker, capsys):
    """The table describes an image built for a ROS distribution. A plain
    Ubuntu package still needs a manifest for every name."""
    import typer

    main = project / "packages" / "main"
    _write_airfield_yaml(main, "main", ["eigen"], ros_distro=None)
    load = mocker.patch("airfield.rosdep_table.load")

    with pytest.raises(typer.Exit):
        _resolve(project)

    load.assert_not_called()
    assert "Dependency 'eigen' manifest not found" in capsys.readouterr().out


def test_build_notice_keeps_guessed_names_apart(capsys):
    """Names rosdep translated are settled. The guessed ones are where a typo
    or a missing manifest hides, so they get their own line."""
    deps = [
        Dependency(name="eigen", apt=["libeigen3-dev"], inferred_from="/ws/p/package.xml", inferred_via=VIA_ROSDEP),
        Dependency(name="rclcpp", apt=["ros-$ROS_DISTRO-rclcpp"], inferred_from="/ws/p/package.xml", inferred_via=VIA_ROS_INDEX),
        Dependency(name="pykalman-pip", pip=["pykalman"], inferred_from="/ws/p/package.xml", inferred_via=VIA_ROSDEP),
        Dependency(name="foo_msgs", apt=["ros-$ROS_DISTRO-foo-msgs"], inferred_from="/ws/p/package.xml", inferred_via=VIA_RULE),
        Dependency(name="has_a_manifest", apt=["libsomething"]),
    ]
    Builder(Package(name="p", ros_distro="jazzy"), deps, "x86_64")._print_inferred_notice()
    out = capsys.readouterr().out.splitlines()

    assert out == [
        "[airfield] no manifest for these, so they are installed as rosdep names them: "
        "eigen (libeigen3-dev), rclcpp (ros-jazzy-rclcpp), pykalman-pip (pip:pykalman)",
        "[airfield] no manifest for these, so they are installed from apt by name: foo_msgs (ros-jazzy-foo-msgs)",
    ]


def test_failed_install_of_a_name_rosdep_translated_is_explained(capsys):
    """Not a typo this time: the table knows the name, apt here does not."""
    deps = [Dependency(name="eigen", apt=["libeigen3-dev"], inferred_from="/ws/p/package.xml", inferred_via=VIA_ROSDEP)]
    Builder(Package(name="p", ros_distro="jazzy"), deps, "x86_64")._explain_inferred_install_failure(
        "E: Unable to locate package libeigen3-dev\n"
    )
    out = capsys.readouterr().out
    assert "apt has no package named 'libeigen3-dev'" in out
    assert "/ws/p/package.xml lists 'eigen'" in out
    assert "translated the way rosdep does" in out
    assert "misspelled" not in out
    assert "eigen.yaml" in out and "skip_dependencies" in out


def test_status_says_how_each_name_was_translated(project, name_table, cli_runner):
    main = project / "packages" / "main"
    _package_xml(main, "main", _depends("eigen", "rclcpp", "foo_msgs", "pykalman-pip", "python-argparse"))

    result = cli_runner.invoke(app, ["status", "--path", str(main), "--target-device", "x86_64"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())

    assert "eigen: no manifest, installed from apt as libeigen3-dev (rosdep's table) (from package.xml)" in out
    assert "rclcpp: no manifest, installed from apt as ros-jazzy-rclcpp (released ROS package) (from package.xml)" in out
    assert "foo_msgs: no manifest, installed from apt as ros-jazzy-foo-msgs (from package.xml)" in out
    assert "pykalman-pip: no manifest, installed with pip as pykalman (rosdep's table)" in out
    assert "python-argparse: no manifest, nothing to install (rosdep's table)" in out


def test_dependencies_pull_brings_the_table_up_to_date(project, name_table, cli_runner, mocker, monkeypatch):
    mocker.patch("airfield.config.pull_packages_repo", return_value=project)
    monkeypatch.chdir(project / "packages" / "main")
    from airfield import rosdep_table

    assert rosdep_table.load("jazzy")[0].lookup("fmt") is None
    base = name_table / "rosdep" / "base.yaml"
    base.write_text(base.read_text() + "fmt:\n  ubuntu: [libfmt-dev]\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["package", "dependencies", "pull"])

    assert result.exit_code == 0, result.output
    assert "Name table up to date: rosdep data for jazzy on Ubuntu noble" in result.output
    assert rosdep_table.load("jazzy")[0].lookup("fmt").packages == ("libfmt-dev",)


# --- when apt rejects a guessed name -------------------------------------------

def _inferred_builder():
    deps = [
        Dependency(name="foo_msgs", apt=["ros-$ROS_DISTRO-foo-msgs"], inferred_from="/ws/pkg/package.xml"),
        Dependency(name="foo_msgs_extra", apt=["ros-$ROS_DISTRO-foo-msgs-extra"], inferred_from="/ws/pkg/package.xml"),
        Dependency(name="written_by_hand", apt=["libwritten-by-hand"]),
    ]
    return Builder(Package(name="p", ros_distro="jazzy"), deps, "x86_64")


def test_failed_install_of_a_guessed_name_is_explained(capsys):
    _inferred_builder()._explain_inferred_install_failure(
        "#12 3.1 Reading package lists...\n#12 3.4 E: Unable to locate package ros-jazzy-foo-msgs\n"
    )
    out = capsys.readouterr().out
    assert "apt has no package named 'ros-jazzy-foo-msgs'" in out
    assert "/ws/pkg/package.xml lists 'foo_msgs'" in out
    assert "foo_msgs.yaml" in out and "skip_dependencies" in out
    assert "airfield package dependencies pull" in out
    assert "foo-msgs-extra" not in out


def test_failed_install_of_a_name_from_airfield_yaml_is_explained(capsys):
    """skip_dependencies is for package.xml entries; a line in airfield.yaml is
    simply removed."""
    deps = [Dependency(name="qt5", apt=["ros-$ROS_DISTRO-qt5"], inferred_from="/ws/pkg/airfield.yaml")]
    Builder(Package(name="p", ros_distro="jazzy"), deps, "x86_64")._explain_inferred_install_failure(
        "E: Unable to locate package ros-jazzy-qt5\n"
    )
    out = capsys.readouterr().out
    assert "/ws/pkg/airfield.yaml lists 'qt5'" in out
    assert "qt5.yaml" in out and "airfield package dependencies pull" in out
    assert "remove 'qt5' from that file" in out
    assert "skip_dependencies" not in out


def test_guessed_name_is_not_blamed_for_a_longer_one(capsys):
    """'ros-jazzy-foo-msgs' is a prefix of the package apt actually rejected."""
    _inferred_builder()._explain_inferred_install_failure(
        "E: Unable to locate package ros-jazzy-foo-msgs-extra\n"
    )
    out = capsys.readouterr().out
    assert "'ros-jazzy-foo-msgs-extra'" in out
    assert "'ros-jazzy-foo-msgs'." not in out


def test_other_build_failures_get_no_dependency_hint(capsys):
    builder = _inferred_builder()
    builder._explain_inferred_install_failure("E: Unable to locate package libwritten-by-hand\n")
    builder._explain_inferred_install_failure("error: failed to solve: process did not complete\n")
    assert capsys.readouterr().out == ""


# --- wrapping an existing ROS package ------------------------------------------

def test_wrap_does_not_copy_dependencies_into_airfield_yaml(cli_runner, temp_workspace, mocker):
    """`package init --path` used to copy the package.xml list into
    airfield.yaml once, and the two drifted from then on."""
    empty_repo = temp_workspace / "empty_packages_repo"
    empty_repo.mkdir()
    mocker.patch("airfield.config.packages_repo_root", return_value=empty_repo)

    pkg_dir = temp_workspace / "my_ros_pkg"
    _package_xml(pkg_dir, "my_ros_pkg", _depends("rclcpp", "urg_node"))

    result = cli_runner.invoke(app, ["package", "init", "--path", str(pkg_dir), "--ros-distro", "jazzy"])
    assert result.exit_code == 0, result.output

    text = (pkg_dir / "airfield.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["name"] == "my_ros_pkg"
    assert data["dependencies"] == []
    assert "read from package.xml" in text
    assert not (pkg_dir / "dependencies").exists(), "no manifests are generated any more"
    assert "ros-jazzy-urg-node" in result.output.replace("\n", "")

    # ...and the freshly wrapped package resolves both names straight away.
    mocker.patch("airfield.cli.package_exec.find_project_root", return_value=None)
    from airfield.cli.package_exec import resolve_package_context

    _, _, deps, _ = resolve_package_context(str(pkg_dir), target_device="x86_64")
    assert [dep.apt for dep in deps] == [["ros-$ROS_DISTRO-rclcpp"], ["ros-$ROS_DISTRO-urg-node"]]


# --- seeing where a dependency came from ---------------------------------------

def test_status_shows_where_each_dependency_comes_from(project, cli_runner):
    main = project / "packages" / "main"
    (main / "airfield.yaml").write_text(
        "kind: package\nname: main\nsource_path: .\nros_distro: jazzy\ndependencies:\n  - nav2_msgs\n",
        encoding="utf-8",
    )
    _package_xml(main, "main", _depends("nav2_msgs", "foo_msgs", "OpenCV 3.4.12"))

    result = cli_runner.invoke(app, ["status", "--path", str(main), "--target-device", "x86_64"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())

    assert "nav2_msgs: manifest xplatform/nav2_msgs.yaml (from airfield.yaml, package.xml)" in out
    assert "foo_msgs: no manifest, installed from apt as ros-jazzy-foo-msgs (from package.xml)" in out
    assert "OpenCV 3.4.12: ignored, not a valid package name" in out
    assert "also_in_package_xml: nav2_msgs" in out
