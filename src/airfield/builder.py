import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from airfield.models import Dependency, Package
from airfield.docker_cache import get_cache_optimization_comment


ROS_BASE_IMAGES = {
    "noetic": "ros:noetic-ros-base",
    "humble": "osrf/ros:humble-desktop",
    "jazzy": "osrf/ros:jazzy-desktop",
}

ROS_CORE_PACKAGES = {
    "noetic": ["python3-catkin-tools"],
    "humble": ["python3-colcon-common-extensions"],
    "jazzy": ["python3-colcon-common-extensions"],
}


class Builder:
    def __init__(self, package: Package, dependencies: List[Dependency], target_device: str):
        self.package = package
        self.dependencies = dependencies
        self.target_device = target_device
        self.ros_distro = self._resolve_ros_distro()
        self.base_image = ROS_BASE_IMAGES[self.ros_distro]

    def _resolve_ros_distro(self) -> str:
        ros_distro = (self.package.ros_distro or "jazzy").strip().lower()
        if ros_distro not in ROS_BASE_IMAGES:
            raise ValueError(
                f"Unsupported ROS distribution '{ros_distro}'. Supported values: {', '.join(sorted(ROS_BASE_IMAGES))}"
            )
        return ros_distro

    def _find_airfield_repo(self, context_dir: Path) -> Optional[Path]:
        for candidate in [context_dir, *context_dir.parents]:
            repo_root = candidate / "airfield"
            if (repo_root / "pyproject.toml").exists() and (repo_root / "src" / "airfield").exists():
                return repo_root
        return None

    def _supports_cache_mounts(self) -> bool:
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
        return True

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
        lines.append(f"ENV ROS_DISTRO={self.ros_distro}")
        lines.append("ARG TORCH_INSTALL_TARGET=cpu")
        lines.append("ARG TORCH_VERSION=")
        lines.append("ARG TORCH_GPU_WHL_TAG=cu121")
        
        # Optimized apt-get with BuildKit cache mounts
        apt_install = (
            "apt-get update && apt-get install -y python3-pip python3-opencv git zsh "
            + " ".join(ROS_CORE_PACKAGES[self.ros_distro])
            + " && rm -rf /var/lib/apt/lists/*"
        )
        if cache_mounts_enabled:
            lines.append(
                "RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \\\n"
                "    --mount=type=cache,target=/var/cache/apt,sharing=locked \\\n"
                f"    {apt_install}"
            )
        else:
            lines.append(f"RUN {apt_install}")

        if install_local_airfield:
            lines.append("COPY airfield /opt/airfield")
            if cache_mounts_enabled:
                lines.append(
                    "RUN --mount=type=cache,target=/root/.cache/pip \\\n"
                    "    python3 -m pip install --no-cache-dir /opt/airfield || \\\n"
                    "    python3 -m pip install --no-cache-dir --break-system-packages /opt/airfield"
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
                    "    python3 -m pip install --no-cache-dir airfield || \\\n"
                    "    python3 -m pip install --no-cache-dir --break-system-packages airfield"
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
                print("[WARN] Command: airfield package dependencies upstream")

            dockerfile_content = self.generate_dockerfile(
                install_local_airfield=airfield_repo is not None,
                cache_mounts_enabled=cache_mounts_enabled,
            )
            df_path = build_root / "Dockerfile"
            df_path.write_text(dockerfile_content, encoding="utf-8")

            uid = str(os.getuid())
            gid = str(os.getgid())
            username = pwd.getpwuid(os.getuid()).pw_name

            cmd = [
                "docker", "build",
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
            env["DOCKER_BUILDKIT"] = "1"

            print(f"Executing: {' '.join(cmd)}")
            print("--- Dockerfile ---")
            print(dockerfile_content)
            print("------------------")

            print("Container is building. This can take a few minutes...")
            if cache_mounts_enabled:
                print("(BuildKit cache mounts enabled for optimized rebuilds)")
            else:
                print("(Cache mounts disabled for this engine; using compatibility mode)")
            if show_all_output:
                result = subprocess.run(cmd, cwd=str(context_dir), text=True, env=env, capture_output=True)
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr)
            else:
                result = self._run_with_loading_indicator(cmd=cmd, cwd=str(context_dir), env=env)

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
                    print("--- Dockerfile (compatibility mode) ---")
                    print(dockerfile_content)
                    print("----------------------------------------")
                    if show_all_output:
                        result = subprocess.run(cmd, cwd=str(context_dir), text=True, env=env, capture_output=True)
                        if result.stdout:
                            print(result.stdout)
                        if result.stderr:
                            print(result.stderr)
                    else:
                        result = self._run_with_loading_indicator(cmd=cmd, cwd=str(context_dir), env=env)

            if result.returncode != 0:
                if not show_all_output:
                    print(result.stdout)
                    print(result.stderr)
                return False, image_name

            return True, image_name

    def _run_with_loading_indicator(self, cmd: List[str], cwd: str, env=None) -> subprocess.CompletedProcess:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        spinner = "|/-\\"
        idx = 0
        last_non_tty_update = 0.0
        while process.poll() is None:
            if sys.stdout.isatty():
                frame = spinner[idx % len(spinner)]
                print(f"\rContainer is building... {frame}", end="", flush=True)
                idx += 1
            else:
                now = time.monotonic()
                if now - last_non_tty_update >= 5.0:
                    print("Container is building...")
                    last_non_tty_update = now
            time.sleep(0.15)

        if sys.stdout.isatty():
            print("\rContainer build finished.     ")

        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(cmd, process.returncode, stdout=stdout, stderr=stderr)
