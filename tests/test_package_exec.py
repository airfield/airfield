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
    assert called_args[-2:] == ["/bin/bash", "-l"]
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
    # Teardown waits a beat after the graceful SIGINT for driver cleanup; don't
    # actually sleep in the test.
    mocker.patch("airfield.cli.package_exec.time.sleep")

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
    assert stop_calls, "SIGHUP did not trigger a teardown"
    # Must docker-stop the named container instead of orphaning it...
    stops = [c for c in stop_calls if c[:2] == ["docker", "stop"]]
    assert stops, "SIGHUP did not trigger a docker stop"
    assert stops[0][-1].startswith("airfield-run-")
    # ...but first SIGINT the in-container workload (docker exec) so hardware
    # drivers -- e.g. the RPLIDAR motor -- shut down cleanly before the kill.
    execs = [i for i, c in enumerate(stop_calls) if c[:2] == ["docker", "exec"]]
    assert execs, "no graceful SIGINT (docker exec) issued before stop"
    assert execs[0] < stop_calls.index(stops[0]), "graceful SIGINT must precede docker stop"
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


def test_configured_file_mounts_are_allowed(temp_workspace):
    """.air `mounts:` entries that are files (e.g. ~/.bash_history) mount
    fine with docker -v; only nonexistent paths are skipped."""
    from airfield.cli.package_exec import docker_mount_args

    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()
    file_mount = temp_workspace / "history.txt"
    file_mount.write_text("x", encoding="utf-8")
    missing = temp_workspace / "does_not_exist"
    (pkg_dir / ".air").write_text(f"mounts:\n  - {file_mount}\n  - {missing}\n", encoding="utf-8")

    args = docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")
    joined = " ".join(args)
    assert f"{file_mount}:{file_mount}" in joined
    assert str(missing) not in joined


def test_air_mount_expands_env_vars_and_uid(temp_workspace, monkeypatch):
    """.air paths may reference env vars and $UID, so one documented snippet
    works on every machine -- e.g. gdm's cookie lives under /run/user/<uid>,
    which differs per host. $UID is a shell variable that is never exported, so
    it must resolve even though it is absent from os.environ."""
    import os
    from airfield.cli.package_exec import docker_mount_args

    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()

    uid_dir = temp_workspace / "run" / str(os.getuid()) / "gdm"
    uid_dir.mkdir(parents=True)
    monkeypatch.setenv("AIRFIELD_TEST_ROOT", str(temp_workspace))

    (pkg_dir / ".air").write_text(
        "mounts:\n  - $AIRFIELD_TEST_ROOT/run/$UID/gdm\n", encoding="utf-8"
    )

    args = docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")
    joined = " ".join(args)
    # resolved to the real uid path (so it is not skipped as "does not exist")
    assert f"{uid_dir}:{uid_dir}" in joined
    assert "$UID" not in joined


# --- peer-source mounts: independent of CWD and of the host's architecture ----

@pytest.fixture
def project_with_peer(tmp_path, mocker, monkeypatch):
    """A project whose `main` package depends on a peer package built from source.

    Deliberately laid out on disk (rather than mocking find_project_root) so the
    tests can move the CWD around and observe how the root is resolved.
    """
    mocker.patch("airfield.config.packages_repo_root", return_value=tmp_path / "no_global_repo")

    proj = tmp_path / "proj"
    (proj / "packages" / "main" / "src").mkdir(parents=True)
    (proj / "packages" / "peer" / "src").mkdir(parents=True)
    (proj / "airfield.yaml").write_text("kind: project\nname: proj\n", encoding="utf-8")
    (proj / "packages" / "main" / "airfield.yaml").write_text(
        "name: main\nsource_path: src\ndependencies:\n  - peer\n", encoding="utf-8"
    )
    (proj / "packages" / "peer" / "airfield.yaml").write_text(
        "name: peer\nsource_path: src\n", encoding="utf-8"
    )

    # Every test here runs from OUTSIDE the project tree.
    monkeypatch.chdir(tmp_path)
    return proj


def test_peer_mounts_resolve_from_outside_the_project(project_with_peer):
    """docker_mount_args() anchors the project root on the package, not the CWD,
    so peer sources still mount when it is invoked from elsewhere."""
    from airfield.cli.package_exec import docker_mount_args

    pkg_dir = project_with_peer / "packages" / "main"
    args = docker_mount_args(pkg_dir, Package.load(pkg_dir / "airfield.yaml"), pkg_dir / "src", "x86_64")

    assert str(project_with_peer / "packages" / "peer" / "src") in " ".join(args)


