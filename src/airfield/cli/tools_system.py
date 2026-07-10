import json
import os
import time
import shutil
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple

import typer
from rich.console import Console

from airfield import __version__
from airfield.cli.docker_cleanup import cleanup_all_airfield_containers
from airfield.cli.doctor import _install_completion, _shell_rc_path
from airfield.config import is_arm_mac

console = Console()


def _prune_build_cache(until: Optional[str] = None, aggressive: bool = False) -> None:
    if is_arm_mac():
        console.print("[yellow]Apple's container tool does not support direct BuildKit builder prune; skipping BuildKit cache prune.[/yellow]")
        return
    try:
        cmd = ["docker", "builder", "prune", "-f"]
        if aggressive:
            cmd.insert(3, "-a")
        if until:
            cmd.extend(["--filter", f"until={until}"])
            
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        console.print("[yellow]Docker not found; skipping BuildKit cache prune.[/yellow]")
        raise typer.Exit(1)

    if result.returncode != 0:
        console.print("[red]Failed to prune BuildKit cache.[/red]")
        details = (result.stderr or result.stdout or "").strip()
        if details:
            console.print(details)
        raise typer.Exit(1)

    console.print("[bold green]Pruned BuildKit cache.[/bold green]")
    if result.stdout:
        console.print(result.stdout.strip())


def run(
    cache: bool = typer.Option(False, "--cache", help="Prune Docker BuildKit cache"),
    until: Optional[str] = typer.Option(None, "--until", help="Only remove items older than this duration (e.g., '168h')"),
    aggressive: bool = typer.Option(False, "--aggressive", help="When used with --cache, remove all build cache including in-use layers"),
):
    """Remove all containers created from Airfield package images."""
    try:
        removed = cleanup_all_airfield_containers(until=until)
    except FileNotFoundError:
        engine_name = "container" if is_arm_mac() else "Docker"
        console.print(f"[yellow]{engine_name} not found; skipping container cleanup.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold green]Removed {removed} Airfield container(s).[/bold green]")
    if cache:
        _prune_build_cache(until=until, aggressive=aggressive)


# Update check implementation
# owner/name slug, overridable for forks/mirrors (also used by system update).
_GITHUB_REPO = os.environ.get("AIRFIELD_REPO", "airfield/airfield")
_TAGS_API = f"https://api.github.com/repos/{_GITHUB_REPO}/tags"


def _cache_file() -> Path:
    # Route through the shared XDG helper so this honors XDG_CACHE_HOME like
    # every other airfield cache path.
    from airfield.config import xdg_cache_root

    return xdg_cache_root() / "last_update_check.json"


def _read_cache() -> Optional[dict]:
    try:
        cache_file = _cache_file()
        if not cache_file.exists():
            return None
        text = cache_file.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


def _write_cache(obj: dict) -> None:
    try:
        _cache_file().write_text(json.dumps(obj), encoding="utf-8")
    except Exception:
        pass


def _parse_version(v: str) -> Tuple:
    # crude parse: split on non-digit and convert to ints where possible
    parts = []
    for chunk in v.lstrip("vV").split("."):
        try:
            parts.append(int(chunk))
        except Exception:
            # keep non-int parts as strings for fallback compare
            parts.append(chunk)
    return tuple(parts)


def _compare_versions(a: str, b: str) -> int:
    pa = _parse_version(a)
    pb = _parse_version(b)
    if pa == pb:
        return 0
    try:
        # element-wise compare where possible
        for x, y in zip(pa, pb):
            if isinstance(x, int) and isinstance(y, int):
                if x < y:
                    return -1
                if x > y:
                    return 1
            else:
                xa = str(x)
                yb = str(y)
                if xa < yb:
                    return -1
                if xa > yb:
                    return 1
        # fallback to length
        if len(pa) < len(pb):
            return -1
        if len(pa) > len(pb):
            return 1
    except Exception:
        pass
    return 0


