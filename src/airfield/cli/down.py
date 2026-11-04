import subprocess
from typing import List, Optional

import typer

from airfield.config import complete_plan_name, plan_names, require_project_root


def _tmux_sessions() -> List[str]:
    # Plans run as tmux sessions, so a host without tmux has none to tear down.
    # Report that instead of dying on FileNotFoundError, so --prune can still
    # sweep containers left over from a run started elsewhere.
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("tmux not found; no plan sessions to tear down.")
        return []

    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _kill_session(name: str) -> bool:
    return subprocess.run(["tmux", "kill-session", "-t", name], check=False).returncode == 0


def _prune_containers() -> None:
    # airfield-run-* containers are explicitly named only for Docker runs
    # (see run_container_foreground). If Docker isn't installed, skip.
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=airfield-run-"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("Docker not found; skipping airfield-run-* container prune.")
        return

    ids = [i for i in (result.stdout or "").split() if i]
    if not ids:
        print("No airfield-run-* containers to prune.")
        return
    subprocess.run(["docker", "rm", "-f", *ids], stdout=subprocess.DEVNULL, check=False)
    print(f"Pruned {len(ids)} airfield container(s).")


def run(
    plan_name: Optional[str] = typer.Argument(
        None,
        help="Plan/session to tear down (default: every running session that matches a plan)",
        autocompletion=complete_plan_name,
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Also force-remove ALL Docker airfield-run-* containers (crash cleanup; affects every airfield session on this host)",
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
        plans = set(plan_names(root))
        targets = [s for s in sessions if s in plans]
        if not targets:
            print("No running plan sessions found.")

    for name in targets:
        if _kill_session(name):
            print(f"Killed tmux session '{name}' (its containers stop via SIGHUP teardown).")
        else:
            print(f"Failed to kill tmux session '{name}'.")

    if prune:
        _prune_containers()
