import importlib.util
from pathlib import Path

import pytest

from airfield.cli import guide
from airfield.main import app

REPO = Path(__file__).resolve().parents[1]


def _copy_guide():
    spec = importlib.util.spec_from_file_location("copy_guide", REPO / "scripts" / "copy_guide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guide_prints_the_quick_start_word_for_word(cli_runner):
    """Agents get the exact commands, not a summary of them."""
    result = cli_runner.invoke(app, ["guide"])

    assert result.exit_code == 0
    assert result.output.startswith("# Quick Start\n")
    assert "pipx install git+https://github.com/airfield/airfield.git" in result.output
    assert "`packages/<name>/`" in result.output


def test_quick_start_ends_with_the_other_topics(cli_runner):
    result = cli_runner.invoke(app, ["guide"])

    more = result.output.split("\n## More topics\n", 1)[1]
    for topic in ["reference", "runtime", "dependencies"]:
        assert f"`airfield guide {topic}`" in more


@pytest.mark.parametrize("topic, heading", [
    ("reference", "# CLI Reference"),
    ("runtime", "# Runtime & Environment"),
    ("dependencies", "# Managing Dependencies"),
])
def test_each_topic_prints_its_page(cli_runner, topic, heading):
    result = cli_runner.invoke(app, ["guide", topic])

    assert result.exit_code == 0
    assert result.output == guide.page(topic)
    assert result.output.startswith(f"{heading}\n")


@pytest.mark.parametrize("topic", ["quick-start", "reference", "runtime", "dependencies"])
def test_users_agents_never_see_the_maintainers_note(cli_runner, topic):
    """The copies say how to refresh them; that is for Airfield's maintainers,
    and an agent working in someone's project must not act on it."""
    result = cli_runner.invoke(app, ["guide", topic])

    assert "copy_guide" not in result.output and "<!--" not in result.output


def test_an_unknown_topic_names_the_real_ones(cli_runner):
    result = cli_runner.invoke(app, ["guide", "plans"])

    assert result.exit_code != 0
    assert "reference" in result.output


def test_guide_prints_nothing_else_inside_a_project(cli_runner, temp_workspace):
    """So `airfield guide > guide.md` saves exactly the guide."""
    cli_runner.invoke(app, ["project", "init", "."])

    result = cli_runner.invoke(app, ["guide"])

    assert result.exit_code == 0
    assert result.output == guide.page()


def test_guide_ships_in_the_wheel():
    """An editable install reads the guide from the checkout, so only a wheel,
    which is what pipx installs, would lose it: when pyproject stops listing it."""
    assert '"guide/*.md"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_every_topic_is_a_page_the_script_copies():
    """The copies are made only by scripts/copy_guide.py, never by hand."""
    assert set(_copy_guide().PAGES.values()) == set(guide.TOPICS)
    for topic in guide.TOPICS:
        assert guide.text(topic).startswith("<!-- Copied from https://airfield.io/docs/")


def test_copy_turns_a_docs_page_into_plain_markdown():
    mdx = (
        "---\nlayout: ../../layouts/Layout.astro\ntitle: Example\n---\n\n"
        "{/* a note for the\n    people editing this page */}\n\n"
        '<p class="eyebrow">Reference</p>\n\n'
        "# Example\n\n"
        "See [Runtime](/docs/runtime/#gpu-access) and [below](#fields), `AIRFIELD_DEP_<NAME>_VERSION`.\n\n"
        "  ```yaml\n  x: {a: 1}\n  ```\n"
    )

    copy = _copy_guide().to_markdown(mdx, "reference")

    header, body = copy.split("-->\n\n", 1)
    assert header.startswith("<!-- Copied from https://airfield.io/docs/reference/")
    assert body.startswith("# Example\n")
    assert "[Runtime](https://airfield.io/docs/runtime/#gpu-access)" in body
    assert "[below](#fields)" in body
    assert "x: {a: 1}" in body
    assert "layout:" not in body and "{/*" not in body and "eyebrow" not in body


@pytest.mark.parametrize("mdx", [
    "# Overview\n\n<DiagramFigure d=\"phases\" />\n",
    "import Term from '../../components/Term.astro';\n\n# Overview\n",
    "# Overview\n\nBuilt in {new Date().getFullYear()}.\n",
])
def test_copy_refuses_what_plain_markdown_cannot_say(mdx):
    with pytest.raises(SystemExit):
        _copy_guide().to_markdown(mdx, "overview")
