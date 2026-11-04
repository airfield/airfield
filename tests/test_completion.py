"""Tab completion.

Each <TAB> runs airfield with a _AIRFIELD_COMPLETE instruction in the
environment, the way the installed bash/zsh completion scripts do. When
nothing is offered, bash falls back to file names and zsh is told to run
`_files`, which is how a plan argument used to complete to the project's
directories.
"""
import subprocess
import sys

import pytest

from airfield.main import app


@pytest.fixture
def project(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])  # scaffolds plans/example.yaml
    plans = temp_workspace / "plans"
    (plans / "dashboard.yaml").write_text("name: dashboard\n", encoding="utf-8")
    (plans / "notes.md").write_text("not a plan\n", encoding="utf-8")
    return temp_workspace


def complete(cli_runner, shell, line):
    """What the <TAB> prints for `line`, cursor at its end."""
    if shell == "bash":
        words = line.split(" ")
        env = {"COMP_WORDS": "\n".join(words), "COMP_CWORD": str(len(words) - 1), "_AIRFIELD_COMPLETE": "complete_bash"}
    else:
        env = {"_TYPER_COMPLETE_ARGS": line, "_AIRFIELD_COMPLETE": "complete_zsh"}
    result = cli_runner.invoke(app, [], prog_name="airfield", env=env)
    assert result.exit_code == 0, result.output
    return result.output.strip()


@pytest.mark.parametrize("command", ["up", "down", "liftoff"])
def test_a_plan_argument_completes_to_the_projects_plans(cli_runner, project, command):
    assert complete(cli_runner, "bash", f"airfield project {command} ").split() == ["dashboard", "example"]
    assert complete(cli_runner, "bash", f"airfield project {command} da").split() == ["dashboard"]


def test_zsh_is_offered_the_plans_instead_of_files(cli_runner, project):
    answer = complete(cli_runner, "zsh", "airfield project up ")
    assert answer.startswith("_arguments"), answer
    assert '"dashboard"' in answer and '"example"' in answer


def test_from_a_subdirectory_of_the_project(cli_runner, project, monkeypatch):
    (project / "packages").mkdir(exist_ok=True)
    monkeypatch.chdir(project / "packages")
    assert complete(cli_runner, "bash", "airfield project up ").split() == ["dashboard", "example"]


def test_outside_a_project_the_shell_completes_files(cli_runner, temp_workspace):
    assert complete(cli_runner, "bash", "airfield project up ") == ""
    assert complete(cli_runner, "zsh", "airfield project up ") == "_files"


def test_an_ambiguous_start_lists_commands_instead_of_a_traceback(cli_runner, project):
    """`airfield p up` (package or project?) stops at the group, as a typo would."""
    assert {"status", "doctor", "guide"} <= set(complete(cli_runner, "bash", "airfield p up ").split())


def test_tab_does_not_load_the_heavy_libraries():
    """Every <TAB> starts airfield afresh, so these are imported only by the
    commands that use them (about 0.3 s of a 0.6 s Tab on a Jetson)."""
    heavy = ["pydantic", "jinja2", "airfield.builder", "airfield.models", "urllib.request"]
    code = f"import sys, airfield.main; print([m for m in {heavy!r} if m in sys.modules])"
    loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert loaded == "[]"
