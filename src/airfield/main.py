import click
import typer
from rich.console import Console
from typer.core import TyperGroup

from airfield.cli import build, doctor, docker_cache_cmd, liftoff, pkg_cmd, pkg_deinit, pkg_init, pkg_shell, proj_deinit, proj_init, run, status, up
from airfield.cli import tools_system
from airfield.config import find_package_root, find_project_root


class PrefixGroup(TyperGroup):
    """Click group with unique-prefix command resolution."""

    def get_command(self, ctx: click.Context, cmd_name: str):
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command

        matches = []
        for name in self.list_commands(ctx):
            if name.startswith(cmd_name) or cmd_name.startswith(name):
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
            raise click.UsageError(
                f"Command '{cmd_name}' is ambiguous. Matches: {', '.join(choices)}"
            )

        return None


class AirfieldRootGroup(PrefixGroup):
    """Root command group with context-aware namespace fallback."""

    def _default_namespace(self):
        package_root = find_package_root()
        if package_root is not None:
            return "package"

        project_root = find_project_root()
        if project_root is not None:
            return "project"

        return None

    def _namespace_subcommand_matches(self, ctx: click.Context, cmd_name: str):
        matches = []
        for namespace in ("project", "package"):
            ns_cmd = self.get_command(ctx, namespace)
            if ns_cmd is None:
                continue
            sub_cmd = ns_cmd.get_command(ctx, cmd_name)
            if sub_cmd is not None:
                matches.append((namespace, sub_cmd))
        return matches

    def resolve_command(self, ctx: click.Context, args):
        cmd_name = click.utils.make_str(args[0])

        # Keep `airfield p ...` as a compact project shorthand without exposing aliases in help.
        if cmd_name == "p":
            project_cmd = self.get_command(ctx, "project")
            if project_cmd is not None:
                return "project", project_cmd, args[1:]

        cmd = self.get_command(ctx, cmd_name)

        if cmd is not None:
            return cmd_name, cmd, args[1:]

        # Allow shorthand like `airfield b` to resolve to `airfield package build`
        # or `airfield project run` by matching namespace subcommands.
        sub_matches = self._namespace_subcommand_matches(ctx, cmd_name)
        if sub_matches:
            default_namespace = self._default_namespace()
            if default_namespace is not None:
                for namespace, sub_cmd in sub_matches:
                    if namespace == default_namespace:
                        ns_cmd = self.get_command(ctx, namespace)
                        if ns_cmd is not None:
                            ctx.meta["airfield_used_default_namespace"] = namespace
                            return namespace, ns_cmd, args

            if len(sub_matches) == 1:
                namespace = sub_matches[0][0]
                ns_cmd = self.get_command(ctx, namespace)
                if ns_cmd is not None:
                    return namespace, ns_cmd, args

            choices = sorted({f"{namespace}:{cmd.name}" for namespace, cmd in sub_matches})
            raise click.UsageError(
                f"Command '{cmd_name}' is ambiguous across namespaces. Matches: {', '.join(choices)}"
            )

        default_namespace = self._default_namespace()
        if default_namespace is not None:
            namespace_cmd = self.get_command(ctx, default_namespace)
            if namespace_cmd is not None:
                ctx.meta["airfield_used_default_namespace"] = default_namespace
                return default_namespace, namespace_cmd, args

        ctx.fail(f"No such command '{cmd_name}'.")


app = typer.Typer(help="Airfield: The robotics orchestration framework", cls=AirfieldRootGroup)
console = Console()

pkg_app = typer.Typer(help="Package operations", cls=PrefixGroup, invoke_without_command=True)
proj_app = typer.Typer(help="Project operations", cls=PrefixGroup, invoke_without_command=True)
tools_app = typer.Typer(help="System tools", cls=PrefixGroup, invoke_without_command=True)
tools_system_app = typer.Typer(help="System maintenance", cls=PrefixGroup, invoke_without_command=True)
docker_app = typer.Typer(help="Docker build optimization", cls=PrefixGroup, invoke_without_command=True)

app.add_typer(pkg_app, name="package")
app.add_typer(proj_app, name="project")
app.add_typer(tools_app, name="tools")
tools_app.add_typer(tools_system_app, name="system")
app.add_typer(docker_app, name="docker")

pkg_app.command(name="init")(pkg_init.run)
pkg_app.command(name="deinit")(pkg_deinit.run)
pkg_app.command(name="build")(build.run)
pkg_app.command(name="shell")(pkg_shell.run)
pkg_app.command(name="cmd")(pkg_cmd.run)
pkg_app.command(name="up")(up.run)

proj_app.command(name="init")(proj_init.run)
proj_app.command(name="deinit")(proj_deinit.run)
proj_app.command(name="run")(run.run)
proj_app.command(name="liftoff")(liftoff.run)

tools_system_app.command(name="clean")(tools_system.run)

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
        return f"Detected Airfield package at {package_root}. Default namespace: package."

    project_root = find_project_root()
    if project_root is not None:
        return f"Detected Airfield project at {project_root}. Default namespace: project."

    return None

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Airfield CLI"""
    message = _context_message()
    if message is not None:
        console.print(f"[dim]{message}[/dim]")

    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()

if __name__ == "__main__":
    app()
