"""Auto-build feature: in-container entry wrapper + colcon_args + project down."""
import os
import shutil
import signal
import subprocess
import time

import pytest

from airfield.builder import Builder, ENTRY_SCRIPT
from airfield.cli.package_exec import entry_wrap_args
from airfield.main import app
from airfield.models import Package


# --- entry_wrap_args -------------------------------------------------------

# Every command starts in the init script, which makes the caller's account in
# the container (tests/test_shared_images.py covers that part). What follows
# it is what these tests are about.

def _package_env(env_args):
    """The env args that are about the package, without the caller's identity."""
    pairs = list(zip(env_args[0::2], env_args[1::2]))
    assert all(flag == "-e" for flag, _ in pairs)
    return [value for _, value in pairs if not value.startswith(("AIRFIELD_UID=", "AIRFIELD_GID=", "AIRFIELD_USER=", "AIRFIELD_HOME="))]


def test_entry_wrap_non_ros_package_runs_plain_login_shell():
    pkg = Package(name="tool_pkg")
    env_args, cmd = entry_wrap_args(pkg, "echo hi")
    assert _package_env(env_args) == []
    assert cmd == ["/opt/airfield-init.sh", "/bin/bash", "-lc", "echo hi"]


def test_entry_wrap_ros_package_routes_through_entry_script():
    pkg = Package(name="ros_pkg", ros_distro="jazzy")
    env_args, cmd = entry_wrap_args(pkg, "ros2 run ros_pkg node")
    assert _package_env(env_args) == ["AIRFIELD_BUILD_PKG=ros_pkg"]
    assert cmd == ["/opt/airfield-init.sh", "/opt/airfield-entry.sh", "ros2 run ros_pkg node"]


def test_entry_wrap_passes_colcon_args_env():
    pkg = Package(name="ros_pkg", ros_distro="jazzy",
                  colcon_args="--cmake-args -DCMAKE_BUILD_MODE=Hardware")
    env_args, _ = entry_wrap_args(pkg, "true")
    assert _package_env(env_args) == [
        "AIRFIELD_BUILD_PKG=ros_pkg",
        "AIRFIELD_COLCON_ARGS=--cmake-args -DCMAKE_BUILD_MODE=Hardware",
    ]


# --- entry script content + Dockerfile wiring ------------------------------

def test_entry_script_is_valid_bash_and_has_guards(tmp_path):
    script = tmp_path / "entry.sh"
    script.write_text(ENTRY_SCRIPT, encoding="utf-8")
    # syntax check
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    # per-package build guard, serialization, and scoped build
    assert 'install/$pkg' in ENTRY_SCRIPT
    assert "flock" in ENTRY_SCRIPT
    assert '--packages-up-to "$pkg"' in ENTRY_SCRIPT
    # failures must not be swallowed
    assert "/dev/null" not in ENTRY_SCRIPT.split("colcon build")[1].split("\n")[0]


def test_dockerfile_copies_entry_script_for_ros_packages():
    builder = Builder(Package(name="p", ros_distro="jazzy"), [], "arm64")
    df = builder.generate_dockerfile(cache_mounts_enabled=False)
    assert "COPY airfield-entry.sh /opt/airfield-entry.sh" in df
    assert "chmod 755 /opt/airfield-entry.sh" in df


def test_dockerfile_omits_entry_script_for_non_ros_packages():
    builder = Builder(Package(name="p"), [], "arm64")
    df = builder.generate_dockerfile(cache_mounts_enabled=False)
    assert "airfield-entry.sh" not in df


# --- Package model ----------------------------------------------------------

def test_package_load_parses_colcon_args(tmp_path):
    cfg = tmp_path / "airfield.yaml"
    cfg.write_text(
        "kind: package\nname: p\nros_distro: jazzy\n"
        "colcon_args: --cmake-args -DCMAKE_BUILD_MODE=Hardware\n",
        encoding="utf-8",
    )
    pkg = Package.load(cfg)
    assert pkg.colcon_args == "--cmake-args -DCMAKE_BUILD_MODE=Hardware"


# --- project down -----------------------------------------------------------

@pytest.fixture
def project_with_plans(temp_workspace):
    (temp_workspace / "airfield.yaml").write_text(
        "kind: project\nname: proj\nversion: 0.1.0\n", encoding="utf-8"
    )
    plans = temp_workspace / "plans"
    plans.mkdir()
    (plans / "teleop.yaml").write_text("name: teleop\n", encoding="utf-8")
    (plans / "navstack.yaml").write_text("name: navstack\n", encoding="utf-8")
    return temp_workspace


