import pytest
from pathlib import Path
from airfield.main import app
from airfield.models import Package

@pytest.fixture
def mock_package_context(mocker):
    pkg = Package(name="test_pkg")
    pkg.run = {"start": "echo 'starting'"}
    
    mocker.patch("airfield.cli.build.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    mocker.patch("airfield.cli.pkg_shell.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    mocker.patch("airfield.cli.pkg_run.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    mocker.patch("airfield.cli.pkg_cmd.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    mocker.patch("airfield.cli.run.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    
    mocker.patch("airfield.cli.build.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.pkg_shell.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.pkg_run.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.pkg_cmd.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.run.build_package_image", return_value="test_image")

    mocker.patch("airfield.cli.pkg_shell.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.pkg_run.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.pkg_cmd.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.run.docker_mount_args", return_value=[])

    mocker.patch("airfield.cli.pkg_shell.is_arm_mac", return_value=False)
    mocker.patch("airfield.cli.pkg_run.is_arm_mac", return_value=False)
    mocker.patch("airfield.cli.pkg_cmd.is_arm_mac", return_value=False)

    mocker.patch("airfield.cli.pkg_shell.gpu_runtime_args", return_value=[])
    mocker.patch("airfield.cli.pkg_run.gpu_runtime_args", return_value=[])
    mocker.patch("airfield.cli.pkg_cmd.gpu_runtime_args", return_value=[])
    mocker.patch("airfield.cli.run.gpu_runtime_args", return_value=[])

    mocker.patch("airfield.cli.pkg_shell.container_workdir", return_value="/work")
    mocker.patch("airfield.cli.pkg_run.container_workdir", return_value="/work")
    mocker.patch("airfield.cli.pkg_cmd.container_workdir", return_value="/work")
    mocker.patch("airfield.cli.run.container_workdir", return_value="/work")

    mocker.patch("airfield.cli.pkg_shell.in_airfield_container", return_value=False)
    mocker.patch("airfield.cli.pkg_run.in_airfield_container", return_value=False)
    mocker.patch("airfield.cli.pkg_cmd.in_airfield_container", return_value=False)

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
    assert "No 'default' run command defined" in result.output

def test_project_run_with_default_command(cli_runner, mock_package_context, mock_docker):
    # mock_package_context provides a package with pkg.run = {"start": "echo 'starting'"}
    # Let's set a 'default' command
    mock_package_context.run["default"] = "echo 'starting default'"
    mock_docker.return_value.returncode = 0
    result = cli_runner.invoke(app, ["project", "run", "."])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[-2:] == ["-lc", "echo 'starting default'"]

def test_package_shell_implicit(cli_runner, mock_package_context, mock_docker, mocker):
    mock_docker.return_value.returncode = 0
    mocker.patch("airfield.cli.pkg_shell.in_airfield_container", return_value=False)
    mocker.patch("airfield.config.find_package_root", return_value=Path("."))
    result = cli_runner.invoke(app, ["package", "shell"])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[0] == "docker"
    assert "/bin/zsh" in called_args

def test_package_run_implicit(cli_runner, mock_package_context, mock_docker, mocker):
    mock_docker.return_value.returncode = 0
    
    # Mock find_package_root and Package.load to make disambiguation succeed
    mock_dir = mocker.MagicMock(spec=Path)
    mock_dir.__truediv__.return_value = mock_dir
    mock_dir.exists.return_value = True
    mocker.patch("airfield.config.find_package_root", return_value=mock_dir)
    mocker.patch("airfield.models.Package.load", return_value=mock_package_context)

    result = cli_runner.invoke(app, ["package", "run", "start"])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[-3:] == ["/bin/bash", "-lc", "echo 'starting'"]

def test_package_cmd_implicit(cli_runner, mock_package_context, mock_docker, mocker):
    mock_docker.return_value.returncode = 0
    
    # Mock find_package_root to trigger disambiguation check
    mock_dir = mocker.MagicMock(spec=Path)
    mock_dir.__truediv__.return_value = mock_dir
    mock_dir.exists.return_value = False # make it look like "echo" isn't a package
    mocker.patch("airfield.config.find_package_root", return_value=mock_dir)
    mocker.patch("airfield.config.find_project_root", return_value=None)

    mocker.patch("airfield.cli.pkg_cmd.resolve_package_context", return_value=(Path("."), mock_package_context, [], Path(".")))

    result = cli_runner.invoke(app, ["package", "cmd", "--", "echo", "hello"])
    
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[-3:] == ["/bin/bash", "-lc", "echo hello"]
