import subprocess
from pathlib import Path

from airfield.main import app


def setup_git_repo(path: Path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "file.txt").write_text("initial\n")
    subprocess.run(["git", "add", "file.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_subprojects_status(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    
    result = cli_runner.invoke(app, ["subpackages", "status"])
    assert result.exit_code == 0
    assert "sub1: dirty" in result.output


def test_subprojects_commit_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    
    # Commit changes
    result = cli_runner.invoke(app, ["subpackages", "commit", "-m", "update", "--auto"])
    assert result.exit_code == 0
    assert "Committed in sub1" in result.output
    
    status_result = cli_runner.invoke(app, ["subpackages", "status"])
    assert "sub1: up to date" in status_result.output
    
    # Undo commit
    undo_result = cli_runner.invoke(app, ["subpackages", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid commit in sub1" in undo_result.output
    
    # Verify it is dirty again
    status_result2 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "sub1: dirty" in status_result2.output


def test_subprojects_stash_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    
    result = cli_runner.invoke(app, ["subpackages", "stash", "--auto"])
    assert result.exit_code == 0
    assert "Stashed in sub1" in result.output
    
    status_result = cli_runner.invoke(app, ["subpackages", "status"])
    assert "sub1: up to date" in status_result.output
    
    undo_result = cli_runner.invoke(app, ["subpackages", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid stash in sub1" in undo_result.output
    
    status_result2 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "sub1: dirty" in status_result2.output


def test_subprojects_push_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    remote_repo = temp_workspace / "remote"
    remote_repo.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=remote_repo, check=True, capture_output=True)
    
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    subprocess.run(["git", "remote", "add", "origin", str(remote_repo)], cwd=sub1, check=True)
    subprocess.run(["git", "push", "-u", "origin", "master"], cwd=sub1, check=True, capture_output=True)
    
    # Create a new commit to push
    (sub1 / "file.txt").write_text("push me\n")
    subprocess.run(["git", "add", "file.txt"], cwd=sub1, check=True)
    subprocess.run(["git", "commit", "-m", "to push"], cwd=sub1, check=True, capture_output=True)
    
    status_result = cli_runner.invoke(app, ["subpackages", "status"])
    assert "ahead 1" in status_result.output
    
    push_result = cli_runner.invoke(app, ["subpackages", "push", "--auto"])
    assert push_result.exit_code == 0
    assert "Pushed in sub1" in push_result.output
    
    status_result2 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "up to date" in status_result2.output
    
    # Undo push
    undo_push = cli_runner.invoke(app, ["subpackages", "undo", "--auto"])
    assert undo_push.exit_code == 0
    assert "Undid push in sub1" in undo_push.output
    
    status_result3 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "ahead 1" in status_result3.output


def test_subprojects_pull_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    remote_repo = temp_workspace / "remote"
    remote_repo.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=remote_repo, check=True, capture_output=True)
    
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    subprocess.run(["git", "remote", "add", "origin", str(remote_repo)], cwd=sub1, check=True)
    subprocess.run(["git", "push", "-u", "origin", "master"], cwd=sub1, check=True, capture_output=True)
    
    # Clone to another place to make a remote commit
    other = temp_workspace / "other"
    subprocess.run(["git", "clone", str(remote_repo), str(other)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
    
    (other / "file.txt").write_text("pulled\n")
    subprocess.run(["git", "add", "file.txt"], cwd=other, check=True)
    subprocess.run(["git", "commit", "-m", "remote commit"], cwd=other, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=other, check=True, capture_output=True)
    
    # Fetch in sub1 so it knows it is behind
    subprocess.run(["git", "fetch"], cwd=sub1, check=True, capture_output=True)
    
    status_result = cli_runner.invoke(app, ["subpackages", "status"])
    assert "behind 1" in status_result.output
    
    pull_result = cli_runner.invoke(app, ["subpackages", "pull", "--auto"])
    assert pull_result.exit_code == 0
    assert "Pulled in sub1" in pull_result.output
    
    status_result2 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "up to date" in status_result2.output
    
    undo_result = cli_runner.invoke(app, ["subpackages", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid pull in sub1" in undo_result.output
    
    status_result3 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "behind 1" in status_result3.output


import yaml

def test_subprojects_track(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    remote_repo = temp_workspace / "remote"
    remote_repo.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=remote_repo, check=True, capture_output=True)
    
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    subprocess.run(["git", "remote", "add", "origin", str(remote_repo)], cwd=sub1, check=True)
    
    result = cli_runner.invoke(app, ["subpackages", "track", "--auto"])
    assert result.exit_code == 0
    assert "Tracked 1 new Subpackages" in result.output
    
    # Verify yaml
    with open(temp_workspace / "airfield.yaml") as f:
        data = yaml.safe_load(f)
    assert "subprojects" in data
    assert "sub1" in data["subprojects"]
    assert data["subprojects"]["sub1"]["url"] == str(remote_repo)


def test_subprojects_checkout(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    # Delete packages folder to test fallback to src
    import shutil
    shutil.rmtree(temp_workspace / "packages")
    
    # Create a remote repo
    remote_repo = temp_workspace / "remote"
    remote_repo.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=remote_repo, check=True, capture_output=True)
    
    # Set up dummy repo content so we can clone it
    dummy = temp_workspace / "dummy"
    dummy.mkdir()
    setup_git_repo(dummy)
    subprocess.run(["git", "remote", "add", "origin", str(remote_repo)], cwd=dummy, check=True)
    subprocess.run(["git", "push", "-u", "origin", "master"], cwd=dummy, check=True, capture_output=True)
    
    # Write to airfield.yaml
    with open(temp_workspace / "airfield.yaml", "w") as f:
        yaml.dump({
            "kind": "project",
            "subprojects": {
                "sub1": {
                    "url": str(remote_repo)
                }
            }
        }, f)
        
    result = cli_runner.invoke(app, ["subpackages", "checkout"])
    assert result.exit_code == 0
    assert "Successfully cloned sub1" in result.output
    
    sub1_path = temp_workspace / "src" / "sub1"
    assert sub1_path.exists()
    assert (sub1_path / ".git").exists()


def test_subprojects_diff(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    # No dirty subprojects
    result = cli_runner.invoke(app, ["subpackages", "diff"])
    assert result.exit_code == 0
    assert "No dirty Subpackages found." in result.output
    
    # Make it dirty
    (sub1 / "file.txt").write_text("changed\n")
    
    result = cli_runner.invoke(app, ["subpackages", "diff"])
    assert result.exit_code == 0
    assert "=== sub1 ===" in result.output
    assert "changed" in result.output
    assert "initial" in result.output
    
    # Stage changes
    subprocess.run(["git", "add", "file.txt"], cwd=sub1, check=True)
    
    # diff --staged
    result = cli_runner.invoke(app, ["subpackages", "diff", "--staged"])
    assert result.exit_code == 0
    assert "=== sub1 ===" in result.output
    assert "changed" in result.output



def test_subprojects_clean_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    (sub1 / "untracked.txt").write_text("untracked\n")
    
    result = cli_runner.invoke(app, ["subpackages", "clean", "--force"])
    assert result.exit_code == 0
    # because of --force, it should only log and have no output printed to console
    # Wait, the output for --force should be empty or at least no confirmation prompts.
    # Actually wait, let's verify if the file is clean.
    
    status_result = cli_runner.invoke(app, ["subpackages", "status"])
    assert "sub1: up to date" in status_result.output
    
    # Check untracked is gone
    assert not (sub1 / "untracked.txt").exists()
    assert (sub1 / "file.txt").read_text() == "initial\n"
    
    # Undo clean
    undo_result = cli_runner.invoke(app, ["subpackages", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid clean in sub1" in undo_result.output
    
    # Wait, undo clean (stash pop) should restore the changed file and untracked file
    status_result2 = cli_runner.invoke(app, ["subpackages", "status"])
    assert "sub1: dirty" in status_result2.output
    assert (sub1 / "untracked.txt").exists()
    assert (sub1 / "file.txt").read_text() == "changed\n"


def test_subprojects_in_packages_folder(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "packages" / "sub_in_proj"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    
    # Test subpackages command alias as well
    result = cli_runner.invoke(app, ["subpackages", "status"])
    assert result.exit_code == 0
    assert "sub_in_proj: dirty" in result.output


def test_subprojects_checkout_to_packages(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    
    # Create a remote repo
    remote_repo = temp_workspace / "remote"
    remote_repo.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=remote_repo, check=True, capture_output=True)
    
    # Set up dummy repo content so we can clone it
    dummy = temp_workspace / "dummy"
    dummy.mkdir()
    setup_git_repo(dummy)
    subprocess.run(["git", "remote", "add", "origin", str(remote_repo)], cwd=dummy, check=True)
    subprocess.run(["git", "push", "-u", "origin", "master"], cwd=dummy, check=True, capture_output=True)
    
    # Pre-create the packages directory to signal we want to clone there (project init does it, but we ensure it here)
    (temp_workspace / "packages").mkdir(exist_ok=True)
    
    # Write to airfield.yaml
    with open(temp_workspace / "airfield.yaml", "w") as f:
        yaml.dump({
            "kind": "project",
            "subprojects": {
                "sub1": {
                    "url": str(remote_repo)
                }
            }
        }, f)
        
    result = cli_runner.invoke(app, ["subpackages", "checkout"])
    assert result.exit_code == 0
    assert "Successfully cloned sub1" in result.output
    
    # Verify it was cloned to packages/sub1
    sub1_path = temp_workspace / "packages" / "sub1"
    assert sub1_path.exists()
    assert (sub1_path / ".git").exists()
    
    # Verify src/sub1 does not exist
    assert not (temp_workspace / "src" / "sub1").exists()


def test_subpackages_switch_and_undo(cli_runner, temp_workspace):
    # Setup parent project git repo
    setup_git_repo(temp_workspace)
    subprocess.run(["git", "branch", "feature-main"], cwd=temp_workspace, check=True)
    subprocess.run(["git", "checkout", "feature-main"], cwd=temp_workspace, check=True)

    cli_runner.invoke(app, ["project", "init", "."])

    # Setup subpackage repo
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    subprocess.run(["git", "branch", "feature-main"], cwd=sub1, check=True)
    subprocess.run(["git", "branch", "explicit-branch"], cwd=sub1, check=True)

    # 1. Test switch without args (should prompt for confirmation unless --auto)
    # Cancel confirmation
    cancel_res = cli_runner.invoke(app, ["subpackages", "switch"], input="n\n")
    assert cancel_res.exit_code == 0
    assert "Skipped switching branches" in cancel_res.output

    # Confirm confirmation
    switch_res = cli_runner.invoke(app, ["subpackage", "switch"], input="y\n")
    assert switch_res.exit_code == 0
    assert "Switched Subpackage 'sub1' to branch 'feature-main'" in switch_res.output

    # Verify branch switched
    res = subprocess.run(["git", "branch", "--show-current"], cwd=sub1, capture_output=True, text=True)
    assert res.stdout.strip() == "feature-main"

    # 2. Undo switch
    undo_res = cli_runner.invoke(app, ["subpackages", "undo", "--auto"])
    assert undo_res.exit_code == 0
    assert "Undid switch in sub1" in undo_res.output
    res_undo = subprocess.run(["git", "branch", "--show-current"], cwd=sub1, capture_output=True, text=True)
    assert res_undo.stdout.strip() in ["master", "main"]

    # 3. Test switch with explicit arg (no prompt needed)
    explicit_res = cli_runner.invoke(app, ["subpackage", "switch", "explicit-branch"])
    assert explicit_res.exit_code == 0
    assert "Switched Subpackage 'sub1' to branch 'explicit-branch'" in explicit_res.output
    res_exp = subprocess.run(["git", "branch", "--show-current"], cwd=sub1, capture_output=True, text=True)
    assert res_exp.stdout.strip() == "explicit-branch"

    # 4. Test switch when branch not found (prompt to create)
    # Skip creation
    skip_res = cli_runner.invoke(app, ["subpackages", "switch", "new-missing-branch"], input="n\n")
    assert "Skipped creating branch 'new-missing-branch' in Subpackage 'sub1'" in skip_res.output

    # Confirm creation
    create_res = cli_runner.invoke(app, ["subpackages", "switch", "new-missing-branch"], input="y\n")
    assert "Created and switched Subpackage 'sub1' to new branch 'new-missing-branch'" in create_res.output
    res_new = subprocess.run(["git", "branch", "--show-current"], cwd=sub1, capture_output=True, text=True)
    assert res_new.stdout.strip() == "new-missing-branch"

    # 5. Test switch with 'a' (yes to all)
    sub2 = temp_workspace / "src" / "sub2"
    sub2.mkdir(parents=True)
    setup_git_repo(sub2)
    all_res = cli_runner.invoke(app, ["subpackages", "switch", "all-missing-branch"], input="a\n")
    assert "Created and switched Subpackage 'sub1' to new branch 'all-missing-branch'" in all_res.output
    assert "Created and switched Subpackage 'sub2' to new branch 'all-missing-branch'" in all_res.output


def test_subpackages_find_and_cd(cli_runner, temp_workspace, mocker):
    setup_git_repo(temp_workspace)
    cli_runner.invoke(app, ["project", "init", "."])

    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)

    # 1. Test find
    find_res = cli_runner.invoke(app, ["subpackages", "find", "sub1"])
    assert find_res.exit_code == 0
    assert str(sub1.resolve()) in find_res.output

    find_bad = cli_runner.invoke(app, ["subpackages", "find", "nonexistent"])
    assert find_bad.exit_code == 1

    # 2. Test cd
    mock_chdir = mocker.patch("os.chdir")
    mock_execvp = mocker.patch("os.execvp")
    cd_res = cli_runner.invoke(app, ["subpackages", "cd", "sub1"])
    assert cd_res.exit_code == 0
    mock_chdir.assert_called_once_with(sub1.resolve())
    mock_execvp.assert_called_once()


