"""Auto-build feature: in-container entry wrapper + colcon_args + project down."""
import subprocess

import pytest

from airfield.builder import Builder, ENTRY_SCRIPT
from airfield.cli.package_exec import entry_wrap_args
from airfield.main import app
from airfield.models import Package


# --- entry_wrap_args -------------------------------------------------------

def test_entry_wrap_non_ros_package_runs_plain_login_shell():
    pkg = Package(name="tool_pkg")
    env_args, cmd = entry_wrap_args(pkg, "echo hi")
    assert env_args == []
    assert cmd == ["/bin/bash", "-lc", "echo hi"]


def test_entry_wrap_ros_package_routes_through_entry_script():
    pkg = Package(name="ros_pkg", ros_distro="jazzy")
    env_args, cmd = entry_wrap_args(pkg, "ros2 run ros_pkg node")
    assert env_args == ["-e", "AIRFIELD_BUILD_PKG=ros_pkg"]
    assert cmd == ["/opt/airfield-entry.sh", "ros2 run ros_pkg node"]


def test_entry_wrap_passes_colcon_args_env():
    pkg = Package(name="ros_pkg", ros_distro="jazzy",
                  colcon_args="--cmake-args -DCMAKE_BUILD_MODE=Hardware")
    env_args, _ = entry_wrap_args(pkg, "true")
    assert env_args == [
        "-e", "AIRFIELD_BUILD_PKG=ros_pkg",
        "-e", "AIRFIELD_COLCON_ARGS=--cmake-args -DCMAKE_BUILD_MODE=Hardware",
    ]


# --- entry script content + Dockerfile wiring ------------------------------

def test_entry_script_is_valid_bash_and_has_guards(tmp_path):
    script = tmp_path / "entry.sh"
    script.write_text(ENTRY_SCRIPT, encoding="utf-8")
    # syntax check
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    # per-package build guard, serialization, and scoped build
    assert 'install/$pkg' in ENTRY_SCRIPT
    assert "flock" in ENTRY_SCRIPT
    assert '--packages-up-to "$pkg"' in ENTRY_SCRIPT
    # failures must not be swallowed
    assert "/dev/null" not in ENTRY_SCRIPT.split("colcon build")[1].split("\n")[0]


def test_dockerfile_copies_entry_script_for_ros_packages():
    builder = Builder(Package(name="p", ros_distro="jazzy"), [], "arm64")
    df = builder.generate_dockerfile(cache_mounts_enabled=False)
    assert "COPY airfield-entry.sh /opt/airfield-entry.sh" in df
    assert "chmod 755 /opt/airfield-entry.sh" in df


def test_dockerfile_omits_entry_script_for_non_ros_packages():
    builder = Builder(Package(name="p"), [], "arm64")
    df = builder.generate_dockerfile(cache_mounts_enabled=False)
    assert "airfield-entry.sh" not in df


# --- Package model ----------------------------------------------------------

def test_package_load_parses_colcon_args(tmp_path):
    cfg = tmp_path / "airfield.yaml"
    cfg.write_text(
        "kind: package\nname: p\nros_distro: jazzy\n"
        "colcon_args: --cmake-args -DCMAKE_BUILD_MODE=Hardware\n",
        encoding="utf-8",
    )
    pkg = Package.load(cfg)
    assert pkg.colcon_args == "--cmake-args -DCMAKE_BUILD_MODE=Hardware"


# --- project down -----------------------------------------------------------

@pytest.fixture
def project_with_plans(temp_workspace):
    (temp_workspace / "airfield.yaml").write_text(
        "kind: project\nname: proj\nversion: 0.1.0\n", encoding="utf-8"
    )
    plans = temp_workspace / "plans"
    plans.mkdir()
    (plans / "teleop.yaml").write_text("name: teleop\n", encoding="utf-8")
    (plans / "navstack.yaml").write_text("name: navstack\n", encoding="utf-8")
    return temp_workspace


def _mock_down_subprocess(mocker, sessions):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = mocker.Mock(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["tmux", "list-sessions"]:
            result.stdout = "\n".join(sessions) + "\n"
        elif cmd[:2] == ["docker", "ps"]:
            result.stdout = "abc123\ndef456\n"
        return result

    mocker.patch("airfield.cli.down.subprocess.run", side_effect=fake_run)
    return calls


def test_project_down_kills_only_plan_sessions(cli_runner, project_with_plans, mocker):
    calls = _mock_down_subprocess(mocker, sessions=["teleop", "unrelated"])
    result = cli_runner.invoke(app, ["project", "down"])
    assert result.exit_code == 0
    kills = [c for c in calls if c[:2] == ["tmux", "kill-session"]]
    assert kills == [["tmux", "kill-session", "-t", "teleop"]]


def test_project_down_named_plan_and_prune(cli_runner, project_with_plans, mocker):
    calls = _mock_down_subprocess(mocker, sessions=["teleop", "navstack"])
    result = cli_runner.invoke(app, ["project", "down", "navstack", "--prune"])
    assert result.exit_code == 0
    kills = [c for c in calls if c[:2] == ["tmux", "kill-session"]]
    assert kills == [["tmux", "kill-session", "-t", "navstack"]]
    rms = [c for c in calls if c[:3] == ["docker", "rm", "-f"]]
    assert rms == [["docker", "rm", "-f", "abc123", "def456"]]


@pytest.mark.parametrize("missing", ["tmux", "docker"])
def test_project_down_survives_missing_binaries(cli_runner, project_with_plans, mocker, missing):
    """`down` reaches for tmux and (with --prune) docker. Neither is guaranteed to
    exist on every host, and a missing one must report itself rather than crash
    the teardown with a FileNotFoundError traceback."""
    def fake_run(cmd, **kwargs):
        if cmd[0] == missing:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        return mocker.Mock(returncode=0, stdout="", stderr="")

    mocker.patch("airfield.cli.down.subprocess.run", side_effect=fake_run)

    result = cli_runner.invoke(app, ["project", "down", "--prune"])
    assert result.exit_code == 0
    assert not isinstance(result.exception, FileNotFoundError)
    assert "not found" in result.output
