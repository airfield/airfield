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


def _dockerfile_with(dependencies, monkeypatch=None, **kwargs):
    package = Package(name="test_pkg")
    builder = Builder(package=package, dependencies=dependencies, target_device="x86_64")
    return builder.generate_dockerfile(**kwargs)


def test_batched_pip_specs_produce_a_single_install(monkeypatch):
    """Every `pip:` requirement lands in one install so pip resolves them together.

    Separate installs each solve in isolation and overwrite each other's
    versions while still exiting 0, which produces a green build and a broken
    image.
    """
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [
        Dependency(name="python3-numpy", pip=["numpy"]),
        Dependency(name="python3-flask", pip=["flask>=3"]),
        Dependency(name="tqdm", pip=["tqdm"]),
    ]
    dockerfile = _dockerfile_with(deps, cache_mounts_enabled=False)

    install_lines = [
        line for line in dockerfile.splitlines()
        if "pip install" in line and "/opt/airfield" not in line and "--upgrade pip" not in line
    ]
    # One install plus its no-break-system-packages retry, nothing per-dependency.
    assert len(install_lines) == 2
    assert "numpy 'flask>=3' tqdm" in install_lines[0]


def test_pip_specs_are_shell_quoted(monkeypatch):
    """Version constraints contain < and >, which an unquoted shell would redirect."""
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [Dependency(name="pinned", pip=["numpy<2", "scipy>=1.11"])]
    dockerfile = _dockerfile_with(deps, cache_mounts_enabled=False)

    assert "'numpy<2'" in dockerfile
    assert "'scipy>=1.11'" in dockerfile


def test_batched_pip_specs_are_deduped_and_ordered(monkeypatch):
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [
        Dependency(name="a", pip=["numpy", "tqdm"]),
        Dependency(name="b", pip=["numpy", "flask"]),
    ]
    package = Package(name="test_pkg")
    builder = Builder(package=package, dependencies=deps, target_device="x86_64")

    assert builder._batched_pip_specs() == ["numpy", "tqdm", "flask"]


def test_raw_user_commands_still_work_alongside_batching(monkeypatch):
    """Manifests that must run their own pip command keep working, and run last.

    Airfield builds on third-party stacks whose manifests it does not control,
    so `user:`/`system:` can never stop being honored.
    """
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [
        Dependency(name="python3-numpy", pip=["numpy"]),
        Dependency(name="torch", user=["python3 -m pip install torch --index-url https://x/cu121"]),
    ]
    dockerfile = _dockerfile_with(deps, cache_mounts_enabled=False)

    batched = dockerfile.index("--break-system-packages numpy")
    escape_hatch = dockerfile.index("--index-url")
    # Deliberate custom-index installs get the last word over the generic resolve.
    assert batched < escape_hatch


def test_pip_check_brackets_the_dependency_installs(monkeypatch):
    """Baseline is recorded before any dependency install, verify after all of them."""
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [
        Dependency(name="apt_dep", system=["apt-get update && apt-get install -y python3-scipy"]),
        Dependency(name="python3-numpy", pip=["numpy"]),
    ]
    dockerfile = _dockerfile_with(deps, cache_mounts_enabled=False)

    baseline = dockerfile.index("airfield-pip-check.sh baseline")
    apt_install = dockerfile.index("python3-scipy")
    pip_install = dockerfile.index("--break-system-packages numpy")
    verify = dockerfile.index("airfield-pip-check.sh verify")

    assert baseline < apt_install < pip_install < verify
    assert "verify /opt/airfield-pip-baseline.txt strict" in dockerfile


@pytest.mark.parametrize(
    "value,expected",
    [("off", None), ("warn", "warn"), ("strict", "strict"), ("", "strict")],
)
def test_pip_check_mode_is_env_controlled(monkeypatch, value, expected):
    monkeypatch.setenv("AIRFIELD_PIP_CHECK", value)
    dockerfile = _dockerfile_with([], cache_mounts_enabled=False)

    if expected is None:
        assert "airfield-pip-check.sh" not in dockerfile
    else:
        assert f"verify /opt/airfield-pip-baseline.txt {expected}" in dockerfile


def test_pip_entries_reject_shell_commands():
    """The likely migration mistake is pasting the old command into `pip:`."""
    with pytest.raises(ValueError, match="not shell commands"):
        Dependency(name="bad", pip=["python3 -m pip install numpy"])

    # Real requirement specs, including ones with spaces, still load.
    assert Dependency(name="ok", pip=["numpy >= 1.24", "flask"]).pip == ["numpy >= 1.24", "flask"]


def test_batched_apt_produces_one_install_with_one_index_refresh(monkeypatch):
    """43 manifests each running `apt-get update` becomes one refresh, one install.

    Batching also means apt refuses an unsatisfiable set instead of resolving a
    conflict by removing a package an earlier manifest installed.
    """
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [
        Dependency(name="rclcpp", apt=["ros-$ROS_DISTRO-rclcpp"]),
        Dependency(name="boost", apt=["libboost-dev", "libboost-system-dev"]),
        Dependency(name="gflags", apt=["libgflags-dev"]),
    ]
    package = Package(name="test_pkg", ros_distro="jazzy")
    builder = Builder(package=package, dependencies=deps, target_device="x86_64")
    dockerfile = builder.generate_dockerfile(cache_mounts_enabled=False)

    # One for the base image's own packages, one for every declared dependency.
    assert dockerfile.count("apt-get update") == 2
    assert (
        "ros-$ROS_DISTRO-rclcpp libboost-dev libboost-system-dev libgflags-dev"
        in dockerfile
    )


def test_apt_entries_allow_distro_variables_but_not_commands():
    """`ros-$ROS_DISTRO-foo` is how a manifest stays usable across distros."""
    dep = Dependency(name="ok", apt=["ros-$ROS_DISTRO-nav2-util", "ros-${ROS_DISTRO}-tf2", "libfoo-dev"])
    assert len(dep.apt) == 3

    with pytest.raises(ValueError, match="not shell commands"):
        Dependency(name="bad", apt=["apt-get update && apt-get install -y libfoo-dev"])


def test_apt_batch_runs_before_escape_hatch_system_commands(monkeypatch):
    """vnc installs its apt packages, then dpkg-installs a downloaded .deb."""
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [
        Dependency(
            name="vnc",
            apt=["wget", "libegl1"],
            system=["wget https://example/turbovnc.deb -O /tmp/t.deb && dpkg -i /tmp/t.deb"],
        ),
    ]
    dockerfile = _dockerfile_with(deps, cache_mounts_enabled=False)

    assert dockerfile.index("wget libegl1") < dockerfile.index("dpkg -i")


def test_apt_batch_is_recorded_after_the_pip_baseline(monkeypatch):
    """apt installs Python packages too, so they must land after the baseline."""
    monkeypatch.delenv("AIRFIELD_PIP_CHECK", raising=False)
    deps = [Dependency(name="python3-scipy", apt=["python3-scipy"])]
    dockerfile = _dockerfile_with(deps, cache_mounts_enabled=False)

    assert dockerfile.index("pip-check.sh baseline") < dockerfile.index("python3-scipy")
