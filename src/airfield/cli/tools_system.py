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

console = Console()


def _prune_build_cache() -> None:
    try:
        result = subprocess.run(
            ["docker", "builder", "prune", "-f"],
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
):
    """Remove all containers created from Airfield package images."""
    try:
        removed = cleanup_all_airfield_containers()
    except FileNotFoundError:
        console.print("[yellow]Docker not found; skipping container cleanup.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold green]Removed {removed} Airfield container(s).[/bold green]")
    if cache:
        _prune_build_cache()


# Update check implementation
_CACHE_PATH = Path(os.path.expanduser("~")) / ".cache" / "airfield"
_CACHE_FILE = _CACHE_PATH / "last_update_check.json"
_GITHUB_REPO = "airfield/airfield"
_RELEASES_API = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"


def _read_cache() -> Optional[dict]:
    try:
        if not _CACHE_FILE.exists():
            return None
        text = _CACHE_FILE.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


def _write_cache(obj: dict) -> None:
    try:
        _CACHE_PATH.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(obj), encoding="utf-8")
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
    """Check GitHub releases for a newer version. Returns dict or None on error.

    Cached results are used if present and fresher than 24 hours unless `force`.
    """
    now = time.time()
    cached = _read_cache()
    if not force and cached:
        ts = cached.get("checked_at", 0)
        if now - ts < 24 * 3600:
            return cached

    req = urllib.request.Request(_RELEASES_API, headers={"User-Agent": "airfield-update-check"})
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

    latest_tag = data.get("tag_name") or data.get("name")
    html_url = data.get("html_url") or f"https://github.com/{_GITHUB_REPO}"
    if not latest_tag:
        if cached:
            return cached
        return None

    result = {
        "checked_at": now,
        "current_version": __version__,
        "latest_version": latest_tag,
        "url": html_url,
        "newer": _compare_versions(__version__, latest_tag) < 0,
    }
    _write_cache(result)
    return result


def update(force: bool = typer.Option(False, "--force", help="Ignore cache and force network check")):
    """Check for newer Airfield releases and print details."""
    res = check_for_update(force=force)
    if res is None:
        console.print("[yellow]Unable to determine update status.[/yellow]")
        raise typer.Exit(1)

    cur = res.get("current_version")
    latest = res.get("latest_version")
    url = res.get("url")
    if res.get("newer"):
        console.print(f"[bold yellow]Update available:[/bold yellow] {cur} → {latest}")
        console.print(f"Run: [bold]airfield system update --force[/bold] to refresh details")
        console.print(f"Release: {url}")
        raise typer.Exit(2)

    console.print(f"[green]Airfield up-to-date ({cur}).[/green]")


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
