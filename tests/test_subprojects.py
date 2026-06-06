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
    
    result = cli_runner.invoke(app, ["subprojects", "status"])
    assert result.exit_code == 0
    assert "sub1: dirty" in result.output


def test_subprojects_commit_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    
    # Commit changes
    result = cli_runner.invoke(app, ["subprojects", "commit", "-m", "update", "--auto"])
    assert result.exit_code == 0
    assert "Committed in sub1" in result.output
    
    status_result = cli_runner.invoke(app, ["subprojects", "status"])
    assert "sub1: up to date" in status_result.output
    
    # Undo commit
    undo_result = cli_runner.invoke(app, ["subprojects", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid commit in sub1" in undo_result.output
    
    # Verify it is dirty again
    status_result2 = cli_runner.invoke(app, ["subprojects", "status"])
    assert "sub1: dirty" in status_result2.output


def test_subprojects_stash_and_undo(cli_runner, temp_workspace):
    cli_runner.invoke(app, ["project", "init", "."])
    sub1 = temp_workspace / "src" / "sub1"
    sub1.mkdir(parents=True)
    setup_git_repo(sub1)
    
    (sub1 / "file.txt").write_text("changed\n")
    
    result = cli_runner.invoke(app, ["subprojects", "stash", "--auto"])
    assert result.exit_code == 0
    assert "Stashed in sub1" in result.output
    
    status_result = cli_runner.invoke(app, ["subprojects", "status"])
    assert "sub1: up to date" in status_result.output
    
    undo_result = cli_runner.invoke(app, ["subprojects", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid stash in sub1" in undo_result.output
    
    status_result2 = cli_runner.invoke(app, ["subprojects", "status"])
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
    
    status_result = cli_runner.invoke(app, ["subprojects", "status"])
    assert "ahead 1" in status_result.output
    
    push_result = cli_runner.invoke(app, ["subprojects", "push", "--auto"])
    assert push_result.exit_code == 0
    assert "Pushed in sub1" in push_result.output
    
    status_result2 = cli_runner.invoke(app, ["subprojects", "status"])
    assert "up to date" in status_result2.output
    
    # Undo push
    undo_push = cli_runner.invoke(app, ["subprojects", "undo", "--auto"])
    assert undo_push.exit_code == 0
    assert "Undid push in sub1" in undo_push.output
    
    status_result3 = cli_runner.invoke(app, ["subprojects", "status"])
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
    
    status_result = cli_runner.invoke(app, ["subprojects", "status"])
    assert "behind 1" in status_result.output
    
    pull_result = cli_runner.invoke(app, ["subprojects", "pull", "--auto"])
    assert pull_result.exit_code == 0
    assert "Pulled in sub1" in pull_result.output
    
    status_result2 = cli_runner.invoke(app, ["subprojects", "status"])
    assert "up to date" in status_result2.output
    
    undo_result = cli_runner.invoke(app, ["subprojects", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid pull in sub1" in undo_result.output
    
    status_result3 = cli_runner.invoke(app, ["subprojects", "status"])
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
    
    result = cli_runner.invoke(app, ["subprojects", "track", "--auto"])
    assert result.exit_code == 0
    assert "Tracked 1 new subprojects" in result.output
    
    # Verify yaml
    with open(temp_workspace / "airfield.yaml") as f:
        data = yaml.safe_load(f)
    assert "subprojects" in data
    assert "sub1" in data["subprojects"]
    assert data["subprojects"]["sub1"]["url"] == str(remote_repo)


def test_subprojects_checkout(cli_runner, temp_workspace):
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
        
    result = cli_runner.invoke(app, ["subprojects", "checkout"])
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
    result = cli_runner.invoke(app, ["subprojects", "diff"])
    assert result.exit_code == 0
    assert "No dirty subprojects found." in result.output
    
    # Make it dirty
    (sub1 / "file.txt").write_text("changed\n")
    
    result = cli_runner.invoke(app, ["subprojects", "diff"])
    assert result.exit_code == 0
    assert "=== sub1 ===" in result.output
    assert "changed" in result.output
    assert "initial" in result.output
    
    # Stage changes
    subprocess.run(["git", "add", "file.txt"], cwd=sub1, check=True)
    
    # diff --staged
    result = cli_runner.invoke(app, ["subprojects", "diff", "--staged"])
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
    
    result = cli_runner.invoke(app, ["subprojects", "clean", "--force"])
    assert result.exit_code == 0
    # because of --force, it should only log and have no output printed to console
    # Wait, the output for --force should be empty or at least no confirmation prompts.
    # Actually wait, let's verify if the file is clean.
    
    status_result = cli_runner.invoke(app, ["subprojects", "status"])
    assert "sub1: up to date" in status_result.output
    
    # Check untracked is gone
    assert not (sub1 / "untracked.txt").exists()
    assert (sub1 / "file.txt").read_text() == "initial\n"
    
    # Undo clean
    undo_result = cli_runner.invoke(app, ["subprojects", "undo", "--auto"])
    assert undo_result.exit_code == 0
    assert "Undid clean in sub1" in undo_result.output
    
    # Wait, undo clean (stash pop) should restore the changed file and untracked file
    status_result2 = cli_runner.invoke(app, ["subprojects", "status"])
    assert "sub1: dirty" in status_result2.output
    assert (sub1 / "untracked.txt").exists()
    assert (sub1 / "file.txt").read_text() == "changed\n"
