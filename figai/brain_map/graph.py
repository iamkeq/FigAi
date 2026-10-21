"""Builds a graph from real wiki notes and their Obsidian-style [[wikilinks]],
and renders it deterministically as a PNG. No image model, no invented edges."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

BACKGROUND = (255, 255, 255, 255)
NODE_COLOR = (142, 68, 173, 255)
EDGE_COLOR = (190, 190, 190, 255)
TEXT_COLOR = (40, 40, 40, 255)


@dataclass
class Graph:
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


def build_graph(notes_by_name: dict[str, str]) -> Graph:
    """`notes_by_name` maps a note's title (filename stem) to its Markdown content.
    An edge is added only between two notes that both actually exist."""
    names = set(notes_by_name)
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for name, content in notes_by_name.items():
        for match in WIKILINK_PATTERN.finditer(content):
            target = match.group(1).strip()
            if target not in names or target == name:
                continue
            edge = tuple(sorted((name, target)))
            if edge not in seen:
                seen.add(edge)
                edges.append(edge)

    return Graph(nodes=sorted(names), edges=edges)


def render_graph_png(graph: Graph, size: int = 900) -> Image.Image:
    image = Image.new("RGBA", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)

    if not graph.nodes:
        draw.text((size / 2 - 50, size / 2), "No notes found", fill=TEXT_COLOR)
        return image

    center = size / 2
    radius = size * 0.38
    node_count = len(graph.nodes)
    positions: dict[str, tuple[float, float]] = {
        name: (
            center + radius * math.cos(2 * math.pi * i / node_count),
            center + radius * math.sin(2 * math.pi * i / node_count),
        )
        for i, name in enumerate(graph.nodes)
    }

    for a, b in graph.edges:
        draw.line([positions[a], positions[b]], fill=EDGE_COLOR, width=2)

    node_radius = 6
    for name, (x, y) in positions.items():
        draw.ellipse([x - node_radius, y - node_radius, x + node_radius, y + node_radius], fill=NODE_COLOR)
        draw.text((x + node_radius + 3, y - 6), name, fill=TEXT_COLOR)

    return image
