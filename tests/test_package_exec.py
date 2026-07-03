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
    assert "/bin/bash" in called_args

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
    assert "/bin/bash" in called_args

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


def _make_project_with_package(tmp_path, *, project_base=None, package_base=None):
    proj = "kind: project\nname: proj\n"
    if project_base:
        proj += f"base_image: {project_base}\n"
    (tmp_path / "airfield.yaml").write_text(proj, encoding="utf-8")
    pkg_dir = tmp_path / "packages" / "p"
    pkg_dir.mkdir(parents=True)
    pkgcfg = "kind: package\nname: p\n"
    if package_base:
        pkgcfg += f"base_image: {package_base}\n"
    (pkg_dir / "airfield.yaml").write_text(pkgcfg, encoding="utf-8")
    return pkg_dir


def test_project_default_base_image_inherited(tmp_path):
    """A package without base_image inherits the project-level default."""
    from airfield.cli.package_exec import _apply_project_default_base_image
    pkg_dir = _make_project_with_package(tmp_path, project_base="my/base:1")
    pkg = Package(name="p")
    assert pkg.base_image is None
    _apply_project_default_base_image(pkg, pkg_dir)
    assert pkg.base_image == "my/base:1"


def test_project_default_base_image_does_not_override_explicit(tmp_path):
    """An explicit per-package base_image wins over the project default."""
    from airfield.cli.package_exec import _apply_project_default_base_image
    pkg_dir = _make_project_with_package(tmp_path, project_base="my/base:1")
    pkg = Package(name="p", base_image="explicit/base:2")
    _apply_project_default_base_image(pkg, pkg_dir)
    assert pkg.base_image == "explicit/base:2"


def test_project_default_base_image_absent_leaves_none(tmp_path):
    """No project default and no package value -> base_image stays None (ROS/ubuntu fallback)."""
    from airfield.cli.package_exec import _apply_project_default_base_image
    pkg_dir = _make_project_with_package(tmp_path, project_base=None)
    pkg = Package(name="p")
    _apply_project_default_base_image(pkg, pkg_dir)
    assert pkg.base_image is None


def test_run_container_foreground_stops_container_on_sighup(mocker):
    """SIGHUP (tmux kill-server / terminal close) must docker-stop the named
    container instead of orphaning it."""
    import os
    import signal
    from airfield.cli.package_exec import run_container_foreground

    stop_calls = []

    def fake_run(cmd, **kwargs):
        stop_calls.append(cmd)
        return mocker.Mock(returncode=0)

    mocker.patch("airfield.cli.package_exec.subprocess.run", side_effect=fake_run)

    proc = mocker.Mock()

    def wait_side_effect():
        if not stop_calls:
            # Simulate tmux kill-server while blocked on the docker client.
            os.kill(os.getpid(), signal.SIGHUP)
        return 137

    proc.wait.side_effect = wait_side_effect
    mocker.patch("airfield.cli.package_exec.subprocess.Popen", return_value=proc)

    rc = run_container_foreground(["docker", "run", "--rm", "img", "true"])

    assert rc == 137
    assert stop_calls, "SIGHUP did not trigger a docker stop"
    assert stop_calls[0][:2] == ["docker", "stop"]
    assert stop_calls[0][-1].startswith("airfield-run-")
    # handler must be restored on exit
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_DFL


def test_run_container_foreground_respects_ignored_sighup(mocker):
    """If SIGHUP is inherited as SIG_IGN (nohup), airfield must not override it."""
    import signal
    from airfield.cli.package_exec import run_container_foreground

    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        seen = {}
        proc = mocker.Mock()

        def wait_side_effect():
            seen["handler"] = signal.getsignal(signal.SIGHUP)
            return 0

        proc.wait.side_effect = wait_side_effect
        mocker.patch("airfield.cli.package_exec.subprocess.Popen", return_value=proc)
        mocker.patch("airfield.cli.package_exec.subprocess.run")

        rc = run_container_foreground(["docker", "run", "--rm", "img", "true"])

        assert rc == 0
        assert seen["handler"] is signal.SIG_IGN
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_package_cmd_wraps_ros_package_with_entry(cli_runner, mock_package_context, mock_docker):
    """ROS packages route through the in-container build-if-needed entry script."""
    mock_package_context.ros_distro = "jazzy"
    mock_docker.return_value.returncode = 0
    result = cli_runner.invoke(app, ["package", "cmd", ".", "--", "ros2", "run", "x", "y"])
    assert result.exit_code == 0
    called_args = mock_docker.call_args[0][0]
    assert called_args[-2:] == ["/opt/airfield-entry.sh", "ros2 run x y"]
    i = called_args.index("AIRFIELD_BUILD_PKG=test_pkg")
    assert called_args[i - 1] == "-e"


class TestGpuRuntimeArgs:
    """Jetson GPU/camera passthrough is always-on; elsewhere it is opt-in via
    TORCH_INSTALL_TARGET=gpu. Plans must run identically on a fresh checkout,
    so basic Jetson hardware access cannot hinge on a torch env var."""

    def _clear_torch_env(self, monkeypatch):
        for var in ("AIRFIELD_TORCH_INSTALL_TARGET", "TORCH_INSTALL_TARGET"):
            monkeypatch.delenv(var, raising=False)

    def test_non_jetson_without_env_returns_nothing(self, mocker, monkeypatch):
        from airfield.cli import package_exec
        self._clear_torch_env(monkeypatch)
        mocker.patch("airfield.cli.package_exec._is_jetson", return_value=False)
        assert package_exec.gpu_runtime_args() == []

    def test_jetson_enables_gpu_without_any_env(self, mocker, monkeypatch):
        from airfield.cli import package_exec
        self._clear_torch_env(monkeypatch)
        mocker.patch("airfield.cli.package_exec._is_jetson", return_value=True)
        mocker.patch(
            "airfield.cli.package_exec._container_engine_alias",
            return_value="docker",
        )
        args = package_exec.gpu_runtime_args()
        assert "--runtime" in args
        assert "nvidia" in args
        assert "NVIDIA_DRIVER_CAPABILITIES=all" in args

    def test_non_jetson_gpu_env_opts_in(self, mocker, monkeypatch):
        from airfield.cli import package_exec
        self._clear_torch_env(monkeypatch)
        monkeypatch.setenv("TORCH_INSTALL_TARGET", "gpu")
        mocker.patch("airfield.cli.package_exec._is_jetson", return_value=False)
        mocker.patch(
            "airfield.cli.package_exec._container_engine_alias",
            return_value="docker",
        )
        args = package_exec.gpu_runtime_args()
        assert "--gpus" in args
        assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility" in args
