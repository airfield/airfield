import pytest
from airfield.main import app

def test_package_cmd_missing_target(cli_runner):
    """Test that omitting the target raises an error."""
    # Running without any arguments should trigger a usage error for the missing PACKAGE_NAME
    result = cli_runner.invoke(app, ["package", "cmd"])
    assert result.exit_code != 0
    assert "Missing argument 'PACKAGE_NAME'" in result.output or "Error" in result.output

def test_package_cmd_with_target(cli_runner, mock_docker, mocker):
    """Test that providing a target works correctly."""
    # We need to mock resolve_package_context and build_package_image so it doesn't fail on missing files
    mocker.patch("airfield.cli.pkg_cmd.resolve_package_context", return_value=(None, None, [], None))
    mocker.patch("airfield.cli.pkg_cmd.build_package_image", return_value="test_image")
    mocker.patch("airfield.cli.pkg_cmd.docker_mount_args", return_value=[])
    mocker.patch("airfield.cli.pkg_cmd.gpu_runtime_args", return_value=[])
    mocker.patch("airfield.cli.pkg_cmd.container_workdir", return_value="/work")
    mocker.patch("airfield.cli.pkg_cmd.in_airfield_container", return_value=False)
    
    # We mock out the actual docker command execution
    mock_docker.return_value.returncode = 0
    
    # Use -- to ensure ls -la is not interpreted as options for the cmd command itself
    result = cli_runner.invoke(app, ["package", "cmd", "my_pkg", "--", "ls", "-la"])
    assert result.exit_code == 0
    assert "Loading package my_pkg" in result.output

    # Verify that the correct command was passed down
    called_args = mock_docker.call_args[0][0]
    assert called_args[-3:] == ["/bin/bash", "-lc", "ls -la"]
