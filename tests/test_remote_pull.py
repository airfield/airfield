import pytest
from pathlib import Path
from unittest.mock import MagicMock

from airfield.config import pull_packages_repo, PACKAGES_GITHUB_REPO
from airfield.main import app


def test_pull_packages_repo_success(tmp_path, mocker):
    repo = tmp_path / "packages"
    (repo / ".git").mkdir(parents=True)
    mocker.patch("airfield.config.packages_repo_root", return_value=repo)
    mock_run = mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))

    result = pull_packages_repo()

    assert result == repo
    mock_run.assert_called_once_with(
        ["git", "-C", str(repo), "pull"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_pull_packages_repo_fallback_https(tmp_path, mocker):
    repo = tmp_path / "packages"
    (repo / ".git").mkdir(parents=True)
    mocker.patch("airfield.config.packages_repo_root", return_value=repo)
    mock_run = mocker.patch(
        "subprocess.run",
        side_effect=[MagicMock(returncode=1), MagicMock(returncode=0)],
    )

    result = pull_packages_repo()

    assert result == repo
    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        ["git", "-C", str(repo), "pull", PACKAGES_GITHUB_REPO],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_pull_commands(cli_runner, mocker):
    mocker.patch("airfield.config.pull_packages_repo", return_value=Path("/fake/packages"))

    res1 = cli_runner.invoke(app, ["package", "pull"])
    assert res1.exit_code == 0
    assert "Successfully updated packages repository" in res1.output

    res2 = cli_runner.invoke(app, ["package", "dependencies", "pull"])
    assert res2.exit_code == 0
    assert "Successfully updated packages repository" in res2.output


def test_missing_dependency_triggers_auto_pull(tmp_path, mocker):
    from airfield.cli.package_exec import resolve_package_context
    mocker.patch("airfield.cli.package_exec.find_project_root", return_value=None)
    mocker.patch("airfield.cli.package_exec.require_package_root", return_value=tmp_path / "pkg")
    
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "airfield.yaml").write_text("name: pkg\ndependencies: [remote_dep]\nsource_path: .\n")

    search_path = tmp_path / "search"
    search_path.mkdir()
    mocker.patch("airfield.cli.package_exec.dependency_search_paths", return_value=[search_path])

    # When pull_packages_repo is called, simulate fetching remote_dep.yaml into search_path
    def side_effect_pull():
        (search_path / "remote_dep.yaml").write_text("name: remote_dep\nversion: 1.0.0\nsystem: []\nuser: []\n")
        return search_path

    mock_pull = mocker.patch("airfield.config.pull_packages_repo", side_effect=side_effect_pull)

    _, pkg, deps, _ = resolve_package_context(package_name=None, target_device="x86_64")

    assert mock_pull.called
    assert len(deps) == 1
    assert deps[0].name == "remote_dep"
