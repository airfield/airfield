import typer
from rich.console import Console

from airfield.cli import create, build, up

app = typer.Typer(help="Airfield: The robotics orchestration framework")
console = Console()

app.command(name="create")(create.run)
app.command(name="build")(build.run)
app.command(name="up")(up.run)

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Airfield CLI"""
    if ctx.invoked_subcommand is None:
        console.print("[bold blue]Airfield[/bold blue] is ready. Run [cyan]airfield --help[/cyan] to see available commands.")

if __name__ == "__main__":
    app()
