"""Shared package definitions: packages a project USES as-is but does not
develop (tools like an AprilTag detector). Manifests with `kind: package` in
the dependency search paths are materialized into <project>/packages/<name>/
before use (redesign of Nathan's run-from-repo-manifest feature — a
definition is never executed out of the manifests folder) and gitignored,
since they are reproducible from the definition."""
import subprocess
from pathlib import Path

import pytest
import typer
import yaml

from airfield.cli.package_exec import resolve_package_context
from airfield.main import app


def _make_project(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    return temp_workspace


def _add_shared_definition(repo_dir: Path, name: str, extra: str = "") -> Path:
    xplatform = repo_dir / "xplatform"
    xplatform.mkdir(parents=True, exist_ok=True)
    manifest = xplatform / f"{name}.yaml"
    manifest.write_text(
        f"kind: package\nname: {name}\ndependencies: []\nros_distro: jazzy\n"
        f"run:\n  default: echo hi\n{extra}",
        encoding="utf-8",
    )
    return manifest


def test_config_only_shared_package_materializes(cli_runner, temp_workspace, mocker):
    _make_project(cli_runner, temp_workspace)
    shared_repo = temp_workspace / "shared_repo"
    _add_shared_definition(shared_repo, "shared_tool")
    mocker.patch("airfield.config.packages_repo_root", return_value=shared_repo)

    pkg_dir, pkg, deps, source_root = resolve_package_context("shared_tool", target_device="x86_64")

    materialized = temp_workspace / "packages" / "shared_tool"
    assert pkg_dir == materialized
    assert (materialized / "airfield.yaml").exists()
    assert (materialized / "src").is_dir()
    assert pkg.name == "shared_tool"
    assert pkg.run == {"default": "echo hi"}
    data = yaml.safe_load((materialized / "airfield.yaml").read_text(encoding="utf-8"))
    assert "source" not in data
    # Use-only tool: never committed to the project repo.
    gitignore = (temp_workspace / ".gitignore").read_text(encoding="utf-8")
    assert "packages/shared_tool/" in gitignore.splitlines()


def test_sourced_shared_package_clones(cli_runner, temp_workspace, mocker):
    _make_project(cli_runner, temp_workspace)

    # A local git repo standing in for the definition's source url.
    upstream = temp_workspace / "upstream_src"
    upstream.mkdir()
    (upstream / "node.py").write_text("print('hi')\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        ["git", "-c", "user.email=t@t.io", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=upstream, check=True, capture_output=True)

    shared_repo = temp_workspace / "shared_repo"
    _add_shared_definition(shared_repo, "shared_src_pkg", extra=f"source:\n  url: {upstream}\n")
    mocker.patch("airfield.config.packages_repo_root", return_value=shared_repo)

    pkg_dir, pkg, deps, source_root = resolve_package_context("shared_src_pkg", target_device="x86_64")

    materialized = temp_workspace / "packages" / "shared_src_pkg"
    assert pkg_dir == materialized
    assert (materialized / "node.py").exists()          # cloned source
    assert (materialized / "airfield.yaml").exists()    # definition written into clone
    assert pkg.source_path == "."
    gitignore = (temp_workspace / ".gitignore").read_text(encoding="utf-8")
    assert "packages/shared_src_pkg/" in gitignore.splitlines()


def test_shared_definition_requires_project(temp_workspace, mocker, capsys):
    shared_repo = temp_workspace / "shared_repo"
    _add_shared_definition(shared_repo, "shared_tool")
    mocker.patch("airfield.config.packages_repo_root", return_value=shared_repo)
    mocker.patch("airfield.cli.package_exec.find_project_root", return_value=None)

    with pytest.raises((typer.Exit, typer.BadParameter)):
        resolve_package_context("shared_tool", target_device="x86_64")


def test_plain_dependency_manifests_are_not_packages(cli_runner, temp_workspace, mocker):
    """No duck-typing: a dependency manifest (no kind) must never be treated
    as a package definition, even if oddly named."""
    _make_project(cli_runner, temp_workspace)
    shared_repo = temp_workspace / "shared_repo"
    xplatform = shared_repo / "xplatform"
    xplatform.mkdir(parents=True)
    (xplatform / "plain_dep.yaml").write_text(
        "name: plain_dep\nversion: 1.0.0\nsystem: []\nuser: []\n", encoding="utf-8"
    )
    mocker.patch("airfield.config.packages_repo_root", return_value=shared_repo)

    with pytest.raises(typer.BadParameter):
        resolve_package_context("plain_dep", target_device="x86_64")
    assert not (temp_workspace / "packages" / "plain_dep").exists()
