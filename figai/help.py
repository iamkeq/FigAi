"""The help menu. Two sources, so it's both friendly and complete:
- the curated cheat sheet in skills/help/SKILL.md (example phrases, grouped by topic), and
- the live tool registry (`!tools`), generated, so a new tool can never go missing from it."""

from __future__ import annotations

import re
from typing import Any

SECTION_SPLIT = re.compile(r"(?m)^## ")
MENU_EXAMPLE_CHARS = 90


def sections(body: str) -> list[tuple[str, str]]:
    """[(heading, text)] for each `## heading` in the sheet."""
    parts = SECTION_SPLIT.split(body)[1:]
    return [(p.split("\n", 1)[0].strip(), p.split("\n", 1)[1].strip() if "\n" in p else "") for p in parts]


def _render(heading: str, text: str) -> str:
    return f"*{heading}*\n{text}"


def _first_example(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("-• ").strip()
        if line:
            return line if len(line) <= MENU_EXAMPLE_CHARS else line[: MENU_EXAMPLE_CHARS - 1] + "…"
    return ""


def menu(body: str) -> str:
    """Short front page: the commands, then one line per topic."""
    parts = sections(body)
    commands = next((text for heading, text in parts if heading.lower() == "commands"), "")
    topics = [(h, t) for h, t in parts if h.lower() != "commands"]
    lines = [
        "*Fig help menu*",
        "Talk to me in plain English, by voice or text -- or use a `!command` (instant, works even if my model is down).",
        "",
        _render("Commands", commands),
        "",
        "*Topics* -- `!help <topic>` shows how to ask for each:",
    ]
    lines += [f"• *{heading}* -- {_first_example(text)}" for heading, text in topics]
    lines += ["", "`!help all` = everything at once · `!tools` = every tool I have, auto-generated"]
    return "\n".join(lines)


def topic(body: str, query: str) -> str:
    query = query.lower().strip()
    parts = sections(body)
    if query in ("all", "everything", "full"):
        return "\n\n".join(["*Fig cheat sheet*", *(_render(h, t) for h, t in parts)])
    matches = [(h, t) for h, t in parts if query in h.lower()]
    if not matches:
        # also match a word in the section's text ("!help spotify" -> This PC)
        matches = [(h, t) for h, t in parts if re.search(rf"\b{re.escape(query)}", t.lower())]
    if not matches:
        names = ", ".join(h.lower() for h, _ in parts)
        return f"No help matches '{query}'. Topics: {names}. `!help` shows the menu."
    return "\n\n".join(_render(h, t) for h, t in matches)


def tools_list(descriptions: list[tuple[str, str]], query: str = "") -> str:
    """`descriptions` is [(tool name, one-line description)]."""
    query = query.lower().strip()
    chosen = [(n, d) for n, d in descriptions if not query or query in n.lower() or query in d.lower()]
    if not chosen:
        return f"No tool matches '{query}'."
    header = f"*{len(chosen)} tool(s)*" + (f" matching '{query}'" if query else "") + " -- I pick these myself from what you ask:"
    return "\n".join([header, *(f"• `{name}` -- {desc}" for name, desc in chosen)])


def one_line(description: str, limit: int = 110) -> str:
    first = re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1] + "…"


def describe_registry(registry: Any) -> list[tuple[str, str]]:
    return sorted((name, one_line(desc)) for name, desc in registry.describe())
