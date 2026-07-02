import yaml
from pathlib import Path
from airfield.main import app
from airfield.models import Plan


def test_up_legacy_plan(cli_runner, temp_workspace, mock_docker):
    """Test 'airfield project up' with a legacy packages plan."""
    # Initialize a dummy project
    cli_runner.invoke(app, ["project", "init", "."])

    # Write a legacy plan
    plan_dir = temp_workspace / "plans"
    plan_dir.mkdir(exist_ok=True)
    plan_yaml = plan_dir / "legacy_plan.yaml"
    plan_yaml.write_text(
        "name: legacy_plan\n"
        "packages:\n"
        "  - package_a\n"
        "  - package_b\n",
        encoding="utf-8"
    )

    result = cli_runner.invoke(app, ["project", "up", "legacy_plan", "--no-launch"])
    assert result.exit_code == 0
    assert "Generated tmuxinator config" in result.output

    generated_file = temp_workspace / ".airfield" / "legacy_plan.tmuxinator.yml"
    assert generated_file.exists()

    content = yaml.safe_load(generated_file.read_text(encoding="utf-8"))
    assert content["name"] == "legacy_plan"
    assert len(content["windows"]) == 2
    assert content["windows"][0] == {"package_a": {"panes": ["airfield project run package_a"]}}
    assert content["windows"][1] == {"package_b": {"panes": ["airfield project run package_b"]}}


def test_up_new_schema_plan(cli_runner, temp_workspace, mock_docker):
    """Test 'airfield project up' with the upgraded windows/panes schema."""
    # Initialize a dummy project
    cli_runner.invoke(app, ["project", "init", "."])

    # Write a new schema plan
    plan_dir = temp_workspace / "plans"
    plan_dir.mkdir(exist_ok=True)
    plan_yaml = plan_dir / "complex_plan.yaml"
    plan_yaml.write_text(
        "name: complex_plan\n"
        "pre_window: export DISPLAY=:9\n"
        "windows:\n"
        "  - name: sim_window\n"
        "    layout: main-vertical\n"
        "    pre_window: echo 'starting'\n"
        "    panes:\n"
        "      - \n"
        "      - vnc\n"
        "      - package: pkg_a\n"
        "        cmd: ros2 run pkg_a node_a\n"
        "      - package: pkg_b\n"
        "        cmd: until [ -S /tmp/sock ]; do sleep 1; done && DISPLAY=:9 run -p mode:=\"none\"\n"
        "      - cmd: echo 'host cmd'\n",
        encoding="utf-8"
    )

    result = cli_runner.invoke(app, ["project", "up", "complex_plan", "--no-launch"])
    assert result.exit_code == 0
    assert "Generated tmuxinator config" in result.output

    generated_file = temp_workspace / ".airfield" / "complex_plan.tmuxinator.yml"
    assert generated_file.exists()

    content = yaml.safe_load(generated_file.read_text(encoding="utf-8"))
    assert content["name"] == "complex_plan"
    assert content["pre_window"] == "export DISPLAY=:9"
    assert len(content["windows"]) == 1

    window = content["windows"][0]["sim_window"]
    assert window["layout"] == "main-vertical"
    assert window["pre_window"] == "echo 'starting'"
    # Package pane cmds are double-quoted into ONE bash -lc argument so shell
    # syntax (&&, ;, loops) runs inside the container rather than being split
    # by the host pane shell; literal double quotes in the cmd are escaped.
    assert window["panes"] == [
        None,
        "vnc",
        'airfield package cmd pkg_a -- bash -lc "ros2 run pkg_a node_a"',
        'airfield package cmd pkg_b -- bash -lc "until [ -S /tmp/sock ]; do sleep 1; done'
        ' && DISPLAY=:9 run -p mode:=\\"none\\""',
        "echo 'host cmd'"
    ]


def test_up_no_plan_name(cli_runner, temp_workspace):
    """Test 'airfield project up' lists available plans when no plan name is passed."""
    # Initialize a dummy project
    cli_runner.invoke(app, ["project", "init", "."])

    # Write a couple of plans
    plan_dir = temp_workspace / "plans"
    plan_dir.mkdir(exist_ok=True)
    (plan_dir / "plan_a.yaml").write_text("name: plan_a\n", encoding="utf-8")
    (plan_dir / "plan_b.yaml").write_text("name: plan_b\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["project", "up"])
    assert result.exit_code == 0
    assert "Available plans:" in result.output
    assert "  - plan_a" in result.output
    assert "  - plan_b" in result.output
