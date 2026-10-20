import pytest

from airfield.main import app

def test_project_init(cli_runner, temp_workspace):
    """Test initializing a new project."""
    result = cli_runner.invoke(app, ["project", "init", "my_robot"])
    
    assert result.exit_code == 0
    assert "Initialized Airfield project" in result.output
    
    project_dir = temp_workspace / "my_robot"
    assert project_dir.exists()
    assert (project_dir / "airfield.yaml").exists()
    assert (project_dir / "packages").exists()
    assert (project_dir / "dependencies" / "x86_64").exists()
    assert (project_dir / "dependencies" / "arm64").exists()
    assert (project_dir / "plans").exists()
    assert (project_dir / "plans" / "example.yaml").exists()
    assert (project_dir / ".dockerignore").exists()
    assert (project_dir / ".gitignore").exists()

def test_package_init_standalone(cli_runner, temp_workspace):
    """Test initializing a standalone package."""
    result = cli_runner.invoke(app, ["package", "init", "nav_stack"])
    
    assert result.exit_code == 0
    assert "Initialized Airfield package" in result.output
    
    pkg_dir = temp_workspace / "nav_stack"
    assert pkg_dir.exists()
    assert (pkg_dir / "airfield.yaml").exists()
    assert (pkg_dir / "src").exists()
    assert (pkg_dir / "README.md").exists()
    assert (pkg_dir / ".dockerignore").exists()
    assert (pkg_dir / ".gitignore").exists()

def test_package_init_inside_project(cli_runner, temp_workspace):
    """Test initializing a package inside a project."""
    cli_runner.invoke(app, ["project", "init", "."])
    
    result = cli_runner.invoke(app, ["package", "init", "nav_stack"])
    
    assert result.exit_code == 0
    
    pkg_dir = temp_workspace / "packages" / "nav_stack"
    assert pkg_dir.exists()
    assert (pkg_dir / "airfield.yaml").exists()
    assert (pkg_dir / "src").exists()
    assert (pkg_dir / "README.md").exists()
    assert (pkg_dir / ".dockerignore").exists()
    assert (pkg_dir / ".gitignore").exists()

def test_project_init_points_agents_at_the_guide(cli_runner, temp_workspace):
    result = cli_runner.invoke(app, ["project", "init", "."])

    assert result.exit_code == 0
    agents = (temp_workspace / "AGENTS.md").read_text()
    assert "run `airfield guide`" in agents
    assert "pipx install git+https://github.com/airfield/airfield.git" in agents

def test_project_init_adds_to_an_existing_agents_md_once(cli_runner, temp_workspace):
    """AGENTS.md belongs to the project: the Airfield section is appended once,
    and nothing that was already there changes."""
    ours = "# Our rules\n\nNever edit the calibration files."  # no final newline
    (temp_workspace / "AGENTS.md").write_text(ours)

    first = cli_runner.invoke(app, ["project", "init", "."])
    again = cli_runner.invoke(app, ["project", "init", ".", "--force"])

    agents = (temp_workspace / "AGENTS.md").read_text()
    assert agents.startswith(ours + "\n\n## Airfield\n")
    assert agents.count("## Airfield") == 1
    assert "Added an Airfield section" in first.output
    assert "Added an Airfield section" not in again.output

@pytest.mark.parametrize(
    "claude_md, import_line",
    [("CLAUDE.md", "@AGENTS.md"), (".claude/CLAUDE.md", "@../AGENTS.md")],
)
def test_project_init_says_when_claude_md_hides_agents_md(cli_runner, temp_workspace, claude_md, import_line):
    """Claude Code skips AGENTS.md in a project that has a CLAUDE.md."""
    (temp_workspace / claude_md).parent.mkdir(exist_ok=True)
    (temp_workspace / claude_md).write_text("# Project notes\n")

    result = cli_runner.invoke(app, ["project", "init", "."])

    assert result.exit_code == 0
    assert import_line in result.output
    assert (temp_workspace / claude_md).read_text() == "# Project notes\n"

def test_project_init_is_quiet_when_claude_md_already_reads_agents_md(cli_runner, temp_workspace):
    (temp_workspace / "CLAUDE.md").write_text("@AGENTS.md\n")

    result = cli_runner.invoke(app, ["project", "init", "."])

    assert "Claude Code" not in result.output
