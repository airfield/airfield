"""rosdep's lookup table is read from its data files, on the host, with no
rosdep installed and no network once the files are cached."""
import json
import time

import pytest
import yaml

from airfield import rosdep_table
from airfield.rosdep_table import (
    VIA_ROS_INDEX,
    VIA_ROSDEP,
    Translation,
    compile_rules,
    read_distribution,
)


# --- picking the rule for an Ubuntu release ------------------------------------

@pytest.mark.parametrize(
    "entry, expected",
    [
        ("ubuntu: [libeigen3-dev]", ["apt", ["libeigen3-dev"]]),
        ("ubuntu: one two", ["apt", ["one", "two"]]),
        ("ubuntu: []", ["apt", []]),
        ("ubuntu: {apt: {packages: [a, b]}}", ["apt", ["a", "b"]]),
        ("ubuntu: {pip: {packages: [pykalman]}}", ["pip", ["pykalman"]]),
        ("ubuntu: {pip: [pykalman]}", ["pip", ["pykalman"]]),
        # the release named, else the wildcard
        ("ubuntu: {'*': [generic], noble: [for-noble]}", ["apt", ["for-noble"]]),
        ("ubuntu: {'*': [generic], jammy: [for-jammy]}", ["apt", ["generic"]]),
        ("ubuntu: {noble: {pip: {packages: [x]}}}", ["pip", ["x"]]),
        # an OS wildcard
        ("'*': [anywhere]", ["apt", ["anywhere"]]),
        # nothing usable for this release
        ("ubuntu: {jammy: [only-jammy]}", None),
        ("ubuntu: {'*': [generic], noble: null}", None),
        ("fedora: [something]", None),
        ("ubuntu: {snap: {packages: [thing]}}", None),
        ("ubuntu: {source: {uri: 'https://example.invalid/x.rdmanifest'}}", None),
    ],
)
def test_rule_for_ubuntu_release(entry, expected):
    rules = compile_rules([{"key": yaml.safe_load(entry)}], "noble")
    assert rules.get("key") == expected


def test_first_file_wins_like_rosdep_sources_list():
    rules = compile_rules(
        [{"cmake": {"ubuntu": ["cmake"]}}, {"cmake": {"ubuntu": ["other"]}, "rake": {"ubuntu": ["rake"]}}],
        "noble",
    )
    assert rules == {"cmake": ["apt", ["cmake"]], "rake": ["apt", ["rake"]]}


def test_distribution_file_gives_the_ubuntu_release_and_the_released_packages():
    codename, released = read_distribution(
        {
            "release_platforms": {"rhel": ["9"], "ubuntu": ["noble"]},
            "repositories": {
                "rclcpp": {"release": {"packages": ["rclcpp", "rclcpp_action"]}},
                "angles": {"release": {"url": "x"}},      # one package, named after the repository
                "unreleased": {"source": {"url": "x"}},   # not installable from apt
            },
        }
    )
    assert codename == "noble"
    assert released == ["angles", "rclcpp", "rclcpp_action"]


# --- fetching and caching ------------------------------------------------------

def test_table_is_fetched_once_and_then_read_from_the_cache(name_table, tmp_path, mocker):
    table, problem = rosdep_table.load("jazzy")
    assert problem is None
    assert table.codename == "noble"
    assert table.lookup("eigen") == Translation("apt", ("libeigen3-dev",), VIA_ROSDEP)
    assert table.lookup("curl").packages == ("libcurl4-openssl-dev", "curl")
    assert table.lookup("rake").packages == ("rake",)
    assert table.lookup("pykalman-pip") == Translation("pip", ("pykalman",), VIA_ROSDEP)
    assert table.lookup("python3-mixed") == Translation("apt", ("python3-mixed",), VIA_ROSDEP)
    assert table.lookup("libpcl-all-dev").packages == ("libpcl-dev",)
    assert table.lookup("python-argparse").packages == ()
    assert table.lookup("cmake").packages == ("cmake",), "base.yaml comes before python.yaml"
    for absent in ("only-on-jammy", "gone-on-noble", "fedora-only", "snap-thing", "not_released_yet", "nope"):
        assert table.lookup(absent) is None, absent

    cached = json.loads((tmp_path / "cache" / "airfield" / "rosdep" / "jazzy.json").read_text())
    assert cached["codename"] == "noble" and "eigen" in cached["rules"]

    # A new process finds the cache and never asks the network again.
    rosdep_table._loaded.clear()
    download = mocker.patch("airfield.rosdep_table._download")
    again, problem = rosdep_table.load("jazzy")
    assert problem is None and again.lookup("eigen").packages == ("libeigen3-dev",)
    download.assert_not_called()


