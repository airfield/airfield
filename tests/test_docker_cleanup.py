import pytest
from airfield.cli.docker_cleanup import cleanup_all_airfield_containers
from airfield.cli.docker_cache_cmd import cache_prune
from airfield.cli.tools_system import _prune_build_cache

def test_cleanup_all_airfield_containers_with_until(mocker):
    mock_run = mocker.patch("airfield.cli.docker_cleanup.subprocess.run")
    
    def mock_subprocess_run(cmd, *args, **kwargs):
        class MockResult:
            def __init__(self, stdout="", returncode=0, stderr=""):
                self.stdout = stdout
                self.returncode = returncode
                self.stderr = stderr
        if cmd[:2] == ["docker", "images"]:
            return MockResult(stdout="airfield-pkg-test:latest\n")
        if cmd[:2] == ["docker", "ps"]:
            return MockResult(stdout="dummy-container-id\n")
        return MockResult()

    mock_run.side_effect = mock_subprocess_run
    
    cleanup_all_airfield_containers(until="168h")
    
    ps_calls = [call for call in mock_run.call_args_list if call[0][0][:2] == ["docker", "ps"]]
    assert len(ps_calls) == 1
    args = ps_calls[0][0][0]
    assert "--filter" in args
    assert "until=168h" in args
    assert "ancestor=airfield-pkg-test:latest" in args

def test_cache_prune_flags(mocker):
    mock_run = mocker.patch("airfield.cli.docker_cache_cmd.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = ""
    
    # Test default
    cache_prune()
    args = mock_run.call_args[0][0]
    assert args == ["docker", "buildx", "prune", "-f"]
    
    # Test aggressive
    cache_prune(aggressive=True)
    args = mock_run.call_args[0][0]
    assert args == ["docker", "buildx", "prune", "-a", "-f"]
    
    # Test until
    cache_prune(until="24h")
    args = mock_run.call_args[0][0]
    assert args == ["docker", "buildx", "prune", "-f", "--filter", "until=24h"]
    
    # Test both
    cache_prune(aggressive=True, until="48h")
    args = mock_run.call_args[0][0]
    assert args == ["docker", "buildx", "prune", "-a", "-f", "--filter", "until=48h"]

def test_prune_build_cache_flags(mocker):
    mock_run = mocker.patch("airfield.cli.tools_system.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = ""
    
    # Test default
    _prune_build_cache()
    args = mock_run.call_args[0][0]
    assert args == ["docker", "builder", "prune", "-f"]
    
    # Test aggressive
    _prune_build_cache(aggressive=True)
    args = mock_run.call_args[0][0]
    assert args == ["docker", "builder", "prune", "-a", "-f"]
    
    # Test until
    _prune_build_cache(until="24h")
    args = mock_run.call_args[0][0]
    assert args == ["docker", "builder", "prune", "-f", "--filter", "until=24h"]
    
    # Test both
    _prune_build_cache(aggressive=True, until="48h")
    args = mock_run.call_args[0][0]
    assert args == ["docker", "builder", "prune", "-a", "-f", "--filter", "until=48h"]
