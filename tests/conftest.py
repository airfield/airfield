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
