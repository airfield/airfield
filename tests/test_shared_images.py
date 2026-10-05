"""An image is the same whoever builds it, is named after its recipe, and can
be fetched from a registry instead of being built again.

Three things make that work, each tested below:

* the image holds no account for the builder; the caller's account is made
  when a container starts (`/opt/airfield-init.sh`);
* the image's tag is a fingerprint of everything that decides its contents;
* `build_package_image` reuses, fetches or builds, in that order.
"""
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest
import typer

import airfield.cli.package_exec as package_exec
from airfield import builder as builder_module
from airfield.builder import BUILD_HOME, BUILD_UID, INIT_SCRIPT, SKEL_DIR, Builder
from airfield.cli.package_exec import (
    build_package_image,
    entry_wrap_args,
    image_registry,
    shell_wrap_args,
    user_setup_args,
)
from airfield.main import app
from airfield.models import Dependency, Package


def _ros_builder(**package_fields):
    deps = [
        Dependency(name="nav2_msgs", apt=["ros-$ROS_DISTRO-nav2-msgs"]),
        Dependency(name="tqdm", pip=["tqdm"]),
        Dependency(name="torch", user=["set -eu; python3 -m pip install torch"]),
    ]
    return Builder(Package(name="p", ros_distro="jazzy", **package_fields), deps, "arm64")


def _torch_builder():
    """A package whose recipe reads the torch build arguments, like the
    shared torch manifest does."""
    torch = Dependency(
        name="torch",
        user=['if [ "${TORCH_INSTALL_TARGET:-cpu}" = "gpu" ]; then python3 -m pip install torch-gpu; else python3 -m pip install torch-cpu; fi'],
    )
    return Builder(Package(name="p", ros_distro="jazzy"), [torch], "arm64")


# --- the image holds no account for whoever builds it ---------------------------

def test_dockerfile_is_the_same_whoever_builds_it(monkeypatch):
    as_me = _ros_builder().generate_dockerfile()

    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(os, "getgid", lambda: 1000)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(("alice", "x", 1000, 1000, "", "/home/alice", "/bin/bash")))
    as_someone_else = _ros_builder().generate_dockerfile()

    assert as_me == as_someone_else
    me = pwd.getpwuid(os.getuid()).pw_name
    for trace in ("USERNAME", "ARG UID", "ARG GID", "/home/", f"/{me}/" if me != "root" else "USERNAME"):
        assert trace not in as_me, trace


def test_image_starts_as_root_and_carries_the_init_script():
    lines = _ros_builder().generate_dockerfile().splitlines()

    assert "COPY airfield-init.sh /opt/airfield-init.sh" in lines
    assert "RUN chmod 755 /opt/airfield-init.sh" in lines
    # The last word on who a container starts as: root, so the init script can
    # create the caller's account before handing over to it.
    assert [line for line in lines if line.startswith("USER ")][-1] == "USER root"
    assert [line for line in lines if line.startswith("ENV HOME=")][-1] == "ENV HOME=/root"


def test_non_ros_image_gets_the_init_script_too():
    dockerfile = Builder(Package(name="tool"), [], "x86_64").generate_dockerfile()
    assert "COPY airfield-init.sh /opt/airfield-init.sh" in dockerfile
    assert "airfield-entry.sh" not in dockerfile
    assert "/opt/ros/" not in dockerfile


def test_shell_startup_files_are_kept_as_a_skeleton():
    """What used to be appended to the builder's own ~/.bashrc and ~/.profile
    is kept in a skeleton that every caller's home starts from."""
    dockerfile = _ros_builder().generate_dockerfile()
    skeleton = next(line for line in dockerfile.splitlines() if f"mkdir -p {SKEL_DIR}" in line)

    assert f"'source /opt/ros/$ROS_DISTRO/setup.bash' >> {SKEL_DIR}/.bashrc" in skeleton
    assert f"'source /opt/ros/$ROS_DISTRO/setup.bash' >> {SKEL_DIR}/.profile" in skeleton
    assert "$HOME/workspace/install/setup.bash" in skeleton
    assert "colcon_build()" in skeleton


