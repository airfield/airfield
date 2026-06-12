import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from airfield.models import Dependency, Package
from airfield.build_progress import run_build_with_progress, with_plain_progress
from airfield.docker_cache import get_cache_optimization_comment
from airfield.config import is_arm_mac


ROS_BASE_IMAGES = {
    "noetic": "ros:noetic-ros-base",
    "humble": "osrf/ros:humble-desktop",
    "jazzy": "osrf/ros:jazzy-desktop",
}

DOCKER_PLATFORMS = {
    "arm64": "linux/arm64",
    "aarch64": "linux/arm64",
    "x86_64": "linux/amd64",
    "amd64": "linux/amd64",
}

ROS_CORE_PACKAGES = {
    "noetic": ["python3-catkin-tools"],
    "humble": ["python3-colcon-common-extensions"],
    "jazzy": ["python3-colcon-common-extensions"],
}

UBUNTU_BASE_IMAGE = "ubuntu:24.04"


class Builder:
    def __init__(self, package: Package, dependencies: List[Dependency], target_device: str):
        self.package = package
        self.dependencies = dependencies
        self.target_device = target_device
        self.ros_distro = self._resolve_ros_distro()
        self.base_image = self._resolve_base_image()

    def _resolve_ros_distro(self) -> Optional[str]:
        if self.package.ros_distro is None:
            return None

        ros_distro = self.package.ros_distro.strip().lower()
        if not ros_distro:
            return None
        if ros_distro not in ROS_BASE_IMAGES:
            raise ValueError(
                f"Unsupported ROS distribution '{ros_distro}'. Supported values: {', '.join(sorted(ROS_BASE_IMAGES))}"
            )
        return ros_distro

    def _resolve_base_image(self) -> str:
        if self.package.base_image:
            return self.package.base_image
        if self.ros_distro in {"jazzy", "humble"} and self.target_device.strip().lower() in {"arm64", "aarch64"}:
            return f"ros:{self.ros_distro}-ros-base"
        if self.ros_distro:
            return ROS_BASE_IMAGES[self.ros_distro]
        return UBUNTU_BASE_IMAGE

    def _resolve_docker_platform(self) -> Optional[str]:
        return DOCKER_PLATFORMS.get(self.target_device.strip().lower())

    def _find_airfield_repo(self, context_dir: Path) -> Optional[Path]:
        # Candidates for the airfield repository root:
        # 1. The context directory and its parents
        # 2. The directory where the airfield source code resides (3 levels up from this file: src/airfield/builder.py)
        candidates = [context_dir, *context_dir.parents]
        try:
            candidates.append(Path(__file__).resolve().parents[2])
        except (IndexError, ValueError):
            pass

        for candidate in candidates:
            if not candidate.exists():
                continue
            # Check if the candidate itself is the repo root
            if (candidate / "pyproject.toml").exists() and (candidate / "src" / "airfield").exists():
                return candidate
            # Check if there is an 'airfield' subdirectory that is the repo root
            repo_root = candidate / "airfield"
            if repo_root.exists() and (repo_root / "pyproject.toml").exists() and (repo_root / "src" / "airfield").exists():
                return repo_root
        return None

    def _supports_cache_mounts(self) -> bool:
        if is_arm_mac():
            return True
        # Manual override for troubleshooting/CI:
        # AIRFIELD_FORCE_DOCKER_CACHE_MOUNTS=1 -> enable
        # AIRFIELD_DISABLE_DOCKER_CACHE_MOUNTS=1 -> disable
        force = (os.environ.get("AIRFIELD_FORCE_DOCKER_CACHE_MOUNTS") or "").strip().lower()
        if force in {"1", "true", "yes", "on"}:
            return True

        disable = (os.environ.get("AIRFIELD_DISABLE_DOCKER_CACHE_MOUNTS") or "").strip().lower()
        if disable in {"1", "true", "yes", "on"}:
            return False

        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False

        version_text = f"{result.stdout}\n{result.stderr}".lower()
        if "podman" in version_text or "buildah" in version_text:
            return False

        try:
            buildx_result = subprocess.run(
                ["docker", "buildx", "version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self._print_buildkit_hint("docker buildx was not found")
            return False

        if buildx_result.returncode != 0:
            details = (buildx_result.stderr or buildx_result.stdout or "docker buildx version failed").strip()
            self._print_buildkit_hint(details)
            return False
        return True

    def _print_buildkit_hint(self, details: str) -> None:
        print("[WARN] Docker Buildx/BuildKit is not available; using compatibility mode.")
        if details:
            print(f"[WARN] Buildx check: {details}")
        print("[WARN] Install or enable Docker Buildx/BuildKit to use Airfield cache mounts.")

    def _apt_install_command(self, packages: List[str], cache_mounts_enabled: bool) -> str:
        command = "apt-get update && apt-get install -y " + " ".join(packages)
        if not cache_mounts_enabled:
            command += " && rm -rf /var/lib/apt/lists/*"
        return command

    def generate_dockerfile(self, install_local_airfield: bool = False, cache_mounts_enabled: bool = True) -> str:
        lines = []
        default_uid = os.getuid()
        default_gid = os.getgid()
        default_username = pwd.getpwuid(default_uid).pw_name
        
        # Add optimization comment at the top
        lines.append(get_cache_optimization_comment(cache_mounts_enabled=cache_mounts_enabled))
        lines.append("")
        
        lines.append(f"FROM {self.base_image}")
        lines.append("USER root")
        lines.append("ENV DEBIAN_FRONTEND=noninteractive")
        if self.ros_distro:
            lines.append(f"ENV ROS_DISTRO={self.ros_distro}")
        lines.append("ARG TORCH_INSTALL_TARGET=cpu")
        lines.append("ARG TORCH_VERSION=")
        lines.append("ARG TORCH_GPU_WHL_TAG=cu121")
        if is_arm_mac():
            lines.append("RUN echo 'Acquire::ForceIPv4 \"true\";' > /etc/apt/apt.conf.d/99force-ipv4")
        
        # Optimized apt-get with BuildKit cache mounts
        base_packages = ["python3-pip", "python3-opencv", "git", "zsh"]
        if self.ros_distro:
            base_packages.extend(ROS_CORE_PACKAGES[self.ros_distro])
        apt_install = self._apt_install_command(base_packages, cache_mounts_enabled=cache_mounts_enabled)
        if cache_mounts_enabled:
            lines.append(
                "RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \\\n"
                "    --mount=type=cache,target=/var/cache/apt,sharing=locked \\\n"
                f"    {apt_install}"
            )
        else:
            lines.append(f"RUN {apt_install}")

        if cache_mounts_enabled:
            lines.append(
                "RUN --mount=type=cache,target=/root/.cache/pip \\\n"
                "    python3 -m pip install --upgrade pip"
            )
        else:
            lines.append("RUN python3 -m pip install --upgrade pip")

        if install_local_airfield:
            lines.append("COPY airfield /opt/airfield")
            if cache_mounts_enabled:
                lines.append(
                    "RUN --mount=type=cache,target=/root/.cache/pip \\\n"
                    "    python3 -m pip install /opt/airfield || \\\n"
                    "    python3 -m pip install --break-system-packages /opt/airfield"
                )
            else:
                lines.append(
                    "RUN python3 -m pip install --no-cache-dir /opt/airfield || "
                    "python3 -m pip install --no-cache-dir --break-system-packages /opt/airfield"
                )
        else:
            if cache_mounts_enabled:
                lines.append(
                    "RUN --mount=type=cache,target=/root/.cache/pip \\\n"
                    "    python3 -m pip install airfield || \\\n"
                    "    python3 -m pip install --break-system-packages airfield"
                )
            else:
                lines.append(
                    "RUN python3 -m pip install --no-cache-dir airfield || "
                    "python3 -m pip install --no-cache-dir --break-system-packages airfield"
                )

        system_cmds = []
        for dep in self.dependencies:
            system_cmds.extend(dep.system)

        if system_cmds:
            for cmd in system_cmds:
                # Optimize apt-get commands in system dependencies
                if cmd.startswith("apt-get") or "apt-get" in cmd:
                    if "apt-get update" in cmd and cache_mounts_enabled:
                        lines.append(f"RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \\\n    --mount=type=cache,target=/var/cache/apt,sharing=locked \\\n    {cmd}")
                    else:
                        lines.append(f"RUN {cmd}")
                elif "pip" in cmd and "install" in cmd and cache_mounts_enabled:
                    lines.append(f"RUN --mount=type=cache,target=/root/.cache/pip \\\n    {cmd}")
                else:
                    lines.append(f"RUN {cmd}")

        lines.append(f"ARG USERNAME={default_username}")
        lines.append(f"ARG UID={default_uid}")
        lines.append(f"ARG GID={default_gid}")
        lines.append(
            "RUN set -e && "
            "(getent group $GID || groupadd -g $GID $USERNAME) >/dev/null && "
            "if id -u $UID >/dev/null 2>&1; then "
            "existing_user=$(id -nu $UID) && "
            "usermod -l $USERNAME -d /home/$USERNAME -m $existing_user 2>/dev/null || true && "
            "usermod -g $GID $USERNAME 2>/dev/null || true; "
            "else "
            "useradd --uid $UID --gid $GID -m $USERNAME; "
            "fi"
        )
        lines.append("RUN usermod -s /bin/zsh $USERNAME")
        lines.append("RUN git config --system --add safe.directory '*'")
        lines.append(
            "RUN git clone --depth=1 https://github.com/ohmyzsh/ohmyzsh.git /home/$USERNAME/.oh-my-zsh && "
            "cp /home/$USERNAME/.oh-my-zsh/templates/zshrc.zsh-template /home/$USERNAME/.zshrc && "
            "chown -R $UID:$GID /home/$USERNAME/.oh-my-zsh /home/$USERNAME/.zshrc"
        )
        lines.append("RUN mkdir -p /home/$USERNAME/workspace/src && chown -R $UID:$GID /home/$USERNAME")
        if self.ros_distro:
            lines.append(
                "RUN printf '%s\\n' 'source /opt/ros/$ROS_DISTRO/setup.bash' >> /home/$USERNAME/.bashrc && "
                "printf '%s\\n' 'if [ -f /home/$USERNAME/workspace/install/setup.bash ]; then source /home/$USERNAME/workspace/install/setup.bash; fi' >> /home/$USERNAME/.bashrc && "
                "printf '%s\\n' 'colcon_build() { mkdir -p log && colcon build \"$@\"; }' >> /home/$USERNAME/.bashrc"
            )
            lines.append(
                "RUN printf '%s\\n' 'source /opt/ros/$ROS_DISTRO/setup.zsh' >> /home/$USERNAME/.zshrc && "
                "printf '%s\\n' 'if [ -f /home/$USERNAME/workspace/install/setup.zsh ]; then source /home/$USERNAME/workspace/install/setup.zsh; fi' >> /home/$USERNAME/.zshrc && "
                "printf '%s\\n' 'colcon_build() { mkdir -p log && colcon build \"$@\"; }' >> /home/$USERNAME/.zshrc"
            )

        lines.append("USER $USERNAME")
        lines.append("ENV HOME=/home/$USERNAME")
        lines.append("WORKDIR /home/$USERNAME/workspace")

        user_cmds = []
        for dep in self.dependencies:
            user_cmds.extend(dep.user)

        if user_cmds:
            for cmd in user_cmds:
                # Optimize pip commands in user dependencies
                if "pip" in cmd and "install" in cmd and cache_mounts_enabled:
                    lines.append(f"RUN --mount=type=cache,target=/home/$USERNAME/.cache/pip \\\n    {cmd}")
                else:
                    lines.append(f"RUN {cmd}")

        lines.append("ENV IN_AIRFIELD_CONTAINER=1")

        return "\n".join(lines)

    def build(self, context_dir: Path, show_all_output: bool = False) -> Tuple[bool, str]:
        # Docker output now streams by default; keep this parameter for CLI compatibility.
        del show_all_output

        image_name = f"airfield-pkg-{self.package.name}:latest"

        with tempfile.TemporaryDirectory() as td:
            build_root = Path(td)
            cache_mounts_enabled = self._supports_cache_mounts()
            airfield_repo = self._find_airfield_repo(context_dir)
            if airfield_repo is not None:
                shutil.copytree(
                    airfield_repo,
                    build_root / "airfield",
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "build", "dist", "*.egg-info"),
                )

            local_dependency_root = context_dir / "dependencies" / self.target_device
            if local_dependency_root.exists() and any(local_dependency_root.glob("**/*.yaml")):
                print("[WARN] Local dependency manifests were found in the source tree.")
                print("[WARN] Please upstream them to the packages repository instead of keeping them local.")
                print("[WARN] Repo: https://github.com/airfield/packages")
                print("[WARN] README: https://github.com/airfield/packages#readme")
                print("[WARN] Command: airfield package dependencies upstream .")

            dockerfile_content = self.generate_dockerfile(
                install_local_airfield=airfield_repo is not None,
                cache_mounts_enabled=cache_mounts_enabled,
            )
            df_path = build_root / "Dockerfile"
            df_path.write_text(dockerfile_content, encoding="utf-8")

            uid = str(os.getuid())
            gid = str(os.getgid())
            username = pwd.getpwuid(os.getuid()).pw_name

            if is_arm_mac():
                container_archs = {
                    "arm64": "arm64",
                    "aarch64": "arm64",
                    "x86_64": "amd64",
                    "amd64": "amd64",
                }
                cmd = [
                    "container", "build",
                    "--arch", container_archs.get(self.target_device.strip().lower(), "arm64"),
                    "--build-arg", f"UID={uid}",
                    "--build-arg", f"GID={gid}",
                    "--build-arg", f"USERNAME={username}",
                    "-t", image_name,
                    "-f", str(df_path),
                    str(build_root),
                ]
            else:
                cmd = [
                    "docker", "build",
                    "--platform", self._resolve_docker_platform() or self.target_device,
                    "--pull",
                    "--build-arg", f"UID={uid}",
                    "--build-arg", f"GID={gid}",
                    "--build-arg", f"USERNAME={username}",
                    "-t", image_name,
                    "-f", str(df_path),
                    str(build_root),
                ]

            torch_build_args = [
                ("TORCH_INSTALL_TARGET", "AIRFIELD_TORCH_INSTALL_TARGET"),
                ("TORCH_VERSION", "AIRFIELD_TORCH_VERSION"),
                ("TORCH_GPU_WHL_TAG", "AIRFIELD_TORCH_GPU_WHL_TAG"),
            ]
            for docker_arg, preferred_host_env in torch_build_args:
                value = os.environ.get(preferred_host_env)
                if value is None:
                    value = os.environ.get(docker_arg)
                if value:
                    cmd.extend(["--build-arg", f"{docker_arg}={value}"])

            # Enable BuildKit for optimized caching
            env = os.environ.copy()
            if cache_mounts_enabled:
                env["DOCKER_BUILDKIT"] = "1"
            else:
                env.pop("DOCKER_BUILDKIT", None)

            print(f"Executing: {' '.join(cmd)}")
            print("--- Dockerfile ---")
            print(dockerfile_content)
            print("------------------")

            print("Container is building. This can take a few minutes...")
            if cache_mounts_enabled:
                print("(BuildKit cache mounts enabled for optimized rebuilds)")
            else:
                print("(Cache mounts disabled for this engine; using compatibility mode)")
            result = run_build_with_progress(
                cmd=with_plain_progress(cmd) if cache_mounts_enabled else cmd,
                cwd=str(context_dir),
                env=env,
                image_name=image_name,
            )

            if result.returncode != 0 and cache_mounts_enabled:
                stderr_text = (result.stderr or "").lower()
                stdout_text = (result.stdout or "").lower()
                if "invalid mount type \"cache\"" in stderr_text or "invalid mount type \"cache\"" in stdout_text:
                    print("Build engine rejected cache mounts. Retrying in compatibility mode...")
                    dockerfile_content = self.generate_dockerfile(
                        install_local_airfield=airfield_repo is not None,
                        cache_mounts_enabled=False,
                    )
                    df_path.write_text(dockerfile_content, encoding="utf-8")
                    env = os.environ.copy()
                    env.pop("DOCKER_BUILDKIT", None)
                    print("--- Dockerfile (compatibility mode) ---")
                    print(dockerfile_content)
                    print("----------------------------------------")
                    result = run_build_with_progress(
                        cmd=cmd,
                        cwd=str(context_dir),
                        env=env,
                        image_name=image_name,
                    )

            if result.returncode != 0:
                return False, image_name

            return True, image_name