def _mock_down_subprocess(mocker, sessions):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = mocker.Mock(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["tmux", "list-sessions"]:
            result.stdout = "\n".join(sessions) + "\n"
        elif cmd[:2] == ["docker", "ps"]:
            result.stdout = "abc123\ndef456\n"
        return result

    mocker.patch("airfield.cli.down.subprocess.run", side_effect=fake_run)
    return calls


def test_project_down_kills_only_plan_sessions(cli_runner, project_with_plans, mocker):
    calls = _mock_down_subprocess(mocker, sessions=["teleop", "unrelated"])
    result = cli_runner.invoke(app, ["project", "down"])
    assert result.exit_code == 0
    kills = [c for c in calls if c[:2] == ["tmux", "kill-session"]]
    assert kills == [["tmux", "kill-session", "-t", "teleop"]]


def test_project_down_named_plan_and_prune(cli_runner, project_with_plans, mocker):
    calls = _mock_down_subprocess(mocker, sessions=["teleop", "navstack"])
    result = cli_runner.invoke(app, ["project", "down", "navstack", "--prune"])
    assert result.exit_code == 0
    kills = [c for c in calls if c[:2] == ["tmux", "kill-session"]]
    assert kills == [["tmux", "kill-session", "-t", "navstack"]]
    rms = [c for c in calls if c[:3] == ["docker", "rm", "-f"]]
    assert rms == [["docker", "rm", "-f", "abc123", "def456"]]


@pytest.mark.parametrize("missing", ["tmux", "docker"])
def test_project_down_survives_missing_binaries(cli_runner, project_with_plans, mocker, missing):
    """`down` reaches for tmux and (with --prune) docker. Neither is guaranteed to
    exist on every host, and a missing one must report itself rather than crash
    the teardown with a FileNotFoundError traceback."""
    def fake_run(cmd, **kwargs):
        if cmd[0] == missing:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        return mocker.Mock(returncode=0, stdout="", stderr="")

    mocker.patch("airfield.cli.down.subprocess.run", side_effect=fake_run)

    result = cli_runner.invoke(app, ["project", "down", "--prune"])
    assert result.exit_code == 0
    assert not isinstance(result.exception, FileNotFoundError)
    assert "not found" in result.output


# --- the entry script's "is it built?" decision, run for real ---------------------
#
# install/<pkg> alone does not mean built: colcon creates the folder when it
# starts on a package. These run the script itself against a stand-in colcon
# that behaves that way. Only the fixed /opt/ros prefix is swapped for a
# temporary folder, so no ROS install and no container is needed.

FAKE_COLCON = r"""#!/bin/bash
# Stand-in for colcon: just enough for airfield-entry.sh.
echo "$*" >> "$FAKE/calls"
case "$1" in
list)
    if [[ " $* " == *" --packages-up-to "* ]]; then
        cat "$FAKE/up_to_${*: -1}" 2>/dev/null
    else
        cat "$FAKE/all"
    fi
    ;;
build)
    target="$3"
    for name in $(cat "$FAKE/up_to_$target"); do
        [ -e "install/$name/built" ] && continue
        mkdir -p "install/$name"    # as colcon does: before the work, not after
        [ -e "$FAKE/slow" ] && sleep "$(cat "$FAKE/slow")"
        [ -e "$FAKE/fail_$name" ] && exit 2
        : > "install/$name/built"
    done
    ;;
esac
"""

needs_flock = pytest.mark.skipif(
    shutil.which("flock") is None or shutil.which("bash") is None,
    reason="the entry script needs bash and flock (util-linux)",
)


class EntryWorkspace:
    """A home with ~/workspace/src, a fake ROS install and a fake colcon."""

    def __init__(self, root):
        self.home = root / "home"
        self.fake = root / "fake"
        self.workspace = self.home / "workspace"
        (self.workspace / "src").mkdir(parents=True)
        self.fake.mkdir()
        (root / "ros" / "fake").mkdir(parents=True)
        (root / "ros" / "fake" / "setup.bash").write_text("", encoding="utf-8")
        (root / "bin").mkdir()
        colcon = root / "bin" / "colcon"
        colcon.write_text(FAKE_COLCON, encoding="utf-8")
        colcon.chmod(0o755)
        self.script = root / "airfield-entry.sh"
        self.script.write_text(ENTRY_SCRIPT.replace("/opt/ros/", f"{root}/ros/"), encoding="utf-8")
        self.env = {
            "HOME": str(self.home), "ROS_DISTRO": "fake", "FAKE": str(self.fake),
            "PATH": f"{root / 'bin'}:/usr/bin:/bin",
        }
        self.packages({"pkg": ["pkg"]})

    def packages(self, up_to):
        """up_to: package -> what `colcon build --packages-up-to` it builds, in order."""
        (self.fake / "all").write_text("".join(f"{name}\n" for name in up_to), encoding="utf-8")
        for name, order in up_to.items():
            (self.fake / f"up_to_{name}").write_text("".join(f"{dep}\n" for dep in order), encoding="utf-8")

    def start(self, package="pkg", command="echo ran"):
        return subprocess.Popen(
            ["bash", str(self.script), command],
            env={**self.env, "AIRFIELD_BUILD_PKG": package},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )

    def run(self, package="pkg", command="echo ran"):
        process = self.start(package, command)
        out, err = process.communicate(timeout=60)
        return process.returncode, out, err

    def calls(self):
        path = self.fake / "calls"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        path.write_text("", encoding="utf-8")
        return lines

    def builds(self):
        return [line for line in self.calls() if line.startswith("build ")]

    def fail(self, name, failing=True):
        path = self.fake / f"fail_{name}"
        path.write_text("", encoding="utf-8") if failing else path.unlink()

    def unfinished(self):
        folder = self.workspace / "install" / ".airfield-unfinished"
        return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


@needs_flock
def test_entry_builds_once_and_then_does_not_even_ask_colcon(tmp_path):
    ws = EntryWorkspace(tmp_path)

    code, out, _ = ws.run()
    assert (code, out.splitlines()[-1]) == (0, "ran")
    assert "(first run)" in out and len(ws.builds()) == 1
    assert ws.unfinished() == []

    code, out, _ = ws.run()
    assert (code, out) == (0, "ran\n")
    assert ws.calls() == [], "`colcon list` costs most of a second per container start"


@needs_flock
def test_entry_tries_again_after_a_build_that_failed(tmp_path):
    """The failed build leaves install/<pkg> behind. Taking that folder for a
    finished build would run the command against a package that was never
    built, on every later run, until someone deleted the folder by hand."""
    ws = EntryWorkspace(tmp_path)
    ws.fail("pkg")

    code, out, err = ws.run()
    assert code == 1 and "ran" not in out and "build of 'pkg' failed" in err
    assert (ws.workspace / "install" / "pkg").is_dir(), "what colcon leaves behind"
    assert ws.unfinished() == ["pkg"]
    ws.calls()

    code, out, err = ws.run()
    assert code == 1 and "ran" not in out, "still broken: build again, do not run"
    assert "its last build did not finish" in out and len(ws.builds()) == 1

    ws.fail("pkg", False)
    code, out, _ = ws.run()
    assert (code, out.splitlines()[-1]) == (0, "ran")
    assert ws.unfinished() == [] and len(ws.builds()) == 1

    assert ws.run()[:2] == (0, "ran\n") and ws.calls() == []


@needs_flock
def test_entry_marks_the_packages_a_failed_build_was_about_to_create(tmp_path):
    """Another pane may run one of them. It has to see that its package is
    not built either, and not find the folder the failed build left."""
    ws = EntryWorkspace(tmp_path)
    ws.packages({"msgs": ["msgs"], "driver": ["driver"], "pkg": ["msgs", "driver", "pkg"]})
    (ws.workspace / "install" / "msgs").mkdir(parents=True)
    (ws.workspace / "install" / "msgs" / "built").write_text("", encoding="utf-8")
    ws.fail("driver")

    assert ws.run()[0] == 1
    assert ws.unfinished() == ["driver", "pkg"], "msgs was built before and stays usable"
    ws.calls()

    assert ws.run("msgs")[:2] == (0, "ran\n") and ws.calls() == []
    code, out, _ = ws.run("driver")
    assert code == 1 and "ran" not in out and len(ws.builds()) == 1


@needs_flock
def test_entry_leaves_a_package_built_before_markers_existed_alone(tmp_path):
    """A workspace made by an earlier Airfield, or by colcon run by hand, has
    no markers. Its packages are built and must not be compiled again."""
    ws = EntryWorkspace(tmp_path)
    (ws.workspace / "install" / "pkg").mkdir(parents=True)

    assert ws.run()[:2] == (0, "ran\n")
    assert ws.calls() == []


@needs_flock
def test_entry_waits_for_a_build_another_container_is_running(tmp_path):
    """Started while the first is still compiling, the second finds
    install/<pkg> already there. It must wait for the build and not run its
    command against a half-built package, and then not build a second time."""
    ws = EntryWorkspace(tmp_path)
    (ws.fake / "slow").write_text("2", encoding="utf-8")

    first = ws.start()
    deadline = time.time() + 20
    while not (ws.workspace / "install" / "pkg").exists():
        assert time.time() < deadline, "the first build never started"
        time.sleep(0.05)
    code, out, _ = ws.run(command='test -e "$HOME/workspace/install/pkg/built" && echo complete || echo half-built')
    first.communicate(timeout=60)

    assert first.returncode == 0
    assert (code, out.splitlines()[-1]) == (0, "complete")
    assert len(ws.builds()) == 1, "the second container found the work done"


@needs_flock
def test_entry_tries_again_after_a_build_that_was_killed(tmp_path):
    """Power loss, `docker kill`, the OOM killer: nothing gets to clean up."""
    ws = EntryWorkspace(tmp_path)
    (ws.fake / "slow").write_text("30", encoding="utf-8")

    process = ws.start()
    deadline = time.time() + 20
    while not (ws.workspace / "install" / "pkg").exists():
        assert time.time() < deadline, "the build never started"
        time.sleep(0.05)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate(timeout=20)
    assert ws.unfinished() == ["pkg"]
    ws.calls()

    (ws.fake / "slow").unlink()
    code, out, _ = ws.run()
    assert (code, out.splitlines()[-1]) == (0, "ran")
    assert len(ws.builds()) == 1 and ws.unfinished() == []