def test_unprivileged_installs_run_as_a_fixed_account_and_stay_shareable():
    """pip's user site ends up in a fixed home. The caller reaches it through
    a link and is someone else, hence umask 0000."""
    dockerfile = _ros_builder().generate_dockerfile(cache_mounts_enabled=False)
    lines = dockerfile.splitlines()
    start = lines.index(f"USER {BUILD_UID}:{BUILD_UID}")
    end = len(lines) - 1 - lines[::-1].index("USER root")
    user_phase = lines[start:end]

    assert f"ENV HOME={BUILD_HOME}" in user_phase
    assert any(line.startswith("RUN umask 0000 && python3 -m pip install --break-system-packages tqdm") for line in user_phase)
    assert "RUN umask 0000; set -eu; python3 -m pip install torch" in user_phase
    assert any("airfield-pip-check.sh verify" in line for line in user_phase)
    # the privileged installs still happen before it, as root
    assert dockerfile.index("apt-get install -y ros-$ROS_DISTRO-nav2-msgs") < dockerfile.index(f"USER {BUILD_UID}:{BUILD_UID}")


def test_pip_cache_mount_is_writable_by_the_build_account():
    dockerfile = _ros_builder().generate_dockerfile(cache_mounts_enabled=True)
    assert f"--mount=type=cache,target={BUILD_HOME}/.cache/pip,uid={BUILD_UID},gid={BUILD_UID}" in dockerfile


# --- the caller's account is made when the container starts ---------------------

