import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from rich.console import Console, Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text


console = Console()

_BUILDKIT_LINE_RE = re.compile(r"^#(?P<id>\d+)\s+(?P<message>.*)$")
_BUILDKIT_STEP_RE = re.compile(r"^\[(?P<step>\d+)/(?P<total>\d+)\]\s+(?P<instruction>.+)$")
_LEGACY_STEP_RE = re.compile(r"^Step\s+(?P<step>\d+)/(?P<total>\d+)\s*:\s*(?P<instruction>.+)$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SHA_TRANSFER_RE = re.compile(
    r"^sha256:[a-f0-9]+\s+(?P<current>\S+)\s*/\s*(?P<total>\S+)(?:\s+(?P<elapsed>\S+))?$",
    re.IGNORECASE,
)


@dataclass
class BuildProgress:
    image_name: str
    current_step: str = "Preparing build"
    current_instruction: str = "Waiting for Docker to start..."
    latest_line: str = "Starting Docker build..."
    status: str = "running"
    started_at: float = field(default_factory=time.monotonic)
    layer_instructions: Dict[str, Tuple[str, str]] = field(default_factory=dict)


def with_plain_progress(cmd: List[str]) -> List[str]:
    if "--progress=plain" in cmd:
        return cmd
    return [*cmd[:2], "--progress=plain", *cmd[2:]]


def run_build_with_progress(
    cmd: List[str],
    cwd: str,
    image_name: str,
    env=None,
) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    progress = BuildProgress(image_name=image_name)
    progress_lock = threading.Lock()
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    reader_errors: List[BaseException] = []

    def read_stream(stream, output: List[str], mirror) -> None:
        try:
            if stream is None:
                return
            for line in stream:
                output.append(line)
                mirror.write(line)
                mirror.flush()
                with progress_lock:
                    apply_docker_progress_line(progress, line)
        except BaseException as exc:
            reader_errors.append(exc)

    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, sys.stdout), daemon=True)
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, sys.stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    returncode = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    with progress_lock:
        progress.status = "finished" if returncode == 0 else "failed"

    for exc in reader_errors:
        raise exc

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def clean_docker_line(line: str) -> str:
    return _ANSI_RE.sub("", line).strip()


def apply_docker_progress_line(progress: BuildProgress, line: str) -> bool:
    clean_line = clean_docker_line(line)
    if not clean_line:
        return False

    progress.latest_line = clean_line

    legacy_match = _LEGACY_STEP_RE.match(clean_line)
    if legacy_match:
        progress.current_step = f"Step {legacy_match.group('step')}/{legacy_match.group('total')}"
        progress.current_instruction = legacy_match.group("instruction")
        progress.status = "running"
        return True

    buildkit_match = _BUILDKIT_LINE_RE.match(clean_line)
    if not buildkit_match:
        return True

    layer_id = buildkit_match.group("id")
    message = buildkit_match.group("message").strip()
    step_match = _BUILDKIT_STEP_RE.match(message)
    if step_match:
        progress.current_step = f"Dockerfile step {step_match.group('step')}/{step_match.group('total')}"
        progress.current_instruction = step_match.group("instruction")
        progress.status = "running"
        progress.layer_instructions[layer_id] = (progress.current_step, progress.current_instruction)
        return True

    upper_message = message.upper()
    if upper_message.startswith(("DONE", "CACHED", "ERROR", "CANCELED")):
        previous = progress.layer_instructions.get(layer_id)
        if previous is not None:
            progress.current_step, progress.current_instruction = previous
        progress.status = message
        return True

    if message:
        previous = progress.layer_instructions.get(layer_id)
        if previous is not None:
            progress.current_step, progress.current_instruction = previous
        else:
            transfer_match = _SHA_TRANSFER_RE.match(message)
            if transfer_match:
                progress.current_step = "Pulling base image"
                progress.current_instruction = "Downloading image layer"
            else:
                progress.current_step = describe_buildkit_phase(message)
                progress.current_instruction = message
        progress.status = message
    return True


def describe_buildkit_phase(message: str) -> str:
    normalized = message.lower()
    if normalized.startswith("[internal] load build definition"):
        return "Loading Dockerfile"
    if normalized.startswith("[internal] load metadata"):
        return "Resolving base image"
    if normalized.startswith("[internal] load .dockerignore"):
        return "Loading .dockerignore"
    if normalized.startswith("[internal] load build context"):
        return "Loading build context"
    if normalized.startswith("[auth]"):
        return "Authenticating registry"
    if normalized.startswith("exporting"):
        return "Exporting image"
    if normalized.startswith("importing cache") or normalized.startswith("loading cache"):
        return "Checking build cache"
    if normalized.startswith("naming to") or normalized.startswith("unpacking to"):
        return "Saving image"
    return "Preparing build"


def build_progress_panel(
    progress: BuildProgress,
    finished: bool = False,
    success: bool = False,
) -> Panel:
    elapsed = time.monotonic() - progress.started_at
    if finished:
        icon = "[green]Build complete[/green]" if success else "[red]Build failed[/red]"
    else:
        icon = Spinner("dots", text="[bold cyan]Building container[/bold cyan]")

    body = Group(
        icon,
        Text(f"Elapsed: {elapsed:0.1f}s", style="dim"),
        Text(f"Step: {progress.current_step}", style="bold"),
        Text(f"Instruction: {shorten_middle(progress.current_instruction)}"),
        Text(f"Latest: {shorten_middle(progress.latest_line)}", style="dim"),
    )
    border_style = "green" if finished and success else "red" if finished else "cyan"
    return Panel(
        body,
        title=f"[bold]{progress.image_name}[/bold]",
        border_style=border_style,
        expand=True,
    )


def print_non_tty_progress(progress: BuildProgress, finished: bool = False) -> None:
    prefix = "Container build finished" if finished else "Container is building"
    print(
        f"{prefix}: {progress.current_step} | "
        f"{shorten_middle(progress.current_instruction, 80)} | "
        f"{shorten_middle(progress.latest_line, 100)}"
    )


def shorten_middle(value: str, max_length: int = 120) -> str:
    if len(value) <= max_length:
        return value
    keep = max_length - 3
    left = keep // 2
    right = keep - left
    return f"{value[:left]}...{value[-right:]}"
