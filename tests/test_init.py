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
    assert "packages/" in (project_dir / ".gitignore").read_text()

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
