"""Local CLI runner — `python -m app.cli "<question>"`.

Useful for smoke-testing the pipeline without spinning up FastAPI or
the frontend. Writes the HTML brief to ``backend/runtime/briefs/`` and
prints the markdown version to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# Windows consoles default to cp1252 — coerce to UTF-8 so the Rich renderer
# (and our fixture content with arrows/em-dashes) doesn't crash on output.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

from . import config
from .agent import run_agent
from .brief import render_markdown, write_html
from .models import Question


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atlas CLI")
    p.add_argument("question", help="The intelligence question to run")
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress logs (show only the brief)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Print the full brief as JSON instead of markdown",
    )
    return p.parse_args()


async def _amain(args: argparse.Namespace) -> int:
    console = Console()
    question = Question(text=args.question)
    brief = await run_agent(question)
    html_path = write_html(brief)

    if args.json:
        console.print_json(brief.model_dump_json())
    else:
        header = (
            f"[bold cyan]Atlas[/]  brief={brief.id}  "
            f"mode=[bold yellow]{brief.mode}[/]  "
            f"confidence={brief.confidence_score:.2f}"
        )
        console.print(Panel.fit(header, border_style="cyan"))
        console.print(Markdown(render_markdown(brief)))
        console.print()
        console.print(f"[dim]HTML written to:[/] {html_path}")
    return 0


def main() -> int:
    args = _parse_args()
    if args.quiet:
        logging.disable(logging.CRITICAL)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        )
    logging.info("Atlas CLI starting (mode=%s, llm=%s)", config.MODE, config.has_llm())
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
