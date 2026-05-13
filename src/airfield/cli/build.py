
import typer

from airfield.cli.package_exec import build_package_image, in_airfield_container, resolve_package_context


def run(
    package_name: str = typer.Argument(..., help="Package name/path (use '.' for current package)"),
    target_device: str = typer.Option("x86_64", "--target-device", help="Target architecture for dependency resolution"),
    show_all_output: bool = typer.Option(False, "--show-all-output", help="Show full Docker build output for debugging"),
):
    """Build a package container image."""
    if in_airfield_container():
        raise typer.BadParameter(
            "Already inside an Airfield container. "
            "You cannot build a new image from inside a container. "
            "Exit to the host and retry."
        )

    pkg_dir, pkg, deps, _ = resolve_package_context(package_name, target_device=target_device)
    image_name = build_package_image(
        pkg_dir,
        pkg,
        deps,
        target_device=target_device,
        show_all_output=show_all_output,
    )
    print(f"Build successful: {image_name}")
