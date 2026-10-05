import os
from pathlib import Path
import pytest
from typer.testing import CliRunner

@pytest.fixture(autouse=True)
def isolate_shared_workspace(tmp_path, monkeypatch):
    """Keep the shared colcon workspace out of the developer's real home.

    ``docker_mount_args`` creates ``$HOME/workspace/{build,install,log}`` so the
    container's non-root user can write there. Point that at a tmp dir for every
    test; the tests that exercise the default location clear this and patch HOME.
    """
    monkeypatch.setenv("AIRFIELD_WORKSPACE", str(tmp_path / "shared_ws"))


@pytest.fixture(autouse=True)
def no_rosdep_table(monkeypatch):
    """Keep name translation off the network and off the developer's cache.

    With the table switched off a name that has no manifest is translated by
    the naming rule alone, which is what most tests want to pin down. Tests of
    the table itself switch it back on against a fixture (see ``name_table``).
    """
    from airfield import rosdep_table

    monkeypatch.setenv("AIRFIELD_ROSDEP_TABLE", "off")
    monkeypatch.setattr(rosdep_table, "_loaded", {})


@pytest.fixture(autouse=True)
def no_real_docker_queries(monkeypatch):
    """Keep image bookkeeping away from the developer's docker daemon.

    Before it builds, Airfield asks docker which images exist, and afterwards
    it untags a package's outdated ones. Against a real daemon a test could
    find, or remove, images of a real package that happens to share its name.
    By default docker "has nothing"; the tests of that logic give it a fake.
    """
    import airfield.cli.package_exec as package_exec

    monkeypatch.setattr(package_exec, "_docker", lambda *args: None)
    monkeypatch.delenv("AIRFIELD_IMAGE_REGISTRY", raising=False)


# A cut-down copy of github.com/ros/rosdistro, in the same layout, holding one
# example of every shape a rosdep rule takes.
ROSDEP_BASE_YAML = """
cmake:
  debian: [cmake]
  fedora: [cmake]
  ubuntu: [cmake]
eigen:
  fedora: [eigen3-devel]
  ubuntu: [libeigen3-dev]
curl:
  ubuntu: [libcurl4-openssl-dev, curl]
libpcl-all-dev:
  ubuntu:
    '*': [libpcl-dev]
    focal: [libpcl-dev, libpcl-doc]
only-on-jammy:
  ubuntu:
    jammy: [only-on-jammy]
gone-on-noble:
  ubuntu:
    '*': [still-here]
    noble: null
fedora-only:
  fedora: [something]
everywhere:
  '*': [works-everywhere]
snap-thing:
  ubuntu:
    snap:
      packages: [thing]
string-form:
  ubuntu: one two
python-argparse:
  ubuntu: []
"""

ROSDEP_PYTHON_YAML = """
python3-numpy:
  ubuntu: [python3-numpy]
pykalman-pip:
  ubuntu:
    pip:
      packages: [pykalman]
python3-mixed:
  ubuntu:
    '*':
      pip:
        packages: [mixed]
    noble: [python3-mixed]
cmake:
  ubuntu: [a-later-file-must-not-win]
"""

ROSDEP_RUBY_YAML = """
rake:
  ubuntu: [rake]
"""

ROSDISTRO_JAZZY_YAML = """
release_platforms:
  rhel: ['9']
  ubuntu: [noble]
repositories:
  rclcpp:
    release:
      packages: [rclcpp, rclcpp_action]
      url: https://example.invalid/rclcpp-release.git
  navigation2:
    release:
      packages: [nav2_msgs, nav2_util]
  angles:
    release:
      url: https://example.invalid/angles-release.git
  not_released_yet:
    source:
      url: https://example.invalid/not_released_yet.git
type: distribution
"""


@pytest.fixture
def name_table(tmp_path, monkeypatch):
    """rosdep's table switched on, served from a local copy, cached in tmp.

    Returns the directory standing in for github.com/ros/rosdistro, so a test
    can change or remove a file to play a new upstream version or an outage.
    """
    source = tmp_path / "rosdistro"
    (source / "rosdep").mkdir(parents=True)
    (source / "jazzy").mkdir()
    (source / "rosdep" / "base.yaml").write_text(ROSDEP_BASE_YAML, encoding="utf-8")
    (source / "rosdep" / "python.yaml").write_text(ROSDEP_PYTHON_YAML, encoding="utf-8")
    (source / "rosdep" / "ruby.yaml").write_text(ROSDEP_RUBY_YAML, encoding="utf-8")
    (source / "jazzy" / "distribution.yaml").write_text(ROSDISTRO_JAZZY_YAML, encoding="utf-8")

    monkeypatch.setenv("AIRFIELD_ROSDISTRO_URL", source.as_uri())
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("AIRFIELD_ROSDEP_TABLE", raising=False)
    return source


@pytest.fixture
def cli_runner():
    return CliRunner()

@pytest.fixture
def temp_workspace(tmp_path: Path):
    """Provides a temporary directory set as the current working directory."""
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)

@pytest.fixture
def mock_docker(mocker):
    """Mocks container execution to prevent actual docker commands from running.

    `pkg_shell` and `project run` invoke the container via ``subprocess.run``.
    `pkg_cmd`/`pkg_run` go through ``run_container_foreground`` (which uses
    ``subprocess.Popen`` so it can tear the container down on interrupt); route
    that helper through the same mock so tests can still assert on the command
    that would have been executed via ``mock_docker.call_args``.
    """
    run_mock = mocker.patch("subprocess.run")

    def _foreground(run_cmd):
        return run_mock(run_cmd).returncode

    mocker.patch("airfield.cli.pkg_cmd.run_container_foreground", side_effect=_foreground)
    mocker.patch("airfield.cli.pkg_run.run_container_foreground", side_effect=_foreground)
    mocker.patch("airfield.cli.run.run_container_foreground", side_effect=_foreground)
    return run_mock
