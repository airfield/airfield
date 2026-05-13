"""Docker build optimization utilities for BuildKit cache mounts."""

from pathlib import Path


# Standard .dockerignore content for optimized builds
DOCKERIGNORE_CONTENT = """# Git
.git/
.gitignore
.gitattributes

# Build and cache directories
build/
install/
log/
devel/
*.egg-info/
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
.tox/
.coverage
.cache/
*.cache

# IDE and editor files
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store
*.sublime-project
*.sublime-workspace

# Testing
.pytest_cache/
.tox/
coverage.xml
test-results/

# Documentation build
docs/_build/
site/

# Temporary files
*.tmp
*.bak
*.orig
temp/
tmp/

# OS
.DS_Store
Thumbs.db

# Docker
.docker/
docker-compose.override.yml
Dockerfile.bak

# Development
.env
.env.local
.venv/
venv/
env/

# Large data files
*.h5
*.hdf5
*.pt
*.pth
*.weights
"""


def generate_dockerignore(root: Path) -> None:
    """Generate .dockerignore file in the given directory."""
    dockerignore_path = root / ".dockerignore"
    if not dockerignore_path.exists():
        dockerignore_path.write_text(DOCKERIGNORE_CONTENT, encoding="utf-8")


def optimize_dockerfile_with_cache(dockerfile_content: str) -> str:
    """
    Transform a standard Dockerfile to use BuildKit cache mounts.
    
    This function:
    - Adds cache mount directives to apt-get commands
    - Adds cache mount directives to pip install commands
    - Optimizes layer consolidation
    """
    lines = dockerfile_content.split('\n')
    optimized_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Optimize: apt-get update && apt-get install -> single layer with cache
        if 'apt-get update' in line and i + 1 < len(lines):
            next_line = lines[i + 1]
            if 'apt-get install' in next_line:
                # Check if it's already optimized
                if '--mount=type=cache' not in line:
                    # Combine into single RUN with cache mounts
                    # Extract the full command (handle multi-line RUN commands)
                    run_cmd = line
                    j = i + 1
                    while j < len(lines) and not lines[j].strip().startswith('RUN '):
                        run_cmd += '\n' + lines[j]
                        j += 1
                    
                    # Add cache mount optimization
                    optimized_lines.append(
                        'RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \\\n'
                        '    --mount=type=cache,target=/var/cache/apt,sharing=locked \\'
                    )
                    
                    # Extract the actual commands (skip the RUN prefix)
                    cmd_part = run_cmd.replace('RUN apt-get update && ', '')
                    optimized_lines.append('    ' + cmd_part)
                    
                    i = j - 1
                else:
                    optimized_lines.append(line)
        
        # Optimize: pip install -> add cache mount
        elif 'pip' in line and 'install' in line and '--mount=type=cache' not in line:
            if line.strip().startswith('RUN'):
                # Check if this is a pip install command
                if 'pip3 install' in line or 'pip install' in line:
                    # Check if it's not already using --mount
                    if '--mount' not in line:
                        # Transform: RUN pip3 install ... 
                        # To: RUN --mount=type=cache,target=/root/.cache/pip pip3 install ...
                        
                        # Determine cache target based on context
                        cache_target = '/root/.cache/pip'
                        if 'RUN --user' in line or 'USER' in '\n'.join(lines[max(0, i-5):i]):
                            cache_target = '/home/$username/.cache/pip'
                        
                        # Insert cache mount
                        run_part = line[:line.find('RUN') + 3]
                        rest = line[line.find('RUN') + 3:].lstrip()
                        
                        optimized_lines.append(
                            f'{run_part} --mount=type=cache,target={cache_target} \\\n    {rest}'
                        )
                        i += 1
                        continue
        
        optimized_lines.append(line)
        i += 1
    
    return '\n'.join(optimized_lines)


def get_buildkit_enabled_env() -> str:
    """Return shell snippet to enable BuildKit."""
    return "export DOCKER_BUILDKIT=1"


def get_cache_optimization_comment(cache_mounts_enabled: bool = True) -> str:
    """Return a comment explaining cache behavior for the generated Dockerfile."""
    if cache_mounts_enabled:
        return """\
# This Dockerfile has been optimized for Docker BuildKit cache mounts:
# - APT packages: Cached in /var/lib/apt and /var/cache/apt
# - PIP wheels: Cached in /root/.cache/pip
#
# Benefits:
# - First build: ~15-20 minutes (normal)
# - Rebuild with cache: ~2-5 minutes (80% faster!)
#
# To use: export DOCKER_BUILDKIT=1 && docker build ...
"""

    return """\
# Cache mounts are disabled for this build engine.
# Reason: this engine does not support RUN --mount=type=cache in Dockerfile builds.
#
# Airfield still builds correctly, but without BuildKit cache-mount acceleration.
"""
