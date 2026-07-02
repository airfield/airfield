import typer
from rich.console import Console
from typer.core import TyperGroup

from airfield.cli import build, dependencies_upstream, doctor, docker_cache_cmd, down, liftoff, pkg_cmd, pkg_deinit, pkg_init, pkg_run, pkg_shell, proj_deinit, proj_init, run, status, subprojects, up
from airfield.cli import tools_system
from airfield.config import ensure_airfield_runtime_dirs, find_package_root, find_project_root

console = Console()

class PrefixGroup(TyperGroup):
    """Click group with unique-prefix command resolution for registered commands."""

    def get_command(self, ctx: typer.Context, cmd_name: str):
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command

        matches = []
        for name in self.list_commands(ctx):
            if name.startswith(cmd_name):
                candidate = super().get_command(ctx, name)
                if candidate is not None:
                    matches.append((name, candidate))

        unique = {}
        for name, candidate in matches:
            unique[id(candidate)] = (name, candidate)

        if len(unique) == 1:
            return next(iter(unique.values()))[1]
        if len(unique) > 1:
            choices = sorted({name for name, _ in matches})
            console.print(f"[red]Error:[/red] Command '{cmd_name}' is ambiguous. Matches: {', '.join(choices)}")
            raise typer.Exit(1)

        return None


app = typer.Typer(help="Airfield: The robotics orchestration framework", cls=PrefixGroup)

pkg_app = typer.Typer(help="Package operations", cls=PrefixGroup, invoke_without_command=True)
proj_app = typer.Typer(help="Project operations", cls=PrefixGroup, invoke_without_command=True)
tools_app = typer.Typer(help="System tools", cls=PrefixGroup, invoke_without_command=True)
tools_system_app = typer.Typer(help="System maintenance", cls=PrefixGroup, invoke_without_command=True)
docker_app = typer.Typer(help="Docker build optimization", cls=PrefixGroup, invoke_without_command=True)

app.add_typer(pkg_app, name="package")
app.add_typer(proj_app, name="project")
app.add_typer(tools_app, name="tools")
tools_app.add_typer(tools_system_app, name="system")
# Expose `airfield system` as a top-level namespace as well
app.add_typer(tools_system_app, name="system")
app.add_typer(docker_app, name="docker")
app.add_typer(subprojects.app, name="subpackages")

pkg_app.command(name="init")(pkg_init.run)
pkg_app.command(name="deinit")(pkg_deinit.run)
pkg_app.command(name="build")(build.run)
pkg_app.command(name="shell")(pkg_shell.run)
pkg_app.command(name="cmd")(pkg_cmd.run)
pkg_app.command(name="run")(pkg_run.run)

deps_app = typer.Typer(help="Dependency repository operations", cls=PrefixGroup, invoke_without_command=True)
pkg_app.add_typer(deps_app, name="dependencies")
deps_app.command(name="check")(dependencies_upstream.check)
deps_app.command(name="upstream")(dependencies_upstream.upstream)

proj_app.command(name="init")(proj_init.run)
proj_app.command(name="deinit")(proj_deinit.run)
proj_app.command(name="run")(run.run)
proj_app.command(name="liftoff")(liftoff.run)
proj_app.command(name="up")(up.run)
proj_app.command(name="down")(down.run)

tools_system_app.command(name="clean")(tools_system.run)
tools_system_app.command(name="setup")(tools_system.setup)
tools_system_app.command(name="update")(tools_system.update)
tools_system_app.command(name="alias")(tools_system.install_alias)
tools_system_app.command(name="install-completion")(tools_system.install_completion)

docker_app.command(name="cache")(docker_cache_cmd.run)

app.command(name="status")(status.run)
app.command(name="doctor")(doctor.run)


@pkg_app.callback(invoke_without_command=True)
def package_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@proj_app.callback(invoke_without_command=True)
def project_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@tools_app.callback(invoke_without_command=True)
def tools_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@tools_system_app.callback(invoke_without_command=True)
def tools_system_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@docker_app.callback(invoke_without_command=True)
def docker_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


def _context_message():
    package_root = find_package_root()
    if package_root is not None:
        return f"Detected Airfield package at {package_root}."

    project_root = find_project_root()
    if project_root is not None:
        return f"Detected Airfield project at {project_root}."

    return None

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Airfield CLI"""
    ensure_airfield_runtime_dirs()
    message = _context_message()
    if message is not None:
        console.print(f"[dim]{message}[/dim]")

    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()

if __name__ == "__main__":
    app()
