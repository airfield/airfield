import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

import typer
from rich.console import Console

from airfield.config import find_project_root, load_project_config, save_project_config

console = Console()

app = typer.Typer(help="Subpackage source code operations", invoke_without_command=True)


def _get_log_file(project_root: Path) -> Path:
    log_dir = project_root / ".airfield" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "subprojects_log.json"


def _read_log(project_root: Path) -> List[Dict[str, Any]]:
    log_file = _get_log_file(project_root)
    if log_file.exists():
        try:
            with open(log_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []


def _write_log(project_root: Path, log_data: List[Dict[str, Any]]):
    log_file = _get_log_file(project_root)
    with open(log_file, "w") as f:
        json.dump(log_data, f, indent=2)


def _record_operation(project_root: Path, operation: str, affected: List[Dict[str, Any]]):
    if not affected:
        return
    log_data = _read_log(project_root)
    log_data.append({
        "timestamp": time.time(),
        "operation": operation,
        "affected": affected
    })
    _write_log(project_root, log_data)


def _get_subprojects(project_root: Path) -> List[Path]:
    subprojects = []
    
    # Check src directory
    src_dir = project_root / "src"
    if src_dir.exists() and src_dir.is_dir():
        for child in sorted(src_dir.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                subprojects.append(child)
                
    # Check packages directory
    packages_dir = project_root / "packages"
    if packages_dir.exists() and packages_dir.is_dir():
        for child in sorted(packages_dir.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                # Avoid duplicate names if they exist in both directories
                if not any(sp.name == child.name for sp in subprojects):
                    subprojects.append(child)
                    
    # Sort combined subprojects by name to keep order deterministic
    subprojects.sort(key=lambda p: p.name)
    return subprojects


def _run_git(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False
    )


def _is_dirty(cwd: Path) -> bool:
    res = _run_git(["status", "--porcelain"], cwd)
    if res.returncode == 0 and res.stdout.strip():
        return True
    return False


def _get_head(cwd: Path) -> Optional[str]:
    res = _run_git(["rev-parse", "HEAD"], cwd)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def _get_upstream(cwd: Path) -> Optional[str]:
    res = _run_git(["rev-parse", "--abbrev-ref", "@{u}"], cwd)
    if res.returncode == 0 and res.stdout.strip() and not res.stdout.strip().startswith("fatal:"):
        return res.stdout.strip()
    return None


def _get_remote_head(cwd: Path, upstream: str) -> Optional[str]:
    res = _run_git(["rev-parse", upstream], cwd)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def _ahead_behind(cwd: Path) -> (int, int):
    # Returns (ahead, behind)
    res = _run_git(["rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd)
    if res.returncode == 0 and res.stdout.strip():
        parts = res.stdout.strip().split()
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    return 0, 0


def _confirm(msg: str, auto: bool, diff_callback=None) -> bool:
    if auto:
        return True
    
    if diff_callback:
        prompt_msg = f"{msg} [y/N/d]"
        while True:
            response = typer.prompt(prompt_msg, default="N", show_default=False).lower()
            if response in ["y", "yes"]:
                return True
            elif response in ["n", "no", ""]:
                return False
            elif response in ["d", "diff"]:
                diff_callback()
            else:
                console.print("Please answer y, n, or d.")
    else:
        return typer.confirm(msg, default=False)


def _echo_via_more(text: str):
    lines = text.splitlines()
    chunk_size = 20
    for i in range(0, len(lines), chunk_size):
        for line in lines[i:i+chunk_size]:
            typer.echo(line)
        if i + chunk_size < len(lines):
            typer.echo("--- More --- (Press Enter to continue, 'q' to quit) ", nl=False)
            c = typer.getchar()
            typer.echo()
            if c.lower() == 'q':
                break


@app.command(name="status")
def cmd_status():
    """Print git status for all Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    if not subprojects:
        console.print("No Subpackages found in src/ or packages/")
        return

    console.print(f"[bold]Subpackages status ({len(subprojects)} total)[/bold]\n")
    for sp in subprojects:
        dirty = _is_dirty(sp)
        ahead, behind = _ahead_behind(sp)
        
        status_parts = []
        if dirty:
            status_parts.append("[red]dirty[/red]")
        else:
            status_parts.append("[green]clean[/green]")
            
        if ahead > 0:
            status_parts.append(f"[yellow]ahead {ahead}[/yellow]")
        if behind > 0:
            status_parts.append(f"[yellow]behind {behind}[/yellow]")
            
        if not dirty and ahead == 0 and behind == 0:
            status_parts = ["[dim]up to date[/dim]"]

        console.print(f"{sp.name}: {', '.join(status_parts)}")


@app.command(name="commit")
def cmd_commit(
    message: str = typer.Option(..., "-m", "--message", help="Commit message"),
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Commit changes in all dirty Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    affected = []

    for sp in subprojects:
        if _is_dirty(sp):
            def show_diff(sp_path=sp):
                status_res = _run_git(["-c", "color.status=always", "status", "-s"], sp_path)
                diff_res = _run_git(["diff", "--color=always", "HEAD"], sp_path)
                
                output = []
                if status_res.stdout.strip():
                    output.append("\033[1;33mStatus:\033[0m\n" + status_res.stdout)
                
                if diff_res.stdout.strip():
                    output.append("\033[1;33mDiff:\033[0m\n" + diff_res.stdout)
                
                if output:
                    _echo_via_more("\n".join(output))
                else:
                    console.print("No changes found.")
            
            if _confirm(f"Commit changes in {sp.name}?", auto, diff_callback=show_diff):
                head_before = _get_head(sp)
                _run_git(["add", "-A"], sp)
                res = _run_git(["commit", "-m", message], sp)
                if res.returncode == 0:
                    console.print(f"[green]Committed in {sp.name}[/green]")
                    affected.append({
                        "path": str(sp.relative_to(project_root)),
                        "old_head": head_before
                    })
                else:
                    console.print(f"[red]Failed to commit in {sp.name}[/red]:\n{res.stderr}")
            else:
                console.print(f"Skipped {sp.name}")

    _record_operation(project_root, "commit", affected)
    console.print(f"\nCommitted in {len(affected)} Subpackages.")


@app.command(name="push")
def cmd_push(
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Push commits in all ahead Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    affected = []

    for sp in subprojects:
        ahead, _ = _ahead_behind(sp)
        if ahead > 0:
            upstream = _get_upstream(sp)
            if not upstream:
                console.print(f"[yellow]Skipping {sp.name}: No upstream branch found[/yellow]")
                continue

            if _confirm(f"Push {ahead} commit(s) in {sp.name}?", auto):
                remote_head_before = _get_remote_head(sp, upstream)
                res = _run_git(["push"], sp)
                if res.returncode == 0:
                    console.print(f"[green]Pushed in {sp.name}[/green]")
                    affected.append({
                        "path": str(sp.relative_to(project_root)),
                        "upstream": upstream,
                        "old_remote_head": remote_head_before
                    })
                else:
                    console.print(f"[red]Failed to push in {sp.name}[/red]:\n{res.stderr}")
            else:
                console.print(f"Skipped {sp.name}")

    _record_operation(project_root, "push", affected)
    console.print(f"\nPushed in {len(affected)} Subpackages.")


@app.command(name="pull")
def cmd_pull(
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Pull changes in all behind Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    affected = []

    for sp in subprojects:
        _, behind = _ahead_behind(sp)
        if behind > 0:
            if _confirm(f"Pull {behind} commit(s) in {sp.name}?", auto):
                head_before = _get_head(sp)
                res = _run_git(["pull"], sp)
                if res.returncode == 0:
                    console.print(f"[green]Pulled in {sp.name}[/green]")
                    affected.append({
                        "path": str(sp.relative_to(project_root)),
                        "old_head": head_before
                    })
                else:
                    console.print(f"[red]Failed to pull in {sp.name}[/red]:\n{res.stderr}")
            else:
                console.print(f"Skipped {sp.name}")

    _record_operation(project_root, "pull", affected)
    console.print(f"\nPulled in {len(affected)} Subpackages.")


@app.command(name="stash")
def cmd_stash(
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Stash changes in all dirty Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    affected = []

    for sp in subprojects:
        if _is_dirty(sp):
            def show_diff(sp_path=sp):
                status_res = _run_git(["-c", "color.status=always", "status", "-s"], sp_path)
                diff_res = _run_git(["diff", "--color=always", "HEAD"], sp_path)
                
                output = []
                if status_res.stdout.strip():
                    output.append("\033[1;33mStatus:\033[0m\n" + status_res.stdout)
                
                if diff_res.stdout.strip():
                    output.append("\033[1;33mDiff:\033[0m\n" + diff_res.stdout)
                
                if output:
                    _echo_via_more("\n".join(output))
                else:
                    console.print("No changes found.")
            
            if _confirm(f"Stash changes in {sp.name}?", auto, diff_callback=show_diff):
                res = _run_git(["stash"], sp)
                if res.returncode == 0 and "No local changes to save" not in res.stdout:
                    console.print(f"[green]Stashed in {sp.name}[/green]")
                    affected.append({
                        "path": str(sp.relative_to(project_root)),
                        "stashed": True
                    })
                else:
                    console.print(f"[yellow]No stash created for {sp.name}[/yellow]")
            else:
                console.print(f"Skipped {sp.name}")

    _record_operation(project_root, "stash", affected)
    console.print(f"\nStashed in {len(affected)} Subpackages.")


@app.command(name="clean")
def cmd_clean(
    force: bool = typer.Option(False, "--force", "-f", help="Clean all changes without confirmation and only log")
):
    """Log, stash, and clean all changes in dirty Subpackages."""
    project_root = find_project_root()
    if not project_root:
        if not force:
            console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    affected = []

    for sp in subprojects:
        if _is_dirty(sp):
            if not force:
                def show_diff(sp_path=sp):
                    status_res = _run_git(["-c", "color.status=always", "status", "-s"], sp_path)
                    diff_res = _run_git(["diff", "--color=always", "HEAD"], sp_path)
                    
                    output = []
                    if status_res.stdout.strip():
                        output.append("\033[1;33mStatus:\033[0m\n" + status_res.stdout)
                    
                    if diff_res.stdout.strip():
                        output.append("\033[1;33mDiff:\033[0m\n" + diff_res.stdout)
                    
                    if output:
                        _echo_via_more("\n".join(output))
                    else:
                        console.print("No changes found.")
                
                if not _confirm(f"Clean changes in {sp.name}?", False, diff_callback=show_diff):
                    console.print(f"Skipped {sp.name}")
                    continue

            # Stash everything including untracked, then hard reset and clean
            _run_git(["stash", "-u"], sp)
            _run_git(["reset", "--hard", "HEAD"], sp)
            _run_git(["clean", "-fd"], sp)
            
            if not force:
                console.print(f"[green]Cleaned in {sp.name}[/green]")
            affected.append({
                "path": str(sp.relative_to(project_root)),
                "cleaned": True
            })

    _record_operation(project_root, "clean", affected)
    if not force:
        console.print(f"\nCleaned in {len(affected)} Subpackages.")


def _get_remote_url(cwd: Path) -> Optional[str]:
    res = _run_git(["config", "--get", "remote.origin.url"], cwd)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def _get_current_branch(cwd: Path) -> Optional[str]:
    res = _run_git(["branch", "--show-current"], cwd)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


@app.command(name="track")
def cmd_track(
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Ensure all Subpackages in src/ or packages/ are tracked in airfield.yaml."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    config_data = load_project_config(project_root)
    subprojects_config = config_data.get("subprojects", {})
    subprojects_on_disk = _get_subprojects(project_root)
    
    added_count = 0
    for sp in subprojects_on_disk:
        if sp.name not in subprojects_config:
            if _confirm(f"Untracked Subpackage '{sp.name}' found. Add to airfield.yaml?", auto):
                url = _get_remote_url(sp)
                branch = _get_current_branch(sp)
                
                entry = {}
                if url:
                    entry["url"] = url
                if branch:
                    entry["version"] = branch
                
                if not entry:
                    console.print(f"[yellow]Could not determine origin URL for {sp.name}. Skipping.[/yellow]")
                    continue
                
                subprojects_config[sp.name] = entry
                added_count += 1
                console.print(f"[green]Added {sp.name} to airfield.yaml[/green]")
            else:
                console.print(f"Skipped {sp.name}")
                
    if added_count > 0:
        config_data["subprojects"] = subprojects_config
        save_project_config(project_root, config_data)
        console.print(f"\nTracked {added_count} new Subpackages in airfield.yaml.")
    else:
        console.print("\nNo new Subpackages were tracked.")


@app.command(name="checkout")
def cmd_checkout():
    """Checkout missing Subpackages that are tracked in airfield.yaml."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    config_data = load_project_config(project_root)
    subprojects_config = config_data.get("subprojects", {})
    if not subprojects_config:
        console.print("No Subpackages configured in airfield.yaml.")
        return

    src_dir = project_root / "src"
    packages_dir = project_root / "packages"
    
    cloned_count = 0
    for name, sp_info in subprojects_config.items():
        sp_path_src = src_dir / name
        sp_path_packages = packages_dir / name
        
        if sp_path_src.exists() or sp_path_packages.exists():
            continue
            
        if packages_dir.exists() and packages_dir.is_dir():
            target_dir = packages_dir
        else:
            target_dir = src_dir
            
        target_dir.mkdir(exist_ok=True)
        sp_path = target_dir / name
        
        url = sp_info.get("url")
        version = sp_info.get("version")
        
        if not url:
            console.print(f"[yellow]Subpackage '{name}' is missing a URL in airfield.yaml. Skipping.[/yellow]")
            continue
            
        console.print(f"Cloning {name} from {url}...")
        
        clone_cmd = ["git", "clone"]
        
        res = subprocess.run(clone_cmd + [url, str(sp_path)], capture_output=True, text=True)
        if res.returncode == 0:
            if version:
                co_res = subprocess.run(["git", "checkout", version], cwd=sp_path, capture_output=True, text=True)
                if co_res.returncode != 0:
                    console.print(f"[red]Failed to checkout version '{version}' for {name}[/red]:\n{co_res.stderr}")
                else:
                    console.print(f"[green]Successfully cloned {name} and checked out '{version}'[/green]")
            else:
                console.print(f"[green]Successfully cloned {name}[/green]")
            cloned_count += 1
        else:
            console.print(f"[red]Failed to clone {name}[/red]:\n{res.stderr}")
                
    if cloned_count > 0:
        console.print(f"\nChecked out {cloned_count} Subpackages.")
    else:
        console.print("\nAll tracked Subpackages are already present.")


@app.command(name="undo")
def cmd_undo(
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Undo the last Subpackages operation (commit, push, pull, stash, or switch)."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    log_data = _read_log(project_root)
    if not log_data:
        console.print("No recent operations to undo.")
        return

    last_op = log_data.pop()
    op_name = last_op.get("operation")
    affected = last_op.get("affected", [])

    console.print(f"[bold]Undoing last operation: {op_name} ({len(affected)} Subpackages)[/bold]\n")

    for item in affected:
        sp = project_root / item["path"]
        if not sp.exists():
            console.print(f"[yellow]Skipping {sp.name} (not found)[/yellow]")
            continue

        if not _confirm(f"Undo {op_name} in {sp.name}?", auto):
            console.print(f"Skipped {sp.name}")
            continue

        if op_name == "commit":
            # Undo commit
            res = _run_git(["reset", "--soft", "HEAD~1"], sp)
            if res.returncode == 0:
                console.print(f"[green]Undid commit in {sp.name}[/green]")
            else:
                console.print(f"[red]Failed to undo commit in {sp.name}[/red]:\n{res.stderr}")
                
        elif op_name == "pull":
            # Undo pull
            old_head = item.get("old_head")
            if old_head:
                res = _run_git(["reset", "--hard", old_head], sp)
                if res.returncode == 0:
                    console.print(f"[green]Undid pull in {sp.name}[/green]")
                else:
                    console.print(f"[red]Failed to undo pull in {sp.name}[/red]:\n{res.stderr}")
                    
        elif op_name == "push":
            # Undo push
            old_remote_head = item.get("old_remote_head")
            upstream = item.get("upstream")
            if old_remote_head and upstream:
                # upstream is typically e.g. origin/main. We need the remote name and the branch.
                parts = upstream.split("/", 1)
                if len(parts) == 2:
                    remote, remote_branch = parts[0], parts[1]
                    res = _run_git(["push", "--force-with-lease", remote, f"{old_remote_head}:{remote_branch}"], sp)
                    if res.returncode == 0:
                        console.print(f"[green]Undid push in {sp.name}[/green]")
                    else:
                        console.print(f"[red]Failed to undo push in {sp.name}[/red]:\n{res.stderr}")
                else:
                    console.print(f"[red]Could not parse upstream branch {upstream} for {sp.name}[/red]")
                    
        elif op_name == "stash":
            # Undo stash
            if item.get("stashed"):
                res = _run_git(["stash", "pop"], sp)
                if res.returncode == 0:
                    console.print(f"[green]Undid stash in {sp.name}[/green]")
                else:
                    console.print(f"[red]Failed to pop stash in {sp.name}[/red]:\n{res.stderr}")
                    
        elif op_name == "clean":
            # Undo clean
            if item.get("cleaned"):
                res = _run_git(["stash", "pop"], sp)
                if res.returncode == 0:
                    console.print(f"[green]Undid clean in {sp.name}[/green]")
                else:
                    console.print(f"[yellow]Could not pop stash in {sp.name} (might be clean)[/yellow]")
                    
        elif op_name == "switch":
            target = item.get("old_branch") or item.get("old_head")
            if target:
                res = _run_git(["checkout", target], sp)
                if res.returncode == 0:
                    console.print(f"[green]Undid switch in {sp.name}[/green]")
                else:
                    console.print(f"[red]Failed to undo switch in {sp.name}[/red]:\n{res.stderr}")

        else:
            console.print(f"[yellow]Unknown operation {op_name} in log[/yellow]")

    _write_log(project_root, log_data)
    console.print("\nUndo complete.")


@app.command(name="diff")
def cmd_diff(
    staged: bool = typer.Option(False, "--staged", "--cached", help="Show diff of staged changes"),
    head: bool = typer.Option(False, "--head", help="Show diff of all changes (staged and unstaged)")
):
    """Show git diff for all dirty Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    subprojects = _get_subprojects(project_root)
    dirty_count = 0
    full_output = []

    for sp in subprojects:
        if _is_dirty(sp):
            dirty_count += 1
            cmd = ["diff", "--color=always"]
            if staged:
                cmd.append("--staged")
            elif head:
                cmd.append("HEAD")
            
            output = []
            status_res = _run_git(["-c", "color.status=always", "status", "-s"], sp)
            if status_res.stdout.strip():
                output.append("\033[1;33mStatus:\033[0m\n" + status_res.stdout)
                
            res = _run_git(cmd, sp)
            if res.stdout.strip():
                output.append("\033[1;33mDiff:\033[0m\n" + res.stdout)
                
            if output:
                full_output.append(f"\033[1;34m=== {sp.name} ===\033[0m\n")
                full_output.append("\n".join(output) + "\n")
    
    if dirty_count == 0:
        console.print("No dirty Subpackages found.")
    elif full_output:
        _echo_via_more("".join(full_output))


@app.command(name="switch")
def cmd_switch(
    branch: Optional[str] = typer.Argument(None, help="Branch name to switch to"),
    auto: bool = typer.Option(False, "--auto", help="Do not prompt for confirmation per Subpackage")
):
    """Switch branches on all Subpackages."""
    project_root = find_project_root()
    if not project_root:
        console.print("[yellow]Not in an Airfield project.[/yellow]")
        raise typer.Exit(1)

    if branch is None:
        target_branch = _get_current_branch(project_root)
        if not target_branch:
            console.print("[red]Could not determine current branch of parent project.[/red]")
            raise typer.Exit(1)
        if not _confirm(f"Switch all Subpackages to branch '{target_branch}'?", auto):
            console.print("Skipped switching branches.")
            return
    else:
        target_branch = branch

    subprojects = _get_subprojects(project_root)
    if not subprojects:
        console.print("No Subpackages found in src/ or packages/")
        return

    affected = []
    for sp in subprojects:
        old_branch = _get_current_branch(sp)
        old_head = _get_head(sp)
        res = _run_git(["checkout", target_branch], sp)
        if res.returncode == 0:
            console.print(f"[green]Switched Subpackage '{sp.name}' to branch '{target_branch}'[/green]")
            affected.append({
                "path": str(sp.relative_to(project_root)),
                "old_branch": old_branch,
                "old_head": old_head,
                "switched_to": target_branch
            })
        else:
            console.print(f"[yellow]Warning: Could not switch Subpackage '{sp.name}' to branch '{target_branch}': {res.stderr.strip()}[/yellow]")

    _record_operation(project_root, "switch", affected)
    console.print(f"\nSwitched branches in {len(affected)} Subpackages.")


@app.callback(invoke_without_command=True)
def subprojects_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()
