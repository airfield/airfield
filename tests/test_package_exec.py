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
