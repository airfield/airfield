from pathlib import Path

TEMPLATE = """name: Build & Test {{ package_name }}

on:
  push:
    branches: [ "main" ]
    paths:
      - 'build/packages/{{ package_name }}/**'
      - 'build/dependencies/**'
  pull_request:
    branches: [ "main" ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Airfield
        run: pip install -e ./airfield

      - name: Build Airfield Package
        run: |
          airfield package build {{ package_name }}
          # airfield project run {{ package_name }} --test
"""

def generate_github_action(package_name: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"build-{package_name}.yml"
    with open(out_path, "w") as f:
        # In a real environment, we'd use Jinja template. Here we use string replace for simplicity.
        f.write(TEMPLATE.replace("{{ package_name }}", package_name))
