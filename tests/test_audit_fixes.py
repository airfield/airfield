"""Regression tests for the 2026-07 portability-audit fixes."""
from pathlib import Path

import yaml

from airfield.builder import Builder
from airfield.main import app
from airfield.models import Package


def _write_package_xml(pkg_dir: Path, name: str, deps):
    dep_lines = "\n".join(f"  <depend>{d}</depend>" for d in deps)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.xml").write_text(
        f"""<?xml version="1.0"?>
<package format="3">
  <name>{name}</name>
  <version>0.0.1</version>
  <description>test</description>
  <maintainer email="t@t.io">t</maintainer>
  <license>MIT</license>
{dep_lines}
</package>
""",
        encoding="utf-8",
    )


def test_dockerfile_never_installs_airfield_from_pypi():
    builder = Builder(package=Package(name="p", ros_distro="jazzy"), dependencies=[], target_device="arm64")
    for cache_mounts in (True, False):
        df = builder.generate_dockerfile(cache_mounts_enabled=cache_mounts)
        assert "COPY airfield /opt/airfield" in df
        assert "/opt/airfield" in df
        # A bare "pip install ... airfield" would fetch the squatted PyPI name.
        for line in df.splitlines():
            if "pip install" in line:
                assert " airfield" not in line.replace("/opt/airfield", ""), line


def test_dockerfile_is_slim():
    builder = Builder(package=Package(name="p", ros_distro="jazzy"), dependencies=[], target_device="arm64")
    df = builder.generate_dockerfile(cache_mounts_enabled=False)
    assert "python3-opencv" not in df
    assert "oh-my-zsh" not in df
    assert "zsh" not in df


def test_stage_airfield_source_synthesizes_without_repo(tmp_path, mocker):
    builder = Builder(package=Package(name="p"), dependencies=[], target_device="x86_64")
    mocker.patch.object(builder, "_find_airfield_repo", return_value=None)
    builder._stage_airfield_source(tmp_path, tmp_path / "ctx")

    staged = tmp_path / "ctx" / "airfield"
    assert (staged / "pyproject.toml").exists()
    assert (staged / "src" / "airfield" / "main.py").exists()
    pyproject = (staged / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "airfield"' in pyproject
    assert "typer" in pyproject


def test_wrap_skips_base_deps_and_generates_manifests(cli_runner, temp_workspace, mocker):
    # Isolate from the developer machine's real packages checkout: with no
    # shared manifests visible, base-provided deps are dropped and the rest
    # get generated manifests.
    empty_repo = temp_workspace / "empty_packages_repo"
    empty_repo.mkdir()
    mocker.patch("airfield.config.packages_repo_root", return_value=empty_repo)

    pkg_dir = temp_workspace / "my_ros_pkg"
    _write_package_xml(pkg_dir, "my_ros_pkg", ["rclcpp", "std_msgs", "ament_cmake", "urg_node"])

    result = cli_runner.invoke(app, ["package", "init", "--path", str(pkg_dir), "--ros-distro", "jazzy"])
    assert result.exit_code == 0

    data = yaml.safe_load((pkg_dir / "airfield.yaml").read_text(encoding="utf-8"))
    # base-image-provided deps are omitted; the rest kept
    assert data["dependencies"] == ["urg_node"]

    manifest = pkg_dir / "dependencies" / "xplatform" / "urg_node.yaml"
    assert manifest.exists()
    assert "ros-$ROS_DISTRO-urg-node" in manifest.read_text(encoding="utf-8")


def test_project_up_errors_on_empty_plan(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    (temp_workspace / "plans" / "empty.yaml").write_text("name: empty\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["project", "up", "empty", "--no-launch"])
    assert result.exit_code == 1
    assert "defines no windows" in result.output


def test_scaffolded_example_plan_renders(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    result = cli_runner.invoke(app, ["project", "up", "example", "--inspect"])
    assert result.exit_code == 0
    assert "Hello from plan 'example'" in result.output


def test_liftoff_errors_on_planless_packages(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    result = cli_runner.invoke(app, ["project", "liftoff", "example"])
    assert result.exit_code == 1
    assert "liftoff has nothing to run" in result.output


def test_project_run_test_flag_requires_test_command(cli_runner, mock_docker, mocker):
    pkg = Package(name="test_pkg")
    pkg.run = {"start": "echo hi"}
    mocker.patch("airfield.cli.run.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    mocker.patch("airfield.cli.run.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.run.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.run.gpu_runtime_args", return_value=[])

    result = cli_runner.invoke(app, ["project", "run", ".", "--test"])
    assert result.exit_code == 1
    assert "No 'test' run command" in result.output
    mock_docker.assert_not_called()
