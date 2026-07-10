import pytest
from pathlib import Path
from airfield.models import Package, Dependency
from airfield.builder import Builder

def test_builder_generate_dockerfile_includes_force_ipv4(mocker):
    # Arrange
    mocker.patch("airfield.builder.is_arm_mac", return_value=True)
    package = Package(name="test_pkg")
    dependencies = []
    builder = Builder(package=package, dependencies=dependencies, target_device="x86_64")

    # Act
    dockerfile = builder.generate_dockerfile()

    # Assert
    assert "RUN echo 'Acquire::ForceIPv4 \"true\";' > /etc/apt/apt.conf.d/99force-ipv4" in dockerfile


def test_builder_generate_dockerfile_cache_mounts_enabled():
    package = Package(name="test_pkg")
    dependencies = []
    builder = Builder(package=package, dependencies=dependencies, target_device="x86_64")

    # Act with cache mounts enabled
    dockerfile_cache = builder.generate_dockerfile(cache_mounts_enabled=True)
    assert "RUN --mount=type=cache" in dockerfile_cache

    # Act with cache mounts disabled
    dockerfile_no_cache = builder.generate_dockerfile(cache_mounts_enabled=False)
    assert "RUN --mount=type=cache" not in dockerfile_no_cache

def test_builder_build_command_selection(mocker, tmp_path):
    package = Package(name="test_pkg")
    dependencies = []
    builder = Builder(package=package, dependencies=dependencies, target_device="arm64")

    # Mock subprocess.run in builder to avoid calling docker/container CLI
    mock_run = mocker.patch("airfield.builder.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "Docker version 20.10.7"
    
    mock_run_progress = mocker.patch("airfield.builder.run_build_with_progress")
    mocker.patch("airfield.builder.shutil.copytree")

    # Mock os.getuid and os.getgid
    mocker.patch("os.getuid", return_value=1000)
    mocker.patch("os.getgid", return_value=1000)
    mock_pwd = mocker.patch("airfield.builder.pwd.getpwuid")
    mock_pwd.return_value.pw_name = "testuser"
    
    # 1. On non-ARM Mac: should use docker build
    mocker.patch("airfield.builder.is_arm_mac", return_value=False)
    builder.build(context_dir=tmp_path)
    
    # Check the build command passed to run_build_with_progress
    build_call = mock_run_progress.call_args[1]["cmd"]
    assert "docker" in build_call
    assert "build" in build_call
    assert "--platform" in build_call

    # 2. On ARM Mac: should use container build
    mocker.patch("airfield.builder.is_arm_mac", return_value=True)
    builder.build(context_dir=tmp_path)
    
    build_call = mock_run_progress.call_args[1]["cmd"]
    assert "container" in build_call
    assert "build" in build_call
    assert "--arch" in build_call