def test_init_script_is_valid_bash(tmp_path):
    script = tmp_path / "init.sh"
    script.write_text(INIT_SCRIPT, encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    # the properties the rest of Airfield leans on
    assert "--keep-groups" in INIT_SCRIPT, "docker's --group-add is how devices are reached"
    assert "/proc/self/mountinfo" in INIT_SCRIPT, "a mounted directory must keep its host owner"


def test_init_script_steps_aside_when_there_is_nobody_to_become(tmp_path, monkeypatch):
    """Run by hand (no AIRFIELD_UID), or already unprivileged: just run the command."""
    if os.getuid() == 0:
        pytest.skip("needs an unprivileged user")
    script = tmp_path / "init.sh"
    script.write_text(INIT_SCRIPT, encoding="utf-8")

    monkeypatch.delenv("AIRFIELD_UID", raising=False)
    by_hand = subprocess.run(["bash", str(script), "echo", "ran"], capture_output=True, text=True)
    monkeypatch.setenv("AIRFIELD_UID", "4242")
    unprivileged = subprocess.run(["bash", str(script), "sh", "-c", "echo $0 as $(id -u)", "ran"], capture_output=True, text=True)

    assert (by_hand.returncode, by_hand.stdout.strip()) == (0, "ran")
    assert (unprivileged.returncode, unprivileged.stdout.strip()) == (0, f"ran as {os.getuid()}")


def test_commands_carry_the_callers_identity_and_start_in_the_init_script(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(os, "getgid", lambda: 1001)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(("alice", "x", 1000, 1001, "", "/home/alice", "/bin/bash")))

    identity = ["-e", "AIRFIELD_UID=1000", "-e", "AIRFIELD_GID=1001", "-e", "AIRFIELD_USER=alice", "-e", "AIRFIELD_HOME=/home/alice"]
    assert user_setup_args() == identity

    env_args, command = entry_wrap_args(Package(name="p", ros_distro="jazzy"), "ros2 run p node")
    assert env_args[:8] == identity
    assert command == ["/opt/airfield-init.sh", "/opt/airfield-entry.sh", "ros2 run p node"]

    env_args, command = shell_wrap_args()
    assert env_args == identity
    assert command == ["/opt/airfield-init.sh", "/bin/bash", "-l"]


@pytest.fixture
def stubbed_cli(mocker):
    """The four commands that start a container, with everything but the
    command line they assemble stubbed out."""
    pkg = Package(name="test_pkg", ros_distro="jazzy")
    for module in ("pkg_shell", "pkg_run", "pkg_cmd", "run"):
        mocker.patch(f"airfield.cli.{module}.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
        mocker.patch(f"airfield.cli.{module}.build_package_image", return_value="test_image")
        mocker.patch(f"airfield.cli.{module}.docker_mount_args", return_value=[])
        mocker.patch(f"airfield.cli.{module}.gpu_runtime_args", return_value=[])
        mocker.patch(f"airfield.cli.{module}.container_workdir", return_value="/work")
        mocker.patch(f"airfield.cli.{module}.is_arm_mac", return_value=False)
    return pkg


@pytest.mark.parametrize(
    "argv, tail",
    [
        (["package", "shell", "."], ["/opt/airfield-init.sh", "/bin/bash", "-l"]),
        (["project", "run", "."], ["/opt/airfield-init.sh", "/bin/bash", "-l"]),
        (["package", "cmd", ".", "--", "echo", "hi"], ["/opt/airfield-init.sh", "/opt/airfield-entry.sh", "echo hi"]),
    ],
)
def test_every_way_into_a_container_goes_through_the_init_script(cli_runner, stubbed_cli, mock_docker, argv, tail):
    mock_docker.return_value.returncode = 0
    result = cli_runner.invoke(app, argv)
    assert result.exit_code == 0, result.output

    command = mock_docker.call_args[0][0]
    assert command[-len(tail):] == tail
    image = command.index("test_image")
    assert f"AIRFIELD_UID={os.getuid()}" in command[:image], "the identity must come before the image name"
    assert "--user" not in command and "-u" not in command, "the container must start as root"


# --- the tag is a fingerprint of the recipe ---------------------------------------

def test_fingerprint_does_not_depend_on_who_builds_or_how(monkeypatch, mocker):
    builder = _ros_builder()
    tag = builder.fingerprint()
    assert len(tag) == 12 and all(c in "0123456789abcdef" for c in tag)

    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(os, "getgid", lambda: 1000)
    assert _ros_builder().fingerprint() == tag

    # An engine without BuildKit cache mounts builds the same image another way.
    mocker.patch.object(Builder, "_supports_cache_mounts", return_value=False)
    assert _ros_builder().fingerprint() == tag


def test_fingerprint_changes_with_anything_that_changes_the_image(monkeypatch):
    for name in ("AIRFIELD_TORCH_INSTALL_TARGET", "TORCH_INSTALL_TARGET", "AIRFIELD_PIP_CHECK"):
        monkeypatch.delenv(name, raising=False)
    base = _ros_builder().fingerprint()
    seen = {base}

    def differs(tag, why):
        assert tag not in seen, why
        seen.add(tag)

    one_more = Builder(Package(name="p", ros_distro="jazzy"), [Dependency(name="a", apt=["liba"])], "arm64")
    differs(one_more.fingerprint(), "another dependency")
    differs(_ros_builder(base_image="example/board-ros:1.0").fingerprint(), "another base image")
    differs(Builder(Package(name="p", ros_distro="jazzy"), _ros_builder().dependencies, "x86_64").fingerprint(), "another architecture")

    monkeypatch.setenv("AIRFIELD_TORCH_INSTALL_TARGET", "gpu")
    differs(_torch_builder().fingerprint(), "a package that installs torch, on a machine with a GPU")
    monkeypatch.delenv("AIRFIELD_TORCH_INSTALL_TARGET")
    differs(_torch_builder().fingerprint(), "the same package on a machine without one")

    monkeypatch.setattr(builder_module, "INIT_SCRIPT", INIT_SCRIPT + "# changed\n")
    differs(_ros_builder().fingerprint(), "another init script")
    monkeypatch.undo()
    for name in ("AIRFIELD_TORCH_INSTALL_TARGET", "TORCH_INSTALL_TARGET", "AIRFIELD_PIP_CHECK"):
        monkeypatch.delenv(name, raising=False)

    files = _ros_builder()._airfield_source_files()
    monkeypatch.setattr(Builder, "_airfield_source_files", lambda self: [*files, ("src/airfield/new_module.py", b"x = 1\n")])
    differs(_ros_builder().fingerprint(), "another version of Airfield in the image")
    monkeypatch.undo()
    for name in ("AIRFIELD_TORCH_INSTALL_TARGET", "TORCH_INSTALL_TARGET", "AIRFIELD_PIP_CHECK"):
        monkeypatch.delenv(name, raising=False)

    assert _ros_builder().fingerprint() == base, "and nothing else moves it"


def test_machine_settings_a_recipe_never_reads_do_not_split_the_fleet(monkeypatch):
    """One car exports TORCH_INSTALL_TARGET=gpu in its shell profile and the
    next does not. For a package that installs no torch the image is the same
    either way, so the two must arrive at the same tag."""
    for name in ("AIRFIELD_TORCH_INSTALL_TARGET", "TORCH_INSTALL_TARGET", "AIRFIELD_TORCH_GPU_WHL_TAG", "TORCH_GPU_WHL_TAG"):
        monkeypatch.delenv(name, raising=False)
    plain = _ros_builder().fingerprint()

    monkeypatch.setenv("TORCH_INSTALL_TARGET", "gpu")
    monkeypatch.setenv("AIRFIELD_TORCH_GPU_WHL_TAG", "cu121")

    assert _ros_builder().fingerprint() == plain
    assert ("TORCH_INSTALL_TARGET", "gpu", "TORCH_INSTALL_TARGET") in _ros_builder()._build_args(), "still passed to the build"


def test_only_airfields_own_package_goes_into_the_image():
    """Not the checkout around it: editing the README, the docs or a test, or
    leaving an editor's swap file behind, must not give every image a new tag."""
    names = [name for name, _ in _ros_builder()._airfield_source_files()]

    assert "pyproject.toml" in names and "src/airfield/main.py" in names
    assert all(name == "pyproject.toml" or name.startswith("src/airfield/") for name in names)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert names == sorted(names[:-1]) + ["pyproject.toml"], "a fixed order, so the fingerprint is stable"


# --- reuse, fetch or build ---------------------------------------------------------

class FakeDocker:
    """Just enough of docker's image store for build_package_image."""

    def __init__(self, monkeypatch, mocker):
        self.images = {}      # reference -> (image id, {label: value})
        self.pullable = {}    # reference -> (image id, labels)
        self.pushed = []
        self.removed = []
        self.builds = []
        monkeypatch.setattr(package_exec, "_docker", self.query)
        mocker.patch("airfield.cli.package_exec.subprocess.run", side_effect=self.run)
        mocker.patch("airfield.cli.package_exec.is_arm_mac", return_value=False)
        mocker.patch("airfield.cli.package_exec._validate_and_configure_host_dependencies")
        mocker.patch.object(Builder, "build", autospec=True, side_effect=self.build)

    # quiet queries, through package_exec._docker
    def query(self, *args):
        if args[:2] == ("image", "inspect"):
            fmt, reference = args[3], args[4]
            if reference not in self.images:
                return None
            image_id, labels = self.images[reference]
            return f"{image_id} {labels.get('airfield.base', '')}".strip() if "Labels" in fmt else image_id
        if args[0] == "images":
            repository = args[3]
            return "\n".join(ref.split(":", 1)[1] for ref in self.images if ref.split(":", 1)[0] == repository)
        if args[0] == "tag":
            self.images[args[2]] = self.images[args[1]]
            return ""
        if args[0] == "rmi":
            self.removed.append(args[1])
            self.images.pop(args[1], None)
            return ""
        raise AssertionError(f"unexpected docker query: {args}")

    # commands whose output the user sees, through subprocess.run
    def run(self, command, **kwargs):
        result = subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "pull"]:
            if command[2] in self.pullable:
                self.images[command[2]] = self.pullable[command[2]]
            else:
                result.returncode = 1
        elif command[:2] == ["docker", "tag"]:
            self.images[command[3]] = self.images[command[2]]
        elif command[:2] == ["docker", "push"]:
            self.pushed.append(command[2])
        else:
            raise AssertionError(f"unexpected docker command: {command}")
        return result

    def build(self, builder, context_dir, show_all_output=False, tag=None, labels=None, no_cache=False):
        self.builds.append({"tag": tag, "labels": labels, "no_cache": no_cache})
        name = f"airfield-pkg-{builder.package.name}"
        self.images[f"{name}:{tag}"] = self.images[f"{name}:latest"] = (f"sha256:built-{len(self.builds)}", dict(labels or {}))
        return True, f"{name}:{tag}"


@pytest.fixture
def docker(monkeypatch, mocker):
    return FakeDocker(monkeypatch, mocker)


BASE = "example/board-ros:1.0"


def _package(tmp_path, registry=None, pull_base_image=False):
    """A project package on a locally built base image, the way a car has it."""
    project = tmp_path / "proj"
    pkg_dir = project / "packages" / "nav"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    lines = ["kind: project", "name: proj", f"base_image: {BASE}", f"pull_base_image: {str(pull_base_image).lower()}"]
    if registry:
        lines.append(f"image_registry: {registry}")
    (project / "airfield.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (pkg_dir / "airfield.yaml").write_text("kind: package\nname: nav\nsource_path: .\nros_distro: jazzy\n", encoding="utf-8")
    return pkg_dir


def _build(pkg_dir, **kwargs):
    return build_package_image(pkg_dir, Package.load(pkg_dir / "airfield.yaml"), [], target_device="arm64", **kwargs)


def test_first_use_builds_and_names_the_image_after_its_recipe(tmp_path, docker, monkeypatch):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path)

    image = _build(pkg_dir)

    tag = docker.builds[0]["tag"]
    assert image == f"airfield-pkg-nav:{tag}"
    assert docker.builds == [{"tag": tag, "labels": {"airfield.tag": tag, "airfield.base": "sha256:base-1"}, "no_cache": False}]
    assert "airfield-pkg-nav:latest" in docker.images


def test_second_use_does_not_build(tmp_path, docker, monkeypatch, capsys):
    """Same recipe on the same base image: `docker build` would find every
    layer cached. Not running it saves each container start a few seconds."""
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path)
    first = _build(pkg_dir)
    capsys.readouterr()

    assert _build(pkg_dir) == first
    assert len(docker.builds) == 1
    assert f"[airfield] image is up to date: {first}" in capsys.readouterr().out


