from airfield.main import app

def test_status_command(cli_runner, mocker):
    mocker.patch("airfield.cli.status.find_project_root", return_value=None)
    mocker.patch("airfield.cli.status.find_package_root", return_value=None)
    result = cli_runner.invoke(app, ["status"])
    # Should fail because no project/package found
    assert result.exit_code != 0
    assert "No Airfield project or package found" in result.output

def test_doctor_command(cli_runner, mocker):
    mocker.patch("airfield.cli.doctor._check_airfield_update", return_value=("pass", "Airfield update", "up-to-date"))
    mocker.patch("airfield.cli.doctor._check_git_hook", return_value=None)
    mocker.patch("airfield.cli.doctor._check_docker", return_value=("pass", "Docker", "ok"))
    mocker.patch("airfield.cli.doctor._check_container", return_value=("pass", "Container", "ok"))
    mocker.patch("airfield.cli.doctor._check_shell_completion", return_value=("pass", "Shell", "ok"))
    mocker.patch("airfield.cli.doctor._check_gpu_accelerator", return_value=("pass", "GPU", "ok"))
    
    result = cli_runner.invoke(app, ["doctor"])
    assert result.exit_code == 0

def test_system_clean(cli_runner, mocker):
    mocker.patch("airfield.cli.tools_system.cleanup_all_airfield_containers", return_value=5)
    result = cli_runner.invoke(app, ["system", "clean"])
    assert result.exit_code == 0
    assert "Removed 5 Airfield container(s)" in result.output

def test_system_update(cli_runner, mocker):
    mocker.patch("airfield.cli.tools_system.check_for_update", return_value={
        "current_version": "0.1.0",
        "latest_version": "0.2.0",
        "url": "https://github.com/airfield/airfield/releases/tag/v0.2.0",
        "newer": True
    })
    subprocess_mock = mocker.patch("subprocess.run")
    subprocess_mock.return_value.returncode = 0
    
    result = cli_runner.invoke(app, ["system", "update"])
    assert result.exit_code == 0
    assert "Updating Airfield via pipx..." in result.output
    subprocess_mock.assert_called_once_with(["pipx", "install", "--force", "git+https://github.com/airfield/airfield.git"], check=False)
