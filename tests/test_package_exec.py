import pytest
from airfield.main import app
from airfield.models import Package

@pytest.fixture
def mock_package_context(mocker):
    pkg = Package(name="test_pkg")
    pkg.run = {"start": "echo 'starting'"}
    
    mocker.patch("airfield.cli.build.resolve_package_context", return_value=(None, pkg, [], None))
    mocker.patch("airfield.cli.pkg_shell.resolve_package_context", return_value=(None, pkg, [], None))
    mocker.patch("airfield.cli.pkg_run.resolve_package_context", return_value=(None, pkg, [], None))
    mocker.patch("airfield.cli.run.resolve_package_context", return_value=(None, pkg, [], None))
    
    mocker.patch("airfield.cli.build.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.pkg_shell.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.pkg_run.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.run.build_package_image", return_value="test_image")

    mocker.patch("airfield.cli.pkg_shell.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.pkg_run.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.run.docker_mount_args", return_value=[])

    mocker.patch("airfield.cli.pkg_shell.gpu_runtime_args", return_value=[])
    mocker.patch("airfield.cli.pkg_run.gpu_runtime_args", return_value=[])
    mocker.patch("airfield.cli.run.gpu_runtime_args", return_value=[])

    mocker.patch("airfield.cli.pkg_shell.container_workdir", return_value="/work")
    mocker.patch("airfield.cli.pkg_run.container_workdir", return_value="/work")
    mocker.patch("airfield.cli.run.container_workdir", return_value="/work")

    mocker.patch("airfield.cli.pkg_shell.in_airfield_container", return_value=False)
    mocker.patch("airfield.cli.pkg_run.in_airfield_container", return_value=False)

    return pkg


def test_package_build(cli_runner, mock_package_context):
    result = cli_runner.invoke(app, ["package", "build", "."])
    assert result.exit_code == 0
    assert "Build successful" in result.output

def test_package_shell(cli_runner, mock_package_context, mock_docker):
    mock_docker.return_value.returncode = 0
    result = cli_runner.invoke(app, ["package", "shell", "."])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[0] == "docker"
    assert "/bin/zsh" in called_args

def test_package_run(cli_runner, mock_package_context, mock_docker):
    mock_docker.return_value.returncode = 0
    result = cli_runner.invoke(app, ["package", "run", ".", "start"])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[-3:] == ["/bin/bash", "-lc", "echo 'starting'"]

def test_project_run(cli_runner, mock_package_context, mock_docker):
    mock_docker.return_value.returncode = 0
    result = cli_runner.invoke(app, ["project", "run", "."])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[-1] == "/bin/bash"