def test_rebuilt_base_image_still_rebuilds_the_package_image(tmp_path, docker, monkeypatch):
    """A local base image rebuilt under the same name used to reach every
    package image through docker's layer cache. It still has to."""
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path)
    _build(pkg_dir)

    docker.images[BASE] = ("sha256:base-2", {})
    _build(pkg_dir)

    assert [b["labels"]["airfield.base"] for b in docker.builds] == ["sha256:base-1", "sha256:base-2"]


def test_changed_recipe_builds_again_and_drops_the_old_tag(tmp_path, docker, monkeypatch):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    docker.images["airfield-pkg-nav:before-a-fix"] = ("sha256:kept-by-hand", {})
    docker.images["airfield-pkg-navigation:0123456789ab"] = ("sha256:another-package", {})
    pkg_dir = _package(tmp_path)
    old = _build(pkg_dir)

    pkg = Package.load(pkg_dir / "airfield.yaml")
    new = build_package_image(pkg_dir, pkg, [Dependency(name="a", apt=["liba"])], target_device="arm64")

    assert new != old and len(docker.builds) == 2
    assert docker.removed == [old]
    assert "airfield-pkg-nav:before-a-fix" in docker.images, "a tag someone made by hand is not ours to remove"
    assert "airfield-pkg-navigation:0123456789ab" in docker.images


