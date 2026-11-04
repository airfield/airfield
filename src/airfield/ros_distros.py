"""Supported ROS distributions, kept apart from models.py so commands can
list them without loading pydantic."""

# Single source of truth for supported ROS distributions. Adding a distro is
# one entry here: base images per arch (osrf desktop images are amd64-only,
# so arm64 uses the official ros-base tags) and the in-image build tool.
ROS_DISTROS = {
    "noetic": {
        "base_image": "ros:noetic-ros-base",
        "arm64_base_image": "ros:noetic-ros-base",
        "core_packages": ["python3-catkin-tools"],
    },
    "humble": {
        "base_image": "osrf/ros:humble-desktop",
        "arm64_base_image": "ros:humble-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
    "jazzy": {
        "base_image": "osrf/ros:jazzy-desktop",
        "arm64_base_image": "ros:jazzy-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
    "kilted": {
        "base_image": "osrf/ros:kilted-desktop",
        "arm64_base_image": "ros:kilted-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
    "rolling": {
        "base_image": "osrf/ros:rolling-desktop",
        "arm64_base_image": "ros:rolling-ros-base",
        "core_packages": ["python3-colcon-common-extensions"],
    },
}

SUPPORTED_ROS_DISTROS = set(ROS_DISTROS)
