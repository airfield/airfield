#!/usr/bin/env python3
"""Copy the airfield.io pages that `airfield guide` prints into the package.

The pages are written in the documentation repository (the source of
airfield.io). This turns each one into plain Markdown under src/airfield/guide/.
Run it after editing one of them, then review and commit the copies:

    python3 scripts/copy_guide.py                      # docs checkout at ../documentation
    python3 scripts/copy_guide.py ~/src/airfield/documentation
    git diff src/airfield/guide

Standard library only, so any python3 runs it.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "src" / "airfield" / "guide"
SITE = "https://airfield.io"

# Page in the documentation repo (src/pages/docs/<page>.mdx) -> topic, which is
# both the copy's file name and the name in `airfield guide <topic>`. A topic
# added here also needs its line in TOPICS in src/airfield/cli/guide.py.
PAGES = {
    "quick-start": "quick-start",
    "reference": "reference",
    "runtime": "runtime",
    "manage-dependencies": "dependencies",
}


def _mdx_only(text: str):
    """The first piece of MDX that plain Markdown has no form for, or None."""
    prose = re.sub(r"^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$", "", text, flags=re.S | re.M)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    found = re.search(r"^import .*|<[A-Z][\w.]*|[{}]", prose, flags=re.M)
    return found.group(0) if found else None


def to_markdown(mdx: str, page: str) -> str:
    text = re.sub(r"\A---\n.*?\n---\n", "", mdx, flags=re.S)
    text = re.sub(r"\{/\*.*?\*/\}\n*", "", text, flags=re.S)
    # The small label the site prints above each title.
    text = re.sub(r'^<p class="eyebrow">[^<]*</p>\n', "", text, flags=re.M)
    # Links within the site must still work from a terminal.
    text = text.replace("](/", f"]({SITE}/")
    leftover = _mdx_only(text)
    if leftover:
        raise SystemExit(
            f"{page}.mdx uses {leftover!r}, which has no plain-text form. "
            "Rewrite that part of the page in plain Markdown, or stop copying the page."
        )
    header = (
        f"<!-- Copied from {SITE}/docs/{page}/ by scripts/copy_guide.py. Don't edit it\n"
        f"     here: edit src/pages/docs/{page}.mdx in the documentation repository,\n"
        "     then run the script again. -->\n\n"
    )
    return header + text.lstrip("\n")


def _git(docs: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(docs), *args], capture_output=True, text=True, check=False)
    # rstrip only: a porcelain status line can start with a space.
    return result.stdout.rstrip() if result.returncode == 0 else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("docs", nargs="?", type=Path, default=REPO.parent / "documentation",
                        help="the documentation repository (default: ../documentation)")
    docs = parser.parse_args().docs.expanduser().resolve()
    pages_dir = docs / "src" / "pages" / "docs"
    if not pages_dir.is_dir():
        sys.exit(f"No documentation pages at {pages_dir}. Pass the documentation repository's path.")

    # Copying from a stale branch or from edits never committed are the easy
    # mistakes, so say what is being copied from.
    branch = _git(docs, "rev-parse", "--abbrev-ref", "HEAD")
    print(f"Copying from {docs} ({branch or 'not a git checkout'} at {_git(docs, 'rev-parse', '--short', 'HEAD')})")
    if branch and branch not in ("master", "main"):
        print(f"  note: airfield.io is published from master, and this checkout is on {branch}")
    dirty = _git(docs, "status", "--porcelain", "--", *(f"src/pages/docs/{page}.mdx" for page in PAGES))
    if dirty:
        print("  note: copying edits not yet committed there:", ", ".join(line[3:] for line in dirty.splitlines()))

    GUIDE.mkdir(parents=True, exist_ok=True)
    for page, topic in PAGES.items():
        source = pages_dir / f"{page}.mdx"
        if not source.is_file():
            sys.exit(f"Missing {source}.")
        copy = GUIDE / f"{topic}.md"
        before = copy.read_text(encoding="utf-8") if copy.exists() else None
        after = to_markdown(source.read_text(encoding="utf-8"), page)
        copy.write_text(after, encoding="utf-8")
        state = "new" if before is None else "unchanged" if before == after else "updated"
        print(f"  {state:9} {copy.relative_to(REPO)}")
    for stale in sorted(set(GUIDE.glob("*.md")) - {GUIDE / f"{topic}.md" for topic in PAGES.values()}):
        stale.unlink()
        print(f"  removed   {stale.relative_to(REPO)}")
    print("Review with: git diff src/airfield/guide")


if __name__ == "__main__":
    main()
