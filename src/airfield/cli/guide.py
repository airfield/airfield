import re
from pathlib import Path

import typer

from airfield.config import GUIDE_DIR

# Topic -> what its page covers. Each is a copy of an airfield.io page under
# guide/, made by scripts/copy_guide.py (which also maps topics to pages).
TOPICS = {
    "quick-start": "install, convert a workspace, build, run, launch a plan",
    "reference": "every command, and every field of airfield.yaml, plans, manifests and .air",
    "runtime": "what a container gets: engine, network, user, mounts, GPU, shared images, environment variables",
    "dependencies": "how a dependency name is resolved, and how to write and share manifests",
}


def text(topic: str) -> str:
    return (Path(__file__).resolve().parent.parent / GUIDE_DIR / f"{topic}.md").read_text(encoding="utf-8")


def page(topic: str = "quick-start") -> str:
    """What `airfield guide` prints. The Quick Start ends with the other topics."""
    # Without the copy's header: it tells Airfield's maintainers how to
    # refresh the copy, and a user's agent must not take it as an instruction.
    body = re.sub(r"\A<!--.*?-->\n+", "", text(topic), flags=re.S)
    if topic != "quick-start":
        return body
    others = "\n".join(f"- `airfield guide {name}`: {about}" for name, about in TOPICS.items() if name != "quick-start")
    return f"{body}\n## More topics\n\nEach prints a full page the same way, for the installed version:\n\n{others}\n"


def run(
    topic: str = typer.Argument("quick-start", help=f"Which page: {', '.join(TOPICS)}."),
):
    """Print the Airfield guide, for people and coding agents."""
    if topic not in TOPICS:
        typer.echo(f"No guide topic '{topic}'. Topics: {', '.join(TOPICS)}.", err=True)
        raise typer.Exit(1)
    # Plain Markdown, never rendered: agents need the exact commands, and
    # `airfield guide > guide.md` should save a file that reads the same.
    typer.echo(page(topic), nl=False)
