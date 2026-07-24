import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.console import Console

from airfield.config import is_arm_mac

console = Console()


def _inside_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    container_env = os.environ.get("container", "").strip().lower()
    return bool(container_env)


def _engine_alias(path: str) -> str:
    resolved = str(Path(path).resolve())
    name = Path(resolved).name.lower()
    if "podman" in name:
        return "podman"
    if "singularity" in name:
        return "singularity"
    if "apptainer" in name:
        return "apptainer"
    if "docker" in name:
        return "docker"
    return name or "unknown"


def _install_container() -> Tuple[bool, str]:
    import tempfile
    import urllib.request

    console.print("Resolving latest Apple container tool release from GitHub...")
    releases_url = "https://api.github.com/repos/apple/container/releases/latest"
    req = urllib.request.Request(releases_url, headers={"User-Agent": "airfield-doctor"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assets = data.get("assets", [])
            pkg_url = None
            for asset in assets:
                name = asset.get("name", "")
                if name.endswith(".pkg"):
                    pkg_url = asset.get("browser_download_url")
                    break
            if not pkg_url:
                return False, "could not find a .pkg installer asset in the latest GitHub release"
    except Exception as exc:
        return False, f"failed to fetch latest GitHub release: {exc}"

    try:
        temp_dir = Path(tempfile.gettempdir())
        pkg_path = temp_dir / "apple-container-installer.pkg"
        console.print(f"Downloading installer from {pkg_url}...")
        req_dl = urllib.request.Request(pkg_url, headers={"User-Agent": "airfield-doctor"})
        with urllib.request.urlopen(req_dl) as response, open(pkg_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception as exc:
        return False, f"failed to download installer: {exc}"

    console.print("Installing Apple container tool (requires administrator privileges)...")
    result = subprocess.run(["sudo", "installer", "-pkg", str(pkg_path), "-target", "/"], check=False)
    
    # Clean up installer
    try:
        pkg_path.unlink()
    except Exception:
        pass

    if result.returncode != 0:
        return False, "installer package execution failed"

    container_path = shutil.which("container") or "/usr/local/bin/container"
    if not Path(container_path).exists():
        return False, f"container CLI not found at {container_path} after installation"

    console.print("Starting container system service...")
    result_start = subprocess.run([container_path, "system", "start"], capture_output=True, text=True, check=False)
    if result_start.returncode != 0:
        stderr = (result_start.stderr or "").strip()
        return False, f"container system start failed: {stderr}"

    return True, "Apple container tool installed and system service started"



def _check_container(auto_fix: bool) -> Tuple[str, str, Optional[str]]:
    container_path = shutil.which("container")
    if container_path is None:
        if _inside_container():
            return "warn", "Container engine", "container not found in PATH inside container (skipping host engine check)"
        if auto_fix:
            ok, msg = _install_container()
            if ok:
                return "pass", "Container tool", "installed and system service started successfully"
            return "fail", "Container CLI", f"container not found in PATH; auto-fix failed: {msg}"
        return "fail", "Container CLI", "container not found in PATH. Run: airfield system setup"

    result = subprocess.run([container_path, "system", "status"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if _inside_container():
            return "warn", "Container engine", f"container detected at {container_path}; service query unavailable in container"
        if auto_fix:
            result_start = subprocess.run([container_path, "system", "start"], capture_output=True, text=True, check=False)
            if result_start.returncode == 0:
                return "pass", "Container tool", "service was stopped but successfully started"
            stderr = (result_start.stderr or "").strip()
            return "fail", "Container system service", f"not running, and failed to start: {stderr}"

        stderr = (result.stderr or "").strip()
        message = stderr.splitlines()[0] if stderr else "container system service is not running"
        return "fail", "Container system service", f"{message}. Try running: container system start"

    return "pass", "Container tool", f"available at {container_path}"


def _check_docker() -> Tuple[str, str, Optional[str]]:
    docker_path = shutil.which("docker")
    if docker_path is None:
        if _inside_container():
            return "warn", "Container engine", "docker not found in PATH inside container (skipping host engine check)"
        return "fail", "Docker CLI", "docker not found in PATH"

    alias = _engine_alias(docker_path)

    result = subprocess.run([docker_path, "info"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        message = stderr.splitlines()[0] if stderr else "docker daemon is not reachable"
        if _inside_container():
            return "warn", "Container engine", f"{alias} detected at {docker_path}; daemon query unavailable in container ({message})"
        return "fail", "Docker daemon", message

    if alias != "docker":
        return "pass", "Container engine", f"{alias} via docker-compatible CLI at {docker_path}"

    return "pass", "Docker", f"available at {docker_path}"


def _check_git() -> Tuple[str, str, Optional[str]]:
    git_path = shutil.which("git")
    if git_path is None:
        return "fail", "Git", (
            "git not found in PATH. Airfield needs it to fetch the shared packages "
            "repository and manage subproject checkouts. Install: apt-get install git"
        )
    return "pass", "Git", f"available at {git_path}"


def _check_plan_runner() -> Tuple[str, str, Optional[str]]:
    """tmux + tmuxinator are only needed for `project up` plan launches, so
    missing tools warn (with install hints) rather than fail."""
    tmux_path = shutil.which("tmux")
    tmuxinator_path = shutil.which("tmuxinator")
    if tmux_path and tmuxinator_path:
        return "pass", "Plan runner", f"tmux at {tmux_path}; tmuxinator at {tmuxinator_path}"

    missing = []
    if tmux_path is None:
        missing.append("tmux (install: apt-get install tmux)")
    if tmuxinator_path is None:
        missing.append("tmuxinator (install: apt-get install tmuxinator, or gem install tmuxinator)")
    return "warn", "Plan runner", (
        f"`airfield project up` will not work until installed: {'; '.join(missing)}"
    )


def _detect_cuda_version() -> Optional[str]:
    nvcc = shutil.which("nvcc")
    if nvcc is None:
        return None

    result = subprocess.run([nvcc, "--version"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    for line in output.splitlines():
        line = line.strip()
        if "release" in line:
            # Example: "Cuda compilation tools, release 12.1, V12.1.66"
            chunk = line.split("release", 1)[1].strip()
            return chunk.split(",", 1)[0].strip()
    return None


def _check_gpu_accelerator() -> Tuple[str, str, Optional[str]]:
    nvidia_smi = shutil.which("nvidia-smi")
    cuda_version = _detect_cuda_version()

    if nvidia_smi is None:
        detail = "NVIDIA GPU not detected"
        if cuda_version:
            detail += f"; CUDA toolkit {cuda_version} is installed"
        return "warn", "GPU accelerator", detail

    query = subprocess.run(
        [nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    if query.returncode != 0:
        message = (query.stderr or "").strip() or "failed to query GPU details"
        if cuda_version:
            message += f"; CUDA toolkit {cuda_version}"
        return "warn", "GPU accelerator", message

    lines = [line.strip() for line in (query.stdout or "").splitlines() if line.strip()]
    if not lines:
        detail = "nvidia-smi present but no GPU rows returned"
        if cuda_version:
            detail += f"; CUDA toolkit {cuda_version}"
        return "warn", "GPU accelerator", detail

    gpu_list = "; ".join(lines)
    detail = f"{len(lines)} GPU(s): {gpu_list}"
    if cuda_version:
        detail += f"; CUDA toolkit {cuda_version}"
    else:
        detail += "; CUDA toolkit not found"
    return "pass", "GPU accelerator", detail


def _check_pytorch_gpu() -> Tuple[str, str, Optional[str]]:
    python_cmd = shutil.which("python3") or shutil.which("python")
    if python_cmd is None:
        return "warn", "PyTorch", "python interpreter not found"

    script = r'''
import json
out = {
    "installed": False,
    "version": None,
    "cuda_available": False,
    "cuda_version": None,
    "gpu_name": None,
    "gpu_tensor_test": False,
    "error": None,
}
try:
    import torch
    out["installed"] = True
    out["version"] = torch.__version__
    out["cuda_available"] = bool(torch.cuda.is_available())
    out["cuda_version"] = torch.version.cuda
    if out["cuda_available"]:
        try:
            out["gpu_name"] = torch.cuda.get_device_name(0)
            x = torch.randn((8, 8), device="cuda")
            y = (x @ x).sum().item()
            out["gpu_tensor_test"] = isinstance(y, float)
        except Exception as exc:
            out["error"] = f"gpu_test_failed: {exc}"
except Exception as exc:
    out["error"] = str(exc)

print(json.dumps(out))
'''

    result = subprocess.run([python_cmd, "-c", script], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return "warn", "PyTorch", stderr or "python execution failed"

    stdout = (result.stdout or "").strip()
    if not stdout:
        return "warn", "PyTorch", "no output from probe"

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return "warn", "PyTorch", f"unexpected probe output: {stdout}"

    if not data.get("installed"):
        err = data.get("error") or "not installed"
        return "warn", "PyTorch", f"not installed ({err})"

    version = data.get("version") or "unknown"
    cuda_available = bool(data.get("cuda_available"))
    cuda_version = data.get("cuda_version") or "none"
    gpu_name = data.get("gpu_name") or "none"
    gpu_tensor_test = bool(data.get("gpu_tensor_test"))
    err = data.get("error")

    if cuda_available and gpu_tensor_test:
        return "pass", "PyTorch", (
            f"version {version}; CUDA available ({cuda_version}); GPU {gpu_name}; tensor-on-GPU test passed"
        )

    detail = (
        f"version {version}; CUDA available={cuda_available}; CUDA version={cuda_version}; "
        f"GPU={gpu_name}; tensor-on-GPU test passed={gpu_tensor_test}"
    )
    if err:
        detail += f"; error={err}"
    return "warn", "PyTorch", detail


def _detect_shell() -> Optional[str]:
    shell = os.environ.get("SHELL", "").strip()
    if not shell:
        return None
    shell_name = Path(shell).name.lower()
    if shell_name in {"bash", "zsh", "fish"}:
        return shell_name
    return None


def _shell_rc_path(shell_name: str) -> Path:
    home = Path.home()
    if shell_name == "bash":
        return home / ".bashrc"
    if shell_name == "zsh":
        return home / ".zshrc"
    return home / ".config" / "fish" / "config.fish"


def _completion_configured(shell_name: str) -> bool:
    rc_path = _shell_rc_path(shell_name)
    # Check user RC file first
    try:
        if rc_path.exists():
            content = rc_path.read_text(encoding="utf-8")
            if "_AIRFIELD_COMPLETE" in content or "airfield --install-completion" in content:
                return True
    except Exception:
        pass

    # Check common system-wide or shell-specific completion locations
    home = Path.home()
    if shell_name == "bash":
        candidates = [
            Path("/etc/bash_completion.d/airfield"),
            Path("/usr/share/bash-completion/completions/airfield"),
            home / ".local" / "share" / "bash-completion" / "completions" / "airfield",
        ]
        for p in candidates:
            if p.exists():
                return True

    if shell_name == "zsh":
        candidates = [
            home / ".zfunc" / "_airfield",
            home / ".zsh" / "_airfield",
            Path("/usr/share/zsh/functions/Completion/Unix/_airfield"),
        ]
        for p in candidates:
            if p.exists():
                return True

    if shell_name == "fish":
        candidates = [
            home / ".config" / "fish" / "completions" / "airfield.fish",
            Path("/etc/fish/completions/airfield.fish"),
        ]
        for p in candidates:
            if p.exists():
                return True

    # As a last-resort, try launching an interactive instance of the user's
    # shell and query whether completion is currently registered. This helps
    # detect completions that are loaded from non-standard locations.
    try:
        shell_bin = os.environ.get("SHELL", "/bin/bash")
        sh_name = Path(shell_bin).name.lower()
        if sh_name == "bash":
            probe = [shell_bin, "-ic", "complete -p airfield >/dev/null 2>&1 && printf yes || printf no"]
        elif sh_name == "zsh":
            probe = [shell_bin, "-ic", "whence -w _airfield >/dev/null 2>&1 && printf yes || printf no"]
        elif sh_name == "fish":
            probe = [shell_bin, "-ic", "complete -c airfield >/dev/null 2>&1 && printf yes || printf no"]
        else:
            probe = None

        if probe is not None:
            result = subprocess.run(probe, capture_output=True, text=True, check=False, timeout=2)
            out = (result.stdout or "").strip().lower()
            if out == "yes":
                return True
    except Exception:
        pass

    return False


def _install_completion(shell_name: str) -> Tuple[bool, str]:
    airfield_cmd = shutil.which("airfield")
    if airfield_cmd is None:
        return False, "airfield command not found in PATH"

    result = subprocess.run(
        [airfield_cmd, "--install-completion", shell_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        return False, stderr or stdout or "failed to install completion"

    return True, "completion installation command succeeded"


def _check_shell_completion(auto_fix: bool) -> Tuple[str, str, Optional[str]]:
    shell_name = _detect_shell()
    if shell_name is None:
        return "warn", "Shell completion", "unsupported or unknown shell in SHELL env"

    if _completion_configured(shell_name):
        return "pass", "Shell completion", f"configured for {shell_name}"

    if auto_fix:
        ok, message = _install_completion(shell_name)
        if ok and _completion_configured(shell_name):
            return "pass", "Shell completion", f"configured for {shell_name}"
        if ok:
            return "warn", "Shell completion", f"{message}; open a new shell session to activate"
        return "fail", "Shell completion", message

    return "warn", "Shell completion", (
        f"not configured for {shell_name}. Run: airfield system install-completion {shell_name}"
    )


def _check_airfield_update() -> Tuple[str, str, Optional[str]]:
    try:
        from airfield.cli.tools_system import check_for_update
        res = check_for_update(timeout=3)
        if res is None:
            return "warn", "Airfield update", "unable to check for updates"
        cur = res.get("current_version")
        latest = res.get("latest_version")
        if res.get("newer"):
            return "warn", "Airfield update", f"update available: {cur} → {latest}. Run: airfield system update"
        return "pass", "Airfield update", f"up-to-date ({cur})"
    except Exception as exc:
        return "warn", "Airfield update", f"failed to check for updates: {exc}"


def _check_git_hook() -> Optional[Tuple[str, str, Optional[str]]]:
    # Check if this file is running within the airfield development repository
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    githooks_dir = repo_root / ".githooks"
    if not githooks_dir.exists():
        return None

    # Only check hook setup if the command is run from inside the airfield repository directory
    try:
        cwd = Path.cwd().resolve()
        cwd.relative_to(repo_root)
    except ValueError:
        return None
    except Exception:
        return None

    # Check if core.hooksPath is set to .githooks or if pre-push script is in .git/hooks
    try:
        result = subprocess.run(["git", "config", "core.hooksPath"], capture_output=True, text=True, check=False)
        hooks_path = result.stdout.strip()
        if hooks_path == ".githooks":
            return "pass", "Git push hook", "installed (core.hooksPath set to .githooks)"
    except Exception:
        pass

    pre_push_git = repo_root / ".git" / "hooks" / "pre-push"
    if pre_push_git.exists() and os.access(pre_push_git, os.X_OK):
        return "pass", "Git push hook", "installed (pre-push script present in .git/hooks)"

    return "fail", "Git push hook", "not installed. Run: git config core.hooksPath .githooks"


def _print_result(status: str, name: str, detail: Optional[str]) -> None:
    if status == "pass":
        prefix = "[green]PASS[/green]"
    elif status == "warn":
        prefix = "[yellow]WARN[/yellow]"
    else:
        prefix = "[red]FAIL[/red]"

    if detail:
        console.print(f"{prefix} {name}: {detail}")
    else:
        console.print(f"{prefix} {name}")


def run(
    fix: bool = typer.Option(False, "--fix", help="Attempt to auto-fix supported doctor checks"),
):
    """Check Airfield system dependencies and shell integration."""
    results: List[Tuple[str, str, Optional[str]]] = []

    # Check for CLI updates first
    results.append(_check_airfield_update())

    # Check for developers' git hook status if within the repo
    hook_res = _check_git_hook()
    if hook_res is not None:
        results.append(hook_res)

    if is_arm_mac():
        results.append(_check_container(auto_fix=fix))
    else:
        results.append(_check_docker())
    results.append(_check_git())
    if not _inside_container():
        results.append(_check_plan_runner())
    results.append(_check_shell_completion(auto_fix=fix))
    results.append(_check_gpu_accelerator())
    # Only probe PyTorch when running inside a container environment where
    # GPU runtime checks are relevant. Avoid warning users on hosts without
    # PyTorch installed.
    if _inside_container():
        results.append(_check_pytorch_gpu())
    else:
        results.append(("pass", "PyTorch", "skipped (not running inside container)"))

    for status, name, detail in results:
        _print_result(status, name, detail)

    failed = any(status == "fail" for status, _, _ in results)
    if failed:
        raise typer.Exit(1)
