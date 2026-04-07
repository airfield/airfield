import os
import questionary
from rich.console import Console
from jinja2 import Environment, PackageLoader, select_autoescape

console = Console()

def run():
    """Scaffold a new Airfield workspace."""
    workspace_name = questionary.text("What is the name of your new workspace?").ask()
    if not workspace_name:
        console.print("[red]Aborted.[/red]")
        return

    ros_distro = questionary.select(
        "Which ROS2 distribution?",
        choices=["humble", "jazzy", "rolling"]
    ).ask()

    # Setup Jinja2 Template Environment
    env = Environment(
        loader=PackageLoader("airfield", "templates"),
        autoescape=select_autoescape()
    )

    # Create the directory
    os.makedirs(workspace_name, exist_ok=True)
    
    # Render and write the Dockerfile template
    try:
        docker_template = env.get_template("Dockerfile.j2")
        docker_content = docker_template.render(ros_distro=ros_distro)
        with open(os.path.join(workspace_name, "Dockerfile"), "w") as f:
            f.write(docker_content)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not render Dockerfile (Template might be missing): {e}[/yellow]")
        touch_cmd = os.path.join(workspace_name, "Dockerfile")
        with open(touch_cmd, 'a'): pass

    console.print(f"[bold green]✨ Successfully created {workspace_name}![/bold green]")