def check_for_update(force: bool = False, timeout: int = 5) -> Optional[dict]:
    """Check GitHub tags for a newer version. Returns dict or None on error.

    Cached results are used if present and fresher than 24 hours unless `force`.
    """
    import re
    from functools import cmp_to_key

    now = time.time()
    cached = _read_cache()
    if not force and cached:
        ts = cached.get("checked_at", 0)
        if now - ts < 24 * 3600:
            return cached

    req = urllib.request.Request(_TAGS_API, headers={"User-Agent": "airfield-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # fall back to cache if available
        if cached:
            return cached
        console.print(f"[yellow]Update-check HTTP error: {exc.code}[/yellow]")
        return None
    except Exception as exc:
        if cached:
            return cached
        console.print(f"[yellow]Update-check failed: {exc}[/yellow]")
        return None

    if not isinstance(data, list):
        if cached:
            return cached
        return None

    # Filter tags matching the vX.X.X pattern
    semver_pattern = re.compile(r"^v\d+\.\d+\.\d+$", re.IGNORECASE)
    valid_tags = []
    for tag_obj in data:
        if isinstance(tag_obj, dict):
            name = tag_obj.get("name")
            if name and semver_pattern.match(name):
                valid_tags.append(name)

    if not valid_tags:
        if cached:
            return cached
        return None

    # Sort matching tags to find the latest (highest semver version)
    valid_tags.sort(key=cmp_to_key(_compare_versions), reverse=True)
    latest_tag = valid_tags[0]
    html_url = f"https://github.com/{_GITHUB_REPO}/releases/tag/{latest_tag}"

    result = {
        "checked_at": now,
        "current_version": __version__,
        "latest_version": latest_tag,
        "url": html_url,
        "newer": _compare_versions(__version__, latest_tag) < 0,
    }
    _write_cache(result)
    return result


def _is_editable_install() -> bool:
    """PEP 610: editable installs record dir_info.editable in direct_url.json."""
    try:
        from importlib import metadata as _metadata

        text = _metadata.distribution("airfield").read_text("direct_url.json")
        if text:
            return bool(json.loads(text).get("dir_info", {}).get("editable"))
    except Exception:
        pass
    return False


def update(
    force: bool = typer.Option(False, "--force", help="Reinstall even if already up-to-date"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Check for updates without installing"),
):
    """Update Airfield to the latest release via pipx."""
    # Never clobber a development checkout: an editable install points the
    # global command at working-tree source (possibly a fork mid-change).
    if _is_editable_install():
        console.print("[yellow]This is an editable (development) install; refusing to overwrite it.[/yellow]")
        console.print("Update it with `git pull` in your source checkout instead.")
        raise typer.Exit(1)

    res = check_for_update(force=True)
    if res is None:
        console.print("[yellow]Unable to determine update status from GitHub.[/yellow]")
        if not force:
            console.print("Use [bold]--force[/bold] to reinstall anyway.")
            raise typer.Exit(1)
    else:
        cur = res.get("current_version")
        latest = res.get("latest_version")
        if res.get("newer"):
            console.print(f"[bold yellow]Update available:[/bold yellow] {cur} → {latest}")
            console.print(f"Release: {res.get('url')}")
        elif not force:
            console.print(f"[green]Airfield up-to-date ({cur}).[/green]")
            raise typer.Exit(0)
        else:
            console.print(f"[green]Airfield is up-to-date ({cur}), but forcing reinstall.[/green]")

    if dry_run:
        raise typer.Exit(2 if (res and res.get("newer")) else 0)

    if shutil.which("pipx") is None:
        console.print("[red]pipx not found; install pipx (or update manually) and retry.[/red]")
        raise typer.Exit(1)

    # Same repo slug the update check uses; AIRFIELD_REPO overrides for forks.
    source = f"git+https://github.com/{_GITHUB_REPO}.git"
    console.print(f"Updating Airfield via pipx from {source}...")
    result = subprocess.run(["pipx", "install", "--force", source], check=False)
    if result.returncode != 0:
        console.print("[red]Failed to update Airfield.[/red]")
        raise typer.Exit(1)

    console.print("[bold green]Airfield updated successfully.[/bold green]")


def install_alias(
    shells: Optional[str] = typer.Option(None, "--shells", help="Comma-separated shells (bash,zsh,fish)"),
    dry_run: bool = typer.Option(True, "--dry-run", help="Show changes without writing"),
    yes: bool = typer.Option(False, "--yes", help="Apply changes without prompting"),
):
    """Install an `a` alias for `airfield` into user RC files (idempotent).

    The command appends a guarded block to the shell RC file.
    """
    target_shells = ["bash", "zsh", "fish"]
    if shells:
        provided = [s.strip() for s in shells.split(",") if s.strip()]
        if provided:
            target_shells = provided

    airfield_cmd = shutil.which("airfield") or "airfield"
    marker_start = "# >>> airfield alias start >>>"
    marker_end = "# <<< airfield alias end <<<"
    alias_lines = {
        "bash": f"{marker_start}\nif [ -x \"{airfield_cmd}\" ]; then\n  alias a=\\'airfield\\'\nfi\n{marker_end}\n",
        "zsh": f"{marker_start}\nif [ -x \"{airfield_cmd}\" ]; then\n  alias a=\\'airfield\\'\nfi\n{marker_end}\n",
        "fish": f"{marker_start}\nif test -x \"{airfield_cmd}\"; \n  alias a airfield\nend\n{marker_end}\n",
    }

    changes = []
    for sh in target_shells:
        rc = _shell_rc_path(sh)
        content = ""
        if rc.exists():
            try:
                content = rc.read_text(encoding="utf-8")
            except Exception:
                content = ""

        if marker_start in content:
            changes.append((sh, rc, "already_installed"))
            continue

        new_content = content + "\n" + alias_lines.get(sh, alias_lines["bash"])
        changes.append((sh, rc, new_content))

    for sh, rc, outcome in changes:
        if outcome == "already_installed":
            console.print(f"[dim]{sh}: alias already present in {rc}[/dim]")
            continue
        console.print(f"[blue]{sh} -> {rc}[/blue]")
        if dry_run:
            console.print(outcome)
            continue
        if not yes:
            console.print(f"Applying change to {rc}")
        try:
            # backup
            if rc.exists():
                rc_text = rc.read_text(encoding="utf-8")
                (rc.with_suffix(rc.suffix + ".airfield.bak")).write_text(rc_text, encoding="utf-8")
            rc.write_text(outcome, encoding="utf-8")
            console.print(f"[green]Wrote alias to {rc}[/green]")
        except Exception as exc:
            console.print(f"[red]Failed to write {rc}: {exc}[/red]")


def install_completion(shell_name: str = typer.Argument(...)):
    """Install shell completion by delegating to doctor helpers."""
    ok, message = _install_completion(shell_name)
    if ok:
        console.print(f"[green]Completion install attempted: {message}[/green]")
        # advise reloading shell
        console.print("Open a new shell session to activate the completion.")
        return
    console.print(f"[red]Completion install failed: {message}[/red]")
    raise typer.Exit(1)


def setup():
    """Set up the local system for Airfield (installs container backend if needed)."""
    if is_arm_mac():
        console.print("[dim]Checking Apple container tool installation...[/dim]")
        container_path = shutil.which("container")
        if container_path is None:
            console.print("Apple container tool is not installed. Installing from latest GitHub release...")
            import tempfile
            
            console.print("Resolving latest Apple container tool release from GitHub...")
            releases_url = "https://api.github.com/repos/apple/container/releases/latest"
            req = urllib.request.Request(releases_url, headers={"User-Agent": "airfield-setup"})
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
                        console.print("[red]Error: Could not find a .pkg installer asset in the latest GitHub release.[/red]")
                        raise typer.Exit(1)
            except Exception as exc:
                console.print(f"[red]Error: Failed to fetch latest GitHub release: {exc}[/red]")
                raise typer.Exit(1)

            try:
                temp_dir = Path(tempfile.gettempdir())
                pkg_path = temp_dir / "apple-container-installer.pkg"
                console.print(f"Downloading installer from {pkg_url}...")
                req_dl = urllib.request.Request(pkg_url, headers={"User-Agent": "airfield-setup"})
                with urllib.request.urlopen(req_dl) as response, open(pkg_path, "wb") as out_file:
                    shutil.copyfileobj(response, out_file)
            except Exception as exc:
                console.print(f"[red]Error: Failed to download installer: {exc}[/red]")
                raise typer.Exit(1)

            console.print("Installing Apple container tool (requires administrator privileges)...")
            result = subprocess.run(["sudo", "installer", "-pkg", str(pkg_path), "-target", "/"], check=False)
            
            # Clean up installer
            try:
                pkg_path.unlink()
            except Exception:
                pass

            if result.returncode != 0:
                console.print("[red]Error: Failed to install container tool via installer package.[/red]")
                raise typer.Exit(1)

            container_path = shutil.which("container") or "/usr/local/bin/container"
            if not Path(container_path).exists():
                console.print(f"[red]Error: container tool not found at {container_path} after installation.[/red]")
                raise typer.Exit(1)
            console.print("[green]Installed Apple container tool successfully.[/green]")
        else:
            console.print(f"[green]Apple container tool is already installed at {container_path}.[/green]")

        # Apply Homebrew plugin path workaround to prevent apiserver hang (only if Homebrew was used previously)
        brew_path = shutil.which("brew")
        if brew_path is not None:
            brew_prefix_res = subprocess.run([brew_path, "--prefix"], capture_output=True, text=True, check=False)
            if brew_prefix_res.returncode == 0:
                brew_prefix = brew_prefix_res.stdout.strip()
                target_plugin_dir = Path(brew_prefix) / "libexec" / "container" / "plugins"
                source_plugin_dir = Path(brew_prefix) / "opt" / "container" / "libexec" / "container-plugins"
                if source_plugin_dir.exists() and not target_plugin_dir.exists():
                    try:
                        target_plugin_dir.parent.mkdir(parents=True, exist_ok=True)
                        target_plugin_dir.symlink_to(source_plugin_dir)
                        console.print(f"[green]Applied Homebrew plugin path symlink workaround: {target_plugin_dir} -> {source_plugin_dir}[/green]")
                    except Exception as exc:
                        console.print(f"[yellow]Warning: Could not apply Homebrew plugin path workaround: {exc}[/yellow]")

        console.print("[dim]Starting container system service...[/dim]")
        result_start = subprocess.run([container_path, "system", "start"], check=False)
        if result_start.returncode != 0:
            console.print("[red]Error: Failed to start container system service.[/red]")
            raise typer.Exit(1)
        console.print("[bold green]Apple container tool is successfully configured and running.[/bold green]")
    else:
        console.print("[dim]Checking Docker installation...[/dim]")
        docker_path = shutil.which("docker")
        if docker_path is None:
            console.print("[yellow]Docker is not found in PATH.[/yellow]")
            console.print("Please install Docker Desktop or compatible container backend (e.g., Podman) for your system.")
            raise typer.Exit(1)
        console.print(f"[green]Docker is installed at {docker_path}.[/green]")
        result = subprocess.run([docker_path, "info"], capture_output=True, check=False)
        if result.returncode != 0:
            console.print("[yellow]Docker daemon is not running or not reachable.[/yellow]")
            console.print("Please start Docker before using Airfield.")
            raise typer.Exit(1)
        console.print("[bold green]Docker is successfully configured and running.[/bold green]")