def test_package_that_refreshes_its_base_image_builds_every_time_as_before(tmp_path, docker, monkeypatch):
    """With pull_base_image on (the default), only a build can tell whether
    the registry has a newer base. Nothing changes for such a package."""
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path, pull_base_image=True)

    _build(pkg_dir)
    _build(pkg_dir)

    assert len(docker.builds) == 2


def test_image_another_machine_built_is_fetched_instead_of_built(tmp_path, docker, monkeypatch, capsys):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-of-this-car", {})
    pkg_dir = _package(tmp_path, registry="ghcr.io/my-org/my-robot")
    tag = Builder(_loaded(pkg_dir), [], "arm64").fingerprint()
    remote = f"ghcr.io/my-org/my-robot:nav-{tag}"
    docker.pullable[remote] = ("sha256:built-on-another-car", {"airfield.base": "sha256:base-of-that-car"})

    image = _build(pkg_dir)

    assert image == f"airfield-pkg-nav:{tag}"
    assert docker.builds == []
    assert docker.images[image][0] == docker.images["airfield-pkg-nav:latest"][0] == "sha256:built-on-another-car"

    # ...and it stays in use, although this car's own base image is another copy.
    capsys.readouterr()
    assert _build(pkg_dir) == image
    assert docker.builds == []
    out = capsys.readouterr().out
    assert "is the shared image" in out and "airfield package build nav --rebuild" in out


