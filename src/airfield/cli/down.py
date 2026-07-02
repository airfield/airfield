import subprocess
from typing import List, Optional

import typer

from airfield.config import plans_dir, require_project_root


def _tmux_sessions() -> List[str]:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _kill_session(name: str) -> bool:
    return subprocess.run(["tmux", "kill-session", "-t", name], check=False).returncode == 0


def _prune_containers() -> None:
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=airfield-run-"],
        capture_output=True,
        text=True,
        check=False,
    )
    ids = [i for i in (result.stdout or "").split() if i]
    if not ids:
        print("No airfield-run-* containers to prune.")
        return
    subprocess.run(["docker", "rm", "-f", *ids], stdout=subprocess.DEVNULL, check=False)
    print(f"Pruned {len(ids)} airfield container(s).")


def run(
    plan_name: Optional[str] = typer.Argument(
        None, help="Plan/session to tear down (default: every running session that matches a plan)"
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Also force-remove ALL airfield-run-* containers (crash cleanup; affects every airfield session on this host)",
    ),
):
    """Tear down a plan's tmux session.

    Killing the session SIGHUPs each pane's airfield process, which stops its
    own container (see run_container_foreground), so a plain `down` leaves no
    orphans. Use --prune to sweep containers left behind by hard crashes
    (SIGKILL, power loss), where no signal handler could run.
    """
    root = require_project_root()
    sessions = _tmux_sessions()

    if plan_name:
        targets = [plan_name] if plan_name in sessions else []
        if not targets:
            print(f"No running tmux session named '{plan_name}'.")
    else:
        p_dir = plans_dir(root)
        plan_names = {p.stem for p in p_dir.glob("*.yaml")} if p_dir.exists() else set()
        targets = [s for s in sessions if s in plan_names]
        if not targets:
            print("No running plan sessions found.")

    for name in targets:
        if _kill_session(name):
            print(f"Killed tmux session '{name}' (its containers stop via SIGHUP teardown).")
        else:
            print(f"Failed to kill tmux session '{name}'.")

    if prune:
        _prune_containers()