def test_peer_mounts_follow_target_device_not_host_arch(project_with_peer):
    """A dep with a manifest is apt-installed and must NOT be mounted from source.
    Which manifests count depends on --target-device, so the same package resolves
    differently per target regardless of the architecture the CLI runs on."""
    from airfield.cli.package_exec import docker_mount_args

    deps = project_with_peer / "dependencies" / "arm64"
    deps.mkdir(parents=True)
    (deps / "peer.yaml").write_text("name: peer\nversion: 1.0.0\nsystem: []\nuser: []\n", encoding="utf-8")

    pkg_dir = project_with_peer / "packages" / "main"
    pkg = Package.load(pkg_dir / "airfield.yaml")
    peer_src = str(project_with_peer / "packages" / "peer" / "src")

    arm = docker_mount_args(pkg_dir, pkg, pkg_dir / "src", "arm64")
    x86 = docker_mount_args(pkg_dir, pkg, pkg_dir / "src", "x86_64")

    assert peer_src not in " ".join(arm), "arm64 manifest exists: peer is installed, not built"
    assert peer_src in " ".join(x86), "no x86_64 manifest: peer must be built from source"


def test_resolve_package_context_anchors_project_on_the_package(project_with_peer):
    """Given a package path from outside any project, dependency resolution finds
    the project the package lives in -- otherwise its peer deps look like missing
    manifests and the command aborts."""
    from airfield.cli.package_exec import resolve_package_context

    pkg_dir, pkg, deps, source_root = resolve_package_context(
        str(project_with_peer / "packages" / "main"), target_device="x86_64"
    )

    assert pkg.name == "main"
    assert deps == [], "peer is built from source, so it contributes no dependency manifest"
    assert source_root == project_with_peer / "packages" / "main" / "src"


# --- shared colcon workspace: build/install/log survive the --rm container ---
#
# The tests below that use a bare `pkg_dir` model a package with no project
# around it, which is the $HOME/workspace fallback. Project scoping (the
# default) is covered separately at the end of this section.

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Isolate $HOME so workspace mounts resolve under tmp_path rather than
    creating directories in the developer's real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AIRFIELD_WORKSPACE", raising=False)
    return home


def _mount_targets(args):
    """Container-side destinations from a `-v host:container` argument list."""
    return [val.split(":", 1)[1] for flag, val in zip(args, args[1:]) if flag == "-v"]


def test_workspace_is_shared_without_any_air_config(temp_workspace, fake_home):
    """The fresh-clone case: no .air anywhere. Core must still persist
    build/install/log. Otherwise the workspace dies with the --rm container, so
    every pane recompiles AND the entry script's build lock
    (log/.airfield_build.lock) is private to each container -- panes then all
    compile concurrently, which OOM-reboots a memory-lean host."""
    from airfield.cli.package_exec import container_home, docker_mount_args

    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()
    assert not (pkg_dir / ".air").exists(), "fixture must model a clone with no local config"

    args = docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")
    joined = " ".join(args)
    for name in ("build", "install", "log"):
        assert f"{fake_home}/workspace/{name}:{container_home()}/workspace/{name}" in joined


def test_workspace_dirs_are_created_when_missing(temp_workspace, fake_home):
    """Docker creates a missing -v source itself, but as root -- which leaves the
    container's non-root user unable to write into its own workspace."""
    from airfield.cli.package_exec import docker_mount_args

    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()
    assert not (fake_home / "workspace").exists()

    docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")

    for name in ("build", "install", "log"):
        assert (fake_home / "workspace" / name).is_dir()


def test_air_listing_workspace_does_not_duplicate_the_mount(temp_workspace, fake_home):
    """Machines set up before this moved into core still list the workspace dirs
    in .air. Two -v args on one destination make docker fail the whole run with
    "Duplicate mount point", so the pre-existing entry must collapse into one."""
    from airfield.cli.package_exec import container_home, docker_mount_args

    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / ".air").write_text(
        "mounts:\n  - ~/workspace/build\n  - ~/workspace/install\n  - ~/workspace/log\n",
        encoding="utf-8",
    )

    args = docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")
    targets = _mount_targets(args)

    assert len(targets) == len(set(targets)), f"duplicate mount destination in {targets}"
    for name in ("build", "install", "log"):
        assert targets.count(f"{container_home()}/workspace/{name}") == 1


def test_workspace_sharing_can_be_opted_out(temp_workspace, fake_home, monkeypatch):
    """AIRFIELD_WORKSPACE=none restores throwaway per-container workspaces, for
    hosts that want isolation over reuse."""
    from airfield.cli.package_exec import docker_mount_args

    monkeypatch.setenv("AIRFIELD_WORKSPACE", "none")
    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()

    args = docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")

    assert "workspace/install" not in " ".join(args)
    assert not (fake_home / "workspace").exists(), "opted out: nothing created on the host"


