import pytest
from pathlib import Path

from airfield.config import dependency_search_paths
from airfield.cli.package_exec import resolve_package_context
from airfield.models import Package, Dependency

def test_dependency_search_paths(tmp_path, mocker):
    mocker.patch("airfield.config.packages_repo_root", return_value=tmp_path / "global_packages")
    
    root = tmp_path / "project"
    
    # Setup directories
    local_target = root / "dependencies" / "arm64"
    local_xplatform = root / "dependencies" / "xplatform"
    global_target = tmp_path / "global_packages" / "arm64"
    global_xplatform = tmp_path / "global_packages" / "xplatform"
    
    # None exist initially
    assert dependency_search_paths(root, "arm64") == []
    
    # Create just global xplatform
    global_xplatform.mkdir(parents=True)
    assert dependency_search_paths(root, "arm64") == [global_xplatform]
    
    # Create global target
    global_target.mkdir(parents=True)
    assert dependency_search_paths(root, "arm64") == [global_target, global_xplatform]
    
    # Create local xplatform
    local_xplatform.mkdir(parents=True)
    assert dependency_search_paths(root, "arm64") == [local_xplatform, global_target, global_xplatform]
    
    # Create local target
    local_target.mkdir(parents=True)
    assert dependency_search_paths(root, "arm64") == [local_target, local_xplatform, global_target, global_xplatform]


def test_resolve_package_context_finds_xplatform_dependency(tmp_path, mocker):
    mocker.patch("airfield.config.find_project_root", return_value=None)
    mocker.patch("airfield.config.packages_repo_root", return_value=tmp_path / "global_packages")
    mocker.patch("airfield.cli.package_exec.require_package_root", return_value=tmp_path / "test_pkg")
    
    pkg_dir = tmp_path / "test_pkg"
    pkg_dir.mkdir()
    
    # Write a test package manifest
    pkg_yaml = pkg_dir / "airfield.yaml"
    pkg_yaml.write_text("""
name: test_pkg
dependencies:
  - shared_dep
  - specific_dep
source_path: .
""")
    
    # Create dependencies in global packages
    global_xplatform = tmp_path / "global_packages" / "xplatform"
    global_xplatform.mkdir(parents=True)
    
    shared_dep_yaml = global_xplatform / "shared_dep.yaml"
    shared_dep_yaml.write_text("""
name: shared_dep
version: 1.0.0
system: []
user: []
""")

    global_target = tmp_path / "global_packages" / "arm64"
    global_target.mkdir(parents=True)
    
    specific_dep_yaml = global_target / "specific_dep.yaml"
    specific_dep_yaml.write_text("""
name: specific_dep
version: 1.0.0
system: []
user: []
""")

    # Resolve context
    _, pkg, deps, _ = resolve_package_context(package_name=None, target_device="arm64")
    
    assert len(deps) == 2
    
    dep_names = {d.name for d in deps}
    assert "shared_dep" in dep_names
    assert "specific_dep" in dep_names
