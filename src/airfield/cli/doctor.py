import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.console import Console

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
    if not rc_path.exists():
        return False

    try:
        content = rc_path.read_text(encoding="utf-8")
    except Exception:
        return False

    return "_AIRFIELD_COMPLETE" in content or "airfield --install-completion" in content


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
        f"not configured for {shell_name}. Run: airfield --install-completion {shell_name}"
    )


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

    results.append(_check_docker())
    results.append(_check_shell_completion(auto_fix=fix))
    results.append(_check_gpu_accelerator())
    results.append(_check_pytorch_gpu())

    for status, name, detail in results:
        _print_result(status, name, detail)

    failed = any(status == "fail" for status, _, _ in results)
    if failed:
        raise typer.Exit(1)