def test_workspace_root_can_be_relocated(temp_workspace, fake_home, monkeypatch):
    """AIRFIELD_WORKSPACE=<path> repoints the host side, which is also how
    several projects can deliberately share one build tree."""
    from airfield.cli.package_exec import container_home, docker_mount_args

    alt = temp_workspace / "alt_ws"
    monkeypatch.setenv("AIRFIELD_WORKSPACE", str(alt))
    pkg_dir = temp_workspace / "pkg"
    pkg_dir.mkdir()

    args = docker_mount_args(pkg_dir, Package(name="p"), pkg_dir, "x86_64")
    joined = " ".join(args)

    assert f"{alt}/install:{container_home()}/workspace/install" in joined
    assert f"{fake_home}/workspace" not in joined


def _project(root: Path, pkg_name: str) -> Path:
    """A minimal project containing one package, returning the package dir."""
    pkg_dir = root / "packages" / pkg_name
    (pkg_dir / "src").mkdir(parents=True)
    (root / "airfield.yaml").write_text(f"kind: project\nname: {root.name}\n", encoding="utf-8")
    (pkg_dir / "airfield.yaml").write_text(f"name: {pkg_name}\nsource_path: src\n", encoding="utf-8")
    return pkg_dir


def test_workspace_is_scoped_to_the_project(temp_workspace, fake_home):
    """The default root is <project>/.airfield/workspace, not a machine-wide
    directory. .airfield/ is already gitignored by `project init`, so build
    output stays out of version control without extra setup."""
    from airfield.cli.package_exec import container_home, docker_mount_args

    pkg_dir = _project(temp_workspace / "proj", "base_driver")

    args = docker_mount_args(pkg_dir, Package(name="base_driver"), pkg_dir / "src", "x86_64")
    joined = " ".join(args)

    ws = temp_workspace / "proj" / ".airfield" / "workspace"
    for name in ("build", "install", "log"):
        assert f"{ws}/{name}:{container_home()}/workspace/{name}" in joined
        assert (ws / name).is_dir()
    assert not (fake_home / "workspace").exists(), "must not fall back to $HOME inside a project"


def test_two_projects_do_not_share_a_workspace(temp_workspace, fake_home):
    """The bug this scoping prevents: two projects that both define a package
    called `base_driver` would otherwise resolve to one install/base_driver, and
    the second project's entry script would find the name already built, skip
    the build, and source the first project's binaries."""
    from airfield.cli.package_exec import docker_mount_args

    a = _project(temp_workspace / "proj_a", "base_driver")
    b = _project(temp_workspace / "proj_b", "base_driver")

    args_a = docker_mount_args(a, Package(name="base_driver"), a / "src", "x86_64")
    args_b = docker_mount_args(b, Package(name="base_driver"), b / "src", "x86_64")

    def install_source(args):
        return [v.split(":", 1)[0] for f, v in zip(args, args[1:])
                if f == "-v" and v.split(":", 1)[1].endswith("/workspace/install")]

    assert install_source(args_a) != install_source(args_b)
    assert install_source(args_a) == [str(temp_workspace / "proj_a" / ".airfield" / "workspace" / "install")]


def test_project_scoping_is_anchored_on_the_package_not_the_cwd(temp_workspace, fake_home, monkeypatch):
    """Panes run `airfield package cmd` from wherever tmux put them, so resolving
    the root from the CWD would give one package two different workspaces."""
    from airfield.cli.package_exec import docker_mount_args

    pkg_dir = _project(temp_workspace / "proj", "base_driver")
    outside = temp_workspace / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)

    args = docker_mount_args(pkg_dir, Package(name="base_driver"), pkg_dir / "src", "x86_64")

    ws = temp_workspace / "proj" / ".airfield" / "workspace"
    assert f"{ws}/install" in " ".join(args)


def test_env_override_still_wins_inside_a_project(temp_workspace, fake_home, monkeypatch):
    """Opting out has to work in a project too, since that is where plans run."""
    from airfield.cli.package_exec import docker_mount_args

    pkg_dir = _project(temp_workspace / "proj", "base_driver")
    monkeypatch.setenv("AIRFIELD_WORKSPACE", "none")

    args = docker_mount_args(pkg_dir, Package(name="base_driver"), pkg_dir / "src", "x86_64")

    assert "workspace/install" not in " ".join(args)
    assert not (temp_workspace / "proj" / ".airfield").exists()
