"""Tests for `package dependencies pull` (adapted from Nathan's temp-pr-branch:
single command spelling, --ff-only, no bare-URL fallback, no implicit pull)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from airfield.config import pull_packages_repo
from airfield.main import app


def test_pull_packages_repo_success(tmp_path, mocker):
    repo = tmp_path / "packages"
    (repo / ".git").mkdir(parents=True)
    mocker.patch("airfield.config.packages_repo_root", return_value=repo)
    mock_run = mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))

    result = pull_packages_repo()

    assert result == repo
    mock_run.assert_called_once_with(
        ["git", "-C", str(repo), "pull", "--ff-only"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_pull_packages_repo_surfaces_git_errors(tmp_path, mocker):
    """No fallback pull from the canonical URL: a failed pull raises with
    git's own message instead of merging the upstream default branch into
    whatever branch the checkout is on."""
    repo = tmp_path / "packages"
    (repo / ".git").mkdir(parents=True)
    mocker.patch("airfield.config.packages_repo_root", return_value=repo)
    mock_run = mocker.patch(
        "subprocess.run",
        return_value=MagicMock(returncode=1, stderr="fatal: not possible to fast-forward", stdout=""),
    )

    with pytest.raises(RuntimeError, match="fast-forward"):
        pull_packages_repo()
    assert mock_run.call_count == 1


def test_cli_dependencies_pull(cli_runner, mocker):
    mocker.patch("airfield.config.pull_packages_repo", return_value=Path("/fake/packages"))

    res = cli_runner.invoke(app, ["package", "dependencies", "pull"])
    assert res.exit_code == 0
    assert "up to date" in res.output


def test_missing_manifest_error_hints_at_pull(tmp_path, mocker, capsys):
    """Dependency resolution stays offline; the error message points at the
    explicit pull command instead of git-pulling mid-build."""
    import typer

    from airfield.cli.package_exec import resolve_package_context

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "airfield.yaml").write_text(
        "name: pkg\ndependencies: [missing_dep]\nsource_path: .\n", encoding="utf-8"
    )
    search_path = tmp_path / "search"
    search_path.mkdir()
    mocker.patch("airfield.cli.package_exec.find_project_root", return_value=None)
    mocker.patch("airfield.cli.package_exec.require_package_root", return_value=pkg_dir)
    mocker.patch("airfield.cli.package_exec.dependency_search_paths", return_value=[search_path])
    git_mock = mocker.patch("subprocess.run")

    with pytest.raises(typer.Exit):
        resolve_package_context(package_name=None, target_device="x86_64")

    out = capsys.readouterr().out
    assert "manifest not found" in out
    assert "airfield package dependencies pull" in out
    git_mock.assert_not_called()
