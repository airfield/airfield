import os
from pathlib import Path
import pytest
from typer.testing import CliRunner

@pytest.fixture
def cli_runner():
    return CliRunner()

@pytest.fixture
def temp_workspace(tmp_path: Path):
    """Provides a temporary directory set as the current working directory."""
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)

@pytest.fixture
def mock_docker(mocker):
    """Mocks subprocess.run to prevent actual docker commands from running."""
    return mocker.patch("subprocess.run")