def _loaded(pkg_dir):
    """The package as build_package_image sees it: with the project's base image."""
    pkg = Package.load(pkg_dir / "airfield.yaml")
    package_exec._apply_project_base_image_defaults(pkg, pkg_dir)
    return pkg


def test_recipe_nobody_has_built_is_built_here(tmp_path, docker, monkeypatch, capsys):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path, registry="ghcr.io/my-org/my-robot")

    _build(pkg_dir)

    assert len(docker.builds) == 1 and docker.pushed == []
    assert "no shared image for this recipe" in capsys.readouterr().out


def test_push_uploads_under_one_repository_with_the_package_in_the_tag(tmp_path, docker, monkeypatch):
    """One repository for the whole project, so the base image's layers are
    uploaded once instead of once per package."""
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path, registry="ghcr.io/my-org/my-robot")

    image = _build(pkg_dir, push=True)

    tag = image.rsplit(":", 1)[1]
    assert docker.pushed == [f"ghcr.io/my-org/my-robot:nav-{tag}"]
    # Having pushed it, this machine treats it as the shared image too.
    assert _build(pkg_dir) == image and len(docker.builds) == 1


def test_push_without_a_registry_is_an_error(tmp_path, docker, monkeypatch, capsys):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path)

    with pytest.raises(typer.Exit):
        _build(pkg_dir, push=True)

    assert "image_registry" in capsys.readouterr().out and docker.pushed == []


def test_rebuild_builds_from_scratch_whatever_exists(tmp_path, docker, monkeypatch):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    docker.images[BASE] = ("sha256:base-1", {})
    pkg_dir = _package(tmp_path, registry="ghcr.io/my-org/my-robot")
    tag = Builder(_loaded(pkg_dir), [], "arm64").fingerprint()
    docker.pullable[f"ghcr.io/my-org/my-robot:nav-{tag}"] = ("sha256:shared", {})
    _build(pkg_dir)
    assert docker.builds == []

    _build(pkg_dir, rebuild=True)

    assert [b["no_cache"] for b in docker.builds] == [True]


def test_failed_build_stops_the_command(tmp_path, docker, monkeypatch, mocker):
    monkeypatch.delenv("AIRFIELD_NO_PULL", raising=False)
    mocker.patch.object(Builder, "build", return_value=(False, "airfield-pkg-nav:latest"))
    with pytest.raises(typer.Exit):
        _build(_package(tmp_path))


def test_macos_container_engine_is_driven_as_before(tmp_path, docker, mocker):
    """No tags, no reuse, no sharing there: none of it has been tried on it."""
    mocker.patch("airfield.cli.package_exec.is_arm_mac", return_value=True)
    build = mocker.patch.object(Builder, "build", return_value=(True, "airfield-pkg-nav:latest"))
    pkg_dir = _package(tmp_path, registry="ghcr.io/my-org/my-robot")

    assert _build(pkg_dir) == "airfield-pkg-nav:latest"
    assert "tag" not in build.call_args.kwargs

    with pytest.raises(typer.Exit):
        _build(pkg_dir, push=True)


# --- where images are shared -------------------------------------------------------

def test_image_registry_comes_from_the_project_and_one_machine_can_override_it(tmp_path, monkeypatch):
    pkg_dir = _package(tmp_path, registry="ghcr.io/my-org/my-robot/")
    assert image_registry(pkg_dir) == "ghcr.io/my-org/my-robot"

    monkeypatch.setenv("AIRFIELD_IMAGE_REGISTRY", "localhost:5000/cars")
    assert image_registry(pkg_dir) == "localhost:5000/cars"
    monkeypatch.setenv("AIRFIELD_IMAGE_REGISTRY", "none")
    assert image_registry(pkg_dir) is None
    monkeypatch.delenv("AIRFIELD_IMAGE_REGISTRY")

    assert image_registry(_package(tmp_path / "other")) is None


def test_image_registry_must_not_name_a_tag(tmp_path):
    """The tag is where the package and its fingerprint go."""
    with pytest.raises(typer.BadParameter):
        image_registry(_package(tmp_path, registry="ghcr.io/my-org/my-robot:latest"))


