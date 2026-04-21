import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from airfield.models import Dependency, HostDependency


_VERSION_PATTERN = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


@dataclass
class HostFacts:
    has_nvidia_gpu: bool
    nvidia_driver_version: Optional[str]
    cuda_toolkit_version: Optional[str]
    suggested_torch_cuda_tag: Optional[str]


@dataclass
class HostDependencyIssue:
    dependency_name: str
    requirement_name: str
    message: str
    required: bool
    install_hint: Optional[str]


def _parse_version(value: str) -> Optional[Tuple[int, int, int]]:
    match = _VERSION_PATTERN.search(value)
    if match is None:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return major, minor, patch


def _version_satisfies(
    actual: Optional[str],
    min_version: Optional[str],
    max_version: Optional[str],
) -> bool:
    if actual is None:
        return False

    actual_parsed = _parse_version(actual)
    if actual_parsed is None:
        return False

    if min_version is not None:
        min_parsed = _parse_version(min_version)
        if min_parsed is None or actual_parsed < min_parsed:
            return False

    if max_version is not None:
        max_parsed = _parse_version(max_version)
        if max_parsed is None or actual_parsed > max_parsed:
            return False

    return True


def _run_capture(command: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def suggest_torch_cuda_tag(cuda_version: Optional[str]) -> Optional[str]:
    if cuda_version is None:
        return None

    parsed = _parse_version(cuda_version)
    if parsed is None:
        return None

    major, minor, _ = parsed
    if major >= 12:
        if minor >= 4:
            return "cu124"
        return "cu121"
    if major == 11 and minor >= 8:
        return "cu118"
    return None


def detect_host_facts() -> HostFacts:
    nvidia_smi = shutil.which("nvidia-smi")
    has_gpu = nvidia_smi is not None

    driver_version = None
    if nvidia_smi is not None:
        driver_version = _run_capture([nvidia_smi, "--query-gpu=driver_version", "--format=csv,noheader"]) 
        if driver_version is not None and "\n" in driver_version:
            driver_version = driver_version.splitlines()[0].strip()

    cuda_toolkit_version = None
    nvcc = shutil.which("nvcc")
    if nvcc is not None:
        nvcc_output = _run_capture([nvcc, "--version"])
        if nvcc_output is not None:
            release_match = re.search(r"release\s+([0-9]+\.[0-9]+)", nvcc_output)
            if release_match:
                cuda_toolkit_version = release_match.group(1)

    return HostFacts(
        has_nvidia_gpu=has_gpu,
        nvidia_driver_version=driver_version,
        cuda_toolkit_version=cuda_toolkit_version,
        suggested_torch_cuda_tag=suggest_torch_cuda_tag(cuda_toolkit_version),
    )


def _should_check(requirement: HostDependency, install_target: str) -> bool:
    mode = (requirement.mode or "any").strip().lower()
    if mode not in {"any", "gpu", "cpu"}:
        return True
    if mode == "any":
        return True
    return mode == install_target


def evaluate_host_dependencies(dependencies: List[Dependency], install_target: str) -> Tuple[HostFacts, List[HostDependencyIssue]]:
    facts = detect_host_facts()
    issues: List[HostDependencyIssue] = []

    for dep in dependencies:
        for requirement in dep.host_dependencies:
            if not _should_check(requirement, install_target):
                continue

            req_name = requirement.name.strip().lower()
            if req_name == "nvidia_gpu":
                if not facts.has_nvidia_gpu:
                    issues.append(
                        HostDependencyIssue(
                            dependency_name=dep.name,
                            requirement_name=req_name,
                            message="No NVIDIA GPU detected on host.",
                            required=requirement.required,
                            install_hint=requirement.install_hint,
                        )
                    )
                continue

            if req_name == "cuda":
                actual = facts.cuda_toolkit_version
                if not _version_satisfies(actual, requirement.min_version, requirement.max_version):
                    issues.append(
                        HostDependencyIssue(
                            dependency_name=dep.name,
                            requirement_name=req_name,
                            message=(
                                f"CUDA toolkit version '{actual or 'missing'}' does not satisfy "
                                f"min={requirement.min_version or '-'} max={requirement.max_version or '-'}"
                            ),
                            required=requirement.required,
                            install_hint=requirement.install_hint,
                        )
                    )
                continue

            if req_name == "nvidia_driver":
                actual = facts.nvidia_driver_version
                if not _version_satisfies(actual, requirement.min_version, requirement.max_version):
                    issues.append(
                        HostDependencyIssue(
                            dependency_name=dep.name,
                            requirement_name=req_name,
                            message=(
                                f"NVIDIA driver version '{actual or 'missing'}' does not satisfy "
                                f"min={requirement.min_version or '-'} max={requirement.max_version or '-'}"
                            ),
                            required=requirement.required,
                            install_hint=requirement.install_hint,
                        )
                    )
                continue

            issues.append(
                HostDependencyIssue(
                    dependency_name=dep.name,
                    requirement_name=req_name,
                    message="No checker implemented for this host dependency type.",
                    required=requirement.required,
                    install_hint=requirement.install_hint,
                )
            )

    return facts, issues
