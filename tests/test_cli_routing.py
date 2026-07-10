import pytest
from pathlib import Path
from airfield.main import app

@pytest.mark.parametrize(
    "command",
    [
        ["package", "init"],
        ["package", "deinit"],
        ["package", "build"],
        ["package", "shell"],
        ["package", "cmd"],
        ["package", "run"],
        ["project", "up"],
        ["package", "dependencies", "check"],
        ["package", "dependencies", "upstream"],
        ["package", "dependencies", "pull"],
        ["project", "init"],
        ["project", "deinit"],
        ["project", "run"],
        ["project", "liftoff"],
        ["system", "clean"],
        ["system", "update"],
        ["system", "alias"],
        ["system", "install-completion"],
        ["tools", "system", "clean"],
        ["docker", "cache"],
        ["status"],
        ["doctor"],
    ],
)
def test_documented_commands_have_help(cli_runner, command):
    """Test documented command paths are registered."""
    result = cli_runner.invoke(app, [*command, "--help"])

    assert result.exit_code == 0

def test_command_surface(cli_runner):
    """Test documented command namespaces are registered and legacy top-level commands are absent."""
    result = cli_runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ["package", "project", "system", "tools", "docker", "status", "doctor"]:
        assert command in result.output

    for legacy_command in ["create", "build", "up", "run", "liftoff"]:
        legacy_result = cli_runner.invoke(app, [legacy_command, "--help"])
        assert legacy_result.exit_code != 0

def test_context_does_not_allow_top_level_package_subcommand(cli_runner, temp_workspace):
    """Test package context still does not allow top-level package subcommands."""
    cli_runner.invoke(app, ["package", "init", "."])

    result = cli_runner.invoke(app, ["b", "--help"])

    assert result.exit_code != 0

@pytest.mark.parametrize(
    "command",
    [
        ["pack", "b", "--help"],
        ["proj", "r", "--help"],
        ["sys", "cle", "--help"],
        ["too", "sys", "cle", "--help"],
        ["pack", "dep", "ch", "--help"],
    ],
)
def test_unique_command_prefixes_are_accepted(cli_runner, command):
    """Test unique prefixes are accepted for registered command paths."""
    result = cli_runner.invoke(app, command)

    assert result.exit_code == 0

def test_ambiguous_command_prefix_is_rejected(cli_runner):
    """Test ambiguous prefixes still fail."""
    result = cli_runner.invoke(app, ["p", "--help"])

    assert result.exit_code != 0
    assert "ambiguous" in result.output.lower()

def test_package_cmd_missing_target(cli_runner):
    """Test that omitting the target raises an error."""
    # Running without any arguments should trigger a usage error for the missing PACKAGE_NAME
    result = cli_runner.invoke(app, ["package", "cmd"])
    assert result.exit_code != 0
    assert "Missing argument 'PACKAGE_NAME'" in result.output or "Error" in result.output

def test_package_cmd_with_target(cli_runner, mock_docker, mocker):
    """Test that providing a target works correctly."""
    # We need to mock resolve_package_context and build_package_image so it doesn't fail on missing files
    mocker.patch("airfield.cli.pkg_cmd.resolve_package_context", return_value=(Path("."), None, [], Path(".")))
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

def test_dependency_check_defaults_to_current_directory(cli_runner, temp_workspace):
    """Test dependency check can run without an explicit target argument."""
    from airfield.config import is_arm64
    target_device = "arm64" if is_arm64() else "x86_64"
    result = cli_runner.invoke(app, ["package", "dependencies", "check"])

    assert result.exit_code == 0
    assert f"No local dependencies found at {temp_workspace / 'dependencies' / target_device}" in result.output

def test_dependency_check_accepts_positional_target(cli_runner, temp_workspace):
    """Test dependency check accepts a package/project path as a positional target."""
    from airfield.config import is_arm64
    target_device = "arm64" if is_arm64() else "x86_64"
    package_dir = temp_workspace / "packages" / "nav_stack"
    dep_dir = package_dir / "dependencies" / target_device
    dep_dir.mkdir(parents=True)
    (dep_dir / "local_only.yaml").write_text("name: local_only\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["package", "dependencies", "check", str(package_dir)])

    assert result.exit_code == 0
    assert f"Local dependency root: {dep_dir}" in result.output
    assert "Local dependency manifests: local_only" in result.output
