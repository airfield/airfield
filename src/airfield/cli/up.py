import subprocess
from pathlib import Path

import typer
from jinja2 import Environment, PackageLoader, select_autoescape

from airfield.config import plans_dir, require_project_root
from airfield.models import Plan


def run(
    plan_name: str,
    output: Path = typer.Option(None, "--output", help="Path to write generated tmuxinator YAML"),
    launch: bool = typer.Option(False, "--launch", help="Launch tmuxinator after generation"),
):
    """Generate tmuxinator config for a plan and optionally launch it."""
    root = require_project_root()
    plan_yaml = plans_dir(root) / f"{plan_name}.yaml"
    if not plan_yaml.exists():
        print(f"Error: Plan {plan_name} not found at {plan_yaml}")
        raise typer.Exit(1)

    plan = Plan.load(plan_yaml)

    env = Environment(
        loader=PackageLoader("airfield", "templates"),
        autoescape=select_autoescape(),
    )
    template = env.get_template("tmux/tmuxinator.yml.j2")
    rendered = template.render(plan_name=plan.name, packages=plan.packages)

    output_path = output or (root / ".airfield" / f"{plan.name}.tmuxinator.yml")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    print(f"Generated tmuxinator config: {output_path}")

    if launch:
        cmd = ["tmuxinator", "start", "-p", str(output_path)]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise typer.Exit(result.returncode)
