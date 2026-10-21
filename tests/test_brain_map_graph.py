from __future__ import annotations

from figai.brain_map.graph import build_graph, render_graph_png


def test_build_graph_creates_edge_for_mutual_existing_wikilink() -> None:
    notes = {"A": "See [[B]] for more.", "B": "Back to [[A]]."}

    graph = build_graph(notes)

    assert graph.nodes == ["A", "B"]
    assert graph.edges == [("A", "B")]


def test_build_graph_ignores_links_to_nonexistent_notes() -> None:
    notes = {"A": "See [[Ghost]]."}

    graph = build_graph(notes)

    assert graph.nodes == ["A"]
    assert graph.edges == []


def test_build_graph_ignores_self_links() -> None:
    notes = {"A": "Refers to [[A]] itself."}

    graph = build_graph(notes)

    assert graph.edges == []


def test_build_graph_handles_piped_wikilinks() -> None:
    notes = {"A": "See [[B|the other note]].", "B": "content"}

    graph = build_graph(notes)

    assert graph.edges == [("A", "B")]


def test_build_graph_deduplicates_edges() -> None:
    notes = {"A": "[[B]] [[B]] again", "B": "[[A]]"}

    graph = build_graph(notes)

    assert graph.edges == [("A", "B")]


def test_render_graph_png_empty_graph_still_returns_image() -> None:
    image = render_graph_png(build_graph({}), size=200)
    assert image.size == (200, 200)


def test_render_graph_png_returns_correct_size_for_populated_graph() -> None:
    graph = build_graph({"A": "[[B]]", "B": "[[A]]"})
    image = render_graph_png(graph, size=300)
    assert image.size == (300, 300)
