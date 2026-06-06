import subprocess
from pathlib import Path

from airfield.config import AIRFIELD_CONFIG
from airfield.models import Package


def cleanup_package_container_artifacts(package_root: Path) -> None:
    manifest = package_root / AIRFIELD_CONFIG

    if not manifest.exists():
        return

    pkg = Package.load(manifest)
    image_name = f"airfield-pkg-{pkg.name}:latest"

    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={image_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if container_ids:
            subprocess.run(["docker", "rm", "-f", *container_ids], check=False)

    subprocess.run(["docker", "rmi", "-f", image_name], check=False)


from typing import Optional

def cleanup_all_airfield_containers(until: Optional[str] = None) -> int:
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0

    image_names = []
    for line in result.stdout.splitlines():
        image_name = line.strip()
        if image_name.startswith("airfield-pkg-"):
            image_names.append(image_name)

    removed = 0
    for image_name in sorted(set(image_names)):
        ps_cmd = ["docker", "ps", "-aq", "--filter", f"ancestor={image_name}"]
        if until:
            ps_cmd.extend(["--filter", f"until={until}"])

        container_result = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if container_result.returncode != 0:
            continue

        container_ids = [line.strip() for line in container_result.stdout.splitlines() if line.strip()]
        if container_ids:
            subprocess.run(["docker", "rm", "-f", *container_ids], check=False)
            removed += len(container_ids)

    return removed