def test_build_command_offers_push_and_rebuild(cli_runner, mocker):
    pkg = Package(name="nav")
    mocker.patch("airfield.cli.build.resolve_package_context", return_value=(Path("."), pkg, [], Path(".")))
    build = mocker.patch("airfield.cli.build.build_package_image", return_value="airfield-pkg-nav:abc")

    result = cli_runner.invoke(app, ["package", "build", "nav", "--push", "--rebuild"])

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["push"] is True and build.call_args.kwargs["rebuild"] is True


# --- the same thing for real, in a container ----------------------------------------
#
# The tests above cannot make an account, which takes root. These do it in a
# throwaway ubuntu container, and are skipped unless asked for:
#     AIRFIELD_TEST_DOCKER=1 pytest tests/test_shared_images.py -k container

needs_docker = pytest.mark.skipif(
    not os.environ.get("AIRFIELD_TEST_DOCKER") or shutil.which("docker") is None,
    reason="set AIRFIELD_TEST_DOCKER=1 to run the init script in real containers",
)


def _run_in_container(tmp_path, env, script, image="ubuntu:24.04", extra=()):
    init = tmp_path / "airfield-init.sh"
    init.write_text(INIT_SCRIPT, encoding="utf-8")
    init.chmod(0o755)
    command = ["docker", "run", "--rm", "--network", "none", "-v", f"{init}:/opt/airfield-init.sh:ro", *extra]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    command += [image, "/opt/airfield-init.sh", "bash", "-lc", script]
    return subprocess.run(command, capture_output=True, text=True)


@needs_docker
@pytest.mark.parametrize(
    "uid, gid, user",
    [
        (2002, 2002, "carol"),     # ids the image knows nothing about
        (1000, 1000, "alice"),     # ubuntu:24.04 already has 'ubuntu' as 1000
        (1000, 1000, "ubuntu"),    # ...and the caller may be called just that
        (4321, 20, "driver"),      # a group that exists (dialout), under another name
        (501, 20, "macuser"),      # the ids a macOS login has
        (0, 0, "root"),            # the caller is root (a CI job, sudo)
        (1500000000, 1500000000, "bigid"),  # ids handed out by a directory service
    ],
)
def test_container_runs_the_command_as_the_caller(tmp_path, uid, gid, user):
    env = {"AIRFIELD_UID": uid, "AIRFIELD_GID": gid, "AIRFIELD_USER": user, "AIRFIELD_HOME": f"/home/{user}"}
    result = _run_in_container(
        tmp_path, env,
        'echo "$(id -u):$(id -g):$(id -un):$HOME:$USER"; touch "$HOME/writable" && echo home-ok; '
        'stat -c %U:%G "$HOME" "$HOME/workspace/src"; id -G',
        extra=("--group-add", "44"),
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split()
    assert lines[0] == f"{uid}:{gid}:{user}:/home/{user}:{user}"
    assert lines[1] == "home-ok"
    assert lines[2].split(":")[0] == user and lines[3].split(":")[0] == user
    assert "44" in lines[4:], "a group added with --group-add must survive the switch"


@needs_docker
def test_container_leaves_mounted_files_alone(tmp_path):
    """A directory or file mounted into the home keeps its host owner and
    content, even where the init script would otherwise have put its own."""
    mounted = tmp_path / "bashrc"
    mounted.write_text("# from the host\n", encoding="utf-8")
    env = {"AIRFIELD_UID": 2002, "AIRFIELD_GID": 2002, "AIRFIELD_USER": "carol", "AIRFIELD_HOME": "/home/carol"}
    result = _run_in_container(
        tmp_path, env, 'cat "$HOME/.bashrc"; ls -a "$HOME" | tr "\\n" " "',
        extra=("-v", f"{mounted}:/home/carol/.bashrc"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# from the host\n")
    assert mounted.read_text() == "# from the host\n" and mounted.stat().st_uid == os.getuid()


@needs_docker
def test_container_without_an_identity_runs_as_it_is(tmp_path):
    result = _run_in_container(tmp_path, {}, "id -u")
    assert (result.returncode, result.stdout.strip()) == (0, "0")
