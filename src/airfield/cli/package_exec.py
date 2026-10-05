import os
import posixpath
import pwd
import re
import glob
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from string import Template
from typing import List, Optional, Tuple

import typer
import yaml
from pydantic import TypeAdapter, ValidationError

from airfield.builder import Builder
from airfield.config import AIRFIELD_CONFIG, AIRFIELD_LOCAL_CONFIG, _load_yaml, dependencies_dir, dependency_search_paths, find_project_root, packages_dir, require_package_root, is_arm_mac
from airfield.dependency_resolver import MISSING, resolve_dependencies
from airfield.host_check import detect_host_facts, evaluate_host_dependencies
from airfield.models import Dependency, Package, SUPPORTED_ROS_DISTROS


def run_container_foreground(run_cmd: List[str]) -> int:
    """Run a container in the foreground, tearing it down if we're interrupted.

    `docker run` without a TTY proxies our signal to PID 1 (``bash -lc ...``), but a
    non-interactive bash does not forward it to the workload, so on Ctrl-C, SIGTERM,
    or SIGHUP (tmux kill-server / terminal close)
    the container survives as an orphan that keeps holding host resources (e.g. the
    CSI camera's single Argus capture session, which then makes the next run fail
    with ``Failed to create CaptureSession``). To make interruption reliable
    regardless of how the in-container process tree handles signals, we name the
    container and stop it explicitly: ``docker stop`` SIGTERMs PID 1 and SIGKILLs the
    whole container after a short grace period, and ``--rm`` reaps it.

    Only the ``docker`` engine is handled specially; any other engine (e.g. the
    arm64 macOS ``container`` runtime) falls through to an unchanged blocking run.
    Returns the child's exit code.
    """
    if not run_cmd or run_cmd[0] != "docker":
        return subprocess.run(run_cmd).returncode

    name = f"airfield-run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    # insert `--name <name>` immediately after the `run` subcommand
    named_cmd = [run_cmd[0], run_cmd[1], "--name", name, *run_cmd[2:]]

    def _graceful_sigint() -> None:
        # Ask the in-container workload to shut down the way ROS nodes and
        # hardware drivers expect -- SIGINT -- BEFORE docker's SIGTERM/SIGKILL.
        # PID 1 is a non-interactive `bash -lc ...` wrapper that does NOT forward
        # signals to the process it launched, so a plain `docker stop` never
        # reaches the node: e.g. the RPLIDAR driver stops its motor only from its
        # SIGINT handler, so without this it is SIGKILLed with the disk still
        # spinning. SIGINT every process EXCEPT PID 1 (kept alive so the
        # container stays up while its children clean up) -- reaching the
        # grandchild nodes the bash parent would otherwise swallow signals for --
        # then give them a moment to run cleanup before we fall through to
        # `docker stop`. Best-effort: if the container is already gone this no-ops.
        subprocess.run(
            [
                "docker", "exec", name, "sh", "-c",
                'for p in /proc/[0-9]*; do pid=${p#/proc/}; '
                '[ "$pid" = 1 ] || kill -INT "$pid" 2>/dev/null; done',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        time.sleep(1.0)

    def _stop() -> None:
        _graceful_sigint()
        subprocess.run(
            ["docker", "stop", "-t", "2", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    proc = subprocess.Popen(named_cmd)

    def _on_term(_signum, _frame) -> None:
        # `timeout` sends SIGTERM to us (not the container); stop it ourselves.
        _stop()

    previous_term = signal.signal(signal.SIGTERM, _on_term)
    # `tmux kill-server` / closing the terminal sends SIGHUP. Python's default
    # action terminates us without running handlers or `finally`, so the docker
    # client dies but the container survives as an orphan (still holding e.g. a
    # display socket or camera session). Trap it like SIGTERM — unless SIGHUP is
    # already ignored (`nohup`), which we must not override.
    previous_hup = signal.getsignal(signal.SIGHUP)
    if previous_hup is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _on_term)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Ctrl-C reached the docker client but not the in-container workload.
        _stop()
        return proc.wait()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if previous_hup is not signal.SIG_IGN:
            signal.signal(signal.SIGHUP, previous_hup)


INIT_SCRIPT_PATH = "/opt/airfield-init.sh"


def user_setup_args() -> List[str]:
    """Container env args naming who the command runs as: the caller.

    An image holds no account for the person using it (it could not be shared
    between logins and machines if it did). /opt/airfield-init.sh, which every
    command is routed through, reads these and makes the account when the
    container starts: same name and ids as on the host, so files written to
    the mounts are the caller's, and the same home path the mounts and the
    plans' ``$HOME`` already point at.
    """
    uid = os.getuid()
    return [
        "-e", f"AIRFIELD_UID={uid}",
        "-e", f"AIRFIELD_GID={os.getgid()}",
        "-e", f"AIRFIELD_USER={pwd.getpwuid(uid).pw_name}",
        "-e", f"AIRFIELD_HOME={container_home()}",
    ]


def entry_wrap_args(pkg: Optional[Package], command_text: str) -> Tuple[List[str], List[str]]:
    """Container env args + command vector for `package cmd` / `package run`.

    Every command starts in /opt/airfield-init.sh, which sets up the caller's
    account (see user_setup_args) and hands over to the rest as that user.

    ROS packages then route through /opt/airfield-entry.sh (baked into their
    image), which builds the target colcon package into the shared workspace
    if it is not built yet — a no-op for apt-only tool packages and
    already-built ones — then execs a login shell running the command. Non-ROS
    packages run the login shell directly (their image has no entry script).
    """
    env_args = user_setup_args()
    if pkg is not None and pkg.ros_distro:
        env_args.extend(["-e", f"AIRFIELD_BUILD_PKG={pkg.name}"])
        if pkg.colcon_args:
            env_args.extend(["-e", f"AIRFIELD_COLCON_ARGS={pkg.colcon_args}"])
        return env_args, [INIT_SCRIPT_PATH, "/opt/airfield-entry.sh", command_text]
    return env_args, [INIT_SCRIPT_PATH, "/bin/bash", "-lc", command_text]


def shell_wrap_args() -> Tuple[List[str], List[str]]:
    """Container env args + command vector for an interactive login shell."""
    return user_setup_args(), [INIT_SCRIPT_PATH, "/bin/bash", "-l"]


def _resolve_package_ros_distro(pkg: Package, project_root: Optional[Path]) -> Optional[str]:
    del project_root

    if pkg.ros_distro is None:
        return None

    ros_distro = pkg.ros_distro.strip().lower()
    if not ros_distro:
        pkg.ros_distro = None
        return None

    if ros_distro not in SUPPORTED_ROS_DISTROS:
        raise typer.BadParameter(
            f"Unsupported ROS distribution '{ros_distro}'. Supported values: {', '.join(sorted(SUPPORTED_ROS_DISTROS))}"
        )
    pkg.ros_distro = ros_distro
    return ros_distro


def find_shared_package_definition(
    name: str, search_paths: List[Path]
) -> Optional[Tuple[Path, dict]]:
    """A shared *package definition* is a manifest in the dependency search
    paths that explicitly declares `kind: package` (dependency manifests have
    no kind). It is for packages a project USES as-is but does not develop —
    e.g. an AprilTag detector or a foxglove bridge — so they can live once in
    the shared packages repository instead of being vendored into every
    project. Packages under active development belong in the project itself;
    they should never round-trip through the shared repo to be edited.
    Strict kind check only — no duck-typing on which keys happen to exist."""
    for sp in search_paths:
        candidate = sp / f"{name}.yaml"
        if candidate.exists():
            data = _load_yaml(candidate)
            if isinstance(data, dict) and data.get("kind") == "package":
                return candidate, data
    return None


def _materialize_shared_package(
    package_name: Optional[str], root: Optional[Path], search_paths: List[Path]
) -> Optional[Path]:
    """Turn a shared package definition into a real packages/<name>/ directory.

    Everything downstream (source mounts, .air, workdir) assumes a package
    directory exists, so a definition is never run from the manifests folder;
    it is materialized into the project first: clone its `source: {url, ref}`
    if declared, otherwise scaffold an empty source dir, then write the
    definition (minus `source`) as the package's airfield.yaml. Loud but
    unprompted — it only creates a directory inside the project.

    Because these are use-only tools (not packages the project develops), the
    materialized directory is gitignored: it is reproducible from the shared
    definition — delete it to re-materialize/update — and a fresh checkout of
    the project regains it automatically on first use.
    """
    if not package_name:
        return None
    found = find_shared_package_definition(package_name, search_paths)
    if found is None:
        return None
    manifest_path, data = found

    if root is None:
        print(f"Error: '{package_name}' is a shared package definition ({manifest_path}),")
        print("which can only be materialized inside an Airfield project (packages/ dir needed).")
        raise typer.Exit(1)

    dest = packages_dir(root) / package_name
    if dest.exists():
        print(f"Error: cannot materialize shared package '{package_name}': {dest} already exists.")
        raise typer.Exit(1)

    data = dict(data)
    source = data.pop("source", None) or {}
    source_url = str(source.get("url", "")).strip()

    print(f"Package '{package_name}' is not in this project; materializing the shared")
    print(f"definition from {manifest_path} into {dest}")

    if source_url:
        # Sourced definition: clone becomes the package dir (wrap-style).
        data.setdefault("source_path", ".")
        clone_cmd = ["git", "clone"]
        ref = str(source.get("ref", "")).strip()
        if ref:
            clone_cmd.extend(["--branch", ref])
        clone_cmd.extend([source_url, str(dest)])
        result = subprocess.run(clone_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            details = (result.stderr or result.stdout or "unknown error").strip()
            print(f"Error: failed to clone {source_url}: {details}")
            raise typer.Exit(1)
    else:
        # Config-only definition (e.g. a containerized tool): no source.
        data.setdefault("source_path", "src")
        (dest / data["source_path"]).mkdir(parents=True, exist_ok=True)

    (dest / AIRFIELD_CONFIG).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    from airfield.cli.proj_init import _ensure_gitignore_entry

    _ensure_gitignore_entry(root, f"packages/{package_name}/")
    print(f"Materialized packages/{package_name}; it now behaves like any local package.")
    print(f"Added packages/{package_name}/ to the project .gitignore (use-only tool,")
    print("reproducible from the shared definition — delete the directory to refresh it).")
    return dest


def resolve_package_context(
    package_name: Optional[str],
    target_device: str = "x86_64",
) -> Tuple[Path, Package, List[Dependency], Path]:
    root = find_project_root()

    if root is not None:
        if package_name is None:
            pkg_dir = require_package_root()
        else:
            candidate = Path(package_name).expanduser()
            # Only treat a CWD-relative path as the package if it's actually an
            # airfield package (has airfield.yaml). Otherwise a same-named non-
            # package dir at the project root (e.g. `rviz2/` configs or the
            # `simulator/` Unity build) would shadow `packages/<name>`.
            if candidate.exists() and (candidate / AIRFIELD_CONFIG).exists():
                pkg_dir = candidate.resolve()
            else:
                pkg_dir = (packages_dir(root) / package_name).resolve()
        search_paths = dependency_search_paths(root, target_device)
    else:
        if package_name is not None:
            pkg_dir = Path(package_name).expanduser().resolve()
        else:
            pkg_dir = require_package_root()
        # The CWD isn't inside a project, but the package we were pointed at may
        # still live in one (e.g. `airfield package run ~/ws/packages/foo ...`
        # run from $HOME). Re-anchor on the package so its dependency manifests
        # and peer-package sources resolve exactly as they do from inside the
        # tree -- docker_mount_args() already derives its root from pkg_dir, and
        # a root here is what lets peer deps be built from source at all.
        root = find_project_root(pkg_dir)
        search_paths = dependency_search_paths(root or pkg_dir, target_device)

    pkg_yaml = pkg_dir / AIRFIELD_CONFIG
    if not pkg_yaml.exists():
        materialized = _materialize_shared_package(package_name, root, search_paths)
        if materialized is None:
            raise typer.BadParameter(
                f"Package config not found at {pkg_dir / AIRFIELD_CONFIG}"
            )
        pkg_dir = materialized
        pkg_yaml = pkg_dir / AIRFIELD_CONFIG

    pkg = Package.load(pkg_yaml)
    _resolve_package_ros_distro(pkg, root)
    source_root = (pkg_dir / pkg.source_path).resolve()
    if not source_root.exists():
        raise typer.BadParameter(f"source_path '{pkg.source_path}' does not exist in {pkg_dir}")

    # airfield.yaml's list plus whatever the package.xml files under
    # source_path ask for; see dependency_resolver for how each name resolves.
    plan = resolve_dependencies(pkg, pkg_dir, source_root, root, search_paths)

    missing = plan.of_kind(MISSING)
    if missing:
        print(f"Error: Dependency '{missing[0].name}' manifest not found in search paths:")
        for sp in search_paths:
            print(f"  - {sp}")
        if root is not None:
            print(f"  - {packages_dir(root)} (peer packages)")
        print(f"It is listed in {missing[0].origin}. A name needs a .yaml manifest unless")
        print("it can be translated into an apt package, which Airfield only does for packages")
        print("that set ros_distro (through rosdep's table, else ros-<distro>-<name> or a")
        print("Debian-style name such as python3-numpy).")
        print("If this manifest was upstreamed recently, refresh your copy of the shared")
        print("repository with: airfield package dependencies pull")
        raise typer.Exit(1)

    # Held for the image build to print. Printing here would also write into
    # shell completion, which resolves the package to list its run commands.
    pkg._resolution_notes = plan.warnings()

    return pkg_dir, pkg, plan.dependencies, source_root


def _apply_project_base_image_defaults(pkg: Package, pkg_dir: Path) -> None:
    """Inherit the project's ``base_image`` and ``pull_base_image`` defaults.

    Lets a whole project pin one base image (e.g. a custom L4T image) in a single
    place — the project's ``airfield.yaml`` — instead of repeating it in every
    package's ``airfield.yaml``. An explicit per-package value still wins; a
    standalone package (no enclosing project) is unaffected and falls back to
    the ROS/ubuntu default as before.

    ``pull_base_image`` describes the project's base image, so it is inherited
    only by packages that don't set their own ``base_image``. A package that
    names a different image keeps the default (pull) unless it opts out itself;
    otherwise a project whose own base is local-only would silently stop
    refreshing that package's registry image.
    """
    if pkg.base_image:
        return
    root = find_project_root(pkg_dir)
    if root is None:
        return
    proj_cfg = root / AIRFIELD_CONFIG
    if not proj_cfg.exists():
        return
    try:
        data = yaml.safe_load(proj_cfg.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return
    default_base = data.get("base_image")
    if isinstance(default_base, str) and default_base.strip():
        pkg.base_image = default_base.strip()
    if pkg.pull_base_image is None and data.get("pull_base_image") is not None:
        try:
            pkg.pull_base_image = TypeAdapter(bool).validate_python(data["pull_base_image"])
        except ValidationError:
            raise typer.BadParameter(
                f"pull_base_image in {proj_cfg} must be true or false "
                f"(got {data['pull_base_image']!r})"
            )


def image_registry(pkg_dir: Path) -> Optional[str]:
    """Where images are shared between machines, or None when they are not.

    One repository holds every package's image, told apart by tag
    (``<repository>:<package>-<fingerprint>``), so the base image's layers are
    uploaded once and not once per package. Set ``image_registry:`` in the
    project's airfield.yaml; ``$AIRFIELD_IMAGE_REGISTRY`` overrides it on one
    machine, and switches sharing off there when set to ``none``.
    """
    value = os.environ.get("AIRFIELD_IMAGE_REGISTRY")
    if value is None:
        root = find_project_root(pkg_dir)
        data = _load_yaml(root / AIRFIELD_CONFIG) if root is not None else None
        value = (data or {}).get("image_registry")
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip("/")
    if not value or value.lower() in {"none", "off", "false", "0"}:
        return None
    if any(ch.isspace() for ch in value) or ":" in value.rsplit("/", 1)[-1]:
        raise typer.BadParameter(
            f"image_registry must be a repository name without a tag, such as "
            f"ghcr.io/my-org/my-robot (got {value!r})"
        )
    return value


_FINGERPRINT_TAG = re.compile(r"^[0-9a-f]{12}$")


def _docker(*args: str) -> Optional[str]:
    """stdout of a quiet docker command, or None if it failed."""
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return None
    return result.stdout.strip()


def _image_id(reference: str) -> Optional[str]:
    return _docker("image", "inspect", "--format", "{{.Id}}", reference) or None


def _reusable_image(builder: Builder, local: str, remote: Optional[str]) -> Optional[str]:
    """The image already on this machine for this recipe, if it can be used
    as it is. None means it has to be fetched or built."""
    described = _docker(
        "image", "inspect", "--format", '{{.Id}} {{index .Config.Labels "airfield.base"}}', local
    )
    if not described:
        return None
    image_id, _, built_on = described.partition(" ")
    base_now = _image_id(builder.base_image)

    if remote and _image_id(remote) == image_id:
        # The image the registry holds for this recipe is the one every
        # machine sharing it runs. That is the point, so it stays in use even
        # where this machine's copy of the base image has moved on.
        if base_now and built_on and built_on != base_now:
            print(
                f"[airfield] {local} is the shared image; it was built on a different copy of "
                f"{builder.base_image} than this machine has. To build it here instead: "
                f"airfield package build {builder.package.name} --rebuild"
            )
        return local

    pull, _ = builder._resolve_pull()
    if pull:
        # This package's builds refresh its base image from its registry.
        # Only a build can tell whether there is a newer one.
        return None
    # Same recipe on the same base image: exactly the case in which
    # `docker build` would find every layer cached and change nothing.
    if base_now is None or built_on == base_now:
        return local
    return None


def _pull_shared_image(remote: str, local: str, latest: str) -> Optional[str]:
    """Fetch another machine's build of this recipe. None if there is none."""
    # flush: docker writes straight to the terminal, and its lines must not
    # overtake ours when the output is piped or logged.
    print(f"[airfield] looking for a shared image: {remote}", flush=True)
    if subprocess.run(["docker", "pull", remote], check=False).returncode != 0:
        print("[airfield] no shared image for this recipe (or the registry is out of reach); building it here.")
        return None
    for name in (local, latest):
        subprocess.run(["docker", "tag", remote, name], check=False)
    print(f"[airfield] using the shared image as {local}")
    return local


def _push_shared_image(local: str, remote: Optional[str]) -> None:
    if remote is None:
        print("Error: --push needs somewhere to push to. Set image_registry: in the project's")
        print("airfield.yaml (a repository name such as ghcr.io/my-org/my-robot), or")
        print("$AIRFIELD_IMAGE_REGISTRY on this machine, and log in to it with `docker login`.")
        raise typer.Exit(1)
    print(f"[airfield] pushing {local} as {remote}", flush=True)
    if subprocess.run(["docker", "tag", local, remote], check=False).returncode != 0:
        raise typer.Exit(1)
    if subprocess.run(["docker", "push", remote], check=False).returncode != 0:
        print(f"Error: could not push {remote}. Is this machine logged in to the registry (docker login)?")
        raise typer.Exit(1)


def _drop_older_tags(package_name: str, tag: str, registry: Optional[str]) -> None:
    """Untag this package's images for recipes it no longer has.

    Each recipe gets its own tag, so without this every past recipe's image
    would stay on disk for good. An image a running container still uses is
    left alone (docker refuses to remove it).
    """
    stale: List[str] = []
    for line in (_docker("images", "--format", "{{.Tag}}", f"airfield-pkg-{package_name}") or "").splitlines():
        if _FINGERPRINT_TAG.match(line.strip()) and line.strip() != tag:
            stale.append(f"airfield-pkg-{package_name}:{line.strip()}")
    if registry:
        prefix = f"{package_name}-"
        for line in (_docker("images", "--format", "{{.Tag}}", registry) or "").splitlines():
            line = line.strip()
            if line.startswith(prefix) and _FINGERPRINT_TAG.match(line[len(prefix):]) and line != f"{prefix}{tag}":
                stale.append(f"{registry}:{line}")
    for reference in stale:
        _docker("rmi", reference)


def build_package_image(
    pkg_dir: Path,
    pkg: Package,
    deps: List[Dependency],
    target_device: str = "x86_64",
    show_all_output: bool = False,
    push: bool = False,
    rebuild: bool = False,
) -> str:
    """Make sure the package's image exists and return the name to run it by.

    The image is named after its recipe (``airfield-pkg-<name>:<fingerprint>``,
    see Builder.fingerprint). So, in order: if this machine already has the
    image for this recipe, it is used as it is; else, if the project shares
    images through a registry and another machine has built this recipe, that
    build is fetched; else it is built here. ``rebuild`` skips the first two
    and builds from scratch. ``push`` uploads the result for other machines.
    """
    for note in pkg._resolution_notes:
        print(f"[WARN] {note}")
    _apply_locked_dependency_versions(pkg)
    _apply_project_base_image_defaults(pkg, pkg_dir)
    _validate_and_configure_host_dependencies(pkg, deps)

    builder = Builder(package=pkg, dependencies=deps, target_device=target_device)

    if is_arm_mac():
        # Apple's `container` engine: build every time, as before. Image
        # reuse and sharing are implemented for docker only.
        if push:
            print("Error: --push is only supported with the docker engine.")
            raise typer.Exit(1)
        success, image_name = builder.build(context_dir=pkg_dir, show_all_output=show_all_output)
        if not success:
            raise typer.Exit(1)
        return image_name

    tag = builder.fingerprint()
    local = f"airfield-pkg-{pkg.name}:{tag}"
    latest = f"airfield-pkg-{pkg.name}:latest"
    registry = image_registry(pkg_dir)
    remote = f"{registry}:{pkg.name}-{tag}" if registry else None

    image: Optional[str] = None
    if not rebuild:
        image = _reusable_image(builder, local, remote)
        if image is not None:
            print(f"[airfield] image is up to date: {image}")
        elif remote is not None:
            image = _pull_shared_image(remote, local, latest)
            if image is not None:
                _drop_older_tags(pkg.name, tag, registry)

    if image is None:
        success, image = builder.build(
            context_dir=pkg_dir,
            show_all_output=show_all_output,
            tag=tag,
            # What the image was built from, for _reusable_image to compare
            # with what this machine has later.
            labels={"airfield.tag": tag, "airfield.base": _image_id(builder.base_image) or ""},
            no_cache=rebuild,
        )
        if not success:
            raise typer.Exit(1)
        _drop_older_tags(pkg.name, tag, registry)

    if push:
        _push_shared_image(local, remote)
    return image


def _is_non_interactive() -> bool:
    if os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return not sys.stdin.isatty()


def _normalize_dep_env_name(dep_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", dep_name).upper().strip("_")


def _set_env_default(env_name: str, value: str) -> None:
    if not os.environ.get(env_name):
        os.environ[env_name] = value


def _apply_locked_dependency_versions(pkg: Package) -> None:
    for dep_name, constraint in pkg.dependency_constraints.items():
        dep_key = _normalize_dep_env_name(dep_name)
        exact_version_match = re.match(r"^==\s*([0-9]+(?:\.[0-9]+){0,2})$", constraint.strip())
        if exact_version_match is None:
            continue

        exact_version = exact_version_match.group(1)
        _set_env_default(f"AIRFIELD_DEP_{dep_key}_VERSION", exact_version)

        # Backward-compatible convenience alias for existing torch installer hooks.
        if dep_name.strip().lower() == "torch":
            _set_env_default("AIRFIELD_TORCH_VERSION", exact_version)


def _resolve_torch_install_target() -> str:
    explicit = os.environ.get("AIRFIELD_TORCH_INSTALL_TARGET") or os.environ.get("TORCH_INSTALL_TARGET")
    if explicit:
        target = explicit.strip().lower()
        return "gpu" if target == "gpu" else "cpu"

    facts = detect_host_facts()
    if facts.has_nvidia_gpu:
        os.environ["AIRFIELD_TORCH_INSTALL_TARGET"] = "gpu"
        if facts.suggested_torch_cuda_tag:
            _set_env_default("AIRFIELD_TORCH_GPU_WHL_TAG", facts.suggested_torch_cuda_tag)
        return "gpu"

    os.environ["AIRFIELD_TORCH_INSTALL_TARGET"] = "cpu"
    return "cpu"


def _print_host_issues(issues) -> None:
    print("Host dependency checks found issues:")
    for issue in issues:
        level = "ERROR" if issue.required else "WARN"
        print(f" - [{level}] {issue.dependency_name}:{issue.requirement_name} -> {issue.message}")
        if issue.install_hint:
            print(f"   hint: {issue.install_hint}")


def _validate_and_configure_host_dependencies(pkg: Package, deps: List[Dependency]) -> None:
    install_target = _resolve_torch_install_target()
    facts, issues = evaluate_host_dependencies(deps, install_target=install_target)

    if install_target == "gpu" and facts.suggested_torch_cuda_tag:
        _set_env_default("AIRFIELD_TORCH_GPU_WHL_TAG", facts.suggested_torch_cuda_tag)

    if not issues:
        return

    required_issues = [issue for issue in issues if issue.required]
    if not required_issues:
        _print_host_issues(issues)
        return

    if _is_non_interactive():
        # Safe default in CI/non-interactive mode: use CPU wheels when host GPU deps fail.
        if install_target == "gpu":
            print("Required GPU host dependencies are not satisfied. Falling back to CPU install mode.")
            os.environ["AIRFIELD_TORCH_INSTALL_TARGET"] = "cpu"
            _, cpu_issues = evaluate_host_dependencies(deps, install_target="cpu")
            blocking_cpu_issues = [issue for issue in cpu_issues if issue.required]
            if blocking_cpu_issues:
                _print_host_issues(blocking_cpu_issues)
                raise typer.Exit(1)
            if cpu_issues:
                _print_host_issues(cpu_issues)
            return

        _print_host_issues(required_issues)
        raise typer.Exit(1)

    _print_host_issues(required_issues)
    print("Please install or upgrade missing host dependencies before building.")
    confirmed = typer.confirm("Continue build anyway?", default=False)
    if not confirmed:
        raise typer.Exit(1)


def container_home() -> str:
    """In-container HOME for the caller: ``/home/<login name>``. The image's
    init script creates it when the container starts (see user_setup_args)."""
    username = pwd.getpwuid(os.getuid()).pw_name
    return f"/home/{username}"


def container_source_mount_path(package_name: str) -> str:
    return f"{container_home()}/workspace/src/{package_name}"


# The colcon workspace dirs that must outlive a single container. Containers run
# with `--rm`, so anything not mounted is discarded when the pane exits.
SHARED_WORKSPACE_DIRS = ("build", "install", "log")


def host_workspace_root(project_root: Optional[Path] = None) -> Optional[Path]:
    """Host directory backing the container's ``~/workspace``.

    Scoped to the project: ``<project>/.airfield/workspace``. One machine-wide
    workspace would let unrelated projects share build output by package name.
    Two projects that each define a ``base_driver`` would resolve to one
    ``install/base_driver``, so the second project's entry script finds the name
    already built, skips the build, and silently sources the first project's
    binaries. Scoping the root makes that unrepresentable rather than
    documented.

    ``.airfield/`` is already the project's scratch directory (``project up``
    writes tmuxinator configs there) and ``project init`` adds it to
    ``.gitignore``, so build output stays out of version control by default.

    Packages outside any project have no root to scope to and fall back to
    ``$HOME/workspace``. Set ``AIRFIELD_WORKSPACE`` to an absolute path to
    relocate the root, which is also how several projects can deliberately
    share one, or to ``none``/empty to opt out and get a throwaway
    per-container workspace (see ``shared_workspace_mounts``).
    """
    override = os.environ.get("AIRFIELD_WORKSPACE")
    if override is not None:
        override = override.strip()
        if not override or override.lower() == "none":
            return None
        return Path(override).expanduser()

    if project_root is not None:
        return project_root / ".airfield" / "workspace"
    return Path.home() / "workspace"


def shared_workspace_mounts(project_root: Optional[Path] = None) -> List[Tuple[Path, str]]:
    """Host->container mounts that persist ``~/workspace/{build,install,log}``.

    Without these the workspace is container-local, and because every run path
    uses ``docker run --rm`` it is destroyed when the pane exits. Two things
    then break at once, both silently:

    1. Every pane recompiles from scratch — the entry script's
       "already built?" check (``[ ! -e install/$pkg ]``) can never see another
       container's output.
    2. The build serialization is defeated. The entry script serializes
       concurrent builds on ``log/.airfield_build.lock``, which only holds panes
       back because they all lock the same file. With a container-local ``log/``
       each pane gets a private copy, so every lock succeeds immediately, no pane
       ever waits, and they all compile at once — which is what OOM-reboots
       memory-lean hosts like a Jetson.

    The location is derived (from the project root, or ``$HOME`` for a
    standalone package), never configured, so this belongs in core rather than
    in per-machine ``.air`` config that a fresh clone does not have.

    The dirs are created host-side when missing: docker would otherwise create
    them itself as root, leaving the container's non-root user unable to write.
    """
    root = host_workspace_root(project_root)
    if root is None:
        return []

    mounts: List[Tuple[Path, str]] = []
    for name in SHARED_WORKSPACE_DIRS:
        host_dir = root / name
        try:
            host_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[WARN] Skipping shared workspace mount '{host_dir}': {exc}")
            continue
        mounts.append((host_dir, f"{container_home()}/workspace/{name}"))
    return mounts


def container_workdir(pkg: Package) -> str:
    """Resolve the default in-container working directory for a package run/shell."""
    source_mount = container_source_mount_path(pkg.name)
    raw = (pkg.default_workdir or ".").strip()

    if raw in {"", ".", "./"}:
        return source_mount
    if raw.startswith("/"):
        return raw
    return posixpath.normpath(f"{source_mount}/{raw}")


def _read_local_mounts(config_path: Path) -> List[str]:
    if not config_path.exists():
        return []

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise typer.BadParameter(f"Failed to parse local config at {config_path}: {exc}")

    if data is None:
        return []
    if not isinstance(data, dict):
        raise typer.BadParameter(f"Local config at {config_path} must be a YAML mapping")

    mounts = data.get("mounts", [])
    if mounts is None:
        return []
    if not isinstance(mounts, list):
        raise typer.BadParameter(f"'mounts' in {config_path} must be a list")

    cleaned: List[str] = []
    for mount in mounts:
        if not isinstance(mount, str):
            raise typer.BadParameter(f"Each mount in {config_path} must be a string path")
        mount_path = mount.strip()
        if mount_path:
            cleaned.append(mount_path)
    return cleaned


def _configured_mounts(pkg_dir: Path) -> List[str]:
    mounts: List[str] = []
    project_root = find_project_root(pkg_dir)
    if project_root is not None:
        mounts.extend(_read_local_mounts(project_root / AIRFIELD_LOCAL_CONFIG))
    mounts.extend(_read_local_mounts(pkg_dir / AIRFIELD_LOCAL_CONFIG))
    return mounts


def _expand_mount_vars(mount: str) -> str:
    """Expand environment variables in a mount path, plus ``$UID``/``$GID``.

    ``.air`` is per-machine config, and some host paths embed the login user's
    numeric id -- e.g. gdm's Xauthority cookie lives under ``/run/user/<uid>``.
    Supporting ``$UID`` lets one documented snippet be copied onto every machine
    instead of each hardcoding its own number (which then silently mounts
    nothing on a host where the id differs).

    ``$UID`` is a shell variable that is never exported, so ``os.environ`` alone
    cannot resolve it -- inject the real ids. Unknown variables are left as-is
    rather than blanked, so a literal ``$`` in a path stays harmless.
    """
    values = {**os.environ, "UID": str(os.getuid()), "GID": str(os.getgid())}
    return Template(mount).safe_substitute(values)


def in_airfield_container() -> bool:
    """Check if currently running inside an Airfield-built container."""
    return os.environ.get("IN_AIRFIELD_CONTAINER") == "1"


def docker_mount_args(pkg_dir: Path, pkg: Package, source_root: Path, target_device: str) -> List[str]:
    """Build docker -v mount arguments from package source and config mounts."""
    mount_args: List[str] = []

    container_src = container_source_mount_path(pkg.name)
    mount_args.extend(["-v", f"{source_root}:{container_src}"])

    seen_mounts = {str(source_root)}
    # Container destinations are tracked separately: docker fails the whole run
    # with "Duplicate mount point" if two -v args target the same path, which is
    # reachable when a pre-existing .air already lists the workspace dirs below.
    seen_targets = {container_src}

    # Anchored on the package, not the CWD, so both the workspace root and the
    # peer mounts below resolve the same way wherever the command is invoked.
    root = find_project_root(pkg_dir)

    # Persist the colcon workspace across containers (build once, launch many)
    # and give the entry script's build lock a shared file to serialize on.
    for host_dir, container_dir in shared_workspace_mounts(root):
        if str(host_dir) in seen_mounts or container_dir in seen_targets:
            continue
        mount_args.extend(["-v", f"{host_dir}:{container_dir}"])
        seen_mounts.add(str(host_dir))
        seen_targets.add(container_dir)

    # Mount peer-package sources: project packages this one depends on that have
    # no dependency manifest, so they are built from source rather than
    # apt-installed. colcon needs them in the workspace alongside this package
    # (ut_automata does find_package(amrl_msgs)); --packages-up-to then compiles
    # them first. Resolved the same way as the image's dependencies, so a peer
    # named only in package.xml is mounted too.
    if root is not None:
        peers = resolve_dependencies(
            pkg, pkg_dir, source_root, root, dependency_search_paths(root, target_device)
        ).peers
        for peer_name, peer_src in peers:
            peer_target = container_source_mount_path(peer_name)
            if str(peer_src) in seen_mounts or not peer_src.exists():
                continue
            mount_args.extend(["-v", f"{peer_src}:{peer_target}"])
            seen_mounts.add(str(peer_src))
            seen_targets.add(peer_target)

    for mount in _configured_mounts(pkg_dir):
        mount_path = Path(_expand_mount_vars(mount)).expanduser()
        if not mount_path.is_absolute():
            mount_path = (pkg_dir / mount_path).resolve()
        else:
            mount_path = mount_path.resolve()

        mount_str = str(mount_path)
        if mount_str in seen_mounts or mount_str in seen_targets:
            continue

        if not mount_path.exists():
            print(f"[WARN] Skipping mount '{mount}': path does not exist (resolved to {mount_path})")
            continue

        # Files mount fine with docker -v (e.g. ~/.bash_history or a single
        # calibration file); only nonexistent paths are skipped.
        mount_args.extend(["-v", f"{mount_path}:{mount_path}"])
        seen_mounts.add(mount_str)
        seen_targets.add(mount_str)

    # Pass through declared host devices (e.g. VESC serial /dev/ttyACM0, joystick
    # /dev/input/js0) and supplementary groups (e.g. dialout) so nodes can access
    # hardware. Devices that aren't present are skipped so the container still
    # starts and the node reports the missing device itself.
    for dev in pkg.devices:
        if Path(dev).exists():
            mount_args.extend(["--device", dev])
        else:
            print(f"[WARN] Skipping device '{dev}': not present on host")
    for grp in pkg.group_add:
        mount_args.extend(["--group-add", grp])

    return mount_args


def _container_engine_alias() -> str:
    if is_arm_mac():
        return "container"
    docker_path = shutil.which("docker")
    if docker_path is None:
        return "docker"
    resolved = str(Path(docker_path).resolve()).lower()
    if "podman" in resolved:
        return "podman"
    if "singularity" in resolved:
        return "singularity"
    if "apptainer" in resolved:
        return "apptainer"
    return "docker"


def _is_jetson() -> bool:
    """Detect NVIDIA Jetson platforms (Tegra-based arm64 boards)."""
    return Path("/etc/nv_tegra_release").exists()


def gpu_runtime_args() -> List[str]:
    install_target = (os.environ.get("AIRFIELD_TORCH_INSTALL_TARGET") or os.environ.get("TORCH_INSTALL_TARGET") or "").strip().lower()
    # On Jetson the nvidia runtime is required for BASIC hardware access —
    # CSI cameras (nvarguscamerasrc), EGL, CUDA — not just for torch, so it
    # must not depend on a torch env var being set on the machine: plans have
    # to run identically on a fresh checkout. Elsewhere, GPU passthrough
    # remains opt-in via TORCH_INSTALL_TARGET=gpu.
    if install_target != "gpu" and not _is_jetson():
        return []

    # On Jetson/L4T, EGL and the Tegra GStreamer plugins (e.g. nvarguscamerasrc)
    # are only mounted into the container when the 'graphics'/'video'/'display'
    # driver capabilities are requested; 'compute,utility' alone leaves
    # libEGL.so.1 missing and CSI camera capture fails.
    driver_caps = "all" if _is_jetson() else "compute,utility"
    args: List[str] = [
        "-e", "NVIDIA_VISIBLE_DEVICES=all",
        "-e", f"NVIDIA_DRIVER_CAPABILITIES={driver_caps}",
    ]

    engine = _container_engine_alias()
    if engine == "docker":
        if _is_jetson():
            args.extend(["--runtime", "nvidia"])
        else:
            args.extend(["--gpus", "all"])

    # Mount the Argus camera daemon socket so CSI cameras work inside the container.
    if _is_jetson() and Path("/tmp/argus_socket").exists():
        args.extend(["-v", "/tmp/argus_socket:/tmp/argus_socket"])
    elif engine == "podman":
        for hook_dir in ("/usr/share/containers/oci/hooks.d", "/etc/containers/oci/hooks.d"):
            if Path(hook_dir).exists():
                args.extend(["--hooks-dir", hook_dir])
                break
        args.extend(["--security-opt", "label=disable"])

    device_candidates = {
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
        "/dev/nvidia-uvm-tools",
        "/dev/nvidia-modeset",
        *glob.glob("/dev/nvidia[0-9]*"),
    }
    # On Jetson, pass through V4L2 video nodes so CSI/USB cameras are visible
    # inside the container (e.g. nvarguscamerasrc / OpenCV capture).
    if _is_jetson():
        device_candidates.update(glob.glob("/dev/video[0-9]*"))
    for dev in sorted(device_candidates):
        if Path(dev).exists():
            args.extend(["--device", dev])

    return args