def test_released_ros_package_gets_its_ros_apt_name(name_table):
    table, _ = rosdep_table.load("jazzy")
    assert table.lookup("nav2_msgs") == Translation("apt", ("ros-$ROS_DISTRO-nav2-msgs",), VIA_ROS_INDEX)
    assert table.lookup("angles").packages == ("ros-$ROS_DISTRO-angles",)
    assert table.lookup("rclcpp_action").via == VIA_ROS_INDEX


def test_refresh_replaces_the_cached_copy(name_table):
    table, _ = rosdep_table.load("jazzy")
    assert table.lookup("fmt") is None

    base = name_table / "rosdep" / "base.yaml"
    base.write_text(base.read_text() + "fmt:\n  ubuntu: [libfmt-dev]\n", encoding="utf-8")

    assert rosdep_table.load("jazzy")[0].lookup("fmt") is None, "never refreshed behind the user's back"
    table, problem = rosdep_table.refresh("jazzy")
    assert problem is None and table.lookup("fmt").packages == ("libfmt-dev",)
    assert rosdep_table.load("jazzy")[0].lookup("fmt").packages == ("libfmt-dev",)


def test_unreachable_source_is_reported_and_not_retried_at_once(name_table, mocker):
    (name_table / "jazzy" / "distribution.yaml").unlink()

    table, problem = rosdep_table.load("jazzy")
    assert table is None
    assert "could not be fetched" in problem and "airfield package dependencies pull" in problem

    # The next command (a new process) within the retry window leaves the
    # network alone but still says why names are translated by rule only.
    rosdep_table._loaded.clear()
    download = mocker.patch("airfield.rosdep_table._download")
    table, problem = rosdep_table.load("jazzy")
    assert table is None and "could not be fetched" in problem
    download.assert_not_called()


def test_fetch_is_retried_after_the_retry_window(name_table, mocker):
    distribution = name_table / "jazzy" / "distribution.yaml"
    text = distribution.read_text()
    distribution.unlink()
    assert rosdep_table.load("jazzy")[0] is None

    distribution.write_text(text, encoding="utf-8")
    rosdep_table._loaded.clear()
    mocker.patch("airfield.rosdep_table.time.time", return_value=time.time() + rosdep_table._RETRY_AFTER + 1)
    table, problem = rosdep_table.load("jazzy")
    assert problem is None and table.lookup("eigen") is not None


def test_failed_refresh_keeps_the_copy_that_was_there(name_table):
    assert rosdep_table.load("jazzy")[0] is not None
    (name_table / "rosdep" / "base.yaml").unlink()

    table, problem = rosdep_table.refresh("jazzy")
    assert "could not be fetched" in problem
    assert table.lookup("eigen").packages == ("libeigen3-dev",)


def test_shell_completion_never_waits_on_the_network(name_table, monkeypatch, mocker):
    monkeypatch.setenv("_AIRFIELD_COMPLETE", "complete_bash")
    download = mocker.patch("airfield.rosdep_table._download")
    assert rosdep_table.load("jazzy") == (None, None)
    download.assert_not_called()

    # ...and the real command that follows still fetches it.
    monkeypatch.delenv("_AIRFIELD_COMPLETE")
    mocker.stopall()
    assert rosdep_table.load("jazzy")[0] is not None


def test_table_can_be_switched_off(name_table, monkeypatch, mocker):
    monkeypatch.setenv("AIRFIELD_ROSDEP_TABLE", "off")
    download = mocker.patch("airfield.rosdep_table._download")
    assert rosdep_table.load("jazzy") == (None, None)
    download.assert_not_called()


def test_unreadable_cache_file_is_fetched_again(name_table, tmp_path):
    cache = tmp_path / "cache" / "airfield" / "rosdep"
    cache.mkdir(parents=True)
    (cache / "jazzy.json").write_text("{ not json", encoding="utf-8")

    table, problem = rosdep_table.load("jazzy")
    assert problem is None and table.lookup("eigen") is not None


def test_unusable_cache_directory_does_not_stop_the_command(name_table, tmp_path, monkeypatch):
    """A read-only home must cost the table, not the build."""
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the cache directory should go", encoding="utf-8")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocked))

    table, problem = rosdep_table.load("jazzy")

    assert table is None
    assert "cannot be kept on this machine" in problem
    assert rosdep_table.cached_distros() == []
